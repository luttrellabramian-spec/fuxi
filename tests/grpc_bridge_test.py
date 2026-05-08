"""gRPC 桥接延迟测试

验证指标：
- gRPC 工具调用延迟 < 200ms（目标）
- 多次调用稳定性
"""
import unittest
import os
import sys
import time
import tempfile
import grpc
from concurrent import futures

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc
from grpc_server import FuxiCoreServicer, MemoryServiceServicer


class GRPCBridgeTest(unittest.TestCase):
    """gRPC 桥接延迟测试"""

    @classmethod
    def setUpClass(cls):
        """启动 gRPC 测试服务器"""
        cls.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

        # 服务端自持内存实例
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(
            FuxiCoreServicer(), cls.server
        )
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(
            MemoryServiceServicer(), cls.server
        )

        cls.port = cls.server.add_insecure_port("[::]:0")
        cls.server.start()
        time.sleep(0.2)

        # 创建客户端
        cls.channel = grpc.insecure_channel(f"localhost:{cls.port}")
        cls.stub = fuxi_pb2_grpc.FuxiCoreStub(cls.channel)
        cls.memory_stub = fuxi_pb2_grpc.MemoryServiceStub(cls.channel)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(0)
        cls.channel.close()

    def test_heartbeat_latency(self):
        """心跳延迟（基准测试）"""
        latencies = []
        for _ in range(20):
            start = time.time()
            self.stub.Heartbeat(fuxi_pb2.SessionPing(session_id="test"))
            latencies.append((time.time() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        p50 = sorted(latencies)[len(latencies) // 2]
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        print(f"\n[Heartbeat] avg={avg:.1f}ms p50={p50:.1f}ms p99={p99:.1f}ms")

        self.assertLess(avg, 50, f"Heartbeat avg latency {avg:.1f}ms should be < 50ms")

    def test_tool_call_latency(self):
        """工具调用延迟（核心指标，目标 < 200ms）"""
        latencies = []
        for _ in range(20):
            req = fuxi_pb2.ToolRequest(
                tool_name="file_exists",
                arguments_json='{"path": "."}',
                session_id="test",
            )
            start = time.time()
            self.stub.InvokeTool(req)
            latencies.append((time.time() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        p50 = sorted(latencies)[len(latencies) // 2]
        p99 = sorted(latencies)[int(len(latencies) * 0.99)]
        print(f"\n[InvokeTool] avg={avg:.1f}ms p50={p50:.1f}ms p99={p99:.1f}ms")

        self.assertLess(avg, 200, f"Tool call avg latency {avg:.1f}ms should be < 200ms (MVP target)")

    def test_memory_query_latency(self):
        """记忆查询延迟（目标 < 100ms）"""
        latencies = []
        for _ in range(20):
            start = time.time()
            self.memory_stub.QueryHot(fuxi_pb2.HotQuery(session_id="test"))
            latencies.append((time.time() - start) * 1000)

        avg = sum(latencies) / len(latencies)
        print(f"\n[QueryHot] avg={avg:.1f}ms")
        self.assertLess(avg, 100, f"Memory query avg {avg:.1f}ms should be < 100ms")

    def test_stream_latency(self):
        """流式补全首字节延迟（跳过，无 API key）"""
        print("\n[StreamComplete] skipped (requires API key)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
