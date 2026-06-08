# Fuxi

[![CI](https://github.com/<owner>/fuxi/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/fuxi/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/<owner>/fuxi/branch/main/graph/badge.svg)](https://codecov.io/gh/<owner>/fuxi)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-%E2%89%A520-green)](https://nodejs.org/)

Fuxi is a work-in-progress self-evolving AI Agent engine for long-term collaboration. It combines a TypeScript HTTP/WebSocket gateway, a Python Agent core, gRPC/protobuf communication, layered memory, tool execution, structured logging, and evolution modules.

**Version**: v0.2.5 WIP (v0.2.6 pending)
**Status**: 561+ unit tests, 66% line coverage; TypeScript gateway builds; full end-to-end verified by `scripts/e2e_verify.py`.  
**Positioning**: research prototype, not production-ready.

## Architecture

```text
User / CLI / Web UI
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

Default ports:

- HTTP Gateway: `18789`
- Python gRPC Server: `50051`

## Quick Start

Windows:

```powershell
.\start.bat
```

Manual startup:

```powershell
$env:LLM_API_KEY = "your-api-key"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
$env:LLM_MODEL = "gpt-4o"
```

```powershell
cd python
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Open another terminal:

```powershell
cd typescript
npm install
npm run build
npm start
```

Useful URLs:

- Chat UI: `http://localhost:18789/chat/ui`
- Settings UI: `http://localhost:18789/settings/ui`
- Health check: `http://localhost:18789/health`

## Validation

```powershell
cd typescript
npm run build
```

```powershell
cd python
python -m pytest -q tests/test_hot_memory.py tests/test_warm_memory.py tests/test_tool_registry.py
```

Or run the core check script from the project root:

```powershell
.\scripts\check_core.ps1
```

See the Chinese `README.md` for the fuller project description, directory structure, API examples, current limitations, and next-step recommendations.
