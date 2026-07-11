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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import fund_flow, seat_lookup
from .. import cache_store

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
# GZip:app.js 140KB / style.css 64KB / HTML 37KB,走隧道必须压
app.add_middleware(GZipMiddleware, minimum_size=512)


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

    def invalidate(self, *keys: tuple) -> int:
        """失效指定 key。返回实际删除条数。
        支持一个或多个 key;空参=全清(小心使用)。
        """
        with self._lock:
            if not keys:
                n = len(self._data)
                self._data.clear()
                return n
            n = 0
            for k in keys:
                if self._data.pop(k, None) is not None:
                    n += 1
            return n

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
_cache_fund    = TTLCache(default_ttl=60.0)    # 资金流 60s (2026-07-11 30→60,减少 akshare 限频期刷新)
_cache_overview = TTLCache(default_ttl=15.0)   # 大盘指数 15s
_cache_global  = TTLCache(default_ttl=60.0)   # 全球情绪 60s(美/韩数据源慢)

# 实时抓取 — 跟踪最近 1h 访问过的 code,后台 poller 用来滚动预热 quote
# (2026-07-11 进页面 ?fresh=1 + 10s 轮询 配合用)
_recent_codes: dict[str, float] = {}      # code → last_access_ts (monotonic)
_RECENT_TTL = 3600                         # 1h
_RECENT_LOCK = threading.Lock()

def _touch_recent(code: str) -> None:
    """记录该 code 最近被访问过 — 后台 poller 拿来预热。"""
    if not code or len(code) != 6 or not code.isdigit():
        return
    with _RECENT_LOCK:
        _recent_codes[code] = time.time()

def _prune_recent() -> list[str]:
    """清理 1h 没访问的,返回剩余活跃 code 列表。"""
    now = time.time()
    with _RECENT_LOCK:
        expired = [c for c, t in _recent_codes.items() if now - t > _RECENT_TTL]
        for c in expired:
            _recent_codes.pop(c, None)
        return list(_recent_codes.keys())

# 短端点池:20 worker — 即使有 1-2 个 12s 长任务排队,其余 18 个 worker 仍能跑快端点
_EXECUTOR = ThreadPoolExecutor(max_workers=20)
# 单独给 long-running 任务 (screen/backtest/optimize) 用的池, 避免占满普通 worker
_LONG_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def cached(ttl_cache: TTLCache, key_fn: Callable[..., tuple]):
    """sync 函数缓存装饰器。

    关键: 不缓存「空结果」/「失败降级」结果 (空 dict/空 list) — 否则上游一次失败
    会污染缓存 5s,期间所有请求都拿到空数据。
    """
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
            # 只缓存「有意义」的结果 — 空 dict / 空 list / None 都不入缓存
            if val is None:
                return None
            if isinstance(val, (dict, list, str)) and len(val) == 0:
                return val  # 但仍返回(让上层决定);不入缓存
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
# 静态资源(自定义 cache-control + 指纹 + ETag)
#   /             → no-cache,渲染时把 ?v=hash 注入,迭代立刻全网生效
#   /static/*     → Cache-Control: max-age=3600, swr=60 + 强 ETag
# ───────────────────────────────────────────────────────────
import mimetypes as _mimetypes
import hashlib as _hashlib
import re as _re
from fastapi import Response as _Response, Request as _Request


def _stale_while_revalidate(seconds: int) -> str:
    """cache-control: 客户端缓存 max-age 秒,过期后 stale 允许 60s 内继续用。"""
    return f"public, max-age={seconds}, stale-while-revalidate=60"


