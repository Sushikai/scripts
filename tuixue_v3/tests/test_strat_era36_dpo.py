#!/usr/bin/env python3
"""
test_strat_era36_dpo.py
Ship 92 单元测试 — DPO (Detrended Price Oscillator)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era36_dpo import (
    DPOSignal, compute_dpo_series,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeDPOSeries(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(60)]
        s = compute_dpo_series(prices, window=20)
        self.assertGreater(len(s), 0)

    def test_short(self):
        prices = [10.0] * 10
        s = compute_dpo_series(prices, window=20)
        self.assertEqual(s, [])


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 10)
        self.assertIsNone(sig)

    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 60
        universe = {
            "up": [10.0 + i * 0.5 for i in range(n)],
            "down": [40.0 - i * 0.5 for i in range(n)],
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 60
        universe = {"a": [10.0 + i * 0.5 for i in range(n)]}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(60)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)