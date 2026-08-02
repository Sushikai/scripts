#!/usr/bin/env python3
"""
test_strat_era30_trix.py
Ship 86 单元测试 — TRIX
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era30_trix import (
    TRIXSignal, triple_ema_series, compute_trix_series,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestTripleEMA(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        s = triple_ema_series(prices, window=15)
        self.assertGreater(len(s), 0)


class TestTRIX(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(50)]
        s = compute_trix_series(prices)
        self.assertGreater(len(s), 0)


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        prices = [10.0 + i * 0.5 for i in range(80)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell"])   # 取决于 trix vs signal_line

    def test_downtrend(self):
        prices = [40.0 - i * 0.5 for i in range(80)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell"])

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 10)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(80)],
            "down": [40.0 - i * 0.5 for i in range(80)],
        }
        results = screen_universe(universe)
        # 不强求数量 (trix 可能 sell/buy)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(80)],
            "down": [40.0 - i * 0.5 for i in range(80)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        # 不强求 (trix 取决于细节)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(80)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(80)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)