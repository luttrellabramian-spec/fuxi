"""MCP 客户端测试 - 通过 mock MCP server 验证协议交互

使用 mock_mcp_server.py 作为子进程，测试：
- 协议握手（initialize）
- 工具发现（tools/list）
- 工具调用（tools/call）
- 断开/重连
- McpToolAdapter 注册到 ToolRegistry
- auto_register_mcp_tools 函数
"""
import sys
import os
import time
import json
import subprocess
import signal
import pytest

# ── 路径设置 ──────────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

MOCK_SERVER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "mock_mcp_server.py"))

from mcp.client import McpClient, McpToolAdapter, auto_register_mcp_tools, get_mcp_client, reset_mcp_client
from tools import registry


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_server_script():
    """返回 mock MCP server 的 Python 命令"""
    return ["python", MOCK_SERVER_PATH]


@pytest.fixture
def mcp_client():
    """干净的 McpClient 实例"""
    reset_mcp_client()
    client = get_mcp_client()
    yield client
    client.disconnect()
    reset_mcp_client()


# ═══════════════════════════════════════════════════════════
# A. 连接与协议握手
# ═══════════════════════════════════════════════════════════

class TestMcpClientConnect:
    """MCP 客户端连接测试"""

    def test_connect_stdio(self, mcp_client, mock_server_script):
        """通过 stdio 连接到 mock server"""
        result = mcp_client.connect_stdio(
            server_name="mock",
            command=mock_server_script,
        )
        assert result is True, "连接应成功"

    def test_connect_duplicate(self, mcp_client, mock_server_script):
        """重复连接同一 server 不报错"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.connect_stdio("mock", mock_server_script)
        assert result is True  # 应返回 True（已连接）

    def test_connect_twice_different(self, mcp_client, mock_server_script):
        """连接不同的 server"""
        r1 = mcp_client.connect_stdio("mock1", mock_server_script)
        r2 = mcp_client.connect_stdio("mock2", mock_server_script)
        assert r1 is True
        assert r2 is True
        assert len(mcp_client._connections) == 2

    def test_disconnect(self, mcp_client, mock_server_script):
        """断开连接"""
        mcp_client.connect_stdio("mock", mock_server_script)
        mcp_client.disconnect("mock")
        assert "mock" not in mcp_client._connections
        assert len(mcp_client.list_tools()) == 0

    def test_disconnect_all(self, mcp_client, mock_server_script):
        """断开所有连接"""
        mcp_client.connect_stdio("mock1", mock_server_script)
        mcp_client.connect_stdio("mock2", mock_server_script)
        mcp_client.disconnect()
        assert len(mcp_client._connections) == 0
        assert len(mcp_client.list_tools()) == 0

    def test_reconnect(self, mcp_client, mock_server_script):
        """断开后重新连接"""
        mcp_client.connect_stdio("mock", mock_server_script)
        mcp_client.disconnect("mock")
        result = mcp_client.connect_stdio("mock", mock_server_script)
        assert result is True
        tools = mcp_client.list_tools()
        assert len(tools) == 4  # mock 有 4 个工具

    def test_connection_mode(self, mcp_client, mock_server_script):
        """连接模式正确"""
        mcp_client.connect_stdio("mock", mock_server_script)
        conn = mcp_client._connections.get("mock")
        assert conn is not None
        assert conn.is_connected is True


# ═══════════════════════════════════════════════════════════
# B. 工具发现
# ═══════════════════════════════════════════════════════════

class TestMcpToolDiscovery:
    """MCP 工具发现测试"""

    def test_discover_tools_count(self, mcp_client, mock_server_script):
        """发现 4 个 mock 工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        tools = mcp_client.list_tools()
        assert len(tools) == 4

    def test_discover_tool_names(self, mcp_client, mock_server_script):
        """发现工具名称正确"""
        mcp_client.connect_stdio("mock", mock_server_script)
        names = [t.name for t in mcp_client.list_tools()]
        assert "echo" in names
        assert "add" in names
        assert "get_time" in names
        assert "error_tool" in names

    def test_discover_tool_metadata(self, mcp_client, mock_server_script):
        """工具元数据完整"""
        mcp_client.connect_stdio("mock", mock_server_script)
        tool = mcp_client.get_tool("echo")
        assert tool is not None
        assert tool.name == "echo"
        assert "回显" in tool.description
        assert "input_schema" in tool.__dict__
        assert "properties" in tool.input_schema
        assert "text" in tool.input_schema["properties"]

    def test_get_tool_nonexistent(self, mcp_client, mock_server_script):
        """不存在的工具返回 None"""
        mcp_client.connect_stdio("mock", mock_server_script)
        tool = mcp_client.get_tool("nonexistent")
        assert tool is None

    def test_tool_server_name(self, mcp_client, mock_server_script):
        """工具关联 server 名称"""
        mcp_client.connect_stdio("mock", mock_server_script)
        tool = mcp_client.get_tool("echo")
        assert tool.server_name == "mock"


