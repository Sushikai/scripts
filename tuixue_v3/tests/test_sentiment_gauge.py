#!/usr/bin/env python3
"""
test_sentiment_gauge.py
Ship 38 单元测试 — 情绪仪表盘
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_gauge import (
    SentimentComponent, SentimentGauge,
    build_gauge, to_dict, trend_label,
)


class TestBuildGauge(unittest.TestCase):
    def test_no_args(self):
        """全 None → 中性 50"""
        g = build_gauge()
        self.assertAlmostEqual(g.gauge_value, 50.0)
        self.assertEqual(g.label, "中性")
        self.assertFalse(g.is_extreme)
        # 全 missing
        self.assertTrue(all(c.missing for c in g.components))

    def test_bullish_signal(self):
        """强烈看多场景"""
        g = build_gauge(
            n_limit_up=80,
            n_limit_down=0,
            n_advancing=4000,
            n_declining=500,
            volume_ratio=1.5,
            north_flow=100.0,
            dragon_net_buy=30.0,
        )
        # 全正 → gauge_value 应 > 80
        self.assertGreater(g.gauge_value, 75)
        self.assertIn(g.label, ["贪婪", "极度贪婪"])

    def test_bearish_signal(self):
        """强烈看空"""
        g = build_gauge(
            n_limit_up=0,
            n_limit_down=50,
            n_advancing=500,
            n_declining=4000,
            volume_ratio=0.3,
            north_flow=-100.0,
            dragon_net_buy=-20.0,
        )
        self.assertLess(g.gauge_value, 25)
        self.assertIn(g.label, ["恐惧", "极度恐惧"])

    def test_mixed_partial(self):
        """部分数据缺失"""
        g = build_gauge(
            n_limit_up=30,
            n_advancing=2000,
            n_declining=2500,
            volume_ratio=1.0,
            # north_flow, dragon_net_buy, limit_down None
        )
        # 不报错即可
        self.assertGreater(g.gauge_value, 0)
        self.assertLess(g.gauge_value, 100)
        # 至少 3 项 missing
        missing = sum(1 for c in g.components if c.missing)
        self.assertEqual(missing, 3)

    def test_weights_applied(self):
        """权重生效"""
        g_normal = build_gauge(
            n_limit_up=80,
            n_limit_down=50,  # 同时给多空
            n_advancing=2000,
            n_declining=2500,
        )
        # 自定义权重: limit_up=0.8, limit_down=0.2
        g_custom = build_gauge(
            n_limit_up=80,
            n_limit_down=50,
            n_advancing=2000,
            n_declining=2500,
            weights={"limit_up": 0.8, "limit_down": 0.2,
                     "advance_decline": 0.0, "volume_ratio": 0.0,
                     "north_flow": 0.0, "dragon_net_buy": 0.0},
        )
        # 不同权重应得不同结果
        self.assertNotAlmostEqual(g_normal.gauge_value, g_custom.gauge_value, places=1)


class TestLabels(unittest.TestCase):
    def test_extreme_high(self):
        g = build_gauge(
            n_limit_up=80, n_limit_down=0,
            n_advancing=4000, n_declining=500,
            volume_ratio=2.0, north_flow=100.0, dragon_net_buy=30.0,
        )
        self.assertGreaterEqual(g.gauge_value, 80)
        self.assertEqual(g.label, "极度贪婪")
        self.assertTrue(g.is_extreme)

    def test_extreme_low(self):
        g = build_gauge(
            n_limit_up=0, n_limit_down=200,
            n_advancing=200, n_declining=4500,
            volume_ratio=0.2, north_flow=-200.0, dragon_net_buy=-50.0,
        )
        self.assertLessEqual(g.gauge_value, 20)
        self.assertEqual(g.label, "极度恐惧")
        self.assertTrue(g.is_extreme)

    def test_neutral(self):
        g = build_gauge()
        self.assertFalse(g.is_extreme)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        g = build_gauge(n_limit_up=10)
        d = to_dict(g)
        self.assertIn("gauge_value", d)
        self.assertIn("label", d)
        self.assertIn("components", d)
        self.assertEqual(len(d["components"]), 6)


class TestTrendLabel(unittest.TestCase):
    def test_first(self):
        self.assertEqual(trend_label(None, 60), "首次")

    def test_warm(self):
        self.assertEqual(trend_label(50, 60), "升温")

    def test_cool(self):
        self.assertEqual(trend_label(60, 50), "降温")

    def test_stable(self):
        self.assertEqual(trend_label(50, 52), "平稳")


class TestScoringFunctions(unittest.TestCase):
    def test_limit_up_score(self):
        from tuixue_v3.sentiment_gauge import _limit_up_score
        self.assertEqual(_limit_up_score(0), 20.0)
        self.assertEqual(_limit_up_score(80), 95.0)
        self.assertEqual(_limit_up_score(100), 95.0)

    def test_limit_down_score(self):
        from tuixue_v3.sentiment_gauge import _limit_down_score
        self.assertEqual(_limit_down_score(0), 80.0)
        self.assertEqual(_limit_down_score(50), 10.0)

    def test_flow_score(self):
        from tuixue_v3.sentiment_gauge import _flow_score
        self.assertEqual(_flow_score(-50), 10.0)
        self.assertAlmostEqual(_flow_score(0), 50.0)
        self.assertEqual(_flow_score(100), 90.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
