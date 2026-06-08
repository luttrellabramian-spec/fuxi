"""伏羲 gRPC 服务端测试 — 占位文件

grpc_server.py 模块加载时触发：
1. 真实 `engine.fuxi_engine` 链（→ fuxi_engine.py → LLMClient）
2. `memory.cold_memory` → sentence-transformers（首次加载耗时 6+ 秒）
3. gRPC proto 生成代码 fuxi_pb2 / fuxi_pb2_grpc（需要先跑 protoc）

完整单元测试需要满足以下前置：
- 跑过 `protoc --python_out=proto/generated/python proto/fuxi.proto`
- 在测试环境屏蔽 sentence-transformers 加载（mock embedding）

目前这两个条件在轻量测试环境里都不满足，所以这里仅占位。
真实端到端覆盖在 scripts/e2e_verify.py 中通过 HTTP 网关间接验证 gRPC 链路。
"""
