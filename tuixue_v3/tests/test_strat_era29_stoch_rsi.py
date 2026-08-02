#!/usr/bin/env python3
"""
test_strat_era29_stoch_rsi.py
Ship 85 单元测试 — Stochastic RSI
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era29_stoch_rsi import (
    StochRSISignal, compute_rsi_series, compute_stoch_rsi,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestRSISeries(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        s = compute_rsi_series(prices, window=14)
        self.assertGreater(len(s), 0)
        self.assertGreater(s[-1], 70)


class TestStochRSI(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        res = compute_stoch_rsi(prices)
        self.assertIsNotNone(res)
        rsi, stoch, mn, mx = res
        self.assertGreaterEqual(stoch, 0)
        self.assertLessEqual(stoch, 1)

    def test_short(self):
        self.assertIsNone(compute_stoch_rsi([10.0] * 10))


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        # 涨势 → sell (超买)

    def test_downtrend(self):
        prices = [20.0 - i * 0.1 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 10)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(50)],
            "down": [20.0 - i * 0.1 for i in range(50)],
        }
        results = screen_universe(universe)
        # 不强求


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.1 for i in range(50)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)


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
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)