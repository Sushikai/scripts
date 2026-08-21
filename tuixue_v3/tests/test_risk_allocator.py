#!/usr/bin/env python3
"""
test_risk_allocator.py
Ship 22 单元测试 — 风险感知仓位分配
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.risk_allocator import (
    CandidatePick, Allocation, AllocationResult,
    allocate, to_dict,
)


def cp(code, score=0.5, sector="A", pct=0.10, price=10.0):
    return CandidatePick(code=code, score=score, sector=sector,
                         suggested_pct=pct, price=price)


class TestAllocate(unittest.TestCase):
    def test_basic(self):
        cands = [cp("A", 0.9, pct=0.10), cp("B", 0.7, pct=0.10), cp("C", 0.5, pct=0.10)]
        r = allocate(100000, cands, max_total_pct=0.50, cash_reserve_pct=0.10)
        print(r.summary())
        # deploy_budget = 100000 * 0.5 = 50000 (cash_reserve 不影响 max_total)
        # 3 只均分: 50000/3 ≈ 16666
        self.assertGreater(r.total_deployed, 0)
        self.assertLessEqual(r.total_deployed, 50000)

    def test_max_position(self):
        """单股不超过 max_position_pct"""
        cands = [cp("A", pct=0.50)]  # 策略建议 50%, 上限 20%
        r = allocate(100000, cands, max_position_pct=0.20, max_total_pct=0.50)
        # A 应被截到 20%
        self.assertLessEqual(r.allocations[0].actual_pct, 0.20)

    def test_sector_concentration_limit(self):
        """同板块累计不能超过 max_sector_pct"""
        cands = [
            cp("A1", 0.9, sector="新能源", pct=0.20),
            cp("A2", 0.8, sector="新能源", pct=0.20),
            cp("A3", 0.7, sector="新能源", pct=0.20),
        ]
        r = allocate(100000, cands, max_position_pct=0.30,
                     max_sector_pct=0.40)
        # 新能源板块 ≤ 40000
        # A1 ≈ 30000 (单股上限), A2 ≈ 10000 (剩余板块), A3 → skip
        self.assertLessEqual(r.sector_allocated.get("新能源", 0), 40000)

    def test_score_order_priority(self):
        """高分先分 + 单股上限保证高分拿到更多"""
        cands = [cp("HI", 0.9, pct=0.15), cp("LO", 0.3, pct=0.15)]
        r = allocate(100000, cands, max_total_pct=0.50, max_position_pct=0.15)
        # HI 分到 15000 (cap), LO 拿剩余 ≈ 10000 (per_cand_cap)
        codes = [a.code for a in r.allocations]
        self.assertIn("HI", codes)
        # HI 拿到 cap 15000, LO 拿剩余
        hi_amt = next(a.amount for a in r.allocations if a.code == "HI")
        self.assertEqual(hi_amt, 15000)

    def test_no_price_skip_lots(self):
        """无价格 → 仅按金额记录, shares=0"""
        cands = [cp("A", pct=0.10, price=None)]
        r = allocate(100000, cands)
        self.assertEqual(r.allocations[0].shares, 0)
        self.assertGreater(r.allocations[0].amount, 0)

    def test_price_too_high_skip(self):
        """价格太高买不起 1 手 → skip"""
        cands = [cp("A", pct=0.10, price=100000)]  # 100 万一股
        r = allocate(100000, cands)
        self.assertEqual(len(r.allocations), 0)
        self.assertEqual(len(r.skipped), 1)

    def test_zero_capital(self):
        r = allocate(0, [cp("A", pct=0.10)])
        self.assertEqual(r.total_deployed, 0)
        self.assertIn("资金为 0", r.warnings)

    def test_lot_size_round(self):
        """shares 按 100 round"""
        cands = [cp("A", pct=0.10, price=11.0)]
        r = allocate(100000, cands)
        # amount ≈ 10000, shares = int(10000/11/100)*100 = 9*100 = 900
        self.assertEqual(r.allocations[0].shares % 100, 0)

    def test_cash_reserve(self):
        """现金预留"""
        cands = [cp("A", pct=0.50)]  # 想 50%, 但 cash reserve 10%
        r = allocate(100000, cands, max_total_pct=0.80, cash_reserve_pct=0.20)
        # deploy_budget = min(80000, 80000) = 80000
        self.assertLessEqual(r.total_deployed, 80000)
        self.assertGreater(r.cash_reserve, 0)

    def test_skipped_has_reasons(self):
        cands = [cp("A", pct=0.0)]  # 0 仓位
        r = allocate(100000, cands)
        self.assertEqual(len(r.skipped), 1)
        self.assertIn("0", r.skipped[0].reason)

    def test_to_dict(self):
        cands = [cp("A", 0.8, pct=0.10)]
        r = allocate(100000, cands)
        d = to_dict(r)
        self.assertIn("allocations", d)
        self.assertIn("sector_allocated", d)
        self.assertEqual(d["n_allocated"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
