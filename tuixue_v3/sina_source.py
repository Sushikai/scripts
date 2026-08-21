#!/usr/bin/env python3
"""
tuixue_v3/sina_source.py
Ship 3/100 — 新浪 hq.sinajs.cn 兜底接入 (HTTPS + Referer 必填)

设计:
- 新浪自 2022 起强制 HTTPS + Referer (https://finance.sina.com.cn), 否则 403
- 字段比腾讯略瘦 (无完整五档), 但分笔成交和大单数据强于腾讯
- 作腾讯 qt.gtimg 挂掉时的备援源
- 注册到 FetchRegistry, 复用 Ship 1 基础设施

接口:
- sina_realtime_quote: 实时五字段 (当前价/涨跌/成交量/成交额/最高最低)

参考: https://hq.sinajs.cn/list=sh600519
格式: var hq_str_sh600519="大秦铁路,27.55,27.25,26.91,27.55,26.20,..."

2026-08-02 Ship 3 — 10000 轮迭代 P0 第三步
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

from tuixue_v3.data_source_registry import FetchSource

logger = logging.getLogger(__name__)

SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_REFERER = "https://finance.sina.com.cn"
SINA_FQ_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

# ─── Session 单例 (复用连接池) ───
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=10,
                pool_maxsize=10,
                max_retries=0,  # 业务层重试
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": SINA_REFERER,  # 必填, 否则 403
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            _session = s
        return _session


def _to_sina_symbol(code: str) -> str:
    """A 股代码 → 新浪 symbol 格式"""
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    if code.startswith(("0", "3", "2")):
        return f"sz{code}"
    if code.startswith(("8", "4")):
        return f"bj{code}"  # 北证 (新浪 2023 起支持)
    return f"sh{code}"  # fallback


def _require_quote(data: Any) -> bool:
    """实时报价校验: 必须有 name + price + volume"""
    if not isinstance(data, dict):
        return False
    return bool(data.get("name")) and data.get("price") is not None


def _parse_sina_hq(symbol: str, raw: str) -> Optional[dict]:
    """解析新浪 hq.sinajs.cn 返回

    格式: var hq_str_sh600519="大秦铁路,27.55,27.25,26.91,27.55,26.20,26.21,...,2026-08-02,15:00:00,00"
    字段 (按索引):
      0: name
      1: open
      2: prev_close
      3: current
      4: high
      5: low
      ...更多 (成交/买卖盘等)
      30: date
      31: time
    """
    m = re.search(rf'var hq_str_{re.escape(symbol)}="([^"]*)"', raw)
    if not m:
        return None
    fields = m.group(1).split(",")
    if len(fields) < 33:
        return None
    try:
        prev_close = float(fields[2]) if fields[2] else 0.0
        current = float(fields[3]) if fields[3] else 0.0
        change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0.0
        return {
            "symbol": symbol,
            "name": fields[0],
            "open": float(fields[1]) if fields[1] else 0.0,
            "prev_close": prev_close,
            "price": current,
            "high": float(fields[4]) if fields[4] else 0.0,
            "low": float(fields[5]) if fields[5] else 0.0,
            "change_pct": round(change_pct, 2),
            "volume": int(float(fields[8])) if fields[8] else 0,  # 成交量(股)
            "amount": float(fields[9]) if fields[9] else 0.0,    # 成交额(元)
            "date": fields[31],
            "time": fields[32],
        }
    except (ValueError, IndexError) as e:
        logger.debug(f"sina 解析 {symbol} 失败: {e}")
        return None


# ─── Fetch 函数 ───

def _sina_realtime_quote(code: str) -> Any:
    """单股实时报价 (新 HTTPS)"""
    symbol = _to_sina_symbol(code)
    try:
        s = _get_session()
        resp = s.get(
            SINA_HQ_URL.format(symbols=symbol),
            timeout=(1.5, 3.0),
        )
        resp.raise_for_status()
        # 新浪 GBK 编码
        try:
            raw = resp.content.decode("gbk")
        except UnicodeDecodeError:
            raw = resp.text
        return _parse_sina_hq(symbol, raw)
    except requests.exceptions.HTTPError as e:
        logger.debug(f"sina {code} HTTP {e.response.status_code} (Referer 失效?)")
        return None
    except Exception as e:
        logger.debug(f"sina {code} 异常: {type(e).__name__}: {e}")
        return None


def _sina_batch_realtime(codes: list) -> Any:
    """批量实时报价 (1 个 HTTP 请求多股,省 quota)"""
    if not codes:
        return None
    symbols = ",".join(_to_sina_symbol(c) for c in codes)
    try:
        s = _get_session()
        resp = s.get(
            SINA_HQ_URL.format(symbols=symbols),
            timeout=(2.0, 5.0),
        )
        resp.raise_for_status()
        try:
            raw = resp.content.decode("gbk")
        except UnicodeDecodeError:
            raw = resp.text
        results = {}
        for code in codes:
            symbol = _to_sina_symbol(code)
            quote = _parse_sina_hq(symbol, raw)
            if quote:
                results[code] = quote
        return results if results else None
    except Exception as e:
        logger.debug(f"sina batch 异常: {e}")
        return None


# ─── 注册到 FetchRegistry ───

def get_sources() -> list[FetchSource]:
    """返回 2 个 FetchSource (单股 + 批量)"""
    return [
        FetchSource(
            name="sina_realtime_hq",
            category="realtime",
            fn=_sina_realtime_quote,
            display_name="新浪 hq.sinajs (HTTPS)",
            timeout=3.5,
            priority=50,  # 中等优先级,作腾讯兜底
            owner="@kai",
            requires=_require_quote,
            schema_version="v1",
            tags=["free", "rate-limited", "https-required", "fallback"],
        ),
        FetchSource(
            name="sina_realtime_batch",
            category="realtime_batch",
            fn=lambda codes: _sina_batch_realtime(codes),
            display_name="新浪 hq.sinajs (批量)",
            timeout=5.0,
            priority=40,
            owner="@kai",
            requires=lambda d: isinstance(d, dict) and len(d) > 0,
            schema_version="v1",
            tags=["free", "rate-limited", "https-required", "batch"],
        ),
    ]


def is_healthy() -> bool:
    """快速健康检查: 试拉 1 个测试股票"""
    quote = _sina_realtime_quote("000001")  # 上证指数
    return quote is not None and quote.get("price", 0) > 0
