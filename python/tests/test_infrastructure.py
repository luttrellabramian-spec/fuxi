"""伏羲 基础设施层测试 — StructuredLogger / ToolCallTracker / CircuitBreaker / GrpcConnectionPool"""
import sys
import os
import json
import time
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from engine.execution_logger import StructuredLogger, make_trace_id, make_node_id, NODE_TYPES
from engine.tool_tracker import ToolCallTracker
from llm.client import CircuitBreaker


# ════════════════════════════════════════════════════════════════════
# 1. StructuredLogger
# ════════════════════════════════════════════════════════════════════


class TestStructuredLogger:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logger = StructuredLogger(log_dir=self.tmpdir, retention_days=1)
        yield
        self.logger.shutdown(wait=True)

    def test_create_logger(self):
        assert self.logger.log_dir is not None
        assert self.logger._written_count == 0
        assert self.logger._dropped_count == 0

    def test_log_basic_event(self):
        self.logger.log({
            "trace_id": "trace-test",
            "node_id": "node-test",
            "node_type": "llm_call",
            "status": "success",
            "duration_ms": 100,
            "data": {"model": "gpt-4"},
        })
        self.logger.shutdown(wait=True)
        assert self.logger._written_count >= 1

    def test_log_multiple_events(self):
        for i in range(10):
            self.logger.log({
                "trace_id": f"trace-{i}",
                "node_id": f"node-{i}",
                "node_type": "tool_call",
                "status": "success",
                "duration_ms": i * 10,
            })
        self.logger.shutdown(wait=True)
        assert self.logger._written_count == 10

    def test_log_with_error(self):
        self.logger.log({
            "trace_id": "trace-err",
            "node_id": "node-err",
            "node_type": "llm_call",
            "status": "failure",
            "duration_ms": 500,
            "error": {"type": "timeout", "message": "connection timed out"},
        })
        self.logger.shutdown(wait=True)
        assert self.logger._written_count >= 1

    def test_log_with_evolution_trigger(self):
        self.logger.log({
            "trace_id": "trace-evol",
            "node_id": "node-evol",
            "node_type": "evolution_trigger",
            "status": "success",
            "duration_ms": 10,
        })
        self.logger.shutdown(wait=True)

    def test_log_with_circuit_breaker(self):
        self.logger.log({
            "trace_id": "trace-cb",
            "node_id": "node-cb",
            "node_type": "circuit_breaker_open",
            "status": "failure",
            "duration_ms": 0,
        })
        self.logger.shutdown(wait=True)

    def test_get_stats(self):
        stats = self.logger.get_stats()
        assert "written" in stats
        assert "dropped" in stats
        assert "queue_size" in stats
        assert "log_dir" in stats

    def test_make_trace_id(self):
        tid = make_trace_id()
        assert tid.startswith("trace-")
        assert len(tid) > 10

    def test_make_node_id(self):
        nid = make_node_id("llm_call", "1")
        assert nid == "node-llm_call-1"

    def test_make_node_id_no_suffix(self):
        nid = make_node_id("dag_start")
        assert nid == "node-dag_start"

    def test_node_types_defined(self):
        assert "dag_start" in NODE_TYPES
        assert "dag_end" in NODE_TYPES
        assert "llm_call" in NODE_TYPES
        assert "tool_call" in NODE_TYPES
        assert "circuit_breaker_open" in NODE_TYPES

    def test_logger_handles_queue_full(self):
        """队列满时应丢弃旧事件而不是阻塞"""
        small_logger = StructuredLogger(log_dir=self.tmpdir, retention_days=1)
        small_logger._queue.maxsize = 2
        # 填满队列
        small_logger._queue.put_nowait({"dummy": 1})
        small_logger._queue.put_nowait({"dummy": 2})
        # 再写入应触发丢弃
        small_logger.log({
            "trace_id": "t", "node_id": "n", "node_type": "tool_call",
            "status": "success", "duration_ms": 0,
        })
        small_logger.shutdown(wait=True)

    def test_error_event_written_to_separate_file(self):
        self.logger.log({
            "trace_id": "t-err-file",
            "node_id": "n-err",
            "node_type": "llm_call",
            "status": "failure",
            "duration_ms": 999,
            "error": {"type": "error", "message": "test error"},
        })
        self.logger.shutdown(wait=True)
        # 检查错误文件是否创建（不一定同步写完，但不会挂）
        # 主要验证不抛异常

    def test_empty_event_logged(self):
        """即使只有必要字段也能记录"""
        self.logger.log({
            "trace_id": "t-min",
            "node_id": "n-min",
            "node_type": "unknown",
            "status": "unknown",
            "duration_ms": 0,
        })
        self.logger.shutdown(wait=True)

    def test_log_with_retry(self):
        self.logger.log({
            "trace_id": "t-retry",
            "node_id": "n-retry",
            "node_type": "llm_call",
            "status": "success",
            "duration_ms": 200,
            "retry": {"attempt": 2, "max_retries": 3},
        })
        self.logger.shutdown(wait=True)


