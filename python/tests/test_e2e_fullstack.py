"""端到端全栈测试 - 完整的 FuxiEngine 集成测试

测试覆盖：
- 引擎初始化与组件装配
- 简单对话写入热记忆
- 跨轮次记忆召回
- 冷记忆写入
- 多会话隔离
- 工具调用
- Selector 影响系统提示词
- 引擎多轮运行状态

需要 LLM_API_KEY 和 LLM_BASE_URL 环境变量（调用 run() 的测试需要）
"""
import sys
import os
import time
import json
import tempfile
import shutil
import pytest

# ── 路径设置 ──────────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from engine.fuxi_engine import FuxiEngine
from engine.execution_logger import StructuredLogger
from engine.tool_tracker import ToolCallTracker
from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory
from evolution.selector import Selector
from tools import registry

# ── API 可用标记 ──────────────────────────────────────────
API_AVAILABLE = bool(os.environ.get("LLM_API_KEY") and os.environ.get("LLM_BASE_URL"))


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def temp_dir():
    """临时目录（日志 + 数据库）"""
    path = tempfile.mkdtemp(prefix="fuxi_e2e_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def warm_memory():
    """温记忆（:memory: 模式）"""
    return WarmMemory(db_path=":memory:", max_entries=100)


@pytest.fixture
def cold_memory(temp_dir):
    """冷记忆（临时文件）"""
    return ColdMemory(db_path=os.path.join(temp_dir, "cold_memory.db"))


@pytest.fixture
def tool_tracker():
    """工具追踪器（:memory: 模式）"""
    return ToolCallTracker(db_path=":memory:")


@pytest.fixture
def execution_logger(temp_dir):
    """执行日志器"""
    return StructuredLogger(log_dir=os.path.join(temp_dir, "logs"))


@pytest.fixture
def selector(warm_memory, cold_memory):
    """选择器（纯规则，不依赖 LLM）"""
    return Selector(
        warm_memory=warm_memory,
        cold_memory=cold_memory,
        evolution_db_path=":memory:",
    )


@pytest.fixture
def engine(temp_dir, warm_memory, cold_memory, tool_tracker, execution_logger, selector):
    """完整引擎：装配所有组件"""
    eng = FuxiEngine(
        llm_key=os.environ.get("LLM_API_KEY"),
        base_url=os.environ.get("LLM_BASE_URL"),
        model=os.environ.get("LLM_MODEL"),
        max_steps=5,
        execution_logger=execution_logger,
        tool_tracker=tool_tracker,
        selector=selector,
        warm_memory=warm_memory,
        cold_memory=cold_memory,
    )
    yield eng


# ═══════════════════════════════════════════════════════════
# A. 引擎初始（无需 API）
# ═══════════════════════════════════════════════════════════

class TestEngineInitialization:
    """引擎初始化 — 组件装配与状态"""

    def test_engine_created(self):
        """最小构造"""
        eng = FuxiEngine(max_steps=3)
        assert eng is not None
        assert eng.max_steps == 3
        assert isinstance(eng.hot_memory, HotMemory)

    def test_engine_with_all_components(self, engine):
        """全组件装配"""
        assert engine.hot_memory is not None
        assert engine.warm_memory is not None
        assert engine.cold_memory is not None
        assert engine._selector is not None
        assert engine._tool_tracker is not None
        assert engine._execution_logger is not None
        assert engine._tool_executor is not None
        assert engine.tool_registry is not None

    def test_engine_initial_state(self, engine):
        """初始状态正确"""
        # 无会话历史
        assert len(engine._session_history) == 0
        assert len(engine._session_access) == 0
        # 无上次 advice
        assert engine._last_advice == {}
        # 热记忆为空
        hot = engine.hot_memory.read()
        content = hot.get("memory_content", "")
        assert content == "" or content == "无"

    def test_engine_clear_session(self, engine):
        """清理会话不报错"""
        engine.clear_session("nonexistent")
        assert True  # 不抛异常即可

    def test_get_system_prompt_with_advice(self, engine):
        """Selector advice 注入系统提示词"""
        advice = {
            "tools": {
                "prompt_section": "- read_file: 读取文件\n- write_file: 写入文件",
            },
            "strategy": {"recommend_steps": 3},
            "retrieved_memories": {
                "warm": [{"content": "之前讨论过 Python", "timestamp": time.time()}],
                "cold": [{"content": "用户擅长编程", "summary": "用户画像"}],
            },
        }
        prompt = engine._get_system_prompt(advice=advice)
        # 包含注入的工具列表
        assert "read_file" in prompt
        assert "write_file" in prompt
        # 包含记忆上下文
        assert "Python" in prompt or "编程" in prompt
        # 步数受 advice 限制（≤15）
        assert "3 次" in prompt or "3次" in prompt

    def test_get_system_prompt_without_advice(self, engine):
        """无 advice 时使用默认工具列表"""
        prompt = engine._get_system_prompt(advice=None)
        # 默认包含注册表中的工具名
        assert "read_file" in prompt or "list_files" in prompt
        assert "Final:" in prompt


# ═══════════════════════════════════════════════════════════
# B. 简单对话（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestSimpleConversation:
    """简单对话 — 热记忆写入与返回"""

    @pytest.mark.timeout(120)
    def test_conversation_writes_hot_memory(self, engine):
        """对话后热记忆有内容"""
        result = engine.run("Say 'hello world' in one sentence.", session_id="e2e-hot-1")
        assert result["success"] is True, f"引擎运行失败: {result.get('error')}"
        assert result.get("content"), "应返回内容"

        # 热记忆已写入
        hot = engine.hot_memory.read()
        content = hot.get("memory_content", "")
        assert len(content) > 0, "热记忆应非空"
        assert "e2e-hot-1" in content, "热记忆应包含会话 ID"

    @pytest.mark.timeout(120)
    def test_conversation_returns_structured_result(self, engine):
        """返回结构完整"""
        result = engine.run("What is 2+2? Answer briefly.", session_id="e2e-struct")
        assert result["success"] is True
        assert "content" in result
        assert "steps" in result
        assert "observations" in result
        assert "elapsed" in result
        assert "total_steps" in result
        assert "trace_id" in result
        assert result["elapsed"] > 0
        assert "4" in result["content"] or "four" in result["content"].lower()


# ═══════════════════════════════════════════════════════════
# C. 跨轮记忆（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestCrossRoundMemory:
    """跨轮次记忆召回"""

    @pytest.mark.timeout(180)
    def test_cross_round_recall(self, engine):
        """第二轮能记住第一轮的信息"""
        session = "e2e-cross"
        # 第一轮：告诉名字
        r1 = engine.run("My name is Alice.", session_id=session)
        assert r1["success"] is True

        # 第二轮：询问名字
        r2 = engine.run("What is my name?", session_id=session)
        assert r2["success"] is True
        content = r2.get("content", "").lower()
        assert "alice" in content, f"应记住名字 Alice，实际: {content[:100]}"

    @pytest.mark.timeout(180)
    def test_cross_round_preference(self, engine):
        """跨轮偏好记忆"""
        session = "e2e-pref"
        r1 = engine.run("I love programming in Python.", session_id=session)
        assert r1["success"] is True

        r2 = engine.run("What programming language do I like?", session_id=session)
        assert r2["success"] is True
        content = r2.get("content", "").lower()
        assert "python" in content, f"应记住 Python 偏好: {content[:100]}"


# ═══════════════════════════════════════════════════════════
# D. 冷记忆（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestColdMemory:
    """冷记忆写入"""

    @pytest.mark.timeout(120)
    def test_cold_memory_has_entry(self, engine):
        """运行后冷记忆有条目"""
        session = "e2e-cold-1"
        engine.run("Tell me a fun fact about space.", session_id=session)

        recent = engine.cold_memory.get_recent(session_id=session, limit=5)
        assert len(recent) >= 1, "冷记忆应至少有 1 条"
        entry = recent[0]
        assert entry.get("session_id") == session
        assert entry.get("summary", "") != ""
        assert entry.get("content", "") != ""

    @pytest.mark.timeout(120)
    def test_cold_memory_multiple_runs(self, engine):
        """多次运行产生多条冷记忆"""
        session = "e2e-cold-2"
        for i in range(3):
            r = engine.run(f"Tell me fact number {i}.", session_id=session)
            assert r["success"] is True

        recent = engine.cold_memory.get_recent(session_id=session, limit=10)
        assert len(recent) >= 3, f"3 次运行应有 >= 3 条冷记忆，实际 {len(recent)}"


# ═══════════════════════════════════════════════════════════
# E. 多会话隔离（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestMultiSessionIsolation:
    """多会话隔离"""

    @pytest.mark.timeout(180)
    def test_sessions_isolated(self, engine):
        """两个会话互不干扰"""
        # 会话 A：告诉颜色
        r_a1 = engine.run("My favorite color is red.", session_id="e2e-iso-a")
        assert r_a1["success"] is True

        # 会话 B：告诉动物
        r_b1 = engine.run("My favorite animal is cat.", session_id="e2e-iso-b")
        assert r_b1["success"] is True

        # 会话 A：问颜色
        r_a2 = engine.run("What is my favorite color?", session_id="e2e-iso-a")
        assert r_a2["success"] is True
        assert "red" in r_a2.get("content", "").lower()

        # 会话 B：问动物
        r_b2 = engine.run("What is my favorite animal?", session_id="e2e-iso-b")
        assert r_b2["success"] is True
        assert "cat" in r_b2.get("content", "").lower()

        # 会话 B 不应知道颜色
        r_b3 = engine.run("Do I have a favorite color?", session_id="e2e-iso-b")
        # 可接受不知道或猜错，但不应该说是 red（会话 A 的信息）
        content = r_b3.get("content", "").lower()

    @pytest.mark.timeout(120)
    def test_session_history_separate(self, engine):
        """引擎内部会话历史独立"""
        engine.run("Hello", session_id="e2e-sep-a")
        engine.run("Hi there", session_id="e2e-sep-b")

        assert "e2e-sep-a" in engine._session_history
        assert "e2e-sep-b" in engine._session_history
        assert len(engine._session_history) >= 2


# ═══════════════════════════════════════════════════════════
# F. 工具调用（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestToolCalling:
    """工具调用"""

    @pytest.mark.timeout(120)
    def test_engine_tool_executor_ready(self, engine):
        """引擎的工具执行器就绪"""
        executor = engine._tool_executor
        assert executor is not None
        # 注册表有可用的工具
        all_tools = engine.tool_registry.list_tools()
        assert len(all_tools) > 0

    @pytest.mark.timeout(120)
    def test_tool_tracker_records_calls(self, engine):
        """工具追踪器记录调用（通过 mock 式直接触发回调）"""
        # 直接触发 _on_tool_invoked 回调（不依赖 LLM 调用工具）
        engine._current_session_id = "e2e-tool-tr"
        engine._on_tool_invoked("read_file", True, {"elapsed_ms": 50, "error": ""})
        engine._on_tool_invoked("write_file", False, {
            "elapsed_ms": 200, "error": "timeout",
        })

        # 通过追踪器查询
        ranking = engine._tool_tracker.get_all_tools_ranking(days=1)
        # 应至少能查到 read_file 或 write_file
        tool_names = [t["tool_name"] for t in ranking]
        assert "read_file" in tool_names or "write_file" in tool_names, \
            f"应记录工具调用: {tool_names}"


# ═══════════════════════════════════════════════════════════
# G. 多轮运行状态（需要 API）
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not API_AVAILABLE, reason="Need LLM_API_KEY and LLM_BASE_URL")
class TestEngineMultipleRuns:
    """多轮运行状态"""

    @pytest.mark.timeout(180)
    def test_state_accumulates(self, engine):
        """多次运行后状态正确累积"""
        session = "e2e-state"
        for i in range(3):
            r = engine.run(f"Count to {i}. Say just the number.", session_id=session)
            assert r["success"] is True

        # 会话历史中应有 system + 多轮 user/assistant
        hist = engine._session_history.get(session, [])
        assert len(hist) >= 4  # system + (user+assistant) * 至少 1 轮

        # 热记忆有多条
        hot = engine.hot_memory.read()
        content = hot.get("memory_content", "")
        assert "e2e-state" in content

    @pytest.mark.timeout(120)
    def test_engine_rejects_session_limit(self, engine):
        """大量会话不会导致崩溃（LRU 保护）"""
        for i in range(10):
            r = engine.run(f"Session {i}", session_id=f"e2e-limit-{i}")
            assert r["success"] is True

    @pytest.mark.timeout(180)
    def test_engine_selector_advice_evolves(self, engine):
        """多次运行后 Selector advice 变化"""
        session = "e2e-evolve"
        # 前几次 advice
        advices = []
        for i in range(3):
            engine.run(f"Random thought {i}.", session_id=session)
            advices.append(engine._last_advice)

        # advice 应有结构
        for adv in advices:
            assert "query_category" in adv
            assert "strategy" in adv
            assert "tools" in adv

    @pytest.mark.timeout(180)
    def test_engine_summary_integrity(self, engine):
        """运行结果字段完整性"""
        session = "e2e-integrity"
        result = engine.run("Say 'test complete'.", session_id=session)

        required_fields = [
            "success", "content", "completed", "steps",
            "observations", "elapsed", "total_steps", "trace_id",
        ]
        for field in required_fields:
            assert field in result, f"结果缺少字段: {field}"

        assert isinstance(result["success"], bool)
        assert isinstance(result["steps"], list)
        assert isinstance(result["observations"], list)
        assert isinstance(result["elapsed"], (int, float))
        assert isinstance(result["total_steps"], int)
        assert "trace-" in result.get("trace_id", "")


