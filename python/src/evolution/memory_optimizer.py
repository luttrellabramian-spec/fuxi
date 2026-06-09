from __future__ import annotations

"""记忆优化器 - 基于反馈优化记忆检索策略

功能：
1. 追踪记忆检索的上下文和效果
2. 学习在不同场景下使用何种记忆检索策略
3. 自适应调整检索参数（阈值、数量、检索方式）
4. 为 FuxiEngine 提供最优的记忆分组策略

数据存储：evolution.db（与 strategy_profiler 共用）
"""
import os
import sqlite3
import json
import time
import threading
import logging
from typing import Dict, Any

logger = logging.getLogger("memory_optimizer")

DEFAULT_EVOLUTION_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "evolution.db",
)

CREATE_MEMORY_LOG_SQL = """
CREATE TABLE IF NOT EXISTS memory_retrieval_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    retrieval_type  TEXT NOT NULL,   -- 'hot' | 'warm_fts' | 'cold_vector'
    query_text      TEXT DEFAULT '',
    retrieved_count INTEGER NOT NULL DEFAULT 0,
    actual_used     INTEGER DEFAULT NULL,  -- NULL=未记录, 0=未使用, >0=使用数
    success         INTEGER NOT NULL DEFAULT 1,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    timestamp       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_retrieval ON memory_retrieval_log(retrieval_type, success DESC);
"""

CREATE_MEMORY_OPTIMIZATION_SQL = """
CREATE TABLE IF NOT EXISTS memory_optimization (
    param_name      TEXT PRIMARY KEY,
    param_value     TEXT NOT NULL,        -- JSON 格式
    total_uses      INTEGER NOT NULL DEFAULT 0,
    last_updated    INTEGER NOT NULL
);
"""


