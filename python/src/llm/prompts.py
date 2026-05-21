"""伏羲提示词模板 - v0.3.0: 与引擎解析器统一

注意：引擎实际使用 fuxi_engine._get_system_prompt() 动态生成提示词，
此文件仅保留工具函数供外部使用。
"""


TOOL_RESULT_TEMPLATE = """观察 {step}: {tool_name} 返回:
{result}
"""


MEMORY_SUMMARY_PROMPT = """你是一个记忆摘要助手。请将以下对话内容压缩为一段简洁的记忆摘要（不超过200字）。

【对话】
{dialogue}

【要求】
- 提取关键事实、结论、决定
- 使用简洁的中文
- 保留最重要信息
- 不超过200字

【摘要格式】
[主题] 关键内容1 | 关键内容2 | ...
"""


REACT_INSTRUCTION = """
【ReAct 执行规则】
1. 每一步用 Action: tool_name({"key":"value"}) 调用工具
2. 工具返回后根据观察决定下一步
3. 问题解决后输出 Final: <答案>
4. 不要重复调用同一个工具超过2次
5. 如果工具返回错误，尝试换一种方法
6. Final: 优先级高于 Action - 如果同时输出两者，以 Final 为准
"""
