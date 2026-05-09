"""冷记忆管理 - 向量检索（基于 Hugging Face sentence-transformers 或纯 SQLite 回退）"""
import os
import sqlite3
import threading
import time
import uuid
import json
from typing import Dict, Any, List, Optional


try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    _HAS_EMBEDDING = True
except Exception:
    _HAS_EMBEDDING = False


class ColdMemory:
    """冷记忆管理器 - 向量检索（带纯 SQLite 回退模式）"""

    def __init__(self, db_path: str = "cold_memory.db", embedding_model: str = "all-MiniLM-L6-v2"):
        self.db_path = db_path
        self._local = threading.local()
        self._has_embedding = _HAS_EMBEDDING
        self._model = None
        self._embedding_dim = 384  # all-MiniLM-L6-v2 默认维度
        if _HAS_EMBEDDING:
            try:
                self._model = SentenceTransformer(embedding_model)
                self._embedding_dim = self._model.get_embedding_dimension() or 384  # Updated method name
            except Exception:
                self._has_embedding = False

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """生成文本 embedding"""
        if not self._has_embedding or not self._model:
            return None
        try:
            vec = self._model.encode(text, convert_to_numpy=True, show_progress_bar=False)
            return vec.tolist()
        except Exception:
            return None

    def _init_db(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    embedding BLOB,
                    timestamp REAL NOT NULL,
                    metadata TEXT
                )
            """)
            conn.commit()

    def insert_summary(
        self,
        content: str,
        summary: str,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        msg_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """插入一条摘要到冷记忆"""
        msg_id = msg_id or str(uuid.uuid4())
        timestamp = time.time()
        embedding = self._get_embedding(summary)
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO summaries (id, session_id, content, summary, embedding, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        session_id,
                        content,
                        summary,
                        sqlite3.Binary(np.array(embedding, dtype=np.float32).tobytes()) if embedding else None,
                        timestamp,
                        json.dumps(metadata or {}),
                    ),
                )
            return {"success": True, "id": msg_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """计算余弦相似度"""
        import math
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def search_similar(
        self,
        query: str,
        limit: int = 10,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """搜索相似的摘要（向量检索或纯文本回退）"""
        conn = self._get_conn()
        try:
            # 空查询直接返回最近条目
            if not query or not query.strip():
                recent = self.get_recent(session_id, limit)
                entries = [{
                    "id": e["id"],
                    "session_id": e["session_id"],
                    "content": e["content"],
                    "summary": e["summary"],
                    "metadata": e.get("metadata", "{}"),
                    "timestamp": e["timestamp"],
                    "similarity": 0.0,
                } for e in recent.get("entries", [])]
                return {"success": True, "entries": entries}

            query_embedding = self._get_embedding(query) if self._has_embedding else None

            if query_embedding and self._has_embedding:
                cursor = conn.execute(
                    "SELECT id, session_id, content, summary, embedding, metadata, timestamp FROM summaries WHERE embedding IS NOT NULL"
                    + (" AND session_id = ?" if session_id else ""),
                    (session_id,) if session_id else (),
                )
                rows = cursor.fetchall()
                scored = []
                qv = np.array(query_embedding)
                for row in rows:
                    emb_bytes = row["embedding"]
                    if emb_bytes:
                        sv = np.frombuffer(emb_bytes, dtype=np.float32)
                        sim = float(np.dot(qv, sv) / (np.linalg.norm(qv) * np.linalg.norm(sv) + 1e-8))
                        scored.append((sim, row))
                scored.sort(key=lambda x: x[0], reverse=True)
                # 只保留相似度 > 0.3 的结果
                scored = [(s, r) for s, r in scored if s > 0.3]
                entries = [
                    {
                        "id": r["id"],
                        "session_id": r["session_id"],
                        "content": r["content"],
                        "summary": r["summary"],
                        "metadata": r["metadata"] or "{}",
                        "timestamp": r["timestamp"],
                        "similarity": s,
                    }
                    for s, r in scored[:limit]
                ]
            else:
                pattern = f"%{query}%"
                cursor = conn.execute(
                    "SELECT id, session_id, content, summary, metadata, timestamp FROM summaries WHERE (summary LIKE ? OR content LIKE ?)"
                    + (" AND session_id = ?" if session_id else "")
                    + " ORDER BY timestamp DESC LIMIT ?",
                    (pattern, pattern, session_id, limit) if session_id else (pattern, pattern, limit),
                )
                entries = [
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "metadata": row["metadata"] or "{}",
                        "timestamp": row["timestamp"],
                        "similarity": 0.0,
                    }
                    for row in cursor.fetchall()
                ]

            return {"success": True, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_recent(self, session_id: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        """获取最近的摘要"""
        conn = self._get_conn()
        try:
            if session_id:
                cursor = conn.execute(
                    "SELECT id, session_id, content, summary, metadata, timestamp FROM summaries WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (session_id, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, session_id, content, summary, metadata, timestamp FROM summaries ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            entries = [
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "content": row["content"],
                    "summary": row["summary"],
                    "metadata": row["metadata"] or "{}",
                    "timestamp": row["timestamp"],
                }
                for row in cursor.fetchall()
            ]
            return {"success": True, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def clear_session(self, session_id: str) -> Dict[str, Any]:
        """清空某个会话的冷记忆"""
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM summaries WHERE session_id = ?", (session_id,))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stats(self) -> Dict[str, Any]:
        """获取冷记忆统计"""
        conn = self._get_conn()
        try:
            cursor = conn.execute("SELECT COUNT(*) as total FROM summaries")
            total = cursor.fetchone()["total"]
            cursor = conn.execute("SELECT COUNT(DISTINCT session_id) as sessions FROM summaries")
            sessions = cursor.fetchone()["sessions"]
            return {"total_summaries": total, "total_sessions": sessions, "has_embedding": self._has_embedding}
        except Exception as e:
            return {"error": str(e)}

    def close(self) -> None:
        """关闭连接池"""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
