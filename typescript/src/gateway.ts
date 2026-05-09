/** Express 网关 - HTTP → gRPC 转发层 */
import express from "express";
import bodyParser from "body-parser";
import cors from "cors";
import { randomUUID } from "crypto";
import { config } from "./config";
import { grpcClient, memoryClient } from "./grpc_client";
import * as grpc from "@grpc/grpc-js";
import { RateLimiterMemory } from "rate-limiter-flexible";
import winston from "winston";

// 扩展 Express Request 类型
declare global {
  namespace Express {
    interface Request {
      traceId?: string;
    }
  }
}

const app = express();
app.use(cors());
app.use(bodyParser.json({ limit: "10mb" }));

// 日志配置
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.json()
  ),
  transports: [
    new winston.transports.Console(),
  ],
});

// 请求追踪 ID 中间件
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  const traceId = req.headers['x-trace-id'] as string || randomUUID();
  req.traceId = traceId;
  res.setHeader('x-trace-id', traceId);
  next();
});

// 日志中间件
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  const start = Date.now();
  res.on("finish", () => {
    logger.info({
      message: `${req.method} ${req.path} ${res.statusCode} ${Date.now() - start}ms`,
      traceId: req.traceId,
      method: req.method,
      path: req.path,
      statusCode: res.statusCode,
      duration: Date.now() - start,
      ip: req.ip,
    });
  });
  next();
});

// 速率限制中间件
const rateLimiter = new RateLimiterMemory({
  points: config.rateLimit.max,
  duration: config.rateLimit.windowMs / 1000,
});

app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  const key = req.ip || 'unknown';
  rateLimiter.consume(key)
    .then(() => next())
    .catch((rateLimitInfo: any) => {
      logger.warn(`Rate limit exceeded for IP: ${key}`);
      res.status(429).json({
        ok: false,
        error: `Too many requests. Please try again in ${Math.ceil(rateLimitInfo.msBeforeNext / 1000)} seconds.`,
        timestamp: Date.now(),
      });
    });
});

/** 统一响应包装 */
function wrapResponse(res: express.Response, success: boolean, data?: any, error?: string) {
  return res.json({
    ok: success,
    data: data || null,
    error: error || null,
    timestamp: Date.now(),
  });
}

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

// ===================== 路由 =====================

/** POST /chat - 聊天对话 */
app.post(
  "/chat",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { message, model, session_id = "default" } = req.body;
    if (!message) {
      return wrapResponse(res, false, null, "message is required");
    }

    const userConfig = extractUserConfig(req);
    const metadata = new grpc.Metadata();

    // 优先级：请求头 > runtimeConfig(/settings) > 环境变量
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

    // 设置超时
    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 30);

    // 模型优先级：请求body > runtimeConfig > config
    const resolvedModel = model || runtimeConfig.model || config.auth.model || "";
    if (resolvedModel) {
      metadata.add('model', resolvedModel);
    }

    const call = grpcClient.streamComplete({
      user_message: message,
      model: resolvedModel,
      session_id,
      max_tokens: runtimeConfig.maxTokens,
    }, metadata, { deadline: deadline });

    let fullContent = "";
    let isFinal = false;
    let responded = false;

    call.on("data", (chunk: any) => {
      fullContent += chunk.content;
      if (chunk.is_final) isFinal = true;
    });

    call.on("end", () => {
      if (!responded) {
        responded = true;
        wrapResponse(res, true, {
          content: fullContent,
          model: resolvedModel,
          session_id,
        });
      }
    });

    call.on("error", (err: any) => {
      logger.error("StreamComplete error:", err);
      if (!responded) {
        responded = true;
        wrapResponse(res, false, null, err.message || "Stream error");
      }
    });
  })
);

/** POST /chat/stream - 流式聊天对话（SSE） */
app.post(
  "/chat/stream",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const { message, model, session_id = "default" } = req.body;
    if (!message) {
      return wrapResponse(res, false, null, "message is required");
    }

    const userConfig = extractUserConfig(req);
    const metadata = new grpc.Metadata();

    // 优先级：请求头 > runtimeConfig(/settings) > 环境变量
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

    // 设置超时
    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 60);

    // 模型优先级：请求body > runtimeConfig > config
    const resolvedModel = model || runtimeConfig.model || config.auth.model || "";
    if (resolvedModel) {
      metadata.add('model', resolvedModel);
    }

    // 设置 SSE 响应头
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    });

    const call = grpcClient.streamComplete({
      user_message: message,
      model: resolvedModel,
      session_id,
      max_tokens: runtimeConfig.maxTokens,
    }, metadata, { deadline: deadline });

    call.on("data", (chunk: any) => {
      // 发送 SSE 事件
      const data = JSON.stringify({
        content: chunk.content,
        is_final: chunk.is_final,
        reasoning: chunk.reasoning || "",
      });
      res.write(`data: ${data}\n\n`);
    });

    call.on("end", () => {
      // 发送完成事件
      res.write('data: [DONE]\n\n');
      res.end();
    });

    call.on("error", (err: any) => {
      logger.error("StreamComplete SSE error:", err);
      const errorData = JSON.stringify({
        error: err.message || "Stream error",
      });
      res.write(`data: ${errorData}\n\n`);
      res.end();
    });

    // 处理客户端断开连接
    req.on('close', () => {
      call.cancel();
    });
  })
);

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

    grpcClient.invokeTool({
      tool_name,
      arguments_json: JSON.stringify(args || {}),
      session_id,
      model: resolvedToolModel,
    }, metadata, { deadline: deadline }, (error: any, response: any) => {
      if (error) {
        logger.error("InvokeTool error:", error);
        return wrapResponse(res, false, null, error.message);
      }
      let result = {};
      try {
        result = response.result_json ? JSON.parse(response.result_json) : {};
      } catch (e) {
        result = { raw: response.result_json };
      }
      wrapResponse(res, response.success, { result, elapsed_ms: Number(response.elapsed_ms) }, response.error);
    });
  })
);

