"""
Meta Strategy: 综合推荐 + 回测
Aggregates ZT涨停 + 策略选股(wb/rl/ma5) + 龙头战法 + 得鑫量变术 → unified ranking.

Live:  parallel fetch all strategies, normalize scores, weighted aggregate.
Backtest: build meta-signal cache from cache.db daily bars, feed to ZT engine.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# ── Strategy weights (sum = 1.0) ──
DEFAULT_WEIGHTS = {
    "zt": 0.25,
    "sp": 0.25,      # 策略选股 (wb + rl + ma5)
    "dragons": 0.20,  # 龙头战法
    "dexin": 0.20,    # 得鑫量变术
    "screen": 0.10,   # 四层流水线 (暂未接入)
}


class MetaAggregator:
    """Normalize heterogeneous strategy scores → unified 0-100 meta score."""

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or dict(DEFAULT_WEIGHTS)

    @staticmethod
    def norm_zt(score: float) -> float:
        """ZT live_pick score (50-90) → 0-100."""
        return max(0.0, min(100.0, (score - 50.0) / 40.0 * 100.0))

    @staticmethod
    def norm_sp(score: float) -> float:
        """策略选股 score (already 0-100)."""
        return max(0.0, min(100.0, score))

    @staticmethod
    def norm_dragons(score: float) -> float:
        """龙头战法 score_total (already 0-100)."""
        return max(0.0, min(100.0, score))

    @staticmethod
    def norm_dexin(score: float) -> float:
        """得鑫 score (already 0-100)."""
        return max(0.0, min(100.0, score))

    def aggregate(self, signals: dict[str, dict]) -> dict:
        """Compute meta_score from per-strategy signals.

        signals: {"zt": {code: {score, ...}}, "sp": {...}, ...}
        Returns list of picks sorted by meta_score desc.
        """
        # Collect all codes seen by any strategy
        all_codes: dict[str, dict] = {}  # code → {strategies: {name: score}, data: {...}}

        for strategy_name, code_map in signals.items():
            if not code_map:
                continue
            norm_fn = getattr(self, f"norm_{strategy_name}", None)
            for code, info in code_map.items():
                if code not in all_codes:
                    all_codes[code] = {"strategies": {}, "data": {}}
                raw = info.get("score", 0) if isinstance(info, dict) else info
                try:
                    raw = float(raw)
                except (TypeError, ValueError):
                    raw = 0.0
                normed = norm_fn(raw) if norm_fn else raw
                if normed > 0:
                    all_codes[code]["strategies"][strategy_name] = round(normed, 2)
                # Keep first non-empty data for display
                if not all_codes[code]["data"] and isinstance(info, dict):
                    all_codes[code]["data"] = info

        picks = []
        for code, entry in all_codes.items():
            strategies = entry["strategies"]
            if not strategies:
                continue
            n_matched = len(strategies)
            # Weighted sum
            weighted_sum = sum(
                strategies[s] * self.weights.get(s, 0.1)
                for s in strategies
            )
            # Boost for multi-strategy consensus (+10% per additional strategy)
            consensus_boost = 1.0 + 0.10 * (n_matched - 1)
            meta_score = round(weighted_sum * consensus_boost, 2)

            data = entry["data"]
            picks.append({
                "code": code,
                "name": data.get("name", code),
                "meta_score": meta_score,
                "strategy_votes": strategies,
                "matched_count": n_matched,
                "price": data.get("price", 0),
                "change_pct": data.get("change_pct", 0),
                "board": data.get("board", "main"),
                "mcap_yi": data.get("mcap_yi", 0),
                "amount_yi": data.get("amount_yi", 0),
                "turnover_pct": data.get("turnover_pct", 0),
                "volume_ratio": data.get("volume_ratio", 0),
            })

        picks.sort(key=lambda p: -p["meta_score"])
        return picks


# ── Live strategy callers ──────────────────────────────────────────

def _call_zt_live(top_n: int = 50) -> dict[str, dict]:
    """Fetch ZT live picks and return {code: info} map."""
    try:
        from .. import multi_source_fetchers as msf
        spot = msf.fetch_spot_a_full(overall_timeout=8)
    except Exception as e:
        log.warning(f"[meta] ZT spot fetch failed: {e}")
        return {}

    board_codes = {"300", "301", "688", "689"}
    result: dict[str, dict] = {}
    for code, info in spot.items():
        try:
            pct = float(info.get("涨跌幅", 0) or 0)
        except Exception:
            continue
        if pct < 7:
            continue
        is_20cm = code[:3] in board_codes
        threshold = 19.5 if is_20cm else 9.5
        locked = pct >= threshold

        # Score matching zt_screener formula
        score = 50.0
        score += min(pct / threshold * 10, 10)
        vol_ratio = float(info.get("量比", 0) or 0)
        if vol_ratio > 5:
            score += 10
        elif vol_ratio > 2:
            score += 5
        amp = float(info.get("振幅", 0) or 0)
        if amp < 5 and locked:
            score += 5
        mcap = float(info.get("总市值", 0) or 0) / 1e8
        if 15 <= mcap <= 150:
            score += 5
        turn = float(info.get("换手率", 0) or 0)
        if 2 <= turn <= 50:
            score += 5
        amount = float(info.get("成交额", 0) or 0) / 1e8
        if amount > 1:
            score += 5
        if locked:
            score += 10

        result[code] = {
            "score": round(score, 2),
            "name": info.get("name", code),
            "price": float(info.get("最新价", 0) or 0),
            "change_pct": round(pct, 2),
            "locked": locked,
            "board": "20cm" if is_20cm else "main",
            "mcap_yi": round(mcap, 2),
            "amount_yi": round(amount, 2),
            "turnover_pct": round(turn, 2),
            "volume_ratio": round(vol_ratio, 2),
            "amplitude": round(amp, 2),
        }

    # Sort by score, keep top_n
    sorted_items = sorted(result.items(), key=lambda x: -x[1]["score"])[:top_n]
    return dict(sorted_items)


def _call_sp_live(wb_min: int = 1, rl_near: bool = True, ma5_breakout: bool = True) -> dict[str, dict]:
    """Run 策略选股 scan and return {code: info} map."""
    try:
        from . import strategy_picker as sp
        result = sp.scan_strategies(
            None, wb_min=wb_min, rl_near=rl_near,
            ma5_breakout=ma5_breakout, mode="or", min_matched=1, max_workers=8,
        )
    except Exception as e:
        log.warning(f"[meta] SP scan failed: {e}")
        return {}

    if not result or result.get("_skip"):
        return {}

    out: dict[str, dict] = {}
    for sig in result.get("signals", []):
        code = sig.get("code", "")
        if not code:
            continue
        sc = sig.get("score", {})
        total = sc.get("total", 0) if isinstance(sc, dict) else sc
        out[code] = {
            "score": float(total),
            "name": sig.get("name", code),
            "matched_keys": sig.get("matched_keys", []),
            "wb_count": (sig.get("wb") or {}).get("count", 0) if sig.get("wb") else 0,
            "rl_near": (sig.get("rl") or {}).get("near_support", False) if sig.get("rl") else False,
            "ma5_ok": (sig.get("ma5") or {}).get("ok", False) if sig.get("ma5") else False,
        }
    return out


def _call_dragons_live() -> dict[str, dict]:
    """Fetch 龙头战法 results and return {code: info} map."""
    try:
        from .. import dragons
        result = dragons.score_dragons()
    except Exception as e:
        log.warning(f"[meta] dragons fetch failed: {e}")
        return {}

    if not result:
        return {}

    out: dict[str, dict] = {}
    for item in result.get("all", []):
        code = item.get("code", "")
        if not code:
            continue
        out[code] = {
            "score": float(item.get("score_total", 0)),
            "name": item.get("name", code),
            "rank": item.get("rank", 99),
            "streak": item.get("streak", 0),
            "mcap_yi": item.get("market_cap_yi", 0),
            "board": "20cm" if code.startswith(("300", "301", "688", "689")) else "main",
        }
    return out


def _call_dexin_live() -> dict[str, dict]:
    """Fetch 得鑫量变术 screen results and return {code: info} map."""
    try:
        from . import dexin_screener as ds
        # dexin_screener has register(app) pattern; _do_screen is inside closure.
        # Use the module-level helper if available, otherwise skip.
        if hasattr(ds, "_do_screen_public"):
            result = ds._do_screen_public()
        elif hasattr(ds, "_screen_cache"):
            # Try accessing the internal cache
            cache = getattr(ds, "_screen_cache", None)
            if cache and cache.get("data"):
                result = cache["data"]
            else:
                log.info("[meta] dexin cache miss, skipping (no public API)")
                return {}
        else:
            log.info("[meta] dexin not accessible from outside closure, skipping")
            return {}
    except Exception as e:
        log.warning(f"[meta] dexin fetch failed: {e}")
        return {}

    out: dict[str, dict] = {}
    stages = result.get("stages", {}) if isinstance(result, dict) else {}
    for stage_name, items in stages.items():
        if stage_name == "xu_sha_dangerous":
            continue
        for item in items:
            code = item.get("code", "")
            if not code:
                continue
            # Only keep the best stage per code (highest score)
            s = float(item.get("score", 0))
            if code in out and out[code]["score"] >= s:
                continue
            out[code] = {
                "score": s,
                "name": item.get("name", code),
                "stage": stage_name,
                "stage_label": item.get("stage_label", ""),
                "price": item.get("volume", {}).get("price", 0) if isinstance(item.get("volume"), dict) else 0,
            }
    return out


# ── Live recommendation ──────────────────────────────────────────

def recommend_live(top_n: int = 20, weights: dict | None = None) -> dict:
    """Run all strategies in parallel, aggregate, return top picks."""
    t0 = time.time()
    agg = MetaAggregator(weights)

    # Phase 1: fetch all strategies in parallel
    strategies_available: list[str] = []
    signals: dict[str, dict] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures: dict[str, Any] = {
            "zt": ex.submit(_call_zt_live, 80),
            "sp": ex.submit(_call_sp_live, 1, True, True),
            "dragons": ex.submit(_call_dragons_live),
            "dexin": ex.submit(_call_dexin_live),
        }

        for name, fut in futures.items():
            try:
                result = fut.result(timeout=120)
                if result:
                    signals[name] = result
                    strategies_available.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
                log.warning(f"[meta] strategy {name} timeout/error: {e}")

    # Phase 2: aggregate
    picks = agg.aggregate(signals)
    picks = picks[:top_n]

    # Attach breakdown for display
    for p in picks:
        votes = p.get("strategy_votes", {})
        weighted_sum = sum(votes[s] * agg.weights.get(s, 0.1) for s in votes)
        breakdown = {}
        for s, score in votes.items():
            w = agg.weights.get(s, 0.1)
            contribution = round(score * w / max(weighted_sum, 0.01) * 100, 1)
            breakdown[s] = {"score": score, "weight": w, "contribution_pct": contribution}
        p["breakdown"] = breakdown

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "picks": picks,
        "stats": {
            "strategies_available": strategies_available,
            "strategies_total": len(signals),
            "total_codes_scanned": sum(len(v) for v in signals.values()),
            "errors": errors,
            "elapsed_ms": elapsed_ms,
        },
        "ts": _dt.datetime.now().isoformat(),
    }


# ── Historical meta signal for backtest ───────────────────────────

def _compute_sp_score_from_df(df: pd.DataFrame, date_str: str) -> float:
    """Compute strategy-picker-style score (0-100) from daily DataFrame.

    Combines: ma5 breakout + volume breakout (wb) + near support (rl).
    df columns: 日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额, 换手率
    """
    if df is None or df.empty or "日期" not in df.columns:
        return 0.0
    target_idx = df.index[df["日期"] == date_str].tolist()
    if not target_idx:
        return 0.0
    ti = target_idx[0]
    if ti < 20:  # need at least 20 bars for MA20
        return 0.0

    # Use full history up to target date
    hist = df.iloc[:ti + 1]
    closes = hist["收盘"].astype(float)
    opens = hist["开盘"].astype(float)
    highs = hist["最高"].astype(float)
    lows = hist["最低"].astype(float)
    volumes = hist["成交量"].astype(float)

    today_close = float(closes.iloc[-1])
    today_open = float(opens.iloc[-1])
    today_high = float(highs.iloc[-1])
    today_low = float(lows.iloc[-1])
    today_vol = float(volumes.iloc[-1])

    if today_close <= 0 or today_open <= 0 or today_vol <= 0:
        return 0.0

    score = 0.0

    # ── MA5 breakout (max 25) ──
    ma5 = float(closes.tail(5).mean())
    ma20 = float(closes.tail(20).mean())
    if len(closes) >= 6:
        prev_ma5 = float(closes.tail(6).head(5).mean())
    else:
        prev_ma5 = ma5
    if ma5 > 0 and prev_ma5 > 0:
        if today_close > ma5:
            score += 8
        if ma5 > prev_ma5:
            score += 8
        if ma5 > ma20:
            score += 9

    # ── Volume breakout / wb (max 25) ──
    if len(volumes) >= 6:
        avg_vol_5d = float(volumes.tail(6).head(5).mean())
        if avg_vol_5d > 0:
            vol_ratio = today_vol / avg_vol_5d
            if vol_ratio > 2.5:
                score += 20
            elif vol_ratio > 1.8:
                score += 15
            elif vol_ratio > 1.2:
                score += 8

    # ── Near support / rl (max 25) ──
    # Check if today's low is near 20-day low (support zone)
    if len(lows) >= 20:
        low_20 = float(lows.tail(20).min())
        if low_20 > 0:
            dist_pct = (today_low - low_20) / low_20 * 100
            if 0 <= dist_pct < 3:
                score += 20
            elif 0 <= dist_pct < 5:
                score += 12
            elif 0 <= dist_pct < 8:
                score += 6

    # ── Bullish candle quality (max 25) ──
    body_pct = (today_close - today_open) / today_open * 100 if today_open > 0 else 0
    if body_pct > 5:
        score += 15
    elif body_pct > 2:
        score += 8
    elif body_pct > 0.5:
        score += 4
    # Upper wick small (strong close)
    upper_wick = (today_high - max(today_close, today_open)) / today_high * 100 if today_high > 0 else 0
    if upper_wick < 1:
        score += 10
    elif upper_wick < 2:
        score += 5

    return min(score, 100.0)


def build_meta_cache_historical(
    start: str, end: str,
    zt_weights: dict | None = None,
) -> tuple[dict, dict, list, dict]:
    """Build historical meta-signal cache for backtest.

    Loads daily_cache from cache_db, detects limit-ups per date,
    scores each candidate with ZT + ma5 breakout, combines via MetaAggregator.

    Returns (daily_cache, dates, all_stocks, meta_cache) for run_zt_backtest(_prebuilt=...).
    """
    from .. import zt_config as cfg
    from ..zt_backtest import (
        _batch_cache_load, _detect_limit_up_from_daily, _is_limit_up,
        _score_zt_candidate,
    )
    from .. import data_layer as dl
    from .. import cache_db as cdb

    t0 = time.time()
    log.info(f"[meta] build_meta_cache {start} → {end}")

    # 1) Load daily_cache
    log.info("[meta] loading daily_cache from cache_db ...")
    daily_cache = _batch_cache_load(cdb)
    log.info(f"[meta] daily_cache: {len(daily_cache)} stocks loaded")

    # 2) Get trade dates and stock list
    dates = dl.fetch_trade_dates(start, end)
    all_stocks = dl.fetch_stock_list_all()

    # 3) Build meta_cache: per date, detect limit-ups and score
    meta_cache: dict[str, list[dict]] = {}
    total_candidates = 0

    for date_str in dates:
        candidates = []
        for stock_entry in all_stocks:
            # all_stocks is set of (code, name) tuples
            code = stock_entry[0] if isinstance(stock_entry, tuple) else stock_entry
            df = daily_cache.get(code)
            if df is None or df.empty:
                continue
            zt_candidate = _detect_limit_up_from_daily(df, code, date_str)
            if not zt_candidate:
                continue

            # ZT score (multi-factor from zt_backtest engine)
            zt_score = _score_zt_candidate(zt_candidate)
            zt_norm = MetaAggregator.norm_zt(zt_score)

            # SP score (ma5 + wb + rl + candle quality, 0-100)
            sp_score = _compute_sp_score_from_df(df, date_str)

            # Meta score: weighted combination
            meta_score = 0.45 * zt_norm + 0.55 * sp_score

            # Enrich candidate with meta fields
            zt_candidate["_meta_score"] = round(meta_score, 2)
            zt_candidate["_zt_score"] = round(zt_score, 2)
            zt_candidate["_zt_norm"] = round(zt_norm, 2)
            zt_candidate["_sp_score"] = round(sp_score, 2)
            candidates.append(zt_candidate)
            total_candidates += 1

        # Sort by meta_score desc
        candidates.sort(key=lambda c: -c.get("_meta_score", 0))
        meta_cache[date_str] = candidates

    elapsed = time.time() - t0
    avg_per_day = total_candidates / max(len(dates), 1)
    log.info(f"[meta] built meta_cache: {total_candidates} candidates "
             f"over {len(dates)} days ({avg_per_day:.0f}/day) in {elapsed:.1f}s")

    return daily_cache, dates, all_stocks, meta_cache


# ── FastAPI registration ─────────────────────────────────────────

def register(app):
    """Register meta strategy endpoints on the FastAPI app."""
    from fastapi import Query
    from fastapi.responses import JSONResponse

    # Import envelope from server (defined early, safe to import)
    from .server import envelope
    # Use cache_store for Redis (defined before register call, unlike _store_get/_store_set)
    from .. import cache_store as _cs

    # ── Cache (mutable containers to avoid nonlocal in nested functions) ──
    _meta_state = {"cache": {}, "ts": 0.0, "inflight": False}
    _META_CACHE_LOCK = threading.Lock()
    _META_TTL_FRESH = 300
    _META_TTL_STALE = 600

    # ── Backtest state ──
    _META_BT_RUNS: dict[str, dict] = {}
    _META_BT_LOCK = threading.Lock()
    _META_BT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meta-bt")

    REDIS_KEY = "tuixue:meta:recommend:v1"
    REDIS_TTL = 300

    def _bg_recommend():
        """Background thread: recompute meta recommendations."""
        try:
            result = recommend_live(top_n=20)
            data = result  # already formatted
            with _META_CACHE_LOCK:
                _meta_state["cache"] = data
                _meta_state["ts"] = time.time()
            # Persist to Redis
            try:
                _cs.get_store().set(REDIS_KEY, data, ttl=REDIS_TTL)
            except Exception:
                pass
        except Exception as e:
            log.error(f"[meta] bg recommend failed: {e}")
        finally:
            _meta_state["inflight"] = False

    # ── GET /api/meta/recommend ───────────────────────────────
    @app.get("/api/meta/recommend")
    async def api_meta_recommend(
        top_n: int = Query(default=20, ge=5, le=50),
        refresh: bool = Query(default=False),
    ):
        """综合推荐：聚合 ZT + 策略选股 + 龙头战法 + 得鑫，返回 top N 股票。"""
        t_start = time.time()

        # Check Redis cache
        if not refresh:
            try:
                cached = _cs.get_store().get(REDIS_KEY)
                if cached:
                    picks = cached.get("picks", [])[:top_n]
                    cached["picks"] = picks
                    elapsed = time.time() - t_start
                    return envelope(
                        data=cached,
                        meta={"_cache": "redis", "_elapsed_ms": int(elapsed * 1000)},
                    )
            except Exception:
                pass

        # Check in-process cache
        with _META_CACHE_LOCK:
            cached = _meta_state.get("cache")
            cache_ts = _meta_state.get("ts", 0)
        fresh = (time.time() - cache_ts) < _META_TTL_FRESH
        stale_ok = (time.time() - cache_ts) < _META_TTL_STALE

        if cached and fresh and not refresh:
            picks = cached.get("picks", [])[:top_n]
            result = dict(cached, picks=picks)
            elapsed = time.time() - t_start
            return envelope(data=result, meta={"_cache": "memory", "_elapsed_ms": int(elapsed * 1000)})

        # Trigger background refresh if stale but not running
        if cached and stale_ok:
            if not _meta_state["inflight"]:
                _meta_state["inflight"] = True
                threading.Thread(target=_bg_recommend, daemon=True).start()
            picks = cached.get("picks", [])[:top_n]
            result = dict(cached, picks=picks)
            elapsed = time.time() - t_start
            return envelope(data=result, meta={"_stale": True, "_warming": True, "_elapsed_ms": int(elapsed * 1000)})

        # Cold path: run synchronously (first request or cache expired)
        inflight = _meta_state["inflight"]
        if not inflight:
            _meta_state["inflight"] = True

        if inflight:
            # Another thread is computing; wait up to 60s
            for _ in range(120):
                await asyncio.sleep(0.5)
                with _META_CACHE_LOCK:
                    if _meta_state.get("cache"):
                        cached = _meta_state["cache"]
                        break
            if not cached:
                elapsed = time.time() - t_start
                return envelope(
                    data={"picks": [], "stats": {"errors": ["timeout waiting for compute"]}},
                    meta={"_warming": True, "_timeout": True, "_elapsed_ms": int(elapsed * 1000)},
                )
            picks = cached.get("picks", [])[:top_n]
            result = dict(cached, picks=picks)
            elapsed = time.time() - t_start
            return envelope(data=result, meta={"_warming": True, "_elapsed_ms": int(elapsed * 1000)})

        # Compute now
        try:
            result = recommend_live(top_n=max(top_n, 20))
            with _META_CACHE_LOCK:
                _meta_state["cache"] = result
                _meta_state["ts"] = time.time()
            try:
                _cs.get_store().set(REDIS_KEY, result, ttl=REDIS_TTL)
            except Exception:
                pass
            picks = result.get("picks", [])[:top_n]
            result = dict(result, picks=picks)
            elapsed = time.time() - t_start
            return envelope(data=result, meta={"_fresh": True, "_elapsed_ms": int(elapsed * 1000)})
        except Exception as e:
            _meta_state["inflight"] = False
            elapsed = time.time() - t_start
            return envelope(
                data={"picks": [], "stats": {"errors": [str(e)]}},
                error=str(e),
                meta={"_elapsed_ms": int(elapsed * 1000)},
            )
        finally:
            _meta_state["inflight"] = False

    # ── POST /api/meta/backtest ───────────────────────────────
    @app.post("/api/meta/backtest")
    async def api_meta_backtest(
        start: str = Query(default="2026-05-01", description="开始日期 YYYY-MM-DD"),
        end: str = Query(default="2026-06-30", description="结束日期 YYYY-MM-DD"),
        top_n: int = Query(default=1, ge=1, le=20, description="每日选股数"),
        entry_rule: str = Query(default="close_t0", description="买入规则"),
        leverage_factor: float = Query(default=1.0, description="杠杆倍数"),
    ):
        """综合回测：以历史 meta 信号选股，ZT 引擎执行买卖。"""
        run_id = uuid.uuid4().hex[:12]

        with _META_BT_LOCK:
            _META_BT_RUNS[run_id] = {"status": "pending", "result": None, "error": None, "ts": time.time()}

        def _run():
            try:
                from ..zt_backtest import run_zt_backtest
                with _META_BT_LOCK:
                    _META_BT_RUNS[run_id]["status"] = "building_cache"

                # Build meta cache
                daily_cache, dates, all_stocks, meta_cache = build_meta_cache_historical(
                    start, end,
                )

                with _META_BT_LOCK:
                    _META_BT_RUNS[run_id]["status"] = "running_backtest"

                # Run ZT backtest with meta cache, neutralizing ZT-specific filters
                result = run_zt_backtest(
                    start=start,
                    end=end,
                    top_n=top_n,
                    entry_rule=entry_rule,
                    leverage_factor=leverage_factor,
                    # Neutralize ZT filter params (meta signal does its own filtering)
                    min_streak=1,
                    max_streak=99,
                    burst_max=99,
                    sealed_before="15:00",  # no time filter
                    mcap_min_yi=0,
                    mcap_max_yi=99999,
                    turnover_min_pct=0,
                    turnover_max_pct=100,
                    limit_order_min_yi=0,
                    board_filter="all",
                    exclude_yiziban=False,
                    yiziban_only=False,
                    gap_activate_pct=999,
                    regime_mode="always",
                    fill_rate=1.0,
                    _prebuilt=(daily_cache, dates, all_stocks, meta_cache),
                )

                # Attach meta info to result
                result["_meta"] = {
                    "meta_cache_dates": len(meta_cache),
                    "meta_candidates_total": sum(len(v) for v in meta_cache.values()),
                }

                with _META_BT_LOCK:
                    _META_BT_RUNS[run_id]["status"] = "done"
                    _META_BT_RUNS[run_id]["result"] = result
            except Exception as e:
                log.error(f"[meta] backtest {run_id} failed: {e}")
                with _META_BT_LOCK:
                    _META_BT_RUNS[run_id]["status"] = "error"
                    _META_BT_RUNS[run_id]["error"] = str(e)

        _META_BT_EXECUTOR.submit(_run)
        return envelope(data={"run_id": run_id, "status": "pending"})

    # ── GET /api/meta/backtest/status ─────────────────────────
    @app.get("/api/meta/backtest/status")
    async def api_meta_backtest_status(run_id: str = Query(...)):
        """查询综合回测状态。"""
        with _META_BT_LOCK:
            run = _META_BT_RUNS.get(run_id)
        if not run:
            return envelope(error=f"run_id {run_id} not found")
        elapsed = time.time() - run.get("ts", time.time())
        return envelope(data={
            "run_id": run_id,
            "status": run["status"],
            "result": run.get("result"),
            "error": run.get("error"),
            "elapsed_sec": round(elapsed, 1),
        })

    log.info("[meta] endpoints registered: /api/meta/recommend, /api/meta/backtest")
