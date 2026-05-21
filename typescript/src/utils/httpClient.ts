/**
 * HTTP 客户端 - 带超时控制和自定义错误类型
 *
 * v0.2.0 新增：LLM 调用 25 秒硬性超时，错误自动转换为 DegradedResponse 错误码
 */
import axios, { AxiosInstance, AxiosError } from 'axios';

// 自定义错误类型
export class LLMTimeoutError extends Error {
  code = 'LLM_TIMEOUT';
  constructor(msg?: string) {
    super(msg || 'LLM 响应超时（> 25s）');
    this.name = 'LLMTimeoutError';
  }
}

export class LLMUnavailableError extends Error {
  code = 'LLM_UNAVAILABLE';
  constructor(msg?: string) {
    super(msg || 'LLM 服务不可达');
    this.name = 'LLMUnavailableError';
  }
}

export class LLMRateLimitError extends Error {
  code = 'LLM_RATE_LIMIT';
  constructor(msg?: string) {
    super(msg || 'LLM 触发限流');
    this.name = 'LLMRateLimitError';
  }
}

/**
 * 单例 HTTP Client（复用连接池）
 * 硬性超时 25 秒，比 LLM 层的 30s 提前 5s，作为网关自身的保护层
 */
const httpClient: AxiosInstance = axios.create({
  timeout: 25 * 1000,                     // 25 秒超时
  timeoutErrorMessage: 'REQUEST_TIMEOUT',  // 超时错误码
  headers: {
    'Content-Type': 'application/json',
  },
});

// 响应拦截器：统一错误转换
httpClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.code === 'ECONNABORTED' || error.message === 'REQUEST_TIMEOUT') {
      throw new LLMTimeoutError('LLM 响应超时（> 25s）');
    }

    if (!error.response) {
      // 网络错误（无响应体）
      throw new LLMUnavailableError('LLM 服务不可达');
    }

    const status = error.response.status;
    if (status === 429) {
      throw new LLMRateLimitError('LLM 触发限流');
    }

    if (status >= 500) {
      throw new LLMUnavailableError(`LLM 返回服务端错误 ${status}`);
    }

    // 其他 HTTP 错误
    throw new LLMUnavailableError(`LLM 返回状态码 ${status}`);
  }
);

export default httpClient;
