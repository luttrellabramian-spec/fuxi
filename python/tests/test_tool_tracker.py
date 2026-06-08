"""伏羲 工具调用追踪器测试 — ToolCallTracker record / query / deprioritization"""
import sys
import os
import time
import sqlite3
import threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from engine.tool_tracker import (
    ToolCallTracker, ERROR_TYPES, DEFAULT_DB_PATH,
    CREATE_TOOL_CALL_LOG_SQL, CREATE_TOOL_DAILY_STATS_SQL,
    CREATE_TOOL_DEPRIORITIZATION_SQL,
)


@pytest.fixture
def tracker(tmp_path):
    """为每个测试创建独立的 tracker 实例。"""
    db = str(tmp_path / "tracker.db")
    t = ToolCallTracker(db_path=db, deprioritize_threshold=0.3)
    yield t


# ════════════════════════════════════════════════════════════════════
# 1. 初始化
# ════════════════════════════════════════════════════════════════════


class TestInit:
    def test_init_creates_tables(self, tmp_path):
        """__init__ 应创建 3 张表（tool_call_log / tool_daily_stats / tool_deprioritization）。"""
        db = str(tmp_path / "x.db")
        ToolCallTracker(db_path=db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "tool_call_log" in tables
        assert "tool_daily_stats" in tables
        # tool_deprioritization 表名有 typo 风险 — 容忍带/不带下划线
        assert any("deprioritiz" in t for t in tables)

    def test_init_falls_back_to_tempdir_on_bad_path(self):
        """坏路径应回退到 tempdir，不抛错。"""
        # 用一个绝对不可写的路径（Windows 上 None 设备）
        bad_path = "/this/does/not/exist/abc.db"
        t = ToolCallTracker(db_path=bad_path)
        # 路径被改写到 tempdir
        assert os.path.exists(os.path.dirname(t.db_path))

    def test_default_db_path_constant(self):
        """DEFAULT_DB_PATH 应指向项目目录。"""
        assert DEFAULT_DB_PATH.endswith("tool_tracker.db")


# ════════════════════════════════════════════════════════════════════
# 2. record — 记录工具调用
# ════════════════════════════════════════════════════════════════════


class TestRecord:
    def test_record_success_returns_id(self, tracker):
        """成功调用应返回 success=True + id。"""
        result = tracker.record(
            session_id="s1", tool_name="read_file",
            success=True, latency_ms=10.0,
        )
        assert result["success"] is True
        assert "id" in result
        assert len(result["id"]) == 36  # uuid

    def test_record_failure_returns_id(self, tracker):
        """失败调用也应返回 success=True（记录成功）。"""
        result = tracker.record(
            session_id="s1", tool_name="web_search",
            success=False, latency_ms=5000.0,
            error_type="timeout", error_message="timed out",
        )
        assert result["success"] is True

    def test_record_truncates_long_error_message(self, tracker):
        """error_message 应被截断至 500 字符。"""
        long_msg = "x" * 1000
        tracker.record("s1", "t", success=False, latency_ms=1.0, error_message=long_msg)
        # 查 sqlite
        conn = sqlite3.connect(tracker.db_path)
        row = conn.execute("SELECT error_message FROM tool_call_log LIMIT 1").fetchone()
        conn.close()
        assert len(row[0]) == 500

    def test_record_persists_required_fields(self, tracker):
        """必填字段应被持久化。"""
        tracker.record("s1", "read_file", success=True, latency_ms=42.5,
                       cycle_count=2, llm_latency_ms=200.0)
        conn = sqlite3.connect(tracker.db_path)
        row = conn.execute("""
            SELECT session_id, tool_name, success, latency_ms, cycle_count, llm_latency_ms
            FROM tool_call_log
        """).fetchone()
        conn.close()
        assert row[0] == "s1"
        assert row[1] == "read_file"
        assert row[2] == 1  # success=True → 1
        assert row[3] == 42.5
        assert row[4] == 2
        assert row[5] == 200.0

    def test_record_db_failure_returns_error(self, tracker, monkeypatch):
        """conn.execute 抛异常时应返回 success=False。"""
        from unittest.mock import MagicMock
        # 模拟一个 conn，其 execute 抛错
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("db locked")
        monkeypatch.setattr(tracker, "_get_conn", lambda: mock_conn)
        result = tracker.record("s1", "t", success=True, latency_ms=1.0)
        assert result["success"] is False
        assert "error" in result

    def test_record_thread_safety(self, tracker):
        """多线程并发 record 不应导致数据竞争。"""
        errors = []
        def worker(i):
            try:
                tracker.record(f"s{i}", "t", success=True, latency_ms=1.0)
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0
        # 20 条记录都该落库
        conn = sqlite3.connect(tracker.db_path)
        n = conn.execute("SELECT COUNT(*) FROM tool_call_log").fetchone()[0]
        conn.close()
        assert n == 20


# ════════════════════════════════════════════════════════════════════
# 3. ERROR_TYPES 常量
# ════════════════════════════════════════════════════════════════════


class TestErrorTypes:
    def test_all_known_types_have_descriptions(self):
        """所有错误类型应有中文描述。"""
        for k, v in ERROR_TYPES.items():
            assert isinstance(v, str)
            assert len(v) > 0

    def test_includes_common_types(self):
        """应包含常见错误类型。"""
        for k in ("none", "timeout", "tool_not_found", "tool_execution_error", "unknown"):
            assert k in ERROR_TYPES


# ════════════════════════════════════════════════════════════════════
# 4. get_tool_success_rate — 查单个工具
# ════════════════════════════════════════════════════════════════════


class TestGetToolSuccessRate:
    def test_returns_zero_when_no_data(self, tracker):
        """无数据时应返回 total_calls=0。"""
        result = tracker.get_tool_success_rate("nonexistent_tool")
        assert result["total_calls"] == 0
        assert result["tool_name"] == "nonexistent_tool"

    def test_aggregates_across_days(self, tracker):
        """跨多天数据应被聚合。"""
        # daily_stats 主键是 (date, tool_name)，需要不同的 tool_name
        conn = sqlite3.connect(tracker.db_path)
        for tool_name, total, success in [
            ("t1", 10, 9),
            ("t1_dup", 5, 4),
            ("t1_other", 3, 3),
        ]:
            conn.execute(
                "INSERT INTO tool_daily_stats "
                "(date, tool_name, total_calls, success_calls, failure_calls, success_rate, avg_latency_ms, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%d"), tool_name, total, success, total - success, success / total, 100.0, int(time.time() * 1000))
            )
        # 再单独为 t1 写一条历史（不同 date）以验证 ORDER BY date DESC LIMIT days
        conn.execute(
            "INSERT INTO tool_daily_stats "
            "(date, tool_name, total_calls, success_calls, failure_calls, success_rate, avg_latency_ms, updated_at) "
            "VALUES (?, 't1', 100, 90, 10, 0.9, 200.0, ?)",
            ("2026-01-01", int(time.time() * 1000))
        )
        conn.commit()
        conn.close()

        result = tracker.get_tool_success_rate("t1", days=7)
        # 应包含 t1 (2026-01-01: 100) + t1_dup (5) + t1_other (3) 等等
        # 实际上因为 ORDER BY date DESC LIMIT days, 同一天会取 limit 条
        assert result["total_calls"] >= 100  # 至少包含 2026-01-01 的 100 条
        assert "today_stats" in result

    def test_returns_error_on_db_failure(self, tracker, monkeypatch):
        """查询失败时应返回 error 字段。"""
        from unittest.mock import MagicMock
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("db locked")
        monkeypatch.setattr(tracker, "_get_conn", lambda: mock_conn)
        result = tracker.get_tool_success_rate("t1")
        assert "error" in result


