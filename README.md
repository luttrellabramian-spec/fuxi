# 伏羲 (Fuxi)

**版本**: v0.1.0-MVP  
**状态**: ✅ 核心功能验证完成，部分已知问题见本文末尾

---

## 0. 名字由来

**伏羲**，上古神话中的创世之神。

> "于是伏羲，仰则观象于天，俯则观法于地，观鸟兽之文与地之宜，近取诸身，远取诸物，始画八卦，以通神明之德，以类万物之情。"
> —— 《易经·系辞》

**一画开天**，传说伏羲以一画开天辟地，创立八卦，开启华夏文明之始。

以此为名，寓意：
- **开天辟地** — 从零构建自进化 Agent 架构
- **八卦万象** — 融合多源技术（Hermes + OpenClaw），化繁为简
- **文明传承** — 承接先贤智慧，开创 AI Agent 新纪元

---

## 1. 项目介绍

伏羲是一个自进化 AI Agent 平台，融合 Hermes Agent 的自进化引擎与 OpenClaw 的多渠道网关能力。

### 核心目标

验证以下三个核心假设：

1. **gRPC 桥接可行** — TS 网关调用 Python 工具的延迟可接受
2. **双工具注册表共存** — Hermes AST 自注册 + OpenClaw MCP 可融合
3. **分层记忆基本可用** — 热/温/冷三层读写闭环

### 技术栈

- **Python >= 3.11** — 核心引擎层
- **TypeScript >= 5.0** — 网关层
- **gRPC + Protocol Buffers** — 双运行时通信
- **多 API 支持** — 灵活接入多种 LLM

---

## 2. 架构设计

伏羲采用四层水平架构（L1-L4）：

```
┌─────────────────────────────────────────────────────┐
│                  L1: 渠道层                          │
│         终端 CLI（暂时不做 WebSocket）               │
└────────────────────────┬────────────────────────────┘
                         │ 文本消息
                         ▼
┌─────────────────────────────────────────────────────┐
│                  L2: 网关层（TS）                    │
│     简单 HTTP 路由 → 鉴权 → 转发 Python             │
│     端口: 18789                                     │
└────────────────────────┬────────────────────────────┘
                         │ gRPC
                         ▼
┌─────────────────────────────────────────────────────┐
│              L3: 核心引擎层（Python）                 │
│     Hermes Engine 简化版                            │
│     - ReAct 主循环（10步）                          │
│     - 工具调度（自注册 + gRPC 暴露）               │
│     - LLM 调用（OpenAI 兼容 API）                  │
└────────────────────────┬────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   热记忆     │  │   温记忆     │  │   冷记忆     │
│  MEMORY.md   │  │ SQLite FTS5 │  │ sqlite-vec  │
│  (2200字符)  │  │  (近期上下文) │  │ (向量知识)  │
└─────────────┘  └─────────────┘  └─────────────┘
                          L4: 记忆层
```

### L1: 渠道层

终端 CLI 入口，用户通过命令行与 Agent 对话。

### L2: 网关层（TypeScript）

- HTTP 服务，监听端口 18789
- 认证、限流、路由
- 将请求转发至 Python gRPC Server

### L3: 核心引擎层（Python）

- **ReAct 主循环**：10 步推理循环
- **工具调度**：自注册机制 + gRPC 暴露
- **LLM 调用**：多 API 灵活接入

### L4: 记忆层

三层记忆架构，兼顾即时响应与长期知识积累：

| 类型 | 存储 | 容量 | 用途 |
|------|------|------|------|
| 热记忆 | MEMORY.md | 2200 字符 | 当前会话目标、活跃工具集 |
| 温记忆 | SQLite FTS5 | 近期上下文 | 近期 50 条消息 |
| 冷记忆 | sqlite-vec | 向量知识库 | 长期知识检索 |

---

## 3. 目录结构

