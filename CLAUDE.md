# 伏羲 - 给 Claude Code 看的项目说明

## 项目概述

伏羲是一个 LLM Agent 引擎，核心是 Python gRPC 服务 + TypeScript HTTP 网关。LLM 驱动 ReAct 循环，通过工具调用解决问题。

## 架构

```
CLI (TS) → Gateway (TS, HTTP :18789) → gRPC (:50051) → Python Engine → 工具 + 记忆
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `proto/fuxi.proto` | gRPC 接口定义（真理来源） |
| `python/src/grpc_server.py` | Python gRPC 服务实现 |
| `python/src/engine/fuxi_engine.py` | ReAct 循环引擎 |
| `python/src/tools/` | 工具注册表 + 工具实现 |
| `python/src/memory/` | 热/温/冷三层记忆 |
| `python/src/llm/client.py` | OpenAI 兼容 API 客户端 |
| `typescript/src/gateway.ts` | HTTP → gRPC 网关 |
| `typescript/src/cli.ts` | 终端对话窗口 |

## Proto 修改规则

修改 `proto/fuxi.proto` 后必须重新生成存根：

```bash
# Python
python -m grpc_tools.protoc -I./proto --python_out=./python/src --grpc_python_out=./python/src ./proto/fuxi.proto

# TypeScript（需要在 typescript/ 目录）
node node_modules/grpc-tools/bin/protoc.js \
  --plugin=protoc-gen-grpc_js=node_modules/grpc-tools/bin/protoc_plugin.js \
  --js_out=import_style=commonjs,binary:./src/proto \
  --grpc_out=grpc_js:./src/proto \
  --proto_path=../proto ../proto/fuxi.proto
```

## LLM API 配置

支持任意 OpenAI 兼容 API，配置优先级：

1. 请求头 `Authorization: Bearer <key>` 和 `X-Base-Url: <url>`（最高）
2. 环境变量 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`
3. `config/default.yaml`

## 运行

```bash
# Python gRPC 服务
cd python && pip install -r requirements.txt && python main.py

# TypeScript 网关
cd typescript && npm install && npm run build && npm start
```

## 测试

```bash
# 全部测试
cd tests && python grpc_bridge_test.py
cd tests && python tool_call_test.py
cd tests && python memory_test.py

# 或用 pytest
cd tests && python -m pytest . -v
```

## 注意事项

- Python gRPC 存根从 `proto/fuxi.proto` 生成，导入名是 `fuxi__pb2`（不是 `hermes_claw_pb2`）
- TypeScript 的 proto 存根在 `typescript/src/proto/`（fuxi_pb.js, fuxi_grpc_pb.js）
- 工具通过 `@registry.register()` 装饰器自注册，不需要手动添加到服务
- 三个测试文件在 `tests/` 目录，不在 `python/tests/`
