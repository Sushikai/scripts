#!/usr/bin/env python3
"""
test_strat_era28_keltner.py
Ship 84 单元测试 — Keltner Channel
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era28_keltner import (
    KeltnerSignal, ema_value, atr_value,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestEMA(unittest.TestCase):
    def test_basic(self):
        v = ema_value([10.0 + i * 0.1 for i in range(30)], window=20)
        self.assertIsNotNone(v)

    def test_short(self):
        self.assertIsNone(ema_value([10.0, 11.0], window=5))


class TestATR(unittest.TestCase):
    def test_basic(self):
        v = atr_value([10.0 + i * 0.1 for i in range(30)], window=14)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0)

    def test_short(self):
        self.assertIsNone(atr_value([10.0] * 5, window=14))


class TestGenerateSignal(unittest.TestCase):
    def test_buy_breakout(self):
        # 平稳 + 最后大涨
        prices = [10.0] * 19 + [10.5, 14.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_breakdown(self):
        prices = [10.0] * 19 + [9.5, 6.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 19 + [10.5, 14.0],
            "down": [10.0] * 19 + [9.5, 6.0],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 19 + [10.5, 14.0],
            "down": [10.0] * 19 + [9.5, 6.0],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [10.5, 14.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 19 + [10.5, 14.0]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)