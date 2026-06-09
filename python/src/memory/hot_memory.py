from __future__ import annotations

"""热记忆管理 - LRU 缓存 + 可选 MEMORY.md 持久化

设计目标（P0-1）：
- 基于 OrderedDict 的 LRU 淘汰策略
- 最大 100 条记录，5000 char/条上限
- 72 小时最大存活时间
- 淘汰条目可下刷至温层
- 线程安全（RLock）
"""
import os
import time
import threading
from collections import OrderedDict
from typing import Dict, Any, Optional, Callable, Tuple


class HotMemory:
    """热记忆管理器

    架构：
    - 内存中维护 OrderedDict LRU 缓存（主存储）
    - MEMORY.md 文件（可选）作为持久化备份
    - RLock 保证线程安全

    使用方式（与 v0.1.0 API 兼容）：
        hm = HotMemory()
        hm.append("用户问了一个技术问题")
        content = hm.read()                     # 返回聚合文本
        content, count = hm.read_with_stats()   # 返回文本 + 字符数
        hm.write("覆盖写入新内容")
        status = hm.get_status()                # 返回缓存统计
    """

    def __init__(
        self,
        memory_file: Optional[str] = None,
        max_size: int = 100,
        max_age_seconds: int = 259200,  # 72 小时
        max_entry_chars: int = 5000,
    ):
        self.memory_file: Optional[str] = None
        if memory_file is not None:
            self.memory_file = os.path.abspath(memory_file)

        # LRU 缓存：OrderedDict(key → (value, timestamp))
        self._cache: OrderedDict[str, Tuple[str, float]] = OrderedDict()
        self._lock = threading.RLock()
        self._max_size = max_size
        self._max_age = max_age_seconds
        self._max_entry_chars = max_entry_chars

        # 统计计数器
        self._hit_count = 0
        self._miss_count = 0
        self._eviction_count = 0

        # 回调（初始化为 None，避免访问未定义属性）
        self._warm_flush_callback: Optional[Callable[[str, str], None]] = None

        # 从 MEMORY.md 加载旧数据（向后兼容迁移）
        self._load_from_legacy_file()

    # ── 公共 API（与 v0.1.0 完全兼容） ──────────────────────────

    def read(self, key: Optional[str] = None) -> Dict[str, Any]:
        """读取热记忆内容

        Args:
            key: 可选，指定条目键名。为 None 时返回聚合内容。

        Returns:
            兼容格式: {"memory_content": str, "char_count": int}
        """
        with self._lock:
            if key is not None:
                return self._read_entry(key)
            return self._read_aggregated()

    def write(self, content: str) -> Dict[str, Any]:
        """写入热记忆（覆盖全部内容）

        Args:
            content: 要写入的内容文本

        Returns:
            {"success": bool, "id": str, "char_count": int}
        """
        with self._lock:
            self._cache.clear()
            # 将整段内容作为单条记录存入
            entry_key = f"entry_{int(time.time() * 1000)}"
            truncated = content[:self._max_entry_chars]
            self._cache[entry_key] = (truncated, time.time())
            self._cache.move_to_end(entry_key)
            self._persist_to_file(truncated)
            return {
                "success": True,
                "id": entry_key,
                "char_count": len(truncated),
            }

    def append(self, content: str) -> Dict[str, Any]:
        """追加一条热记忆记录

        自动生成唯一键名，超出容量时按 LRU 淘汰。

        Args:
            content: 要追加的内容

        Returns:
            {"success": bool, "id": str, "char_count": int}
        """
        with self._lock:
            # 截断超长内容
            truncated = content[:self._max_entry_chars]
            entry_key = f"entry_{int(time.time() * 1000)}_{id(content)}"

            # 容量检查：超出时淘汰最久未使用的（防止 max_size <= 0 时的无限循环）
            while self._max_size > 0 and len(self._cache) >= self._max_size:
                if not self._evict_one():
                    break  # 缓存为空，无法淘汰

            if self._max_size <= 0:
                return {"success": True, "id": "", "char_count": 0}

            self._cache[entry_key] = (truncated, time.time())
            self._cache.move_to_end(entry_key)
            self._persist_to_file(self._serialize_aggregated())
            return {
                "success": True,
                "id": entry_key,
                "char_count": len(truncated),
            }

    def clear(self) -> Dict[str, Any]:
        """清空热记忆

        Returns:
            {"success": bool, "id": str, "char_count": 0}
        """
        with self._lock:
            self._cache.clear()
            self._persist_to_file("")
            return {"success": True, "id": "hot", "char_count": 0}

    def get_status(self) -> Dict[str, Any]:
        """获取热记忆状态

        Returns:
            包含缓存容量、命中率等统计的字典
        """
        with self._lock:
            total_calls = self._hit_count + self._miss_count
            hit_rate = self._hit_count / total_calls if total_calls > 0 else 0.0
            aggregated = self._read_aggregated()
            return {
                "max_size": self._max_size,
                "current_size": len(self._cache),
                "max_age_hours": round(self._max_age / 3600, 1),
                "char_count": aggregated["char_count"],
                "hit_rate": round(hit_rate, 3),
                "eviction_count": self._eviction_count,
                "memory_file": self.memory_file or "(无)",
            }

    # ── 新 API（v0.2.0 新增） ──────────────────────────────────

    def get_entry(self, key: str) -> Optional[str]:
        """获取指定条目（LRU 自动刷新）

        Args:
            key: 条目键名

        Returns:
            条目内容，不存在时返回 None
        """
        with self._lock:
            if key not in self._cache:
                self._miss_count += 1
                return None
            self._hit_count += 1
            value, timestamp = self._cache[key]
            self._cache.move_to_end(key)
            return value

    def set_entry(self, key: str, value: str) -> bool:
        """写入或更新指定条目（LRU 自动管理）

        Args:
            key: 条目键名
            value: 条目内容

        Returns:
            是否成功
        """
        with self._lock:
            truncated = value[:self._max_entry_chars]
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                while self._max_size > 0 and len(self._cache) >= self._max_size:
                    if not self._evict_one():
                        break
            self._cache[key] = (truncated, time.time())
            self._cache.move_to_end(key)
            self._persist_to_file(self._serialize_aggregated())
            return True

    def evict_expired(self) -> int:
        """清理超过 max_age 的条目（同时触发温层下刷回调）

        Returns:
            清理的条目数
        """
        with self._lock:
            now = time.time()
            expired_keys = [
                k for k, (_, ts) in self._cache.items()
                if now - ts > self._max_age
            ]
            for k in expired_keys:
                value, _ = self._cache.pop(k)
                # 触发温层下刷回调
                if self._warm_flush_callback:
                    try:
                        self._warm_flush_callback(k, value)
                    except Exception:
                        pass
            if expired_keys:
                self._persist_to_file(self._serialize_aggregated())
            return len(expired_keys)

    def set_warm_flush_callback(self, callback: Callable[[str, str], None]) -> None:
        """注册淘汰条目下刷温层的回调

        Args:
            callback: (key, value) → None，淘汰条目时自动调用
        """
        self._warm_flush_callback = callback

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息（详细版）

        Returns:
            包含各维度的统计字典
        """
        return self.get_status()

    # ── 私有方法 ──────────────────────────────────────────────

    def _read_entry(self, key: str) -> Dict[str, Any]:
        """读取单个条目（返回兼容格式）"""
        value = self.get_entry(key)
        if value is None:
            return {"memory_content": "", "char_count": 0}
        return {"memory_content": value, "char_count": len(value)}

    def _read_aggregated(self) -> Dict[str, Any]:
        """聚合所有条目内容为文本"""
        all_content = []
        total_chars = 0
        for key in self._cache:
            value, _ = self._cache[key]
            all_content.append(value)
            total_chars += len(value)
        aggregated = "\n".join(all_content)
        return {"memory_content": aggregated, "char_count": total_chars}

    def _evict_one(self) -> Optional[Tuple[str, str]]:
        """淘汰最久未使用的条目

        Returns:
            (key, value) 被淘汰的条目，若无则返回 None
        """
        if not self._cache:
            return None
        key, (value, _) = self._cache.popitem(last=False)
        self._eviction_count += 1

        # 下刷至温层
        if hasattr(self, '_warm_flush_callback') and self._warm_flush_callback:
            try:
                self._warm_flush_callback(key, value)
            except Exception:
                pass  # 下刷失败不影响热记忆
        return (key, value)

    def _serialize_aggregated(self) -> str:
        """将所有缓存条目序列化为聚合文本"""
        parts = []
        for key in self._cache:
            value, timestamp = self._cache[key]
            parts.append(value)
        return "\n".join(parts)

    def _persist_to_file(self, content: str) -> None:
        """将内容持久化到 MEMORY.md（原子写入）"""
        if not self.memory_file:
            return
        try:
            import tempfile
            dir_name = os.path.dirname(self.memory_file)
            os.makedirs(dir_name, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=dir_name, delete=False
            ) as tmp_file:
                tmp_path = tmp_file.name
                tmp_file.write(content)
            if os.path.exists(self.memory_file):
                os.replace(tmp_path, self.memory_file)
            else:
                os.rename(tmp_path, self.memory_file)
        except Exception:
            pass  # 持久化失败不影响主流程

    def _load_from_legacy_file(self) -> None:
        """从旧版 MEMORY.md 加载数据（迁移兼容）

        仅在缓存为空且文件存在时执行，确保 v0.1.0 → v0.2.0 无缝迁移。
        """
        if self._cache or not self.memory_file or not os.path.exists(self.memory_file):
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                # 按行分割，恢复为多条记录
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped:
                        key = f"legacy_{i}"
                        self._cache[key] = (stripped[:self._max_entry_chars], time.time())
                # 确保不超过容量上限
                while len(self._cache) > self._max_size:
                    self._cache.popitem(last=False)
        except Exception:
            pass
