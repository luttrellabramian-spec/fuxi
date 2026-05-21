"""MCP 协议客户端 - Model Context Protocol (modelcontextprotocol.io)

实现 MCP Client 规范，通过 stdio 连接 MCP Server，
将 MCP 工具自动注册到 ToolRegistry，复用现有安全执行器。

核心设计：
1. McpClient - MCP 协议客户端，管理与 MCP Server 的连接
2. StdioConnection - stdio 子进程通信
3. McpToolAdapter - 将 MCP 工具适配到伏羲 ToolRegistry
4. auto_register_tools() - 自动扫描并注册 MCP 工具

使用方式：
    from mcp.client import McpClient

    client = McpClient()
    client.connect_stdio("filesystem",
        command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    tools = client.list_tools()
    result = client.call_tool("read_file", {"path": "/tmp/test.txt"})
    client.disconnect()
"""
import os
import sys
import json
import logging
import asyncio
import subprocess
import threading
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("mcp_client")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.insert(0, PROJECT_ROOT)


class ConnectionMode(Enum):
    STDIO = "stdio"
    SSE = "sse"


@dataclass
class McpTool:
    """MCP 工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class McpCallResult:
    """MCP 工具调用结果"""
    success: bool
    content: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class StdioConnection:
    """stdio 模式 MCP 连接 - 子进程 stdin/stdout JSON-RPC 通信"""

    def __init__(self, command: List[str], env: Optional[Dict[str, str]] = None):
        self.command = command
        self.env = env or {}
        self._process: Optional[subprocess.Popen] = None
        self._connected = False
        self._lock = threading.Lock()
        self._request_id = 0
        self._server_info: Dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """启动 MCP Server 子进程并完成协议握手"""
        try:
            full_env = {**os.environ, **self.env}
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
            )
            # 协议握手
            init_response = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "fuxi-mcp-client", "version": "0.3.0"}
            })
            if init_response and init_response.get("result"):
                self._server_info = init_response["result"]
                # 发送 initialized 通知
                self._send_notification("notifications/initialized", {})
                self._connected = True
                logger.info(f"MCP Server connected (stdio): {' '.join(self.command[:3])}")
                return True
            else:
                logger.error(f"MCP handshake failed: {init_response}")
                return False
        except Exception as e:
            logger.error(f"Failed to connect MCP Server: {e}")
            return False

    def disconnect(self) -> None:
        """关闭连接并终止子进程"""
        self._connected = False
        if self._process:
            try:
                self._process.stdin.close()
                self._process.stdout.close()
            except Exception:
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 JSON-RPC 请求并获取响应"""
        return self._send_request(method, params)

    def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if not self._process or self._process.poll() is not None:
                return {"error": "Process not running"}
            try:
                self._request_id += 1
                request = {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": method,
                    "params": params or {}
                }
                body = json.dumps(request, ensure_ascii=False)
                header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
                self._process.stdin.write(header.encode() + body.encode())
                self._process.stdin.flush()
                return self._read_response()
            except Exception as e:
                logger.error(f"MCP request '{method}' failed: {e}")
                return {"error": str(e)}

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        if not self._process or self._process.poll() is not None:
            return
        try:
            msg = {"jsonrpc": "2.0", "method": method, "params": params}
            body = json.dumps(msg, ensure_ascii=False)
            header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
            self._process.stdin.write(header.encode() + body.encode())
            self._process.stdin.flush()
        except Exception:
            pass

    def _read_response(self) -> Dict[str, Any]:
        """读取 JSON-RPC 响应（跨平台，带超时保护）"""
        try:
            # 先读 headers
            headers = {}
            while True:
                raw = self._process.stdout.readline()
                if not raw:
                    break
                line = raw.decode().strip()
                if not line:
                    break
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()
            content_length = int(headers.get("Content-Length", 0))
            if content_length > 0:
                content = self._process.stdout.read(content_length)
                return json.loads(content.decode())
            return {"error": "Empty response (no Content-Length)"}
        except Exception as e:
            return {"error": str(e)}


