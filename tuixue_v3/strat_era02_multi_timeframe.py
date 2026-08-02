#!/usr/bin/env python3
"""
tuixue_v3/strat_era02_multi_timeframe.py
Ship 58/100 — 量化 era 2026 高级策略 #2

Multi-Timeframe Strategy (多时间框架策略)

设计:
对同一只股票用多个时间维度打分, 然后融合:
- daily: 5 日动量
- weekly: 20 日动量
- monthly: 60 日动量
- quarterly: 120 日动量

融合方式:
- 加权平均 (默认等权)
- 一致信号加成 (所有 frame 同向 → boost)
- 冲突惩罚 (反向 → 抑制)

输出: 综合分数 + frame 详情

降级: 任一 frame 缺失 → 等权处理

2026-08-03 Ship 58 — 10000 轮迭代 P5 第三步
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════

FRAME_WINDOWS = {
    "daily": 5,
    "weekly": 20,
    "monthly": 60,
    "quarterly": 120,
}


@dataclass
class FrameScore:
    name: str
    window: int
    score: float            # -1 到 1 (动量分数)
    is_valid: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "window": self.window,
            "score": self.score,
            "is_valid": self.is_valid,
        }


@dataclass
class MultiTimeframeResult:
    code: str
    frames: list[FrameScore]
    combined_score: float
    consistency: float          # 0-1, 越高越一致
    alignment: str              # "aligned" / "mixed" / "diverged"
    confidence: float           # 0-1, 置信度

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "frames": [f.to_dict() for f in self.frames],
            "combined_score": self.combined_score,
            "consistency": self.consistency,
            "alignment": self.alignment,
            "confidence": self.confidence,
        }


# ═══════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════

def momentum_score(prices: list[float], window: int) -> float:
    """动量分数 (近 window 日)"""
    if len(prices) < window + 1 or window < 1:
        return 0.0
    start = prices[-window - 1]
    end = prices[-1]
    if start == 0:
        return 0.0
    # 简单百分比
    momentum = (end - start) / start
    # 限 [-0.5, 0.5], 即 ±50%
    return max(-1.0, min(1.0, momentum * 2))


def compute_frames(prices: list[float]) -> list[FrameScore]:
    """对单只股票算所有 frame"""
    out = []
    for name, window in FRAME_WINDOWS.items():
        score = momentum_score(prices, window)
        out.append(FrameScore(
            name=name, window=window,
            score=round(score, 4),
            is_valid=len(prices) >= window + 1,
        ))
    return out


# ═══════════════════════════════════════════════════════
# 融合
# ═══════════════════════════════════════════════════════

def combine_frames(
    code: str,
    frames: list[FrameScore],
    *,
    consistency_boost: float = 0.3,
    divergence_penalty: float = 0.3,
) -> MultiTimeframeResult:
    """融合多 frame"""
    valid = [f for f in frames if f.is_valid]
    if not valid:
        return MultiTimeframeResult(
            code=code, frames=frames,
            combined_score=0.0, consistency=0.0,
            alignment="diverged", confidence=0.0,
        )

    # 平均分
    avg_score = statistics.mean(f.score for f in valid)

    # 一致性: 都同号 = 1, 一半对半 = 0
    pos = sum(1 for f in valid if f.score > 0)
    neg = sum(1 for f in valid if f.score < 0)
    max_same = max(pos, neg)
    consistency = max_same / len(valid)

    # alignment
    if consistency >= 0.8:
        alignment = "aligned"
    elif consistency >= 0.5:
        alignment = "mixed"
    else:
        alignment = "diverged"

    # 一致 bonus / 分歧 penalty
    if pos > neg and avg_score > 0:
        combined = avg_score * (1 + consistency_boost * consistency)
    elif neg > pos and avg_score < 0:
        combined = avg_score * (1 + consistency_boost * consistency)
    else:
        combined = avg_score * (1 - divergence_penalty * (1 - consistency))

    # 限制
    combined = max(-1.0, min(1.0, combined))

    # 置信度: 一致性 × 样本数
    sample_factor = min(len(valid) / len(frames), 1.0)
    confidence = round(consistency * sample_factor, 4)

    return MultiTimeframeResult(
        code=code, frames=frames,
        combined_score=round(combined, 4),
        consistency=round(consistency, 4),
        alignment=alignment,
        confidence=confidence,
    )


def select_picks(
    candidates: list[tuple[str, list[float]]],
    *,
    top_n: int = 10,
) -> list[MultiTimeframeResult]:
    """对多只股票做 multi-frame 分析"""
    out = []
    for code, prices in candidates:
        frames = compute_frames(prices)
        result = combine_frames(code, frames)
        out.append(result)

    out.sort(key=lambda r: r.combined_score, reverse=True)
    return out[:top_n]


# ═══════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════

def summarize(r: MultiTimeframeResult) -> str:
    """人类可读"""
    frames_str = " ".join(f"{f.name}={f.score:+.2f}" for f in r.frames if f.is_valid)
    return (f"{r.code}: combined={r.combined_score:+.3f} "
            f"consistency={r.consistency:.0%} "
            f"alignment={r.alignment} "
            f"[{frames_str}]")
