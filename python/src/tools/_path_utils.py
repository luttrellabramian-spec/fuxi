from __future__ import annotations

"""路径校验共享工具

集中路径遍历防御，所有读写主机文件的工具（file_tools / search_tools）
都应通过 _validate_path() 验证路径。
"""
import os

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


def get_base_dir() -> str:
    """返回允许访问的根目录绝对路径。"""
    return _BASE_DIR


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
        raise ValueError("Access denied: path traversal detected")

    # 规范化路径（在检查敏感目录之后进行）
    abs_path = os.path.abspath(path)

    # 检查是否在允许的目录内
    if not abs_path.startswith(_BASE_DIR):
        raise ValueError("Access denied: path outside allowed directory")

    # 检查是否包含敏感目录（使用规范化后的相对路径）
    rel_path = os.path.relpath(abs_path, _BASE_DIR)
    for pattern in _SENSITIVE_PATTERNS:
        if pattern in rel_path.split(os.sep):
            raise ValueError(f"Access denied: sensitive directory '{pattern}'")

    return abs_path
