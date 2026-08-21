#!/usr/bin/env python3
"""
test_strat_era15_rsi.py
Ship 71 单元测试 — RSI 策略
"""
import sys
import unittest
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era15_rsi import (
    RSISignal, compute_rsi, generate_signal, generate_multi_window,
    screen_universe, split_buy_sell, summarize,
)


class TestRSI(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        res = compute_rsi(prices, window=14)
        self.assertIsNotNone(res)
        rsi, _, _ = res
        self.assertGreater(rsi, 70)   # 一直涨 → rsi 高

    def test_short(self):
        self.assertIsNone(compute_rsi([10.0] * 5, window=14))


class TestGenerateSignal(unittest.TestCase):
    def test_sell_overbought(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices, window=14)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_buy_oversold(self):
        prices = [10.0 - i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices, window=14)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_hold_neutral(self):
        random.seed(42)
        prices = [10.0 + random.gauss(0, 1.0) for _ in range(30)]
        sig = generate_signal("a", prices, window=14)
        self.assertIsNotNone(sig)

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, window=14)
        self.assertIsNone(sig)


class TestMultiWindow(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        sig = generate_multi_window("a", prices, windows=(7, 14, 21))
        self.assertIsNotNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(30)],
            "down": [10.0 - i * 0.1 for i in range(30)],
        }
        results = screen_universe(universe)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(30)],
            "down": [10.0 - i * 0.1 for i in range(30)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "sell")


if __name__ == "__main__":
    unittest.main(verbosity=2)