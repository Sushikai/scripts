#!/usr/bin/env python3
"""
tuixue_v3/factor_leaderboard.py
Ship 53/100 — 因子排行榜 (Factor Leaderboard)

设计:
对所有因子按 IC/IR 排序, 输出排行榜:
- rank, factor, ic, ir, t_stat, status
- 头部 (top 3), 底部 (bottom 3)
- 状态: 活跃/弃用/警告

输入: {factor: IC series}
输出: 排序后的列表

2026-08-03 Ship 53 — 10000 轮迭代 P4 第十三步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class FactorEntry:
    rank: int
    factor: str
    ic_mean: float
    ir: float
    t_stat: float
    hit_rate: float
    n: int
    status: str      # "active" / "warning" / "deprecated"


@dataclass
class Leaderboard:
    entries: list[FactorEntry]
    deprecated: list[str]
    top: list[FactorEntry]
    bottom: list[FactorEntry]

    def to_dict(self) -> dict:
        return {
            "entries": [
                {
                    "rank": e.rank, "factor": e.factor,
                    "ic_mean": e.ic_mean, "ir": e.ir,
                    "t_stat": e.t_stat, "hit_rate": e.hit_rate,
                    "n": e.n, "status": e.status,
                }
                for e in self.entries
            ],
            "deprecated": list(self.deprecated),
            "top": [{"factor": e.factor, "ir": e.ir} for e in self.top],
            "bottom": [{"factor": e.factor, "ir": e.ir} for e in self.bottom],
        }


# ═══════════════════════════════════════════════════════
# 构造
# ═══════════════════════════════════════════════════════

def build_leaderboard(
    ic_dict: dict[str, list[float]],
    *,
    active_threshold: float = 0.5,    # IR > 0.5 → active
    warning_threshold: float = 0.2,    # IR > 0.2 → warning
) -> Leaderboard:
    """构造排行榜

    Args:
        ic_dict: {factor_name: IC series}
        active_threshold: IR >= 此值 → active
        warning_threshold: IR >= 此值 → warning, 否则 deprecated
    """
    entries: list[FactorEntry] = []

    for i, (factor, series) in enumerate(ic_dict.items()):
        n = len(series)
        if n == 0:
            entries.append(FactorEntry(
                rank=i + 1, factor=factor, ic_mean=0.0, ir=0.0,
                t_stat=0.0, hit_rate=0.0, n=0, status="deprecated",
            ))
            continue

        mu = statistics.mean(series)
        sigma = statistics.stdev(series) if n > 1 else 0.0
        ir = mu / sigma if sigma > 0 else 0.0
        t_stat = mu / sigma * math.sqrt(n) if sigma > 0 else 0.0
        hit_rate = sum(1 for v in series if v > 0) / n

        if abs(ir) >= active_threshold and abs(t_stat) >= 2.0:
            status = "active"
        elif abs(ir) >= warning_threshold:
            status = "warning"
        else:
            status = "deprecated"

        entries.append(FactorEntry(
            rank=i + 1, factor=factor,
            ic_mean=round(mu, 4),
            ir=round(ir, 4),
            t_stat=round(t_stat, 4),
            hit_rate=round(hit_rate, 4),
            n=n, status=status,
        ))

    # 按 IR |..| 降序排
    entries.sort(key=lambda e: abs(e.ir), reverse=True)
    # 重新赋 rank
    for i, e in enumerate(entries):
        e.rank = i + 1

    deprecated = [e.factor for e in entries if e.status == "deprecated"]
    top = entries[:3]
    bottom = list(reversed(entries[-3:]))   # 最差在前

    return Leaderboard(
        entries=entries, deprecated=deprecated,
        top=top, bottom=bottom,
    )


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(board: Leaderboard) -> str:
    """人类可读"""
    out = [
        f"Factors: {len(board.entries)}, "
        f"active: {sum(1 for e in board.entries if e.status == 'active')}, "
        f"deprecated: {len(board.deprecated)}"
    ]
    out.append("Top:")
    for e in board.top:
        out.append(f"  #{e.rank} {e.factor}: IR={e.ir:+.3f} ({e.status})")
    return "\n".join(out)


def active_factors(board: Leaderboard) -> list[str]:
    return [e.factor for e in board.entries if e.status == "active"]


def usable_factors(board: Leaderboard) -> list[str]:
    """active + warning"""
    return [e.factor for e in board.entries if e.status != "deprecated"]
