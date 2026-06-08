# 伏羲 v0.2.5 架构增量说明

> 本文档是对旧版 `伏羲架构设计文档(3).docx` 的补充，专门记录 v0.2.5 → v0.2.6 期间的**架构变更**和**新增模块**。

## 一、新增模块

### 1.1 Python 侧

| 模块 | 行数 | 职责 | 来源 |
|------|------|------|------|
| `python/src/engine/response_parser.py` | 98 | 从 LLM 输出提取 Action / Final，修复非标 JSON | 从 `fuxi_engine.py` 抽出 |
| `python/src/engine/task_persistence.py` | 134 | SQLite 任务状态保存/恢复（崩溃恢复） | 从 `fuxi_engine.py` 抽出并 class 化 |

### 1.2 TypeScript 侧

| 模块 | 行数 | 职责 |
|------|------|------|
| `typescript/src/ui/chat.html` | 390 | 对话界面 HTML 模板 |
| `typescript/src/ui/settings.html` | 404 | 设置界面 HTML 模板 |

模板由 `gateway.ts` 通过 `fs.readFileSync` 在启动时加载。

### 1.3 工具/脚本

| 工具 | 用途 |
|------|------|
| `scripts/e2e_verify.py` | 端到端启动验证（端口/health/工具/UI/chat 共 10 项） |

### 1.4 CI

`.github/workflows/ci.yml` 包含 4 个并行 job：
- **build**: Python 3.11/3.12 × Node 20 矩阵
- **test**: pytest + 覆盖率（上传 Codecov）
- **typescript**: tsc 编译 + HTML 模板存在性
- **e2e-smoke**: 启动 gRPC + 网关 + 跑 e2e_verify.py

## 二、关键 Bug 修复

### 2.1 gateway.ts HTTP 模式启动崩溃

**位置**：`typescript/src/gateway.ts:1777`（修复前）

**症状**：
```
TypeError: Cannot read properties of undefined (reading 'listen')
```

**根因**：HTTP 分支直接 `server.listen()`，但 `server` 从未被实例化（只在 HTTPS 分支用 `https.createServer()` 创建）。

**修复**：
```ts
} else {
  // HTTP 模式
  server = http.createServer(app).listen(port, () => { ... });
}
```

**影响**：Windows `start.bat` 启动后网关立即崩溃；整个端到端演示链路从未真正跑通。

## 三、代码组织

### 3.1 行数对比

| 文件 | 之前 | v0.2.6 |
|------|------|--------|
| `python/src/engine/fuxi_engine.py` | 979 | 893 |
| `typescript/src/gateway.ts` | 1829 | 1039 |

### 3.2 测试组织

| 文件 | 用例数 | 覆盖目标 |
|------|--------|----------|
| `test_hot_memory.py` | ~250 | `memory/hot_memory.py` |
| `test_warm_memory.py` | ~200 | `memory/warm_memory.py` |
| `test_tool_registry.py` | ~50 | 工具注册 |
| `test_engine_core.py` | ~250 | `fuxi_engine.py` 主路径 |
| `test_tool_executor.py` | ~30 | 工具执行器 |
| `test_evolution.py` | ~80 | 演化模块 |
| `test_security.py` | ~50 | 安全层 + 降级链 |
| `test_grpc_pool.py` | 25 | `grpc_utils/connection_pool.py` |
| `test_file_tools.py` | 24 | `tools/file_tools.py` |
| `test_memory_tools.py` | 23 | `tools/memory_tools.py` |
| `test_evolution_engine.py` | 17 | `evolution/evolution_engine.py` |
| `test_mcp_client.py` | 46 | `mcp/client.py` |
| `test_task_persistence.py` | 19 | `engine/task_persistence.py` |
| `test_tool_tracker.py` | 25 | `engine/tool_tracker.py` |
| **合计** | **~1100** | **66% 覆盖率** |

## 四、配置说明

### 4.1 环境变量（新增/调整）

| 变量 | 默认 | 说明 |
|------|------|------|
| `FUXI_TASK_DB` | `""`（禁用）| SQLite 路径，启用任务持久化 |
| `AUTH_ENABLED` | `true` | 网关鉴权开关 |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | — | HTTPS 模式证书 |

### 4.2 pytest 调用约定

**稳定核心子集**（README 推荐）：
```bash
python -m pytest -q --timeout=30 \
  tests/test_hot_memory.py \
  tests/test_warm_memory.py \
  tests/test_tool_registry.py \
  tests/test_engine_core.py \
  tests/test_tool_executor.py \
  tests/test_evolution.py
```

**完整子集**（不含真实网络）：
```bash
python -m pytest -q --timeout=30 \
  tests/test_hot_memory.py tests/test_warm_memory.py tests/test_tool_registry.py \
  tests/test_engine_core.py tests/test_tool_executor.py tests/test_evolution.py \
  tests/test_security.py tests/test_grpc_pool.py tests/test_file_tools.py \
  tests/test_memory_tools.py tests/test_evolution_engine.py tests/test_mcp_client.py \
  tests/test_task_persistence.py tests/test_tool_tracker.py
```

**跳过**：test_real_api.py（需要 LLM Key）、test_e2e_fullstack.py（需要服务）。

## 五、下一步建议

详见 `docs/reports/伏羲系统完善度与开发建议_v0.3.0.docx`，主要方向：

1. 拆分 `gateway.ts` 路由层（chat/tools/health 各自独立）
2. `grpc_server.py` 单元测试（当前 0%，需 mock proto 生成代码）
3. cold_memory / search_tools / web_tools 测试覆盖
4. 集成 MCP 工具到默认工具集
5. 完整 ReAct 长任务演示
