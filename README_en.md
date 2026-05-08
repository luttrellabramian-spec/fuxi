# 伏羲 (Fuxi)

**Version**: v0.1.0-MVP  
**Status**: ✅ Core features validated. See "Known Issues" below for details.

---

## 0. Name Origin

**Fuxi (伏羲)**, the primordial creator deity in Chinese mythology.

> "Hence Fuxi, gazing up, observed the images of heaven; gazing down, examined the patterns of earth. He observed the markings of birds and beasts and their suitability to the earth. Taking from near at hand, he grasped the body; taking from far away, he grasped the principles of things. Thus he originated the eight trigrams, to penetrate the virtues of the gods and to classify the qualities of all things."
> —— *I Ching (Book of Changes)*

**"One Stroke Opens Heaven"** — Legend has it that Fuxi opened the heavens and created the world with a single stroke, founding the Eight Trigrams and initiating Chinese civilization.

The name embodies:
- **Opening Heaven and Earth** — Building a self-evolving Agent architecture from scratch
- **Eight Trigrams, Myriad Phenomena** — Fusing multiple technologies (Hermes + OpenClaw), simplifying the complex
- **Civilization Inheritance** — Carrying forward ancestral wisdom, pioneering a new era of AI Agents

---

## 1. Project Overview

Fuxi is a self-evolving AI Agent platform, merging Hermes Agent's self-evolution engine with OpenClaw's multi-channel gateway capabilities.

### Core Objectives

Verify these three core hypotheses:

1. **gRPC Bridge Feasibility** — Latency of TS gateway calling Python tools is acceptable
2. **Dual Tool Registry Coexistence** — Hermes AST self-registration + OpenClaw MCP can integrate
3. **Stratified Memory Basically Usable** — Hot/Warm/Cold three-tier memory read/write closed loop

### Tech Stack

- **Python >= 3.11** — Core engine layer
- **TypeScript >= 5.0** — Gateway layer
- **gRPC + Protocol Buffers** — Dual-runtime communication
- **Multi-API Support** — Flexible integration with various LLMs

---

## 2. Architecture

Fuxi uses a four-layer horizontal architecture (L1-L4):

```
┌─────────────────────────────────────────────────────┐
│                  L1: Channel Layer                    │
│            Terminal CLI (WebSocket later)            │
└────────────────────────┬────────────────────────────┘
                         │ Text Message
                         ▼
┌─────────────────────────────────────────────────────┐
│               L2: Gateway Layer (TS)                │
│      Simple HTTP routing → Auth → Forward to Python │
│     Port: 18789                                      │
└────────────────────────┬────────────────────────────┘
                         │ gRPC
                         ▼
┌─────────────────────────────────────────────────────┐
│              L3: Core Engine Layer (Python)          │
│              Hermes Engine (Simplified)              │
│     - ReAct Main Loop (10 steps)                    │
│     - Tool Dispatch (self-registration + gRPC)     │
│     - LLM Calls (Multi-API support)                │
└────────────────────────┬────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Hot Memory  │  │ Warm Memory  │  │ Cold Memory  │
│  MEMORY.md   │  │ SQLite FTS5  │  │ sqlite-vec   │
│ (2200 chars) │  │(recent ctx)  │  │(vector store)│
└─────────────┘  └─────────────┘  └─────────────┘
                         L4: Memory Layer
```

### L1: Channel Layer

Terminal CLI entry point. Users interact with the Agent via command line.

### L2: Gateway Layer (TypeScript)

- HTTP service, listening on port 18789
- Authentication, rate limiting, routing
- Forwards requests to Python gRPC Server

### L3: Core Engine Layer (Python)

- **ReAct Main Loop**: 10-step reasoning loop
- **Tool Dispatch**: Self-registration mechanism + gRPC exposure
- **LLM Calls**: Flexible multi-API integration

### L4: Memory Layer

Three-tier memory architecture, balancing instant response with long-term knowledge accumulation:

