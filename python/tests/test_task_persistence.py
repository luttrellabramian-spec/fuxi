"""伏羲 任务持久化测试 — TaskPersistence save/restore/clear/disabled 路径"""
import sys
import os
import json
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from engine.task_persistence import TaskPersistence


# ════════════════════════════════════════════════════════════════════
# 1. 禁用模式（db_path 为空）
# ════════════════════════════════════════════════════════════════════


class TestDisabled:
    def test_disabled_when_no_db_path(self):
        """db_path 为空时 enabled 应为 False。"""
        p = TaskPersistence(db_path="")
        assert p.enabled is False

    def test_save_returns_false_when_disabled(self, tmp_path):
        """禁用时 save 应返回 False。"""
        p = TaskPersistence(db_path="")
        ok = p.save("s1", 1, [{"role": "user", "content": "x"}], [], [])
        assert ok is False

    def test_restore_returns_none_when_disabled(self):
        """禁用时 restore 应返回 None。"""
        p = TaskPersistence(db_path="")
        assert p.restore("s1") is None

    def test_clear_returns_false_when_disabled(self):
        """禁用时 clear 应返回 False。"""
        p = TaskPersistence(db_path="")
        assert p.clear("s1") is False

    def test_init_db_noop_when_disabled(self, tmp_path):
        """禁用时 _init_db 不应创建任何文件。"""
        p = TaskPersistence(db_path="")
        p._init_db()  # 不应抛错
        # 数据库文件不应存在
        assert not any(tmp_path.iterdir())


# ════════════════════════════════════════════════════════════════════
# 2. 启用模式 — save
# ════════════════════════════════════════════════════════════════════


class TestSave:
    def test_save_returns_true_and_persists(self, tmp_path):
        """save 应返回 True 且数据可被读取。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        ok = p.save(
            session_id="s1",
            step=2,
            messages=[{"role": "user", "content": "hi"}],
            observations=[{"tool": "x", "result": "y"}],
            tools_used=[{"tool": "x", "args": {}}],
        )
        assert ok is True
        # 验证数据落到 sqlite
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT step, messages, observations, tools_used FROM task_state WHERE session_id = ?",
            ("s1",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 2
        assert json.loads(row[1]) == [{"role": "user", "content": "hi"}]

    def test_save_preserves_chinese_unicode(self, tmp_path):
        """save 应保留中文。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save("s1", 1, [{"role": "user", "content": "伏羲你好"}], [], [])
        # 验证
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT messages FROM task_state WHERE session_id = 's1'").fetchone()
        conn.close()
        assert "伏羲你好" in row[0]

    def test_save_overwrites_existing_session(self, tmp_path):
        """同 session 二次 save 应覆盖（INSERT OR REPLACE）。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save("s1", 1, [{"role": "user", "content": "first"}], [], [])
        p.save("s1", 2, [{"role": "user", "content": "second"}], [], [])
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM task_state WHERE session_id = 's1'").fetchone()[0]
        step = conn.execute("SELECT step FROM task_state WHERE session_id = 's1'").fetchone()[0]
        conn.close()
        assert n == 1
        assert step == 2

    def test_save_preserves_created_at_on_update(self, tmp_path):
        """save 应保留原 created_at，只更新 updated_at。"""
        import time
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save("s1", 1, [], [], [])
        conn = sqlite3.connect(db)
        first = conn.execute(
            "SELECT created_at, updated_at FROM task_state WHERE session_id='s1'"
        ).fetchone()
        conn.close()
        time.sleep(0.05)
        p.save("s1", 2, [], [], [])
        conn = sqlite3.connect(db)
        second = conn.execute(
            "SELECT created_at, updated_at FROM task_state WHERE session_id='s1'"
        ).fetchone()
        conn.close()
        # created_at 保持不变
        assert first[0] == second[0]
        # updated_at 变了
        assert second[1] > first[1]

    def test_save_failure_returns_false(self, tmp_path, monkeypatch):
        """sqlite 抛异常时 save 应返回 False。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        # 强制 sqlite3.connect 抛错
        monkeypatch.setattr("sqlite3.connect", lambda *a, **kw: (_ for _ in ()).throw(OSError("db locked")))
        ok = p.save("s1", 1, [], [], [])
        assert ok is False


