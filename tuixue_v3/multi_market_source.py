#!/usr/bin/env python3
"""
tuixue_v3/multi_market_source.py
Ship 6/100 — 多市场扩展 (港股 / 北交所 / ETF)

设计:
- 复用 FetchRegistry (Ship 1 基础设施), 新增 market_scope 字段
- 港股: yfinance (主力) + Tencent (hk 前缀,兜底)
- 北交所: 8/4 开头自动走 bj 前缀, ak/stock_bj_a_spot_em
- ETF: ak/stock_etf_spot_em + Tencent (51/58 开头 ETF)
- 注册到 FetchRegistry, 不破坏现有 A 股调用

字段标准化: market, code, name, price, change_pct, volume, amount, timestamp

2026-08-02 Ship 6 — 10000 轮迭代 P1 第一步
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from tuixue_v3.data_source_registry import FetchSource

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 代码识别 (复用 lib_common 启发式)
# ═══════════════════════════════════════════════════════

_MARKET_PATTERNS = [
    ("hk", re.compile(r"^\d{4,5}$")),                # 港股 4-5 位纯数字
    ("etf", re.compile(r"^(15|16|51|56|58)\d{4}$")), # ETF 上交所/深交所
    ("bj", re.compile(r"^(8|43|83|87)\d{4}$")),      # 北证 8/43/83/87 开头 + 4 位
    ("sh", re.compile(r"^(6|9|5)\d{5}$")),           # 沪市
    ("sz", re.compile(r"^(0|3|2)\d{5}$")),           # 深市
]


def detect_market(code: str) -> str:
    """根据代码识别市场

    Returns:
        hk / etf / bj / sh / sz / unknown
    """
    for market, pat in _MARKET_PATTERNS:
        if pat.match(code):
            return market
    return "unknown"


# ═══════════════════════════════════════════════════════
# 港股 Fetch
# ═══════════════════════════════════════════════════════

def _hk_yfinance(code: str) -> Any:
    """港股实时 — yfinance (主力, 免费)

    yfinance 港股代码格式: 0700.HK (5 位补 0)
    """
    try:
        import yfinance as yf
        # 补 0 到 5 位
        hk_code = code.zfill(5)
        ticker = f"{hk_code}.HK"
        t = yf.Ticker(ticker)
        info = t.info or {}
        # yfinance info 字段: regularMarketPrice, regularMarketChangePercent, ...
        price = info.get("regularMarketPrice") or info.get("previousClose")
        if price is None:
            return None
        return {
            "market": "hk",
            "code": hk_code,
            "name": info.get("shortName") or info.get("longName") or "",
            "price": float(price),
            "change_pct": float(info.get("regularMarketChangePercent") or 0),
            "open": float(info.get("regularMarketOpen") or 0),
            "high": float(info.get("regularMarketDayHigh") or 0),
            "low": float(info.get("regularMarketDayLow") or 0),
            "volume": int(info.get("regularMarketVolume") or 0),
            "currency": info.get("currency") or "HKD",
            "timestamp": info.get("regularMarketTime"),
            "source": "yfinance",
        }
    except Exception as e:
        logger.debug(f"yfinance hk {code} 失败: {type(e).__name__}: {e}")
        return None


def _hk_tencent(code: str) -> Any:
    """港股实时 — 腾讯 qt.gtimg (兜底)

    腾讯港股格式: rt_hk00700
    """
    try:
        import tuixue_v3.web._constants as const
        session = getattr(const, "_FAST_SESSION", None)
        if session is None:
            # Fallback: 创建临时 session
            import requests
            from requests.adapters import HTTPAdapter
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=0)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        hk_code = code.zfill(5)
        symbol = f"rt_hk{hk_code}"
        url = f"https://qt.gtimg.cn/q={symbol}"
        resp = session.get(url, timeout=(1.5, 3.0))
        resp.raise_for_status()
        raw = resp.content.decode("gbk", errors="ignore")
        # 格式: v_rt_hk00700="100~腾讯控股~00700~..."
        m = re.search(rf'v_{re.escape(symbol)}="([^"]+)"', raw)
        if not m:
            return None
        fields = m.group(1).split("~")
        if len(fields) < 40:
            return None
        return {
            "market": "hk",
            "code": hk_code,
            "name": fields[1],
            "price": float(fields[3]) if fields[3] else 0,
            "change_pct": float(fields[32]) if fields[32] else 0,
            "open": float(fields[5]) if fields[5] else 0,
            "prev_close": float(fields[4]) if fields[4] else 0,
            "high": float(fields[33]) if fields[33] else 0,
            "low": float(fields[34]) if fields[34] else 0,
            "volume": int(float(fields[6])) if fields[6] else 0,
            "amount": float(fields[37]) if fields[37] else 0,
            "currency": "HKD",
            "timestamp": fields[30],
            "source": "tencent",
        }
    except Exception as e:
        logger.debug(f"tencent hk {code} 失败: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 北证 Fetch
# ═══════════════════════════════════════════════════════

def _bj_akshare(code: str) -> Any:
    """北证实时 — akshare stock_bj_a_spot_em"""
    try:
        import akshare as ak
        df = ak.stock_bj_a_spot_em()
        if df is None or len(df) == 0:
            return None
        row = df[df["代码"] == code]
        if len(row) == 0:
            return None
        r = row.iloc[0]
        return {
            "market": "bj",
            "code": code,
            "name": str(r.get("名称", "")),
            "price": float(r.get("最新价", 0)),
            "change_pct": float(r.get("涨跌幅", 0)),
            "open": float(r.get("今开", 0)),
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "volume": int(float(r.get("成交量", 0))),
            "amount": float(r.get("成交额", 0)),
            "currency": "CNY",
            "source": "akshare",
        }
    except Exception as e:
        logger.debug(f"akshare bj {code} 失败: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# ETF Fetch
# ═══════════════════════════════════════════════════════

def _etf_akshare(code: str) -> Any:
    """ETF 实时 — akshare fund_etf_spot_em"""
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        if df is None or len(df) == 0:
            return None
        row = df[df["代码"] == code]
        if len(row) == 0:
            return None
        r = row.iloc[0]
        return {
            "market": "etf",
            "code": code,
            "name": str(r.get("名称", "")),
            "price": float(r.get("最新价", 0)),
            "change_pct": float(r.get("涨跌幅", 0)),
            "open": float(r.get("今开", 0)),
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "volume": int(float(r.get("成交量", 0))),
            "amount": float(r.get("成交额", 0)),
            "currency": "CNY",
            "source": "akshare",
        }
    except Exception as e:
        logger.debug(f"akshare etf {code} 失败: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 数据校验
# ═══════════════════════════════════════════════════════

def _require_quote(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("name")) and data.get("price", 0) > 0


# ═══════════════════════════════════════════════════════
# 注册到 FetchRegistry
# ═══════════════════════════════════════════════════════

def get_sources() -> list[FetchSource]:
    """返回 4 个 FetchSource (港股 2 + 北证 1 + ETF 1)"""
    return [
        # 港股 yfinance (主力)
        FetchSource(
            name="hk_yfinance",
            category="hk_realtime",
            fn=_hk_yfinance,
            display_name="港股 yfinance",
            timeout=5.0,
            priority=10,
            owner="@kai",
            requires=_require_quote,
            schema_version="v1",
            tags=["hk", "free", "yfinance"],
        ),
        # 港股 tencent (兜底)
        FetchSource(
            name="hk_tencent",
            category="hk_realtime",
            fn=_hk_tencent,
            display_name="港股 Tencent qt.gtimg",
            timeout=3.5,
            priority=20,
            owner="@kai",
            requires=_require_quote,
            schema_version="v1",
            tags=["hk", "free", "fallback"],
        ),
        # 北证 akshare
        FetchSource(
            name="bj_akshare",
            category="bj_realtime",
            fn=_bj_akshare,
            display_name="北证 akshare",
            timeout=6.0,
            priority=10,
            owner="@kai",
            requires=_require_quote,
            schema_version="v1",
            tags=["bj", "free", "akshare"],
        ),
        # ETF akshare
        FetchSource(
            name="etf_akshare",
            category="etf_realtime",
            fn=_etf_akshare,
            display_name="ETF akshare",
            timeout=6.0,
            priority=10,
            owner="@kai",
            requires=_require_quote,
            schema_version="v1",
            tags=["etf", "free", "akshare"],
        ),
    ]


def fetch_for_market(code: str) -> Any:
    """便捷函数: 根据代码自动选 category + fetch"""
    from tuixue_v3.data_source_registry import fetch_with_registry

    market = detect_market(code)
    cat_map = {
        "hk": "hk_realtime",
        "bj": "bj_realtime",
        "etf": "etf_realtime",
        "sh": "realtime",  # 复用 A 股
        "sz": "realtime",
    }
    category = cat_map.get(market)
    if not category:
        logger.warning(f"无法识别 market: code={code}")
        return None
    result = fetch_with_registry(category, code)
    return result.data


def list_supported_markets() -> list[str]:
    return ["hk", "bj", "etf", "sh", "sz"]
