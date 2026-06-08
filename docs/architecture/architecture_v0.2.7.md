# 伏羲 v0.2.7 架构增量说明

> 本文档记录 v0.2.6 → v0.2.7 期间的**架构变更**、**真实 LLM 验证**和**部署流程**改进。

## 一、v0.2.7 关键修复

### 1.1 gRPC 自动加载 config/local.yaml

**问题**：
- `grpc_server.py` 启动时只读 `os.environ`，不读 `config/local.yaml`
- 启动 gRPC 必须手动 export `LLM_API_KEY` / `LLM_BASE_URL` / `DEFAULT_MODEL`
- 一旦 yaml 改了，必须重启服务并重设环境变量

**修复**：在 `grpc_server.py` 顶部加 `_load_local_config()`：

```python
def _load_local_config() -> None:
    candidates = [
        os.path.join(PROJECT_ROOT, "config", "local.yaml"),
        os.path.join(os.path.dirname(PROJECT_ROOT), "config", "local.yaml"),
    ]
    for path in candidates:
        if not os.path.exists(path): continue
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        llm = cfg.get("llm", {})
        for env_key, yaml_key in [
            ("LLM_API_KEY", "api_key"),
            ("LLM_BASE_URL", "base_url"),
            ("DEFAULT_MODEL", "model"),
        ]:
            # 优先级：env > yaml
            if env_key not in os.environ and llm.get(yaml_key):
                os.environ[env_key] = str(llm[yaml_key])
```

**优先级**：`环境变量 > config/local.yaml`

### 1.2 网关转发 Authorization header

**问题**：
- `/chat` 和 `/chat/stream` 路由硬编码使用 `runtimeConfig.apiKey || config.auth.apiKey`
- 客户端无法用自己的 key 覆盖服务端默认
- 多用户场景下所有请求都共用服务端 key

**修复**：优先级改为
```ts
const userConfig = extractUserConfig(req);  // 从 Authorization header 解析
const apiKey = userConfig.apiKey || runtimeConfig.apiKey || config.auth.apiKey;
```

### 1.3 think 标签二次清理

**问题**：
- Python 端 `fuxi_engine.strip_think_tags` 已剥离 think
- 但 gRPC StreamComplete 流式累积把 think 块拼到 final content
- 网关原样透传给客户端，前端看到 `<think>...</think>Final: ...`

**修复**：网关在 `is_final` 时用 `stripThinkTagsInPlace()` 做二次清理

**双层防御**：
1. Python 端（fuxi_engine.py）剥离 — 主防御
2. TypeScript 端（asyncHandler.ts）剥离 — 兜底

### 1.4 UI 模板路径修复

**问题**：
- `readFileSync(__dirname + "ui/chat.html")` 在 prod（`dist/` 目录）找不到模板
- 返回 114 字节的错误页

**修复**：新增 `readUiTemplate()` 助手，支持 fallback 路径：
```ts
const candidates = [
  path.join(__dirname, "ui", name),                  // prod: dist/ui/
  path.join(__dirname, "..", "src", "ui", name),    // dev: src/ 跑
];
```

## 二、strip_think_tags 算法（v0.2.7 增强）

```python
def strip_think_tags(text: str) -> str:
    if not text: return ""

    # 1. 完整闭合块
    cleaned = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)

    # 2. 未闭合块 — 必须在 Final 标记前停止
    if "<think>" in cleaned:
        for marker in ("Final:", "最终答案:", "最终:"):
            if marker in cleaned:
                cleaned = cleaned[cleaned.index(marker):]
                break
        else:
            cleaned = cleaned[:cleaned.find("<think>")]

    # 3. 孤立标签
    cleaned = re.sub(r'</?think>', '', cleaned)

    # 4. 规范化空白
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    return cleaned.strip()
```

**30 个单测覆盖**：完整闭合、多行、未闭合、孤立开/闭标签、嵌套、Final 重复、空白规范化。

## 三、真实 LLM 端到端验证

### 3.1 测试环境

| 项目 | 值 |
|------|-----|
| 模型 | `MiniMax-M2.7` |
| Base URL | `https://api.minimaxi.com/v1` |
| 启动方式 | `python main.py` （gRPC 自动读 yaml） |

### 3.2 测试用例

