"""温记忆 & 冷记忆 单元测试"""
import unittest
import os
import tempfile
import shutil
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory


class TestHotMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.m = HotMemory(os.path.join(self.tmp, "MEMORY.md"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_basic(self):
        r = self.m.write("hello world")
        self.assertTrue(r["success"])
        rd = self.m.read()
        self.assertEqual(rd["memory_content"], "hello world")

    def test_truncate(self):
        long_text = "a" * 3000
        self.m.write(long_text)
        rd = self.m.read()
        self.assertLessEqual(len(rd["memory_content"]), 2200)


class TestWarmMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "warm.db")
        self.m = WarmMemory(db_path=self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_and_get_recent(self):
        r1 = self.m.add_message("s1", "hello from test")
        r2 = self.m.add_message("s1", "second message")
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])

        rr = self.m.get_recent("s1", limit=10)
        self.assertTrue(rr["success"])
        self.assertEqual(len(rr["entries"]), 2)
        self.assertEqual(rr["entries"][0]["content"], "hello from test")
        self.assertEqual(rr["entries"][1]["content"], "second message")

    def test_session_isolation(self):
        self.m.add_message("s1", "msg1")
        self.m.add_message("s2", "msg2")
        r1 = self.m.get_recent("s1", limit=10)
        r2 = self.m.get_recent("s2", limit=10)
        self.assertEqual(len(r1["entries"]), 1)
        self.assertEqual(len(r2["entries"]), 1)

    def test_fts_search(self):
        self.m.add_message("s1", "apple banana orange")
        self.m.add_message("s1", "grape strawberry")
        sr = self.m.search("s1", "apple")
        self.assertTrue(sr["success"], f"FTS search failed: {sr.get('error', '')}")
        self.assertGreaterEqual(len(sr["entries"]), 1)
        self.assertIn("apple", sr["entries"][0]["content"])

    def test_limit_50(self):
        for i in range(60):
            self.m.add_message("session_limit", f"msg{i}")
        rr = self.m.get_recent("session_limit", limit=100)
        self.assertTrue(rr["success"])
        self.assertLessEqual(len(rr["entries"]), 50)


class TestColdMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "cold.db")
        self.m = ColdMemory(db_path=self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_insert_and_get_recent(self):
        r1 = self.m.insert_summary("content1", "summary1")
        r2 = self.m.insert_summary("content2", "summary2")
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])

        rr = self.m.get_recent(limit=10)
        self.assertTrue(rr["success"])
        self.assertGreaterEqual(len(rr["entries"]), 2)

    def test_session_isolation(self):
        self.m.insert_summary("c1", "s1", session_id="A")
        self.m.insert_summary("c2", "s2", session_id="B")
        rr = self.m.get_recent(session_id="A", limit=10)
        self.assertTrue(rr["success"])
        self.assertEqual(len(rr["entries"]), 1)
        self.assertEqual(rr["entries"][0]["summary"], "s1")

    def test_search_fallback(self):
        self.m.insert_summary("hello world", "hello world")
        self.m.insert_summary("foo bar", "foo bar")
        sr = self.m.search_similar("hello", limit=10)
        self.assertTrue(sr["success"])
        self.assertGreaterEqual(len(sr["entries"]), 1)

    def test_stats(self):
        stats = self.m.get_stats()
        self.assertIn("total_summaries", stats)
        self.assertIn("has_embedding", stats)


if __name__ == "__main__":
    unittest.main()
