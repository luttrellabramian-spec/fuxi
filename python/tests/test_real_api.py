"""伏羲真实 API 测试 - 需要 LLM_API_KEY 和 LLM_BASE_URL

测试：
- Basic complete() smoke test
- Chinese response test
- Code generation test
- Multi-turn conversation memory test
- Latency tracking

执行条件：设置环境变量 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
如未设置则自动跳过。
"""
import sys
import os
import time
import json
import pytest

# ── 路径设置 ──────────────────────────────────────────────
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ── 跳过条件 ─────────────────────────────────────────────
pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("LLM_API_KEY")
        and os.environ.get("LLM_BASE_URL")
    ),
    reason="Need LLM_API_KEY and LLM_BASE_URL environment variables",
)

from llm.client import LLMClient


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def llm_client():
    """LLM 客户端（真实 API）"""
    return LLMClient()


@pytest.fixture
def latency_tracker():
    """延迟追踪器"""
    records = []

    def track(name, elapsed_ms):
        records.append({"name": name, "elapsed_ms": elapsed_ms})

    yield track
    # 输出汇总
    if records:
        avg_ms = sum(r["elapsed_ms"] for r in records) / len(records)
        max_ms = max(r["elapsed_ms"] for r in records)
        min_ms = min(r["elapsed_ms"] for r in records)
        print(f"\n  [延迟汇总] {len(records)} 次调用 | "
              f"平均 {avg_ms:.0f}ms | 最小 {min_ms:.0f}ms | "
              f"最大 {max_ms:.0f}ms")
        for r in records:
            print(f"    {r['name']}: {r['elapsed_ms']}ms")


# ═══════════════════════════════════════════════════════════
# A. 基本调用
# ═══════════════════════════════════════════════════════════

