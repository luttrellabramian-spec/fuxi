"""伏羲核心引擎 - ReAct 循环"""
import sys
import os
import ast

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(CURRENT_DIR), '..'))

import time
import threading
import json
from typing import Dict, Any, List, Optional

import tools
import tools.file_tools
from tools import registry
from llm.client import LLMClient
from memory.hot_memory import HotMemory


class FuxiEngine:
    """伏羲引擎：ReAct + 工具调度 + 记忆管理"""

    def __init__(
        self,
        deepseek_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_steps: int = 10,
    ):
        self.llm = LLMClient(
            api_key=deepseek_key,
            base_url=base_url,
        )
        self.hot_memory = HotMemory()
        self.tool_registry = registry
        self.max_steps = max_steps
        self._session_history: Dict[str, List[Dict[str, str]]] = {}

    def _get_system_prompt(self, context: str = "") -> str:
        """获取系统提示词"""
        hot = self.hot_memory.read()
        memory_context = hot.get("memory_content", "")
        tools_list = list(self.tool_registry.list_tools().keys())

        # 添加工具描述
        tool_descs = []
        for name, info in self.tool_registry.list_tools().items():
            tool_descs.append(f"- {name}: {info.get('doc', 'no description')}")

        system = f"""你是一个高效的 AI 助手（伏羲引擎），擅长工具调用和问题解决。

【热记忆】
{memory_context[:800] if memory_context else "无"}

【可用工具】
{chr(10).join(tool_descs[:15])}

【工作模式】
使用 ReAct 模式：Thought → Action → Observation，循环最多 {self.max_steps} 次。

【输出格式】禁止解释，直接格式输出：

工具调用：
Thought: <思考>
Action: tool_name({{"param": "value"}})

最终答案：
Answer: <答案>

【输出格式】
你必须严格使用以下格式，禁止输出除格式外的任何解释性文字：

## 工具调用（需要执行工具时）
Action: tool_name({{"param": "value"}})

## 最终答案（问题已解决，不需要更多工具）
Final: <直接给出答案，什么前缀都不要加>

注意：不要在 Final 前加任何 Thinking 或解释。
"""
        return system

    def run(self, user_message: str, session_id: str = "default", history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """运行真正的 ReAct 循环
        Args:
            user_message: 用户消息
            session_id: 会话 ID
            history: 可选的对话历史列表，用于初始化上下文
        """
        start_time = time.time()

        if session_id not in self._session_history:
            self._session_history[session_id] = [
                {"role": "system", "content": self._get_system_prompt()},
            ]
            if history:
                for h in history:
                    if isinstance(h, dict) and "role" in h and "content" in h:
                        self._session_history[session_id].append(h)

        messages = self._session_history[session_id]
        messages.append({"role": "user", "content": user_message})

        steps = []
        observations = []
        final_answer = None

        for step in range(self.max_steps):
            # 调用 LLM
            llm_result = self.llm.complete(
                messages=messages,
                temperature=0.7,
                max_tokens=2048,
            )

            if not llm_result["success"]:
                return {
                    "success": False,
                    "error": llm_result.get("error", "Unknown error"),
                    "elapsed": time.time() - start_time,
                    "steps": step,
                }

            content = llm_result["content"]
            messages.append({"role": "assistant", "content": content})

            # 解析动作
            action = self._parse_one_action(content)
            if action is None:
                # 没有动作，检查是否是最终答案
                final_match = self._parse_final(content)
                if final_match:
                    final_answer = final_match
                    break
                # 无法解析，继续尝试
                continue

            # 执行工具
            tool_name = action.get("tool")
            args = action.get("arguments", {})

            tool_result = self.tool_registry.invoke(tool_name, json.dumps(args))
            obs = tool_result.get("result_json", tool_result.get("error", ""))
            observations.append({"step": step + 1, "tool": tool_name, "result": obs})

            # 将观察结果反馈给 LLM
            obs_message = f"观察 {step + 1}: {tool_name} 返回: {obs[:500]}"
            messages.append({"role": "user", "content": obs_message})
            steps.append({
                "step": step + 1,
                "action": action,
                "observation": obs,
            })

            # 检查是否是最终答案（在观察后）
            # 如果工具返回了明确的答案或信息，可能足够回答了
            # 但 ReAct 模式要求显式 Final 标记

        # 保存 assistant 回复到 session 历史
        messages.append({"role": "assistant", "content": final_answer or "推理未完成或未得到明确结论"})

        # 更新热记忆
        if observations:
            summary = f"[{session_id}] 完成了 {len(steps)} 步推理，最终: {final_answer or '无结论'}"
            self.hot_memory.append(summary)

        return {
            "success": True,
            "content": final_answer or "推理未完成或未得到明确结论",
            "steps": steps,
            "observations": observations,
            "elapsed": round(time.time() - start_time, 3),
            "total_steps": len(steps),
            "usage": llm_result.get("usage", {}),
        }

    def _parse_one_action(self, content: str) -> Optional[Dict[str, Any]]:
        """解析一个工具调用"""
        import re

        patterns = [
            r'Action:\s*(\w+)\s*\(\s*(\{.*\})\s*\)',
            r'行动:\s*(\w+)\s*\(\s*(\{.*\})\s*\)',
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                tool_name = match.group(1)
                args_str = match.group(2)
                try:
                    args = json.loads(args_str)
                    return {"tool": tool_name, "arguments": args}
                except json.JSONDecodeError:
                    try:
                        args = ast.literal_eval(args_str)
                        return {"tool": tool_name, "arguments": args}
                    except (ValueError, SyntaxError):
                        continue
        return None

    def _parse_final(self, content: str) -> Optional[str]:
        """解析最终答案"""
        import re
        patterns = [
            r'Answer:\s*(.+)',
            r'Final:\s*(.+)',
            r'最终答案:\s*(.+)',
            r'最终:\s*(.+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                return match.group(1).strip()
        return None