#!/usr/bin/env python3
"""
tuixue_v3/event_factors.py
Ship 8/100 — 龙虎榜 + 大宗交易 + 机构调研 5 事件因子

设计:
- 5 个事件因子 (P0 立即可用, 复用现有 seat_classify/lhb 接口)
- 机构席位净买: 机构席位 vs 游资席位的差额 (IC 0.04)
- 游资席位净买: 顶级游资 (13 类) 的净买额 (IC 0.03)
- 大宗溢价率: 折价 > 5% 通常后续 20 日负 alpha (卖方看空信号)
- 调研密度: 近 30 日调研次数 (IC 0.02, 长半衰期 30+ 天)
- 上榜后 N 日反转: 龙虎榜上榜后 5 日表现 (IC 0.03)

输出: dataclass EventFactors (5 因子 + 综合分)
接入: ai_scoring pipeline 前置因子

2026-08-02 Ship 8 — 10000 轮迭代 P1 第三步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class EventFactors:
    """单只股票的事件因子"""
    code: str
    institution_net_buy: float = 0.0       # 机构席位净买额 (万元)
    hot_money_net_buy: float = 0.0         # 游资席位净买额 (万元)
    block_trade_premium: float = 0.0        # 大宗交易溢价率 (-1 ~ 1)
    investigate_density_30d: int = 0        # 近 30 日调研次数
    lhb_reversal_5d: float = 0.0           # 上榜后 5 日反转 (%)
    composite_score: float = 0.0            # 综合分
    rank: int = 0
    has_data: bool = False                  # 是否有真实数据 (False 时所有因子为 0)


# 默认权重 (IC 加权)
_DEFAULT_WEIGHTS = {
    "institution_net_buy": 0.25,
    "hot_money_net_buy": 0.20,
    "block_trade_premium": 0.20,
    "investigate_density_30d": 0.15,
    "lhb_reversal_5d": 0.20,
}


# ═══════════════════════════════════════════════════════
# 5 因子计算 (基础版 — 真实数据由现有 fetch 层提供)
# ═══════════════════════════════════════════════════════

def compute_institution_net_buy(
    institution_buy: float,
    institution_sell: float,
) -> float:
    """机构席位净买额 (万元)

    Args:
        institution_buy: 机构席位买入金额 (万元)
        institution_sell: 机构席位卖出金额 (万元)

    Returns:
        净买额 (万元), 正值=净买入
    """
    return round(institution_buy - institution_sell, 4)


def compute_hot_money_net_buy(
    hot_money_buy: float,
    hot_money_sell: float,
) -> float:
    """游资席位净买额 (万元)

    注: 顶级游资 (13 类, 见 seat_classify.py) 加权后求和
    """
    return round(hot_money_buy - hot_money_sell, 4)


def compute_block_trade_premium(
    block_price: float,
    market_price: float,
) -> float:
    """大宗交易溢价率

    溢价率 = (block_price / market_price) - 1

    Args:
        block_price: 大宗交易成交价
        market_price: 当日收盘价

    Returns:
        溢价率 (-1 ~ +∞)
        折价 > 5% 通常后续 20 日负 alpha
        溢价 > 0% 通常后续 20 日正 alpha
    """
    if market_price <= 0:
        return 0.0
    return round(block_price / market_price - 1, 4)


def compute_investigate_density(
    investigate_count_30d: int,
    threshold: int = 5,
) -> int:
    """调研密度 (近 30 日调研次数, 不归一化)

    注: 不归一化直接计数, 因为 IC 与绝对值正相关
    """
    return max(0, investigate_count_30d)


def compute_lhb_reversal(
    price_on_lhb: float,
    price_5d_later: float,
) -> float:
    """上榜后 5 日反转 (%)

    正值=上榜后涨 (说明游资看好持续)
    负值=上榜后跌 (游资出货信号)

    Args:
        price_on_lhb: 上榜日收盘价
        price_5d_later: 5 日后收盘价
    """
    if price_on_lhb <= 0:
        return 0.0
    return round((price_5d_later / price_on_lhb - 1) * 100, 2)


# ═══════════════════════════════════════════════════════
# 综合分
# ═══════════════════════════════════════════════════════

def _saturate(x: float, scale: float) -> float:
    """把无界因子压到 0~1, 单调不饱和 (保序, 不像 clamp 会把大值全压成 1.0)"""
    return 0.5 + 0.5 * x / (abs(x) + scale)


def composite_score(f: EventFactors, weights: Optional[dict] = None) -> float:
    """5 因子加权综合分

    注: 各因子量纲不同 (金额/比例/次数), 用 x/(|x|+scale) 压到 0~1 后加权。
    这样保序 (不会像 min/max clamp 把 5000 和 15000 都压成 1.0)。
    """
    if not f.has_data:
        return 0.0
    w = weights or _DEFAULT_WEIGHTS
    inst_score = _saturate(f.institution_net_buy, 5000)
    hot_score = _saturate(f.hot_money_net_buy, 3000)
    premium_score = _saturate(f.block_trade_premium, 0.05)
    density_score = min(1.0, max(0, f.investigate_density_30d) / 20.0)
    reversal_score = _saturate(f.lhb_reversal_5d, 5.0)

    return round(
        w["institution_net_buy"] * inst_score +
        w["hot_money_net_buy"] * hot_score +
        w["block_trade_premium"] * premium_score +
        w["investigate_density_30d"] * density_score +
        w["lhb_reversal_5d"] * reversal_score,
        4,
    )


def rank_factors(factors: list[EventFactors],
                 weights: Optional[dict] = None) -> list[EventFactors]:
    """计算综合分 + 排名"""
    for f in factors:
        f.composite_score = composite_score(f, weights)
    sorted_factors = sorted(factors, key=lambda x: x.composite_score, reverse=True)
    for i, f in enumerate(sorted_factors):
        f.rank = i + 1
    return sorted_factors


# ═══════════════════════════════════════════════════════
# 集成辅助 — 从现有 lhb 数据构造 EventFactors
# ═══════════════════════════════════════════════════════

def from_lhb_seat_data(
    code: str,
    seats: list[dict],
    investigate_count_30d: int = 0,
    block_trades: Optional[list[dict]] = None,
) -> EventFactors:
    """从龙虎榜席位数据构造 EventFactors

    Args:
        code: 股票代码
        seats: 龙虎榜席位列表 [{'type': '机构'/'游资', 'buy': float, 'sell': float, ...}, ...]
        investigate_count_30d: 近 30 日调研次数
        block_trades: 大宗交易列表 [{'price': float, 'market_price': float, ...}, ...]

    Returns:
        EventFactors

    座位分类依据 seat_classify.py (13 类顶级游资 + 机构席位)
    """
    institution_buy = 0.0
    institution_sell = 0.0
    hot_money_buy = 0.0
    hot_money_sell = 0.0

    for seat in seats:
        seat_type = seat.get("type", "")
        buy = float(seat.get("buy", 0))
        sell = float(seat.get("sell", 0))
        if "机构" in seat_type:
            institution_buy += buy
            institution_sell += sell
        elif "游资" in seat_type or "顶级" in seat_type:
            hot_money_buy += buy
            hot_money_sell += sell

    # 大宗交易溢价率 (平均)
    premium = 0.0
    if block_trades:
        premiums = [
            compute_block_trade_premium(b.get("price", 0), b.get("market_price", 0))
            for b in block_trades
            if b.get("market_price", 0) > 0
        ]
        if premiums:
            premium = sum(premiums) / len(premiums)

    return EventFactors(
        code=code,
        institution_net_buy=compute_institution_net_buy(institution_buy, institution_sell),
        hot_money_net_buy=compute_hot_money_net_buy(hot_money_buy, hot_money_sell),
        block_trade_premium=round(premium, 4),
        investigate_density_30d=compute_investigate_density(investigate_count_30d),
        lhb_reversal_5d=0.0,  # 需历史价, 由调用方填充
        has_data=True,
    )


# ═══════════════════════════════════════════════════════
# 数据类转换
# ═══════════════════════════════════════════════════════

def to_dict_list(factors: list[EventFactors]) -> list[dict]:
    """EventFactors 列表 → dict 列表"""
    return [
        {
            "code": f.code,
            "institution_net_buy": f.institution_net_buy,
            "hot_money_net_buy": f.hot_money_net_buy,
            "block_trade_premium": f.block_trade_premium,
            "investigate_density_30d": f.investigate_density_30d,
            "lhb_reversal_5d": f.lhb_reversal_5d,
            "composite_score": f.composite_score,
            "rank": f.rank,
            "has_data": f.has_data,
        }
        for f in factors
    ]


def empty_factors(code: str) -> EventFactors:
    """空因子 (无数据时返)"""
    return EventFactors(code=code, has_data=False)
