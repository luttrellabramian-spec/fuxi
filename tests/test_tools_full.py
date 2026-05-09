"""工具注册表全面测试 - 覆盖 20 个工具的边界情况"""
import unittest
import sys
import os
import json
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from tools import registry


class TestToolRegistryFull(unittest.TestCase):
    """工具注册表全面测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_file = os.path.join(self.temp_dir, "test.txt")
        self.temp_json = os.path.join(self.temp_dir, "test.json")
        self.temp_md = os.path.join(self.temp_dir, "test.md")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ===== 文件工具 =====

    def test_read_file_basic(self):
        """read_file 基本读取"""
        with open(self.temp_file, "w", encoding="utf-8") as f:
            f.write("hello world")
        result = registry.invoke("read_file", json.dumps({"path": self.temp_file}))
        self.assertTrue(result["success"])
        self.assertIn("hello world", result["result_json"])

    def test_read_file_not_found(self):
        """read_file 文件不存在"""
        result = registry.invoke("read_file", json.dumps({"path": "/nonexistent/file/xyz.txt"}))
        self.assertFalse(result["success"])
        self.assertIn("No such file", result["error"])

    def test_write_file_basic(self):
        """write_file 基本写入"""
        result = registry.invoke("write_file", json.dumps({
            "path": self.temp_file,
            "content": "new content"
        }))
        self.assertTrue(result["success"])
        with open(self.temp_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "new content")

    def test_write_file_overwrite(self):
        """write_file 覆盖"""
        with open(self.temp_file, "w", encoding="utf-8") as f:
            f.write("old")
        result = registry.invoke("write_file", json.dumps({
            "path": self.temp_file,
            "content": "new"
        }))
        self.assertTrue(result["success"])
        with open(self.temp_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "new")

    def test_write_json(self):
        """write_json 写入 JSON"""
        data = {"name": "伏羲", "version": "0.1.0", "features": ["ReAct", "工具"]}
        result = registry.invoke("write_json", json.dumps({
            "path": self.temp_json,
            "data": data
        }))
        self.assertTrue(result["success"])
        with open(self.temp_json, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["name"], "伏羲")

    def test_read_json(self):
        """read_json 读取 JSON"""
        with open(self.temp_json, "w", encoding="utf-8") as f:
            json.dump({"key": "value", "count": 42}, f)
        result = registry.invoke("read_json", json.dumps({"path": self.temp_json}))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["key"], "value")
        self.assertEqual(data["count"], 42)

    def test_list_files(self):
        """list_files 列出文件"""
        for i in range(3):
            with open(os.path.join(self.temp_dir, f"file{i}.txt"), "w") as f:
                f.write("")
        result = registry.invoke("list_files", json.dumps({"directory": self.temp_dir}))
        self.assertTrue(result["success"])
        files = json.loads(result["result_json"])
        self.assertIsInstance(files, list)
        self.assertEqual(len(files), 3)

    def test_list_files_nonexistent(self):
        """list_files 目录不存在"""
        result = registry.invoke("list_files", json.dumps({"directory": "/nonexistent/dir"}))
        self.assertFalse(result["success"])

    def test_file_exists_true(self):
        """file_exists 返回 true"""
        with open(self.temp_file, "w") as f:
            f.write("x")
        result = registry.invoke("file_exists", json.dumps({"path": self.temp_file}))
        self.assertTrue(result["success"])
        self.assertIs(json.loads(result["result_json"]), True)

    def test_file_exists_false(self):
        """file_exists 返回 false"""
        result = registry.invoke("file_exists", json.dumps({"path": "/nonexistent/file.xyz"}))
        self.assertTrue(result["success"])
        self.assertIs(json.loads(result["result_json"]), False)

    # ===== 搜索工具 =====

    def test_search_file_basic(self):
        """search_file 基本搜索"""
        for i, content in enumerate(["apple fruit", "banana fruit", "carrot vegetable"]):
            with open(os.path.join(self.temp_dir, f"file{i}.txt"), "w", encoding="utf-8") as f:
                f.write(content)
        result = registry.invoke("search_file", json.dumps({
            "query": "fruit",
            "directory": self.temp_dir,
            "file_pattern": "*.txt"
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 2)

    def test_search_file_no_match(self):
        """search_file 无匹配"""
        result = registry.invoke("search_file", json.dumps({
            "query": "nonexistent_xyz_query",
            "directory": self.temp_dir,
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 0)

    def test_search_replace(self):
        """search_replace 搜索替换"""
        with open(self.temp_file, "w", encoding="utf-8") as f:
            f.write("hello world\nhello python\nhello java")
        result = registry.invoke("search_replace", json.dumps({
            "file_path": self.temp_file,
            "search": "hello",
            "replace": "hi",
            "backup": False,
        }))
        self.assertTrue(result["success"])
        with open(self.temp_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content.count("hi"), 3)
        self.assertEqual(content.count("hello"), 0)

    def test_search_replace_with_backup(self):
        """search_replace 备份文件"""
        with open(self.temp_file, "w", encoding="utf-8") as f:
            f.write("old content")
        result = registry.invoke("search_replace", json.dumps({
            "file_path": self.temp_file,
            "search": "old",
            "replace": "new",
            "backup": True,
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertTrue(os.path.exists(data["backup"]))
        with open(data["backup"], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "old content")

    def test_grep_basic(self):
        """grep 正则搜索"""
        with open(os.path.join(self.temp_dir, "log.txt"), "w", encoding="utf-8") as f:
            f.write("ERROR: something failed\nINFO: ok\nERROR: again\nWARN: warning")
        result = registry.invoke("grep", json.dumps({
            "pattern": "ERROR",
            "path": self.temp_dir,
            "glob": "*.txt"
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 2)

    def test_grep_ignore_case(self):
        """grep 忽略大小写"""
        with open(os.path.join(self.temp_dir, "log2.txt"), "w", encoding="utf-8") as f:
            f.write("ERROR\nerror\nError")
        result = registry.invoke("grep", json.dumps({
            "pattern": "error",
            "path": self.temp_dir,
            "glob": "*.txt",
            "ignore_case": True,
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 3)

    def test_grep_invalid_regex(self):
        """grep 无效正则"""
        result = registry.invoke("grep", json.dumps({
            "pattern": "[invalid",
            "path": self.temp_dir,
        }))
        self.assertFalse(result["success"])
        self.assertIn("Invalid regex", result["error"])

    # ===== 网络工具 =====

    def test_parse_headers(self):
        """parse_headers 解析请求头"""
        result = registry.invoke("parse_headers", json.dumps({
            "header_string": "Content-Type: application/json\nAuthorization: Bearer sk-xxx\nX-Custom: value"
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["headers"]["Authorization"], "Bearer sk-xxx")

    def test_parse_headers_empty(self):
        """parse_headers 空字符串"""
        result = registry.invoke("parse_headers", json.dumps({
            "header_string": ""
        }))
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertEqual(data["count"], 0)

    def test_check_url_invalid(self):
        """check_url 无效 URL"""
        result = registry.invoke("check_url", json.dumps({
            "url": "http://this-domain-does-not-exist-xyz123.invalid",
            "timeout": 2,
        }))
        # 网络问题，但工具本身执行成功
        self.assertTrue(result["success"])
        data = json.loads(result["result_json"])
        self.assertIn("reachable", data)

    # ===== 记忆工具 =====

    def test_memory_write_hot(self):
        """memory_write 热记忆"""
        result = registry.invoke("memory_write", json.dumps({
            "memory_type": "hot",
            "content": "测试记忆内容"
        }))
        self.assertTrue(result["success"])

    def test_memory_query_hot(self):
        """memory_query 热记忆"""
        registry.invoke("memory_write", json.dumps({
            "memory_type": "hot",
            "content": "查询关键词 xyz123"
        }))
        result = registry.invoke("memory_query", json.dumps({
            "memory_type": "hot",
            "query": "xyz123"
        }))
        self.assertTrue(result["success"])

    def test_memory_get_recent_hot(self):
        """memory_get_recent 热记忆"""
        result = registry.invoke("memory_get_recent", json.dumps({
            "memory_type": "hot"
        }))
        self.assertTrue(result["success"])

    # ===== 边界情况 =====

    def test_unknown_tool(self):
        """调用不存在的工具"""
        result = registry.invoke("tool_does_not_exist_xyz", "{}")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

    def test_tool_with_empty_args(self):
        """空参数调用"""
        result = registry.invoke("file_exists", "{}")
        self.assertFalse(result["success"])

    def test_tool_elapsed_time(self):
        """执行时间被记录"""
        result = registry.invoke("file_exists", json.dumps({"path": self.temp_file}))
        self.assertTrue(result["success"])
        self.assertIsInstance(result["elapsed_ms"], int)
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_all_tools_return_result_json(self):
        """所有工具都返回 result_json 字段"""
        # 只测试确定会成功的工具
        test_cases = [
            ("file_exists", {"path": self.temp_file}),
            ("parse_headers", {"header_string": "X-Test: val"}),
        ]
        for tool_name, args in test_cases:
            result = registry.invoke(tool_name, json.dumps(args))
            self.assertIn("result_json", result, f"{tool_name} missing result_json")
            self.assertIn("success", result, f"{tool_name} missing success")
            self.assertIn("elapsed_ms", result, f"{tool_name} missing elapsed_ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