| Type | Storage | Capacity | Purpose |
|------|---------|----------|---------|
| Hot | MEMORY.md | 2200 chars | Current session goals, active tools |
| Warm | SQLite FTS5 | Recent context | Recent 50 messages |
| Cold | sqlite-vec | Vector knowledge | Long-term knowledge retrieval |

---

## 3. Directory Structure

```
fuxi/
├── proto/
│   ├── hermes_claw.proto          # gRPC interface definition
│   └── generated/                  # Compiled artifacts
├── python/
│   ├── src/
│   │   ├── engine.py               # Hermes Engine (simplified)
│   │   ├── tools/
│   │   │   ├── registry.py         # Tool registry
│   │   │   ├── file_tools.py       # File I/O tools
│   │   │   ├── search_tools.py     # Search tools
│   │   │   ├── memory_tools.py     # Memory tools
│   │   │   └── web_tools.py        # Web tools
│   │   ├── memory/
│   │   │   ├── hot_memory.py       # MEMORY.md management
│   │   │   ├── warm_memory.py      # SQLite FTS5
│   │   │   └── cold_memory.py      # sqlite-vec
│   │   ├── llm/
│   │   │   ├── client.py           # LLM API client
│   │   │   └── prompts.py          # Prompt templates
│   │   └── grpc_server.py          # gRPC Server
│   ├── requirements.txt
│   └── main.py                     # Entry point
├── typescript/
│   ├── src/
│   │   ├── gateway.ts              # Gateway (HTTP → gRPC)
│   │   ├── routes/
│   │   │   ├── chat.ts             # Chat routes
│   │   │   └── tool.ts            # Tool routes
│   │   ├── proto/                 # Proto compiled output
│   │   └── cli.ts                  # Terminal CLI
│   ├── package.json
│   └── tsconfig.json
├── tests/
│   ├── grpc_bridge_test.py         # gRPC latency test
│   ├── tool_call_test.py          # Tool call test
│   └── memory_test.py             # Memory I/O test
├── config/
│   └── default.yaml                # Default configuration
└── README.md
```

---

## 4. Quick Start

### Prerequisites

- Python >= 3.11
- Node.js >= 18 (for TypeScript gateway)
- At least one supported LLM API (DeepSeek / MiniMax)

### 1. Configure API Key

```bash
# DeepSeek (recommended, for ReAct tool calling)
export DEEPSEEK_API_KEY=your_key_here
export DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Or MiniMax (NOTE: does NOT support function calling, chat-only)
export MINIMAX_API_KEY=your_key_here
export MINIMAX_BASE_URL=https://api.minimaxi.com/v1

# Custom model
export MODEL=deepseek-v4-pro
```

### 2. Start Python gRPC Server

```bash
cd python
pip install -r requirements.txt
python main.py
# Default: 0.0.0.0:50051
```

### 3. Start TypeScript HTTP Gateway

```bash
cd typescript
npm install
npm run build
npm start
# Default: 0.0.0.0:18789
```

### 4. Start Chatting

```bash
cd typescript
npx ts-node src/cli.ts
# or after build
node dist/cli.js
```

### API Examples

```bash
# Chat
curl -X POST http://localhost:18789/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "session_id": "test-001"}'

# Invoke tool
curl -X POST http://localhost:18789/tool/invoke \
  -H "Content-Type: application/json" \
  -d '{"tool": "check_url", "params": {"url": "https://github.com"}}'

# List available tools
curl http://localhost:18789/tool/list

# Health check
curl http://localhost:18789/health
```

---

## 5. Validation Criteria & Test Results

| Metric | Target | Actual Result | Status |
|--------|--------|---------------|--------|
| gRPC tool call latency | < 200ms | Normal in testing | ✅ |
| Tool call success rate | > 95% | Core tools passing | ✅ |
| Hot memory read/write | 100% | Working correctly | ✅ |
| Warm/Cold memory I/O | Usable | Code OK, env limitation* | ⚠️ |
| End-to-end conversation | Usable | ReAct + tools working | ✅ |
| LLM reasoning output | Normal | DeepSeek V4 working | ✅ |

> \* Warm/Cold memory DB files are under filesystem read-only restriction in the project directory. See "Known Issues" below.

---

## 6. Known Issues & Limitations

