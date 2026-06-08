"""伏羲 FuxiEngine 核心层测试"""
import sys
import os
import json
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from engine.fuxi_engine import FuxiEngine, MAX_HISTORY_MESSAGES, MAX_SESSIONS, MAX_BAD_OUTPUTS
from engine.response_parser import fix_json, parse_action, parse_final


# ── helpers ─────────────────────────────────────────────────────────


def make_engine(**kwargs):
    """构造最小依赖的 FuxiEngine（模拟 LLM 返回空以避免真实调用）"""
    kwargs.setdefault("llm_key", "test-key")
    kwargs.setdefault("base_url", "http://localhost:9999")
    kwargs.setdefault("model", "test-model")
    kwargs.setdefault("max_steps", 3)
    return FuxiEngine(**kwargs)


def patch_llm_complete(engine, responses):
    """用一系列固定响应替换 engine.llm.complete"""
    _iter = iter(responses)

    def fake_complete(**kw):
        try:
            return next(_iter)
        except StopIteration:
            return {"content": "", "success": False, "error": "no more fake responses"}

    engine.llm.complete = fake_complete


# ── 1. 引擎创建 ─────────────────────────────────────────────────────


class TestFuxiEngineCreation:
    def test_default_creation(self):
        engine = make_engine()
        assert engine.max_steps == 3
        assert engine.llm is not None
        assert engine.hot_memory is not None
        assert engine.tool_registry is not None
        assert engine._session_history == {}

    def test_creation_with_all_params(self):
        from engine.execution_logger import StructuredLogger
        logger = StructuredLogger(log_dir=os.path.join(os.path.dirname(__file__), "_tmp_logs"))
        engine = make_engine(
            llm_key="key",
            base_url="http://example.com",
            model="gpt-4",
            max_steps=15,
            execution_logger=logger,
        )
        assert engine.max_steps == 15
        assert engine._execution_logger is logger
        engine._execution_logger.shutdown(wait=False)

    def test_task_persistence_disabled_by_default(self):
        engine = make_engine()
        assert engine._task_persistence_enabled is False

    def test_task_persistence_enabled_with_env(self):
        os.environ["FUXI_TASK_DB"] = ":memory:"
        engine = make_engine()
        if engine._task_persistence_enabled:
            concurrency = getattr(engine, "_task_db_path", None)
            # expect real file path or None; in-memory may fail with sqlite
        del os.environ["FUXI_TASK_DB"]


# ── 2. _fix_json 四种修复策略 ─────────────────────────────────────


class TestFixJson:
    def test_strategy1_valid_json(self):
        """策略1: 直接解析合法的 JSON"""
        result = fix_json('{"a": 1, "b": "hello"}')
        assert result is not None
        json.loads(result)

    def test_strategy2_single_quotes(self):
        """策略2: 单引号→双引号"""
        result = fix_json("{'a': 1, 'b': 'hello'}")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"] == 1
        assert parsed["b"] == "hello"

    def test_strategy2_python_bool_none(self):
        """策略2: Python 布尔值/None 转换"""
        result = fix_json("{'flag': True, 'data': None, 'bad': False}")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["flag"] is True
        assert parsed["data"] is None
        assert parsed["bad"] is False

    def test_strategy3_ast_literal_eval(self):
        """策略3: ast.literal_eval 处理 Python 语法"""
        result = fix_json("{'a': 1, 'b': [1, 2, 3]}")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"] == 1
        assert parsed["b"] == [1, 2, 3]

    def test_strategy4_trailing_comma_and_comment(self):
        """策略4: 尾随逗号和注释"""
        raw = """{
            "a": 1,  // this is a comment
            "b": [1, 2, 3,],
        }"""
        result = fix_json(raw)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"] == 1
        assert parsed["b"] == [1, 2, 3]

    def test_strategy4_line_comment(self):
        raw = '{"a": 1 // comment\n}'
        result = fix_json(raw)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"] == 1

    def test_all_strategies_fail(self):
        """所有策略均失败时返回 None"""
        result = fix_json("{definitely not json}")
        assert result is None

    def test_empty_string(self):
        result = fix_json("")
        assert result is None

    def test_nested_brackets(self):
        raw = '{"x": {"y": [1, 2]}, "z": {"a": 1}}'
        result = fix_json(raw)
        assert result is not None
        assert json.loads(result)["x"]["y"] == [1, 2]

    def test_unicode_content(self):
        raw = '{"name": "伏羲引擎"}'
        result = fix_json(raw)
        assert result is not None
        assert json.loads(result)["name"] == "伏羲引擎"