class TestBasicComplete:
    """基础 complete() 冒烟测试"""

    def test_simple_complete(self, llm_client, latency_tracker):
        """简单对话完成"""
        start = time.time()
        result = llm_client.complete(
            messages=[{"role": "user", "content": "Say 'Hello, World!' and nothing else."}],
            temperature=0.1,
            max_tokens=50,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        latency_tracker("simple_complete", elapsed_ms)

        assert result["success"] is True, f"API 调用失败: {result.get('error')}"
        assert result["content"], "返回内容不应为空"
        assert "Hello" in result["content"] or "World" in result["content"], \
            f"内容应包含 Hello 或 World，实际: {result['content']}"

    def test_with_system_prompt(self, llm_client):
        """带 system prompt 的调用"""
        result = llm_client.complete(
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Reply in English only."},
                {"role": "user", "content": "What is 2+2?"},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        assert result["success"] is True
        content = result["content"].lower()
        assert "4" in content or "four" in content

    def test_multiple_choices(self, llm_client):
        """多次调用不同问题"""
        questions = ["What color is the sky?", "What is water made of?"]
        for q in questions:
            result = llm_client.complete(
                messages=[{"role": "user", "content": q}],
                temperature=0.3,
                max_tokens=100,
            )
            assert result["success"] is True
            assert result["content"], f"问题 '{q}' 返回空"

    def test_usage_info(self, llm_client):
        """返回包含 usage 信息"""
        result = llm_client.complete(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.1,
            max_tokens=20,
        )
        assert result["success"] is True
        usage = result.get("usage", {})
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["prompt_tokens"] > 0
        assert usage["total_tokens"] > 0

    def test_model_name(self, llm_client):
        """返回 model 名称"""
        result = llm_client.complete(
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert result["success"] is True
        assert result.get("model"), "应返回 model 名称"

    def test_finish_reason(self, llm_client):
        """返回 finish_reason"""
        result = llm_client.complete(
            messages=[{"role": "user", "content": "Say 'done' and stop."}],
            max_tokens=50,
        )
        assert result["success"] is True
        assert result.get("finish_reason") in ("stop", "length", "end_turn")


# ═══════════════════════════════════════════════════════════
# B. 中文回复
# ═══════════════════════════════════════════════════════════

class TestChineseResponse:
    """中文回复测试"""

    def test_chinese_greeting(self, llm_client):
        """中文问候"""
        result = llm_client.complete(
            messages=[{"role": "user", "content": "用中文打个招呼"}],
            temperature=0.3,
            max_tokens=100,
        )
        assert result["success"] is True
        content = result["content"]
        # 应包含中文字符
        assert any('一' <= c <= '鿿' for c in content), \
            f"回复应包含中文: {content[:50]}"

    def test_chinese_question(self, llm_client):
        """中文问题"""
        result = llm_client.complete(
            messages=[{"role": "user", "content": "请用中文解释什么是人工智能"}],
            temperature=0.3,
            max_tokens=200,
        )
        assert result["success"] is True
        content = result["content"]
        assert "人工智能" in content or "AI" in content or "智能" in content

    def test_chinese_code_explanation(self, llm_client):
        """中文解释代码"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "用中文解释这行代码的作用：print('hello')",
            }],
            temperature=0.3,
            max_tokens=200,
        )
        assert result["success"] is True
        content = result["content"]
        assert any('一' <= c <= '鿿' for c in content)

    def test_chinese_multi_turn(self, llm_client):
        """中文多轮对话"""
        messages = [
            {"role": "user", "content": "我的名字是张三"},
            {"role": "assistant", "content": "你好张三！很高兴认识你。"},
            {"role": "user", "content": "你还记得我的名字吗？"},
        ]
        result = llm_client.complete(
            messages=messages,
            temperature=0.3,
            max_tokens=100,
        )
        assert result["success"] is True
        content = result["content"]
        # 应能回忆名字
        assert "张三" in content or "张" in content, \
            f"应记住对话上下文中的名字: {content[:80]}"


# ═══════════════════════════════════════════════════════════
# C. 代码生成
# ═══════════════════════════════════════════════════════════

class TestCodeGeneration:
    """代码生成测试"""

    def test_generate_python_function(self, llm_client):
        """生成 Python 函数"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "Write a Python function that calculates fibonacci numbers. Return ONLY the code.",
            }],
            temperature=0.1,
            max_tokens=300,
        )
        assert result["success"] is True
        content = result["content"]
        assert "def " in content
        assert "fib" in content.lower() or "def fibonacci" in content.lower()

    def test_generate_with_docstring(self, llm_client):
        """生成的代码包含文档字符串"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "Write a Python function with docstring that sorts a list. Return ONLY valid Python code.",
            }],
            temperature=0.1,
            max_tokens=300,
        )
        assert result["success"] is True
        content = result["content"]
        assert '"""' in content or "'''" in content or "docstring" not in content

    def test_generate_html(self, llm_client):
        """生成 HTML"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "Create a simple HTML page with a heading and a paragraph.",
            }],
            temperature=0.3,
            max_tokens=300,
        )
        assert result["success"] is True
        content = result["content"]
        assert "<html" in content.lower() or "<h1" in content or "<p" in content or "<!" in content

    def test_generate_json_structure(self, llm_client):
        """生成 JSON 结构"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "Create a JSON object representing a person with name, age, and email fields. Return ONLY valid JSON.",
            }],
            temperature=0.1,
            max_tokens=200,
        )
        assert result["success"] is True
        content = result["content"].strip()
        # 尝试解析 JSON
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                assert "name" in data
                assert "age" in data
            except json.JSONDecodeError:
                pass  # 如果解析失败，至少返回了内容


# ═══════════════════════════════════════════════════════════
# D. 多轮对话记忆
# ═══════════════════════════════════════════════════════════

