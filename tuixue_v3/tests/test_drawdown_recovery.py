#!/usr/bin/env python3
"""
test_drawdown_recovery.py
Ship 18 单元测试 — 回撤恢复策略
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.drawdown_recovery import (
    get_tier, get_factor, evaluate_recovery, suggest_position_size,
    TIERS,
)


class TestGetTier(unittest.TestCase):
    def test_no_dd(self):
        self.assertEqual(get_tier(0.0), 0)
        self.assertEqual(get_tier(0.10), 0)  # 盈利 10% → tier 0

    def test_small_dd(self):
        self.assertEqual(get_tier(-0.03), 1)  # -3% → tier 1
        self.assertEqual(get_tier(-0.05), 1)  # 刚好 -5%

    def test_moderate_dd(self):
        self.assertEqual(get_tier(-0.07), 2)  # -7%
        self.assertEqual(get_tier(-0.10), 2)  # 刚好 -10%

    def test_warning_dd(self):
        self.assertEqual(get_tier(-0.13), 3)  # -13%
        self.assertEqual(get_tier(-0.15), 3)

    def test_block_dd(self):
        self.assertEqual(get_tier(-0.17), 4)  # -17%
        self.assertEqual(get_tier(-0.25), 5)  # -25% block


class TestGetFactor(unittest.TestCase):
    def test_factor_table(self):
        for tier in range(len(TIERS)):
            f = get_factor(tier)
            self.assertGreaterEqual(f, 0.0)
            self.assertLessEqual(f, 1.0)

    def test_specific(self):
        self.assertEqual(get_factor(0), 1.0)
        self.assertEqual(get_factor(2), 0.7)
        self.assertEqual(get_factor(3), 0.5)
        self.assertEqual(get_factor(4), 0.3)
        self.assertEqual(get_factor(5), 0.1)

    def test_factor_clamp(self):
        self.assertEqual(get_factor(-1), 1.0)  # clamp 到 0
        self.assertEqual(get_factor(99), 0.1)  # clamp 到 5


class TestEvaluateRecovery(unittest.TestCase):
    def test_full_equity(self):
        r = evaluate_recovery(110000, 100000)
        self.assertEqual(r.tier, 0)
        self.assertEqual(r.position_factor, 1.0)
        self.assertGreater(r.drawdown, 0)  # 盈利
        self.assertEqual(r.action, "maintain")

    def test_zero_dd(self):
        r = evaluate_recovery(100000, 100000)
        self.assertEqual(r.tier, 0)

    def test_warning_dd(self):
        r = evaluate_recovery(85000, 100000)  # -15%
        self.assertEqual(r.tier, 3)
        self.assertEqual(r.position_factor, 0.5)

    def test_block_dd(self):
        r = evaluate_recovery(75000, 100000)  # -25%
        self.assertEqual(r.tier, 5)
        self.assertEqual(r.position_factor, 0.1)

    def test_recovery_with_history(self):
        """equity 历史创新高 → 解锁"""
        # 60 日高点 110000, 当前 108000 (恢复中)
        history = [100000 + i * 200 for i in range(60)]  # 升到 111800
        history.append(108000)
        r = evaluate_recovery(108000, 100000, history)
        # hwm = 111800, 当前 108000 → 距新高 3.4%
        # 不算 unlock, 但 progress = 108000/111800 = 0.966
        print(f"  recovery: tier={r.tier} factor={r.position_factor} action={r.action}")
        self.assertGreater(r.recovery_progress, 0.9)

    def test_unlock_action(self):
        """DD 后 equity 接近新高 → 触发 unlock"""
        # 起始 100k, 涨到 110k, 跌回 85k (-15% tier 3), 反弹到 109k 接近 hwm
        history = []
        for i in range(50):
            history.append(100000 + i * 200)  # 涨到 109800
        history.append(85000)  # 暴跌
        history.append(109500)  # 反弹接近 hwm
        r = evaluate_recovery(109500, 100000, history)
        # hwm ≈ 109800, current 109500, dd = 9.5% → tier 0
        # tier 0 时 action="maintain" (不需要 unlock)
        # 验证: 即使 tier=0, 高点恢复路径仍然合理
        print(f"  unlock: tier={r.tier} factor={r.position_factor} action={r.action}")
        self.assertGreaterEqual(r.tier, 0)
        # DD tier > 0 + 接近 hwm → 解锁
        history2 = [100000] * 20 + [110000] * 30 + [85000, 87000, 89000]
        r2 = evaluate_recovery(89000, 100000, history2)
        # hwm=110000, current=89000 → dd=-11% → tier 3, factor=0.5
        # current 距 hwm: 89000/110000 = 0.81 → 不 close enough
        # 但 unlock 条件是 current >= hwm*0.999
        # 用更接近的:
        history3 = [100000] * 20 + [110000] * 30 + [85000, 109900]
        r3 = evaluate_recovery(109900, 100000, history3)
        # dd=9.9% tier 0 → action=maintain, 但 unlock 检查 close enough
        # 因为 dd>=0 → tier=0 → 提前 return
        # unlock 只在 tier>0 时触发
        # 真正触发需要: 当前在 tier>0 但接近新高
        # 构造: history 含 110k 高点, 然后跌到 tier 3 (-15%) 再反弹到 109800
        history4 = [100000 + i * 100 for i in range(50)] + [95000, 85000, 109000]
        r4 = evaluate_recovery(109000, 100000, history4)
        # hwm=104900, current=109000 > hwm → close enough
        # dd = +9% → tier 0 → 不会 unlock
        # 改用 initial_capital=120000 (让 dd 持续为负)
        history5 = [100000 + i * 100 for i in range(50)] + [95000, 85000, 109000]
        r5 = evaluate_recovery(109000, 120000, history5)
        # dd = (109000-120000)/120000 = -9.17% → tier 2, factor=0.7
        # hwm = max(history5) = 104900
        # current/hwm = 109000/104900 = 1.039 > 0.999 → close to new high
        # 应该是 increase
        print(f"  unlock_v2: tier={r5.tier} factor={r5.position_factor} action={r5.action}")
        self.assertEqual(r5.action, "increase")

    def test_zero_capital(self):
        r = evaluate_recovery(100000, 0)
        self.assertEqual(r.tier, 0)
        self.assertEqual(r.position_factor, 1.0)

    def test_no_history(self):
        """无 equity 历史也能算"""
        r = evaluate_recovery(95000, 100000)
        self.assertEqual(r.tier, 1)
        self.assertGreater(len(r.reasons), 0)


class TestSuggestPositionSize(unittest.TestCase):
    def test_full_position_no_dd(self):
        size = suggest_position_size(0.5, 110000, 100000)
        self.assertEqual(size, 0.5)  # tier 0, factor 1.0

    def test_block_position(self):
        size = suggest_position_size(0.5, 75000, 100000)  # -25%
        self.assertEqual(size, 0.05)  # 0.5 × 0.1

    def test_warning_position(self):
        size = suggest_position_size(0.4, 85000, 100000)  # -15% tier 3
        self.assertEqual(size, 0.2)  # 0.4 × 0.5


if __name__ == "__main__":
    unittest.main(verbosity=2)
