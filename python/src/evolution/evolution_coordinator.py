"""进化协调器 - 质量评分 + 冲突仲裁 + 安全门禁 + 回滚（设计文档 L6+L7）

整合 Fast Loop (SmartOptimizer) + Slow Loop (GepaSlowLoop) + Dreaming (DreamingEngine)
统一调度三层进化机制的输出，确保安全可控。
"""
import json
import time
import threading
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("evolution_coordinator")


class ApprovalStatus(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass
class ChangeProposal:
    """变更提案"""
    id: str
    source: str  # "fast_loop" / "slow_loop" / "dreaming"
    action: str  # "prompt_update" / "threshold_adjust" / "strategy_shift"
    before: Dict[str, Any]
    after: Dict[str, Any]
    reason: str
    risk_level: str = "low"  # low / medium / high / critical
    quality_score: float = 0.0
    status: str = "pending"  # pending / approved / rejected / rolled_back
    created_at: float = field(default_factory=time.time)
    approved_at: Optional[float] = None
    rollback_version: Optional[Dict[str, Any]] = None


@dataclass
class FrozenSnapshot:
    """冻结快照 — 用于回滚"""
    timestamp: float
    component: str  # "hot_memory" / "system_prompt" / "strategy_params"
    data: Dict[str, Any]
    version: int


class QualityScorer:
    """质量评分器 — 评估进化建议的可信度"""

    MIN_SAMPLE_SIZE = 10
    CONFIDENCE_MULTIPLIER = 1.0

    def score(self, proposal: ChangeProposal, history: List[Dict]) -> float:
        """计算进化建议的质量评分 (0.0 ~ 1.0)"""
        score = 0.5  # 基线

        # 因素1: 样本量
        sample_size = proposal.after.get("sample_size", 0)
        if sample_size >= self.MIN_SAMPLE_SIZE:
            score += 0.15
        elif sample_size >= 5:
            score += 0.05
        else:
            score -= 0.2  # 样本太小不可信

        # 因素2: 成功率变化
        before_rate = proposal.before.get("success_rate", 0.5)
        after_rate = proposal.after.get("success_rate", 0.5)
        delta = after_rate - before_rate
        if delta > 0.1:
            score += 0.2
        elif delta > 0:
            score += 0.05
        elif delta < -0.05:
            score -= 0.3  # 退化, 严重扣分

        # 因素3: 来源可信度
        source_weights = {"slow_loop": 0.15, "fast_loop": 0.05, "dreaming": -0.1}
        score += source_weights.get(proposal.source, 0)

        # 因素4: 相似建议历史成功率
        similar_approved = sum(1 for h in history
                               if h.get("action") == proposal.action
                               and h.get("status") == "approved")
        if similar_approved > 3:
            score += 0.1
        elif similar_approved == 0:
            score -= 0.05

        return max(0.0, min(1.0, score))


class SafetyGate:
    """安全门禁 — 检查进化建议的安全边界"""

    FORBIDDEN_ACTIONS = {
        "system_prompt_core": "禁止修改核心定位",
        "tool_removal": "禁止删除已验证工具",
        "unbounded_loop": "禁止取消步数限制",
    }

    MAX_THRESHOLD_CHANGE = 0.3  # 单次阈值变化不超过 30%
    MAX_STEP_CHANGE = 5  # 单次步数变化不超过 5

    def check(self, proposal: ChangeProposal) -> tuple[bool, str]:
        """安全检查, 返回 (通过, 原因)"""
        # 规则1: 禁止修改核心定位
        if proposal.action in self.FORBIDDEN_ACTIONS:
            return False, self.FORBIDDEN_ACTIONS[proposal.action]

        # 规则2: 阈值变化不能太激进
        if proposal.action == "threshold_adjust":
            before_val = float(proposal.before.get("value", 0))
            after_val = float(proposal.after.get("value", 0))
            if before_val > 0:
                change = abs(after_val - before_val) / before_val
                if change > self.MAX_THRESHOLD_CHANGE:
                    return False, f"阈值变化 {change:.0%} 超过 {self.MAX_THRESHOLD_CHANGE:.0%} 上限"

        # 规则3: 步数变化不能太激进
        if "steps" in proposal.after:
            before_steps = proposal.before.get("max_steps", 10)
            after_steps = proposal.after.get("max_steps", 10)
            if abs(after_steps - before_steps) > self.MAX_STEP_CHANGE:
                return False, f"步数变化 {abs(after_steps - before_steps)} 超过上限"

        # 规则4: 高风险操作需要额外确认
        if proposal.risk_level == "critical":
            return False, "高风险操作需要人工确认"

        return True, "安全门禁通过"


class RollbackManager:
    """回滚管理器"""

    def __init__(self, max_snapshots: int = 20):
        self._snapshots: List[FrozenSnapshot] = []
        self._max_snapshots = max_snapshots
        self._rollback_count = 0

    def create_snapshot(self, component: str, data: Dict[str, Any]):
        """创建冻结快照"""
        snapshot = FrozenSnapshot(
            timestamp=time.time(),
            component=component,
            data=json.loads(json.dumps(data, ensure_ascii=False, default=str)),
            version=len(self._snapshots) + 1,
        )
        self._snapshots.append(snapshot)
        # 保持最新 N 个快照
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots.pop(0)

    def rollback(self, component: str, to_version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """回滚到指定版本或最新快照"""
        if to_version:
            for s in self._snapshots:
                if s.version == to_version and s.component == component:
                    self._rollback_count += 1
                    return s.data
        # 回滚到最新
        for s in reversed(self._snapshots):
            if s.component == component:
                self._rollback_count += 1
                return s.data
        return None

    def get_status(self) -> Dict:
        return {
            "total_snapshots": len(self._snapshots),
            "rollback_count": self._rollback_count,
            "components": list(set(s.component for s in self._snapshots)),
        }


class EvolutionCoordinator:
    """进化协调器 — 三层进化机制的统一调度器"""

    def __init__(self):
        self._quality_scorer = QualityScorer()
        self._safety_gate = SafetyGate()
        self._rollback_mgr = RollbackManager()
        self._proposals: List[ChangeProposal] = []
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def propose_change(self, source: str, action: str,
                       before: Dict, after: Dict, reason: str,
                       risk_level: str = "low") -> ChangeProposal:
        """提交一个进化变更提案"""
        import uuid
        pid = str(uuid.uuid4())[:8]
        proposal = ChangeProposal(
            id=pid, source=source, action=action,
            before=before, after=after, reason=reason,
            risk_level=risk_level,
        )

        with self._lock:
            # 1. 质量评分
            proposal.quality_score = self._quality_scorer.score(proposal, self._history)

            # 2. 安全检查
            passed, gate_reason = self._safety_gate.check(proposal)

            # 3. 决策: 高分 + 通过安全门禁 = 自动批准
            if passed and proposal.quality_score >= 0.7:
                # 创建回滚快照
                self._rollback_mgr.create_snapshot(
                    f"{proposal.action}",
                    {"before": proposal.before, "after": proposal.after}
                )
                proposal.status = "approved"
                proposal.approved_at = time.time()
                logger.info(f"进化提案 [{proposal.id}] 自动批准: {reason} (score={proposal.quality_score:.2f})")
            elif passed and proposal.quality_score >= 0.5:
                proposal.status = "pending"  # 需要人工审核
                logger.info(f"进化提案 [{proposal.id}] 待审核: {reason} (score={proposal.quality_score:.2f})")
            else:
                proposal.status = "rejected"
                logger.info(f"进化提案 [{proposal.id}] 已拒绝: {reason} (score={proposal.quality_score:.2f}, gate={gate_reason})")

            self._proposals.append(proposal)
            self._history.append({
                "id": pid, "action": action, "source": source,
                "status": proposal.status, "score": proposal.quality_score,
                "time": proposal.created_at,
            })

        return proposal

    def approve_manually(self, proposal_id: str) -> bool:
        """人工批准一个待审核的提案"""
        with self._lock:
            for p in self._proposals:
                if p.id == proposal_id and p.status == "pending":
                    self._rollback_mgr.create_snapshot(
                        f"{p.action}",
                        {"before": p.before, "after": p.after}
                    )
                    p.status = "approved"
                    p.approved_at = time.time()
                    return True
        return False

    def trigger_rollback(self, component: str, reason: str = "") -> Optional[Dict]:
        """触发回滚（当检测到退化时）"""
        data = self._rollback_mgr.rollback(component)
        if data:
            logger.warning(f"回滚触发: {component} (原因: {reason})")
            # 标记最后关联的提案为 rolled_back
            with self._lock:
                for p in reversed(self._proposals):
                    if p.action == component and p.status == "approved":
                        p.status = "rolled_back"
                        break
        return data

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            approved = sum(1 for p in self._proposals if p.status == "approved")
            pending = sum(1 for p in self._proposals if p.status == "pending")
            rejected = sum(1 for p in self._proposals if p.status == "rejected")
            rolled_back = sum(1 for p in self._proposals if p.status == "rolled_back")
        return {
            "total_proposals": len(self._proposals),
            "approved": approved,
            "pending": pending,
            "rejected": rejected,
            "rolled_back": rolled_back,
            "rollback_mgr": self._rollback_mgr.get_status(),
        }

    def get_pending_proposals(self) -> List[Dict]:
        return [
            {"id": p.id, "source": p.source, "action": p.action,
             "reason": p.reason, "score": p.quality_score, "risk": p.risk_level}
            for p in self._proposals if p.status == "pending"
        ]
