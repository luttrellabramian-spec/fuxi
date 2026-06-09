from __future__ import annotations

"""策略概览器 - 追踪不同策略的成功率，自适应优化 ReAct 参数

学习内容：
- 不同查询类别的成功策略（直接回答 vs 多步工具调用）
- 最优 max_steps 和 temperature 配置
- 策略模式演化

数据存储：SQLite（evolution.db）
"""
import os
import sqlite3
import json
import time
import threading
import logging
from typing import Dict, Any, List

from evolution.query_classifier import QUERY_CATEGORIES

logger = logging.getLogger("strategy_profiler")

DEFAULT_EVOLUTION_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evolution.db",
)

# DDL
CREATE_STRATEGY_LOG_SQL = """
CREATE TABLE IF NOT EXISTS strategy_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    query_category  TEXT NOT NULL,
    strategy_type   TEXT NOT NULL,   -- 'direct' | 'react_multi_step' | 'react_single'
    max_steps       INTEGER NOT NULL DEFAULT 10,
    temperature     REAL NOT NULL DEFAULT 0.2,
    actual_steps    INTEGER NOT NULL DEFAULT 0,
    completed       INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    tools_used      TEXT DEFAULT '',           -- JSON 数组
    success         INTEGER NOT NULL DEFAULT 0,
    error_type      TEXT DEFAULT '',
    timestamp       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_cat ON strategy_log(query_category, success DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_time ON strategy_log(timestamp DESC);
"""

CREATE_STRATEGY_STATS_SQL = """
CREATE TABLE IF NOT EXISTS strategy_stats (
    query_category   TEXT NOT NULL,
    strategy_type    TEXT NOT NULL,
    total_runs       INTEGER NOT NULL DEFAULT 0,
    success_runs     INTEGER NOT NULL DEFAULT 0,
    avg_steps        REAL NOT NULL DEFAULT 0.0,
    avg_elapsed_ms   REAL NOT NULL DEFAULT 0.0,
    avg_tokens       REAL NOT NULL DEFAULT 0.0,
    success_rate     REAL NOT NULL DEFAULT 0.0,
    recommend_steps  INTEGER NOT NULL DEFAULT 6,
    recommend_temp   REAL NOT NULL DEFAULT 0.2,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (query_category, strategy_type)
);
"""