```
fuxi/
├── proto/
│   ├── hermes_claw.proto          # gRPC 接口定义
│   └── generated/                  # 编译产物
├── python/
│   ├── src/
│   │   ├── engine.py               # Hermes Engine 简化版
│   │   ├── tools/
│   │   │   ├── registry.py         # 工具注册表
│   │   │   ├── file_tools.py       # 文件读写工具
│   │   │   ├── search_tools.py     # 搜索工具
│   │   │   ├── memory_tools.py     # 记忆工具
│   │   │   └── web_tools.py        # 网络工具
│   │   ├── memory/
│   │   │   ├── hot_memory.py       # MEMORY.md 管理
│   │   │   ├── warm_memory.py      # SQLite FTS5
│   │   │   └── cold_memory.py      # sqlite-vec
│   │   ├── llm/
│   │   │   ├── client.py           # OpenAI 兼容 API 调用
│   │   │   └── prompts.py          # 提示词模板
│   │   └── grpc_server.py          # gRPC Server
│   ├── requirements.txt
│   └── main.py                     # 入口
├── typescript/
│   ├── src/
│   │   ├── gateway.ts              # 网关（HTTP → gRPC）
│   │   ├── routes/
│   │   │   ├── chat.ts             # 聊天路由
│   │   │   └── tool.ts            # 工具路由
│   │   ├── proto/                 # Proto 编译产物
│   │   └── cli.ts                  # 终端 CLI
│   ├── package.json
│   └── tsconfig.json
├── tests/
│   ├── grpc_bridge_test.py         # gRPC 延迟测试
│   ├── tool_call_test.py          # 工具调用测试
│   └── memory_test.py             # 记忆读写测试
├── config/
│   └── default.yaml                # 默认配置
└── README.md
```

---

## 4. 快速开始

### 前置要求

- Python >= 3.11
- Node.js >= 18 (for TypeScript gateway)
- 至少一个支持 OpenAI API 格式的 LLM（OpenAI、DeepSeek、Claude 等）

### 1. 配置 API Key

```bash
# OpenAI 兼容格式的 API（推荐，用于 ReAct 工具调用）
export OPENAI_API_KEY=your_key_here
export OPENAI_BASE_URL=https://api.openai.com/v1

# 自定义模型（必填）
export MODEL=gpt-4o
```

### 2. 启动 Python gRPC 服务

```bash
cd python
pip install -r requirements.txt
python main.py
# 默认监听 0.0.0.0:50051
```

### 3. 启动 TypeScript HTTP 网关

```bash
cd typescript
npm install
npm run build
npm start
# 默认监听 0.0.0.0:18789
```

### 4. 开始对话

```bash
cd typescript
npx ts-node src/cli.ts
# 或编译后
node dist/cli.js
```

### API 调用示例

```bash
# 对话
curl -X POST http://localhost:18789/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好", "session_id": "test-001"}'

# 调用工具
curl -X POST http://localhost:18789/tool/invoke \
  -H "Content-Type: application/json" \
  -d '{"tool": "check_url", "params": {"url": "https://github.com"}}'

# 查看可用工具
curl http://localhost:18789/tool/list

# 健康检查
curl http://localhost:18789/health
```

---

## 5. 验证标准与实测结果

| 指标 | 目标 | 实测结果 | 状态 |
|------|------|----------|------|
| gRPC 工具调用延迟 | < 200ms | 实测正常 | ✅ |
| 工具调用成功率 | > 95% | 核心工具通过 | ✅ |
| 热记忆读写正确 | 100% | 读写正常 | ✅ |
| 温/冷记忆读写 | 可用 | 代码正常，环境限制* | ⚠️ |
| 端到端对话 | 可用 | ReAct + 工具调用正常 | ✅ |
| LLM reasoning 输出 | 正常 | OpenAI 兼容模型推理正常 | ✅ |

> \* 温/冷记忆数据库文件在项目目录下存在环境只读限制，详见"已知问题"章节。

---

## 6. 已知问题与限制

> ⚠️ **MVP 阶段已知问题，请在使用前阅读**

### 6.1 环境限制

