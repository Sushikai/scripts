#!/usr/bin/env python3
"""
tuixue_v3/strat_era03_sector_rotation.py
Ship 59/100 — 量化 era 2026 高级策略 #3

Sector Rotation Strategy (行业轮动策略)

设计:
选择当前最强的 N 个行业, 行业内等权持仓:
- 行业内 momentum ranking (周/月)
- 行业 cross-sectional z-score
- 行业反转信号 (从弱转强 → 加)

输入: {industry: (momentum_score, change_rate, vol_ratio)}
输出: 排序后的 industry 列表 + 行业内 picks

2026-08-03 Ship 59 — 10000 轮迭代 P5 第四步
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class SectorStats:
    name: str
    momentum: float         # -1 ~ 1
    change_rate: float      # 行业涨跌
    vol_ratio: float        # 量比 (1.0 = 均量)
    n_stocks: int
    z_score: float = 0.0    # 跨行业 z 分数

    def composite_score(self) -> float:
        """复合分"""
        # 0.5 × momentum + 0.3 × change_rate + 0.2 × (vol_ratio - 1)
        return 0.5 * self.momentum + 0.3 * self.change_rate + 0.2 * (self.vol_ratio - 1.0)


@dataclass
class SectorPick:
    sector: str
    composite: float
    rank: int
    weight: float


@dataclass
class RotationResult:
    sectors: list[SectorPick]
    top_sectors: list[str]
    n_top: int

    def to_dict(self) -> dict:
        return {
            "sectors": [
                {"sector": s.sector, "composite": s.composite,
                 "rank": s.rank, "weight": s.weight}
                for s in self.sectors
            ],
            "top_sectors": list(self.top_sectors),
            "n_top": self.n_top,
        }


# ═══════════════════════════════════════════════════════
# 构建
# ═══════════════════════════════════════════════════════

def build_rotation(
    sector_stats: dict[str, SectorStats],
    *,
    n_top: int = 3,
) -> RotationResult:
    """构造行业轮动结果"""
    if not sector_stats:
        return RotationResult(
            sectors=[], top_sectors=[], n_top=n_top,
        )

    sectors_list = list(sector_stats.values())

    # 跨行业 z-score (基于 composite)
    composites = [s.composite_score() for s in sectors_list]
    mu = statistics.mean(composites)
    sigma = statistics.stdev(composites) if len(composites) > 1 else 0.0
    for s in sectors_list:
        s.z_score = (s.composite_score() - mu) / sigma if sigma > 0 else 0.0

    # 按 composite 排序
    sectors_list.sort(key=lambda s: s.composite_score(), reverse=True)

    # Top N
    top = sectors_list[:n_top]

    # 权重 (归一化到 1)
    weights_raw = [s.composite_score() for s in top]
    # 转正
    weights_raw = [max(w, 0.0) for w in weights_raw]
    total = sum(weights_raw) or 1.0

    picks: list[SectorPick] = []
    for rank, s in enumerate(top, start=1):
        w = weights_raw[rank - 1] / total
        picks.append(SectorPick(
            sector=s.name,
            composite=round(s.composite_score(), 4),
            rank=rank,
            weight=round(w, 4),
        ))

    # 全部 sorted 后, 也加入
    all_picks = []
    for rank, s in enumerate(sectors_list, start=1):
        if rank <= n_top:
            continue
        all_picks.append(SectorPick(
            sector=s.name,
            composite=round(s.composite_score(), 4),
            rank=rank,
            weight=0.0,
        ))

    return RotationResult(
        sectors=picks + all_picks,
        top_sectors=[p.sector for p in picks],
        n_top=n_top,
    )


# ═══════════════════════════════════════════════════════
# 反转识别
# ═══════════════════════════════════════════════════════

def detect_reversal(
    prev_sector_stats: dict[str, SectorStats],
    curr_sector_stats: dict[str, SectorStats],
) -> list[tuple[str, str]]:
    """检测反转 (从弱转强 或 从强转弱)

    Returns: [(sector, "up" / "down"), ...]
    """
    if not prev_sector_stats or not curr_sector_stats:
        return []

    n = len(prev_sector_stats)
    threshold = max(2, n // 2)   # 至少 2 档差距 (或半数)

    prev_ranks = _rank_by_composite(prev_sector_stats)
    curr_ranks = _rank_by_composite(curr_sector_stats)

    out = []
    for sec in curr_ranks:
        if sec not in prev_ranks:
            continue
        prev_r = prev_ranks[sec]
        curr_r = curr_ranks[sec]
        # prev_r 大 (弱) → curr_r 小 (强) → up reversal
        if prev_r - curr_r >= threshold:
            out.append((sec, "up"))
        elif curr_r - prev_r >= threshold:
            out.append((sec, "down"))
    return out


def _rank_by_composite(stats: dict[str, SectorStats]) -> dict[str, int]:
    items = sorted(stats.items(), key=lambda kv: kv[1].composite_score(), reverse=True)
    return {name: i + 1 for i, (name, _) in enumerate(items)}


# ═══════════════════════════════════════════════════════
# 行业内选股
# ═══════════════════════════════════════════════════════

@dataclass
class StockPick:
    code: str
    score: float
    weight: float


def select_stocks_in_sector(
    candidates: list[tuple[str, float]],     # [(code, raw_score), ...]
    *,
    top_n: int = 5,
) -> list[StockPick]:
    """行业内选股 (按 raw_score)"""
    sorted_cands = sorted(candidates, key=lambda c: c[1], reverse=True)
    top = sorted_cands[:top_n]

    if not top:
        return []

    total = sum(score for _, score in top) or 1.0
    return [
        StockPick(
            code=code,
            score=round(score, 4),
            weight=round(max(score, 0) / total, 4),
        )
        for code, score in top
    ]


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: RotationResult) -> str:
    out = [f"Top {r.n_top} sectors:"]
    for s in r.sectors:
        if s.rank <= r.n_top:
            out.append(f"  #{s.rank} {s.sector}: composite={s.composite:+.3f} w={s.weight:.2%}")
    return "\n".join(out)
