#!/usr/bin/env python3
"""
test_model_adapter.py
Ship 4 单元测试 — ModelAdapter 多模型共存 (MiniMax 主 + DeepSeek 辅 + Qwen 本地兜底)
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3 import model_adapter
from tuixue_v3.model_adapter import (
    MiniMaxAdapter, DeepSeekAdapter, QwenLocalAdapter,
    ModelAdapterRegistry, adapter_registry, bootstrap_default_chain,
    call_with_fallback, call_with_chain,
)


class TestMiniMaxAdapter(unittest.TestCase):
    """MiniMax 默认主"""

    def setUp(self):
        self.adapter = MiniMaxAdapter(api_key="test_key", model="MiniMax-M3")

    def test_available_with_key(self):
        self.assertTrue(self.adapter.is_available())

    def test_unavailable_without_key(self):
        # 严格隔离 env
        with patch.dict(os.environ, {"MINIMAX_API_KEY": ""}, clear=False):
            a = MiniMaxAdapter(api_key="")
            self.assertFalse(a.is_available())

    def test_build_spec(self):
        spec = self.adapter.build_spec(
            system="sys", user="hi", max_tokens=100, name="test_call",
        )
        self.assertEqual(spec.url, MiniMaxAdapter.DEFAULT_URL)
        self.assertEqual(spec.headers["Authorization"], "Bearer test_key")
        self.assertEqual(spec.model, "MiniMax-M3")
        self.assertEqual(spec.name, "minimax_test_call")
        self.assertEqual(spec.body["messages"][0]["content"], "sys")
        self.assertEqual(spec.body["messages"][1]["content"], "hi")
        self.assertEqual(spec.body["max_tokens"], 100)

    def test_model_override(self):
        spec = self.adapter.build_spec(system="s", user="u", model="custom-model")
        self.assertEqual(spec.model, "custom-model")
        self.assertEqual(spec.body["model"], "custom-model")

    def test_env_api_key_loaded(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "env_key"}, clear=False):
            a = MiniMaxAdapter()
            self.assertEqual(a.api_key, "env_key")


class TestDeepSeekAdapter(unittest.TestCase):
    """DeepSeek 辅助"""

    def setUp(self):
        self.adapter = DeepSeekAdapter(api_key="ds_key", model="deepseek-chat")

    def test_available_with_key(self):
        self.assertTrue(self.adapter.is_available())

    def test_unavailable_without_key(self):
        self.assertFalse(DeepSeekAdapter(api_key="").is_available())

    def test_build_spec(self):
        spec = self.adapter.build_spec(system="s", user="u", name="sentiment")
        self.assertEqual(spec.url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(spec.headers["Authorization"], "Bearer ds_key")
        self.assertEqual(spec.name, "deepseek_sentiment")
        self.assertEqual(spec.body["stream"], False)
        self.assertEqual(spec.body["model"], "deepseek-chat")

    def test_timeout_shorter_than_minimax(self):
        """DeepSeek 应该比 MiniMax 快 (专用推理)"""
        ds_spec = self.adapter.build_spec(system="s", user="u")
        mm_spec = MiniMaxAdapter(api_key="k").build_spec(system="s", user="u")
        self.assertLessEqual(ds_spec.timeout, mm_spec.timeout)


class TestQwenLocalAdapter(unittest.TestCase):
    """Qwen 本地 (Ollama) 兜底"""

    def setUp(self):
        self.adapter = QwenLocalAdapter(url="http://localhost:11434/v1/chat/completions")

    def test_build_spec_no_auth_header(self):
        spec = self.adapter.build_spec(system="s", user="u", name="local_test")
        self.assertNotIn("Authorization", spec.headers)
        self.assertEqual(spec.name, "qwen_local_local_test")

    @patch("urllib.request.urlopen")
    def test_available_when_ollama_up(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        self.assertTrue(self.adapter.health_check())

    @patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_unavailable_when_ollama_down(self, mock_urlopen):
        self.assertFalse(self.adapter.health_check())

    def test_is_available_default_true(self):
        """默认乐观假设,避免启动阻塞;真实失败时 ai_client 自动切主备"""
        self.assertTrue(self.adapter.is_available())


class TestRegistry(unittest.TestCase):
    """ModelAdapterRegistry 注册 / 查询 / 切换"""

    def setUp(self):
        # 用独立 registry 测试,避免污染全局
        self.reg = ModelAdapterRegistry()

    def test_register_first_becomes_primary(self):
        a1 = MiniMaxAdapter(api_key="k1")
        self.reg.register(a1)
        self.assertEqual(self.reg._primary, "minimax")
        self.assertEqual(self.reg.primary().name, "minimax")

    def test_register_explicit_primary(self):
        a1 = MiniMaxAdapter(api_key="k1")
        a2 = DeepSeekAdapter(api_key="k2")
        self.reg.register(a1, primary=False)
        self.reg.register(a2, primary=True)
        self.assertEqual(self.reg._primary, "deepseek")

    def test_get(self):
        a = MiniMaxAdapter(api_key="k")
        self.reg.register(a)
        self.assertEqual(self.reg.get("minimax").name, "minimax")
        self.assertIsNone(self.reg.get("nope"))

    def test_fallback_chain_filters_unavailable(self):
        a1 = MiniMaxAdapter(api_key="k1")
        a2 = DeepSeekAdapter(api_key="")  # 不可用
        self.reg.register(a1, primary=True)
        self.reg.register(a2)
        chain = self.reg.fallback_chain()
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].name, "minimax")

    def test_set_primary(self):
        a1 = MiniMaxAdapter(api_key="k1")
        a2 = DeepSeekAdapter(api_key="k2")
        self.reg.register(a1, primary=True)
        self.reg.register(a2)
        self.reg.set_primary("deepseek")
        self.assertEqual(self.reg.primary().name, "deepseek")

    def test_list_all(self):
        a1 = MiniMaxAdapter(api_key="k1")
        a2 = DeepSeekAdapter(api_key="k2")
        self.reg.register(a1)
        self.reg.register(a2)
        self.assertEqual(set(self.reg.list_all()), {"minimax", "deepseek"})


class TestBootstrap(unittest.TestCase):
    """bootstrap_default_chain() 自动装配"""

    def setUp(self):
        # 清空全局 registry
        adapter_registry._adapters.clear()
        adapter_registry._primary = None

    def test_bootstrap_with_minimax_key(self):
        env = {
            "MINIMAX_API_KEY": "k1",
            "DEEPSEEK_API_KEY": "k2",
            "MINIMAX_MODEL": "",
            "DEEPSEEK_MODEL": "",
        }
        with patch.dict(os.environ, env, clear=True):
            bootstrap_default_chain()
            self.assertEqual(adapter_registry._primary, "minimax")
            chain = adapter_registry.fallback_chain()
            self.assertEqual([a.name for a in chain], ["minimax", "deepseek", "qwen_local"])

    def test_bootstrap_without_minimax(self):
        env = {
            "MINIMAX_API_KEY": "",
            "DEEPSEEK_API_KEY": "k2",
            "MINIMAX_MODEL": "",
            "DEEPSEEK_MODEL": "",
        }
        with patch.dict(os.environ, env, clear=True):
            bootstrap_default_chain()
            self.assertEqual(adapter_registry._primary, "deepseek")
            chain = adapter_registry.fallback_chain()
            self.assertEqual([a.name for a in chain], ["deepseek", "qwen_local"])

    def test_bootstrap_idempotent(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "k1"}, clear=False):
            bootstrap_default_chain()
            count1 = len(adapter_registry.list_all())
            bootstrap_default_chain()
            count2 = len(adapter_registry.list_all())
            self.assertEqual(count1, count2)


class TestCallHelpers(unittest.TestCase):
    """call_with_fallback / call_with_chain"""

    def setUp(self):
        adapter_registry._adapters.clear()
        adapter_registry._primary = None
        # 注入 3 个 adapter
        adapter_registry.register(MiniMaxAdapter(api_key="k1"), primary=True)
        adapter_registry.register(DeepSeekAdapter(api_key="k2"))
        adapter_registry.register(QwenLocalAdapter())

    def test_call_with_fallback_returns_primary(self):
        spec = call_with_fallback(system="s", user="u", name="test")
        self.assertEqual(spec.name, "minimax_test")

    def test_call_with_chain_returns_all_available(self):
        specs = call_with_chain(system="s", user="u", name="test")
        names = [s.name for s in specs]
        self.assertEqual(names, ["minimax_test", "deepseek_test", "qwen_local_test"])

    def test_call_with_fallback_no_available_raises(self):
        # 清空 registry 模拟全挂
        adapter_registry._adapters.clear()
        adapter_registry._primary = None
        with self.assertRaises(RuntimeError):
            call_with_fallback(system="s", user="u")


if __name__ == "__main__":
    unittest.main(verbosity=2)
