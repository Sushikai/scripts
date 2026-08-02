#!/usr/bin/env python3
"""
test_strat_era26_parabolic_sar.py
Ship 82 单元测试 — Parabolic SAR
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era26_parabolic_sar import (
    ParabolicSARSignal, compute_sar,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestSAR(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(30)]
        res = compute_sar(prices)
        self.assertIsNotNone(res)
        sar, trend, af, ep = res
        self.assertIn(trend, ["up", "down"])

    def test_short(self):
        self.assertIsNone(compute_sar([10.0, 11.0]))


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        prices = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_downtrend(self):
        prices = [20.0 - i * 0.5 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0])
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(30)],
            "down": [20.0 - i * 0.5 for i in range(30)],
        }
        results = screen_universe(universe)
        self.assertGreater(len(results), 0)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(30)],
            "down": [20.0 - i * 0.5 for i in range(30)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)