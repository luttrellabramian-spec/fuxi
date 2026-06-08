/** Express 网关 - HTTP → gRPC 转发层（v0.3.0: WebSocket + 请求追踪 + 降级中间件） */
import express from "express";
import http from "http";
import fs from "fs";
import path from "path";
import ws from "ws";
import helmet from "helmet";
import bodyParser from "body-parser";
import cors from "cors";
import { config } from "./config";
import { grpcClient, memoryClient } from "./grpc_client";
import * as grpc from "@grpc/grpc-js";
import { RateLimiterMemory } from "rate-limiter-flexible";
// @ts-ignore - generated proto file
import fuxiProto from "../src/proto/fuxi_pb.js";
import { requestIdMiddleware, getRequestId } from "./middleware/requestId";
import { catchAsync, wrapResponse, stripThinkTagsInPlace } from "./middleware/asyncHandler";
import { degradedHandler } from "./middleware/degradedHandler";

// 扩展 Express Request 类型
declare global {
  namespace Express {
    interface Request {
      traceId?: string;
    }
  }
}

const app = express();
const server = http.createServer(app);

/** 读取 UI 模板（兼容 dev 跑 src 和 prod 跑 dist）
 *  候选路径：
 *    1) __dirname/ui/<filename>  (生产：dist/ui/)
 *    2) __dirname/../src/ui/<filename>  (开发：src/ 跑)
 *  找不到时返回简单占位符并记录错误。 */
function readUiTemplate(name: string): string {
  const candidates = [
    path.join(__dirname, "ui", name),
    path.join(__dirname, "..", "src", "ui", name),
  ];
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, "utf-8");
    } catch (e) {
      // 继续尝试下一个
    }
  }
  console.error(`[ERROR] UI template not found: ${name}, tried:`, candidates);
  return `<!DOCTYPE html><html><body><h1>Template ${name} not found</h1></body></html>`;
}

// P2-2: WebSocket 服务器（与 HTTP 共用端口，支持双向通信）
const wss = new ws.Server({ server, path: "/ws/chat" });

// WebSocket 连接管理
const wsSessions = new Map<string, ws.WebSocket>();

wss.on("connection", (ws: ws.WebSocket, req: http.IncomingMessage) => {
  const url = new URL(req.url || "/", `http://${req.headers.host}`);
  const sessionId = url.searchParams.get("session_id") || `ws-${Date.now()}`;
  const requestId = getRequestId(req as any);

  wsSessions.set(sessionId, ws);
  console.log(`[WS] Client connected: session=${sessionId}, request_id=${requestId}`);

  ws.on("message", async (data: ws.Data) => {
    try {
      const msg = JSON.parse(data.toString());
      // 处理 WebSocket 消息（双向通信支持）
      if (msg.type === "chat") {
        // 处理聊天消息
        const { message, model } = msg;
        if (!message) {
          ws.send(JSON.stringify({ error: "message is required" }));
          return;
        }

        const metadata = new grpc.Metadata();
        const apiKey = runtimeConfig.apiKey || config.auth.apiKey;
        const baseUrl = runtimeConfig.baseUrl || config.auth.baseUrl;
        if (apiKey) metadata.add("authorization", `Bearer ${apiKey}`);
        if (baseUrl) metadata.add("base-url", baseUrl);
        if (model) metadata.add("model", model);

        const reqMsg = new fuxiProto.CompletionRequest();
        reqMsg.setSessionId(sessionId);
        reqMsg.setUserMessage(message);
        if (model) reqMsg.setModel(model);
        reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

        const deadline = new Date();
        deadline.setSeconds(deadline.getSeconds() + 60);

        const call = grpcClient.streamComplete(reqMsg, metadata, { deadline });
        let fullContent = "";

        call.on("data", (chunk: any) => {
          const content = typeof chunk.getContent === "function" ? chunk.getContent() : chunk.content;
          const is_final = typeof chunk.getIsFinal === "function" ? chunk.getIsFinal() : chunk.is_final;
          fullContent += content || "";
          ws.send(JSON.stringify({ type: "token", content, is_final: !!is_final }));
        });

        call.on("end", () => {
          ws.send(JSON.stringify({ type: "done", content: fullContent }));
        });

        call.on("error", (err: any) => {
          ws.send(JSON.stringify({ type: "error", error: err.message }));
        });
      } else if (msg.type === "ping") {
        ws.send(JSON.stringify({ type: "pong", timestamp: Date.now() }));
      }
    } catch (e: any) {
      ws.send(JSON.stringify({ type: "error", error: e.message }));
    }
  });

  ws.on("close", () => {
    wsSessions.delete(sessionId);
    console.log(`[WS] Client disconnected: session=${sessionId}`);
  });

  ws.on("error", (err: Error) => {
    console.error(`[WS] Error for ${sessionId}:`, err.message);
  });
});

