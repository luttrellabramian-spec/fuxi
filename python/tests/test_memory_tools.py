"""伏羲 记忆工具测试 — memory_write / memory_query / memory_get_recent"""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
import tools.memory_tools as memory_tools


@pytest.fixture(autouse=True)
def reset_memory_cache():
    """每个测试前后重置 memory_tools 模块级单例缓存。

    注意：_get_memories() 只检查 _hot is None，因此必须同时设置三个
    变量才能阻止懒加载覆盖 mock。
    """
    memory_tools._hot = MagicMock()
    memory_tools._warm = MagicMock()
    memory_tools._cold = MagicMock()
    yield
    memory_tools._hot = None
    memory_tools._warm = None
    memory_tools._cold = None


# ════════════════════════════════════════════════════════════════════
# 1. memory_write
# ════════════════════════════════════════════════════════════════════


class TestMemoryWrite:
    def test_writes_to_hot(self):
        """memory_type=hot 应调用 _hot.write。"""
        mock_hot = MagicMock()
        mock_hot.write.return_value = {"success": True}
        memory_tools._hot = mock_hot

        result = memory_tools.memory_write(
            memory_type="hot", content="hello", session_id="s1"
        )

        mock_hot.write.assert_called_once_with("hello")
        assert result["success"] is True
        assert result["elapsed_ms"] == 10

    def test_writes_to_warm(self):
        """memory_type=warm 应调用 _warm.add_message。"""
        mock_warm = MagicMock()
        mock_warm.add_message.return_value = {"success": True, "id": 1}
        memory_tools._warm = mock_warm

        result = memory_tools.memory_write(
            memory_type="warm", content="hello", session_id="s1"
        )

        mock_warm.add_message.assert_called_once_with(
            session_id="s1", content="hello"
        )
        assert result["success"] is True
        assert result["id"] == 1

    def test_writes_to_cold(self):
        """memory_type=cold 应调用 _cold.insert_summary。"""
        mock_cold = MagicMock()
        mock_cold.insert_summary.return_value = {"success": True, "id": "abc"}
        memory_tools._cold = mock_cold

        result = memory_tools.memory_write(
            memory_type="cold", content="x", session_id="s1"
        )

        mock_cold.insert_summary.assert_called_once_with(
            content="x", summary="x"
        )
        assert result["id"] == "abc"

    def test_unknown_type_returns_error(self):
        """未知 memory_type 应返回 success=False。"""
        result = memory_tools.memory_write(
            memory_type="unknown", content="x", session_id="s1"
        )
        assert result["success"] is False
        assert "Unknown memory_type" in result["error"]

    def test_exception_returns_error(self):
        """底层抛异常时应被捕获并返回 error 字典。"""
        mock_hot = MagicMock()
        mock_hot.write.side_effect = RuntimeError("boom")
        memory_tools._hot = mock_hot

        result = memory_tools.memory_write(
            memory_type="hot", content="x", session_id="s1"
        )
        assert result["success"] is False
        assert "boom" in result["error"]

    def test_default_values(self):
        """memory_type 默认为 hot，session_id 默认为 default。"""
        mock_hot = MagicMock()
        mock_hot.write.return_value = {"success": True}
        memory_tools._hot = mock_hot

        memory_tools.memory_write(content="x")
        # 验证默认参数
        mock_hot.write.assert_called_once_with("x")


# ════════════════════════════════════════════════════════════════════
# 2. memory_query
# ════════════════════════════════════════════════════════════════════


class TestMemoryQuery:
    def test_query_hot(self):
        """查询 hot 应返回 hot 读结果。"""
        mock_hot = MagicMock()
        mock_hot.read.return_value = {"memory_content": "hot data", "char_count": 8}
        memory_tools._hot = mock_hot

        result = memory_tools.memory_query(memory_type="hot", session_id="s1")
        assert result["success"] is True
        assert result["memory_type"] == "hot"
        assert result["memory_content"] == "hot data"

    def test_query_warm_with_query(self):
        """warm 带 query 应调用 _warm.search。"""
        mock_warm = MagicMock()
        mock_warm.search.return_value = {"entries": [{"id": 1, "content": "x"}]}
        memory_tools._warm = mock_warm

        result = memory_tools.memory_query(
            memory_type="warm", session_id="s1", query="hi", limit=5
        )
        mock_warm.search.assert_called_once_with(
            session_id="s1", query="hi", limit=5
        )
        assert result["entries"][0]["id"] == 1

    def test_query_warm_without_query_uses_recent(self):
        """warm 不带 query 应调用 _warm.get_recent。"""
        mock_warm = MagicMock()
        mock_warm.get_recent.return_value = {"entries": []}
        memory_tools._warm = mock_warm

        memory_tools.memory_query(memory_type="warm", session_id="s1", limit=10)
        mock_warm.get_recent.assert_called_once_with(session_id="s1", limit=10)

    def test_query_cold_with_query(self):
        """cold 带 query 应调用 _cold.search_similar。"""
        mock_cold = MagicMock()
        mock_cold.search_similar.return_value = {"memories": []}
        memory_tools._cold = mock_cold

        memory_tools.memory_query(
            memory_type="cold", session_id="s1", query="q", limit=3
        )
        mock_cold.search_similar.assert_called_once_with(
            query="q", limit=3, session_id="s1"
        )

    def test_query_cold_without_query_uses_recent(self):
        """cold 不带 query 应调用 _cold.get_recent。"""
        mock_cold = MagicMock()
        mock_cold.get_recent.return_value = {"memories": []}
        memory_tools._cold = mock_cold

        memory_tools.memory_query(memory_type="cold", session_id="s1", limit=5)
        mock_cold.get_recent.assert_called_once_with(session_id="s1", limit=5)

    def test_unknown_type_returns_error(self):
        """未知 memory_type 应返回 success=False。"""
        result = memory_tools.memory_query(memory_type="xx", session_id="s1")
        assert result["success"] is False
        assert "Unknown memory_type" in result["error"]

    def test_exception_returns_error(self):
        """底层抛异常时应被捕获。"""
        mock_hot = MagicMock()
        mock_hot.read.side_effect = RuntimeError("db down")
        memory_tools._hot = mock_hot

        result = memory_tools.memory_query(memory_type="hot", session_id="s1")
        assert result["success"] is False
        assert "db down" in result["error"]


