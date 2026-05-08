"""文件操作工具"""
import os
import json
from typing import Dict, Any, List
from . import registry

_ALLOWED_DIR = os.environ.get("FUXI_ALLOWED_DIR", "")


def _safe_path(path: str) -> str:
    """防止路径遍历攻击"""
    abs_path = os.path.abspath(path)
    if _ALLOWED_DIR:
        common = os.path.commonpath([abs_path, _ALLOWED_DIR])
        if common != _ALLOWED_DIR:
            raise PermissionError(f"Access denied: {path} is outside allowed directory")
    return abs_path


@registry.register(name="read_file", level="L0")
def read_file(path: str) -> str:
    """读取文件内容
    Args:
        path: 文件路径
    Returns:
        文件内容
    """
    safe_path = _safe_path(path)
    try:
        with open(safe_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"No such file: {path}")
    except Exception as e:
        raise IOError(f"Error reading file: {str(e)}")


@registry.register(name="write_file", level="L1")
def write_file(path: str, content: str) -> Dict[str, Any]:
    """写入文件内容
    Args:
        path: 文件路径
        content: 内容
    Returns:
        结果字典
    """
    safe_path = _safe_path(path)
    try:
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": f"File written: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="list_files", level="L0")
def list_files(directory: str) -> List[str]:
    """列出目录下文件
    Args:
        directory: 目录路径
    Returns:
        文件列表
    """
    safe_dir = _safe_path(directory)
    if not os.path.isdir(safe_dir):
        raise NotADirectoryError(f"No such directory: {directory}")
    return [f for f in os.listdir(safe_dir) if os.path.isfile(os.path.join(safe_dir, f))]


@registry.register(name="file_exists", level="L0")
def file_exists(path: str) -> bool:
    """检查文件是否存在
    Args:
        path: 文件路径
    Returns:
        是否存在
    """
    safe_path = _safe_path(path)
    return os.path.exists(safe_path) and os.path.isfile(safe_path)


@registry.register(name="read_json", level="L0")
def read_json(path: str) -> Any:
    """读取 JSON 文件
    Args:
        path: 文件路径
    Returns:
        解析后的 JSON 对象
    """
    safe_path = _safe_path(path)
    try:
        with open(safe_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}


@registry.register(name="write_json", level="L1")
def write_json(path: str, data: Any) -> Dict[str, Any]:
    """写入 JSON 文件
    Args:
        path: 文件路径
        data: 数据对象
    Returns:
        结果字典
    """
    safe_path = _safe_path(path)
    try:
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)
        with open(safe_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"success": True, "message": f"JSON written: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
