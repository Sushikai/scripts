#!/usr/bin/env python3
"""
test_factor_leaderboard.py
Ship 53 单元测试 — 因子排行榜
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_leaderboard import (
    FactorEntry, Leaderboard,
    build_leaderboard, summarize, active_factors, usable_factors,
)


class TestBuild(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.04, 0.05, 0.06, 0.07, 0.04, 0.05, 0.06, 0.04] * 5,
            "b": [-0.02] * 50,
            "c": [0.01] * 20,   # 小样本
        }
        lb = build_leaderboard(ic_dict)
        self.assertEqual(len(lb.entries), 3)

    def test_ranking(self):
        # std 必须大不同: strong 的 std 很小 (稳定)
        ic_dict = {
            "weak": [0.01, -0.01, 0.02, -0.02, 0.01, -0.01] * 5,    # mean≈0, IR 小
            "strong": [0.10, 0.11, 0.10, 0.09, 0.10, 0.11] * 5,    # mean=0.10, std=0.007
            "medium": [0.05, -0.05, 0.06, -0.04, 0.05] * 6,
        }
        lb = build_leaderboard(ic_dict)
        # strong 的 IR 应该最大
        self.assertEqual(lb.entries[0].factor, "strong")

    def test_status(self):
        ic_dict = {
            "good": [0.08, 0.09, 0.07, 0.08, 0.09, 0.07, 0.08] * 8,  # 56 个
            "weak": [0.005, 0.004, 0.006, 0.005, 0.004, 0.006, 0.005] * 8,
        }
        lb = build_leaderboard(ic_dict)
        good = next(e for e in lb.entries if e.factor == "good")
        # good 应 high IR, t_stat > 2
        self.assertEqual(good.status, "active")

    def test_no_data(self):
        ic_dict = {"empty": []}
        lb = build_leaderboard(ic_dict)
        self.assertEqual(lb.entries[0].factor, "empty")
        self.assertEqual(lb.entries[0].status, "deprecated")

    def test_top_bottom(self):
        ic_dict = {
            "a": [0.10] * 30,
            "b": [0.05] * 30,
            "c": [0.02] * 30,
            "d": [0.01] * 30,
        }
        lb = build_leaderboard(ic_dict)
        self.assertEqual(lb.top[0].factor, "a")
        # bottom 应是 IR 最小的
        self.assertEqual(lb.bottom[0].factor, "d")


class TestToDict(unittest.TestCase):
    def test_basic(self):
        ic_dict = {"a": [0.05] * 30}
        lb = build_leaderboard(ic_dict)
        d = lb.to_dict()
        self.assertIn("entries", d)
        self.assertIn("deprecated", d)
        self.assertIn("top", d)
        self.assertIn("bottom", d)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        ic_dict = {
            "a": [0.10] * 30,
            "b": [0.05] * 30,
        }
        lb = build_leaderboard(ic_dict)
        s = summarize(lb)
        self.assertIn("Factors", s)
        self.assertIn("Top:", s)


class TestActiveAndUsable(unittest.TestCase):
    def test_active_factors(self):
        # good: 高 IC, 低 std → active
        # weak: IC ~ 0
        ic_dict = {
            "good": [0.10, 0.11, 0.09, 0.10, 0.11, 0.10] * 8,
            "weak": [0.01, -0.01, 0.02, -0.02] * 8,
        }
        lb = build_leaderboard(ic_dict)
        active = active_factors(lb)
        self.assertIn("good", active)
        self.assertNotIn("weak", active)

    def test_usable_includes_warning(self):
        ic_dict = {
            "good": [0.10, 0.12, 0.08] * 10,
            "warn": [0.03, 0.025, 0.035] * 10,
            "dead": [0.001, 0.001, 0.001] * 10,
        }
        lb = build_leaderboard(ic_dict)
        usable = usable_factors(lb)
        # 不应包含 dead
        self.assertNotIn("dead", usable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