def _fingerprint(path: Path) -> str:
    """mtime + size → SHA-256[:8] — 文件变则指纹变。
    比全内容 hash 快 100x;误判概率约 2^-32(校验和碰撞),可忽略。
    """
    try:
        st = path.stat()
        h = _hashlib.sha256(f"{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:8]
        return h
    except Exception:
        return "0" * 8


# 启动时算一次 — 启动后很少再改;每次请求时校验 mtime 是否变了,变了就重算
_FP_CACHE: dict[str, tuple[float, str]] = {}


def _live_fingerprint(name: str, path: Path) -> str:
    """按 mtime 缓存 — 文件修改立即感知(<1ms,内存 dict lookup)。"""
    try:
        mt = path.stat().st_mtime_ns
    except Exception:
        return "0" * 8
    cached = _FP_CACHE.get(name)
    if cached and cached[0] == mt:
        return cached[1]
    h = _fingerprint(path)
    _FP_CACHE[name] = (mt, h)
    return h


_INDEX_HTML_PATH = STATIC_DIR / "index.html"
_PLACEHOLDER = _re.compile(r"__([A-Z_]+)_V__")


def _render_index_html() -> bytes:
    """读取 index.html,把指纹占位符替换掉。"""
    try:
        raw = _INDEX_HTML_PATH.read_text(encoding="utf-8")
    except Exception:
        return b"<h1>index.html missing</h1>"
    css_h = _live_fingerprint("style.css", STATIC_DIR / "style.css")
    js_h  = _live_fingerprint("app.js",   STATIC_DIR / "app.js")
    raw = raw.replace("__JS_V__",  js_h).replace("__CSS_V__", css_h)
    # 同时把 sw.js 也注入指纹 — 让 SW 注册不会被卡死缓存
    sw_h = _live_fingerprint("sw.js", STATIC_DIR / "sw.js")
    if sw_h != "0" * 8:
        raw = raw.replace("</head>", f'<script>__SW_URL__="/sw.js?v={sw_h}"</script></head>', 1)
    return raw.encode("utf-8")


@app.get("/", include_in_schema=False)
async def root():
    """HTML 强刷:no-cache + 指纹注入 — 用户改前端,所有访问会被强制重新加载新的 ?v=xxx。"""
    body = _render_index_html()
    return _Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            # ETag 让有条件的客户端只校验,304 省带宽
            "ETag": _hashlib.md5(body).hexdigest()[:16],
        },
    )


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service Worker 文件 — 单独路由,绕过 /static 的 SHA 缓存(浏览器 SW scope 必须根路径)。
    Cache-Control: no-cache + ETag: 改了 SW 后浏览器下次启动能立刻检测到更新。
    """
    sw_path = STATIC_DIR / "sw.js"
    if not sw_path.is_file():
        return _Response(content=b"// sw.js not found", media_type="application/javascript", status_code=404)
    raw = sw_path.read_bytes()
    try:
        st = sw_path.stat()
        etag = _hashlib.md5(f"{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
    except Exception:
        etag = '"unknown"'
    return _Response(
        content=raw,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, must-revalidate",
            # 必须让 SW 知道什么时候更新 — 改 SW 后下次访问会拉新
            "Service-Worker-Allowed": "/",
            "ETag": etag,
        },
    )


@app.get("/static/{path:path}")
async def static_files(path: str, request: _Request):
    """静态文件 → 1h 客户端缓存 + 60s stale-while-revalidate + 强 ETag。
    配合 HTML 里的 `?v=hash`,新版 URL 区别 → 立刻生效;旧版 max-age 到期前还在。
    """
    target = (STATIC_DIR / path).resolve()
    static_root = STATIC_DIR.resolve()
    if not str(target).startswith(str(static_root)) or not target.is_file():
        raise HTTPException(404)
    mime, _ = _mimetypes.guess_type(str(target))
    try:
        st = target.stat()
        etag = _hashlib.md5(f"{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
    except Exception:
        etag = '"unknown"'

    # 304 协商缓存 — If-None-Match 命中,且请求是 GET → 省传输
    inm = request.headers.get("if-none-match")
    if inm and inm.strip() == etag:
        return _Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": _stale_while_revalidate(3600),
            },
        )

    body = target.read_bytes()
    return _Response(
        content=body,
        media_type=mime or "application/octet-stream",
        headers={
            "Cache-Control": _stale_while_revalidate(3600),
            "ETag": etag,
        },
    )


# 兼容旧的 StaticFiles mount(被上面的路由优先匹配,但留着不影响)


# ───────────────────────────────────────────────────────────
# 健康检查 + 缓存统计
# ───────────────────────────────────────────────────────────

# 滑动窗口限频(per IP,内存) - 防止 1 个客户端打爆上游
_ip_window: dict[str, list[float]] = {}
_ip_lock = threading.Lock()
RATE_WINDOW_SEC = 10.0
# 2026-07-11: 60→200。 100 轮 × 14 端点 × 3 并发 = 4200 次 / ~25s = 1680 req/10s
# 实际用户不会这么快, 200 是给前端轮询 + 压测同时跑的安全边界。
RATE_MAX_REQ = 200  # 10s 内最多 200 次


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
    """健康检查。SQLite stats 走 1s 硬超时 — DB 卡时仍能 200 返。
    2026-07-11: 之前 to_thread 走 _EXECUTOR 12 worker,screen/backtest 把池占满时
    health 也跟着排队 3s 超时。改成尽量不抢 worker。
    """
    db_stats = {"rows": 0, "codes": 0, "size_kb": 0}
    store_stats: dict = {}
    store_status: dict = {}

    def _db():
        from .. import cache_db
        return cache_db.daily().stats()

    try:
        # 直接跑,不上 to_thread(避免 worker 池占满时被卡)。
        # SQLite stats 是 SELECT count(*), 亚毫秒级,卡死概率极低。
        db_stats = _db()
    except Exception:
        pass  # DB 慢/锁 → 返空 stats

    try:
        _store = cache_store.get_store()
        store_stats = _store.stats()
        store_status = _store.status()
    except Exception as e:
        store_stats = {"error": str(e)[:120]}

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
            "redis_store": store_stats,
            "redis_status": store_status,
        },
        "realtime": {
            "recent_count": len(_recent_codes),
            "recent_codes": list(_recent_codes.keys())[:20],
            "poller_running": getattr(app.state, "_poller", None) is not None,
        },
    }


@app.get("/api/healthz")
async def healthz():
    """K8s liveness probe — 进程活着就 200。
    不做 IO / 不查 DB / 不读磁盘。用于 k8s/load balancer 检测进程崩溃。"""
    return {"ok": True, "kind": "live"}


@app.get("/api/readyz")
async def readyz():
    """K8s readiness probe — 检查关键依赖是否可用。
    通过条件: ① SQLite cache 可写 ② 至少一个网络可达(DNS lookup)
    失败时返回 503,前端可显示 degraded UI。
    """
    checks = {"sqlite": False, "dns": False, "disk": False}
    # 1) SQLite cache
    try:
        from .. import cache_db
        cache_db.daily().stats()
        checks["sqlite"] = True
    except Exception as e:
        checks["sqlite_err"] = str(e)[:120]
    # 2) DNS lookup (轻量,能解析 cf/akshare/qq 即认为上游可达)
    try:
        import socket
        socket.gethostbyname("push2.eastmoney.com")
        socket.gethostbyname("qt.gtimg.cn")
        checks["dns"] = True
    except Exception as e:
        checks["dns_err"] = str(e)[:120]
    # 3) 磁盘空间 < 95% 视为 ok — 但绝对值至少留 200MB (sandboxes 常 <1GB total)
    try:
        import shutil
        usage = shutil.disk_usage("/")
        free_pct = usage.free / usage.total
        free_gb = usage.free / 1024**3
        # 双重门槛:剩 5% 或至少 200MB,避免 sandbox 误报
        checks["disk"] = free_pct > 0.05 or free_gb > 0.2
        checks["disk_free_gb"] = round(free_gb, 2)
        checks["disk_free_pct"] = round(free_pct * 100, 1)
    except Exception as e:
        checks["disk_err"] = str(e)[:120]

    all_ok = all(checks[k] for k in ("sqlite", "dns", "disk"))
    return JSONResponse(
        {"ok": all_ok, "checks": checks, "ts": datetime.datetime.now().isoformat(timespec="seconds")},
        status_code=200 if all_ok else 503,
    )


@app.get("/api/tunnel/status")
async def tunnel_status():
    """读 tunnel_url.txt → 公网 URL + 局域网 IP + 端口.
    供 web UI 显示/生成 QR 码.
    """
    import os
    url_file = os.environ.get(
        "TUNNEL_URL_FILE",
        os.path.join(os.path.dirname(__file__), "..", "tunnel_url.txt"),
    )
    url = ""
    try:
        if os.path.exists(url_file):
            with open(url_file, "r", encoding="utf-8") as f:
                url = f.read().strip()
    except Exception:
        pass

    method = ""
    try:
        method_file = os.path.join(os.path.dirname(__file__), "..", "tunnel_method.txt")
        if os.path.exists(method_file):
            with open(method_file, "r", encoding="utf-8") as f:
                method = f.read().strip()
    except Exception:
        pass

    # 局域网 IP (优先 en0 / Wi-Fi)
    lan_ip = ""
    try:
        import subprocess as _sp
        out = _sp.check_output(["ipconfig", "getifaddr", "en0"], text=True, timeout=2).strip()
        lan_ip = out
    except Exception:
        try:
            import socket as _sk
            s = _sk.socket(_sk.AF_INET, _sk.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            lan_ip = "127.0.0.1"

    # tunnel 进程在不在 (cloudflared / ngrok / ssh 三种都查)
    running = False
    port = int(os.environ.get("TUIXUE_PORT", "7799"))
    try:
        import subprocess as _sp2
        for pat in (f"cloudflared tunnel --url",
                    f"ngrok http {port}",
                    f"ssh -tt -R 80:localhost:{port}"):
            try:
                _sp2.check_output(["pgrep", "-f", pat], timeout=1)
                running = True
                break
            except Exception:
                pass
    except Exception:
        pass

    return envelope(data={
        "url":        url,
        "method":     method,
        "lan_ip":     lan_ip,
        "port":       port,
        "running":    running,
        "ts":         datetime.datetime.now().isoformat(timespec="seconds"),
    })


@app.post("/api/tunnel/start")
async def tunnel_start():
    """启动多路 fallback tunnel (后台). 阻塞轮询 url_file 拿到 URL 后立刻推 TG.

    2026-07-11: 改用 start_tunnel_only.sh — server-safe 6 路 fallback
    (cloudflare quic/http2/ipv4 → ngrok → localhost.run → serveo),
    不再 kill 现有 server。拿到 URL 后自动推 Telegram。
    """
    import os
    import subprocess as _sp

    script = os.path.join(os.path.dirname(__file__), "start_tunnel_only.sh")
    if not os.path.exists(script):
        return envelope(error="start_tunnel_only.sh 不存在")

    url_file = os.path.join(os.path.dirname(__file__), "..", "tunnel_url.txt")
    method_file = os.path.join(os.path.dirname(__file__), "..", "tunnel_method.txt")

    # 先清理旧 URL 文件,前端能立刻看到「启动中」状态
    for f in (url_file, method_file):
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    try:
        _sp.Popen(
            ["nohup", "setsid", "bash", script],
            stdin=_sp.DEVNULL,
            stdout=open("/tmp/tuixue_tunnel_start.log", "a"),
            stderr=_sp.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        return envelope(error=f"启动 tunnel 失败: {e}")

    # 阻塞轮询 url_file — 最多 65s, 每 1s 看一次
    deadline = time.time() + 65
    while time.time() < deadline:
        await asyncio.sleep(1)
        if os.path.exists(url_file):
            try:
                url = open(url_file, encoding="utf-8").read().strip()
            except Exception:
                url = ""
            if url:
                method = ""
                if os.path.exists(method_file):
                    try:
                        method = open(method_file, encoding="utf-8").read().strip()
                    except Exception:
                        pass

                # 自动推 TG
                tg_sent = False
                tg_err = ""
                try:
                    port = int(os.environ.get("TUIXUE_PORT", "7799"))
                    lan_ip = _sp.check_output(["ipconfig", "getifaddr", "en0"], text=True, timeout=2).strip()
                except Exception:
                    lan_ip = "127.0.0.1"
                    port = 7799
                lines = [
                    "📡 退学 v3 · 外网入口",
                    "",
                    f"🌐 公网 URL: {url}",
                    "",
                    f"🔧 隧道方法: {method or 'unknown'}",
                    f"🏠 局域网: http://{lan_ip}:{port}/",
                    f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "",
                    "iPhone 浏览器直接打开公网 URL。临时隧道约 24h 后失效。",
                ]
                try:
                    from ..lib_common import send_telegram
                    tg_sent = send_telegram("\n".join(lines), parse_mode="text", silent=True)
                    if not tg_sent:
                        tg_err = "send_telegram 返回 False (api.telegram.org 可能被 DNS 拦截)"
                except Exception as e:
                    tg_err = str(e)
                log.info(f"tunnel 启动成功 url={url} method={method} tg_sent={tg_sent}")
                return envelope(data={
                    "ok": True, "url": url, "method": method,
                    "tg_sent": tg_sent, "tg_err": tg_err,
                    "elapsed_sec": round(65 - (deadline - time.time()), 1),
                })

    return envelope(data={
        "ok": False,
        "error": "65s 内 6 路 tunnel 全部失败（网络层 DNS 劫持到 198.18.x + TLS 阻断是常见原因）。请检查 ~/.hermes/.env 网络配置或稍后重试。",
    })


@app.post("/api/tunnel/stop")
async def tunnel_stop():
    """停掉所有 tunnel 进程（不动 server）。三种机制都杀: cloudflared / ngrok / ssh-reverse."""
    import os
    import subprocess as _sp
    try:
        port = int(os.environ.get("TUIXUE_PORT", "7799"))
        for pat in (f"cloudflared tunnel --url",
                    f"ngrok http {port}",
                    f"ssh -tt -R 80:localhost:{port}"):
            _sp.Popen(["pkill", "-f", pat], stdin=_sp.DEVNULL,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        # 清 URL 文件
        for fname in ("tunnel_url.txt", "tunnel_method.txt", "tunnel_pid.txt"):
            p = os.path.join(os.path.dirname(__file__), "..", fname)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        return envelope(data={"ok": True})
    except Exception as e:
        return envelope(error=str(e))


@app.post("/api/tunnel/push")
async def tunnel_push():
    """把当前 tunnel 公网 URL 推到 Telegram（方便手机扫码后存书签，或分享给同事）。
    URL 从 tunnel_url.txt 读；附带 LAN IP + 端口作 fallback。
    即使 TG 推送失败（DNS/TLS 拦截常见）,也返回 URL 让前端 fallback 到剪贴板/原生分享。
    """
    import os
    url_file = os.environ.get(
        "TUNNEL_URL_FILE",
        os.path.join(os.path.dirname(__file__), "..", "tunnel_url.txt"),
    )
    url = ""
    try:
        if os.path.exists(url_file):
            with open(url_file, "r", encoding="utf-8") as f:
                url = f.read().strip()
    except Exception:
        pass

    lan_ip = "127.0.0.1"
    port = int(os.environ.get("TUIXUE_PORT", "7799"))
    try:
        import subprocess as _sp
        lan_ip = _sp.check_output(["ipconfig", "getifaddr", "en0"], text=True, timeout=2).strip()
    except Exception:
        pass

    lan_url = f"http://{lan_ip}:{port}/"
    target_url = url or lan_url

    lines = ["📡 退学 v3 · 外网入口", ""]
    if url:
        lines += [f"🌐 公网 URL：{url}", ""]
    else:
        lines += ["⚠ 公网 tunnel 未启动（点击「启动隧道」生成）", ""]
    lines.append(f"🏠 局域网：http://{lan_ip}:{port}/")
    text = "\n".join(lines)

    tg_ok = False
    tg_err = ""
    try:
        from ..lib_common import send_telegram
        tg_ok = send_telegram(text, parse_mode="text", silent=True)
    except Exception as e:
        tg_err = str(e)
    if not tg_ok and not tg_err:
        tg_err = "DNS/TLS 阻断 api.telegram.org（沙箱网络常见）"

    # 关键：TG 失败也照样返回 URL — 让前端 fallback 到复制/分享
    # 同时把当前 target URL 生成 QR (data URL) 一起返回,前端可一键下载/截图
    qr_data_url = ""
    try:
        import base64, io
        import qrcode  # pillow dep
        qr = qrcode.QRCode(version=None, box_size=8, border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(target_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0a0908", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        # qrcode 没装 / PIL 不可用 — 返回空字符串,前端用已有 #tunnel-qr-btn 兜底
        qr_data_url = ""

    return envelope(data={
        "tg_ok": tg_ok,
        "tg_err": tg_err,
        "url": url,
        "lan": lan_url,
        "target": target_url,   # 给前端"一键分享/复制"用
        "text": text,
        "qr_data_url": qr_data_url,  # 推到手机/iMessage/Slack 直接扫码
    })


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
    # 关键:raw code 000001 会被 fetch_realtime 误判为股票(平安银行),
    # 必须直接走 _index_realtime_qq(指数专用)
    @cached(_cache_overview, key_fn=lambda c: ("idx", c))
    def _load(c: str):
        from .. import lib_common as lc
        # 1) 指数专用源:qq → em
        if hasattr(lc, "_index_realtime_qq"):
            rt = lc._index_realtime_qq(c) or {}
            if rt:
                rt["_source"] = "tencent_qq_index"
                return rt
        if hasattr(lc, "_index_realtime_em"):
            rt = lc._index_realtime_em(c) or {}
            if rt:
                rt["_source"] = "em_push2delay_idx"
                return rt
        # 2) 兜底走 fetch_realtime (盘后 qt 也可能空)
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
    """6 大指数并行拉取 + 涨停数估算(东财限频时降级到部分数据)
    2026-07-11: indices 与 zt_count 并行跑 (互不依赖), 总闸 8s。
    """
    async def _zt_count():
        from .. import multi_source_fetchers as msf
        today = datetime.datetime.now().strftime("%Y%m%d")
        try:
            return await asyncio.wait_for(to_thread(msf.fetch_zt_pool, today), timeout=6)
        except Exception:
            return None

    async def _indices():
        return await asyncio.gather(*[_fetch_index(c, n) for c, n in INDICES])

    try:
        indices_raw, zt = await asyncio.wait_for(
            asyncio.gather(_indices(), _zt_count(), return_exceptions=True),
            timeout=8,
        )
    except asyncio.TimeoutError:
        indices_raw, zt = (
            [{"code": c, "name": n, "price": 0, "change_pct": 0, "amount": 0} for c, n in INDICES],
            None,
        )

    if isinstance(indices_raw, Exception) or not isinstance(indices_raw, list):
        indices = [{"code": c, "name": n, "price": 0, "change_pct": 0, "amount": 0} for c, n in INDICES]
    else:
        indices = indices_raw
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
# 首页 dashboard 信号面板 — A股 / 韩股 / 美股 是否适合买 + 热门板块
# ───────────────────────────────────────────────────────────
_dashboard_cache: dict[str, Any] = {"ts": 0.0, "signal": None, "hot": None}
_DASHBOARD_TTL = 30.0


def _verdict_from_pct(avg_pct: float, allow: float = 0.3, block: float = -0.3) -> str:
    """根据涨跌幅判定适合度。allow/block 是阈值百分比(不是小数)。"""
    if avg_pct >= allow:
        return "allow"
    if avg_pct <= block:
        return "block"
    return "cautious"


def _fetch_index_sync(code: str, name: str) -> dict:
    """同步版 — 给 dashboard 用 (避免走异步路径拉慢)
    优先缓存,缓存空时顺序试 qq → em 同步接口。
    """
    cached = _cache_overview.get(("idx", code))
    if cached and (time.monotonic() - cached.get("_ts", 0) < 14):
        return {"code": code, "name": name,
                "price": cached.get("price", 0),
                "change_pct": cached.get("change_pct", 0)}
    from .. import lib_common as lc
    rt: dict = {}
    if hasattr(lc, "_index_realtime_qq"):
        try:
            rt = lc._index_realtime_qq(code) or {}
            if rt:
                rt["_source"] = "tencent_qq_index"
        except Exception:
            rt = {}
    if not rt and hasattr(lc, "_index_realtime_em"):
        try:
            rt = lc._index_realtime_em(code) or {}
            if rt:
                rt["_source"] = "em_push2delay_idx"
        except Exception:
            rt = {}
    return {
        "code": code,
        "name": name,
        "price": _safe_float(rt.get("最新价") or rt.get("price")),
        "change_pct": _safe_float(rt.get("涨跌幅") or rt.get("change_pct")),
    }


def _build_dashboard_signal() -> dict:
    """
    合并 /api/market/overview (A股 6 指数 + 涨停数) + /api/global/sentiment (美/韩 + sector_impact)
    输出三市场 verdict: allow / cautious / block
    A 股指数用 ThreadPoolExecutor 6 路并行 (避免 6×2s = 12s 串行延迟)
    """
    from . import global_markets as _gm
    from .. import lib_common as lc
    from concurrent.futures import ThreadPoolExecutor as _TPE

    # 1) A股 — 6 指数并行
    a_indices: list[dict] = []
    a_pcts: list[float] = []
    try:
        with _TPE(max_workers=6) as _pool:
            results = list(_pool.map(lambda cn: _fetch_index_sync(cn[0], cn[1]), INDICES))
        for r in results:
            if r and r.get("change_pct") is not None:
                a_indices.append(r)
                a_pcts.append(r["change_pct"])
    except Exception as e:
        log.warning(f"dashboard A股 indices 失败: {e}")

    a_avg = round(sum(a_pcts) / len(a_pcts), 2) if a_pcts else 0.0
    a_verdict = _verdict_from_pct(a_avg)
    a_headline = f"6 指数均值 {a_avg:+.2f}%"
    a_warnings: list[str] = []

    # 2) 全球情绪 (60s 缓存)
    gm_data: dict = {}
    try:
        gm_data = _gm.fetch_global_sentiment() or {}
    except Exception as e:
        log.warning(f"dashboard global_markets 失败: {e}")

    # 3) 美股
    us_sent = (gm_data.get("sentiment") or "neutral").lower()
    us_idx_pct = float(gm_data.get("sentiment_score") or 0)
    if us_sent == "risk_on":
        us_verdict = "allow"
    elif us_sent == "risk_off":
        us_verdict = "block"
    else:
        us_verdict = "cautious"
    us_indices = gm_data.get("indices") or []
    if us_indices:
        try:
            us_idx_pct = sum(_safe_float(i.get("change_pct")) for i in us_indices) / len(us_indices)
        except Exception:
            pass
    us_headline = f"风险偏好 {us_sent} · 综合 {us_idx_pct:+.2f}%"

    # 4) 韩股 — KOSPI (KS11) — 走 global_markets._fetch_one 多源兜底链 (yahoo → eastmoney)
    #    旧版 _index_realtime_em/qq 不支持 KS11 (secid 前缀只有 sh/sz), 永远 0.00%
    kr_pct = 0.0
    kr_verdict = "cautious"
    kr_source = ""
    try:
        from . import global_markets as gm
        if hasattr(gm, "_fetch_one"):
            kr_data = gm._fetch_one("KS11", "kr") or {}
            if kr_data:
                kr_pct = _safe_float(kr_data.get("change_pct") or kr_data.get("涨跌幅"))
                kr_verdict = _verdict_from_pct(kr_pct, allow=0.5, block=-0.5)
                kr_source = kr_data.get("source", "")
    except Exception as e:
        log.warning(f"dashboard KOSPI 拉取失败: {e}")
    kr_headline = f"KOSPI {kr_pct:+.2f}%" + (f" · {kr_source}" if kr_source else "")

    # 5) 不利新闻 — sector_impact 板块跌 ≥ 3% 且驱动数 ≥ 2 → A股警告;n ≥ 3 → 也提醒美股
    sec_impact = gm_data.get("sector_impact") or {}
    us_warnings: list[str] = []
    for sw, info in sec_impact.items():
        try:
            pct = float(info.get("change_pct") or 0)
            n = int(info.get("n_drivers") or 0)
        except Exception:
            continue
        if pct <= -3 and n >= 2:
            a_warnings.append(f"⚠ {sw} {pct:+.1f}% · {n} 标的拖累")
            if n >= 3:
                us_warnings.append(f"⚠ {sw} {pct:+.1f}% 关联拖累")

    return {
        "a_share": {
            "verdict": a_verdict,
            "change_pct": a_avg,
            "headline": a_headline,
            "indices": a_indices,
            "warnings": a_warnings,
        },
        "kr": {
            "verdict": kr_verdict,
            "change_pct": kr_pct,
            "headline": kr_headline,
            "warnings": [],
        },
        "us": {
            "verdict": us_verdict,
            "change_pct": us_idx_pct,
            "sentiment": us_sent,
            "headline": us_headline,
            "warnings": us_warnings,
        },
        "ts": time.time(),
    }


@app.get("/api/dashboard/signal")
async def api_dashboard_signal(force: bool = False):
    """首页三市场信号面板 — A/KR/US verdict + 关键指数 + 不利新闻。
    30s 内存缓存; force=true 强制重算 (数据源冷启动或调试)。
    """
    now = time.time()
    if not force and _dashboard_cache["signal"] is not None and (now - _dashboard_cache["ts"]) < _DASHBOARD_TTL:
        return envelope(data=_dashboard_cache["signal"])

    try:
        sig = await asyncio.wait_for(to_thread(_build_dashboard_signal), timeout=25)
    except asyncio.TimeoutError:
        log.warning("dashboard signal 超时 25s")
        # 兜底:返上一次缓存(可能 None)
        return envelope(error="信号计算超时", data=_dashboard_cache["signal"] or {
            "a_share": {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "kr":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "us":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
        })
    except Exception as e:
        log.warning(f"dashboard signal 异常: {e}")
        return envelope(error=str(e), data=_dashboard_cache["signal"] or {
            "a_share": {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "kr":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "us":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
        })

    _dashboard_cache["signal"] = sig
    _dashboard_cache["ts"] = now
    return envelope(data=sig)


@app.get("/api/dashboard/hot_sectors")
async def api_dashboard_hot_sectors(force: bool = False):
    """今日热门板块 — 简化版:fetch_hot_sectors 拿 18 个板块(涨跌+资金净流入),
    按 (涨停数 × 2 + 涨幅% × 10) 综合排序,取 Top 5。
    避开了 score_dragons 中不稳定的小 Racer/V8 调用(2026-07-11 libmini_racer 段错误)。
    """
    now = time.time()
    if not force and _dashboard_cache["hot"] is not None and (now - _dashboard_cache["ts"]) < _DASHBOARD_TTL:
        return envelope(data=_dashboard_cache["hot"])

    def _load():
        from .. import multi_source_fetchers as msf
        from . import sector_classify as _sc
        from .sector_taxonomy import MAINLINE_ZT_THRESHOLD
        from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _FutTimeout

        # 1) 板块涨跌 + 资金净流入 (THS 接口) 并行 涨停池 (em/ths)
        def _sectors():
            try:
                return msf.fetch_hot_sectors(top_n_flow=20, top_n_pct=20) or []
            except Exception as e:
                log.warning(f"hot_sectors fetch_hot_sectors 失败: {e}")
                return []

        def _zt():
            try:
                return msf.fetch_zt_pool(datetime.datetime.now().strftime("%Y%m%d")) or []
            except Exception as e:
                log.warning(f"hot_sectors zt_pool 失败: {e}")
                return []

        sectors: list = []
        zt_pool: list = []
        try:
            with _TPE(max_workers=2) as _pool:
                f_s = _pool.submit(_sectors)
                f_z = _pool.submit(_zt)
                # 各给 10s 硬超时 — 单源挂掉不影响整体
                # 内部有 fallback (东财→THS), 5s+5s 串行可能要 8-10s
                try: sectors = f_s.result(timeout=10)
                except _FutTimeout:
                    log.warning("hot_sectors: sectors 10s 超时")
                try: zt_pool = f_z.result(timeout=10)
                except _FutTimeout:
                    log.warning("hot_sectors: zt_pool 10s 超时")
        except Exception as e:
            log.warning(f"hot_sectors 并行拉取异常: {e}")

        if not sectors:
            return {"mainline": [], "sentiment": {"label": "—", "zt_count": 0}}

        # 2) 涨停池 → 板块涨停计数
        zt_count_by_sector: dict[str, int] = {}
        for z in zt_pool:
            sw = (z.get("sector") or "").strip()
            if sw:
                zt_count_by_sector[sw] = zt_count_by_sector.get(sw, 0) + 1

        # 3) 综合排序: 涨停数 × 2 + 涨幅% × 10
        def _score(s: dict) -> float:
            zt = zt_count_by_sector.get(s.get("name") or "", 0)
            return zt * 2 + (s.get("涨跌幅") or s.get("change_pct") or 0)

        sectors.sort(key=_score, reverse=True)
        top5 = sectors[:5]
        total_zt = sum(zt_count_by_sector.values())

        tiles = []
        for i, s in enumerate(top5, start=1):
            tiles.append({
                "name": s.get("name") or "",
                "change_pct": float(s.get("涨跌幅") or s.get("change_pct") or 0),
                "net_inflow_yi": float(s.get("净流入") or s.get("fund_flow_yi") or 0),
                "rank_flow": i,
                "zt_count": zt_count_by_sector.get(s.get("name") or "", 0),
            })

        # 情绪档
        zt_n = total_zt
        if zt_n >= 60:    label, max_streak = "高潮", 5
        elif zt_n >= 30:  label, max_streak = "活跃", 4
        elif zt_n >= 15:  label, max_streak = "震荡", 3
        else:             label, max_streak = "低迷", 2
        return {
            "mainline": tiles,
            "sentiment": {"label": label, "zt_count": zt_n, "max_streak": max_streak,
                          "threshold": MAINLINE_ZT_THRESHOLD},
        }

    try:
        out = await asyncio.wait_for(to_thread(_load), timeout=20)
    except asyncio.TimeoutError:
        log.warning("dashboard hot_sectors 超时 20s")
        return envelope(error="热门板块超时", data={"mainline": [], "sentiment": {}})
    except Exception as e:
        log.warning(f"dashboard hot_sectors 失败: {e}")
        return envelope(error=str(e), data={"mainline": [], "sentiment": {}})

    out["ts"] = time.time()
    _dashboard_cache["hot"] = out
    _dashboard_cache["ts"] = now
    return envelope(data=out)


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

    try:
        kline = await asyncio.wait_for(to_thread(_load, code, days), timeout=15)
    except asyncio.TimeoutError:
        log.warning(f"stock_kline {code} 15s 超时,降级")
        return envelope(data={"code": code, "kline": [], "_degraded": "upstream_timeout"})
    return envelope(data={"code": code, "kline": kline or []})


@app.get("/api/stock/{code}/fund_flow")
async def stock_fund(code: str, days: int = Query(60, ge=10, le=180),
                     fresh: int = Query(0, ge=0, le=1)):
    code = code.strip().zfill(6)
    _touch_recent(code)
    if fresh:
        _cache_fund.invalidate(("fund_flow", code, days))
    @cached(_cache_fund, key_fn=lambda c, d: ("fund_flow", c, d))
    def _load(code_, days_):
        return fund_flow.get_combined(code_, days=days_)
    try:
        flow = await asyncio.wait_for(to_thread(_load, code, days), timeout=12)
    except asyncio.TimeoutError:
        log.warning(f"stock_fund {code} 12s 超时,降级")
        return envelope(data={"code": code, "today": None, "history": [], "_degraded": "upstream_timeout"})
    return envelope(data=flow or {"code": code, "today": None, "history": []})


@app.get("/api/stock/{code}/seats")
async def stock_seats(code: str, days: int = Query(30, ge=5, le=90)):
    code = code.strip().zfill(6)
    seats = await to_thread(seat_lookup.get_stock_seats, code, days)
    return envelope(data=seats or {"code": code, "rows": [], "blacklisted": False,
                                    "seat_count": 0, "total_lhb_rows": 0,
                                    "known_groups": []})


@app.get("/api/stock/{code}/seat_breakdown")
async def stock_seat_breakdown(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    6 类席位分类 + 资金占比 + 风险标记 + 短线筛选标签.
    复用 seat_lookup.get_stock_seats + fund_flow.get_main_flow.
    ?fresh=1 — 不读缓存,重新跑 6 类分类 (2026-07-11 进页面强制刷新用)
    """
    from . import seat_classify
    code = code.strip().zfill(6)
    _touch_recent(code)
    _empty = {"code": code, "rows": [], "all_rows_count": 0, "last_date": None,
              "categories": [], "total_amount_wan": None,
              "intraday": {}, "risks": [], "tags": []}
    try:
        breakdown = await asyncio.wait_for(
            to_thread(seat_classify.build_breakdown, code), timeout=18)
    except asyncio.TimeoutError:
        log.warning(f"seat_breakdown {code} 18s 超时(akshare 冷启/限频),降级空表")
        return envelope(data={**_empty, "_degraded": "upstream_timeout"})
    except Exception as e:
        log.warning(f"seat_breakdown {code} 异常: {e}")
        return envelope(data={**_empty, "_degraded": "upstream_error"})
    return envelope(data=breakdown or _empty)


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
        result = await asyncio.wait_for(to_thread(_load), timeout=18)
    except asyncio.TimeoutError:
        return envelope(error="intraday_5d 超时 18s", data={"code": code, "daily_5d": [], "intraday_today": None})
    if result is None:
        return envelope(error="intraday_5d 上游异常（详见日志）", data={"code": code, "daily_5d": [], "intraday_today": None})
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
        cache = news_lookup.load_cache()
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
        cache = news_lookup.load_cache()
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
        cache = news_lookup.load_cache()
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


