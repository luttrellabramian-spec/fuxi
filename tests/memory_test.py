"""记忆读写测试

验证指标：
- 热记忆读写正确性 100%
- 三层记忆基本操作可用
"""
import unittest
import os
import sys
import tempfile
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory


class TestHotMemory(unittest.TestCase):
    """热记忆读写测试（MEMORY.md）"""

    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".md")
        os.close(self.fd)
        self.memory = HotMemory(memory_file=self.path)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_write_and_read(self):
        """写入后能正确读取"""
        self.memory.write("test content")
        result = self.memory.read()
        self.assertIn("memory_content", result)
        self.assertIn("test content", result["memory_content"])

    def test_concurrent_write(self):
        """并发写入安全（RLock 保护）"""
        results = []

        def writer(content):
            r = self.memory.write(content)
            results.append(r)

        threads = [threading.Thread(target=writer, args=(f"content-{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for r in results:
            self.assertTrue(r["success"], f"Concurrent write failed: {r}")

    def test_char_limit(self):
        """2200 字符硬限制"""
        long_content = "x" * 3000
        self.memory.write(long_content)
        result = self.memory.read()
        self.assertLessEqual(result["char_count"], 2200)

    def test_append(self):
        """追加写入"""
        self.memory.write("first entry")
        self.memory.append("second entry")
        result = self.memory.read()
        self.assertIn("first entry", result["memory_content"])
        self.assertIn("second entry", result["memory_content"])


class TestWarmMemory(unittest.TestCase):
    """温记忆测试（SQLite FTS5）"""

    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.memory = WarmMemory(db_path=self.path)

    def tearDown(self):
        self.memory.close()
        try:
            os.unlink(self.path)
        except Exception:
            pass

    def test_add_and_search(self):
        """添加后能搜索到"""
        self.memory.add_message(session_id="warm-test", content="Python 是一门编程语言")
        time.sleep(0.01)
        self.memory.add_message(session_id="warm-test", content="JavaScript 用于 Web 开发")

        result = self.memory.search(session_id="warm-test", query="Python", limit=10)
        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result.get("entries", [])), 1)

    def test_get_recent(self):
        """获取最近消息"""
        for i in range(5):
            self.memory.add_message(session_id="recent-test", content=f"message-{i}")

        result = self.memory.get_recent(session_id="recent-test", limit=3)
        self.assertLessEqual(len(result.get("entries", [])), 3)
        self.assertTrue(result["success"])

    def test_clear_session(self):
        """清空会话记忆"""
        self.memory.add_message(session_id="clear-test", content="data")
        self.memory.clear_session("clear-test")
        result = self.memory.get_recent(session_id="clear-test", limit=50)
        self.assertEqual(len(result.get("entries", [])), 0)


class TestColdMemory(unittest.TestCase):
    """冷记忆测试（向量搜索）"""

    def setUp(self):
        self.fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.memory = ColdMemory(db_path=self.db)

    def tearDown(self):
        self.memory.close()
        try:
            os.unlink(self.db)
        except Exception:
            pass

    def test_insert_and_search(self):
        """插入摘要后能检索到"""
        self.memory.insert_summary(
            content="Python 机器学习框架 PyTorch 发布了新版本",
            summary="PyTorch 新版本发布",
            session_id="cold-test",
        )

        result = self.memory.search_similar("PyTorch", session_id="cold-test", limit=5)
        self.assertTrue(result["success"])

    def test_get_recent(self):
        """获取最近摘要"""
        for i in range(3):
            self.memory.insert_summary(
                content=f"内容 {i}",
                summary=f"摘要 {i}",
                session_id="cold-recent-test",
            )
            time.sleep(0.01)

        result = self.memory.get_recent(session_id="cold-recent-test", limit=2)
        self.assertTrue(result["success"])
        self.assertLessEqual(len(result.get("entries", [])), 2)

    def test_fallback_without_embedding(self):
        """无 embedding 模型时降级为纯文本搜索"""
        memory_no_emb = ColdMemory(db_path=self.db + ".noemb")
        memory_no_emb._has_embedding = False
        memory_no_emb._model = None

        result = memory_no_emb.insert_summary(
            content="test content about programming",
            summary="programming summary",
            session_id="fallback-test",
        )
        self.assertTrue(result["success"])

        result = memory_no_emb.search_similar("programming", session_id="fallback-test")
        self.assertTrue(result["success"])

        memory_no_emb.close()
        try:
            os.unlink(self.db + ".noemb")
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