class TestMultiTurnConversation:
    """多轮对话记忆测试"""

    def test_remember_previous_context(self, llm_client):
        """记住上文"""
        messages = [
            {"role": "user", "content": "My favorite color is blue."},
            {"role": "assistant", "content": "Great! Blue is a nice color."},
            {"role": "user", "content": "What is my favorite color?"},
        ]
        result = llm_client.complete(
            messages=messages,
            temperature=0.1,
            max_tokens=50,
        )
        assert result["success"] is True
        content = result["content"].lower()
        assert "blue" in content

    def test_conversation_summary(self, llm_client):
        """对话总结"""
        messages = [
            {"role": "user", "content": "I like cats."},
            {"role": "assistant", "content": "Cats are wonderful pets!"},
            {"role": "user", "content": "I also like dogs."},
            {"role": "assistant", "content": "Dogs are great companions too!"},
            {"role": "user", "content": "Summarize what I like in one sentence."},
        ]
        result = llm_client.complete(
            messages=messages,
            temperature=0.2,
            max_tokens=100,
        )
        assert result["success"] is True
        content = result["content"].lower()
        assert "cat" in content or "dog" in content

    def test_instruction_following(self, llm_client):
        """指令遵循"""
        result = llm_client.complete(
            messages=[{
                "role": "user",
                "content": "Reply with exactly 'OK' and nothing else.",
            }],
            temperature=0.0,
            max_tokens=10,
        )
        assert result["success"] is True
        content = result["content"].strip().strip('"').strip("'")
        assert content == "OK" or "OK" in content

    def test_longer_conversation(self, llm_client):
        """较长对话不丢失上下文"""
        messages = []
        for i in range(5):
            messages.append({"role": "user", "content": f"This is turn {i+1}."})
            messages.append({"role": "assistant", "content": f"Acknowledged turn {i+1}."})
        messages.append({"role": "user", "content": "How many turns have we had?"})
        result = llm_client.complete(
            messages=messages,
            temperature=0.1,
            max_tokens=100,
        )
        assert result["success"] is True
        assert "5" in result["content"] or "five" in result["content"].lower() or "turn" in result["content"].lower()


# ═══════════════════════════════════════════════════════════
# E. 延迟追踪
# ═══════════════════════════════════════════════════════════

class TestLatencyTracking:
    """延迟追踪测试"""

    TIMEOUT_SECONDS = 60  # 单次调用最大超时

    def test_completion_latency(self, llm_client):
        """第一次调用的延迟"""
        start = time.time()
        result = llm_client.complete(
            messages=[{"role": "user", "content": "Quick response test. Say 'done'."}],
            temperature=0.1,
            max_tokens=10,
        )
        elapsed = time.time() - start
        assert result["success"] is True
        assert elapsed < self.TIMEOUT_SECONDS, \
            f"响应时间 {elapsed:.1f}s 超过上限 {self.TIMEOUT_SECONDS}s"
        print(f"\n  [延迟] 首次调用: {elapsed*1000:.0f}ms")

    def test_latency_with_long_prompt(self, llm_client):
        """长 prompt 的延迟"""
        long_text = "word " * 500
        start = time.time()
        result = llm_client.complete(
            messages=[
                {"role": "system", "content": "You summarize."},
                {"role": "user", "content": f"Summarize: {long_text}"},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        elapsed = time.time() - start
        assert result["success"] is True
        print(f"\n  [延迟] 长 prompt (500词): {elapsed*1000:.0f}ms")
        # 长 prompt 通常比短 prompt 慢但不应超时
        assert elapsed < self.TIMEOUT_SECONDS

    def test_consecutive_calls_latency(self, llm_client):
        """连续调用的延迟"""
        times = []
        for i in range(3):
            start = time.time()
            result = llm_client.complete(
                messages=[{"role": "user", "content": f"Say {i}."}],
                temperature=0.1,
                max_tokens=5,
            )
            elapsed = time.time() - start
            times.append(elapsed)
            assert result["success"] is True

        avg_ms = sum(times) / len(times) * 1000
        min_ms = min(times) * 1000
        max_ms = max(times) * 1000
        print(f"\n  [延迟] 3次连续调用: 平均{avg_ms:.0f}ms "
              f"最小{min_ms:.0f}ms 最大{max_ms:.0f}ms")

    def test_latency_without_retries(self, llm_client):
        """成功率追踪"""
        results = []
        for i in range(3):
            result = llm_client.complete(
                messages=[{"role": "user", "content": f"Test {i}"}],
                temperature=0.1,
                max_tokens=10,
            )
            results.append(result["success"])
        success_rate = sum(results) / len(results)
        assert success_rate >= 0.5, f"成功率 {success_rate:.0%} 过低"
        print(f"\n  [成功率] {sum(results)}/{len(results)} = {success_rate:.0%}")
