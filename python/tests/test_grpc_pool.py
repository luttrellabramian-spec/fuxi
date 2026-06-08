"""伏羲 gRPC 连接池测试 — GrpcCircuitBreaker / GrpcConnectionPool / 单例"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from grpc_utils.connection_pool import (
    GrpcCircuitBreaker,
    GrpcConnectionPool,
    GrpcCircuitOpenError,
    GrpcPoolExhaustedError,
)


# ════════════════════════════════════════════════════════════════════
# 1. GrpcCircuitBreaker — 三态断路器
# ════════════════════════════════════════════════════════════════════


class TestGrpcCircuitBreaker:
    def setup_method(self):
        self.cb = GrpcCircuitBreaker(failure_threshold=3, recovery_seconds=0.5)

    def test_initial_state_closed(self):
        """初始状态应为 CLOSED，可正常放行。"""
        assert self.cb.state == GrpcCircuitBreaker.CLOSED
        assert self.cb.can_proceed() is True

    def test_record_success_resets_failure_count(self):
        """成功一次应清零失败计数。"""
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        assert self.cb._failure_count == 0
        assert self.cb.state == GrpcCircuitBreaker.CLOSED

    def test_opens_after_threshold_failures(self):
        """连续 N 次失败后断路器应断开。"""
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == GrpcCircuitBreaker.OPEN
        assert self.cb.can_proceed() is False

    def test_half_open_after_recovery_seconds(self):
        """经过 recovery_seconds 后应进入 HALF_OPEN 状态。"""
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == GrpcCircuitBreaker.OPEN
        time.sleep(0.6)
        # 触发 state 重新计算
        assert self.cb.state == GrpcCircuitBreaker.HALF_OPEN
        assert self.cb.can_proceed() is True

    def test_half_open_failure_reopens_immediately(self):
        """HALF_OPEN 状态下再失败应立即重新断开。"""
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(0.6)
        assert self.cb.state == GrpcCircuitBreaker.HALF_OPEN
        self.cb.record_failure()
        assert self.cb.state == GrpcCircuitBreaker.OPEN

    def test_half_open_success_closes_circuit(self):
        """HALF_OPEN 状态下成功应关闭断路器。"""
        for _ in range(3):
            self.cb.record_failure()
        time.sleep(0.6)
        self.cb.state  # 触发 HALF_OPEN 转换
        self.cb.record_success()
        assert self.cb.state == GrpcCircuitBreaker.CLOSED
        assert self.cb._failure_count == 0
        assert self.cb._opened_at is None

    def test_reset_clears_state(self):
        """reset() 应清空所有状态。"""
        for _ in range(3):
            self.cb.record_failure()
        self.cb.reset()
        assert self.cb.state == GrpcCircuitBreaker.CLOSED
        assert self.cb._failure_count == 0
        assert self.cb._opened_at is None

    def test_record_failure_increments_count(self):
        """每次失败应累加计数。"""
        self.cb.record_failure()
        assert self.cb._failure_count == 1
        self.cb.record_failure()
        assert self.cb._failure_count == 2


# ════════════════════════════════════════════════════════════════════
# 2. GrpcConnectionPool — 并发控制 / 上下文管理器 / 单例
# ════════════════════════════════════════════════════════════════════


class TestGrpcConnectionPool:
    def setup_method(self):
        """每个测试前重置单例。"""
        GrpcConnectionPool.reset_instance()

    def teardown_method(self):
        GrpcConnectionPool.reset_instance()

    def test_get_instance_singleton(self):
        """get_instance 多次调用应返回同一实例。"""
        a = GrpcConnectionPool.get_instance()
        b = GrpcConnectionPool.get_instance()
        assert a is b

    def test_get_instance_creates_when_none(self):
        """首次调用应创建新实例。"""
        assert GrpcConnectionPool._instance is None
        pool = GrpcConnectionPool.get_instance()
        assert pool is not None
        assert GrpcConnectionPool._instance is pool

    def test_acquire_release_basic(self):
        """基本 acquire/release 配对。"""
        pool = GrpcConnectionPool.get_instance()
        assert pool.acquire() is True
        assert pool._request_count == 1
        pool.release(success=True)
        assert pool._request_count == 0

    def test_acquire_release_with_failure(self):
        """release(success=False) 应记录一次失败。"""
        pool = GrpcConnectionPool.get_instance()
        pool.acquire()
        pool.release(success=False)
        # 一次失败不会断开
        assert pool._circuit_breaker._failure_count == 1
        assert pool._circuit_breaker.state == GrpcCircuitBreaker.CLOSED

    def test_acquire_raises_when_circuit_open(self):
        """断路器断开时 acquire 应抛 GrpcCircuitOpenError。"""
        pool = GrpcConnectionPool.get_instance()
        # 模拟断路器断开
        for _ in range(pool.CIRCUIT_FAILURE_THRESHOLD):
            pool._circuit_breaker.record_failure()
        with pytest.raises(GrpcCircuitOpenError):
            pool.acquire()

    def test_request_context_manager_success(self):
        """with 语句应正常 acquire+release，并按异常状态记成功/失败。"""
        pool = GrpcConnectionPool.get_instance()
        with pool.request_context() as ok:
            assert ok is True
            assert pool._request_count == 1
        assert pool._request_count == 0

    def test_request_context_manager_on_exception(self):
        """with 内抛异常应被记为失败。"""
        pool = GrpcConnectionPool.get_instance()
        with pytest.raises(ValueError):
            with pool.request_context() as ok:
                assert ok is True
                raise ValueError("boom")
        # 异常被识别为失败
        assert pool._circuit_breaker._failure_count >= 1

    def test_set_health_check(self):
        """set_health_check 应记录回调。"""
        pool = GrpcConnectionPool.get_instance()
        fn = lambda: True
        pool.set_health_check(fn)
        assert pool._health_check_fn is fn

    def test_concurrent_acquire_under_limit(self):
        """并发数低于上限时所有请求都应通过。"""
        pool = GrpcConnectionPool.get_instance()
        results = []
        lock = threading.Lock()

        def worker():
            with pool.request_context() as ok:
                if ok:
                    with lock:
                        results.append(1)
                time.sleep(0.01)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
        assert pool._request_count == 0

    def test_start_server_requires_create_first(self):
        """未 create_server 就 start_server 应抛 RuntimeError。"""
        pool = GrpcConnectionPool.get_instance()
        with pytest.raises(RuntimeError, match="必须先调用 create_server"):
            pool.start_server()

    def test_shutdown_resets_singleton(self):
        """shutdown 应清空单例。"""
        pool = GrpcConnectionPool.get_instance()
        pool.shutdown()
        assert GrpcConnectionPool._instance is None

    def test_class_constants(self):
        """关键常量应有合理值。"""
        assert GrpcConnectionPool.MAX_CONCURRENT_REQUESTS == 100
        assert GrpcConnectionPool.HEARTBEAT_INTERVAL_SEC == 30
        assert GrpcConnectionPool.CIRCUIT_FAILURE_THRESHOLD == 5
        assert GrpcConnectionPool.CIRCUIT_RECOVERY_SEC == 30


# ════════════════════════════════════════════════════════════════════
# 3. _RequestContext — 上下文管理器
# ════════════════════════════════════════════════════════════════════


class TestRequestContext:
    def setup_method(self):
        GrpcConnectionPool.reset_instance()

    def teardown_method(self):
        GrpcConnectionPool.reset_instance()

    def test_context_returns_acquired_status(self):
        """__enter__ 应返回 acquire 的结果。"""
        pool = GrpcConnectionPool.get_instance()
        ctx = pool.request_context()
        assert ctx.__enter__() is True

    def test_context_no_release_when_not_acquired(self):
        """未 acquire 成功时 __exit__ 不应 release。"""
        pool = GrpcConnectionPool.get_instance()
        ctx = pool.request_context()
        # 模拟未 acquire
        ctx._acquired = False
        # 不应抛错
        ctx.__exit__(None, None, None)
        # request_count 应未变化
        assert pool._request_count == 0
