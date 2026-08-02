#!/usr/bin/env python3
"""
test_sentiment_color.py
Ship 52 单元测试 — 情绪颜色
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_color import (
    color_for_score, color_with_blend,
    trend_color, sign_color, zone_color,
    _hex_to_rgb, _rgb_to_hex, _blend,
    gradient_stops, theme_palette,
)


class TestColorForScore(unittest.TestCase):
    def test_extreme_fear(self):
        self.assertEqual(color_for_score(10), "#8b0000")

    def test_extreme_greed(self):
        self.assertEqual(color_for_score(90), "#006400")

    def test_neutral(self):
        # 50-60 区间
        self.assertEqual(color_for_score(55), "#999999")

    def test_clamp(self):
        # 不应崩溃
        c1 = color_for_score(-10)
        c2 = color_for_score(110)
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)


class TestColorWithBlend(unittest.TestCase):
    def test_basic(self):
        c = color_with_blend(50)
        # 中性 → 灰色
        self.assertIn(c, ["#999999", "#969696", "#a0a0a0"])

    def test_fear(self):
        c = color_with_blend(0)
        # 应偏红
        r, g, b = _hex_to_rgb(c)
        self.assertGreater(r, g)

    def test_greed(self):
        c = color_with_blend(100)
        r, g, b = _hex_to_rgb(c)
        self.assertGreater(g, r)


class TestTrendColor(unittest.TestCase):
    def test_strong_up(self):
        self.assertEqual(trend_color(10), "#00b050")

    def test_weak_down(self):
        self.assertEqual(trend_color(-1), "#d97070")

    def test_strong_down(self):
        self.assertEqual(trend_color(-10), "#c00000")

    def test_flat(self):
        self.assertEqual(trend_color(0), "#999999")


class TestSignColor(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(sign_color(True), "#00b050")

    def test_negative(self):
        self.assertEqual(sign_color(False), "#c00000")


class TestZoneColor(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(zone_color("extreme_fear"), "#8b0000")
        self.assertEqual(zone_color("extreme_greed"), "#006400")
        self.assertEqual(zone_color("neutral"), "#999999")

    def test_unknown(self):
        self.assertEqual(zone_color(""), "#999999")


class TestHexConv(unittest.TestCase):
    def test_hex_to_rgb(self):
        self.assertEqual(_hex_to_rgb("#ff0000"), (255, 0, 0))
        self.assertEqual(_hex_to_rgb("#00ff00"), (0, 255, 0))

    def test_rgb_to_hex(self):
        self.assertEqual(_rgb_to_hex(255, 0, 0), "#ff0000")

    def test_blend(self):
        c = _blend("#000000", "#ffffff", 0.5)
        # 应在中点附近
        self.assertEqual(_hex_to_rgb(c), (127, 127, 127))


class TestGradientStops(unittest.TestCase):
    def test_basic(self):
        stops = gradient_stops(5)
        self.assertEqual(len(stops), 5)

    def test_n_one(self):
        stops = gradient_stops(1)
        self.assertEqual(len(stops), 1)


class TestThemePalette(unittest.TestCase):
    def test_basic(self):
        p = theme_palette()
        self.assertIn("fear", p)
        self.assertIn("greed", p)
        self.assertIn("neutral", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
