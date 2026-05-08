"""温记忆管理 - SQLite FTS5 全文检索"""
import sqlite3
import time
import threading
from typing import Dict, Any, List, Optional


class WarmMemory:
    """温记忆管理器 - 基于 SQLite FTS5"""

    def __init__(self, db_path: str = "warm_memory.db", max_entries: int = 50):
        self.db_path = db_path
        self.max_entries = max_entries
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content)
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
                END
            """)
            conn.commit()
            # 注意：FTS5 delete 触发器在部分 SQLite 版本有 bug（SQL logic error）
            # 删除和更新改用手动 rebuild
            try:
                conn.execute("DROP TRIGGER IF EXISTS messages_ad")
                conn.execute("DROP TRIGGER IF EXISTS messages_au")
            except Exception:
                pass
            conn.commit()

    def add_message(self, session_id: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        """添加一条消息到温记忆"""
        import uuid
        msg_id = msg_id or str(uuid.uuid4())
        timestamp = time.time()
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO messages (id, session_id, content, timestamp) VALUES (?, ?, ?, ?)",
                    (msg_id, session_id, content, timestamp),
                )
                # 清理超过 max_entries 的旧消息（仅限同一会话）
                conn.execute("""
                    DELETE FROM messages
                    WHERE session_id = ?
                    AND id NOT IN (
                        SELECT id FROM messages
                        WHERE session_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                """, (session_id, session_id, self.max_entries))
            return {"success": True, "id": msg_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_recent(self, session_id: str, limit: int = 50) -> Dict[str, Any]:
        """获取最近的消息"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT id, session_id, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            )
            rows = cursor.fetchall()
            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ]
            return {"success": True, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search(self, session_id: str, query: str, limit: int = 10) -> Dict[str, Any]:
        """在温记忆中全文检索消息"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT m.id, m.session_id, m.content, m.timestamp
                FROM messages m
                JOIN messages_fts fts ON m.rowid = fts.rowid
                WHERE m.session_id = ? AND fts.content MATCH ?
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (session_id, query, limit),
            )
            rows = cursor.fetchall()
            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ]
            return {"success": True, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _rebuild_fts(self) -> None:
        """重建 FTS 索引（用于同步被删除/更新的消息）"""
        conn = self._get_conn()
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()

    def clear_session(self, session_id: str) -> Dict[str, Any]:
        """清空某个会话的所有温记忆"""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            # 重建 FTS 索引以同步删除
            try:
                self._rebuild_fts()
            except Exception:
                pass
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stats(self) -> Dict[str, Any]:
        """获取温记忆统计"""
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT COUNT(*) as total FROM messages")
            total = cursor.fetchone()["total"]
            cursor = conn.execute("SELECT COUNT(DISTINCT session_id) as sessions FROM messages")
            sessions = cursor.fetchone()["sessions"]
            return {"total_messages": total, "total_sessions": sessions}
        except Exception as e:
            return {"error": str(e)}

    def close(self) -> None:
        """关闭连接池"""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