// 安全中间件
app.use(helmet({
  contentSecurityPolicy: false, // 允许内联脚本（设置/聊天 UI 使用 onclick 属性）
  crossOriginEmbedderPolicy: false,
}));

// CORS 配置（生产环境建议限制来源）
const corsOptions: cors.CorsOptions = {
  origin: ['http://localhost:18789', 'http://127.0.0.1:18789', 'http://localhost:3000'],
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'X-Request-ID', 'X-Base-Url'],
  credentials: true,
  maxAge: 86400,
};
app.use(cors(corsOptions));
app.options('*', cors(corsOptions));

// 请求体大小限制（防止恶意超大请求）
app.use(bodyParser.json({ limit: "2mb" }));

// 请求追踪中间件（必须早注册，确保所有路由都能获取 request_id）
app.use(requestIdMiddleware);

// 指标收集中间件（必须在所有路由之前注册）
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

// 速率限制中间件（必须在所有路由之前注册）
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

// 监控指标
const metrics = {
  requests: { total: 0, success: 0, error: 0, byPath: {} as Record<string, number> },
  latency: { total: 0, count: 0, avg: 0 },
  uptime: Date.now(),
};

// 日志配置
const logger = {
  info: (...args: any[]) => console.log('[INFO]', ...args),
  warn: (...args: any[]) => console.warn('[WARN]', ...args),
  error: (...args: any[]) => console.error('[ERROR]', ...args),
};

/** 从请求头提取用户自定义配置 */
function extractUserConfig(req: express.Request): { apiKey?: string, baseUrl?: string } {
  const authHeader = req.headers['authorization'] as string;
  const baseUrlHeader = req.headers['x-base-url'] as string;
  return {
    apiKey: authHeader?.replace('Bearer ', ''),
    baseUrl: baseUrlHeader,
  };
}

/** POST /chat - 聊天对话 */
app.post(
  "/chat",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { message, session_id = "default", model: bodyModel = "" } = req.body;
    if (!message) {
      return wrapResponse(res, false, null, "message is required");
    }

    // 模型优先级：请求body > runtimeConfig > config.auth
    const model = bodyModel || runtimeConfig.model || config.auth.model || "";

    // API key 优先级：请求 Authorization header > runtimeConfig > config.auth
    // 让客户端可按用户/按请求覆盖服务端默认 key
    const userConfig = extractUserConfig(req);
    const apiKey = userConfig.apiKey || runtimeConfig.apiKey || config.auth.apiKey;
    const baseUrl = userConfig.baseUrl || runtimeConfig.baseUrl || config.auth.baseUrl;

    const metadata = new grpc.Metadata();
    if (apiKey) metadata.add('authorization', `Bearer ${apiKey}`);
    if (baseUrl) metadata.add('base-url', baseUrl);
    if (model) metadata.add('model', model);

    // 多轮对话需要更长时间，设 60s
    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 60);

    const reqMsg = new fuxiProto.CompletionRequest();
    reqMsg.setSessionId(session_id);
    reqMsg.setUserMessage(message);
    if (model) reqMsg.setModel(model);
    reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

    const call = grpcClient.streamComplete(reqMsg, metadata, { deadline: deadline });

    let fullContent = "";
    let responded = false;

    call.on("data", (chunk: any) => {
      const content = typeof chunk.getContent === 'function' ? chunk.getContent() : chunk.content;
      const is_final = typeof chunk.getIsFinal === 'function' ? chunk.getIsFinal() : chunk.is_final;
      // v0.2.7: 累积所有 token 用于流式调试
      fullContent += content || "";
      // v0.2.7: 收到 final chunk 时，用其 content 替换累积（final 已是解析后的最终答案）
      if (is_final && !responded) {
        responded = true;
        const finalContent = content || fullContent;
        const cleaned = stripThinkTagsInPlace(finalContent);
        wrapResponse(res, true, { content: cleaned, model: model || '当前模型' });
      }
    });

    call.on("end", () => {
      if (!responded) {
        responded = true;
        const cleaned = stripThinkTagsInPlace(fullContent);
        wrapResponse(res, true, { content: cleaned, model: model || '当前模型' });
      }
    });

    call.on("error", (err: any) => {
      if (!responded) {
        responded = true;
        wrapResponse(res, false, null, err.message);
      }
    });
  })
);