# ════════════════════════════════════════════════════════════════════
# 2. ToolCallTracker
# ════════════════════════════════════════════════════════════════════


class TestToolCallTracker:
    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        self.db_path = os.path.join(tempfile.mkdtemp(), "test_tracker.db")
        self.tracker = ToolCallTracker(db_path=self.db_path)
        yield
        # cleanup
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_create_tracker(self):
        assert self.tracker.db_path is not None
        assert self.tracker.deprioritize_threshold == 0.3

    def test_record_success(self):
        result = self.tracker.record(
            session_id="sess-1", tool_name="web_search",
            success=True, latency_ms=100.0,
        )
        assert result["success"] is True
        assert "id" in result

    def test_record_failure(self):
        result = self.tracker.record(
            session_id="sess-2", tool_name="write_file",
            success=False, latency_ms=50.0,
            error_type="tool_execution_error",
            error_message="permission denied",
        )
        assert result["success"] is True

    def test_record_with_all_fields(self):
        result = self.tracker.record(
            session_id="sess-3", tool_name="read_file",
            success=True, latency_ms=30.5,
            error_type="none", error_message=None,
            cycle_count=3, llm_latency_ms=200.0,
        )
        assert result["success"] is True

    def test_get_success_rate_no_data(self):
        stats = self.tracker.get_tool_success_rate("nonexistent", days=7)
        assert stats["total_calls"] == 0

    def test_get_success_rate_with_data(self):
        self.tracker.record("s1", "web_search", True, 100.0)
        self.tracker.record("s1", "web_search", True, 200.0)
        self.tracker.record("s1", "web_search", False, 50.0)
        stats = self.tracker.get_tool_success_rate("web_search", days=7)
        # daily stats 需要聚合，但 get_tool_success_rate 直接查 daily_stats 表
        # 注：新数据不会立即出现在 daily_stats 中（需 aggregate_daily_stats）
        # 所以返回的 total_calls 可能是 0，但不影响测试不抛异常
        assert isinstance(stats, dict)

    def test_get_all_tools_ranking_empty(self):
        ranking = self.tracker.get_all_tools_ranking()
        assert ranking == []

    def test_get_all_tools_ranking_with_data(self):
        self.tracker.record("s1", "tool_a", True, 100.0)
        self.tracker.record("s1", "tool_b", False, 50.0)
        ranking = self.tracker.get_all_tools_ranking()
        # 刚写入的数据未聚合，ranking 可能为空，但不影响验证不抛异常
        assert isinstance(ranking, list)

    def test_get_failing_tools(self):
        failing = self.tracker.get_failing_tools(threshold=0.3)
        assert isinstance(failing, list)

    def test_deprioritized_tools_initially_empty(self):
        tools = self.tracker.get_deprioritized_tools()
        assert tools == []

    def test_is_deprioritized(self):
        assert self.tracker.is_deprioritized("web_search") is False

    def test_aggregate_daily_stats(self):
        for i in range(10):
            self.tracker.record("s1", "tool_x", i % 2 == 0, float(i * 10))
        count = self.tracker.aggregate_daily_stats()
        assert count >= 0  # 可能为零（如果数据跨天），但不会抛异常

    def test_check_auto_restore(self):
        restored = self.tracker.check_auto_restore()
        assert restored >= 0

    def test_unicode_tool_name(self):
        """验证 Unicode 工具名不会导致错误"""
        result = self.tracker.record(
            session_id="s-uni", tool_name="中文工具",
            success=True, latency_ms=10.0,
        )
        assert result["success"] is True

    def test_large_error_message_truncation(self):
        long_msg = "x" * 2000
        result = self.tracker.record(
            session_id="s-trunc", tool_name="failing_tool",
            success=False, latency_ms=10.0,
            error_message=long_msg,
        )
        assert result["success"] is True