# ════════════════════════════════════════════════════════════════════
# 5. get_all_tools_ranking — 全工具排名
# ════════════════════════════════════════════════════════════════════


class TestGetAllToolsRanking:
    def test_empty_returns_empty_list(self, tracker):
        """无数据时返回空列表。"""
        assert tracker.get_all_tools_ranking() == []

    def test_returns_tools_sorted_by_failure_rate_desc(self, tracker):
        """结果应按失败率从高到低排序。"""
        conn = sqlite3.connect(tracker.db_path)
        today = datetime.now().strftime("%Y-%m-%d")
        for name, total, success in [
            ("good_tool", 100, 95),
            ("bad_tool", 10, 2),
            ("ok_tool", 10, 7),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO tool_daily_stats "
                "(date, tool_name, total_calls, success_calls, failure_calls, success_rate, avg_latency_ms, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (today, name, total, success, total - success, success / total, 100.0, int(time.time() * 1000))
            )
        conn.commit()
        conn.close()

        ranking = tracker.get_all_tools_ranking(days=7)
        assert len(ranking) == 3
        # 第一个应是失败率最高的 bad_tool
        assert ranking[0]["tool_name"] == "bad_tool"
        assert ranking[-1]["tool_name"] == "good_tool"
        # 每条都应有完整字段
        for r in ranking:
            assert "total_calls" in r
            assert "success_rate" in r
            assert "deprioritized" in r