class MemoryOptimizer:
    """记忆优化器 - 自适应记忆检索参数优化"""

    def __init__(self, db_path: str = None):
        if db_path is None or db_path == "":
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".db", prefix="fuxi_memory_")
            os.close(fd)

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
        except Exception:
            import tempfile
            fd, db_path = tempfile.mkstemp(suffix=".db", prefix="fuxi_memory_")
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
            conn.executescript(CREATE_MEMORY_LOG_SQL)
            conn.executescript(CREATE_MEMORY_OPTIMIZATION_SQL)
            defaults = {
                "cold_similarity_threshold": "0.3",
                "warm_search_limit": "10",
                "cold_search_limit": "5",
                "hot_max_chars": "800",
                "enable_warm_search": "true",
                "enable_cold_search": "true",
            }
            for name, value in defaults.items():
                conn.execute(
                    """INSERT OR IGNORE INTO memory_optimization
                       (param_name, param_value, total_uses, last_updated)
                       VALUES (?, ?, 0, ?)""",
                    (name, value, int(time.time() * 1000)),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to init memory optimization tables: {e}")
        finally:
            conn.close()

    # ── 记录 ──────────────────────────────────────────────

    def record_retrieval(
        self,
        trace_id: str,
        session_id: str,
        retrieval_type: str,
        query_text: str = "",
        retrieved_count: int = 0,
        elapsed_ms: int = 0,
    ) -> None:
        """记录一次记忆检索操作"""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO memory_retrieval_log
                   (trace_id, session_id, retrieval_type, query_text,
                    retrieved_count, elapsed_ms, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id, session_id, retrieval_type, query_text[:200],
                    retrieved_count, elapsed_ms, int(time.time() * 1000),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to record retrieval: {e}")
        finally:
            conn.close()

    def record_memory_usage(self, trace_id: str, actual_used: int) -> None:
        """记录检索出的记忆有多少被实际使用"""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE memory_retrieval_log
                   SET actual_used = ?
                   WHERE trace_id = ? AND actual_used IS NULL
                   ORDER BY timestamp DESC LIMIT 3""",
                (actual_used, trace_id),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to record memory usage: {e}")
        finally:
            conn.close()

    # ── 参数优化 ──────────────────────────────────────────

    def get_param(self, param_name: str, default: Any = None) -> Any:
        """获取优化参数"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT param_value FROM memory_optimization
                   WHERE param_name = ?""",
                (param_name,),
            )
            row = cursor.fetchone()
            if row:
                raw = row[0] if isinstance(row, (list, tuple)) else row["param_value"]
                # 尝试 JSON 解析处理复杂类型（list/dict）
                # 但字符串/数字/布尔值保持原始字符串形态
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                    return raw  # 字符串/数字/布尔值保持原始字符串
                except (json.JSONDecodeError, TypeError, ValueError):
                    return raw
            return default
        except Exception as e:
            logger.warning(f"Failed to get param {param_name}: {e}")
            return default
        finally:
            conn.close()

    def update_param(self, param_name: str, param_value: Any) -> None:
        """更新优化参数"""
        value_str = json.dumps(param_value) if not isinstance(param_value, str) else param_value
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE memory_optimization
                   SET param_value = ?, total_uses = total_uses + 1,
                       last_updated = ?
                   WHERE param_name = ?""",
                (value_str, int(time.time() * 1000), param_name),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to update param {param_name}: {e}")
        finally:
            conn.close()

    def get_retrieval_recommendation(self, query_type: str) -> Dict[str, Any]:
        """获取记忆检索建议

        Args:
            query_type: 查询类型

        Returns:
            {cold_threshold, warm_limit, cold_limit, hot_max_chars, ...}
        """
        return {
            "cold_similarity_threshold": float(self.get_param("cold_similarity_threshold", 0.3)),
            "warm_search_limit": int(self.get_param("warm_search_limit", 10)),
            "cold_search_limit": int(self.get_param("cold_search_limit", 5)),
            "hot_max_chars": int(self.get_param("hot_max_chars", 800)),
            "enable_warm_search": self.get_param("enable_warm_search", "true") == "true",
            "enable_cold_search": self.get_param("enable_cold_search", "true") == "true",
        }

    # ── 统计 ──────────────────────────────────────────────

    def get_retrieval_stats(self) -> Dict[str, Any]:
        """获取检索统计"""
        conn = self._get_conn()
        try:
            stats = {}
            cursor = conn.execute(
                """SELECT retrieval_type,
                          COUNT(*) as total,
                          AVG(elapsed_ms) as avg_elapsed,
                          AVG(retrieved_count) as avg_count
                   FROM memory_retrieval_log
                   GROUP BY retrieval_type"""
            )
            for row in cursor.fetchall():
                stats[row["retrieval_type"]] = {
                    "total_retrievals": row["total"],
                    "avg_elapsed_ms": round(row["avg_elapsed"] or 0, 1),
                    "avg_retrieved": round(row["avg_count"] or 0, 1),
                }
            return stats
        except Exception as e:
            logger.warning(f"Failed to get retrieval stats: {e}")
            return {}
        finally:
            conn.close()

    def auto_tune_threshold(self) -> float:
        """根据历史数据自动调整冷记忆相似度阈值

        策略：
        - 如果检索到的记忆被使用的比例高，降低阈值（检索更多）
        - 如果检索结果很少被使用，提高阈值（减少噪音）
        """
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT AVG(actual_used * 1.0 / retrieved_count) as usage_ratio
                   FROM memory_retrieval_log
                   WHERE retrieval_type = 'cold_vector'
                     AND actual_used IS NOT NULL
                     AND retrieved_count > 0"""
            )
            row = cursor.fetchone()

            current_threshold = float(self.get_param("cold_similarity_threshold", 0.3))

            if row and row["usage_ratio"] is not None:
                usage_ratio = row["usage_ratio"]
                if usage_ratio > 0.8 and current_threshold > 0.15:
                    # 高效使用，降低阈值检索更多
                    new_threshold = round(current_threshold - 0.05, 2)
                elif usage_ratio < 0.3 and current_threshold < 0.6:
                    # 低效使用，提高阈值减少噪音
                    new_threshold = round(current_threshold + 0.05, 2)
                else:
                    new_threshold = current_threshold

                if new_threshold != current_threshold:
                    self.update_param("cold_similarity_threshold", str(new_threshold))
                    logger.info(
                        f"MemoryOptimizer: 调整冷检索阈值 {current_threshold} → {new_threshold}"
                        f" (usage_ratio={usage_ratio:.2f})"
                    )
                    return new_threshold

            return current_threshold
        except Exception as e:
            logger.warning(f"Failed to auto tune threshold: {e}")
            return 0.3
        finally:
            conn.close()

    def get_all_parameters(self) -> Dict[str, Any]:
        """获取所有优化参数"""
        conn = self._get_conn()
        try:
            params = {}
            cursor = conn.execute("SELECT * FROM memory_optimization")
            for row in cursor.fetchall():
                try:
                    params[row["param_name"]] = json.loads(row["param_value"])
                except (json.JSONDecodeError, TypeError):
                    params[row["param_name"]] = row["param_value"]
            return params
        except Exception as e:
            logger.warning(f"Failed to get all parameters: {e}")
            return {}
        finally:
            conn.close()
