#!/usr/bin/env python3
"""
test_strat_era38_uo.py
Ship 94 单元测试 — Ultimate Oscillator
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era38_uo import (
    UOSignal, compute_ultimate_oscillator,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeUO(unittest.TestCase):
    def test_basic(self):
        n = 40
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        v = compute_ultimate_oscillator(highs, lows, closes)
        self.assertIsNotNone(v)
        self.assertGreaterEqual(v, 0)
        self.assertLessEqual(v, 100)

    def test_insufficient(self):
        closes = [10.0] * 10
        highs = [11.0] * 10
        lows = [9.0] * 10
        self.assertIsNone(compute_ultimate_oscillator(highs, lows, closes))


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [11.0] * 10, [9.0] * 10, [10.0] * 10)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 40
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 40
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 40
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
        n = 40
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
        n = 40
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        sig = generate_signal("a", highs, lows, closes)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)