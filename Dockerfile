# === 阶段 1：编译 TypeScript ===
FROM node:20-alpine AS builder

WORKDIR /app/typescript

# 复制依赖文件
COPY typescript/package*.json ./
RUN npm ci || npm install

# 复制 TypeScript 源码
COPY typescript/src ./src
COPY typescript/tsconfig.json ./

# 编译 TypeScript
RUN npm run build

# === 阶段 2：运行 Python gRPC 服务 ===
FROM python:3.11-slim AS fuxi-python

WORKDIR /app

# 安装 Python 依赖
COPY python/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制 Python 源码
COPY python/src ./python/src
COPY proto ./proto

# 预编译 proto（将 proto 生成好的 py 文件 COPY 进来）
COPY proto/generated/python ./proto/generated/python

# 暴露端口
EXPOSE 50051

# 启动 gRPC 服务
CMD ["python", "-u", "python/src/grpc_server.py"]

# === 阶段 3：运行 Node.js 网关 ===
FROM node:20-alpine AS fuxi-gateway

WORKDIR /app

# 安装运行时依赖
COPY typescript/package*.json ./
RUN npm ci --omit=dev || npm install --omit=dev

# 复制编译好的 TypeScript 代码
COPY --from=builder /app/typescript/dist ./dist
COPY --from=builder /app/typescript/src/proto ./src/proto

# 端口
EXPOSE 18789

# 启动网关
CMD ["node", "dist/gateway.js"]
