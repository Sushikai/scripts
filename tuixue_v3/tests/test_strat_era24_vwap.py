#!/usr/bin/env python3
"""
test_strat_era24_vwap.py
Ship 80 单元测试 — VWAP
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era24_vwap import (
    VWAPSignal, compute_vwap, rolling_vwap,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestVWAP(unittest.TestCase):
    def test_basic(self):
        closes = [10.0, 11.0, 12.0]
        volumes = [100.0, 200.0, 300.0]
        vwap = compute_vwap(closes, volumes)
        self.assertIsNotNone(vwap)
        # (10*100 + 11*200 + 12*300) / (100+200+300) = 6800/600 = 11.33
        self.assertAlmostEqual(vwap, 6800.0 / 600.0, places=4)

    def test_short(self):
        self.assertIsNone(compute_vwap([10.0], [100.0]))


class TestRollingVWAP(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.1 for i in range(30)]
        volumes = [100.0 + i for i in range(30)]
        v = rolling_vwap(closes, volumes, window=20)
        self.assertIsNotNone(v)


class TestGenerateSignal(unittest.TestCase):
    def test_buy_above(self):
        closes = [10.0 + i * 0.1 for i in range(30)]
        volumes = [100.0 + i for i in range(30)]
        sig = generate_signal("a", closes, volumes, window=20, threshold=0.005)
        self.assertIsNotNone(sig)

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, [100.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": ([10.0 + i * 0.5 for i in range(30)],
                  [100.0 + i for i in range(30)]),
            "b": ([15.0 - i * 0.5 for i in range(30)],
                  [100.0 + i for i in range(30)]),
        }
        results = screen_universe(universe)
        # 视情况


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": ([10.0 + i * 0.5 for i in range(30)],
                  [100.0 + i for i in range(30)]),
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        # 视情况


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        volumes = [100.0 + i for i in range(30)]
        sig = generate_signal("a", closes, volumes, window=20, threshold=0.005)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        volumes = [100.0 + i for i in range(30)]
        sig = generate_signal("a", closes, volumes, window=20, threshold=0.005)
        self.assertIsNotNone(sig)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)