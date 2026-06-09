from __future__ import annotations

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
from engine.response_parser import (
    fix_json, parse_action, parse_final, strip_think_tags,  # noqa: F401
)
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
        """v0.2.6 (H2): 委托给 engine.feedback.on_tool_invoked"""
        from .feedback import on_tool_invoked as _impl
        _impl(self, tool_name, success, result)

    def _on_hot_evict(self, key: str, value: str) -> None:
        """v0.2.6 (H2): 委托给 engine.feedback.on_hot_evict"""
        from .feedback import on_hot_evict as _impl
        _impl(self, key, value)

    def _strip_think_tags(self, text: str) -> str:
        """兼容 shim — 实际剥离逻辑在 response_parser.strip_think_tags。
        本方法已无调用者（v0.2.7 起新代码统一调模块函数），保留仅为老测试兼容；
        新代码不应再使用本方法。"""
        from .response_parser import strip_think_tags
        return strip_think_tags(text)

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
        """v0.2.6 (H2): 委托给 engine.run_sync.run_sync"""
        from .run_sync import run_sync as _impl
        return _impl(self, user_message, session_id)

    def stream_run(self, user_message: str, session_id: str = "default", llm: Optional[LLMClient] = None):
        """v0.2.6 (H2): 委托给 engine.run_stream.stream_run"""
        from .run_stream import stream_run as _impl
        yield from _impl(self, user_message, session_id, llm)
