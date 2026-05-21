/** Express 网关 - HTTP → gRPC 转发层（v0.3.0: WebSocket + 请求追踪 + 降级中间件） */
import express from "express";
import http from "http";
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

/** 统一响应包装（v0.2.0: 增加 request_id） */
function wrapResponse(res: express.Response, success: boolean, data?: any, error?: string) {
  return res.json({
    ok: success,
    data: data || null,
    error: error || null,
    timestamp: Date.now(),
    request_id: res.req?.headers['x-request-id'] || '',
  });
}

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

/** 错误捕获包装 */
function catchAsync(fn: Function) {
  return (req: express.Request, res: express.Response, next: express.NextFunction) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

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

    const metadata = new grpc.Metadata();
    const apiKey = runtimeConfig.apiKey || config.auth.apiKey;
    const baseUrl = runtimeConfig.baseUrl || config.auth.baseUrl;
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
      fullContent += content || "";
      // is_final 时标记完成但不 cancel（让流自然走到 end 事件）
      if (is_final && !responded) {
        responded = true;
        wrapResponse(res, true, { content: fullContent, model: model || '当前模型' });
      }
    });

    call.on("end", () => {
      if (!responded) {
        responded = true;
        wrapResponse(res, true, { content: fullContent, model: model || '当前模型' });
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

  const metadata = new grpc.Metadata();
  const apiKey = runtimeConfig.apiKey || config.auth.apiKey;
  const baseUrl = runtimeConfig.baseUrl || config.auth.baseUrl;
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
    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>伏羲 · 设置</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f0f14; color: #e0e0e8; min-height: 100vh; }
  .header { background: #1a1a24; border-bottom: 1px solid #2a2a3a; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 20px; font-weight: 600; color: #fff; }
  .header-right { display: flex; gap: 12px; }
  .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; transition: all 0.2s; text-decoration: none; display: inline-flex; align-items: center; }
  .btn-primary { background: #6b6bff; color: #fff; }
  .btn-ghost { background: transparent; color: #888; border: 1px solid #2a2a3a; }
  .btn-ghost:hover { border-color: #444; color: #b0b0c0; }
  .container { max-width: 720px; margin: 0 auto; padding: 40px 20px; }
  h1 { font-size: 28px; font-weight: 600; margin-bottom: 8px; color: #fff; }
  .subtitle { color: #888; font-size: 14px; margin-bottom: 32px; }
  .card { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
  .card-title { font-size: 14px; font-weight: 600; color: #fff; margin-bottom: 20px; display: flex; align-items: center; gap: 8px; }
  .card-title .icon { font-size: 18px; }
  .field { margin-bottom: 20px; }
  .field:last-child { margin-bottom: 0; }
  label { display: block; font-size: 13px; color: #888; margin-bottom: 8px; font-weight: 500; }
  input[type="text"], input[type="number"], input[type="password"], select {
    width: 100%; padding: 12px 16px; background: #0f0f18; border: 1px solid #2a2a3a; border-radius: 8px; color: #fff; font-size: 14px; outline: none; transition: border-color 0.2s;
  }
  input:focus, select:focus { border-color: #6b6bff; }
  input::placeholder { color: #555; }
  .hint { font-size: 12px; color: #666; margin-top: 8px; line-height: 1.5; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 12px 24px; border-radius: 8px; font-size: 14px; cursor: pointer; border: none; transition: all 0.2s; }
  .btn-primary { background: #6b6bff; color: #fff; }
  .btn-primary:hover { background: #5a5aee; }
  .btn-success { background: #22c55e; color: #fff; }
  .btn-success:hover { background: #16a34a; }
  .btn-ghost { background: transparent; color: #888; border: 1px solid #2a2a3a; }
  .btn-ghost:hover { border-color: #444; color: #b0b0c0; }
  .btn-danger { background: #ef4444; color: #fff; }
  .btn-danger:hover { background: #dc2626; }
  .actions { display: flex; gap: 12px; margin-top: 24px; }
  .status { font-size: 13px; color: #888; margin-top: 16px; text-align: center; padding: 12px; border-radius: 8px; }
  .status.success { background: #22c55e20; color: #22c55e; }
  .status.error { background: #ef444420; color: #ef4444; }
  .env-row { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #222; font-size: 13px; }
  .env-row:last-child { border-bottom: none; }
  .env-key { color: #888; font-weight: 500; }
  .env-val { color: #6b6bff; font-family: monospace; }
  .env-val.empty { color: #444; font-style: italic; }
  .divider { border: none; border-top: 1px solid #2a2a3a; margin: 24px 0; }
  .preset-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #2a2a3a; background: transparent; color: #888; margin-right: 8px; margin-bottom: 8px; transition: all 0.2s; }
  .preset-btn:hover { border-color: #6b6bff; color: #6b6bff; background: #6b6bff10; }
  .preset-btn.active { border-color: #6b6bff; color: #6b6bff; background: #6b6bff20; }
  .presets { margin-bottom: 20px; }
  .presets-label { font-size: 12px; color: #666; margin-bottom: 12px; font-weight: 500; }
  .provider-section { margin-bottom: 24px; }
  .provider-title { font-size: 12px; color: #888; margin-bottom: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
  .badge-required { background: #ef444420; color: #ef4444; }
  .badge-optional { background: #6b6bff20; color: #6b6bff; }
  .tabs { display: flex; gap: 4px; margin-bottom: 20px; background: #0f0f18; border-radius: 8px; padding: 4px; }
  .tab { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; background: transparent; color: #888; transition: all 0.2s; }
  .tab.active { background: #6b6bff; color: #fff; }
  .tab:hover:not(.active) { color: #fff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .test-btn { margin-top: 12px; }
  .test-result { margin-top: 12px; padding: 12px; border-radius: 8px; font-size: 13px; display: none; }
  .test-result.success { background: #22c55e20; color: #22c55e; display: block; }
  .test-result.error { background: #ef444420; color: #ef4444; display: block; }
</style>
</head>
<body>
<div class="header">
  <h1>⚙️ 伏羲设置</h1>
  <div class="header-right">
    <a href="/chat/ui" class="btn btn-primary">💬 进入对话</a>
  </div>
</div>
<div class="container">
  <h1>伏羲设置</h1>
  <p class="subtitle">配置 LLM API 和系统参数，所有更改立即生效</p>

  <div class="tabs">
    <button class="tab active" data-tab="llm" onclick="switchTab('llm', event)">LLM 配置</button>
    <button class="tab" data-tab="system" onclick="switchTab('system', event)">系统配置</button>
    <button class="tab" data-tab="env" onclick="switchTab('env', event)">环境变量</button>
  </div>

  <div id="tab-llm" class="tab-content active">
    <div class="card">
      <div class="card-title"><span class="icon">🤖</span> 模型选择</div>
      <div class="presets">
        <div class="presets-label">快速预设（点击自动填充）</div>
        <div class="provider-section">
          <div class="provider-title">🗣️ 国内领先模型（推荐）</div>
          <button class="preset-btn" onclick="setPreset('qwen3.6-plus', 'https://dashscope.aliyuncs.com/compatible-mode/v1', event)">通义千问 Qwen3.6-Plus</button>
          <button class="preset-btn" onclick="setPreset('qwen3-max', 'https://dashscope.aliyuncs.com/compatible-mode/v1', event)">通义千问 Qwen3-Max</button>
          <button class="preset-btn" onclick="setPreset('glm-5.1', 'https://open.bigmodel.cn/api/paas/v4', event)">智谱 GLM-5.1</button>
          <button class="preset-btn" onclick="setPreset('glm-4-flash', 'https://open.bigmodel.cn/api/paas/v4', event)">智谱 GLM-4-Flash（快速）</button>
          <button class="preset-btn" onclick="setPreset('MiniMax-M2.7', 'https://api.minimaxi.com/v1', event)">MiniMax M2.7</button>
          <button class="preset-btn" onclick="setPreset('moonshot-v1-auto', 'https://api.moonshot.cn/v1', event)">Kimi Moonshot</button>
          <button class="preset-btn" onclick="setPreset('hunyuan-turbo-latest', 'https://api.hunyuan.cloud.tencent.com/v1', event)">腾讯混元 Turbo</button>
          <button class="preset-btn" onclick="setPreset('doubao-pro-32k', 'https://ark.cn-beijing.volces.com/api/v3', event)">字节豆包 Pro</button>
          <button class="preset-btn" onclick="setPreset('ernie-4.5-turbo', 'https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat', event)">百度文心 4.5 Turbo</button>
          <button class="preset-btn" onclick="setPreset('yi-lightning', 'https://api.lingyiwanwu.com/v1', event)">零一万物 Yi</button>
          <button class="preset-btn" onclick="setPreset('baichuan4', 'https://api.baichuan-ai.com/v1', event)">百川 Baichuan4</button>
        </div>
        <div class="provider-section">
          <div class="provider-title">🌐 国际模型</div>
          <button class="preset-btn" onclick="setPreset('gpt-4o', 'https://api.openai.com/v1', event)">OpenAI GPT-4o</button>
          <button class="preset-btn" onclick="setPreset('gpt-4o-mini', 'https://api.openai.com/v1', event)">GPT-4o Mini</button>
          <button class="preset-btn" onclick="setPreset('claude-sonnet-4-20250514', 'https://api.anthropic.com/v1', event)">Claude Sonnet 4</button>
        </div>
        <div class="provider-section">
          <div class="provider-title">💻 本地部署</div>
          <button class="preset-btn" onclick="setPreset('qwen2.5', 'http://localhost:11434/v1', event)">Ollama 本地</button>
          <button class="preset-btn" onclick="setPreset('default', 'http://localhost:8000/v1', event)">vLLM 本地</button>
        </div>
      </div>
      <div class="field">
        <label>模型名称 <span class="badge badge-required">必填</span></label>
        <input type="text" id="model" placeholder="qwen3.6-plus / glm-5.1 / MiniMax-M2.7">
        <div class="hint">选择上方预设自动填充，或手动输入模型名称</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="icon">🔗</span> API 配置</div>
      <div class="field">
        <label>API Base URL <span class="badge badge-required">必填</span></label>
        <input type="text" id="baseUrl" placeholder="https://api.openai.com/v1">
        <div class="hint">API 服务地址，必须与模型提供商匹配</div>
      </div>
      <div class="field">
        <label>API Key <span class="badge badge-required">必填</span></label>
        <input type="password" id="apiKey" placeholder="sk-...">
        <div class="hint">你的 API 密钥，保存后不会显示完整内容</div>
      </div>
      <button class="btn btn-ghost test-btn" onclick="testConnection()">测试连接</button>
      <div id="testResult" class="test-result"></div>
    </div>

    <div class="card">
      <div class="card-title"><span class="icon">⚙️</span> 生成参数</div>
      <div class="row">
        <div class="field">
          <label>Max Tokens</label>
          <input type="number" id="maxTokens" min="100" max="128000" step="256" value="4096">
          <div class="hint">单次生成最大 token 数</div>
        </div>
        <div class="field">
          <label>Temperature</label>
          <input type="number" id="temperature" min="0" max="2" step="0.1" value="0.7">
          <div class="hint">0=确定性，2=更随机</div>
        </div>
      </div>
    </div>
  </div>

  <div id="tab-system" class="tab-content">
    <div class="card">
      <div class="card-title"><span class="icon">⚡</span> 网关配置</div>
      <div class="row">
        <div class="field">
          <label>HTTP 端口</label>
          <input type="number" id="httpPort" value="18789" disabled>
          <div class="hint">需要重启服务才能生效</div>
        </div>
        <div class="field">
          <label>gRPC 端口</label>
          <input type="number" id="grpcPort" value="50051" disabled>
          <div class="hint">需要重启服务才能生效</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="icon">🛡️</span> 速率限制</div>
      <div class="row">
        <div class="field">
          <label>时间窗口（秒）</label>
          <input type="number" id="rateWindow" value="900">
        </div>
        <div class="field">
          <label>最大请求数</label>
          <input type="number" id="rateMax" value="100">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="icon">🧠</span> 记忆配置</div>
      <div class="row-3">
        <div class="field">
          <label>热记忆上限</label>
          <input type="number" id="hotLimit" value="2200">
          <div class="hint">字符数</div>
        </div>
        <div class="field">
          <label>温记忆上限</label>
          <input type="number" id="warmLimit" value="50">
          <div class="hint">消息数</div>
        </div>
        <div class="field">
          <label>ReAct 步数</label>
          <input type="number" id="maxSteps" value="10">
          <div class="hint">最大推理步数</div>
        </div>
      </div>
    </div>
  </div>

  <div id="tab-env" class="tab-content">
    <div class="card">
      <div class="card-title"><span class="icon">📋</span> 环境变量（启动时读取）</div>
      <div id="envInfo"></div>
    </div>
    <div class="card">
      <div class="card-title"><span class="icon">💡</span> 配置说明</div>
      <div style="font-size: 13px; line-height: 1.8; color: #888;">
        <p><strong>优先级：</strong>页面设置 > 环境变量 > config/default.yaml</p>
        <p><strong>环境变量：</strong>在终端中设置，重启后生效</p>
        <p><strong>配置文件：</strong>编辑 config/local.yaml（会自动加载）</p>
        <hr class="divider">
        <p><strong>常用环境变量：</strong></p>
        <p><code>LLM_API_KEY</code> - API 密钥</p>
        <p><code>LLM_BASE_URL</code> - API 地址</p>
        <p><code>DEFAULT_MODEL</code> - 默认模型</p>
        <p><code>HTTP_PORT</code> - HTTP 端口</p>
      </div>
    </div>
  </div>

  <div class="actions">
    <button class="btn btn-primary" onclick="saveSettings()">保存配置</button>
    <button class="btn btn-ghost" onclick="loadSettings()">重新加载</button>
    <button class="btn btn-danger" onclick="resetSettings()">恢复默认</button>
  </div>
  <div id="status" class="status" style="display:none;"></div>
</div>

<script>
let current = {};

function switchTab(name, evt) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  var tab = document.querySelector('[data-tab="'+name+'"]');
  if (tab) tab.classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}

function setPreset(model, baseUrl, evt) {
  document.getElementById('model').value = model;
  document.getElementById('baseUrl').value = baseUrl;
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  if (evt && evt.target) evt.target.classList.add('active');
}

async function loadSettings() {
  try {
    const res = await fetch('/settings');
    const data = await res.json();
    if (!data.ok) { showStatus('加载失败', 'error'); return; }
    const cfg = data.data;
    current = cfg.config;
    document.getElementById('model').value = cfg.config.model || '';
    document.getElementById('baseUrl').value = cfg.config.baseUrl || '';
    document.getElementById('apiKey').value = '';
    document.getElementById('maxTokens').value = cfg.config.maxTokens || 4096;
    document.getElementById('temperature').value = cfg.config.temperature || 0.7;

    const env = data.data.envDefaults;
    const envInfo = document.getElementById('envInfo');
    if (envInfo) {
      envInfo.innerHTML = '';
      Object.entries(env).forEach(([k, v]) => {
        const row = document.createElement('div');
        row.className = 'env-row';
        const keySpan = document.createElement('span');
        keySpan.className = 'env-key';
        keySpan.textContent = k;
        const valSpan = document.createElement('span');
        valSpan.className = 'env-val' + (v === '未设置' ? ' empty' : '');
        valSpan.textContent = v as string;
        row.appendChild(keySpan);
        row.appendChild(valSpan);
        envInfo.appendChild(row);
      });
    }
  } catch(e) {
    showStatus('加载失败: ' + e.message, 'error');
  }
}

async function saveSettings() {
  const payload = {
    model: document.getElementById('model').value,
    baseUrl: document.getElementById('baseUrl').value,
    apiKey: document.getElementById('apiKey').value,
    maxTokens: parseInt(document.getElementById('maxTokens').value),
    temperature: parseFloat(document.getElementById('temperature').value),
  };
  if (!payload.apiKey) delete payload.apiKey;
  if (!payload.model) delete payload.model;
  if (!payload.baseUrl) delete payload.baseUrl;

  if (!payload.model && !current.model) {
    showStatus('请填写模型名称', 'error');
    return;
  }
  if (!payload.baseUrl && !current.baseUrl) {
    showStatus('请填写 API Base URL', 'error');
    return;
  }

  try {
    const res = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      showStatus('✓ ' + data.data.message, 'success');
      loadSettings();
    } else {
      showStatus('✗ ' + (data.error || '保存失败'), 'error');
    }
  } catch(e) {
    showStatus('保存失败: ' + e.message, 'error');
  }
}

async function resetSettings() {
  if (!confirm('确定要恢复默认配置吗？')) return;
  try {
    await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: '', baseUrl: '', apiKey: '', maxTokens: 4096, temperature: 0.7 }),
    });
    loadSettings();
    showStatus('✓ 已恢复默认配置', 'success');
  } catch(e) {
    showStatus('恢复失败: ' + e.message, 'error');
  }
}

async function testConnection() {
  const resultEl = document.getElementById('testResult');
  resultEl.className = 'test-result';
  resultEl.style.display = 'block';
  resultEl.textContent = '测试中...';
  
  const apiKey = document.getElementById('apiKey').value || current.apiKey;
  const baseUrl = document.getElementById('baseUrl').value || current.baseUrl;
  const model = document.getElementById('model').value || current.model;

  if (!apiKey || !baseUrl) {
    resultEl.className = 'test-result error';
    resultEl.textContent = '请先填写 API Key 和 Base URL';
    return;
  }

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey,
        'X-Base-Url': baseUrl,
      },
      body: JSON.stringify({ message: 'Hi', model: model }),
    });
    const data = await res.json();
    if (data.ok) {
      resultEl.className = 'test-result success';
      resultEl.textContent = '✓ 连接成功！模型响应正常';
    } else {
      resultEl.className = 'test-result error';
      resultEl.textContent = '✗ 连接失败: ' + (data.error || '未知错误');
    }
  } catch(e) {
    resultEl.className = 'test-result error';
    resultEl.textContent = '✗ 连接失败: ' + e.message;
  }
}

function showStatus(msg, type) {
  const el = document.getElementById('status');
  el.style.display = 'block';
  el.className = 'status ' + type;
  el.textContent = msg;
  setTimeout(() => { el.style.display = 'none'; }, 5000);
}

loadSettings();
</script>
</body>
</html>`;
    res.type("html").send(html);
  })
);

/** GET /chat/ui - 独立对话界面 */
app.get(
  "/chat/ui",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>伏羲</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f0f14; color: #e0e0e8; min-height: 100vh; display: flex; flex-direction: column; }
  .header { background: #1a1a24; border-bottom: 1px solid #2a2a3a; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
  .header h1 { font-size: 20px; font-weight: 600; color: #fff; }
  .header-right { display: flex; gap: 12px; align-items: center; }
  .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; transition: all 0.2s; }
  .btn-primary { background: #6b6bff; color: #fff; }
  .btn-ghost { background: transparent; color: #888; border: 1px solid #2a2a3a; }
  .btn-ghost:hover { border-color: #444; color: #b0b0c0; }
  .chat-container { flex: 1; display: flex; flex-direction: column; max-width: 900px; margin: 0 auto; width: 100%; padding: 20px; }
  .messages { flex: 1; overflow-y: auto; padding: 20px 0; display: flex; flex-direction: column; gap: 16px; }
  .message { padding: 16px 20px; border-radius: 12px; max-width: 80%; word-wrap: break-word; }
  .message.user { background: #6b6bff20; border: 1px solid #6b6bff40; align-self: flex-end; }
  .message.assistant { background: #1a1a24; border: 1px solid #2a2a3a; align-self: flex-start; }
  .message .role { font-size: 12px; color: #888; margin-bottom: 8px; font-weight: 500; }
  .message .content { font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
  .message.thinking .content { color: #888; font-style: italic; }
  .thinking-indicator { display: flex; align-items: center; gap: 8px; color: #888; font-size: 13px; padding: 8px 0; }
  .thinking-dots { display: flex; gap: 4px; }
  .thinking-dots span { width: 6px; height: 6px; background: #888; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out; }
  .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
  .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
  @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }
  .input-area { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 12px; padding: 16px; display: flex; gap: 12px; align-items: flex-end; }
  .input-area textarea { flex: 1; background: transparent; border: none; color: #fff; font-size: 14px; resize: none; outline: none; min-height: 24px; max-height: 120px; font-family: inherit; line-height: 1.5; }
  .input-area textarea::placeholder { color: #555; }
  .input-area .send-btn { padding: 8px 20px; background: #6b6bff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: background 0.2s; align-self: flex-end; min-width: 52px; }
  .input-area .send-btn:hover { background: #5a5aee; }
  .input-area .send-btn:disabled { background: #333; color: #666; cursor: not-allowed; }
  .input-area .stop-btn { padding: 8px 16px; background: #ef4444; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 13px; transition: background 0.2s; align-self: flex-end; }
  .input-area .stop-btn:hover { background: #dc2626; }
  .stream-content { white-space: pre-wrap; }
  .config-bar { background: #1a1a24; border-bottom: 1px solid #2a2a3a; padding: 12px 24px; display: flex; gap: 16px; align-items: center; font-size: 13px; }
  .config-bar span { color: #888; }
  .config-bar .value { color: #6b6bff; }
  .status { font-size: 12px; color: #888; padding: 8px 0; text-align: center; }
  .status.error { color: #ef4444; }
  .toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: #1a1a24; border: 1px solid #2a2a3a; color: #e0e0e8; padding: 8px 16px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
  <div class="header">
    <h1>伏羲</h1>
    <div class="header-right">
      <button class="btn btn-ghost" onclick="clearChat()" id="clearBtn">清空对话</button>
      <button class="btn btn-danger" id="stopBtn" style="display:none" onclick="stopGeneration()">■ 停止</button>
      <button class="btn btn-ghost" onclick="location.href='/settings/ui'">设置</button>
    </div>
  </div>

<div class="config-bar">
  <span>模型: <span class="value" id="modelName">-</span></span>
  <span>状态: <span class="value" id="status">就绪</span></span>
</div>

<div class="chat-container">
  <div class="messages" id="messages"></div>
  <div class="input-area">
    <textarea id="messageInput" placeholder="输入消息... (Shift+Enter 换行，Enter 发送)" rows="1"></textarea>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()">发送</button>
  </div>
  <div class="toast" id="toast"></div>
</div>

<script>
let sessionId = 'chat-' + Math.random().toString(36).substr(2, 9);
let isStreaming = false;
let abortController = null;

const messagesDiv = document.getElementById('messages');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const stopBtn = document.getElementById('stopBtn');
const clearBtn = document.getElementById('clearBtn');
const statusEl = document.getElementById('status');
const modelNameEl = document.getElementById('modelName');
const toastEl = document.getElementById('toast');

/**
 * 安全转义 HTML，防止 XSS
 */
function escapeHtml(text) {
  const el = document.createElement('div');
  el.textContent = text;
  return el.innerHTML;
}

/**
 * 格式化时间
 */
function formatTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

/**
 * 添加消息到对话区
 */
function addMessage(role, content, isThinking = false) {
  const div = document.createElement('div');
  div.className = 'message ' + role + (isThinking ? ' thinking' : '');
  const roleName = role === 'user' ? '你' : '伏羲';
  const timestamp = formatTime();
  div.innerHTML = '<div class="role">' + roleName + ' · ' + timestamp + '</div><div class="content">' + escapeHtml(content || '') + '</div>';
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  return div;
}

/**
 * 添加思考中动画（流式消息占位）
 */
function addThinkingMessage() {
  const div = document.createElement('div');
  div.className = 'message assistant thinking';
  div.innerHTML = '<div class="role">伏羲 · ' + formatTime() + '</div>' +
    '<div class="content"><div class="thinking-indicator">' +
    '<span>思考中</span>' +
    '<div class="thinking-dots"><span></span><span></span><span></span></div>' +
    '</div></div>';
  messagesDiv.appendChild(div);
  messagesDiv.scrollTop = messagesDiv.scrollHeight;
  return div;
}

/**
 * 更新状态栏
 */
function updateStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.className = isError ? 'status error' : 'status';
}

/**
 * 设置流式状态
 */
/**
 * Toast 通知
 */
function showToast(msg, duration = 3000) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastEl._hideTimer);
  toastEl._hideTimer = setTimeout(() => {
    toastEl.classList.remove('show');
  }, duration);
}

/**
 * 设置流式状态
 */
function setStreaming(streaming) {
  isStreaming = streaming;
  sendBtn.disabled = streaming;
  messageInput.disabled = streaming;
  sendBtn.textContent = streaming ? '…' : '发送';
  stopBtn.style.display = streaming ? 'inline-block' : 'none';
  clearBtn.disabled = streaming;
  if (!streaming) {
    messageInput.focus();
  }
}

/**
 * 加载当前配置
 */
async function loadSettings() {
  try {
    const res = await fetch('/settings');
    const data = await res.json();
    if (data.ok) {
      const model = data.data.config.model || '未知';
      modelNameEl.textContent = model;
    }
  } catch(e) { /* 静默失败，不影响主要功能 */ }
}
loadSettings();

/**
 * SSE 流式解析器 - 正确处理跨 chunk 分割的 SSE 事件
 */
async function parseSSEStream(reader, onData, onDone, onError) {
  const decoder = new TextDecoder();
  let buffer = '';
  let fullContent = '';
  let completed = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // 解码累积到 buffer
      buffer += decoder.decode(value, { stream: true });

      // 按行分割处理
      const lines = buffer.split('\n');
      // 保留最后可能不完整的一行在 buffer 中
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed === '') continue; // 空行
        if (trimmed === 'data: [DONE]') {
          completed = true;
          break;
        }
        if (trimmed.startsWith('data: ')) {
          try {
            const data = JSON.parse(trimmed.slice(6));
            if (data.error) {
              onError(data.error);
              completed = true;
              break;
            }
            if (data.is_final) {
              completed = true;
            }
            if (data.content) {
              fullContent += data.content;
              onData(fullContent, data, onDone);
            } else if (data.is_final && !data.content) {
              // is_final 但没有内容，仍然标记完成
              completed = true;
            }
          } catch (e) {
            // JSON 解析失败，可能是不完整行，继续等待
          }
        }
      }
      if (completed) break;
    }
  } catch (e) {
    onError(e.message);
    return;
  }

  // 最终检查
  if (!completed && fullContent) {
    completed = true;
  }

  onDone(fullContent, completed);
}

/**
 * 发送消息
 */
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || isStreaming) return;

  // 清空输入框并添加用户消息
  messageInput.value = '';
  messageInput.style.height = 'auto';
  addMessage('user', text);
  setStreaming(true);
  updateStatus('生成中...');

  // 创建 AbortController 用于取消请求
  abortController = new AbortController();

  // 创建助手消息占位
  const assistantDiv = addThinkingMessage();

  try {
    const res = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
      signal: abortController.signal,
    });

    if (!res.ok) {
      throw new Error('请求失败 (' + res.status + ')');
    }

    const reader = res.body.getReader();
    let lastContent = '';

    await parseSSEStream(
      reader,
      // onData - 更新流式内容
      (fullContent, data) => {
        lastContent = fullContent;
        // 更新消息内容（安全使用 textContent）
        const contentDiv = assistantDiv.querySelector('.content');
        if (contentDiv) {
          contentDiv.textContent = fullContent;
          // 移除思考中样式
          assistantDiv.classList.remove('thinking');
        }
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
      },
      // onDone - 流结束
      (fullContent, completed) => {
        assistantDiv.classList.remove('thinking');
        if (!fullContent && !completed) {
          const contentDiv = assistantDiv.querySelector('.content');
          if (contentDiv) {
            contentDiv.textContent = '（无响应内容）';
          }
        }
        updateStatus(completed ? '就绪' : '响应未完成');
      },
      // onError - 出错了
      (errorMsg) => {
        assistantDiv.classList.remove('thinking');
        const contentDiv = assistantDiv.querySelector('.content');
        if (contentDiv) {
          contentDiv.textContent = '错误: ' + errorMsg;
        }
        updateStatus('请求失败: ' + errorMsg, true);
      }
    );
  } catch (e) {
    if (e.name === 'AbortError') {
      updateStatus('已取消');
    } else {
      assistantDiv.classList.remove('thinking');
      const contentDiv = assistantDiv.querySelector('.content');
      if (contentDiv) {
        contentDiv.textContent = '连接失败: ' + e.message;
      }
      updateStatus('连接失败: ' + e.message, true);
    }
  } finally {
    setStreaming(false);
    abortController = null;
  }
}

/**
 * 清空对话
 */
function clearChat() {
  if (isStreaming) {
    if (abortController) {
      abortController.abort();
    }
  }
  messagesDiv.innerHTML = '';
  sessionId = 'chat-' + Math.random().toString(36).substr(2, 9);
  updateStatus('新会话已创建');
  messageInput.focus();
}

/**
 * 停止生成
 */
function stopGeneration() {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  setStreaming(false);
  updateStatus('已停止');
}

// 键盘事件
messageInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
  // Escape 取消当前请求
  if (e.key === 'Escape' && isStreaming) {
    e.preventDefault();
    stopGeneration();
  }
});

// 自动增长输入框
messageInput.addEventListener('input', () => {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
});

// 初始化焦点
messageInput.focus();
</script>
</body>
</html>`;
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
    server.listen(port, () => {
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
