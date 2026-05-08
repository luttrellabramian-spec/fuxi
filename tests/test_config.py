"""配置加载测试"""
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python", "src"))


class TestConfig(unittest.TestCase):
    """配置加载测试"""

    def _resolve_path(self, *segments: str) -> str:
        """从 fuxi_v0.1.0/tests/ 出发，定位同根目录下的文件"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, *segments)

    def test_grpc_server_default_port(self):
        """gRPC 服务默认端口"""
        from grpc_server import DEFAULT_PORT
        self.assertEqual(DEFAULT_PORT, 50051)

    def test_llm_client_default_model(self):
        """LLM 客户端默认模型"""
        from llm.client import LLMClient
        client = LLMClient()
        self.assertIsNotNone(client.model)
        self.assertIsInstance(client.model, str)

    def test_llm_client_env_override(self):
        """环境变量覆盖"""
        with unittest.mock.patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "env-key",
            "DEEPSEEK_BASE_URL": "https://env.com",
            "DEEPSEEK_MODEL": "env-model",
        }):
            from llm.client import LLMClient
            # 需要重新导入以获取 env 值
            import importlib
            import llm.client
            importlib.reload(llm.client)
            client = llm.client.LLMClient()
            self.assertEqual(client.api_key, "env-key")
            self.assertEqual(client.base_url, "https://env.com")
            importlib.reload(llm.client)

    def test_default_yaml_exists(self):
        """default.yaml 存在"""
        yaml_path = self._resolve_path("config", "default.yaml")
        self.assertTrue(os.path.exists(yaml_path), f"default.yaml not found at {yaml_path}")

    def test_yaml_config_structure(self):
        """YAML 配置结构"""
        import yaml
        yaml_path = self._resolve_path("config", "default.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertIn("llm", config)
        self.assertIn("grpc", config)
        self.assertIn("gateway", config)
        self.assertIn("rate_limit", config)
        self.assertIn("memory", config)
        self.assertIn("engine", config)

    def test_yaml_grpc_port(self):
        """YAML 中 gRPC 端口"""
        import yaml
        yaml_path = self._resolve_path("config", "default.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config["grpc"]["port"], 50051)

    def test_yaml_gateway_port(self):
        """YAML 中网关端口"""
        import yaml
        yaml_path = self._resolve_path("config", "default.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config["gateway"]["port"], 18789)

    def test_yaml_memory_config(self):
        """YAML 记忆层配置"""
        import yaml
        yaml_path = self._resolve_path("config", "default.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config["memory"]["hot"]["char_limit"], 2200)
        self.assertEqual(config["memory"]["warm"]["max_messages"], 50)

    def test_yaml_engine_config(self):
        """YAML 引擎配置"""
        import yaml
        yaml_path = self._resolve_path("config", "default.yaml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.assertEqual(config["engine"]["max_steps"], 10)
        self.assertEqual(config["engine"]["timeout"], 60)

    def test_typescript_config_export(self):
        """TypeScript 配置导出存在"""
        ts_config_path = self._resolve_path("typescript", "src", "config.ts")
        self.assertTrue(os.path.exists(ts_config_path))
        with open(ts_config_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("grpcPort", content)
        self.assertIn("httpPort", content)
        self.assertIn("DEEPSEEK_API_KEY", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
