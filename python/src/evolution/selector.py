"""统一选择器 - 集中管理所有"选择"决策

整合三层选择：
1. 工具选择：根据历史成功率和查询类型动态排序工具
2. 记忆选择：根据查询分类主动调用温/冷记忆检索
3. 策略选择：SmartOptimizer (Thompson Sampling + UCB) 替代简单平均
"""
import logging
import os
import time
import tempfile
from typing import Dict, Any, Optional

from evolution.query_classifier import QueryClassifier, QueryCategory
from evolution.strategy_profiler import StrategyProfiler
from evolution.tool_ranker import ToolRanker
from evolution.memory_optimizer import MemoryOptimizer
from evolution.smart_optimizer import SmartOptimizer, CausalValidator
from evolution.gepa_loops import GepaSlowLoop, DreamingEngine
from evolution.evolution_coordinator import EvolutionCoordinator
from evolution.behavior_evolution import BehaviorEvolution

logger = logging.getLogger("selector")


def _ensure_db_path(path: Optional[str]) -> str:
    """确保数据库路径有效，None 时创建临时文件"""
    if path is None or path == "":
        fd, path = tempfile.mkstemp(suffix=".db", prefix="fuxi_evolution_")
        os.close(fd)
    return path


class Selector:
    """统一选择器 - FuxiEngine 唯一的选型入口 (v0.3: SmartOptimizer)"""

    def __init__(
        self,
        evolution_db_path: Optional[str] = None,
        tracker_db_path: Optional[str] = None,
        warm_memory=None,
        cold_memory=None,
    ):
        self._db_path = _ensure_db_path(evolution_db_path)
        self._query_classifier = QueryClassifier()
        self._strategy_profiler = StrategyProfiler(db_path=self._db_path)
        self._tool_ranker = ToolRanker(tracker_db_path=tracker_db_path)
        self._memory_optimizer = MemoryOptimizer(db_path=self._db_path)

        # v0.3: SmartOptimizer 替代简单平均策略
        self._smart_optimizer = SmartOptimizer()
        self._causal_validator = CausalValidator(window_size=10)

        # v0.3: GEPA Slow Loop + Dreaming + BehaviorEvolution
        self._slow_loop = GepaSlowLoop(db_path=self._db_path)
        self._dreaming = DreamingEngine(warm_memory=warm_memory, cold_memory=cold_memory)
        self._evolution_coordinator = EvolutionCoordinator()
        self._behavior_evolution = BehaviorEvolution()

        self._warm_memory = warm_memory
        self._cold_memory = cold_memory

        self._last_category: Optional[str] = None
        self._last_advice: Dict[str, Any] = {}
        self._prev_success_rate: float = 0.5  # 用于因果验证

    def set_tracker_db(self, path: str) -> None:
        self._tool_ranker.set_tracker_db(path)

    def set_memory_instances(self, warm_memory=None, cold_memory=None) -> None:
        """设置温/冷记忆实例（注入模式，避免循环导入）"""
        self._warm_memory = warm_memory
        self._cold_memory = cold_memory

    # ── 核心选择接口 ─────────────────────────────────

    def select(
        self,
        user_message: str,
        session_id: str,
        available_tools: Dict[str, Any],
        default_steps: int = 10,
        default_temp: float = 0.2,
        is_new_message: bool = True,
    ) -> Dict[str, Any]:
        """执行一次完整的选择决策

        每次新消息调用都会刷新分类和工具排序。
        is_new_message=True 时执行完整决策，False 时仅返回缓存的建议。

        Returns:
            {query_category, strategy, tools(含 prompt_section), memory(含主动检索结果)}
        """
        if not is_new_message and self._last_advice:
            return self._last_advice

        # 1. 查询分类
        category: QueryCategory = self._query_classifier.classify(user_message)
        self._last_category = category.name

        # 2. 策略推荐（v0.3: SmartOptimizer Thompson Sampling）
        strategy_rec = self._smart_optimizer.get_recommendation_dict(category.name)

        # 3. 工具排序
        ranked_tools = self._tool_ranker.rank_tools(
            available_tools=available_tools,
            query_category=category.name,
        )
        tool_prompt = self._tool_ranker.build_prompt_section(ranked_tools)

        # 4. 记忆检索配置 + 主动检索
        memory_rec = self._memory_optimizer.get_retrieval_recommendation(category.name)
        retrieved_memories = self._retrieve_memories(
            query=user_message,
            session_id=session_id,
            config=memory_rec,
        )

        advice = {
            "query_category": category.name,
            "query_category_cn": category.label_cn,
            "complexity": category.complexity,
            "strategy": {
                "recommend_steps": strategy_rec.get("recommend_steps", default_steps),
                "recommend_temp": strategy_rec.get("recommend_temp", default_temp),
                "best_strategy": strategy_rec.get("best_strategy", "react_multi_step"),
                "history_success_rate": strategy_rec.get("success_rate", 0),
                "history_runs": strategy_rec.get("total_runs", 0),
            },
            "tools": {
                "ranked_list": ranked_tools,
                "prompt_section": tool_prompt,
            },
            "memory": memory_rec,
            "retrieved_memories": retrieved_memories,
        }
        self._last_advice = advice
        return advice

    def record_outcome(
        self,
        result: Dict[str, Any],
        user_message: str,
        session_id: str,
        trace_id: str,
    ) -> None:
        """记录一次执行结果（反馈给进化层）"""
        query_category = self._last_category or "unknown"
        success = result.get("success", False)
        completed = result.get("completed", False)
        steps = result.get("steps", [])
        total_steps = result.get("total_steps", 0)
        elapsed = result.get("elapsed", 0)
        usage = result.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        tools_used = [s["action"]["tool"] for s in steps if "action" in s]
        error_type = result.get("error", "") if not success else ""

        # v0.3: SmartOptimizer 记录（Bandit 学习）
        self._smart_optimizer.record(
            category=query_category,
            steps=total_steps,
            temperature=0.2,
            success=completed,
            tokens=total_tokens or 0,
            latency_ms=int(elapsed * 1000),
        )
        # 同时保留 StrategyProfiler 的持久化记录
        self._strategy_profiler.record_run(
            trace_id=trace_id, session_id=session_id,
            query_category=query_category, max_steps=total_steps,
            temperature=0.2, actual_steps=total_steps,
            completed=completed, total_tokens=total_tokens or 0,
            elapsed_ms=int(elapsed * 1000), tools_used=tools_used,
            success=success, error_type=error_type,
        )

        # v0.3: 因果验证 - 追踪优化是否真的有效
        self._causal_validator.record_after_change(
            category=query_category,
            success=completed,
            strategy_name="react",
            prev_success_rate=self._prev_success_rate,
        )
        # 更新基准成功率
        stats = self._smart_optimizer._category_stats.get(query_category, {})
        total = stats.get("success", 0) + stats.get("failure", 1)
        self._prev_success_rate = stats.get("success", 0) / total if total > 0 else 0.5

        # GEPA Slow Loop: 累计运行次数，触发深度分析
        self._slow_loop.on_complete()
        slow_result = self._slow_loop.analyze_and_optimize(
            self._strategy_profiler.get_all_stats()
        )
        if slow_result:
            for rec in slow_result.get("recommendations", []):
                self._evolution_coordinator.propose_change(
                    source="slow_loop",
                    action=rec["type"],
                    before={"success_rate": self._prev_success_rate},
                    after={"success_rate": self._prev_success_rate,
                           "new_variant": rec.get("new_variant", ""),
                           "reason": rec["reason"]},
                    reason=rec["reason"],
                )
            logger.info(f"Slow Loop 完成: {len(slow_result['recommendations'])} 条建议")

        # 每 10 次触发一次自动阈值调优
        if int(time.time()) % 10 == 0:
            try:
                self._memory_optimizer.auto_tune_threshold()
            except Exception:
                pass

    # ── 内部：主动记忆检索 ────────────────────────────

    def _retrieve_memories(
        self,
        query: str,
        session_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """根据进化配置主动执行记忆检索"""
        result = {"warm": [], "cold": []}
        prefix = f"mem_{session_id}_{int(time.time() * 1000)}"

        # 温记忆 FTS5 检索
        if config.get("enable_warm_search", True) and self._warm_memory:
            try:
                warm_result = self._warm_memory.search(
                    session_id=session_id,
                    query=query,
                    limit=config.get("warm_search_limit", 10),
                )
                if warm_result.get("success"):
                    result["warm"] = [
                        {
                            "content": e["content"][:300],
                            "timestamp": e.get("timestamp", 0),
                        }
                        for e in warm_result.get("entries", [])
                    ]
            except Exception as e:
                logger.debug(f"Warm memory retrieve failed: {e}")

        # 冷记忆向量检索
        if config.get("enable_cold_search", True) and self._cold_memory:
            try:
                cold_result = self._cold_memory.search_similar(
                    query=query,
                    limit=config.get("cold_search_limit", 5),
                )
                if cold_result.get("success"):
                    result["cold"] = [
                        {
                            "summary": e.get("summary", e.get("content", ""))[:200],
                            "similarity": e.get("similarity", 0),
                        }
                        for e in cold_result.get("entries", [])
                        if e.get("similarity", 0) >= config.get("cold_similarity_threshold", 0.3)
                    ]
            except Exception as e:
                logger.debug(f"Cold memory retrieve failed: {e}")

        return result

    def format_memory_context(
        self,
        retrieved: Dict[str, Any],
        hot_content: str = "",
    ) -> str:
        """将检索到的记忆格式化为可注入 system prompt 的文本"""
        parts = []

        # 热记忆（最近上下文）
        if hot_content:
            parts.append(f"【近期记忆】\n{hot_content[:800]}")

        # 温记忆（历史相关）
        warm_entries = retrieved.get("warm", [])
        if warm_entries:
            warm_text = "\n".join(
                f"- {e['content']}" for e in warm_entries[:5]
            )
            parts.append(f"【历史相关记录】\n{warm_text}")

        # 冷记忆（语义相关）
        cold_entries = retrieved.get("cold", [])
        if cold_entries:
            cold_text = "\n".join(
                f"- {e['summary']} (相关度:{e.get('similarity', 0):.2f})"
                for e in cold_entries[:3]
            )
            parts.append(f"【语义相关记忆】\n{cold_text}")

        return "\n\n".join(parts) if parts else ""

    # ── 统计 ─────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "selector": {"last_category": self._last_category},
            "strategy_profiler": self._strategy_profiler.get_all_stats(),
            "smart_optimizer": {
                "categories": {
                    cat: {
                        "candidates": [
                            {"strategy": c.strategy_type, "steps": c.max_steps,
                             "temp": c.temperature, "runs": c.total_runs,
                             "success_rate": round(c.alpha / (c.alpha + c.beta), 3)
                             if (c.alpha + c.beta) > 0 else 0}
                            for c in candidates if c.total_runs > 0
                        ]
                    }
                    for cat, candidates in self._smart_optimizer._candidates.items()
                },
                "category_stats": self._smart_optimizer._category_stats,
            },
            "causal_validator": {
                cat: self._causal_validator.get_improvement_summary(cat)
                for cat in self._smart_optimizer._candidates
            },
            "slow_loop": {
                "variants": self._slow_loop.get_active_variants(),
                "evolution_history": self._slow_loop.get_evolution_history(),
            },
            "dreaming": self._dreaming.get_status(),
            "evolution_coordinator": self._evolution_coordinator.get_status(),
            "tool_ranker": self._tool_ranker.get_tool_insights(),
            "memory_optimizer": {
                "retrieval_stats": self._memory_optimizer.get_retrieval_stats(),
                "params": self._memory_optimizer.get_all_parameters(),
            },
        }

    def start_dreaming(self):
        """启动后台 Dreaming 线程"""
        self._dreaming.start()

    def stop_dreaming(self):
        """停止后台 Dreaming 线程"""
        self._dreaming.stop()
