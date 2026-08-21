#!/usr/bin/env python3
"""
test_strat_era40_eom.py
Ship 96 单元测试 — EOM (Ease of Movement)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era40_eom import (
    EOMSignal, compute_eom_series,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeEOMSeries(unittest.TestCase):
    def test_basic(self):
        n = 25
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        s = compute_eom_series(highs, lows, vols)
        self.assertEqual(len(s), n)

    def test_short(self):
        self.assertEqual(compute_eom_series([11.0], [9.0], [1000.0]), [])

    def test_zero_volume(self):
        n = 10
        highs = [11.0] * n
        lows = [9.0] * n
        vols = [0.0] * n
        s = compute_eom_series(highs, lows, vols)
        self.assertEqual(len(s), n)


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [11.0] * 5, [9.0] * 5, [1000.0] * 5)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 30
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 30
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 30
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        universe = {
            "up": (highs, lows, vols),
            "down": ([40.0 - i * 0.5 for i in range(n)],
                     [39.0 - i * 0.5 for i in range(n)],
                     vols),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 30
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        universe = {"a": (highs, lows, vols)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 30
        highs = [10.0 + i * 0.5 for i in range(n)]
        lows = [9.0 + i * 0.5 for i in range(n)]
        vols = [1000.0] * n
        sig = generate_signal("a", highs, lows, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)