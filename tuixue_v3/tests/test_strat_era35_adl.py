#!/usr/bin/env python3
"""
test_strat_era35_adl.py
Ship 91 单元测试 — ADL (Accumulation/Distribution Line)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era35_adl import (
    ADLSignal, compute_adl_series, compute_slope,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeADLSeries(unittest.TestCase):
    def test_basic(self):
        n = 20
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        s = compute_adl_series(highs, lows, closes, vols)
        self.assertEqual(len(s), n)

    def test_short(self):
        s = compute_adl_series([], [], [], [])
        self.assertEqual(s, [])


class TestComputeSlope(unittest.TestCase):
    def test_up(self):
        s = [10.0 + i for i in range(20)]
        slope = compute_slope(s, window=10)
        self.assertIsNotNone(slope)
        self.assertGreater(slope, 0)


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a",
                              [11.0, 12.0],
                              [9.0, 10.0],
                              [10.0, 11.0],
                              [1000.0, 1100.0])
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        universe = {
            "up": (highs, lows, closes, vols),
            "down": (highs, lows, [40.0 - i * 0.5 for i in range(n)], vols),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        universe = {"a": (highs, lows, closes, vols)}
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
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)