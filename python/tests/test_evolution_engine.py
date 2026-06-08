"""伏羲 进化引擎测试 — EvolutionEngine 集成中枢"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from evolution.evolution_engine import EvolutionEngine


# ════════════════════════════════════════════════════════════════════
# 1. 初始化
# ════════════════════════════════════════════════════════════════════


class TestEvolutionEngineInit:
    def test_init_with_defaults(self):
        """不传参应使用默认 db 路径。"""
        engine = EvolutionEngine()
        assert engine._query_classifier is not None
        assert engine._strategy_profiler is not None
        assert engine._tool_ranker is not None
        assert engine._memory_optimizer is not None
        assert engine._last_category is None

    def test_init_with_custom_paths(self):
        """传入自定义路径应注入子组件。"""
        engine = EvolutionEngine(evolution_db_path="/tmp/e.db", tracker_db_path="/tmp/t.db")
        assert engine._strategy_profiler is not None
        assert engine._tool_ranker is not None
        assert engine._memory_optimizer is not None

    def test_set_tracker_db(self):
        """set_tracker_db 应转发给 ToolRanker。"""
        engine = EvolutionEngine()
        with patch.object(engine._tool_ranker, "set_tracker_db") as mock:
            engine.set_tracker_db("/tmp/x.db")
            mock.assert_called_once_with("/tmp/x.db")


# ════════════════════════════════════════════════════════════════════
# 2. before_run — 运行前优化建议
# ════════════════════════════════════════════════════════════════════


class TestBeforeRun:
    def setup_method(self):
        self.engine = EvolutionEngine()

    def test_returns_full_advice_structure(self):
        """返回值应包含 strategy / tools / memory 三个子节。"""
        result = self.engine.before_run(
            user_message="你好",
            session_id="s1",
            available_tools={"read_file": {}, "write_file": {}},
        )
        assert "query_category" in result
        assert "complexity" in result
        assert "strategy" in result
        assert "tools" in result
        assert "memory" in result

    def test_caches_last_category(self):
        """查询分类结果应被缓存供 after_run 使用。"""
        assert self.engine._last_category is None
        self.engine.before_run("你好", "s1", {})
        assert self.engine._last_category is not None

    def test_strategy_recommendation_includes_steps(self):
        """strategy 应包含 recommend_steps / recommend_temp。"""
        result = self.engine.before_run("test", "s1", {}, default_steps=10, default_temp=0.5)
        assert "recommend_steps" in result["strategy"]
        assert "recommend_temp" in result["strategy"]
        assert "best_strategy" in result["strategy"]

    def test_tools_section_has_ranked_list(self):
        """tools 节应返回 ranked_list / prompt_section / insights。"""
        result = self.engine.before_run("test", "s1", {"read_file": {}})
        assert "ranked_list" in result["tools"]
        assert "prompt_section" in result["tools"]
        assert "insights" in result["tools"]

    def test_complex_message_classified_differently(self):
        """不同消息应被分类到不同类别（至少 query_category 字段存在）。"""
        a = self.engine.before_run("你好", "s1", {})
        b = self.engine.before_run("请用Python写一个排序算法", "s1", {})
        # 都应有 query_category 字段
        assert "query_category" in a
        assert "query_category" in b

    def test_default_steps_fallback(self):
        """当策略推荐无 recommend_steps 时应回退到 default_steps。"""
        with patch.object(self.engine._strategy_profiler, "get_recommendation") as mock:
            mock.return_value = {}  # 空推荐
            result = self.engine.before_run("test", "s1", {}, default_steps=7)
            assert result["strategy"]["recommend_steps"] == 7


# ════════════════════════════════════════════════════════════════════
# 3. after_run — 运行后反馈
# ════════════════════════════════════════════════════════════════════


class TestAfterRun:
    def setup_method(self):
        self.engine = EvolutionEngine()

    def test_after_run_uses_cached_category(self):
        """after_run 应使用 before_run 缓存的类别。"""
        self.engine._last_category = "simple_qa"
        with patch.object(self.engine._strategy_profiler, "record_run") as mock:
            self.engine.after_run(
                result={"success": True, "completed": True, "steps": [], "total_steps": 1, "elapsed": 0.1, "usage": {}, "error": ""},
                user_message="hi",
                session_id="s1",
                trace_id="t1",
            )
            assert mock.call_args.kwargs["query_category"] == "simple_qa"

    def test_after_run_falls_back_to_unknown_category(self):
        """未缓存类别时应回退到 'unknown'。"""
        with patch.object(self.engine._strategy_profiler, "record_run") as mock:
            self.engine.after_run(
                result={"success": True, "completed": True, "steps": [], "total_steps": 1, "elapsed": 0, "usage": {}, "error": ""},
                user_message="x",
                session_id="s1",
                trace_id="t2",
            )
            assert mock.call_args.kwargs["query_category"] == "unknown"

    def test_after_run_extracts_tools_from_steps(self):
        """应从 steps 中提取使用的工具列表。"""
        steps = [
            {"action": {"tool": "read_file"}},
            {"action": {"tool": "write_file"}},
            {"thought": "no action"},
        ]
        with patch.object(self.engine._strategy_profiler, "record_run") as mock:
            self.engine.after_run(
                result={"success": True, "completed": True, "steps": steps, "total_steps": 3, "elapsed": 0, "usage": {}, "error": ""},
                user_message="x",
                session_id="s1",
                trace_id="t3",
            )
            # tools_used 是 record_run 的关键字参数
            call_kwargs = mock.call_args.kwargs
            assert call_kwargs["tools_used"] == ["read_file", "write_file"]

    def test_after_run_captures_error_type(self):
        """失败时 error_type 应被记录。"""
        with patch.object(self.engine._strategy_profiler, "record_run") as mock:
            self.engine.after_run(
                result={"success": False, "completed": False, "steps": [], "total_steps": 0, "elapsed": 0, "usage": {}, "error": "timeout"},
                user_message="x",
                session_id="s1",
                trace_id="t4",
            )
            assert mock.call_args.kwargs["error_type"] == "timeout"

    def test_after_run_success_has_empty_error_type(self):
        """成功时 error_type 应为空字符串。"""
        with patch.object(self.engine._strategy_profiler, "record_run") as mock:
            self.engine.after_run(
                result={"success": True, "completed": True, "steps": [], "total_steps": 1, "elapsed": 0.1, "usage": {}, "error": ""},
                user_message="x",
                session_id="s1",
                trace_id="t5",
            )
            assert mock.call_args.kwargs["error_type"] == ""


# ════════════════════════════════════════════════════════════════════
# 4. get_evolution_stats
# ════════════════════════════════════════════════════════════════════


class TestGetEvolutionStats:
    def setup_method(self):
        self.engine = EvolutionEngine()

    def test_returns_all_four_sections(self):
        """应返回 query_classifier / strategy_profiler / tool_ranker / memory_optimizer 四节。"""
        stats = self.engine.get_evolution_stats()
        assert "query_classifier" in stats
        assert "strategy_profiler" in stats
        assert "tool_ranker" in stats
        assert "memory_optimizer" in stats

    def test_query_classifier_includes_categories(self):
        """query_classifier 应包含所有 categories 列表。"""
        stats = self.engine.get_evolution_stats()
        assert "categories" in stats["query_classifier"]
        # 至少应有一些分类
        assert len(stats["query_classifier"]["categories"]) > 0

    def test_category_includes_complexity(self):
        """每个 category 应包含 label / complexity / recommended_steps / recommended_temp。"""
        stats = self.engine.get_evolution_stats()
        cats = stats["query_classifier"]["categories"]
        first_cat = next(iter(cats.values()))
        assert "label" in first_cat
        assert "complexity" in first_cat
        assert "recommended_steps" in first_cat
        assert "recommended_temp" in first_cat
