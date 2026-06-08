"""伏羲 MCP 客户端测试 — McpClient / StdioConnection / McpToolAdapter / get_mcp_client 单例"""
import sys
import os
import json
import threading
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
import mcp.client as mcp_client
from mcp.client import (
    McpClient, StdioConnection, McpTool, McpCallResult,
    McpToolAdapter, ConnectionMode,
    get_mcp_client, reset_mcp_client, auto_register_mcp_tools,
)


@pytest.fixture(autouse=True)
def reset_global_mcp_client():
    """每个测试前重置全局单例。"""
    reset_mcp_client()
    yield
    reset_mcp_client()


# ════════════════════════════════════════════════════════════════════
# 1. McpTool / McpCallResult dataclass
# ════════════════════════════════════════════════════════════════════


class TestMcpDataClasses:
    def test_mcp_tool_defaults(self):
        """McpTool 应有正确的默认字段。"""
        tool = McpTool(name="read_file", description="reads a file")
        assert tool.name == "read_file"
        assert tool.description == "reads a file"
        assert tool.input_schema == {}
        assert tool.server_name == ""

    def test_mcp_tool_with_schema(self):
        """McpTool 应支持自定义 input_schema。"""
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        tool = McpTool(name="t", description="d", input_schema=schema, server_name="srv")
        assert tool.input_schema == schema
        assert tool.server_name == "srv"

    def test_mcp_call_result_defaults(self):
        """McpCallResult 失败时 content 应为空列表。"""
        result = McpCallResult(success=False, error="boom")
        assert result.success is False
        assert result.content == []
        assert result.error == "boom"

    def test_mcp_call_result_success(self):
        """McpCallResult 成功时携带 content。"""
        result = McpCallResult(success=True, content=[{"type": "text", "text": "hi"}])
        assert result.success is True
        assert result.content[0]["text"] == "hi"


# ════════════════════════════════════════════════════════════════════
# 2. ConnectionMode enum
# ════════════════════════════════════════════════════════════════════


class TestConnectionMode:
    def test_enum_values(self):
        """ConnectionMode 枚举值应稳定。"""
        assert ConnectionMode.STDIO.value == "stdio"
        assert ConnectionMode.SSE.value == "sse"


# ════════════════════════════════════════════════════════════════════
# 3. StdioConnection — 子进程通信
# ════════════════════════════════════════════════════════════════════


