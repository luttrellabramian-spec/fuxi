# 伏羲 (Fuxi)

LLM Agent 引擎：ReAct 循环 + 工具调度 + 三层记忆系统。

**[English](#english) | [中文](#中文)**

---

## English

### Overview

Fuxi is an LLM Agent engine featuring:
- **ReAct Loop**: Thought → Action → Observation循环，LLM驱动决策
- **Tool System**: 自注册工具表，支持文件/网络/记忆等操作
- **3-Layer Memory**: 热( MEMORY.md) / 温(SQLite FTS5) / 冷(向量检索)
- **200K Context**: 支持高达200K token的上下文窗口
- **Multi-Session**: 会话隔离，独立记忆

### Architecture

```
CLI (TypeScript)
    ↓ HTTP
Gateway (:18789)
    ↓ gRPC
Python Engine (:50051)
    ├── ReAct Loop (LLM-driven)
    ├── Tool Registry (self-registering)
    └── 3-Layer Memory
        ├── Hot (MEMORY.md, 2200 chars)
        ├── Warm (SQLite FTS5, 50 entries/session)
        └── Cold (Vector search with embeddings)
```

### Quick Start

```bash
# 1. Configure API Key
export DEEPSEEK_API_KEY=your_key_here
export DEEPSEEK_BASE_URL=https://api.example.com/v1  # or your preferred endpoint

# 2. Start Python gRPC Service
cd python
pip install -r requirements.txt
python main.py  # listens on :50051

# 3. Start TypeScript Gateway
cd typescript
npm install
npm run build
npm start  # listens on :18789

# 4. Chat
curl -X POST http://localhost:18789/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is 2+2?", "session_id": "test"}'
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Chat with AI |
| `/tool/invoke` | POST | Invoke a tool |
| `/tool/list` | GET | List available tools |
| `/memory/hot` | GET/POST | Hot memory (MEMORY.md) |
| `/memory/warm/recent` | GET | Recent warm memories |
| `/memory/cold/search` | GET | Search cold memory |
| `/health` | GET | Health check |

### Configuration

| Env Variable | Description | Default |
|--------------|-------------|---------|
| `DEEPSEEK_API_KEY` | API key | - |
| `DEEPSEEK_BASE_URL` | API endpoint | `https://api.example.com/v1` |
| `DEFAULT_MODEL` | Model name | `MiniMax-M2.7` |
| `GRPC_PORT` | gRPC port | `50051` |
| `HTTP_PORT` | HTTP gateway port | `18789` |
| `RATE_LIMIT_MAX` | Max requests per window | `100` |

### Testing

```bash
cd tests
python grpc_bridge_test.py    # gRPC latency test
python tool_call_test.py      # Tool call success rate
python memory_test.py         # 3-layer memory test
```

---

## 中文

### 概述

伏羲是一个LLM Agent引擎，核心特性：

- **ReAct 循环**：Thought → Action → Observation，LLM驱动决策
- **工具系统**：自注册工具表，支持文件/网络/记忆等操作
- **三层记忆**：热(MEMORY.md) / 温(SQLite FTS5) / 冷(向量检索)
- **200K上下文**：支持高达200K token的上下文窗口
- **多会话隔离**：会话独立记忆，互不干扰

### 架构

```
CLI (TypeScript)
    ↓ HTTP
Gateway (:18789)
    ↓ gRPC
Python Engine (:50051)
    ├── ReAct 循环（LLM驱动）
    ├── 工具注册表（自注册）
    └── 三层记忆
        ├── 热记忆（MEMORY.md, 2200字符）
        ├── 温记忆（SQLite FTS5, 每会话50条）
        └── 冷记忆（向量搜索，支持embedding）
```

### 快速开始

```bash
# 1. 配置 API Key
export DEEPSEEK_API_KEY=your_key_here
export DEEPSEEK_BASE_URL=https://api.minimaxi.com/v1  # 或你偏好的端点

# 2. 启动 Python gRPC 服务
cd python
pip install -r requirements.txt
python main.py  # 监听 :50051

# 3. 启动 TypeScript 网关
cd typescript
npm install
npm run build
npm start  # 监听 :18789

# 4. 开始对话
curl -X POST http://localhost:18789/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "2+2等于几？", "session_id": "test"}'
```

### API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 与AI对话 |
| `/tool/invoke` | POST | 调用工具 |
| `/tool/list` | GET | 获取可用工具列表 |
| `/memory/hot` | GET/POST | 热记忆读写 |
| `/memory/warm/recent` | GET | 获取最近温记忆 |
| `/memory/cold/search` | GET | 搜索冷记忆 |
| `/health` | GET | 健康检查 |

### 配置

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `DEEPSEEK_API_KEY` | API密钥 | - |
| `DEEPSEEK_BASE_URL` | API端点 | `https://api.example.com/v1` |
| `DEFAULT_MODEL` | 模型名称 | `MiniMax-M2.7` |
| `GRPC_PORT` | gRPC端口 | `50051` |
| `HTTP_PORT` | HTTP网关端口 | `18789` |
| `RATE_LIMIT_MAX` | 速率限制（次/窗口） | `100` |

### 测试

```bash
cd tests
python grpc_bridge_test.py    # gRPC延迟测试
python tool_call_test.py       # 工具调用成功率测试
python memory_test.py          # 三层记忆测试
```

### 内置工具

| 工具 | 层级 | 说明 |
|------|------|------|
| `read_file` | L0 | 读取文件 |
| `write_file` | L1 | 写入文件 |
| `list_files` | L0 | 列目录 |
| `file_exists` | L0 | 检查文件是否存在 |
| `http_get` | L0 | HTTP GET请求 |
| `http_post` | L0 | HTTP POST请求 |
| `fetch_page` | L1 | 抓取网页内容 |
| `memory_write` | L1 | 写入记忆 |
| `memory_query` | L0 | 查询记忆 |
| `memory_get_recent` | L0 | 获取最近记忆 |

### 项目结构

```
fuxi/
├── proto/
│   └── fuxi.proto           # gRPC接口定义
├── python/
│   ├── main.py              # 入口
│   ├── requirements.txt
│   └── src/
│       ├── grpc_server.py   # gRPC服务
│       ├── engine/
│       │   └── fuxi_engine.py  # ReAct引擎
│       ├── tools/           # 工具注册表
│       ├── memory/          # 三层记忆
│       │   ├── hot_memory.py   # 热记忆
│       │   ├── warm_memory.py  # 温记忆
│       │   └── cold_memory.py  # 冷记忆
│       └── llm/
│           └── client.py   # OpenAI兼容API客户端
├── typescript/
│   ├── src/
│   │   ├── gateway.ts      # HTTP网关
│   │   └── cli.ts          # CLI对话窗口
│   └── package.json
├── tests/                   # 测试套件
├── config/
│   └── default.yaml        # 配置文件
└── README.md
```

---

## License

MIT