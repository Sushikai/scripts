"""web/stock_full_helpers.py — R134 方向C: _build_stock_full 拆解

模块抽出原因:
  原 _build_stock_full 400+ 行,含 7 个内嵌闭包(quote/holders/fund_flow/
  hist_snapshot/hist_flow/hist_seats + name lookup + extras 计算)。
  内嵌闭包不易单测, 且依赖 _cache_quote/_cache_fund 装饰器时无法在模块级共享。

设计:
  所有 helpers 都是纯函数, 依赖通过参数注入(caller 在 server.py 内提供),
  避免 web.stock_full_helpers ↔ web.server 循环 import。

用法 (server.py):
  from .stock_full_helpers import (
      load_stock_quote, load_stock_holders, load_stock_fund_flow,
      load_stock_hist_snapshot, load_stock_hist_flow, load_stock_hist_seats,
      derive_stock_name_from_list, compute_stock_extras,
  )

  # 实时加载器 (cache 已由 server.py 的 @cached 装饰)
  load_stock_quote(code, _store_get, _store_set, cache_store, K.QUOTE)
  load_stock_holders(code, holder_lookup)
  load_stock_fund_flow(code, 60, fund_flow)

  # 历史快照 (传入需要调用的依赖函数)
  load_stock_hist_snapshot(code, cutoff, stock_kline_loader_fn, _quote_realtime_fn)
  load_stock_hist_flow(code, cutoff, _load_fund_flow_cached_fn, _norm_dash_fn)
  load_stock_hist_seats(code, cutoff, _load_seats_cached_fn, _norm_dash_fn)

  # name 兜底 + extras 计算
  quote = derive_stock_name_from_list(quote, code, log)
  extras, ma5 = compute_stock_extras(quote, kline, code, _compute_ma5_fn)
"""
from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Tuple

_log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 实时加载器 (cache / 全局对象 由 caller 注入)
# ──────────────────────────────────────────────────────────────

def load_stock_quote(code: str, _store_get: Callable, _store_set: Callable,
                     cache_store, K_QUOTE) -> dict | None:
    """
    R3: Redis 5s 层 — /full 与 overview 共享, 上游竞速 3s 不必每次重跑
    R3.1: 缺最新价 = 上游降级/失败 → 不入任何缓存 (空 quote 卡 600s 长缓存)
    """
    # 局部 import 避免循环
    from .. import lib_common as lc
    rk = cache_store.versioned(K_QUOTE, code=code)
    r = _store_get(rk, ttl=5)
    if r and (r.get("最新价") or r.get("price")):
        return r
    q = lc.fetch_realtime(code)
    if q and not (q.get("最新价") or q.get("price")):
        return None
    if q:
        _store_set(rk, q, ttl=5)
    return q


def load_stock_holders(code: str, holder_lookup) -> Any:
    """股东信息 — 走 Redis cache, 内置限流."""
    return holder_lookup.fetch_holder_info(code)


def load_stock_fund_flow(code: str, days: int, fund_flow) -> Any:
    """资金流 — 跟 /api/stock/{code}/fund_flow 同一把 Redis 缓存 (R21)."""
    return fund_flow.get_combined(code, days=days)


# ──────────────────────────────────────────────────────────────
# 历史快照加载器 (依赖函数由 caller 注入, 避免循环)
# ──────────────────────────────────────────────────────────────

def load_stock_hist_snapshot(code: str, cutoff_date: str,
                              stock_kline_loader: Callable,
                              _quote_realtime_fn: Callable) -> Tuple[Dict, list]:
    """
    历史快照模式: 取 cutoff_date 之前最后一根 K 线 → 构造伪 quote + 截断 seats.
    返回 (quote_dict, kline_list).

    Args:
      stock_kline_loader: server.stock_kline_loader(code, n) -> list[dict]
      _quote_realtime_fn: server 的实时 quote 加载器 (兜底取 name 用)
    """
    k = stock_kline_loader(code, 250) or []
    k.sort(key=lambda r: r.get("date") or "")
    bar = None
    for row in reversed(k):
        rd = str(row.get("date") or "")[:10]
        if rd <= cutoff_date:
            bar = row
            break
    if not bar:
        return {}, []
    prev_c = 0
    for row in k:
        rd = str(row.get("date") or "")[:10]
        if rd < cutoff_date:
            prev_c = float(row.get("close") or 0)
    op = float(bar.get("open") or 0)
    cl = float(bar.get("close") or 0)
    hi = float(bar.get("high") or 0)
    lo = float(bar.get("low") or 0)
    ps = _quote_realtime_fn(code) or {}
    q = {
        "name": ps.get("name") or "",
        "最新价": cl, "今开": op, "昨收": prev_c,
        "最高": hi, "最低": lo, "涨跌幅": (cl / prev_c - 1) * 100 if prev_c else 0,
        "涨跌额": cl - prev_c, "成交量": float(bar.get("volume") or 0),
        "成交额": float(bar.get("amount") or 0),
        "时间": bar.get("date"),
        "换手率": 0, "振幅": ((hi - lo) / prev_c * 100) if prev_c else 0,
        "流通市值": 0, "总市值": 0, "市盈率": 0,
    }
    return q, k


