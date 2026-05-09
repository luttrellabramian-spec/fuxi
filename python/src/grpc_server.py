"""伏羲 gRPC 服务端"""
import grpc
from concurrent import futures
import time
import os
import sys
import signal
import logging
import copy

# 当前目录是 python/src
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'proto/generated/python'))
sys.path.insert(0, CURRENT_DIR)

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc

# 预先导入 tools 模块以触发注册
import tools
import tools.file_tools

import engine.fuxi_engine as fuxi_engine_module
from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory
from llm.client import DeepSeekClient

FuxiEngine = fuxi_engine_module.FuxiEngine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('fuxi_grpc')

# 从环境变量加载配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "")

# 默认超时配置（毫秒）
DEFAULT_PORT = 50051
DEFAULT_TIMEOUT = 30000


class FuxiCoreServicer(fuxi_pb2_grpc.FuxiCoreServicer):
    """Fuxi gRPC 服务实现"""

    def __init__(self):
        self.engine = FuxiEngine(
            deepseek_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        # 保存默认配置，用于每个请求的副本
        self._default_api_key = DEEPSEEK_API_KEY
        self._default_base_url = DEEPSEEK_BASE_URL
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
        if not self._default_api_key:
            return True  # 未配置 key，不验证
        provided_key = metadata.get("api_key", "")
        if not provided_key:
            # 配置了 key 但客户端没提供，拒绝
            return False
        # 使用 hmac.compare_digest 防止时序攻击
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
            return fuxi_pb2.ToolResult(
                success=result["success"],
                result_json=result.get("result_json", "{}"),
                error=result.get("error", ""),
                elapsed_ms=result.get("elapsed_ms", 0),
            )
        except Exception as e:
            logger.error(f"InvokeTool error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal error: {str(e)}")
            return fuxi_pb2.ToolResult(success=False, error=str(e))

    def StreamComplete(self, request, context):
        """流式回复"""
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

            # 获取配置（不修改共享状态）
            config = self._get_client_config(metadata, request.model)

            # 创建独立的 LLM 客户端（线程安全）
            llm_client = DeepSeekClient(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config["model"],
            )

            # 创建临时引擎实例（使用独立的 LLM 客户端）
            temp_engine = FuxiEngine(
                deepseek_key=config["api_key"],
                base_url=config["base_url"],
            )
            # 替换 LLM 客户端
            temp_engine.deepseek = llm_client

            # 使用 ReAct 引擎处理（含工具调用）
            run_result = temp_engine.run(
                user_message=request.user_message,
                session_id=request.session_id or "default",
            )

            if run_result.get("success"):
                yield fuxi_pb2.CompletionChunk(
                    content=run_result.get("content", ""),
                    is_final=True,
                    reasoning="",
                )
            else:
                yield fuxi_pb2.CompletionChunk(
                    content=f"Error: {run_result.get('error', 'Unknown')}",
                    is_final=True,
                    reasoning="",
                )
        except Exception as e:
            logger.error(f"StreamComplete error: {e}")
            yield fuxi_pb2.CompletionChunk(
                content=f"Server error: {str(e)}",
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
                result = self.cold_memory.insert_summary(
                    content=request.content,
                    summary=request.summary or request.content[:200],
                    session_id=request.session_id or "default",
                    metadata=dict(request.metadata) if request.metadata else None,
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
    """启动 gRPC 服务"""
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_receive_message_length", 10 * 1024 * 1024),  # 10MB
            ("grpc.max_send_message_length", 10 * 1024 * 1024),
        ],
    )
    fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(
        FuxiCoreServicer(), server
    )
    fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(
        MemoryServiceServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info(f"Fuxi gRPC Server started on port {port}")

    # 优雅关闭
    def shutdown(signum, frame):
        logger.info("Shutting down gracefully...")
        server.stop(grace=10).wait()
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