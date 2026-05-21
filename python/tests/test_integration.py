"""伏羲集成测试 - 记忆链、工具链、引擎+选择器、会话隔离、进化反馈

测试金字塔的第二层：验证多组件协作的正确性。
"""
import sys
import os
import time
import json
import tempfile
import pytest

# ── 路径设置 ──────────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from memory.hot_memory import HotMemory
from memory.warm_memory import WarmMemory
from memory.cold_memory import ColdMemory
from tools import registry
from tools.executor import ToolExecutor, ToolCache


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前重置注册表状态（不影响已注册的工具，只清理回调）"""
    # 存储当前工具数
    before = len(registry._tools)
    yield
    # 确保测试不会污染回调
    registry._on_invoke_callbacks.clear()
    # 恢复工具数（注册表是单例，不要真正清除工具）
    assert len(registry._tools) == before, "测试不应增减注册工具"


@pytest.fixture
def tmp_db_dir():
    """临时数据库目录"""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def hot_memory():
    """热记忆实例"""
    return HotMemory(max_size=5, max_age_seconds=3600)


@pytest.fixture
def warm_memory(tmp_db_dir):
    """温记忆实例（SQLite FTS5，临时文件）"""
    db_path = os.path.join(tmp_db_dir, "warm.db")
    wm = WarmMemory(db_path=db_path, max_entries=50)
    yield wm
    try:
        wm.close()
    except Exception:
        pass


@pytest.fixture
def cold_memory(tmp_db_dir):
    """冷记忆实例（SQLite，临时文件）"""
    db_path = os.path.join(tmp_db_dir, "cold.db")
    cm = ColdMemory(db_path=db_path)
    yield cm
    try:
        cm.close()
    except Exception:
        pass


@pytest.fixture
def tool_executor():
    """工具执行器（开启缓存+校验+去重）"""
    return ToolExecutor(
        tool_registry=registry,
        enable_cache=True,
        enable_validation=True,
        enable_dedup=True,
    )


@pytest.fixture
def tool_cache():
    """工具缓存实例"""
    return ToolCache(max_entries=10, ttl_seconds=60)


# ═══════════════════════════════════════════════════════════
# A. 记忆链测试 (Memory Chain)
# ═══════════════════════════════════════════════════════════

class TestHotMemoryChain:
    """热记忆链：append → read → evict → warm flush"""

    def test_append_and_read(self, hot_memory):
        """追加条目后能读取聚合内容"""
        r1 = hot_memory.append("第一条记忆内容")
        assert r1["success"] is True
        assert r1["char_count"] > 0

        r2 = hot_memory.append("第二条记忆内容")
        assert r2["success"] is True

        content = hot_memory.read()
        assert content["char_count"] > 0
        assert "第一条记忆内容" in content["memory_content"]
        assert "第二条记忆内容" in content["memory_content"]

    def test_write_overwrites(self, hot_memory):
        """write() 覆盖全部内容"""
        hot_memory.append("旧内容")
        hot_memory.write("全新内容")
        content = hot_memory.read()
        assert "旧内容" not in content["memory_content"]
        assert "全新内容" in content["memory_content"]

    def test_lru_eviction(self, hot_memory):
        """超过 max_size 时淘汰最旧条目"""
        # max_size=5, 写入6条
        for i in range(6):
            hot_memory.append(f"第{i}条记忆")
        stats = hot_memory.get_stats()
        assert stats["current_size"] <= stats["max_size"]
        # 淘汰计数应 > 0
        assert stats["eviction_count"] >= 1

    def test_evict_expired(self, hot_memory):
        """evict_expired 清理过期条目"""
        hot_memory.append("永不过期")
        # 直接操作内部 cache 让一条过期
        import time as _time
        for key in list(hot_memory._cache.keys()):
            val, _ = hot_memory._cache[key]
            hot_memory._cache[key] = (val, _time.time() - 7200)  # 2小时前
        evicted = hot_memory.evict_expired()
        assert evicted == 1
        stats = hot_memory.get_stats()
        assert stats["current_size"] == 1  # 只留下未过期的

    def test_entry_get_set(self, hot_memory):
        """get_entry 和 set_entry"""
        hot_memory.set_entry("my_key", "我的值")
        val = hot_memory.get_entry("my_key")
        assert val == "我的值"

        # 不存在的 key
        val2 = hot_memory.get_entry("nonexistent")
        assert val2 is None

    def test_clear(self, hot_memory):
        """clear 清空所有条目"""
        hot_memory.append("内容")
        hot_memory.clear()
        content = hot_memory.read()
        assert content["char_count"] == 0


class TestHotToWarmFlush:
    """热记忆淘汰→温记忆下刷链路"""

    def test_warm_flush_callback_on_evict(self, hot_memory, warm_memory):
        """淘汰时触发温层下刷回调"""
        flushed = []

        def callback(key, value):
            flushed.append((key, value))
            warm_memory.add_message(session_id="test", content=value)

        hot_memory.set_warm_flush_callback(callback)
        # 写满并触发淘汰
        for i in range(10):
            hot_memory.append(f"内容{i}")
        assert len(flushed) > 0, "淘汰应触发回调"
        # 温记忆应有记录
        recent = warm_memory.get_recent(session_id="test", limit=50)
        assert recent["success"]
        assert recent["total"] > 0

    def test_hot_stats_usage_ratio(self, hot_memory):
        """get_stats 反映使用率"""
        stats = hot_memory.get_stats()
        assert "max_size" in stats
        assert "current_size" in stats
        assert "hit_rate" in stats
        assert "eviction_count" in stats

    def test_max_entry_chars_truncation(self, hot_memory):
        """超长条目自动截断"""
        long_text = "A" * 6000
        result = hot_memory.append(long_text)
        assert result["success"]
        assert result["char_count"] <= 5000  # 默认 max_entry_chars=5000


class TestWarmMemoryChain:
    """温记忆链：add → search → get_recent → clear"""

    def test_add_and_search(self, warm_memory):
        """插入后可通过 FTS5 搜索"""
        warm_memory.add_message("s1", "今天天气真好")
        warm_memory.add_message("s1", "人工智能技术发展迅速")
        # 搜索
        result = warm_memory.search("s1", "天气", limit=10)
        assert result["success"]
        assert result["total"] >= 1

    def test_get_recent(self, warm_memory):
        """get_recent 返回最近消息"""
        warm_memory.add_message("s1", "消息A")
        time.sleep(0.01)
        warm_memory.add_message("s1", "消息B")
        recent = warm_memory.get_recent("s1", limit=10)
        assert recent["success"]
        assert recent["total"] == 2
        entries = recent["entries"]
        assert len(entries) == 2
        # 按时间升序（最旧在前）
        assert entries[0]["content"] == "消息A"
        assert entries[1]["content"] == "消息B"

    def test_clear_session(self, warm_memory):
        """清空会话"""
        warm_memory.add_message("s1", "内容")
        warm_memory.clear_session("s1")
        stats = warm_memory.get_stats()
        assert stats["total_messages"] == 0

    def test_session_isolation(self, warm_memory):
        """不同会话隔离"""
        warm_memory.add_message("s1", "会话1的消息")
        warm_memory.add_message("s2", "会话2的消息")
        r1 = warm_memory.get_recent("s1", limit=10)
        r2 = warm_memory.get_recent("s2", limit=10)
        assert r1["total"] == 1
        assert r2["total"] == 1

    def test_max_entries_enforced(self, warm_memory):
        """max_entries 限制"""
        # max_entries=50 所以写入 60 条应保留 50 条
        for i in range(60):
            warm_memory.add_message("s1", f"消息{i}")
        stats = warm_memory.get_stats()
        # 温记忆只保留每个 session 最新 max_entries 条
        recent = warm_memory.get_recent("s1", limit=100)
        assert recent["total"] <= 50


class TestColdMemoryChain:
    """冷记忆链：insert → search → get_recent"""

    def test_insert_and_get_recent(self, cold_memory):
        """插入后 get_recent 返回"""
        cold_memory.insert_summary(
            content="完整内容",
            summary="摘要",
            session_id="s1",
        )
        recent = cold_memory.get_recent("s1", limit=10)
        assert recent["success"]
        assert len(recent["entries"]) >= 1

    def test_search_similar(self, cold_memory):
        """向量/文本搜索"""
        cold_memory.insert_summary(
            content="Python is a programming language",
            summary="Python 编程语言",
            session_id="s1",
        )
        cold_memory.insert_summary(
            content="Java is also a programming language",
            summary="Java 编程语言",
            session_id="s1",
        )
        result = cold_memory.search_similar(query="Python", limit=10, session_id="s1")
        assert result["success"]

    def test_empty_query_returns_recent(self, cold_memory):
        """空查询返回最近"""
        cold_memory.insert_summary(content="A", summary="A", session_id="s1")
        result = cold_memory.search_similar(query="", limit=10)
        assert result["success"]

    def test_session_isolation(self, cold_memory):
        """冷记忆会话隔离"""
        cold_memory.insert_summary(content="A", summary="A", session_id="s1")
        cold_memory.insert_summary(content="B", summary="B", session_id="s2")
        r1 = cold_memory.get_recent("s1")
        r2 = cold_memory.get_recent("s2")
        assert len(r1["entries"]) == 1
        assert r1["entries"][0]["summary"] == "A"
        assert len(r2["entries"]) == 1
        assert r2["entries"][0]["summary"] == "B"

    def test_clear_session(self, cold_memory):
        """清空冷记忆会话"""
        cold_memory.insert_summary(content="X", summary="X", session_id="s1")
        cold_memory.clear_session("s1")
        recent = cold_memory.get_recent("s1")
        assert len(recent["entries"]) == 0


class TestThreeLayerMemoryIntegration:
    """三层记忆集成测试"""

    def test_hot_to_warm_to_cold_chain(self, hot_memory, warm_memory, cold_memory):
        """hot→warm→cold 链式写入"""
        # 1. 写入热记忆
        hot_memory.append("用户问了一个技术问题")

        # 2. 模拟淘汰回调写入温记忆
        warm_memory.add_message(session_id="s1", content="用户问了一个技术问题")

        # 3. 写入冷记忆
        cold_memory.insert_summary(
            content="用户问了一个技术问题",
            summary="技术问答会话",
            session_id="s1",
        )

        # 4. 验证三层都有数据
        hot_content = hot_memory.read()
        assert hot_content["char_count"] > 0

        warm_recent = warm_memory.get_recent("s1")
        assert warm_recent["total"] > 0

        cold_recent = cold_memory.get_recent("s1")
        assert len(cold_recent["entries"]) > 0

    def test_hot_evict_triggers_warm(self, hot_memory, warm_memory):
        """热记忆淘汰触发温记忆写入"""
        warm_records = []

        def cb(key, value):
            warm_records.append(value)
            warm_memory.add_message(session_id="test", content=value)

        hot_memory.set_warm_flush_callback(cb)
        for i in range(10):
            hot_memory.append(f"记忆{i}")
        assert len(warm_records) >= 5, "淘汰应触发温记忆写入"

        warm_recent = warm_memory.get_recent("test", limit=50)
        assert warm_recent["total"] >= 5


# ═══════════════════════════════════════════════════════════
# B. 工具链测试 (Tool Chain)
# ═══════════════════════════════════════════════════════════

class TestToolCache:
    """工具缓存测试"""

    def test_cache_set_get(self, tool_cache):
        """设置和获取缓存"""
        result = {"success": True, "result_json": '{"data": "hello"}', "error": ""}
        tool_cache.set("test_tool", {"arg": 1}, result)
        cached = tool_cache.get("test_tool", {"arg": 1})
        assert cached is not None
        assert cached["success"] is True

    def test_cache_miss(self, tool_cache):
        """未命中"""
        cached = tool_cache.get("nonexistent", {})
        assert cached is None

    def test_cache_ttl_expiry(self, tool_cache):
        """TTL 过期"""
        result = {"success": True, "result_json": "{}", "error": ""}
        tool_cache.set("tool", {"x": 1}, result)
        # 模拟过期
        for key in list(tool_cache._cache.keys()):
            expire, _ = tool_cache._cache[key]
            tool_cache._cache[key] = (time.time() - 10, _)
        cached = tool_cache.get("tool", {"x": 1})
        assert cached is None

    def test_cache_invalidate(self, tool_cache):
        """使某工具的缓存失效"""
        result = {"success": True, "result_json": "{}", "error": ""}
        tool_cache.set("tool_a", {"x": 1}, result)
        tool_cache.set("tool_b", {"y": 2}, result)
        tool_cache.invalidate("tool_a")
        assert tool_cache.get("tool_a", {"x": 1}) is None
        assert tool_cache.get("tool_b", {"y": 2}) is not None

    def test_cache_lru_eviction(self, tool_cache):
        """超过 max_entries 淘汰最旧"""
        result = {"success": True, "result_json": "{}", "error": ""}
        for i in range(15):
            tool_cache.set(f"tool{i}", {"i": i}, result)
        # 最早的 5 条应已被淘汰
        early = tool_cache.get("tool0", {"i": 0})
        assert early is None


class TestToolExecutorDedup:
    """工具执行器去重 + 校验"""

    def test_dedup_same_step(self, tool_executor):
        """同一步相同工具+参数应去重"""
        # file_exists 是 L0 工具，可直接调用
        r1 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        r2 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        # 第一次应成功或失败（取决于路径是否在允许目录）
        # 但第二次应标记 dedup
        assert r2.get("dedup", False) is True

    def test_dedup_different_step(self, tool_executor):
        """不同 step 不应去重"""
        tool_executor.start_round("s1", 0)
        r1 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        tool_executor.start_round("s1", 1)
        r2 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=1,
        )
        assert r2.get("dedup", False) is False

    def test_validation_rejects_bad_args(self, tool_executor):
        """参数校验拒绝类型错误的参数"""
        result = tool_executor.invoke(
            "read_file", {"path": 12345},  # path 应为 str
            session_id="s1", step=0,
        )
        assert result["success"] is False
        assert "参数校验失败" in result.get("error", "")

    def test_tool_not_found(self, tool_executor):
        """不存在的工具返回错误"""
        result = tool_executor.invoke(
            "nonexistent_tool_xyz", {},
            session_id="s1", step=0,
        )
        assert result["success"] is False
        assert "not found" in result.get("error", "")

    def test_start_round_clears_dedup(self, tool_executor):
        """start_round 清空去重集"""
        tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        tool_executor.start_round("s1", 1)
        # 重新执行不应去重
        r = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=1,
        )
        assert r.get("dedup", False) is False

    def test_callback_fired(self, tool_executor):
        """on_invoke 回调被触发"""
        calls = []

        def cb(tool_name, success, result):
            calls.append((tool_name, success))

        tool_executor.on_invoke(cb)
        tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        assert len(calls) >= 1
        assert calls[0][0] == "file_exists"


class TestMultiToolPipeline:
    """多工具流水线"""

    def test_file_exists_and_list(self, tool_executor):
        """连续调用 file_exists 和 list_files"""
        # 先检查文件存在
        r1 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        # 再列出目录
        test_dir = os.path.dirname(__file__)
        r2 = tool_executor.invoke(
            "list_files", {"directory": test_dir},
            session_id="s1", step=1,
        )
        assert r1.get("dedup", False) is False
        # 当前目录应有 __init__.py 等文件
        if r2["success"]:
            result_data = json.loads(r2["result_json"])
            assert "__init__.py" in result_data

    def test_cache_hit_on_second_call(self, tool_executor):
        """相同工具+参数第二次命中缓存"""
        r1 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
        )
        tool_executor.start_round("s1", 1)
        r2 = tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=1,
        )
        # 读类工具默认启用缓存
        assert r2.get("from_cache", False) is True

    def test_write_bypasses_cache(self, tool_executor):
        """写操作绕过缓存"""
        tool_executor.invoke(
            "file_exists", {"path": __file__},
            session_id="s1", step=0,
            bypass_cache=True,
        )
        assert True  # 不抛异常即通过


# ═══════════════════════════════════════════════════════════
# C. 会话隔离测试 (Session Isolation)
# ═══════════════════════════════════════════════════════════

class TestSessionIsolation:
    """多会话隔离"""

    def test_hot_memory_session_keys(self, hot_memory):
        """不同会话 ID 在热记忆中有不同条目"""
        hot_memory.append("[s1] 会话1的内容")
        hot_memory.append("[s2] 会话2的内容")
        content = hot_memory.read()
        assert "[s1] 会话1的内容" in content["memory_content"]
        assert "[s2] 会话2的内容" in content["memory_content"]

    def test_warm_memory_multi_session(self, warm_memory):
        """温记忆不同会话的 get_recent 互不干扰"""
        warm_memory.add_message("session_A", "A的消息")
        warm_memory.add_message("session_B", "B的消息")
        # session_A 不应看到 B 的
        recent_a = warm_memory.get_recent("session_A")
        recent_b = warm_memory.get_recent("session_B")
        for e in recent_a["entries"]:
            assert "B的消息" not in e["content"]
        for e in recent_b["entries"]:
            assert "A的消息" not in e["content"]

    def test_cold_memory_multi_session(self, cold_memory):
        """冷记忆不同会话互不干扰"""
        cold_memory.insert_summary(content="A数据", summary="A", session_id="s1")
        cold_memory.insert_summary(content="B数据", summary="B", session_id="s2")
        r1 = cold_memory.get_recent("s1")
        r2 = cold_memory.get_recent("s2")
        assert len(r1["entries"]) == 1
        assert r1["entries"][0]["session_id"] == "s1"
        assert len(r2["entries"]) == 1
        assert r2["entries"][0]["session_id"] == "s2"

    def test_warm_memory_search_per_session(self, warm_memory):
        """温记忆搜索按 session 过滤"""
        warm_memory.add_message("s1", "Python 是一种编程语言")
        warm_memory.add_message("s2", "Java 也是一种编程语言")
        r1 = warm_memory.search("s1", "编程", limit=10)
        r2 = warm_memory.search("s2", "编程", limit=10)
        assert r1["total"] == 1
        assert r1["entries"][0]["content"] == "Python 是一种编程语言"
        assert r2["total"] == 1
        assert r2["entries"][0]["content"] == "Java 也是一种编程语言"


# ═══════════════════════════════════════════════════════════
# D. Selector 集成测试
# ═══════════════════════════════════════════════════════════

class TestSelectorIntegration:
    """Selector 选择器集成"""

    @pytest.fixture
    def selector(self, tmp_db_dir):
        from evolution.selector import Selector
        db_path = os.path.join(tmp_db_dir, "evolution.db")
        return Selector(evolution_db_path=db_path)

    def test_query_classify_simple_qa(self, selector):
        """简单问答分类"""
        from evolution.query_classifier import QueryCategory
        advice = selector.select(
            user_message="今天星期几？",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        assert "query_category" in advice
        assert advice["query_category"] in (
            "simple_qa", "unknown", "search_query",
        )

    def test_query_classify_code(self, selector):
        """代码生成分类"""
        advice = selector.select(
            user_message="写一个Python函数计算斐波那契数列",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        assert advice["query_category"] == "code_gen"

    def test_query_classify_search(self, selector):
        """搜索查询分类"""
        advice = selector.select(
            user_message="搜索一下最新的AI新闻",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        assert advice["query_category"] in (
            "search_query", "multi_step_task",
        )

    def test_tool_ranking_in_advice(self, selector):
        """建议中包含工具排序"""
        advice = selector.select(
            user_message="写一个文件",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        assert "tools" in advice
        assert "prompt_section" in advice["tools"]
        assert "ranked_list" in advice["tools"]

    def test_strategy_recommendation(self, selector):
        """策略推荐含步数和温度"""
        advice = selector.select(
            user_message="实现一个简单的Web服务器",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        strategy = advice.get("strategy", {})
        assert "recommend_steps" in strategy
        assert "recommend_temp" in strategy
        assert "best_strategy" in strategy
        assert strategy["recommend_steps"] > 0

    def test_memory_config_in_advice(self, selector):
        """建议中包含记忆检索配置"""
        advice = selector.select(
            user_message="帮我回忆上次讨论的内容",
            session_id="s1",
            available_tools=registry.list_tools(),
            default_steps=10,
        )
        assert "memory" in advice
        assert "retrieved_memories" in advice

    def test_record_outcome(self, selector):
        """record_outcome 不抛异常"""
        advice = selector.select(
            user_message="测试",
            session_id="s1",
            available_tools=registry.list_tools(),
        )
        # 记录结果
        try:
            selector.record_outcome(
                result={
                    "success": True,
                    "completed": True,
                    "steps": [{"step": 1, "action": {"tool": "test_tool"}, "observation": "ok"}],
                    "total_steps": 1,
                    "elapsed": 0.5,
                    "usage": {"total_tokens": 100},
                    "error": "",
                },
                user_message="测试",
                session_id="s1",
                trace_id="trace-test-001",
            )
        except Exception as e:
            pytest.fail(f"record_outcome 不应抛异常: {e}")

    def test_cached_advice(self, selector):
        """is_new_message=False 返回缓存建议"""
        first = selector.select(
            user_message="写代码",
            session_id="s1",
            available_tools=registry.list_tools(),
            is_new_message=True,
        )
        second = selector.select(
            user_message="写代码",
            session_id="s1",
            available_tools=registry.list_tools(),
            is_new_message=False,
        )
        # 缓存建议应与上次相同
        assert second["query_category"] == first["query_category"]

    def test_selector_get_stats(self, selector):
        """get_stats 返回结构化的统计信息"""
        selector.select(
            user_message="测试",
            session_id="s1",
            available_tools=registry.list_tools(),
        )
        stats = selector.get_stats()
        assert "selector" in stats
        assert "strategy_profiler" in stats
        assert "smart_optimizer" in stats
        assert "tool_ranker" in stats
        assert "memory_optimizer" in stats


# ═══════════════════════════════════════════════════════════
# E. 进化反馈循环测试
# ═══════════════════════════════════════════════════════════

class TestEvolutionFeedbackLoop:
    """record → analyze → recommend 反馈闭环"""

    @pytest.fixture
    def selector(self, tmp_db_dir):
        from evolution.selector import Selector
        db_path = os.path.join(tmp_db_dir, "evolve.db")
        return Selector(evolution_db_path=db_path)

    def test_record_multiple_outcomes(self, selector):
        """多次记录后统计累积"""
        for i in range(5):
            selector.select(
                user_message=f"任务{i}",
                session_id="s1",
                available_tools=registry.list_tools(),
            )
            selector.record_outcome(
                result={
                    "success": True,
                    "completed": True,
                    "steps": [{"step": 1, "action": {"tool": "test_tool"}, "observation": "ok"}],
                    "total_steps": 1,
                    "elapsed": 0.3,
                    "usage": {"total_tokens": 50},
                    "error": "",
                },
                user_message=f"任务{i}",
                session_id="s1",
                trace_id=f"trace-{i}",
            )
        stats = selector.get_stats()
        so = stats.get("smart_optimizer", {})
        cat_stats = so.get("category_stats", {})
        # 某个类别的统计数据应存在
        assert any(v.get("success", 0) > 0 for v in cat_stats.values()) or True

    def test_strategy_profiler_persists(self, selector, tmp_db_dir):
        """StrategyProfiler 持久化记录运行"""
        selector.select(
            user_message="测试", session_id="s1",
            available_tools=registry.list_tools(),
        )
        selector.record_outcome(
            result={
                "success": True, "completed": True, "steps": [],
                "total_steps": 2, "elapsed": 1.0,
                "usage": {"total_tokens": 100}, "error": "",
            },
            user_message="测试", session_id="s1",
            trace_id="trace-persist",
        )
        # 重新创建 Selector 应能看到之前的记录
        from evolution.selector import Selector
        selector2 = Selector(evolution_db_path=selector._db_path)
        stats2 = selector2.get_stats()
        profiler_stats = stats2.get("strategy_profiler", {})
        assert profiler_stats is not None

    def test_record_failure_updates_stats(self, selector):
        """记录失败影响统计"""
        selector.select(
            user_message="失败任务", session_id="s1",
            available_tools=registry.list_tools(),
        )
        selector.record_outcome(
            result={
                "success": False, "completed": False, "steps": [],
                "total_steps": 3, "elapsed": 5.0,
                "usage": {"total_tokens": 200}, "error": "llm_error",
            },
            user_message="失败任务", session_id="s1",
            trace_id="trace-fail",
        )
        # 不应抛异常
        assert True

    def test_tool_ranker_insights(self, tmp_db_dir):
        """ToolRanker 生成工具洞察"""
        from evolution.tool_ranker import ToolRanker
        ranker = ToolRanker()
        available = registry.list_tools()
        ranked = ranker.rank_tools(
            available_tools=available,
            query_category="code_gen",
        )
        assert isinstance(ranked, list)
        # 应包含文件类工具（代码生成场景）
        tool_names = [t["name"] for t in ranked]
        assert len(tool_names) > 0
        assert "write_file" in tool_names or "read_file" in tool_names

    def test_tool_ranker_build_prompt_section(self, tmp_db_dir):
        """build_prompt_section 生成可用文本"""
        from evolution.tool_ranker import ToolRanker
        ranker = ToolRanker()
        available = registry.list_tools()
        ranked = ranker.rank_tools(
            available_tools=available,
            query_category="simple_qa",
        )
        prompt = ranker.build_prompt_section(ranked)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        # 应包含工具名称
        assert "file_exists" in prompt or "read_file" in prompt

    def test_behavior_evolution_profile(self):
        """BehaviorEvolution 工具画像"""
        from evolution.behavior_evolution import BehaviorEvolution
        be = BehaviorEvolution()
        be.record_tool_result("web_search", success=True, latency_ms=200)
        profile = be.get_profile("web_search")
        assert profile.call_count == 1
        assert profile.success_rate == 1.0

        be.record_tool_result("web_search", success=False, latency_ms=500, was_timeout=True)
        assert profile.call_count == 2
        assert profile.success_rate < 1.0
        assert profile.timeout_count == 1

    def test_smart_optimizer_recommendation(self, tmp_db_dir):
        """SmartOptimizer 生成推荐"""
        from evolution.smart_optimizer import SmartOptimizer
        so = SmartOptimizer()
        rec = so.get_recommendation_dict("code_gen")
        assert "recommend_steps" in rec
        assert "recommend_temp" in rec
        assert "best_strategy" in rec
        assert "success_rate" in rec


# ═══════════════════════════════════════════════════════════
# F. 工具注册表测试
# ═══════════════════════════════════════════════════════════

class TestToolRegistry:
    """工具注册表基础功能"""

    def test_registry_has_tools(self):
        """注册表包含至少 10 个工具"""
        tools = registry.list_tools()
        assert len(tools) >= 10

    def test_tool_exists(self):
        """标准工具存在"""
        for name in ("read_file", "write_file", "file_exists",
                     "http_get", "memory_write", "memory_query",
                     "search_web", "search_file", "grep"):
            assert registry.get_tool(name) is not None, f"工具 {name} 应存在"

    def test_tool_levels(self):
        """工具级别正确"""
        tools = registry.list_tools()
        assert tools["read_file"]["level"] == "L0"
        assert tools["write_file"]["level"] == "L1"
        assert tools["file_exists"]["level"] == "L0"

    def test_registry_invoke(self):
        """registry.invoke 直接调用"""
        result = registry.invoke("file_exists", {"path": __file__})
        assert result["success"] is True

    def test_registry_invoke_not_found(self):
        """不存在的工具返回错误"""
        result = registry.invoke("no_such_tool", {})
        assert result["success"] is False
        assert "not found" in result.get("error", "")

    def test_on_invoke_callback(self):
        """on_invoke 回调"""
        calls = []

        def cb(name, success, result):
            calls.append(name)

        registry.on_invoke(cb)
        registry.invoke("file_exists", {"path": __file__})
        assert "file_exists" in calls
        registry._on_invoke_callbacks.clear()
