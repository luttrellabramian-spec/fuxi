# 伏羲 (Fuxi)

伏羲是一个面向长期协作的自进化 AI Agent 引擎。它把 TypeScript HTTP/WebSocket 网关、Python Agent 核心、gRPC 协议、分层记忆、工具执行和行为演化模块组织在同一个工程里，用来验证一个更完整的 Agent 系统如何从“能对话”走向“能记忆、能调用工具、能记录执行过程、能逐步优化策略”。

**当前版本**: v0.2.5 WIP  
**当前状态**: 核心模块已具备较完整测试；TypeScript 网关可编译；完整端到端运行依赖本地 LLM/API 配置。  
**项目定位**: 研究型工程原型，不建议直接暴露到公网或生产环境。

## 核心能力

- **Agent 核心引擎**: Python ReAct 主循环，支持多轮推理、工具调用和会话上下文。
- **HTTP/gRPC 双运行时架构**: TypeScript 负责 HTTP、SSE、WebSocket 和 UI 入口，Python 负责核心推理与工具执行。
- **三层记忆系统**: 热记忆、温记忆、冷记忆分别处理当前会话、近期上下文和长期知识。
- **工具执行层**: 内置文件、搜索、网页、记忆等工具，并加入超时、重试、缓存和执行记录。
- **演化模块**: 查询分类、策略画像、工具排序、记忆优化和行为演化，为后续自优化能力预留结构。
- **可观测性**: 结构化执行日志、工具成功率追踪和 gRPC 连接池，为排错和演化分析提供数据。

## 架构概览

```text
用户 / CLI / Web UI
        |
        v
TypeScript Gateway
HTTP / SSE / WebSocket / Settings UI
        |
        v
gRPC + Protocol Buffers
        |
        v
Python Fuxi Core
ReAct Engine / Tool Executor / LLM Client / Evolution Selector
        |
        v
Memory Layer
Hot Memory / Warm Memory / Cold Memory
```

默认端口：

- HTTP Gateway: `18789`
- Python gRPC Server: `50051`

## 目录结构

```text
fuxi_v0.2.5/
├── config/                  # 默认配置和本地配置入口
├── docs/                    # 架构、规划、评估文档
│   ├── architecture/
│   ├── planning/
│   └── reports/
├── proto/                   # gRPC proto 定义和 Python 生成文件
├── python/                  # Python Agent 核心、记忆层、工具层、测试
│   ├── src/
│   └── tests/
├── scripts/                 # 报告、演化、备用启动脚本
│   ├── p2_evolution/
│   └── startup/
├── typescript/              # HTTP 网关、CLI、Web UI、TS 生成文件
│   └── src/
├── Dockerfile
├── docker-compose.yml
├── start.bat                # Windows 主启动入口
├── start.sh                 # Linux/macOS 启动入口
└── README.md
```

## 快速启动

### 方式一：Windows 一键启动

在项目根目录运行：

```powershell
.\start.bat
```

脚本会检查 Python 和 Node.js，安装依赖，编译 TypeScript 网关，并启动 Python gRPC 服务与 HTTP 网关。

启动后访问：

- 对话页面: `http://localhost:18789/chat/ui`
- 设置页面: `http://localhost:18789/settings/ui`
- 健康检查: `http://localhost:18789/health`

### 方式二：手动启动

先配置 LLM 环境变量：

```powershell
$env:LLM_API_KEY = "your-api-key"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
$env:LLM_MODEL = "gpt-4o"
```

启动 Python gRPC 服务：

```powershell
cd python
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

另开一个终端启动 TypeScript 网关：

```powershell
cd typescript
npm install
npm run build
npm start
```

## API 示例

健康检查：

```powershell
curl http://localhost:18789/health
```

普通对话：

```powershell
curl -X POST http://localhost:18789/chat `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"你好，介绍一下你自己\",\"session_id\":\"demo\"}"
```

流式对话：

```powershell
curl -X POST http://localhost:18789/chat/stream `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"用三句话解释伏羲\",\"session_id\":\"demo\"}"
```

工具调用：

```powershell
curl -X POST http://localhost:18789/tool/invoke `
  -H "Content-Type: application/json" `
  -d "{\"tool_name\":\"check_url\",\"arguments\":{\"url\":\"https://github.com\"},\"session_id\":\"demo\"}"
```

查看工具列表：

```powershell
curl http://localhost:18789/tool/list
```

## 测试与验证

TypeScript 网关编译：

```powershell
cd typescript
npm run build
```

Python 核心测试建议先跑稳定子集：

```powershell
cd python
python -m pytest -q tests/test_hot_memory.py tests/test_warm_memory.py tests/test_tool_registry.py
python -m pytest -q tests/test_engine_core.py tests/test_tool_executor.py tests/test_evolution.py
```

也可以在项目根目录直接运行核心检查脚本：

```powershell
.\scripts\check_core.ps1
```

完整测试：

```powershell
cd python
python -m pytest -q
```

注意：完整测试包含集成、MCP、安全和真实 API 相关场景，可能需要本地服务、端口、API Key 或更长超时时间。

## 配置

推荐使用环境变量或 `config/local.yaml` 覆盖默认配置。不要把真实 API Key 写入 `config/default.yaml`。

环境变量：

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `HTTP_PORT`
- `GRPC_PORT`
- `AUTH_ENABLED`
- `RATE_LIMIT_MAX`

也可以参考 `.env.example` 创建本地 `.env` 文件。`.env` 会被 TypeScript 网关自动加载；Python 侧建议使用系统环境变量，或通过网关请求元数据转发模型配置。

## 当前限制

- 当前仍是 WIP 原型，认证、鉴权、工具权限和公网暴露策略还没有达到生产标准。
- TypeScript 网关内暂时包含较多内嵌 UI，后续建议拆成独立前端或模板目录。
- 部分历史启动脚本已移入 `scripts/startup/`，可能包含旧路径，仅作为参考或调试用。
- `npm audit` 当前会提示 1 个 moderate 级别依赖风险，发布前需要单独处理。
- 完整 Python 测试会比核心子集更慢，且部分用例依赖真实 API 或外部服务。

## 文档

- `docs/planning/伏羲-v0.2.0框架规划_v0.1.md`：v0.2.0 阶段规划文档
- `docs/architecture/伏羲架构设计文档(3).docx`
- `docs/reports/伏羲系统完善度与开发建议_v0.3.0.docx`
- `docs/README.md`

## 下一步建议

1. 建立一条“演示级绿色路径”：`start.bat` 能启动，`/health` 能通过，`/chat/ui` 能完成一次对话。
2. 把 gateway 中的内嵌 UI 拆出，形成可展示的产品界面。
3. 给真实 API、MCP 和安全类测试增加标记，默认测试只跑稳定核心集。
4. 在 README 中持续维护“当前能跑什么、不能跑什么”，不要让文档和工程状态分叉。
