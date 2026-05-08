/** Express 网关 - HTTP → gRPC 转发层 */
import express from "express";
import bodyParser from "body-parser";
import cors from "cors";
import { config } from "./config";
import { grpcClient, memoryClient, fuxiProto } from "./grpc_client";
import * as grpc from "@grpc/grpc-js";
import { RateLimiterMemory } from "rate-limiter-flexible";
import winston from "winston";

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

// 日志中间件
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  const start = Date.now();
  res.on("finish", () => {
    logger.info(`${req.method} ${req.path} ${res.statusCode} ${Date.now() - start}ms`);
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
        error: `Too many requests. Please try again in ${Math.ceil((rateLimitInfo.msBeforeNext || 0) / 1000)} seconds.`,
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
    const { message, model, session_id = "default", history } = req.body;
    if (!message) {
      return wrapResponse(res, false, null, "message is required");
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
    if (history && Array.isArray(history) && history.length > 0) {
      metadata.add('history', JSON.stringify(history));
    }

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 30);

    const resolvedModel = model || runtimeConfig.model;
    const completionRequest = new fuxiProto.CompletionRequest();
    completionRequest.setUserMessage(message);
    completionRequest.setModel(resolvedModel);
    completionRequest.setSessionId(session_id);
    const call = grpcClient.streamComplete(completionRequest, metadata, { deadline });

    let fullContent = "";
    let isFinal = false;
    let responded = false;

    const sendResponse = (success: boolean, data?: any, error?: string) => {
      if (responded) return;
      responded = true;
      call.removeAllListeners();
      call.cancel();
      wrapResponse(res, success, data, error);
    };

    call.on("data", (chunk: any) => {
      fullContent += chunk.getContent ? chunk.getContent() : chunk.content;
      if (chunk.getIsFinal) isFinal = chunk.getIsFinal();
      else if (chunk.is_final) isFinal = chunk.is_final;
    });

    call.on("end", () => {
      sendResponse(true, {
        content: fullContent,
        model: resolvedModel,
        session_id,
      });
    });

    call.on("error", (err: any) => {
      logger.error("StreamComplete error:", err);
      sendResponse(false, null, err.message || "Stream error");
    });

    req.on("close", () => {
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

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 15);

    let argsJson: string;
    try {
      argsJson = JSON.stringify(args || {});
    } catch (e) {
      argsJson = "{}";
    }

    const toolRequest = new fuxiProto.ToolRequest();
    toolRequest.setToolName(tool_name);
    toolRequest.setArgumentsJson(argsJson);
    toolRequest.setSessionId(session_id);
    grpcClient.invokeTool(toolRequest, metadata, { deadline }, (error: any, response: any) => {
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

    memoryClient.QueryHot({ session_id }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.PersistMemory({
      memory_type: "hot",
      content,
      session_id,
      metadata: {},
    }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.PersistMemory({
      memory_type: "warm",
      content,
      session_id,
      metadata: {},
    }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.QueryWarm({ session_id, limit }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.QueryWarm({ session_id, query, limit }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.PersistMemory({
      memory_type: "cold",
      content,
      summary,
      session_id,
      metadata,
    }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.QueryCold({ session_id, limit }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

    memoryClient.QueryCold({ query, limit, session_id }, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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
  model: process.env.DEFAULT_MODEL || process.env.DEEPSEEK_MODEL || "MiniMax-M2.7",
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

/** POST /settings - 更新配置 */
app.post(
  "/settings",
  catchAsync(async (req: express.Request, res: express.Response) => {
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
  .container { max-width: 640px; margin: 0 auto; padding: 40px 20px; }
  h1 { font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #fff; }
  .subtitle { color: #888; font-size: 13px; margin-bottom: 32px; }
  .card { background: #1a1a24; border: 1px solid #2a2a3a; border-radius: 12px; padding: 24px; margin-bottom: 20px; }
  .card-title { font-size: 13px; font-weight: 500; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 20px; }
  .field { margin-bottom: 20px; }
  .field:last-child { margin-bottom: 0; }
  label { display: block; font-size: 14px; color: #b0b0c0; margin-bottom: 8px; }
  input[type="text"], input[type="number"], input[type="password"] { width: 100%; padding: 10px 14px; background: #0f0f18; border: 1px solid #2a2a3a; border-radius: 8px; color: #fff; font-size: 14px; outline: none; transition: border-color 0.2s; }
  input:focus { border-color: #6b6bff; }
  input::placeholder { color: #555; }
  .hint { font-size: 11px; color: #666; margin-top: 6px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; }
  .btn-primary { background: #6b6bff; color: #fff; }
  .btn-primary:hover { background: #5a5aee; }
  .btn-ghost { background: transparent; color: #888; border: 1px solid #2a2a3a; }
  .btn-ghost:hover { border-color: #444; color: #b0b0c0; }
  .actions { display: flex; gap: 12px; margin-top: 24px; }
  .status { font-size: 12px; color: #888; margin-top: 16px; text-align: center; }
  .env-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #222; font-size: 13px; }
  .env-row:last-child { border-bottom: none; }
  .env-key { color: #888; }
  .env-val { color: #6b6bff; }
  .env-val.empty { color: #444; }
  .divider { border: none; border-top: 1px solid #2a2a3a; margin: 20px 0; }
  .preset-btn { padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; border: 1px solid #2a2a3a; background: transparent; color: #888; margin-right: 8px; margin-bottom: 8px; transition: all 0.2s; }
  .preset-btn:hover { border-color: #6b6bff; color: #6b6bff; }
  .presets { margin-bottom: 16px; }
  .presets-label { font-size: 12px; color: #666; margin-bottom: 8px; }
</style>
</head>
<body>
<div class="container">
  <h1>⚙️ 伏羲设置</h1>
  <p class="subtitle">实时调整 LLM 配置，无需重启服务</p>

  <div class="card">
    <div class="card-title">模型配置</div>
    <div class="presets">
      <div class="presets-label">快速预设</div>
      <button class="preset-btn" onclick="setPreset('gpt-4o', 'https://api.openai.com/v1', '')">GPT-4o</button>
      <button class="preset-btn" onclick="setPreset('claude-3-5-sonnet-20241022', 'https://api.anthropic.com/v1', '')">Claude</button>
      <button class="preset-btn" onclick="setPreset('deepseek-chat', 'https://api.minimaxi.com/v1', '')">DeepSeek</button>
      <button class="preset-btn" onclick="setPreset('MiniMax-M2.7', 'https://api.minimaxi.com/v1', '')">MiniMax</button>
      <button class="preset-btn" onclick="setPreset('Qwen/Qwen2.5-72B-Instruct', 'https://api.together.xyz/v1', '')">Qwen</button>
    </div>
    <div class="field">
      <label>模型名称</label>
      <input type="text" id="model" placeholder="gpt-4o / claude-3-5-sonnet / deepseek-chat ...">
    </div>
    <div class="field">
      <label>API Base URL</label>
      <input type="text" id="baseUrl" placeholder="https://api.openai.com/v1">
    </div>
    <div class="field">
      <label>API Key</label>
      <input type="password" id="apiKey" placeholder="sk-...">
    </div>
    <hr class="divider">
    <div class="row">
      <div class="field">
        <label>Max Tokens</label>
        <input type="number" id="maxTokens" min="100" max="32000" step="256">
      </div>
      <div class="field">
        <label>Temperature</label>
        <input type="number" id="temperature" min="0" max="2" step="0.1">
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">当前环境变量（启动时读取）</div>
    <div id="envInfo"></div>
  </div>

  <div class="actions">
    <button class="btn btn-primary" onclick="saveSettings()">保存配置</button>
    <button class="btn btn-ghost" onclick="loadSettings()">重置</button>
  </div>
  <div class="status" id="status"></div>
</div>

<script>
let current = {};

function escHtml(s: string): string {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setPreset(model, baseUrl, apiKey) {
  document.getElementById('model').value = escHtml(model);
  document.getElementById('baseUrl').value = escHtml(baseUrl);
  document.getElementById('apiKey').value = apiKey;
}

async function loadSettings() {
  const res = await fetch('/settings');
  const data = await res.json();
  if (!data.ok) { document.getElementById('status').textContent = '加载失败'; return; }
  const cfg = data.data;
  current = cfg.config;
  document.getElementById('model').value = cfg.config.model || '';
  document.getElementById('baseUrl').value = cfg.config.baseUrl || '';
  document.getElementById('apiKey').value = '';
  document.getElementById('maxTokens').value = cfg.config.maxTokens || 2048;
  document.getElementById('temperature').value = cfg.config.temperature || 0.7;

  const env = data.data.envDefaults;
  document.getElementById('envInfo').innerHTML = Object.entries(env).map(([k,v]) =>
    '<div class="env-row"><span class="env-key">'+escHtml(k)+'</span><span class="env-val '+(v==='未设置'?'empty':'')+'">'+escHtml(v)+'</span></div>'
  ).join('');
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

  const res = await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  const el = document.getElementById('status');
  if (data.ok) {
    el.textContent = '✓ ' + data.data.message + ' — 下次请求生效';
    el.style.color = '#4caf50';
  } else {
    el.textContent = '✗ ' + (data.error || '保存失败');
    el.style.color = '#f44336';
  }
  setTimeout(() => { el.textContent = ''; }, 3000);
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

    const ping = new fuxiProto.SessionPing();
    grpcClient.heartbeat(ping, new grpc.Metadata(), { deadline }, (err: any, response: any) => {
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

/** 错误处理中间件 */
app.use((err: any, req: express.Request, res: express.Response, next: express.NextFunction) => {
  logger.error("Unhandled error:", err);
  wrapResponse(res, false, null, err.message || "Internal server error");
});

/** 启动服务器 */
export function startServer(port: number = config.httpPort) {
  const server = app.listen(port, () => {
    console.log(`\n================================================`);
    console.log(`  Fuxi Gateway started`);
    console.log(`  HTTP port: ${port}`);
    console.log(`  gRPC target: ${config.grpcHost}:${config.grpcPort}`);
    console.log(`  Rate limit: ${config.rateLimit.max} req / ${config.rateLimit.windowMs}ms`);
    console.log(`  Auth enabled: ${config.auth.enabled}`);
    console.log(`================================================\n`);
  });

  // 优雅关闭
  process.on('SIGTERM', () => {
    console.log('SIGTERM received, shutting down gracefully...');
    server.close(() => {
      console.log('Server closed.');
      process.exit(0);
    });
  });

  process.on('SIGINT', () => {
    console.log('SIGINT received, shutting down gracefully...');
    server.close(() => {
      console.log('Server closed.');
      process.exit(0);
    });
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