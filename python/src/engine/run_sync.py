"""同步 ReAct 循环（v0.2.6 从 fuxi_engine.py 拆出）

`FuxiEngine.run()` 的实际主循环体在这里。
fuxi_engine.py 保留一个同名方法作为 thin delegate。

要点：
- 通过参数 `engine` 访问 FuxiEngine 全部属性
- 不修改类结构（向后兼容已有测试）
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, Optional

from engine.execution_logger import make_trace_id
from engine.response_parser import strip_think_tags, parse_action, parse_final

# 来自 fuxi_engine.py 的常量
MAX_BAD_OUTPUTS = 3
MAX_EMPTY_OUTPUTS = 3

logger = logging.getLogger("fuxi_run_sync")


def run_sync(engine, user_message: str, session_id: str = "default") -> Dict[str, Any]:
    """运行真正的 ReAct 循环（v0.3.0: 循环保护 + 安全网）"""
    engine._current_session_id = session_id
    start_time = time.time()
    trace_id = make_trace_id()
    llm_logger = engine._execution_logger

    # 会话数上限保障
    engine._enforce_session_limit()

    # 每次消息都执行完整的 Selector 选择决策
    advice = None
    if engine._selector is not None:
        try:
            advice = engine._selector.select(
                user_message=user_message,
                session_id=session_id,
                available_tools=engine.tool_registry.list_tools(),
                default_steps=engine.max_steps,
                is_new_message=True,
            )
            engine._last_advice = advice
        except Exception as e:
            logger.warning(f"Selector select failed: {e}")

    # 确定本次运行的有效步数
    effective_steps = engine.max_steps
    if advice:
        rec = advice.get("strategy", {}).get("recommend_steps")
        if rec:
            effective_steps = min(rec, 15)

    with engine._session_lock:
        # 每次消息都重建 system prompt
        fresh_system_prompt = engine._get_system_prompt(advice=advice)
        if session_id in engine._session_history:
            existing = engine._session_history[session_id]
            if existing and existing[0]["role"] == "system":
                existing[0]["content"] = fresh_system_prompt
            else:
                existing.insert(0, {"role": "system", "content": fresh_system_prompt})
        else:
            engine._session_history[session_id] = [
                {"role": "system", "content": fresh_system_prompt},
            ]
        # 更新访问时间
        engine._session_access[session_id] = time.time()
        engine._session_access.move_to_end(session_id)

        messages = engine._session_history[session_id]
        messages = engine._trim_history(messages)
        engine._session_history[session_id] = messages
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
        messages = engine._trim_history(messages)

        llm_start = time.time()
        llm_result = engine.llm.complete(
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
            with engine._session_lock:
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
        content = strip_think_tags(content)
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
                    "model": llm_result.get("model", engine.llm.model or ""),
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
        engine._tool_executor.start_round(session_id, step)

        tool_result = engine._tool_executor.invoke(
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
    with engine._session_lock:
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
    engine.hot_memory.append(summary)

    # 主动下刷：热记忆溢出 → 温记忆 + 冷记忆存档
    if engine.warm_memory is not None:
        stats = engine.hot_memory.get_stats()
        usage_ratio = stats.get("current_size", 0) / max(stats.get("max_size", 1), 1)
        if usage_ratio > 0.7:
            evicted = engine.hot_memory.evict_expired()
            if evicted > 0:
                logger.debug(f"热记忆下刷 {evicted} 条到温记忆 (使用率 {usage_ratio:.0%})")

    # 冷记忆写入：有结果就写入（不限于 completed 状态）
    if engine.cold_memory is not None:
        try:
            cold_content = final_answer or (observations[-1].get("result", "") if observations else user_message[:200])
            cold_summary = f"[{session_id}] {len(steps)}步: {cold_content[:200]}"
            engine.cold_memory.insert_summary(
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
    if engine._selector is not None:
        try:
            engine._selector.record_outcome(
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
