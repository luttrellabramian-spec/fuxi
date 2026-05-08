# 伏羲 (Fuxi)

**Version**: v0.1.0-MVP  
**Goal**: Verify L1-L4 core loop, L5-L7 not included yet

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
│  MEMORY.md   │  │ SQLite FTS5  │  │  sqlite-vec  │
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
│   │   └── grpc_server.py         # gRPC Server
│   ├── requirements.txt
│   └── main.py                     # Entry point
├── typescript/
│   ├── src/
│   │   ├── gateway.ts              # Gateway (HTTP → gRPC)
│   │   ├── routes/
│   │   │   ├── chat.ts             # Chat routes
│   │   │   └── tool.ts             # Tool routes
│   │   ├── proto/                 # Proto compiled output
│   │   └── cli.ts                  # Terminal CLI
│   ├── package.json
│   └── tsconfig.json
├── tests/
│   ├── grpc_bridge_test.py         # gRPC latency test
│   ├── tool_call_test.py          # Tool call test
│   └── memory_test.py              # Memory I/O test
├── config/
│   └── default.yaml                # Default configuration
└── README.md
```

---

## 4. Validation Criteria

| Metric | Target | Measurement |
|--------|--------|-------------|
| gRPC tool call latency | < 200ms | `grpc_bridge_test.py` timing |
| Tool call success rate | > 95% | `tool_call_test.py` 100 calls |
| Hot memory read/write accuracy | 100% | `memory_test.py` compare |
| End-to-end conversation | Usable | CLI real conversation test |
| LLM reasoning | Working | Check reasoning output |

---

## 5. License

MIT

---

## 6. In Development

⚙️ **Fuxi v0.1.0-MVP is under development...**

Current progress:
- [x] Proto interface definition
- [x] Python gRPC Server
- [x] Memory layer implementation
- [ ] TypeScript Gateway (in progress)
- [ ] CLI + Integration
- [ ] Testing & Validation
