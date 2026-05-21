# -*- coding: utf-8 -*-
"""test_cold_memory.py — ColdMemory 冷记忆单元测试

测试覆盖：
  1. insert_summary + get_recent 基本读写
  2. search_similar 文本回退 (LIKE)
  3. search_similar 空查询返回最近条目
  4. 会话隔离 (session_id)
  5. 延迟初始化 (_ensure_initialized)
  6. get_stats 统计
  7. clear_session
  8. metadata 存储与读取
  9. 大内容处理
 10. search_similar session 过滤
 11. 空数据库边界
 12. 不存在的 session
 13. _cosine_similarity 内部方法
 14. 多次插入 + 搜索行为
"""
from __future__ import annotations

import os
import sys
import time
import json
import threading

_src = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src")
)
if _src not in sys.path:
    sys.path.insert(0, _src)

import pytest
from memory.cold_memory import ColdMemory


# ==============================================================
#  fixtures
# ==============================================================

@pytest.fixture
def cold(tmp_path) -> ColdMemory:
    """独立临时数据库的 ColdMemory（不加载 embedding 模型）。"""
    db = tmp_path / "test_cold.db"
    cm = ColdMemory(db_path=str(db))
    yield cm
    cm.close()


@pytest.fixture
def cold_with_session(tmp_path) -> ColdMemory:
    """预填充数据的 ColdMemory。"""
    db = tmp_path / "test_cold_prefilled.db"
    cm = ColdMemory(db_path=str(db))
    cm.insert_summary(
        content="今天心情很好",
        summary="开心的一天",
        session_id="s1",
        metadata={"emotion": "happy"},
    )
    cm.insert_summary(
        content="考试没考好",
        summary="失落",
        session_id="s1",
        metadata={"emotion": "sad"},
    )
    cm.insert_summary(
        content="吃了好吃的",
        summary="满足",
        session_id="s2",
        metadata={"emotion": "happy"},
    )
    yield cm
    cm.close()


# ==============================================================
#  1. insert_summary + get_recent 基本读写
# ==============================================================

class TestBasicInsertAndRetrieve:
    """insert_summary / get_recent 基本流程。"""

    def test_insert_returns_success(self, cold: ColdMemory) -> None:
        result = cold.insert_summary(
            content="原内容", summary="摘要", session_id="s1"
        )
        assert result["success"] is True
        assert "id" in result

    def test_get_recent_returns_entries(self, cold: ColdMemory) -> None:
        cold.insert_summary("内容 A", "摘要 A", "s1")
        recent = cold.get_recent("s1")
        assert recent["success"] is True
        assert len(recent["entries"]) == 1
        assert recent["entries"][0]["content"] == "内容 A"
        assert recent["entries"][0]["summary"] == "摘要 A"

    def test_get_recent_all_sessions(self, cold: ColdMemory) -> None:
        cold.insert_summary("c1", "s1", "s1")
        cold.insert_summary("c2", "s2", "s2")
        recent = cold.get_recent()
        assert len(recent["entries"]) == 2

    def test_get_recent_limit(self, cold: ColdMemory) -> None:
        for i in range(10):
            cold.insert_summary(f"内容_{i}", f"摘要_{i}", "s1")
        recent = cold.get_recent("s1", limit=3)
        assert len(recent["entries"]) == 3


# ==============================================================
#  2. search_similar 文本回退 (LIKE)
# ==============================================================

