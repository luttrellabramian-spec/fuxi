# 伏羲 - AI Agent 引擎

<div align="center">

**伏羲** 是一个基于 LLM 的 AI Agent 引擎，支持工具调用、多轮对话和三层记忆系统。

[English](./README_EN.md) | 中文

</div>

---

## 特性

- **ReAct 循环引擎** - 支持 10 步推理和工具调用
- **gRPC 服务** - 高性能远程过程调用
- **HTTP 网关** - Express.js 构建的 RESTful API
- **三层记忆系统** - 热记忆（文件）、温记忆（SQLite FTS5）、冷记忆（向量搜索）
- **工具注册表** - 9 个内置工具，支持自定义扩展
- **SSE 流式响应** - 实时流式输出
- **TLS 支持** - 生产级安全通信
- **请求追踪** - 完整的请求链路追踪
- **CLI 对话窗口** - 终端交互式对话

## 架构

```
CLI (TS) → Gateway (TS, HTTP :18789) → gRPC (:50051) → Python Engine → 工具 + 记忆
```

## 快速开始

### 方式一：一键启动（推荐）

```bash
# Windows - 启动服务并打开设置页面
start.bat

# Windows - 启动服务并直接进入聊天
chat.bat
```

### 方式二：手动启动

```bash
# 1. 安装 Python 依赖
cd python
pip install -r requirements.txt

# 2. 安装 TypeScript 依赖
cd ../typescript
npm install
npm run build

# 3. 启动 gRPC 服务
cd ../python
python main.py

# 4. 启动 HTTP 网关（新终端）
cd ../typescript
npm start

# 5. 启动 CLI 聊天（新终端）
cd ../typescript
node dist/src/cli.js
```

### 方式三：npm 命令

```bash
npm start      # 启动 HTTP 网关
npm run cli    # 启动 CLI 聊天
npm run build  # 编译 TypeScript
npm test       # 运行测试
```

## 配置

### 首次运行

首次运行时，系统会引导你配置：
- API Key
- Base URL（API 地址）
- 模型名称

配置会保存到 `config/local.yaml`。

### 配置文件

```yaml
# config/default.yaml
llm:
  api_key: ""        # 必填，你的 API Key
  base_url: ""       # 必填，API 地址
  model: ""          # 必填，模型名称
  max_tokens: 4096
  temperature: 0.7
```

### 环境变量

```bash
export DEEPSEEK_API_KEY=your_key
export DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
export DEFAULT_MODEL=deepseek-chat
```

### 设置页面

启动后访问 http://localhost:18789/settings/ui 进行可视化配置。

支持的模型预设：
- OpenAI（GPT-4o, GPT-4o-mini）
- Claude（Claude 3.5）
- DeepSeek（DeepSeek Chat, DeepSeek Coder）
- 通义千问
- 智谱 GLM-4
- 本地模型（Ollama, vLLM）

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 聊天对话 |
| `/chat/stream` | POST | 流式聊天（SSE） |
| `/tool/invoke` | POST | 调用工具 |
| `/tool/list` | GET | 工具列表 |
| `/memory/hot` | GET/POST | 热记忆读写 |
| `/memory/warm/*` | GET/POST | 温记忆操作 |
| `/memory/cold/*` | GET/POST | 冷记忆操作 |
| `/settings` | GET/POST | 配置管理 |
| `/settings/ui` | GET | 设置页面 |
| `/health` | GET | 健康检查 |
| `/metrics` | GET | 监控指标 |

## 工具列表

| 工具 | 级别 | 说明 |
|------|------|------|
| `read_file` | L0 | 读取文件 |
| `write_file` | L1 | 写入文件 |
| `list_files` | L0 | 列出目录 |
| `file_exists` | L0 | 检查文件 |
| `read_json` | L0 | 读取 JSON |
| `write_json` | L1 | 写入 JSON |
| `http_get` | L0 | HTTP GET |
| `http_post` | L0 | HTTP POST |
| `check_url` | L0 | 检查 URL |

## 记忆系统

| 类型 | 存储 | 容量 | 用途 |
|------|------|------|------|
| 热记忆 | MEMORY.md | 2200 字符 | 当前对话上下文 |
| 温记忆 | SQLite FTS5 | 50 条/会话 | 近期对话历史 |
| 冷记忆 | sqlite-vec | 无限制 | 长期知识存储 |

## 目录结构

```
fuxi/
├── proto/                  # gRPC 接口定义
│   └── fuxi.proto
├── python/                 # Python 后端
│   ├── src/
│   │   ├── engine/         # ReAct 引擎
│   │   ├── grpc_server.py  # gRPC 服务
│   │   ├── llm/            # LLM 客户端
│   │   ├── memory/         # 记忆系统
│   │   └── tools/          # 工具集
│   └── requirements.txt
├── typescript/             # TypeScript 前端
│   ├── src/
│   │   ├── gateway.ts      # HTTP 网关
│   │   ├── cli.ts          # CLI 终端
│   │   └── config.ts       # 配置管理
│   └── package.json
├── config/                 # 配置文件
│   └── default.yaml
├── tests/                  # 测试文件
├── start.bat               # Windows 启动脚本
├── chat.bat                # Windows 聊天脚本
└── package.json            # npm 配置
```

## 测试

```bash
# 运行所有测试
npm test

# Python 测试
cd tests && python -m pytest . -v

# TypeScript 测试
cd typescript && npm test
```

## 安全特性

- **路径遍历防护** - 限制文件访问范围
- **SSRF 防护** - 阻止内网访问
- **API Key 认证** - 可选的身份验证
- **速率限制** - 防止滥用
- **TLS 支持** - 加密通信

## 环境要求

- Python 3.10+
- Node.js 18+
- npm 或 yarn

## 许可证

MIT License

## 链接

- [GitHub 仓库](https://github.com/luttrellabramian-spec/fuxi)
- [问题反馈](https://github.com/luttrellabramian-spec/fuxi/issues)