# ═══════════════════════════════════════════════════════════
# C. 工具调用
# ═══════════════════════════════════════════════════════════

class TestMcpToolCall:
    """MCP 工具调用测试"""

    def test_call_echo(self, mcp_client, mock_server_script):
        """调用 echo 工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.call_tool("echo", {"text": "hello world"})
        assert result.success is True
        assert len(result.content) > 0
        assert result.content[0]["type"] == "text"
        assert "Echo: hello world" in result.content[0]["text"]

    def test_call_add(self, mcp_client, mock_server_script):
        """调用 add 工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.call_tool("add", {"a": 3, "b": 4})
        assert result.success is True
        assert result.content[0]["text"] == "7"

    def test_call_get_time(self, mcp_client, mock_server_script):
        """调用 get_time 工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.call_tool("get_time", {})
        assert result.success is True
        assert "2026" in result.content[0]["text"]

    def test_call_error_tool(self, mcp_client, mock_server_script):
        """调用 error_tool 应返回 isError"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.call_tool("error_tool", {})
        # isError=True 时 success=False
        assert result.success is False

    def test_call_nonexistent_tool(self, mcp_client, mock_server_script):
        """调用不存在的工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        result = mcp_client.call_tool("no_such_tool", {})
        assert result.success is False
        assert "not found" in result.error.lower() or "not found" in str(result.error).lower()

    def test_call_before_connect(self, mcp_client):
        """未连接时调用返回错误"""
        result = mcp_client.call_tool("echo", {})
        assert result.success is False


# ═══════════════════════════════════════════════════════════
# D. 适配器注册
# ═══════════════════════════════════════════════════════════

class TestMcpToolAdapter:
    """McpToolAdapter 注册到 ToolRegistry"""

    def test_register_to_registry(self, mcp_client, mock_server_script):
        """注册 MCP 工具到 ToolRegistry"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        count = adapter.register_to_registry(registry)
        assert count == 4

        # 验证工具已注册
        echo_fn = registry.get_tool("echo")
        assert echo_fn is not None
        assert callable(echo_fn)

        # 清理注册的工具
        self._cleanup_mcp_tools(registry)

    def test_registered_tool_callable(self, mcp_client, mock_server_script):
        """注册后的工具可通过 registry.invoke 调用"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        adapter.register_to_registry(registry)

        # 通过 registry 调用 echo
        result = registry.invoke("echo", {"text": "hello from registry"})
        assert result["success"] is True
        result_data = json.loads(result.get("result_json", "{}"))
        content = result_data.get("content", "")
        assert "Echo: hello from registry" in content

        # 清理注册的工具
        self._cleanup_mcp_tools(registry)

    def test_registered_error_tool(self, mcp_client, mock_server_script):
        """注册的错误工具通过 registry 失败"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        adapter.register_to_registry(registry)

        result = registry.invoke("error_tool", {})
        assert result["success"] is False

        self._cleanup_mcp_tools(registry)

    def test_adapter_metadata(self, mcp_client, mock_server_script):
        """适配器注册的工具包含 MCP 元数据"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        adapter.register_to_registry(registry)

        tools_info = registry.list_tools()
        assert "echo" in tools_info
        assert tools_info["echo"]["level"] == "L0"
        assert "[MCP:mock]" in tools_info["echo"]["doc"]

        self._cleanup_mcp_tools(registry)

    def test_register_twice(self, mcp_client, mock_server_script):
        """重复注册覆盖"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        adapter.register_to_registry(registry, level="L1")
        adapter.register_to_registry(registry, level="L0")

        tools_info = registry.list_tools()
        assert tools_info["echo"]["level"] == "L0"
        assert tools_info["add"]["level"] == "L0"

        self._cleanup_mcp_tools(registry)

    def test_register_specific_server(self, mcp_client, mock_server_script):
        """只注册指定 server 的工具"""
        mcp_client.connect_stdio("mock", mock_server_script)
        adapter = mcp_client.create_mcp_adapter("mock")
        # 创建另一个 adapter 指向不同的 server
        from mcp.client import McpToolAdapter as Adapter
        adapter2 = Adapter(mcp_client, "nonexistent")

        count = adapter.register_to_registry(registry)
        count2 = adapter2.register_to_registry(registry)

        assert count == 4
        assert count2 == 0  # nonexistent 无工具

        self._cleanup_mcp_tools(registry)

    @staticmethod
    def _cleanup_mcp_tools(reg):
        """清理注册表中由 MCP 添加的工具"""
        mcp_tools = [name for name in reg._tools if name in ("echo", "add", "get_time", "error_tool")]
        for name in mcp_tools:
            del reg._tools[name]


