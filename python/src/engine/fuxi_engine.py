"""伏羲核心引擎 - ReAct 循环（v0.3.0: 全面架构加固）"""
import sys
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(CURRENT_DIR), '..'))

import re
import time
import threading
import json
import logging
import atexit
from typing import Dict, Any, List, Optional
from collections import OrderedDict

from tools import registry
from tools.executor import ToolExecutor
from llm.client import LLMClient
from memory.hot_memory import HotMemory
from engine.execution_logger import StructuredLogger, make_trace_id
from engine.tool_tracker import ToolCallTracker
from engine.response_parser import fix_json, parse_action, parse_final  # noqa: F401
from evolution.selector import Selector

logger = logging.getLogger("fuxi_engine")

MAX_HISTORY_MESSAGES = 40         # 单会话历史上限
MAX_SESSIONS = 100                # 全局会话数上限（LRU淘汰）
MAX_BAD_OUTPUTS = 3               # 连续格式错误上限
MAX_EMPTY_OUTPUTS = 3             # 连续空输出上限
TOOL_NAME_PATTERN = r'([\w.-]+)'  # 工具名正则（支持连字符和点号）


class FuxiEngine:
    """伏羲引擎：ReAct + 工具调度 + 记忆管理 + 自进化（v0.3.0）"""

    def __init__(
        self,
        llm_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_steps: int = 10,
        execution_logger: Optional[StructuredLogger] = None,
        tool_tracker: Optional[ToolCallTracker] = None,
        selector: Optional[Selector] = None,
        warm_memory=None,
        cold_memory=None,
    ):
        self.llm = LLMClient(
            api_key=llm_key,
            base_url=base_url,
            model=model,
        )
        self.hot_memory = HotMemory()
        self.warm_memory = warm_memory
        self.cold_memory = cold_memory
        self.tool_registry = registry
        self.max_steps = max_steps
        # session_history: 全局 LRU 淘汰（超过 MAX_SESSIONS 时淘汰最久未用）
        self._session_history: Dict[str, List[Dict[str, str]]] = OrderedDict()
        self._session_access = OrderedDict()  # session_id → timestamp
        self._session_lock = threading.Lock()
        self._execution_logger = execution_logger
        self._tool_tracker = tool_tracker
        self._selector = selector
        self._last_advice: Dict[str, Any] = {}
        # P1-2: 任务持久化（SQLite）
        self._task_db_path = os.environ.get("FUXI_TASK_DB", "")
        self._task_persistence_enabled = bool(self._task_db_path)
        self._init_task_persistence()

        # v0.2.0: 工具安全执行器（超时+重试+缓存+校验+去重）
        # P1-3: L0/L1 权限检查（默认关闭，ENABLE_LEVEL_CHECK=true 启用）
        enable_level_check = os.environ.get("ENABLE_LEVEL_CHECK", "false").lower() == "true"
        self._tool_executor = ToolExecutor(
            tool_registry=registry,
            enable_cache=True,
            enable_validation=True,
            enable_dedup=True,
            enable_level_check=enable_level_check,
        )
        # 注册 atexit 确保线程池关闭
        atexit.register(self._tool_executor.shutdown)

        # 注册工具调用追踪回调（通过执行器）
        if tool_tracker is not None:
            self._tool_executor.on_invoke(self._on_tool_invoked)

        # v0.2.0: 注册热记忆淘汰下刷回调
        if warm_memory is not None:
            self.hot_memory.set_warm_flush_callback(self._on_hot_evict)

        # v0.3: 行为进化引擎（真正改写系统行为）
        self._behavior_evolution = None  # 由外部注入

    def clear_session(self, session_id: str) -> None:
        """清理指定会话（释放内存）"""
        with self._session_lock:
            self._session_history.pop(session_id, None)
            self._session_access.pop(session_id, None)

    # ── P1-2: 任务持久化与中断恢复 ──────────────────────

    def _init_task_persistence(self) -> None:
        """初始化任务持久化数据库"""
        if not self._task_persistence_enabled:
            return
        import sqlite3
        self._task_db_path = self._task_db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "task_state.db"
        )
        try:
            conn = sqlite3.connect(self._task_db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_state (
                    session_id TEXT PRIMARY KEY,
                    step INTEGER NOT NULL,
                    messages TEXT NOT NULL,  -- JSON 序列化
                    observations TEXT NOT NULL,  -- JSON 序列化
                    tools_used TEXT NOT NULL,  -- JSON 序列化
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            logger.debug(f"Task persistence DB initialized: {self._task_db_path}")
        except Exception as e:
            logger.warning(f"Failed to init task persistence: {e}")
            self._task_persistence_enabled = False

    def save_task_state(
        self,
        session_id: str,
        step: int,
        messages: List[Dict[str, str]],
        observations: List[Dict[str, Any]],
        tools_used: List[Dict[str, Any]],
    ) -> bool:
        """保存任务执行状态到 SQLite

        Args:
            session_id: 会话 ID
            step: 当前 step 编号
            messages: 当前 messages 历史
            observations: 已收集的观察结果
            tools_used: 已使用的工具列表

        Returns:
            是否保存成功
        """
        if not self._task_persistence_enabled:
            return False

        import sqlite3, json
        try:
            conn = sqlite3.connect(self._task_db_path)
            now = time.time()
            conn.execute("""
                INSERT OR REPLACE INTO task_state
                (session_id, step, messages, observations, tools_used, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM task_state WHERE session_id = ?), ?), ?)
            """, (
                session_id, step,
                json.dumps(messages, ensure_ascii=False),
                json.dumps(observations, ensure_ascii=False),
                json.dumps(tools_used, ensure_ascii=False),
                session_id, now, now
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to save task state: {e}")
            return False

    def restore_task_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """恢复未完成的任务状态

        Args:
            session_id: 会话 ID

        Returns:
            恢复的状态 {"step": int, "messages": [...], "observations": [...], "tools_used": [...]}
            或 None（无保存状态）
        """
        if not self._task_persistence_enabled:
            return None

        import sqlite3, json
        try:
            conn = sqlite3.connect(self._task_db_path)
            cursor = conn.execute(
                "SELECT step, messages, observations, tools_used FROM task_state WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            step, messages_json, observations_json, tools_used_json = row
            return {
                "step": step,
                "messages": json.loads(messages_json),
                "observations": json.loads(observations_json),
                "tools_used": json.loads(tools_used_json),
            }
        except Exception as e:
            logger.warning(f"Failed to restore task state: {e}")
            return None

    def clear_task_state(self, session_id: str) -> bool:
        """清除某个会话的持久化状态（任务完成后调用）"""
        if not self._task_persistence_enabled:
            return False

        import sqlite3
        try:
            conn = sqlite3.connect(self._task_db_path)
            conn.execute("DELETE FROM task_state WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to clear task state: {e}")
            return False

    def _enforce_session_limit(self) -> None:
        """强制会话数上限（超过 MAX_SESSIONS 时淘汰最久未访问的）"""
        while len(self._session_history) > MAX_SESSIONS:
            oldest_sid, _ = self._session_access.popitem(last=False)
            self._session_history.pop(oldest_sid, None)
            logger.debug(f"淘汰最旧会话 {oldest_sid} (总数 {len(self._session_history)})")

    def _on_tool_invoked(self, tool_name: str, success: bool, result: Dict) -> None:
        """工具调用回调：记录到 ToolCallTracker + BehaviorEvolution"""
        tracker = self._tool_tracker
        if tracker is not None:
            try:
                tracker.record(
                    session_id=self._current_session_id or "default",
                    tool_name=tool_name,
                    success=success,
                    latency_ms=result.get("elapsed_ms", 0),
                    error_type="none" if success else "tool_execution_error",
                    error_message=result.get("error", ""),
                )
            except Exception:
                pass

        # v0.3: 行为进化记录
        if self._behavior_evolution is not None:
            try:
                self._behavior_evolution.record_tool_result(
                    tool_name=tool_name,
                    success=success,
                    latency_ms=result.get("elapsed_ms", 0),
                    was_timeout=("timeout" in str(result.get("error", "")).lower()),
                )
            except Exception:
                pass

    def _on_hot_evict(self, key: str, value: str) -> None:
        """热记忆淘汰回调：自动下刷到温记忆"""
        if self.warm_memory is None:
            return
        try:
            session_id = key.split("_")[1] if "_" in key else "default"
            self.warm_memory.add_message(
                session_id=session_id,
                content=value,
            )
        except Exception:
            pass

    def _strip_think_tags(self, text: str) -> str:
        """剥离 <think>...</think> 标签及其内容，包括未闭合的 <think>"""
        # 移除完整的 <think>... 块
        cleaned = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)
        # 移除未闭合的 <think>... 到 Final: 或文本末尾
        cleaned = re.sub(r'<think>[\s\S]*?(?=Final:|$)', '', cleaned)
        # 移除残留的单个标签
        cleaned = re.sub(r'<\/?think>', '', cleaned)
        return cleaned.strip()

    def _get_system_prompt(self, advice: Optional[Dict[str, Any]] = None) -> str:
        """获取系统提示词（v0.2.0: Selector 工具排序 + 主动记忆注入）"""
        hot = self.hot_memory.read()
        hot_content = hot.get("memory_content", "")
        model_name = self.llm.model or "当前模型"

        # Selector 工具排序
        tool_prompt_section = ""
        if advice and advice.get("tools", {}).get("prompt_section"):
            tool_prompt_section = advice["tools"]["prompt_section"]
        else:
            tool_descs = []
            for name, info in self.tool_registry.list_tools().items():
                tool_descs.append(f"- {name}: {info.get('doc', 'no description')}")
            tool_prompt_section = "\n".join(tool_descs[:15])

        # Selector 策略参数
        steps = self.max_steps
        if advice:
            rec_steps = advice.get("strategy", {}).get("recommend_steps")
            if rec_steps:
                steps = min(rec_steps, 15)

        # Selector 主动检索的记忆上下文
        retrieved = advice.get("retrieved_memories", {}) if advice else {}
        memory_section = ""
        if hot_content or retrieved.get("warm") or retrieved.get("cold"):
            if self._selector:
                memory_section = self._selector.format_memory_context(
                    retrieved=retrieved,
                    hot_content=hot_content,
                )
            else:
                memory_section = f"【近期记忆】\n{hot_content[:800]}" if hot_content else "无"

        system = f"""你是伏羲引擎 (Fuxi Engine)，一个高效的 AI 助手，基于 {model_name} 模型驱动，擅长工具调用和问题解决。

{memory_section if memory_section else "【近期记忆】\n无"}

【可用工具】
{tool_prompt_section}

【工作模式】
使用 ReAct 模式：Thought → Action → Observation，循环最多 {steps} 次。

【输出格式】
你必须严格使用以下格式，禁止输出除格式外的任何解释性文字：

## 工具调用（需要执行工具时）
Action: tool_name({{"param": "value"}})

## 最终答案（问题已解决，不需要更多工具）
Final: <直接给出答案>

注意：不要在 Final 前加任何 Thinking 或解释。只使用 Final: 作为最终答案标记。不要输出 <think> 标签。
"""
        return system

    def _trim_history(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """裁剪历史消息，防止超过 context window

        策略（P1-1 增强：上下文压缩）：
        - 第一条（system prompt）始终保留
        - 包含 system 在内的总条数不超过 MAX_HISTORY_MESSAGES
        - 超出时：
          - 消息数 > 30：调用 LLM 将早期对话压缩为摘要消息
          - 否则：直接裁剪最旧的非 system 消息
        """
        if len(messages) <= MAX_HISTORY_MESSAGES:
            return messages

        # 分离 system prompt 和普通消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]

        # 保留最新的 (MAX_HISTORY_MESSAGES - len(system_msgs)) 条其他消息
        keep_count = MAX_HISTORY_MESSAGES - len(system_msgs)
        if keep_count <= 0:
            return system_msgs[:MAX_HISTORY_MESSAGES]

        # P1-1 增强：上下文压缩
        # 当消息数 > 30 时，将 history[1:10]（最早的非 system 消息）压缩为一条摘要
        if len(other_msgs) > 30:
            # 早期消息（将被压缩的部分）
            early_msgs = other_msgs[:len(other_msgs) - keep_count]
            # 最近的保留消息
            recent_msgs = other_msgs[len(other_msgs) - keep_count:]

            if early_msgs and len(early_msgs) >= 5:
                # 调用 LLM 压缩上下文
                summary_text = self._compress_context(early_msgs)
                if summary_text:
                    summary_msg = {
                        "role": "system",
                        "content": f"【早期对话摘要】{summary_text}"
                    }
                    return system_msgs + [summary_msg] + recent_msgs

        recent_msgs = other_msgs[-keep_count:]
        return system_msgs + recent_msgs

    def _compress_context(self, messages: List[Dict[str, str]]) -> str:
        """调用 LLM 将早期对话压缩为一条摘要

        Args:
            messages: 需要压缩的早期消息列表

        Returns:
            压缩后的摘要文本
        """
        if not messages or len(messages) < 3:
            return ""

        try:
            # 构建压缩提示
            conversation_text = "\n".join(
                f"{'[用户]' if m.get('role') == 'user' else '[助手]'} {m.get('content', '')[:200]}"
                for m in messages[:10]  # 最多处理前 10 条
            )

            compress_prompt = f"""请将以下对话记录压缩为一段简洁的摘要，保留关键信息和结论：

{conversation_text}

压缩后的摘要（100字以内）："""

            response = self.llm.complete(
                messages=[{"role": "user", "content": compress_prompt}],
                temperature=0.3,
                max_tokens=200,
            )

            if response.get("success"):
                summary = response.get("content", "").strip()
                # 截断过长摘要
                return summary[:200] if summary else ""
            else:
                logger.warning(f"Context compression LLM failed: {response.get('error')}")
                return ""

        except Exception as e:
            logger.warning(f"Context compression failed: {e}")
            return ""

    def run(self, user_message: str, session_id: str = "default") -> Dict[str, Any]:
        """运行真正的 ReAct 循环（v0.3.0: 循环保护 + 安全网）"""
        self._current_session_id = session_id
        start_time = time.time()
        trace_id = make_trace_id()
        llm_logger = self._execution_logger

        # 会话数上限保障
        self._enforce_session_limit()

        # 每次消息都执行完整的 Selector 选择决策
        advice = None
        if self._selector is not None:
            try:
                advice = self._selector.select(
                    user_message=user_message,
                    session_id=session_id,
                    available_tools=self.tool_registry.list_tools(),
                    default_steps=self.max_steps,
                    is_new_message=True,
                )
                self._last_advice = advice
            except Exception as e:
                logger.warning(f"Selector select failed: {e}")

        # 确定本次运行的有效步数
        effective_steps = self.max_steps
        if advice:
            rec = advice.get("strategy", {}).get("recommend_steps")
            if rec:
                effective_steps = min(rec, 15)

        with self._session_lock:
            # 每次消息都重建 system prompt
            fresh_system_prompt = self._get_system_prompt(advice=advice)
            if session_id in self._session_history:
                existing = self._session_history[session_id]
                if existing and existing[0]["role"] == "system":
                    existing[0]["content"] = fresh_system_prompt
                else:
                    existing.insert(0, {"role": "system", "content": fresh_system_prompt})
            else:
                self._session_history[session_id] = [
                    {"role": "system", "content": fresh_system_prompt},
                ]
            # 更新访问时间
            self._session_access[session_id] = time.time()
            self._session_access.move_to_end(session_id)

            messages = self._session_history[session_id]
            messages = self._trim_history(messages)
            self._session_history[session_id] = messages
            messages.append({"role": "user", "content": user_message})

        steps = []
        observations = []
        final_answer = None
        completed = False
        llm_result = None
        consecutive_bad_output = 0
        consecutive_empty_output = 0

        # 日志: DAG 开始
        if llm_logger:
            llm_logger.log({
                "trace_id": trace_id,
                "node_id": "dag-start",
                "node_type": "dag_start",
                "status": "success",
                "duration_ms": 0,
                "data": {"session_id": session_id, "message_length": len(user_message)},
            })

        for step in range(effective_steps):
            # 每步后裁剪历史（防止循环内上下文溢出）
            messages = self._trim_history(messages)

            llm_start = time.time()
            llm_result = self.llm.complete(
                messages=messages,
                temperature=0.2,
                max_tokens=2048,
            )
            llm_duration = int((time.time() - llm_start) * 1000)

            # ── LLM 调用失败 ──
            if not llm_result["success"]:
                if llm_logger:
                    llm_logger.log({
                        "trace_id": trace_id,
                        "node_id": f"llm-call-{step}",
                        "node_type": "llm_call",
                        "status": "failure",
                        "duration_ms": llm_duration,
                        "error": {"type": "llm_error", "message": llm_result.get("error", "")},
                    })
                with self._session_lock:
                    if messages and messages[-1]["role"] == "user":
                        messages.pop()
                return {
                    "success": False,
                    "error": llm_result.get("error", "Unknown error"),
                    "elapsed": time.time() - start_time,
                    "steps": step,
                }

            content = (llm_result["content"] or "").strip()

            # ── 空输出保护 ──
            if not content:
                consecutive_empty_output += 1
                consecutive_bad_output = 0
                if consecutive_empty_output >= MAX_EMPTY_OUTPUTS:
                    logger.warning(f"LLM 连续 {MAX_EMPTY_OUTPUTS} 次空输出，终止循环")
                    break
                messages.append({"role": "user", "content": "请继续完整的回复，不要输出空内容。"})
                continue
            consecutive_empty_output = 0

            # 剥离 <think> 标签
            content = self._strip_think_tags(content)
            messages.append({"role": "assistant", "content": content})

            # 日志: LLM 调用成功
            if llm_logger:
                usage = llm_result.get("usage", {})
                llm_logger.log({
                    "trace_id": trace_id,
                    "node_id": f"llm-call-{step}",
                    "node_type": "llm_call",
                    "status": "success",
                    "duration_ms": llm_duration,
                    "data": {
                        "model": llm_result.get("model", self.llm.model or ""),
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "temperature": 0.2,
                    },
                })

            # ── 解析: Action优先检查, 但如果同时有Final则优先Final ──
            action = parse_action(content)
            final_match = parse_final(content)

            # 如果同时有 Action 和 Final，优先 Final（LLM 想结束）
            if action and final_match:
                logger.debug(f"第{step+1}步同时有 Action 和 Final，优先 Final")
                final_answer = final_match
                completed = True
                break

            if final_match:
                final_answer = final_match
                completed = True
                break

            if action is None:
                # 无法解析：累计计数，超出则终止
                consecutive_bad_output += 1
                if consecutive_bad_output >= MAX_BAD_OUTPUTS:
                    logger.warning(f"LLM 连续 {MAX_BAD_OUTPUTS} 次无法解析输出，终止循环")
                    break
                messages.append({"role": "user",
                                 "content": "请使用 Action: tool_name({...}) 调用工具，或使用 Final: 给出最终答案。"})
                continue
            consecutive_bad_output = 0

            # ── 执行工具 ──
            tool_name = action.get("tool")
            args = action.get("arguments", {})
            tool_start = time.time()
            self._tool_executor.start_round(session_id, step)

            tool_result = self._tool_executor.invoke(
                tool_name=tool_name,
                arguments_json=args,
                session_id=session_id,
                step=step,
                bypass_cache=(tool_name in ("write_file", "write_json", "memory_write")),
            )
            tool_duration = int((time.time() - tool_start) * 1000)
            obs = tool_result.get("result_json", tool_result.get("error", ""))
            tool_success = tool_result.get("success", False)
            from_cache = tool_result.get("from_cache", False)
            retries = tool_result.get("retries", 0)
            dedup = tool_result.get("dedup", False)

            # ── 去重处理：仍然追加观察，保持 ReAct 链完整 ──
            if dedup:
                obs_msg = (f"观察 {step + 1}: {tool_name} 已在当前步骤执行过，"
                           f"使用之前的观察结果。")
                messages.append({"role": "user", "content": obs_msg})
                continue

            # 日志: 工具调用
            if llm_logger:
                llm_logger.log({
                    "trace_id": trace_id,
                    "node_id": f"tool-{tool_name}-{step}",
                    "node_type": "tool_call",
                    "status": "success" if tool_success else "failure",
                    "duration_ms": tool_duration,
                    "data": {
                        "tool_name": tool_name,
                        "tool_args": args,
                        "tool_result_size": len(obs),
                        "success": tool_success,
                        "from_cache": from_cache,
                        "retries": retries,
                    },
                    "error": None if tool_success else {"type": "tool_execution_error",
                                                         "message": tool_result.get("error", "")},
                })

            obs_msg = (f"观察 {step + 1}: {tool_name} "
                       f"{'成功返回' if tool_success else '执行失败'}: {obs[:500]}")
            observations.append({"step": step + 1, "tool": tool_name, "result": obs})
            messages.append({"role": "user", "content": obs_msg})
            steps.append({
                "step": step + 1,
                "action": action,
                "observation": obs,
            })

        # 保存 assistant 回复到 session 历史（仅一次）
        with self._session_lock:
            if completed and final_answer:
                messages.append({"role": "assistant", "content": final_answer})
            else:
                # 循环耗尽，不写入无意义消息
                pass

        # 更新热记忆（无论有无工具调用都写入）
        total_elapsed = round(time.time() - start_time, 3)
        if final_answer:
            summary = (f"[{session_id}] 推理完成({len(steps)}步): "
                       f"{final_answer[:200]}")
        elif observations:
            summary = (f"[{session_id}] 推理未完成({len(steps)}步)，"
                       f"观察: {observations[-1].get('result', '')[:200]}")
        else:
            # 无工具调用的简单对话也记录
            summary = (f"[{session_id}] 简单对话: {user_message[:100]}")
        self.hot_memory.append(summary)

        # 主动下刷：热记忆溢出 → 温记忆 + 冷记忆存档
        if self.warm_memory is not None:
            stats = self.hot_memory.get_stats()
            usage_ratio = stats.get("current_size", 0) / max(stats.get("max_size", 1), 1)
            if usage_ratio > 0.7:
                evicted = self.hot_memory.evict_expired()
                if evicted > 0:
                    logger.debug(f"热记忆下刷 {evicted} 条到温记忆 (使用率 {usage_ratio:.0%})")

        # 冷记忆写入：有结果就写入（不限于 completed 状态）
        if self.cold_memory is not None:
            try:
                cold_content = final_answer or (observations[-1].get("result", "") if observations else user_message[:200])
                cold_summary = f"[{session_id}] {len(steps)}步: {cold_content[:200]}"
                self.cold_memory.insert_summary(
                    content=cold_content,
                    summary=cold_summary,
                    session_id=session_id,
                )
            except Exception as e:
                logger.debug(f"冷记忆写入失败: {e}")

        # 日志: DAG 结束
        if llm_logger:
            llm_logger.log({
                "trace_id": trace_id,
                "node_id": "dag-end",
                "node_type": "dag_end",
                "status": "success" if completed else "failure",
                "duration_ms": int(total_elapsed * 1000),
                "data": {
                    "session_id": session_id,
                    "total_steps": len(steps),
                    "completed": completed,
                    "tools_used": [s["action"]["tool"] for s in steps],
                },
                "error": None if completed else {"type": "incomplete", "message": "ReAct 循环耗尽或未完成"},
            })

        # v0.2.0: Selector 反馈
        if self._selector is not None:
            try:
                self._selector.record_outcome(
                    result={
                        "success": completed,
                        "completed": completed,
                        "steps": steps,
                        "total_steps": len(steps),
                        "elapsed": total_elapsed,
                        "usage": llm_result.get("usage", {}) if llm_result else {},
                        "error": "",
                    },
                    user_message=user_message,
                    session_id=session_id,
                    trace_id=trace_id,
                )
            except Exception as e:
                logger.warning(f"Selector record_outcome failed: {e}")

        return {
            "success": completed,
            "content": final_answer or "推理未完成或未得到明确结论",
            "completed": completed,
            "steps": steps,
            "observations": observations,
            "elapsed": total_elapsed,
            "total_steps": len(steps),
            "trace_id": trace_id,
            "usage": llm_result.get("usage", {}) if llm_result else {},
        }

    def stream_run(self, user_message: str, session_id: str = "default"):
        """流式运行 - 逐步 yield 内容块（v0.2.0: 集成 Selector）
        
        每条流式消息也使用 Selector 刷新 system prompt。
        """
        start_time = time.time()
        trace_id = make_trace_id()

        # Selector 选择决策
        advice = None
        if self._selector is not None:
            try:
                advice = self._selector.select(
                    user_message=user_message,
                    session_id=session_id,
                    available_tools=self.tool_registry.list_tools(),
                    default_steps=self.max_steps,
                    is_new_message=True,
                )
            except Exception as e:
                logger.warning(f"Selector select failed (stream): {e}")

        with self._session_lock:
            fresh_system_prompt = self._get_system_prompt(advice=advice)
            if session_id in self._session_history:
                existing = self._session_history[session_id]
                if existing and existing[0]["role"] == "system":
                    existing[0]["content"] = fresh_system_prompt
                else:
                    existing.insert(0, {"role": "system", "content": fresh_system_prompt})
            else:
                self._session_history[session_id] = [
                    {"role": "system", "content": fresh_system_prompt},
                ]
            messages = self._session_history[session_id]
            messages = self._trim_history(messages)
            self._session_history[session_id] = messages
            messages.append({"role": "user", "content": user_message})

        final_answer = None
        completed = False

        for step in range(self.max_steps):
            try:
                stream = self.llm.stream_complete(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2048,
                )

                full_content = ""
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    token = delta.content or ""
                    if token:
                        full_content += token
                        yield {"type": "token", "content": token}

                # 流结束，处理完整内容
                content = self._strip_think_tags(full_content)
                messages.append({"role": "assistant", "content": content})

                # 尝试解析最终答案
                final_match = parse_final(content)
                if final_match:
                    final_answer = final_match
                    completed = True
                    break

                # 尝试解析工具调用
                action = parse_action(content)
                if action is None:
                    # 无法解析，添加提示继续
                    messages.append({"role": "user", "content": "请使用 Action: tool_name({...}) 调用工具，或使用 Final: 给出最终答案。"})
                    continue

                # 执行工具（通过安全执行器）
                tool_name = action.get("tool")
                args = action.get("arguments", {})
                yield {"type": "tool_call", "tool": tool_name, "arguments": args}

                self._tool_executor.start_round(session_id, step)
                tool_result = self._tool_executor.invoke(
                    tool_name=tool_name,
                    arguments_json=args,
                    session_id=session_id,
                    step=step,
                    bypass_cache=(tool_name in ("write_file", "write_json", "memory_write")),
                )
                obs = tool_result.get("result_json", tool_result.get("error", ""))
                if tool_result.get("success"):
                    obs_msg = f"观察 {step + 1}: {tool_name} 成功返回: {obs[:500]}"
                else:
                    obs_msg = f"观察 {step + 1}: {tool_name} 执行失败: {obs[:500]}"

                messages.append({"role": "user", "content": obs_msg})
                yield {"type": "observation", "content": obs_msg[:200]}

            except Exception as e:
                yield {"type": "error", "content": str(e)}
                break

        # 保存结果
        with self._session_lock:
            if completed and final_answer:
                messages.append({"role": "assistant", "content": final_answer})

        # 记忆写入（与 run() 对称）
        if final_answer:
            self.hot_memory.append(
                f"[{session_id}] 流式对话: {final_answer[:200]}")
        if self.cold_memory is not None and final_answer:
            try:
                self.cold_memory.insert_summary(
                    content=final_answer,
                    summary=f"[{session_id}] 流式: {final_answer[:200]}",
                    session_id=session_id,
                )
            except Exception as e:
                logger.debug(f"流式冷记忆写入失败: {e}")

        # Selector 反馈
        if self._selector is not None and self._last_advice:
            try:
                self._selector.record_outcome(
                    result={"success": completed, "completed": completed,
                            "steps": [], "total_steps": 0,
                            "elapsed": round(time.time() - start_time, 3),
                            "usage": {}, "error": ""},
                    user_message=user_message,
                    session_id=session_id,
                    trace_id=trace_id,
                )
            except Exception as e:
                logger.warning(f"Selector stream record failed: {e}")

        result_content = final_answer or "推理未完成或未得到明确结论"
        yield {
            "type": "done",
            "content": result_content,
            "success": completed,
            "elapsed": round(time.time() - start_time, 3),
        }
