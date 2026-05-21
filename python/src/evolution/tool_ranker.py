"""工具排名器 - 基于历史数据动态优化工具选择推荐

功能：
1. 从 ToolCallTracker 获取工具成功率等数据
2. 根据查询类别推荐最可能成功/最相关的工具
3. 生成动态排序的工具列表用于提示词
4. 降权工具移动到 "谨慎使用" 区域

数据来源：ToolCallTracker 的 SQLite 数据库
"""
import sqlite3
import logging
import os
import time
from typing import Dict, Any, List, Optional

logger = logging.getLogger("tool_ranker")

# 工具与查询类别的关联权重（可基于数据分析持续优化）
# 值范围 0.0-1.0，越高表示关联性越强
TOOL_CATEGORY_RELEVANCE: Dict[str, Dict[str, float]] = {
    # code_gen
    "write_file": {"code_gen": 0.9, "multi_step_task": 0.6},
    "write_json": {"code_gen": 0.8, "multi_step_task": 0.5},
    "search_replace": {"code_gen": 0.8, "multi_step_task": 0.6},
    # file_operation
    "read_file": {"file_operation": 0.9, "code_gen": 0.7, "analysis": 0.6},
    "list_files": {"file_operation": 0.8, "multi_step_task": 0.4},
    "file_exists": {"file_operation": 0.7},
    "read_json": {"file_operation": 0.7, "code_gen": 0.5, "analysis": 0.5},
    "grep": {"file_operation": 0.7, "code_gen": 0.5, "search_query": 0.6},
    "search_file": {"file_operation": 0.8, "search_query": 0.7, "code_gen": 0.5},
    # search
    "http_get": {"search_query": 0.8, "multi_step_task": 0.5},
    "check_url": {"search_query": 0.6},
    "parse_headers": {"search_query": 0.5},
    # memory
    "memory_write": {"memory_query": 0.6},
    "memory_query": {"memory_query": 0.9, "simple_qa": 0.4},
    "memory_get_recent": {"memory_query": 0.8, "simple_qa": 0.3},
    # multi-purpose
    "search_replace": {"multi_step_task": 0.7},
}


# 工具优先级层级（影响展示顺序）
TOOL_PRIORITY_L1 = [  # 最常用工具
    "read_file", "write_file", "search_file", "memory_query",
    "file_exists", "list_files",
]
TOOL_PRIORITY_L2 = [  # 较常用工具
    "grep", "http_get", "memory_write", "memory_get_recent",
    "read_json", "write_json",
]
TOOL_PRIORITY_L3 = [  # 特殊场景工具
    "search_replace", "check_url", "parse_headers",
]


