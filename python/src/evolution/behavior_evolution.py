from __future__ import annotations

"""行为进化 — 真正改写系统行为，不只是改 prompt

能力:
1. 动态启用/禁用工具（基于成功率自动管理工具生命周期）
2. 动态调整并行策略（学习何时该并行执行）
3. 动态调整工具超时（慢工具给更多时间）
4. 动态调整记忆检索策略（根据类别跳过不必要的检索层）
5. 运行时修改 ReAct 解析策略（宽松/严格模式）
"""
import time
import threading
import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("behavior_evolution")


@dataclass
class ToolBehaviorProfile:
    """工具行为画像 — 从历史数据中学习"""
    name: str
    avg_latency_ms: float = 0
    success_rate: float = 1.0
    call_count: int = 0
    timeout_count: int = 0
    deprioritized_count: int = 0

    # 学习到的行为偏好
    preferred_timeout_ms: int = 30000   # 该工具的最佳超时
    safe_to_parallelize: bool = False   # 可以安全地与其他工具并行
    requires_user_confirmation: bool = False  # 需要用户确认

    def update_from_result(self, success: bool, latency_ms: float, was_timeout: bool):
        self.call_count += 1
        self.avg_latency_ms = (self.avg_latency_ms * (self.call_count - 1) + latency_ms) / self.call_count
        if not success:
            self.success_rate = ((self.success_rate * (self.call_count - 1)) + 0) / self.call_count
        else:
            self.success_rate = ((self.success_rate * (self.call_count - 1)) + 1) / self.call_count
        if was_timeout:
            self.timeout_count += 1

        # 自适应超时: 平均延迟 × 3, 最少 5 秒, 最多 60 秒
        self.preferred_timeout_ms = max(5000, min(60000, int(self.avg_latency_ms * 3)))

        # 标记为可并行: 只读 + 延迟稳定 + 成功率高
        self.safe_to_parallelize = (
            self.success_rate > 0.9
            and self.timeout_count < 3
            and self.avg_latency_ms < 5000
        )


