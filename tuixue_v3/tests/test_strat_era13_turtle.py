#!/usr/bin/env python3
"""
test_strat_era13_turtle.py
Ship 69 单元测试 — 海龟交易
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era13_turtle import (
    TurtleSignal, n_day_high, n_day_low, atr_from_prices,
    generate_signal, generate_dual_system, screen_universe,
    split_buy_sell, summarize,
)


class TestNDayHigh(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 12.0, 11.0, 13.0, 14.0]
        self.assertEqual(n_day_high(prices, 3), 13.0)

    def test_short(self):
        self.assertIsNone(n_day_high([10.0] * 5, window=10))


class TestNDayLow(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 12.0, 11.0, 8.0, 14.0]
        self.assertEqual(n_day_low(prices, 3), 8.0)


class TestATR(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 10.5, 11.0, 10.5, 10.0, 9.5, 10.0]
        atr = atr_from_prices(prices, window=5)
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0)


class TestGenerateSignal(unittest.TestCase):
    def test_buy_breakout(self):
        # 30 个平稳 + 1 大涨突破 20 日高
        prices = [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]
        sig = generate_signal("a", prices, entry_window=20)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_breakout(self):
        # 30 个平稳 + 1 大跌
        prices = [10.0] * 20 + [9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0]
        sig = generate_signal("a", prices, entry_window=20, exit_window=10)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_hold_in_range(self):
        # 30 个价格微波动, 末值 10.0 在 range 内
        prices = [10.0] * 28 + [10.1, 10.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "hold")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestDualSystem(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 60 + [12.0 + i * 0.5 for i in range(20)]
        out = generate_dual_system("a", prices)
        self.assertIn("s1", out)
        self.assertIn("s2", out)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 20 + [10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0],
            "down": [10.0] * 20 + [9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0],
        }
        results = screen_universe(universe)
        self.assertGreater(len(results), 0)


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
        self.assertIsNotNone(sig)
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