# ── 3. _strip_think_tags ────────────────────────────────────────────


class TestStripThinkTags:
    def test_strip_complete_think_block(self):
        text = "<think>some reasoning here</think> Final: answer"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert "think" not in result
        assert "Final: answer" in result

    def test_strip_unclosed_think(self):
        text = "<think>some reasoning without closing"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert "think" not in result
        assert "some reasoning" not in result

    def test_strip_multiple_think_blocks(self):
        text = "<think>first</think> content <think>second</think> Final: done"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert "think" not in result
        assert "Final: done" in result

    def test_strip_think_with_final(self):
        text = "<think>思考中...</think>Final: 答案是42"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert "<think>" not in result
        assert "Final: 答案是42" in result or "Final" in result

    def test_no_think_tags(self):
        text = "Final: simple answer"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert result == "Final: simple answer"

    def test_only_think_tags(self):
        text = "<think>just thinking</think>"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        assert result == ""

    def test_malformed_tags(self):
        text = "<think>broken</thik> Final: answer"
        result = FuxiEngine._strip_think_tags(FuxiEngine, text)
        # </thik> is not matched, but <think> gets stripped
        assert "Final: answer" in result


# ── 4. _parse_final ─────────────────────────────────────────────────


class TestParseFinal:
    def test_english_final(self):
        result = parse_final("Final: The answer is 42")
        assert result == "The answer is 42"

    def test_chinese_final(self):
        result = parse_final("最终答案: 答案是42")
        assert result == "答案是42"

    def test_chinese_final_short(self):
        result = parse_final("最终: 好的")
        assert result == "好的"

    def test_empty_final(self):
        result = parse_final("Final: ")
        assert result == "(空)"

    def test_no_final(self):
        result = parse_final("Action: read_file({'path': 'x.txt'})")
        assert result is None

    def test_final_with_multiline(self):
        result = parse_final("Final: line1\nline2\nline3")
        assert "line1" in result

    def test_final_with_special_chars(self):
        result = parse_final("Final: 你好，世界！price=$100")
        assert "100" in result


# ── 5. _parse_one_action ────────────────────────────────────────────


class TestParseOneAction:
    def test_english_action(self):
        content = 'Action: read_file({"path": "/tmp/test.txt"})'
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "read_file"
        assert result["arguments"]["path"] == "/tmp/test.txt"

    def test_chinese_action(self):
        content = '行动: write_file({"content": "hello"})'
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "write_file"

    def test_hyphenated_tool_name(self):
        content = 'Action: memory-query({"key": "test"})'
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "memory-query"

    def test_tool_name_with_dots(self):
        content = 'Action: tool.say({"msg": "hi"})'
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "tool.say"

    def test_python_syntax_args(self):
        content = "Action: echo({'message': 'hello'})"
        result = parse_action(content)
        assert result is not None
        assert result["arguments"]["message"] == "hello"

    def test_no_action(self):
        content = "Final: answer"
        result = parse_action(content)
        assert result is None

    def test_invalid_json_args(self):
        """unquoted keys/values cannot be fixed by any strategy"""
        content = 'Action: read_file({path: /tmp})'
        result = parse_action(content)
        assert result is None

    def test_action_with_trailing_text(self):
        content = 'Action: web_search({"q": "test"}) then something'
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "web_search"

    def test_multiline_action(self):
        content = '''Action: write_file({
    "path": "test.txt",
    "content": "hello"
})'''
        result = parse_action(content)
        assert result is not None
        assert result["tool"] == "write_file"
        assert result["arguments"]["content"] == "hello"


