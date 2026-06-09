from __future__ import annotations

"""查询分类器 - 轻量级查询意图分类

将用户查询分类为不同类别，供进化层其他模块选择优化策略。
不依赖外部 ML 库，使用关键词模式匹配 + 启发式规则。

分类列表：
- simple_qa: 简单问答（无需工具）
- code_gen: 代码生成
- file_operation: 文件操作
- search_query: 搜索类
- memory_query: 记忆查询
- multi_step_task: 多步复杂任务
- analysis: 分析/总结类
- unknown: 无法分类
"""
import re
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class QueryCategory:
    """查询类别"""
    name: str
    label_cn: str
    complexity: int  # 1-5, 1=最简单
    recommended_steps: int  # 推荐最大 ReAct 步数
    recommended_temp: float  # 建议温度


# 预定义查询类别
QUERY_CATEGORIES = {
    "simple_qa": QueryCategory("simple_qa", "简单问答", 1, 3, 0.1),
    "code_gen": QueryCategory("code_gen", "代码生成", 3, 6, 0.3),
    "file_operation": QueryCategory("file_operation", "文件操作", 2, 5, 0.2),
    "search_query": QueryCategory("search_query", "搜索查询", 2, 5, 0.15),
    "memory_query": QueryCategory("memory_query", "记忆查询", 2, 4, 0.1),
    "multi_step_task": QueryCategory("multi_step_task", "多步复杂任务", 4, 10, 0.25),
    "analysis": QueryCategory("analysis", "分析总结", 3, 6, 0.3),
    "unknown": QueryCategory("unknown", "未分类", 2, 6, 0.2),
}


class QueryClassifier:
    """查询分类器（轻量级，关键词+模式匹配）"""

    # 各类别的关键词模式
    _PATTERNS: Dict[str, List[str]] = {
        "code_gen": [
            r"写(个|一).*(函数|代码|程序|脚本|类|算法)",
            r"实现(一个|个)?(函数|算法|功能)",
            r"生成.*(代码|脚本|SQL|HTML|CSS|JS|文件)",
            r"编写.*(测试|接口|API|代码|函数)",
            r"如何.*实现",
            r"用.*(语言|框架).*实现",
            r"(代码|程序).*(问题|bug|错误|优化|重构)",
            r"重构.*(代码|函数|模块)",
            r"\b(write|create|implement|generate|build|develop)\b.*\b(function|code|script|program|class|algorithm|api)\b",
            r"\b(how to|refactor|optimize)\b.*\b(code|function|program)\b",
        ],
        "file_operation": [
            r"(读取|写入|创建|删除|移动|复制).*(文件|目录|文件夹)",
            r"列出.*(文件|目录)",
            r"搜索.*(文件|内容)",
            r"文件.*(存在|大小|修改)",
            r"(读|写)\s*(JSON|文本|文件)",
            r"读取\s+\S+",
            r"(文件|目录|路径).*(操作|处理|管理)",
            r"\b(read|write|delete|copy|move|list)\b.*\b(file|directory|folder|path)\b",
        ],
        "search_query": [
            r"搜索.*(信息|资料|文档|内容)",
            r"查找.*(关于|有关)",
            r"查(一下|询)",
            r"搜索",
            r"找(一下|找)",
            r"\b(search|find|look up|lookup)\b",
        ],
        "memory_query": [
            r"(记(不)?|还)?记得.*(吗|么)",
            r"之前.*(说过|提到|讨论|问过)",
            r"历史.*(对话|记录|消息)",
            r"查(看|询).*(记忆|历史|记录)",
            r"回忆.*(内容|对话)",
            r"\b(remember|recall|memory|previous|before)\b",
            r"what (did|was).*(say|mention|talk)",
        ],
        "multi_step_task": [
            r"先.*(然后|再).*",
            r"第一步.*第二步",
            r"(同时|并且).*(和|以及)",
            r"涉及.*多个.*(步骤|文件|模块)",
            r"流程.*(设计|实现)",
            r"自动化.*(工作流|流程)",
            r"(first|step 1).*(then|next|step 2)",
            r"\b(multi[-\s]?step|workflow|pipeline|automation)\b",
        ],
        "analysis": [
            r"(分析|总结|概括|归纳|汇总)",
            r"比较.*(和|与|区别|异同)",
            r"优(化|化)$",
            r"评估.*(性能|质量|效果)",
            r"从.*(提取|解析|整理)",
            r"\b(analyze|summarize|compare|evaluate|optimize)\b",
        ],
    }

    # 简单问答关键词（简短的消息通常不需要工具）
    _SIMPLE_PATTERNS = [
        r"^(你好|嗨|Hi|Hello|早上好|下午好|晚上好)[\s!！。.]*$",
        r"^(是|否|好|可以|不行|谢谢|感谢|明白了|懂了)[\s!！。.]*$",
        r"^(hi|hello|hey|thanks|thank you|good|bye|ok|okay)[\s!.']*$",
        r"天气|时间|日期|星期几",            # 简单查询（非工具场景）
    ]

    def classify(self, message: str, history_hint: Optional[str] = None) -> QueryCategory:
        """对用户消息进行分类

        Args:
            message: 用户消息
            history_hint: 可选的历史上下文提示

        Returns:
            分类后的 QueryCategory
        """
        if not message or not message.strip():
            return QUERY_CATEGORIES["unknown"]

        msg_lower = message.lower().strip()

        # 1. 检查是否简单问答
        for pat in self._SIMPLE_PATTERNS:
            if re.search(pat, msg_lower):
                return QUERY_CATEGORIES["simple_qa"]

        # 2. 按优先级检查各类别（multi_step_task 优先级最高，避免被其他类别抢先）
        for cat_name in ["multi_step_task", "code_gen", "file_operation",
                         "search_query", "analysis", "memory_query"]:
            patterns = self._PATTERNS.get(cat_name, [])
            for pat in patterns:
                if re.search(pat, msg_lower):
                    return QUERY_CATEGORIES[cat_name]

        # 3. 基于消息长度启发式判断
        char_count = len(message)
        word_count = len(message.split())

        if char_count > 100 or word_count > 30:
            return QUERY_CATEGORIES["multi_step_task"]
        elif char_count > 30:
            return QUERY_CATEGORIES["analysis"]

        return QUERY_CATEGORIES["unknown"]

    def get_all_categories(self) -> Dict[str, QueryCategory]:
        """返回所有可用的查询类别"""
        return QUERY_CATEGORIES
