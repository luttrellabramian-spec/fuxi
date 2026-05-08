"""工具注册与执行模块"""
import json
import inspect
import time
import asyncio
from typing import Dict, Any, Callable, Optional
from threading import Lock

# 全局注册表实例 - 必须先创建，避免循环导入
class _ToolRegistry:
    _instance = None
    _lock = Lock()
    _init_lock = Lock()
    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tools = {}
                    cls._instance._tools_lock = Lock()
        return cls._instance
    def register(self, name=None, level="L1"):
        def decorator(func):
            tool_name = name or func.__name__
            self._tools[tool_name] = {"func": func, "level": level, "signature": str(inspect.signature(func)), "doc": inspect.getdoc(func) or "", "module": func.__module__}
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
            args = json.loads(arguments_json) if isinstance(arguments_json, str) else (arguments_json or {})
            r = t(**args)
            return {"success":True, "result_json":json.dumps(r,ensure_ascii=False), "error":"", "elapsed_ms":int(time.time()*1000)-start}
        except Exception as e:
            return {"success":False, "result_json":"{}", "error":f"{type(e).__name__}: {str(e)}", "elapsed_ms":int(time.time()*1000)-start}
    def list_tools(self):
        return {n:{"level":i["level"],"signature":i["signature"],"doc":i["doc"],"module":i["module"]} for n,i in self._tools.items()}

registry = _ToolRegistry()

# 然后再导入工具模块（它们会使用上面已创建的 registry）
from . import file_tools
from . import search_tools
from . import web_tools
from . import memory_tools  # noqa: E402

__all__ = ["registry"]
