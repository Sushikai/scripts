#!/usr/bin/env python3
"""
test_strat_era34_obv_trend.py
Ship 90 单元测试 — OBV Trend
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era34_obv_trend import (
    OBVTrendSignal, compute_obv_series, compute_slope,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeOBVSeries(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(20)]
        vols = [1000.0] * 20
        s = compute_obv_series(closes, vols)
        self.assertGreater(len(s), 0)

    def test_short(self):
        self.assertEqual(compute_obv_series([10.0], [1000.0]), [])

    def test_flat(self):
        closes = [10.0] * 10
        vols = [1000.0] * 10
        s = compute_obv_series(closes, vols)
        self.assertEqual(len(s), 10)
        # 全平 → OBV 全 0


class TestComputeSlope(unittest.TestCase):
    def test_up(self):
        s = [10.0 + i for i in range(20)]
        slope = compute_slope(s, window=10)
        self.assertIsNotNone(slope)
        self.assertGreater(slope, 0)

    def test_down(self):
        s = [20.0 - i for i in range(20)]
        slope = compute_slope(s, window=10)
        self.assertIsNotNone(slope)
        self.assertLess(slope, 0)

    def test_insufficient(self):
        self.assertIsNone(compute_slope([1.0, 2.0], window=10))


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [10.0, 11.0], [1000.0, 1000.0])
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        up_c = [10.0 + i * 0.5 for i in range(n)]
        down_c = [40.0 - i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        universe = {
            "up": (up_c, vols),
            "down": (down_c, vols),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        universe = {"a": (closes, vols)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)