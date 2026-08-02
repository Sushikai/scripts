#!/usr/bin/env python3
"""
test_strat_era37_aroon.py
Ship 93 单元测试 — Aroon Oscillator
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era37_aroon import (
    AroonSignal, compute_aroon,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeAroon(unittest.TestCase):
    def test_basic(self):
        highs = [10.0 + i for i in range(30)]
        lows = [9.0 + i for i in range(30)]
        res = compute_aroon(highs, lows, window=25)
        self.assertIsNotNone(res)
        up, down = res
        self.assertGreaterEqual(up, 0)
        self.assertLessEqual(up, 100)
        self.assertGreaterEqual(down, 0)
        self.assertLessEqual(down, 100)

    def test_insufficient(self):
        highs = [10.0] * 5
        lows = [9.0] * 5
        self.assertIsNone(compute_aroon(highs, lows, window=25))

    def test_uptrend(self):
        # 高点最新 → aroon_up=100
        highs = [10.0] * 20 + [20.0]
        lows = [9.0] * 20 + [19.0]
        res = compute_aroon(highs, lows, window=20)
        self.assertIsNotNone(res)
        up, down = res
        self.assertEqual(up, 100.0)


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [11.0] * 5, [9.0] * 5)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [9.0 + i for i in range(n)]
        sig = generate_signal("a", highs, lows)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [9.0 + i for i in range(n)]
        sig = generate_signal("a", highs, lows)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        up_h = [10.0 + i for i in range(n)]
        up_l = [9.0 + i for i in range(n)]
        down_h = [40.0 - i for i in range(n)]
        down_l = [39.0 - i for i in range(n)]
        universe = {
            "up": (up_h, up_l),
            "down": (down_h, down_l),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [9.0 + i for i in range(n)]
        universe = {"a": (highs, lows)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [9.0 + i for i in range(n)]
        sig = generate_signal("a", highs, lows)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)