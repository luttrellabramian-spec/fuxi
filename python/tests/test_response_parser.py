"""伏羲 响应解析器测试 — strip_think_tags / fix_json / parse_action / parse_final"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from engine.response_parser import (
    strip_think_tags, fix_json, parse_action, parse_final, TOOL_NAME_PATTERN,
)


# ════════════════════════════════════════════════════════════════════
# 1. strip_think_tags
# ════════════════════════════════════════════════════════════════════


class TestStripThinkTags:
    def test_empty_input(self):
        """空输入应返回空字符串。"""
        assert strip_think_tags("") == ""

    def test_none_input(self):
        """None 输入应返回空字符串。"""
        assert strip_think_tags(None) == ""  # type: ignore

    def test_no_think_tags(self):
        """没有 think 标签应原样返回。"""
        text = "Hello world, no thinking here."
        assert strip_think_tags(text) == text

    def test_complete_think_block(self):
        """完整闭合的 <think>...</think> 应被移除。"""
        text = "<think>让我想想</think>Final: 答案是 42"
        result = strip_think_tags(text)
        assert "<think>" not in result
        assert "</think>" not in result
        assert "让我想想" not in result
        assert "答案是 42" in result

    def test_multiline_think_block(self):
        """多行 think 块应被完整移除。"""
        text = """<think>
让我想想这个问题
需要考虑以下因素
1. 第一个
2. 第二个
</think>
Final: 答案"""
        result = strip_think_tags(text)
        assert "让我想想" not in result
        assert "答案" in result

    def test_multiple_think_blocks(self):
        """多个 think 块都应被移除。"""
        text = "<think>第一段</think>中间<think>第二段</think>末尾"
        result = strip_think_tags(text)
        assert "第一段" not in result
        assert "第二段" not in result
        assert "中间" in result
        assert "末尾" in result

    def test_unclosed_think_block(self):
        """未闭合的 think 应截到 Final: 之前。"""
        text = "<think>思考中...Final: 答案"
        result = strip_think_tags(text)
        assert "思考中" not in result
        assert "答案" in result

    def test_stray_closing_tag(self):
        """孤立的 </think> 应被移除。"""
        text = "前面</think>后面"
        result = strip_think_tags(text)
        assert "</think>" not in result
        assert "前面" in result
        assert "后面" in result

    def test_stray_opening_tag(self):
        """孤立的 <think>（无闭合）应截断到 <think> 处。"""
        text = "前面<think>后面"
        result = strip_think_tags(text)
        assert "<think>" not in result
        # 前面 标签本身保留
        assert "前面" in result
        # 未闭合 think 后的内容被丢弃（不可信）
        assert "后面" not in result

    def test_collapses_excessive_whitespace(self):
        """>=3 个连续换行应折叠为 2 个。"""
        text = "A\n\n\n\n\nB"
        result = strip_think_tags(text)
        assert result == "A\n\nB"

    def test_real_llm_output_minimax(self):
        """真实 MiniMax 模型输出（含 <think> 和重复 Final）。"""
        text = """<think>用户问好，我应该回复</think>\n\nFinal: 你好！\n\nFinal: 你好！"""
        result = strip_think_tags(text)
        # think 块应被移除
        assert "<think>" not in result
        # 应包含至少一个 Final 内容
        assert "你好" in result


# ════════════════════════════════════════════════════════════════════
# 2. fix_json
# ════════════════════════════════════════════════════════════════════


class TestFixJson:
    def test_valid_json_unchanged(self):
        """标准 JSON 应原样返回。"""
        assert fix_json('{"a": 1, "b": "hi"}') == '{"a": 1, "b": "hi"}'

    def test_single_quotes_to_double(self):
        """单引号 JSON 应被修复。"""
        assert fix_json("{'a': 1}") == '{"a": 1}'

    def test_python_literals_replaced(self):
        """Python 字面量 True/False/None 应被替换。"""
        result = fix_json("{'flag': True, 'data': None}")
        assert "true" in result
        assert "null" in result

    def test_unfixable_returns_none(self):
        """无法修复的 JSON 应返回 None。"""
        assert fix_json("{definitely not json") is None

    def test_empty_string_returns_none(self):
        """空字符串应返回 None。"""
        assert fix_json("") is None


# ════════════════════════════════════════════════════════════════════
# 3. parse_action
# ════════════════════════════════════════════════════════════════════


class TestParseAction:
    def test_simple_action(self):
        """标准 Action 格式应被解析。"""
        content = "Action: read_file({\"path\": \"/tmp/test\"})"
        result = parse_action(content)
        assert result == {"tool": "read_file", "arguments": {"path": "/tmp/test"}}

    def test_action_in_chinese(self):
        """中文 '行动:' 也应支持。"""
        content = "行动: read_file({\"path\": \"/tmp/test\"})"
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "read_file"

    def test_tool_name_with_dash(self):
        """工具名带连字符应被支持。"""
        content = "Action: web-search({\"query\": \"test\"})"
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "web-search"

    def test_no_action_returns_none(self):
        """无 Action 时应返回 None。"""
        assert parse_action("Final: just an answer") is None

    def test_invalid_json_returns_none(self):
        """Action 后跟无效 JSON 应返回 None。"""
        assert parse_action("Action: read_file({not valid}") is None


# ════════════════════════════════════════════════════════════════════
# 4. parse_final
# ════════════════════════════════════════════════════════════════════


class TestParseFinal:
    def test_basic_final(self):
        """标准 Final 应被解析。"""
        result = parse_final("Final: The answer is 42")
        assert result == "The answer is 42"

    def test_chinese_final(self):
        """中文 最终答案: 应被支持。"""
        assert parse_final("最终答案: 答案是 42") == "答案是 42"

    def test_short_chinese_final(self):
        """中文 最终: 应被支持。"""
        assert parse_final("最终: 好的") == "好的"

    def test_empty_final_returns_placeholder(self):
        """空 Final 应返回占位符。"""
        assert parse_final("Final: ") == "(空)"

    def test_no_final_returns_none(self):
        """无 Final 标记应返回 None。"""
        assert parse_final("just some text") is None

    def test_multiline_final(self):
        """多行 Final 应被完整保留。"""
        result = parse_final("Final: line1\nline2\nline3")
        assert "line1" in result
        assert "line3" in result


# ════════════════════════════════════════════════════════════════════
# 5. TOOL_NAME_PATTERN 常量
# ════════════════════════════════════════════════════════════════════


class TestToolNamePattern:
    def test_matches_simple_name(self):
        import re
        assert re.match(TOOL_NAME_PATTERN, "read_file")

    def test_matches_with_dash(self):
        import re
        assert re.match(TOOL_NAME_PATTERN, "web-search")

    def test_matches_with_dot(self):
        import re
        assert re.match(TOOL_NAME_PATTERN, "fs.read")
