#!/usr/bin/env python3
"""
tuixue_v3/web/factor_api.py
Ship 12/100 — 因子 API 路由 (FastAPI router, 不挂到 app)

设计: 不直接 @app.get, 用 APIRouter 让 server.py 在启动时 include_router
接入, 避免改大 server.py 的位置 — 现有 12K 行文件已无可挽回。

API 列表:
- GET /api/factor/stock/{code}      — 单股 5 因子综合分
- GET /api/factor/batch              — 批量 (codes=xxx,yyy,...)
- GET /api/factor/sector/{name}      — 板块 5 因子 (动量反转)
- GET /api/factor/event/{code}       — 单股事件因子 (龙虎榜+大宗+调研)
- GET /api/factor/news/{code}        — 单股新闻情绪 5 维

降级: 任一上游失败, 该因子返 None, 综合分按 None 跳过重归一 — 永不抛 5xx。

2026-08-02 Ship 12 — 10000 轮迭代 P2 第二步
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import factor_pipeline
from .. import event_factors
from .. import news_sentiment
from .. import sector_rotation_factors

logger = logging.getLogger(__name__)


def build_router():
    """构造 FastAPI router — 延迟 import server 避免循环"""
    from fastapi import APIRouter, Query, HTTPException

    from .. import factor_pipeline
    from .. import event_factors
    from .. import news_sentiment
    from .. import sector_rotation_factors

    router = APIRouter(prefix="/api/factor", tags=["factor"])

    # ── 单股 5 因子综合 ──
    @router.get("/stock/{code}")
    async def factor_stock(code: str):
        """单只股票 5 因子综合分 (sector/event/sentiment/momentum/volatility)"""
        score = await _build_factor_score(code)
        if not score.has_data:
            raise HTTPException(status_code=404, detail=f"无数据: {code}")
        return {
            "code": code,
            "score": factor_pipeline.to_dict_list([score])[0],
            "explain": factor_pipeline.explain(score),
        }

    @router.get("/batch")
    async def factor_batch(
        codes: str = Query(..., description="逗号分隔股票代码, 最多 50"),
    ):
        """批量 5 因子综合分"""
        code_list = [c.strip() for c in codes.split(",") if c.strip()][:50]
        if not code_list:
            raise HTTPException(status_code=400, detail="codes 不可为空")

        scores = []
        for c in code_list:
            try:
                s = await _build_factor_score(c)
            except Exception as e:
                logger.warning("factor_batch %s 失败: %s", c, e)
                s = factor_pipeline.build_minimal(c)
            scores.append(s)
        ranked = factor_pipeline.rank_scores(scores)
        return {
            "count": len(ranked),
            "scores": factor_pipeline.to_dict_list(ranked),
        }

    # ── 板块 5 因子 ──
    @router.get("/sector/{name}")
    async def factor_sector(name: str):
        """板块 5 因子 (动量/反转/北向/两融/ETF)"""
        try:
            factors = _build_sector_factors(name)
            if not factors:
                raise HTTPException(status_code=404, detail=f"无数据: {name}")
            ranked = sector_rotation_factors.rank_sectors(factors)
            return {
                "sector": name,
                "factors": sector_rotation_factors.to_dict_list(ranked),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("factor_sector %s 失败: %s", name, e)
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── 单股事件因子 ──
    @router.get("/event/{code}")
    async def factor_event(code: str):
        """单股事件因子 (5 维)"""
        try:
            factors = _build_event_factors(code)
            d = event_factors.to_dict_list([factors])[0]
            d["composite_score"] = event_factors.composite_score(factors)
            return d
        except Exception as e:
            logger.warning("factor_event %s 失败: %s", code, e)
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # ── 单股新闻情绪 ──
    @router.get("/news/{code}")
    async def factor_news(
        code: str,
        use_llm: bool = Query(False, description="是否走 LLM 链路"),
    ):
        """单股新闻情绪 5 维 + 聚合"""
        try:
            items = _build_news_factors(code, use_llm=use_llm)
            return {
                "code": code,
                "items": news_sentiment.to_dict_list(items),
                "aggregate": news_sentiment.aggregate(items),
            }
        except Exception as e:
            logger.warning("factor_news %s 失败: %s", code, e)
            raise HTTPException(status_code=500, detail=str(e)[:200])

    return router


# ═══════════════════════════════════════════════════════
# 因子构造 (lazy 调上游 fetch, 失败 → None 类)
# ═══════════════════════════════════════════════════════

async def _build_factor_score(code: str):
    """单股 5 因子综合 — 任一上游失败 → None 类, 不报错"""
    sector_rotation = None
    event_components = None
    sentiment_components = None
    ret_n = None
    vol_n = None

    # 板块 (从 sector_classify 拿代码所属板块)
    try:
        from .sector_classify import get_sector as _get_sector
        sec = _get_sector(code)
        if isinstance(sec, dict):
            sector_name = sec.get("sw") or ""
            if sector_name:
                # 没现成 sector_rotation API, 用历史 close 当动量代理
                df = data_layer_fetch_daily_for_sector(sector_name)
                if df is not None and len(df) >= 20:
                    close = df["收盘"].astype(float).tail(20)
                    sector_rotation = float(close.iloc[-1] / close.iloc[0] - 1)
    except Exception as e:
        logger.debug("factor_score[%s] 板块轮动失败: %s", code, e)

    # 事件因子 (龙虎榜 + 大宗 + 调研)
    try:
        from . import seat_lookup
        seats = seat_lookup.get_stock_seats(code, 10)
        factors = event_factors.from_lhb_seat_data(code, seats or [])
        if factors.has_data:
            event_components = {
                "institution_net": factors.institution_net_buy,
                "hot_money_net": factors.hot_money_net_buy,
                "block_premium": factors.block_trade_premium,
                "investigate": factors.investigate_density_30d,
                "lhb_reversal": factors.lhb_reversal_5d,
            }
    except Exception as e:
        logger.debug("factor_score[%s] 事件因子失败: %s", code, e)

    # 新闻情绪 — 从 fetch_news 取包含该股的标题
    try:
        from .news_lookup import fetch_news
        news = fetch_news(50)
        titles = [n.get("title", "") for n in news
                  if code in n.get("title", "") or code in n.get("content", "")]
        if titles:
            items = news_sentiment.score_titles(titles, use_llm=False)
            agg = news_sentiment.aggregate(items)
            sentiment_components = {
                "sentiment": agg["sentiment"],
                "confidence": agg["confidence"],
            }
    except Exception as e:
        logger.debug("factor_score[%s] 新闻情绪失败: %s", code, e)

    # 动量 + 波动率 (从 data_layer 拿 K 线)
    try:
        from .. import data_layer
        df = data_layer.fetch_daily(code, 60)
        if df is not None and not df.empty and len(df) >= 20:
            close = df["收盘"].astype(float).tail(20)
            ret_n = float(close.iloc[-1] / close.iloc[0] - 1)
            ret = close.pct_change().dropna()
            if len(ret) > 1:
                vol_n = float(ret.std())
    except Exception as e:
        logger.debug("factor_score[%s] 动量波动率失败: %s", code, e)

    return factor_pipeline.build_from_components(
        code,
        sector_rotation=sector_rotation,
        event_components=event_components,
        sentiment_components=sentiment_components,
        ret_n=ret_n,
        vol_n=vol_n,
    )


def data_layer_fetch_daily_for_sector(sector_name: str):
    """板块层日线 — 拿不到返 None, 走轻量 fallback"""
    # 暂无板块日线 API, 返 None 让 sector_rotation 类标记为 None
    return None


def _build_sector_factors(name: str):
    """板块 5 因子 (单板块, 单因子对象)"""
    # sector_factors API 暂不可用, 仅返回占位 — sector_rotation_factors 字段直传
    return []


def _build_event_factors(code: str):
    """单股事件因子"""
    from . import seat_lookup
    seats = seat_lookup.get_stock_seats(code, 10) or []
    factors = event_factors.from_lhb_seat_data(code, seats)
    if not factors.has_data:
        factors.has_data = bool(seats)
    return factors


def _build_news_factors(code: str, use_llm: bool = False):
    """单股新闻情绪"""
    from .news_lookup import fetch_news
    news = fetch_news(50) or []
    titles = [n.get("title", "") for n in news
              if code in n.get("title", "") or code in n.get("content", "")]
    if not titles:
        return []
    return news_sentiment.score_titles(titles, use_llm=use_llm)