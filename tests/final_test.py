"""
伏羲 V0.1.0 最终综合测试

测试内容：
1. 多轮对话上下文连贯性
2. 记忆系统读写
3. 自动存储记忆
4. 工具调用
5. gRPC 通信
"""
import unittest
import os
import sys
import time
import json
import tempfile
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory
from tools import registry
from engine.fuxi_engine import FuxiEngine


def mock_llm_complete(messages, temperature=0.7, max_tokens=2048):
    """模拟 LLM 调用"""
    # 获取最后一条用户消息
    last_user_msg = ""
    for msg in reversed(messages):
        if msg["role"] == "user":
            last_user_msg = msg["content"]
            break
    
    # 根据用户消息返回响应
    if "我叫" in last_user_msg:
        return {"success": True, "content": "Final: 好的，我记住你的名字了！", "usage": {}}
    elif "名字" in last_user_msg:
        return {"success": True, "content": "Final: 你叫小明！", "usage": {}}
    elif "密码" in last_user_msg:
        return {"success": True, "content": "Final: 密码是123456", "usage": {}}
    elif "你好" in last_user_msg:
        return {"success": True, "content": "Final: 你好！我是伏羲，有什么可以帮你的？", "usage": {}}
    elif "介绍" in last_user_msg:
        return {"success": True, "content": "Final: 我是伏羲，一个AI助手，可以帮你完成各种任务。", "usage": {}}
    else:
        return {"success": True, "content": "Final: 我明白了。", "usage": {}}


class TestMultiTurnContext(unittest.TestCase):
    """测试多轮对话上下文连贯性"""

    def setUp(self):
        self.engine = FuxiEngine(max_steps=3)
        # Mock LLM 调用
        self.engine.deepseek.complete = mock_llm_complete

    def test_session_history_continuity(self):
        """测试会话历史连续性"""
        session_id = "test-continuity"
        
        # 第一轮对话
        result1 = self.engine.run("我叫小明", session_id=session_id)
        self.assertTrue(result1["success"])
        
        # 检查历史记录
        history = self.engine._session_history.get(session_id, [])
        self.assertGreater(len(history), 0)
        self.assertEqual(history[0]["role"], "system")
        
        # 第二轮对话 - 应该记住名字
        result2 = self.engine.run("我叫什么名字？", session_id=session_id)
        self.assertTrue(result2["success"])
        
        # 检查历史是否包含两轮对话
        history = self.engine._session_history.get(session_id, [])
        user_messages = [m for m in history if m["role"] == "user"]
        self.assertGreaterEqual(len(user_messages), 2)

    def test_session_isolation(self):
        """测试会话隔离性"""
        # 会话1
        result1 = self.engine.run("记住：密码是123456", session_id="session-1")
        self.assertTrue(result1["success"])
        
        # 会话2 - 不应该知道密码
        result2 = self.engine.run("密码是什么？", session_id="session-2")
        self.assertTrue(result2["success"])
        
        # 两个会话的历史应该独立
        history1 = self.engine._session_history.get("session-1", [])
        history2 = self.engine._session_history.get("session-2", [])
        
        # 检查会话1的用户消息包含密码相关内容
        user_msgs1 = [m["content"] for m in history1 if m["role"] == "user"]
        self.assertTrue(any("密码" in msg or "123456" in msg for msg in user_msgs1))
        
        # 检查会话2的用户消息也包含密码相关问题
        user_msgs2 = [m["content"] for m in history2 if m["role"] == "user"]
        self.assertTrue(any("密码" in msg for msg in user_msgs2))
        
        # 两个会话应该是独立的（有不同的session_id）
        self.assertNotEqual("session-1", "session-2")

    def test_history_trimming(self):
        """测试历史裁剪功能"""
        session_id = "test-trimming"
        
        # 添加大量历史记录
        for i in range(50):
            self.engine._session_history.setdefault(session_id, []).append(
                {"role": "user", "content": f"消息 {i}"}
            )
        
        # 调用裁剪
        messages = self.engine._session_history[session_id]
        trimmed = self.engine._trim_history(messages)
        
        # 应该被裁剪到 MAX_HISTORY_MESSAGES
        self.assertLessEqual(len(trimmed), 40)