# ═══════════════════════════════════════════════════════════
# E. auto_register_mcp_tools
# ═══════════════════════════════════════════════════════════

class TestAutoRegister:
    """auto_register_mcp_tools 函数"""

    def test_auto_register(self, mock_server_script):
        """自动注册流程"""
        reset_mcp_client()
        try:
            result = auto_register_mcp_tools(
                tool_registry=registry,
                stdio_commands=[("mock", mock_server_script)],
            )
            assert result["registered"] == 4
            assert len(result["errors"]) == 0
        finally:
            # 清理
            get_mcp_client().disconnect()
            reset_mcp_client()
            TestMcpToolAdapter._cleanup_mcp_tools(registry)

    def test_auto_register_empty(self):
        """空命令列表不注册任何工具"""
        reset_mcp_client()
        result = auto_register_mcp_tools(
            tool_registry=registry,
            stdio_commands=[],
        )
        assert result["registered"] == 0
        reset_mcp_client()

    def test_auto_register_multiple(self, mock_server_script):
        """注册多个 server"""
        reset_mcp_client()
        try:
            result = auto_register_mcp_tools(
                tool_registry=registry,
                stdio_commands=[
                    ("mock1", mock_server_script),
                    ("mock2", mock_server_script),
                ],
            )
            assert result["registered"] == 8
            # 两个 server 各 4 个工具
            # 但第二个 server 会覆盖第一个的同名工具，所以最终只有 4 个
            assert "echo" in registry._tools
        finally:
            get_mcp_client().disconnect()
            reset_mcp_client()
            TestMcpToolAdapter._cleanup_mcp_tools(registry)

    def test_auto_register_default_level(self, mock_server_script):
        """默认级别 L0"""
        reset_mcp_client()
        try:
            result = auto_register_mcp_tools(
                tool_registry=registry,
                stdio_commands=[("mock", mock_server_script)],
                default_level="L1",
            )
            assert result["registered"] == 4
            tools = registry.list_tools()
            assert tools["echo"]["level"] == "L1"
        finally:
            get_mcp_client().disconnect()
            reset_mcp_client()
            TestMcpToolAdapter._cleanup_mcp_tools(registry)

    def test_auto_register_error_handling(self):
        """处理连接错误"""
        reset_mcp_client()
        result = auto_register_mcp_tools(
            tool_registry=registry,
            stdio_commands=[("bad_server", ["python", "-c", "import sys; sys.exit(1)"])],
        )
        assert result["registered"] == 0
        assert len(result["errors"]) > 0
        reset_mcp_client()
