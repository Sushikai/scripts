#!/usr/bin/env python3
"""
test_multi_market_source.py
Ship 6 单元测试 — 多市场扩展 (港股/北证/ETF)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3 import multi_market_source as mm


class TestDetectMarket(unittest.TestCase):
    """代码 → 市场识别"""

    def test_hk(self):
        self.assertEqual(mm.detect_market("0700"), "hk")  # 腾讯
        self.assertEqual(mm.detect_market("9988"), "hk")  # 阿里
        self.assertEqual(mm.detect_market("12345"), "hk")

    def test_shanghai(self):
        self.assertEqual(mm.detect_market("600519"), "sh")  # 茅台
        self.assertEqual(mm.detect_market("900901"), "sh")  # B 股
        self.assertEqual(mm.detect_market("501018"), "sh")  # ETF

    def test_shenzhen(self):
        self.assertEqual(mm.detect_market("000001"), "sz")  # 平安
        self.assertEqual(mm.detect_market("300750"), "sz")  # 宁德
        self.assertEqual(mm.detect_market("201001"), "sz")  # B 股

    def test_beijing(self):
        self.assertEqual(mm.detect_market("830799"), "bj")  # 北证
        self.assertEqual(mm.detect_market("430001"), "bj")  # 北证
        self.assertEqual(mm.detect_market("870001"), "bj")  # 北证

    def test_etf(self):
        self.assertEqual(mm.detect_market("510500"), "etf")
        self.assertEqual(mm.detect_market("159915"), "etf")
        self.assertEqual(mm.detect_market("560010"), "etf")

    def test_unknown(self):
        self.assertEqual(mm.detect_market(""), "unknown")
        self.assertEqual(mm.detect_market("abc"), "unknown")


class TestRequireQuote(unittest.TestCase):
    """_require_quote 校验"""

    def test_valid(self):
        d = {"market": "hk", "code": "0700", "name": "腾讯", "price": 350.0}
        self.assertTrue(mm._require_quote(d))

    def test_missing_name(self):
        d = {"market": "hk", "code": "0700", "price": 350.0}
        self.assertFalse(mm._require_quote(d))

    def test_zero_price(self):
        d = {"market": "hk", "code": "0700", "name": "x", "price": 0}
        self.assertFalse(mm._require_quote(d))

    def test_non_dict(self):
        self.assertFalse(mm._require_quote(None))
        self.assertFalse(mm._require_quote("string"))


class TestGetSources(unittest.TestCase):
    """get_sources 返回 4 个 FetchSource"""

    def test_count(self):
        sources = mm.get_sources()
        self.assertEqual(len(sources), 4)
        names = {s.name for s in sources}
        self.assertEqual(names, {"hk_yfinance", "hk_tencent", "bj_akshare", "etf_akshare"})

    def test_categories(self):
        sources = mm.get_sources()
        cats = {s.category for s in sources}
        self.assertEqual(cats, {"hk_realtime", "bj_realtime", "etf_realtime"})

    def test_tags(self):
        sources = mm.get_sources()
        for s in sources:
            self.assertIn("free", s.tags)

    def test_priorities_hk(self):
        sources = mm.get_sources()
        hk = [s for s in sources if s.name.startswith("hk_")]
        # yfinance priority 10 < tencent priority 20 (yfinance 优先)
        self.assertEqual(hk[0].priority, 10)
        self.assertEqual(hk[1].priority, 20)


class TestFetchForMarket(unittest.TestCase):
    """fetch_for_market 自动选 category"""

    @patch("tuixue_v3.data_source_registry.fetch_with_registry")
    def test_hk(self, mock_fetch):
        mock_fetch.return_value = MagicMock(data={"price": 350, "market": "hk"})
        result = mm.fetch_for_market("0700")
        mock_fetch.assert_called_once()
        args, kwargs = mock_fetch.call_args
        self.assertEqual(args[0], "hk_realtime")
        self.assertEqual(result["market"], "hk")

    @patch("tuixue_v3.data_source_registry.fetch_with_registry")
    def test_bj(self, mock_fetch):
        mock_fetch.return_value = MagicMock(data={"price": 10, "market": "bj"})
        result = mm.fetch_for_market("830799")
        args, kwargs = mock_fetch.call_args
        self.assertEqual(args[0], "bj_realtime")
        self.assertEqual(result["market"], "bj")

    @patch("tuixue_v3.data_source_registry.fetch_with_registry")
    def test_etf(self, mock_fetch):
        mock_fetch.return_value = MagicMock(data={"price": 1.5, "market": "etf"})
        result = mm.fetch_for_market("510500")
        args, kwargs = mock_fetch.call_args
        self.assertEqual(args[0], "etf_realtime")
        self.assertEqual(result["market"], "etf")

    @patch("tuixue_v3.data_source_registry.fetch_with_registry")
    def test_sh_fallback_a_share(self, mock_fetch):
        """sh/sz 走 A 股 realtime category"""
        mock_fetch.return_value = MagicMock(data={"price": 1700, "market": "sh"})
        mm.fetch_for_market("600519")
        args, kwargs = mock_fetch.call_args
        self.assertEqual(args[0], "realtime")  # A 股复用

    def test_unknown_returns_none(self):
        result = mm.fetch_for_market("abc")
        self.assertIsNone(result)


class TestHkTencentParser(unittest.TestCase):
    """腾讯港股 raw → dict 解析 (mock 网络层)"""

    def test_tencent_hk_success(self):
        # 构造 mock session 注入到 _constants
        mock_session = MagicMock()
        mock_resp = MagicMock()
        # 港股典型 raw: v_rt_hk00700="100~腾讯控股~00700~350.000~345.000~348.000~..."
        raw = "v_rt_hk00700=" + '"100~腾讯控股~00700~350.000~345.000~348.000~1000000~12345~67890~1.2~3.4~5.6~7.8~9.0~1.1~2.2~3.3~4.4~5.5~6.6~7.7~8.8~9.9~1.0~2.0~3.0~4.0~5.0~6.0~7.0~8.0~9.0~+1.20~345.0~355.0~335.0~1000000~12345678~9999.0~+0.123~0.456~12345~2026-08-02~16:00:00~00";'
        mock_resp.content = raw.encode("gbk")
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        import tuixue_v3.web._constants as const
        orig_session = getattr(const, "_FAST_SESSION", None)
        const._FAST_SESSION = mock_session
        try:
            result = mm._hk_tencent("0700")
            self.assertIsNotNone(result)
            self.assertEqual(result["market"], "hk")
            self.assertEqual(result["code"], "00700")  # 5 位补 0
            self.assertEqual(result["name"], "腾讯控股")
            self.assertEqual(result["price"], 350.0)
            self.assertEqual(result["change_pct"], 1.2)
            self.assertEqual(result["source"], "tencent")
        finally:
            if orig_session is not None:
                const._FAST_SESSION = orig_session
            else:
                if hasattr(const, "_FAST_SESSION"):
                    del const._FAST_SESSION

    def test_tencent_hk_no_session(self):
        """_FAST_SESSION 不存在时用 fallback session"""
        import tuixue_v3.web._constants as const
        if hasattr(const, "_FAST_SESSION"):
            del const._FAST_SESSION
        # 不应抛错 (实际网络请求会失败,但函数应 graceful 返 None)
        result = mm._hk_tencent("0700")
        self.assertIsNone(result)  # 网络失败 → None


class TestHkYfinanceErrorHandling(unittest.TestCase):
    """yfinance 失败时不抛错"""

    @patch("tuixue_v3.multi_market_source.logger")
    def test_import_error_returns_none(self, mock_logger):
        # yfinance 未装时 (sys.modules 删除)
        import sys
        original = sys.modules.get("yfinance")
        sys.modules["yfinance"] = None  # 模拟 ImportError
        try:
            result = mm._hk_yfinance("0700")
            self.assertIsNone(result)
        finally:
            if original is not None:
                sys.modules["yfinance"] = original


class TestListSupportedMarkets(unittest.TestCase):
    def test_markets(self):
        markets = mm.list_supported_markets()
        self.assertEqual(set(markets), {"hk", "bj", "etf", "sh", "sz"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
