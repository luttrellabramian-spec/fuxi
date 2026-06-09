from __future__ import annotations

"""MCP 协议模块 - Model Context Protocol 支持

使用方式：
    from mcp.client import McpClient, auto_register_mcp_tools

    # 方式1：手动连接
    client = McpClient()
    client.connect_stdio("filesystem", ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"])
    adapter = client.create_mcp_adapter("filesystem")
    adapter.register_to_registry(registry)

    # 方式2：自动注册
    auto_register_mcp_tools(
        registry,
        stdio_commands=[
            ("filesystem", ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]),
        ],
    )
"""

from mcp.client import (
    McpClient,
    McpTool,
    McpCallResult,
    StdioConnection,
    McpToolAdapter,
    auto_register_mcp_tools,
    get_mcp_client,
    reset_mcp_client,
)

__all__ = [
    "McpClient",
    "McpTool",
    "McpCallResult",
    "StdioConnection",
    "McpToolAdapter",
    "auto_register_mcp_tools",
    "get_mcp_client",
    "reset_mcp_client",
]
