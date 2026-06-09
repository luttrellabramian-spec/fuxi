from __future__ import annotations

"""记忆工具 - 提供 memory_write / memory_query / memory_get_recent 工具"""
from typing import Dict, Any

from . import registry

# Lazy initialization to avoid sys.path issues
_hot = None
_warm = None
_cold = None

def _get_memories():
    global _hot, _warm, _cold
    if _hot is None:
        from memory.hot_memory import HotMemory
        from memory.warm_memory import WarmMemory
        from memory.cold_memory import ColdMemory
        _hot = HotMemory()
        _warm = WarmMemory()
        _cold = ColdMemory()

@registry.register(name="memory_write", level="L1")
def memory_write(**kwargs) -> Dict[str, Any]:
    """
    写入记忆到指定类型（hot/warm/cold）。
    参数: memory_type, content, session_id
    """
    try:
        memory_type = kwargs.get("memory_type", "hot")
        content = kwargs.get("content", "")
        session_id = kwargs.get("session_id", "default")

        if memory_type == "hot":
            _get_memories()
            result = _hot.write(content)
        elif memory_type == "warm":
            _get_memories()
            result = _warm.add_message(session_id=session_id, content=content)
        elif memory_type == "cold":
            _get_memories()
            result = _cold.insert_summary(content=content, summary=content)
        else:
            return {"success": False, "error": f"Unknown memory_type: {memory_type}"}

        result["elapsed_ms"] = 10
        return result
    except Exception as e:
        return {"success": False, "error": str(e), "elapsed_ms": 0}


@registry.register(name="memory_query", level="L1")
def memory_query(**kwargs) -> Dict[str, Any]:
    """
    查询记忆。
    参数: memory_type, session_id, query, limit
    """
    try:
        memory_type = kwargs.get("memory_type", "warm")
        session_id = kwargs.get("session_id", "default")

        if memory_type == "hot":
            _get_memories()
            result = _hot.read()
            return {"success": True, "memory_type": "hot", **result}

        elif memory_type == "warm":
            _get_memories()
            query = kwargs.get("query", "")
            limit = kwargs.get("limit", 50)
            if query:
                result = _warm.search(session_id=session_id, query=query, limit=limit)
            else:
                result = _warm.get_recent(session_id=session_id, limit=limit)
            return {"success": True, "memory_type": "warm", **result}

        elif memory_type == "cold":
            _get_memories()
            query = kwargs.get("query", "")
            limit = kwargs.get("limit", 10)
            if query:
                result = _cold.search_similar(query=query, limit=limit, session_id=session_id)
            else:
                result = _cold.get_recent(session_id=session_id, limit=limit)
            return {"success": True, "memory_type": "cold", **result}

        else:
            return {"success": False, "error": f"Unknown memory_type: {memory_type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="memory_get_recent", level="L1")
def memory_get_recent(**kwargs) -> Dict[str, Any]:
    """
    获取最近的记忆条目。
    参数: memory_type, session_id, limit
    """
    try:
        memory_type = kwargs.get("memory_type", "warm")
        session_id = kwargs.get("session_id", "default")
        limit = kwargs.get("limit", 10)

        if memory_type == "hot":
            _get_memories()
            result = _hot.read()
            return {"success": True, "memory_type": "hot", **result}

        elif memory_type == "warm":
            _get_memories()
            result = _warm.get_recent(session_id=session_id, limit=limit)
            return {"success": True, "memory_type": "warm", **result}

        elif memory_type == "cold":
            _get_memories()
            result = _cold.get_recent(session_id=session_id, limit=limit)
            return {"success": True, "memory_type": "cold", **result}

        else:
            return {"success": False, "error": f"Unknown memory_type: {memory_type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
