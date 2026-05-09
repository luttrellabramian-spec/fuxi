"""热记忆管理 - MEMORY.md 读写"""
import os
import time
import tempfile
import threading
import uuid
from typing import Dict, Any, Optional


class HotMemory:
    """热记忆管理器 - 基于 MEMORY.md 文件

    线程安全，支持多线程并发读写。使用 threading.RLock 做内存锁，
    文件操作前先获取锁，保证一致性。
    """

    def __init__(self, memory_file: Optional[str] = None):
        # 默认使用项目根目录的 MEMORY.md（确保路径一致）
        if memory_file is None:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            memory_file = os.path.join(current_dir, "MEMORY.md")

        self.memory_file = os.path.abspath(memory_file)
        self.max_chars = 2200  # 硬限制
        self._lock = threading.RLock()  # 可重入锁，支持同一线程多次获取

    def read(self) -> Dict[str, Any]:
        """读取热记忆内容"""
        with self._lock:
            if not os.path.exists(self.memory_file):
                return {"memory_content": "", "char_count": 0}
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    content = f.read()
                return {"memory_content": content, "char_count": len(content)}
            except Exception as e:
                return {"memory_content": "", "char_count": 0, "error": str(e)}

    def write(self, content: str) -> Dict[str, Any]:
        """写入热记忆内容 - 带字符限制（原子写入）"""
        with self._lock:
            try:
                # 强制截断到 2200 字符
                truncated_content = content[:self.max_chars]
                
                # 原子写入：先写临时文件，再重命名
                dir_name = os.path.dirname(self.memory_file)
                with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', dir=dir_name, delete=False
                ) as tmp_file:
                    tmp_path = tmp_file.name
                    tmp_file.write(truncated_content)
                
                # Windows 上需要先删除目标文件
                if os.path.exists(self.memory_file):
                    os.replace(tmp_path, self.memory_file)
                else:
                    os.rename(tmp_path, self.memory_file)
                
                return {
                    "success": True,
                    "id": "hot",
                    "char_count": len(truncated_content),
                }
            except Exception as e:
                # 清理临时文件
                try:
                    if 'tmp_path' in locals() and os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except Exception:
                    pass
                return {"success": False, "error": str(e), "char_count": 0}

    def append(self, content: str) -> Dict[str, Any]:
        """追加内容到热记忆（原子操作）"""
        with self._lock:
            # 在同一个锁内完成读取和写入
            if not os.path.exists(self.memory_file):
                current_content = ""
            else:
                try:
                    with open(self.memory_file, "r", encoding="utf-8") as f:
                        current_content = f.read()
                except Exception:
                    current_content = ""
            
            new_content = current_content + "\n" + content
            
            # 原子写入
            try:
                dir_name = os.path.dirname(self.memory_file)
                truncated_content = new_content[:self.max_chars]
                
                with tempfile.NamedTemporaryFile(
                    mode='w', encoding='utf-8', dir=dir_name, delete=False
                ) as tmp_file:
                    tmp_path = tmp_file.name
                    tmp_file.write(truncated_content)
                
                if os.path.exists(self.memory_file):
                    os.replace(tmp_path, self.memory_file)
                else:
                    os.rename(tmp_path, self.memory_file)
                
                return {
                    "success": True,
                    "id": "hot",
                    "char_count": len(truncated_content),
                }
            except Exception as e:
                try:
                    if 'tmp_path' in locals() and os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except Exception:
                    pass
                return {"success": False, "error": str(e), "char_count": 0}

    def clear(self) -> Dict[str, Any]:
        """清空热记忆"""
        return self.write("")

    def get_status(self) -> Dict[str, Any]:
        """获取热记忆状态"""
        with self._lock:
            if not os.path.exists(self.memory_file):
                return {"exists": False, "size": 0, "max_chars": self.max_chars}
            stat = os.stat(self.memory_file)
            return {
                "exists": True,
                "size": stat.st_size,
                "max_chars": self.max_chars,
                "path": self.memory_file,
                "mtime": stat.st_mtime,
            }