from __future__ import annotations

"""网络工具 - HTTP 请求、API 调用、内容抓取"""
import json
import re
import time
import ipaddress
from typing import Dict, Any, Optional
from urllib.parse import urlparse

from . import registry

# 禁止访问的内网 IP 范围
_PRIVATE_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),      # Loopback
    ipaddress.ip_network('10.0.0.0/8'),        # Private Class A
    ipaddress.ip_network('172.16.0.0/12'),     # Private Class B
    ipaddress.ip_network('192.168.0.0/16'),    # Private Class C
    ipaddress.ip_network('169.254.0.0/16'),    # Link-local
    ipaddress.ip_network('::1/128'),           # IPv6 Loopback
    ipaddress.ip_network('fc00::/7'),          # IPv6 Private
    ipaddress.ip_network('fe80::/10'),         # IPv6 Link-local
]

# 允许的协议
_ALLOWED_SCHEMES = {'http', 'https'}

# 最大 URL 长度
_MAX_URL_LENGTH = 2048


def _validate_url(url: str) -> str:
    """验证 URL，防止 SSRF 攻击
    
    Args:
        url: 原始 URL
        
    Returns:
        验证后的 URL
        
    Raises:
        ValueError: URL 不合法
    """
    # 检查 URL 长度
    if len(url) > _MAX_URL_LENGTH:
        raise ValueError(f"URL too long: {len(url)} chars (max: {_MAX_URL_LENGTH})")

    # 解析 URL
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Invalid URL format")

    # 检查协议
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported scheme: {parsed.scheme} (allowed: {_ALLOWED_SCHEMES})")

    # 检查是否为空主机
    if not parsed.hostname:
        raise ValueError(f"Missing hostname")

    # 解析主机名到 IP 地址
    try:
        import socket
        ip_str = socket.gethostbyname(parsed.hostname)
        ip = ipaddress.ip_address(ip_str)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {parsed.hostname}")
    except ValueError:
        raise ValueError(f"Invalid IP address: {ip_str}")

    # 检查是否是内网 IP
    for network in _PRIVATE_NETWORKS:
        if ip in network:
            raise ValueError(f"Access denied: private network IP {ip}")

    # 检查端口（禁止常见内部服务端口）
    if parsed.port:
        _blocked_ports = {22, 23, 25, 445, 3389, 5900, 6379, 27017}
        if parsed.port in _blocked_ports:
            raise ValueError(f"Access denied: blocked port {parsed.port}")

    return url


def _clean_html(text: str) -> str:
    """去除 HTML 标签"""
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
        # 验证 URL
        validated_url = _validate_url(url)

        import requests

        default_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Fuxi/1.0)",
            "Accept": "text/html,application/json,*/*",
        }
        if headers:
            default_headers.update(headers)

        resp = requests.get(validated_url, headers=default_headers, timeout=timeout)
        content = resp.text[:max_length]

        # 过滤敏感 headers
        safe_headers = {k: v for k, v in resp.headers.items() 
                       if k.lower() not in ('set-cookie', 'authorization', 'proxy-authorization')}

        return {
            "success": True,
            "status": resp.status_code,
            "content": content,
            "content_type": resp.headers.get("Content-Type", ""),
            "headers": safe_headers,
            "url": resp.url,
            "truncated": len(resp.text) > max_length,
        }

    except ImportError:
        return {
            "success": False,
            "error": "requests library not installed. Run: pip install requests",
        }
    except ValueError as e:
        return {
            "success": False,
            "error": f"URL validation failed: {str(e)}",
            "url": url,
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
        # 验证 URL
        validated_url = _validate_url(url)

        import requests

        default_headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Fuxi/1.0)",
            "Accept": "application/json,*/*",
        }
        if headers:
            default_headers.update(headers)

        resp = requests.post(
            validated_url,
            data=data,
            json=json_data,
            headers=default_headers,
            timeout=timeout,
        )

        # 过滤敏感 headers
        safe_headers = {k: v for k, v in resp.headers.items() 
                       if k.lower() not in ('set-cookie', 'authorization', 'proxy-authorization')}

        return {
            "success": True,
            "status": resp.status_code,
            "content": resp.text[:50000],
            "content_type": resp.headers.get("Content-Type", ""),
            "headers": safe_headers,
        }

    except ImportError:
        return {"success": False, "error": "requests not installed"}
    except ValueError as e:
        return {"success": False, "error": f"URL validation failed: {str(e)}"}
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
    # 验证 URL
    try:
        validated_url = _validate_url(url)
    except ValueError as e:
        return {"success": False, "error": f"URL validation failed: {str(e)}"}

    result = http_get(validated_url, timeout=10, max_length=max_length * 2)
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
        method: HTTP 方法（GET/POST/PUT/DELETE/PATCH）
        headers: 请求头
        body: 请求体（dict 会自动序列化为 JSON）

    Returns:
        {"success": true, "status": 200, "data": {}, "headers": {}}
    """
    try:
        # 验证 URL
        validated_url = _validate_url(url)

        # 验证 HTTP 方法
        allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}
        method_upper = method.upper()
        if method_upper not in allowed_methods:
            return {"success": False, "error": f"Unsupported method: {method} (allowed: {allowed_methods})"}

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
            method=method_upper,
            url=validated_url,
            data=body,
            headers=default_headers,
            timeout=15,
        )

        # 尝试解析 JSON
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:50000]}

        # 过滤敏感 headers
        safe_headers = {k: v for k, v in resp.headers.items() 
                       if k.lower() not in ('set-cookie', 'authorization', 'proxy-authorization')}

        return {
            "success": True,
            "status": resp.status_code,
            "data": data,
            "headers": safe_headers,
        }

    except ImportError:
        return {"success": False, "error": "requests not installed"}
    except ValueError as e:
        return {"success": False, "error": f"URL validation failed: {str(e)}"}
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
        # 验证 URL
        validated_url = _validate_url(url)

        import requests

        start = time.time()
        resp = requests.head(validated_url, timeout=timeout, allow_redirects=True)
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
    except ValueError as e:
        return {"success": False, "error": f"URL validation failed: {str(e)}", "reachable": False}
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
    from urllib.parse import urljoin

    # 验证 URL
    try:
        validated_url = _validate_url(url)
    except ValueError as e:
        return {"success": False, "error": f"URL validation failed: {str(e)}"}

    result = http_get(validated_url, max_length=100000)
    if not result["success"]:
        return result

    content = result["content"]
    parsed_base = urlparse(validated_url)

    # 提取所有 href
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', content)

    links = []
    seen = set()  # 去重
    for href, text in hrefs:
        text = _clean_html(text).strip()[:100]
        if href.startswith("//"):
            full_url = f"{parsed_base.scheme}:{href}"
        elif href.startswith("/"):
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
        elif href.startswith("http"):
            full_url = href
        else:
            full_url = urljoin(validated_url, href)

        if full_url.startswith("http") and full_url not in seen:
            seen.add(full_url)
            links.append({"url": full_url, "text": text})

    return {
        "success": True,
        "links": links[:50],  # 最多 50 条
        "count": len(links),
    }
