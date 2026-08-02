#!/usr/bin/env python3
"""
test_strat_era27_donchian.py
Ship 83 单元测试 — Donchian
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era27_donchian import (
    DonchianSignal, compute_donchian,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestDonchian(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        res = compute_donchian(prices, window=5)
        # 排除最后, 取前 5 个 [10, 11, 12, 13, 14]
        # upper=14, lower=10, middle=12
        self.assertIsNotNone(res)
        upper, lower, middle = res
        self.assertEqual(upper, 14.0)
        self.assertEqual(lower, 10.0)
        self.assertAlmostEqual(middle, 12.0)

    def test_short(self):
        self.assertIsNone(compute_donchian([10.0] * 5, window=10))


class TestGenerateSignal(unittest.TestCase):
    def test_buy_breakout(self):
        # 20 个平稳 + 最后突破
        prices = [10.0] * 19 + [11.0, 12.5]
        sig = generate_signal("a", prices, window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_breakdown(self):
        prices = [10.0] * 19 + [9.0, 7.5]
        sig = generate_signal("a", prices, window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, window=20)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 19 + [11.0, 12.5],
            "down": [10.0] * 19 + [9.0, 7.5],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 19 + [11.0, 12.5],
            "down": [10.0] * 19 + [9.0, 7.5],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [11.0, 12.5]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [12.0, 12.0]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)