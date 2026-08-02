#!/usr/bin/env python3
"""
test_strat_era20_williams_r.py
Ship 76 单元测试 — Williams %R
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era20_williams_r import (
    WilliamsSignal, compute_williams_r,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestWilliamsR(unittest.TestCase):
    def test_overbought(self):
        # 一直涨 → 接近最高 → %R 接近 0 → 超买
        prices = [10.0 + i * 0.1 for i in range(20)]
        res = compute_williams_r(prices)
        self.assertIsNotNone(res)
        wr, _, _ = res
        self.assertGreater(wr, -20)

    def test_oversold(self):
        prices = [15.0 - i * 0.1 for i in range(20)]
        res = compute_williams_r(prices)
        wr, _, _ = res
        self.assertLess(wr, -80)

    def test_short(self):
        self.assertIsNone(compute_williams_r([10.0] * 5))


class TestGenerateSignal(unittest.TestCase):
    def test_sell_overbought(self):
        prices = [10.0 + i * 0.1 for i in range(20)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_buy_oversold(self):
        prices = [15.0 - i * 0.1 for i in range(20)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(20)],
            "down": [15.0 - i * 0.1 for i in range(20)],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(20)],
            "down": [15.0 - i * 0.1 for i in range(20)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(20)]
        sig = generate_signal("a", prices)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(20)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "sell")


if __name__ == "__main__":
    unittest.main(verbosity=2)