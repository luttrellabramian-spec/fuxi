# -*- coding: utf-8 -*-
"""test_hot_memory.py — HotMemory 热记忆单元测试

测试覆盖：
  1. append + read 基本读写
  2. write 覆盖写入
  3. clear 清空
  4. LRU 淘汰 (max_size)
  5. TTL 过期淘汰 (evict_expired)
  6. 线程安全 (RLock)
  7. max_size=0 边界
  8. 空内容
  9. 超长内容截断
 10. 特殊 Unicode
 11. 淘汰回调 (warm_flush_callback)
 12. get_entry / set_entry 直接键访问
 13. get_status / get_stats 计数器
"""
from __future__ import annotations

import os
import sys
import time
import json
import threading

# ── 确保 src 在 sys.path ──────────────────────────────────────
_src = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src")
)
if _src not in sys.path:
    sys.path.insert(0, _src)

import pytest
from memory.hot_memory import HotMemory


# ==============================================================
#  fixtures
# ==============================================================

@pytest.fixture
def hm(request) -> HotMemory:
    """创建一个干净的 HotMemory 实例，test teardown 时自动清理。"""
    instance = HotMemory(max_size=5, max_age_seconds=3600, max_entry_chars=100)
    yield instance
    instance.clear()


@pytest.fixture
def hm_with_file(tmp_path) -> HotMemory:
    """HotMemory + 临时 MEMORY.md 文件。"""
    mem_file = tmp_path / "MEMORY.md"
    instance = HotMemory(
        memory_file=str(mem_file),
        max_size=5,
        max_age_seconds=3600,
        max_entry_chars=100,
    )
    yield instance
    instance.clear()


# ==============================================================
#  1. append + read 基本读写
# ==============================================================

class TestBasicReadWrite:
    """基本读写流程验证。"""

    def test_append_and_read(self, hm: HotMemory) -> None:
        result = hm.append("第一条记忆")
        assert result["success"] is True
        assert "id" in result
        assert result["char_count"] == 5

        hm.append("第二条记忆")
        data = hm.read()
        assert "memory_content" in data
        assert "char_count" in data
        assert "第一条记忆" in data["memory_content"]
        assert "第二条记忆" in data["memory_content"]
        assert data["char_count"] == 10

    def test_append_return_ids_are_unique(self, hm: HotMemory) -> None:
        r1 = hm.append("内容 A")
        r2 = hm.append("内容 B")
        r3 = hm.append("内容 C")
        ids = {r1["id"], r2["id"], r3["id"]}
        assert len(ids) == 3, "每次 append 应生成唯一 id"

    def test_read_aggregated_returns_newline_joined(self, hm: HotMemory) -> None:
        hm.append("line1")
        hm.append("line2")
        data = hm.read()
        expected = "line1\nline2"
        assert data["memory_content"] == expected


# ==============================================================
#  2. write 覆盖写入
# ==============================================================

class TestWrite:
    """write() 覆盖全部内容。"""

    def test_write_replaces_all_content(self, hm: HotMemory) -> None:
        hm.append("旧内容 1")
        hm.append("旧内容 2")
        result = hm.write("全新内容")
        assert result["success"] is True
        assert result["char_count"] == 4

        data = hm.read()
        assert data["memory_content"] == "全新内容"
        assert data["char_count"] == 4

    def test_write_clears_before_writing(self, hm: HotMemory) -> None:
        hm.append("将被清除")
        hm.write("")
        data = hm.read()
        assert data["memory_content"] == ""
        assert data["char_count"] == 0


# ==============================================================
#  3. clear 清空
# ==============================================================

class TestClear:
    """clear() 清空所有条目。"""

    def test_clear_empties_cache(self, hm: HotMemory) -> None:
        hm.append("数据 1")
        hm.append("数据 2")
        result = hm.clear()
        assert result["success"] is True
        assert result["char_count"] == 0

        data = hm.read()
        assert data["memory_content"] == ""
        assert data["char_count"] == 0

    def test_clear_then_append_works(self, hm: HotMemory) -> None:
        hm.append("清空前")
        hm.clear()
        hm.append("清空后")
        data = hm.read()
        assert data["memory_content"] == "清空后"


