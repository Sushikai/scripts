#!/usr/bin/env python3
"""
test_strat_era22_adx.py
Ship 78 单元测试 — ADX
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era22_adx import (
    ADXSignal, compute_adx,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestADX(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        res = compute_adx(closes)
        self.assertIsNotNone(res)
        adx, plus_di, minus_di = res
        self.assertGreater(adx, 0)
        # 涨势 +DI > -DI
        self.assertGreater(plus_di, minus_di)

    def test_short(self):
        self.assertIsNone(compute_adx([10.0] * 5))


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", closes)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_downtrend(self):
        closes = [15.0 - i * 0.5 for i in range(30)]
        sig = generate_signal("a", closes)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(30)],
            "down": [15.0 - i * 0.5 for i in range(30)],
        }
        results = screen_universe(universe)
        self.assertGreater(len(results), 0)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(30)],
            "down": [15.0 - i * 0.5 for i in range(30)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", closes)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        sig = generate_signal("a", closes)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)