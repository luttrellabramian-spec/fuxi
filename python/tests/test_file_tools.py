"""伏羲 文件工具测试 — read_file / write_file / list_files / file_exists / read_json / write_json / _validate_path 安全"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from tools import file_tools
from tools.file_tools import (
    read_file, write_file, list_files, file_exists, read_json, write_json,
    _validate_path, _SENSITIVE_PATTERNS, _BASE_DIR,
)


def _abs(path: str) -> str:
    """把项目根下的相对路径转成绝对路径。

    _validate_path 用 os.path.abspath（基于 CWD）解析相对路径，
    而 CWD 不一定是项目根，所以测试中统一用绝对路径避免依赖 CWD。
    """
    return os.path.join(_BASE_DIR, path)


# ════════════════════════════════════════════════════════════════════
# 1. _validate_path — 路径安全校验
# ════════════════════════════════════════════════════════════════════


class TestValidatePath:
    def test_relative_path_under_base_dir(self, project_root):
        """项目根下的相对路径应被接受。"""
        result = _validate_path("README.md")
        assert os.path.isabs(result)
        assert result.startswith(os.path.abspath(project_root))

    def test_absolute_path_under_base_dir(self, project_root):
        """项目根下的绝对路径应被接受。"""
        target = os.path.join(project_root, "README.md")
        result = _validate_path(target)
        assert result == os.path.abspath(target)

    def test_rejects_path_traversal_dotdot(self, project_root):
        """含 `..` 的路径应被拒绝。"""
        with pytest.raises(ValueError, match="path traversal"):
            _validate_path("../outside.txt")

    def test_rejects_outside_base_dir(self):
        """项目根外的路径应被拒绝。"""
        with pytest.raises(ValueError, match="outside allowed directory"):
            _validate_path("C:/Windows/System32/drivers/etc/hosts")

    def test_rejects_sensitive_directory_git(self, tmp_test_dir):
        """`.git` 目录应被拒绝。"""
        sensitive = os.path.join(tmp_test_dir, ".git", "config")
        with pytest.raises(ValueError, match="sensitive directory"):
            _validate_path(sensitive)

    def test_rejects_sensitive_directory_venv(self, tmp_test_dir):
        """`venv` 目录应被拒绝。"""
        sensitive = os.path.join(tmp_test_dir, "venv", "lib", "x.py")
        with pytest.raises(ValueError, match="sensitive directory"):
            _validate_path(sensitive)

    def test_rejects_sensitive_directory_node_modules(self, tmp_test_dir):
        """`node_modules` 目录应被拒绝。"""
        sensitive = os.path.join(tmp_test_dir, "node_modules", "x.js")
        with pytest.raises(ValueError, match="sensitive directory"):
            _validate_path(sensitive)

    def test_sensitive_patterns_cover_all(self):
        """所有预期敏感目录应在白名单中。"""
        for expected in [".git", ".env", "node_modules", "__pycache__", ".venv", "venv"]:
            assert expected in _SENSITIVE_PATTERNS


# ════════════════════════════════════════════════════════════════════
# 2. file_exists — 检查文件存在
# ════════════════════════════════════════════════════════════════════


class TestFileExists:
    def test_returns_true_for_existing_file(self, project_root):
        """已存在的文件应返回 True。"""
        assert file_exists(_abs("README.md")) is True

    def test_returns_false_for_nonexistent_file(self):
        """不存在的文件应返回 False。"""
        assert file_exists("definitely_not_here_xyz_12345.txt") is False

    def test_returns_false_for_directory(self, project_root):
        """目录应返回 False（不是文件）。"""
        assert file_exists("python") is False

    def test_returns_false_for_path_outside_base(self):
        """项目外路径应返回 False（不抛错）。"""
        assert file_exists("C:/Windows/System32/cmd.exe") is False

    def test_returns_false_for_path_traversal(self):
        """路径遍历应返回 False（不抛错）。"""
        assert file_exists("../outside.txt") is False


# ════════════════════════════════════════════════════════════════════
# 3. read_file — 读文件
# ════════════════════════════════════════════════════════════════════


class TestReadFile:
    def test_reads_existing_file(self, tmp_test_dir):
        """已存在的文件应正确读取。"""
        path = os.path.join(tmp_test_dir, "hello.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("hello world")
        assert read_file(path) == "hello world"

    def test_raises_filenotfound_for_missing(self, tmp_test_dir):
        """不存在的文件应抛 FileNotFoundError。"""
        path = os.path.join(tmp_test_dir, "missing.txt")
        with pytest.raises(FileNotFoundError):
            read_file(path)

    def test_raises_for_file_too_large(self, tmp_test_dir):
        """超 max_size 应抛异常（read_file 把所有异常统一包装为 IOError）。"""
        path = os.path.join(tmp_test_dir, "big.bin")
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        with pytest.raises(IOError, match="File too large"):
            read_file(path, max_size=10)

    def test_reads_unicode_content(self, tmp_test_dir):
        """UTF-8 中文内容应正确读取。"""
        path = os.path.join(tmp_test_dir, "cn.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("伏羲测试中文")
        assert read_file(path) == "伏羲测试中文"

    def test_rejects_outside_base_dir(self):
        """项目外路径应被拒绝。"""
        with pytest.raises(IOError, match="outside allowed directory"):
            read_file("C:/Windows/System32/drivers/etc/hosts")


# ════════════════════════════════════════════════════════════════════
# 4. write_file — 写文件
# ════════════════════════════════════════════════════════════════════


class TestWriteFile:
    def test_writes_new_file(self, tmp_test_dir):
        """写入新文件应成功。"""
        path = os.path.join(tmp_test_dir, "new.txt")
        result = write_file(path, "new content")
        assert result["success"] is True
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "new content"

    def test_overwrites_existing_file_creates_backup(self, tmp_test_dir):
        """覆盖已存在文件应创建 .bak 备份。"""
        path = os.path.join(tmp_test_dir, "existing.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("original")
        result = write_file(path, "modified")
        assert result["success"] is True
        # 备份存在
        assert os.path.exists(path + ".bak")
        with open(path + ".bak", encoding="utf-8") as f:
            assert f.read() == "original"
        with open(path, encoding="utf-8") as f:
            assert f.read() == "modified"

    def test_rejects_oversized_content(self, tmp_test_dir):
        """超过 max_size 的内容应返回失败结果。"""
        path = os.path.join(tmp_test_dir, "huge.txt")
        huge = "x" * (11 * 1024 * 1024)  # 11MB
        result = write_file(path, huge, max_size=10 * 1024 * 1024)
        assert result["success"] is False
        assert "too large" in result["error"]

    def test_rejects_outside_base_dir(self):
        """项目外路径应被拒绝。"""
        result = write_file("C:/Windows/System32/evil.exe", "x")
        assert result["success"] is False

    def test_rejects_path_traversal(self):
        """路径遍历应被拒绝。"""
        result = write_file("../escape.txt", "x")
        assert result["success"] is False


# ════════════════════════════════════════════════════════════════════
# 5. list_files — 列目录文件
# ════════════════════════════════════════════════════════════════════


class TestListFiles:
    def test_lists_files_in_directory(self, tmp_test_dir):
        """应只列出文件，不包含子目录。"""
        a = os.path.join(tmp_test_dir, "a.txt")
        b = os.path.join(tmp_test_dir, "b.txt")
        sub = os.path.join(tmp_test_dir, "sub")
        with open(a, "w") as f:
            f.write("a")
        with open(b, "w") as f:
            f.write("b")
        os.makedirs(sub)
        result = sorted(list_files(tmp_test_dir))
        assert result == ["a.txt", "b.txt"]

    def test_empty_directory(self, tmp_test_dir):
        """空目录应返回空列表。"""
        assert list_files(tmp_test_dir) == []

    def test_raises_for_not_a_directory(self, tmp_test_dir):
        """文件路径应抛异常（list_files 包装为 IOError）。"""
        path = os.path.join(tmp_test_dir, "f.txt")
        with open(path, "w") as f:
            f.write("x")
        with pytest.raises(IOError, match="No such directory"):
            list_files(path)

    def test_rejects_outside_base_dir(self):
        """项目外路径应被拒绝。"""
        with pytest.raises(IOError):
            list_files("C:/Windows")


# ════════════════════════════════════════════════════════════════════
# 6. read_json / write_json
# ════════════════════════════════════════════════════════════════════


class TestJsonOps:
    def test_read_json_success(self, tmp_test_dir):
        """应能正确解析 JSON 文件。"""
        path = os.path.join(tmp_test_dir, "data.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"k": "v", "n": 42}, f, ensure_ascii=False)
        result = read_json(path)
        assert result == {"k": "v", "n": 42}

    def test_read_json_invalid_returns_error(self, tmp_test_dir):
        """非法 JSON 应返回错误字典。"""
        path = os.path.join(tmp_test_dir, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json {{{")
        result = read_json(path)
        assert result.get("success") is False

    def test_write_json_success(self, tmp_test_dir):
        """应能写入合法 JSON。"""
        path = os.path.join(tmp_test_dir, "out.json")
        result = write_json(path, {"伏羲": "测试", "list": [1, 2, 3]})
        assert result["success"] is True
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"伏羲": "测试", "list": [1, 2, 3]}

    def test_write_json_creates_backup(self, tmp_test_dir):
        """覆盖已存在 JSON 应创建备份。"""
        path = os.path.join(tmp_test_dir, "exist.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"v": 1}, f)
        write_json(path, {"v": 2})
        assert os.path.exists(path + ".bak")
        with open(path + ".bak", encoding="utf-8") as f:
            assert json.load(f) == {"v": 1}
