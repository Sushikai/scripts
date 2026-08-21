#!/usr/bin/env python3
"""
tuixue_v3/strategy_registry.py
Ship 20/100 — 策略注册表 (策略抽象 + 动态启停)

设计:
策略 = 一个可调用的对象, 输入 candidates + 上下文, 输出排序后的 picks
支持:
- 装饰器注册 @register_strategy("name")
- 列出 / 启用 / 禁用 / 按需启用
- 元信息 (author, version, description, regime_suitability)
- 上下文: Regime + Portfolio + FactorScores

每个策略可声明:
- regime_suit: [regime names] — 该策略适合哪些 regime, 其他 regime 自动降权
- min_factor_score: 因子硬阈值
- max_recommendations: 默认推荐数

降级: 策略不存在 → 返回空 picks + warn log

2026-08-02 Ship 20 — 10000 轮迭代 P2 第十步
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════

@dataclass
class StrategyContext:
    """策略执行上下文"""
    date: str
    candidates: list[str]                  # 候选 code 列表
    factor_scores: dict[str, float]        # code → composite
    regime: str = "unknown"                # bull/bear/range/crisis/unknown
    portfolio_value: float = 0.0
    cash: float = 0.0
    initial_capital: float = 100000.0
    extra: dict = field(default_factory=dict)  # 自定义数据


@dataclass
class StrategyPick:
    """策略输出"""
    code: str
    score: float                # 0~1, 高分优先
    confidence: float = 1.0
    reason: str = ""


@dataclass
class StrategyInfo:
    """策略元信息"""
    name: str
    func: Callable
    description: str
    author: str = "tuixue"
    version: str = "1.0"
    regime_suit: tuple[str, ...] = ("bull", "range", "bear", "crisis", "unknown")
    min_factor_score: float = -0.5
    max_recommendations: int = 20
    enabled: bool = True


# ═══════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════

_REGISTRY: dict[str, StrategyInfo] = {}


def register(
    name: str,
    *,
    description: str = "",
    author: str = "tuixue",
    version: str = "1.0",
    regime_suit: tuple[str, ...] = ("bull", "range", "bear", "crisis", "unknown"),
    min_factor_score: float = -0.5,
    max_recommendations: int = 20,
) -> Callable:
    """装饰器: 注册一个策略"""
    def decorator(func: Callable) -> Callable:
        _REGISTRY[name] = StrategyInfo(
            name=name, func=func, description=description,
            author=author, version=version,
            regime_suit=regime_suit,
            min_factor_score=min_factor_score,
            max_recommendations=max_recommendations,
        )
        logger.info("注册策略 %s v%s (%s)", name, version, description)
        return func
    return decorator


def get(name: str) -> Optional[StrategyInfo]:
    """获取策略"""
    return _REGISTRY.get(name)


def list_all(enabled_only: bool = False) -> list[StrategyInfo]:
    """列出所有策略"""
    items = list(_REGISTRY.values())
    if enabled_only:
        items = [s for s in items if s.enabled]
    return items


def enable(name: str) -> bool:
    """启用策略"""
    s = _REGISTRY.get(name)
    if not s:
        return False
    s.enabled = True
    return True


def disable(name: str) -> bool:
    """禁用策略"""
    s = _REGISTRY.get(name)
    if not s:
        return False
    s.enabled = False
    return True


def clear() -> None:
    """清空注册表 (测试用)"""
    _REGISTRY.clear()


# ═══════════════════════════════════════════════════════
# 执行
# ═══════════════════════════════════════════════════════

def run_strategy(name: str, ctx: StrategyContext) -> list[StrategyPick]:
    """执行一个策略

    Args:
        name: 策略名
        ctx: 上下文

    Returns:
        StrategyPick 列表 (按 score 降序)
    """
    info = _REGISTRY.get(name)
    if not info:
        logger.warning("策略 %s 不存在", name)
        return []
    if not info.enabled:
        logger.debug("策略 %s 已禁用", name)
        return []

    # regime suit 检查: 不在白名单 → 降权 (×0.5)
    regime_factor = 1.0 if ctx.regime in info.regime_suit else 0.5

    try:
        result = info.func(ctx)
    except Exception as e:
        logger.warning("策略 %s 执行失败: %s", name, e)
        return []

    # 过滤 + 应用 regime 降权
    picks: list[StrategyPick] = []
    for p in result:
        score = info.func.__name__ if False else p.score  # 保留原 score
        composite = ctx.factor_scores.get(p.code, 0.0)
        if composite < info.min_factor_score:
            continue
        picks.append(StrategyPick(
            code=p.code,
            score=p.score * regime_factor,
            confidence=p.confidence * regime_factor,
            reason=p.reason,
        ))

    picks.sort(key=lambda p: p.score, reverse=True)
    return picks[:info.max_recommendations]


def run_strategies(names: list[str], ctx: StrategyContext) -> dict[str, list[StrategyPick]]:
    """批量执行多个策略

    Returns:
        {strategy_name: [picks]}
    """
    return {name: run_strategy(name, ctx) for name in names}


# ═══════════════════════════════════════════════════════
# 内置策略示例
# ═══════════════════════════════════════════════════════

@register("top_factor", description="Top by factor composite",
          regime_suit=("bull", "range", "unknown"))
def _top_factor_strategy(ctx: StrategyContext) -> list[StrategyPick]:
    """按 factor_score 排序, top N"""
    picks = []
    for code in ctx.candidates:
        score = ctx.factor_scores.get(code)
        if score is None:
            continue
        picks.append(StrategyPick(
            code=code, score=score,
            reason=f"factor={score:.2f}",
        ))
    return picks


@register("top_factor_bull_only",
          description="Bull regime top factor (其他 regime 降权)",
          regime_suit=("bull",))
def _top_factor_bull(ctx: StrategyContext) -> list[StrategyPick]:
    return [
        StrategyPick(code=c, score=ctx.factor_scores.get(c, 0.0),
                    reason=f"factor={ctx.factor_scores.get(c, 0.0):.2f}")
        for c in ctx.candidates if c in ctx.factor_scores
    ]
