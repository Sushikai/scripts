"""
退学 v3 控制台 - FastAPI 应用。
- 端口:7799(避开 5000/8000/8080,与 macOS AirPlay/常见冲突)
- bind:0.0.0.0(手机同网段可访问)
- CORS:全开(同源部署为主,但允许 ngrok 等代理)
"""
from __future__ import annotations
import asyncio
import datetime
import functools
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests as _requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import fund_flow, seat_lookup

log = logging.getLogger("tuixue_v3.web")

STATIC_DIR = Path(__file__).parent / "static"


# ───────────────────────────────────────────────────────────
# JSON 净化器 — NaN/Inf → null,防 stock 接口 500
# ───────────────────────────────────────────────────────────
import math as _math
_SAFE = {float("nan"), float("inf"), float("-inf"), -float("inf")}


def _sanitize(o):
    if isinstance(o, float):
        if _math.isnan(o) or _math.isinf(o):
            return None
        return o
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(x) for x in o]
    return o


# 全局 monkey-patch json 模块的 dumps — 让所有响应自动过滤 NaN/Inf
_orig_dumps = json.dumps


def _safe_dumps(obj, **kw):
    return _orig_dumps(_sanitize(obj), **kw)


json.dumps = _safe_dumps

# ───────────────────────────────────────────────────────────
# 应用
# ───────────────────────────────────────────────────────────
app = FastAPI(
    title="退学 v3 控制台",
    description="实时选股 / 回测 / 资金流向 / 游资席位(远程浏览器)",
    version="2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────────────────────────────────────────
# 工具:TTL 缓存 + 线程池 + 统一错误信封
# ───────────────────────────────────────────────────────────
_T = TypeVar("_T")

class TTLCache:
    """进程内同步 TTL 缓存(key 必须是 hashable)。"""
    def __init__(self, default_ttl: float = 30.0):
        self.ttl = default_ttl
        self._data: dict[tuple, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._miss = 0

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                self._miss += 1
                return None
            data, ts = entry
            if time.monotonic() - ts > self.ttl:
                del self._data[key]
                self._miss += 1
                return None
            self._hits += 1
            return data

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic())

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._miss
            return {
                "size": len(self._data),
                "hits": self._hits,
                "miss": self._miss,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0,
            }


import threading  # noqa: E402

# 三档 TTL
_cache_spot    = TTLCache(default_ttl=60.0)    # 全市场股票列表 60s
_cache_quote   = TTLCache(default_ttl=5.0)     # 实时行情 5s(盘口活)
_cache_kline   = TTLCache(default_ttl=300.0)   # 日线 5min
_cache_fund    = TTLCache(default_ttl=30.0)    # 资金流 30s
_cache_overview = TTLCache(default_ttl=15.0)   # 大盘指数 15s
_cache_global  = TTLCache(default_ttl=60.0)   # 全球情绪 60s(美/韩数据源慢)

# 8 worker 线程足够 8 个端点并发(CPU 不重,I/O 重)
_EXECUTOR = ThreadPoolExecutor(max_workers=12)
# 单独给 long-running 任务 (screen/backtest) 用的池, 避免占满普通 worker
_LONG_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def cached(ttl_cache: TTLCache, key_fn: Callable[..., tuple]):
    """sync 函数缓存装饰器。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kw):
            key = key_fn(*args, **kw)
            hit = ttl_cache.get(key)
            if hit is not None:
                return hit
            try:
                val = fn(*args, **kw)
            except Exception as e:
                log.debug(f"cache miss fn {fn.__name__} err: {e}")
                return None
            if val is not None:
                ttl_cache.set(key, val)
            return val
        return wrap
    return deco


def envelope(data: Any = None, error: str | None = None, **extra) -> dict:
    """统一返回信封。"""
    return {
        "ok": error is None,
        "data": data,
        "error": error,
        "ts": time.time(),
        **extra,
    }


async def to_thread(fn: Callable[..., _T], *args, **kw) -> _T | None:
    """在 executor 跑同步函数,捕获异常 → None。永远不抛。"""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kw))
    except Exception as e:
        log.warning(f"{fn.__name__}{args[:1]} 失败: {e}")
        return None


# ───────────────────────────────────────────────────────────
# 静态资源
# ───────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ───────────────────────────────────────────────────────────
# 健康检查 + 缓存统计
# ───────────────────────────────────────────────────────────

# 滑动窗口限频(per IP,内存) - 防止 1 个客户端打爆上游
_ip_window: dict[str, list[float]] = {}
_ip_lock = threading.Lock()
RATE_WINDOW_SEC = 10.0
RATE_MAX_REQ = 60  # 10s 内最多 60 次


@app.middleware("http")
async def _rate_limit_middleware(request, call_next):
    """每 IP 滑动窗口限频。超过 RATE_MAX_REQ/10s → 429。"""
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    with _ip_lock:
        hits = _ip_window.setdefault(ip, [])
        hits[:] = [t for t in hits if now - t < RATE_WINDOW_SEC]
        if len(hits) >= RATE_MAX_REQ:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"ok": False, "error": f"rate-limited: {RATE_MAX_REQ}/{RATE_WINDOW_SEC}s per IP"},
                status_code=429,
            )
        hits.append(now)

    # trace id
    trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex[:12]
    request.state.trace_id = trace_id

    t0 = time.monotonic()
    try:
        resp = await call_next(request)
    except Exception as e:
        log.exception(f"[{trace_id}] {request.method} {request.url.path} 异常: {e}")
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "error": "internal", "trace_id": trace_id}, status_code=500)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    resp.headers["x-trace-id"] = trace_id

    # 指标计数
    counter_inc(request.url.path, elapsed_ms, resp.status_code >= 500)
    if resp.status_code >= 500:
        log.warning(f"[{trace_id}] {request.method} {request.url.path} → {resp.status_code} {elapsed_ms}ms")
    return resp


_metrics_lock = threading.Lock()
_metrics_counters: dict[str, dict] = {}


def counter_inc(path: str, ms: int, is_err: bool):
    """按 path 聚合:{count, total_ms, err_count, max_ms}"""
    with _metrics_lock:
        m = _metrics_counters.setdefault(path, {"count": 0, "err": 0, "sum_ms": 0, "max_ms": 0})
        m["count"] += 1
        m["sum_ms"] += ms
        m["max_ms"] = max(m["max_ms"], ms)
        if is_err:
            m["err"] += 1


@app.get("/api/health")
async def health():
    # SQLite 状态
    db_stats = {"rows": 0, "codes": 0, "size_kb": 0}
    try:
        from .. import cache_db
        db_stats = cache_db.daily().stats()
    except Exception:
        pass

    return {
        "ok": True,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": "2.0",
        "cache": {
            "spot": _cache_spot.stats(),
            "quote": _cache_quote.stats(),
            "kline": _cache_kline.stats(),
            "fund": _cache_fund.stats(),
            "overview": _cache_overview.stats(),
            "sqlite": db_stats,
        },
    }


@app.get("/api/metrics")
async def metrics():
    """API 指标(并发用户行为可视化用)"""
    with _metrics_lock:
        rows = []
        for path, m in sorted(_metrics_counters.items()):
            avg = round(m["sum_ms"] / max(1, m["count"]), 1)
            err_rate = round(m["err"] / max(1, m["count"]) * 100, 1)
            rows.append({
                "path": path,
                "count": m["count"],
                "err": m["err"],
                "err_rate_pct": err_rate,
                "avg_ms": avg,
                "max_ms": m["max_ms"],
                "p99_ms_estimate": m["max_ms"],
            })
    with _ip_lock:
        active_ips = len(_ip_window)
    return {
        "ok": True,
        "ts": time.time(),
        "endpoints": rows,
        "active_ips": active_ips,
        "limit": f"{RATE_MAX_REQ} req / {RATE_WINDOW_SEC}s / IP",
    }


@app.post("/api/admin/reset_sources")
async def reset_sources():
    """
    重置所有数据源冷却状态 - 解决「连续失败 → 5 分钟冷却 → 全源被禁用 → screen 超时」的死循环。
    用法: curl -X POST http://localhost:7799/api/admin/reset_sources
    """
    from tuixue_v3 import lib_common as lc
    result = lc.reset_source_health()
    log.info(f"🔄 数据源冷却已重置: {result}")
    return envelope(data=result)


# ───────────────────────────────────────────────────────────
# 退学心法 · 42 条铁律
# ───────────────────────────────────────────────────────────
@app.get("/api/laws")
async def laws_endpoint():
    """42 条铁律 + 4 大类 + 合规审计。前端 laws view 与 AI 复盘共用同一源。"""
    from .. import laws as _laws
    return envelope(data={
        "categories": _laws.CATEGORIES,
        "koujue": _laws.KOUJUE_TEXT,
        "audit": _laws.AUDIT,
        "flat": _laws.flat_laws(),
        "summary": _laws.summary(),
    })


# ───────────────────────────────────────────────────────────
# 大盘概览
# ───────────────────────────────────────────────────────────
INDICES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000300", "沪深300"),
    ("000688", "科创50"),
    ("399905", "中证500"),
]


async def _fetch_index(code: str, name: str) -> dict:
    # 每个指数缓存 15s - 防止 6 个并发请求同时打 qt.gtimg 被频控
    @cached(_cache_overview, key_fn=lambda c: ("idx", c))
    def _load(c: str):
        from .. import lib_common as lc
        return lc.fetch_realtime(c) or {}
    q = await to_thread(_load, code) or {}
    return {
        "code": code,
        "name": name,
        "price": _safe_float(q.get("最新价") or q.get("price")),
        "change_pct": _safe_float(q.get("涨跌幅") or q.get("change_pct")),
        "amount": _safe_float(q.get("成交额") or q.get("amount")),
    }


def _safe_float(x) -> float:
    try:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):  # NaN / Inf
            return 0.0
        return v
    except (TypeError, ValueError):
        return 0.0


@app.get("/api/market/overview")
async def market_overview():
    """6 大指数并行拉取 + 涨停数估算(东财限频时降级到部分数据)"""
    try:
        indices = await asyncio.wait_for(
            asyncio.gather(*[_fetch_index(c, n) for c, n in INDICES]),
            timeout=8,
        )
    except asyncio.TimeoutError:
        # 即使整体超时,也尝试给个空 envelope(指标全 0)避免前端 5xx
        indices = [{"code": c, "name": n, "price": 0, "change_pct": 0, "amount": 0} for c, n in INDICES]

    async def _zt_count():
        from .. import multi_source_fetchers as msf
        today = datetime.datetime.now().strftime("%Y%m%d")
        try:
            return await asyncio.wait_for(to_thread(msf.fetch_zt_pool, today), timeout=6)
        except Exception:
            return None

    zt = await _zt_count()
    zt_count = len(zt) if isinstance(zt, list) else 0

    return envelope(data={
        "indices": indices,
        "limit_up": zt_count,
        "limit_up_available": isinstance(zt, list),
        "ts": time.time(),
    })


# ───────────────────────────────────────────────────────────
# 全球情绪 — 美股 / 韩股 + 板块联动 + 风险偏好
# ───────────────────────────────────────────────────────────
@app.get("/api/global/sentiment")
async def global_sentiment(force: bool = False):
    """
    美股(纳指/标普/道指 + 七巨头 + 60 重点个股) + 韩股(KOSPI/KOSDAQ + 三星/SK Hynix)
    派生: 风险偏好(risk_on/risk_off/neutral) + sector impact 加权表
    60s 内存缓存; force=true 强制重拉。
    """
    if not force:
        cached = _cache_global.get(("global_sentiment",))
        if cached is not None:
            cached["from_cache"] = True
            return envelope(data=cached)

    def _do_fetch():
        from . import global_markets
        return global_markets.fetch_global_sentiment()

    try:
        result = await asyncio.wait_for(
            to_thread(_do_fetch), timeout=8,
        )
    except asyncio.TimeoutError:
        log.warning("global_sentiment 超时 8s")
        return envelope(error="全球情绪拉取超时", data={
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "indices": [], "us_leaders": [], "us_losers": [],
            "kr_leaders": [], "sector_impact": {},
        })
    except Exception as e:
        log.warning(f"global_sentiment 失败: {e}")
        return envelope(error=f"全球情绪失败: {e}", data={
            "sentiment": "neutral", "sentiment_score": 0.0,
            "indices": [], "us_leaders": [], "us_losers": [],
            "kr_leaders": [], "sector_impact": {},
        })

    _cache_global.set(("global_sentiment",), result)
    return envelope(data=result)


@app.get("/api/global/sentiment/prompt")
async def global_sentiment_prompt():
    """给 AI prompt 用: 把全球情绪压缩成 ≤1500 字符文本。"""
    from . import global_markets
    cached = _cache_global.get(("global_sentiment",))
    if cached is None:
        # 第一次未填充,临时拉一次
        try:
            data = await asyncio.wait_for(
                to_thread(global_markets.fetch_global_sentiment), timeout=8,
            )
            _cache_global.set(("global_sentiment",), data)
            cached = data
        except Exception:
            return envelope(data="(全球情绪暂不可用)")
    text = global_markets.render_for_prompt(cached, max_chars=1500)
    return envelope(data=text)


# ───────────────────────────────────────────────────────────
# 个股相关 - 路由顺序很关键:具体路径必须放在 {code} 之前
# ───────────────────────────────────────────────────────────
@app.get("/api/stock/search")
async def stock_search(q: str = Query(..., min_length=1, max_length=10)):
    """模糊搜索:按 code 或 name 命中。
    改用 fetch_stock_list (有缓存, 1s 内返回), 不再调 fetch_spot_a (全市场实时, 经常被 ban 卡 15s+)
    """
    q = q.strip()
    if not q:
        return envelope(data={"q": q, "results": []})

    @cached(_cache_spot, key_fn=lambda: ("stock_list_for_search",))
    def _load_stock_list():
        try:
            from .. import data_layer as dl
            return dl.fetch_stock_list() or []
        except Exception:
            return []

    try:
        spot = await asyncio.wait_for(to_thread(_load_stock_list), timeout=5) or []
    except asyncio.TimeoutError:
        spot = []
    hits = []
    q_lower = q.lower()
    for code, name in spot[:5000]:
        c, n = str(code), str(name or "")
        if q_lower in c.lower() or q in n:
            hits.append({"code": c, "name": n})
            if len(hits) >= 20:
                break
    return envelope(data={"q": q, "results": hits})


@app.get("/api/stock/{code}/kline")
async def stock_kline(code: str, days: int = Query(120, ge=30, le=400)):
    code = code.strip().zfill(6)

    @cached(_cache_kline, key_fn=lambda c, d: ("kline", c, d))
    def _load(code_, days_):
        from .. import lib_common as lc
        df = lc.fetch_daily(code_, days=days_)
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "date": str(row.get("日期", ""))[:10],
                "open": _safe_float(row.get("开盘")),
                "high": _safe_float(row.get("最高")),
                "low": _safe_float(row.get("最低")),
                "close": _safe_float(row.get("收盘")),
                "volume": _safe_float(row.get("成交量")),
                "amount": _safe_float(row.get("成交额")),
                "change_pct": _safe_float(row.get("涨跌幅")),
            })
        # 计算 MA5/MA10/MA20/MA60 + 涨跌幅 + 量比(相对 5 日均量)
        closes = [r["close"] for r in rows]
        vols = [r["volume"] for r in rows]
        for i, r in enumerate(rows):
            r["ma5"]  = round(sum(closes[max(0,i-4):i+1]) / min(i+1, 5), 3) if i+1 else 0
            r["ma10"] = round(sum(closes[max(0,i-9):i+1]) / min(i+1, 10), 3) if i+1 else 0
            r["ma20"] = round(sum(closes[max(0,i-19):i+1]) / min(i+1, 20), 3) if i+1 else 0
            r["ma60"] = round(sum(closes[max(0,i-59):i+1]) / min(i+1, 60), 3) if i+1 else 0
            v5 = sum(vols[max(0,i-4):i+1]) / min(i+1, 5) if i+1 else 0
            r["vol_ratio_5d"] = round(r["volume"] / v5, 2) if v5 else 0
        return rows

    kline = await to_thread(_load, code, days)
    return envelope(data={"code": code, "kline": kline or []})


@app.get("/api/stock/{code}/fund_flow")
async def stock_fund(code: str, days: int = Query(60, ge=10, le=180)):
    code = code.strip().zfill(6)
    @cached(_cache_fund, key_fn=lambda c, d: ("fund_flow", c, d))
    def _load(code_, days_):
        return fund_flow.get_combined(code_, days=days_)
    flow = await to_thread(_load, code, days)
    return envelope(data=flow or {"code": code, "today": None, "history": []})


@app.get("/api/stock/{code}/seats")
async def stock_seats(code: str, days: int = Query(30, ge=5, le=90)):
    code = code.strip().zfill(6)
    seats = await to_thread(seat_lookup.get_stock_seats, code, days)
    return envelope(data=seats or {"code": code, "rows": [], "blacklisted": False,
                                    "seat_count": 0, "total_lhb_rows": 0,
                                    "known_groups": []})


@app.get("/api/stock/{code}/intraday_5d")
async def stock_intraday_5d(code: str):
    """
    个股近 5 日分时 + 封成比。
    - 5 日日线 (本地 cache)
    - 5 日封成比 / 封单金额 / 连板 (涨停池)
    - 今日分时 tick (akshare stock_intraday_em)
    历史分钟 K 走东财 RemoteDisconnected 拿不到,盘中分时 tick 兜底
    """
    code = code.strip().zfill(6)

    def _load():
        from .. import multi_source_fetchers as msf
        from .. import lib_common as lc
        from pathlib import Path
        import json
        out = {"code": code, "daily_5d": [], "intraday_today": None, "note": ""}

        # 1) 交易日历(取最近 5 个)
        all_dates = sorted(msf.fetch_trade_dates())
        if not all_dates:
            return {**out, "note": "无交易日历"}
        # 找最近的 5 个交易日(按真实日期排序,过滤未来)
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        past_dates = [d for d in all_dates if d <= today_str]
        recent5 = past_dates[-5:] if len(past_dates) >= 5 else past_dates

        # 2) 5 日日线(本地 cache)
        cache_path = Path(f"tuixue_v3/cache/daily_{code}_130.json")
        daily_dict = {}
        if cache_path.exists():
            try:
                with cache_path.open() as f:
                    rows = json.load(f)
                daily_dict = {r["日期"]: r for r in rows if "日期" in r}
            except Exception as e:
                log.warning(f"读 daily cache 失败: {e}")

        # 3) 5 日涨停池(拿封成比/封单/连板)
        seal_by_date = {}
        for d in recent5:
            d_compact = d.replace("-", "")
            try:
                pool = msf.fetch_zt_pool(d_compact) or []
            except Exception as e:
                log.warning(f"涨停池拉取失败 {d}: {e}")
                pool = []
            for s in pool:
                if s.get("code") == code:
                    lo = float(s.get("limit_order_amount", 0) or 0)
                    amt = float(s.get("amount", 0) or 0)
                    seal_by_date[d] = {
                        "was_limit_up": True,
                        "streak": int(s.get("streak", 1) or 1),
                        "sealed_amount": lo,
                        "total_amount": amt,
                        "seal_ratio_pct": round(lo / amt * 100, 2) if amt > 0 else None,
                        "first_seal_time": s.get("first_time", ""),
                        "burst_count": int(s.get("burst_count", 0) or 0),
                        "sector": s.get("sector", ""),
                    }
                    break

        # 4) 拼 5 日
        prev_close = None
        for d in reversed(recent5):  # 老→新 排
            r = daily_dict.get(d, {})
            close = float(r.get("收盘", 0) or 0)
            change_pct = ((close / prev_close - 1) * 100) if (prev_close and close) else None
            seal = seal_by_date.get(d, {})
            out["daily_5d"].append({
                "date": d,
                "open": float(r.get("开盘", 0) or 0),
                "high": float(r.get("最高", 0) or 0),
                "low": float(r.get("最低", 0) or 0),
                "close": close,
                "volume": float(r.get("成交量", 0) or 0),
                "amount": float(r.get("成交额", 0) or 0),
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "was_limit_up": seal.get("was_limit_up", False),
                "streak": seal.get("streak"),
                "sealed_amount": seal.get("sealed_amount"),
                "seal_ratio_pct": seal.get("seal_ratio_pct"),
                "first_seal_time": seal.get("first_seal_time", ""),
                "burst_count": seal.get("burst_count"),
                "sector": seal.get("sector", ""),
            })
            prev_close = close
        out["daily_5d"].reverse()  # 回到新→老

        # 5 日累计 / 统计
        rows_5 = out["daily_5d"]
        if rows_5:
            valid_changes = [r["change_pct"] for r in rows_5 if r.get("change_pct") is not None]
            closes_5 = [r["close"] for r in rows_5 if r["close"]]
            seal_ratios = [r["seal_ratio_pct"] for r in rows_5 if r.get("seal_ratio_pct") is not None]
            streaks = [r["streak"] for r in rows_5 if r.get("was_limit_up") and r.get("streak")]
            up_days = sum(1 for c in valid_changes if c > 0)
            lu_days = sum(1 for r in rows_5 if r.get("was_limit_up"))
            cum_pct = round((closes_5[-1] / closes_5[0] - 1) * 100, 2) if len(closes_5) >= 2 and closes_5[0] else None
            out["summary_5d"] = {
                "cum_pct":        cum_pct,
                "up_days":        up_days,
                "down_days":      len(valid_changes) - up_days,
                "limit_up_days":  lu_days,
                "avg_change_pct": round(sum(valid_changes) / len(valid_changes), 2) if valid_changes else None,
                "max_change_pct": round(max(valid_changes), 2) if valid_changes else None,
                "min_change_pct": round(min(valid_changes), 2) if valid_changes else None,
                "avg_seal_ratio": round(sum(seal_ratios) / len(seal_ratios), 2) if seal_ratios else None,
                "max_streak":     max(streaks) if streaks else 0,
                "high_5d":        round(max(r["high"] for r in rows_5 if r["high"]), 2) if rows_5 else None,
                "low_5d":         round(min(r["low"] for r in rows_5 if r["low"]), 2) if rows_5 else None,
            }

        # 5) 今日分时 tick(akshare stock_intraday_em)
        try:
            import akshare as ak
            df = ak.stock_intraday_em(symbol=code)
            if df is not None and not df.empty:
                ticks = []
                for _, r in df.iterrows():
                    ticks.append({
                        "time": str(r.get("时间", "")),
                        "price": _safe_float(r.get("成交价")),
                        "volume_hand": _safe_float(r.get("手数")),  # 单位:手
                        "side": str(r.get("买卖盘性质", "")),
                    })
                out["intraday_today"] = {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "ticks": ticks,
                    "ticks_n": len(ticks),
                }
        except Exception as e:
            log.warning(f"分时 tick 拉取失败: {e}")
            out["note"] = f"今日分时未取到({str(e)[:60]})"

        # 6) 5 日每时 intraday(拼合多日分时,用于多日连续分时图)
        out["intraday_per_day"] = _fetch_intraday_per_day(code, recent5, out.get("intraday_today"))

        return out

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=15)
    except asyncio.TimeoutError:
        return envelope(error="intraday_5d 超时 15s", data={"code": code, "daily_5d": [], "intraday_today": None})
    return envelope(data=result)


def _fetch_intraday_for_date(code: str, date_str: str, prefer_source: str = "") -> dict:
    """
    拉取指定日期的 intraday tick。
    - date_str 格式 YYYY-MM-DD
    - prefer_source: "tencent" / "sina" / "" - 指定优先源(空 = 自动)
      - today + "" 自动时:tencent > sina > akshare(取 1min 精度优先)
      - 历史 + "" 自动时:sina > akshare
    - 失败时返回空 ticks + note 字段说明原因
    """
    from datetime import datetime
    import threading
    out = {"code": code, "date": date_str, "ticks": [], "ticks_n": 0, "source": "", "note": ""}
    today_str = datetime.now().strftime("%Y-%m-%d")
    is_today = (date_str == today_str)
    ymd = date_str.replace("-", "")

    # 1) akshare 主源 - DNS 劫持环境下会卡住 socket,用线程 + join timeout 强制 5s 上限
    def _ak_call():
        import akshare as ak
        if is_today:
            return ak.stock_intraday_em(symbol=code), "stock_intraday_em"
        try:
            return (ak.stock_zh_a_hist_min_em(symbol=code, period="1",
                                              start_date=ymd, end_date=ymd, adjust="qfq"),
                    "stock_zh_a_hist_min_em(1m)")
        except Exception:
            return (ak.stock_zh_a_hist_min_em(symbol=code, period="5",
                                              start_date=ymd, end_date=ymd, adjust="qfq"),
                    "stock_zh_a_hist_min_em(5m)")

    ak_box = {"df": None, "src": ""}
    def _ak_run():
        try:
            df, src = _ak_call()
            if df is not None and not df.empty:
                ak_box["df"] = df
                ak_box["src"] = src
        except Exception as e:
            log.info(f"akshare intraday 内部异常: {e}")

    t = threading.Thread(target=_ak_run, daemon=True)
    t.start()
    t.join(timeout=5)
    df = ak_box["df"]
    if df is not None and not df.empty:
        ticks = []
        for _, r in df.iterrows():
            ticks.append({
                "time":        str(r.get("时间", "")),
                "price":       _safe_float(r.get("成交价", r.get("收盘", r.get("最新价")))),
                "open":        _safe_float(r.get("开盘", 0)) or None,
                "high":        _safe_float(r.get("最高", 0)) or None,
                "low":         _safe_float(r.get("最低", 0)) or None,
                "volume_hand": _safe_float(r.get("手数", r.get("成交量"))),
                "amount":      _safe_float(r.get("成交额", 0)) or None,
                "side":        str(r.get("买卖盘性质", "")),
            })
        out["ticks"] = ticks
        out["ticks_n"] = len(ticks)
        out["source"] = ak_box["src"]
        return out

    # 2) sina 5min K(最多 480 根 ≈ 5 交易日,day 过滤到指定日期;今天/历史都走这条)
    try:
        import requests
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
        url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        r = requests.get(url, params={"symbol": f"{mkt}{code}", "scale": "5", "ma": "no", "datalen": "1440"},
                         timeout=6, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        if r.status_code == 200 and r.text.strip().startswith("["):
            import json as _json
            arr = _json.loads(r.text)
            # 过滤到指定日期
            day_prefix = date_str  # YYYY-MM-DD
            ticks = []
            for it in arr:
                day = it.get("day", "")
                if not day.startswith(day_prefix):
                    continue
                ticks.append({
                    "time":        day[11:19] if len(day) >= 19 else day,
                    "price":       _safe_float(it.get("close")),
                    "open":        _safe_float(it.get("open")) or None,
                    "high":        _safe_float(it.get("high")) or None,
                    "low":         _safe_float(it.get("low")) or None,
                    "volume_hand": _safe_float(it.get("volume")),  # sina 返回股数
                    "amount":      None,
                    "side":        "",
                })
            if ticks:
                out["ticks"] = ticks
                out["ticks_n"] = len(ticks)
                out["source"] = "sina_5min_k"
                return out
    except Exception as e:
        log.info(f"sina 5min 失败: {e}")

    # 3) 腾讯分钟 K - 只用于今天(历史传 date 参数仍返回最新一天,不可靠)
    if is_today:
        try:
            import requests as _req
            mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
            url = "http://web.ifzq.gtimg.cn/appstock/app/minute/query"
            params = {"code": f"{mkt}{code}"}
            r = _req.get(url, params=params, timeout=6,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            if r.status_code == 200:
                import json as _json
                j = r.json()
                data = (j.get("data") or {}).get(f"{mkt}{code}", {}).get("data") or {}
                raw = data.get("data") or []
                if raw:
                    ticks = []
                    for line in raw:
                        parts = line.split(" ")
                        if len(parts) < 3:
                            continue
                        t = parts[0]  # HHMM
                        if len(t) == 4 and t.isdigit():
                            t = f"{t[:2]}:{t[2:]}:00"
                        ticks.append({
                            "time":        t,
                            "price":       _safe_float(parts[1]),
                            "volume_hand": _safe_float(parts[2]) if len(parts) > 2 else None,
                            "amount":      _safe_float(parts[3]) if len(parts) > 3 else None,
                            "open": None, "high": None, "low": None, "side": "",
                        })
                    if ticks:
                        out["ticks"] = ticks
                        out["ticks_n"] = len(ticks)
                        out["source"] = "tencent_minute"
                        return out
        except Exception as e:
            log.info(f"tencent minute 失败: {e}")

    out["note"] = f"{date_str} 分时拉取失败(akshare + sina + tencent 三源全挂,可能是网络层 DNS 劫持或非交易日)"
    return out


def _fetch_intraday_today_tencent_first(code: str) -> dict | None:
    """
    今日分时 - 优先腾讯 1min,失败回退 _fetch_intraday_for_date
    返回 dict 或 None
    """
    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        import requests as _req
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
        url = "http://web.ifzq.gtimg.cn/appstock/app/minute/query"
        r = _req.get(url, params={"code": f"{mkt}{code}"}, timeout=5,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        if r.status_code == 200:
            j = r.json()
            data = (j.get("data") or {}).get(f"{mkt}{code}", {}).get("data") or {}
            raw = data.get("data") or []
            if raw:
                ticks = []
                for line in raw:
                    parts = line.split(" ")
                    if len(parts) < 3: continue
                    t = parts[0]
                    if len(t) == 4 and t.isdigit():
                        t = f"{t[:2]}:{t[2:]}:00"
                    ticks.append({
                        "time":        t,
                        "price":       _safe_float(parts[1]),
                        "volume_hand": _safe_float(parts[2]) if len(parts) > 2 else None,
                        "amount":      _safe_float(parts[3]) if len(parts) > 3 else None,
                        "open": None, "high": None, "low": None, "side": "",
                    })
                if ticks:
                    return {
                        "code": code, "date": today_str,
                        "ticks": ticks, "ticks_n": len(ticks),
                        "source": "tencent_minute", "note": "",
                    }
    except Exception as e:
        log.info(f"tencent minute (today first) 失败: {e}")
    return None


def _fetch_intraday_per_day(code: str, recent5: list[str], intraday_today: dict | None) -> dict:
    """
    给定近 5 个交易日 + 当日 intraday_today,返回每日的分时 ticks。
    - 今天优先 intraday_today(1min),无则走 tencent 1min,再回退 _fetch_intraday_for_date
    - 历史 4 天走 sina 5min K(按 day 过滤)
    返回 {"days":[{date, ticks:[{time,price,volume_hand,open,high,low}], source, ticks_n}], "note": ""}
    """
    from datetime import datetime
    out = {"code": code, "days": [], "note": ""}
    today_str = datetime.now().strftime("%Y-%m-%d")

    for d in recent5:
        day_obj = {"date": d, "ticks": [], "ticks_n": 0, "source": ""}
        if d == today_str:
            if intraday_today and intraday_today.get("ticks"):
                day_obj["ticks"] = intraday_today["ticks"]
                day_obj["ticks_n"] = len(intraday_today["ticks"])
                day_obj["source"] = intraday_today.get("source", "akshare_today")
            else:
                # akshare 失败 → tencent 1min → sina 5min
                ten = _fetch_intraday_today_tencent_first(code)
                if ten and ten.get("ticks"):
                    day_obj["ticks"] = ten["ticks"]
                    day_obj["ticks_n"] = ten["ticks_n"]
                    day_obj["source"] = ten["source"]
                else:
                    sub = _fetch_intraday_for_date(code, d)
                    if sub.get("ticks"):
                        day_obj["ticks"] = sub["ticks"]
                        day_obj["ticks_n"] = sub["ticks_n"]
                        day_obj["source"] = sub["source"]
            out["days"].append(day_obj)
            continue

        # 历史日期:复用 _fetch_intraday_for_date 的 sina 兜底逻辑
        try:
            sub = _fetch_intraday_for_date(code, d)
            if sub.get("ticks"):
                day_obj["ticks"] = sub["ticks"]
                day_obj["ticks_n"] = sub["ticks_n"]
                day_obj["source"] = sub["source"]
        except Exception as e:
            log.info(f"5 日分时 {d} 拉取失败: {e}")
        out["days"].append(day_obj)

    have = sum(1 for d in out["days"] if d.get("ticks"))
    out["note"] = "" if have == len(out["days"]) else f"5 日中 {have}/{len(out['days'])} 拉到分时"
    return out


@app.get("/api/stock/{code}/intraday")
async def stock_intraday(code: str, date: str = Query("", description="YYYY-MM-DD or YYYYMMDD, 留空=今日")):
    """
    任意交易日的分时 tick。
    - date=YYYY-MM-DD (推荐) 或 YYYYMMDD;不传默认今天
    - 今日: stock_intraday_em(盘中/盘后)
    - 历史: stock_zh_a_hist_min_em 1min → 5min 兜底
    失败时返回空 ticks + note 字段说明原因
    """
    code = code.strip().zfill(6)
    # 归一化 date
    d = (date or "").strip()
    if not d:
        from datetime import datetime
        d = datetime.now().strftime("%Y-%m-%d")
    d = d.replace("/", "-").replace(".", "-")
    if len(d) == 8 and d.isdigit():
        d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    def _load():
        return _fetch_intraday_for_date(code, d)

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=12)
    except asyncio.TimeoutError:
        return envelope(error="intraday 超时 12s",
                        data={"code": code, "date": d, "ticks": [], "ticks_n": 0, "note": "超时"})
    return envelope(data=result)


# ─────────────────────────────────────────────────────────────
# NEWS 模块:/api/news + /api/news/refresh + /api/news/analyze
# ─────────────────────────────────────────────────────────────
from . import news_lookup

@app.get("/api/news")
async def news_list(refresh: bool = Query(False, description="是否强制刷新抓取")):
    """
    返回当前新闻缓存(含 AI 评分)。
    - refresh=true 时:重新抓取 sina,但不强制 AI 重跑
    - 新闻按 ctime 倒序,AI 评分内嵌到每条 news.ai 字段
    """
    def _load():
        cache = news_lookup.get_cached_news(force_refresh=refresh)
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        out = []
        for n in news:
            item = dict(n)
            item["ai"] = ai.get(n["id"]) or None
            out.append(item)
        # 按 AI 分数降序(无 AI 的排最后,保持时间倒序)
        out.sort(key=lambda x: ((x.get("ai") or {}).get("score") or 0), reverse=True)
        return {
            "fetched_at":  cache.get("fetched_at") or 0,
            "analyzed_at": cache.get("analyzed_at") or 0,
            "news":        out,
            "count":       len(out),
            "ai_count":    sum(1 for n in out if n.get("ai")),
        }
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=15)
    except asyncio.TimeoutError:
        return envelope(error="news 拉取超时", data={"news": [], "count": 0})
    return envelope(data=result)


@app.post("/api/news/analyze")
async def news_analyze():
    """
    跑 AI 分析(增量:只分析尚未评分的新闻)。
    返回 {"analyzed": N, "total": M}
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return envelope(error="MINIMAX_API_KEY 未配置", data={"analyzed": 0, "total": 0})
    model   = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")

    def _run():
        cache = news_lookup._load_cache()
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        # 增量:跳过已有评分的
        pending = [n for n in news if n["id"] not in ai]
        if not pending:
            return {"analyzed": 0, "total": len(news), "skipped": len(news)}
        log.info(f"news AI analyze: {len(pending)} 条待评 (已有 {len(ai)})")
        new_ai = _analyze_news_with_ai(pending, api_key, model, base_url)
        if new_ai:
            news_lookup.save_ai_analysis(new_ai)
        return {"analyzed": len(new_ai), "total": len(news), "skipped": len(news) - len(pending)}

    try:
        result = await asyncio.wait_for(to_thread(_run), timeout=120)
    except asyncio.TimeoutError:
        return envelope(error="news AI 超时 120s", data={"analyzed": 0})
    return envelope(data=result)


@app.post("/api/news/refresh")
async def news_refresh():
    """
    强制重新抓取 + 立即跑 AI 分析(用于前端"刷新"按钮)。
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    model   = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")

    def _run():
        cache = news_lookup.get_cached_news(force_refresh=True)
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        pending = [n for n in news if n["id"] not in ai]
        analyzed = 0
        if pending and api_key:
            log.info(f"news refresh: AI 评 {len(pending)} 条")
            new_ai = _analyze_news_with_ai(pending, api_key, model, base_url)
            if new_ai:
                news_lookup.save_ai_analysis(new_ai)
                analyzed = len(new_ai)
        return {
            "fetched":  len(news),
            "analyzed": analyzed,
            "total":    len(news),
        }
    try:
        result = await asyncio.wait_for(to_thread(_run), timeout=130)
    except asyncio.TimeoutError:
        return envelope(error="news refresh 超时", data={"fetched": 0, "analyzed": 0})
    return envelope(data=result)


@app.get("/api/stock/{code}/sector")
async def stock_sector(code: str):
    """
    个股板块分类(交易所板块 + 4 套行业)
    """
    from .sector_classify import get_sector
    code = code.strip().zfill(6)
    def _load():
        return get_sector(code)
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=10)
    except asyncio.TimeoutError:
        return envelope(error="sector 超时", data={"code": code})
    return envelope(data=result)


@app.get("/api/stock/{code}/related_news")
async def stock_related_news(code: str):
    """
    与个股相关的新闻(按 AI 评分降序):
    - 该股所在申万行业被新闻 sectors 包含
    - 或新闻 stocks 列表里包含此 code
    """
    from .sector_classify import get_sector
    code = code.strip().zfill(6)
    def _load():
        sec = get_sector(code)
        sw = sec.get("sw")
        cache = news_lookup._load_cache()
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        matched = []
        for n in news:
            a = ai.get(n["id"])
            if not a: continue
            hit_reason = []
            if code in (a.get("stocks") or []):
                hit_reason.append("提及该股")
            if sw and sw in (a.get("sectors") or []):
                hit_reason.append(f"行业={sw}")
            if not hit_reason:
                continue
            item = dict(n)
            item["ai"] = a
            item["hit_reason"] = " · ".join(hit_reason)
            matched.append(item)
        matched.sort(key=lambda x: x["ai"].get("score") or 0, reverse=True)
        return {
            "code": code,
            "sector": sec,
            "news": matched,
            "count": len(matched),
        }
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=8)
    except asyncio.TimeoutError:
        return envelope(error="related_news 超时", data={"code": code, "news": []})
    return envelope(data=result)


@app.get("/api/sectors/realtime")
async def sectors_realtime():
    """申万 31 行业实时涨跌 + 资金流 rank → 综合板块情绪
    数据源: akshare → em 兜底;失败 → 静默返空数组
    返回 [{sw, change_pct, fund_flow_yi, top_stocks: [{code,name,change_pct}]}]
    """
    from .. import multi_source_fetchers as msf

    def _do_fetch():
        # 复用 fetch_hot_sectors(THS 单接口取全 90 板块按净流入/涨跌幅排序)
        try:
            sectors = msf.fetch_hot_sectors(top_n_flow=20, top_n_pct=20) or []
        except Exception as e:
            log.warning(f"sectors_realtime fetch_hot_sectors 失败: {e}")
            sectors = []
        rows = []
        for s in sectors:
            n = s.get("name") or ""
            if not n:
                continue
            rows.append({
                "sw":          n,
                "fund_flow_yi": s.get("fund_flow_yi") or s.get("净流入") or 0,
                "change_pct":   s.get("change_pct") or s.get("涨跌幅") or 0,
                "top_stocks":   s.get("top_stocks") or [],
            })
        return sorted(rows, key=lambda x: -(x.get("change_pct") or 0))

    @cached(_cache_global, key_fn=lambda: ("sectors_realtime",))
    def _cached_fetch():
        return _do_fetch()

    try:
        rows = await asyncio.wait_for(to_thread(_cached_fetch), timeout=10)
    except asyncio.TimeoutError:
        log.warning("sectors_realtime 超时 10s")
        return envelope(error="板块数据拉取超时", data={"sectors": []})
    except Exception as e:
        log.warning(f"sectors_realtime 失败: {e}")
        return envelope(error=f"板块数据失败: {e}", data={"sectors": []})

    # 综合情绪派生:看涨板块数 / 总板块数
    up = sum(1 for r in rows if r["change_pct"] > 0)
    down = sum(1 for r in rows if r["change_pct"] < 0)
    if up > down * 1.5:    agg = "积极"
    elif down > up * 1.5:  agg = "谨慎"
    else:                  agg = "震荡"

    return envelope(data={
        "sectors":     rows[:31],
        "n_up":        up,
        "n_down":      down,
        "aggregate":   agg,
        "ts":          time.time(),
    })


@app.get("/api/sectors/sw")
async def sectors_sw_overview():
    """
    申万 31 行业 - 用新闻 AI 评分聚合情绪(不做实时板块拉取,因 push2 被 DNS 拦截)
    返回 [{sw, news_count, bull_count, bear_count, avg_score, top_news:[{id,title,score,direction,reason}]}]
    """
    from .sector_classify import SW_31
    def _load():
        cache = news_lookup._load_cache()
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        agg = {sw: {"sw": sw, "news_count": 0, "bull_count": 0, "bear_count": 0,
                    "score_sum": 0.0, "top_news": []} for sw in SW_31}
        for n in news:
            a = ai.get(n["id"])
            if not a: continue
            for sw in (a.get("sectors") or []):
                if sw not in agg: continue
                e = agg[sw]
                e["news_count"] += 1
                if a.get("direction") == "利好": e["bull_count"] += 1
                elif a.get("direction") == "利空": e["bear_count"] += 1
                e["score_sum"] += (a.get("score") or 0)
                e["top_news"].append({"id": n["id"], "title": n["title"],
                                      "score": a.get("score", 0),
                                      "direction": a.get("direction", ""),
                                      "reason": a.get("reason", ""),
                                      "ctime_str": n.get("ctime_str", "")})
        out = []
        for sw, e in agg.items():
            nc = e["news_count"]
            e["avg_score"] = round(e["score_sum"] / nc, 2) if nc else 0
            del e["score_sum"]
            e["top_news"].sort(key=lambda x: x.get("score") or 0, reverse=True)
            e["top_news"] = e["top_news"][:3]
            out.append(e)
        out.sort(key=lambda x: (x["news_count"], x["avg_score"]), reverse=True)
        return {
            "fetched_at":  cache.get("fetched_at") or 0,
            "analyzed_at": cache.get("analyzed_at") or 0,
            "sectors":     out,
        }
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=8)
    except asyncio.TimeoutError:
        return envelope(error="sectors 超时", data={"sectors": []})
    return envelope(data=result)


@app.get("/api/stock/{code}")
async def stock_overview(code: str):
    """个股综合数据:4 个上游并行 + 单源失败不阻塞其他。"""
    code = code.strip().zfill(6)

    from .. import lib_common as lc
    from .. import data_layer as dl

    @cached(_cache_quote, key_fn=lambda c: ("quote", c))
    def _quote(code_):
        return lc.fetch_realtime(code_)

    def _holders(code_):
        from . import holder_lookup
        return holder_lookup.fetch_holder_info(code_)

    async def _extras():
        return None  # placeholder for future

    quote_t = to_thread(_quote, code)
    flow_t  = to_thread(fund_flow.get_combined, code, 60)
    seats_t = to_thread(seat_lookup.get_stock_seats, code, 10)
    kline_t = to_thread(stock_kline_loader, code, 120)
    holders_t = to_thread(_holders, code)

    # 逃生:每分支独立超时 + 独立失败,
    # 避免"4 个上游全冷启动→所有 await 全挂 18s→返回空 envelope"的局面
    async def _with_timeout(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except asyncio.TimeoutError:
            log.warning(f"上游超时 {sec}s (code={code})")
            return None
        except Exception as e:
            log.warning(f"上游异常: {e} (code={code})")
            return None

    quote, flow, seats, kline, holders = await asyncio.wait_for(asyncio.gather(
        _with_timeout(quote_t, 3),
        _with_timeout(flow_t, 4),
        _with_timeout(seats_t, 3),
        _with_timeout(kline_t, 4),
        _with_timeout(holders_t, 5),
    ), timeout=10)
    # 兜底:异常/None 转为前端期望的默认值
    def _ok(v, default):
        if isinstance(v, BaseException) or v is None:
            return default
        return v
    quote = _ok(quote, {})
    flow  = _ok(flow, {"code": code, "today": None, "history": []})
    seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False,
                         "seat_count": 0, "total_lhb_rows": 0, "known_groups": [],
                         "buy_total_wan": None, "sell_total_wan": None})
    kline = _ok(kline, [])
    holders = _ok(holders, None)

    # 补 name 字段 - 腾讯/东财 quote 偶尔把 code 当 name 返回,要查表替换
    # 用全 A 股票池 (含创业板/科创板), 不然 300xxx/688xxx 查不到
    cur_name = (quote.get("name") or "").strip()
    if not cur_name or cur_name == code or (cur_name.isdigit() and len(cur_name) == 6):
        try:
            for c, n in dl.fetch_stock_list_all() or []:
                if c == code:
                    quote["name"] = n
                    break
        except Exception as e:
            log.warning(f"[name-lookup] error for {code}: {e}")
    if not quote.get("name"):
        quote["name"] = code  # 最后兜底

    # 计算扩展指标 - 5日涨跌 / 振幅 / 量比 / 当日 OHLC
    price = float(quote.get("最新价") or 0)
    high = float(quote.get("最高") or 0)
    low = float(quote.get("最低") or 0)
    open_p = float(quote.get("今开") or 0)
    prev_close = float(quote.get("昨收") or 0)
    amplitude = ((high - low) / prev_close * 100) if (high and low and prev_close) else 0

    # 从 kline 算 5日涨跌、连板历史、5日均量
    kline5 = (kline or [])[-5:] if kline else []
    pct_5d = None
    pct_20d = None
    vol_5d_avg = None
    streak_history = []  # 近期涨停历史 [(date, streak)]
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
        # 涨停判定:用昨收(上一日 close) 算涨跌幅,避免一字板(open=close)误判
        # 一字板 op=cl=涨停价,但算 chg 应是 vs 昨收的 +10%
        # 主板/中小板 ±10%,科创/创业 ±20% → 阈值 9.5 / 19.5
        prev_c = 0
        for k in kline[-12:]:  # 多取 2 行确保有 prev
            cl = float(k.get("close") or 0)
            hi = float(k.get("high") or 0)
            op = float(k.get("open") or 0)
            if prev_c <= 0:
                prev_c = cl
                continue
            chg = (cl / prev_c - 1) * 100 if prev_c else 0
            # 阈值:主板 9.5,科创/创业 19.5
            limit_pct = 0.20 if str(k.get("date", "")).startswith("") and code.startswith(("300", "301", "688")) else 0.10
            limit_th = 19.0 if limit_pct >= 0.20 else 9.0
            if chg >= limit_th and abs(hi - cl) < 0.02 * cl:
                streak_history.append({"date": k.get("date"), "change_pct": round(chg, 2), "limit_pct": int(limit_pct * 100)})
            prev_c = cl

    # 涨停价 / 跌停价(按昨收 ±10%,科创创业 ±20%)
    is_kc = code.startswith(("300", "301", "688"))
    limit_pct = 0.20 if is_kc else 0.10
    limit_up_price = round(prev_close * (1 + limit_pct), 2) if prev_close else None
    limit_dn_price = round(prev_close * (1 - limit_pct), 2) if prev_close else None
    # 量比(若有)
    vol_ratio = quote.get("量比") or 0

    return envelope(data={
        "code": code,
        "quote": quote or {},
        "fund_flow": flow or {"code": code, "today": None, "history": []},
        "seats": seats or {"code": code, "rows": [], "blacklisted": False,
                            "seat_count": 0, "total_lhb_rows": 0, "known_groups": []},
        "kline": kline or [],
        "holders": holders,  # 散户/主力持股 (季报,含前十大流通股东集中度)
        "main_exit": None,
        # 扩展信息(前端可读)
        "extras": {
            "amplitude_pct":     round(amplitude, 2),
            "pct_5d":            pct_5d,
            "pct_20d":           pct_20d,
            "vol_5d_avg":        vol_5d_avg,
            "limit_up_price":    limit_up_price,
            "limit_dn_price":    limit_dn_price,
            "limit_pct":         limit_pct * 100,
            "streak_history":    streak_history,
            "is_chinext_star":   is_kc,
        },
        "ts": time.time(),
    })


def stock_kline_loader(code: str, days: int = 120) -> list[dict]:
    from .. import lib_common as lc
    @cached(_cache_kline, key_fn=lambda c, d: ("kline", c, d))
    def _load(code_, days_):
        df = lc.fetch_daily(code_, days=days_)
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "date": str(row.get("日期", ""))[:10],
                "open": _safe_float(row.get("开盘")),
                "high": _safe_float(row.get("最高")),
                "low": _safe_float(row.get("最低")),
                "close": _safe_float(row.get("收盘")),
                "volume": _safe_float(row.get("成交量")),
                "amount": _safe_float(row.get("成交额")),
                "change_pct": _safe_float(row.get("涨跌幅")),
            })
        # MA5/10/20/60 + 量比(相对 5 日均量)
        closes = [r["close"] for r in rows]
        vols = [r["volume"] for r in rows]
        for i, r in enumerate(rows):
            r["ma5"]  = round(sum(closes[max(0,i-4):i+1])  / min(i+1, 5),  3) if i+1 else 0
            r["ma10"] = round(sum(closes[max(0,i-9):i+1])  / min(i+1, 10), 3) if i+1 else 0
            r["ma20"] = round(sum(closes[max(0,i-19):i+1]) / min(i+1, 20), 3) if i+1 else 0
            r["ma60"] = round(sum(closes[max(0,i-59):i+1]) / min(i+1, 60), 3) if i+1 else 0
            v5 = sum(vols[max(0,i-4):i+1]) / min(i+1, 5) if i+1 else 0
            r["vol_ratio_5d"] = round(r["volume"] / v5, 2) if v5 else 0
        return rows
    return _load(code, days)


# ───────────────────────────────────────────────────────────
# AI 分析 - 调用 MiniMax(外部 MiniMax-M3 模型)给出基于铁律的"是否买"判定
# ───────────────────────────────────────────────────────────
def _build_ai_system_prompt() -> str:
    """精简版 system prompt, 让 M3 更快出 JSON (原版太长, max_tokens 600 不够)"""
    return """你是退学炒股 AI 助手。任务:基于行情/资金/席位,给"买/观望/回避"判定。

【核心铁律】
- 庄股/跟风/杂股 → 回避
- 拉萨天团主导 → 回避
- 九连阳后放量跌停 → 回避
- 4 层(风控/周期/形态/分时)全过 + 风险可控 → 买
- 任一层不过 → 观望
- 主力层失败 → 回避

【连板 & 板块联动加成规则】
- 该股连板 ≥ 3 且板块当日有 ≥ 5 只涨停 + ≥ 2 只连板 → 主线龙头, conviction 80+
- 该股刚首板 + 板块当日涨停 ≥ 10 只 → 主线启动中, 强烈关注, conviction 60-80
- 该股 ≥ 5 连板且板块已无新增涨停 → 高位分化, 风险加大, conviction ≤ 40
- 该股 ≥ 2 连板 + 板块当日同板块连板 ≥ 3 只 → 板块联动强, conviction +10
- 该股今日未涨停 + 近 5 日无涨停 + 板块有涨停 → 杂毛, conviction ≤ 30

【AI 概念标加成规则(2026-07 主战场 = 机器人/AI)】
- 该股 ai_tag.is_main_field = false(如"传统行业"或"未分类"),与机器人/AI 无关 → conviction ≤ 30, 不参与主线
- 该股 ai_tag ∈ {机器人本体, 机器人零部件, 机器视觉, AI 算力, AI 芯片, AI 软件, 半导体} → 主战场, conviction ≥ 50 起
- 该股 ai_tag = 智能驾驶 → 主战场外延, conviction 40-60 视乎资金流强弱
- 该股 ai_tag = 新能源车 → 主战场外溢, conviction ≤ 40(除非其他指标强烈支持)

【板块内角色定位(龙头 / 中军 / 杂毛)· 必填 role 字段】
- 龙头:该股连板 ≥ 3,且板块当日涨停 ≥ 5 只,或属于资金/席位/题材三维共振的核心标的(板块内辨识度第一梯队,涨跌幅/封单/连板领跑)
- 中军:板块内中等涨幅/中等连板(< 3 连板),市值偏中大盘,资金参与健康(非跟风拉抬、但跟随主线),是板块"中坚力量"
- 杂毛:今日未涨停 + 近 5 日无涨停 + 板块当日有涨停 / 拉萨天团主导 / 跟风无辨识度 / 不属于板块主战场 → 杂毛,verdict 倾向"回避"
- 默认:无法明确归类时回退"中军",避免错杀

【严格 JSON 输出 · 不许额外文字】
{
  "verdict": "买/观望/回避",
  "role": "龙头/中军/杂毛",
  "conviction": 0-100整数,
  "layer_pass": {"L1_风控": true/false, "L2_周期主线": true/false, "L3_形态": true/false, "L4_分时": true/false},
  "rules_passed": ["通过的规则名"],
  "rules_failed": ["失败的规则名"],
  "key_risks": ["关键风险点"],
  "summary": "一句话总结(60字内)"
}"""


# 兼容旧引用 (启动时初始化)
AI_SYSTEM_PROMPT = _build_ai_system_prompt()


# ── 新闻 AI 分析:批量送 MiniMax ───────────────────────────────
def _build_news_ai_system() -> str:
    """系统 prompt:要求按 JSON 输出每条新闻的资金引爆潜力评分"""
    from .sector_classify import sw_industry_choices_text, SW_31
    choices = sw_industry_choices_text()
    return f"""你是 A 股游资操盘手,专精"政策/产业新闻 → 资金板块流入"传导链分析。

任务:对每条财经新闻评估【资金引爆潜力】,输出标准化 JSON。

sectors 字段必须从以下【申万 31 一级行业】中选择(严格匹配):
{choices}

其它字段:
- score (0-10):资金引爆潜力。8+ 重大政策/突发利空/产业革命;5-7 中等利好;<5 弱
- direction: "利好" / "利空" / "中性"
- stocks: 涉及的 A 股代码列表(6 位),不确定不写
- reason: 1 句话(≤35 字)讲为什么能引爆资金,提到具体传导路径

输出严格 JSON 格式:
{{"items": [
  {{"id":"<原样回填>", "score":7.5, "direction":"利好", "sectors":["电力设备"], "stocks":["300750"], "reason":"..."}},
  ...
]}}

只输出 JSON,不要任何 markdown 围栏或注释。"""


def _analyze_news_with_ai(news_list: list[dict], api_key: str, model: str, base_url: str) -> dict[str, dict]:
    """
    批量分析新闻 → {id: {score, direction, sectors, stocks, reason}}
    自动分批(每批 15 条),避免单次 prompt 过大导致 finish=length 或超时
    """
    from .sector_classify import SW_31
    if not news_list:
        return {}

    BATCH = 15
    out: dict[str, dict] = {}
    for i in range(0, len(news_list), BATCH):
        batch = news_list[i:i + BATCH]
        out.update(_analyze_news_batch(batch, api_key, model, base_url))
    return out


def _analyze_news_batch(batch: list[dict], api_key: str, model: str, base_url: str) -> dict[str, dict]:
    """单批(≤15 条)→ {id: ai_dict}"""
    from .sector_classify import SW_31
    lines = ["请分析以下 {} 条 A 股财经新闻:\n".format(len(batch))]
    for n in batch:
        lines.append(f"--- id={n['id']} time={n.get('ctime_str','')} media={n.get('media','')} ---")
        lines.append(f"标题:{n['title']}")
        if n.get("intro"):
            lines.append(f"摘要:{n['intro'][:200]}")
        if n.get("keywords"):
            lines.append(f"关键词:{','.join(n['keywords'])}")
        lines.append("")
    user_content = "\n".join(lines)
    system_content = _build_news_ai_system()

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 3000,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err = ""
    for attempt, max_t in [(1, 3000), (2, 4500)]:
        body_local = dict(body)
        body_local["max_tokens"] = max_t
        try:
            r = _requests.post(base_url, json=body_local, headers=headers, timeout=45)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            j = r.json()
            text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not text:
                finish = j.get("choices", [{}])[0].get("finish_reason", "?")
                reasoning = j.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")[:200]
                last_err = f"empty content (finish={finish}, reasoning={reasoning})"
                log.warning(f"news AI attempt {attempt} content 为空 (finish={finish})")
                continue
            parsed = _parse_news_ai_json(text)
            if parsed.get("items"):
                out = {}
                for it in parsed["items"]:
                    if "id" not in it:
                        continue
                    sectors = [s for s in (it.get("sectors") or []) if s in SW_31]
                    out[it["id"]] = {
                        "score":     float(it.get("score", 0) or 0),
                        "direction": it.get("direction", "中性"),
                        "sectors":   sectors,
                        "stocks":    it.get("stocks", []) or [],
                        "reason":    (it.get("reason", "") or "")[:60],
                    }
                return out
            last_err = "parsed empty items"
        except _requests.exceptions.ReadTimeout as e:
            last_err = f"timeout (attempt {attempt}): {e}"
            log.warning(f"news AI attempt {attempt} 超时")
            continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log.warning(f"news AI attempt {attempt} 异常: {e}")
            continue

    log.warning(f"news AI batch 失败: {last_err}")
    return {}


def _parse_news_ai_json(text: str) -> dict:
    """宽松解析 news AI 的 JSON"""
    import re
    text = text.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]+?)(?:```|$)", text)
        if m:
            text = m.group(1).strip()
    if not text.startswith("{"):
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except Exception as e:
        log.warning(f"news AI JSON 解析失败: {e}; 原始前 200 字: {text[:200]}")
        return {}


def _call_minimax(api_key: str, code: str, ctx: dict) -> dict:
    """同步调用 MiniMax(M3) chat completion. 失败抛 RuntimeError."""
    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")

    last5 = ctx.get("kline", [])[-5:]
    fund_hist = ctx.get("fund_flow", {}).get("history", [])[-5:]
    seats = ctx.get("seats", {})
    quote = ctx.get("quote", {})
    limit_up = ctx.get("limit_up", {}) or {}
    sector = ctx.get("sector", {}) or {}
    ai_tags = sector.get("ai_tags", {}) or {}

    user_content = f"""分析 {code}。行情={json.dumps(quote, ensure_ascii=False)[:200]}; K线(5日)={json.dumps(last5, ensure_ascii=False)[:300]}; 资金(5日)={json.dumps(fund_hist, ensure_ascii=False)[:300]}; 席位={json.dumps(seats, ensure_ascii=False)[:200]}; 连板={json.dumps({
        'today_lianban': (limit_up.get('today') or {}).get('连板数', 0),
        'recent_5d_count': len(limit_up.get('recent_5d', [])),
        'recent_5d_dates': [r.get('date') for r in limit_up.get('recent_5d', [])[:5]],
        'sector_zt_count': len(limit_up.get('sector_today', [])),
        'sector_consecutive_count': sum(1 for x in limit_up.get('sector_today', []) if (x.get('连板数') or 0) >= 2),
        'sector_top': limit_up.get('sector_today', [])[:3],
        'summary': limit_up.get('summary', '')
    }, ensure_ascii=False)[:400]}; 行业={json.dumps({
        'sw': sector.get('sw'),
        'csrc': sector.get('csrc'),
        'ai_tags': ai_tags.get('labels', []),
        'is_main_field': ai_tags.get('is_main_field', False),
    }, ensure_ascii=False)[:200]}。按 JSON 格式返回。"""

    # 可选:全局(美/韩)情绪上下文(由 ai_scoring.score_batch 注入,key="_global_text")
    global_text = ctx.get("_global_text") or ""
    if global_text:
        user_content = f"{global_text}\n\n--- 个股具体数据 ---\n{user_content}"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # MiniMax-M3 会先输出 reasoning_content 再输出 JSON,
        # 800 tokens 在 46 铁律 + 长上下文下不够,已实测 content 被截断 (finish=length)
        "max_tokens": 700,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # M3 模型: max_tokens>700 会因长 reasoning 超时
    # 实测: 500/600 都能 10s 内完成, content_len 300-420 足够
    last_err = None
    text = ""
    # 2 次调用: 第一次试 700 tokens, 若 finish=length content 截断, 第二次补 1500 tokens
    for attempt, max_t in [(1, 700), (2, 1500)]:
        body_local = dict(body)
        body_local["max_tokens"] = max_t
        try:
            r = _requests.post(url, json=body_local, headers=headers, timeout=15)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            j = r.json()
            text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
            finish = j.get("choices", [{}])[0].get("finish_reason", "?")
            if text:
                return _parse_ai_json(text)
            # content 为空, 可能是 finish=length 截断, 给 retry 机会
            reasoning = j.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")[:200]
            last_err = f"empty content (finish={finish}, reasoning={reasoning})"
            log.warning(f"AI attempt {attempt} content 为空 (finish={finish})")
        except _requests.exceptions.ReadTimeout as e:
            last_err = f"timeout (attempt {attempt}): {e}"
            log.warning(f"AI attempt {attempt} 超时")
            continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log.warning(f"AI attempt {attempt} 异常: {e}")
            continue
    raise RuntimeError(f"AI 调用失败 ({last_err})")


def _parse_ai_json(text: str) -> dict:
    """宽松解析:AI 可能把 JSON 嵌在 ```json ... ``` 里, 也可能截断"""
    import re
    text = text.strip()

    # 1. 提取 markdown 围栏
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*([\s\S]+?)(?:```|$)", text)
        if m:
            text = m.group(1).strip()

    # 2. 提取第一个 {...} 块 (兜底: 即使有杂字符也能找)
    if not text.startswith("{"):
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            text = m.group(0)

    try:
        return json.loads(text)
    except Exception as e:
        # 3. 截断 JSON 兜底: 找到最后一个 } 截断
        idx = text.rfind("}")
        if idx > 0:
            try:
                return json.loads(text[:idx+1])
            except:
                pass
        log.warning(f"AI JSON parse failed: {e}; raw={text[:200]}")
        # 4. 最后兜底: 尝试用正则从截断文本中提取 verdict / role / conviction / summary
        v_m = re.search(r'"verdict"\s*:\s*"([^"]+)"', text)
        r_m = re.search(r'"role"\s*:\s*"([^"]+)"', text)
        c_m = re.search(r'"conviction"\s*:\s*(\d+)', text)
        s_m = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
        return {
            "verdict": v_m.group(1) if v_m else "观望",
            "role":    r_m.group(1) if r_m else "中军",
            "conviction": int(c_m.group(1)) if c_m else 30,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [],
            "rules_failed": [],
            "key_risks": ["AI 返回被截断,部分数据已尽力恢复"],
            "summary": s_m.group(1) if s_m else text[:200],
        }


@app.get("/api/stock/{code}/limit_up_context")
async def stock_limit_up_context(code: str, sector: str | None = None):
    """
    个股连板 & 板块涨停上下文

    数据源: akshare.stock_zt_pool_em (最近 N 个交易日涨停池)

    Args:
        code: 6 位股票代码
        sector: 该股所属板块(可选)- 传入可拉板块内涨停清单
    """
    from . import limit_up_context as _lu_ctx
    code = code.strip().zfill(6)

    def _load(c: str, s):
        return _lu_ctx.get_limit_up_context(c, sector_name=s)

    try:
        result = await asyncio.wait_for(
            to_thread(_load, code, sector),
            timeout=8,
        )
        return envelope(data=result)
    except asyncio.TimeoutError:
        log.warning(f"limit_up_context 超时 (code={code})")
        return envelope(error="连板/板块数据查询超时", data={
            "code": code, "today": None, "recent_5d": [],
            "sector_today": [], "summary": "查询超时",
        })
    except Exception as e:
        log.warning(f"limit_up_context 失败: {e}")
        return envelope(error=f"查询失败: {e}", data={
            "code": code, "today": None, "recent_5d": [],
            "sector_today": [], "summary": "查询失败",
        })


@app.get("/api/stock/{code}/ai_analysis")
async def stock_ai_analysis(code: str):
    """基于铁律的 AI 买入判断. 需配置 MINIMAX_API_KEY 环境变量."""
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()

    # 1) 先查 SQLite 日内缓存 — 命中直接返(避免每次都打 25-35s LLM)
    from .. import cache_db as _cdb
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    hit = _cdb.get_cached_ai(today_str, code, "MiniMax-M3")
    if hit:
        return envelope(data=hit)

    if not api_key:
        return envelope(
            error="MINIMAX_API_KEY 未配置",
            data={
                "verdict": "-",
                "role": "杂毛",
                "conviction": 0,
                "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
                "rules_passed": [],
                "rules_failed": [],
                "key_risks": [],
                "summary": "AI 模块未配置。请在 shell 中执行:export MINIMAX_API_KEY=你的新key(必须先在控制台撤销旧 key 重发)",
            },
        )

    # 并行拉所有上下文
    from .. import lib_common as lc
    @cached(_cache_quote, key_fn=lambda c: ("quote", c))
    def _quote(code_):
        return lc.fetch_realtime(code_)

    quote_t = to_thread(_quote, code)
    flow_t  = to_thread(fund_flow.get_combined, code, 60)
    seats_t = to_thread(seat_lookup.get_stock_seats, code, 10)
    kline_t = to_thread(stock_kline_loader, code, 60)
    # 限仓/连板上下文: 并行获取该股的连板状态 + 板块涨停清单
    from . import limit_up_context as _limit_up_ctx
    from .sector_classify import get_sector as _get_sector

    def _limit_up_load(c: str):
        # 拿到 sector 名称后过滤板块涨停 - 这里为简化先拿不带 sector 的版本
        return _limit_up_ctx.get_limit_up_context(c, sector_name=None)

    def _sector_load(c: str):
        return _get_sector(c)

    limit_up_t = to_thread(_limit_up_load, code)
    sector_t = to_thread(_sector_load, code)

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    # 硬超时总闸 10 秒:避免某个数据源 hang 把 AI 接口挂死
    try:
        quote, flow, seats, kline, limit_up, sector = await asyncio.wait_for(
            asyncio.gather(
                _wt(quote_t, 4),
                _wt(flow_t, 6),
                _wt(seats_t, 4),
                _wt(kline_t, 6),
                _wt(limit_up_t, 6),
                _wt(sector_t, 5),
            ),
            timeout=14,
        )
    except asyncio.TimeoutError:
        log.warning(f"AI 上游总超时 12s (code={code})")
        return envelope(error="AI 上游数据拉取超时(12s)", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "数据源拉取超时,请稍后重试或检查网络",
        })
    def _ok(v, default):
        return default if isinstance(v, BaseException) or v is None else v
    quote = _ok(quote, {})
    flow  = _ok(flow, {"code": code, "today": None, "history": []})
    seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False,
                         "seat_count": 0, "total_lhb_rows": 0, "known_groups": [],
                         "buy_total_wan": None, "sell_total_wan": None})
    kline = _ok(kline, [])
    limit_up = _ok(limit_up, {"code": code, "today": None, "recent_5d": [], "sector_today": [], "summary": "限仓数据拉取失败"})
    sector = _ok(sector, {"code": code, "sw": None, "ai_tags": {"labels": [], "is_main_field": False}})

    ctx = {"quote": quote, "fund_flow": flow, "seats": seats, "kline": kline, "limit_up": limit_up, "sector": sector}

    # 如果 K线 和 quote 都空, 才放弃 (允许 fund_flow/seats 空 - 它们有时拉不到)
    if (not kline) and (not quote):
        return envelope(error="数据源全挂, 无法分析", data={
            "code": code, "quote": quote, "fund_flow": flow,
            "seats": seats, "kline": kline,
        })

    try:
        # AI 调用 35s 硬闸 (_call_minimax 内部 2 次调用 × 15s, 留 5s 缓冲)
        result = await asyncio.wait_for(
            to_thread(_call_minimax, api_key, code, ctx),
            timeout=35,
        )
    except asyncio.TimeoutError:
        log.warning(f"AI 调用超时 35s (code={code})")
        return envelope(error="AI 调用超时", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "AI 调用超时(12s)- 数据源或模型连接慢,建议稍后重试",
        })
    except Exception as e:
        log.warning(f"AI 调用失败: {e}")
        return envelope(error=f"AI 调用失败: {e}", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": f"AI 调用失败: {e}",
        })

    if not result:
        return envelope(error="AI 返回空", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "AI 调用返回空(数据不足或上游超时)",
        })

    # 2) 写入 SQLite 日内缓存 — 下次同一只 stock 同一天秒返
    try:
        sector_name = (sector or {}).get("sw") or (sector or {}).get("name") or ""
        _cdb.upsert_ai(today_str, code, "MiniMax-M3", result, sector=sector_name)
    except Exception as e:
        log.debug(f"AI cache write fail: {e}")

    return envelope(data=result)


# ───────────────────────────────────────────────────────────
# 实时选股 / 回测 - 异步任务跑同步函数,SSE 推进度
# ───────────────────────────────────────────────────────────
class ScreenRequest(BaseModel):
    date: str | None = Field(None, description="YYYYMMDD;None=今日")
    mode: str = Field("live", pattern="^(live|backtest)$")
    top_n: int = Field(3, ge=1, le=10)
    pool_size: int = Field(20, ge=1, le=200, description="扫描前 N 只(默认 20, 50 只在数据源 ban 时会 60s 超时)")


class BacktestRequest(BaseModel):
    start: str = "2025-01-01"
    end: str = "2026-06-30"
    top_n: int = 3
    hold_days: int = 5
    sample: int = 200
    sell_mode: str = Field("rule", pattern="^(rule|max|close)$")


@app.post("/api/screen")
async def api_screen(req: ScreenRequest):
    from ..screen import run_stock_screen
    from .. import data_layer as dl
    stocks = None
    if req.pool_size and req.pool_size > 0:
        try:
            all_stocks = dl.fetch_stock_list() or []
            stocks = all_stocks[: req.pool_size]
        except Exception:
            stocks = None
    # 硬超时 60s: 防止 2786 全市场拖死 server
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _LONG_EXECUTOR,
                functools.partial(
                    run_stock_screen,
                    date_str=req.date, mode=req.mode, stocks=stocks,
                ),
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        log.warning(f"screen 超时 60s (pool={req.pool_size}, mode={req.mode})")
        return envelope(error="扫描超时 60s, 请缩小 pool_size 或重试", data={
            "candidates": [], "stats_by_layer": {},
        })
    return envelope(data=result or {})


@app.post("/api/backtest")
async def api_backtest(req: BacktestRequest):
    from ..backtest import run_backtest
    result = await to_thread(
        run_backtest,
        start=req.start, end=req.end,
        top_n=req.top_n, hold_days=req.hold_days,
        sell_mode=req.sell_mode, sample=req.sample,
    )
    return envelope(data=result or {})


# ───────────────────────────────────────────────────────────
# 复盘系统 (2026-07-10)
# ───────────────────────────────────────────────────────────
from . import review as _review

# ───────────────────────────────────────────────────────────
# AI 对话框 (2026-07-10)
# ───────────────────────────────────────────────────────────
from . import ai_chat

@app.get("/api/review/trades")
async def api_review_list_trades(limit: int = 50, code: str | None = None, since_days: int | None = 90):
    """最近 N 笔交易(含最新一次 AI 复盘摘要)。"""
    try:
        trades = _review.list_trades(limit=limit, code=code, since_days=since_days)
        # 每笔挂上 last_review(若有)
        for t in trades:
            t["last_review"] = _review.list_reviews(t["id"])[0] if _review.list_reviews(t["id"]) else None
        return envelope(data={"trades": trades, "count": len(trades)})
    except Exception as e:
        log.exception("list_trades")
        return envelope(error=str(e), status_code=500)


@app.post("/api/review/trades")
async def api_review_record_trade(payload: dict):
    """记一笔交易。payload: {code, direction, price, shares, occurred_at?, memo?, tags?[]}"""
    try:
        tid = _review.record_trade(
            code=payload.get("code", ""),
            direction=payload.get("direction", "buy"),
            price=float(payload.get("price", 0)),
            shares=int(payload.get("shares", 0)),
            occurred_at=payload.get("occurred_at"),
            memo=payload.get("memo", ""),
            tags=payload.get("tags", []) or [],
        )
        return envelope(data={"trade_id": tid, "trade": _review.get_trade(tid)})
    except Exception as e:
        log.exception("record_trade")
        return envelope(error=str(e), status_code=400)


@app.put("/api/review/trades/{trade_id}")
async def api_review_update_trade(trade_id: int, payload: dict):
    try:
        ok = _review.update_trade(trade_id, **payload)
        return envelope(data={"updated": ok, "trade": _review.get_trade(trade_id)})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.delete("/api/review/trades/{trade_id}")
async def api_review_delete_trade(trade_id: int):
    try:
        ok = _review.delete_trade(trade_id)
        return envelope(data={"deleted": ok})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.post("/api/review/trades/{trade_id}/review")
async def api_review_run(trade_id: int, force: bool = False):
    """AI 复盘:查 trade_reviews, 已有返缓存(force=False),否则调 LLM。"""
    try:
        result = _review.review_trade(trade_id, force=force)
        return envelope(data=result)
    except Exception as e:
        log.exception("review_trade")
        return envelope(error=str(e), status_code=500)


@app.get("/api/review/trades/{trade_id}/reviews")
async def api_review_list(trade_id: int):
    """某笔交易的所有 review 记录。"""
    try:
        return envelope(data={"reviews": _review.list_reviews(trade_id)})
    except Exception as e:
        return envelope(error=str(e), status_code=500)


@app.get("/api/review/stats")
async def api_review_stats(since_days: int = 90):
    """胜率/平均盈亏/常见错误。"""
    try:
        return envelope(data=_review.summary_stats(since_days=since_days))
    except Exception as e:
        return envelope(error=str(e), status_code=500)


@app.get("/api/review/next_picks")
async def api_review_next_picks():
    """次日选股 + 用户错模式风险。"""
    try:
        return envelope(data=_review.next_day_picks())
    except Exception as e:
        log.exception("next_picks")
        return envelope(error=str(e), status_code=500)


class ChatRequest(BaseModel):
    message: str
    code: str | None = None
    history: list[dict] | None = None  # [{role, content}]


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """AI 对话:用户问一句,带盘面/铁律 context,返打板建议。"""
    try:
        result = ai_chat.chat(req.message, code=req.code, history=req.history or [])
        return envelope(data=result)
    except Exception as e:
        log.exception("chat")
        return envelope(error=str(e), status_code=500)


# ───────────────────────────────────────────────────────────
# 龙头战法 - 6 维评分 + Top 10 + 全涨停列表
# ───────────────────────────────────────────────────────────
@app.get("/api/dragons")
async def api_dragons(date: str | None = None, refresh: bool = False):
    """
    龙头战法:
      - 情绪 (涨停家数 + 最高连板)
      - 主线 (前 5 板块)
      - Top 10 龙头 (按 6 维评分排序)
      - 全涨停列表 (含板块归属)
    30s 内存缓存; refresh=true 强制重拉
    """
    from ..dragons import score_dragons
    cache_key = f"dragons_{date or 'today'}"
    if not refresh:
        cached = _DRAGONS_CACHE.get(cache_key)
        if cached and (datetime.datetime.now() - cached["ts"]).total_seconds() < 30:
            return envelope(data=cached["data"])
    try:
        result = await asyncio.wait_for(
            to_thread(score_dragons, date),
            timeout=45,
        )
    except asyncio.TimeoutError:
        log.warning(f"dragons 超时 45s (date={date})")
        return envelope(error="龙头评分超时 45s", data={
            "top10": [], "all": [], "mainline": [],
            "sentiment": {"label": "-", "zt_count": 0, "max_streak": 0, "streak_dist": {}},
            "stats": {"reason": "timeout"},
        })
    if result:
        _DRAGONS_CACHE[cache_key] = {"data": result, "ts": datetime.datetime.now()}
    return envelope(data=result or {})


_DRAGONS_CACHE: dict[str, dict] = {}


@app.post("/api/optimize")
async def api_optimize():
    from ..optimizer import run_optimize
    result = await to_thread(run_optimize)
    return envelope(data=result or {})


# ───────────────────────────────────────────────────────────
# SSE 进度流 - 长任务实时反馈
# ───────────────────────────────────────────────────────────
@app.get("/api/stream/screen")
async def stream_screen(date: str | None = None, mode: str = "live"):
    """SSE:屏幕里 scan 时的进度。如果 run_stock_screen 内部没有 hook,就只推 done。

    2026-07-09: 增加 AI 打分阶段推送
      - phase="rule_done" 规则筛选完成, 准备进入 AI
      - phase="ai_done" 单只 AI 完成 (data: {code, ai})
      - phase="ai_aggregate" 综合榜完成 (data: {ranking, overall_view})
      - phase="done" 整个跑完
    """
    from ..screen import run_stock_screen
    from . import ai_scoring

    async def gen():
        yield {"event": "phase", "data": json.dumps({"phase": "start", "msg": "开始扫描 ..."})}
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _EXECUTOR,
            functools.partial(run_stock_screen, date_str=date, mode=mode),
        )
        candidates = result.get("candidates", []) if result else []
        yield {"event": "phase", "data": json.dumps({
            "phase": "rule_done",
            "n_picks": len(candidates),
            "elapsed_sec": result.get("elapsed_sec") if result else 0,
        })}

        # AI 阶段:回测/无 key 不打;否则 fan-out
        if mode == "live" and candidates and os.environ.get("MINIMAX_API_KEY", "").strip():
            ai_progress_done = []  # 已完成的 ai dict 缓存, 推送阶段用

            async def _on_progress(code, ai_or_none):
                # SSE 推送单只完成
                ai_progress_done.append((code, ai_or_none))

            ai_result = await ai_scoring.score_batch(
                candidates,
                on_progress=_on_progress,
            )
            # 推送 ai_done 事件:每个 code 推一条
            for code, ai in ai_progress_done:
                yield {"event": "phase", "data": json.dumps({
                    "phase": "ai_done",
                    "code": code,
                    "ai": ai,
                })}
            # 把 ai 注入 candidates
            ai_by_code = {s["code"]: s.get("ai") for s in ai_result["scored"]}
            for c in candidates:
                c["ai"] = ai_by_code.get(c.get("code"))
            # 综合榜 — 拉一次 global 共用(60s 缓存)
            from . import global_markets as _gm
            try:
                global_payload = await asyncio.get_event_loop().run_in_executor(
                    None, _gm.fetch_global_sentiment
                )
            except Exception:
                global_payload = None
            agg = await ai_scoring.score_aggregate(ai_result["scored"], global_payload=global_payload)
            if agg:
                result["ai_aggregate"] = agg
                yield {"event": "phase", "data": json.dumps({
                    "phase": "ai_aggregate",
                    "aggregate": agg,
                })}

        yield {"event": "phase", "data": json.dumps({
            "phase": "done",
            "n_picks": len(candidates),
            "elapsed_sec": result.get("elapsed_sec") if result else 0,
        })}
        yield {"event": "result", "data": json.dumps(result, ensure_ascii=False, default=str)}

    return EventSourceResponse(gen())


class AIEvaluateRequest(BaseModel):
    """已选股后单独拉综合榜(场景:用户重新调整排序/手工覆盖时)。"""
    scored: list[dict] = Field(default_factory=list,
                                description="[{code,name,sector,ai:dict}]")


@app.post("/api/screen/ai_aggregate")
async def api_screen_ai_aggregate(req: AIEvaluateRequest):
    """纯综合榜(无 MiniMax key → 直接返 error 不 500)。"""
    from . import ai_scoring
    if not os.environ.get("MINIMAX_API_KEY", "").strip():
        return envelope(error="MINIMAX_API_KEY 未配置", data={"ranking": [], "overall_view": "AI 未配置"})
    agg = await ai_scoring.score_aggregate(req.scored or [])
    if not agg:
        return envelope(error="综合榜生成失败", data={"ranking": [], "overall_view": "生成失败"})
    return envelope(data=agg)


@app.get("/api/stream/backtest")
async def stream_backtest(req: BacktestRequest):
    """SSE:回测进度。回测 loop 内部每 20 天打 log,前端这里每 2s 轮询一次 stats。"""
    from .. import backtest as bt_mod
    progress_state = {"done": False, "result": None}

    async def gen():
        yield {"event": "phase", "data": json.dumps({"phase": "start", "msg": "回测启动,可能数分钟 ..."})}

        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(
            _EXECUTOR,
            functools.partial(
                bt_mod.run_backtest,
                start=req.start, end=req.end,
                top_n=req.top_n, hold_days=req.hold_days,
                sell_mode=req.sell_mode, sample=req.sample,
            ),
        )

        last_log_size = 0
        log_path = Path("/tmp/tuixue_server.log")
        while not future.done():
            await asyncio.sleep(1.5)
            try:
                if log_path.exists():
                    cur = log_path.stat().st_size
                    if cur > last_log_size:
                        with log_path.open() as f:
                            f.seek(last_log_size)
                            chunk = f.read(cur - last_log_size)
                            last_log_size = cur
                        # 找最近一行回测进度
                        for line in chunk.splitlines()[::-1]:
                            if "回测进度" in line or "用时" in line:
                                yield {"event": "phase", "data": json.dumps({"phase": "progress", "msg": line.strip()})}
                                break
            except Exception:
                pass

        try:
            result = future.result()
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return

        yield {"event": "phase", "data": json.dumps({"phase": "done", "elapsed_sec": result.get("elapsed_sec"), "trades": result.get("summary", {}).get("trades", 0)})}
        yield {"event": "result", "data": json.dumps(result, ensure_ascii=False, default=str)}

    return EventSourceResponse(gen())


# ───────────────────────────────────────────────────────────
# 历史回测 / 优化报告
# ───────────────────────────────────────────────────────────
@app.get("/api/reports")
async def list_reports():
    from .. import config as cfg
    try:
        reports = sorted(
            [p for p in cfg.REPORT_DIR.glob("*.json")],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )[:50]
    except Exception as e:
        return envelope(error=str(e))

    out = []
    for p in reports:
        st = p.stat()
        out.append({
            "name": p.name,
            "size_kb": round(st.st_size / 1024, 1),
            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "type": "optimize" if "optimize" in p.name else "backtest",
            "url": f"/api/reports/{p.name}",
        })
    return envelope(data={"reports": out})


@app.get("/api/reports/{name}")
async def get_report(name: str):
    from .. import config as cfg
    p = cfg.REPORT_DIR / name
    if not p.exists() or not name.endswith(".json"):
        raise HTTPException(404)
    try:
        content = json.loads(p.read_text())
    except Exception as e:
        return envelope(error=str(e))
    return envelope(data={"name": name, "content": content})


# ───────────────────────────────────────────────────────────
# 启动入口
# ───────────────────────────────────────────────────────────
async def _preheat_cache_on_startup():
    """启动后预热 cache,避免首次接口拉到上游 11s+ 延迟

    用 httpx async client 调自己的端口 (不能用 TestClient 在已运行 server 里)
    """
    import asyncio
    import logging as _log
    import httpx as _httpx
    pre_log = _log.getLogger("tuixue_v3.preheat")
    pre_log.info("[启动预热] 开始...")

    # 从 main args 拿 host/port
    bind_host = os.environ.get("TUIXUE_PREHEAT_HOST", "127.0.0.1")
    bind_port = int(os.environ.get("TUIXUE_PREHEAT_PORT", "7799"))
    base = f"http://{bind_host}:{bind_port}"

    paths = [
        ("/api/market/overview", 8),
        ("/api/dragons",         20),  # 拉上游首次 11s, cache hit 0.1s
        ("/api/sectors/sw",      8),
        ("/api/stock/002747",    8),   # 机器人本体
        ("/api/stock/000977",    10),  # AI 算力 (多 6 个并行数据源)
        ("/api/stock/000524",    8),   # 杂毛
        ("/api/laws",            5),
    ]

    # 等 server 真起来了再发
    await asyncio.sleep(1.5)
    # httpx.Timeout 必须4个都给
    timeout = _httpx.Timeout(connect=3.0, read=25.0, write=10.0, pool=5.0)
    async with _httpx.AsyncClient(timeout=timeout, base_url=base) as client:
        for path, sec in paths:
            t0 = asyncio.get_event_loop().time()
            try:
                r = await client.get(path)
                ok = r.status_code == 200
            except Exception as e:
                pre_log.warning(f"[预热失败] {path}: {type(e).__name__}: {e}")
                continue
            t1 = asyncio.get_event_loop().time()
            mark = "✓" if ok else f"✗({r.status_code})"
            pre_log.info(f"[预热] {mark} {path} ({t1-t0:.2f}s)")

    pre_log.info("[启动预热] 完成 → 慢接口秒开")


@app.on_event("startup")
async def _on_startup_preheat():
    """uvicorn 启动后立即调 1 次预热, 不阻塞 server"""
    import asyncio
    if getattr(app, "_skip_preheat", False):
        log.info("[启动预热] 已跳过 (--no-preheat)")
        return
    asyncio.create_task(_preheat_cache_on_startup())


def main():
    import uvicorn
    import argparse
    # 加载 ~/.hermes/env.sh (MINIMAX_API_KEY 等)
    _env_sh = Path.home() / ".hermes" / "env.sh"
    if _env_sh.exists():
        try:
            import subprocess
            r = subprocess.run(
                ["bash", "-c", f"source {_env_sh} && env -0"],
                capture_output=True, timeout=5, text=True,
            )
            for line in (r.stdout or "").split("\x00"):
                if "=" in line and not line.startswith("_"):
                    k, _, v = line.partition("=")
                    if k and k not in os.environ:
                        os.environ[k] = v
            log.info(f"已 source {_env_sh} ({sum(1 for k in os.environ if k.startswith('MINIMAX'))} 个 MINIMAX_*)")
        except Exception as e:
            log.warning(f"source env.sh 失败: {e}")

    p = argparse.ArgumentParser(description="退学 v3 控制台")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=7799)
    p.add_argument("--reload", action="store_true")
    p.add_argument("--no-preheat", action="store_true",
                   help="跳过启动预热(开发用)")
    args = p.parse_args()

    if args.no_preheat:
        app._skip_preheat = True

    # 把端口/地址同步给 startup 钩子 (env var 走 os.environ)
    os.environ["TUIXUE_PREHEAT_PORT"] = str(args.port)
    os.environ["TUIXUE_PREHEAT_HOST"] = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host

    print(f"\n  退学 v3 控制台 v2.0  →  http://{args.host}:{args.port}")
    print(f"  本机访问:     http://localhost:{args.port}")
    print(f"  手机访问:     http://<你的 Mac 内网 IP>:{args.port}  (同 WiFi)")
    print(f"  一键远程:bash {Path(__file__).parent}/start_remote.sh")
    print(f"  关闭:         Ctrl+C")
    if not args.no_preheat:
        print(f"  🚀 启动预热已开启 (自动拉 7 个慢接口, 25-35s 后秒开)")
    print()
    uvicorn.run("tuixue_v3.web.server:app", host=args.host, port=args.port,
                reload=args.reload, log_level="warning")


# ═══════════════════════════════════════════════════════════════
# 复盘系统 v2 — 资金占比 + SSE 流 (2026-07-10)
# ═══════════════════════════════════════════════════════════════

# 资金流缓存(60s 短 TTL,前端每 10s 拉)
_cache_capital = TTLCache(default_ttl=60.0)


def _fetch_capital_one(code: str) -> dict:
    """单只股票的资金结构(主力/散户/基金占比)。
    3 源降级: 东财 push2 → 腾讯 qt.gtimg → akshare
    关键: server 进程网络可能受限,用 subprocess 调独立 Python 拿数据(绕开 server 网络栈)
    """
    code = code.strip().zfill(6)

    # 用独立子进程拿数据,避免 server 进程网络问题
    py = sys.executable  # 当前 server 用的 python
    helper = textwrap.dedent(f'''
        import sys, json, requests
        code = "{code}"
        out = {{"code": code, "ts": 0, "main_pct": 0, "retail_pct": 0, "fund_pct": 0,
                "main_amount": 0, "big_amount": 0, "mid_amount": 0, "sml_amount": 0, "source": "fallback"}}
        # 试 1: 东财 push2
        try:
            secid = ("0" if code.startswith(("6","9","5")) else "1") + "." + code
            r = requests.get("https://push2.eastmoney.com/api/qt/stock/get",
                             params={{"secid": secid, "fields": "f170,f171,f168,f169"}},
                             headers={{"User-Agent": "Mozilla/5.0"}}, timeout=5)
            if r.status_code == 200:
                j = r.json() or {{}}
                d = j.get("data") or {{}}
                if d:
                    main = float(d.get("f170", 0) or 0) / 1e4
                    big = float(d.get("f168", 0) or 0) / 1e4
                    mid = float(d.get("f169", 0) or 0) / 1e4
                    sml = float(d.get("f171", 0) or 0) / 1e4
                    total = abs(main) + abs(sml) + 1e-6
                    out.update({{
                        "main_pct": round(main / total * 100, 2),
                        "retail_pct": round(sml / total * 100, 2),
                        "main_amount": round(main, 2),
                        "big_amount": round(big, 2),
                        "mid_amount": round(mid, 2),
                        "sml_amount": round(sml, 2),
                        "source": "eastmoney"
                    }})
                    print(json.dumps(out))
                    sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"eastmoney fail: {{e}}\\n")
        # 试 2: 腾讯
        try:
            r = requests.get(f"https://qt.gtimg.cn/q=ff_{{code}}",
                             headers={{"User-Agent": "Mozilla/5.0"}}, timeout=5)
            if r.status_code == 200 and '="' in r.text:
                body = r.text.split('="', 1)[1].rstrip('";')
                fs = body.split("~")
                if len(fs) > 34:
                    main = float(fs[30] or 0)
                    big = float(fs[31] or 0)
                    mid = float(fs[33] or 0)
                    sml = float(fs[34] or 0)
                    total = abs(main) + abs(sml) + 1e-6
                    out.update({{
                        "main_pct": round(main / total * 100, 2),
                        "retail_pct": round(sml / total * 100, 2),
                        "main_amount": round(main, 2),
                        "big_amount": round(big, 2),
                        "mid_amount": round(mid, 2),
                        "sml_amount": round(sml, 2),
                        "source": "tencent"
                    }})
                    print(json.dumps(out))
                    sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"tencent fail: {{e}}\\n")
        # 试 3: akshare
        try:
            import akshare as ak
            market = "sh" if code.startswith(("6","9","5")) else "sz"
            df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                main = float(row.get("主力净流入-净额") or 0) / 1e4
                big = float(row.get("大单净额") or 0) / 1e4
                mid = float(row.get("中单净额") or 0) / 1e4
                sml = float(row.get("小单净额") or 0) / 1e4
                main_pct = float(row.get("主力净流入-净占比") or 0)
                total = abs(main) + abs(sml) + 1e-6
                out.update({{
                    "main_pct": main_pct,
                    "retail_pct": round(sml / total * 100, 2),
                    "main_amount": round(main, 2),
                    "big_amount": round(big, 2),
                    "mid_amount": round(mid, 2),
                    "sml_amount": round(sml, 2),
                    "source": "akshare"
                }})
                print(json.dumps(out))
                sys.exit(0)
        except Exception as e:
            sys.stderr.write(f"akshare fail: {{e}}\\n")
        # 全失败
        print(json.dumps(out))
    ''').strip()

    try:
        r = subprocess.run(
            [py, "-c", helper],
            capture_output=True, timeout=20, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip().splitlines()[-1])
            data["ts"] = int(time.time())
            return data
        else:
            log.debug(f"capital helper failed: rc={r.returncode} stderr={r.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log.warning(f"_fetch_capital_one {code} 子进程超时")
    except Exception as e:
        log.warning(f"_fetch_capital_one {code} 异常: {e}")

    return {
        "code": code, "ts": int(time.time()),
        "main_pct": 0.0, "retail_pct": 0.0, "fund_pct": 0.0,
        "main_amount": 0.0, "big_amount": 0.0, "mid_amount": 0.0, "sml_amount": 0.0,
        "source": "fallback",
    }


@app.get("/api/capital_flow")
async def api_capital_flow(codes: str = Query(..., description="逗号分隔,最多 20 只")):
    """批量资金结构(主力/散户/基金占比)。
    前端表格每 10s 调一次。
    性能:用 1 个 helper 子进程拿所有 code,3 源并发,避免每只 1 个 subprocess。
    """
    code_list = [c.strip().zfill(6) for c in codes.split(",") if c.strip()][:20]
    if not code_list:
        return envelope(data={"flows": []})

    # 先返缓存命中,缺哪些再拉
    cached_out = []
    missing = []
    for c in code_list:
        hit = _cache_capital.get(("cap", c))
        if hit is not None:
            cached_out.append(hit)
        else:
            missing.append(c)
    if not missing:
        return envelope(data={"flows": cached_out, "ts": time.time(), "cached": True})

    # 用 1 个 helper 子进程批量拉 missing
    try:
        result = await asyncio.wait_for(
            to_thread(_batch_capital_helper, missing),
            timeout=min(20, 4 + len(missing) * 2),
        )
        if result:
            for item in result:
                _cache_capital.set(("cap", item["code"]), item)
            flows = cached_out + result
        else:
            flows = cached_out
        return envelope(data={"flows": flows, "ts": time.time(), "cached": False})
    except Exception as e:
        log.warning(f"/api/capital_flow 失败: {e}")
        return envelope(error=str(e), data={"flows": cached_out})


def _batch_capital_helper(codes: list[str]) -> list[dict]:
    """单子进程批量拉 N 只股票的资金结构(3 源降级:东财→腾讯→akshare)。
    用 curl 子进程 (系统 PATH 里有),不用 Python requests — 绕开 server 进程网络栈问题。
    """
    out = []
    for code in codes:
        result = {
            "code": code, "ts": int(time.time()),
            "main_pct": 0.0, "retail_pct": 0.0, "fund_pct": 0.0,
            "main_amount": 0.0, "big_amount": 0.0, "mid_amount": 0.0, "sml_amount": 0.0,
            "source": "fallback",
        }
        # 1) 东财 push2 (用 curl 命令行数组,避免引号转义问题)
        for retry in range(2):
            try:
                secid = ("0" if code.startswith(("6","9","5")) else "1") + "." + code
                r = subprocess.run(
                    ["curl", "-s", "--max-time", "6",
                     "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36",
                     "-H", "Referer: https://quote.eastmoney.com/",
                     f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f170,f171,f168,f169"],
                    capture_output=True, timeout=8, text=True,
                )
                if r.returncode == 0 and r.stdout.strip():
                    j = json.loads(r.stdout) or {}
                    d = j.get("data") or {}
                    if d and d.get("f170") is not None:
                        main = float(d.get("f170", 0) or 0) / 1e4
                        big = float(d.get("f168", 0) or 0) / 1e4
                        mid = float(d.get("f169", 0) or 0) / 1e4
                        sml = float(d.get("f171", 0) or 0) / 1e4
                        tot = abs(main) + abs(sml) + 1e-6
                        result.update({
                            "main_pct": round(main / tot * 100, 2),
                            "retail_pct": round(sml / tot * 100, 2),
                            "main_amount": round(main, 2),
                            "big_amount": round(big, 2),
                            "mid_amount": round(mid, 2),
                            "sml_amount": round(sml, 2),
                            "source": "eastmoney",
                        })
                        break
            except Exception as e:
                log.debug(f"{code} eastmoney retry{retry}: {e}")
        if result["source"] != "fallback":
            out.append(result); continue
        # 2) 腾讯 qt.gtimg
        try:
            market_prefix = "sh" if code.startswith(("6","9","5")) else "sz"
            r = subprocess.run(
                ["curl", "-s", "--max-time", "5",
                 "-H", "User-Agent: Mozilla/5.0",
                 "-H", "Referer: https://gu.qq.com/",
                 f"https://qt.gtimg.cn/q=ff_{market_prefix}{code}"],
                capture_output=True, timeout=7, text=True,
            )
            if r.returncode == 0 and r.stdout and '="' in r.stdout and 'none_match' not in r.stdout:
                body = r.stdout.split('="', 1)[1].rstrip('";')
                fs = body.split("~")
                if len(fs) > 34:
                    main = float(fs[30] or 0); big = float(fs[31] or 0)
                    mid = float(fs[33] or 0); sml = float(fs[34] or 0)
                    tot = abs(main) + abs(sml) + 1e-6
                    result.update({
                        "main_pct": round(main / tot * 100, 2),
                        "retail_pct": round(sml / tot * 100, 2),
                        "main_amount": round(main, 2),
                        "big_amount": round(big, 2),
                        "mid_amount": round(mid, 2),
                        "sml_amount": round(sml, 2),
                        "source": "tencent",
                    })
                    out.append(result); continue
        except Exception as e:
            log.debug(f"{code} tencent: {e}")
        # 3) akshare 兜底
        try:
            py = sys.executable
            market = "sh" if code.startswith(("6","9","5")) else "sz"
            ak_script = (
                f"import sys, json\n"
                f"sys.path.insert(0, '/Users/kaikai/.hermes/hermes-agent/venv/lib/python3.11/site-packages')\n"
                f"import akshare as ak\n"
                f"df = ak.stock_individual_fund_flow(stock='{code}', market='{market}')\n"
                f"r = df.iloc[-1].to_dict() if df is not None and not df.empty else {{}}\n"
                f"print(json.dumps(r, default=str))"
            )
            r = subprocess.run([py, "-c", ak_script], capture_output=True, timeout=18, text=True)
            if r.returncode == 0 and r.stdout.strip():
                row = json.loads(r.stdout.strip().splitlines()[-1])
                main = float(row.get("主力净流入-净额") or 0) / 1e4
                big = float(row.get("大单净额") or 0) / 1e4
                mid = float(row.get("中单净额") or 0) / 1e4
                sml = float(row.get("小单净额") or 0) / 1e4
                main_pct = float(row.get("主力净流入-净占比") or 0)
                tot = abs(main) + abs(sml) + 1e-6
                result.update({
                    "main_pct": main_pct,
                    "retail_pct": round(sml / tot * 100, 2),
                    "main_amount": round(main, 2),
                    "big_amount": round(big, 2),
                    "mid_amount": round(mid, 2),
                    "sml_amount": round(sml, 2),
                    "source": "akshare",
                })
                out.append(result); continue
        except Exception as e:
            log.debug(f"{code} akshare: {e}")
        out.append(result)
    return out


@app.get("/api/stream/review/{trade_id}")
async def api_stream_review(trade_id: int):
    """SSE 流:AI 复盘进度推送。
    事件类型:
      - 'start'   复盘开始
      - 'progress' 阶段消息(build_ctx / ai_call / parse)
      - 'rules'    铁律分析片段
      - 'done'     完成(带最终结果)
      - 'error'    失败
    """
    async def event_gen():
        # 1) start
        yield {"event": "start", "data": json.dumps({"trade_id": trade_id, "ts": time.time()}, ensure_ascii=False)}
        await asyncio.sleep(0.05)
        try:
            # 2) build context
            yield {"event": "progress", "data": json.dumps({"stage": "build_ctx", "msg": "拉盘面 K线/资金/游资..."}, ensure_ascii=False)}
            await asyncio.sleep(0.05)
            # 3) ai call (同步执行 review_trade; force=False 优先用缓存)
            import functools as _ft
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _EXECUTOR, _ft.partial(_review.review_trade, trade_id, force=False)
            )
            # 4) rules 流式推送
            for r in result.get("rules_failed", []):
                yield {"event": "rule_failed", "data": json.dumps(r, ensure_ascii=False)}
                await asyncio.sleep(0.05)
            for r in result.get("rules_passed", []):
                yield {"event": "rule_passed", "data": json.dumps(r, ensure_ascii=False)}
                await asyncio.sleep(0.03)
            # 5) done
            yield {"event": "done", "data": json.dumps(result, ensure_ascii=False, default=str)}
        except Exception as e:
            log.exception("stream_review")
            yield {"event": "error", "data": json.dumps({"err": str(e)[:300]}, ensure_ascii=False)}
    return EventSourceResponse(event_gen())


if __name__ == "__main__":
    main()
