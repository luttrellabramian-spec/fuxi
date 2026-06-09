from __future__ import annotations

"""搜索工具 - 支持多引擎内容检索"""
import re
from typing import Dict, Any

from . import registry
from ._path_utils import _validate_path  # 共享路径校验（v0.2.5 接入）


@registry.register(name="search_web", level="L1")
def search_web(query: str, engine: str = "duckduckgo", limit: int = 5) -> Dict[str, Any]:
    """搜索网页内容

    Args:
        query: 搜索关键词
        engine: 搜索引擎（duckduckgo / google / bing）
        limit: 返回结果数量上限

    Returns:
        {"success": true, "results": [{"title": "", "url": "", "snippet": ""}], "count": N}
    """
    try:
        import requests

        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Fuxi/1.0)"
        }

        if engine == "duckduckgo":
            try:
                # 尝试使用 duckduckgo-lite
                resp = requests.get(
                    "https://lite.duckduckgo.com/lite/",
                    params={"q": query},
                    headers=headers,
                    timeout=5,
                )
                if resp.status_code == 200:
                    # 解析 HTML 结果
                    snippets = re.findall(
                        r'<a class="result-link"[^>]*href="([^"]+)"[^>]*>[^<]*</a>[^<]*<a class="result-snippet"[^>]*>([^<]+)</a>',
                        resp.text,
                    )
                    for url, snippet in snippets[:limit]:
                        results.append({
                            "title": "",
                            "url": url,
                            "snippet": re.sub(r'<[^>]+>', '', snippet).strip()[:300],
                        })
            except Exception:
                pass

        if not results:
            # 回退：尝试简单 HTTP 请求
            try:
                resp = requests.get(
                    "https://www.google.com/search",
                    params={"q": query, "num": limit},
                    headers=headers,
                    timeout=5,
                )
                if resp.status_code == 200:
                    titles = re.findall(r'<h3[^>]*>([^<]+)</h3>', resp.text)
                    links = re.findall(r'<a href="(https?://[^"]+)"[^>]*class="[^"]*action[^"]*"', resp.text)
                    for i, (title, url) in enumerate(zip(titles, links[:limit])):
                        results.append({"title": title.strip(), "url": url, "snippet": ""})
            except Exception:
                pass

        if not results:
            return {
                "success": False,
                "error": f"Search engine '{engine}' returned no results or is unavailable",
                "results": [],
                "count": 0,
            }

        return {
            "success": True,
            "results": results,
            "count": len(results),
            "engine": engine,
        }

    except ImportError:
        return {
            "success": False,
            "error": "requests library not installed. Install with: pip install requests",
            "results": [],
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "results": [],
        }


@registry.register(name="search_file", level="L0")
def search_file(query: str, directory: str = ".", file_pattern: str = "*.py", case_sensitive: bool = False) -> Dict[str, Any]:
    """在指定目录下搜索文件内容

    Args:
        query: 要搜索的关键词
        directory: 搜索目录（默认当前目录）
        file_pattern: 文件匹配模式（如 *.py, *.md, *.txt）
        case_sensitive: 是否区分大小写

    Returns:
        {"success": true, "matches": [{"file": "", "line": N, "content": ""}], "count": N}
    """
    import fnmatch
    import os

    # 路径校验：拒绝 ../ 遍历、跳出项目根目录、进入敏感目录
    try:
        directory = _validate_path(directory)
    except ValueError as e:
        return {"success": False, "error": str(e), "matches": []}

    if not os.path.isdir(directory):
        return {"success": False, "error": f"Directory not found: {directory}", "matches": []}

    matches = []
    query_lower = query.lower() if not case_sensitive else query

    try:
        for root, _, filenames in os.walk(directory, followlinks=False):
            for filename in filenames:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_no, line in enumerate(f, 1):
                            content = line if case_sensitive else line.lower()
                            if query in content if case_sensitive else query_lower in content:
                                matches.append({
                                    "file": filepath,
                                    "line": line_no,
                                    "content": line.rstrip()[:200],
                                })
                                if len(matches) >= 100:  # 上限
                                    break
                except Exception:
                    continue
    except Exception as e:
        return {"success": False, "error": str(e), "matches": []}

    return {
        "success": True,
        "matches": matches,
        "count": len(matches),
        "directory": directory,
        "pattern": file_pattern,
    }


@registry.register(name="search_replace", level="L1")
def search_replace(file_path: str, search: str, replace: str, count: int = -1, backup: bool = True) -> Dict[str, Any]:
    """在文件中搜索并替换文本

    Args:
        file_path: 文件路径
        search: 要搜索的文本
        replace: 替换为的文本
        count: 替换次数（-1 表示全部替换）
        backup: 是否创建备份（.bak）

    Returns:
        {"success": true, "replaced": N, "backup": "path"}
    """
    import os
    import shutil

    # 写工具更危险：先校验路径再做任何 open
    try:
        file_path = _validate_path(file_path)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if not os.path.isfile(file_path):
        return {"success": False, "error": f"File not found: {file_path}"}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if backup:
            backup_path = file_path + ".bak"
            shutil.copy2(file_path, backup_path)
        else:
            backup_path = None

        if count == -1:
            replaced = content.count(search)
            new_content = content.replace(search, replace)
        else:
            result_parts = []
            remaining = content
            actual_replaced = 0
            for _ in range(count):
                idx = remaining.find(search)
                if idx == -1:
                    break
                result_parts.append(remaining[:idx])
                result_parts.append(replace)
                remaining = remaining[idx + len(search):]
                actual_replaced += 1
            result_parts.append(remaining)
            new_content = "".join(result_parts)
            replaced = actual_replaced

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "replaced": replaced,
            "backup": backup_path,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="grep", level="L0")
def grep(pattern: str, path: str = ".", glob: str = "*", ignore_case: bool = False) -> Dict[str, Any]:
    """正则表达式文件搜索

    Args:
        pattern: 正则表达式
        path: 搜索路径
        glob: 文件名匹配（默认所有文件）
        ignore_case: 忽略大小写

    Returns:
        {"success": true, "matches": [{"file": "", "line": N, "content": ""}], "count": N}
    """
    import fnmatch
    import os
    import re

    flags = re.IGNORECASE if ignore_case else 0

    try:
        compiled = re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}")

    # 路径校验
    try:
        path = _validate_path(path)
    except ValueError as e:
        return {"success": False, "error": str(e), "matches": []}

    matches = []
    try:
        for root, _, filenames in os.walk(path, followlinks=False):
            for filename in filenames:
                if not fnmatch.fnmatch(filename, glob):
                    continue
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        for line_no, line in enumerate(f, 1):
                            if compiled.search(line):
                                matches.append({
                                    "file": filepath,
                                    "line": line_no,
                                    "content": line.rstrip()[:200],
                                })
                                if len(matches) >= 100:
                                    break
                except Exception:
                    continue
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "matches": matches,
        "count": len(matches),
    }