@app.get("/api/sectors/taxonomy")
async def sectors_taxonomy():
    """4 层板块分类 (静态) — 前端渲染顶部 6 集群 chip + 主线标记

    返回结构:
      {
        "clusters": [
          {"name": "大科技", "color": "#5b8def", "icon": "🧠", "desc": "...",
           "sw_set": ["电子","计算机",...],
           "industries": {
             "电子": {"chains": ["半导体芯片","存储",...]},
             ...
           }}, ... 6 个
        ],
        "threshold": 15,    // 主线涨停阈值
        "version": "2026-07-11"
      }
    """
    from .sector_taxonomy import (
        taxonomy_tree, CLUSTER_ORDER, CLUSTERS, MAINLINE_ZT_THRESHOLD, ALL_CHAINS
    )
    tree = taxonomy_tree()
    clusters = []
    for cname in CLUSTER_ORDER:
        if cname == "其他":
            continue
        cinfo = tree.get(cname) or {}
        clusters.append({
            "name":       cname,
            "color":      cinfo.get("color", "#888"),
            "icon":       cinfo.get("icon", ""),
            "desc":       cinfo.get("desc", ""),
            "sw_set":     cinfo.get("sw_set", []),
            "industries": cinfo.get("industries", {}),
        })
    return envelope(data={
        "clusters":   clusters,
        "threshold":  MAINLINE_ZT_THRESHOLD,
        "all_chains": sorted(ALL_CHAINS.keys()),
        "version":    "2026-07-11",
    })


