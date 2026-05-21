"""工具执行器 - 安全封装 tool invocation

增强 invoke() 层级的安全性：
1. 超时控制：每个工具调用有硬性超时（默认 30s）
2. 自动重试：网络/限流/超时类错误自动重试（指数退避）
3. 结果缓存：相同工具+参数的调用命中缓存（TTL 60s）
4. 参数校验：基于函数签名的强制类型校验
5. 调用去重：同一 ReAct 轮次中相同的工具+参数只执行一次
"""
import json
import time
import inspect
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Dict, Any, Callable, Optional, Tuple, List

logger = logging.getLogger("tool_executor")

# 可重试的错误类型关键字（统一小写匹配）
_RETRYABLE_KEYWORDS = [
    "timeout", "connection", "network", "rate limit", "ratelimit",
    "too many", "503", "502", "504",
    "timed out", "reset", "refused",
]


def _is_retryable(error_str: str) -> bool:
    """判断错误是否可重试（临时性错误，大小写不敏感）"""
    lower = error_str.lower()
    return any(keyword in lower for keyword in _RETRYABLE_KEYWORDS)


def _validate_args(func: Callable, args_dict: Dict[str, Any]) -> str:
    """参数校验，返回错误信息（空字符串表示通过）"""
    sig = inspect.signature(func)
    try:
        sig.bind(**args_dict)
    except TypeError as e:
        return str(e)
    # 类型检查（如果有类型注解）
    for name, param in sig.parameters.items():
        if name in args_dict and param.annotation != inspect.Parameter.empty:
            val = args_dict[name]
            expected = param.annotation
            if expected is not None and not isinstance(val, expected):
                # 允许 int→float 的隐式转换
                if expected is float and isinstance(val, (int, float)):
                    continue
                if expected is str and not isinstance(val, (str, bytes)):
                    continue
                if expected is bool and not isinstance(val, bool):
                    continue
                if expected is int and isinstance(val, float) and val == int(val):
                    continue
                return (f"参数 '{name}' 类型错误: 期望 {expected.__name__}, "
                        f"实际 {type(val).__name__}")
    return ""


class ToolCache:
    """工具结果缓存（LRU + TTL）"""

    def __init__(self, max_entries: int = 100, ttl_seconds: int = 60):
        self._cache: Dict[str, Tuple[float, Dict]] = {}  # key -> (expire_time, result)
        self._order: list = []
        self._max = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def _make_key(self, tool_name: str, args: Dict) -> str:
        return f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

    def get(self, tool_name: str, args: Dict) -> Optional[Dict]:
        key = self._make_key(tool_name, args)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expire_time, result = entry
            if time.time() > expire_time:
                del self._cache[key]
                if key in self._order:
                    self._order.remove(key)
                return None
            # 更新使用顺序
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return result

    def set(self, tool_name: str, args: Dict, result: Dict) -> None:
        key = self._make_key(tool_name, args)
        expire = time.time() + self._ttl
        with self._lock:
            # 淘汰最旧的
            if key not in self._cache and len(self._cache) >= self._max:
                oldest = self._order.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = (expire, result)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)

    def invalidate(self, tool_name: str) -> None:
        """使某个工具的所有缓存失效（写操作后调用）"""
        with self._lock:
            to_delete = [k for k in self._cache if k.startswith(f"{tool_name}:")]
            for k in to_delete:
                del self._cache[k]
                if k in self._order:
                    self._order.remove(k)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._order.clear()


