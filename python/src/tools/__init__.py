from __future__ import annotations

"""工具注册与执行模块（v0.2.0 增加调用追踪集成）"""
import json
import inspect
import time
import threading
from typing import Dict, Any, Callable, Optional

# 全局注册表实例 - 必须先创建，避免循环导入
class _ToolRegistry:
    _instance = None
    _lock = threading.Lock()
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tools = {}
                    cls._instance._on_invoke_callbacks = []
        return cls._instance

    def register(self, name=None, level="L1"):
        def decorator(func):
            tool_name = name or func.__name__
            self._instance._tools[tool_name] = {"func": func, "level": level, "signature": str(inspect.signature(func)), "doc": inspect.getdoc(func) or "", "module": func.__module__}
            return func
        return decorator

    def get_tool(self, name):
        e = self._tools.get(name)
        return e["func"] if e else None

    def invoke(self, tool_name, arguments_json):
        start = int(time.time()*1000)
        t = self.get_tool(tool_name)
        if not t:
            return {"success":False, "result_json":"{}", "error":f"Tool '{tool_name}' not found", "elapsed_ms":int(time.time()*1000)-start}
        try:
            args = arguments_json if isinstance(arguments_json, dict) else (json.loads(arguments_json) if arguments_json else {})
            r = t(**args)
            result = {"success":True, "result_json":json.dumps(r,ensure_ascii=False), "error":"", "elapsed_ms":int(time.time()*1000)-start}
            self._fire_callbacks(tool_name, True, result)
            return result
        except Exception as e:
            result = {"success":False, "result_json":"{}", "error":f"{type(e).__name__}: {str(e)}", "elapsed_ms":int(time.time()*1000)-start}
            self._fire_callbacks(tool_name, False, result)
            return result

    def list_tools(self):
        return {n:{"level":i["level"],"signature":i["signature"],"doc":i["doc"],"module":i["module"]} for n,i in self._tools.items()}

    def on_invoke(self, callback: Callable[[str, bool, Dict], None]) -> None:
        """注册工具调用回调（用于追踪器等外部模块）"""
        self._on_invoke_callbacks.append(callback)

    def _fire_callbacks(self, tool_name: str, success: bool, result: Dict) -> None:
        """触发所有注册的回调"""
        for cb in self._on_invoke_callbacks:
            try:
                cb(tool_name, success, result)
            except Exception:
                pass  # 回调失败不影响主流程

registry = _ToolRegistry()

# 然后再导入工具模块（它们会使用上面已创建的 registry）
from . import file_tools
from . import search_tools
from . import web_tools
from . import memory_tools  # noqa: E402

__all__ = ["registry"]