@app.get("/api/sectors/mainlines")
async def sectors_mainlines():
    """当日主线 — 同一 L3 产业链涨停 ≥ MAINLINE_ZT_THRESHOLD (默认 15)

    返回:
      {
        "mainlines": [
          {"chain": "人形机器人", "sw": "机械设备", "cluster": "大科技",
           "zt_count": 22, "is_mainline": true, "rank": 1, "desc": "..."}, ...
        ],
        "chain_counts": {"人形机器人": 22, ...},  // 全 chain 计数(用于调试)
        "threshold": 15,
        "ts": <epoch>
      }
    """
    import time as _t
    from .sector_taxonomy import (
        detect_mainline, count_zt_by_chain, MAINLINE_ZT_THRESHOLD
    )
    from .sector_classify import get_sector
    from .. import data_layer as _dl

    def _calc():
        try:
            zt = _dl.fetch_limit_up_pool() or []
        except Exception:
            zt = []
        codes = [str(z.get("code") or "").zfill(6) for z in zt]
        ml = detect_mainline(zt_codes=codes, sector_lookup=get_sector, threshold=MAINLINE_ZT_THRESHOLD)
        chain_counts = count_zt_by_chain(codes, get_sector)
        return ml, chain_counts

    try:
        ml, chain_counts = await asyncio.wait_for(to_thread(_calc), timeout=8)
    except asyncio.TimeoutError:
        ml, chain_counts = [], {}
    return envelope(data={
        "mainlines":    ml,
        "chain_counts": chain_counts,
        "threshold":    MAINLINE_ZT_THRESHOLD,
        "ts":           _t.time(),
    })


