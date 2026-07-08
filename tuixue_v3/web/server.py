"""
退学 v3 控制台 — FastAPI 应用。
- 端口：7799（避开 5000/8000/8080，与 macOS AirPlay/常见冲突）
- bind：0.0.0.0（手机同网段可访问）
- CORS：全开（同源部署为主，但允许 ngrok 等代理）
"""
from __future__ import annotations
import asyncio
import datetime
import functools
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests as _requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import fund_flow, seat_lookup

log = logging.getLogger("tuixue_v3.web")

STATIC_DIR = Path(__file__).parent / "static"

# ───────────────────────────────────────────────────────────
# 应用
# ───────────────────────────────────────────────────────────
app = FastAPI(
    title="退学 v3 控制台",
    description="实时选股 / 回测 / 资金流向 / 游资席位（远程浏览器）",
    version="2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────────────────────────────────────────
# 工具：TTL 缓存 + 线程池 + 统一错误信封
# ───────────────────────────────────────────────────────────
_T = TypeVar("_T")

class TTLCache:
    """进程内同步 TTL 缓存（key 必须是 hashable）。"""
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
_cache_quote   = TTLCache(default_ttl=5.0)     # 实时行情 5s（盘口活）
_cache_kline   = TTLCache(default_ttl=300.0)   # 日线 5min
_cache_fund    = TTLCache(default_ttl=30.0)    # 资金流 30s
_cache_overview = TTLCache(default_ttl=15.0)   # 大盘指数 15s

# 8 worker 线程足够 8 个端点并发（CPU 不重，I/O 重）
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
    """在 executor 跑同步函数，捕获异常 → None。永远不抛。"""
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

# 滑动窗口限频(per IP,内存) — 防止 1 个客户端打爆上游
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
    重置所有数据源冷却状态 — 解决「连续失败 → 5 分钟冷却 → 全源被禁用 → screen 超时」的死循环。
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
    # 每个指数缓存 15s — 防止 6 个并发请求同时打 qt.gtimg 被频控
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
# 个股相关 — 路由顺序很关键：具体路径必须放在 {code} 之前
# ───────────────────────────────────────────────────────────
@app.get("/api/stock/search")
async def stock_search(q: str = Query(..., min_length=1, max_length=10)):
    """模糊搜索：按 code 或 name 命中。
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
            })
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


@app.get("/api/stock/{code}")
async def stock_overview(code: str):
    """个股综合数据：4 个上游并行 + 单源失败不阻塞其他。"""
    code = code.strip().zfill(6)

    from .. import lib_common as lc
    from .. import data_layer as dl

    @cached(_cache_quote, key_fn=lambda c: ("quote", c))
    def _quote(code_):
        return lc.fetch_realtime(code_)

    async def _extras():
        return None  # placeholder for future

    quote_t = to_thread(_quote, code)
    flow_t  = to_thread(fund_flow.get_combined, code, 60)
    seats_t = to_thread(seat_lookup.get_stock_seats, code, 10)
    kline_t = to_thread(stock_kline_loader, code, 120)

    # 逃生：每分支独立超时 + 独立失败，
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

    quote, flow, seats, kline = await asyncio.wait_for(asyncio.gather(
        _with_timeout(quote_t, 3),
        _with_timeout(flow_t, 4),
        _with_timeout(seats_t, 3),
        _with_timeout(kline_t, 4),
    ), timeout=8)
    # 兜底：异常/None 转为前端期望的默认值
    def _ok(v, default):
        if isinstance(v, BaseException) or v is None:
            return default
        return v
    quote = _ok(quote, {})
    flow  = _ok(flow, {"code": code, "today": None, "history": []})
    seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False,
                         "seat_count": 0, "total_lhb_rows": 0, "known_groups": []})
    kline = _ok(kline, [])

    # 补 name 字段 — 腾讯/东财 quote 偶尔把 code 当 name 返回,要查表替换
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

    return envelope(data={
        "code": code,
        "quote": quote or {},
        "fund_flow": flow or {"code": code, "today": None, "history": []},
        "seats": seats or {"code": code, "rows": [], "blacklisted": False,
                            "seat_count": 0, "total_lhb_rows": 0, "known_groups": []},
        "kline": kline or [],
        "main_exit": None,
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
            })
        return rows
    return _load(code, days)


# ───────────────────────────────────────────────────────────
# AI 分析 — 调用 MiniMax（外部 MiniMax-M3 模型）给出基于铁律的"是否买"判定
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

【严格 JSON 输出 · 不许额外文字】
{
  "verdict": "买/观望/回避",
  "conviction": 0-100整数,
  "layer_pass": {"L1_风控": true/false, "L2_周期主线": true/false, "L3_形态": true/false, "L4_分时": true/false},
  "rules_passed": ["通过的规则名"],
  "rules_failed": ["失败的规则名"],
  "key_risks": ["关键风险点"],
  "summary": "一句话总结(60字内)"
}"""


# 兼容旧引用 (启动时初始化)
AI_SYSTEM_PROMPT = _build_ai_system_prompt()


def _call_minimax(api_key: str, code: str, ctx: dict) -> dict:
    """同步调用 MiniMax(M3) chat completion. 失败抛 RuntimeError."""
    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")

    last5 = ctx.get("kline", [])[-5:]
    fund_hist = ctx.get("fund_flow", {}).get("history", [])[-5:]
    seats = ctx.get("seats", {})
    quote = ctx.get("quote", {})

    user_content = f"""分析 {code}。行情={json.dumps(quote, ensure_ascii=False)[:200]}; K线(5日)={json.dumps(last5, ensure_ascii=False)[:300]}; 资金(5日)={json.dumps(fund_hist, ensure_ascii=False)[:300]}; 席位={json.dumps(seats, ensure_ascii=False)[:200]}。按 JSON 格式返回。"""

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # MiniMax-M3 会先输出 reasoning_content 再输出 JSON，
        # 800 tokens 在 46 铁律 + 长上下文下不够，已实测 content 被截断 (finish=length)
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
        # 4. 最后兜底: 尝试用正则从截断文本中提取 verdict / conviction / summary
        v_m = re.search(r'"verdict"\s*:\s*"([^"]+)"', text)
        c_m = re.search(r'"conviction"\s*:\s*(\d+)', text)
        s_m = re.search(r'"summary"\s*:\s*"([^"]+)"', text)
        return {
            "verdict": v_m.group(1) if v_m else "观望",
            "conviction": int(c_m.group(1)) if c_m else 30,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [],
            "rules_failed": [],
            "key_risks": ["AI 返回被截断,部分数据已尽力恢复"],
            "summary": s_m.group(1) if s_m else text[:200],
        }


@app.get("/api/stock/{code}/ai_analysis")
async def stock_ai_analysis(code: str):
    """基于铁律的 AI 买入判断. 需配置 MINIMAX_API_KEY 环境变量."""
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return envelope(
            error="MINIMAX_API_KEY 未配置",
            data={
                "verdict": "—",
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

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    # 硬超时总闸 10 秒：避免某个数据源 hang 把 AI 接口挂死
    try:
        quote, flow, seats, kline = await asyncio.wait_for(
            asyncio.gather(
                _wt(quote_t, 4),
                _wt(flow_t, 6),
                _wt(seats_t, 4),
                _wt(kline_t, 6),
            ),
            timeout=10,
        )
    except asyncio.TimeoutError:
        log.warning(f"AI 上游总超时 12s (code={code})")
        return envelope(error="AI 上游数据拉取超时（12s）", data={
            "verdict": "—", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "数据源拉取超时，请稍后重试或检查网络",
        })
    def _ok(v, default):
        return default if isinstance(v, BaseException) or v is None else v
    quote = _ok(quote, {})
    flow  = _ok(flow, {"code": code, "today": None, "history": []})
    seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False,
                         "seat_count": 0, "total_lhb_rows": 0, "known_groups": []})
    kline = _ok(kline, [])

    ctx = {"quote": quote, "fund_flow": flow, "seats": seats, "kline": kline}

    # 如果 K线 和 quote 都空, 才放弃 (允许 fund_flow/seats 空 — 它们有时拉不到)
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
            "verdict": "—", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "AI 调用超时（12s）— 数据源或模型连接慢，建议稍后重试",
        })
    except Exception as e:
        log.warning(f"AI 调用失败: {e}")
        return envelope(error=f"AI 调用失败: {e}", data={
            "verdict": "—", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": f"AI 调用失败: {e}",
        })

    if not result:
        return envelope(error="AI 返回空", data={
            "verdict": "—", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "AI 调用返回空（数据不足或上游超时）",
        })

    return envelope(data=result)


