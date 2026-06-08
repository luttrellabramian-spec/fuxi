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

/** 剥离 <think>...</think> 块（v0.2.7: 网关层兜底）
 *
 *  Python 端 fuxi_engine 已经做过剥离，但 gRPC StreamComplete 流式
 *  累积多个 chunk 时，仍可能残留 think 标签。本函数在网关层做二次清理。
 */
export function stripThinkTagsInPlace(text: string): string {
  if (!text) return "";

  let cleaned = text;

  // 1. 完整闭合块
  cleaned = cleaned.replace(/<think>[\s\S]*?<\/think>\s*/g, "");

  // 2. 未闭合块 — 截到 Final/最终 标记或文本末尾
  if (cleaned.includes("<think>")) {
    for (const marker of ["Final:", "最终答案:", "最终:"]) {
      const idx = cleaned.indexOf(marker);
      if (idx >= 0) {
        cleaned = cleaned.slice(idx);
        break;
      }
    }
    if (cleaned.includes("<think>")) {
      const idx = cleaned.indexOf("<think>");
      cleaned = cleaned.slice(0, idx);
    }
  }

  // 3. 孤立标签
  cleaned = cleaned.replace(/<\/?think>/g, "");

  // 4. 规范化空白
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

  return cleaned.trim();
}

