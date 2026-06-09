from __future__ import annotations

"""伏羲 gRPC 服务端（v0.2.0: 集成连接池 + 断路器 + 进化层）"""
import grpc
import json
import re
import time
import os
import sys
import signal
import logging

# 当前目录是 python/src
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'proto/generated/python'))
sys.path.insert(0, CURRENT_DIR)

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc

# 预先导入 tools 模块以触发注册

import engine.fuxi_engine as fuxi_engine_module
from engine.execution_logger import StructuredLogger
from engine.tool_tracker import ToolCallTracker
from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory
from llm.client import LLMClient
from grpc_utils.connection_pool import GrpcConnectionPool
from evolution.selector import Selector

FuxiEngine = fuxi_engine_module.FuxiEngine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('fuxi_grpc')


# ════════════════════════════════════════════════════════════════════
# 工具错误脱敏（v0.2.5 加：CRITICAL-4 全量修）
# ════════════════════════════════════════════════════════════════════
#
# 工具返回的 error 字段可能含绝对路径（File not found: C:\...）或 Python
# 异常消息（[Errno 2] No such file or directory: '/home/...'），
# 直接回客户端会泄露本地文件系统结构。
#
# 处理原则：
#   1. 工具的 success/error 类型语义保留（用户能区分"找不到"vs"权限拒绝"）
#   2. 路径片段用 <path> 占位符替换
#   3. 原始错误写 server log（logger.warning），方便排查
#   4. 不动 URL、字节数、enum 错误等非路径内容
#
_PATH_PATTERNS = [
    # Windows 绝对路径：盘符前不能是字母（避免 http: 误伤）
    re.compile(r'(?<![A-Za-z])[A-Za-z]:[\\/](?:[^\\/:*?"<>|\r\n]+[\\/])*[^\\/:*?"<>|\r\n]*'),
    # Unix 绝对路径：前导空白/引号后跟 / 段（避免误伤 URL）
    re.compile(r'(?<=[\s"\'\'])((?:/[^/\s"\'<>|]+)+/?)'),
]


def _sanitize_tool_error(error: str) -> str:
    """把工具 error 字段中的绝对路径替换为 <path>。

    Args:
        error: 工具原始 error 消息

    Returns:
        脱敏后的 error（结构语义保留，路径被替换）
    """
    if not error:
        return error
    sanitized = error
    for pat in _PATH_PATTERNS:
        sanitized = pat.sub('<path>', sanitized)
    return sanitized


