# Fuxi - AI Agent Engine

<div align="center">

**Fuxi** is an LLM-based AI Agent engine with tool calling, multi-turn conversation, and a three-layer memory system.

English | [中文](./README.md)

</div>

---

## Features

- **ReAct Loop Engine** - 10-step reasoning with tool calling
- **gRPC Service** - High-performance remote procedure calls
- **HTTP Gateway** - RESTful API built with Express.js
- **Three-Layer Memory** - Hot (file), Warm (SQLite FTS5), Cold (vector search)
- **Tool Registry** - 9 built-in tools, extensible
- **SSE Streaming** - Real-time streaming responses
- **TLS Support** - Production-grade secure communication
- **Request Tracing** - Full request chain tracing
- **CLI Chat** - Interactive terminal conversation

## Architecture

```
CLI (TS) → Gateway (TS, HTTP :18789) → gRPC (:50051) → Python Engine → Tools + Memory
```

## Quick Start

### Option 1: One-Click Start (Recommended)

```bash
# Windows - Start service and open settings page
start.bat

# Windows - Start service and enter chat directly
chat.bat
```

### Option 2: Manual Start

```bash
# 1. Install Python dependencies
cd python
pip install -r requirements.txt

# 2. Install TypeScript dependencies
cd ../typescript
npm install
npm run build

# 3. Start gRPC service
cd ../python
python main.py

# 4. Start HTTP gateway (new terminal)
cd ../typescript
npm start

# 5. Start CLI chat (new terminal)
cd ../typescript
node dist/src/cli.js
```

### Option 3: npm Commands

```bash
npm start      # Start HTTP gateway
npm run cli    # Start CLI chat
npm run build  # Compile TypeScript
npm test       # Run tests
```

## Configuration

### First Run

On first run, the system will guide you to configure:
- API Key
- Base URL (API endpoint)
- Model name

Configuration is saved to `config/local.yaml`.

### Configuration File

```yaml
# config/default.yaml
llm:
  api_key: ""        # Required: your API key
  base_url: ""       # Required: API endpoint
  model: ""          # Required: model name
  max_tokens: 4096
  temperature: 0.7
```

### Environment Variables

```bash
export DEEPSEEK_API_KEY=your_key
export DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
export DEFAULT_MODEL=deepseek-chat
```

### Settings Page

After startup, visit http://localhost:18789/settings/ui for visual configuration.

Supported model presets:
- OpenAI (GPT-4o, GPT-4o-mini)
- Claude (Claude 3.5)
- DeepSeek (DeepSeek Chat, DeepSeek Coder)
- Qwen (Tongyi Qianwen)
- Zhipu GLM-4
- Local models (Ollama, vLLM)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Chat conversation |
| `/chat/stream` | POST | Streaming chat (SSE) |
| `/tool/invoke` | POST | Call a tool |
| `/tool/list` | GET | List tools |
| `/memory/hot` | GET/POST | Hot memory read/write |
| `/memory/warm/*` | GET/POST | Warm memory operations |
| `/memory/cold/*` | GET/POST | Cold memory operations |
| `/settings` | GET/POST | Configuration management |
| `/settings/ui` | GET | Settings page |
| `/health` | GET | Health check |
| `/metrics` | GET | Monitoring metrics |

## Tool List

| Tool | Level | Description |
|------|-------|-------------|
| `read_file` | L0 | Read file content |
| `write_file` | L1 | Write file content |
| `list_files` | L0 | List directory files |
| `file_exists` | L0 | Check file existence |
| `read_json` | L0 | Read JSON file |
| `write_json` | L1 | Write JSON file |
| `http_get` | L0 | HTTP GET request |
| `http_post` | L0 | HTTP POST request |
| `check_url` | L0 | Check URL accessibility |

## Memory System

| Type | Storage | Capacity | Purpose |
|------|---------|----------|---------|
| Hot | MEMORY.md | 2200 chars | Current conversation context |
| Warm | SQLite FTS5 | 50 msgs/session | Recent conversation history |
| Cold | sqlite-vec | Unlimited | Long-term knowledge storage |

## Directory Structure

```
fuxi/
├── proto/                  # gRPC interface definitions
│   └── fuxi.proto
├── python/                 # Python backend
│   ├── src/
│   │   ├── engine/         # ReAct engine
│   │   ├── grpc_server.py  # gRPC service
│   │   ├── llm/            # LLM client
│   │   ├── memory/         # Memory system
│   │   └── tools/          # Tool set
│   └── requirements.txt
├── typescript/             # TypeScript frontend
│   ├── src/
│   │   ├── gateway.ts      # HTTP gateway
│   │   ├── cli.ts          # CLI terminal
│   │   └── config.ts       # Configuration
│   └── package.json
├── config/                 # Configuration files
│   └── default.yaml
├── tests/                  # Test files
├── start.bat               # Windows start script
├── chat.bat                # Windows chat script
└── package.json            # npm configuration
```

## Testing

```bash
# Run all tests
npm test

# Python tests
cd tests && python -m pytest . -v

# TypeScript tests
cd typescript && npm test
```

## Security Features

- **Path Traversal Protection** - Restricts file access scope
- **SSRF Protection** - Blocks internal network access
- **API Key Authentication** - Optional identity verification
- **Rate Limiting** - Prevents abuse
- **TLS Support** - Encrypted communication

## Requirements

- Python 3.10+
- Node.js 18+
- npm or yarn

## License

MIT License

## Links

- [GitHub Repository](https://github.com/luttrellabramian-spec/fuxi)
- [Issue Tracker](https://github.com/luttrellabramian-spec/fuxi/issues)
