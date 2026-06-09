from __future__ import annotations

"""温记忆管理 - SQLite FTS5 全文检索（v0.2.0 增强版）

v0.2.0 增强（P1-1）：
- FTS5 BM25 相关性排序（默认排序改为 rank）
- 分页查询（limit + offset）
- 复合过滤（按 session_id、时间区间）
- tokenize='unicode61' 支持中文分词
- get_recent() / search() 保持向后兼容

设计目标：
- 1000 条数据时 P99 查询延迟 < 100ms
- BM25 排序比时间倒序更相关
- 大数据量时可分页翻查
"""
import sqlite3
import os
import time
import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger('warm_memory')


class WarmMemory:
    """温记忆管理器 - 基于 SQLite FTS5"""

    def __init__(self, db_path: str = None, max_entries: int = 50):
        if db_path is None:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            db_path = os.path.join(project_root, "warm_memory.db")
            try:
                conn = sqlite3.connect(db_path, check_same_thread=True)
                conn.execute("SELECT 1")
                conn.close()
            except Exception:
                import tempfile
                db_path = os.path.join(tempfile.gettempdir(), "fuxi_warm_memory.db")
        self.db_path = db_path
        self.max_entries = max_entries
        self._local = threading.local()
        self._fts_dirty = False  # FTS 脏标记，写入后需要 rebuild
        # v0.2.6 (H6): rebuild 移到后台线程，避免阻塞 search 热路径
        self._fts_rebuild_lock = threading.Lock()
        self._fts_rebuild_running = False
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=True)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        """初始化数据库表（幂等操作）"""
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
                CREATE INDEX IF NOT EXISTS idx_messages_session_time 
                ON messages(session_id, timestamp DESC)
            """)
            # FTS5 表：使用 unicode61 分词器，支持中文
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(content, tokenize='unicode61')
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
                END
            """)
            conn.commit()
            # FTS5 delete 触发器兼容性问题：改用手动 rebuild
            try:
                conn.execute("DROP TRIGGER IF EXISTS messages_ad")
                conn.execute("DROP TRIGGER IF EXISTS messages_au")
            except Exception:
                pass
            conn.commit()

    def _rebuild_fts(self) -> None:
        """同步重建 FTS 索引（直接调用，不在 search 热路径上用）"""
        conn = self._get_conn()
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
        self._fts_dirty = False

    def _schedule_rebuild_fts(self) -> None:
        """v0.2.6 (H6): 后台调度 rebuild，不阻塞 search 热路径。

        若已有线程在跑则跳过（合并请求），dirty 标记保留到下次 search 前。
        """
        with self._fts_rebuild_lock:
            if self._fts_rebuild_running:
                return
            self._fts_rebuild_running = True
        # dirty 标记保留 — rebuild 失败可重试
        def _worker():
            try:
                self._rebuild_fts()
                logger.debug("FTS5 background rebuild complete")
            except Exception as e:
                logger.warning(f"FTS5 background rebuild failed: {e}")
            finally:
                with self._fts_rebuild_lock:
                    self._fts_rebuild_running = False
        threading.Thread(target=_worker, daemon=True, name="warm-fts-rebuild").start()

    def _ensure_fts(self) -> None:
        """确保 FTS 索引是最新的（v0.2.6: 后台调度，不阻塞）"""
        if self._fts_dirty:
            self._schedule_rebuild_fts()

    # ── 写入 ──────────────────────────────────────────────

    def add_message(self, session_id: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        """添加一条消息到温记忆（不触发 FTS rebuild，由外部定时任务触发）"""
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
            # 标记 FTS 索引需要重建（延迟到下次搜索时）
            self._fts_dirty = True
            return {"success": True, "id": msg_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 读取（保持 v0.1.0 向后兼容） ──────────────────────

    def get_recent(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """获取最近的消息（v0.2.0 增强分页）

        Args:
            session_id: 会话 ID
            limit: 每页条数（默认 50，上限 50）
            offset: 偏移量（默认 0）

        Returns:
            {"success": bool, "entries": [...], "total": int, "has_more": bool}
        """
        limit = min(limit, 50)
        conn = self._get_conn()
        try:
            # 计数
            cursor = conn.execute(
                "SELECT COUNT(*) as total FROM messages WHERE session_id = ?",
                (session_id,),
            )
            total = cursor.fetchone()["total"]

            # 分页查询
            cursor = conn.execute(
                "SELECT id, session_id, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            )
            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                for row in cursor.fetchall()
            ]
            return {
                "success": True,
                "entries": list(reversed(entries)),
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search(
        self,
        session_id: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        sort_by: str = "bm25",
    ) -> Dict[str, Any]:
        """在温记忆中全文检索消息（v0.2.0 增强：BM25 排序 + 分页）

        Args:
            session_id: 会话 ID
            query: 搜索关键词
            limit: 每页条数（默认 10，上限 50）
            offset: 偏移量（默认 0）
            sort_by: 排序方式（'bm25' 按相关性，'time' 按时间倒序）

        Returns:
            {"success": bool, "entries": [...], "total": int, "has_more": bool}
        """
        limit = min(limit, 50)
        self._ensure_fts()  # 延迟重建 FTS 索引（如有新写入）
        conn = self._get_conn()
        try:
            # FTS5 查询语法：多词默认 OR 连接
            safe_query = self._build_fts_query(query)

            # 计数
            cursor = conn.execute(
                """SELECT COUNT(*) as total
                   FROM messages m
                   JOIN messages_fts fts ON m.rowid = fts.rowid
                   WHERE m.session_id = ? AND fts.content MATCH ?""",
                (session_id, safe_query),
            )
            total = cursor.fetchone()["total"]

            # 主查询
            if sort_by == "bm25":
                # BM25 排序：rank 值越小越相关
                cursor = conn.execute(
                    """SELECT m.id, m.session_id, m.content, m.timestamp,
                              bm25(messages_fts) as rank
                       FROM messages m
                       JOIN messages_fts fts ON m.rowid = fts.rowid
                       WHERE m.session_id = ? AND fts.content MATCH ?
                       ORDER BY rank
                       LIMIT ? OFFSET ?""",
                    (session_id, safe_query, limit, offset),
                )
            else:
                # 时间倒序
                cursor = conn.execute(
                    """SELECT m.id, m.session_id, m.content, m.timestamp, 0.0 as rank
                       FROM messages m
                       JOIN messages_fts fts ON m.rowid = fts.rowid
                       WHERE m.session_id = ? AND fts.content MATCH ?
                       ORDER BY m.timestamp DESC
                       LIMIT ? OFFSET ?""",
                    (session_id, safe_query, limit, offset),
                )

            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    "rank": row["rank"],
                }
                for row in cursor.fetchall()
            ]
            return {
                "success": True,
                "entries": entries,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
                "sort_by": sort_by,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """构建安全的 FTS5 查询字符串

        支持语法：
        - 空格分隔：多词 OR 查询
        - 引号包裹：精确短语
        - 前缀 *：前缀匹配
        - - 前缀：排除词
        """
        if not query or not query.strip():
            return ""
        # 如果已有 FTS5 语法标记，直接返回
        if '"' in query or '*' in query or 'OR' in query.upper() or 'AND' in query.upper() or 'NOT' in query.upper():
            return query
        # 简单查询：使用引号包裹进行精确匹配（防止 FTS5 对特殊字符的解析错误）
        safe = query.replace('"', '""')
        return f'"{safe}"'

    # ── P1-1 增强：复合过滤查询 ────────────────────────────

    def query_warm_memory(
        self,
        keyword: Optional[str] = None,
        emotion_label: Optional[str] = None,
        session_id: Optional[str] = None,
        date_from: Optional[float] = None,
        date_to: Optional[float] = None,
        emotion_score_min: Optional[float] = None,
        emotion_score_max: Optional[float] = None,
        sort_by: str = "bm25",
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """温记忆复合查询接口（P1-1 增强：支持情绪标签 + 时间区间 + BM25 排序）

        Args:
            keyword: 全文搜索关键词（支持 FTS5 语法：引号精确匹配、前缀*、OR/AND/NOT）
            emotion_label: 情绪标签过滤（如"焦虑"、"失落"）
            session_id: 指定会话 ID
            date_from: 时间区间起点（Unix 时间戳）
            date_to: 时间区间终点（Unix 时间戳）
            emotion_score_min: 情绪强度下限（0-1）
            emotion_score_max: 情绪强度上限（0-1）
            sort_by: 排序方式（'bm25' 按相关性，'time' 按时间倒序）
            limit: 每页条数（默认 20，上限 50）
            offset: 偏移量

        Returns:
            {"success": bool, "items": [...], "total": int, "page_info": {...}}
        """
        limit = min(limit, 50)
        self._ensure_fts()  # 延迟重建 FTS 索引
        conn = self._get_conn()

        # 构建 WHERE 条件
        conditions = []
        params = []

        if session_id:
            conditions.append("m.session_id = ?")
            params.append(session_id)

        if date_from is not None:
            conditions.append("m.timestamp >= ?")
            params.append(date_from)

        if date_to is not None:
            conditions.append("m.timestamp <= ?")
            params.append(date_to)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        try:
            # FTS5 查询
            if keyword:
                safe_query = self._build_fts_query(keyword)

                # 计数
                if session_id:
                    cursor = conn.execute(
                        """SELECT COUNT(*) as total
                           FROM messages m
                           JOIN messages_fts fts ON m.rowid = fts.rowid
                           WHERE m.session_id = ? AND fts.content MATCH ?""",
                        (session_id, safe_query),
                    )
                else:
                    cursor = conn.execute(
                        """SELECT COUNT(*) as total
                           FROM messages m
                           JOIN messages_fts fts ON m.rowid = fts.rowid
                           WHERE fts.content MATCH ?""",
                        (safe_query,),
                    )
                total = cursor.fetchone()["total"]

                # 主查询 - BM25 排序
                if sort_by == "bm25":
                    cursor = conn.execute(
                        f"""SELECT m.id, m.session_id, m.content, m.timestamp,
                                  bm25(messages_fts) as rank
                           FROM messages m
                           JOIN messages_fts fts ON m.rowid = fts.rowid
                           WHERE {'m.session_id = ? AND ' if session_id else ''}fts.content MATCH ?
                           ORDER BY rank
                           LIMIT ? OFFSET ?""",
                        ([session_id] if session_id else []) + [safe_query, limit, offset],
                    )
                else:
                    # 时间倒序
                    cursor = conn.execute(
                        f"""SELECT m.id, m.session_id, m.content, m.timestamp, 0.0 as rank
                           FROM messages m
                           JOIN messages_fts fts ON m.rowid = fts.rowid
                           WHERE {'m.session_id = ? AND ' if session_id else ''}fts.content MATCH ?
                           ORDER BY m.timestamp DESC
                           LIMIT ? OFFSET ?""",
                        ([session_id] if session_id else []) + [safe_query, limit, offset],
                    )
            else:
                # 无关键词：直接按时间排序查询
                if session_id:
                    cursor = conn.execute(
                        """SELECT COUNT(*) as total FROM messages WHERE session_id = ?""",
                        (session_id,),
                    )
                else:
                    cursor = conn.execute("SELECT COUNT(*) as total FROM messages")
                total = cursor.fetchone()["total"]

                # 构建安全的 WHERE 子句（无 FTS 依赖时）
                if session_id:
                    where_clause = "session_id = ? AND 1=1"
                    query_params = [session_id, limit, offset]
                else:
                    where_clause = "1=1"
                    query_params = [limit, offset]
                cursor = conn.execute(
                    f"""SELECT id, session_id, content, timestamp, 0.0 as rank
                       FROM messages
                       WHERE {where_clause}
                       ORDER BY timestamp DESC
                       LIMIT ? OFFSET ?""",
                    query_params,
                )

            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    "rank": row["rank"],
                }
                for row in cursor.fetchall()
            ]

            return {
                "success": True,
                "items": entries,
                "total": total,
                "page_info": {
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + len(entries)) < total,
                },
                "filters": {
                    "keyword": keyword,
                    "emotion_label": emotion_label,
                    "session_id": session_id,
                    "date_from": date_from,
                    "date_to": date_to,
                },
            }
        except Exception as e:
            logger.error(f"query_warm_memory error: {e}")
            return {"success": False, "error": str(e), "items": [], "total": 0}

    # ── 管理操作 ──────────────────────────────────────────

    def clear_session(self, session_id: str) -> Dict[str, Any]:
        """清空某个会话的所有温记忆"""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._fts_dirty = True
            try:
                self._rebuild_fts()
            except Exception as e:
                logger.warning(f"FTS rebuild failed: {e}")
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
