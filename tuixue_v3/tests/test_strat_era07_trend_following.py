#!/usr/bin/env python3
"""
test_strat_era07_trend_following.py
Ship 63 单元测试 — 趋势跟踪
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era07_trend_following import (
    TFSignal, simple_ma, slope,
    generate_signal, generate_multi_ma,
    screen_universe, split_buy_sell, summarize,
)


class TestMA(unittest.TestCase):
    def test_basic(self):
        ma = simple_ma([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
        self.assertAlmostEqual(ma, 3.0)

    def test_short(self):
        self.assertIsNone(simple_ma([1.0, 2.0], window=5))


class TestSlope(unittest.TestCase):
    def test_up(self):
        s = slope([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
        self.assertAlmostEqual(s, 1.0)

    def test_flat(self):
        s = slope([5.0] * 10, window=5)
        self.assertAlmostEqual(s, 0.0)


class TestGenerateSignal(unittest.TestCase):
    def test_golden_cross(self):
        # 30 个价格, 前 20 个平稳 10.0, 后 10 个上涨
        prices = [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]
        sig = generate_signal("a", prices, fast=5, slow=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_death_cross(self):
        # 前 20 个平稳, 后 10 个下跌
        prices = [10.0] * 20 + [9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0]
        sig = generate_signal("a", prices, fast=5, slow=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_hold_converge(self):
        # 完全平稳
        prices = [10.0] * 30
        sig = generate_signal("a", prices, fast=5, slow=20)
        # spread=0 < exit_spread → hold
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "hold")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, fast=5, slow=20)
        self.assertIsNone(sig)


class TestMultiMA(unittest.TestCase):
    def test_consistent_buy(self):
        prices = [10.0] * 60 + [11.0 + i * 0.5 for i in range(30)]
        sig = generate_multi_ma("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_short_data(self):
        prices = [10.0] * 5
        sig = generate_multi_ma("a", prices)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0],
            "down": [10.0] * 20 + [9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0],
        }
        results = screen_universe(universe, top_n=10)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0],
            "down": [10.0] * 20 + [9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]
        sig = generate_signal("a", prices)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "buy")


if __name__ == "__main__":
    unittest.main(verbosity=2)