"""gRPC 集成测试"""
import unittest
import os
import sys
import time
import threading
import grpc
from concurrent import futures

# 设置路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import fuxi_pb2 as fuxi_pb2
import fuxi_pb2_grpc as fuxi_pb2_grpc
from grpc_server import FuxiCoreServicer, MemoryServiceServicer
from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory


class TestGRPCIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """启动 gRPC 服务器"""
        cls.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        cls.hot_memory = HotMemory()
        cls.warm_memory = WarmMemory()
        cls.cold_memory = ColdMemory()

        # 添加服务
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(
            FuxiCoreServicer(), cls.server
        )
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(
            MemoryServiceServicer(), cls.server
        )

        # 监听端口
        cls.port = 50051
        cls.server.add_insecure_port(f'[::]:{cls.port}')
        cls.server.start()

        # 等待服务器启动
        time.sleep(1)

        # 创建客户端
        cls.channel = grpc.insecure_channel(f'localhost:{cls.port}')
        cls.core_stub = fuxi_pb2_grpc.FuxiCoreStub(cls.channel)
        cls.memory_stub = fuxi_pb2_grpc.MemoryServiceStub(cls.channel)

    @classmethod
    def tearDownClass(cls):
        """停止 gRPC 服务器"""
        cls.channel.close()
        cls.server.stop(0)
        # 移除 close 调用，因为 HotMemory 没有 close 方法
        # cls.hot_memory.close()
        # cls.warm_memory.close()
        # cls.cold_memory.close()
        time.sleep(0.5)

    def test_heartbeat(self):
        """测试心跳功能"""
        response = self.core_stub.Heartbeat(
            fuxi_pb2.SessionPing(session_id="test"),
            timeout=5
        )
        self.assertTrue(response.alive)
        self.assertGreater(response.timestamp, 0)

    def test_memory_operations(self):
        """测试记忆操作"""
        hot_response = self.memory_stub.QueryHot(
            fuxi_pb2.HotQuery(session_id="test"),
            timeout=5
        )
        self.assertIsInstance(hot_response.memory_content, str)

        self.memory_stub.PersistMemory(
            fuxi_pb2.MemoryWrite(
                memory_type="warm",
                content="test message",
                session_id="test"
            ),
            timeout=5
        )

        warm_response = self.memory_stub.QueryWarm(
            fuxi_pb2.WarmQuery(query="test", limit=10, session_id="test"),
            timeout=5
        )
        self.assertGreaterEqual(len(warm_response.entries), 1)

        self.memory_stub.PersistMemory(
            fuxi_pb2.MemoryWrite(
                memory_type="cold",
                content="test summary",
                session_id="test"
            ),
            timeout=5
        )

        cold_response = self.memory_stub.QueryCold(
            fuxi_pb2.ColdQuery(query="test", limit=10),
            timeout=5
        )
        self.assertGreaterEqual(len(cold_response.memories), 1)

    def test_concurrent_memory_operations(self):
        """测试并发记忆操作"""
        def persist_memory(i):
            self.memory_stub.PersistMemory(
                fuxi_pb2.MemoryWrite(
                    memory_type="warm",
                    content=f"concurrent message {i}",
                    session_id="concurrent_test"
                ),
                timeout=5
            )

        # 启动多个线程
        threads = []
        for i in range(10):
            t = threading.Thread(target=persist_memory, args=(i,))
            threads.append(t)
            t.start()

        # 等待所有线程完成
        for t in threads:
            t.join()

        # 验证所有消息都被持久化
        response = self.memory_stub.QueryWarm(
            fuxi_pb2.WarmQuery(query="concurrent", limit=50, session_id="concurrent_test"),
            timeout=5
        )
        # 由于并发测试可能有残留数据，只检查是否至少有10条（我们写入的数量）
        self.assertGreaterEqual(len(response.entries), 10)


if __name__ == '__main__':
    unittest.main()