@app.get("/api/stock/{code}")
async def stock_overview(code: str, fresh: int = Query(0, ge=0, le=1)):
    """个股综合数据:4 个上游并行 + 单源失败不阻塞其他。
    ?fresh=1 — 失效该 code 的 quote / fund_flow 缓存,进页面必拿最新 (2026-07-11)
    """
    code = code.strip().zfill(6)
    _touch_recent(code)
    if fresh:
        _cache_quote.invalidate(("quote", code))
        _cache_fund.invalidate(("fund_flow", code, 60))

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
        _with_timeout(quote_t, 12),   # 实时行情 — 东财冷启动可达 6-8s,周末/晚间限频 10-12s 常见
        _with_timeout(flow_t, 6),
        _with_timeout(seats_t, 4),
        _with_timeout(kline_t, 6),
        _with_timeout(holders_t, 8),
    ), timeout=20)
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
            limit_pct = 0.20 if code.startswith(("300", "301", "688")) else 0.10
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

【4 层板块定位 (2026-07-11) — 必读】
每只股票会拿到 4 层 + role 字段:
  cluster (6 大类): 大科技/高端制造/消费/医药/金融/周期资源
  chain (产业链): 主线识别最小单位,例如「人形机器人」「高速光互联」「HBM 存储」
  sub (细分多标签): 例如「谐波减速器」「800G 光模块」
  role (个股标签): main(主战场龙头)/second(二线弹性)/noise(杂毛跟风)