def _load_local_config() -> None:
    """从 config/local.yaml 加载 LLM 配置，注入到 os.environ。

    优先级：环境变量 > config/local.yaml
    这样 CI 可以通过 env 覆盖本地 yaml 值。

    找不到 yaml / 解析失败 时静默回退到 env vars。
    """
    try:
        import yaml  # PyYAML 在 requirements.txt 中
    except ImportError:
        return

    candidates = [
        os.path.join(PROJECT_ROOT, "config", "local.yaml"),
        os.path.join(os.path.dirname(PROJECT_ROOT), "config", "local.yaml"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to parse {path}: {e}")
            continue
        llm = cfg.get("llm", {})
        # 仅当 env 未设时才覆盖
        for env_key, yaml_key in [
            ("LLM_API_KEY", "api_key"),
            ("LLM_BASE_URL", "base_url"),
            ("DEFAULT_MODEL", "model"),
            ("LLM_MODEL", "model"),
        ]:
            if env_key not in os.environ and llm.get(yaml_key):
                os.environ[env_key] = str(llm[yaml_key])
        logger.info(f"Loaded LLM config from {path}")
        return
    logger.debug("No config/local.yaml found; using environment variables only")


# 在读 env vars 之前先加载 local.yaml
_load_local_config()

# 从环境变量加载配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "")
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() != "false"

# 默认超时配置（毫秒）
DEFAULT_PORT = 50051
DEFAULT_TIMEOUT = 30000


class FuxiCoreServicer(fuxi_pb2_grpc.FuxiCoreServicer):
    """Fuxi gRPC 服务实现（v0.2.0: 集成 Selector + 进化层）"""

    def __init__(self):
        # 创建执行日志器和工具追踪器
        self._execution_logger = StructuredLogger()
        self._tool_tracker = ToolCallTracker()

        # v0.2.0: 创建统一 Selector（整合工具排序 + 策略推荐 + 记忆检索）
        self._warm_memory = WarmMemory()
        self._cold_memory = ColdMemory()
        self._selector = Selector(
            tracker_db_path=self._tool_tracker.db_path,
            warm_memory=self._warm_memory,
            cold_memory=self._cold_memory,
        )

        self.engine = FuxiEngine(
            llm_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=DEFAULT_MODEL,
            execution_logger=self._execution_logger,
            tool_tracker=self._tool_tracker,
            selector=self._selector,
            warm_memory=self._warm_memory,
            cold_memory=self._cold_memory,
        )
        # 保存默认配置，用于每个请求的副本
        self._default_api_key = LLM_API_KEY
        self._default_base_url = LLM_BASE_URL
        self._default_model = DEFAULT_MODEL

    def _get_metadata(self, context) -> dict:
        """从 gRPC metadata 中提取认证和配置信息"""
        metadata_dict = dict(context.invocation_metadata())
        return {
            "api_key": metadata_dict.get("authorization", "").replace("Bearer ", ""),
            "base_url": metadata_dict.get("base-url", ""),
            "model": metadata_dict.get("model", ""),
        }

    def _check_auth(self, context, metadata: dict) -> bool:
        """验证 API key"""
        if not AUTH_ENABLED:
            return True  # 认证已关闭
        if not self._default_api_key:
            # fail-closed：开了鉴权但没配 key → 拒绝所有请求，避免未授权访问
            logger.error(
                "AUTH_ENABLED=true but no default API key configured; "
                "refusing all authenticated requests"
            )
            return False
        provided_key = metadata.get("api_key", "")
        if not provided_key:
            return False
        import hmac
        return hmac.compare_digest(provided_key, self._default_api_key)

    def _get_client_config(self, metadata: dict, request_model: str = "") -> dict:
        """获取客户端配置（线程安全，不修改共享状态）"""
        api_key = metadata.get("api_key") or self._default_api_key
        base_url = metadata.get("base_url") or self._default_base_url
        model = metadata.get("model") or request_model or self._default_model
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }

    def InvokeTool(self, request, context):
        """调用工具"""
        try:
            metadata = self._get_metadata(context)

            # 验证
            if not self._check_auth(context, metadata):
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid API key")
                return fuxi_pb2.ToolResult()

            # 获取配置（不修改共享状态）
            config = self._get_client_config(metadata, request.model)

            result = self.engine.tool_registry.invoke(
                request.tool_name, request.arguments_json
            )

            # 工具错误脱敏：剥离绝对路径，原始消息写服务端日志
            # registry 把工具的 dict 返回 JSON 化放进 result_json，
            # wrapper.success=True（无异常）但工具自身可能 success=False。
            # 所以要解析 result_json 检查工具自己的 error。
            tool_error = result.get("error", "")
            tool_result_json = result.get("result_json", "{}")

            # 1) wrapper 异常路径（registry 自己 except 到的）
            if not result.get("success") and tool_error:
                sanitized = _sanitize_tool_error(tool_error)
                if sanitized != tool_error:
                    logger.warning(
                        f"Tool {request.tool_name} wrapper error (sanitized): {tool_error}"
                    )
                    tool_error = sanitized

            # 2) 工具自身错误路径（result_json 里）
            if tool_result_json and tool_result_json != "{}":
                try:
                    parsed = json.loads(tool_result_json)
                    if isinstance(parsed, dict) and parsed.get("success") is False and parsed.get("error"):
                        original = parsed["error"]
                        sanitized = _sanitize_tool_error(original)
                        if sanitized != original:
                            logger.warning(
                                f"Tool {request.tool_name} error (sanitized): {original}"
                            )
                            parsed["error"] = sanitized
                            tool_result_json = json.dumps(parsed, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    pass  # 不是 JSON 字典，原样返回

            return fuxi_pb2.ToolResult(
                success=result["success"],
                result_json=tool_result_json,
                error=tool_error,
                elapsed_ms=result.get("elapsed_ms", 0),
            )
        except Exception as e:
            # 服务端记录完整 traceback；只回客户端归一化消息，避免泄露文件路径/SQLite细节
            logger.error(f"InvokeTool error: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal error")
            return fuxi_pb2.ToolResult(success=False, error="Internal error")

    def StreamComplete(self, request, context):
        """流式回复 - 使用真正的流式 LLM API
        
        优化：复用引擎的工具注册表和热记忆，仅替换 LLM 客户端配置，
        避免每次请求都创建完整的引擎实例。
        """
        try:
            metadata = self._get_metadata(context)

            if not self._check_auth(context, metadata):
                context.set_code(grpc.StatusCode.UNAUTHENTICATED)
                context.set_details("Invalid API key")
                yield fuxi_pb2.CompletionChunk(
                    content="Authentication failed: Invalid API key",
                    is_final=True,
                    reasoning="",
                )
                return

            config = self._get_client_config(metadata, request.model)

            # v0.2.6: 不再替换 self.engine.llm（共享实例并发不安全）。
            # 改为按需 new LLMClient 传给 stream_run()。
            current_model = self.engine.llm.model
            target_model = config.get("model") or current_model

            request_llm = self.engine.llm  # 默认用共享实例
            if (target_model != current_model or
                config.get("api_key") != self.engine.llm.api_key or
                config.get("base_url") != self.engine.llm.base_url):
                # 配置不同 → 本次请求用独立 LLMClient（不影响其他并发请求）
                request_llm = LLMClient(
                    api_key=config["api_key"],
                    base_url=config["base_url"],
                    model=target_model,
                )

            # 使用流式方法
            for event in self.engine.stream_run(
                user_message=request.user_message,
                session_id=request.session_id or "default",
                llm=request_llm,
            ):
                if event["type"] == "token":
                    yield fuxi_pb2.CompletionChunk(
                        content=event["content"],
                        is_final=False,
                        reasoning="",
                    )
                elif event["type"] == "done":
                    yield fuxi_pb2.CompletionChunk(
                        content=event["content"],
                        is_final=True,
                        reasoning="",
                    )
                elif event["type"] == "error":
                    yield fuxi_pb2.CompletionChunk(
                        content=f"Error: {event['content']}",
                        is_final=True,
                        reasoning="",
                    )

        except Exception as e:
            # 同 InvokeTool：服务端记 traceback，客户端只收归一化消息
            logger.error(f"StreamComplete error: {e}", exc_info=True)
            yield fuxi_pb2.CompletionChunk(
                content="Server error",
                is_final=True,
                reasoning="",
            )

    def Heartbeat(self, request, context):
        """心跳"""
        logger.info(f"Heartbeat from session: {request.session_id}")
        return fuxi_pb2.SessionPong(alive=True, timestamp=int(time.time()))


class MemoryServiceServicer(fuxi_pb2_grpc.MemoryServiceServicer):
    """Memory gRPC 服务实现"""

    def __init__(self):
        self.hot_memory = HotMemory()
        self.warm_memory = WarmMemory()
        self.cold_memory = ColdMemory()

    def QueryHot(self, request, context):
        """查询热记忆"""
        result = self.hot_memory.read()
        return fuxi_pb2.HotResult(
            memory_content=result["memory_content"],
            char_count=result["char_count"],
        )

    def QueryWarm(self, request, context):
        """查询温记忆"""
        try:
            limit = min(request.limit, 200) if request.limit else 50
            # 如果有 query 参数，使用搜索功能
            if request.query:
                result = self.warm_memory.search(
                    session_id=request.session_id,
                    query=request.query,
                    limit=limit,
                )
            else:
                result = self.warm_memory.get_recent(request.session_id, limit=limit)

            entries = []
            if result.get("success"):
                for entry in result["entries"]:
                    entries.append(
                        fuxi_pb2.MemoryEntry(
                            id=entry["id"],
                            content=entry["content"],
                            timestamp=int(entry["timestamp"]),
                            channel="warm",
                        )
                    )
            else:
                logger.error(f"QueryWarm error: {result.get('error')}")
            return fuxi_pb2.WarmResult(entries=entries)
        except Exception as e:
            logger.error(f"QueryWarm exception: {e}")
            return fuxi_pb2.WarmResult(entries=[])

    def QueryCold(self, request, context):
        """查询冷记忆"""
        try:
            limit = min(request.limit, 100) if request.limit else 10
            # 如果有 query 参数，使用语义搜索
            if request.query:
                result = self.cold_memory.search_similar(
                    query=request.query,
                    limit=limit,
                )
            else:
                result = self.cold_memory.get_recent(
                    session_id=request.session_id,
                    limit=limit,
                )

            memories = []
            if result.get("success"):
                for entry in result["entries"]:
                    memories.append(
                        fuxi_pb2.SemanticMemory(
                            id=entry["id"],
                            content=entry.get("summary", entry.get("content", "")),
                            similarity=entry.get("similarity", 0.0),
                        )
                    )
            else:
                logger.error(f"QueryCold error: {result.get('error')}")
            return fuxi_pb2.ColdResult(memories=memories)
        except Exception as e:
            logger.error(f"QueryCold exception: {e}")
            return fuxi_pb2.ColdResult(memories=[])

    def PersistMemory(self, request, context):
        """持久化记忆"""
        try:
            if not request.content:
                return fuxi_pb2.PersistResult(
                    success=False,
                    id="",
                    error="content is required",
                )

            if request.memory_type == "hot":
                result = self.hot_memory.append(request.content)
                return fuxi_pb2.PersistResult(
                    success=result.get("success", False),
                    id=result.get("id", "hot"),
                    error=result.get("error", ""),
                )
            elif request.memory_type == "warm":
                if not request.session_id:
                    return fuxi_pb2.PersistResult(
                        success=False,
                        id="",
                        error="session_id is required for warm memory",
                    )
                result = self.warm_memory.add_message(
                    session_id=request.session_id,
                    content=request.content,
                )
                return fuxi_pb2.PersistResult(
                    success=result.get("success", False),
                    id=result.get("id", "warm"),
                    error=result.get("error", ""),
                )
            elif request.memory_type == "cold":
                try:
                    metadata_val = dict(request.metadata) if hasattr(request, 'metadata') and request.metadata else None
                except (TypeError, ValueError, AttributeError):
                    metadata_val = None
                result = self.cold_memory.insert_summary(
                    content=request.content,
                    summary=request.summary or request.content[:200],
                    session_id=request.session_id or "default",
                    metadata=metadata_val,
                )
                return fuxi_pb2.PersistResult(
                    success=result.get("success", False),
                    id=result.get("id", "cold"),
                    error=result.get("error", ""),
                )
            else:
                return fuxi_pb2.PersistResult(
                    success=False,
                    id="",
                    error=f"Unknown memory_type: {request.memory_type}",
                )
        except Exception as e:
            logger.error(f"PersistMemory exception: {e}")
            return fuxi_pb2.PersistResult(
                success=False,
                id="",
                error=str(e),
            )


def serve(port: int = 50051):
    """启动 gRPC 服务（v0.2.0: 集成连接池）

    使用 GrpcConnectionPool 管理服务器生命周期和并发控制：
    - 内置 keepalive/heartbeat 参数
    - Semaphore(100) 并发控制
    - CircuitBreaker 熔断保护
    """
    # 通过连接池创建服务器（获取正确的 keepalive 参数）
    pool = GrpcConnectionPool.get_instance(max_workers=10)
    server = pool.create_server(port=port)

    fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(
        FuxiCoreServicer(), server
    )
    fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(
        MemoryServiceServicer(), server
    )

    # 注册健康检查
    pool.set_health_check(lambda: True)

    pool.start_server()
    logger.info(f"Fuxi gRPC Server started on port {port}, concurrency limit={GrpcConnectionPool.MAX_CONCURRENT_REQUESTS}")

    # 优雅关闭
    def shutdown(signum, frame):
        logger.info("Shutting down gracefully...")
        pool.shutdown(grace=10)
        logger.info("Server stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    port = int(os.environ.get("GRPC_PORT", DEFAULT_PORT))
    serve(port=port)