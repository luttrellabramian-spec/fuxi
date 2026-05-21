"""引擎增强 - LLM 降级链 + 并行工具决策 + IterationBudget（设计文档 L3）

1. LLM 降级链: 主模型失败时自动切换备用端点
2. 并行工具决策: 检测无依赖工具, 并行执行
3. IterationBudget: 线程安全的迭代预算控制
"""
import threading
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("engine_enhancements")


@dataclass
class LLMEndpoint:
    """LLM 端点配置"""
    name: str
    api_key: str
    base_url: str
    model: str
    priority: int  # 1=最高优先级
    cooldown_until: float = 0  # 冷却到何时
    consecutive_failures: int = 0


class LLMFallbackChain:
    """LLM 降级链 - 主模型失败时自动切换

    用法:
        chain = LLMFallbackChain([
            LLMEndpoint("main", key1, url1, "gpt-4o", 1),
            LLMEndpoint("backup", key2, url2, "claude-3", 2),
        ])
        result = chain.call_with_fallback(messages)
    """

    MAX_CONSECUTIVE_FAILURES = 3
    COOLDOWN_SECONDS = 30

    def __init__(self, endpoints: List[LLMEndpoint] = None):
        self._endpoints = sorted(endpoints or [], key=lambda e: e.priority)
        self._lock = threading.Lock()

    def add_endpoint(self, endpoint: LLMEndpoint):
        with self._lock:
            self._endpoints.append(endpoint)
            self._endpoints.sort(key=lambda e: e.priority)

    def get_available(self) -> Optional[LLMEndpoint]:
        now = time.time()
        for ep in self._endpoints:
            if now >= ep.cooldown_until:
                return ep
        return None

    def call_with_fallback(self, messages: List[Dict], temperature: float = 0.2,
                           max_tokens: int = 2048) -> Tuple[Optional[Dict], str]:
        """使用降级链调用 LLM, 返回 (result, endpoint_name)"""
        from llm.client import LLMClient

        for ep in self._endpoints:
            now = time.time()
            if now < ep.cooldown_until:
                continue

            try:
                client = LLMClient(
                    api_key=ep.api_key,
                    base_url=ep.base_url,
                    model=ep.model,
                )
                result = client.complete(messages=messages, temperature=temperature,
                                         max_tokens=max_tokens)
                if result.get("success"):
                    with self._lock:
                        ep.consecutive_failures = 0
                    return result, ep.name

                # 失败处理
                with self._lock:
                    ep.consecutive_failures += 1
                    if ep.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                        ep.cooldown_until = now + self.COOLDOWN_SECONDS
                        logger.warning(f"LLM {ep.name} 进入冷却 (连续 {self.MAX_CONSECUTIVE_FAILURES} 次失败)")

            except Exception as e:
                with self._lock:
                    ep.consecutive_failures += 1
                logger.warning(f"LLM {ep.name} 异常: {e}")

        return None, "all_endpoints_failed"

    def get_status(self) -> List[Dict]:
        now = time.time()
        return [
            {"name": e.name, "priority": e.priority,
             "available": now >= e.cooldown_until,
             "failures": e.consecutive_failures}
            for e in self._endpoints
        ]


class IterationBudget:
    """线程安全的迭代预算控制器（移植自 Hermes Agent）

    控制 Agent 自主探索的边界:
    - 每会话最大步数上限
    - 每会话最大 LLM 调用次数
    - 剩余预算查询
    """

    def __init__(self, max_steps: int = 10, max_llm_calls: int = 15, max_tokens: int = 100000):
        self._max_steps = max_steps
        self._max_llm_calls = max_llm_calls
        self._max_tokens = max_tokens
        self._lock = threading.Lock()

        # 当前会话预算
        self._steps_used = 0
        self._llm_calls_used = 0
        self._tokens_used = 0

    def reset(self):
        with self._lock:
            self._steps_used = 0
            self._llm_calls_used = 0
            self._tokens_used = 0

    def can_proceed(self) -> Tuple[bool, str]:
        with self._lock:
            if self._steps_used >= self._max_steps:
                return False, f"步数耗尽 ({self._steps_used}/{self._max_steps})"
            if self._llm_calls_used >= self._max_llm_calls:
                return False, f"LLM调用次数耗尽 ({self._llm_calls_used}/{self._max_llm_calls})"
            if self._tokens_used >= self._max_tokens:
                return False, f"Token预算耗尽 ({self._tokens_used}/{self._max_tokens})"
            return True, ""

    def consume_step(self):
        with self._lock:
            self._steps_used += 1

    def consume_llm_call(self, tokens: int = 0):
        with self._lock:
            self._llm_calls_used += 1
            self._tokens_used += tokens

    @property
    def remaining_steps(self) -> int:
        return max(0, self._max_steps - self._steps_used)

    @property
    def usage_ratio(self) -> float:
        return self._steps_used / max(1, self._max_steps)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "steps": f"{self._steps_used}/{self._max_steps}",
                "llm_calls": f"{self._llm_calls_used}/{self._max_llm_calls}",
                "tokens": f"{self._tokens_used}/{self._max_tokens}",
                "usage_ratio": self.usage_ratio,
            }


class ParallelToolDecider:
    """并行工具决策器 - 检测无依赖工具, 决定是否并行执行"""

    # 已知的读写依赖关系
    WRITE_TOOLS = {"write_file", "write_json", "memory_write", "search_replace"}
    READ_TOOLS = {"read_file", "read_json", "list_files", "file_exists",
                  "grep", "search_file", "memory_query", "memory_get_recent",
                  "http_get", "check_url", "web_search", "parse_headers"}

    def should_parallelize(self, tool_calls: List[Tuple[str, Dict]]) -> bool:
        """判断一组工具调用是否可以并行执行"""
        if len(tool_calls) <= 1:
            return False

        has_write = any(name in self.WRITE_TOOLS for name, _ in tool_calls)
        if not has_write:
            # 全是读操作, 可以并行
            return True

        # 有写操作时, 检查写操作之间是否有依赖
        write_tools = [(n, a) for n, a in tool_calls if n in self.WRITE_TOOLS]
        if len(write_tools) > 1:
            return False  # 多个写操作, 可能有文件冲突, 串行安全

        # 单个写操作 + 多个读操作: 可以并行（读不依赖写的结果）
        return True

    def classify_calls(self, tool_calls: List[Tuple[str, Dict]]) -> Dict[str, List]:
        """将工具调用分类为并行组"""
        if not self.should_parallelize(tool_calls):
            return {"serial": tool_calls}

        read_group = []
        write_group = []
        other_group = []

        for name, args in tool_calls:
            if name in self.WRITE_TOOLS:
                write_group.append((name, args))
            elif name in self.READ_TOOLS:
                read_group.append((name, args))
            else:
                other_group.append((name, args))

        result = {}
        if read_group:
            result["parallel_reads"] = read_group
        if write_group:
            result["serial_writes"] = write_group
        if other_group:
            result["unknown"] = other_group
        return result
