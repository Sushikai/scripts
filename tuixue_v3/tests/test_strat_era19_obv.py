#!/usr/bin/env python3
"""
test_strat_era19_obv.py
Ship 75 单元测试 — OBV
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era19_obv import (
    OBVSignal, compute_obv, slope, detect_divergence,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestOBV(unittest.TestCase):
    def test_basic(self):
        closes = [10.0, 11.0, 10.5, 12.0]
        volumes = [100.0, 200.0, 150.0, 300.0]
        obv = compute_obv(closes, volumes)
        self.assertEqual(len(obv), 4)
        # 10→11 (涨): +200 → 200
        # 11→10.5 (跌): -150 → 50
        # 10.5→12 (涨): +300 → 350
        self.assertAlmostEqual(obv[-1], 350.0)


class TestSlope(unittest.TestCase):
    def test_up(self):
        s = slope([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
        self.assertAlmostEqual(s, 1.0)

    def test_flat(self):
        self.assertAlmostEqual(slope([5.0] * 10, window=5), 0.0)


class TestDivergence(unittest.TestCase):
    def test_bullish(self):
        # 价格新低, OBV 升高
        closes = [10.0, 9.0]
        obv = [0.0, 100.0]
        self.assertEqual(detect_divergence(closes, obv, lookback=1), "bullish")

    def test_bearish(self):
        closes = [10.0, 11.0]
        obv = [100.0, 50.0]
        self.assertEqual(detect_divergence(closes, obv, lookback=1), "bearish")

    def test_none(self):
        closes = [10.0, 11.0]
        obv = [50.0, 100.0]
        self.assertEqual(detect_divergence(closes, obv, lookback=1), "none")


class TestGenerateSignal(unittest.TestCase):
    def test_buy_uptrend(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        volumes = [100.0 + i * 10 for i in range(30)]
        sig = generate_signal("a", closes, volumes)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, [100.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": ([10.0 + i * 0.5 for i in range(30)],
                   [100.0 + i * 10 for i in range(30)]),
            "down": ([15.0 - i * 0.5 for i in range(30)],
                     [100.0 + i * 10 for i in range(30)]),
        }
        results = screen_universe(universe)
        self.assertGreater(len(results), 0)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": ([10.0 + i * 0.5 for i in range(30)],
                   [100.0 + i * 10 for i in range(30)]),
            "down": ([15.0 - i * 0.5 for i in range(30)],
                     [100.0 + i * 10 for i in range(30)]),
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        volumes = [100.0 + i * 10 for i in range(30)]
        sig = generate_signal("a", closes, volumes)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        closes = [10.0 + i * 0.5 for i in range(30)]
        volumes = [100.0 + i * 10 for i in range(30)]
        sig = generate_signal("a", closes, volumes)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)