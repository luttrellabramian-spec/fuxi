# Changelog

All notable changes to Fuxi will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.6] - 2026-06-09

### Fixed（CRITICAL 5 项）
- **gRPC 鉴权 fail-open**：`grpc_server.py:141` 当 `AUTH_ENABLED=true` 但无默认 key 时不再放行所有请求
- **search_tools 路径遍历**：`grep`/`search_file`/`search_replace` 接入 `_validate_path` 共享校验 + `os.walk(followlinks=False)`
- **gRPC 错误归一化**：服务端 `str(e)` 写入 `logger.error(..., exc_info=True)`，客户端只收 `"Internal error"`；工具 `result_json` 里的绝对路径用 `_sanitize_tool_error` 替换为 `<path>`
- **死代码删除**：`engine/task_persistence.py`（0 处 import）+ `tests/test_task_persistence.py` 移除
- **e2e_verify DEVNULL 静默日志修复**：`os.makedirs(log_dir, exist_ok=True)` + 始终落盘到 `logs/`

### Added
- `_path_utils.py` 共享路径校验（从 file_tools 抽出）
- `engine/feedback.py`：on_tool_invoked / on_hot_evict 抽出，4 个关键回调加 `logger.warning`
- `engine/run_sync.py`：run() 主循环拆出
- `engine/run_stream.py`：stream_run() 拆出，支持可选 `llm` 参数避免并发竞态
- `typescript/src/helpers.ts`：buildMetadata、readUiTemplate、logger、metrics
- `typescript/src/types.ts`：RouteContext + ProtoChunk/Response 接口
- `typescript/src/routes/{chat,tool,memory,ui}.ts`：按职责拆分
- `typescript/src/ws/chatSocket.ts`：WebSocket 拆出
- `typescript/scripts/copy-ui.js`：build 复制 src/ui → dist/ui
- `docs/planning/伏羲-v0.2.6-HIGH修复路线.md`

### Changed
- `stream_run(llm=...)` 参数化：每次请求 new LLMClient 不替换共享 `self.engine.llm`（修并发竞态）
- `time.time() % 10` → 计数器（selector + evolution_engine）
- `WarmMemory._schedule_rebuild_fts`：后台线程调度，搜索热路径不再阻塞
- `Selector._retrieve_memories` 加 10s TTL cache
- `gateway.ts` 1058 → 190 行（-82%）；`fuxi_engine.py` 904 → 412 行（-54%）
- `buildMetadata(req, runtimeConfig)` 统一 Authorization 优先级链；6 个 memory 路由 + WS 接入
- `/chat/stream` 每 chunk `stripThinkTagsInPlace` + 跨 chunk 兜底
- 38 个 Python 文件加 `from __future__ import annotations`
- 60+ 处 TS callback 形参 `any` → `unknown` + 类型收窄

## [0.2.7] - 2026-06-08

### Fixed
- **gateway.ts 路径硬编码问题**：`_strip_think_tags` 等 use 模板文件硬编码 `__dirname/ui/...`，
  生产环境（`dist/` 目录）找不到模板。修复：新增 `readUiTemplate()` 支持 fallback 路径。
- **gateway.ts 不转发 Authorization header**：`/chat` 和 `/chat/stream` 路由硬编码使用
  `runtimeConfig.apiKey`，从不读客户端请求头。修复：优先级改为
  `extractUserConfig(req) > runtimeConfig > config.auth`。
- **think 标签在最终答案中残留**：Python 端 fuxi_engine 已剥离 think，但 gRPC
  StreamComplete 流式累积会把 think 块拼到最终 content。修复：gateway 端在 is_final
  时调用 `stripThinkTagsInPlace()` 做二次清理。

### Added
- **gRPC 自动加载 config/local.yaml**：`grpc_server.py` 启动时读 yaml，注入到
  `os.environ`。这样 `python main.py` 不再需要手动设环境变量。优先级：env > yaml，
  方便 CI 覆盖。
- **strip_think_tags 函数到 response_parser.py**：从 fuxi_engine 抽出，含完整/未闭合
  think 块 + 孤立标签处理。新增 30 个单测覆盖各种边界。