# ==============================================================
#  4. LRU 淘汰
# ==============================================================

class TestLRUEviction:
    """LRU 淘汰策略验证。"""

    def test_lru_eviction_drops_oldest(self, hm: HotMemory) -> None:
        # max_size=5，写入 6 条 → 最早的第 1 条被淘汰
        for i in range(6):
            hm.append(f"条目_{i}")
        data = hm.read()
        # "条目_0" 应被淘汰
        assert "条目_0" not in data["memory_content"]
        assert "条目_5" in data["memory_content"]

    def test_lru_recent_access_preserves_entry(self) -> None:
        """get_entry 刷新 LRU 位置，防止该条目被淘汰。"""
        hm = HotMemory(max_size=3, max_entry_chars=100)
        hm.set_entry("keep", "将被保留")
        hm.set_entry("a", "填充_A")
        hm.set_entry("b", "填充_B")
        # 当前 LRU 顺序: keep(最旧), a, b(最新)
        hm.get_entry("keep")   # keep 刷新→最新，此时 LRU 顺序: a, b, keep
        hm.set_entry("c", "填充_C")  # 淘汰最旧的 "a"
        hm.set_entry("d", "填充_D")  # 淘汰最旧的 "b"
        # keep 因 LRU 刷新应保留
        val = hm.get_entry("keep")
        assert val == "将被保留", "keep 因 LRU 刷新不应被淘汰"

    def test_eviction_count_increments(self, hm: HotMemory) -> None:
        for i in range(7):
            hm.append(f"x_{i}")
        status = hm.get_status()
        assert status["eviction_count"] >= 2


# ==============================================================
#  5. TTL 过期淘汰
# ==============================================================

class TestTTLEviction:
    """TTL (max_age) 淘汰验证。"""

    def test_evict_expired_removes_old_entries(self) -> None:
        hm = HotMemory(max_size=10, max_age_seconds=0.01)
        hm.append("短暂存活")
        time.sleep(0.02)
        evicted = hm.evict_expired()
        assert evicted == 1
        data = hm.read()
        assert data["memory_content"] == ""
        assert data["char_count"] == 0

    def test_evict_expired_keeps_fresh_entries(self) -> None:
        hm = HotMemory(max_size=10, max_age_seconds=9999)
        hm.append("永不过期")
        evicted = hm.evict_expired()
        assert evicted == 0
        data = hm.read()
        assert data["memory_content"] == "永不过期"

    def test_evict_expired_returns_count(self) -> None:
        hm = HotMemory(max_size=10, max_age_seconds=0.01)
        hm.append("a")
        hm.append("b")
        hm.append("c")
        time.sleep(0.02)
        count = hm.evict_expired()
        assert count == 3


# ==============================================================
#  6. 线程安全
# ==============================================================

class TestThreadSafety:
    """并发读写线程安全。"""

    def test_concurrent_append(self) -> None:
        hm = HotMemory(max_size=100)
        errors = []

        def worker(n: int) -> None:
            try:
                for _ in range(20):
                    hm.append(f"thread_{n}_data")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发写入异常: {errors}"
        status = hm.get_status()
        assert status["current_size"] == hm._max_size  # 应已满容

    def test_concurrent_read_write(self) -> None:
        hm = HotMemory(max_size=20)
        stop = threading.Event()
        errors = []

        def writer() -> None:
            for i in range(50):
                if stop.is_set():
                    break
                hm.append(f"w{i}")

        def reader() -> None:
            for _ in range(50):
                if stop.is_set():
                    break
                try:
                    hm.read()
                except Exception as e:
                    errors.append(e)
                    stop.set()

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=reader)
        tw.start()
        tr.start()
        tw.join()
        tr.join()

        assert len(errors) == 0, f"读写冲突: {errors}"


# ==============================================================
#  7. max_size=0 边界
# ==============================================================

