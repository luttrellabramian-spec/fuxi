"""工具调用与热记忆淘汰的反馈回调（v0.2.6: 从 fuxi_engine.py 拆出）

这些回调把工具调用结果/记忆淘汰事件喂给追踪器和进化层。
单独抽出是为了让 fuxi_engine.py 不至于既负责 ReAct 主循环又负责副作用记账。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("fuxi_engine_feedback")


def on_tool_invoked(
    engine,
    tool_name: str,
    success: bool,
    result: Dict[str, Any],
) -> None:
    """工具调用完成后的副作用：写 tracker + 行为进化。

    任何子回调失败应记日志但不抛 — 这是 best-effort 记账。
    """
    # 1. tool_tracker
    if engine._tool_tracker is not None:
        try:
            engine._tool_tracker.record(
                session_id=getattr(engine, "_current_session_id", None) or "default",
                tool_name=tool_name,
                success=success,
                latency_ms=result.get("elapsed_ms", 0),
                error_type="none" if success else "tool_execution_error",
                error_message=result.get("error", ""),
            )
        except Exception as e:
            logger.warning(f"tool_tracker.record failed: {e}")

    # 2. behavior_evolution
    if engine._behavior_evolution is not None:
        try:
            engine._behavior_evolution.record_tool_result(
                tool_name=tool_name,
                success=success,
                latency_ms=result.get("elapsed_ms", 0),
                was_timeout=("timeout" in str(result.get("error", "")).lower()),
            )
        except Exception as e:
            logger.warning(f"behavior_evolution.record_tool_result failed: {e}")


def on_hot_evict(engine, key: str, value: str) -> None:
    """热记忆淘汰事件：把被淘汰的条目下刷到温记忆。"""
    if engine.warm_memory is None:
        return
    try:
        session_id = key.split("_")[1] if "_" in key else "default"
        engine.warm_memory.add_message(
            session_id=session_id,
            content=value,
        )
    except Exception as e:
        logger.warning(f"_on_hot_evict add_message failed (key={key}): {e}")