# ════════════════════════════════════════════════════════════════════
# 3. memory_get_recent
# ════════════════════════════════════════════════════════════════════


class TestMemoryGetRecent:
    def test_get_recent_hot(self):
        """hot 应直接返回 _hot.read。"""
        mock_hot = MagicMock()
        mock_hot.read.return_value = {"memory_content": "recent"}
        memory_tools._hot = mock_hot

        result = memory_tools.memory_get_recent(memory_type="hot", limit=5)
        assert result["success"] is True
        assert result["memory_type"] == "hot"
        assert result["memory_content"] == "recent"

    def test_get_recent_warm(self):
        """warm 应调用 _warm.get_recent。"""
        mock_warm = MagicMock()
        mock_warm.get_recent.return_value = {"entries": []}
        memory_tools._warm = mock_warm

        result = memory_tools.memory_get_recent(
            memory_type="warm", session_id="s1", limit=20
        )
        mock_warm.get_recent.assert_called_once_with(session_id="s1", limit=20)
        assert result["memory_type"] == "warm"

    def test_get_recent_cold(self):
        """cold 应调用 _cold.get_recent。"""
        mock_cold = MagicMock()
        mock_cold.get_recent.return_value = {"memories": []}
        memory_tools._cold = mock_cold

        result = memory_tools.memory_get_recent(
            memory_type="cold", session_id="s1", limit=3
        )
        mock_cold.get_recent.assert_called_once_with(session_id="s1", limit=3)
        assert result["memory_type"] == "cold"

    def test_unknown_type_returns_error(self):
        """未知 memory_type 应返回 success=False。"""
        result = memory_tools.memory_get_recent(memory_type="xx", session_id="s1")
        assert result["success"] is False
        assert "Unknown memory_type" in result["error"]

    def test_exception_returns_error(self):
        """底层抛异常时应被捕获。"""
        mock_warm = MagicMock()
        mock_warm.get_recent.side_effect = RuntimeError("x")
        memory_tools._warm = mock_warm

        result = memory_tools.memory_get_recent(
            memory_type="warm", session_id="s1", limit=5
        )
        assert result["success"] is False


# ════════════════════════════════════════════════════════════════════
# 4. 懒加载 _get_memories
# ════════════════════════════════════════════════════════════════════


class TestGetMemories:
    def test_lazy_init_creates_all_three(self):
        """首次调用应创建 hot/warm/cold 三个实例。"""
        # 重置到 None 以触发懒加载
        memory_tools._hot = None
        memory_tools._warm = None
        memory_tools._cold = None

        with patch("memory.hot_memory.HotMemory") as mock_hot_cls, \
             patch("memory.warm_memory.WarmMemory") as mock_warm_cls, \
             patch("memory.cold_memory.ColdMemory") as mock_cold_cls:
            mock_hot_cls.return_value = MagicMock()
            mock_warm_cls.return_value = MagicMock()
            mock_cold_cls.return_value = MagicMock()

            memory_tools._get_memories()

            mock_hot_cls.assert_called_once()
            mock_warm_cls.assert_called_once()
            mock_cold_cls.assert_called_once()
            assert memory_tools._hot is not None
            assert memory_tools._warm is not None
            assert memory_tools._cold is not None

    def test_lazy_init_reuses_existing(self):
        """二次调用应复用已存在的实例（_hot 非 None 时跳过）。"""
        existing_hot = MagicMock()
        memory_tools._hot = existing_hot
        memory_tools._warm = MagicMock()
        memory_tools._cold = MagicMock()

        with patch("memory.hot_memory.HotMemory") as mock_cls:
            memory_tools._get_memories()
            mock_cls.assert_not_called()
        assert memory_tools._hot is existing_hot
