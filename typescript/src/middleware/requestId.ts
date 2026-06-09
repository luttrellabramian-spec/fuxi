/**
 * 请求追踪中间件 - 为每个请求注入 request_id
 *
 * v0.2.0 新增：全链路 request_id 追踪
 * 客户端 → TS 网关 → Python 后端
 */
import { Request, Response, NextFunction } from 'express';
import { randomUUID } from 'crypto';

/**
 * request_id 中间件
 *
 * 从请求头 x-request-id 获取（客户端可自传），若不存在则自动生成 UUID。
 * 响应中也带上 X-Request-ID header，便于客户端关联请求与响应。
 */
export function requestIdMiddleware(req: Request, res: Response, next: NextFunction) {
  const id = (req.headers['x-request-id'] as string) || randomUUID();
  req.headers['x-request-id'] = id;           // 注入供后续中间件/路由使用
  res.setHeader('X-Request-ID', id);           // 响应 header 带回
  next();
}

/**
 * 从请求中获取 request_id（兼容 Express Request 和 Node IncomingMessage）
 */
export function getRequestId(req: Request | { headers: Record<string, string | string[] | undefined> }): string {
  return (req.headers['x-request-id'] as string) || 'unknown';
}