/** GET /tool/list - 列出所有可用工具（从 gRPC 获取） */
app.get(
  "/tool/list",
  catchAsync(async (req: express.Request, res: express.Response) => {
    // 工具列表从 Python registry 动态获取
    // 作为简化，先返回已知的工具列表（后续可添加 ListTools RPC）
    const tools = [
      { name: "read_file", level: "L0", desc: "读取文件内容" },
      { name: "write_file", level: "L1", desc: "写入文件内容" },
      { name: "list_files", level: "L0", desc: "列出目录下文件" },
      { name: "file_exists", level: "L0", desc: "检查文件是否存在" },
      { name: "read_json", level: "L0", desc: "读取 JSON 文件" },
      { name: "write_json", level: "L1", desc: "写入 JSON 文件" },
      { name: "memory_write", level: "L1", desc: "写入记忆（hot/warm/cold）" },
      { name: "memory_query", level: "L0", desc: "查询记忆" },
      { name: "memory_get_recent", level: "L0", desc: "获取最近记忆" },
    ];
    wrapResponse(res, true, { tools });
  })
);

/** GET /memory/hot - 查询热记忆 */
app.get(
  "/memory/hot",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const session_id = (req.query.session_id as string) || "default";

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 5);

    memoryClient.queryHot({ session_id }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryHot error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, true, {
        content: response.memory_content,
        char_count: response.char_count,
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

    memoryClient.persistMemory({
      memory_type: "hot",
      content,
      session_id,
      metadata: {},
    }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory hot error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, response.success, { id: response.id }, response.error);
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

    memoryClient.persistMemory({
      memory_type: "warm",
      content,
      session_id,
      metadata: {},
    }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory warm error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, response.success, { id: response.id }, response.error);
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

    memoryClient.queryWarm({ session_id, limit }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryWarm error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, true, {
        entries: response.entries || [],
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

    memoryClient.queryWarm({ session_id, query, limit }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryWarm search error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, true, {
        entries: response.entries || [],
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

    memoryClient.persistMemory({
      memory_type: "cold",
      content,
      summary,
      session_id,
      metadata,
    }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("PersistMemory cold error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, response.success, { id: response.id }, response.error);
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

    memoryClient.queryCold({ session_id, limit }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryCold error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, true, {
        memories: response.memories || [],
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

    memoryClient.queryCold({ query, limit, session_id }, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        logger.error("QueryCold search error:", err);
        return wrapResponse(res, false, null, err.message);
      }
      wrapResponse(res, true, {
        memories: response.memories || [],
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
  model: process.env.DEFAULT_MODEL || process.env.DEEPSEEK_MODEL || "",
  apiKey: process.env.DEEPSEEK_API_KEY || "",
  baseUrl: process.env.DEEPSEEK_BASE_URL || "",
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
        DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY ? "已设置" : "未设置",
        DEEPSEEK_BASE_URL: process.env.DEEPSEEK_BASE_URL || "未设置",
        DEFAULT_MODEL: process.env.DEFAULT_MODEL || process.env.DEEPSEEK_MODEL || "未设置",
      },
    });
  })
);

/** POST /settings - 更新配置（需要认证） */
app.post(
  "/settings",
  catchAsync(async (req: express.Request, res: express.Response) => {
    // 验证认证
    const authHeader = req.headers['authorization'] as string;
    const settingsApiKey = process.env.SETTINGS_API_KEY || config.auth.apiKey;
    
    if (settingsApiKey) {
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

    wrapResponse(res, true, {
      message: "配置已更新",
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
  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 12px 24px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; }
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
<div class="container">
  <h1>伏羲设置</h1>
  <p class="subtitle">配置 LLM API 和系统参数，所有更改立即生效</p>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('llm')">LLM 配置</button>
    <button class="tab" onclick="switchTab('system')">系统配置</button>
    <button class="tab" onclick="switchTab('env')">环境变量</button>
  </div>

  <div id="tab-llm" class="tab-content active">
    <div class="card">
      <div class="card-title"><span class="icon">🤖</span> 模型选择</div>
      <div class="presets">
        <div class="presets-label">快速预设（点击自动填充）</div>
        <div class="provider-section">
          <div class="provider-title">国外模型</div>
          <button class="preset-btn" onclick="setPreset('gpt-4o', 'https://api.openai.com/v1')">OpenAI GPT-4o</button>
          <button class="preset-btn" onclick="setPreset('gpt-4o-mini', 'https://api.openai.com/v1')">GPT-4o Mini</button>
          <button class="preset-btn" onclick="setPreset('claude-3-5-sonnet-20241022', 'https://api.anthropic.com/v1')">Claude 3.5</button>
        </div>
        <div class="provider-section">
          <div class="provider-title">国内模型</div>
          <button class="preset-btn" onclick="setPreset('deepseek-chat', 'https://api.deepseek.com/v1')">DeepSeek Chat</button>
          <button class="preset-btn" onclick="setPreset('deepseek-coder', 'https://api.deepseek.com/v1')">DeepSeek Coder</button>
          <button class="preset-btn" onclick="setPreset('qwen-turbo', 'https://dashscope.aliyuncs.com/compatible-mode/v1')">通义千问</button>
          <button class="preset-btn" onclick="setPreset('glm-4', 'https://open.bigmodel.cn/api/paas/v4')">智谱 GLM-4</button>
        </div>
        <div class="provider-section">
          <div class="provider-title">本地模型</div>
          <button class="preset-btn" onclick="setPreset('llama3', 'http://localhost:11434/v1')">Ollama 本地</button>
          <button class="preset-btn" onclick="setPreset('default', 'http://localhost:8000/v1')">vLLM 本地</button>
        </div>
      </div>
      <div class="field">
        <label>模型名称 <span class="badge badge-required">必填</span></label>
        <input type="text" id="model" placeholder="deepseek-chat / gpt-4o / claude-3-5-sonnet">
        <div class="hint">选择上方预设自动填充，或手动输入模型名称</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="icon">🔗</span> API 配置</div>
      <div class="field">
        <label>API Base URL <span class="badge badge-required">必填</span></label>
        <input type="text" id="baseUrl" placeholder="https://api.deepseek.com/v1">
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
        <p><code>DEEPSEEK_API_KEY</code> - API 密钥</p>
        <p><code>DEEPSEEK_BASE_URL</code> - API 地址</p>
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

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector('[onclick="switchTab(\\''+name+'\\')"]').classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}

function setPreset(model, baseUrl) {
  document.getElementById('model').value = model;
  document.getElementById('baseUrl').value = baseUrl;
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
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
    document.getElementById('envInfo').innerHTML = Object.entries(env).map(([k,v]) =>
      '<div class="env-row"><span class="env-key">'+k+'</span><span class="env-val '+(v==='未设置'?'empty':'')+'">'+v+'</span></div>'
    ).join('');
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

/** GET /health - 健康检查 */
app.get(
  "/health",
  catchAsync(async (req: express.Request, res: express.Response) => {
    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 3);

    grpcClient.heartbeat({}, new grpc.Metadata(), { deadline: deadline }, (err: any, response: any) => {
      if (err) {
        return res.status(503).json({
          ok: false,
          error: "gRPC service unavailable",
          timestamp: Date.now(),
        });
      }
      res.json({
        ok: true,
        alive: response.alive,
        timestamp: response.timestamp,
        grpcHost: `${config.grpcHost}:${config.grpcPort}`,
      });
    });
  })
);

// 监控指标
const metrics = {
  requests: {
    total: 0,
    success: 0,
    error: 0,
    byPath: {} as Record<string, number>,
  },
  latency: {
    total: 0,
    count: 0,
    avg: 0,
  },
  uptime: Date.now(),
};

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

/** GET /metrics - 监控指标 */
app.get(
  "/metrics",
  catchAsync(async (req: express.Request, res: express.Response) => {
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
  })
);

/** 错误处理中间件 */
app.use((err: any, req: express.Request, res: express.Response, next: express.NextFunction) => {
  logger.error("Unhandled error:", err);
  const statusCode = err.statusCode || 500;
  res.status(statusCode).json({
    ok: false,
    data: null,
    error: err.message || "Internal server error",
    timestamp: Date.now(),
  });
});

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
        console.log(`  Fuxi Gateway started (HTTPS)`);
        console.log(`  HTTPS port: ${port}`);
        console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
        console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
        console.log(`  Auth enabled: ${config.auth.enabled}`);
        console.log(`================================================\n`);
      });
    } catch (e) {
      console.error('Failed to start HTTPS server:', e);
      console.log('Falling back to HTTP...');
      server = app.listen(port, () => {
        console.log(`\n================================================`);
        console.log(`  Fuxi Gateway started (HTTP fallback)`);
        console.log(`  HTTP port: ${port}`);
        console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
        console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
        console.log(`  Auth enabled: ${config.auth.enabled}`);
        console.log(`================================================\n`);
      });
    }
  } else {
    // HTTP 模式
    server = app.listen(port, () => {
      console.log(`\n================================================`);
      console.log(`  Fuxi Gateway started`);
      console.log(`  HTTP port: ${port}`);
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

  return server;
}

// 直接启动
if (require.main === module) {
  const { loadEnv } = require("./config");
  loadEnv();
  startServer(config.httpPort);
}

export default app;