# ───────────────────────────────────────────────────────────
# 实时选股 / 回测 — 异步任务跑同步函数，SSE 推进度
# ───────────────────────────────────────────────────────────
class ScreenRequest(BaseModel):
    date: str | None = Field(None, description="YYYYMMDD；None=今日")
    mode: str = Field("live", pattern="^(live|backtest)$")
    top_n: int = Field(3, ge=1, le=10)
    pool_size: int = Field(20, ge=1, le=200, description="扫描前 N 只（默认 20, 50 只在数据源 ban 时会 60s 超时）")


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
# 龙头战法 — 6 维评分 + Top 10 + 全涨停列表
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
            "sentiment": {"label": "—", "zt_count": 0, "max_streak": 0, "streak_dist": {}},
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
# SSE 进度流 — 长任务实时反馈
# ───────────────────────────────────────────────────────────
@app.get("/api/stream/screen")
async def stream_screen(date: str | None = None, mode: str = "live"):
    """SSE：屏幕里 scan 时的进度。如果 run_stock_screen 内部没有 hook，就只推 done。"""
    from ..screen import run_stock_screen

    async def gen():
        yield {"event": "phase", "data": json.dumps({"phase": "start", "msg": "开始扫描 …"})}
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _EXECUTOR,
            functools.partial(run_stock_screen, date_str=date, mode=mode),
        )
        yield {"event": "phase", "data": json.dumps({"phase": "done", "n_picks": len(result.get("candidates", [])), "elapsed_sec": result.get("elapsed_sec")})}
        yield {"event": "result", "data": json.dumps(result, ensure_ascii=False, default=str)}

    return EventSourceResponse(gen())


@app.get("/api/stream/backtest")
async def stream_backtest(req: BacktestRequest):
    """SSE：回测进度。回测 loop 内部每 20 天打 log，前端这里每 2s 轮询一次 stats。"""
    from .. import backtest as bt_mod
    progress_state = {"done": False, "result": None}

    async def gen():
        yield {"event": "phase", "data": json.dumps({"phase": "start", "msg": "回测启动，可能数分钟 …"})}

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
def main():
    import uvicorn
    import argparse
    p = argparse.ArgumentParser(description="退学 v3 控制台")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=7799)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    print(f"\n  退学 v3 控制台 v2.0  →  http://{args.host}:{args.port}")
    print(f"  本机访问：     http://localhost:{args.port}")
    print(f"  手机访问：     http://<你的 Mac 内网 IP>:{args.port}  （同 WiFi）")
    print(f"  一键远程：bash {Path(__file__).parent}/start_remote.sh")
    print(f"  关闭：         Ctrl+C\n")
    uvicorn.run("tuixue_v3.web.server:app", host=args.host, port=args.port,
                reload=args.reload, log_level="warning")


if __name__ == "__main__":
    main()
