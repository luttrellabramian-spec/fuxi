"""伏羲引擎 ReAct 循环测试（Mock LLM）"""
import unittest
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from engine.fuxi_engine import FuxiEngine
from unittest.mock import patch, MagicMock


class TestFuxiEngine(unittest.TestCase):
    """FuxiEngine ReAct 循环测试"""

    def test_engine_init(self):
        """引擎初始化"""
        engine = FuxiEngine(
            deepseek_key="test-key",
            base_url="https://test.com",
            max_steps=5,
        )
        self.assertEqual(engine.max_steps, 5)
        self.assertIsNotNone(engine.tool_registry)

    def test_engine_init_defaults(self):
        """默认参数"""
        engine = FuxiEngine()
        self.assertEqual(engine.max_steps, 10)

    def test_get_system_prompt(self):
        """系统提示词生成"""
        engine = FuxiEngine()
        prompt = engine._get_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("伏羲", prompt)
        self.assertIn("ReAct", prompt)

    def test_parse_one_action_success(self):
        """动作解析 - 成功"""
        engine = FuxiEngine()

        # Action 格式
        content = 'Think: 检查当前目录\nAction: list_files({"directory": "."})'
        action = engine._parse_one_action(content)
        self.assertIsNotNone(action)
        self.assertEqual(action["tool"], "list_files")
        self.assertEqual(action["arguments"], {"directory": "."})

        # 行动: 格式（中文）
        content2 = '思考：需要先查看目录\n行动: read_file({"path": "test.py"})'
        action2 = engine._parse_one_action(content2)
        self.assertIsNotNone(action2)
        self.assertEqual(action2["tool"], "read_file")

    def test_parse_one_action_no_action(self):
        """动作解析 - 无动作时返回 None"""
        engine = FuxiEngine()
        content = "这只是普通回复，没有动作"
        action = engine._parse_one_action(content)
        self.assertIsNone(action)

    def test_parse_final_success(self):
        """最终答案解析 - 成功"""
        engine = FuxiEngine()

        content = '好的，我来回答你的问题。Final: 答案是42'
        final = engine._parse_final(content)
        self.assertEqual(final, "答案是42")

        # 最终答案: 格式
        content2 = '最终答案: Python 是一种编程语言'
        final2 = engine._parse_final(content2)
        self.assertEqual(final2, "Python 是一种编程语言")

        # 最终: 格式
        content3 = '最终: 测试通过'
        final3 = engine._parse_final(content3)
        self.assertEqual(final3, "测试通过")

    def test_parse_final_no_final(self):
        """最终答案解析 - 无最终答案"""
        engine = FuxiEngine()
        content = "还在思考中..."
        final = engine._parse_final(content)
        self.assertIsNone(final)

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_final_answer(self, mock_client_class):
        """ReAct 循环 - 直接返回最终答案"""
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "success": True,
            "content": "Final: 42",
            "usage": {"total_tokens": 100},
        }
        mock_client_class.return_value = mock_client

        engine = FuxiEngine()
        result = engine.run("1+1等于几？", session_id="test")

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "42")
        self.assertEqual(result["total_steps"], 0)
        self.assertEqual(result["usage"]["total_tokens"], 100)

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_single_tool_call(self, mock_client_class):
        """ReAct 循环 - 单步工具调用"""
        mock_client = MagicMock()

        # 第一步：返回动作
        mock_client.complete.side_effect = [
            {
                "success": True,
                "content": 'Think: 需要检查文件\nAction: file_exists({"path": "test.py"})',
                "usage": {"total_tokens": 50},
            },
            {
                "success": True,
                "content": "Final: 文件存在",
                "usage": {"total_tokens": 80},
            },
        ]

        mock_client_class.return_value = mock_client

        engine = FuxiEngine()
        result = engine.run("test.py 存在吗？", session_id="test")

        self.assertTrue(result["success"])
        self.assertEqual(result["total_steps"], 1)
        self.assertEqual(result["steps"][0]["action"]["tool"], "file_exists")
        self.assertIn("false", result["observations"][0]["result"])

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_max_steps_limit(self, mock_client_class):
        """ReAct 循环 - 达到最大步数限制"""
        mock_client = MagicMock()

        # 始终返回动作，不返回 Final（直到超限）
        def always_action(*args, **kwargs):
            return {
                "success": True,
                "content": 'Action: file_exists({"path": "x"})',
                "usage": {"total_tokens": 50},
            }

        mock_client.complete.side_effect = always_action
        mock_client_class.return_value = mock_client

        engine = FuxiEngine(max_steps=3)
        result = engine.run("repeat test", session_id="test")

        self.assertTrue(result["success"])
        self.assertLessEqual(result["total_steps"], 3)

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_llm_error(self, mock_client_class):
        """ReAct 循环 - LLM 调用失败"""
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "success": False,
            "error": "Connection refused",
        }
        mock_client_class.return_value = mock_client

        engine = FuxiEngine()
        result = engine.run("hello", session_id="test")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Connection refused")

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_unknown_tool(self, mock_client_class):
        """ReAct 循环 - 调用不存在的工具"""
        mock_client = MagicMock()

        mock_client.complete.side_effect = [
            {
                "success": True,
                "content": 'Action: nonexistent_tool({"arg": 1})',
                "usage": {"total_tokens": 50},
            },
            {
                "success": True,
                "content": "Final: 工具不存在",
                "usage": {"total_tokens": 80},
            },
        ]

        mock_client_class.return_value = mock_client

        engine = FuxiEngine()
        result = engine.run("call nonexistent tool", session_id="test")

        self.assertTrue(result["success"])
        # 工具不存在，但 ReAct 循环继续直到给出 Final
        self.assertGreaterEqual(result["total_steps"], 1)

    @patch("engine.fuxi_engine.LLMClient")
    def test_run_elapsed_time_recorded(self, mock_client_class):
        """运行时间被记录"""
        mock_client = MagicMock()
        mock_client.complete.return_value = {
            "success": True,
            "content": "Final: done",
            "usage": {"total_tokens": 10},
        }
        mock_client_class.return_value = mock_client

        engine = FuxiEngine()
        result = engine.run("quick test", session_id="test")

        self.assertIn("elapsed", result)
        self.assertIsInstance(result["elapsed"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