class StrategyProfiler:
    """策略概览器 - 自适应优化引擎参数"""

    def __init__(self, db_path: str = None):
        # 自动生成有效路径
        if db_path is None or db_path == "":
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".db", prefix="fuxi_strategy_")
            os.close(fd)

        # 中文路径兼容
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
        except Exception:
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".db", prefix="fuxi_strategy_")
            os.close(fd)

        self.db_path = db_path
        self._write_lock = threading.Lock()
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_tables(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(CREATE_STRATEGY_LOG_SQL)
            conn.executescript(CREATE_STRATEGY_STATS_SQL)
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to init strategy tables: {e}")
        finally:
            conn.close()

    # ── 记录 ──────────────────────────────────────────────

    def record_run(
        self,
        trace_id: str,
        session_id: str,
        query_category: str,
        max_steps: int,
        temperature: float,
        actual_steps: int,
        completed: bool,
        total_tokens: int,
        elapsed_ms: int,
        tools_used: List[str],
        success: bool,
        error_type: str = "",
    ) -> bool:
        """记录一次引擎运行

        Args:
            trace_id: 追踪 ID
            session_id: 会话 ID
            query_category: 查询类别
            max_steps: 配置的最大步数
            temperature: 使用的温度
            actual_steps: 实际执行步数
            completed: 是否完成（得出 final 答案）
            total_tokens: 消耗的总 tokens
            elapsed_ms: 耗时（毫秒）
            tools_used: 使用的工具列表
            success: 是否成功
            error_type: 错误类型
        """
        strategy_type = self._determine_strategy(actual_steps, tools_used)

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO strategy_log
                   (trace_id, session_id, query_category, strategy_type,
                    max_steps, temperature, actual_steps, completed,
                    total_tokens, elapsed_ms, tools_used, success,
                    error_type, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id, session_id, query_category, strategy_type,
                    max_steps, temperature, actual_steps, 1 if completed else 0,
                    total_tokens, elapsed_ms,
                    json.dumps(tools_used, ensure_ascii=False),
                    1 if success else 0,
                    error_type,
                    int(time.time() * 1000),
                ),
            )
            conn.commit()

            # 更新统计
            self._update_stats(query_category, strategy_type)
            return True
        except Exception as e:
            logger.warning(f"Failed to record strategy run: {e}")
            return False
        finally:
            conn.close()

    def _determine_strategy(self, steps: int, tools_used: List[str]) -> str:
        """根据实际执行情况确定策略类型"""
        if steps == 0 or (steps == 1 and not tools_used):
            return "direct"
        elif steps == 1 and len(tools_used) <= 1:
            return "react_single"
        else:
            return "react_multi_step"

    def _update_stats(self, query_category: str, strategy_type: str) -> None:
        """更新策略统计"""
        with self._write_lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """SELECT COUNT(*), SUM(success), AVG(actual_steps),
                              AVG(elapsed_ms), AVG(total_tokens)
                       FROM strategy_log
                       WHERE query_category = ? AND strategy_type = ?""",
                    (query_category, strategy_type),
                )
                row = cur.fetchone()
                if row and row[0] and row[0] > 0:
                    total = int(row[0])
                    successes = int(row[1] or 0)
                    avg_steps_val = float(row[2] or 0)
                    avg_elapsed = float(row[3] or 0)
                    avg_tokens = float(row[4] or 0)
                    success_rate = successes / total

                    # 计算推荐步数
                    cur.execute(
                        """SELECT AVG(actual_steps)
                           FROM strategy_log
                           WHERE query_category = ? AND strategy_type = ?
                             AND success = 1""",
                        (query_category, strategy_type),
                    )
                    success_row = cur.fetchone()
                    recommend_steps = int((success_row[0] or 5) + 2)

                    # 推荐温度
                    cur.execute(
                        """SELECT AVG(temperature)
                           FROM strategy_log
                           WHERE query_category = ? AND strategy_type = ?
                             AND success = 1""",
                        (query_category, strategy_type),
                    )
                    temp_row = cur.fetchone()
                    recommend_temp = temp_row[0] or 0.2

                    conn.execute(
                        """INSERT OR REPLACE INTO strategy_stats
                           (query_category, strategy_type, total_runs, success_runs,
                            avg_steps, avg_elapsed_ms, avg_tokens, success_rate,
                            recommend_steps, recommend_temp, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            query_category, strategy_type, total, successes,
                            avg_steps_val, avg_elapsed, avg_tokens, success_rate,
                            min(recommend_steps, 15),
                            round(recommend_temp, 1),
                            int(time.time() * 1000),
                        ),
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Failed to update strategy stats: {e}")
            finally:
                conn.close()

    # ── 查询 ──────────────────────────────────────────────

    def get_recommendation(self, query_category: str) -> Dict[str, Any]:
        """获取某个查询类别的优化建议"""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT recommend_steps, recommend_temp, success_rate,
                          total_runs, avg_elapsed_ms, avg_steps
                   FROM strategy_stats
                   WHERE query_category = ?
                   ORDER BY success_rate DESC
                   LIMIT 1""",
                (query_category,),
            )
            best = cursor.fetchone()

            if best is None:
                cat = QUERY_CATEGORIES.get(query_category)
                default = cat if cat else None
                return {
                    "recommend_steps": default.recommended_steps if default else 6,
                    "recommend_temp": default.recommended_temp if default else 0.2,
                    "best_strategy": "react_multi_step",
                    "success_rate": 0.0,
                    "total_runs": 0,
                    "avg_elapsed_ms": 0,
                    "avg_steps": 0,
                    "strategies": [],
                }

            # 找最佳策略
            strategy_cursor = conn.cursor()
            strategy_cursor.execute(
                """SELECT strategy_type, success_rate
                   FROM strategy_stats
                   WHERE query_category = ?
                   ORDER BY success_rate DESC
                   LIMIT 3""",
                (query_category,),
            )
            strategies_raw = strategy_cursor.fetchall()
            strategies = [
                {"type": s[0], "success_rate": s[1]}
                for s in strategies_raw
            ]
            best_strategy = strategies[0]["type"] if strategies else "react_multi_step"

            return {
                "recommend_steps": min(int(best[0]), 15),
                "recommend_temp": float(best[1]),
                "best_strategy": best_strategy,
                "success_rate": float(best[2]),
                "total_runs": int(best[3]),
                "avg_elapsed_ms": float(best[4]),
                "avg_steps": float(best[5]),
                "strategies": strategies,
            }
        except Exception as e:
            logger.warning(f"Failed to get strategy recommendation: {e}")
            return {"recommend_steps": 6, "recommend_temp": 0.2}
        finally:
            conn.close()

    def get_category_stats(self, query_category: str) -> Dict[str, Any]:
        """获取某个查询类别的详细统计"""
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT COUNT(*), SUM(success), AVG(actual_steps), AVG(elapsed_ms)
                   FROM strategy_log
                   WHERE query_category = ?""",
                (query_category,),
            )
            row = cur.fetchone()
            if not row or not row[0] or row[0] == 0:
                return {"total_runs": 0, "success_rate": 0}

            total = int(row[0])
            successes = int(row[1] or 0)
            return {
                "query_category": query_category,
                "total_runs": total,
                "success_runs": successes,
                "success_rate": successes / total,
                "avg_steps": round(float(row[2] or 0), 1),
                "avg_elapsed_ms": round(float(row[3] or 0), 0),
            }
        except Exception as e:
            logger.warning(f"Failed to get category stats: {e}")
            return {"total_runs": 0}
        finally:
            conn.close()

    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有类别的汇总统计"""
        conn = self._get_conn()
        try:
            categories = {}
            cursor = conn.execute(
                """SELECT DISTINCT query_category FROM strategy_stats"""
            )
            for row in cursor.fetchall():
                cat = row["query_category"]
                categories[cat] = self.get_category_stats(cat)
            return categories
        except Exception as e:
            logger.warning(f"Failed to get all stats: {e}")
            return {}
        finally:
            conn.close()
