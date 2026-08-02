#!/usr/bin/env python3
"""
test_sentiment_dashboard_api.py
Ship 55 单元测试 — 情绪仪表盘 API
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_dashboard_api import (
    SentimentDashboard, build_dashboard, build_multi,
    _label, _color,
)


class TestLabel(unittest.TestCase):
    def test_extreme_greed(self):
        self.assertEqual(_label(85), "极度贪婪")

    def test_neutral(self):
        self.assertEqual(_label(50), "中性")

    def test_extreme_fear(self):
        self.assertEqual(_label(15), "极度恐惧")


class TestColor(unittest.TestCase):
    def test_extreme_high(self):
        self.assertEqual(_color(85), "#006400")

    def test_extreme_low(self):
        self.assertEqual(_color(15), "#8b0000")

    def test_neutral(self):
        self.assertEqual(_color(50), "#999999")


class TestBuildDashboard(unittest.TestCase):
    def test_basic(self):
        history = [50] * 20 + [60, 65, 70, 75, 80]
        d = build_dashboard(80, history)
        self.assertEqual(d.gauge, 80.0)
        self.assertEqual(d.label, "极度贪婪")
        self.assertTrue(d.is_extreme)
        self.assertEqual(d.color, "#006400")

    def test_trend_up(self):
        history = [50, 50, 50, 60, 70]
        d = build_dashboard(70, history)
        self.assertEqual(d.trend_color, "#00b050")
        self.assertIn("升温", "".join(d.signals))

    def test_trend_down(self):
        history = [70, 70, 70, 60, 50]
        d = build_dashboard(50, history)
        self.assertEqual(d.trend_color, "#c00000")

    def test_zscore(self):
        # 历史均值 50, std 5; 当前 80 → z ≈ 6
        history = [45, 50, 55, 50, 45, 50]
        d = build_dashboard(80, history)
        self.assertGreater(d.zscore, 5.0)

    def test_zscore_low(self):
        history = [45, 50, 55, 50, 45, 50]
        d = build_dashboard(20, history)
        self.assertLess(d.zscore, -5.0)

    def test_clamp_score(self):
        d = build_dashboard(150, [50] * 10)
        self.assertEqual(d.gauge, 100.0)

    def test_with_components(self):
        d = build_dashboard(50, [50] * 5,
                            components={"limit_up": 50, "volume": 60})
        self.assertEqual(d.components["limit_up"], 50)

    def test_no_signals(self):
        # 中性分, 历史稳定
        history = [50] * 30
        d = build_dashboard(50, history)
        # 中性可能没有信号
        self.assertIsInstance(d.signals, list)

    def test_short_history(self):
        d = build_dashboard(50, [50])
        # 不足 5 个 → z=0
        self.assertEqual(d.zscore, 0.0)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        d = build_dashboard(70, [50] * 10)
        out = d.to_dict()
        self.assertIn("gauge", out)
        self.assertIn("signals", out)
        self.assertIn("color", out)
        self.assertEqual(len(out["trend"]), 10)


class TestBuildMulti(unittest.TestCase):
    def test_basic(self):
        views = {
            "broad": (60.0, [50] * 10),
            "sh": (40.0, [50] * 10),
        }
        result = build_multi(views)
        self.assertEqual(set(result.keys()), {"broad", "sh"})

    def test_with_components(self):
        views = {"broad": (60.0, [50] * 10)}
        comps = {"broad": {"limit_up": 50}}
        result = build_multi(views, components_per_view=comps)
        self.assertEqual(result["broad"].components["limit_up"], 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
