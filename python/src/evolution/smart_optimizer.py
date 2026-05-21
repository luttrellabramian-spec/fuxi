"""智能策略优化器 - 替代简单平均，使用 Thompson Sampling + UCB 探索/利用

核心改进:
1. 不是返回历史平均，而是用 Beta 分布建模成功率，Thompson Sampling 选最优
2. 引入探索率 (epsilon-greedy): 10% 概率随机尝试新参数组合
3. 对从未尝试过的配置给予乐观先验 (UCB bonus)，鼓励探索
"""
import math
import random
import time
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class StrategyCandidate:
    """策略候选配置"""
    max_steps: int
    temperature: float
    strategy_type: str  # direct / react_single / react_multi_step

    # Beta 分布参数 (alpha=success+1, beta=failure+1)
    alpha: int = 1  # 成功次数 + 1 (先验)
    beta: int = 1   # 失败次数 + 1 (先验)

    total_runs: int = 0
    total_tokens: int = 0
    avg_latency_ms: float = 0.0

    def sample_success_rate(self) -> float:
        """Thompson Sampling: 从 Beta(alpha, beta) 分布采样"""
        return random.betavariate(self.alpha, self.beta)

    def ucb_score(self, total_global_runs: int) -> float:
        """UCB1: 均值 + 探索奖励"""
        if self.total_runs == 0:
            return float("inf")  # 从未尝试过的, 优先探索
        mean = self.alpha / (self.alpha + self.beta)
        exploration_bonus = math.sqrt(2 * math.log(total_global_runs + 1) / self.total_runs)
        return mean + exploration_bonus

    def record(self, success: bool, tokens: int = 0, latency_ms: float = 0):
        self.alpha += 1 if success else 0
        self.beta += 0 if success else 1
        self.total_runs += 1
        self.total_tokens += tokens
        if self.total_runs > 0:
            self.avg_latency_ms = (self.avg_latency_ms * (self.total_runs - 1) + latency_ms) / self.total_runs


