#!/usr/bin/env python3
"""
tuixue_v3/drawdown_recovery.py
Ship 18/100 — 回撤恢复策略

设计:
当组合触发总回撤 (例如 -15% warning / -25% block) 时, 不应一刀切全部平仓,
而是按"恢复阶梯"逐步减仓 → 恢复 → 再加仓。

回撤档位 (以 initial_capital 为基准):
- Tier 1: dd ≥ -5%    → 正常仓位 (position_factor = 1.0)
- Tier 2: -5% > dd ≥ -10%  → 减仓 30% (factor = 0.7)
- Tier 3: -10% > dd ≥ -15% → 减仓 50% (factor = 0.5)
- Tier 4: -15% > dd ≥ -20% → 减仓 70% (factor = 0.3) (warning 起点)
- Tier 5: dd < -20%   → 减仓 90% (factor = 0.1) (block 起点, 仅留观察仓)

恢复路径: 当 equity 创 60 日新高, 自动解锁一档 (factor 上调 0.2)

输出:
- DrawdownState: tier, factor, recovery_target, recovery_progress
- next_action: 加仓 / 维持 / 减仓

降级: 无 equity 历史 → tier=0, factor=1.0 (默认满仓)

2026-08-02 Ship 18 — 10000 轮迭代 P2 第八步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 回撤档位
# ═══════════════════════════════════════════════════════

# (drawdown 阈值上限 inclusive, position_factor)
# dd 是负值: -0.05 = -5%
TIERS = [
    (0.00, 1.0),    # dd >= 0 (盈利) → 满仓
    (-0.05, 1.0),   # 0 > dd >= -5% → 满仓
    (-0.10, 0.7),   # -5% > dd >= -10% → 7 成
    (-0.15, 0.5),   # -10% > dd >= -15% → 5 成
    (-0.20, 0.3),   # -15% > dd >= -20% → 3 成 (warning 起点)
    (-1.01, 0.1),   # dd < -20% → 1 成 (block)
]


@dataclass
class DrawdownState:
    """回撤恢复状态"""
    tier: int                       # 0~5
    drawdown: float                 # 当前回撤 (负值)
    position_factor: float          # 建议仓位系数
    recovery_target: float          # 恢复到下一档需要的回撤幅度 (正数)
    recovery_progress: float        # 0~1, 当前距下一档解锁的进度
    action: str                     # "increase" / "maintain" / "decrease"
    high_water_60d: float           # 60 日最高 equity
    reasons: list[str]


# ═══════════════════════════════════════════════════════
# 核心
# ═══════════════════════════════════════════════════════

def get_tier(drawdown: float) -> int:
    """给定当前回撤 (负值), 返回档位 0~5"""
    for i, (threshold, _) in enumerate(TIERS):
        if drawdown >= threshold:
            return i
    return len(TIERS) - 1


def get_factor(tier: int) -> float:
    """tier → 仓位系数"""
    tier = max(0, min(len(TIERS) - 1, tier))
    return TIERS[tier][1]


def evaluate_recovery(
    current_equity: float,
    initial_capital: float,
    equity_history: Optional[list[float]] = None,
) -> DrawdownState:
    """评估回撤恢复状态

    Args:
        current_equity: 当前总市值
        initial_capital: 初始资金 (基准)
        equity_history: 近 N 日 equity 曲线 (用于算 high water mark)

    Returns:
        DrawdownState
    """
    reasons: list[str] = []

    if initial_capital <= 0:
        return DrawdownState(
            tier=0, drawdown=0.0, position_factor=1.0,
            recovery_target=0.0, recovery_progress=1.0,
            action="maintain", high_water_60d=0.0,
            reasons=["初始资金无效"],
        )

    # 当前回撤
    dd = (current_equity - initial_capital) / initial_capital
    tier = get_tier(dd)
    factor = get_factor(tier)

    # 60 日高点 (基于历史 equity, 不夹 initial_capital — 防止虚高)
    if equity_history and len(equity_history) > 0:
        hwm = max(equity_history[-60:]) if len(equity_history) >= 60 else max(equity_history)
    else:
        hwm = max(current_equity, initial_capital)

    # 恢复目标: 达到上一档 (tier-1) 需要的回撤幅度
    if tier == 0:
        recovery_target = 0.0
        recovery_progress = 1.0
        action = "maintain"
    else:
        # 上一档阈值 (TIERS[tier-1][0])
        prev_threshold = TIERS[tier - 1][0]  # 例如 tier=2 → prev=-0.05
        # 当前 dd 是负数, 要恢复到 prev_threshold, 需要 dd 上升到 prev_threshold
        # recovery_target = 当前需要"涨回去"多少 (绝对值)
        # 简化: 直接用 dd 距 prev_threshold 的距离
        recovery_target = abs(prev_threshold - dd)
        # progress = 1 - 当前 dd / 上一档 dd (越接近 0 → progress 越大)
        # 实际: progress = (initial_capital + dd * initial) / initial_capital → 距离盈亏平衡
        # 简化: progress 用 high water mark 当目标
        if hwm > 0:
            recovery_progress = max(0.0, min(1.0, current_equity / hwm))
        else:
            recovery_progress = 0.0

        # 行动建议: 60 日新高 → 解锁一档 (调高 factor)
        is_new_high = current_equity >= hwm * 0.999
        if is_new_high and tier > 0:
            new_factor = min(1.0, factor + 0.2)
            action = "increase"
            reasons.append(f"接近 60 日高点 {hwm:.0f}, 解锁 +0.2 → {new_factor:.2f}")
            factor = new_factor
        elif tier > 0:
            action = "decrease" if factor < 1.0 else "maintain"
            reasons.append(f"未恢复, 维持 {factor:.2f} 仓位")
        else:
            action = "maintain"

    reasons.insert(0, f"回撤 {dd:.2%} → Tier {tier} (factor {factor:.2f})")

    return DrawdownState(
        tier=tier,
        drawdown=round(dd, 4),
        position_factor=round(factor, 4),
        recovery_target=round(recovery_target, 4),
        recovery_progress=round(recovery_progress, 4),
        action=action,
        high_water_60d=round(hwm, 2),
        reasons=reasons,
    )


def suggest_position_size(
    base_position: float,
    current_equity: float,
    initial_capital: float,
    equity_history: Optional[list[float]] = None,
) -> float:
    """根据回撤状态调整仓位

    Args:
        base_position: 策略原本建议的仓位 (0~1)
        current_equity: 当前组合市值
        initial_capital: 初始资金
        equity_history: equity 曲线

    Returns:
        调整后仓位 (0~1) = base × position_factor
    """
    state = evaluate_recovery(current_equity, initial_capital, equity_history)
    return round(base_position * state.position_factor, 4)
