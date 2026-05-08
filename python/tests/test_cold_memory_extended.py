"""扩展测试 - 覆盖冷记忆边界情况"""
import unittest
import os
import tempfile
import shutil
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from memory.cold_memory import ColdMemory


class TestColdMemoryExtended(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "cold.db")
        self.m = ColdMemory(db_path=self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_empty_content(self):
        """插入空内容"""
        r = self.m.insert_summary("", "")
        self.assertTrue(r["success"])

    def test_insert_with_metadata(self):
        """带元数据插入"""
        r = self.m.insert_summary("content", "summary", metadata={"key": "value"})
        self.assertTrue(r["success"])
        rr = self.m.get_recent(limit=1)
        self.assertTrue(rr["success"])
        self.assertEqual(rr["entries"][0]["metadata"], '{"key": "value"}')

    def test_search_empty_query(self):
        """空查询"""
        self.m.insert_summary("hello", "hello")
        sr = self.m.search_similar("", limit=10)
        self.assertTrue(sr["success"])

    def test_search_no_results(self):
        """无结果查询"""
        self.m.insert_summary("hello", "hello")
        sr = self.m.search_similar("nonexistent", limit=10)
        self.assertTrue(sr["success"])
        # 向量模式下，不相关的查询应该返回空
        self.assertEqual(len(sr["entries"]), 0)

    def test_get_recent_empty(self):
        """空数据库获取最近"""
        rr = self.m.get_recent(limit=10)
        self.assertTrue(rr["success"])
        self.assertEqual(len(rr["entries"]), 0)

    def test_clear_nonexistent_session(self):
        """清空不存在的会话"""
        r = self.m.clear_session("nonexistent")
        self.assertTrue(r["success"])

    def test_stats_empty(self):
        """空数据库统计"""
        stats = self.m.get_stats()
        self.assertIn("total_summaries", stats)
        self.assertEqual(stats["total_summaries"], 0)


if __name__ == "__main__":
    unittest.main()