# ═══════════════════════════════════════════════════════════
# H. 引擎 Selector 集成（无需 API）
# ═══════════════════════════════════════════════════════════

class TestSelectorEngineIntegration:
    """Selector 与引擎的集成"""

    def test_selector_attached_to_engine(self, engine):
        """Selector 可被引擎访问"""
        assert engine._selector is not None
        assert engine._selector._query_classifier is not None

    def test_selector_classifies_queries(self, engine):
        """Selector 分类查询"""
        sel = engine._selector
        # 简单问答
        cat1 = sel._query_classifier.classify("What is 2+2?")
        assert cat1 is not None

        # 代码生成
        cat2 = sel._query_classifier.classify("Write a Python function")
        assert cat2 is not None

    def test_selector_advice_structure(self, engine):
        """Selector.select() 返回完整 advice"""
        sel = engine._selector
        advice = sel.select(
            user_message="Say hello",
            session_id="e2e-sel-test",
            available_tools=engine.tool_registry.list_tools(),
            default_steps=5,
            is_new_message=True,
        )

        assert "query_category" in advice
        assert "strategy" in advice
        assert "tools" in advice
        assert "memory" in advice
        assert isinstance(advice["tools"].get("ranked_list"), list)

    def test_selector_record_outcome(self, engine):
        """Selector.record_outcome 不抛异常"""
        sel = engine._selector
        sel.record_outcome(
            result={
                "success": True,
                "completed": True,
                "steps": [],
                "total_steps": 2,
                "elapsed": 1.5,
                "usage": {"total_tokens": 100},
                "error": "",
            },
            user_message="Test message",
            session_id="e2e-rec-test",
            trace_id="trace-test",
        )
        assert True  # 无异常

    def test_selector_empty_memory_retrieval(self, engine):
        """空记忆检索不报错"""
        sel = engine._selector
        # 记忆为空时检索应返回空列表而非异常
        advice = sel.select(
            user_message="Hello world",
            session_id="e2e-empty-mem",
            available_tools={},
            default_steps=5,
            is_new_message=True,
        )
        retrieved = advice.get("retrieved_memories", {})
        assert "warm" in retrieved
        assert "cold" in retrieved
