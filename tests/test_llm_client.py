"""LLM 客户端测试（Mock，无真实 API 调用）"""
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))

from llm.client import LLMClient
from unittest.mock import patch, MagicMock


class TestLLMClient(unittest.TestCase):
    """LLM 客户端测试"""

    def test_init_with_env_vars(self):
        """从环境变量初始化"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_BASE_URL": "https://test.com/v1"}):
            client = LLMClient()
            self.assertEqual(client.api_key, "test-key")
            self.assertEqual(client.base_url, "https://test.com/v1")

    def test_init_with_explicit_args(self):
        """显式参数优先于环境变量"""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}):
            client = LLMClient(api_key="arg-key", base_url="https://arg.com")
            self.assertEqual(client.api_key, "arg-key")
            self.assertEqual(client.base_url, "https://arg.com")

    def test_init_defaults(self):
        """无参数时使用默认值"""
        client = LLMClient()
        self.assertIsNotNone(client.model)
        self.assertIsInstance(client.model, str)
        self.assertEqual(client.max_tokens, 4096)

    def test_init_custom_model(self):
        """自定义模型"""
        client = LLMClient(model="gpt-4", max_tokens=2048)
        self.assertEqual(client.model, "gpt-4")
        self.assertEqual(client.max_tokens, 2048)

    @patch("llm.client.openai.OpenAI")
    def test_complete_success(self, mock_openai_class):
        """成功调用"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 2
        mock_response.usage.total_tokens = 7
        mock_response.model = "deepseek-v4-pro"

        mock_client_instance = MagicMock()
        mock_client_instance.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client_instance

        client = LLMClient(api_key="test", base_url="https://test.com")
        result = client.complete([{"role": "user", "content": "hi"}])

        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "Hello!")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["total_tokens"], 7)

    @patch("llm.client.openai.OpenAI")
    def test_complete_error(self, mock_openai_class):
        """API 调用错误"""
        mock_client_instance = MagicMock()
        mock_client_instance.chat.completions.create.side_effect = Exception("Connection timeout")
        mock_openai_class.return_value = mock_client_instance

        client = LLMClient(api_key="test")
        result = client.complete([{"role": "user", "content": "hi"}])

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Connection timeout")
        self.assertEqual(result["content"], "")

    @patch("llm.client.openai.OpenAI")
    def test_complete_temperature(self, mock_openai_class):
        """温度参数"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.usage.total_tokens = 2
        mock_client_instance = MagicMock()
        mock_client_instance.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client_instance

        client = LLMClient(api_key="test")
        client.complete([{"role": "user", "content": "hi"}], temperature=0.9)

        call_kwargs = mock_client_instance.chat.completions.create.call_args
        self.assertEqual(call_kwargs.kwargs["temperature"], 0.9)

    @patch("llm.client.openai.OpenAI")
    def test_complete_max_tokens(self, mock_openai_class):
        """max_tokens 参数"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.usage.prompt_tokens = 1
        mock_response.usage.completion_tokens = 1
        mock_response.usage.total_tokens = 2
        mock_client_instance = MagicMock()
        mock_client_instance.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client_instance

        client = LLMClient(api_key="test", max_tokens=2048)
        client.complete([{"role": "user", "content": "hi"}], max_tokens=1024)

        call_kwargs = mock_client_instance.chat.completions.create.call_args
        self.assertEqual(call_kwargs.kwargs["max_tokens"], 1024)

    @patch("llm.client.openai.OpenAI")
    def test_stream_complete(self, mock_openai_class):
        """流式调用返回生成器"""
        mock_stream = MagicMock()
        mock_stream.__iter__ = lambda self: iter([
            MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content=" World"))]),
        ])
        mock_client_instance = MagicMock()
        mock_client_instance.chat.completions.create.return_value = mock_stream
        mock_openai_class.return_value = mock_client_instance

        client = LLMClient(api_key="test")
        gen = client.stream_complete([{"role": "user", "content": "hi"}])

        chunks = list(gen)
        self.assertEqual(len(chunks), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