主线判定:同 chain 当日涨停 ≥ 15 家 → 主线,该股可重点关注;否则仅作参考。
杂股规则 (硬约束):
  - taxonomy.role = noise → verdict 强制 ≤ "观望",conviction ≤ 50
  - taxonomy.role = noise 且 ai_tag.is_main_field = false → verdict = "回避"
  - taxonomy.role = main 且 所在 chain 是当日主线 → 可加 "龙头" role, conviction 上限解锁至 90

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
    tax = sector.get("taxonomy") or {}  # 2026-07-11 — 4 层分类
    # 主线判定 — 该股所在 L3 chain 当日涨停 ≥15 家?
    is_mainline_brief = ""
    try:
        l3 = (tax.get("level3_chain") or "").strip()
        if l3:
            from .. import data_layer as _dl
            from .sector_classify import get_sector as _gs
            from .sector_taxonomy import count_zt_by_chain
            _zt = _dl.fetch_limit_up_pool() or []
            _codes = [str(z.get("code") or "").zfill(6) for z in _zt]
            _cnt = count_zt_by_chain(_codes, _gs).get(l3, 0)
            if _cnt >= 15:
                is_mainline_brief = f" · 主线({_cnt}家涨停)"
            elif l3:
                is_mainline_brief = f" · 非主线({_cnt}家)"
    except Exception:
        pass

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
        'cluster': tax.get('level1_cluster'),
        'chain': tax.get('level3_chain'),
        'sub': tax.get('level4_subconcept'),
        'role': tax.get('role'),
        'noise_reason': tax.get('noise_reason', ''),
    }, ensure_ascii=False)[:300]}{is_mainline_brief}。按 JSON 格式返回。"""

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
        # MiniMax-M3 reasoning_content 极长 → 首次 700 容易被截断,失败用 1500
        "max_tokens": 1200,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # M3 reasoning 慢: 单次 read timeout 必须 ≥ 25s, 否则 reasoning 还没完就 ReadTimeout
    # 2 次调用: 第一次 1200 tokens / 25s, 第二次 1500 / 30s 兜底
    last_err = None
    text = ""
    for attempt, max_t, t_out in [(1, 1200, 25), (2, 1500, 30)]:
        body_local = dict(body)
        body_local["max_tokens"] = max_t
        try:
            r = _requests.post(url, json=body_local, headers=headers, timeout=t_out)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            j = r.json()
            text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
            finish = j.get("choices", [{}])[0].get("finish_reason", "?")
            if text:
                return _parse_ai_json(text)
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
        # 即使命中缓存, 也同步写一份 watchlist_ai (首次访问 + 已在 watchlist 中)
        try:
            if _watchlist.get(code) and not _watchlist.get_ai(code):
                extras = await asyncio.to_thread(_watchlist.enrich_extras, code)
                wl_payload = dict(hit)
                wl_payload["extras"] = extras
                wl_payload.setdefault("suggested_window", "暂观望")
                wl_payload.setdefault("entry_price_range", "")
                wl_payload.setdefault("stop_loss", "")
                wl_payload.setdefault("time_horizon", "")
                _watchlist.upsert_ai(code, wl_payload, today_str)
        except Exception as e:
            log.debug(f"watchlist_ai cache-sync fail: {e}")
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
        # ⚠ 不要用 to_thread: 它吞异常返 None,这里会误判为"AI 返回空"
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, functools.partial(_call_minimax, api_key, code, ctx)),
            timeout=35,
        )
    except asyncio.TimeoutError:
        log.warning(f"AI 调用超时 35s (code={code})")
        return envelope(error="AI 调用超时", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": "AI 调用超时(35s)- 数据源或模型连接慢,建议稍后重试",
        })
    except Exception as e:
        log.warning(f"AI 调用失败: {e}")
        return envelope(error=f"AI 调用失败: {e}", data={
            "verdict": "-", "role": "中军", "conviction": 0,
            "layer_pass": {"L1_风控": None, "L2_周期主线": None, "L3_形态": None, "L4_分时": None},
            "rules_passed": [], "rules_failed": [], "key_risks": [],
            "summary": f"AI 调用失败: {type(e).__name__}: {str(e)[:120]}",
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

    # 3) 若该股票在 watchlist 中 → 同步写 watchlist_ai (供选股页表格秒返)
    #    异步后台跑,不阻塞响应 (enrich_extras 会再打 5/10 日涨跌 + 资金占比)
    async def _sync_watchlist():
        try:
            if _watchlist.get(code):
                extras = await asyncio.to_thread(_watchlist.enrich_extras, code)
                wl_payload = dict(result)
                wl_payload["extras"] = extras
                wl_payload.setdefault("suggested_window", "暂观望")
                wl_payload.setdefault("entry_price_range", "")
                wl_payload.setdefault("stop_loss", "")
                wl_payload.setdefault("time_horizon", "")
                _watchlist.upsert_ai(code, wl_payload, today_str)
        except Exception as e:
            log.debug(f"watchlist_ai sync fail: {e}")
    asyncio.create_task(_sync_watchlist())

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

# ───────────────────────────────────────────────────────────
# 自选股池 + AI 建议 (2026-07-11)
# ───────────────────────────────────────────────────────────
from . import watchlist as _watchlist

@app.get("/api/review/trades")
async def api_review_list_trades(limit: int = 50, code: str | None = None, since_days: int | None = 90):
    """最近 N 笔交易(含最新一次 AI 复盘摘要 + 逐笔实时盈亏)。返回数组。"""
    try:
        trades = await to_thread(_review.live_trades, limit=limit, code=code, since_days=since_days)
        return envelope(data=trades or [])
    except Exception as e:
        log.exception("list_trades")
        return envelope(error=str(e), status_code=500)


@app.get("/api/review/portfolio")
async def api_review_portfolio(total_capital: float | None = None):
    """顶部资金栏:总资金/仓位/今日盈亏/总盈亏/盈亏比 + 持仓明细(实时价)。"""
    try:
        data = await to_thread(_review.portfolio_overview, total_capital)
        return envelope(data=data or {})
    except Exception as e:
        log.exception("portfolio")
        return envelope(error=str(e), status_code=500)


@app.get("/api/review/settings")
async def api_review_get_settings():
    """读取复盘设置(总资金)。"""
    try:
        cap = _review.get_setting("total_capital", 0)
        return envelope(data={"total_capital": float(cap or 0)})
    except Exception as e:
        return envelope(error=str(e), status_code=500)


@app.post("/api/review/settings")
async def api_review_set_settings(payload: dict):
    """保存复盘设置。payload: {total_capital}"""
    try:
        if "total_capital" in payload:
            _review.set_setting("total_capital", float(payload.get("total_capital") or 0))
        cap = _review.get_setting("total_capital", 0)
        return envelope(data={"total_capital": float(cap or 0)})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.get("/api/review/time_points")
async def api_review_time_points(code: str, date: str | None = None, price: float | None = None):
    """按买入价从分时反推可能的成交时刻(多时刻→前端下拉选)。"""
    try:
        data = await to_thread(_review.infer_time_points, code, date, price)
        return envelope(data=data or {"available": False, "points": []})
    except Exception as e:
        log.exception("time_points")
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
        # 复盘涉及 subprocess(75s 超时)+ 多源拉取,放线程池不阻塞 event loop
        result = await to_thread(_review.review_trade, trade_id, force=force)
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


# ═══════════════════════════════════════════════════════════════
# 自选股池 + AI 建议 (2026-07-11)
# ═══════════════════════════════════════════════════════════════
class WatchlistAddRequest(BaseModel):
    code: str
    name: str | None = None
    tag: str | None = "自选"
    note: str | None = ""


class WatchlistUpdateRequest(BaseModel):
    tag: str | None = None
    note: str | None = None
    sort_order: int | None = None


@app.get("/api/watchlist")
async def api_watchlist_list():
    """列出全部自选股 + 实时行情 + 最新 AI 建议(同日有效)。"""
    try:
        items = await asyncio.to_thread(_watchlist.list_with_ai_snapshot)
        return envelope(data={"items": items, "count": len(items)})
    except Exception as e:
        log.exception("watchlist list")
        return envelope(error=str(e), status_code=500)


@app.post("/api/watchlist")
async def api_watchlist_add(req: WatchlistAddRequest):
    """添加股票到自选股池。"""
    try:
        row = _watchlist.add(req.code, name=req.name or "", tag=req.tag or "自选", note=req.note or "")
        return envelope(data={"item": row})
    except Exception as e:
        log.exception("watchlist add")
        return envelope(error=str(e), status_code=400)


@app.delete("/api/watchlist/{code}")
async def api_watchlist_remove(code: str):
    try:
        ok = _watchlist.remove(code)
        return envelope(data={"removed": ok})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.patch("/api/watchlist/{code}")
async def api_watchlist_update(code: str, req: WatchlistUpdateRequest):
    try:
        ok = _watchlist.update(code, tag=req.tag, note=req.note, sort_order=req.sort_order)
        return envelope(data={"updated": ok})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.get("/api/watchlist/{code}/ai")
async def api_watchlist_get_ai(code: str):
    """单只股票最新 AI 建议。"""
    try:
        ai = _watchlist.get_ai(code)
        return envelope(data={"ai": ai, "code": code})
    except Exception as e:
        return envelope(error=str(e), status_code=500)


@app.post("/api/watchlist/{code}/ai")
async def api_watchlist_analyze(code: str, force: bool = False):
    """触发单只股票 AI 分析(走 /api/stock/{code}/ai_analysis 路径),写入 watchlist_ai。

    流程:
      1) 调 _call_minimax_with_watchlist — 增强 system prompt (新增 时间窗口/入场价/止损/时间维度)
      2) enrich_extras — 附加 5/10 日涨跌 + 主力散户占比 + 板块涨停数
      3) upsert_ai — 写入 watchlist_ai 表
    """
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return envelope(error="MINIMAX_API_KEY 未配置", data={"code": code, "ai": None})
    try:
        # 先 enrich extras (并行 5/10日涨跌 + 资金 + 板块 + quote)
        extras = await asyncio.to_thread(_watchlist.enrich_extras, code)

        # 复用现有 stock_ai_analysis 的数据采集 (quote/flow/seats/kline/limit_up/sector)
        from . import fund_flow as _ff, seat_lookup as _sl
        from . import limit_up_context as _luc
        from .sector_classify import get_sector as _get_sector

        def _q(): return _call_minimax_safe(code)  # 用现有 stock_overview / ai_analysis 路径更安全
        # 直接调用 stock_ai_analysis 的内部组装, 但走 ai_envelope
        # 简化: 调 server 内部 _call_minimax(走原 prompt), 然后追加字段由前端 prompt-injected
        # 这里我们自定义一段 prompt: 带 extras 进去,要求返回增强 JSON
        result = await _analyze_for_watchlist(code, extras, api_key)
        if not result:
            return envelope(error="AI 返回空", data={"code": code, "ai": None})
        # 写入 watchlist_ai
        trade_date = datetime.datetime.now().strftime("%Y%m%d")
        await asyncio.to_thread(_watchlist.upsert_ai, code, result, trade_date)
        # 同时写回 ai_verdict (兼容旧 _stock/{code}/ai_analysis 缓存)
        try:
            from .. import cache_db as _cdb
            sector_name = (extras or {}).get("sector_name") or ""
            _cdb.upsert_ai(trade_date, code, "MiniMax-M3", result, sector=sector_name)
        except Exception:
            pass
        return envelope(data={"ai": result, "extras": extras, "code": code})
    except Exception as e:
        log.exception("watchlist analyze")
        return envelope(error=str(e), status_code=500)


async def _analyze_for_watchlist(code: str, extras: dict, api_key: str) -> dict:
    """自选股专用 AI 分析: 走增强 system prompt,要求返回 {verdict, role, conviction,
    suggested_window, entry_price_range, stop_loss, time_horizon, summary,
    rules_passed, rules_failed, key_risks}。"""
    import json as _json
    from .. import lib_common as lc
    from . import fund_flow as _ff, seat_lookup as _sl
    from . import limit_up_context as _luc
    from .sector_classify import get_sector as _get_sector

    # 1) 拉多源数据(与 stock_ai_analysis 类似,但 6 路并发)
    def _quote():    return lc.fetch_realtime(code)
    def _flow():     return _ff.get_combined(code, 60)
    def _seats():    return _sl.get_stock_seats(code, 10)
    def _kline():    return stock_kline_loader(code, 60)
    def _limitup():  return _luc.get_limit_up_context(code, sector_name=None)
    def _sector():   return _get_sector(code)

    async def _wt(fn, sec):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=sec)
        except Exception:
            return None

    quote, flow, seats, kline, limit_up, sector = await asyncio.wait_for(
        asyncio.gather(
            _wt(_quote, 6),
            _wt(_flow, 6),
            _wt(_seats, 4),
            _wt(_kline, 6),
            _wt(_limitup, 6),
            _wt(_sector, 5),
        ),
        timeout=14,
    )

    def _ok(v, d):
        return d if isinstance(v, BaseException) or v is None else v
    quote   = _ok(quote, {})
    flow    = _ok(flow, {"code": code, "today": None, "history": []})
    seats   = _ok(seats, {"code": code, "rows": [], "blacklisted": False})
    kline   = _ok(kline, [])
    limit_up = _ok(limit_up, {"code": code, "today": None, "recent_5d": [], "sector_today": []})
    sector  = _ok(sector, {"code": code, "sw": None, "ai_tags": {"labels": [], "is_main_field": False}})

    # 2) K线 → 5/10 日涨跌
    last5 = (kline or [])[-5:] if kline else []
    last10 = (kline or [])[-10:] if kline else []
    def _close(arr): return [float(r.get("close") or 0) for r in arr if r.get("close") is not None]

    closes5 = _close(last5)
    closes10 = _close(last10)
    pct_5d = round((closes5[-1] / closes5[0] - 1) * 100, 2) if len(closes5) >= 2 and closes5[0] else None
    pct_10d = round((closes10[-1] / closes10[0] - 1) * 100, 2) if len(closes10) >= 2 and closes10[0] else None

    # 3) 资金占比
    today_ff = (flow or {}).get("today") or {}
    main_a = abs(float(today_ff.get("main_net") or 0))
    big_a = abs(float(today_ff.get("big_net") or 0))
    mid_a = abs(float(today_ff.get("mid_net") or 0))
    sml_a = abs(float(today_ff.get("sml_net") or 0))
    total = main_a + big_a + mid_a + sml_a
    main_pct = round(main_a / total * 100, 1) if total else None
    retail_pct = round((mid_a + sml_a) / total * 100, 1) if total else None

    # 4) 板块联动
    sec_today = (limit_up or {}).get("sector_today") or []
    recent_5d_lu = (limit_up or {}).get("recent_5d") or []
    today_lu = (limit_up or {}).get("today") or {}
    streak = today_lu.get("连板数") or 0
    sector_zt_count = len(sec_today)
    sector_consecutive = sum(1 for x in sec_today if (x.get("连板数") or 0) >= 2)
    ai_tags = (sector or {}).get("ai_tags") or {}

    # 5) 组装 user content(显式列出 5/10 日涨跌 + 占比 + 板块联动,要求 AI 严格结合)
    user_content = f"""分析 {code} · 自选股深度判定。