#### 数据库文件只读问题
- **现象**：`warm_memory.db` 和 `cold_memory.db` 在项目目录下被文件系统设为只读（`Read-Only`挂载）
- **影响**：温/冷记忆的持久化写入会失败
- **临时方案**：
  - 将数据库路径配置到 `/tmp` 等可写目录
  - 或在 `config/default.yaml` 中修改 `db_path`
- **代码状态**：实现本身正常，仅受环境限制

#### 部分模型不支持 function calling
- **现象**：并非所有 LLM 都支持 function calling / tool use
- **影响**：不支持的模型无法使用 ReAct 工具调用，只能进行纯对话
- **建议**：使用 OpenAI GPT-4、DeepSeek V3、Claude 3.5 等明确支持 function calling 的模型

### 6.2 功能限制

| 限制 | 说明 |
|------|------|
| **CLI 无持久化会话** | 当前 CLI 每次启动为独立会话，无跨会话记忆 |
| **无 WebSocket** | L1 渠道层仅支持 CLI，WebSocket 暂未实现 |
| **无身份认证** | 当前版本未实现用户身份验证，勿直接暴露公网 |
| **单步工具调用** | `InvokeTool` 每次只支持调用一个工具 |
| **ReAct 步数上限** | 主循环最多 10 步，防止无限循环 |
| **模型输出不稳定** | 部分模型偶尔输出不完整（"推理未完成"），可能需要重试 |

### 6.3 安全注意事项

> ⚠️ **安全警告 — MVP 阶段请勿用于生产环境**

#### 1. API Key 安全
- **严禁**将真实 API Key 提交到 GitHub
- 生产部署务必使用环境变量或密钥管理服务
- 建议在 `.gitignore` 中添加：
  ```
  python/.env
  config/secrets.yaml
  *.log
  ```

#### 2. 网络暴露风险
- 当前版本 **无认证、无鉴权**，HTTP 网关直接暴露存在严重风险
- `AUTH_ENABLED` 配置目前仅为占位，未实际启用
- **禁止**将服务直接暴露在公网（0.0.0.0）
- 本地开发建议使用 `127.0.0.1` 或 `localhost`

#### 3. 工具调用风险
- `write_file` / `http_post` 等写入类工具可能造成文件损坏或数据泄露
- 当前无工具调用权限控制，任何人都可调用所有已注册工具
- 建议通过网关层自行实现权限控制后再使用写入工具

#### 4. Prompt Injection
- 用户输入未经消毒处理直接拼接入 LLM Prompt
- 恶意用户可通过特殊构造的输入操纵 Agent 行为
- 生产环境需在网关层实现输入过滤

#### 5. 依赖安全
- 依赖第三方包存在潜在漏洞，定期运行：
  ```bash
  pip audit
  npm audit
  ```

#### 6. 日志与调试信息
- gRPC 和 HTTP 请求的详细错误信息可能泄露内部架构
- 生产环境应关闭详细日志或配置日志级别

### 6.4 待解决项

- [ ] 实现完整的 API Key 认证与鉴权
- [ ] 温/冷记忆数据库路径可配置化
- [ ] CLI 跨会话持久化
- [ ] WebSocket 渠道支持
- [ ] 工具调用权限控制
- [ ] 输入消毒（Prompt Injection 防护）
- [ ] 单元测试覆盖率提升
- [ ] Rate Limiting 实际实现

---

## 7. 开发说明

### 环境变量

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | - |
| `OPENAI_BASE_URL` | API 端点 | `https://api.openai.com/v1` |
| `MODEL` | 当前使用模型 | **必填，无默认值** |
| `GRPC_HOST` | gRPC 服务地址 | `localhost` |
| `GRPC_PORT` | gRPC 端口 | `50051` |
| `HTTP_PORT` | HTTP 网关端口 | `18789` |

### 运行测试

```bash
cd tests

# gRPC 延迟测试（目标 < 200ms）
python grpc_bridge_test.py

# 工具调用成功率测试（目标 > 95%）
python tool_call_test.py

# 三层记忆测试
python memory_test.py
```

---

## 8. License

MIT