/** POST /chat/stream - 流式聊天对话（SSE） */
app.post("/chat/stream", catchAsync(async (req: express.Request, res: express.Response) => {
  const { message, session_id = "default", model: bodyModel = "" } = req.body;
  if (!message) {
    res.status(400).json({ ok: false, error: "message is required" });
    return;
  }

  // 模型优先级：请求body > runtimeConfig > config.auth
  const model = bodyModel || runtimeConfig.model || config.auth.model || "";

  // API key 优先级：请求 Authorization header > runtimeConfig > config.auth
  const userConfig = extractUserConfig(req);
  const apiKey = userConfig.apiKey || runtimeConfig.apiKey || config.auth.apiKey;
  const baseUrl = userConfig.baseUrl || runtimeConfig.baseUrl || config.auth.baseUrl;

  const metadata = new grpc.Metadata();
  if (apiKey) metadata.add('authorization', `Bearer ${apiKey}`);
  if (baseUrl) metadata.add('base-url', baseUrl);
  if (model) metadata.add('model', model);

  // SSE 响应头
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',  // 禁用 Nginx 缓冲
  });

  const reqMsg = new fuxiProto.CompletionRequest();
  reqMsg.setSessionId(session_id);
  reqMsg.setUserMessage(message);
  if (model) reqMsg.setModel(model);
  reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

  const deadline = new Date();
  deadline.setSeconds(deadline.getSeconds() + 60);

  const call = grpcClient.streamComplete(reqMsg, metadata, { deadline: deadline });
  let hasSentData = false;

  call.on("data", (chunk: any) => {
    const content = typeof chunk.getContent === 'function' ? chunk.getContent() : chunk.content;
    const is_final = typeof chunk.getIsFinal === 'function' ? chunk.getIsFinal() : chunk.is_final;
    
    if (content) {
      hasSentData = true;
      res.write(`data: ${JSON.stringify({ content, is_final: !!is_final })}\n\n`);
    }
  });

  call.on("end", () => {
    if (!hasSentData) {
      // 空响应，发送一个标记信息
      res.write(`data: ${JSON.stringify({ content: "", is_final: true })}\n\n`);
    }
    res.write('data: [DONE]\n\n');
    res.end();
  });

  call.on("error", (err: any) => {
    // 错误信息友好化
    let errorMsg = err.message || "未知错误";
    if (errorMsg.includes("DEADLINE_EXCEEDED")) {
      errorMsg = "服务响应超时，请稍后重试";
    } else if (errorMsg.includes("UNAVAILABLE")) {
      errorMsg = "服务暂时不可用";
    }
    res.write(`data: ${JSON.stringify({ error: errorMsg, is_final: true })}\n\n`);
    res.write('data: [DONE]\n\n');
    res.end();
  });

  // 客户端断开时取消 gRPC 流，防止资源泄漏
  req.on("close", () => {
    try { call.cancel(); } catch(e) { /* ignore */ }
  });
}));

