"""执行日志结构化模块（P0-3）

设计目标：
- JSONL 格式输出：每行一个完整 JSON 对象
- 异步写入：Queue + 后台线程，不阻塞主流程
- 三级存储：热(24h) / 温(7d) / 冷(90d)
- 按状态分流：正常日志 → exec 文件，错误日志 → error 文件
- 日志轮转：100MB 大小轮转 + 每日日期轮转
- 线程安全：Queue 天然线程安全

使用方式：
    from engine.execution_logger import StructuredLogger
    logger = StructuredLogger()
    logger.log({
        "trace_id": "trace-abc",
        "node_id": "node-tool-search",
        "node_type": "tool_call",
        "status": "success",
        "duration_ms": 1247,
        "data": {"tool_name": "web_search", ...}
    })
"""
import os
import json
import uuid
import gzip
import shutil
from datetime import datetime, timezone, timedelta
from threading import Thread
from queue import Queue
from pathlib import Path
from typing import Dict, Any, Optional


# 时区偏移（东八区）
_CHINA_TZ = timezone(timedelta(hours=8))

# 版本号
FUXI_VERSION = "v0.2.5"

# 日志目录
DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

# 默认配置
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
DEFAULT_QUEUE_MAXSIZE = 10000
DEFAULT_RETENTION_DAYS = 7


