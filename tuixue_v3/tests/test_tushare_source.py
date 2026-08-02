#!/usr/bin/env python3
"""
test_tushare_source.py
Ship 2 单元测试 — Tushare Pro 接入 (无 token 降级 + token 注入升级)
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3 import tushare_source
from tuixue_v3.data_source_registry import FetchSource


class TestTokenLoading(unittest.TestCase):
    """token 加载 3 优先级: env > _constants > ~/.tushare_token"""

    def setUp(self):
        # 强制重置模块级缓存
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def tearDown(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def test_no_token_returns_none(self):
        """无 token 任何来源时,降级返 None"""
        with patch.dict(os.environ, {}, clear=True):
            # 屏蔽 ~/.tushare_token 文件
            with patch("os.path.isfile", return_value=False):
                # 屏蔽 web._constants
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(
                    TUSHARE_TOKEN="",
                )}):
                    token = tushare_source._load_token()
                    self.assertIsNone(token)

    def test_env_token_loaded(self):
        """env TUSHARE_TOKEN 优先级最高"""
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "env_token_abc123"}):
            # mock tushare 包导入
            mock_ts = MagicMock()
            with patch.dict(sys.modules, {"tushare": mock_ts}):
                token = tushare_source._load_token()
                self.assertEqual(token, "env_token_abc123")
                mock_ts.set_token.assert_called_once_with("env_token_abc123")


class TestFetchFunctionsGracefulDegradation(unittest.TestCase):
    """无 token 时所有 fetch 返 None,不抛错"""

    def setUp(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def tearDown(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def test_daily_no_token_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.isfile", return_value=False):
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(TUSHARE_TOKEN="")}):
                    result = tushare_source._tushare_daily("600519", days=120)
                    self.assertIsNone(result)

    def test_daily_basic_no_token_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.isfile", return_value=False):
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(TUSHARE_TOKEN="")}):
                    result = tushare_source._tushare_daily_basic("600519")
                    self.assertIsNone(result)

    def test_financial_no_token_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.isfile", return_value=False):
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(TUSHARE_TOKEN="")}):
                    result = tushare_source._tushare_financial("600519")
                    self.assertIsNone(result)


class TestGetSources(unittest.TestCase):
    """get_sources() 返回 3 个 FetchSource 配置正确"""

    def setUp(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def tearDown(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def test_no_token_sources_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.isfile", return_value=False):
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(TUSHARE_TOKEN="")}):
                    sources = tushare_source.get_sources()
                    self.assertEqual(len(sources), 3)
                    for s in sources:
                        self.assertFalse(s.enabled, f"{s.name} 应禁用 (无 token)")

    def test_with_token_sources_enabled(self):
        # 模拟 token 已加载
        tushare_source._tushare_token = "mock_token"
        tushare_source._tushare_pro = MagicMock()
        tushare_source._token_checked = True

        sources = tushare_source.get_sources()
        self.assertEqual(len(sources), 3)
        names = {s.name for s in sources}
        self.assertEqual(names, {"tushare_daily", "tushare_daily_basic", "tushare_financial"})
        for s in sources:
            self.assertTrue(s.enabled, f"{s.name} 应启用 (有 token)")

    def test_categories(self):
        tushare_source._tushare_token = "mock"
        tushare_source._tushare_pro = MagicMock()
        tushare_source._token_checked = True
        sources = tushare_source.get_sources()
        cats = {s.category for s in sources}
        self.assertIn("daily", cats)
        self.assertIn("fundamentals", cats)
        self.assertIn("financial", cats)

    def test_tushare_financial_marked_expensive(self):
        tushare_source._tushare_token = "mock"
        tushare_source._tushare_pro = MagicMock()
        tushare_source._token_checked = True
        sources = tushare_source.get_sources()
        fin = [s for s in sources if s.name == "tushare_financial"][0]
        self.assertIn("expensive", fin.tags)


class TestIsConnected(unittest.TestCase):
    """is_connected() 健康检查"""

    def setUp(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def tearDown(self):
        tushare_source._token_checked = False
        tushare_source._tushare_token = None
        tushare_source._tushare_pro = None

    def test_not_connected_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("os.path.isfile", return_value=False):
                with patch.dict(sys.modules, {"tuixue_v3.web._constants": MagicMock(TUSHARE_TOKEN="")}):
                    self.assertFalse(tushare_source.is_connected())

    def test_connected_with_pro(self):
        tushare_source._tushare_token = "mock"
        tushare_source._tushare_pro = MagicMock()
        tushare_source._token_checked = True
        self.assertTrue(tushare_source.is_connected())


class TestFetchResultValidation(unittest.TestCase):
    """_require_data 校验逻辑"""

    def test_none_rejected(self):
        self.assertFalse(tushare_source._require_data(None))

    def test_empty_df_rejected(self):
        import pandas as pd
        self.assertFalse(tushare_source._require_data(pd.DataFrame()))

    def test_non_empty_df_accepted(self):
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2, 3]})
        self.assertTrue(tushare_source._require_data(df))

    def test_non_iterable_returns_false(self):
        # 非 DataFrame 也非 None — 比如 int
        self.assertFalse(tushare_source._require_data(42))


if __name__ == "__main__":
    unittest.main(verbosity=2)
