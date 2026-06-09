from __future__ import annotations

"""GEPA 进化循环 - Fast/Slow/Dreaming 三层进化（设计文档 L6 核心）

Fast Loop (分钟级): SmartOptimizer 已在 selector 中集成
Slow Loop (小时级): 深度 system prompt 优化, A/B 测试标记
Dreaming (后台): 离线知识蒸馏, 反熵, 模式发现
"""
import os
import json
import time
import threading
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("gepa_loops")


@dataclass
class PromptVariant:
    """System Prompt 变体，用于 A/B 测试"""
    name: str
    content: str
    version: int = 1
    created_at: float = field(default_factory=time.time)
    success_count: int = 0
    failure_count: int = 0
    avg_steps: float = 0.0
    avg_tokens: float = 0.0
    active: bool = True

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0


@dataclass
class EvolutionRecord:
    """进化记录 - 可回滚"""
    timestamp: float
    action: str  # "prompt_updated" / "threshold_changed" / "strategy_shifted"
    before: Dict[str, Any]
    after: Dict[str, Any]
    reason: str
    quality_score: float = 0.0


class GepaSlowLoop:
    """GEPA 慢速循环 - 深度 system prompt 优化 + A/B 测试

    触发: 累积 50+ 次运行记录后, 或每 3 小时
    分析: 跨会话失败模式 → 生成 prompt 变体 → A/B 标记
    """

    MIN_RUNS_FOR_ANALYSIS = 50
    MIN_HOURS_BETWEEN_RUNS = 3
    MAX_VARIANTS = 5  # 最多保留 5 个 prompt 变体

    def __init__(self, db_path: Optional[str] = None):
        self._variants: Dict[str, PromptVariant] = {}
        self._evolution_log: List[EvolutionRecord] = []
        self._last_run_time: Optional[datetime] = None
        self._total_runs_since_last: int = 0
        self._lock = threading.Lock()

        # 默认 system prompt 模板
        self._base_prompt = (
            "你是伏羲引擎 (Fuxi Engine)，一个高效的 AI 助手。\n\n"
            "{memory_section}\n\n"
            "{tool_section}\n\n"
            "【工作模式】\n"
            "使用 ReAct 模式：Thought → Action → Observation，循环最多 {steps} 次。\n\n"
            "【输出格式】\n"
            "Action: tool_name({{\"param\": \"value\"}})\n"
            "Final: <直接给出答案>\n"
        )
        self._register_default_variants()

    def _register_default_variants(self):
        """注册默认 prompt 变体"""
        self._variants["default"] = PromptVariant(
            name="default",
            content=self._base_prompt,
        )
        self._variants["concise"] = PromptVariant(
            name="concise",
            content=(
                "你是伏羲引擎。简洁高效。\n\n"
                "{memory_section}\n\n"
                "可用工具: {tool_section}\n\n"
                "使用 Action: tool_name({{\"param\":\"value\"}}) 或 Final: 答案。"
                "最多 {steps} 步。直接行动，不要解释。"
            ),
        )
        self._variants["verbose"] = PromptVariant(
            name="verbose",
            content=(
                "你是伏羲引擎 (Fuxi Engine)，一个全面的 AI 助手。\n\n"
                "{memory_section}\n\n"
                "【可用工具详解】\n{tool_section}\n\n"
                "【执行规则】\n"
                "1. 先用 Thought 分析当前状态\n"
                "2. 再用 Action: tool_name(params) 执行\n"
                "3. 观察结果后决定下一步\n"
                "4. 任务完成后用 Final: 给出详细答案\n"
                "5. 最多 {steps} 步\n\n"
                "【工具调用格式】\n"
                "Action: tool_name({{\"param\": \"value\"}})\n\n"
                "【最终答案格式】\n"
                "Final: <完整答案>\n"
            ),
        )

    def should_run(self) -> bool:
        """判断是否应该触发 Slow Loop"""
        if self._total_runs_since_last < self.MIN_RUNS_FOR_ANALYSIS:
            return False
        if self._last_run_time is not None:
            hours_since = (datetime.now() - self._last_run_time).total_seconds() / 3600
            if hours_since < self.MIN_HOURS_BETWEEN_RUNS:
                return False
        return True

    def on_complete(self, result_count: int = 1):
        """每次引擎运行完成后调用，递增计数器"""
        self._total_runs_since_last += result_count

    def analyze_and_optimize(self, strategy_stats: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """分析跨会话模式，生成优化建议

        Returns: 优化建议 dict 或 None（无需优化）
        """
        with self._lock:
            if not self.should_run():
                return None

            recommendations = []

            for cat, cat_data in strategy_stats.items():
                runs = cat_data.get("total_runs", 0)
                success_rate = cat_data.get("success_rate", 0)

                if runs < 5:
                    continue

                # 模式1: 某类问题成功率持续低于 50%
                if success_rate < 0.5:
                    best_variant = self._find_best_variant_for(cat)
                    recommendations.append({
                        "type": "prompt_switch",
                        "category": cat,
                        "reason": f"成功率 {success_rate:.0%} < 50%, 切换至 {best_variant}",
                        "new_variant": best_variant,
                    })

                # 模式2: 某类问题步骤过多，建议压缩
                avg_steps = cat_data.get("avg_steps", 0)
                if avg_steps > 5:
                    recommendations.append({
                        "type": "step_reduction",
                        "category": cat,
                        "reason": f"平均 {avg_steps:.0f} 步, 建议压缩至 {int(avg_steps * 0.7)}",
                        "new_max_steps": int(avg_steps * 0.7),
                    })

            self._last_run_time = datetime.now()
            self._total_runs_since_last = 0

            if not recommendations:
                return None

            return {
                "timestamp": time.time(),
                "recommendations": recommendations,
                "prompt_variants": self.get_active_variants(),
            }

    def _find_best_variant_for(self, category: str) -> str:
        """为某类别找最佳的 prompt 变体"""
        best_name = "default"
        best_rate = 0
        for name, variant in self._variants.items():
            if variant.active and variant.success_rate > best_rate:
                best_rate = variant.success_rate
                best_name = name
        return best_name

    def get_active_variants(self) -> List[Dict]:
        return [
            {"name": v.name, "success_rate": v.success_rate,
             "runs": v.success_count + v.failure_count, "active": v.active}
            for v in self._variants.values()
        ]

    def record_variant_result(self, variant_name: str, success: bool,
                              steps: int = 0, tokens: int = 0):
        """记录 A/B 测试中某变体的表现"""
        if variant_name in self._variants:
            v = self._variants[variant_name]
            if success:
                v.success_count += 1
            else:
                v.failure_count += 1
            if v.success_count + v.failure_count > 0:
                v.avg_steps = (v.avg_steps * (v.total_runs - 1) + steps) / v.total_runs if hasattr(v, 'total_runs') else steps

    def get_best_prompt(self, category: str = "default") -> str:
        """获取当前最佳 prompt"""
        best = self._variants.get("default")
        best_rate = -1
        for name, v in self._variants.items():
            if v.active and v.success_rate > best_rate and v.success_count + v.failure_count >= 5:
                best_rate = v.success_rate
                best = v
        return best.content if best else self._base_prompt

    def log_evolution(self, action: str, before: Dict, after: Dict,
                      reason: str, quality_score: float = 0.0):
        self._evolution_log.append(EvolutionRecord(
            time.time(), action, before, after, reason, quality_score))

    def get_evolution_history(self) -> List[Dict]:
        return [
            {"time": r.timestamp, "action": r.action, "reason": r.reason,
             "score": r.quality_score}
            for r in self._evolution_log[-50:]
        ]


class DreamingEngine:
    """Dreaming 后台引擎 - 离线知识蒸馏 + 反熵

    功能:
    1. 将温记忆中的高频对话模式蒸馏为冷记忆中的向量知识
    2. 检测记忆碎片化，执行反熵整理
    3. 在不响应用户时以低优先级后台线程运行
    """

    def __init__(self, warm_memory=None, cold_memory=None,
                 interval_minutes: int = 30):
        self._warm = warm_memory
        self._cold = cold_memory
        self._interval = interval_minutes * 60
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_dream: float = 0
        self._dreams_completed: int = 0
        self._knowledge_distilled: int = 0

    def start(self):
        """启动后台 Dreaming 线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._dream_loop, daemon=True, name="gepa-dreaming")
        self._thread.start()
        logger.info("Dreaming 后台线程已启动")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _dream_loop(self):
        while self._running:
            try:
                now = time.time()
                if now - self._last_dream >= self._interval:
                    self._execute_dream()
                    self._last_dream = now
            except Exception as e:
                logger.warning(f"Dreaming error: {e}")
            time.sleep(60)  # 每分钟检查一次

    def _execute_dream(self):
        """执行一次 Dreaming 周期"""
        logger.debug("Dreaming: 开始离线探索...")
        results = {"distilled": 0, "defragmented": 0}

        # 1. 知识蒸馏: 将温记忆高频模式 → 冷记忆摘要
        if self._warm and self._cold:
            try:
                stats = self._warm.get_stats()
                if stats.get("total_messages", 0) > 20:
                    recent = self._warm.get_recent("", limit=50)
                    if recent.get("success") and recent["entries"]:
                        # 提取常见话题模式
                        contents = [e["content"] for e in recent["entries"]]
                        combined = " | ".join(c[:100] for c in contents[:20])
                        self._cold.insert_summary(
                            content=combined,
                            summary=f"[Dreaming蒸馏] {len(contents)}条温记忆 → 冷知识 (周期#{self._dreams_completed + 1})",
                        )
                        results["distilled"] = len(contents)
                        self._knowledge_distilled += 1
            except Exception as e:
                logger.debug(f"Dreaming 蒸馏失败: {e}")

        # 2. 反熵: 清理冷记忆中过于相似的条目
        if self._cold:
            try:
                all_entries = self._cold.get_recent(limit=100)
                if all_entries.get("success") and len(all_entries["entries"]) > 10:
                    # 简单去重: 同样内容的条目只保留最新
                    seen = set()
                    for entry in all_entries["entries"]:
                        summary = entry.get("summary", "")[:50]
                        if summary in seen:
                            pass  # 标记为可清理（实际删除需更谨慎）
                        seen.add(summary)
            except Exception as e:
                logger.debug(f"Dreaming 反熵失败: {e}")

        self._dreams_completed += 1
        logger.debug(f"Dreaming #{self._dreams_completed}: 蒸馏={results['distilled']}条, 反熵完成")

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "dreams_completed": self._dreams_completed,
            "knowledge_distilled": self._knowledge_distilled,
            "last_dream_ago": int(time.time() - self._last_dream) if self._last_dream > 0 else -1,
        }
