#!/usr/bin/env python3
"""
test_strat_era32_cmf.py
Ship 88 单元测试 — CMF (Chaikin Money Flow)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era32_cmf import (
    CMFSignal, compute_cmf, generate_signal,
    screen_universe, split_buy_sell, summarize,
)


class TestComputeCMF(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.1 for i in range(25)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        vols = [1000.0] * 25
        v = compute_cmf(closes, highs, lows, vols, window=20)
        self.assertIsNotNone(v)

    def test_insufficient(self):
        closes = [10.0] * 10
        highs = [11.0] * 10
        lows = [9.0] * 10
        vols = [1000.0] * 10
        self.assertIsNone(compute_cmf(closes, highs, lows, vols, window=20))

    def test_zero_volume(self):
        closes = [10.0] * 25
        highs = [11.0] * 25
        lows = [9.0] * 25
        vols = [0.0] * 25
        self.assertIsNone(compute_cmf(closes, highs, lows, vols, window=20))


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a",
                              [10.0] * 10,
                              [11.0] * 10,
                              [9.0] * 10,
                              [1000.0] * 10)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.1 for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, highs, lows, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        closes = [10.0 + i * 0.1 for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, highs, lows, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        up_c = [10.0 + i * 0.5 for i in range(n)]
        down_c = [40.0 - i * 0.5 for i in range(n)]
        up_h = [c + 1.0 for c in up_c]
        up_l = [c - 1.0 for c in up_c]
        down_h = [c + 1.0 for c in down_c]
        down_l = [c - 1.0 for c in down_c]
        vols = [1000.0] * n
        universe = {
            "up": (up_c, up_h, up_l, vols),
            "down": (down_c, down_h, down_l, vols),
        }
        results = screen_universe(universe)
        # 不强求 side


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        vols = [1000.0] * n
        universe = {"a": (closes, highs, lows, vols)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        closes = [10.0 + i * 0.1 for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        vols = [1000.0] * n
        sig = generate_signal("a", closes, highs, lows, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)