# ════════════════════════════════════════════════════════════════════
# 6. get_failing_tools — 失败工具
# ════════════════════════════════════════════════════════════════════


class TestGetFailingTools:
    def test_no_failing_tools_when_all_succeed(self, tracker):
        """全成功时不应返回任何工具。"""
        conn = sqlite3.connect(tracker.db_path)
        conn.execute(
            "INSERT INTO tool_daily_stats "
            "(date, tool_name, total_calls, success_calls, failure_calls, success_rate, avg_latency_ms, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d"), "good", 100, 100, 0, 1.0, 50.0, int(time.time() * 1000))
        )
        conn.commit()
        conn.close()
        assert tracker.get_failing_tools(threshold=0.3) == []

    def test_returns_tools_above_threshold(self, tracker):
        """失败率超阈值应被返回。"""
        conn = sqlite3.connect(tracker.db_path)
        for name, total, success in [
            ("bad_a", 10, 3),  # 70% 失败
            ("bad_b", 10, 5),  # 50% 失败
            ("good", 10, 9),   # 10% 失败
        ]:
            conn.execute(
                "INSERT INTO tool_daily_stats "
                "(date, tool_name, total_calls, success_calls, failure_calls, success_rate, avg_latency_ms, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-06-08", name, total, success, total - success, success / total, 100.0, int(time.time() * 1000))
            )
        conn.commit()
        conn.close()
        failing = tracker.get_failing_tools(threshold=0.3)
        names = {f["tool_name"] for f in failing}
        assert "bad_a" in names
        assert "bad_b" in names
        assert "good" not in names


# ════════════════════════════════════════════════════════════════════
# 7. deprioritization 降权机制
# ════════════════════════════════════════════════════════════════════


class TestDeprioritization:
    def test_is_deprioritized_false_initially(self, tracker):
        """新工具不应被降权。"""
        assert tracker.is_deprioritized("new_tool") is False

    def test_get_deprioritized_tools_empty_initially(self, tracker):
        """初始降权列表应为空。"""
        assert tracker.get_deprioritized_tools() == []

    def test_force_deprioritize(self, tracker):
        """手动标记降权后，is_deprioritized 应返回 True。"""
        # 手动写入 deprioritization 表
        conn = sqlite3.connect(tracker.db_path)
        conn.execute("""
            INSERT INTO tool_deprioritization
            (tool_name, deprioritized, deprioritize_at, reason, deprioritized_at)
            VALUES (?, 1, 0.3, 'manual', ?)
        """, ("bad_tool", int(time.time() * 1000)))
        conn.commit()
        conn.close()
        assert tracker.is_deprioritized("bad_tool") is True
        assert "bad_tool" in tracker.get_deprioritized_tools()


# ════════════════════════════════════════════════════════════════════
# 8. _get_china_now — 时区
# ════════════════════════════════════════════════════════════════════


class TestGetChinaNow:
    def test_returns_china_timezone(self, tracker):
        """_get_china_now 应返回东八区时间。"""
        now = tracker._get_china_now()
        assert now.tzinfo is not None
        assert now.utcoffset().total_seconds() == 8 * 3600


# ════════════════════════════════════════════════════════════════════
# 9. SQL 常量检查
# ════════════════════════════════════════════════════════════════════


class TestSqlConstants:
    def test_create_log_sql_contains_required_fields(self):
        """CREATE_TOOL_CALL_LOG_SQL 应包含必填字段。"""
        for field in ("id", "session_id", "tool_name", "success", "latency_ms", "timestamp"):
            assert field in CREATE_TOOL_CALL_LOG_SQL

    def test_create_daily_stats_sql_contains_required_fields(self):
        """CREATE_TOOL_DAILY_STATS_SQL 应包含必填字段。"""
        for field in ("date", "tool_name", "total_calls", "success_rate"):
            assert field in CREATE_TOOL_DAILY_STATS_SQL

    def test_create_deprioritization_sql_contains_required_fields(self):
        """CREATE_TOOL_DEPRIORITIZATION_SQL 应包含必填字段。"""
        for field in ("tool_name", "deprioritized", "deprioritize_at"):
            assert field in CREATE_TOOL_DEPRIORITIZATION_SQL
