#!/usr/bin/env python3
"""
test_event_factors.py
Ship 8 单元测试 — 龙虎榜 + 大宗交易 + 机构调研 5 事件因子
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.event_factors import (
    EventFactors, _DEFAULT_WEIGHTS,
    compute_institution_net_buy, compute_hot_money_net_buy,
    compute_block_trade_premium, compute_investigate_density,
    compute_lhb_reversal, composite_score, rank_factors,
    from_lhb_seat_data, to_dict_list, empty_factors,
)


class TestInstitutionNetBuy(unittest.TestCase):
    """机构席位净买"""

    def test_net_buy_positive(self):
        # 机构买 1 亿, 卖 5000 万 → 净买 5000 万
        self.assertEqual(
            compute_institution_net_buy(10000, 5000),
            5000.0,
        )

    def test_net_buy_negative(self):
        self.assertEqual(
            compute_institution_net_buy(2000, 8000),
            -6000.0,
        )

    def test_zero(self):
        self.assertEqual(compute_institution_net_buy(0, 0), 0.0)


class TestHotMoneyNetBuy(unittest.TestCase):
    """游资席位净买"""

    def test_basic(self):
        self.assertEqual(compute_hot_money_net_buy(3000, 1000), 2000.0)

    def test_negative(self):
        self.assertEqual(compute_hot_money_net_buy(500, 2000), -1500.0)


class TestBlockTradePremium(unittest.TestCase):
    """大宗交易溢价率"""

    def test_premium(self):
        # 11 元成交, 10 元市价 → +10%
        self.assertAlmostEqual(
            compute_block_trade_premium(11.0, 10.0),
            0.10,
            places=4,
        )

    def test_discount(self):
        # 9 元成交, 10 元市价 → -10%
        self.assertAlmostEqual(
            compute_block_trade_premium(9.0, 10.0),
            -0.10,
            places=4,
        )

    def test_zero_market(self):
        self.assertEqual(compute_block_trade_premium(10.0, 0), 0.0)

    def test_negative_market(self):
        self.assertEqual(compute_block_trade_premium(10.0, -1), 0.0)

    def test_significant_discount(self):
        """折价 > 5% 应被识别为负 alpha 信号"""
        result = compute_block_trade_premium(9.0, 10.0)
        self.assertLess(result, -0.05)


class TestInvestigateDensity(unittest.TestCase):
    """调研密度"""

    def test_normal(self):
        self.assertEqual(compute_investigate_density(10), 10)

    def test_zero(self):
        self.assertEqual(compute_investigate_density(0), 0)

    def test_negative_clamped(self):
        self.assertEqual(compute_investigate_density(-3), 0)


class TestLhbReversal(unittest.TestCase):
    """上榜后 5 日反转"""

    def test_rise(self):
        # 100 → 110 → +10%
        self.assertAlmostEqual(compute_lhb_reversal(100, 110), 10.0, places=2)

    def test_fall(self):
        self.assertAlmostEqual(compute_lhb_reversal(100, 90), -10.0, places=2)

    def test_zero_price(self):
        self.assertEqual(compute_lhb_reversal(0, 100), 0.0)


class TestCompositeScore(unittest.TestCase):
    """综合分"""

    def test_default_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(_DEFAULT_WEIGHTS.values()), 1.0, places=4)

    def test_empty_factors_score_zero(self):
        f = EventFactors(code="x")
        self.assertEqual(composite_score(f), 0.0)

    def test_high_values_score_high(self):
        f = EventFactors(
            code="x",
            institution_net_buy=10000,
            hot_money_net_buy=5000,
            block_trade_premium=0.10,
            investigate_density_30d=20,
            lhb_reversal_5d=10.0,
            has_data=True,
        )
        score = composite_score(f)
        self.assertGreater(score, 0.7)  # 高分 (>0.7)

    def test_negative_values_score_low(self):
        f = EventFactors(
            code="x",
            institution_net_buy=-5000,
            hot_money_net_buy=-3000,
            block_trade_premium=-0.10,
            investigate_density_30d=0,
            lhb_reversal_5d=-5.0,
            has_data=True,
        )
        score = composite_score(f)
        self.assertLess(score, 0.3)  # 低分 (<0.3)


class TestRankFactors(unittest.TestCase):
    """排序 + 排名"""

    def test_basic(self):
        factors = [
            EventFactors(code="A", institution_net_buy=5000, has_data=True),
            EventFactors(code="B", institution_net_buy=15000, has_data=True),
            EventFactors(code="C", institution_net_buy=10000, has_data=True),
        ]
        ranked = rank_factors(factors)
        self.assertEqual(ranked[0].code, "B")
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[1].code, "C")
        self.assertEqual(ranked[2].code, "A")


class TestFromLhbSeatData(unittest.TestCase):
    """从龙虎榜数据构造因子"""

    def test_basic(self):
        seats = [
            {"type": "机构专用席位", "buy": 8000, "sell": 2000},
            {"type": "顶级游资-赵老哥", "buy": 3000, "sell": 1000},
            {"type": "普通席位", "buy": 500, "sell": 200},  # 不计
        ]
        f = from_lhb_seat_data("600519", seats, investigate_count_30d=8)
        self.assertEqual(f.code, "600519")
        self.assertTrue(f.has_data)
        # 机构净买 = 8000 - 2000 = 6000
        self.assertEqual(f.institution_net_buy, 6000.0)
        # 游资净买 = 3000 - 1000 = 2000
        self.assertEqual(f.hot_money_net_buy, 2000.0)
        self.assertEqual(f.investigate_density_30d, 8)

    def test_with_block_trades(self):
        seats = [{"type": "机构专用席位", "buy": 1000, "sell": 500}]
        block_trades = [
            {"price": 9.0, "market_price": 10.0},  # -10%
            {"price": 11.0, "market_price": 10.0},  # +10%
        ]
        f = from_lhb_seat_data("000001", seats, block_trades=block_trades)
        # 平均 = (-0.10 + 0.10) / 2 = 0
        self.assertAlmostEqual(f.block_trade_premium, 0.0, places=4)

    def test_empty_seats(self):
        """空席位 + 0 调研 + 无大宗 → has_data=False"""
        f = from_lhb_seat_data("x", [])
        self.assertFalse(f.has_data)
        self.assertEqual(f.institution_net_buy, 0.0)
        self.assertEqual(f.hot_money_net_buy, 0.0)

    def test_investigate_only(self):
        """只传调研次数 → has_data=True (有数据)"""
        f = from_lhb_seat_data("x", [], investigate_count_30d=3)
        self.assertTrue(f.has_data)
        self.assertEqual(f.investigate_density_30d, 3)


class TestToDictList(unittest.TestCase):
    """序列化"""

    def test_serialization(self):
        f = EventFactors(code="x", institution_net_buy=5000, has_data=True)
        d = to_dict_list([f])[0]
        self.assertEqual(d["code"], "x")
        self.assertEqual(d["institution_net_buy"], 5000)
        self.assertTrue(d["has_data"])

    def test_empty(self):
        self.assertEqual(to_dict_list([]), [])


class TestEmptyFactors(unittest.TestCase):
    """空因子构造"""

    def test_returns_empty(self):
        f = empty_factors("x")
        self.assertEqual(f.code, "x")
        self.assertFalse(f.has_data)
        self.assertEqual(f.institution_net_buy, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