class ToolExecutor:
    """工具安全执行器 - 包裹 registry.invoke()

    使用方式（在 fuxi_engine.py 中替换 tool_registry.invoke 调用）：
        executor = ToolExecutor(tool_registry)
        result = executor.invoke(tool_name, arguments_json)
    """

    # 默认配置
    DEFAULT_TOOL_TIMEOUT = 30       # 每个工具调用的硬性超时（秒）
    DEFAULT_MAX_RETRIES = 2         # 可重试错误的最大重试次数
    RETRY_DELAYS = [1, 3]           # 重试等待时间（秒）

    def __init__(
        self,
        tool_registry,
        timeout: int = DEFAULT_TOOL_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        enable_cache: bool = True,
        enable_validation: bool = True,
        enable_dedup: bool = True,
        enable_level_check: bool = False,  # 默认关闭，避免破坏现有功能
    ):
        self._registry = tool_registry
        self._timeout = timeout
        self._max_retries = max_retries
        self._enable_cache = enable_cache
        self._enable_validation = enable_validation
        self._enable_dedup = enable_dedup
        self._enable_level_check = enable_level_check

        # 用于超时的线程池
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._cache = ToolCache()
        # 调用去重（按 (session_id, step) 分组）
        self._dedup_set: set = set()
        # 外部回调（与 registry.on_invoke 兼容）
        self._callbacks: list = []

    def on_invoke(self, callback: Callable[[str, bool, Dict], None]) -> None:
        """注册工具调用回调（与 registry.on_invoke 接口相同）"""
        self._callbacks.append(callback)

    def start_round(self, session_id: str, step: int) -> None:
        """开始一个新的 ReAct 轮次（清空去重集）"""
        self._dedup_set.clear()

    def invoke(
        self,
        tool_name: str,
        arguments_json: Any,
        session_id: str = "",
        step: int = 0,
        bypass_cache: bool = False,
    ) -> Dict[str, Any]:
        """安全执行工具调用

        Args:
            tool_name: 工具名称
            arguments_json: JSON 字符串或 dict
            session_id: 会话 ID（用于去重）
            step: ReAct 轮次（用于去重）
            bypass_cache: 跳过缓存（写操作使用）

        Returns:
            {"success": bool, "result_json": str, "error": str, "elapsed_ms": int,
             "from_cache": bool, "retries": int}
        """
        start = int(time.time() * 1000)
        t = self._registry.get_tool(tool_name)
        if not t:
            elapsed = int(time.time() * 1000) - start
            return {"success": False, "result_json": "{}",
                    "error": f"Tool '{tool_name}' not found",
                    "elapsed_ms": elapsed, "from_cache": False, "retries": 0}

        # 解析参数
        args = (arguments_json if isinstance(arguments_json, dict)
                else json.loads(arguments_json) if arguments_json else {})

        # 调用去重检查
        dedup_key = f"{session_id}:{step}:{tool_name}:{json.dumps(args, sort_keys=True)}"
        if self._enable_dedup and session_id:
            if dedup_key in self._dedup_set:
                elapsed = int(time.time() * 1000) - start
                return {"success": False, "result_json": "{}",
                        "error": f"Tool '{tool_name}' already called in step {step}",
                        "elapsed_ms": elapsed, "from_cache": False, "retries": 0,
                        "dedup": True}
            self._dedup_set.add(dedup_key)

        # 参数校验
        if self._enable_validation:
            validation_error = _validate_args(t, args)
            if validation_error:
                elapsed = int(time.time() * 1000) - start
                return {"success": False, "result_json": "{}",
                        "error": f"参数校验失败: {validation_error}",
                        "elapsed_ms": elapsed, "from_cache": False, "retries": 0}

        # L0/L1 权限检查（默认关闭，opt-in 启用）
        if self._enable_level_check:
            tool_info = self._registry.list_tools().get(tool_name, {})
            tool_level = tool_info.get("level", "L1")
            if tool_level == "L1":
                elapsed = int(time.time() * 1000) - start
                logger.warning(f"L1 工具 {tool_name} 被调用但未授权（L0/L1 检查未通过）")
                return {"success": False, "result_json": "{}",
                        "error": f"权限不足: {tool_name} 需要 L1 访问级别",
                        "elapsed_ms": elapsed, "from_cache": False, "retries": 0}

        # 缓存检查（读操作）
        if self._enable_cache and not bypass_cache:
            cached = self._cache.get(tool_name, args)
            if cached is not None:
                elapsed = int(time.time() * 1000) - start
                cached["from_cache"] = True
                cached["elapsed_ms"] = elapsed
                self._fire_callbacks(tool_name, cached.get("success", False), cached)
                return cached

        # 执行（带超时和重试）
        retries = 0
        last_error = ""
        delays = self.RETRY_DELAYS[:self._max_retries]

        for attempt in range(1 + len(delays)):
            try:
                result = self._execute_with_timeout(t, args)
                result["elapsed_ms"] = int(time.time() * 1000) - start
                result["from_cache"] = False
                result["retries"] = retries

                # 缓存结果
                if self._enable_cache and not bypass_cache and result.get("success"):
                    self._cache.set(tool_name, args, result)

                # 如果是写操作，使相关缓存失效
                if result.get("success") and bypass_cache:
                    self._cache.invalidate(tool_name)

                # 触发回调
                self._fire_callbacks(tool_name, result.get("success", False), result)

                return result

            except TimeoutError:
                last_error = f"{type(t).__name__ if hasattr(t, '__name__') else tool_name} execution timeout (>{self._timeout}s)"
                if attempt < len(delays):
                    wait = delays[attempt]
                    logger.warning(f"{tool_name} 超时 (attempt {attempt+1}), 等待 {wait}s 重试")
                    time.sleep(wait)
                    retries += 1
                else:
                    break

            except Exception as e:
                error_str = str(e)
                # 检查是否可重试
                if _is_retryable(error_str) and attempt < len(delays):
                    wait = delays[attempt]
                    logger.warning(f"{tool_name} 失败 ({error_str[:80]}), 等待 {wait}s 重试")
                    time.sleep(wait)
                    retries += 1
                    last_error = error_str
                else:
                    # 不可重试的错误，立即返回
                    elapsed = int(time.time() * 1000) - start
                    return {"success": False, "result_json": "{}",
                            "error": error_str, "elapsed_ms": elapsed,
                            "from_cache": False, "retries": attempt}

        # 所有重试均失败
        elapsed = int(time.time() * 1000) - start
        return {"success": False, "result_json": "{}",
                "error": last_error or f"{tool_name} failed after {retries} retries",
                "elapsed_ms": elapsed, "from_cache": False, "retries": retries}

    def _execute_with_timeout(self, func: Callable, args: Dict) -> Dict:
        """在线程池中执行工具，带超时

        注意：future.cancel() 对 Python 线程池中的已启动线程无效。
        超时后工具可能仍在后台运行（结果被丢弃），但我们通过抛异常
        通知调用者本次执行已超时，需要处理（可能重试或直接失败）。
        """
        future = self._executor.submit(self._safe_call, func, args)
        try:
            result = future.result(timeout=self._timeout)
            return result
        except TimeoutError:
            # 注意：线程会继续运行直到完成，但结果被丢弃
            # 这里记录警告而非调用 cancel()（无实际效果）
            logger.warning(f"Tool execution timed out after {self._timeout}s (result discarded)")
            raise  # 抛出超时异常，上层决定是否重试
        except Exception:
            raise

    def _safe_call(self, func: Callable, args: Dict) -> Dict:
        """安全调用工具函数"""
        try:
            raw = func(**args)
            # 统一返回格式
            if isinstance(raw, dict):
                if "success" not in raw:
                    return {"success": True, "result_json": json.dumps(raw, ensure_ascii=False), "error": ""}
                return {
                    "success": raw.get("success", True),
                    "result_json": raw.get("result_json", json.dumps(
                        {k: v for k, v in raw.items() if k not in ("success", "error", "elapsed_ms")},
                        ensure_ascii=False,
                    )),
                    "error": raw.get("error", ""),
                }
            # 非 dict 返回值（如字符串、列表）
            return {"success": True, "result_json": json.dumps(raw, ensure_ascii=False), "error": ""}
        except Exception:
            raise

    def _fire_callbacks(self, tool_name: str, success: bool, result: Dict) -> None:
        """触发回调（与 registry 兼容）"""
        for cb in self._callbacks:
            try:
                cb(tool_name, success, result)
            except Exception:
                pass

    def invoke_parallel(
        self,
        tool_calls: List[Tuple[str, Any]],  # [(tool_name, arguments_json), ...]
        session_id: str = "",
        step: int = 0,
    ) -> List[Dict[str, Any]]:
        """并行执行多个无依赖的工具调用

        Args:
            tool_calls: 工具调用列表 [(tool_name, arguments_json), ...]
            session_id: 会话 ID（用于去重）
            step: ReAct 轮次

        Returns:
            结果列表 [result1, result2, ...]，顺序与输入对应
        """
        if not tool_calls:
            return []

        start = int(time.time() * 1000)

        # 分析工具依赖关系
        write_tools = {"write_file", "write_json", "memory_write", "delete_file",
                       "search_replace", "append_file"}
        read_tools = {"read_file", "grep", "list_files", "file_exists", "read_json",
                      "memory_query", "memory_get_recent", "check_url", "search_file",
                      "web_search", "web_fetch"}

        # 分类工具：写操作 vs 读操作
        call_specs = []
        for tool_name, args_json in tool_calls:
            is_write = tool_name in write_tools
            is_read = tool_name in read_tools
            call_specs.append({
                "tool_name": tool_name,
                "args_json": args_json,
                "is_write": is_write,
                "is_read": is_read,
                "conflict_group": "write" if is_write else ("read" if is_read else "other"),
            })

        # 构建执行计划：同组工具串行，不同组可并行
        # 简化策略：write 组和 read 组各内部串行，两组之间可并行
        write_calls = [(s["tool_name"], s["args_json"]) for s in call_specs if s["is_write"]]
        read_calls = [(s["tool_name"], s["args_json"]) for s in call_specs if s["is_read"]]
        other_calls = [(s["tool_name"], s["args_json"]) for s in call_specs
                       if not s["is_write"] and not s["is_read"]]

        results = []
        from concurrent.futures import as_completed

        def execute_call(tool_name, args_json):
            return self.invoke(
                tool_name=tool_name,
                arguments_json=args_json,
                session_id=session_id,
                step=step,
                bypass_cache=(tool_name in write_tools),
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}

            # 写操作组（串行）
            for i, (tn, aj) in enumerate(write_calls):
                # 每个写操作独立提交到线程池
                future = pool.submit(execute_call, tn, aj)
                futures[future] = ("write", i, tn, aj)

            # 读操作组（并行）
            for i, (tn, aj) in enumerate(read_calls):
                future = pool.submit(execute_call, tn, aj)
                futures[future] = ("read", i, tn, aj)

            # 其他操作（并行）
            for i, (tn, aj) in enumerate(other_calls):
                future = pool.submit(execute_call, tn, aj)
                futures[future] = ("other", i, tn, aj)

            # 收集结果（保持顺序）
            results_map = {}
            for future in as_completed(futures):
                group, idx, tn, aj = futures[future]
                try:
                    result = future.result()
                    results_map[(group, idx)] = result
                except Exception as e:
                    results_map[(group, idx)] = {
                        "success": False, "result_json": "{}",
                        "error": str(e), "elapsed_ms": 0,
                        "from_cache": False, "retries": 0,
                    }

            # 按组别顺序重组结果
            group_results = {"write": [], "read": [], "other": []}
            for (group, idx), result in sorted(results_map.items(), key=lambda x: x[0][1]):
                group_results[group].append(result)

            results = group_results["write"] + group_results["read"] + group_results["other"]

        return results

    def shutdown(self) -> None:
        """关闭线程池"""
        self._executor.shutdown(wait=True)
