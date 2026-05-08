"""伏羲 gRPC 服务端"""
import grpc
from concurrent import futures
import time
import os
import sys
import signal

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
except ImportError:
    pass

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

FuxiEngine = fuxi_engine_module.FuxiEngine

# 从环境变量加载配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "")

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

    def _get_metadata(self, context) -> dict:
        """从 gRPC metadata 中提取认证和配置信息"""
        metadata_dict = dict(context.invocation_metadata())
        return {
            "api_key": metadata_dict.get("authorization", "").replace("Bearer ", ""),
            "base_url": metadata_dict.get("base-url", ""),
            "model": metadata_dict.get("model", ""),
            "history": metadata_dict.get("history", ""),
        }

    def _check_auth(self, context, metadata: dict) -> bool:
        """验证 API key"""
        if not DEEPSEEK_API_KEY:
            return True  # 未配置 key，不验证
        provided_key = metadata.get("api_key", "")
        if not provided_key:
            # 没从 metadata 传 key？用 engine 自己配置的 key（允许直连）
            return True
        return provided_key == DEEPSEEK_API_KEY

    def InvokeTool(self, request, context):
        """调用工具"""
        metadata = self._get_metadata(context)

        # 验证
        if not self._check_auth(context, metadata):
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("Invalid API key")
            return fuxi_pb2.ToolResult()

        # 动态设置 base_url（用户可自定义）
        if metadata.get("base_url"):
            self.engine.llm.base_url = metadata["base_url"]
        if metadata.get("api_key"):
            self.engine.llm.api_key = metadata["api_key"]

        # 设置超时
        timeout_ms = context.time_remaining() * 1000 if context.time_remaining() else DEFAULT_TIMEOUT

        result = self.engine.tool_registry.invoke(
            request.tool_name, request.arguments_json
        )
        return fuxi_pb2.ToolResult(
            success=result["success"],
            result_json=result.get("result_json", "{}"),
            error=result.get("error", ""),
            elapsed_ms=result.get("elapsed_ms", 0),
        )

    def StreamComplete(self, request, context):
        """流式回复"""
        import json
        metadata = self._get_metadata(context)

        print(f"[StreamComplete] metadata: {metadata}")
        print(f"[StreamComplete] request.user_message: {request.user_message}")
        print(f"[StreamComplete] request.model: {request.model}")
        print(f"[StreamComplete] request.session_id: {request.session_id}")

        if not self._check_auth(context, metadata):
            context.set_code(grpc.StatusCode.UNAUTHENTICATED)
            context.set_details("Invalid API key")
            yield fuxi_pb2.CompletionChunk(
                content="Authentication failed: Invalid API key",
                is_final=True,
                reasoning="",
            )
            return

        if metadata.get("base_url"):
            self.engine.llm.base_url = metadata["base_url"]
        if metadata.get("api_key"):
            self.engine.llm.api_key = metadata["api_key"]
        model_override = metadata.get("model") or request.model
        if model_override:
            self.engine.llm.model = model_override

        history = None
        if metadata.get("history"):
            try:
                history = json.loads(metadata["history"])
            except json.JSONDecodeError:
                pass

        run_result = self.engine.run(
            user_message=request.user_message,
            session_id=request.session_id or "default",
            history=history,
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

    def Heartbeat(self, request, context):
        """心跳"""
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
        limit = min(request.limit, 200) if request.limit else 50
        query = getattr(request, 'query', '') or ''
        if query:
            result = self.warm_memory.search(request.session_id, query, limit=limit)
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
        return fuxi_pb2.WarmResult(entries=entries)

    def QueryCold(self, request, context):
        """查询冷记忆"""
        limit = min(request.limit, 100) if request.limit else 10
        query = getattr(request, 'query', '') or ''
        session_id = getattr(request, 'session_id', '') or None
        if query:
            result = self.cold_memory.search_similar(query, limit=limit, session_id=session_id)
        else:
            result = self.cold_memory.get_recent(session_id=session_id, limit=limit)
        memories = []
        if result.get("success"):
            for entry in result["entries"]:
                memories.append(
                    fuxi_pb2.SemanticMemory(
                        id=entry["id"],
                        content=entry["summary"],
                        similarity=float(entry.get("similarity", 0.0)),
                    )
                )
        return fuxi_pb2.ColdResult(memories=memories)

    def PersistMemory(self, request, context):
        """持久化记忆"""
        if request.memory_type == "hot":
            result = self.hot_memory.write(request.content)
            return fuxi_pb2.PersistResult(
                success=result.get("success", False),
                id=result.get("id", "hot"),
                error=result.get("error", ""),
            )
        elif request.memory_type == "warm":
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
                metadata=dict(request.metadata.items()) if request.metadata else None,
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


def serve(port: int = 50051):
    """启动 gRPC 服务"""
    # Debug: 打印环境变量
    print(f"DEEPSEEK_API_KEY: {'SET' if os.environ.get('DEEPSEEK_API_KEY') else 'EMPTY'}")
    print(f"DEEPSEEK_BASE_URL: {os.environ.get('DEEPSEEK_BASE_URL', 'EMPTY')}")
    print(f"DEFAULT_MODEL: {os.environ.get('DEFAULT_MODEL', 'EMPTY')}")

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
    print(f"Fuxi gRPC Server started on port {port}")

    # 优雅关闭
    def shutdown(signum, frame):
        print("\nShutting down gracefully...")
        server.stop(grace=10).wait()
        print("Server stopped.")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    serve()