/** POST /tool/invoke - 调用工具 */
app.post(
  "/tool/invoke",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { tool_name, arguments: args, session_id = "default", model } = req.body;
    if (!tool_name) {
      return wrapResponse(res, false, null, "tool_name is required");
    }

    const userConfig = extractUserConfig(req);
    const metadata = new grpc.Metadata();

    if (userConfig.apiKey) {
      metadata.add('authorization', `Bearer ${userConfig.apiKey}`);
    } else if (runtimeConfig.apiKey) {
      metadata.add('authorization', `Bearer ${runtimeConfig.apiKey}`);
    } else if (config.auth.apiKey) {
      metadata.add('authorization', `Bearer ${config.auth.apiKey}`);
    }
    if (userConfig.baseUrl) {
      metadata.add('base-url', userConfig.baseUrl);
    } else if (runtimeConfig.baseUrl) {
      metadata.add('base-url', runtimeConfig.baseUrl);
    } else if (config.auth.baseUrl) {
      metadata.add('base-url', config.auth.baseUrl);
    }

    // 模型优先级：请求body > runtimeConfig > config
    const resolvedToolModel = model || runtimeConfig.model || config.auth.model || "";
    if (resolvedToolModel) {
      metadata.add('model', resolvedToolModel);
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 15);

    const toolReqMsg = new fuxiProto.ToolRequest();
    toolReqMsg.setToolName(tool_name);
    toolReqMsg.setArgumentsJson(JSON.stringify(args || {}));
    toolReqMsg.setSessionId(session_id);
    if (resolvedToolModel) toolReqMsg.setModel(resolvedToolModel);

    grpcClient.invokeTool(toolReqMsg, metadata, { deadline: deadline }, (error: any, response: any) => {
      if (error) {
        logger.error("InvokeTool error:", error);
        return wrapResponse(res, false, null, error.message);
      }
      let result = {};
      try {
        const resultJson = typeof response.getResultJson === 'function' ? response.getResultJson() : response.result_json;
        result = resultJson ? JSON.parse(resultJson) : {};
      } catch (e) {
        const resultJson = typeof response.getResultJson === 'function' ? response.getResultJson() : response.result_json;
        result = { raw: resultJson };
      }
      const respSuccess = typeof response.getSuccess === 'function' ? response.getSuccess() : response.success;
      const respElapsed = typeof response.getElapsedMs === 'function' ? response.getElapsedMs() : response.elapsed_ms;
      const respError = typeof response.getError === 'function' ? response.getError() : response.error;
      wrapResponse(res, respSuccess, { result, elapsed_ms: Number(respElapsed) }, respError);
    });
  })
);

/** GET /tool/list - 列出所有可用工具（从缓存 + gRPC 动态获取） */
app.get(
  "/tool/list",
  catchAsync(async (req: express.Request, res: express.Response) => {
    // 先返回内置工具列表作为快速响应
    const fallbackTools = [
      { name: "read_file", level: "L0", desc: "读取文件内容" },
      { name: "write_file", level: "L1", desc: "写入文件内容" },
      { name: "list_files", level: "L0", desc: "列出目录下文件" },
      { name: "file_exists", level: "L0", desc: "检查文件是否存在" },
      { name: "read_json", level: "L0", desc: "读取 JSON 文件" },
      { name: "write_json", level: "L1", desc: "写入 JSON 文件" },
      { name: "check_url", level: "L0", desc: "检查 URL 可达性" },
      { name: "grep", level: "L0", desc: "在文件中搜索文本" },
      { name: "search_replace", level: "L1", desc: "搜索并替换文本" },
      { name: "search_file", level: "L0", desc: "按模式搜索文件" },
      { name: "memory_write", level: "L1", desc: "写入记忆（hot/warm/cold）" },
      { name: "memory_query", level: "L0", desc: "查询记忆" },
      { name: "memory_get_recent", level: "L0", desc: "获取最近记忆" },
    ];
    wrapResponse(res, true, { tools: fallbackTools, source: "cache" });
  })
);

/** GET /memory/hot - 查询热记忆 */
app.get(
  "/memory/hot",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const session_id = (req.query.session_id as string) || "default";

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const hotQueryMsg = new fuxiProto.HotQuery();
    hotQueryMsg.setSessionId(session_id);

    memoryClient.queryHot(hotQueryMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryHot error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const memContent = typeof response.getMemoryContent === 'function' ? response.getMemoryContent() : response.memory_content;
      const charCount = typeof response.getCharCount === 'function' ? response.getCharCount() : response.char_count;
      wrapResponse(res, true, {
        content: memContent,
        char_count: charCount,
      });
    });
  })
);

/** POST /memory/hot - 写入热记忆 */
app.post(
  "/memory/hot",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { content, session_id = "default" } = req.body;
    if (!content) {
      return wrapResponse(res, false, null, "content is required");
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const memWriteMsg = new fuxiProto.MemoryWrite();
    memWriteMsg.setMemoryType("hot");
    memWriteMsg.setContent(content);
    memWriteMsg.setSessionId(session_id);

    memoryClient.persistMemory(memWriteMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory hot error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const respSuccess = typeof response.getSuccess === 'function' ? response.getSuccess() : response.success;
      const respId = typeof response.getId === 'function' ? response.getId() : response.id;
      const respError = typeof response.getError === 'function' ? response.getError() : response.error;
      wrapResponse(res, respSuccess, { id: respId }, respError);
    });
  })
);

