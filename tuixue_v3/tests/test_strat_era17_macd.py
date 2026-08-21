#!/usr/bin/env python3
"""
test_strat_era17_macd.py
Ship 73 单元测试 — MACD
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era17_macd import (
    MACDSignal, ema, ema_series, compute_macd,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestEMA(unittest.TestCase):
    def test_basic(self):
        v = ema([10.0, 11.0, 12.0, 13.0, 14.0], window=3)
        self.assertIsNotNone(v)
        self.assertGreater(v, 10)

    def test_short(self):
        self.assertIsNone(ema([10.0, 11.0], window=3))


class TestEMASeries(unittest.TestCase):
    def test_basic(self):
        s = ema_series([10.0, 11.0, 12.0, 13.0, 14.0], window=3)
        self.assertEqual(len(s), 3)   # 5 - 3 + 1 = 3


class TestMACD(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        res = compute_macd(prices)
        self.assertIsNotNone(res)
        dif, dea, hist = res
        # 涨势: dif > dea, hist > 0
        self.assertGreater(dif, dea)

    def test_short(self):
        self.assertIsNone(compute_macd([10.0] * 5))


class TestGenerateSignal(unittest.TestCase):
    def test_buy_uptrend(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_downtrend(self):
        prices = [10.0 - i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(50)],
            "down": [10.0 - i * 0.1 for i in range(50)],
        }
        results = screen_universe(universe)
        self.assertGreater(len(results), 0)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(50)],
            "down": [10.0 - i * 0.1 for i in range(50)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "buy")


if __name__ == "__main__":
    unittest.main(verbosity=2)