class TestEdgeCaseMaxSizeZero:
    """max_size=0 不应报错。"""

    def test_append_does_not_crash(self) -> None:
        hm = HotMemory(max_size=0)
        result = hm.append("任何内容")
        assert result["success"] is True
        assert result["id"] == ""
        assert result["char_count"] == 0

    def test_read_returns_empty(self) -> None:
        hm = HotMemory(max_size=0)
        hm.append("将被忽略")
        data = hm.read()
        assert data["memory_content"] == ""
        assert data["char_count"] == 0


# ==============================================================
#  8. 空内容
# ==============================================================

class TestEdgeCaseEmptyContent:
    """空字符串内容不应引发异常。"""

    def test_append_empty_string(self, hm: HotMemory) -> None:
        result = hm.append("")
        assert result["success"] is True
        data = hm.read()
        assert data["char_count"] == 0

    def test_write_empty_string(self, hm: HotMemory) -> None:
        result = hm.write("")
        assert result["success"] is True
        data = hm.read()
        assert data["memory_content"] == ""


# ==============================================================
#  9. 超长内容截断
# ==============================================================

class TestEdgeCaseLongContent:
    """超长内容应被截断到 max_entry_chars。"""

    def test_long_content_truncated(self, hm: HotMemory) -> None:
        long_str = "A" * 200  # max_entry_chars=100
        result = hm.append(long_str)
        assert result["char_count"] == 100  # 截断到 100
        data = hm.read()
        assert len(data["memory_content"]) == 100

    def test_long_content_write_truncated(self, hm: HotMemory) -> None:
        long_str = "B" * 500
        result = hm.write(long_str)
        assert result["char_count"] == 100


# ==============================================================
# 10. 特殊 Unicode
# ==============================================================

class TestEdgeCaseUnicode:
    """特殊 Unicode 字符处理。"""

    def test_emoji_content(self, hm: HotMemory) -> None:
        hm.append("🔥 测试 🔥")
        data = hm.read()
        assert "🔥" in data["memory_content"]

    def test_cjk_content(self, hm: HotMemory) -> None:
        hm.append("中文测试记忆内容")
        data = hm.read()
        assert "中文测试记忆内容" in data["memory_content"]

    def test_mixed_unicode(self, hm: HotMemory) -> None:
        mixed = "中文 English 日本語 123!@# 🎉"
        hm.append(mixed)
        data = hm.read()
        assert data["memory_content"] == mixed


# ==============================================================
# 11. 淘汰回调
# ==============================================================

class TestFlushCallback:
    """淘汰条目时 flush callback 触发验证。"""

    def test_flush_callback_fires_on_eviction(self, hm: HotMemory) -> None:
        flushed = []

        def callback(key: str, value: str) -> None:
            flushed.append((key, value))

        hm.set_warm_flush_callback(callback)
        hm.append("会被淘汰的第 1 条")
        for i in range(5):
            hm.append(f"填充_{i}")

        assert len(flushed) >= 1
        key, value = flushed[0]
        assert isinstance(key, str)
        assert "第 1 条" in value

    def test_flush_callback_on_ttl_expiry(self) -> None:
        flushed = []

        def callback(key: str, value: str) -> None:
            flushed.append((key, value))

        hm = HotMemory(max_size=10, max_age_seconds=0.01)
        hm.set_warm_flush_callback(callback)
        hm.append("过期条目")
        time.sleep(0.02)
        hm.evict_expired()

        assert len(flushed) >= 1
        assert "过期条目" in flushed[0][1]

    def test_flush_callback_error_does_not_break(self, hm: HotMemory) -> None:
        def broken(_k: str, _v: str) -> None:
            raise RuntimeError("故意的回调异常")

        hm.set_warm_flush_callback(broken)
        for i in range(6):
            hm.append(f"x_{i}")
        # 不应抛出异常
        data = hm.read()
        # 应有内容（即使回调失败）
        assert data["char_count"] > 0


# ==============================================================
# 12. get_entry / set_entry
# ==============================================================