/** POST /memory/warm/add - 添加到温记忆 */
app.post(
  "/memory/warm/add",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { content, session_id = "default" } = req.body;
    if (!content) {
      return wrapResponse(res, false, null, "content is required");
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const memWriteWarmMsg = new fuxiProto.MemoryWrite();
    memWriteWarmMsg.setMemoryType("warm");
    memWriteWarmMsg.setContent(content);
    memWriteWarmMsg.setSessionId(session_id);

    memoryClient.persistMemory(memWriteWarmMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory warm error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const respSuccess = typeof response.getSuccess === 'function' ? response.getSuccess() : response.success;
      const respId = typeof response.getId === 'function' ? response.getId() : response.id;
      const respError = typeof response.getError === 'function' ? response.getError() : response.error;
      wrapResponse(res, respSuccess, { id: respId }, respError);
    });
  })
);

/** GET /memory/warm/recent - 获取温记忆最近 */
app.get(
  "/memory/warm/recent",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const session_id = (req.query.session_id as string) || "default";
    const limit = parseInt(req.query.limit as string) || 50;

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const warmQueryMsg = new fuxiProto.WarmQuery();
    warmQueryMsg.setSessionId(session_id);
    warmQueryMsg.setLimit(limit);

    memoryClient.queryWarm(warmQueryMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryWarm error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const entries = (response.getEntriesList ? response.getEntriesList() : response.entries || []).map((e: any) => ({
        id: typeof e.getId === 'function' ? e.getId() : e.id,
        content: typeof e.getContent === 'function' ? e.getContent() : e.content,
        timestamp: typeof e.getTimestamp === 'function' ? e.getTimestamp() : e.timestamp,
      }));
      wrapResponse(res, true, {
        entries,
      });
    });
  })
);

/** GET /memory/warm/search - 搜索温记忆 */
app.get(
  "/memory/warm/search",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const session_id = (req.query.session_id as string) || "default";
    const query = (req.query.query as string) || "";
    const limit = parseInt(req.query.limit as string) || 10;

    if (!query) {
      return wrapResponse(res, false, null, "query parameter is required");
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const warmSearchMsg = new fuxiProto.WarmQuery();
    warmSearchMsg.setSessionId(session_id);
    warmSearchMsg.setQuery(query);
    warmSearchMsg.setLimit(limit);

    memoryClient.queryWarm(warmSearchMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryWarm search error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const entries = (response.getEntriesList ? response.getEntriesList() : response.entries || []).map((e: any) => ({
        id: typeof e.getId === 'function' ? e.getId() : e.id,
        content: typeof e.getContent === 'function' ? e.getContent() : e.content,
        timestamp: typeof e.getTimestamp === 'function' ? e.getTimestamp() : e.timestamp,
      }));
      wrapResponse(res, true, {
        entries,
      });
    });
  })
);

/** POST /memory/cold/add - 添加到冷记忆 */
app.post(
  "/memory/cold/add",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { content, summary, session_id = "default", metadata = {} } = req.body;
    if (!content || !summary) {
      return wrapResponse(res, false, null, "content and summary are required");
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const memWriteColdMsg = new fuxiProto.MemoryWrite();
    memWriteColdMsg.setMemoryType("cold");
    memWriteColdMsg.setContent(content);
    memWriteColdMsg.setSessionId(session_id);
    if (summary) {
      memWriteColdMsg.setSummary(summary);
    }
    // 传递 metadata（protobuf map<string,string>）
    if (metadata && typeof metadata === 'object') {
      const metaMap = memWriteColdMsg.getMetadataMap ? memWriteColdMsg.getMetadataMap() : null;
      if (metaMap) {
        Object.entries(metadata).forEach(([k, v]) => {
          if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
            metaMap.set(k, String(v));
          }
        });
      }
    }

    memoryClient.persistMemory(memWriteColdMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory cold error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const respSuccess = typeof response.getSuccess === 'function' ? response.getSuccess() : response.success;
      const respId = typeof response.getId === 'function' ? response.getId() : response.id;
      const respError = typeof response.getError === 'function' ? response.getError() : response.error;
      wrapResponse(res, respSuccess, { id: respId }, respError);
    });
  })
);