class TestStdioConnection:
    def test_init_defaults(self):
        """未指定 env 时应使用空 dict。"""
        conn = StdioConnection(command=["echo", "hi"])
        assert conn.command == ["echo", "hi"]
        assert conn.env == {}
        assert conn._process is None
        assert conn.is_connected is False

    def test_init_with_env(self):
        """指定 env 时应被记录。"""
        conn = StdioConnection(command=["x"], env={"K": "V"})
        assert conn.env == {"K": "V"}

    def test_disconnect_when_not_connected(self):
        """未连接时 disconnect 不应抛错。"""
        conn = StdioConnection(command=["x"])
        conn.disconnect()  # 应静默成功
        assert conn.is_connected is False
        assert conn._process is None

    def test_disconnect_terminates_process(self):
        """disconnect 应终止并清理子进程。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        conn._process = mock_proc
        conn._connected = True
        conn.disconnect()
        mock_proc.stdin.close.assert_called_once()
        mock_proc.stdout.close.assert_called_once()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert conn._process is None
        assert conn._connected is False

    def test_disconnect_kills_on_timeout(self):
        """disconnect 等待超时应 kill -9。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        import subprocess
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=5)
        conn._process = mock_proc
        conn._connected = True
        conn.disconnect()
        mock_proc.kill.assert_called_once()

    def test_send_request_no_process(self):
        """无子进程时 _send_request 应返回 error 字典。"""
        conn = StdioConnection(command=["x"])
        result = conn._send_request("tools/list", {})
        assert "error" in result
        assert "Process not running" in result["error"]

    def test_send_request_dead_process(self):
        """进程已退出时 _send_request 应返回 error。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # 已退出
        conn._process = mock_proc
        result = conn._send_request("tools/list", {})
        assert "error" in result

    def test_send_request_writes_json_rpc(self):
        """_send_request 应写入带 Content-Length 头的 JSON-RPC 消息。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # 运行中
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        # 模拟空响应（不写 Content-Length）
        mock_proc.stdout.readline.return_value = b""
        conn._process = mock_proc

        result = conn._send_request("tools/list", {"k": "v"})
        # 验证 stdin 写入被调用
        assert mock_proc.stdin.write.called
        # 写入内容应包含 JSON-RPC
        written = mock_proc.stdin.write.call_args[0][0]
        decoded = written.decode("utf-8")
        assert "Content-Length" in decoded
        assert '"jsonrpc"' in decoded
        assert '"method"' in decoded
        assert "tools/list" in decoded

    def test_send_request_handles_exception(self):
        """_send_request 内异常应被捕获并返回 error 字典。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin.write.side_effect = IOError("pipe closed")
        conn._process = mock_proc
        result = conn._send_request("tools/list", {})
        assert "error" in result
        assert "pipe closed" in result["error"]

    def test_send_notification_silent_on_dead_process(self):
        """_send_notification 在进程死掉时应静默返回。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        conn._process = mock_proc
        # 不应抛错
        conn._send_notification("notifications/initialized", {})
        # 且不应写任何东西
        mock_proc.stdin.write.assert_not_called()

    def test_send_notification_writes_when_alive(self):
        """_send_notification 在进程存活时应写入。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        conn._process = mock_proc
        conn._send_notification("notifications/initialized", {})
        assert mock_proc.stdin.write.called

    def test_read_response_with_content_length(self):
        """_read_response 应按 Content-Length 读取并解析 JSON。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()
        header = f"Content-Length: {len(body)}\r\n".encode()
        # 第一次 readline 返回 header，第二次返回空行触发 break
        mock_proc.stdout.readline.side_effect = [header, b""]
        mock_proc.stdout.read.return_value = body
        conn._process = mock_proc
        result = conn._read_response()
        assert result["result"]["ok"] is True

    def test_read_response_no_content_length(self):
        """无 Content-Length 时应返回 error。"""
        conn = StdioConnection(command=["x"])
        mock_proc = MagicMock()
        # 第一次 readline 返回空 → break 循环
        mock_proc.stdout.readline.return_value = b""
        conn._process = mock_proc
        result = conn._read_response()
        assert "error" in result

    def test_connect_handshake_success(self):
        """connect 协议握手成功应返回 True。"""
        conn = StdioConnection(command=["fake-server"])
        with patch("subprocess.Popen") as mock_popen_cls:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            handshake_body = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"serverInfo": {"name": "fake", "version": "0.0.1"}}
            }).encode()
            handshake_header = f"Content-Length: {len(handshake_body)}\r\n".encode()
            # 第一次 readline 返回 header，第二次返回空 → 退出 header 循环
            mock_proc.stdout.readline.side_effect = [handshake_header, b""]
            mock_proc.stdout.read.return_value = handshake_body
            mock_popen_cls.return_value = mock_proc

            assert conn.connect() is True
            assert conn._connected is True
            assert conn._server_info.get("serverInfo", {}).get("name") == "fake"

    def test_connect_handshake_failure(self):
        """握手失败应返回 False。"""
        conn = StdioConnection(command=["x"])
        with patch("subprocess.Popen") as mock_popen_cls:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "fail"}}).encode()
            header = f"Content-Length: {len(body)}\r\n".encode()
            mock_proc.stdout.readline.side_effect = [header, b""]
            mock_proc.stdout.read.return_value = body
            mock_popen_cls.return_value = mock_proc
            assert conn.connect() is False
            assert conn._connected is False

    def test_connect_subprocess_fails(self):
        """Popen 抛异常应被捕获。"""
        conn = StdioConnection(command=["bad"])
        with patch("subprocess.Popen", side_effect=OSError("not found")):
            assert conn.connect() is False


# ════════════════════════════════════════════════════════════════════
# 4. McpClient — 客户端主类
# ════════════════════════════════════════════════════════════════════


