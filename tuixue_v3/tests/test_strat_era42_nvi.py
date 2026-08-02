#!/usr/bin/env python3
"""
test_strat_era42_nvi.py
Ship 98 单元测试 — NVI (Negative Volume Index)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era42_nvi import (
    NVISignal, compute_nvi_series,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestComputeNVISeries(unittest.TestCase):
    def test_basic(self):
        n = 25
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        s = compute_nvi_series(closes, vols)
        self.assertEqual(len(s), n)
        self.assertEqual(s[0], 100.0)

    def test_short(self):
        self.assertEqual(compute_nvi_series([10.0], [1000.0]), [])

    def test_decreasing_volume(self):
        n = 10
        closes = [10.0 + i for i in range(n)]
        vols = [1000.0 - i * 100 for i in range(n)]
        s = compute_nvi_series(closes, vols)
        # 减量日 NVI 应变化
        self.assertNotEqual(s[-1], s[0])


class TestGenerateSignal(unittest.TestCase):
    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, [1000.0] * 5)
        self.assertIsNone(sig)

    def test_basic(self):
        n = 60
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        sig = generate_signal("a", closes, vols)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_to_dict(self):
        n = 60
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        sig = generate_signal("a", closes, vols)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        n = 60
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        universe = {
            "a": (closes, vols),
            "b": ([40.0 - i * 0.5 for i in range(n)], vols),
        }
        results = screen_universe(universe)
        self.assertIsInstance(results, list)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        n = 60
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        universe = {"a": (closes, vols)}
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertIsInstance(buys, list)
        self.assertIsInstance(sells, list)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        n = 60
        closes = [10.0 + i * 0.5 for i in range(n)]
        vols = [1000.0 + i for i in range(n)]
        sig = generate_signal("a", closes, vols)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)