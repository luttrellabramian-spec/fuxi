"""HTTP 网关 API 测试"""
import unittest
import sys
import os
import json
import time
import threading
import requests
from http.server import HTTPServer
import socketserver

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc
from grpc_server import FuxiCoreServicer, MemoryServiceServicer
import grpc
from concurrent import futures


class TestGatewayAPI(unittest.TestCase):
    """HTTP 网关 API 测试（需要 gRPC 服务运行）"""

    @classmethod
    def setUpClass(cls):
        """启动测试 gRPC 服务"""
        cls.grpc_port = 51051  # 固定端口避免冲突
        cls.http_port = 18790

        cls.server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), cls.server)
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), cls.server)
        cls.server.add_insecure_port(f"[::]:{cls.grpc_port}")
        cls.server.start()
        time.sleep(0.3)

        # 设置环境变量供 gateway 使用
        os.environ["GRPC_HOST"] = "localhost"
        os.environ["GRPC_PORT"] = str(cls.grpc_port)
        os.environ["HTTP_PORT"] = str(cls.http_port)
        os.environ["DEEPSEEK_API_KEY"] = ""
        os.environ["AUTH_ENABLED"] = "false"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(0)

    def test_grpc_client_import(self):
        """gRPC 客户端可导入"""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "typescript", "src"))
        try:
            import grpc_client
            self.assertIsNotNone(grpc_client.grpcClient)
            self.assertIsNotNone(grpc_client.memoryClient)
        except ImportError as e:
            self.skipTest(f"TypeScript modules not importable from Python: {e}")

    def test_gateway_config(self):
        """网关配置正确（读取源码确认）"""
        ts_config_path = os.path.join(
            os.path.dirname(__file__), "..", "typescript", "src", "config.ts"
        )
        self.assertTrue(os.path.exists(ts_config_path))
        with open(ts_config_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("grpcHost", content)
        self.assertIn("grpcPort", content)
        self.assertIn("httpPort", content)
        self.assertIn('"50051"', content)
        self.assertIn('"18789"', content)

    def test_proto_services_defined(self):
        """Proto 服务正确定义"""
        self.assertTrue(hasattr(fuxi_pb2_grpc, "FuxiCoreStub"))
        self.assertTrue(hasattr(fuxi_pb2_grpc, "MemoryServiceStub"))
        self.assertTrue(hasattr(fuxi_pb2_grpc, "add_FuxiCoreServicer_to_server"))
        self.assertTrue(hasattr(fuxi_pb2_grpc, "add_MemoryServiceServicer_to_server"))

    def test_proto_messages(self):
        """Proto 消息定义完整"""
        # CompletionRequest
        req = fuxi_pb2.CompletionRequest(
            session_id="s1",
            user_message="hello",
            model="test-model",
            max_tokens=100,
        )
        self.assertEqual(req.session_id, "s1")
        self.assertEqual(req.user_message, "hello")

        # ToolRequest
        req2 = fuxi_pb2.ToolRequest(
            tool_name="test_tool",
            arguments_json='{"key": "value"}',
            session_id="s2",
        )
        self.assertEqual(req2.tool_name, "test_tool")

        # MemoryWrite - 包含 summary 字段
        req3 = fuxi_pb2.MemoryWrite(
            memory_type="cold",
            content="long content",
            summary="short summary",
            session_id="s3",
        )
        self.assertEqual(req3.summary, "short summary")

        # SessionPing
        pong = fuxi_pb2.SessionPong(alive=True, timestamp=1234567890)
        self.assertTrue(pong.alive)
        self.assertEqual(pong.timestamp, 1234567890)

    def test_tool_result_serialization(self):
        """ToolResult 序列化"""
        result = fuxi_pb2.ToolResult(
            success=True,
            result_json='{"key": "value"}',
            error="",
            elapsed_ms=10,
        )
        # 可以序列化
        serialized = result.SerializeToString()
        self.assertIsInstance(serialized, bytes)
        # 可以反序列化
        restored = fuxi_pb2.ToolResult()
        restored.ParseFromString(serialized)
        self.assertEqual(restored.success, True)
        self.assertEqual(restored.result_json, '{"key": "value"}')

    def test_memory_result_serialization(self):
        """Memory 消息序列化"""
        cold_result = fuxi_pb2.ColdResult(memories=[
            fuxi_pb2.SemanticMemory(id="1", content="test", similarity=0.95)
        ])
        serialized = cold_result.SerializeToString()
        self.assertIsInstance(serialized, bytes)

        warm_result = fuxi_pb2.WarmResult(entries=[
            fuxi_pb2.MemoryEntry(id="1", content="msg", timestamp=12345, channel="cli")
        ])
        warm_serialized = warm_result.SerializeToString()
        self.assertIsInstance(warm_serialized, bytes)

    def test_chunk_serialization(self):
        """CompletionChunk 序列化"""
        chunk = fuxi_pb2.CompletionChunk(
            content="hello",
            is_final=False,
            reasoning="thinking...",
        )
        serialized = chunk.SerializeToString()
        restored = fuxi_pb2.CompletionChunk()
        restored.ParseFromString(serialized)
        self.assertEqual(restored.content, "hello")
        self.assertEqual(restored.reasoning, "thinking...")
        self.assertFalse(restored.is_final)

    def test_persist_result(self):
        """PersistResult"""
        result = fuxi_pb2.PersistResult(
            success=True,
            id="mem-123",
            error="",
        )
        serialized = result.SerializeToString()
        restored = fuxi_pb2.PersistResult()
        restored.ParseFromString(serialized)
        self.assertTrue(restored.success)
        self.assertEqual(restored.id, "mem-123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
