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
        # read_file
        result = registry.invoke("read_file", '{"path": "python/src/__init__.py"}')
        self.assertTrue(result["success"], f"read_file failed: {result['error']}")

        # file_exists
        result = registry.invoke("file_exists", '{"path": "python/src/__init__.py"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIs(data, True)

        # file_exists (not exist)
        result = registry.invoke("file_exists", '{"path": "nonexistent_file_xyz.py"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIs(data, False)

        result = registry.invoke("list_files", '{"directory": "python/src"}')
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
        result = registry.invoke("search_file", '{"query": "import", "directory": "python/src", "file_pattern": "*.py"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertGreater(data["count"], 0, "Should find some matches")

        result = registry.invoke("grep", '{"pattern": "def.*test", "path": "python/src", "glob": "*.py"}')
        self.assertTrue(result["success"])

    def test_web_tools(self):
        """网络工具测试"""
        # parse_headers
        result = registry.invoke("parse_headers", '{"header_string": "Content-Type: application/json\\nAuthorization: Bearer test"}')
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["headers"]["Content-Type"], "application/json")

    def test_error_handling(self):
        """错误处理"""
        # 不存在的工具
        result = registry.invoke("nonexistent_tool_xyz", "{}")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

        # 参数错误 - 缺少必需参数
        result = registry.invoke("read_file", '{"invalid_param": true}')
        # 应该返回错误（缺少 path 参数）
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        print(f"\n[Error handling] Missing param error: {result['error'][:100]}")

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


class ToolSecurityTest(unittest.TestCase):
    """工具安全性测试"""

    def test_path_traversal_protection(self):
        """路径遍历防护测试"""
        # 尝试访问系统敏感文件
        result = registry.invoke("read_file", '{"path": "/etc/passwd"}')
        self.assertFalse(result["success"])
        self.assertIn("Access denied", result["error"])

        # 尝试使用 .. 进行路径遍历
        result = registry.invoke("read_file", '{"path": "python/src/../../../etc/passwd"}')
        self.assertFalse(result["success"])

    def test_ssrf_protection(self):
        """SSRF 防护测试"""
        # 尝试访问内网地址
        result = registry.invoke("http_get", '{"url": "http://127.0.0.1:8080"}')
        self.assertFalse(result["success"])
        self.assertIn("private network", result["error"])

        # 尝试访问元数据服务
        result = registry.invoke("http_get", '{"url": "http://169.254.169.254/latest/meta-data/"}')
        self.assertFalse(result["success"])

    def test_invalid_url_scheme(self):
        """无效 URL 协议测试"""
        result = registry.invoke("http_get", '{"url": "file:///etc/passwd"}')
        self.assertFalse(result["success"])
        self.assertIn("Unsupported scheme", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
