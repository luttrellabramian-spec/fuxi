/** v0.2.6 (H1) — UI / settings / health / metrics 路由
 */
import express from "express";
import * as grpc from "@grpc/grpc-js";
import { catchAsync, wrapResponse } from "../middleware/asyncHandler";
import { readUiTemplate, metrics } from "../helpers";
import { RouteContext } from "../types";

export function registerUiRoutes(app: express.Express, ctx: RouteContext): void {
  const { runtimeConfig, grpcClient, fuxiProto, config } = ctx;

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
        if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true });
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
      } catch (e) {
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

  /** GET /settings/ui - 设置页面 */
  app.get(
    "/settings/ui",
    catchAsync(async (req: express.Request, res: express.Response) => {
      res.type("html").send(readUiTemplate("settings.html"));
    })
  );

  /** GET /chat/ui - 对话页面 */
  app.get(
    "/chat/ui",
    catchAsync(async (req: express.Request, res: express.Response) => {
      res.type("html").send(readUiTemplate("chat.html"));
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
      grpcClient.heartbeat(pingMsg, new grpc.Metadata(), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          return res.status(503).json({
            ok: false,
            error: "gRPC service unavailable",
            timestamp: Date.now(),
          });
        }
        const r = response as { getAlive?: () => boolean; getTimestamp?: () => number; alive?: boolean; timestamp?: number };
        const alive = r.getAlive ? r.getAlive() : r.alive;
        const ts = r.getTimestamp ? r.getTimestamp() : r.timestamp;
        res.json({
          ok: true,
          alive: alive,
          timestamp: ts,
          grpcHost: `${config.grpcHost}:${config.grpcPort}`,
        });
      });
    })
  );

  /** GET /metrics - Prometheus 指标 */
  app.get(
    "/metrics",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const accept = req.headers.accept || "";
      const wantsPrometheus = accept.includes("text/plain") || accept.includes("prometheus");

      if (wantsPrometheus || req.query.format === "prometheus") {
        const uptimeSeconds = Math.floor((Date.now() - metrics.uptime) / 1000);
        const mem = process.memoryUsage();
        const memRss = Math.round(mem.rss / 1024 / 1024);
        const memHeapUsed = Math.round(mem.heapUsed / 1024 / 1024);

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
        res.json({
          ok: true,
          data: {
            uptime: Math.floor((Date.now() - metrics.uptime) / 1000),
            requests: metrics.requests,
            latency: { avg: Math.round(metrics.latency.avg), count: metrics.latency.count },
            memory: process.memoryUsage(),
            timestamp: Date.now(),
          },
        });
      }
    })
  );
}
