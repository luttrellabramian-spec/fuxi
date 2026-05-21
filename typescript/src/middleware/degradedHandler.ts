/**
 * 全局降级响应中间件
 *
 * v0.2.0 新增：统一捕获所有未处理异常，返回标准化 DegradedResponse
 * 确保不向客户端暴露内部堆栈信息
 */
import { Request, Response, NextFunction } from 'express';
import { getRequestId } from './requestId';

/** 降级响应接口 */
export interface DegradedResponseBody {
  status: 'degraded' | 'error';
  code: string;
  message: string;
  retry_after?: number;
  request_id: string;
}

/** 已知可重试的错误类型 → retry_after 秒数 */
const RETRY_TABLE: Record<string, number> = {
  LLM_TIMEOUT: 5,
  LLM_UNAVAILABLE: 30,
  LLM_RATE_LIMIT: 60,
  REQUEST_TIMEOUT: 5,
};

/**
 * 将错误码转换为用户友好的提示文案
 */
function getUserMessage(code: string): string {
  const messages: Record<string, string> = {
    LLM_TIMEOUT:       '服务响应稍慢，请稍后重试',
    LLM_UNAVAILABLE:   '当前服务繁忙，请稍后重试',
    LLM_RATE_LIMIT:    '请求过于频繁，请稍后再试',
    REQUEST_TIMEOUT:   '请求超时，请稍后重试',
    INVALID_REQUEST:   '请求参数有误，请检查输入',
    SESSION_NOT_FOUND: '会话不存在或已过期',
    INTERNAL_ERROR:    '服务遇到问题，请稍后重试',
  };
  return messages[code] ?? '服务遇到问题，请稍后重试';
}

/**
 * 全局降级中间件
 *
 * 捕获所有未处理的异常，返回统一的 DegradedResponse。
 * 必须作为 Express 应用的最后一个中间件注册（在所有路由之后）。
 *
 * 使用方式：
 *   app.use(degradedHandler);
 *
 * 路由中通过 next(error) 将异常传递给此中间件：
 *   app.get('/api/chat', async (req, res, next) => {
 *     try { ... } catch (err) { next(err); }
 *   });
 */
export function degradedHandler(
  err: Error,
  req: Request,
  res: Response,
  _next: NextFunction
): void {
  const requestId = getRequestId(req);

  // 记录结构化错误日志（仅供服务端排查，不返回给客户端）
  console.error(JSON.stringify({
    level: 'error',
    request_id: requestId,
    path: req.path,
    method: req.method,
    error_code: (err as any).code || 'INTERNAL_ERROR',
    error_message: err.message,
    stack: err.stack?.slice(0, 500),      // 截断防泄漏
    timestamp: new Date().toISOString(),
  }));

  // 确定错误码和 retry_after
  const code = (err as any).code || 'INTERNAL_ERROR';
  const retryAfter = RETRY_TABLE[code] ?? null;

  // 构建降级响应（永远不包含 stack）
  const responseBody: DegradedResponseBody = {
    status: code.startsWith('INVALID') ? 'error' : 'degraded',
    code,
    message: getUserMessage(code),
    ...(retryAfter !== null && { retry_after: retryAfter }),
    request_id: requestId,
  };

  // HTTP 状态码：
  // - 客户端错误 4xx → 4xx
  // - 降级（超时/不可用）→ 200（业务层面的降级，而非网络错误）
  // - 未知内部错误 → 500
  const httpStatus = code.startsWith('INVALID') ? 400
    : code === 'SESSION_NOT_FOUND' ? 404
    : code === 'INTERNAL_ERROR' ? 500
    : 200;

  res.status(httpStatus).json(responseBody);
}
