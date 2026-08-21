#!/usr/bin/env python3
"""
test_strat_era25_ichimoku.py
Ship 81 单元测试 — Ichimoku
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era25_ichimoku import (
    IchimokuSignal, midpoint, compute_ichimoku,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestMidpoint(unittest.TestCase):
    def test_basic(self):
        m = midpoint([10.0, 12.0, 14.0, 16.0, 18.0], window=5)
        self.assertAlmostEqual(m, 14.0)

    def test_short(self):
        self.assertIsNone(midpoint([10.0, 11.0], window=5))


class TestIchimoku(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        res = compute_ichimoku(prices)
        self.assertIsNotNone(res)
        t, k, sa, sb, top, bot = res
        self.assertGreater(top, bot)

    def test_short(self):
        self.assertIsNone(compute_ichimoku([10.0] * 10))


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_downtrend(self):
        prices = [30.0 - i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 10)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(60)],
            "down": [30.0 - i * 0.5 for i in range(60)],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(60)],
            "down": [30.0 - i * 0.5 for i in range(60)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)