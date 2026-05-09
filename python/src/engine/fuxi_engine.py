"""伏羲核心引擎 - ReAct 循环"""
import sys
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(CURRENT_DIR), '..'))

import re
import time
import threading
import json
from typing import Dict, Any, List, Optional

import tools
import tools.file_tools
from tools import registry
from llm.client import DeepSeekClient
from memory.hot_memory import HotMemory

# 最大历史消息数（防止超过 context window）
MAX_HISTORY_MESSAGES = 40


class FuxiEngine:
    """伏羲引擎：ReAct + 工具调度 + 记忆管理"""

    def __init__(
        self,
        deepseek_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_steps: int = 10,
    ):
        self.deepseek = DeepSeekClient(
            api_key=deepseek_key,
            base_url=base_url,
        )
        self.hot_memory = HotMemory()
        self.tool_registry = registry
        self.max_steps = max_steps
        self._session_history: Dict[str, List[Dict[str, str]]] = {}
        self._session_lock = threading.Lock()

    def _get_system_prompt(self, context: str = "") -> str:
        """获取系统提示词"""
        hot = self.hot_memory.read()
        memory_context = hot.get("memory_content", "")

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

【输出格式】
你必须严格使用以下格式，禁止输出除格式外的任何解释性文字：

## 工具调用（需要执行工具时）
Action: tool_name({{"param": "value"}})

## 最终答案（问题已解决，不需要更多工具）
Final: <直接给出答案>

注意：不要在 Final 前加任何 Thinking 或解释。只使用 Final: 作为最终答案标记。
"""
        return system

    def _trim_history(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """裁剪历史消息，防止超过 context window"""
        if len(messages) <= MAX_HISTORY_MESSAGES:
            return messages
        # 保留 system prompt（第一条）和最近的消息
        system_msg = messages[0]
        recent_messages = messages[-(MAX_HISTORY_MESSAGES - 1):]
        return [system_msg] + recent_messages

    def run(self, user_message: str, session_id: str = "default") -> Dict[str, Any]:
        """运行真正的 ReAct 循环"""
        start_time = time.time()

        with self._session_lock:
            # 构建消息历史（包含观察反馈）
            if session_id not in self._session_history:
                self._session_history[session_id] = [
                    {"role": "system", "content": self._get_system_prompt()},
                ]
            messages = self._session_history[session_id]
            # 裁剪历史
            messages = self._trim_history(messages)
            self._session_history[session_id] = messages
            messages.append({"role": "user", "content": user_message})

        steps = []
        observations = []
        final_answer = None
        completed = False
        llm_result = None

        for step in range(self.max_steps):
            # 调用 LLM
            llm_result = self.deepseek.complete(
                messages=messages,
                temperature=0.2,  # 降低温度，提高格式一致性
                max_tokens=2048,
            )

            if not llm_result["success"]:
                # LLM 调用失败，回滚 user 消息
                with self._session_lock:
                    if messages and messages[-1]["role"] == "user":
                        messages.pop()
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
                    completed = True
                    break
                # 无法解析，添加提示后继续
                messages.append({"role": "user", "content": "请使用 Action: tool_name({...}) 调用工具，或使用 Final: 给出最终答案。"})
                continue

            # 执行工具
            tool_name = action.get("tool")
            args = action.get("arguments", {})

            try:
                tool_result = self.tool_registry.invoke(tool_name, json.dumps(args))
                obs = tool_result.get("result_json", tool_result.get("error", ""))
                # 区分成功和失败
                if tool_result.get("success"):
                    obs_msg = f"观察 {step + 1}: {tool_name} 成功返回: {obs[:500]}"
                else:
                    obs_msg = f"观察 {step + 1}: {tool_name} 执行失败: {obs[:500]}"
            except Exception as e:
                obs_msg = f"观察 {step + 1}: {tool_name} 执行异常: {str(e)}"
                tool_result = {"success": False, "error": str(e)}

            observations.append({"step": step + 1, "tool": tool_name, "result": obs})

            # 将观察结果反馈给 LLM
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

        # 更新热记忆
        if observations:
            summary = f"[{session_id}] 完成了 {len(steps)} 步推理，最终: {final_answer or '无结论'}"
            self.hot_memory.append(summary)

        return {
            "success": completed,
            "content": final_answer or "推理未完成或未得到明确结论",
            "completed": completed,
            "steps": steps,
            "observations": observations,
            "elapsed": round(time.time() - start_time, 3),
            "total_steps": len(steps),
            "usage": llm_result.get("usage", {}) if llm_result else {},
        }

    def _parse_one_action(self, content: str) -> Optional[Dict[str, Any]]:
        """解析一个工具调用（支持嵌套 JSON）"""
        # 匹配 Action: tool_name({...})  支持多种格式
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
                    # 尝试修复常见格式问题（如单引号）
                    try:
                        # 替换单引号为双引号
                        fixed = args_str.replace("'", '"')
                        args = json.loads(fixed)
                        return {"tool": tool_name, "arguments": args}
                    except json.JSONDecodeError:
                        continue
        return None

    def _parse_final(self, content: str) -> Optional[str]:
        """解析最终答案"""
        patterns = [
            r'Final:\s*(.+)',
            r'最终答案:\s*(.+)',
            r'最终:\s*(.+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                return match.group(1).strip()
        return None