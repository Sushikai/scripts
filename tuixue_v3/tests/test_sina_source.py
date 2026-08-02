#!/usr/bin/env python3
"""
test_sina_source.py
Ship 3 单元测试 — 新浪 hq.sinajs (HTTPS+Referer) 兜底接入
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3 import sina_source


class TestSymbolMapping(unittest.TestCase):
    """A 股代码 → 新浪 symbol"""

    def test_shanghai(self):
        self.assertEqual(sina_source._to_sina_symbol("600519"), "sh600519")
        self.assertEqual(sina_source._to_sina_symbol("900901"), "sh900901")

    def test_shenzhen(self):
        self.assertEqual(sina_source._to_sina_symbol("000001"), "sz000001")
        self.assertEqual(sina_source._to_sina_symbol("300750"), "sz300750")
        self.assertEqual(sina_source._to_sina_symbol("201001"), "sz201001")

    def test_beijing(self):
        self.assertEqual(sina_source._to_sina_symbol("830799"), "bj830799")
        self.assertEqual(sina_source._to_sina_symbol("430001"), "bj430001")

    def test_fallback(self):
        # 未知前缀 fallback sh
        self.assertEqual(sina_source._to_sina_symbol("999999"), "sh999999")


class TestParser(unittest.TestCase):
    """新浪 hq.sinajs 返回解析"""

    def test_parse_valid(self):
        raw = 'var hq_str_sh600519="大秦铁路,27.55,27.25,26.91,27.55,26.20,26.21,12345,67890,0,0,1234567,800000,300000,200000,100000,50000,30000,20000,10000,5000,3000,2000,1000,500,300,200,100,50,30,20,2026-08-02,15:00:00,00";'
        result = sina_source._parse_sina_hq("sh600519", raw)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "大秦铁路")
        self.assertEqual(result["price"], 26.91)
        self.assertEqual(result["open"], 27.55)
        self.assertEqual(result["prev_close"], 27.25)
        self.assertEqual(result["high"], 27.55)
        self.assertEqual(result["low"], 26.20)
        self.assertAlmostEqual(result["change_pct"], -1.25, places=2)
        self.assertEqual(result["date"], "2026-08-02")
        self.assertEqual(result["time"], "15:00:00")

    def test_parse_missing_symbol(self):
        raw = 'var hq_str_sz000001="...";'  # 不同 symbol
        result = sina_source._parse_sina_hq("sh600519", raw)
        self.assertIsNone(result)

    def test_parse_empty_data(self):
        raw = 'var hq_str_sh600519="";'
        result = sina_source._parse_sina_hq("sh600519", raw)
        self.assertIsNone(result)

    def test_parse_malformed(self):
        raw = 'garbage'
        result = sina_source._parse_sina_hq("sh600519", raw)
        self.assertIsNone(result)

    def test_parse_too_few_fields(self):
        raw = 'var hq_str_sh600519="a,b,c";'
        result = sina_source._parse_sina_hq("sh600519", raw)
        self.assertIsNone(result)


class TestRequireQuote(unittest.TestCase):
    """_require_quote 校验"""

    def test_valid_quote(self):
        d = {"name": "x", "price": 10.0}
        self.assertTrue(sina_source._require_quote(d))

    def test_missing_name(self):
        d = {"name": "", "price": 10.0}
        self.assertFalse(sina_source._require_quote(d))

    def test_missing_price(self):
        d = {"name": "x", "price": None}
        self.assertFalse(sina_source._require_quote(d))

    def test_non_dict(self):
        self.assertFalse(sina_source._require_quote("string"))
        self.assertFalse(sina_source._require_quote(None))
        self.assertFalse(sina_source._require_quote([1, 2]))


class TestSessionHeaders(unittest.TestCase):
    """Session 必须带 Referer + UA"""

    def test_referer_set(self):
        # 强制重置单例
        sina_source._session = None
        s = sina_source._get_session()
        self.assertEqual(s.headers.get("Referer"), sina_source.SINA_REFERER)
        self.assertIn("User-Agent", s.headers)


class TestRealtimeFetch(unittest.TestCase):
    """_sina_realtime_quote 网络层 mock"""

    @patch("tuixue_v3.sina_source._get_session")
    def test_fetch_success(self, mock_get_session):
        # 用 unicode_escape 编码模拟 GBK → UTF-8 decode 链路 (新浪 GBK)
        gb = b'var hq_str_sh600519="\xd4\xc6\xc7\xea\xc1\xfa\xc2\xb7,27.55,27.25,26.91,27.55,26.20,26.21,12345,67890,0,0,1234567,800000,300000,200000,100000,50000,30000,20000,10000,5000,3000,2000,1000,500,300,200,100,50,30,20,2026-08-02,15:00:00,00";'
        mock_resp = MagicMock()
        mock_resp.content = gb
        mock_resp.raise_for_status = MagicMock()
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_get_session.return_value = mock_session

        result = sina_source._sina_realtime_quote("600519")
        self.assertIsNotNone(result)
        self.assertEqual(result["price"], 26.91)
        # 验证超时配置
        call_kwargs = mock_session.get.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], (1.5, 3.0))

    @patch("tuixue_v3.sina_source._get_session")
    def test_fetch_403_returns_none(self, mock_get_session):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=req.exceptions.HTTPError(response=MagicMock(status_code=403))
        )
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_get_session.return_value = mock_session

        result = sina_source._sina_realtime_quote("600519")
        self.assertIsNone(result)


class TestGetSources(unittest.TestCase):
    """get_sources() 返回 2 个 FetchSource"""

    def test_returns_two_sources(self):
        sources = sina_source.get_sources()
        self.assertEqual(len(sources), 2)
        names = {s.name for s in sources}
        self.assertEqual(names, {"sina_realtime_hq", "sina_realtime_batch"})

    def test_categories(self):
        sources = sina_source.get_sources()
        cats = {s.category for s in sources}
        self.assertIn("realtime", cats)
        self.assertIn("realtime_batch", cats)

    def test_referer_tag_present(self):
        sources = sina_source.get_sources()
        for s in sources:
            self.assertIn("https-required", s.tags)
            self.assertIn("free", s.tags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