| # | 场景 | 期望 | 实际 | 时延 |
|---|------|------|------|------|
| 1 | "用一句话介绍你自己" | LLM 简介 | "我是伏羲引擎，一个高效的 AI 助手..." | 8.0s |
| 2 | "25 × 4 = ?" | 数字 100 | "100" | 5.9s |
| 3 | "读取 README.md 前 200 字符" | 工具调用 | `Action: read_file({"path": "README.md", ...})` | 13.2s |
| 4a | "我的名字是 Alice。记住它" | 确认 | （think 块后回复）| 1.1s |
| 4b | "我叫什么名字？" | "Alice" | "您的名字是 Alice" | 4.1s |

### 3.3 链路验证

```
用户 → POST /chat (with Authorization)
        ↓
TS Gateway → extractUserConfig() → apiKey
        ↓ metadata.add("authorization", `Bearer ${apiKey}`)
gRPC :50051
        ↓ _check_auth(metadata.api_key == _default_api_key)
FuxiCoreServicer.StreamComplete
        ↓ _get_client_config(metadata)  # 注入 client key/base_url
engine.stream_run()
        ↓ strip_think_tags()  # Python 端剥离
ReAct Loop
        ↓ yield {type: "token", content}
        ↓ yield {type: "done", content}  # 解析后的 Final
TS Gateway 累积 → stripThinkTagsInPlace()  # 网关兜底
        ↓ wrapResponse(res, true, { content: cleaned })
        ↓
HTTP 200 → {ok: true, data: {content: "OK", ...}}
```

### 3.4 MiniMax-M2.7 模型观察

- 大量 `<think>...</think>` 块（双层防御必要）
- 偶有重复输出（"100100" 而非 "100"）
- Action JSON 偶尔末尾缺 `}`（模型本身问题）

## 四、CI/CD 流程（v0.2.7 增强）

### 4.1 6 个 Job

| Job | 触发 | 作用 |
|-----|------|------|
| **build** | PR / push | 矩阵：Python 3.11/3.12 × Node 20 |
| **test** | 依赖 build | pytest + 覆盖率 + Codecov |
| **typescript** | 独立 | tsc 编译 + HTML 模板存在性 |
| **e2e-smoke** | 依赖以上 | 启动 gRPC + 网关 + 跑 e2e_verify（不开 LLM） |
| **e2e-live** | **仅手动** | 真实 LLM 测试（用 GitHub Secrets 提供 key） |
| **release** | 仅 push tag | 自动构建 artifact + 创建 GitHub Release |

### 4.2 真实 LLM 配置

需要在 GitHub repo 设置：
- `Settings > Secrets and variables > Actions > Repository secrets`
- 添加：
  - `LLM_API_KEY` — LLM 提供商的 API key
  - `LLM_BASE_URL` — 默认 `https://api.openai.com/v1`
  - `LLM_MODEL` — 默认 `gpt-4o`

手动触发：
- `Actions > CI > Run workflow > Branch: main`

### 4.3 发布流程

```bash
# 1. 改 CHANGELOG
# 2. 提交
git add -A && git commit -m "release: v0.2.7"
# 3. 打 tag
git tag -a v0.2.7 -m "Release v0.2.7: ..."
# 4. push（CI 自动构建 + 创建 release draft）
git push origin main --tags
```

## 五、配置说明

### 5.1 本地开发

```bash
# 1. 编辑 config/local.yaml
cat > config/local.yaml << EOF
llm:
  api_key: sk-...
  base_url: https://api.openai.com/v1
  model: gpt-4o
EOF

# 2. 启动 gRPC（自动读 yaml）
cd python && python main.py &

# 3. 启动网关
cd typescript && node dist/gateway.js

# 4. 端到端验证
python scripts/e2e_verify.py
# 或
E2E_LIVE=1 python scripts/e2e_verify.py  # 含真实 LLM 对话
```

### 5.2 CI 环境

CI 通过 GitHub Secrets 提供 LLM key：
- `LLM_API_KEY` → 注入到 e2e-live job 的 `config/local.yaml`
- `e2e-smoke` 不需要 key，只验证可启动链路

## 六、下一步建议

1. **grpc_server.py 单测** — 解决 sentence-transformers 慢加载后补到 80%+
2. **gateway.ts 路由层拆分** — chat/tools/health 各自独立
3. **MiniMax-M2.7 模型适配** — 减少重复输出
4. **多用户鉴权** — 完善 token 刷新 / 多 key 轮询
5. **grpc 网关流式** — gRPC 端用真正的 stream 流而非 collect