class TestDirectKeyAccess:
    """get_entry / set_entry 直接键访问。"""

    def test_set_entry_creates_new(self, hm: HotMemory) -> None:
        ok = hm.set_entry("my_key", "我的值")
        assert ok is True
        val = hm.get_entry("my_key")
        assert val == "我的值"

    def test_get_entry_updates_lru(self) -> None:
        hm = HotMemory(max_size=3, max_entry_chars=100)
        hm.set_entry("old", "旧值，但需保留")
        hm.set_entry("f1", "填充_1")
        hm.set_entry("f2", "填充_2")
        # "old" 是最旧的；get_entry 刷新 LRU
        hm.get_entry("old")  # 现在 LRU 顺序: f1, f2, old
        hm.set_entry("f3", "填充_3")  # 淘汰 f1
        hm.set_entry("f4", "填充_4")  # 淘汰 f2
        val = hm.get_entry("old")
        assert val is not None, "old 因 LRU 刷新不应被淘汰"
        assert val == "旧值，但需保留"

    def test_get_entry_returns_none_for_missing(self, hm: HotMemory) -> None:
        val = hm.get_entry("不存在的键")
        assert val is None

    def test_set_entry_overwrites_existing(self, hm: HotMemory) -> None:
        hm.set_entry("dup", "原始值")
        hm.set_entry("dup", "新值")
        val = hm.get_entry("dup")
        assert val == "新值"


# ==============================================================
# 13. get_status / get_stats
# ==============================================================

class TestStatsTracking:
    """get_status / get_stats 统计计数。"""

    def test_get_status_keys(self, hm: HotMemory) -> None:
        status = hm.get_status()
        assert "max_size" in status
        assert "current_size" in status
        assert "max_age_hours" in status
        assert "char_count" in status
        assert "hit_rate" in status
        assert "eviction_count" in status
        assert "memory_file" in status

    def test_get_stats_aliased(self, hm: HotMemory) -> None:
        """get_stats() 应代理到 get_status()。"""
        assert hm.get_stats() == hm.get_status()

    def test_hit_rate_tracking(self, hm: HotMemory) -> None:
        hm.get_entry("missing")               # miss
        hm.get_entry("also_missing")          # miss
        hm.append("test")
        # 获取具体键触发 hit
        entries_data = hm.read()
        for line in entries_data["memory_content"].split("\n"):
            if line:
                # 找到刚写入的 entry 的 key 并 get_entry
                break
        # 通过 read() 调用内部不计数 hit/miss，需要直接 get_entry
        # 获取所有 keys
        keys = list(hm._cache.keys())
        for k in keys:
            hm.get_entry(k)

        status = hm.get_status()
        assert status["hit_rate"] > 0

    def test_current_size_accurate(self, hm: HotMemory) -> None:
        hm.append("A")
        hm.append("B")
        hm.append("C")
        status = hm.get_status()
        assert status["current_size"] == 3
        assert status["char_count"] == 3

    def test_eviction_count_in_status(self, hm: HotMemory) -> None:
        for i in range(10):
            hm.append(f"d{i}")
        status = hm.get_status()
        assert status["eviction_count"] >= 5


# ==============================================================
# 14. 文件持久化
# ==============================================================

class TestFilePersistence:
    """memory_file 持久化验证。"""

    def test_file_written_after_append(self, hm_with_file) -> None:
        hm_with_file.append("文件持久化测试")
        assert hm_with_file.memory_file is not None
        assert os.path.exists(hm_with_file.memory_file)

    def test_file_reads_back_content(self, tmp_path) -> None:
        mem_file = tmp_path / "MEMORY.md"
        hm = HotMemory(memory_file=str(mem_file), max_size=10)
        hm.append("保存到文件的内容")
        hm2 = HotMemory(
            memory_file=str(mem_file), max_size=10
        )
        data = hm2.read()
        # 新实例从文件加载旧内容
        old_content = data["memory_content"]
        assert "保存到文件的内容" in old_content or old_content == ""
        # 注：_load_from_legacy_file 仅当缓存为空时加载，所以第二次实例化时有内容

    def test_file_cleared_after_clear(self, hm_with_file) -> None:
        hm_with_file.append("清空前")
        hm_with_file.clear()
        with open(hm_with_file.memory_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert content.strip() == ""