> ⚠️ **Known issues at MVP stage — please read before using**

### 6.1 Environment Limitations

#### Database Files Read-Only
- **Symptom**: `warm_memory.db` and `cold_memory.db` are on a read-only filesystem mount
- **Impact**: Warm/Cold memory persistence writes will fail
- **Workaround**:
  - Configure DB path to `/tmp` or other writable location
  - Or modify `db_path` in `config/default.yaml`
- **Code status**: Implementation is correct; this is an environment issue only

#### MiniMax Model Does NOT Support Function Calling
- **Symptom**: MiniMax-M2.7 and other MiniMax models **do NOT support function calling / tool use**
- **Impact**: Cannot use ReAct tool calling; chat-only mode
- **Recommendation**: Use DeepSeek V4 / OpenAI or other function-calling-capable models in production

### 6.2 Functional Limitations

| Limitation | Description |
|------------|-------------|
| **CLI has no persistent sessions** | Each CLI invocation is an isolated session; no cross-session memory |
| **No WebSocket** | L1 channel only supports CLI; WebSocket not yet implemented |
| **No authentication** | No user authentication implemented; do not expose to public internet |
| **Single-tool invocation** | `InvokeTool` only calls one tool per request |
| **ReAct step cap** | Maximum 10 steps to prevent infinite loops |
| **Model output instability** | Some models occasionally produce incomplete output ("inference unfinished"); retry may be needed |

### 6.3 Security Considerations

> ⚠️ **Security Warning — DO NOT use in production at MVP stage**

#### 1. API Key Security
- **NEVER** commit real API keys to GitHub
- Use environment variables or a secrets manager in production
- Recommended `.gitignore` entries:
  ```
  python/.env
  config/secrets.yaml
  *.log
  ```

#### 2. Network Exposure Risk
- Current version has **no authentication or authorization**; exposing the HTTP gateway directly is a critical risk
- `AUTH_ENABLED` config is a placeholder and not actually enforced
- **DO NOT** expose the service on a public IP (0.0.0.0)
- Use `127.0.0.1` or `localhost` for local development

#### 3. Tool Invocation Risk
- `write_file` / `http_post` and other write tools can corrupt files or leak data
- There is currently **no tool call permission control**; anyone can invoke any registered tool
- Implement your own permission layer at the gateway before using write tools

#### 4. Prompt Injection
- User input is concatenated into LLM prompts without sanitization
- Malicious users can craft inputs to manipulate Agent behavior
- Production deployments must implement input filtering at the gateway layer

#### 5. Dependency Security
- Third-party dependencies may contain vulnerabilities; run regularly:
  ```bash
  pip audit
  npm audit
  ```

#### 6. Logging & Debug Info
- gRPC and HTTP request errors may leak internal architecture details
- Production should disable detailed logging or set appropriate log levels

### 6.4 TODO

- [ ] Implement full API Key authentication & authorization
- [ ] Make warm/cold memory DB path configurable
- [ ] CLI cross-session persistence
- [ ] WebSocket channel support
- [ ] Tool call permission controls
- [ ] Input sanitization (Prompt Injection protection)
- [ ] Improve unit test coverage
- [ ] Actually implement Rate Limiting

---

## 7. Development

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek API key | - |
| `DEEPSEEK_BASE_URL` | DeepSeek API endpoint | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | DeepSeek model name | `deepseek-v4-pro` |
| `MINIMAX_API_KEY` | MiniMax API key | - |
| `MINIMAX_BASE_URL` | MiniMax API endpoint | `https://api.minimaxi.com/v1` |
| `MODEL` | Current model in use | `deepseek-v4-pro` |
| `GRPC_HOST` | gRPC server address | `localhost` |
| `GRPC_PORT` | gRPC port | `50051` |
| `HTTP_PORT` | HTTP gateway port | `18789` |

### Running Tests

```bash
cd tests

# gRPC latency test (target < 200ms)
python grpc_bridge_test.py

# Tool call success rate test (target > 95%)
python tool_call_test.py

# Three-tier memory test
python memory_test.py
```

---

## 8. License

MIT
