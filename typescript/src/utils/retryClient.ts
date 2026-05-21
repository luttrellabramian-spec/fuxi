/**
 * 自动重试封装 - 只在收到降级响应（status=degraded）且有 retry_after 时重试
 *
 * v0.2.0 新增：指数退避重试策略，支持 maxAttempts 和自定义延迟
 */

export interface DegradedResponse {
  status: 'degraded' | 'error';
  code: string;
  message: string;
  retry_after?: number;
  request_id?: string;
}

export type ApiResponse<T> =
  | { success: true; data: T; request_id: string }
  | DegradedResponse;

export interface RetryOptions {
  maxAttempts?: number;   // 最大重试次数
  baseDelayMs?: number;   // 基础等待时间（毫秒）
  maxDelayMs?: number;    // 最大等待时间
}

/**
 * 判断响应是否为降级响应
 */
function isDegradedResponse<T>(response: ApiResponse<T>): response is DegradedResponse {
  return (response as DegradedResponse).status === 'degraded';
}

/**
 * 休眠函数
 */
function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * 带自动重试的 API 调用
 *
 * 只在收到降级响应（status=degraded）且有 retry_after 时重试。
 * 客户端错误（status=error）不重试，直接返回。
 *
 * @param fn - 要调用的 API 函数
 * @param options - 重试配置
 * @returns API 响应
 * @throws 所有重试均失败时抛出最后错误
 */
export async function callWithRetry<T>(
  fn: () => Promise<ApiResponse<T>>,
  options: RetryOptions = { maxAttempts: 3, baseDelayMs: 1000, maxDelayMs: 10000 }
): Promise<ApiResponse<T>> {
  const maxAttempts = options.maxAttempts ?? 3;
  const baseDelayMs = options.baseDelayMs ?? 1000;
  const maxDelayMs = options.maxDelayMs ?? 10000;

  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const response = await fn();

      // 成功
      if ((response as any).success) {
        return response;
      }

      // 降级响应
      if (isDegradedResponse(response)) {
        if (response.retry_after && attempt < maxAttempts) {
          // 有 retry_after：按建议等待后重试
          const delay = Math.min(response.retry_after * 1000, maxDelayMs);
          console.warn(
            `[RetryClient] Attempt ${attempt}/${maxAttempts} degraded (${response.code}), retrying after ${delay}ms...`
          );
          await sleep(delay);
          lastError = new Error(response.message);
          continue;
        }

        // 降级但无 retry_after（客户端错误如 INVALID_REQUEST），不重试
        return response;
      }

      // 未知格式，不重试
      return response;

    } catch (err) {
      lastError = err as Error;
      if (attempt < maxAttempts) {
        // 指数退避：1s → 2s → 4s ...
        const delay = Math.min(baseDelayMs * 2 ** (attempt - 1), maxDelayMs);
        console.warn(
          `[RetryClient] Attempt ${attempt}/${maxAttempts} error: ${(err as Error).message}, retrying after ${delay}ms...`
        );
        await sleep(delay);
      }
    }
  }

  // 所有重试均失败
  throw lastError || new Error('所有重试均失败');
}
