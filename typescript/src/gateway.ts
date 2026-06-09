/** v0.2.6 (H1) — Express 网关薄壳
 *
 * 中间件、运行时配置、服务器启动。
 * 路由全部按职责拆到 routes/* 模块。
 * WebSocket 在 ws/chatSocket.ts。
 */
import express from "express";
import http from "http";
import helmet from "helmet";
import bodyParser from "body-parser";
import cors from "cors";
import { RateLimiterMemory } from "rate-limiter-flexible";
// @ts-ignore - generated proto file
import fuxiProto from "../src/proto/fuxi_pb.js";

import { config } from "./config";
import { grpcClient, memoryClient } from "./grpc_client";
import { requestIdMiddleware } from "./middleware/requestId";
import { catchAsync } from "./middleware/asyncHandler";
import { degradedHandler } from "./middleware/degradedHandler";
import { metrics } from "./helpers";

import { registerChatRoutes } from "./routes/chat";
import { registerToolRoutes } from "./routes/tool";
import { registerMemoryRoutes } from "./routes/memory";
import { registerUiRoutes } from "./routes/ui";
import { attachChatSocket } from "./ws/chatSocket";
import { RouteContext } from "./types";

// 扩展 Express Request 类型
declare global {
  namespace Express {
    interface Request {
      traceId?: string;
    }
  }
}

const app = express();

/** 内存中的运行时配置（可动态修改） */
interface RuntimeConfig {
  model: string;
  apiKey: string;
  baseUrl: string;
  maxTokens: number;
  temperature: number;
}

const runtimeConfig: RuntimeConfig = {
  model: process.env.DEFAULT_MODEL || process.env.LLM_MODEL || "",
  apiKey: process.env.LLM_API_KEY || "",
  baseUrl: process.env.LLM_BASE_URL || "",
  maxTokens: 2048,
  temperature: 0.7,
};

// ── 中间件（按顺序） ──────────────────────────────────────

app.use(helmet({
  contentSecurityPolicy: false,
  crossOriginEmbedderPolicy: false,
}));

const corsOptions: cors.CorsOptions = {
  origin: ['http://localhost:18789', 'http://127.0.0.1:18789', 'http://localhost:3000'],
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'X-Request-ID', 'X-Base-Url'],
  credentials: true,
  maxAge: 86400,
};
app.use(cors(corsOptions));
app.options('*', cors(corsOptions));

app.use(bodyParser.json({ limit: "2mb" }));
app.use(requestIdMiddleware);

// 指标收集中间件
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  const start = Date.now();
  metrics.requests.total++;
  metrics.requests.byPath[req.path] = (metrics.requests.byPath[req.path] || 0) + 1;
  res.on("finish", () => {
    const duration = Date.now() - start;
    metrics.latency.total += duration;
    metrics.latency.count++;
    metrics.latency.avg = metrics.latency.total / metrics.latency.count;
    if (res.statusCode >= 200 && res.statusCode < 400) {
      metrics.requests.success++;
    } else {
      metrics.requests.error++;
    }
  });
  next();
});

// 速率限制
const rateLimiter = new RateLimiterMemory({
  points: config.rateLimit.max,
  duration: Math.floor(config.rateLimit.windowMs / 1000),
});
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  rateLimiter.consume(req.ip || req.socket.remoteAddress || 'unknown')
    .then(() => next())
    .catch(() => {
      res.status(429).json({
        ok: false,
        error: `Too many requests, please try again later. Max ${config.rateLimit.max} requests per ${config.rateLimit.windowMs / 1000}s.`,
        timestamp: Date.now(),
      });
    });
});

// ── 注册所有路由（按职责拆分到 routes/*） ──────────────────

const ctx: RouteContext = { runtimeConfig, grpcClient, memoryClient, fuxiProto, config };
registerChatRoutes(app, ctx);
registerToolRoutes(app, ctx);
registerMemoryRoutes(app, ctx);
registerUiRoutes(app, ctx);

// 降级处理中间件（最后注册，兜底所有错误）
app.use(degradedHandler);

// ── 启动服务器 ──────────────────────────────────────────

export function startServer(port: number = config.httpPort) {
  const server = http.createServer(app);
  // WebSocket 绑到同一 server
  attachChatSocket(server, ctx);

  const printBanner = (scheme: string) => {
    console.log(`\n================================================`);
    console.log(`  Fuxi Gateway started (${scheme} + WebSocket)`);
    console.log(`  ${scheme} port: ${port}`);
    console.log(`  WebSocket: ws://localhost:${port}/ws/chat`);
    console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
    console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
    console.log(`  Auth enabled: ${config.auth.enabled}`);
    console.log(`================================================\n`);
  };

  // 检查是否启用 TLS
  const tlsCertPath = process.env.TLS_CERT_PATH;
  const tlsKeyPath = process.env.TLS_KEY_PATH;

  if (tlsCertPath && tlsKeyPath) {
    try {
      const https = require('https');
      const fs = require('fs');
      const httpsOptions = { cert: fs.readFileSync(tlsCertPath), key: fs.readFileSync(tlsKeyPath) };
      const httpsServer = https.createServer(httpsOptions, app);
      attachChatSocket(httpsServer, ctx);
      httpsServer.listen(port, () => printBanner("HTTPS"));
    } catch (e) {
      console.error('Failed to start HTTPS server:', e);
      console.log('Falling back to HTTP...');
      server.listen(port, () => printBanner("HTTP"));
    }
  } else {
    server.listen(port, () => printBanner("HTTP"));
  }

  // 优雅关闭
  const gracefulShutdown = () => {
    console.log('Shutting down gracefully...');
    server.close(() => {
      try { grpcClient.close(); memoryClient.close(); } catch (e) { /* ignore */ }
      process.exit(0);
    });
  };
  process.on('SIGTERM', gracefulShutdown);
  process.on('SIGINT', gracefulShutdown);

  process.on('unhandledRejection', (reason) => console.error('[FATAL] Unhandled Rejection:', reason));
  process.on('uncaughtException', (err) => {
    console.error('[FATAL] Uncaught Exception:', err.message);
    console.error(err.stack);
  });

  return server;
}

if (require.main === module) {
  const { loadEnv } = require("./config");
  loadEnv();
  startServer(config.httpPort);
}

export default app;
