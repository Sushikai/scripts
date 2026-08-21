#!/usr/bin/env python3
"""
test_strat_era14_dual_momentum.py
Ship 70 单元测试 — 双重动量
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era14_dual_momentum import (
    DualMomentumSignal, period_return, cross_section_returns,
    generate_signal, dual_screen, rank_by_combined,
    filter_buys, filter_cash, summarize,
)


class TestPeriodReturn(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 11.0, 12.0, 13.0]
        # lookback=2: past=prices[-3]=11.0, curr=prices[-1]=13.0
        # ret = (13/11) - 1 ≈ 0.1818
        self.assertAlmostEqual(period_return(prices, 2), 2.0/11.0)

    def test_short(self):
        self.assertIsNone(period_return([10.0, 11.0], 5))


class TestCrossSectionReturns(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 - i * 0.05 for i in range(100)],
        }
        rets = cross_section_returns(universe, lookback=60)
        self.assertEqual(len(rets), 2)
        self.assertGreater(rets["a"], 0)
        self.assertLess(rets["b"], 0)


class TestGenerateSignal(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(100)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertGreater(sig.abs_return, 0)
        self.assertTrue(sig.abs_pass)


class TestDualScreen(unittest.TestCase):
    def test_buys(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],   # 涨
            "b": [10.0 + i * 0.05 for i in range(100)],  # 也涨 (弱)
            "c": [10.0 - i * 0.05 for i in range(100)],  # 跌
        }
        sigs = dual_screen(universe)
        self.assertEqual(len(sigs), 3)
        # a + b 应 = buy, c 应 = sell
        codes_buy = {s.code for s in sigs if s.side == "buy"}
        self.assertIn("a", codes_buy)
        self.assertIn("c", {s.code for s in sigs if s.side == "sell"})

    def test_empty(self):
        sigs = dual_screen({})
        self.assertEqual(sigs, [])


class TestRankByCombined(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 + i * 0.05 for i in range(100)],
        }
        sigs = dual_screen(universe)
        ranked = rank_by_combined(sigs)
        self.assertEqual(ranked[0].code, "a")     # 强动量在前


class TestFilter(unittest.TestCase):
    def test_buys(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 - i * 0.05 for i in range(100)],
        }
        sigs = dual_screen(universe)
        buys = filter_buys(sigs)
        self.assertGreater(len(buys), 0)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
        }
        sigs = dual_screen(universe)
        s = summarize(sigs)
        self.assertIn("Dual Momentum", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(100)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)