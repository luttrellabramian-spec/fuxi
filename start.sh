#!/bin/bash
# 伏羲引擎 Docker 启动脚本

set -e

echo "========================================="
echo "  伏羲引擎 v0.2.5 - Docker 启动"
echo "========================================="

# 等待依赖服务
sleep 2

# 启动 gRPC 服务（后台）
echo "[1/2] 启动 gRPC 服务 (端口 $GRPC_PORT)..."
python -u python/src/grpc_server.py &
GRPC_PID=$!

# 等待 gRPC 启动
sleep 3

# 可选：启动 Node.js 网关（如果已编译）
if [ -f "typescript/dist/gateway.js" ]; then
    echo "[2/2] 启动 HTTP 网关 (端口 $HTTP_PORT)..."
    cd typescript && node dist/gateway.js &
    GATEWAY_PID=$!
else
    echo "[INFO] 网关未编译，gRPC 服务独立运行"
fi

echo ""
echo "服务已启动:"
echo "  - gRPC: 端口 $GRPC_PORT"
[ ! -z "$GATEWAY_PID" ] && echo "  - HTTP: 端口 $HTTP_PORT"
echo "========================================="

# 等待信号
wait