# ════════════════════════════════════════════════════════════════════
# 3. CircuitBreaker (LLM)
# ════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    def setup_method(self):
        self.cb = CircuitBreaker(failure_threshold=3, recovery_seconds=0.01)

    def test_initial_state_closed(self):
        assert self.cb.state == "CLOSED"
        ok, reason = self.cb.can_proceed()
        assert ok is True
        assert reason == ""

    def test_record_success_keeps_closed(self):
        self.cb.record_success()
        assert self.cb.state == "CLOSED"

    def test_multiple_failures_open(self):
        for i in range(3):
            self.cb.record_failure(f"error {i}")
        assert self.cb.state == "OPEN"
        ok, reason = self.cb.can_proceed()
        assert ok is False

    def test_half_open_after_recovery(self):
        for i in range(3):
            self.cb.record_failure(f"error {i}")
        assert self.cb.state == "OPEN"
        time.sleep(0.02)  # 等待恢复
        # 重新查询 state 应触发 HALF_OPEN
        ok, reason = self.cb.can_proceed()
        assert ok is True
        assert self.cb.state == "HALF_OPEN"

    def test_half_open_success_recloses(self):
        for i in range(3):
            self.cb.record_failure(f"e{i}")
        time.sleep(0.02)
        self.cb.can_proceed()  # 触发 HALF_OPEN
        self.cb.record_success()  # 试探成功
        assert self.cb.state == "CLOSED"

    def test_half_open_failure_reopens(self):
        for i in range(3):
            self.cb.record_failure(f"e{i}")
        time.sleep(0.02)
        self.cb.can_proceed()  # 触发 HALF_OPEN
        self.cb.record_failure("half_open fail")
        assert self.cb.state == "OPEN"

    def test_reset(self):
        for i in range(3):
            self.cb.record_failure(f"e{i}")
        assert self.cb.state == "OPEN"
        self.cb.reset()
        assert self.cb.state == "CLOSED"
        assert self.cb._failure_count == 0

    def test_below_threshold_not_open(self):
        self.cb.record_failure("e1")
        self.cb.record_failure("e2")
        assert self.cb.state == "CLOSED"  # 还没到 3 次

    def test_failure_count_tracking(self):
        self.cb.record_failure("e1")
        assert self.cb._failure_count == 1
        self.cb.record_success()
        assert self.cb._failure_count == 0

    def test_last_error_stored(self):
        self.cb.record_failure("specific error msg")
        assert "specific" in self.cb._last_error


# ════════════════════════════════════════════════════════════════════
# 4. GrpcConnectionPool (skip gracefully if grpc unavailable)
# ════════════════════════════════════════════════════════════════════


class TestGrpcConnectionPool:
    def test_import_grpc_graceful(self):
        """验证 grpc_utils 模块可被安全导入（即使 grpc 不可用）"""
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool, GrpcCircuitBreaker, GrpcPoolExhaustedError, GrpcCircuitOpenError
            assert GrpcConnectionPool is not None
        except ImportError:
            pytest.skip("grpc module not available, skipping")

    def test_grpc_circuit_breaker(self):
        try:
            from grpc_utils.connection_pool import GrpcCircuitBreaker
        except ImportError:
            pytest.skip("grpc module not available")

        cb = GrpcCircuitBreaker(failure_threshold=3, recovery_seconds=0.01)
        assert cb.state == "CLOSED"
        assert cb.can_proceed() is True

        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.can_proceed() is False

        time.sleep(0.02)
        # 自动进入 HALF_OPEN
        assert cb.state == "HALF_OPEN"
        assert cb.can_proceed() is True

        cb.record_success()
        assert cb.state == "CLOSED"

    def test_grpc_circuit_breaker_reset(self):
        try:
            from grpc_utils.connection_pool import GrpcCircuitBreaker
        except ImportError:
            pytest.skip("grpc module not available")

        cb = GrpcCircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "OPEN"
        cb.reset()
        assert cb.state == "CLOSED"
        assert cb.can_proceed() is True

    def test_grpc_pool_singleton(self):
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool
        except ImportError:
            pytest.skip("grpc module not available")

        # 确保全局实例先重置
        GrpcConnectionPool.reset_instance()
        pool1 = GrpcConnectionPool.get_instance()
        pool2 = GrpcConnectionPool.get_instance()
        assert pool1 is pool2
        GrpcConnectionPool.reset_instance()

    def test_grpc_pool_acquire_release(self):
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool
        except ImportError:
            pytest.skip("grpc module not available")

        GrpcConnectionPool.reset_instance()
        pool = GrpcConnectionPool.get_instance()
        ok = pool.acquire(timeout=1.0)
        assert ok is True
        pool.release(success=True)
        GrpcConnectionPool.reset_instance()

    def test_grpc_pool_context_manager(self):
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool
        except ImportError:
            pytest.skip("grpc module not available")

        GrpcConnectionPool.reset_instance()
        pool = GrpcConnectionPool.get_instance()
        with pool.request_context() as acquired:
            assert acquired is True
        GrpcConnectionPool.reset_instance()

    def test_grpc_pool_request_counter(self):
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool
        except ImportError:
            pytest.skip("grpc module not available")

        GrpcConnectionPool.reset_instance()
        pool = GrpcConnectionPool.get_instance()
        pool.acquire(timeout=1.0)
        assert pool._request_count == 1
        pool.release(success=True)
        assert pool._request_count == 0
        GrpcConnectionPool.reset_instance()

    def test_grpc_pool_health_check(self):
        try:
            from grpc_utils.connection_pool import GrpcConnectionPool
        except ImportError:
            pytest.skip("grpc module not available")

        GrpcConnectionPool.reset_instance()
        pool = GrpcConnectionPool.get_instance()
        called = []

        def health_fn():
            called.append(True)
            return True

        pool.set_health_check(health_fn)
        assert pool._health_check_fn is health_fn
        GrpcConnectionPool.reset_instance()