/** GET /memory/cold/recent - 获取冷记忆最近 */
app.get(
  "/memory/cold/recent",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const session_id = (req.query.session_id as string) || "default";
    const limit = parseInt(req.query.limit as string) || 10;

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    const coldQueryMsg = new fuxiProto.ColdQuery();
    coldQueryMsg.setSessionId(session_id);
    coldQueryMsg.setLimit(limit);

    memoryClient.queryCold(coldQueryMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryCold error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const memories = (response.getMemoriesList ? response.getMemoriesList() : response.memories || []).map((m: any) => ({
        id: typeof m.getId === 'function' ? m.getId() : m.id,
        content: typeof m.getContent === 'function' ? m.getContent() : m.content,
        similarity: typeof m.getSimilarity === 'function' ? m.getSimilarity() : m.similarity,
      }));
      wrapResponse(res, true, {
        memories,
      });
    });
  })
);

/** GET /memory/cold/search - 搜索冷记忆 */
app.get(
  "/memory/cold/search",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const query = (req.query.query as string) || "";
    const session_id = (req.query.session_id as string) || "default";
    const limit = parseInt(req.query.limit as string) || 10;

    if (!query) {
      return wrapResponse(res, false, null, "query parameter is required");
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 10);

    const coldSearchMsg = new fuxiProto.ColdQuery();
    coldSearchMsg.setSessionId(session_id);
    coldSearchMsg.setQuery(query);
    coldSearchMsg.setLimit(limit);

    memoryClient.queryCold(coldSearchMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryCold search error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      const memories = (response.getMemoriesList ? response.getMemoriesList() : response.memories || []).map((m: any) => ({
        id: typeof m.getId === 'function' ? m.getId() : m.id,
        content: typeof m.getContent === 'function' ? m.getContent() : m.content,
        similarity: typeof m.getSimilarity === 'function' ? m.getSimilarity() : m.similarity,
      }));
      wrapResponse(res, true, {
        memories,
      });
    });
  })
);


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

/** GET /settings - 获取当前配置 */
app.get(
  "/settings",
  catchAsync(async (req: express.Request, res: express.Response) => {
    wrapResponse(res, true, {
      config: {
        model: runtimeConfig.model,
        apiKey: runtimeConfig.apiKey ? "***" + runtimeConfig.apiKey.slice(-4) : "",
        baseUrl: runtimeConfig.baseUrl,
        maxTokens: runtimeConfig.maxTokens,
        temperature: runtimeConfig.temperature,
      },
      envDefaults: {
        LLM_API_KEY: process.env.LLM_API_KEY ? "已设置" : "未设置",
        LLM_BASE_URL: process.env.LLM_BASE_URL || "未设置",
        LLM_MODEL: process.env.DEFAULT_MODEL || process.env.LLM_MODEL || "未设置",
      },
    });
  })
);

/** POST /settings - 更新配置 */
app.post(
  "/settings",
  catchAsync(async (req: express.Request, res: express.Response) => {
    // 验证认证 - 只有明确设置 AUTH_ENABLED=true 时才验证
    const authEnabled = process.env.AUTH_ENABLED === 'true';
    const settingsApiKey = process.env.SETTINGS_API_KEY || config.auth.apiKey;

    if (authEnabled && settingsApiKey) {
      const authHeader = req.headers['authorization'] as string;
      if (!authHeader || authHeader.replace('Bearer ', '') !== settingsApiKey) {
        return res.status(401).json({
          ok: false,
          error: "Unauthorized: invalid or missing API key",
          timestamp: Date.now(),
        });
      }
    }

    const { model, apiKey, baseUrl, maxTokens, temperature } = req.body;
    if (model !== undefined) runtimeConfig.model = model;
    if (apiKey !== undefined) runtimeConfig.apiKey = apiKey;
    if (baseUrl !== undefined) runtimeConfig.baseUrl = baseUrl;
    if (maxTokens !== undefined) runtimeConfig.maxTokens = parseInt(String(maxTokens), 10);
    if (temperature !== undefined) runtimeConfig.temperature = parseFloat(String(temperature));

    (global as any).__fuxi_runtime_config = runtimeConfig;

    try {
      const fs = require('fs');
      const path = require('path');
      const configDir = path.join(__dirname, '..', '..', 'config');
      const localConfigPath = path.join(configDir, 'local.yaml');

      if (!fs.existsSync(configDir)) {
        fs.mkdirSync(configDir, { recursive: true });
      }

      const yaml = require('js-yaml');
      const configContent = {
        llm: {
          api_key: runtimeConfig.apiKey,
          base_url: runtimeConfig.baseUrl,
          model: runtimeConfig.model,
        },
        gateway: {
          max_tokens: runtimeConfig.maxTokens,
          temperature: runtimeConfig.temperature,
        }
      };
      fs.writeFileSync(localConfigPath, yaml.dump(configContent), 'utf8');
    } catch(e) {
      console.error('Failed to save config file:', e);
    }

    wrapResponse(res, true, {
      message: "配置已保存到本地",
      config: {
        model: runtimeConfig.model,
        apiKey: runtimeConfig.apiKey ? "***" + runtimeConfig.apiKey.slice(-4) : "",
        baseUrl: runtimeConfig.baseUrl,
        maxTokens: runtimeConfig.maxTokens,
        temperature: runtimeConfig.temperature,
      },
    });
  })
);

