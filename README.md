# 伏羲 (Fuxi)

**版本**: v0.1.0-MVP  
**目标**: 验证 L1-L4 核心闭环，暂不包含 L5-L7

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
│     - LLM 调用（DeepSeek 官方 API）                │
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
│   │   │   ├── client.py           # DeepSeek API 调用
│   │   │   └── prompts.py          # 提示词模板
│   │   └── grpc_server.py         # gRPC Server
│   ├── requirements.txt
│   └── main.py                     # 入口
├── typescript/
│   ├── src/
│   │   ├── gateway.ts              # 网关（HTTP → gRPC）
│   │   ├── routes/
│   │   │   ├── chat.ts             # 聊天路由
│   │   │   └── tool.ts             # 工具路由
│   │   ├── proto/                 # Proto 编译产物
│   │   └── cli.ts                  # 终端 CLI
│   ├── package.json
│   └── tsconfig.json
├── tests/
│   ├── grpc_bridge_test.py         # gRPC 延迟测试
│   ├── tool_call_test.py          # 工具调用测试
│   └── memory_test.py              # 记忆读写测试
├── config/
│   └── default.yaml                # 默认配置
└── README.md
```

---

## 4. 验证标准

| 指标 | 目标 | 测量方法 |
|------|------|----------|
| gRPC 工具调用延迟 | < 200ms | `grpc_bridge_test.py` 计时 |
| 工具调用成功率 | > 95% | `tool_call_test.py` 100次调用 |
| 热记忆读写正确 | 100% | `memory_test.py` 读写对比 |
| 端到端对话 | 可用 | CLI 实际对话测试 |
| DeepSeek V4 推理 | 正常工作 | 检查 reasoning 输出 |

---

## 5. License

MIT

---

## 6. 开发中

⚙️ **伏羲 v0.1.0-MVP 正在开发中...**

当前进度：
- [x] Proto 接口定义
- [x] Python gRPC Server
- [x] 记忆层实现
- [ ] TypeScript 网关（进行中）
- [ ] CLI + 联调
- [ ] 测试验证