class ToolRanker:
    """工具排名器 - 动态优化工具选择"""

    def __init__(self, tracker_db_path: Optional[str] = None):
        """
        Args:
            tracker_db_path: ToolCallTracker 的 SQLite 数据库路径
        """
        self._tracker_db_path = tracker_db_path
        self._cache: Dict[str, Any] = {}
        self._cache_time = 0
        self._cache_ttl = 60  # 缓存 60 秒

    def set_tracker_db(self, path: str) -> None:
        """设置 ToolCallTracker 数据库路径"""
        self._tracker_db_path = path
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        self._cache = {}
        self._cache_time = 0

    def _get_tracker_data(self) -> Dict[str, Any]:
        """从 ToolCallTracker 数据库读取统计数据"""
        if not self._tracker_db_path or not os.path.exists(self._tracker_db_path):
            return {}

        # 缓存检查
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        try:
            conn = sqlite3.connect(self._tracker_db_path, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    """SELECT tool_name,
                              SUM(total_calls) as total,
                              SUM(success_calls) as success,
                              SUM(failure_calls) as failures,
                              AVG(avg_latency_ms) as avg_latency
                       FROM tool_daily_stats
                       GROUP BY tool_name"""
                )
                data = {}
                for row in cursor.fetchall():
                    total = row["total"] or 0
                    success = row["success"] or 0
                    data[row["tool_name"]] = {
                        "total_calls": total,
                        "success_rate": success / total if total > 0 else 1.0,
                        "avg_latency_ms": row["avg_latency"] or 0,
                        "failure_count": row["failures"] or 0,
                    }

                # 读取降权信息
                cursor2 = conn.execute(
                    """SELECT tool_name FROM tool_deprioritization
                       WHERE deprioritized = 1 AND restored_at IS NULL"""
                )
                deprioritized = {r["tool_name"] for r in cursor2.fetchall()}

                self._cache = {"tools": data, "deprioritized": deprioritized}
                self._cache_time = time.time()
                return self._cache
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to read tracker data: {e}")
            return {}

    def rank_tools(
        self,
        available_tools: Dict[str, Any],
        query_category: str,
        top_n: int = 20,
    ) -> List[Dict[str, Any]]:
        """根据查询类别和统计数据进行工具推荐排序

        Args:
            available_tools: {name: {doc, level, ...}}
            query_category: 查询类别名称
            top_n: 返回数量上限

        Returns:
            排序后的工具列表，每个元素包含 name, doc, level, priority, score
        """
        tracker_data = self._get_tracker_data()
        tool_stats = tracker_data.get("tools", {})
        deprioritized = tracker_data.get("deprioritized", set())

        # 计算每个工具的评分
        scored_tools = []
        for name, info in available_tools.items():
            # 1. 类别相关性分 (0-100)
            cat_relevance = TOOL_CATEGORY_RELEVANCE.get(name, {})
            relevance_score = cat_relevance.get(query_category, 0.0) * 100

            # 2. 历史成功率分 (0-100)
            stats = tool_stats.get(name, {})
            success_rate = stats.get("success_rate", 1.0)
            success_score = success_rate * 100

            # 3. 优先级基础分 (0-50)
            if name in TOOL_PRIORITY_L1:
                priority_score = 50
            elif name in TOOL_PRIORITY_L2:
                priority_score = 30
            elif name in TOOL_PRIORITY_L3:
                priority_score = 15
            else:
                priority_score = 10

            # 4. 调用频率分 (0-30)
            total_calls = stats.get("total_calls", 0)
            freq_score = min(total_calls / 10, 30)

            # 总分：成功率较重权重，以防止高推荐失败工具
            total_score = (relevance_score * 0.35 +
                           success_score * 0.35 +
                           priority_score * 0.20 +
                           freq_score * 0.10)

            # 降权工具标记（但不完全排除）
            is_deprioritized = name in deprioritized
            if is_deprioritized:
                total_score *= 0.3  # 权重降低 70%

            scored_tools.append({
                "name": name,
                "doc": info.get("doc", ""),
                "level": info.get("level", ""),
                "signature": info.get("signature", ""),
                "score": round(total_score, 1),
                "success_rate": round(success_rate * 100, 1),
                "total_calls": total_calls,
                "avg_latency_ms": stats.get("avg_latency_ms", 0),
                "deprioritized": is_deprioritized,
                "relevance": round(relevance_score, 1),
            })

        # 按总分排序
        scored_tools.sort(key=lambda x: x["score"], reverse=True)

        return scored_tools[:top_n]

    def build_prompt_section(
        self,
        ranked_tools: List[Dict[str, Any]],
        max_tools: int = 15,
        include_low_relevance: bool = True,
    ) -> str:
        """生成提示词中的工具列表区域

        Args:
            ranked_tools: rank_tools() 的返回结果
            max_tools: 最多展示的工具数
            include_low_relevance: 是否包含低相关但可用的工具

        Returns:
            格式化的工具列表文本
        """
        if not ranked_tools:
            return "（无可用工具）"

        lines = []

        # 1. 高推荐工具（评分 >= 40）
        high_recommend = [t for t in ranked_tools if t["score"] >= 40 and not t["deprioritized"]]
        if high_recommend:
            lines.append("【推荐工具】")
            for t in high_recommend[:5]:
                doc = t["doc"][:80] if t["doc"] else "无描述"
                lines.append(f"- {t['name']}{t['signature']}: {doc}")
            lines.append("")

        # 2. 普通工具
        remaining = [t for t in ranked_tools
                     if t["score"] < 40 and not t["deprioritized"]]
        remaining = remaining[:max_tools - len(high_recommend)]

        if remaining:
            lines.append("【可用工具】")
            for t in remaining[:8]:
                doc = t["doc"][:80] if t["doc"] else "无描述"
                lines.append(f"- {t['name']}{t['signature']}: {doc}")
            lines.append("")

        # 3. 降权工具（失败率较高，谨慎使用）
        deprioritized = [t for t in ranked_tools if t["deprioritized"]]
        if deprioritized and include_low_relevance:
            lines.append("【谨慎使用（失败率较高）】")
            for t in deprioritized[:3]:
                lines.append(f"- {t['name']}: 成功率 {t.get('success_rate', 0)}%")

        return "\n".join(lines)

    def get_tool_insights(self) -> Dict[str, Any]:
        """获取工具洞察报告"""
        tracker_data = self._get_tracker_data()
        tool_stats = tracker_data.get("tools", {})
        deprioritized = tracker_data.get("deprioritized", set())

        # 最佳工具（高成功率 + 高频使用）
        best_tools = []
        for name, stats in tool_stats.items():
            if stats["total_calls"] >= 3 and stats["success_rate"] >= 0.9:
                best_tools.append({
                    "name": name,
                    "success_rate": stats["success_rate"],
                    "total_calls": stats["total_calls"],
                    "avg_latency_ms": stats["avg_latency_ms"],
                })
        best_tools.sort(key=lambda x: x["success_rate"], reverse=True)

        return {
            "total_tools_tracked": len(tool_stats),
            "deprioritized_tools": list(deprioritized),
            "best_performing": best_tools[:10],
        }