【行情快照】
- 最新价: {quote.get("最新价")} · 涨跌%:{quote.get("涨跌幅")} · 换手:{quote.get("换手率")}% · 量比:{quote.get("量比")}
- 总市值:{quote.get("总市值")}亿 · 流通:{quote.get("流通市值")}亿

【涨跌维度 — 必读】
- 5 日涨跌: {pct_5d}%
- 10 日涨跌: {pct_10d}%
- 5 日均量: {(extras or {}).get("vol_5d_avg")} 手

【资金占比 — 必读】
- 主力净占比: {main_pct}% · 主力净额:{today_ff.get("main_net")}万
- 大单占比: {round(big_a/total*100,1) if total else None}% · 中单+散户占比: {retail_pct}%

【板块联动 — 必读】
- 行业: {sector.get("sw")} · AI 标签: {ai_tags.get("labels")}
- 是否主战场: {ai_tags.get("is_main_field")}
- 今日板块涨停: {sector_zt_count}只 · 其中连板 ≥2: {sector_consecutive}只
- 该股今日连板: {streak} · 近 5 日涨停次数: {len(recent_5d_lu)}

【席位风险】
- 龙虎席位: {len((seats or {}).get("rows") or [])} 条 · 黑名单:{seats.get("blacklisted")}
- 已知游资组: {(seats or {}).get("known_groups")}

【退学铁律 — 必读】
{_WATCHLIST_LAWS_INJECT}

【关键铁律提示】
- 5日涨幅 > 25% 或 10日 > 50% → 高位风险,谨慎
- 主力占比 < 30% + 散户占比 > 50% → 跟风盘嫌疑
- 该股今日未涨停 + 近 5 日无涨停 + 板块有涨停 → 杂毛,回避
- 拉萨天团主导 → 回避
- 4 层全过 + 风险可控 → 买
- 任一层不过 → 观望
- 主力层失败 → 回避

【输出要求】
返回严格 JSON (不许 markdown 围栏):
{{
  "verdict": "买/观望/回避",
  "role": "龙头/中军/杂毛",
  "conviction": 0-100整数,
  "suggested_window": "今早竞价 / 9:35-10:00 / 10:30 后 / 14:00 后 / 收盘前 / 暂观望",
  "entry_price_range": "25.20-25.50" (基于当前价 ±2% 给出),
  "stop_loss": "24.50" (建议止损位,基于当前价 -3% 到 -5%),
  "time_horizon": "1-3天 / 5-10天 / 中长期",
  "summary": "一句话(≤80字)综合判定,必须引用 5/10日涨跌 + 资金占比 + 板块联动",
  "rules_passed": ["通过的规则"],
  "rules_failed": ["违背的规则"],
  "key_risks": ["关键风险点"]
}}"""

    system_prompt = _build_watchlist_system_prompt()

    url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    text = ""
    last_err = ""
    for attempt, max_t in [(1, 1500), (2, 2500)]:
        body_local = dict(body)
        body_local["max_tokens"] = max_t
        try:
            r = await asyncio.to_thread(_requests.post, url, json=body_local, headers=headers, timeout=30)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log.warning(f"watchlist AI attempt {attempt} 异常 {code}: {e}")
            continue
        if r.status_code != 200:
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            log.warning(f"watchlist AI attempt {attempt} HTTP {r.status_code} {code}")
            continue
        j = r.json()
        text = j.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        if text:
            break
        finish = j.get("choices", [{}])[0].get("finish_reason", "?")
        reasoning = j.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")[:200]
        last_err = f"empty content (finish={finish}, reasoning={reasoning})"
        log.warning(f"watchlist AI attempt {attempt} content 空 {code} (finish={finish})")
    if not text:
        log.warning(f"watchlist AI 失败 {code}: {last_err}")
        return {}
    parsed = _parse_ai_json(text)
    # 附 extras
    parsed["extras"] = extras
    return parsed


def _call_minimax_safe(code: str) -> dict:
    """(保留) 备用入口,未来 stock_ai_analysis 完全迁移到 watchlist 时用"""
    return {}


# 自选股 AI 专用 system prompt (强化铁律 + 时间维度)
_WATCHLIST_LAWS_INJECT = """- 控心魔:不追高、不情绪化、不补仓
- 弃杂毛:不参与无主线、无辨识度的票
- 设止损:任一笔单都有止损位
- 无计划不买票:买入前必须有清晰理由
- 看不清就空仓:连板高位 + 量能不足 → 空仓
- 主线合力才参与:板块当日 ≥5 只涨停 + ≥2 只连板 → 主线
- 连续盈利减仓位:已获利 > 20% → 减仓
- 错单果断离场:违背铁律立即离场"""

def _build_watchlist_system_prompt() -> str:
    return """你是退学炒股 AI 助手,任务:基于行情 / 5日10日涨跌 / 资金占比 / 板块联动 / 龙虎席位,
给自选股"买/观望/回避"判定,必须严格结合退学铁律。

【核心铁律(摘要)】
- 庄股 / 跟风 / 杂股 → 回避
- 拉萨天团主导 → 回避
- 九连阳后放量跌停 → 回避
- 4 层(风控/周期/形态/分时)全过 + 风险可控 → 买
- 任一层不过 → 观望
- 主力层失败 → 回避

