"""工具调用成功率测试

验证指标：
- 工具调用成功率 > 95%（100次调用）
- 错误类型分类
"""
import unittest
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from tools import registry


class ToolCallTest(unittest.TestCase):
    """工具调用成功率测试"""

    def test_tool_registry_not_empty(self):
        """注册表非空"""
        tools = registry.list_tools()
        self.assertGreater(len(tools), 0, "Tool registry should not be empty")
        print(f"\n[Registry] Total tools registered: {len(tools)}")
        for name in sorted(tools.keys()):
            print(f"  - {name} [{tools[name]['level']}]")

    def test_file_tools(self):
        """文件工具测试"""
        import os
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        init_path = os.path.join(base_dir, "python", "src", "__init__.py")
        init_path_json = init_path.replace("\\", "\\\\")

        # read_file
        result = registry.invoke("read_file", f'{{"path": "{init_path_json}"}}')
        self.assertTrue(result["success"], f"read_file failed: {result.get('error', 'unknown')}")

        # file_exists
        result = registry.invoke("file_exists", f'{{"path": "{init_path_json}"}}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIs(data, True)

        # file_exists (not exist)
        result = registry.invoke("file_exists", '{"path": "nonexistent_file_xyz.py"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIs(data, False)

        src_dir = os.path.join(base_dir, "python", "src")
        src_dir_json = src_dir.replace("\\", "\\\\")
        result = registry.invoke("list_files", f'{{"directory": "{src_dir_json}"}}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIsInstance(data, list)

    def test_memory_tools(self):
        """记忆工具测试"""
        result = registry.invoke("memory_write", '{"memory_type": "hot", "content": "test memory content"}')
        self.assertTrue(result["success"])

        result = registry.invoke("memory_query", '{"memory_type": "hot", "query": "test"}')
        self.assertTrue(result["success"])

        result = registry.invoke("memory_get_recent", '{"memory_type": "hot"}')
        self.assertTrue(result["success"])

    def test_search_tools(self):
        """搜索工具测试"""
        import os
        base_dir = os.path.join(os.path.dirname(__file__), "..")
        src_dir = os.path.join(base_dir, "python", "src")
        src_dir_json = src_dir.replace("\\", "\\\\")

        result = registry.invoke("search_file", f'{{"query": "import", "directory": "{src_dir_json}", "file_pattern": "*.py"}}')
        self.assertTrue(result["success"], f"search_file failed: {result.get('error', 'unknown')}")
        data = json.loads(result["result_json"])
        self.assertIn("count", data)
        self.assertIn("matches", data)

        result = registry.invoke("grep", f'{{"pattern": "def.*test", "path": "{src_dir_json}", "glob": "*.py"}}')
        self.assertTrue(result["success"], f"grep failed: {result.get('error', 'unknown')}")

    def test_web_tools(self):
        """网络工具测试"""
        # check_url
        result = registry.invoke("check_url", '{"url": "https://www.baidu.com", "timeout": 5}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIn("reachable", data)

        # parse_headers
        result = registry.invoke("parse_headers", '{"header_string": "Content-Type: application/json\\nAuthorization: Bearer test"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["headers"]["Content-Type"], "application/json")

    def test_error_handling(self):
        """错误处理"""
        result = registry.invoke("nonexistent_tool_xyz", "{}")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

        # 参数错误
        result = registry.invoke("read_file", '{"invalid_param": true}')

    def test_consecutive_calls(self):
        """连续 100 次调用测试（成功率 > 95%）"""
        total = 100
        successes = 0
        errors = []
        for i in range(total):
            result = registry.invoke("file_exists", '{"path": "python/src/__init__.py"}')
            if result["success"]:
                successes += 1
            else:
                errors.append(result["error"])

        rate = successes / total * 100
        print(f"\n[Consecutive] {successes}/{total} success ({rate:.1f}%)")
        if errors[:3]:
            print(f"  Sample errors: {errors[:3]}")

        self.assertGreater(rate, 95, f"Success rate {rate:.1f}% should be > 95%")


if __name__ == "__main__":
    unittest.main(verbosity=2)