- **readUiTemplate() 助手**：兼容 dev（src/ui）和 prod（dist/ui）路径。
- **stripThinkTagsInPlace() 助手**：网关层兜底剥离 think 块。
- **E2E_LIVE 真实 LLM 模式**：`e2e_verify.py` 加 `E2E_LIVE=1` 环境变量，启用真实
  LLM 对话测试（需 config/local.yaml）。
- **`/tool/invoke` 自动用本地 key 鉴权**：e2e 测试自动读 yaml 注入 Authorization。

### Changed
- **fuxi_engine.py 内部调用 `strip_think_tags`**：从 `self._strip_think_tags()` 改为
  `strip_think_tags()` 模块函数（方法仍保留为兼容 shim）。
- **网关使用 Authorization 优先级链**：客户端 > runtime > config。

### Verified
- **真实 LLM 端到端**：用 `MiniMax-M2.7` 跑了 4 个场景：
  1. 自我介绍 → "我是伏羲引擎..." ✅ 8.0s
  2. 简单数学 "25 × 4" → "100" ✅ 5.9s
  3. 工具调用 "读 README.md" → 生成正确 `Action: read_file({...})` ✅ 13.2s
  4. 多轮上下文 "我叫 Alice / 我叫什么？" → "您的名字是 Alice" ✅ 1.1s + 4.1s
- **e2e_verify.py 10/10 通过**，含真实 LLM 响应 "OK"（干净无 think 块）

### Notes
- 用户授权使用 `config/local.yaml` 的 API key 做了真实端到端测试
- `MiniMax-M2.7` 模型本身有小瑕疵（重复输出、think 块多），但链路完全 OK
- 建议后续支持 token 过期刷新

## [0.2.6] - 2026-06-08

### Fixed
- **gateway.ts: HTTP 模式 `server` 变量未创建导致启动崩溃**
  在 `startServer()` 的 HTTP 分支（else），原代码直接调用 `server.listen(port, ...)`，
  但 `server` 从未实例化（只在 HTTPS 分支用 `https.createServer()` 一步到位创建）。
  修复：HTTP 分支改为 `server = http.createServer(app).listen(port, ...)`。
  影响：Windows `start.bat` 启动后网关立即崩溃；这个 bug 让整条端到端演示链路从未成功过。
  同时编译后的 `dist/gateway.js` 同步修复。

### Added
- **单元测试覆盖**：从 329 → 561 用例，覆盖率从 43% → 66%
  - 新增测试文件：`test_grpc_pool.py`, `test_file_tools.py`, `test_memory_tools.py`,
    `test_evolution_engine.py`, `test_mcp_client.py`, `test_task_persistence.py`,
    `test_tool_tracker.py`
- **端到端验证脚本** `scripts/e2e_verify.py`
  - 检查端口 /health /tool/list /tool/invoke /chat/ui /settings/ui /chat 共 10 项
  - 支持 `--start` 自动启动服务
  - 适合 CI 集成
- **CI 工作流** `.github/workflows/ci.yml`
  - 矩阵测试：Python 3.11/3.12 + Node 20
  - 4 个 Job：build / test+coverage / typescript / e2e-smoke

### Changed
- **fuxi_engine.py**: 979 → 893 行（-86 行）
  - 抽出 `engine/response_parser.py`：`fix_json` / `parse_action` / `parse_final`
  - 抽出 `engine/task_persistence.py`：TaskPersistence 类
- **gateway.ts**: 1829 → 1037 行（-792 行，-43%）
  - 抽出 `typescript/src/ui/chat.html` 和 `settings.html`
  - 网关用 `fs.readFileSync` 读取模板
- **start.bat**: 启动后自动运行 `e2e_verify.py` 验证
- **tests/test_security.py**: `test_failure_increments_counter` 改用 mock，
  修复真实网络调用卡住测试的问题
- **tests/test_engine_core.py**: 调用点改用 `engine.response_parser`

### Notes
- 5 个原来 0% 覆盖的模块补到 80%+：`engine_enhancements` 95%, `security_guard` 83%,
  `tools/file_tools` 93%, `tools/memory_tools` 100%, `mcp/client` 94%,
  `task_persistence` 95%
- `grpc_server.py` 仍为 0% — 模块导入触发 sentence-transformers 加载（6+ 秒），
  端到端覆盖通过 `scripts/e2e_verify.py` 间接验证 gRPC 链路
