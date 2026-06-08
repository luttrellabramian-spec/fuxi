/** 异步错误处理中间件 + 统一响应包装 */
import express from "express";

/** 错误捕获包装 — 把 async handler 的 Promise reject 传给 next(err) */
export function catchAsync(
  fn: (req: express.Request, res: express.Response, next: express.NextFunction) => Promise<any>
) {
  return (req: express.Request, res: express.Response, next: express.NextFunction) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

/** 统一响应包装（v0.2.0: 增加 request_id） */
export function wrapResponse(
  res: express.Response,
  success: boolean,
  data?: any,
  error?: string
) {
  return res.json({
    ok: success,
    data: data || null,
    error: error || null,
    timestamp: Date.now(),
    request_id: res.req?.headers['x-request-id'] || '',
  });
}
