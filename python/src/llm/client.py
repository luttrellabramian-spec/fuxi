"""LLM 客户端 - 支持任意 OpenAI 兼容 API（v0.2.0: 超时+重试+CircuitBreaker）"""
import os
import time
import logging
import openai
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """断路器（三态：CLOSED → OPEN → HALF_OPEN）

    用于保护 LLM 调用：
    - 连续 5 次失败 → 断路器断开（OPEN），所有请求直接拒绝
    - 30 秒后进入半开（HALF_OPEN），允许一个试探请求
    - 试探成功 → 闭合（CLOSED），失败 → 重新断开
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._last_error: str = ""

    @property
    def state(self) -> str:
        """获取当前状态（自动检查是否可进入 HALF_OPEN）"""
        if self._state == self.OPEN and self._opened_at:
            if time.time() - self._opened_at >= self._recovery_seconds:
                self._state = self.HALF_OPEN
                logger.info("CircuitBreaker 进入 HALF_OPEN，尝试恢复")
        return self._state

    def can_proceed(self) -> Tuple[bool, str]:
        """是否允许请求通过"""
        s = self.state
        if s == self.CLOSED:
            return True, ""
        if s == self.OPEN:
            return False, f"断路器已断开（{self._failure_threshold} 次连续失败），{self._recovery_seconds}秒后恢复"
        # HALF_OPEN：允许试探，完成后立即回到 OPEN 或 CLOSED
        return True, ""

    def record_success(self):
        """记录成功（重置失败计数，闭合断路器）"""
        self._failure_count = 0
        self._state = self.CLOSED
        self._opened_at = None
        self._last_error = ""

    def record_failure(self, error: str = ""):
        """记录失败"""
        self._failure_count += 1
        self._last_error = error or self._last_error
        if self._state == self.HALF_OPEN or self._failure_count >= self._failure_threshold:
            self._state = self.OPEN
            self._opened_at = time.time()
            logger.warning(
                f"CircuitBreaker 断开（连续 {self._failure_count} 次失败）: {self._last_error[:200]}"
            )

    def reset(self):
        """手动重置断路器"""
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._last_error = ""


# 全局共享的 LLM 断路器实例
llm_circuit_breaker = CircuitBreaker()


class LLMClient:
    """通用 OpenAI 兼容 API 客户端（v0.2.0 增强版）

    新增特性：
    - 超时控制：连接超时 5s，读取超时 30s（可配置）
    - 自动重试：指数退避（1s→2s→4s），最多 3 次（可配置）
    - CircuitBreaker：连续 5 次失败后断开 30s

    支持配置（环境变量）：
    - LLM_API_KEY: API 密钥
    - LLM_BASE_URL: API endpoint
    - LLM_MODEL: 模型名称
    - LLM_TIMEOUT_CONNECT: 连接超时秒数（默认 5）
    - LLM_TIMEOUT_READ: 读取超时秒数（默认 30）
    - LLM_MAX_RETRIES: 最大重试次数（默认 3）
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        timeout_connect: Optional[float] = None,
        timeout_read: Optional[float] = None,
        max_retries: Optional[int] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "")
        self.model = (
            model
            or os.environ.get("LLM_MODEL", "")
            or os.environ.get("DEFAULT_MODEL", "")
            or "gpt-4o"
        )
        self.max_tokens = max_tokens

        # 超时配置：5s connect + 30s read + 10s write + 10s pool（环境变量可覆盖）
        self._timeout_connect = timeout_connect or float(
            os.environ.get("LLM_TIMEOUT_CONNECT", "5")
        )
        self._timeout_read = timeout_read or float(
            os.environ.get("LLM_TIMEOUT_READ", "30")
        )
        self._timeout_write = float(
            os.environ.get("LLM_TIMEOUT_WRITE", "10")
        )
        self._timeout_pool = float(
            os.environ.get("LLM_TIMEOUT_POOL", "10")
        )
        # 重试次数：最多 3 次（环境变量可覆盖）
        self._max_retries = max_retries or int(
            os.environ.get("LLM_MAX_RETRIES", "3")
        )
        # CircuitBreaker（默认使用全局共享实例）
        self._circuit_breaker = circuit_breaker or llm_circuit_breaker

        # 懒加载 OpenAI 客户端
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import httpx
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url if self.base_url else None,
                http_client=httpx.Client(
                    timeout=httpx.Timeout(
                        connect=self._timeout_connect,
                        read=self._timeout_read,
                        write=self._timeout_write,
                        pool=self._timeout_pool,
                    ),
                ),
            )
        return self._client

    def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """完成对话（带超时控制、自动重试和 CircuitBreaker）

        Args:
            messages: 消息列表，如 [{"role": "user", "content": "..."}]
            temperature: 温度
            max_tokens: 最大 token 数
        Returns:
            响应字典（success=True/False）
        """
        # 1. CircuitBreaker 前置检查
        can_proceed, reason = self._circuit_breaker.can_proceed()
        if not can_proceed:
            logger.warning(f"LLM 请求被断路器拦截: {reason}")
            return {
                "content": "",
                "success": False,
                "error": f"circuit_breaker: {reason}",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "retries": 0,
            }

        # 2. 重试循环
        last_error = ""
        retry_delays = [1, 2, 4]  # 指数退避: 1s, 2s, 4s

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens or self.max_tokens,
                )
                choice = response.choices[0]
                result = {
                    "content": choice.message.content or "",
                    "finish_reason": choice.finish_reason,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                    "model": response.model,
                    "success": True,
                    "retries": attempt - 1,
                }
                self._circuit_breaker.record_success()
                return result

            except openai.RateLimitError as e:
                # 限流错误：最多重试
                last_error = f"rate_limit: {str(e)}"
                logger.warning(f"LLM 限流 (attempt {attempt}/{self._max_retries}): {last_error[:100]}")
                if attempt < self._max_retries:
                    wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    logger.info(f"等待 {wait}s 后重试...")
                    time.sleep(wait)
                self._circuit_breaker.record_failure(last_error)

            except openai.APITimeoutError as e:
                # 超时错误：需要重试
                last_error = f"timeout: {str(e)}"
                logger.warning(f"LLM 超时 (attempt {attempt}/{self._max_retries}): {last_error[:100]}")
                if attempt < self._max_retries:
                    wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(wait)
                self._circuit_breaker.record_failure(last_error)

            except openai.APIConnectionError as e:
                # 连接错误：需要重试
                last_error = f"connection: {str(e)}"
                logger.warning(f"LLM 连接失败 (attempt {attempt}/{self._max_retries}): {last_error[:100]}")
                if attempt < self._max_retries:
                    wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(wait)
                self._circuit_breaker.record_failure(last_error)

            except openai.AuthenticationError as e:
                # 认证错误：不重试，立即失败
                last_error = f"auth: {str(e)}"
                logger.error(f"LLM 认证失败: {last_error[:200]}")
                self._circuit_breaker.record_failure(last_error)
                return {
                    "content": "",
                    "success": False,
                    "error": last_error,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "retries": attempt - 1,
                }

            except openai.BadRequestError as e:
                # 参数错误：不重试
                last_error = f"bad_request: {str(e)}"
                logger.error(f"LLM 请求参数错误: {last_error[:200]}")
                return {
                    "content": "",
                    "success": False,
                    "error": last_error,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "retries": attempt - 1,
                }

            except Exception as e:
                # 其他未知错误：尝试重试
                last_error = f"unknown: {str(e)}"
                logger.warning(f"LLM 未知错误 (attempt {attempt}/{self._max_retries}): {last_error[:200]}")
                if attempt < self._max_retries:
                    wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(wait)
                self._circuit_breaker.record_failure(last_error)

        # 3. 所有重试均失败
        logger.error(f"LLM 调用失败（{self._max_retries} 次重试后）: {last_error[:200]}")
        return {
            "content": "",
            "success": False,
            "error": last_error,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "retries": self._max_retries,
        }

    def stream_complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """流式完成（生成器）- 带超时控制和基础错误重试

        注意：流式场景下无法完整实现 CircuitBreaker，
        因为生成器是延迟求值的。首次连接失败时触发重试。
        """
        # CircuitBreaker 前置检查（仅检查流创建阶段）
        can_proceed, reason = self._circuit_breaker.can_proceed()
        if not can_proceed:
            logger.warning(f"流式请求被断路器拦截: {reason}")
            return iter([])  # 返回空生成器

        last_error = ""
        retry_delays = [1, 2, 4]

        for attempt in range(1, self._max_retries + 1):
            try:
                stream = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens or self.max_tokens,
                    stream=True,
                )
                self._circuit_breaker.record_success()
                return stream

            except openai.AuthenticationError as e:
                last_error = f"auth: {str(e)}"
                logger.error(f"流式 LLM 认证失败: {last_error[:200]}")
                self._circuit_breaker.record_failure(last_error)
                return iter([])

            except openai.BadRequestError as e:
                last_error = f"bad_request: {str(e)}"
                logger.error(f"流式 LLM 参数错误: {last_error[:200]}")
                return iter([])

            except Exception as e:
                last_error = f"stream_create: {str(e)}"
                logger.warning(f"流式 LLM 创建失败 (attempt {attempt}/{self._max_retries})")
                if attempt < self._max_retries:
                    wait = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    time.sleep(wait)
                self._circuit_breaker.record_failure(last_error)

        logger.error(f"流式 LLM 创建失败（{self._max_retries} 次重试后）: {last_error[:200]}")
        return iter([])