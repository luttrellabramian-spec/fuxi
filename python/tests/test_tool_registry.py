"""工具调用测试"""
import unittest
import os
import sys
import json

# 设置路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools import registry
from tools.file_tools import read_file, write_file, file_exists
from tools.memory_tools import memory_write, memory_query, memory_get_recent


class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        """设置测试环境"""
        self.test_file = "test_file.txt"
        self.test_content = "Hello, World!"

    def tearDown(self):
        """清理测试环境"""
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_file_tools(self):
        """测试文件工具"""
        # 测试文件不存在
        self.assertFalse(file_exists(self.test_file))

        # 写入文件
        result = write_file(self.test_file, self.test_content)
        self.assertTrue(result["success"])

        # 检查文件存在
        self.assertTrue(file_exists(self.test_file))

        # 读取文件
        content = read_file(self.test_file)
        self.assertEqual(content, self.test_content)

    def test_memory_tools(self):
        """测试记忆工具"""
        # 写入热记忆
        result = memory_write(memory_type="hot", content="test hot memory")
        self.assertTrue(result["success"])

        # 查询热记忆
        result = memory_query(memory_type="hot")
        self.assertTrue(result["success"])
        self.assertIn("test hot memory", result.get("memory_content", ""))

        # 写入温记忆
        result = memory_write(memory_type="warm", content="test warm memory", session_id="test_session")
        self.assertTrue(result["success"])

        # 查询温记忆
        result = memory_get_recent(memory_type="warm", session_id="test_session")
        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result.get("entries", [])), 1)

    def test_tool_registry(self):
        """测试工具注册表"""
        # 检查工具已注册
        tools = registry.list_tools()
        self.assertIn("read_file", tools)
        self.assertIn("write_file", tools)
        self.assertIn("memory_write", tools)
        self.assertIn("memory_query", tools)

        # 先写入文件
        write_file(self.test_file, self.test_content)

        # 测试工具调用
        result = registry.invoke("read_file", json.dumps({"path": self.test_file}))
        self.assertTrue(result["success"])
        self.assertEqual(result["result_json"], json.dumps(self.test_content))


if __name__ == '__main__':
    unittest.main()
