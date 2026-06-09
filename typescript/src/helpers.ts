/** v0.2.6 (H1) — gateway.ts 拆分出来的共享 helpers
 *
 * 包含：
 * - readUiTemplate: 读 HTML 模板（dev/prod fallback）
 * - extractUserConfig: 提取 Authorization + X-Base-Url 头
 * - buildMetadata: 统一 gRPC metadata 构建（含优先级链）
 * - logger, metrics: 简单的运行时对象
 */
import fs from "fs";
import path from "path";
import http from "http";
import * as grpc from "@grpc/grpc-js";
import express from "express";
import { config } from "./config";

/** runtimeConfig 形状（由 gateway.ts 维护） */
interface RuntimeConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
  maxTokens: number;
  temperature: number;
}

/** 读取 UI 模板（兼容 dev 跑 src 和 prod 跑 dist）
 *  候选路径：
 *    1) __dirname/ui/   (生产：dist/ui/)
 *    2) __dirname/../src/ui/  (开发：src/ 跑)
 *  找不到时返回简单占位符并记录错误。 */
export function readUiTemplate(name: string): string {
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

/** 从请求头提取用户自定义配置 */
export function extractUserConfig(req: express.Request): { apiKey?: string; baseUrl?: string } {
  const authHeader = req.headers['authorization'] as string;
  const baseUrlHeader = req.headers['x-base-url'] as string;
  return {
    apiKey: authHeader?.replace('Bearer ', ''),
    baseUrl: baseUrlHeader,
  };
}

/** 构建 gRPC metadata，统一 Authorization 优先级链（v0.2.6）
 *
 * 优先级：客户端 header > runtimeConfig > config.auth
 * 适用：所有路由（chat/stream/tool/memory）+ WebSocket
 */
export function buildMetadata(
  req: express.Request | http.IncomingMessage,
  runtimeConfig: RuntimeConfig,
  opts: { model?: string; includeAuth?: boolean } = {}
): grpc.Metadata {
  const md = new grpc.Metadata();
  const userConfig = extractUserConfig(req as express.Request);
  const apiKey = userConfig.apiKey || runtimeConfig.apiKey || config.auth.apiKey;
  const baseUrl = userConfig.baseUrl || runtimeConfig.baseUrl || config.auth.baseUrl;
  if (apiKey && opts.includeAuth !== false) {
    md.add('authorization', `Bearer ${apiKey}`);
  }
  if (baseUrl) md.add('base-url', baseUrl);
  if (opts.model) md.add('model', opts.model);
  return md;
}

/** 简单 logger */
export const logger = {
  info: (...args: unknown[]) => console.log('[INFO]', ...args),
  warn: (...args: unknown[]) => console.warn('[WARN]', ...args),
  error: (...args: unknown[]) => console.error('[ERROR]', ...args),
};

/** 监控指标 */
export const metrics = {
  requests: { total: 0, success: 0, error: 0, byPath: {} as Record<string, number> },
  latency: { total: 0, count: 0, avg: 0 },
  uptime: Date.now(),
};
