#!/usr/bin/env python3
"""
test_strat_era39_mfi.py
Ship 95 单元测试 — MFI (Money Flow Index)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era39_mfi import (
    MFISignal, compute_mfi,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeMFI(unittest.TestCase):
    def test_basic(self):
        n = 25
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        v = compute_mfi(highs, lows, closes, vols, window=14)
        self.assertIsNotNone(v)
        self.assertGreaterEqual(v, 0)
        self.assertLessEqual(v, 100)

    def test_insufficient(self):
        closes = [10.0] * 10
        highs = [11.0] * 10
        lows = [9.0] * 10
        vols = [1000.0] * 10
        self.assertIsNone(compute_mfi(highs, lows, closes, vols, window=14))

    def test_no_negative_mf(self):
        # 全部上涨 → -MF=0 → mfi=100
        n = 20
        closes = [10.0 + i for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        v = compute_mfi(highs, lows, closes, vols, window=14)
        self.assertEqual(v, 100.0)


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [11.0] * 10, [9.0] * 10, [10.0] * 10, [1000.0] * 10)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        universe = {
            "a": (highs, lows, closes, vols),
            "b": (highs, lows, [40.0 - i * 0.5 for i in range(n)], vols),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        universe = {"a": (highs, lows, closes, vols)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, closes, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)