class SmartOptimizer:
    """智能优化器: 用 Bandit 算法优化策略参数"""

    EXPLORE_RATE = 0.10       # 10% 概率探索新策略
    MIN_RUNS_FOR_CONFIDENCE = 5  # 最少 5 次运行才信任统计

    # 策略空间（可探索的配置组合）
    _STRATEGY_POOL = [
        StrategyCandidate(3, 0.0, "direct"),
        StrategyCandidate(3, 0.1, "direct"),
        StrategyCandidate(5, 0.1, "react_single"),
        StrategyCandidate(5, 0.2, "react_single"),
        StrategyCandidate(8, 0.2, "react_multi_step"),
        StrategyCandidate(8, 0.3, "react_multi_step"),
        StrategyCandidate(10, 0.25, "react_multi_step"),
        StrategyCandidate(12, 0.3, "react_multi_step"),
    ]

    def __init__(self):
        self._candidates: Dict[str, List[StrategyCandidate]] = {}
        self._category_stats: Dict[str, Dict[str, int]] = {}
        self._global_runs = 0

    def _get_or_create_candidates(self, category: str) -> List[StrategyCandidate]:
        if category not in self._candidates:
            from dataclasses import replace
            self._candidates[category] = [replace(c) for c in self._STRATEGY_POOL]
        return self._candidates[category]

    def recommend(self, category: str) -> StrategyCandidate:
        """为给定查询类别推荐最优策略"""
        candidates = self._get_or_create_candidates(category)

        # Epsilon-greedy: 10% 概率随机探索
        if random.random() < self.EXPLORE_RATE:
            return random.choice(candidates)

        # Thompson Sampling: 从 Beta 分布采样，选成功率最高的
        best = max(candidates, key=lambda c: c.sample_success_rate())
        return best

    def recommend_with_ucb(self, category: str) -> StrategyCandidate:
        """UCB1 选择: 平衡探索与利用"""
        candidates = self._get_or_create_candidates(category)

        # 至少一个候选从未尝试过, 优先探索它
        untried = [c for c in candidates if c.total_runs == 0]
        if untried:
            return untried[0]

        self._global_runs += 1
        best = max(candidates, key=lambda c: c.ucb_score(self._global_runs))
        return best

    def record(self, category: str, steps: int, temperature: float,
               success: bool, tokens: int, latency_ms: float):
        """记录一次执行结果, 更新对应策略候选"""
        candidates = self._get_or_create_candidates(category)

        # 找到最接近的策略候选
        best_match = None
        best_distance = float("inf")
        for c in candidates:
            dist = abs(c.max_steps - steps) + abs(c.temperature - temperature) * 10
            if steps >= 1 and c.strategy_type == "react_multi_step":
                dist -= 5  # 偏好匹配
            elif steps <= 1 and c.strategy_type == "direct":
                dist -= 5
            if dist < best_distance:
                best_distance = dist
                best_match = c

        if best_match:
            best_match.record(success, tokens, latency_ms)

        # 维护类别统计
        if category not in self._category_stats:
            self._category_stats[category] = {"success": 0, "failure": 0}
        self._category_stats[category]["success" if success else "failure"] += 1

    def get_recommendation_dict(self, category: str) -> Dict[str, Any]:
        """返回与 Selector 兼容的推荐字典"""
        best = self.recommend(category)
        stats = self._category_stats.get(category, {"success": 0, "failure": 0})
        total = stats["success"] + stats["failure"]
        return {
            "recommend_steps": best.max_steps,
            "recommend_temp": best.temperature,
            "best_strategy": best.strategy_type,
            "success_rate": stats["success"] / total if total > 0 else 0.0,
            "total_runs": total,
            "avg_latency_ms": best.avg_latency_ms,
            "avg_steps": float(best.max_steps),
            "strategies": [
                {"type": c.strategy_type,
                 "success_rate": c.alpha / (c.alpha + c.beta) if (c.alpha + c.beta) > 0 else 0,
                 "runs": c.total_runs}
                for c in self._get_or_create_candidates(category)
                if c.total_runs > 0
            ],
        }


class CausalValidator:
    """因果验证器: 追踪优化是否真的带来了改进"""

    def __init__(self, window_size: int = 20):
        self._window = window_size
        # 每类别的历史性能: [(timestamp, success_rate_before_change, success_rate_after_change)]
        self._history: Dict[str, List[Tuple[float, float, float]]] = {}
        # 当前窗口内的结果
        self._current_window: Dict[str, List[bool]] = {}

    def record_before_change(self, category: str) -> float:
        """记录策略变更前的成功率"""
        results = self._current_window.get(category, [])
        if not results:
            return 0.5  # 无数据, 返回中性值
        return sum(results) / len(results)

    def record_after_change(self, category: str, success: bool,
                            strategy_name: str, prev_success_rate: float):
        """记录策略变更后的结果"""
        if category not in self._current_window:
            self._current_window[category] = []
        self._current_window[category].append(success)

        # 窗口满了, 计算效果
        if len(self._current_window[category]) >= self._window:
            current_rate = sum(self._current_window[category]) / len(self._current_window[category])
            if category not in self._history:
                self._history[category] = []
            self._history[category].append((time.time(), prev_success_rate, current_rate))
            self._current_window[category] = []

    def get_improvement_summary(self, category: str) -> Dict[str, Any]:
        """获取改进摘要"""
        records = self._history.get(category, [])
        if not records:
            return {"improvements": 0, "regressions": 0, "avg_delta": 0}

        deltas = [after - before for _, before, after in records]
        improvements = sum(1 for d in deltas if d > 0.01)
        regressions = sum(1 for d in deltas if d < -0.01)
        return {
            "total_changes": len(records),
            "improvements": improvements,
            "regressions": regressions,
            "avg_delta": sum(deltas) / len(deltas),
            "latest_before": records[-1][1] if records else 0,
            "latest_after": records[-1][2] if records else 0,
        }