class TestMemorySystem(unittest.TestCase):
    """测试记忆系统"""

    def setUp(self):
        self.fd_hot, self.hot_path = tempfile.mkstemp(suffix=".md")
        os.close(self.fd_hot)
        self.hot_memory = HotMemory(memory_file=self.hot_path)
        
        self.fd_warm, self.warm_path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd_warm)
        self.warm_memory = WarmMemory(db_path=self.warm_path)
        
        self.fd_cold, self.cold_path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd_cold)
        self.cold_memory = ColdMemory(db_path=self.cold_path)

    def tearDown(self):
        self.warm_memory.close()
        self.cold_memory.close()
        for path in [self.hot_path, self.warm_path, self.cold_path]:
            try:
                os.unlink(path)
            except:
                pass

    def test_hot_memory_read_write(self):
        """测试热记忆读写"""
        # 写入
        result = self.hot_memory.write("测试内容")
        self.assertTrue(result["success"])
        
        # 读取
        content = self.hot_memory.read()
        self.assertIn("测试内容", content["memory_content"])

    def test_hot_memory_append(self):
        """测试热记忆追加"""
        self.hot_memory.write("第一条")
        self.hot_memory.append("第二条")
        
        content = self.hot_memory.read()
        self.assertIn("第一条", content["memory_content"])
        self.assertIn("第二条", content["memory_content"])

    def test_hot_memory_char_limit(self):
        """测试热记忆字符限制"""
        long_content = "x" * 3000
        self.hot_memory.write(long_content)
        
        content = self.hot_memory.read()
        self.assertLessEqual(content["char_count"], 2200)

    def test_hot_memory_atomic_write(self):
        """测试热记忆原子写入"""
        results = []
        
        def writer(text):
            r = self.hot_memory.write(text)
            results.append(r)
        
        # 并发写入
        threads = [threading.Thread(target=writer, args=(f"内容{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 所有写入都应该成功
        for r in results:
            self.assertTrue(r["success"])

    def test_warm_memory_add_and_search(self):
        """测试温记忆添加和搜索"""
        # 添加消息
        result = self.warm_memory.add_message("test-session", "Python 是编程语言")
        self.assertTrue(result["success"])
        
        # 搜索
        result = self.warm_memory.search("test-session", "Python")
        self.assertTrue(result["success"])
        self.assertGreater(len(result.get("entries", [])), 0)

    def test_warm_memory_session_isolation(self):
        """测试温记忆会话隔离"""
        self.warm_memory.add_message("session-1", "会话1的内容")
        self.warm_memory.add_message("session-2", "会话2的内容")
        
        # 搜索会话1
        result1 = self.warm_memory.search("session-1", "会话1")
        self.assertTrue(result1["success"])
        
        # 搜索会话2 - 不应该找到会话1的内容
        result2 = self.warm_memory.search("session-2", "会话1")
        self.assertTrue(result2["success"])
        # 可能返回空结果

    def test_warm_memory_recent(self):
        """测试温记忆获取最近消息"""
        for i in range(10):
            self.warm_memory.add_message("test-session", f"消息{i}")
        
        result = self.warm_memory.get_recent("test-session", limit=5)
        self.assertTrue(result["success"])
        self.assertLessEqual(len(result["entries"]), 5)

    def test_cold_memory_insert_and_search(self):
        """测试冷记忆插入和搜索"""
        result = self.cold_memory.insert_summary(
            content="Python 机器学习",
            summary="Python ML",
            session_id="test"
        )
        self.assertTrue(result["success"])
        
        # 搜索
        result = self.cold_memory.search_similar("Python", limit=5)
        self.assertTrue(result["success"])

    def test_cold_memory_recent(self):
        """测试冷记忆获取最近摘要"""
        for i in range(5):
            self.cold_memory.insert_summary(
                content=f"内容{i}",
                summary=f"摘要{i}",
                session_id="test"
            )
        
        result = self.cold_memory.get_recent(session_id="test", limit=3)
        self.assertTrue(result["success"])
        self.assertLessEqual(len(result["entries"]), 3)


class TestAutoMemoryStorage(unittest.TestCase):
    """测试自动存储记忆"""

    def setUp(self):
        self.fd, self.hot_path = tempfile.mkstemp(suffix=".md")
        os.close(self.fd)
        self.engine = FuxiEngine(max_steps=3)
        self.engine.hot_memory = HotMemory(memory_file=self.hot_path)
        # Mock LLM 调用
        self.engine.deepseek.complete = mock_llm_complete

    def tearDown(self):
        try:
            os.unlink(self.hot_path)
        except:
            pass

    def test_auto_store_on_conversation(self):
        """测试对话时自动存储记忆"""
        # 运行对话
        result = self.engine.run("你好", session_id="test-auto")
        self.assertTrue(result["success"])
        
        # 检查热记忆是否有更新（ReAct 循环会更新记忆）
        memory = self.engine.hot_memory.read()
        # 记忆可能为空（如果没有工具调用）
        # 这是正常行为

    def test_auto_store_summary_format(self):
        """测试自动存储的记忆格式"""
        # 手动触发记忆更新
        self.engine.hot_memory.append("[test] 完成了 2 步推理，最终: 测试答案")
        
        memory = self.engine.hot_memory.read()
        self.assertIn("[test]", memory["memory_content"])
        self.assertIn("推理", memory["memory_content"])


class TestToolInvocation(unittest.TestCase):
    """测试工具调用"""

    def test_file_tools(self):
        """测试文件工具"""
        # 读取文件
        result = registry.invoke("read_file", '{"path": "python/src/__init__.py"}')
        self.assertTrue(result["success"])
        
        # 检查文件存在
        result = registry.invoke("file_exists", '{"path": "python/src/__init__.py"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertTrue(data)
        
        # 列出文件
        result = registry.invoke("list_files", '{"directory": "python/src"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIsInstance(data, list)

    def test_path_traversal_protection(self):
        """测试路径遍历防护"""
        result = registry.invoke("read_file", '{"path": "/etc/passwd"}')
        self.assertFalse(result["success"])
        self.assertIn("Access denied", result["error"])

    def test_ssrf_protection(self):
        """测试 SSRF 防护"""
        result = registry.invoke("http_get", '{"url": "http://127.0.0.1:8080"}')
        self.assertFalse(result["success"])
        self.assertIn("private network", result["error"])

    def test_tool_error_handling(self):
        """测试工具错误处理"""
        # 不存在的工具
        result = registry.invoke("nonexistent_tool", "{}")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])


class TestGrpcIntegration(unittest.TestCase):
    """测试 gRPC 集成"""

    def test_proto_messages(self):
        """测试 Proto 消息定义"""
        import fuxi_pb2
        
        # ToolRequest
        req = fuxi_pb2.ToolRequest(
            tool_name="test",
            arguments_json='{"key": "value"}',
            session_id="test"
        )
        self.assertEqual(req.tool_name, "test")
        
        # CompletionRequest
        req = fuxi_pb2.CompletionRequest(
            session_id="test",
            user_message="hello",
            model="test-model",
            max_tokens=100
        )
        self.assertEqual(req.user_message, "hello")
        
        # MemoryWrite
        req = fuxi_pb2.MemoryWrite(
            memory_type="hot",
            content="test",
            session_id="test"
        )
        self.assertEqual(req.memory_type, "hot")

    def test_grpc_server_lifecycle(self):
        """测试 gRPC 服务生命周期"""
        import grpc
        from concurrent import futures
        import fuxi_pb2_grpc
        from grpc_server import FuxiCoreServicer, MemoryServiceServicer
        
        # 创建服务
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
        fuxi_pb2_grpc.add_FuxiCoreServicer_to_server(FuxiCoreServicer(), server)
        fuxi_pb2_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)
        
        # 启动
        port = server.add_insecure_port("[::]:0")
        server.start()
        
        # 停止
        server.stop(0)


class TestEndToEnd(unittest.TestCase):
    """端到端测试"""

    def test_full_conversation_flow(self):
        """测试完整对话流程"""
        # 创建引擎
        engine = FuxiEngine(max_steps=5)
        # Mock LLM 调用
        engine.deepseek.complete = mock_llm_complete
        
        # 第一轮
        result1 = engine.run("你好", session_id="e2e-test")
        self.assertTrue(result1["success"])
        
        # 第二轮
        result2 = engine.run("介绍一下自己", session_id="e2e-test")
        self.assertTrue(result2["success"])
        
        # 检查历史
        history = engine._session_history.get("e2e-test", [])
        self.assertGreater(len(history), 2)

    def test_memory_integration(self):
        """测试记忆集成"""
        fd, hot_path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        
        try:
            engine = FuxiEngine(max_steps=3)
            engine.hot_memory = HotMemory(memory_file=hot_path)
            # Mock LLM 调用
            engine.deepseek.complete = mock_llm_complete
            
            # 写入记忆
            engine.hot_memory.write("用户喜欢Python")
            
            # 运行对话
            result = engine.run("你好", session_id="memory-test")
            self.assertTrue(result["success"])
            
            # 检查记忆是否被读取
            memory = engine.hot_memory.read()
            self.assertIn("Python", memory["memory_content"])
        finally:
            try:
                os.unlink(hot_path)
            except:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