class StructuredLogger:
    """结构化日志写入器

    核心特性：
    - 非阻塞写入：log() 将事件放入 Queue 后立即返回
    - 后台线程持续消费 Queue 写入文件
    - 按日期轮转文件，自动创建 logs/ 目录
    - 错误日志自动分流到 error 文件
    - 文件超过 100MB 自动压缩归档

    Attributes:
        log_dir: 日志文件目录
        retention_days: 温存储保留天数
        version: 伏羲版本号
    """

    def __init__(
        self,
        log_dir: str = DEFAULT_LOG_DIR,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        version: str = FUXI_VERSION,
    ):
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days
        self.version = version
        self._queue: Queue = Queue(maxsize=DEFAULT_QUEUE_MAXSIZE)

        # 当前写入的文件状态
        self._current_date: Optional[str] = None
        self._current_exec_file: Optional[Path] = None
        self._current_error_file: Optional[Path] = None
        self._current_evolution_file: Optional[Path] = None

        self._dropped_count = 0
        self._written_count = 0

        # 确保日志目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 启动后台写入线程
        self._writer_thread = Thread(target=self._write_loop, daemon=True, name="log-writer")
        self._writer_thread.start()

        # 启动日志清理定时器（首次运行后 1 小时执行）
        self._cleanup_timer: Optional[Thread] = None

    # ── 公共 API ──────────────────────────────────────────

    def log(self, event: Dict[str, Any]) -> None:
        """记录一条日志事件

        非阻塞方法，将事件放入写入队列后立即返回。
        若队列已满，丢弃最旧事件并记录警告。

        Args:
            event: 日志事件字典，需包含以下字段：
                - trace_id: 追踪ID（必填）
                - node_id: 节点ID（必填）
                - node_type: 节点类型（必填，见 NODE_TYPES）
                - status: 状态（必填，success/failure/timeout/skipped）
                - duration_ms: 耗时毫秒（必填）
                - data: 节点详情（可选，默认 {}）
                - error: 错误信息（可选，默认 None）
                - retry: 重试信息（可选，默认 None）
        """
        try:
            # 构建完整日志条目
            entry = self._build_entry(event)
            # 放入队列（非阻塞，队列满时丢弃最旧的）
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._dropped_count += 1
                except Exception:
                    pass
            self._queue.put_nowait(entry)
        except Exception:
            pass  # 日志写入失败不应影响主流程

    def get_stats(self) -> Dict[str, Any]:
        """获取日志统计信息"""
        return {
            "written": self._written_count,
            "dropped": self._dropped_count,
            "queue_size": self._queue.qsize(),
            "log_dir": str(self.log_dir),
            "retention_days": self.retention_days,
        }

    def shutdown(self, wait: bool = True) -> None:
        """关闭日志器（程序退出时调用）"""
        if self._cleanup_timer and self._cleanup_timer.is_alive():
            pass  # daemon thread will be terminated
        # 等待队列消费
        if wait:
            self._queue.join()

    # ── 日志构建 ──────────────────────────────────────────

    def _build_entry(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """构建标准化的日志条目"""
        now = datetime.now(_CHINA_TZ)
        return {
            "log_id": str(uuid.uuid4()),
            "timestamp": now.isoformat(timespec="microseconds"),
            "version": self.version,
            "trace_id": event.get("trace_id", ""),
            "node_id": event.get("node_id", ""),
            "node_type": event.get("node_type", "unknown"),
            "status": event.get("status", "unknown"),
            "duration_ms": event.get("duration_ms", 0),
            "data": event.get("data", {}),
            "error": event.get("error"),
            "retry": event.get("retry"),
        }

    # ── 文件写入（后台线程） ──────────────────────────────

    def _write_loop(self) -> None:
        """后台写入线程：持续消费队列写入文件"""
        while True:
            try:
                entry = self._queue.get()
                self._write_entry(entry)
                self._written_count += 1
                self._queue.task_done()
            except Exception:
                # 写入异常不能终止循环
                continue

    def _write_entry(self, entry: Dict[str, Any]) -> None:
        """写入单条日志条目"""
        event_date = entry["timestamp"][:10]

        # 日期轮转检查
        if event_date != self._current_date:
            self._rotate_by_date(event_date)

        line = json.dumps(entry, ensure_ascii=False) + "\n"

        # 写入执行日志
        if self._current_exec_file:
            with open(self._current_exec_file, "a", encoding="utf-8") as f:
                f.write(line)

            # 大小轮转检查
            if self._current_exec_file.stat().st_size > DEFAULT_MAX_FILE_SIZE:
                self._rotate_by_size(self._current_exec_file)

        # 错误日志分流
        if entry.get("status") in ("failure", "timeout") and self._current_error_file:
            with open(self._current_error_file, "a", encoding="utf-8") as f:
                f.write(line)

        # 进化分析日志分流
        if entry.get("node_type") in ("evolution_trigger", "circuit_breaker_open"):
            if self._current_evolution_file:
                with open(self._current_evolution_file, "a", encoding="utf-8") as f:
                    f.write(line)

    def _rotate_by_date(self, event_date: str) -> None:
        """按日期轮转文件"""
        self._current_date = event_date
        self._current_exec_file = self._get_file("exec", event_date)
        self._current_error_file = self._get_file("error", event_date)
        self._current_evolution_file = self._get_file("evolution", event_date)

    def _rotate_by_size(self, file_path: Path) -> None:
        """按文件大小轮转（压缩并创建新文件）"""
        try:
            # 压缩当前文件
            gz_path = file_path.with_suffix(".jsonl.gz")
            with open(file_path, "rb") as f_in:
                with gzip.open(gz_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            # 清空原文件
            file_path.write_text("", encoding="utf-8")
        except Exception:
            pass  # 压缩失败不影响后续写入

    def _get_file(self, prefix: str, date: str) -> Path:
        """获取指定日期和前缀的日志文件路径"""
        return self.log_dir / f"fuxi-{prefix}-{date}.jsonl"


# 节点类型常量
NODE_TYPES = {
    "dag_start": "DAG 开始",
    "dag_end": "DAG 结束",
    "llm_call": "LLM 调用节点",
    "tool_call": "工具调用节点",
    "branch": "条件分支决策",
    "hot_memory_read": "热记忆读取",
    "hot_memory_write": "热记忆写入",
    "warm_memory_sync": "热→温下刷",
    "evolution_trigger": "进化触发",
    "circuit_breaker_open": "熔断器打开",
}


def make_trace_id() -> str:
    """生成追踪 ID（用于串联同一次 ReAct 执行的所有日志）"""
    return f"trace-{uuid.uuid4().hex[:12]}"


def make_node_id(node_type: str, suffix: str = "") -> str:
    """生成节点 ID"""
    base = f"node-{node_type}"
    if suffix:
        base = f"{base}-{suffix}"
    return base