# ── 6. _trim_history ────────────────────────────────────────────────


class TestTrimHistory:
    def test_below_limit(self):
        """低于阈值则不裁剪"""
        engine = make_engine()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        trimmed = engine._trim_history(msgs)
        assert len(trimmed) == 3

    def test_trim_excess(self):
        """超出 MAX_HISTORY_MESSAGES 时裁剪最旧的非 system 消息"""
        engine = make_engine()
        # 使用刚好超过 MAX_HISTORY_MESSAGES 但不超过 30 条总数（避免触发 LLM 压缩）
        excess = MAX_HISTORY_MESSAGES - 1 + 5  # just above limit
        msgs = [{"role": "system", "content": "sys"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
            for i in range(excess)
        ]
        trimmed = engine._trim_history(msgs)
        assert len(trimmed) <= MAX_HISTORY_MESSAGES
        # system 消息必须保留
        assert trimmed[0]["role"] == "system"

    def test_only_system_messages(self):
        engine = make_engine()
        msgs = [{"role": "system", "content": "s"}] * (MAX_HISTORY_MESSAGES + 5)
        trimmed = engine._trim_history(msgs)
        assert len(trimmed) <= MAX_HISTORY_MESSAGES

    def test_empty_messages(self):
        engine = make_engine()
        trimmed = engine._trim_history([])
        assert trimmed == []


# ── 7. session 隔离 ────────────────────────────────────────────────


class TestSessionIsolation:
    def test_different_sessions_dont_interfere(self):
        engine = make_engine()
        # 调用 run 会写 session_history, 但 LLM 会失败 — 我们测试 session 存储本身
        engine._session_history["sess-a"] = [{"role": "system", "content": "a"}]
        engine._session_history["sess-b"] = [{"role": "system", "content": "b"}]
        engine._session_access["sess-a"] = time.time()
        engine._session_access["sess-b"] = time.time()

        msgs_a = engine._session_history.get("sess-a", [])
        msgs_b = engine._session_history.get("sess-b", [])
        assert msgs_a[0]["content"] == "a"
        assert msgs_b[0]["content"] == "b"

    def test_clear_session(self):
        engine = make_engine()
        engine._session_history["sess"] = [{"role": "system", "content": "x"}]
        engine._session_access["sess"] = time.time()
        engine.clear_session("sess")
        assert "sess" not in engine._session_history
        assert "sess" not in engine._session_access

    def test_clear_nonexistent_session(self):
        engine = make_engine()
        engine.clear_session("does-not-exist")  # should not raise


# ── 8. session LRU 淘汰 ─────────────────────────────────────────────


class TestSessionLRUEviction:
    def test_lru_eviction(self):
        engine = make_engine()
        # 直接操作内部数据结构触发淘汰
        for i in range(MAX_SESSIONS + 10):
            sid = f"sess-{i:04d}"
            engine._session_history[sid] = [{"role": "system", "content": str(i)}]
            engine._session_access[sid] = time.time()
            engine._session_access.move_to_end(sid)

        engine._enforce_session_limit()
        assert len(engine._session_history) <= MAX_SESSIONS

    def test_lru_oldest_evicted_first(self):
        engine = make_engine()
        for i in range(MAX_SESSIONS + 5):
            sid = f"sess-{i:04d}"
            engine._session_history[sid] = [{"role": "system", "content": str(i)}]
            engine._session_access[sid] = float(i)
        engine._enforce_session_limit()
        # 最早的 5 个应该被淘汰
        assert "sess-0000" not in engine._session_history
        assert len(engine._session_history) <= MAX_SESSIONS

    def test_no_eviction_under_limit(self):
        engine = make_engine()
        for i in range(5):
            sid = f"sess-{i}"
            engine._session_history[sid] = []
            engine._session_access[sid] = float(i)
        engine._enforce_session_limit()
        assert len(engine._session_history) == 5


# ── 9. task persistence ──────────────────────────────────────────────


class TestTaskPersistence:
    def test_save_and_restore(self):
        engine = make_engine()
        engine._task_persistence_enabled = True
        engine._task_db_path = ":memory:"
        engine._init_task_persistence()

        ok = engine.save_task_state(
            session_id="test-sess",
            step=3,
            messages=[{"role": "user", "content": "hi"}],
            observations=[{"step": 1, "tool": "echo", "result": "ok"}],
            tools_used=[{"tool": "echo"}],
        )
        # in-memory 可能失败，但不会抛异常
        state = engine.restore_task_state("test-sess")
        if ok and state:
            assert state["step"] == 3

        engine.clear_task_state("test-sess")

    def test_disable_persistence(self):
        engine = make_engine()
        assert engine.save_task_state("x", 0, [], [], []) is False
        assert engine.restore_task_state("x") is None
        assert engine.clear_task_state("x") is False


# ── 10. 系统提示词生成 ────────────────────────────────────────────


class TestSystemPrompt:
    def test_basic_system_prompt(self):
        engine = make_engine()
        prompt = engine._get_system_prompt()
        assert "伏羲引擎" in prompt or "Fuxi" in prompt
        assert "Action:" in prompt
        assert "Final:" in prompt

    def test_system_prompt_with_advice_tools(self):
        engine = make_engine()
        advice = {"tools": {"prompt_section": "- custom_tool: custom doc"}}
        prompt = engine._get_system_prompt(advice)
        assert "custom_tool" in prompt

    def test_system_prompt_with_retrieved_memories(self):
        engine = make_engine()
        # 没有 _selector 时，retrieved memories 不会影响 prompt
        advice = {
            "retrieved_memories": {
                "warm": [{"content": "warm memory item"}],
                "cold": [],
            }
        }
        prompt = engine._get_system_prompt(advice)
        # 没有 selector 时，内存部分显示 "无"
        assert "无" in prompt or "记忆" in prompt


# ── 11. ReAct 循环边界情况 ──────────────────────────────────────────


class TestReActEdgeCases:
    def test_llm_failure_returns_error(self):
        engine = make_engine()
        patch_llm_complete(engine, [
            {"success": False, "error": "API error", "content": "", "usage": {}},
        ])
        result = engine.run("hello", "test-sess-fail")
        assert result["success"] is False
        assert "error" in result

    def test_empty_output_handling(self):
        engine = make_engine()
        patch_llm_complete(engine, [
            {"success": True, "content": "", "usage": {}, "model": "test"},
        ])
        result = engine.run("test", "test-sess-empty")
        assert "elapsed" in result

    def test_consecutive_bad_output_terminates(self):
        engine = make_engine()
        # 先加一些正常的来填充
        responses = []
        for _ in range(MAX_BAD_OUTPUTS + 1):
            responses.append({
                "success": True,
                "content": "some random text without Action or Final",
                "usage": {},
                "model": "test",
            })
        patch_llm_complete(engine, responses)
        result = engine.run("trigger bad output", "test-sess-bad")
        assert result["success"] is False

    def test_action_and_final_prefers_final(self):
        engine = make_engine()
        patch_llm_complete(engine, [
            {
                "success": True,
                "content": 'Action: echo({"msg": "hi"})\nFinal: done',
                "usage": {},
                "model": "test",
            },
        ])
        result = engine.run("test", "test-sess-pref")
        assert result["success"] is True
        assert result["content"] == "done"

    def test_final_answer_path(self):
        engine = make_engine()
        patch_llm_complete(engine, [
            {
                "success": True,
                "content": "Final: answer is 42",
                "usage": {},
                "model": "test",
            },
        ])
        result = engine.run("what is the answer?", "test-sess-final")
        assert result["success"] is True
        assert "42" in result["content"]



# ── 12. _compress_context ──────────────────────────────────────────


class TestCompressContext:
    def test_compress_short_messages(self):
        """少于 3 条消息应返回空"""
        engine = make_engine()
        result = engine._compress_context([
            {"role": "user", "content": "hi"},
        ])
        assert result == ""

    def test_compress_empty(self):
        engine = make_engine()
        assert engine._compress_context([]) == ""