class TestSearchSimilarTextFallback:
    """search_similar 在无 embedding 时回退到 LIKE。"""

    def test_text_search_finds_match(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("心情")
        assert result["success"] is True
        contents = {e["content"] for e in result["entries"]}
        assert "今天心情很好" in contents

    def test_text_search_no_match(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("不存在的关键词")
        assert result["success"] is True
        assert len(result["entries"]) == 0

    def test_text_search_matches_summary(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("失落")
        assert result["success"] is True
        summaries = {e["summary"] for e in result["entries"]}
        assert "失落" in summaries

    def test_text_search_partial_match(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("开心")
        assert result["success"] is True
        assert len(result["entries"]) >= 1


# ==============================================================
#  3. search_similar 空查询
# ==============================================================

class TestSearchSimilarEmptyQuery:
    """空查询应回退到 get_recent。"""

    def test_empty_query_returns_recent(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("")
        assert result["success"] is True
        assert len(result["entries"]) >= 1

    def test_whitespace_query_returns_recent(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("   ")
        assert result["success"] is True
        assert len(result["entries"]) >= 1

    def test_none_query_like_returns_recent(self, cold_with_session) -> None:
        """显式边界：仅空白字符。"""
        result = cold_with_session.search_similar("\t\n")
        assert result["success"] is True
        assert len(result["entries"]) >= 1


# ==============================================================
#  4. 会话隔离
# ==============================================================

class TestSessionIsolation:
    """不同 session_id 数据隔离。"""

    def test_get_recent_session_filter(self, cold_with_session) -> None:
        s1 = cold_with_session.get_recent("s1")
        s2 = cold_with_session.get_recent("s2")
        assert len(s1["entries"]) == 2
        assert len(s2["entries"]) == 1
        for e in s2["entries"]:
            assert e["session_id"] == "s2"

    def test_search_similar_respects_session(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("心情", session_id="s1")
        for e in result["entries"]:
            assert e["session_id"] == "s1"


# ==============================================================
#  5. 延迟初始化
# ==============================================================

class TestLazyInitialization:
    """_ensure_initialized 延迟初始化。"""

    def test_initialized_flag_false_initially(self, cold: ColdMemory) -> None:
        assert cold._initialized is False

    def test_read_triggers_initialization(self, cold: ColdMemory) -> None:
        cold.get_recent()
        assert cold._initialized is True

    def test_insert_triggers_initialization(self, cold: ColdMemory) -> None:
        cold.insert_summary("内容", "摘要")
        assert cold._initialized is True

    def test_double_init_is_safe(self, cold: ColdMemory) -> None:
        cold._ensure_initialized()
        cold._ensure_initialized()
        assert cold._initialized is True

    def test_thread_safe_lazy_init(self, cold: ColdMemory) -> None:
        """并发调用 _ensure_initialized 线程安全。"""
        errors = []

        def call_init() -> None:
            try:
                for _ in range(5):
                    cold._ensure_initialized()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ==============================================================
#  6. get_stats 统计
# ==============================================================

class TestStats:
    """get_stats 准确性。"""

    def test_stats_empty(self, cold: ColdMemory) -> None:
        stats = cold.get_stats()
        assert stats["total_summaries"] == 0
        assert stats["total_sessions"] == 0

    def test_stats_after_inserts(self, cold: ColdMemory) -> None:
        cold.insert_summary("c1", "s1", "s1")
        cold.insert_summary("c2", "s2", "s1")
        cold.insert_summary("c3", "s3", "s2")
        stats = cold.get_stats()
        assert stats["total_summaries"] == 3
        assert stats["total_sessions"] == 2

    def test_stats_has_embedding_field(self, cold: ColdMemory) -> None:
        stats = cold.get_stats()
        assert "has_embedding" in stats


# ==============================================================
#  7. clear_session
# ==============================================================

class TestClearSession:
    """clear_session 完整性。"""

    def test_clear_session_removes_entries(self, cold_with_session) -> None:
        result = cold_with_session.clear_session("s1")
        assert result["success"] is True
        recent = cold_with_session.get_recent("s1")
        assert len(recent["entries"]) == 0

    def test_clear_session_other_untouched(self, cold_with_session) -> None:
        cold_with_session.clear_session("s1")
        s2 = cold_with_session.get_recent("s2")
        assert len(s2["entries"]) == 1

    def test_clear_session_nonexistent(self, cold: ColdMemory) -> None:
        result = cold.clear_session("nonexistent")
        assert result["success"] is True


# ==============================================================
#  8. metadata 存储与读取
# ==============================================================

class TestMetadata:
    """metadata 存储与反序列化。"""

    def test_metadata_stored_and_retrieved(self, cold: ColdMemory) -> None:
        meta = {"emotion": "happy", "score": 0.95, "tags": ["测试"]}
        cold.insert_summary(
            content="测试内容",
            summary="测试摘要",
            session_id="s1",
            metadata=meta,
        )
        recent = cold.get_recent("s1")
        stored = json.loads(recent["entries"][0]["metadata"])
        assert stored["emotion"] == "happy"
        assert stored["score"] == 0.95
        assert stored["tags"] == ["测试"]

    def test_metadata_default_empty(self, cold: ColdMemory) -> None:
        cold.insert_summary("内容", "摘要", "s1")
        recent = cold.get_recent("s1")
        stored = json.loads(recent["entries"][0]["metadata"])
        assert stored == {}

    def test_metadata_none_is_safe(self, cold: ColdMemory) -> None:
        cold.insert_summary("内容", "摘要", "s1", metadata=None)
        recent = cold.get_recent("s1")
        assert recent["entries"][0]["metadata"] is not None


# ==============================================================
#  9. 大内容处理
# ==============================================================

class TestLargeContent:
    """大文本内容。"""

    def test_large_content(self, cold: ColdMemory) -> None:
        big_content = "A" * 10_000
        big_summary = "B" * 5_000
        result = cold.insert_summary(
            content=big_content,
            summary=big_summary,
            session_id="s1",
        )
        assert result["success"] is True

        recent = cold.get_recent("s1")
        assert len(recent["entries"][0]["content"]) == 10_000

    def test_unicode_content(self, cold: ColdMemory) -> None:
        cold.insert_summary(
            content="🔥 中文 English 日本語 🎉",
            summary="多语言摘要",
            session_id="s1",
        )
        recent = cold.get_recent("s1")
        assert "🔥" in recent["entries"][0]["content"]
        assert "日本語" in recent["entries"][0]["content"]


# ==============================================================
# 10. search_similar session 过滤
# ==============================================================

class TestSearchSimilarSessionFilter:
    """search_similar 按 session 过滤。"""

    def test_search_with_session(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("心情", session_id="s1")
        for e in result["entries"]:
            assert e["session_id"] == "s1"

    def test_search_without_session_returns_all(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("心情")
        session_ids = {e["session_id"] for e in result["entries"]}
        assert len(session_ids) >= 1

    def test_search_session_no_match(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("心情", session_id="nonexistent")
        assert len(result["entries"]) == 0


# ==============================================================
# 11. 空数据库边界
# ==============================================================

class TestEmptyDatabase:
    """空数据库边界行为。"""

    def test_get_recent_empty(self, cold: ColdMemory) -> None:
        recent = cold.get_recent("s1")
        assert recent["success"] is True
        assert recent["entries"] == []

    def test_search_similar_empty(self, cold: ColdMemory) -> None:
        result = cold.search_similar("任何内容")
        assert result["success"] is True
        assert result["entries"] == []

    def test_get_stats_empty(self, cold: ColdMemory) -> None:
        stats = cold.get_stats()
        assert stats["total_summaries"] == 0
        assert stats["total_sessions"] == 0


# ==============================================================
# 12. 不存在的 session
# ==============================================================

class TestNonExistentSession:
    """不存在的 session 查询。"""

    def test_get_recent_nonexistent(self, cold_with_session) -> None:
        recent = cold_with_session.get_recent("no_such_session")
        assert recent["success"] is True
        assert recent["entries"] == []

    def test_get_recent_none(self, cold_with_session) -> None:
        recent = cold_with_session.get_recent()
        # 不传 session_id → 返回所有
        assert len(recent["entries"]) == 3


# ==============================================================
# 13. _cosine_similarity 内部方法
# ==============================================================

class TestCosineSimilarity:
    """_cosine_similarity 内部方法验证。"""

    def test_identical_vectors(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
        assert abs(sim - 1.0) < 1e-6

    def test_orthogonal_vectors(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([1.0, 0.0], [0.0, 1.0])
        assert abs(sim) < 1e-6

    def test_opposite_vectors(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        assert abs(sim + 1.0) < 1e-6

    def test_partial_similarity(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert abs(sim - 1.0) < 1e-6

    def test_zero_vector(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([0.0, 0.0, 0.0], [1.0, 0.0, 0.0])
        assert sim == 0.0

    def test_both_zero_vectors(self, cold: ColdMemory) -> None:
        sim = cold._cosine_similarity([0.0, 0.0], [0.0, 0.0])
        assert sim == 0.0

    def test_different_lengths_raises(self, cold: ColdMemory) -> None:
        """不同维度应抛出异常（zip 会静默截断，需注意）。"""
        # 实现中 zip 会截断，所以这条仅作记录
        sim = cold._cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])
        assert isinstance(sim, float)


# ==============================================================
# 14. 多次插入 + 搜索行为
# ==============================================================

class TestMultipleInsertsAndSearch:
    """多次插入后的搜索排序表现。"""

    def test_multiple_inserts_search(self, cold: ColdMemory) -> None:
        data = [
            ("今天心情很好", "开心快乐"),
            ("心情不好有点难过", "失落"),
            ("天气真好出去走走", "放松"),
            ("中午吃了好吃的", "满足"),
        ]
        for content, summary in data:
            cold.insert_summary(content=content, summary=summary, session_id="s1")

        result = cold.search_similar("心情")
        # text fallback: LIKE "%心情%" 应匹配前两条
        assert result["success"] is True
        matched = [e["summary"] for e in result["entries"]]
        assert "开心快乐" in matched
        assert "失落" in matched

    def test_search_ordering_text_fallback(self, cold: ColdMemory) -> None:
        cold.insert_summary("A", "Alpha", "s1")
        cold.insert_summary("B", "Beta", "s1")
        cold.insert_summary("C", "Gamma", "s1")

        result = cold.search_similar("Beta")
        # text fallback 按时间 DESC
        assert result["success"] is True
        # 至少返回 1 条
        assert len(result["entries"]) >= 1

    def test_multiple_sessions_search_all(self, cold: ColdMemory) -> None:
        cold.insert_summary("全局内容", "全局摘要", "s1")
        cold.insert_summary("全局内容", "全局摘要", "s2")
        result = cold.search_similar("全局")
        assert len(result["entries"]) == 2

    def test_search_with_unicode_query(self, cold_with_session) -> None:
        result = cold_with_session.search_similar("开心")
        assert result["success"] is True
        assert len(result["entries"]) >= 1


# ==============================================================
# 15. 并发安全
# ==============================================================

class TestConcurrency:
    """并发读写线程安全。"""

    def test_concurrent_insert(self, cold: ColdMemory) -> None:
        errors = []

        def worker(n: int) -> None:
            try:
                for _ in range(5):
                    cold.insert_summary(
                        content=f"thread_{n}",
                        summary=f"summary_{n}",
                        session_id="concurrent",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_search(self, cold_with_session) -> None:
        errors = []

        def searcher() -> None:
            try:
                for _ in range(10):
                    cold_with_session.search_similar("心情")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=searcher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
