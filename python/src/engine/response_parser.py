"""引擎响应解析 — 从 LLM 输出中提取 Action / Final

从 fuxi_engine.py 抽出，便于单独测试和复用。

- `strip_think_tags`: 剥离 <think>...</think> 块（含嵌套/未闭合）
- `fix_json`: 容忍 4 种常见 LLM JSON 输出错误
- `parse_action`: 从 LLM 输出中抽取单个工具调用（含 Action: 和 行动:）
- `parse_final`: 从 LLM 输出中抽取最终答案（含 Final: / 最终答案: / 最终:）
"""
import ast
import json
import re
from typing import Any, Dict, Optional

# 工具名正则（支持连字符和点号）
TOOL_NAME_PATTERN = r'([\w.-]+)'


def strip_think_tags(text: str) -> str:
    """剥离 <think>...</think> 块及其内容。

    处理策略（按顺序）：
    1. 移除完整闭合的 <think>...</think> 块（多行、非贪婪）
    2. 移除未闭合的 <think>...（到 Final:/最终答案:/最终: 标记停止；不匹配则保留）
    3. 移除孤立的 <think> 或 </think> 标签
    4. 规范化连续空白（>=3 个 \\n 折叠为 2 个）
    """
    if not text:
        return ""

    # 1. 完整闭合块
    cleaned = re.sub(r'<think>[\s\S]*?</think>\s*', '', text)

    # 2. 未闭合块 — 必须在 Final 标记或文本末尾前停止
    # 关键：使用 lookahead 而非 [\\s\\S]*?(?=Final:|$)
    # 如果 [\\s\\S]*? 贪婪到末尾，结果就是空串 — 但这没问题，因为
    # 未闭合 think 块之后的内容没价值，保留只会污染 Final 答案
    if "<think>" in cleaned:
        # 看是否有 Final 标记
        for marker in ("Final:", "最终答案:", "最终:"):
            if marker in cleaned:
                # 找到 marker，把 marker 之前的所有内容（包括未闭合 <think>）都去掉
                idx = cleaned.index(marker)
                cleaned = cleaned[idx:]
                break
        else:
            # 没有 Final 标记 — 把未闭合 think 到末尾都去掉
            idx = cleaned.find("<think>")
            cleaned = cleaned[:idx]

    # 3. 孤立的标签（防御性，正常情况已被前两步覆盖）
    cleaned = re.sub(r'</?think>', '', cleaned)

    # 4. 规范化空白
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    return cleaned.strip()



def fix_json(raw: str) -> Optional[str]:
    """尝试修复 LLM 输出的非标准 JSON，按 4 种策略依次尝试：

    1. 直接解析
    2. 单引号→双引号
    3. ast.literal_eval（处理 Python True/None/False）
    4. 移除尾随逗号 + 注释
    """
    # 策略1: 直接解析
    try:
        json.loads(raw)
        return raw
    except json.JSONDecodeError:
        pass

    # 策略2: 单引号→双引号
    try:
        fixed = raw.replace("'", '"')
        fixed = fixed.replace("True", "true").replace("False", "false").replace("None", "null")
        json.loads(fixed)
        return fixed
    except json.JSONDecodeError:
        pass

    # 策略3: ast.literal_eval 处理 Python 语法
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            return json.dumps(parsed)
    except (ValueError, SyntaxError):
        pass

    # 策略4: 移除尾随逗号和注释
    try:
        cleaned = re.sub(r'//[^\n]*', '', raw)
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        cleaned = cleaned.replace("'", '"')
        cleaned = cleaned.replace("True", "true").replace("False", "false").replace("None", "null")
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    return None


def parse_action(content: str) -> Optional[Dict[str, Any]]:
    """解析一个工具调用（支持连字符工具名、Python 语法参数）"""
    patterns = [
        rf'Action:\s*{TOOL_NAME_PATTERN}\s*\(\s*(\{{.*\}})\s*\)',
        rf'行动:\s*{TOOL_NAME_PATTERN}\s*\(\s*(\{{.*\}})\s*\)',
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            tool_name = match.group(1)
            args_str = match.group(2)
            fixed = fix_json(args_str)
            if fixed is not None:
                try:
                    args = json.loads(fixed)
                    return {"tool": tool_name, "arguments": args}
                except json.JSONDecodeError:
                    continue
    return None


def parse_final(content: str) -> Optional[str]:
    """解析最终答案（支持空内容 Final:）"""
    patterns = [
        r'Final:\s*(.*)',
        r'最终答案:\s*(.*)',
        r'最终:\s*(.*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            result = match.group(1).strip()
            return result if result else "(空)"
    return None
