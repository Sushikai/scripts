#!/usr/bin/env python3
"""
test_strat_era23_aroon.py
Ship 79 单元测试 — Aroon
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era23_aroon import (
    AroonSignal, compute_aroon,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestAroon(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        res = compute_aroon(prices)
        self.assertIsNotNone(res)
        up, down = res
        # 持续上涨 → up 高, down 低
        self.assertGreater(up, 50)
        self.assertLess(down, 50)

    def test_short(self):
        self.assertIsNone(compute_aroon([10.0] * 5))


class TestGenerateSignal(unittest.TestCase):
    def test_buy_uptrend(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_downtrend(self):
        prices = [15.0 - i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(30)],
            "down": [15.0 - i * 0.1 for i in range(30)],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(30)],
            "down": [15.0 - i * 0.1 for i in range(30)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)