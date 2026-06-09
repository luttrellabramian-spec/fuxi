"""流式 ReAct 循环（v0.2.6 从 fuxi_engine.py 拆出）

`FuxiEngine.stream_run()` 的实际主循环体在这里。
fuxi_engine.py 保留一个同名方法作为 thin delegate。
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, Optional

from llm.client import LLMClient
from engine.response_parser import strip_think_tags, parse_action, parse_final

logger = logging.getLogger("fuxi_run_stream")


def stream_run(engine, user_message: str, session_id: str = "default", llm: Optional[LLMClient] = None):
    """流式运行 - 逐步 yield 内容块（v0.2.0: 集成 Selector）

    每条流式消息也使用 Selector 刷新 system prompt。

    Args:
        engine: FuxiEngine 实例
        user_message: 用户消息
        session_id: 会话 ID
        llm: v0.2.6 起支持 — 可选的 LLM 客户端；不传则用 engine.llm
    """
    start_time = time.time()
    trace_id = ""  # stream_run 不直接用 trace_id，留空兼容

    # Selector 选择决策
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
        except Exception as e:
            logger.warning(f"Selector select failed (stream): {e}")

    with engine._session_lock:
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
        messages = engine._session_history[session_id]
        messages = engine._trim_history(messages)
        engine._session_history[session_id] = messages
        messages.append({"role": "user", "content": user_message})

    final_answer = None
    completed = False

    for step in range(engine.max_steps):
        try:
            active_llm = llm if llm is not None else engine.llm
            stream = active_llm.stream_complete(
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
            content = strip_think_tags(full_content)
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

            engine._tool_executor.start_round(session_id, step)
            tool_result = engine._tool_executor.invoke(
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
    with engine._session_lock:
        if completed and final_answer:
            messages.append({"role": "assistant", "content": final_answer})

    # 记忆写入（与 run() 对称）
    if final_answer:
        engine.hot_memory.append(
            f"[{session_id}] 流式对话: {final_answer[:200]}")
    if engine.cold_memory is not None and final_answer:
        try:
            engine.cold_memory.insert_summary(
                content=final_answer,
                summary=f"[{session_id}] 流式: {final_answer[:200]}",
                session_id=session_id,
            )
        except Exception as e:
            logger.debug(f"流式冷记忆写入失败: {e}")

    # Selector 反馈
    if engine._selector is not None and engine._last_advice:
        try:
            engine._selector.record_outcome(
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
