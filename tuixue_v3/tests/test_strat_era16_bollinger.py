#!/usr/bin/env python3
"""
test_strat_era16_bollinger.py
Ship 72 单元测试 — 布林带
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era16_bollinger import (
    BollingerSignal, compute_bands, generate_signal,
    is_squeeze, screen_universe, split_buy_sell, summarize,
)


class TestBands(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        res = compute_bands(prices, window=20)
        self.assertIsNotNone(res)
        middle, upper, lower = res
        self.assertLess(lower, middle)
        self.assertGreater(upper, middle)

    def test_short(self):
        self.assertIsNone(compute_bands([10.0] * 5, window=20))


class TestGenerateSignal(unittest.TestCase):
    def test_buy_lower_break(self):
        # 20 平稳 10.0 + 最后大跌 6
        prices = [10.0] * 19 + [6.0, 6.0]   # 21 个
        sig = generate_signal("a", prices, window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_upper_break(self):
        prices = [10.0] * 19 + [14.0, 14.0]
        sig = generate_signal("a", prices, window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_hold_in_range(self):
        # 微波动
        prices = [10.0] * 18 + [10.5, 10.0, 10.0]
        sig = generate_signal("a", prices, window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "hold")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, window=20)
        self.assertIsNone(sig)


class TestSqueeze(unittest.TestCase):
    def test_basic(self):
        # 带宽收缩 → squeeze
        prices = [10.0 + 0.01 * (i % 5) for i in range(50)]
        self.assertTrue(is_squeeze(prices, window=20, threshold=0.05))


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "down": [10.0] * 19 + [6.0, 6.0],
            "up": [10.0] * 19 + [14.0, 14.0],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "down": [10.0] * 19 + [6.0, 6.0],
            "up": [10.0] * 19 + [14.0, 14.0],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [6.0, 6.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [6.0, 6.0]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "buy")


if __name__ == "__main__":
    unittest.main(verbosity=2)