# ════════════════════════════════════════════════════════════════════
# 3. 启用模式 — restore
# ════════════════════════════════════════════════════════════════════


class TestRestore:
    def test_restore_returns_saved_data(self, tmp_path):
        """restore 应返回 save 时存的数据。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save(
            "s1", 5,
            [{"role": "user", "content": "msg1"}, {"role": "assistant", "content": "msg2"}],
            [{"obs": "a"}],
            [{"tool": "t1", "args": {}}],
        )
        result = p.restore("s1")
        assert result is not None
        assert result["step"] == 5
        assert len(result["messages"]) == 2
        assert result["messages"][1]["content"] == "msg2"
        assert result["observations"] == [{"obs": "a"}]
        assert result["tools_used"] == [{"tool": "t1", "args": {}}]

    def test_restore_missing_session_returns_none(self, tmp_path):
        """不存在的 session_id 应返回 None。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        assert p.restore("nonexistent") is None

    def test_restore_after_clear_returns_none(self, tmp_path):
        """clear 后 restore 应返回 None。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save("s1", 1, [], [], [])
        p.clear("s1")
        assert p.restore("s1") is None

    def test_restore_handles_corrupt_json(self, tmp_path):
        """损坏的 JSON 应被 catch 并返回 None。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        # 直接写入坏数据
        conn = sqlite3.connect(db)
        conn.execute("""
            INSERT INTO task_state
            (session_id, step, messages, observations, tools_used, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("bad", 1, "not json {{{", "[]", "[]", 0, 0))
        conn.commit()
        conn.close()
        assert p.restore("bad") is None


# ════════════════════════════════════════════════════════════════════
# 4. 启用模式 — clear
# ════════════════════════════════════════════════════════════════════


class TestClear:
    def test_clear_removes_session(self, tmp_path):
        """clear 应删除指定 session。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        p.save("s1", 1, [], [], [])
        p.save("s2", 1, [], [], [])
        assert p.clear("s1") is True
        assert p.restore("s1") is None
        # s2 应仍在
        assert p.restore("s2") is not None

    def test_clear_missing_session_returns_true(self, tmp_path):
        """删除不存在的 session 应返回 True（SQL DELETE 不抛错）。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        assert p.clear("nonexistent") is True

    def test_clear_only_affects_target_session(self, tmp_path):
        """clear 不应影响其他 session。"""
        db = str(tmp_path / "task.db")
        p = TaskPersistence(db_path=db)
        for sid in ("s1", "s2", "s3"):
            p.save(sid, 1, [{"role": "user", "content": sid}], [], [])
        p.clear("s2")
        # s1 / s3 仍在
        assert p.restore("s1")["messages"][0]["content"] == "s1"
        assert p.restore("s3")["messages"][0]["content"] == "s3"
        assert p.restore("s2") is None


# ════════════════════════════════════════════════════════════════════
# 5. 错误处理 — DB 不可用
# ════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    def test_init_db_failure_disables_persistence(self, tmp_path, monkeypatch):
        """_init_db 失败时应自动禁用 persistence。"""
        # 强制 connect 抛错
        monkeypatch.setattr("sqlite3.connect", lambda *a, **kw: (_ for _ in ()).throw(OSError("perm denied")))
        p = TaskPersistence(db_path=str(tmp_path / "x.db"))
        assert p.enabled is False

    def test_multiple_persistence_instances_independent(self, tmp_path):
        """多个实例可独立管理不同 DB。"""
        db1 = str(tmp_path / "a.db")
        db2 = str(tmp_path / "b.db")
        p1 = TaskPersistence(db_path=db1)
        p2 = TaskPersistence(db_path=db2)
        p1.save("shared", 1, [{"role": "user", "content": "from p1"}], [], [])
        # p1 看得到
        assert p1.restore("shared")["messages"][0]["content"] == "from p1"
        # p2 应看不到（不同 DB）
        assert p2.restore("shared") is None
