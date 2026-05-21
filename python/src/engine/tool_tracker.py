"""工具调用追踪器（P1-3）

设计目标：
- 每次工具调用记录一条结构化日志（持久化 SQLite）
- 日聚合：按工具统计成功率、平均耗时、P95 耗时
- 降权策略：失败率 > 30% 自动软降权（降低调度优先级，不断路）
- 自动恢复：成功率回到 80% 时自动恢复

使用方式（在工具执行层调用）：
    from engine.tool_tracker import ToolCallTracker
    tracker = ToolCallTracker(db_path)
    tracker.record(
        session_id="sess_xxx",
        tool_name="web_search",
        success=True,
        latency_ms=1234.5,
        cycle_count=3,
    )

查询接口（HTTP/gRPC 可暴露）：
    stats = tracker.get_tool_success_rate("web_search", days=7)
    ranking = tracker.get_all_tools_ranking(days=7)
    failing = tracker.get_failing_tools(threshold=0.3)
"""
import os
import sqlite3
import uuid
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger("tool_tracker")

# 表创建 DDL
CREATE_TOOL_CALL_LOG_SQL = """
CREATE TABLE IF NOT EXISTS tool_call_log (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    success         INTEGER NOT NULL,
    latency_ms      REAL NOT NULL,
    error_type      TEXT DEFAULT 'none',
    error_message   TEXT DEFAULT '',
    cycle_count     INTEGER NOT NULL DEFAULT 0,
    llm_latency_ms  REAL,
    timestamp       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_call_tool_time ON tool_call_log(tool_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tool_call_session ON tool_call_log(session_id, timestamp DESC);
"""

CREATE_TOOL_DAILY_STATS_SQL = """
CREATE TABLE IF NOT EXISTS tool_daily_stats (
    date            TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    total_calls     INTEGER NOT NULL DEFAULT 0,
    success_calls   INTEGER NOT NULL DEFAULT 0,
    failure_calls   INTEGER NOT NULL DEFAULT 0,
    success_rate    REAL NOT NULL DEFAULT 0.0,
    avg_latency_ms  REAL NOT NULL DEFAULT 0.0,
    p95_latency_ms  REAL,
    failure_threshold_exceeded INTEGER DEFAULT 0,
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (date, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_daily_tool_date ON tool_daily_stats(date DESC, success_rate ASC);
"""

CREATE_TOOL_DEPRIORITIZATION_SQL = """
CREATE TABLE IF NOT EXISTS tool_deprioritization (
    tool_name       TEXT PRIMARY KEY,
    deprioritized   INTEGER DEFAULT 0,
    deprioritize_at REAL DEFAULT 0.3,
    reason          TEXT DEFAULT '',
    deprioritized_at INTEGER,
    restored_at     INTEGER,
    restored_reason TEXT
);
"""

# 错误类型枚举
ERROR_TYPES = {
    "none": "无错误",
    "timeout": "LLM/工具调用超时",
    "tool_not_found": "工具不存在",
    "tool_execution_error": "工具执行时异常",
    "invalid_params": "参数格式错误",
    "rate_limit": "触发限流",
    "network_error": "网络异常",
    "unknown": "未知错误",
}

# SQLite 数据库路径
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tool_tracker.db",
)