【判定优先级】
1. 数据铁律:5日涨幅 > 25% 或 10日 > 50% → 高位风险,conviction ≤ 40,verdict 倾向"观望"
2. 资金铁律:主力占比 < 30% + 散户占比 > 50% → 跟风盘,verdict 倾向"回避"
3. 联动铁律:该股今日未涨停 + 近 5 日无涨停 + 板块有涨停 → 杂毛 → 回避
4. 席位铁律:拉萨天团 / 黑名单席位 → 回避
5. 共识铁律:4 层全过 + 板块联动强 + 主力净流入 → 买,conviction 60+
6. 时间铁律:盘中需给出"建议时间窗口"(竞价 / 9:35-10:00 / 10:30 后 / 14:00 后 / 收盘前)

【suggested_window 选取规则】
- 高位 + 获利盘丰厚 → "10:30 后" (等获利盘消化)
- 首板 + 板块启动 → "今早竞价" 或 "9:35-10:00" (跟启动)
- 中军 + 趋势走强 → "10:30 后" (确认方向)
- 杂毛 / 风险高 → "暂观望"
- 尾盘抢筹 → "14:00 后"

【entry_price_range 选取规则】
- 在当前价 ±2% 区间,不要离现价太远
- 主线龙头:现价附近 ±1%
- 中军:现价 ±2%
- 杂毛:不输出 entry_price_range (返回空)

【stop_loss 选取规则】
- 短线 (1-3 天):现价 -5%
- 波段 (5-10 天):现价 -8%
- 中长期:现价 -10%
- 杂毛:不输出 stop_loss (返回空)

【time_horizon 选取规则】
- 短线 → "1-3天"
- 波段 → "5-10天"
- 中长期 → "中长期"
- 回避/观望 → "—"

【严格 JSON 输出 · 不许额外文字】"""


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


@app.get("/api/stream/optimize")
async def stream_optimize(iterations: int | None = None):
    """SSE: 优化器实时进度推送。客户端断开 → 后台停止 (通过 cancellation)。
    旧版 POST /api/optimize 兼容保留（无进度反馈，30min 跑完才返）。
    """
    from ..optimizer import run_optimize
    from sse_starlette.sse import EventSourceResponse

    async def gen():
        loop = asyncio.get_event_loop()
        progress_queue = asyncio.Queue()

        def _cb(p: dict):
            loop.call_soon_threadsafe(progress_queue.put_nowait, p)

        def _run_with_cb():
            try:
                return run_optimize(iterations=iterations, progress_cb=_cb)
            except Exception as e:
                return {"error": str(e), "phase": "failed"}

        task = loop.run_in_executor(_LONG_EXECUTOR, _run_with_cb)
        try:
            yield {"event": "phase", "data": json.dumps({"phase": "start", "msg": "优化器启动 ..."})}
            while True:
                try:
                    p = await asyncio.wait_for(progress_queue.get(), timeout=2.0)
                    yield {"event": "progress", "data": json.dumps(p, default=str)}
                    if p.get("phase") == "done":
                        break
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": json.dumps({"t": time.time()})}
                if task.done():
                    break
            result = await task
            yield {"event": "done", "data": json.dumps(result, ensure_ascii=False, default=str)}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(gen())


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
            _LONG_EXECUTOR,
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

    return EventSourceResponse(gen(), ping=15)


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
            _LONG_EXECUTOR,
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

    return EventSourceResponse(gen(), ping=15)


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
    # 1) 实时抓取 poller 必须永远启动 — 它跟 --no-preheat 无关
    #    (2026-07-11 进页面 ?fresh=1 + 10s 轮询 配合;poller 预热是它的伴生)
    try:
        from . import _realtime_poller
        _poller = _realtime_poller.RealtimePoller(
            _recent_codes_provider=_prune_recent,
            cache_quote=_cache_quote,
            ttl_seconds=30,
        )
        _poller.start()
        app.state._poller = _poller   # 让 health 端点能 introspect
        log.warning(f"[实时抓取] poller 已启动 (TTL {_poller.ttl_seconds}s, thread={_poller._thread.name if _poller._thread else 'NONE'})")
    except Exception as e:
        log.warning(f"[实时抓取] poller 启动失败: {e}")
        import traceback
        log.warning(traceback.format_exc())
    # 2) 数据预热(慢接口 cache 填充)— --no-preheat 时跳过
    if getattr(app, "_skip_preheat", False):
        log.info("[启动预热] 已跳过 (--no-preheat),poller 正常运行中")
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
    p.add_argument("--server", choices=["auto", "uvicorn", "hypercorn"], default="auto",
                   help="HTTP server: auto=hypercorn (HTTP/2 + h2) 优先,uvicorn fallback")
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

    # ── 选择 server:hypercorn(HTTP/2) 优先,失败回落 uvicorn(1.x) ──
    use_h2 = False
    runner_name = "uvicorn"
    if args.server in ("auto", "hypercorn"):
        try:
            from hypercorn.config import Config as HCConfig
            from hypercorn.asyncio import serve as hc_serve
            use_h2 = True
            runner_name = "hypercorn"
        except Exception as e:
            if args.server == "hypercorn":
                log.warning(f"hypercorn 不可用,fallback 到 uvicorn: {e}")
            use_h2 = False

    if use_h2:
        import asyncio
        cfg = HCConfig()
        cfg.bind = [f"{args.host}:{args.port}"]
        cfg.loglevel = "warning"
        cfg.keep_alive_timeout = 300   # 同 uvicorn,防 tunnel 闲置踢连
        cfg.h2 = True                  # HTTP/2 多路复用
        cfg.alpn_protocols = ["h2", "http/1.1"]
        cfg.workers = 1
        print(f"  ⚡ {runner_name} (HTTP/2 + h2 多路复用) ·  keep-alive 300s")
        print()
        asyncio.run(hc_serve(app, cfg))
        return

    print(f"  · {runner_name} (HTTP/1.1) ·  keep-alive 300s")
    print()
    uvicorn.run(
        "tuixue_v3.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
        # keep-alive 默认 5s,免费隧道闲置 5s 就把连接踢了
        # 手机下次请求又要重新建链(200-500ms 隧道开销)
        # 拉到 300s 让浏览器复用 TCP 连接
        timeout_keep_alive=300,
    )


# ═══════════════════════════════════════════════════════════════
# 复盘系统 v2 — 资金占比 + SSE 流 (2026-07-10)
# ═══════════════════════════════════════════════════════════════

# 资金流缓存(60s 短 TTL,前端每 10s 拉)
_cache_capital = TTLCache(default_ttl=60.0)


def _fetch_capital_one(code: str) -> dict:
    """单只股票的资金结构(主力/散户/基金占比)。
    3 源降级: 东财 push2 → 腾讯 qt.gtimg → akshare
    关键: server 进程网络可能受限,用 subprocess 调独立 Python 拿数据(绕开 server 网络栈)
    公式: 占比 = 该档净额绝对值 / (主+超+大+中+小绝对值合计) × 100
    """
    code = code.strip().zfill(6)

    # 用独立子进程拿数据,避免 server 进程网络问题
    py = sys.executable  # 当前 server 用的 python
    helper = textwrap.dedent(f'''
        import sys, json, requests
        code = "{code}"
        out = {{"code": code, "ts": 0, "main_pct": 0, "retail_pct": 0, "fund_pct": 0,
                "main_amount": 0, "big_amount": 0, "mid_amount": 0, "sml_amount": 0, "source": "fallback"}}
        def _calc_pcts(main, big, mid, sml):
            # 用各档净额绝对值之和做分母 (更接近真实占比)
            total = abs(main) + abs(big) + abs(mid) + abs(sml) + 1e-6
            return {{
                "main_pct": round(abs(main) / total * 100, 2),
                "retail_pct": round((abs(mid) + abs(sml)) / total * 100, 2),
            }}
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
                    # f170=主力净额 f168=超大单 f169=大单 f171=中单 f172=小单 (单位:元)
                    # 注意:部分接口字段顺序不一致,只用净额算占比
                    main = float(d.get("f170", 0) or 0) / 1e4  # 元→万
                    big = float(d.get("f168", 0) or 0) / 1e4
                    mid = float(d.get("f169", 0) or 0) / 1e4
                    sml = float(d.get("f171", 0) or 0) / 1e4
                    pcts = _calc_pcts(main, big, mid, sml)
                    out.update({{
                        **pcts,
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
                    # ff_* 字段:30=主力 31=超大 33=大 34=中 35=小 (单位:万)
                    main = float(fs[30] or 0)
                    big = float(fs[31] or 0)
                    mid = float(fs[33] or 0)
                    sml = float(fs[34] or 0)
                    pcts = _calc_pcts(main, big, mid, sml)
                    out.update({{
                        **pcts,
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
                main = float(row.get("主力净流入-净额") or 0) / 1e4  # 元→万
                big = float(row.get("超大单净额") or 0) / 1e4
                mid = float(row.get("中单净额") or 0) / 1e4
                sml = float(row.get("小单净额") or 0) / 1e4
                pcts = _calc_pcts(main, big, mid, sml)
                out.update({{
                    **pcts,
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
    return EventSourceResponse(event_gen(), ping=15)


if __name__ == "__main__":
    main()
