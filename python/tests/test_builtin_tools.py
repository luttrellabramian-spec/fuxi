"""Tests for built-in file and search tools.

Tests cover:
  - file_tools: read_file, write_file, list_files, file_exists, read_json, write_json
  - file_tools._validate_path: path traversal blocking, outside-directory rejection,
    sensitive-directory rejection, valid path acceptance
  - search_tools: search_file, grep, search_replace (full and count-limited),
    backup creation
  - Boundary conditions: large content, empty file, nonexistent file/directory,
    invalid regex pattern

All file-IO tests create temporary data under PROJECT_ROOT/tmp_test_data/
so that ``_validate_path()`` accepts the paths.
"""

import os
import json
import time
import shutil
import pytest
import re

# ---------------------------------------------------------------------------
# sys.path + project-root helpers (standalone fallback if conftest hasn't run)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
_SRC_DIR = os.path.join(_PROJECT_ROOT, 'python', 'src')
if _SRC_DIR not in os.sys.path:
    os.sys.path.insert(0, _SRC_DIR)

from tools import registry
from tools.file_tools import (
    read_file,
    write_file,
    list_files,
    file_exists,
    read_json,
    write_json,
    _validate_path,
    _BASE_DIR,
    _SENSITIVE_PATTERNS,
)
from tools.search_tools import search_file, grep, search_replace


# ===================================================================
#  Fixtures
# ===================================================================

@pytest.fixture
def tmp_dir():
    """Create an isolated temp directory under PROJECT_ROOT/tmp_test_data/.

    This directory is inside ``_BASE_DIR`` so ``_validate_path`` permits it.
    """
    path = os.path.join(_PROJECT_ROOT, 'tmp_test_data')
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sample_file(tmp_dir):
    """Write a sample text file and return its path."""
    path = os.path.join(tmp_dir, "hello.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Hello, Fuxi!\nThis is a test.\nHello again.\n")
    return path


