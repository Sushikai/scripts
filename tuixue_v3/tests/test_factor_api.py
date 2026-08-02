#!/usr/bin/env python3
"""
test_factor_api.py
Ship 12 单元测试 — 因子 API router 构造 (mock 上游 fetch)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.web.factor_api import build_router, _build_factor_score, _build_news_factors


class TestBuildRouter(unittest.TestCase):
    def test_returns_router(self):
        r = build_router()
        self.assertEqual(r.prefix, "/api/factor")
        paths = {route.path for route in r.routes}
        self.assertIn("/api/factor/stock/{code}", paths)
        self.assertIn("/api/factor/batch", paths)
        self.assertIn("/api/factor/sector/{name}", paths)
        self.assertIn("/api/factor/event/{code}", paths)
        self.assertIn("/api/factor/news/{code}", paths)


class TestBuildFactorScore(unittest.TestCase):
    """单股 5 因子综合 — mock 上游全部失败 → 全 None → has_data=False"""

    @patch("tuixue_v3.data_layer.fetch_daily")
    @patch("tuixue_v3.web.news_lookup.fetch_news")
    @patch("tuixue_v3.web.seat_lookup.get_stock_seats")
    @patch("tuixue_v3.web.sector_classify.get_sector")
    def test_all_upstreams_fail(self, mock_sec, mock_seats,
                                mock_news, mock_daily):
        mock_daily.side_effect = Exception("no daily")
        mock_news.return_value = []
        mock_seats.return_value = []
        mock_sec.return_value = {}

        import asyncio
        score = asyncio.run(_build_factor_score("600519"))
        self.assertEqual(score.code, "600519")
        self.assertFalse(score.has_data)
        self.assertEqual(score.composite, 0.0)

    @patch("tuixue_v3.data_layer.fetch_daily")
    @patch("tuixue_v3.web.news_lookup.fetch_news")
    @patch("tuixue_v3.web.seat_lookup.get_stock_seats")
    @patch("tuixue_v3.web.sector_classify.get_sector")
    def test_partial_data(self, mock_sec, mock_seats,
                          mock_news, mock_daily):
        """只板块有数据 → confidence=0.2"""
        mock_daily.side_effect = Exception("no daily")
        mock_news.return_value = []
        mock_seats.return_value = []
        mock_sec.return_value = {"sw": "新能源"}

        import asyncio
        score = asyncio.run(_build_factor_score("600519"))
        # 板块类暂无 sector_rotation API, 也返 None → 全 None
        self.assertFalse(score.has_data)


class TestBuildNewsFactors(unittest.TestCase):
    def test_empty_titles(self):
        """无匹配 → 空列表 (永不抛错)"""
        # 没 patch 时走真 fetch_news 失败也返空列表 (catch 异常)
        items = _build_news_factors("nonexistent_zzz_xx")
        self.assertEqual(items, [])

    def test_with_titles(self):
        # 用 local impl 测纯路径, 不 patch 上游 import chain
        from tuixue_v3 import news_sentiment
        titles = ["XX股份业绩预增 200%", "XX股份被证监会立案调查"]
        items = news_sentiment.score_titles(titles, use_llm=False)
        self.assertEqual(len(items), 2)
        self.assertGreater(items[0].sentiment, 0)
        self.assertLess(items[1].sentiment, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)