/** GET /settings/ui - 可视化设置页面 */
app.get(
  "/settings/ui",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const html = readUiTemplate("settings.html");
    res.type("html").send(html);
  })
);

/** GET /chat/ui - 独立对话界面 */
app.get(
  "/chat/ui",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const html = readUiTemplate("chat.html");
    res.type("html").send(html);
  })
);

/** GET /health - 健康检查 */
app.get(
  "/health",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 3);

    const pingMsg = new fuxiProto.SessionPing();
    pingMsg.setSessionId("health-check");

    grpcClient.heartbeat(pingMsg, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        return res.status(503).json({
          ok: false,
          error: "gRPC service unavailable",
          timestamp: Date.now(),
        });
      }
      const alive = typeof response.getAlive === 'function' ? response.getAlive() : response.alive;
      const ts = typeof response.getTimestamp === 'function' ? response.getTimestamp() : response.timestamp;
      res.json({
        ok: true,
        alive: alive,
        timestamp: ts,
        grpcHost: `${config.grpcHost}:${config.grpcPort}`,
      });
    });
  })
);

/** GET /metrics - Prometheus 格式指标暴露（P2-3） */
app.get(
  "/metrics",
  catchAsync(async (req: express.Request, res: express.Response) => {
    // 检查是否请求 Prometheus 格式
    const accept = req.headers.accept || "";
    const wantsPrometheus = accept.includes("text/plain") || accept.includes("prometheus");

    if (wantsPrometheus || req.query.format === "prometheus") {
      // Prometheus 文本格式
      const uptimeSeconds = Math.floor((Date.now() - metrics.uptime) / 1000);
      const mem = process.memoryUsage();
      const memRss = Math.round(mem.rss / 1024 / 1024);  // MB
      const memHeapUsed = Math.round(mem.heapUsed / 1024 / 1024);  // MB

      const lines = [
        `# HELP fuxi_uptime_seconds Gateway uptime in seconds`,
        `# TYPE fuxi_uptime_seconds gauge`,
        `fuxi_uptime_seconds ${uptimeSeconds}`,
        ``,
        `# HELP fuxi_requests_total Total HTTP requests`,
        `# TYPE fuxi_requests_total counter`,
        `fuxi_requests_total ${metrics.requests.total}`,
        ``,
        `# HELP fuxi_requests_success_total Successful HTTP requests`,
        `# TYPE fuxi_requests_success_total counter`,
        `fuxi_requests_success_total ${metrics.requests.success}`,
        ``,
        `# HELP fuxi_requests_error_total Failed HTTP requests`,
        `# TYPE fuxi_requests_error_total counter`,
        `fuxi_requests_error_total ${metrics.requests.error}`,
        ``,
        `# HELP fuxi_latency_ms_total Total latency in milliseconds`,
        `# TYPE fuxi_latency_ms_total counter`,
        `fuxi_latency_ms_total ${metrics.latency.total}`,
        ``,
        `# HELP fuxi_latency_ms_avg Average latency in milliseconds`,
        `# TYPE fuxi_latency_ms_avg gauge`,
        `fuxi_latency_ms_avg ${Math.round(metrics.latency.avg)}`,
        ``,
        `# HELP fuxi_memory_rss_bytes RSS memory in bytes`,
        `# TYPE fuxi_memory_rss_bytes gauge`,
        `fuxi_memory_rss_bytes ${mem.rss}`,
        ``,
        `# HELP fuxi_memory_heap_used_bytes Heap used memory in bytes`,
        `# TYPE fuxi_memory_heap_used_bytes gauge`,
        `fuxi_memory_heap_used_bytes ${mem.heapUsed}`,
        ``,
        // 按路径统计的请求数
        ...Object.entries(metrics.requests.byPath || {}).map(([path, count]) => {
          const sanitizedPath = path.replace(/[^a-zA-Z0-9_]/g, "_");
          return [
            `# HELP fuxi_requests_by_path_requests_total Requests by path`,
            `# TYPE fuxi_requests_by_path_requests_total counter`,
            `fuxi_requests_by_path_requests_total{path="${path}"} ${count}`,
          ].join("\n");
        }),
      ].filter(Boolean);

      res.set("Content-Type", "text/plain; charset=utf-8");
      res.send(lines.join("\n"));
    } else {
      // 保持 JSON 格式（向后兼容）
      res.json({
        ok: true,
        data: {
          uptime: Math.floor((Date.now() - metrics.uptime) / 1000),
          requests: metrics.requests,
          latency: {
            avg: Math.round(metrics.latency.avg),
            count: metrics.latency.count,
          },
          memory: process.memoryUsage(),
          timestamp: Date.now(),
        },
      });
    }
  })
);