class TestMcpClient:
    def test_init_empty(self):
        """新客户端应没有任何连接和工具。"""
        client = McpClient()
        assert client._connections == {}
        assert client._tools == {}
        assert client._tool_servers == {}

    def test_connect_stdio_already_connected_returns_true(self):
        """已连接时再调 connect_stdio 应返回 True 且不重复连接。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        client._connections["srv"] = mock_conn
        assert client.connect_stdio("srv", ["x"]) is True
        # 不应调用 connect
        mock_conn.connect.assert_not_called()

    def test_connect_stdio_success_registers_tools(self):
        """连接成功时应触发工具发现。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.is_connected = True
        # 模拟 send_request 返回工具列表
        mock_conn.send_request.return_value = {
            "result": {
                "tools": [
                    {"name": "read_file", "description": "Read", "inputSchema": {}},
                    {"name": "write_file", "description": "Write", "inputSchema": {}},
                ]
            }
        }
        with patch("mcp.client.StdioConnection", return_value=mock_conn):
            # 模拟 connect 返回 True
            mock_conn.connect.return_value = True
            assert client.connect_stdio("srv", ["cmd"]) is True

        assert "read_file" in client._tools
        assert "write_file" in client._tools
        assert client._tools["read_file"].server_name == "srv"
        assert client._tool_servers["read_file"] == "srv"

    def test_connect_stdio_failure(self):
        """连接失败时应返回 False 且不加入。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.connect.return_value = False
        with patch("mcp.client.StdioConnection", return_value=mock_conn):
            assert client.connect_stdio("srv", ["bad"]) is False
        assert "srv" not in client._connections

    def test_disconnect_specific_server(self):
        """指定 server_name 时只断开该连接并清理工具。"""
        client = McpClient()
        mock_conn = MagicMock()
        client._connections["srv1"] = mock_conn
        client._tools["t1"] = McpTool(name="t1", description="", server_name="srv1")
        client._tools["t2"] = McpTool(name="t2", description="", server_name="srv2")
        client._tool_servers["t1"] = "srv1"
        client._tool_servers["t2"] = "srv2"

        client.disconnect("srv1")
        mock_conn.disconnect.assert_called_once()
        assert "srv1" not in client._connections
        assert "t1" not in client._tools
        assert "t2" in client._tools

    def test_disconnect_all(self):
        """不传 server_name 应断开所有连接。"""
        client = McpClient()
        c1, c2 = MagicMock(), MagicMock()
        client._connections["s1"] = c1
        client._connections["s2"] = c2
        client._tools["t"] = McpTool(name="t", description="")
        client._tool_servers["t"] = "s1"

        client.disconnect()
        c1.disconnect.assert_called_once()
        c2.disconnect.assert_called_once()
        assert client._connections == {}
        assert client._tools == {}
        assert client._tool_servers == {}

    def test_list_tools_returns_all(self):
        """list_tools 应返回所有工具的列表。"""
        client = McpClient()
        client._tools["a"] = McpTool(name="a", description="")
        client._tools["b"] = McpTool(name="b", description="")
        tools = client.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"a", "b"}

    def test_get_tool_existing(self):
        """存在的工具应返回 McpTool。"""
        client = McpClient()
        client._tools["a"] = McpTool(name="a", description="d")
        assert client.get_tool("a").description == "d"

    def test_get_tool_missing(self):
        """不存在的工具应返回 None。"""
        client = McpClient()
        assert client.get_tool("missing") is None

    def test_call_tool_not_found(self):
        """调用不存在的工具应返回 failure。"""
        client = McpClient()
        result = client.call_tool("missing", {})
        assert result.success is False
        assert "not found" in result.error

    def test_call_tool_no_connection(self):
        """工具存在但无连接时应返回 failure。"""
        client = McpClient()
        client._tools["t"] = McpTool(name="t", description="")
        client._tool_servers["t"] = "no_such_server"
        result = client.call_tool("t", {})
        assert result.success is False
        assert "No connection" in result.error

    def test_call_tool_success(self):
        """完整链路：调通 -> 成功。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.send_request.return_value = {
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": "hello"}]
            }
        }
        client._connections["srv"] = mock_conn
        client._tools["t"] = McpTool(name="t", description="", server_name="srv")
        client._tool_servers["t"] = "srv"

        result = client.call_tool("t", {"x": 1})
        assert result.success is True
        assert result.content[0]["text"] == "hello"
        mock_conn.send_request.assert_called_once_with("tools/call", {
            "name": "t",
            "arguments": {"x": 1}
        })

    def test_call_tool_server_error(self):
        """MCP server 返回 isError=True 应记为失败。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.send_request.return_value = {
            "result": {"isError": True, "content": [], "error": "boom"}
        }
        client._connections["srv"] = mock_conn
        client._tools["t"] = McpTool(name="t", description="", server_name="srv")
        client._tool_servers["t"] = "srv"

        result = client.call_tool("t", {})
        assert result.success is False
        assert result.error == "boom"

    def test_call_tool_exception_caught(self):
        """call_tool 抛异常应被捕获。"""
        client = McpClient()
        mock_conn = MagicMock()
        mock_conn.send_request.side_effect = RuntimeError("connection lost")
        client._connections["srv"] = mock_conn
        client._tools["t"] = McpTool(name="t", description="", server_name="srv")
        client._tool_servers["t"] = "srv"

        result = client.call_tool("t", {})
        assert result.success is False
        assert "connection lost" in result.error

    def test_create_mcp_adapter(self):
        """create_mcp_adapter 应返回 McpToolAdapter。"""
        client = McpClient()
        adapter = client.create_mcp_adapter("srv")
        assert isinstance(adapter, McpToolAdapter)
        assert adapter._server_name == "srv"


# ════════════════════════════════════════════════════════════════════
# 5. McpToolAdapter — 注册到 ToolRegistry
# ════════════════════════════════════════════════════════════════════


class TestMcpToolAdapter:
    def test_make_tool_fn_text_content(self):
        """包装函数应处理 type=text 的 content。"""
        client = MagicMock()
        client.call_tool.return_value = McpCallResult(
            success=True,
            content=[{"type": "text", "text": "hello"}]
        )
        adapter = McpToolAdapter(client, "srv")
        fn = adapter._make_tool_fn("t")
        result = fn()
        assert result["success"] is True
        assert "hello" in result["result_json"]

    def test_make_tool_fn_resource_content(self):
        """包装函数应处理 type=resource 的 content（序列化为 JSON）。"""
        client = MagicMock()
        client.call_tool.return_value = McpCallResult(
            success=True,
            content=[{"type": "resource", "resource": {"k": "v"}}]
        )
        adapter = McpToolAdapter(client, "srv")
        fn = adapter._make_tool_fn("t")
        result = fn()
        assert result["success"] is True
        # 解析 result_json 后应包含原始字段
        decoded = json.loads(result["result_json"])
        # decoded["content"] 是 join 后的字符串，需要再解析
        inner = json.loads(decoded["content"])
        assert inner == {"k": "v"}

    def test_make_tool_fn_failure(self):
        """失败时应返回 success=False + error。"""
        client = MagicMock()
        client.call_tool.return_value = McpCallResult(success=False, error="boom")
        adapter = McpToolAdapter(client, "srv")
        fn = adapter._make_tool_fn("t")
        result = fn()
        assert result["success"] is False
        assert result["error"] == "boom"

    def test_register_to_registry_only_matching_server(self):
        """register_to_registry 只注册匹配 server 的工具。"""
        client = MagicMock()
        client.list_tools.return_value = [
            McpTool(name="t1", description="d1", server_name="srv"),
            McpTool(name="t2", description="d2", server_name="other"),
        ]
        # 工具注册表必须有 _tools 字典属性
        class _FakeRegistry:
            def __init__(self):
                self._tools = {}
        registry = _FakeRegistry()
        adapter = McpToolAdapter(client, "srv")
        count = adapter.register_to_registry(registry, level="L0")
        assert count == 1
        assert "t1" in registry._tools
        assert "t2" not in registry._tools
        assert registry._tools["t1"]["level"] == "L0"
        assert "srv" in registry._tools["t1"]["doc"]


# ════════════════════════════════════════════════════════════════════
# 6. get_mcp_client / reset_mcp_client / auto_register_mcp_tools
# ════════════════════════════════════════════════════════════════════


class TestGlobalMcpClient:
    def test_get_mcp_client_singleton(self):
        """get_mcp_client 应返回同一实例。"""
        a = get_mcp_client()
        b = get_mcp_client()
        assert a is b

    def test_reset_mcp_client_disconnects(self):
        """reset_mcp_client 应断开并清空。"""
        client = get_mcp_client()
        mock_conn = MagicMock()
        client._connections["x"] = mock_conn
        reset_mcp_client()
        assert mcp_client._mcp_client is None
        # 验证 disconnect 被调用（在 reset 之前 mock 已保存）
        mock_conn.disconnect.assert_called_once()

    def test_reset_when_no_client_is_safe(self):
        """无客户端时 reset 不应抛错。"""
        mcp_client._mcp_client = None
        reset_mcp_client()  # 不抛错

    def test_auto_register_mcp_tools_no_commands(self):
        """无 stdio_commands 时只应返回空结果。"""
        registry = {"_tools": {}}
        result = auto_register_mcp_tools(registry)
        assert result["registered"] == 0
        assert result["errors"] == []

    def test_auto_register_mcp_tools_connect_failure(self):
        """连接失败应被记入 errors。"""
        registry = {"_tools": {}}
        client = get_mcp_client()
        with patch.object(client, "connect_stdio", return_value=False):
            result = auto_register_mcp_tools(
                registry,
                stdio_commands=[("srv", ["bad"])],
            )
        assert result["registered"] == 0
        assert len(result["errors"]) == 1
        assert "srv" in result["errors"][0]

    def test_auto_register_mcp_tools_full_path(self):
        """完整链路：连接 -> 适配 -> 注册。"""
        class _FakeRegistry:
            def __init__(self):
                self._tools = {}
        registry = _FakeRegistry()
        client = get_mcp_client()
        with patch.object(client, "connect_stdio", return_value=True), \
             patch.object(client, "list_tools", return_value=[
                 McpTool(name="t1", description="d1", server_name="srv"),
                 McpTool(name="t2", description="d2", server_name="srv"),
             ]):
            result = auto_register_mcp_tools(
                registry,
                stdio_commands=[("srv", ["cmd"])],
                default_level="L1",
            )
        assert result["registered"] == 2
        assert result["errors"] == []
        assert "t1" in registry._tools
        assert registry._tools["t1"]["level"] == "L1"
