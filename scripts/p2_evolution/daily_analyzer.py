#!/usr/bin/env python3
"""
伏羲 P2 自进化脚本 - 每日日志分析 + 失败模式报告

功能：
1. 扫描 logs/fuxi-exec-*.jsonl 和 logs/fuxi-error-*.jsonl
2. 统计失败类型 × 工具 × 频率
3. 识别连续失败模式（某类问题连续出现 3 次 → 生成 Prompt 优化建议）
4. 输出周汇总报告

使用方式：
    python scripts/p2_evolution/daily_analysis.py        # 每日分析
    python scripts/p2_evolution/weekly_summary.py         # 周汇总
"""
import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, Any, List, Optional, Tuple

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("p2_evolution")

# 时区（东八区）
_CHINA_TZ = timezone(timedelta(hours=8))

# 日志目录
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
# 建议输出目录
SUGGESTIONS_DIR = os.path.join(PROJECT_ROOT, "suggestions")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# ── 失败模式分析 ────────────────────────────────────────

class FailureAnalyzer:
    """分析执行日志中的失败模式"""

    def __init__(self, log_dir: str = LOG_DIR):
        self.log_dir = Path(log_dir)

    def scan_error_logs(self, days: int = 1) -> List[Dict[str, Any]]:
        """扫描过去 N 天的错误日志"""
        errors = []
        target_date = datetime.now(_CHINA_TZ)

        for d in range(days):
            dt = target_date - timedelta(days=d)
            date_str = dt.strftime("%Y-%m-%d")
            error_file = self.log_dir / f"fuxi-error-{date_str}.jsonl"

            if not error_file.exists():
                continue

            try:
                with open(error_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            errors.append(entry)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"Failed to read {error_file}: {e}")

        return errors

    def analyze_failure_patterns(self, errors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析失败模式：按 error_type、tool_name、node_type 聚合"""
        by_error_type = Counter()
        by_tool = Counter()
        by_session = Counter()
        consecutive_failures = defaultdict(list)  # session_id → [(timestamp, error), ...]

        for entry in errors:
            error_type = (entry.get("error") or {}).get("type", "unknown")
            tool_name = (entry.get("data") or {}).get("tool_name", "unknown")
            session_id = entry.get("data", {}).get("session_id", "unknown")
            timestamp = entry.get("timestamp", "")

            by_error_type[error_type] += 1
            if tool_name != "unknown":
                by_tool[tool_name] += 1
            by_session[session_id] += 1

            if session_id:
                consecutive_failures[session_id].append((timestamp, error_type))

        return {
            "total_errors": len(errors),
            "by_error_type": dict(by_error_type.most_common(10)),
            "by_tool": dict(by_tool.most_common(10)),
            "by_session": dict(by_session.most_common(5)),
            "consecutive_failures": dict(consecutive_failures),
        }

    def detect_consecutive_patterns(self, errors: List[Dict[str, Any]], threshold: int = 3) -> List[Dict[str, Any]]:
        """检测某类问题连续出现 N 次的模式（触发 Prompt 优化建议）"""
        # 按 query_category 或 error_type 连续计数
        pattern_counts = defaultdict(list)

        for entry in errors:
            node_type = entry.get("node_type", "unknown")
            error_type = (entry.get("error") or {}).get("type", "unknown")
            session_id = entry.get("data", {}).get("session_id", "unknown")
            timestamp = entry.get("timestamp", "")

            key = f"{node_type}:{error_type}"
            pattern_counts[key].append({
                "session_id": session_id,
                "timestamp": timestamp,
                "node_type": node_type,
                "error_type": error_type,
            })

        # 找出连续出现 >= threshold 的模式
        triggers = []
        for pattern, occurrences in pattern_counts.items():
            if len(occurrences) >= threshold:
                node_type = occurrences[0]["node_type"]
                error_type = occurrences[0]["error_type"]
                triggers.append({
                    "pattern": pattern,
                    "count": len(occurrences),
                    "node_type": node_type,
                    "error_type": error_type,
                    "sessions": list(set(o["session_id"] for o in occurrences[:5])),
                    "occurrences": occurrences[:10],  # 保留最近 10 次
                })

        return sorted(triggers, key=lambda x: x["count"], reverse=True)

    def generate_prompt_suggestions(self, triggers: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """根据连续失败模式生成 Prompt 优化建议"""
        suggestions = []
        for trigger in triggers:
            node_type = trigger["node_type"]
            error_type = trigger["error_type"]
            count = trigger["count"]

            if node_type == "llm_call" and error_type in ("llm_error", "timeout"):
                suggestions.append({
                    "type": "llm_timeout",
                    "title": f"LLM 调用超时问题（连续 {count} 次）",
                    "description": f"检测到 LLM 调用连续 {count} 次失败，建议：\n"
                                  f"1. 检查网络连接和 API 可用性\n"
                                  f"2. 考虑增加 timeout 配置\n"
                                  f"3. 审查 system prompt 是否过于复杂",
                    "severity": "high",
                })
            elif node_type == "tool_call":
                tool_failures = [o for o in trigger["occurrences"] if o.get("tool_name")]
                if tool_failures:
                    tool_name = tool_failures[0].get("tool_name", "unknown")
                    suggestions.append({
                        "type": "tool_failure",
                        "title": f"工具 {tool_name} 连续失败（{count} 次）",
                        "description": f"工具 {tool_name} 连续 {count} 次执行失败，建议：\n"
                                      f"1. 检查工具参数格式是否正确\n"
                                      f"2. 审查工具实现是否存在 bug\n"
                                      f"3. 考虑在 system prompt 中补充工具使用示例",
                        "severity": "medium",
                    })

        return suggestions

    def run_daily_analysis(self, output_dir: str = SUGGESTIONS_DIR) -> Dict[str, Any]:
        """执行每日分析"""
        _ensure_dir(output_dir)

        errors = self.scan_error_logs(days=1)
        if not errors:
            logger.info("No error logs found for today")
            return {"status": "no_data", "errors_found": 0}

        patterns = self.analyze_failure_patterns(errors)
        triggers = self.detect_consecutive_patterns(errors, threshold=3)
        suggestions = self.generate_prompt_suggestions(triggers)

        # 生成报告
        report = {
            "date": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d"),
            "total_errors": patterns["total_errors"],
            "patterns": patterns,
            "triggers": triggers,
            "suggestions": suggestions,
        }

        # 保存报告
        report_path = Path(output_dir) / f"failure_report_{datetime.now(_CHINA_TZ).strftime('%Y%m%d')}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 保存 Prompt 优化建议（如果触发）
        if suggestions:
            suggestions_path = Path(output_dir) / f"prompt_suggestions_{datetime.now(_CHINA_TZ).strftime('%Y%m%d')}.json"
            with open(suggestions_path, "w", encoding="utf-8") as f:
                json.dump({
                    "date": report["date"],
                    "triggers": triggers,
                    "suggestions": suggestions,
                }, f, ensure_ascii=False, indent=2)

        logger.info(f"Daily analysis complete: {patterns['total_errors']} errors, {len(triggers)} triggers, {len(suggestions)} suggestions")
        return report


# ── 周汇总 ──────────────────────────────────────────────

class WeeklyAggregator:
    """跨会话日志聚合"""

    def __init__(self, log_dir: str = LOG_DIR):
        self.log_dir = Path(log_dir)

    def scan_exec_logs(self, days: int = 7) -> List[Dict[str, Any]]:
        """扫描过去 N 天的执行日志"""
        entries = []
        target_date = datetime.now(_CHINA_TZ)

        for d in range(days):
            dt = target_date - timedelta(days=d)
            date_str = dt.strftime("%Y-%m-%d")
            exec_file = self.log_dir / f"fuxi-exec-{date_str}.jsonl"

            if not exec_file.exists():
                continue

            try:
                with open(exec_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"Failed to read {exec_file}: {e}")

        return entries

    def aggregate(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """聚合统计数据"""
        if not entries:
            return {"total": 0}

        total = len(entries)
        sessions = set()
        tools_used = Counter()
        llm_latencies = []
        cycle_counts = []
        success_count = 0
        failure_count = 0

        for entry in entries:
            # session_id
            session_id = entry.get("data", {}).get("session_id", "")
            if session_id:
                sessions.add(session_id)

            # tools used
            if entry.get("node_type") == "tool_call":
                tool_name = entry.get("data", {}).get("tool_name", "unknown")
                tools_used[tool_name] += 1

            # llm latency
            if entry.get("node_type") == "llm_call" and entry.get("duration_ms"):
                llm_latencies.append(entry.get("duration_ms", 0))

            # cycle count
            if entry.get("node_type") == "dag_end":
                total_steps = entry.get("data", {}).get("total_steps", 0)
                if total_steps:
                    cycle_counts.append(total_steps)

            # status
            status = entry.get("status", "")
            if status == "success":
                success_count += 1
            elif status in ("failure", "timeout"):
                failure_count += 1

        avg_latency = sum(llm_latencies) / len(llm_latencies) if llm_latencies else 0
        p95_latency = sorted(llm_latencies)[int(len(llm_latencies) * 0.95)] if llm_latencies else 0
        avg_cycles = sum(cycle_counts) / len(cycle_counts) if cycle_counts else 0

        return {
            "total_log_entries": total,
            "unique_sessions": len(sessions),
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_count / total if total > 0 else 0,
            "avg_llm_latency_ms": round(avg_latency, 1),
            "p95_llm_latency_ms": round(p95_latency, 1),
            "avg_cycle_count": round(avg_cycles, 2),
            "top_tools": dict(tools_used.most_common(10)),
        }

    def run_weekly_summary(self, output_dir: str = SUGGESTIONS_DIR) -> Dict[str, Any]:
        """执行周汇总"""
        _ensure_dir(output_dir)

        entries = self.scan_exec_logs(days=7)
        stats = self.aggregate(entries)

        summary = {
            "week_start": (datetime.now(_CHINA_TZ) - timedelta(days=6)).strftime("%Y-%m-%d"),
            "week_end": datetime.now(_CHINA_TZ).strftime("%Y-%m-%d"),
            "stats": stats,
        }

        summary_path = Path(output_dir) / f"weekly_summary_{datetime.now(_CHINA_TZ).strftime('%Y%m%d')}.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        logger.info(f"Weekly summary complete: {stats.get('total_log_entries', 0)} entries, {stats.get('unique_sessions', 0)} sessions")
        return summary


# ── 入口 ───────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="伏羲 P2 自进化脚本")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    p_daily = subparsers.add_parser("daily", help="每日分析（扫描失败日志，生成报告）")
    p_daily.add_argument("--days", type=int, default=1, help="分析天数")

    p_weekly = subparsers.add_parser("weekly", help="周汇总（跨会话聚合）")

    p_full = subparsers.add_parser("full", help="完整运行（daily + weekly）")

    args = parser.parse_args()

    if args.command == "daily" or args.command is None:
        analyzer = FailureAnalyzer()
        result = analyzer.run_daily_analysis()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.command == "weekly":
        aggregator = WeeklyAggregator()
        result = aggregator.run_weekly_summary()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.command == "full":
        analyzer = FailureAnalyzer()
        daily = analyzer.run_daily_analysis()
        aggregator = WeeklyAggregator()
        weekly = aggregator.run_weekly_summary()
        print("=== Daily Analysis ===")
        print(json.dumps(daily, ensure_ascii=False, indent=2))
        print("\n=== Weekly Summary ===")
        print(json.dumps(weekly, ensure_ascii=False, indent=2))