/** 降级处理中间件（v0.2.0: 统一降级格式，隐藏服务端堆栈） */
app.use(degradedHandler);

/** 启动服务器 */
export function startServer(port: number = config.httpPort) {
  let server: any;

  // 检查是否启用 TLS
  const tlsCertPath = process.env.TLS_CERT_PATH;
  const tlsKeyPath = process.env.TLS_KEY_PATH;

  if (tlsCertPath && tlsKeyPath) {
    // HTTPS 模式
    try {
      const https = require('https');
      const fs = require('fs');
      
      const httpsOptions = {
        cert: fs.readFileSync(tlsCertPath),
        key: fs.readFileSync(tlsKeyPath),
      };
      
      server = https.createServer(httpsOptions, app).listen(port, () => {
        console.log(`\n================================================`);
        console.log(`  Fuxi Gateway started (HTTPS + WebSocket)`);
        console.log(`  HTTPS port: ${port}`);
        console.log(`  WebSocket: ws://localhost:${port}/ws/chat`);
        console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
        console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
        console.log(`  Auth enabled: ${config.auth.enabled}`);
        console.log(`================================================\n`);
      });
    } catch (e) {
      console.error('Failed to start HTTPS server:', e);
      console.log('Falling back to HTTP...');
      server.listen(port, () => {
        console.log(`\n================================================`);
        console.log(`  Fuxi Gateway started (HTTP + WebSocket)`);
        console.log(`  HTTP port: ${port}`);
        console.log(`  WebSocket: ws://localhost:${port}/ws/chat`);
        console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
        console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
        console.log(`  Auth enabled: ${config.auth.enabled}`);
        console.log(`================================================\n`);
      });
    }
  } else {
    // HTTP 模式
    server = http.createServer(app).listen(port, () => {
      console.log(`\n================================================`);
      console.log(`  Fuxi Gateway started`);
      console.log(`  HTTP port: ${port}`);
      console.log(`  WebSocket: ws://localhost:${port}/ws/chat`);
      console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
      console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
      console.log(`  Auth enabled: ${config.auth.enabled}`);
      console.log(`================================================\n`);
    });
  }

  // 优雅关闭
  const gracefulShutdown = () => {
    console.log('Shutting down gracefully...');
    server.close(() => {
      console.log('HTTP server closed.');
      // 关闭 gRPC 连接
      try {
        grpcClient.close();
        memoryClient.close();
        console.log('gRPC connections closed.');
      } catch (e) {
        console.error('Error closing gRPC connections:', e);
      }
      process.exit(0);
    });
  };

  process.on('SIGTERM', gracefulShutdown);
  process.on('SIGINT', gracefulShutdown);

  // 全局未捕获异常处理
  process.on('unhandledRejection', (reason, promise) => {
    console.error('[FATAL] Unhandled Rejection:', reason);
  });
  process.on('uncaughtException', (err) => {
    console.error('[FATAL] Uncaught Exception:', err.message);
    console.error(err.stack);
    // 不退出进程，让健康检查检测到异常后由外部重启
  });

  return server;
}

// 直接启动
if (require.main === module) {
  const { loadEnv } = require("./config");
  loadEnv();
  startServer(config.httpPort);
}

export default app;
