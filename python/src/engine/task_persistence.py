"""任务持久化 — 把 ReAct 循环的中间状态存到 SQLite

从 fuxi_engine.py 抽出，便于在崩溃/重启后恢复未完成任务。

通过环境变量 `FUXI_TASK_DB` 启用；留空则禁用（向后兼容）。
"""
import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("task_persistence")


class TaskPersistence:
    """任务状态持久化（基于 SQLite）

    数据表::

        CREATE TABLE task_state (
            session_id TEXT PRIMARY KEY,
            step INTEGER NOT NULL,
            messages TEXT NOT NULL,    -- JSON 序列化
            observations TEXT NOT NULL,  -- JSON 序列化
            tools_used TEXT NOT NULL,  -- JSON 序列化
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """

    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self.enabled = bool(db_path)
        if self.enabled:
            self._init_db()

    def _init_db(self) -> None:
        if not self.enabled:
            return
        self.db_path = self.db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "task_state.db"
        )
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_state (
                    session_id TEXT PRIMARY KEY,
                    step INTEGER NOT NULL,
                    messages TEXT NOT NULL,
                    observations TEXT NOT NULL,
                    tools_used TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            logger.debug(f"Task persistence DB initialized: {self.db_path}")
        except Exception as e:
            logger.warning(f"Failed to init task persistence: {e}")
            self.enabled = False

    def save(
        self,
        session_id: str,
        step: int,
        messages: List[Dict[str, str]],
        observations: List[Dict[str, Any]],
        tools_used: List[Dict[str, Any]],
    ) -> bool:
        if not self.enabled:
            return False
        try:
            conn = sqlite3.connect(self.db_path)
            now = time.time()
            conn.execute("""
                INSERT OR REPLACE INTO task_state
                (session_id, step, messages, observations, tools_used, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM task_state WHERE session_id = ?), ?), ?)
            """, (
                session_id, step,
                json.dumps(messages, ensure_ascii=False),
                json.dumps(observations, ensure_ascii=False),
                json.dumps(tools_used, ensure_ascii=False),
                session_id, now, now
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to save task state: {e}")
            return False

    def restore(self, session_id: str) -> Optional[Dict[str, Any]]:
        """返回 `{"step", "messages", "observations", "tools_used"}` 或 None。"""
        if not self.enabled:
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT step, messages, observations, tools_used FROM task_state WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            step, messages_json, observations_json, tools_used_json = row
            return {
                "step": step,
                "messages": json.loads(messages_json),
                "observations": json.loads(observations_json),
                "tools_used": json.loads(tools_used_json),
            }
        except Exception as e:
            logger.warning(f"Failed to restore task state: {e}")
            return None

    def clear(self, session_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM task_state WHERE session_id = ?", (session_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Failed to clear task state: {e}")
            return False
