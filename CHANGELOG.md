# Changelog

All notable changes to Fuxi will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