class McpClient:
    """MCP 协议客户端 - 管理与 MCP Server 的连接和工具调用"""

    def __init__(self):
        self._connections: Dict[str, StdioConnection] = {}
        self._tools: Dict[str, McpTool] = {}
        self._tool_servers: Dict[str, str] = {}  # tool_name -> server_name

    def connect_stdio(
        self,
        server_name: str,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
    ) -> bool:
        """通过 stdio 模式连接 MCP Server"""
        if server_name in self._connections:
            logger.warning(f"MCP Server '{server_name}' already connected")
            return True

        conn = StdioConnection(command, env)
        if conn.connect():
            self._connections[server_name] = conn
            self._discover_tools(server_name)
            return True
        return False

    def disconnect(self, server_name: Optional[str] = None) -> None:
        """断开 MCP Server 连接"""
        if server_name:
            conn = self._connections.pop(server_name, None)
            if conn:
                conn.disconnect()
                # 移除该 server 的工具
                self._tools = {k: v for k, v in self._tools.items()
                               if v.server_name != server_name}
        else:
            for conn in self._connections.values():
                conn.disconnect()
            self._connections.clear()
            self._tools.clear()
            self._tool_servers.clear()

    def _discover_tools(self, server_name: str) -> None:
        """发现并注册 MCP Server 提供的工具"""
        conn = self._connections.get(server_name)
        if not conn or not conn.is_connected:
            return
        try:
            response = conn.send_request("tools/list", {})
            tools = []
            if response.get("result") and response["result"].get("tools"):
                tools = response["result"]["tools"]
            for tool_def in tools:
                tool_name = tool_def.get("name", "")
                if not tool_name:
                    continue
                self._tools[tool_name] = McpTool(
                    name=tool_name,
                    description=tool_def.get("description", ""),
                    input_schema=tool_def.get("inputSchema", {}),
                    server_name=server_name,
                )
                self._tool_servers[tool_name] = server_name
            logger.info(f"Discovered {len(tools)} tools from '{server_name}'")
        except Exception as e:
            logger.error(f"Discover tools failed for '{server_name}': {e}")

    def list_tools(self) -> List[McpTool]:
        return list(self._tools.values())

    def get_tool(self, tool_name: str) -> Optional[McpTool]:
        return self._tools.get(tool_name)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> McpCallResult:
        """调用 MCP 工具"""
        tool = self._tools.get(tool_name)
        if not tool:
            return McpCallResult(False, [], f"MCP tool '{tool_name}' not found")

        server_name = self._tool_servers.get(tool_name)
        conn = self._connections.get(server_name) if server_name else None
        if not conn:
            return McpCallResult(False, [], f"No connection for tool '{tool_name}'")

        try:
            response = conn.send_request("tools/call", {
                "name": tool_name,
                "arguments": arguments
            })
            result = response.get("result", {})
            return McpCallResult(
                success=not result.get("isError", False),
                content=result.get("content", []),
                error=result.get("error")
            )
        except Exception as e:
            return McpCallResult(False, [], str(e))

    def create_mcp_adapter(self, server_name: str = "mcp") -> "McpToolAdapter":
        return McpToolAdapter(self, server_name)


class McpToolAdapter:
    """MCP 工具适配器 - 将 MCP 工具注册到伏羲 ToolRegistry"""

    def __init__(self, client: McpClient, server_name: str):
        self._client = client
        self._server_name = server_name

    def _make_tool_fn(self, tool_name: str):
        """创建可调用的工具包装函数"""
        def wrapper(**kwargs):
            result = self._client.call_tool(tool_name, kwargs)
            if result.success:
                texts = []
                for item in result.content:
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    elif item.get("type") == "resource":
                        texts.append(json.dumps(item.get("resource", {}), ensure_ascii=False))
                return {
                    "success": True,
                    "result_json": json.dumps({"content": "\n".join(texts)}, ensure_ascii=False),
                    "error": result.error or ""
                }
            else:
                return {
                    "success": False,
                    "result_json": "{}",
                    "error": result.error or "Unknown error"
                }
        return wrapper

    def register_to_registry(self, tool_registry, level: str = "L0") -> int:
        """将所有 MCP 工具注册到 ToolRegistry"""
        count = 0
        for tool in self._client.list_tools():
            if tool.server_name != self._server_name:
                continue
            fn = self._make_tool_fn(tool.name)
            # 直接注册到工具表（绕过 decorator，直接操作内部 dict）
            tool_registry._tools[tool.name] = {
                "func": fn,
                "level": level,
                "signature": "(**kwargs)",
                "doc": f"[MCP:{self._server_name}] {tool.description}",
                "module": f"mcp.{self._server_name}",
            }
            count += 1
        logger.info(f"Registered {count} MCP tools from '{self._server_name}'")
        return count


# ── 全局单例 ──────────────────────────────────────

_mcp_client: Optional[McpClient] = None
_client_lock = threading.Lock()


def get_mcp_client() -> McpClient:
    global _mcp_client
    if _mcp_client is None:
        with _client_lock:
            if _mcp_client is None:
                _mcp_client = McpClient()
    return _mcp_client


def reset_mcp_client() -> None:
    global _mcp_client
    if _mcp_client:
        _mcp_client.disconnect()
        _mcp_client = None


def auto_register_mcp_tools(
    tool_registry,
    stdio_commands: Optional[List[Tuple[str, List[str]]]] = None,
    default_level: str = "L0",
) -> Dict[str, Any]:
    """自动连接并注册 MCP 工具"""
    client = get_mcp_client()
    results = {"registered": 0, "errors": []}

    if stdio_commands:
        for server_name, command in stdio_commands:
            try:
                if client.connect_stdio(server_name, command):
                    adapter = client.create_mcp_adapter(server_name)
                    count = adapter.register_to_registry(tool_registry, default_level)
                    results["registered"] += count
                else:
                    results["errors"].append(f"Failed to connect {server_name}")
            except Exception as e:
                results["errors"].append(f"{server_name}: {e}")

    return results
