#!/usr/bin/env python3
"""
test_strat_era44_chop.py
Ship 100 单元测试 — Choppiness Index (终极策略 + Phase 5 完结)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era44_chop import (
    ChoppinessSignal, compute_choppiness,
    generate_signal, screen_universe, split_buy_sell, summarize,
    phase5_summary,
)


class TestComputeChoppiness(unittest.TestCase):
    def test_basic(self):
        n = 25
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        res = compute_choppiness(highs, lows, closes, window=14)
        self.assertIsNotNone(res)
        ci, atr_sum, rng = res
        self.assertGreaterEqual(ci, 0)
        self.assertLessEqual(ci, 100)

    def test_insufficient(self):
        closes = [10.0] * 5
        highs = [11.0] * 5
        lows = [9.0] * 5
        self.assertIsNone(compute_choppiness(highs, lows, closes, window=14))

    def test_zero_range(self):
        n = 20
        closes = [10.0] * n
        highs = [10.0] * n
        lows = [10.0] * n
        self.assertIsNone(compute_choppiness(highs, lows, closes, window=14))


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
        self.assertIn(sig.trend_strength, ["trending", "choppy", "normal"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")
        self.assertIn("trend_strength", d)


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


class TestPhase5Summary(unittest.TestCase):
    def test_summary(self):
        s = phase5_summary()
        self.assertEqual(s["phase"], 5)
        self.assertEqual(s["total_strategies"], 44)
        self.assertEqual(s["completion"], "100/100 🎉")
        self.assertIn("oscillator", s["categories"])
        self.assertIn("trend", s["categories"])


if __name__ == "__main__":
    unittest.main(verbosity=2)