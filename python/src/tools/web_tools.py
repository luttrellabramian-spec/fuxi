"""网络工具 - HTTP 请求、API 调用、内容抓取"""
import json
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from . import registry


def _clean_html(text: str) -> str:
    """去除 HTML 标签"""
    import re
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


@registry.register(name="http_get", level="L0")
def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 10, max_length: int = 50000) -> Dict[str, Any]:
    """发送 HTTP GET 请求

    Args:
        url: 目标 URL
        headers: 额外请求头
        timeout: 超时秒数
        max_length: 响应体最大长度（截断）

    Returns:
        {"success": true, "status": 200, "content": "", "headers": {}}
    """
    try:
        import requests

        default_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Fuxi/1.0)",
            "Accept": "text/html,application/json,*/*",
        }
        if headers:
            default_headers.update(headers)

        resp = requests.get(url, headers=default_headers, timeout=timeout)
        content = resp.text[:max_length]

        return {
            "success": True,
            "status": resp.status_code,
            "content": content,
            "content_type": resp.headers.get("Content-Type", ""),
            "headers": dict(resp.headers),
            "url": resp.url,
            "truncated": len(resp.text) > max_length,
        }

    except ImportError:
        return {
            "success": False,
            "error": "requests library not installed. Run: pip install requests",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "url": url,
        }


@registry.register(name="http_post", level="L0")
def http_post(url: str, data: Any = None, json_data: Optional[Dict] = None, headers: Optional[Dict[str, str]] = None, timeout: int = 10) -> Dict[str, Any]:
    """发送 HTTP POST 请求

    Args:
        url: 目标 URL
        data: 表单数据（字典或字符串）
        json_data: JSON body（传入时优先使用）
        headers: 额外请求头
        timeout: 超时秒数

    Returns:
        {"success": true, "status": 200, "content": "", "headers": {}}
    """
    try:
        import requests

        default_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Fuxi/1.0)",
            "Accept": "application/json,*/*",
        }
        if headers:
            default_headers.update(headers)

        resp = requests.post(
            url,
            data=data,
            json=json_data,
            headers=default_headers,
            timeout=timeout,
        )

        return {
            "success": True,
            "status": resp.status_code,
            "content": resp.text[:50000],
            "content_type": resp.headers.get("Content-Type", ""),
            "headers": dict(resp.headers),
        }

    except ImportError:
        return {"success": False, "error": "requests not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="fetch_page", level="L1")
def fetch_page(url: str, clean_html: bool = True, max_length: int = 8000) -> Dict[str, Any]:
    """抓取网页内容（自动清理 HTML）

    Args:
        url: 目标 URL
        clean_html: 是否清理 HTML 标签
        max_length: 最大返回字符数

    Returns:
        {"success": true, "title": "", "content": "", "url": ""}
    """
    import re

    result = http_get(url, timeout=10, max_length=max_length * 2)
    if not result["success"]:
        return result

    content = result["content"]

    # 提取 title
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    # 清理 HTML
    if clean_html:
        content = _clean_html(content)

    # 截断
    content = content[:max_length]

    return {
        "success": True,
        "title": title,
        "content": content,
        "url": url,
        "truncated": len(content) >= max_length,
    }


@registry.register(name="fetch_api", level="L1")
def fetch_api(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, body: Optional[Any] = None) -> Dict[str, Any]:
    """调用 REST API（自动处理 JSON）

    Args:
        url: API 地址
        method: HTTP 方法（GET/POST/PUT/DELETE）
        headers: 请求头
        body: 请求体（dict 会自动序列化为 JSON）

    Returns:
        {"success": true, "status": 200, "data": {}, "headers": {}}
    """
    try:
        import requests

        default_headers = {
            "User-Agent": "Fuxi/1.0",
            "Accept": "application/json",
        }
        if headers:
            default_headers.update(headers)

        if body and isinstance(body, dict):
            default_headers["Content-Type"] = "application/json"
            body = json.dumps(body)

        resp = requests.request(
            method=method.upper(),
            url=url,
            data=body,
            headers=default_headers,
            timeout=15,
        )

        # 尝试解析 JSON
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:50000]}

        return {
            "success": True,
            "status": resp.status_code,
            "data": data,
            "headers": dict(resp.headers),
        }

    except ImportError:
        return {"success": False, "error": "requests not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="check_url", level="L0")
def check_url(url: str, timeout: int = 5) -> Dict[str, Any]:
    """检查 URL 是否可访问

    Args:
        url: 目标 URL
        timeout: 超时秒数

    Returns:
        {"success": true, "reachable": true, "status": 200, "latency_ms": N}
    """
    try:
        import requests

        start = time.time()
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        latency_ms = int((time.time() - start) * 1000)

        return {
            "success": True,
            "reachable": resp.status_code < 500,
            "status": resp.status_code,
            "latency_ms": latency_ms,
            "url": resp.url,
            "content_type": resp.headers.get("Content-Type", ""),
        }

    except ImportError:
        return {"success": False, "error": "requests not installed"}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "reachable": False,
            "url": url,
        }


@registry.register(name="parse_headers", level="L0")
def parse_headers(header_string: str) -> Dict[str, Any]:
    """解析 HTTP 请求头字符串

    Args:
        header_string: 形如 "Content-Type: application/json\\nAuthorization: Bearer xxx" 的字符串

    Returns:
        {"success": true, "headers": {"Content-Type": "..."}}
    """
    import re

    headers = {}
    lines = header_string.strip().split("\n")
    for line in lines:
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip()] = val.strip()

    return {
        "success": True,
        "headers": headers,
        "count": len(headers),
    }


@registry.register(name="extract_links", level="L1")
def extract_links(url: str) -> Dict[str, Any]:
    """从网页中提取所有链接

    Args:
        url: 目标 URL

    Returns:
        {"success": true, "links": [{"url": "", "text": ""}], "count": N}
    """
    import re
    from urllib.parse import urljoin

    result = http_get(url, max_length=100000)
    if not result["success"]:
        return result

    content = result["content"]
    parsed_base = urlparse(url)

    # 提取所有 href
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', content)

    links = []
    for href, text in hrefs:
        text = _clean_html(text).strip()[:100]
        if href.startswith("//"):
            full_url = f"{parsed_base.scheme}:{href}"
        elif href.startswith("/"):
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
        elif href.startswith("http"):
            full_url = href
        else:
            full_url = urljoin(url, href)

        if full_url.startswith("http"):
            links.append({"url": full_url, "text": text})

    return {
        "success": True,
        "links": links[:50],  # 最多 50 条
        "count": len(links),
    }