class ToolCallTracker:
    """工具调用追踪器

    线程安全，使用 WAL 模式支持并发读写。
    写操作通过锁保护，读操作无需锁（SQLite WAL 兼容）。

    Attributes:
        db_path: SQLite 数据库路径
        deprioritize_threshold: 降权阈值（失败率），默认 0.3
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        deprioritize_threshold: float = 0.3,
    ):
        # 中文路径兼容：回退到临时目录
        try:
            conn = sqlite3.connect(db_path, check_same_thread=True)
            conn.execute("SELECT 1")
            conn.close()
        except Exception:
            import tempfile
            db_path = os.path.join(tempfile.gettempdir(), "fuxi_tool_tracker.db")

        self.db_path = db_path
        self.deprioritize_threshold = deprioritize_threshold
        self._write_lock = threading.Lock()
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_tables(self) -> None:
        """初始化数据库表（幂等操作）"""
        conn = self._get_conn()
        try:
            conn.executescript(CREATE_TOOL_CALL_LOG_SQL)
            conn.executescript(CREATE_TOOL_DAILY_STATS_SQL)
            conn.executescript(CREATE_TOOL_DEPRIORITIZATION_SQL)
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to init tool tracker tables: {e}")
        finally:
            conn.close()

    # ── 记录 ──────────────────────────────────────────────

    def record(
        self,
        session_id: str,
        tool_name: str,
        success: bool,
        latency_ms: float,
        error_type: str = "none",
        error_message: Optional[str] = None,
        cycle_count: int = 0,
        llm_latency_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """记录一次工具调用

        同步写入 SQLite，失败不影响主流程（降级为日志警告）。

        Args:
            session_id: 会话 ID
            tool_name: 工具名称
            success: 是否成功
            latency_ms: 执行耗时（毫秒）
            error_type: 错误类型（参见 ERROR_TYPES）
            error_message: 错误信息（截断至 500 字）
            cycle_count: 当前 ReAct 循环次数
            llm_latency_ms: 触发本次工具的 LLM 耗时

        Returns:
            {"success": True, "id": str} 或 {"success": False, "error": str}
        """
        record_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)
        truncated_error = (error_message or "")[:500]

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO tool_call_log
                   (id, session_id, tool_name, success, latency_ms, error_type,
                    error_message, cycle_count, llm_latency_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    session_id,
                    tool_name,
                    1 if success else 0,
                    latency_ms,
                    error_type,
                    truncated_error,
                    cycle_count,
                    llm_latency_ms,
                    timestamp,
                ),
            )
            conn.commit()

            # 异步检查降权
            if not success:
                self._async_check_deprioritization(tool_name)

            return {"success": True, "id": record_id}
        except Exception as e:
            logger.warning(f"Failed to record tool call: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    # ── 查询接口 ──────────────────────────────────────────

    def get_tool_success_rate(self, tool_name: str, days: int = 7) -> Dict[str, Any]:
        """查询单个工具的成功率

        Args:
            tool_name: 工具名
            days: 统计天数

        Returns:
            {tool_name, days, total_calls, success_rate, failure_rate,
             avg_latency_ms, today_stats, deprioritized}
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT total_calls, success_calls, failure_calls,
                          success_rate, avg_latency_ms, p95_latency_ms
                   FROM tool_daily_stats
                   WHERE tool_name = ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (tool_name, days),
            )
            rows = cursor.fetchall()
            if not rows:
                return {"tool_name": tool_name, "days": days, "total_calls": 0}

            total_calls = sum(r["total_calls"] for r in rows)
            total_success = sum(r["success_calls"] for r in rows)
            total_failure = sum(r["failure_calls"] for r in rows)
            avg_latency = sum(r["avg_latency_ms"] for r in rows) / len(rows) if rows else 0

            cursor.execute(
                """SELECT deprioritized FROM tool_deprioritization
                   WHERE tool_name = ? AND restored_at IS NULL""",
                (tool_name,),
            )
            deprioritized_row = cursor.fetchone()
            deprioritized = deprioritized_row is not None and deprioritized_row["deprioritized"] == 1

            return {
                "tool_name": tool_name,
                "days": days,
                "total_calls": total_calls,
                "success_rate": total_success / total_calls if total_calls > 0 else 0,
                "failure_rate": total_failure / total_calls if total_calls > 0 else 0,
                "avg_latency_ms": round(avg_latency, 1),
                "today_stats": {
                    "total": rows[0]["total_calls"],
                    "success": rows[0]["success_calls"],
                    "failure": rows[0]["failure_calls"],
                    "success_rate": rows[0]["success_rate"],
                    "p95_latency_ms": rows[0]["p95_latency_ms"],
                } if rows else None,
                "deprioritized": deprioritized,
            }
        except Exception as e:
            logger.error(f"Failed to query tool success rate: {e}")
            return {"tool_name": tool_name, "error": str(e)}
        finally:
            conn.close()

    def get_all_tools_ranking(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取所有工具的成功率排名（失败率从高到低）

        Args:
            days: 统计天数

        Returns:
            [{tool_name, total_calls, success_calls, failures, success_rate, failure_rate, deprioritized}, ...]
        """
        conn = self._get_conn()
        try:
            threshold_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor = conn.execute(
                """SELECT tds.tool_name,
                          SUM(tds.total_calls) as total,
                          SUM(tds.success_calls) as success,
                          SUM(tds.failure_calls) as failure,
                          td.deprioritized
                   FROM tool_daily_stats tds
                   LEFT JOIN tool_deprioritization td
                       ON tds.tool_name = td.tool_name AND td.restored_at IS NULL
                   WHERE tds.date >= ?
                   GROUP BY tds.tool_name
                   ORDER BY CASE WHEN SUM(tds.total_calls) > 0
                       THEN 1.0 - (SUM(tds.success_calls) * 1.0 / SUM(tds.total_calls))
                       ELSE 0 END DESC""",
                (threshold_date,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "tool_name": row["tool_name"],
                    "total_calls": row["total"],
                    "success_calls": row["success"],
                    "failure_calls": row["failure"],
                    "success_rate": row["success"] / row["total"] if row["total"] > 0 else 0,
                    "failure_rate": row["failure"] / row["total"] if row["total"] > 0 else 0,
                    "deprioritized": row["deprioritized"] == 1 if row["deprioritized"] is not None else False,
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Failed to get tool ranking: {e}")
            return []
        finally:
            conn.close()

    def get_failing_tools(self, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """获取失败率超过阈值的所有工具

        Args:
            threshold: 失败率阈值，默认 0.3

        Returns:
            [{tool_name, total_calls, failure_calls, failure_rate}, ...]
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT tool_name, total_calls, failure_calls
                   FROM tool_daily_stats
                   WHERE date = date('now', '+08:00', 'start of day')
                     AND total_calls > 0
                     AND failure_calls * 1.0 / total_calls >= ?
                   ORDER BY failure_calls * 1.0 / total_calls DESC""",
                (threshold,),
            )
            return [
                {
                    "tool_name": row["tool_name"],
                    "total_calls": row["total_calls"],
                    "failure_calls": row["failure_calls"],
                    "failure_rate": row["failure_calls"] / row["total_calls"],
                }
                for row in cursor.fetchall()
            ]
        except Exception as e:
            logger.error(f"Failed to get failing tools: {e}")
            return []
        finally:
            conn.close()

    def get_deprioritized_tools(self) -> List[str]:
        """获取当前被降权的工具列表"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT tool_name FROM tool_deprioritization
                   WHERE deprioritized = 1 AND restored_at IS NULL"""
            )
            return [row["tool_name"] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get deprioritized tools: {e}")
            return []
        finally:
            conn.close()

    def is_deprioritized(self, tool_name: str) -> bool:
        """检查工具是否被降权"""
        return tool_name in self.get_deprioritized_tools()

    # ── 日聚合（由定时任务调用） ──────────────────────────

    def aggregate_daily_stats(self) -> int:
        """执行日聚合统计（应作为每日定时任务执行）

        从 tool_call_log 原始数据聚合到 tool_daily_stats 表。

        Returns:
            聚合的工具数
        """
        conn = self._get_conn()
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            yesterday_start = int((datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).timestamp() * 1000)

            cursor = conn.execute(
                """SELECT tool_name,
                          COUNT(*) as total,
                          SUM(success) as success_calls,
                          SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failure_calls,
                          AVG(latency_ms) as avg_latency
                   FROM tool_call_log
                   WHERE timestamp >= ? AND timestamp < ?
                   GROUP BY tool_name""",
                (yesterday_start, today_start),
            )
            rows = cursor.fetchall()

            count = 0
            for row in rows:
                total = row["total"]
                success = row["success_calls"]
                failure = row["failure_calls"]
                success_rate = success / total if total > 0 else 0
                failure_exceeded = 1 if (failure / total if total > 0 else 0) >= self.deprioritize_threshold else 0

                # P95 latency
                cursor.execute(
                    """SELECT latency_ms FROM tool_call_log
                       WHERE tool_name = ? AND timestamp >= ? AND timestamp < ?
                       ORDER BY latency_ms DESC
                       LIMIT 1 OFFSET MAX(0, ? - 1)""",
                    (row["tool_name"], yesterday_start, today_start, max(1, int(total * 0.05))),
                )
                p95_row = cursor.fetchone()
                p95_latency = p95_row["latency_ms"] if p95_row else row["avg_latency"]

                conn.execute(
                    """INSERT OR REPLACE INTO tool_daily_stats
                       (date, tool_name, total_calls, success_calls, failure_calls,
                        success_rate, avg_latency_ms, p95_latency_ms,
                        failure_threshold_exceeded, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (yesterday, row["tool_name"], total, success, failure,
                     success_rate, row["avg_latency"] or 0, p95_latency or 0,
                     failure_exceeded, int(time.time() * 1000)),
                )
                count += 1

            conn.commit()
            logger.info(f"Daily stats aggregated: {yesterday}, {count} tools")
            return count
        except Exception as e:
            logger.error(f"Failed to aggregate daily stats: {e}")
            return 0
        finally:
            conn.close()

    # ── 降权与恢复 ────────────────────────────────────────

    def _async_check_deprioritization(self, tool_name: str) -> None:
        """异步检查是否需要降权（后台线程，不阻塞记录）"""
        def _check():
            try:
                self._check_and_apply_deprioritization(tool_name)
            except Exception:
                pass

        threading.Thread(target=_check, daemon=True, name=f"dep-check-{tool_name}").start()

    def _get_china_now(self) -> datetime:
        """获取中国时区（UTC+8）的当前时间"""
        return datetime.now(timezone(timedelta(hours=8)))

    def _check_and_apply_deprioritization(self, tool_name: str) -> None:
        """检查失败率，超过阈值则降权"""
        with self._write_lock:
            conn = self._get_conn()
            try:
                today = self._get_china_now().strftime("%Y-%m-%d")
                cursor = conn.execute(
                    """SELECT success_rate FROM tool_daily_stats
                       WHERE tool_name = ? AND date = ?""",
                    (tool_name, today),
                )
                row = cursor.fetchone()
                if row is None:
                    return

                success_rate = row["success_rate"]
                failure_rate = 1.0 - success_rate
                if failure_rate < self.deprioritize_threshold:
                    return

                # 检查是否已降权
                cursor.execute(
                    """SELECT deprioritized FROM tool_deprioritization
                       WHERE tool_name = ? AND restored_at IS NULL""",
                    (tool_name,),
                )
                if cursor.fetchone():
                    return  # 已降权

                conn.execute(
                    """INSERT OR REPLACE INTO tool_deprioritization
                       (tool_name, deprioritized, deprioritize_at, reason, deprioritized_at)
                       VALUES (?, 1, ?, ?, ?)""",
                    (tool_name, self.deprioritize_threshold,
                     f"失败率 {failure_rate:.1%} 超过 {self.deprioritize_threshold:.0%} 阈值",
                     int(time.time() * 1000)),
                )
                conn.commit()
                logger.warning(f"工具 {tool_name} 失败率 {failure_rate:.1%}，已降权")
            except Exception as e:
                logger.error(f"Failed to apply deprioritization: {e}")
            finally:
                conn.close()

    def check_auto_restore(self) -> int:
        """检查可以自动恢复的降权工具（成功率 > 80% 恢复）

        Returns:
            恢复的工具数
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT td.tool_name, tds.success_rate
                   FROM tool_deprioritization td
                   JOIN tool_daily_stats tds ON td.tool_name = tds.tool_name
                   WHERE td.deprioritized = 1
                     AND td.restored_at IS NULL
                     AND tds.date = date('now')""",
            )
            restored = 0
            for row in cursor.fetchall():
                if row["success_rate"] >= 0.8:
                    conn.execute(
                        """UPDATE tool_deprioritization
                           SET restored_at = ?, restored_reason = ?
                           WHERE tool_name = ? AND restored_at IS NULL""",
                        (int(time.time() * 1000),
                         f"自动恢复：成功率回到 {row['success_rate']:.1%}",
                         row["tool_name"]),
                    )
                    restored += 1
                    logger.info(f"工具 {row['tool_name']} 已自动恢复降权（成功率 {row['success_rate']:.1%}）")
            conn.commit()
            return restored
        except Exception as e:
            logger.error(f"Failed to check auto restore: {e}")
            return 0
        finally:
            conn.close()
