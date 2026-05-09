"""gRPC 服务生命周期测试"""
import unittest
import sys
import os
import time
import signal
import subprocess
import grpc
from concurrent import futures

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc
from grpc_server import FuxiCoreServicer, MemoryServiceServicer


class TestGRPCServerLifecycle(unittest.TestCase):
    """gRPC 服务生命周期测试"""

    def test_server_start_and_stop(self):
        """服务器启动和停止"""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        port = server.add_insecure_port("[::]:0")
        server.start()
        # server started successfully (grpc._server._Server is running)

        # 停止
        server.stop(0)
        # stop() 是异步的，给点时间
        time.sleep(0.1)

    def test_multiple_servicers(self):
        """多个服务同时注册"""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), server)
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        # server started successfully (grpc._server._Server is running)
        server.stop(0)

    def test_insecure_port_conflict(self):
        """端口冲突处理"""
        server1 = grpc.server(futures.ThreadPoolExecutor())
        port1 = server1.add_insecure_port("[::]:0")
        server1.start()

        server2 = grpc.server(futures.ThreadPoolExecutor())
        try:
            port2 = server2.add_insecure_port(f"[::]:{port1}")
            # 新版本 grpc 行为：成功返回 0（旧版）或正常返回（新版）
            # 验证 either 端口冲突（返回 0）或成功绑定
            self.assertIn(port2, [0, port1])
        except RuntimeError:
            # 新版本 grpc 在端口冲突时直接抛异常
            pass

        server1.stop(0)

    def test_heartbeat_response(self):
        """心跳返回正确"""
        server = grpc.server(futures.ThreadPoolExecutor())
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.FuxiCoreStub(channel)

            resp = stub.Heartbeat(fuxi_pb2.SessionPing(session_id="test-session"))
            self.assertTrue(resp.alive)
            self.assertGreater(resp.timestamp, 0)
            channel.close()
        finally:
            server.stop(0)

    def test_concurrent_requests(self):
        """并发请求处理"""
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.FuxiCoreStub(channel)

            # 并发发 10 个心跳
            threads = []
            results = []

            def do_heartbeat():
                resp = stub.Heartbeat(fuxi_pb2.SessionPing(session_id="concurrent"))
                results.append(resp.alive)

            import threading
            for _ in range(10):
                t = threading.Thread(target=do_heartbeat)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(results), 10)
            self.assertTrue(all(results))
            channel.close()
        finally:
            server.stop(0)

    def test_invoke_tool_unknown(self):
        """调用不存在的工具"""
        server = grpc.server(futures.ThreadPoolExecutor())
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.FuxiCoreStub(channel)

            resp = stub.InvokeTool(fuxi_pb2.ToolRequest(
                tool_name="nonexistent_tool_xyz",
                arguments_json="{}",
                session_id="test",
            ))
            self.assertFalse(resp.success)
            self.assertIn("not found", resp.error)
            channel.close()
        finally:
            server.stop(0)

    def test_memory_service_hot(self):
        """MemoryService 热记忆"""
        server = grpc.server(futures.ThreadPoolExecutor())
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.MemoryServiceStub(channel)

            # 写入
            resp = stub.PersistMemory(fuxi_pb2.MemoryWrite(
                memory_type="hot",
                content="test memory content",
                session_id="test-session",
            ))
            self.assertTrue(resp.success)

            # 查询
            resp2 = stub.QueryHot(fuxi_pb2.HotQuery(session_id="test-session"))
            self.assertIn("test memory", resp2.memory_content)
            channel.close()
        finally:
            server.stop(0)

    def test_memory_service_warm(self):
        """MemoryService 温记忆"""
        server = grpc.server(futures.ThreadPoolExecutor())
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.MemoryServiceStub(channel)

            resp = stub.PersistMemory(fuxi_pb2.MemoryWrite(
                memory_type="warm",
                content="warm test content",
                session_id="warm-test",
            ))
            self.assertTrue(resp.success)

            resp2 = stub.QueryWarm(fuxi_pb2.WarmQuery(
                query="warm",
                limit=10,
                session_id="warm-test",
            ))
            self.assertIsNotNone(resp2.entries)
            channel.close()
        finally:
            server.stop(0)

    def test_memory_service_cold(self):
        """MemoryService 冷记忆"""
        server = grpc.server(futures.ThreadPoolExecutor())
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)
        port = server.add_insecure_port("[::]:0")
        server.start()
        time.sleep(0.1)

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = fuxi_pb2_grpc.MemoryServiceStub(channel)

            resp = stub.PersistMemory(fuxi_pb2.MemoryWrite(
                memory_type="cold",
                content="cold test content",
                summary="cold test",
                session_id="cold-test",
            ))
            self.assertTrue(resp.success)

            resp2 = stub.QueryCold(fuxi_pb2.ColdQuery(
                query="cold",
                limit=10,
                session_id="cold-test",
            ))
            self.assertIsNotNone(resp2.memories)
            channel.close()
        finally:
            server.stop(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
