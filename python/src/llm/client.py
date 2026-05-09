"""LLM 客户端 - 支持任意 OpenAI 兼容 API"""
import os
import openai
from typing import Dict, Any, List, Optional


class DeepSeekClient:
    """通用 OpenAI 兼容 API 客户端

    支持配置：
    - DEEPSEEK_API_KEY: API 密钥
    - DEEPSEEK_BASE_URL: API endpoint
    - DEEPSEEK_MODEL / DEFAULT_MODEL: 模型名称（默认 gpt-4o，可通过环境变量或代码参数覆盖）
    - DEEPSEEK_MAX_TOKENS: 最大 token 数
    """


    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", "")
        # 默认值从环境变量读取，完全不写死
        self.model = (
            model
            or os.environ.get("DEEPSEEK_MODEL", "")
            or os.environ.get("DEFAULT_MODEL", "")
            or "gpt-4o"
        )
        self.max_tokens = max_tokens

        # 懒加载 OpenAI 客户端（避免新版 SDK 在无 key 时初始化报错）
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url if self.base_url else None,
            )
        return self._client

    def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """完成对话
        Args:
            messages: 消息列表，如 [{"role": "user", "content": "..."}]
            temperature: 温度
            max_tokens: 最大 token 数（默认使用客户端配置值）
        Returns:
            响应字典
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or self.max_tokens,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "finish_reason": choice.finish_reason,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": response.model,
                "success": True,
            }
        except Exception as e:
            return {
                "content": "",
                "success": False,
                "error": str(e),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    def stream_complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """流式完成（生成器）"""
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or self.max_tokens,
            stream=True,
        )
        return stream