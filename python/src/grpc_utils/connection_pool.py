from __future__ import annotations

"""gRPC 连接池（v0.2.0: 单例 Channel + Semaphore 并发控制 + CircuitBreaker）

核心设计：
1. 单例 pool，管理 gRPC 服务端生命周期
2. 并发上限 100，超出排队等待
3. 30 秒心跳保活
4. CircuitBreaker 三态保护
"""
import threading
import time
import logging
from concurrent import futures
from typing import Optional, Callable

# 注意：需要 grpcio>=1.62.0 已安装
import grpc as grpc_mod

logger = logging.getLogger(__name__)


class GrpcCircuitBreaker:
    """gRPC 断路器（独立于 LLM 断路器的专用实例）

    三态模型：CLOSED（正常）→ OPEN（断开）→ HALF_OPEN（半开试探）
    阈值：连续 5 次失败 → OPEN，30 秒后 HALF_OPEN
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int = 5, recovery_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN and self._opened_at:
                if time.time() - self._opened_at >= self._recovery_seconds:
                    self._state = self.HALF_OPEN
                    logger.info("gRPC 断路器进入 HALF_OPEN 状态")
            return self._state

    def can_proceed(self) -> bool:
        s = self.state
        if s == self.CLOSED or s == self.HALF_OPEN:
            return True
        return False

    def record_success(self):
        with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED
            self._opened_at = None

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            if self._state == self.HALF_OPEN or self._failure_count >= self._failure_threshold:
                self._state = self.OPEN
                self._opened_at = time.time()
                logger.warning(f"gRPC 断路器断开（连续 {self._failure_count} 次失败）")

    def reset(self):
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None


class GrpcPoolExhaustedError(Exception):
    """连接池耗尽异常（100 并发已达上限）"""
    pass


class GrpcCircuitOpenError(Exception):
    """断路器断开异常"""
    pass


class GrpcConnectionPool:
    """gRPC 连接池（单例 - 管理 gRPC 服务端并发与健康）

    功能：
    1. 创建和启动 gRPC server（内置 keepalive 参数）
    2. Semaphore(100) 并发控制
    3. CircuitBreaker 自动熔断
    4. 心跳保活检测
    5. Graceful Shutdown
    """

    _instance: Optional['GrpcConnectionPool'] = None
    _lock = threading.Lock()

    MAX_CONCURRENT_REQUESTS = 100      # 并发上限
    HEARTBEAT_INTERVAL_SEC = 30        # 心跳间隔
    KEEPALIVE_TIMEOUT_SEC = 10         # keepalive 超时
    CIRCUIT_FAILURE_THRESHOLD = 5      # 连续失败阈值
    CIRCUIT_RECOVERY_SEC = 30          # 恢复时间
    GRPC_SERVER_MAX_WORKERS = 10       # 服务线程数

    def __init__(self, max_workers: int = GRPC_SERVER_MAX_WORKERS):
        if GrpcConnectionPool._instance is not None:
            return

        self._max_workers = max_workers
        self._server: Optional[grpc_mod.Server] = None
        self._server_lock = threading.Lock()

        # 并发控制
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        self._request_count = 0
        self._request_lock = threading.Lock()

        # Circuit Breaker
        self._circuit_breaker = GrpcCircuitBreaker(
            failure_threshold=self.CIRCUIT_FAILURE_THRESHOLD,
            recovery_seconds=self.CIRCUIT_RECOVERY_SEC,
        )

        # 心跳线程
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._health_check_fn: Optional[Callable[[], bool]] = None

        GrpcConnectionPool._instance = self

    # ── 服务端管理 ──────────────────────────────────

    def create_server(self, port: int = 50051) -> grpc_mod.Server:
        """创建并启动 gRPC 服务器（带 keepalive 参数）"""
        options = [
            ('grpc.keepalive_time_ms', self.HEARTBEAT_INTERVAL_SEC * 1000),
            ('grpc.keepalive_timeout_ms', self.KEEPALIVE_TIMEOUT_SEC * 1000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.http2.max_pings_without_data', 0),
            ('grpc.max_receive_message_length', 10 * 1024 * 1024),
            ('grpc.max_send_message_length', 10 * 1024 * 1024),
            ('grpc.http2.initial_window_size', 1024 * 1024 * 4),
            ('grpc.http2.max_frame_size', 16384),
        ]

        # 先关闭旧服务器（如果存在）
        if self._server is not None:
            self._server.stop(grace=0).wait()
            self._server = None

        self._server = grpc_mod.server(
            futures.ThreadPoolExecutor(max_workers=self._max_workers),
            options=options,
        )
        self._server.add_insecure_port(f"[::]:{port}")
        return self._server

    def start_server(self) -> None:
        if self._server is None:
            raise RuntimeError("必须先调用 create_server()")
        self._server.start()
        self._start_heartbeat()
        logger.info(f"gRPC 服务器已启动（max_workers={self._max_workers}）")

    def set_health_check(self, fn: Callable[[], bool]) -> None:
        self._health_check_fn = fn

    # ── 并发控制 ──────────────────────────────────

    def acquire(self, timeout: float = 30.0) -> bool:
        """获取并发执行许可"""
        if not self._circuit_breaker.can_proceed():
            raise GrpcCircuitOpenError(
                f"gRPC 断路器已断开（连续 {self.CIRCUIT_FAILURE_THRESHOLD} 次失败），"
                f"{self.CIRCUIT_RECOVERY_SEC} 秒后自动恢复"
            )

        acquired = self._semaphore.acquire(timeout=timeout)
        if not acquired:
            raise GrpcPoolExhaustedError(
                f"gRPC 连接池耗尽（{self.MAX_CONCURRENT_REQUESTS} 并发已达上限）"
            )

        with self._request_lock:
            self._request_count += 1
        return True

    def release(self, success: bool = True) -> None:
        self._semaphore.release()
        with self._request_lock:
            self._request_count -= 1
        if success:
            self._circuit_breaker.record_success()
        else:
            self._circuit_breaker.record_failure()

    # ── 上下文管理器 ──────────────────────────────────

    def request_context(self):
        return _RequestContext(self)

    # ── 心跳 ──────────────────────────────────

    def _start_heartbeat(self):
        def heartbeat_loop():
            while not self._stop_heartbeat.wait(self.HEARTBEAT_INTERVAL_SEC):
                try:
                    if self._health_check_fn:
                        ok = self._health_check_fn()
                        if ok:
                            logger.debug("gRPC 心跳保活成功")
                        else:
                            logger.warning("gRPC 健康检查失败")
                    else:
                        logger.debug("gRPC 心跳（无健康检查函数）")
                except Exception as e:
                    logger.warning(f"gRPC 心跳异常: {e}")

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    # ── 关闭 ──────────────────────────────────

    def shutdown(self, grace: int = 10):
        logger.info("gRPC 连接池关闭中...")
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        if self._server:
            self._server.stop(grace=grace).wait()
            self._server = None
        GrpcConnectionPool._instance = None
        logger.info("gRPC 连接池已关闭")

    # ── 单例 ──────────────────────────────────

    @classmethod
    def get_instance(cls, max_workers: int = GRPC_SERVER_MAX_WORKERS) -> 'GrpcConnectionPool':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(max_workers=max_workers)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        if cls._instance:
            cls._instance.shutdown()
        cls._instance = None


class _RequestContext:
    """请求上下文管理器"""

    def __init__(self, pool: GrpcConnectionPool):
        self._pool = pool
        self._acquired = False

    def __enter__(self) -> bool:
        self._acquired = self._pool.acquire()
        return self._acquired

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            success = exc_type is None
            self._pool.release(success=success)
        return False