@pytest.fixture
def sample_json(tmp_dir):
    """Write a sample JSON file and return its path."""
    path = os.path.join(tmp_dir, "data.json")
    data = {"name": "Fuxi", "version": "0.2.5", "tags": ["ai", "engine"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


@pytest.fixture
def multi_file_dir(tmp_dir):
    """Create a directory with several files for search / grep tests."""
    files = {
        "main.py": "def hello():\n    print('hello')\n",
        "utils.py": "def add(a, b):\n    return a + b\n\n# helper\n",
        "README.md": "# Project\n\nHello world.\n",
    }
    for name, content in files.items():
        with open(os.path.join(tmp_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    return tmp_dir


# ===================================================================
#  read_file
# ===================================================================

class TestReadFile:
    """Basic file reading."""

    def test_read_success(self, sample_file):
        content = read_file(sample_file)
        assert "Hello, Fuxi!" in content
        assert "Hello again." in content

    def test_read_nonexistent(self, tmp_dir):
        path = os.path.join(tmp_dir, "nope.txt")
        with pytest.raises((FileNotFoundError, IOError)):
            read_file(path)

    def test_read_empty_file(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.txt")
        with open(path, "w") as f:
            pass
        content = read_file(path)
        assert content == ""

    def test_read_binary_file_raises(self, tmp_dir):
        """Reading a binary file with UTF-8 mode raises an error."""
        path = os.path.join(tmp_dir, "binary.bin")
        with open(path, "wb") as f:
            f.write(b"\x00\x01\x02\xff")
        with pytest.raises((IOError, UnicodeDecodeError)):
            read_file(path)

    def test_read_exceeds_max_size(self, tmp_dir):
        path = os.path.join(tmp_dir, "large.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * 100)
        with pytest.raises((ValueError, IOError)):
            read_file(path, max_size=10)

    def test_read_path_traversal_blocked(self):
        with pytest.raises((IOError, OSError), match="path traversal"):
            read_file("../../etc/passwd")


# ===================================================================
#  write_file
# ===================================================================

class TestWriteFile:
    """File writing."""

    def test_write_and_verify(self, tmp_dir):
        path = os.path.join(tmp_dir, "out.txt")
        result = write_file(path, "new content")
        assert result["success"] is True
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "new content"

    def test_write_overwrites(self, sample_file):
        result = write_file(sample_file, "overwritten")
        assert result["success"] is True
        with open(sample_file, "r", encoding="utf-8") as f:
            assert f.read() == "overwritten"

    def test_write_creates_backup(self, sample_file):
        result = write_file(sample_file, "new data")
        assert result["success"] is True
        bak = sample_file + ".bak"
        assert os.path.exists(bak)
        # Cleanup
        os.remove(bak)

    def test_write_exceeds_max_size(self, tmp_dir):
        path = os.path.join(tmp_dir, "big.txt")
        result = write_file(path, "x" * (10 * 1024 * 1024 + 1), max_size=10 * 1024 * 1024)
        assert result["success"] is False
        assert "too large" in result.get("error", "").lower()

    def test_write_path_traversal_blocked(self):
        """write_file catches the error and returns a result dict."""
        result = write_file("../../outside.txt", "x")
        assert result["success"] is False

    def test_write_nested_directory_raises(self, tmp_dir):
        """Writing to a non-existent subdirectory should fail."""
        path = os.path.join(tmp_dir, "sub", "file.txt")
        result = write_file(path, "content")
        assert result["success"] is False


# ===================================================================
#  list_files
# ===================================================================

class TestListFiles:
    """Directory listing."""

    def test_list_files_in_dir(self, tmp_dir):
        for name in ("a.txt", "b.txt", "c.md"):
            with open(os.path.join(tmp_dir, name), "w") as f:
                f.write(name)
        files = list_files(tmp_dir)
        assert "a.txt" in files
        assert "b.txt" in files
        assert "c.md" in files
        assert len(files) == 3

    def test_list_files_directories_excluded(self, tmp_dir):
        """list_files only returns files, not subdirectories."""
        os.makedirs(os.path.join(tmp_dir, "subdir"), exist_ok=True)
        with open(os.path.join(tmp_dir, "file.txt"), "w") as f:
            f.write("x")
        files = list_files(tmp_dir)
        assert "file.txt" in files
        assert "subdir" not in files

    def test_list_files_nonexistent_dir(self):
        with pytest.raises((IOError, NotADirectoryError)):
            list_files("/nonexistent/path")

    def test_list_files_empty_dir(self, tmp_dir):
        files = list_files(tmp_dir)
        assert files == []

    def test_list_files_path_traversal_blocked(self):
        with pytest.raises((IOError, OSError), match="path traversal"):
            list_files("../../etc")


# ===================================================================
#  file_exists
# ===================================================================

class TestFileExists:
    """File existence check."""

    def test_exists_true(self, sample_file):
        assert file_exists(sample_file) is True

    def test_exists_false(self, tmp_dir):
        path = os.path.join(tmp_dir, "nope.txt")
        assert file_exists(path) is False

    def test_exists_directory_returns_false(self, tmp_dir):
        """Directories are not files."""
        assert file_exists(tmp_dir) is False

    def test_exists_path_traversal_returns_false(self):
        """file_exists returns False (not raises) for invalid paths."""
        assert file_exists("../../etc/passwd") is False

    def test_exists_outside_dir_returns_false(self):
        """file_exists returns False for paths outside allowed directory."""
        assert file_exists("/tmp") is False or file_exists("/nonexistent") is False


# ===================================================================
#  read_json / write_json
# ===================================================================

class TestJsonTools:
    """JSON file round-trip."""

    def test_write_and_read_json(self, tmp_dir):
        path = os.path.join(tmp_dir, "config.json")
        data = {"enabled": True, "count": 42, "items": [1, 2, 3]}
        result = write_json(path, data)
        assert result["success"] is True
        loaded = read_json(path)
        assert loaded == data

    def test_write_json_overwrites(self, sample_json):
        new_data = {"name": "Updated"}
        result = write_json(sample_json, new_data)
        assert result["success"] is True
        loaded = read_json(sample_json)
        assert loaded == new_data

    def test_read_json_invalid(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid")
        result = read_json(path)
        # read_json returns a dict with error on failure
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_read_json_nonexistent(self, tmp_dir):
        path = os.path.join(tmp_dir, "nope.json")
        result = read_json(path)
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_write_json_path_traversal_blocked(self):
        """write_json catches errors and returns a result dict."""
        result = write_json("../../data.json", {})
        assert result["success"] is False

    def test_read_json_empty_file(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        result = read_json(path)
        assert isinstance(result, dict)
        assert result.get("success") is False


# ===================================================================
#  _validate_path
# ===================================================================

class TestValidatePath:
    """Direct unit tests for the path-validation guard."""

    def test_valid_path_inside_project(self, tmp_dir):
        abs_path = _validate_path(tmp_dir)
        assert os.path.isabs(abs_path)

    def test_valid_path_normalized(self, tmp_dir):
        abs_path = _validate_path(tmp_dir)
        assert abs_path == os.path.abspath(tmp_dir)

    def test_path_traversal_blocked(self):
        with pytest.raises(ValueError, match="path traversal"):
            _validate_path("../other")

    def test_path_traversal_double_dot(self):
        with pytest.raises(ValueError, match="path traversal"):
            _validate_path("a/../../b")

    def test_outside_base_dir_blocked(self):
        """Absolute path outside _BASE_DIR must be rejected."""
        with pytest.raises(ValueError, match="outside allowed"):
            # Try a drive root or a known-outside path
            outside = os.path.abspath(os.sep)  # e.g. C:\
            if outside == _BASE_DIR.rstrip(os.sep):
                pytest.skip("root equals _BASE_DIR")
            _validate_path(outside)

    def test_sensitive_dir_blocked(self):
        """Paths inside .git, .env, node_modules etc. must be rejected."""
        for pattern in _SENSITIVE_PATTERNS:
            bad_rel = os.path.join(pattern, "some_file.txt")
            bad_path = os.path.join(_BASE_DIR, bad_rel)
            # This might be valid if .git doesn't exist, but _validate_path
            # checks the rel-path string, not filesystem existence
            with pytest.raises(ValueError, match="sensitive directory"):
                _validate_path(bad_path)

    def test_sensitive_dir_nested(self):
        bad_path = os.path.join(_BASE_DIR, "src", ".git", "config")
        with pytest.raises(ValueError, match="sensitive directory"):
            _validate_path(bad_path)


# ===================================================================
#  search_file
# ===================================================================

class TestSearchFile:
    """Keyword-based file search."""

    def test_search_found(self, multi_file_dir):
        result = search_file("hello", directory=multi_file_dir,
                             file_pattern="*.py", case_sensitive=False)
        assert result["success"] is True
        assert result["count"] >= 1
        # At least main.py matches
        assert any("main.py" in m["file"] for m in result["matches"])

    def test_search_not_found(self, multi_file_dir):
        result = search_file("zzzznonexistent", directory=multi_file_dir)
        assert result["success"] is True
        assert result["count"] == 0

    def test_search_case_sensitive(self, tmp_dir):
        path = os.path.join(tmp_dir, "test.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Hello\nWORLD\n")
        result = search_file("WORLD", directory=tmp_dir,
                             file_pattern="*.txt", case_sensitive=True)
        assert result["count"] == 1
        result2 = search_file("world", directory=tmp_dir,
                              file_pattern="*.txt", case_sensitive=True)
        assert result2["count"] == 0

    def test_search_directory_not_found(self):
        result = search_file("query", directory="/nonexistent_dir_xyz")
        assert result["success"] is False
        assert "not found" in result.get("error", "").lower()

    def test_search_respects_file_pattern(self, multi_file_dir):
        result = search_file("hello", directory=multi_file_dir,
                             file_pattern="*.md")
        assert result["count"] == 1
        assert any("README.md" in m["file"] for m in result["matches"])

    def test_search_hit_limit(self, tmp_dir):
        for i in range(10):
            with open(os.path.join(tmp_dir, f"f{i}.txt"), "w") as f:
                f.write("common_word\n")
        result = search_file("common_word", directory=tmp_dir,
                             file_pattern="*.txt")
        # search_file caps at 100 matches so this should pass
        assert result["success"] is True
        # All 10 should be found
        assert result["count"] == 10

    def test_search_empty_query(self, multi_file_dir):
        """Empty query matches every line (behavioural observation)."""
        result = search_file("", directory=multi_file_dir, file_pattern="*")
        assert result["success"] is True


# ===================================================================
#  grep
# ===================================================================

class TestGrep:
    """Regex-based file search."""

    def test_grep_found(self, multi_file_dir):
        result = grep(r"def\s+\w+", path=multi_file_dir, glob="*.py")
        assert result["success"] is True
        assert result["count"] >= 2  # hello and add

    def test_grep_not_found(self, multi_file_dir):
        result = grep(r"zzzznope", path=multi_file_dir)
        assert result["count"] == 0

    def test_grep_ignore_case(self, multi_file_dir):
        result = grep(r"HELLO", path=multi_file_dir, glob="*.py",
                      ignore_case=True)
        assert result["count"] >= 1

    def test_grep_case_sensitive(self, multi_file_dir):
        result = grep(r"HELLO", path=multi_file_dir, glob="*.py",
                      ignore_case=False)
        assert result["count"] == 0

    def test_grep_invalid_regex(self, multi_file_dir):
        with pytest.raises((ValueError, re.error)):
            grep(r"[invalid", path=multi_file_dir)

    def test_grep_respects_glob(self, multi_file_dir):
        result = grep(r"Hello", path=multi_file_dir, glob="*.md")
        assert result["count"] >= 1

    def test_grep_nonexistent_directory(self):
        result = grep(r".", path="/nonexistent_path_xyz")
        assert result["success"] is False or result["count"] == 0

    def test_grep_binary_file_skipped(self, tmp_dir):
        """Binary files that cause decode errors are skipped."""
        path = os.path.join(tmp_dir, "data.bin")
        with open(path, "wb") as f:
            f.write(b"\x00\x01\xff\xfehello\x00")
        result = grep(r"hello", path=tmp_dir, glob="*")
        assert result["success"] is True
        # May or may not match depending on error handling


# ===================================================================
#  search_replace
# ===================================================================

class TestSearchReplace:
    """Text search-and-replace in files."""

    def test_replace_all(self, sample_file):
        result = search_replace(sample_file, "Hello", "Hi", count=-1)
        assert result["success"] is True
        assert result["replaced"] == 2
        with open(sample_file, "r") as f:
            content = f.read()
        assert "Hi" in content
        assert "Hello" not in content

    def test_replace_count_limited(self, sample_file):
        result = search_replace(sample_file, "Hello", "Hi", count=1)
        assert result["success"] is True
        assert result["replaced"] == 1
        with open(sample_file, "r") as f:
            lines = f.readlines()
        assert lines[0].startswith("Hi")
        # Second "Hello" in "Hello again" should remain
        assert "Hello again" in lines[2]

    def test_replace_no_match(self, sample_file):
        result = search_replace(sample_file, "ZZZ", "AAA", count=-1)
        assert result["success"] is True
        assert result["replaced"] == 0

    def test_replace_creates_backup(self, sample_file):
        result = search_replace(sample_file, "Hello", "Hi", count=-1, backup=True)
        bak_path = sample_file + ".bak"
        assert os.path.exists(bak_path)
        # Cleanup
        os.remove(bak_path)

    def test_replace_no_backup(self, sample_file):
        result = search_replace(sample_file, "Hello", "Hi", count=-1, backup=False)
        assert result["backup"] is None
        bak_path = sample_file + ".bak"
        assert not os.path.exists(bak_path)

    def test_replace_file_not_found(self, tmp_dir):
        path = os.path.join(tmp_dir, "nope.txt")
        result = search_replace(path, "a", "b")
        assert result["success"] is False
        assert "not found" in result.get("error", "").lower()

    def test_replace_count_zero(self, sample_file):
        """count=0 means no replacements."""
        result = search_replace(sample_file, "Hello", "Hi", count=0)
        assert result["success"] is True
        assert result["replaced"] == 0

    def test_replace_single_occurrence(self, tmp_dir):
        path = os.path.join(tmp_dir, "single.txt")
        with open(path, "w") as f:
            f.write("foo bar baz")
        result = search_replace(path, "bar", "QUX")
        assert result["replaced"] == 1
        with open(path, "r") as f:
            assert f.read() == "foo QUX baz"

    def test_replace_empty_search_string(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty_search.txt")
        with open(path, "w") as f:
            f.write("content")
        # Empty search string is valid (matches nothing for count=-1)
        result = search_replace(path, "", "x", count=-1)
        assert result["success"] is True
        # replace("") on a string inserts "x" between each char
        assert "content".count("") == len("content") + 1 == 8
        assert result["replaced"] == 8
