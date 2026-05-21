"""文件操作工具"""
import os
import json
from typing import Dict, Any, List
from . import registry

# 允许访问的基础目录（项目根目录）
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

# 禁止访问的敏感目录模式
_SENSITIVE_PATTERNS = [
    '.git',
    '.env',
    'node_modules',
    '__pycache__',
    '.venv',
    'venv',
]


def _validate_path(path: str) -> str:
    """验证并规范化路径，防止路径遍历攻击
    
    Args:
        path: 原始路径
        
    Returns:
        规范化的绝对路径
        
    Raises:
        ValueError: 路径不合法
    """
    # 先检查路径遍历攻击（检查原始输入）
    if '..' in path:
        raise ValueError(f"Access denied: path traversal detected")

    # 规范化路径（在检查敏感目录之后进行）
    abs_path = os.path.abspath(path)

    # 检查是否在允许的目录内
    if not abs_path.startswith(_BASE_DIR):
        raise ValueError(f"Access denied: path outside allowed directory")

    # 检查是否包含敏感目录（使用规范化后的相对路径）
    rel_path = os.path.relpath(abs_path, _BASE_DIR)
    for pattern in _SENSITIVE_PATTERNS:
        if pattern in rel_path.split(os.sep):
            raise ValueError(f"Access denied: sensitive directory '{pattern}'")

    return abs_path


@registry.register(name="read_file", level="L0")
def read_file(path: str, max_size: int = 1024 * 1024) -> str:
    """读取文件内容
    Args:
        path: 文件路径
        max_size: 最大读取字节数（默认 1MB）
    Returns:
        文件内容
    """
    try:
        validated_path = _validate_path(path)

        # 检查文件大小
        file_size = os.path.getsize(validated_path)
        if file_size > max_size:
            raise ValueError(f"File too large: {file_size} bytes (max: {max_size})")

        with open(validated_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"No such file: {path}")
    except Exception as e:
        raise IOError(f"Error reading file: {str(e)}")


@registry.register(name="write_file", level="L1")
def write_file(path: str, content: str, max_size: int = 10 * 1024 * 1024) -> Dict[str, Any]:
    """写入文件内容
    Args:
        path: 文件路径
        content: 内容
        max_size: 最大写入字节数（默认 10MB）
    Returns:
        结果字典
    """
    try:
        validated_path = _validate_path(path)

        # 检查内容大小
        content_size = len(content.encode('utf-8'))
        if content_size > max_size:
            return {"success": False, "error": f"Content too large: {content_size} bytes (max: {max_size})"}

        # 创建备份（如果文件存在）
        if os.path.exists(validated_path):
            backup_path = validated_path + '.bak'
            try:
                import shutil
                shutil.copy2(validated_path, backup_path)
            except Exception:
                pass  # 备份失败不影响写入

        with open(validated_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": f"File written: {path}", "size": content_size}
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
    try:
        validated_path = _validate_path(directory)
        if not os.path.isdir(validated_path):
            raise NotADirectoryError(f"No such directory: {directory}")
        return [f for f in os.listdir(validated_path) if os.path.isfile(os.path.join(validated_path, f))]
    except Exception as e:
        raise IOError(f"Error listing files: {str(e)}")


@registry.register(name="file_exists", level="L0")
def file_exists(path: str) -> bool:
    """检查文件是否存在
    Args:
        path: 文件路径
    Returns:
        是否存在
    """
    try:
        validated_path = _validate_path(path)
        return os.path.exists(validated_path) and os.path.isfile(validated_path)
    except ValueError:
        return False


@registry.register(name="read_json", level="L0")
def read_json(path: str) -> Any:
    """读取 JSON 文件
    Args:
        path: 文件路径
    Returns:
        解析后的 JSON 对象
    """
    try:
        validated_path = _validate_path(path)
        with open(validated_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"success": False, "error": str(e)}


@registry.register(name="write_json", level="L1")
def write_json(path: str, data: Any) -> Dict[str, Any]:
    """写入 JSON 文件
    Args:
        path: 文件路径
        data: 数据对象
    Returns:
        结果字典
    """
    try:
        validated_path = _validate_path(path)

        # 创建备份（如果文件存在）
        if os.path.exists(validated_path):
            backup_path = validated_path + '.bak'
            try:
                import shutil
                shutil.copy2(validated_path, backup_path)
            except Exception:
                pass

        with open(validated_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"success": True, "message": f"JSON written: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
