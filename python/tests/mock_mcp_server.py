"""Mock MCP Server - 模拟 MCP 协议服务器（stdio JSON-RPC）

处理：
- initialize 协议握手
- tools/list 返回预定义工具列表
- tools/call 根据工具有效性返回模拟结果

通过 stdin/stdout 通信，Content-Length 头格式。
"""
import sys
import json


# ── 预定义工具列表 ─────────────────────────────────────
MOCK_TOOLS = [
    {
        "name": "echo",
        "description": "回显输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要回显的文本"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两个数字相加",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个数"},
                "b": {"type": "number", "description": "第二个数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "get_time",
        "description": "返回当前模拟时间",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "error_tool",
        "description": "总是返回错误的工具（用于测试）",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def read_msg():
    """读取一条 JSON-RPC 消息（Content-Length 头格式）"""
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    length = int(headers.get("Content-Length", 0))
    if length > 0:
        raw = sys.stdin.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def send_msg(data):
    """发送一条 JSON-RPC 消息"""
    body = json.dumps(data, ensure_ascii=False)
    encoded = body.encode("utf-8")
    sys.stdout.write(f"Content-Length: {len(encoded)}\r\n\r\n")
    sys.stdout.write(body)
    sys.stdout.flush()


def handle_initialize(msg):
    """处理 initialize 请求"""
    _id = msg.get("id", 0)
    return {
        "jsonrpc": "2.0",
        "id": _id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "logging": {},
            },
            "serverInfo": {
                "name": "mock-mcp-server",
                "version": "1.0.0",
            },
        },
    }


def handle_tools_list(msg):
    """处理 tools/list 请求"""
    _id = msg.get("id", 0)
    return {
        "jsonrpc": "2.0",
        "id": _id,
        "result": {
            "tools": MOCK_TOOLS,
        },
    }


def handle_tools_call(msg):
    """处理 tools/call 请求"""
    _id = msg.get("id", 0)
    params = msg.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    if tool_name == "echo":
        text = arguments.get("text", "")
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "result": {
                "content": [
                    {"type": "text", "text": f"Echo: {text}"}
                ],
            },
        }

    elif tool_name == "add":
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        result = a + b
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "result": {
                "content": [
                    {"type": "text", "text": str(result)}
                ],
            },
        }

    elif tool_name == "get_time":
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "result": {
                "content": [
                    {"type": "text", "text": "2026-05-04T12:00:00Z"}
                ],
            },
        }

    elif tool_name == "error_tool":
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "result": {
                "content": [],
                "isError": True,
            },
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": _id,
            "error": {
                "code": -32601,
                "message": f"Tool not found: {tool_name}",
            },
        }


def handle_notification(msg):
    """处理通知（忽略）"""
    pass


def main():
    """主循环"""
    initialized = False

    while True:
        msg = read_msg()
        if msg is None:
            break

        method = msg.get("method", "")
        is_notification = "id" not in msg

        if is_notification:
            handle_notification(msg)
            continue

        if method == "initialize":
            response = handle_initialize(msg)
            send_msg(response)
            initialized = True

        elif not initialized:
            send_msg({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32000, "message": "Not initialized"},
            })

        elif method == "tools/list":
            send_msg(handle_tools_list(msg))

        elif method == "tools/call":
            send_msg(handle_tools_call(msg))

        elif method == "ping":
            send_msg({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})

        else:
            send_msg({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()
