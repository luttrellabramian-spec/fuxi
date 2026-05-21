"""进化引擎 - 进化层集成中枢

在 FuxiEngine 的每次运行前后调用：
- before_run(): 提供优化建议（策略参数、工具排序、记忆检索参数）
- after_run(): 收集反馈数据，更新进化模型

工作流程：
┌──────────────────────────────────────────────────┐
│ before_run( message, session_id )                 │
│   ├─ classify_query() → QueryCategory             │
│   ├─ get_strategy_recommendation() → params       │
│   ├─ rank_tools() → optimized_tool_list           │
│   └─ get_memory_recommendation() → retrieval_cfg  │
├──────────────────────────────────────────────────┤
│   ┌──────────────┐                               │
│   │  FuxiEngine  │  (使用优化后的参数运行)         │
│   └──────────────┘                               │
├──────────────────────────────────────────────────┤
│ after_run( result, trace_id )                     │
│   ├─ record_strategy_run() → update profiler     │
│   ├─ record_memory_usage() → update optimizer    │
│   └─ auto_tune() → adaptive adjustment            │
└──────────────────────────────────────────────────┘
"""
import logging
import time
from typing import Dict, Any, Optional

from evolution.query_classifier import QueryClassifier, QueryCategory
from evolution.strategy_profiler import StrategyProfiler
from evolution.tool_ranker import ToolRanker
from evolution.memory_optimizer import MemoryOptimizer

logger = logging.getLogger("evolution_engine")


class EvolutionEngine:
    """进化引擎 - 自进化中枢

    向 FuxiEngine 提供三层进化能力：
    1. 策略进化：自适应 max_steps、temperature
    2. 工具选择进化：动态排序工具列表
    3. 记忆检索进化：自适应检索参数
    """

    def __init__(
        self,
        evolution_db_path: Optional[str] = None,
        tracker_db_path: Optional[str] = None,
    ):
        self._query_classifier = QueryClassifier()
        self._strategy_profiler = StrategyProfiler(
            db_path=evolution_db_path or ""
        )
        self._tool_ranker = ToolRanker(
            tracker_db_path=tracker_db_path
        )
        self._memory_optimizer = MemoryOptimizer(
            db_path=evolution_db_path or ""
        )

        # 最新查询分类缓存（供 after_run 使用）
        self._last_category: Optional[str] = None

    def set_tracker_db(self, path: str) -> None:
        """设置 ToolCallTracker 数据库路径"""
        self._tool_ranker.set_tracker_db(path)

    # ── 运行前：提供优化建议 ──────────────────────────

    def before_run(
        self,
        user_message: str,
        session_id: str,
        available_tools: Dict[str, Any],
        default_steps: int = 10,
        default_temp: float = 0.2,
    ) -> Dict[str, Any]:
        """引擎运行前的进化建议

        Args:
            user_message: 用户消息
            session_id: 会话 ID
            available_tools: 可用工具字典
            default_steps: 默认最大步数
            default_temp: 默认温度

        Returns:
            {建议的策略参数、工具列表、记忆检索配置}
        """
        # 1. 查询分类
        category: QueryCategory = self._query_classifier.classify(user_message)
        self._last_category = category.name
        logger.debug(f"Evolution: 查询分类='{category.label_cn}' (复杂度={category.complexity})")

        # 2. 策略推荐
        strategy_rec = self._strategy_profiler.get_recommendation(category.name)

        # 3. 工具排序
        ranked_tools = self._tool_ranker.rank_tools(
            available_tools=available_tools,
            query_category=category.name,
        )
        tool_prompt = self._tool_ranker.build_prompt_section(ranked_tools)
        tool_insights = self._tool_ranker.get_tool_insights()

        # 4. 记忆检索配置
        memory_rec = self._memory_optimizer.get_retrieval_recommendation(category.name)

        return {
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
                "insights": tool_insights,
            },
            "memory": memory_rec,
        }

    # ── 运行后：收集反馈 ─────────────────────────────

    def after_run(
        self,
        result: Dict[str, Any],
        user_message: str,
        session_id: str,
        trace_id: str,
    ) -> None:
        """引擎运行后的进化反馈处理

        Args:
            result: run() 的返回结果
            user_message: 用户原始消息
            session_id: 会话 ID
            trace_id: 追踪 ID
        """
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

        # 1. 记录策略分析
        self._strategy_profiler.record_run(
            trace_id=trace_id,
            session_id=session_id,
            query_category=query_category,
            max_steps=total_steps,
            temperature=0.2,
            actual_steps=total_steps,
            completed=completed,
            total_tokens=total_tokens or 0,
            elapsed_ms=int(elapsed * 1000),
            tools_used=tools_used,
            success=success,
            error_type=error_type,
        )

        # 2. 记忆优化器自动调参（每 10 次运行触发一次）
        if int(time.time()) % 10 == 0:
            try:
                self._memory_optimizer.auto_tune_threshold()
            except Exception:
                pass

    # ── 统计查询 ─────────────────────────────────────

    def get_evolution_stats(self) -> Dict[str, Any]:
        """获取进化层全量统计

        Returns:
            {query_classification, strategy_profiler, tool_ranker, memory_optimizer}
        """
        return {
            "query_classifier": {
                "categories": {
                    name: {
                        "label": cat.label_cn,
                        "complexity": cat.complexity,
                        "recommended_steps": cat.recommended_steps,
                        "recommended_temp": cat.recommended_temp,
                    }
                    for name, cat in self._query_classifier.get_all_categories().items()
                },
            },
            "strategy_profiler": self._strategy_profiler.get_all_stats(),
            "tool_ranker": self._tool_ranker.get_tool_insights(),
            "memory_optimizer": {
                "retrieval_stats": self._memory_optimizer.get_retrieval_stats(),
                "params": self._memory_optimizer.get_all_parameters(),
            },
        }
