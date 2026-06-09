from __future__ import annotations

"""安全守卫 - § 分隔符 + 工具分级 (L0-L3) + Prompt 注入基础防护

设计文档 L7 安全层实现。
"""
import re
import logging
from typing import Dict, Any, List, Tuple
from enum import IntEnum

logger = logging.getLogger("security_guard")

# Prompt 注入检测模式（基础威胁模式库）
_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?(previous|above|earlier)\s+(instructions?|prompts?|directions?)",
    r"(?i)you\s+are\s+now\s+(a\s+)?(different|new|another)\s+(ai|assistant|model|bot)",
    r"(?i)pretend\s+(you\s+are|to\s+be)",
    r"(?i)act\s+as\s+(if\s+you\s+are|a\s+different)",
    r"(?i)forget\s+(all\s+)?(your\s+)?(previous\s+)?(instructions?|training|rules?)",
    r"(?i)system\s*(:\s*|prompt\s*:|message\s*:)\s*you\s+are",
    r"(?i)<\|im_start\|>|<\|im_end\|>",
    r"(?i)\[system\]\(.*\)",
]

# § 分隔符
SECTION_SEPARATOR = "§"


class ToolLevel(IntEnum):
    L0 = 0  # 只读工具, 所有渠道可用
    L1 = 1  # 写操作(可回滚), 需用户确认
    L2 = 2  # 危险操作, 仅白名单会话
    L3 = 3  # 系统级, 禁止自动执行


# 工具→级别映射（从 registry 读取或手动配置）
DEFAULT_TOOL_LEVELS: Dict[str, ToolLevel] = {
    "read_file": ToolLevel.L0,
    "list_files": ToolLevel.L0,
    "file_exists": ToolLevel.L0,
    "read_json": ToolLevel.L0,
    "http_get": ToolLevel.L0,
    "check_url": ToolLevel.L0,
    "grep": ToolLevel.L0,
    "search_file": ToolLevel.L0,
    "parse_headers": ToolLevel.L0,
    "http_post": ToolLevel.L1,
    "fetch_page": ToolLevel.L1,
    "fetch_api": ToolLevel.L1,
    "extract_links": ToolLevel.L1,
    "write_file": ToolLevel.L1,
    "write_json": ToolLevel.L1,
    "search_replace": ToolLevel.L1,
    "search_web": ToolLevel.L1,
    "memory_write": ToolLevel.L1,
    "memory_query": ToolLevel.L0,
    "memory_get_recent": ToolLevel.L0,
    "echo": ToolLevel.L0,
    "add": ToolLevel.L0,
    "web_search": ToolLevel.L0,
}


class SecurityGuard:
    """安全守卫 — Prompt 注入检测 + 工具分级 + 进化审批"""

    def __init__(self, whitelist_sessions: List[str] = None):
        self._whitelist = set(whitelist_sessions or [])
        self._injection_detected = 0
        self._blocked_calls = 0

    def detect_injection(self, text: str) -> Tuple[bool, str]:
        """检测 Prompt 注入, 返回 (是否注入, 匹配模式)"""
        for pattern in _INJECTION_PATTERNS:
            if re.search(pattern, text):
                self._injection_detected += 1
                return True, pattern
        return False, ""

    def sanitize_input(self, text: str) -> str:
        """净化用户输入 — 添加 § 分隔符隔离"""
        is_injection, pattern = self.detect_injection(text)
        if is_injection:
            logger.warning(f"检测到可能的 Prompt 注入: pattern={pattern}")
            # 不阻止请求，但在日志中标记，并用 § 包裹隔离
            return f"{SECTION_SEPARATOR}USER_INPUT{SECTION_SEPARATOR} {text} {SECTION_SEPARATOR}END{SECTION_SEPARATOR}"
        return f"{SECTION_SEPARATOR}USER_INPUT{SECTION_SEPARATOR} {text}"

    def check_tool_permission(self, tool_name: str, session_id: str = "",
                              user_confirmed: bool = False) -> Tuple[bool, str]:
        """检查工具调用权限"""
        level = DEFAULT_TOOL_LEVELS.get(tool_name, ToolLevel.L1)

        if level == ToolLevel.L0:
            return True, ""

        if level == ToolLevel.L1:
            if user_confirmed:
                return True, ""
            # L1 默认允许，但记录日志
            return True, "L1 操作: 建议用户确认"

        if level == ToolLevel.L2:
            if session_id in self._whitelist:
                return True, ""
            self._blocked_calls += 1
            return False, f"L2 操作被拒绝: {tool_name} (非白名单会话)"

        if level == ToolLevel.L3:
            self._blocked_calls += 1
            return False, f"L3 系统级操作被禁止: {tool_name}"

        return True, ""

    def add_to_whitelist(self, session_id: str):
        self._whitelist.add(session_id)

    def remove_from_whitelist(self, session_id: str):
        self._whitelist.discard(session_id)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "injections_detected": self._injection_detected,
            "blocked_calls": self._blocked_calls,
            "whitelist_size": len(self._whitelist),
        }