def load_stock_hist_flow(code: str, cutoff_date: str,
                          _load_fund_flow_cached_fn: Callable,
                          _norm_dash_fn: Callable) -> dict:
    """历史快照的资金流: 截 today + history 到 cutoff_date 之前."""
    try:
        f = _load_fund_flow_cached_fn(code, 60) or {}
        today_v = (f.get("today") or {})
        if today_v:
            today_d = _norm_dash_fn(today_v.get("date") or "")
            if today_d and today_d > cutoff_date:
                f["today"] = None
        hist = f.get("history") or []
        f["history"] = [r for r in hist if _norm_dash_fn(r.get("date") or "") <= cutoff_date]
        return f
    except Exception as e:
        _log.warning(f"[hist_flow] {code} cutoff={cutoff_date} 截断失败: {e}")
        return {"code": code, "today": None, "history": []}


def load_stock_hist_seats(code: str, cutoff_date: str,
                           _load_seats_cached_fn: Callable,
                           _norm_dash_fn: Callable) -> dict:
    """历史快照的席位数据: 截 rows 到 cutoff_date 之前."""
    try:
        sd = _load_seats_cached_fn(code, 60) or {}
        rows = sd.get("rows") or []
        rows = [r for r in rows if _norm_dash_fn(r.get("date") or "") <= cutoff_date]
        sd["rows"] = rows
        sd["is_historical"] = True
        sd["snapshot_date"] = cutoff_date
        return sd
    except Exception as e:
        _log.warning(f"[hist_seats] {code} cutoff={cutoff_date} 截断失败: {e}")
        return {"code": code, "rows": [], "is_historical": True, "snapshot_date": cutoff_date}


# ──────────────────────────────────────────────────────────────
# name 兜底 + extras 计算
# ──────────────────────────────────────────────────────────────

def derive_stock_name_from_list(quote: dict, code: str, log) -> dict:
    """原 quote.name 缺 / 是 code 数字 / 是另一股 code → 走股票全表查真名.
    R2000.31 (2026-08-17): efinance 源把名字放在 _efinance_name,北交所 92/83 前缀
    akshare 池不含,要先读 _efinance_name 后再查全表。
    """
    cur_name = (quote.get("name") or "").strip()
    cur_name_bad = (
        not cur_name
        or cur_name == code
        or (cur_name.isdigit() and len(cur_name) == 6)
    )
    if cur_name_bad:
        ef_name = (quote.get("_efinance_name") or "").strip()
        if ef_name and ef_name != code:
            quote["name"] = ef_name
            cur_name_bad = False
    if cur_name_bad:
        try:
            from .. import data_layer as dl
            for c, n in dl.fetch_stock_list_all() or []:
                if c == code:
                    quote["name"] = n
                    break
        except Exception as e:
            log.warning(f"[name-lookup] error for {code}: {e}")
    if not quote.get("name"):
        quote["name"] = code
    return quote


def compute_stock_extras(quote: dict, kline: list, code: str,
                          _compute_ma5_principles_fn: Callable) -> Tuple[dict, list]:
    """
    返回 (extras_dict, ma5_principles)。
    extras: amplitude / pct_5d / pct_20d / vol_5d_avg / limit_up/dn_price / streak_history / is_chinext_star
    """
    high = float(quote.get("最高") or 0)
    low = float(quote.get("最低") or 0)
    prev_close = float(quote.get("昨收") or 0)
    amplitude = ((high - low) / prev_close * 100) if (high and low and prev_close) else 0

    kline5 = (kline or [])[-5:] if kline else []
    pct_5d = None
    pct_20d = None
    vol_5d_avg = None
    streak_history: List[Dict] = []
    if kline5:
        closes_5 = [float(k.get("close") or 0) for k in kline5]
        if closes_5[0] and closes_5[-1]:
            pct_5d = round((closes_5[-1] / closes_5[0] - 1) * 100, 2)
        if kline:
            closes_all = [float(k.get("close") or 0) for k in kline]
            if len(closes_all) >= 20 and closes_all[-20] and closes_all[-1]:
                pct_20d = round((closes_all[-1] / closes_all[-20] - 1) * 100, 2)
            vols = [float(k.get("volume") or 0) for k in kline[-5:]]
            vol_5d_avg = int(sum(vols) / len(vols)) if vols else None
        prev_c = 0
        for k in kline[-12:]:
            cl = float(k.get("close") or 0)
            hi = float(k.get("high") or 0)
            if prev_c <= 0:
                prev_c = cl
                continue
            chg = (cl / prev_c - 1) * 100 if prev_c else 0
            limit_pct = 0.20 if code.startswith(("300", "301", "688")) else 0.10
            limit_th = 19.0 if limit_pct >= 0.20 else 9.0
            if chg >= limit_th and abs(hi - cl) < 0.02 * cl:
                streak_history.append({"date": k.get("date"), "change_pct": round(chg, 2),
                                        "limit_pct": int(limit_pct * 100)})
            prev_c = cl

    is_kc = code.startswith(("300", "301", "688"))
    limit_pct = 0.20 if is_kc else 0.10
    limit_up_price = round(prev_close * (1 + limit_pct), 2) if prev_close else None
    limit_dn_price = round(prev_close * (1 - limit_pct), 2) if prev_close else None

    extras_dict = {
        "amplitude_pct":     round(amplitude, 2),
        "pct_5d":            pct_5d,
        "pct_20d":           pct_20d,
        "vol_5d_avg":        vol_5d_avg,
        "limit_up_price":    limit_up_price,
        "limit_dn_price":    limit_dn_price,
        "limit_pct":         limit_pct * 100,
        "streak_history":    streak_history,
        "is_chinext_star":   is_kc,
    }

    ma5_principles = _compute_ma5_principles_fn(kline or [])
    return extras_dict, ma5_principles