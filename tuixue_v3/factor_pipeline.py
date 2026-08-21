#!/usr/bin/env python3
"""
tuixue_v3/factor_pipeline.py
Ship 11/100 — 多因子融合 pipeline

把 ships 7-9 的 3 类因子 (板块轮动 / 事件 / 新闻情绪) + 通用动量反转
合并成单只股票的 composite_score, 给 ai_scoring 做前置特征。

设计:
- 5 类因子:
  1) sector_rotation: 来自板块层的动量反转
  2) event_factors:   来自龙虎榜 / 大宗交易 / 调研
  3) news_sentiment:  5 维新闻情绪
  4) momentum:        个股 N 日动量
  5) volatility:      个股近 N 日波动率
- 每类因子给 -1~+1 的贡献分 (无量纲)
- 综合分 = sum(weight * class_score), 缺数据的类权重按比例重分配

降级: 任何一类因子缺失/失败 → 该类分=0, 权重按 1.0 重归一, 不报错。

2026-08-02 Ship 11 — 10000 轮迭代 P2 第一步
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class FactorScore:
    """单只股票的多因子综合分

    注: 5 类因子值默认 None = 缺数据 (而不是 0.0); 0.0 是合法值
    (例如情绪中性、波动率为 0)。
    """
    code: str
    sector_rotation: Optional[float] = None
    event: Optional[float] = None
    sentiment: Optional[float] = None
    momentum: Optional[float] = None
    volatility: Optional[float] = None
    composite: float = 0.0            # 加权综合分
    confidence: float = 0.0           # 0~1, 多少类因子有数据
    rank: int = 0
    has_data: bool = False
    components: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# 单类因子内部打分 (无量纲到 -1~+1)
# ═══════════════════════════════════════════════════════

def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _saturate(x: float, scale: float) -> float:
    """x/(|x|+scale) 压到 -1~+1, 保序"""
    if scale <= 0:
        return _clamp(x)
    return _clamp(x / (abs(x) + scale))


def _score_sector_rotation(rotation_score: float) -> float:
    """板块轮动综合分 (-1~+1) → 因子贡献 (-1~+1)"""
    return _clamp(rotation_score)


def _score_event(institution_net: float, hot_money_net: float,
                 block_premium: float, investigate: int,
                 lhb_reversal: float) -> float:
    """5 事件因子 → 单值 (-1~+1)

    权: 机构 0.30 / 游资 0.20 / 大宗 0.20 / 调研 0.10 / 反转 0.20
    """
    inst = _saturate(institution_net, 5000)           # ±5000 万 → ±0.5
    hot = _saturate(hot_money_net, 3000)              # ±3000 万 → ±0.5
    premium = _clamp(block_premium * 4)               # ±25% → ±1.0
    density = _clamp(investigate / 20.0)              # 0~20 次 → 0~1
    reversal = _clamp(lhb_reversal / 10.0)            # ±10% → ±1.0
    return _clamp(0.30 * inst + 0.20 * hot + 0.20 * premium
                  + 0.10 * density + 0.20 * reversal)


def _score_sentiment(sentiment: float, confidence: float) -> float:
    """新闻情绪 (-1~+1, conf 0~1) → 因子贡献"""
    return _clamp(sentiment * max(0.3, confidence))


def _score_momentum(ret_n: float) -> float:
    """N 日收益率 → 动量因子 (-1~+1)"""
    # ±20% → ±0.5
    return _clamp(ret_n / 0.20)


def _score_volatility(vol_n: float) -> float:
    """近 N 日日收益率 std (年化前) → 波动率因子 (低波动加分)

    vol=0 → +1, vol=0.03 → 0, vol=0.05 → -1 (线性)
    """
    return _clamp(1.0 - vol_n / 0.025)


# ═══════════════════════════════════════════════════════
# 综合分
# ═══════════════════════════════════════════════════════

# 默认权重 (归一化前, 缺数据的会重归一)
_DEFAULT_WEIGHTS = {
    "sector_rotation": 0.20,
    "event":           0.25,
    "sentiment":       0.20,
    "momentum":        0.20,
    "volatility":      0.15,
}


def _safe_normalize(scores: dict, weights: dict) -> tuple[float, float]:
    """weights 按 1.0 重归一 (跳过 None 类), 返回 (composite, confidence)"""
    valid_keys = [k for k in scores if scores[k] is not None]
    if not valid_keys:
        return 0.0, 0.0
    w_sum = sum(weights[k] for k in valid_keys)
    if w_sum <= 0:
        # 极端: 所有权重都为 0, 退化为均匀权重
        return sum(scores[k] for k in valid_keys) / len(valid_keys), \
               len(valid_keys) / len(_DEFAULT_WEIGHTS)
    composite = sum(scores[k] * weights[k] for k in valid_keys) / w_sum
    confidence = len(valid_keys) / len(_DEFAULT_WEIGHTS)
    return _clamp(composite), confidence


def composite_score(f: FactorScore, weights: Optional[dict] = None) -> FactorScore:
    """计算综合分 + 置信度, in-place 返回"""
    w = weights or _DEFAULT_WEIGHTS
    scores = {
        "sector_rotation": f.sector_rotation,
        "event":           f.event,
        "sentiment":       f.sentiment,
        "momentum":        f.momentum,
        "volatility":      f.volatility,
    }
    composite, conf = _safe_normalize(scores, w)
    f.composite = round(composite, 4)
    f.confidence = round(conf, 4)
    f.has_data = any(v is not None for v in scores.values())
    return f


# ═══════════════════════════════════════════════════════
# 集成入口 — 从板块 / 事件 / 新闻 API 喂入
# ═══════════════════════════════════════════════════════

def build_from_components(
    code: str,
    *,
    sector_rotation: Optional[float] = None,
    event_components: Optional[dict] = None,
    sentiment_components: Optional[dict] = None,
    ret_n: Optional[float] = None,
    vol_n: Optional[float] = None,
) -> FactorScore:
    """从原始组件拼 FactorScore + 算综合分

    Args:
        code: 股票代码
        sector_rotation: 板块轮动综合分 (-1~+1), None = 缺
        event_components: {institution_net, hot_money_net, block_premium,
                            investigate, lhb_reversal} 或 None
        sentiment_components: {sentiment, confidence} 或 None
        ret_n: N 日累计收益率 (例如 0.05 = +5%), None = 缺
        vol_n: N 日日收益率标准差 (例如 0.02 = 2%), None = 缺
    """
    f = FactorScore(code=code)

    # sector_rotation
    if sector_rotation is not None:
        f.sector_rotation = round(_score_sector_rotation(sector_rotation), 4)
    # event
    if event_components:
        f.event = round(_score_event(
            float(event_components.get("institution_net", 0.0)),
            float(event_components.get("hot_money_net", 0.0)),
            float(event_components.get("block_premium", 0.0)),
            int(event_components.get("investigate", 0)),
            float(event_components.get("lhb_reversal", 0.0)),
        ), 4)
    # sentiment
    if sentiment_components:
        f.sentiment = round(_score_sentiment(
            float(sentiment_components.get("sentiment", 0.0)),
            float(sentiment_components.get("confidence", 0.0)),
        ), 4)
    # momentum / volatility
    if ret_n is not None:
        f.momentum = round(_score_momentum(ret_n), 4)
    if vol_n is not None:
        f.volatility = round(_score_volatility(vol_n), 4)

    # 留 raw 值便于调试
    f.components = {
        "sector_rotation": sector_rotation,
        "event": event_components or {},
        "sentiment": sentiment_components or {},
        "ret_n": ret_n,
        "vol_n": vol_n,
    }

    return composite_score(f)


def build_minimal(code: str, **kwargs) -> FactorScore:
    """最简构造 — 任意子类因子都可不传, 走缺数据降级"""
    return build_from_components(code, **kwargs)


def rank_scores(scores: list[FactorScore], top_n: Optional[int] = None) -> list[FactorScore]:
    """按 composite 降序, 写回 rank 字段"""
    sorted_scores = sorted(scores, key=lambda s: s.composite, reverse=True)
    for i, s in enumerate(sorted_scores, 1):
        s.rank = i
    out = sorted_scores[:top_n] if top_n else sorted_scores
    return out


# 因子分 → 简易解释 (给前端 tooltip)
def explain(f: FactorScore) -> str:
    """单只股票因子贡献的文本解释"""
    parts = []
    for label, val in (
        ("板块", f.sector_rotation),
        ("事件", f.event),
        ("情绪", f.sentiment),
        ("动量", f.momentum),
        ("波动", f.volatility),
    ):
        if val is not None and abs(val) > 0.1:
            sign = "+" if val > 0 else ""
            parts.append(f"{label}{sign}{val:.2f}")
    if not parts:
        return f"综合 {f.composite:+.2f} (无显著因子)"
    return f"{' '.join(parts)} → 综合 {f.composite:+.2f}"


def to_dict_list(scores: list[FactorScore]) -> list[dict]:
    """FactorScore → JSON dict"""
    return [asdict(s) for s in scores]