#!/usr/bin/env python3
"""
test_strat_era41_wad.py
Ship 97 单元测试 — WAD (Williams Accumulation/Distribution)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era41_wad import (
    WADSignal, compute_wad_series, compute_slope,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeWADSeries(unittest.TestCase):
    def test_basic(self):
        n = 25
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        s = compute_wad_series(highs, lows, closes)
        self.assertEqual(len(s), n)

    def test_short(self):
        self.assertEqual(compute_wad_series([11.0], [9.0], [10.0]), [])

    def test_uptrend(self):
        n = 10
        closes = [10.0 + i for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        s = compute_wad_series(highs, lows, closes)
        # 单调上涨 → WAD 持续上升
        for i in range(1, len(s)):
            self.assertGreaterEqual(s[i], s[i - 1])


class TestComputeSlope(unittest.TestCase):
    def test_up(self):
        s = [10.0 + i for i in range(20)]
        slope = compute_slope(s, window=10)
        self.assertGreater(slope, 0)


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [11.0] * 5, [9.0] * 5, [10.0] * 5)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        universe = {
            "up": (highs, lows, closes),
            "down": (highs, lows, [40.0 - i * 0.5 for i in range(n)]),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        universe = {"a": (highs, lows, closes)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)