class BehaviorEvolution:
    """行为进化引擎 — 真正改写运行时行为"""

    def __init__(self):
        self._tool_profiles: Dict[str, ToolBehaviorProfile] = {}
        self._disabled_tools: Set[str] = set()        # 被进化的工具
        self._enabled_parallel: bool = True            # 全局并行开关
        self._parse_mode: str = "strict"               # strict / relaxed
        self._memory_skip_layers: Set[str] = set()     # 跳过的记忆层
        self._lock = threading.Lock()

        # 进化历史
        self._changes: List[Dict] = []

    # ── 工具生命周期管理 ──────────────────────

    def get_profile(self, tool_name: str) -> ToolBehaviorProfile:
        if tool_name not in self._tool_profiles:
            self._tool_profiles[tool_name] = ToolBehaviorProfile(name=tool_name)
        return self._tool_profiles[tool_name]

    def record_tool_result(self, tool_name: str, success: bool,
                           latency_ms: float, was_timeout: bool = False):
        profile = self.get_profile(tool_name)
        prev_success_rate = profile.success_rate
        profile.update_from_result(success, latency_ms, was_timeout)

        with self._lock:
            # 规则1: 连续 10 次调用, 成功率 < 20% → 禁用该工具
            if profile.call_count >= 10 and profile.success_rate < 0.2:
                if tool_name not in self._disabled_tools:
                    self._disabled_tools.add(tool_name)
                    self._log_change("tool_disabled", {
                        "tool": tool_name,
                        "reason": f"成功率 {profile.success_rate:.0%} < 20% (调用 {profile.call_count} 次)",
                    })

            # 规则2: 禁用后, 如果有新工具替代, 尝试恢复
            elif tool_name in self._disabled_tools and profile.success_rate > 0.6:
                self._disabled_tools.discard(tool_name)
                self._log_change("tool_restored", {
                    "tool": tool_name,
                    "reason": f"成功率恢复到 {profile.success_rate:.0%}",
                })

            # 规则3: 超时率 > 50% → 增加该工具超时时间
            if profile.timeout_count > profile.call_count * 0.5 and profile.call_count >= 4:
                self._log_change("timeout_adjusted", {
                    "tool": tool_name,
                    "new_timeout_ms": profile.preferred_timeout_ms,
                    "reason": f"超时率 {profile.timeout_count}/{profile.call_count}",
                })

    def is_tool_enabled(self, tool_name: str) -> bool:
        return tool_name not in self._disabled_tools

    def get_disabled_tools(self) -> List[str]:
        return list(self._disabled_tools)

    # ── 并行策略控制 ──────────────────────────

    def should_enable_parallel(self) -> bool:
        """全局并行是否应该启用"""
        with self._lock:
            return self._enabled_parallel

    def update_parallel_strategy(self, parallel_success_rate: float):
        """根据并行执行的实际效果调整全局并行策略"""
        with self._lock:
            if parallel_success_rate < 0.5 and self._enabled_parallel:
                self._enabled_parallel = False
                self._log_change("parallel_disabled", {
                    "reason": f"并行成功率 {parallel_success_rate:.0%} < 50%",
                })
            elif parallel_success_rate > 0.9 and not self._enabled_parallel:
                self._enabled_parallel = True
                self._log_change("parallel_enabled", {
                    "reason": f"并行成功率恢复到 {parallel_success_rate:.0%}",
                })

    # ── 记忆检索策略控制 ──────────────────────

    def skip_memory_layer(self, layer: str):
        """跳过某个记忆层（如果该层检索一直没帮助）"""
        self._memory_skip_layers.add(layer)
        self._log_change("memory_layer_skipped", {"layer": layer})

    def restore_memory_layer(self, layer: str):
        self._memory_skip_layers.discard(layer)

    def should_skip_layer(self, layer: str) -> bool:
        return layer in self._memory_skip_layers

    # ── ReAct 解析模式 ────────────────────────

    def get_parse_mode(self) -> str:
        return self._parse_mode

    def set_parse_mode(self, mode: str):
        """strict: 严格匹配 Action/Final 格式
           relaxed: 尝试从任意文本中提取意图"""
        if mode in ("strict", "relaxed"):
            self._parse_mode = mode
            self._log_change("parse_mode_changed", {"mode": mode})

    # ── 快照与回滚 ────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "disabled_tools": list(self._disabled_tools),
                "enabled_parallel": self._enabled_parallel,
                "parse_mode": self._parse_mode,
                "memory_skip_layers": list(self._memory_skip_layers),
                "tool_profile_count": len(self._tool_profiles),
            }

    def rollback_to(self, snapshot: Dict[str, Any]):
        with self._lock:
            self._disabled_tools = set(snapshot.get("disabled_tools", []))
            self._enabled_parallel = snapshot.get("enabled_parallel", True)
            self._parse_mode = snapshot.get("parse_mode", "strict")
            self._memory_skip_layers = set(snapshot.get("memory_skip_layers", []))
            self._log_change("rollback", {"to_snapshot": snapshot})

    # ── 查询接口 ──────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "disabled_tools": list(self._disabled_tools),
                "enabled_parallel": self._enabled_parallel,
                "parse_mode": self._parse_mode,
                "memory_skip_layers": list(self._memory_skip_layers),
                "tool_profiles": {
                    name: {
                        "success_rate": p.success_rate,
                        "avg_latency_ms": p.avg_latency_ms,
                        "call_count": p.call_count,
                        "timeout_count": p.timeout_count,
                        "safe_to_parallelize": p.safe_to_parallelize,
                        "preferred_timeout_ms": p.preferred_timeout_ms,
                    }
                    for name, p in self._tool_profiles.items()
                    if p.call_count > 0
                },
                "changes_count": len(self._changes),
            }

    def get_tool_timeout(self, tool_name: str) -> int:
        profile = self.get_profile(tool_name)
        if profile.call_count >= 3:
            return profile.preferred_timeout_ms
        return 30000  # 默认

    def _log_change(self, action: str, detail: Dict):
        self._changes.append({
            "time": time.time(),
            "action": action,
            "detail": detail,
        })
        logger.info(f"[行为进化] {action}: {detail}")
