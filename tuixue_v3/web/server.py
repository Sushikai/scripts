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
import secrets
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
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, BackgroundTasks, Body, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import fund_flow, seat_lookup
from .. import cache_store

log = logging.getLogger("tuixue_v3.web")

try:
    from dotenv import load_dotenv as _load_dotenv
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    for _env_path in (_PROJECT_ROOT / ".env", Path.home() / ".env_minimax"):
        if _env_path.exists():
            _load_dotenv(_env_path, override=False)
            log.info(f"loaded env: {_env_path}")
except ImportError:
    pass

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
    # R-cfg-032: endpoint 分组 — /docs 自动按 tag 折叠,文档更好读
    openapi_tags=[
        {"name": "health",     "description": "健康检查 / 版本 / 指标"},
        {"name": "dashboard",  "description": "首页信号 / 热门板块 / 涨停统计"},
        {"name": "stock",      "description": "个股详情 / K线 / 分时 / 资金 / 席位"},
        {"name": "ai",         "description": "AI 对话 / AI 打分 / AI 复盘 / AI 风险"},
        {"name": "review",     "description": "交易复盘 / 持仓 / 设置"},
        {"name": "screener",   "description": "选股引擎 / 回测 / 历史快照"},
        {"name": "sectors",    "description": "板块实时 / 申万 / 4 层分类 / 主线"},
        {"name": "news",       "description": "财经新闻 / AI 分析"},
        {"name": "tunnel",     "description": "ngrok / cloudflared 隧道启停"},
        {"name": "admin",      "description": "管理: 缓存清理 / DB 备份 / 重置"},
    ],
)

# R-sec-001: 全局异常处理 — 所有 500/422 走统一 envelope 格式 {ok:false, error, trace_id}
# 这之前所有未捕获异常返回裸 500 + stack trace,既泄露内部又破坏前端 envelope 协议
@app.exception_handler(Exception)
async def _global_exception_handler(request, exc):
    trace_id = getattr(request.state, "trace_id", "-")
    log.exception(f"[trace={trace_id}] {request.method} {request.url.path} 未捕获: {exc}")
    return JSONResponse(
        {"ok": False, "error": f"internal: {type(exc).__name__}", "trace_id": trace_id},
        status_code=500,
    )

@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request, exc):
    trace_id = getattr(request.state, "trace_id", "-")
    log.warning(f"[trace={trace_id}] {request.method} {request.url.path} 422: {exc}")
    return JSONResponse(
        {"ok": False, "error": "validation failed", "detail": exc.errors(), "trace_id": trace_id},
        status_code=422,
    )

@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request, exc):
    # 例: raise HTTPException(403, "forbidden") → 统一 envelope 格式
    return JSONResponse(
        {"ok": False, "error": exc.detail, "status_code": exc.status_code, "trace_id": getattr(request.state, 'trace_id', '-')},
        status_code=exc.status_code,
    )

# ───────────────────────────────────────────────────────────
# R-sec-2026-07-15: admin / destructive endpoint 鉴权
# TUIXUE_ADMIN_TOKEN 未设置 → 放行(开发模式),设置后所有 admin_*/DELETE 端点要求 X-Admin-Token
# 前端 core.js api() 自动从 localStorage 读 'tuixue-admin-token' 注入到这些端点
# 首次访问 /api/admin/* 时若未配置,后端返 401 + 提示用户在浏览器 console 设置:
#   localStorage.setItem('tuixue-admin-token', '<token-from-env>')
# ───────────────────────────────────────────────────────────
def _check_admin_token(request) -> bool:
    expected = os.environ.get("TUIXUE_ADMIN_TOKEN", "").strip()
    if not expected:
        return True  # 未配置 → 放行(向后兼容开发模式)
    provided = (request.headers.get("X-Admin-Token") or "").strip()
    if provided and provided == expected:
        return True
    log.warning(f"[auth] admin token mismatch from {request.client.host if request.client else '?'}")
    return False


# B5: tunnel/start 5min 冷却时间戳(进程内)— 防止疯狂重试拖死 worker
_last_tunnel_start_ts: float = 0.0


# P1-5 (2026-07-15) · tunnel 自愈 loop 状态 — 进程内, 重启清零
#  - _tunnel_last_method: 上次成功的 method, 重启时优先
#  - _tunnel_heal_attempts: 当前连续自愈失败次数 (>=3 → TG 推送失败并停一段时间)
#  - _tunnel_heal_paused_until: 上次 push 失败后, 等 30min 再重启 loop
#  - _tunnel_last_health_ok_ts: 最近一次 HEAD /api/health 200 的时间 (用于探测"假活")
_tunnel_heal_state: dict = {
    "last_method": "",
    "attempts": 0,
    "paused_until": 0.0,
    "last_health_ok_ts": 0.0,
    "last_heal_at": 0.0,
}


def _tunnel_files() -> tuple[str, str, str]:
    """返回 url/method/pid 文件路径"""
    base = os.path.dirname(__file__)
    return (
        os.path.join(base, "..", "tunnel_url.txt"),
        os.path.join(base, "..", "tunnel_method.txt"),
        os.path.join(base, "..", "tunnel_pid.txt"),
    )


def _read_tunnel_url_pair() -> tuple[str, str]:
    """同步读 url + method, 失败返空。线程安全 (只读)"""
    try:
        url_f, method_f, _ = _tunnel_files()
        url = open(url_f, encoding="utf-8").read().strip() if os.path.exists(url_f) else ""
        method = open(method_f, encoding="utf-8").read().strip() if os.path.exists(method_f) else ""
    except Exception:
        url, method = "", ""
    return url, method


def _get_lan_ip() -> str:
    """C4: 取局域网 IP — 扫所有非 lo/awdl/utun 接口,取首个非空 IP。
    全失败时回 UDP-connect 拿本机外网 IP,再失败回 127.0.0.1。"""
    try:
        import subprocess as _sp
        try:
            ifaces_raw = _sp.check_output(["networksetup", "-listallhardwareports"], text=True, timeout=2)
            import re as _re
            devs = _re.findall(r"Device:\s*(\S+)", ifaces_raw)
        except Exception:
            devs = ["en0", "en1", "en2"]
        for dev in devs:
            if dev.startswith(("lo", "awdl", "llw", "utun", "bridge")):
                continue
            try:
                ip = _sp.check_output(["ipconfig", "getifaddr", dev], text=True, timeout=1).strip()
                if ip and not ip.startswith("127."):
                    return ip
            except Exception:
                continue
        raise RuntimeError("no ifaddr on any iface")
    except Exception:
        try:
            import socket as _sk
            s = _sk.socket(_sk.AF_INET, _sk.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"


def _validated_extra_origins() -> list[str]:
    """R-sec-018: 解析 TUIXUE_EXTRA_ORIGINS,只接受合法 https://域 + 几个可信子域。
    错字如 "*.evil.com" / "javascript:..." / "null" / 端口被吞的, 直接拒收。
    """
    from urllib.parse import urlparse
    raw = os.environ.get("TUIXUE_EXTRA_ORIGINS", "") or ""
    out: list[str] = []
    for token in (t.strip() for t in raw.split(",") if t.strip()):
        try:
            u = urlparse(token)
        except Exception:
            log.warning(f"[CORS] 拒收非法 origin: {token!r}")
            continue
        if u.scheme not in ("http", "https"):
            log.warning(f"[CORS] 拒收非 http(s) origin: {token!r}")
            continue
        if not u.netloc:
            continue
        # cloudflared / ngrok / localhost.run 域允许
        host = u.hostname or ""
        if not any(host.endswith(d) for d in (
            ".trycloudflare.com",
            ".ngrok.io", ".ngrok-free.app", ".ngrok.app",
            ".loca.lt",
            ".serveo.net",
            "localhost", "127.0.0.1",
            ".tuixue.dev",  # 自有域 (若部署)
        )):
            log.warning(f"[CORS] 拒收不在白名单的 origin: {token!r}")
            continue
        out.append(f"{u.scheme}://{u.netloc}")
    return out


app.add_middleware(
    CORSMiddleware,
    # 收紧 CORS:仅允许同源 + 本地开发 + ngrok/cloudflare 隧道域名
    # 原 allow_origins=["*"] 把管理/控制类接口暴露给任意网页 (R1-B 修复)
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:8000",
        "http://localhost:7799",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:7799",
        # ngrok / cloudflared 隧道域(在环境变量里加白名单)
        *_validated_extra_origins(),
    ],
    allow_origin_regex=r"https?://.*\.(ngrok-free\.dev|trycloudflare\.com|loca\.lt|ngrok\.io|ngrok\.app|serveo\.net)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Trace-Id"],
)
# GZip:app.js 140KB / style.css 64KB / HTML 37KB,走隧道必须压
app.add_middleware(GZipMiddleware, minimum_size=512)


# R-sec-019: CSP / X-Content-Type-Options / Referrer-Policy 加到所有响应
@app.middleware("http")
async def _security_headers_middleware(request, call_next):
    resp = await call_next(request)
    # CSP 宽松型: 允许 inline-script (本项目 index.html 头部有 inline 主题脚本)
    # + 允许 data: img (背景 mesh/noise) + 同源 worker
    # frame-ancestors 'self' 允许同源 iframe (screener/sector_* 嵌进主 app shell) 2026-07-14
    # 反 clickjacking 用 X-Frame-Options: SAMEORIGIN 兜底(浏览器兼容更老)
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "style-src-elem 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https:; "
        "font-src 'self' https://fonts.gstatic.com https://fonts.googleapis.com data:; "
        "frame-ancestors 'self'; "
        "base-uri 'self';"
    )
    resp.headers.setdefault("Content-Security-Policy", csp)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    # 我们同源 API 为主, 不向外暴露完整 URL 参数
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return resp


# R9-A: 请求 trace-id 中间件 — 每个请求分配一个短 id, 日志 + 响应头透出
import uuid as _uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Req

# R-sec-002: 删 _TraceMiddleware — 这与 _rate_limit_middleware 双倍写 trace_id / 覆写 X-Trace-Id 响应头
# trace-id 现在由 _rate_limit_middleware 单一来源管理; 异常由全局 exception_handler 统一打印
# (老 _TraceMiddleware 见 git log 恢复)


# ───────────────────────────────────────────────────────────
# 工具:TTL 缓存 + 线程池 + 统一错误信封
# ───────────────────────────────────────────────────────────
_T = TypeVar("_T")

class TTLCache:
    """进程内同步 TTL 缓存(key 必须是 hashable)。
    R3 升级: 加 LRU 上限 + 后台扫描清理过期项(原版只在 get 时惰性清理,导致冷数据占内存)。
    """
    def __init__(self, default_ttl: float = 30.0, max_size: int = 512):
        self.ttl = default_ttl
        self.max_size = max_size
        self._data: dict[tuple, tuple[Any, float]] = {}
        # 记录访问顺序用于 LRU 淘汰 (key -> 上次访问时间)
        self._lru: dict[tuple, float] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._miss = 0
        self._evictions = 0

    def _touch_lru(self, key: tuple) -> None:
        self._lru[key] = time.monotonic()

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                self._miss += 1
                return None
            data, ts = entry
            if time.monotonic() - ts > self.ttl:
                self._data.pop(key, None)
                self._lru.pop(key, None)
                self._miss += 1
                return None
            self._hits += 1
            self._touch_lru(key)
            return data

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            # 超出上限时淘汰最久未访问的 key (LRU)
            if key not in self._data and len(self._data) >= self.max_size:
                if self._lru:
                    victim = min(self._lru, key=self._lru.get)
                    self._data.pop(victim, None)
                    self._lru.pop(victim, None)
                    self._evictions += 1
            self._data[key] = (value, time.monotonic())
            self._touch_lru(key)

    def invalidate(self, *keys: tuple, confirm: str = "") -> int:
        """失效指定 key。返回实际删除条数。
        支持一个或多个 key;空参=全清(必须 confirm='YES')。

        R-sec-014: 之前空参偷偷清全部, /api/_meta/cache_clear?scope=ttl 误清 _cache_quote
        把用户当前热查股票的所有 quote 都清掉, 下一次请求上游 rate-limit ban。
        现在空参要 confirm='YES' 才生效 + log.warning 留痕迹。
        """
        with self._lock:
            if not keys:
                if confirm != "YES":
                    log.warning(f"TTLCache.invalidate 全清未授权拒绝 (需要 confirm='YES')")
                    return 0
                log.warning(f"TTLCache.invalidate 全清 ({len(self._data)} keys) — confirm=YES 通过")
                n = len(self._data)
                self._data.clear()
                self._lru.clear()
                return n
            n = 0
            for k in keys:
                if self._data.pop(k, None) is not None:
                    n += 1
                self._lru.pop(k, None)
            return n

    def sweep_expired(self) -> int:
        """主动扫描并清理所有过期项(后台线程每 60s 跑一次)。"""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, ts) in self._data.items() if now - ts > self.ttl]
            for k in expired:
                self._data.pop(k, None)
                self._lru.pop(k, None)
            return len(expired)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._miss
            return {
                "size": len(self._data),
                "max_size": self.max_size,
                "hits": self._hits,
                "miss": self._miss,
                "evictions": self._evictions,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0,
            }


class SingleFlight:
    """防止 cache miss 时 N 个并发请求同时打到下游。
    同一 key 在飞时复用同一个 Future,后续 await 全部 join。

    2026-07-14 重写: 用 _completed 分担已完成结果, 避免 race (旧版 pop 后 waiter 读
    _inflight 拿到 None) + 异常路径覆盖问题。
    """
    def __init__(self):
        self._lock = threading.Lock()
        # in-flight: 正在执行中的请求 (key → Event)
        self._inflight: dict[tuple, threading.Event] = {}
        # completed: 已经完成的结果缓存 (key → (val, err, ts))
        self._completed: dict[tuple, tuple] = {}

    def run(self, key: tuple, fn, *args, **kwargs):
        with self._lock:
            ev = self._inflight.get(key)
            if ev is None:
                ev = threading.Event()
                self._inflight[key] = ev
                _is_first = True
            else:
                _is_first = False
        if not _is_first:
            # join path
            ev.wait(timeout=kwargs.pop("_sf_timeout", 30.0))
            with self._lock:
                completed = self._completed.get(key)
            if completed is None:
                # 没有完成记录(超时 / 异常路径 / 还是同一 caller), 退化: 自己跑一次
                return fn(*args, **kwargs)
            val, err, _ts = completed
            return val if err is None else (_ for _ in ()).throw(err)
        _val_local = None
        _exc_local: Exception | None = None
        try:
            _val_local = fn(*args, **kwargs)
            return _val_local
        except Exception as e:
            _exc_local = e
            with self._lock:
                self._completed[key] = (None, e, time.monotonic())
            raise
        finally:
            with self._lock:
                self._completed[key] = (_val_local, _exc_local, time.monotonic())
                self._inflight.pop(key, None)
            ev.set()


import threading  # noqa: E402

# 三档 TTL
_cache_spot    = TTLCache(default_ttl=60.0)    # 全市场股票列表 60s
_cache_quote   = TTLCache(default_ttl=5.0)     # 实时行情 5s(盘口活)
_cache_kline   = TTLCache(default_ttl=300.0)   # 日线 5min
_cache_fund    = TTLCache(default_ttl=60.0)    # 资金流 60s (2026-07-11 30→60,减少 akshare 限频期刷新)
_cache_overview = TTLCache(default_ttl=15.0)   # 大盘指数 15s
_cache_global  = TTLCache(default_ttl=60.0)   # 全球情绪 60s(美/韩数据源慢)
_cache_layer   = TTLCache(default_ttl=600.0)  # AI 层详情 10min (4 路并行 + 规则,纯计算,值得缓存;100 轮压测 P99 16s→2ms)

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

    R-sec-004: 异常从静默 log.debug → log.warning + 计数器; 对特定已知可降级的域
    (行情/资金流超上游) 仍允许返回 None,但留指标; 其它域 propagate 给上层。
    """
    # 模块级异常计数器(给 /api/metrics 观察)
    _ERR = {"err": 0, "none_return": 0}

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
                _ERR["err"] += 1
                # raise 关键错误 (DB schema 错、AI key 丢失、配置错),
                # 这些不应被吞掉
                fn_name = fn.__name__
                if any(s in str(e) for s in ("no such column", "no such table",
                                              "API key", "MINIMAX_API_KEY")):
                    log.warning(f"[cache-decorator] {fn_name} 关键错误 propagate: {e}")
                    raise
                # 其余上游/网络错误 → 返回 None (老行为) + 警告级日志 + 计数器
                log.warning(f"[cache-decorator] {fn_name} 上游失败返回 None: {type(e).__name__}: {e}")
                _ERR["none_return"] += 1
                return None
            # 只缓存「有意义」的结果 — 空 dict / 空 list / None 都不入缓存
            if val is None:
                return None
            if isinstance(val, (dict, list, str)) and len(val) == 0:
                return val  # 但仍返回(让上层决定);不入缓存
            ttl_cache.set(key, val)
            return val
        # 暴露计数器供 metrics 拉取
        wrap._cache_stats = _ERR
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


def json_etag_response(request: _Request, payload: dict, *, max_age: int = 0) -> _Response:
    """R-perf-023: 路由级 ETag + 条件请求 304。

    对内容稳定的只读 JSON 端点用 — 计算 body 的强 ETag,
    若客户端 If-None-Match 命中则返回 304(不带 body,省带宽/序列化)。
    max_age>0 时附 Cache-Control(默认 0 = 仅协商缓存,始终校验)。
    """
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    etag = '"' + _hashlib.md5(body).hexdigest()[:16] + '"'
    cc = (f"public, max-age={max_age}, stale-while-revalidate=60"
          if max_age > 0 else "no-cache, must-revalidate")
    inm = request.headers.get("if-none-match", "")
    if inm and inm.strip() == etag:
        return _Response(status_code=304, headers={"ETag": etag, "Cache-Control": cc})
    return _Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": cc},
    )


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
    # 所有 JS 分片文件的合并指纹 — 任一个修改则所有缓存失效
    js_files = ["core.js", "app.js", "view-dash.js", "view-stock.js", "view-other.js", "view-all-stocks.js"]
    js_h = "0" * 8
    for fname in js_files:
        fh = _live_fingerprint(fname, STATIC_DIR / fname)
        if fh != "0" * 8:
            js_h = _hashlib.sha256(f"{js_h}:{fh}".encode()).hexdigest()[:8]
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
    R1-B 修复: 路径穿越防护用相对路径比较而非 str.startswith
    (原版可被 '../static_evil/...' 绕过 — 同前缀但非子目录)
    """
    target = (STATIC_DIR / path).resolve()
    static_root = STATIC_DIR.resolve()
    try:
        target.relative_to(static_root)  # 越界即抛 ValueError
    except ValueError:
        raise HTTPException(404)
    if not target.is_file():
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
_IP_WINDOW_MAX = 4096  # R-perf-026: 上限 IP 数,防字典膨胀 (LRU 驱逐)
# R-perf-026: 路径分级限频(AI/上传/SSE) — 二段 dict,独立维护
_path_tier_hits: dict[str, list[float]] = {}
# R-sec-015: 图片上传 (parse_trade_image) 单独滑动窗 — 与通用 rate-limit 解耦
# 通用 200 req/10s 太大, 15 张图/分钟 足够正常用户
_img_window: dict[str, list[float]] = {}
_img_window_lock = threading.Lock()
RATE_WINDOW_SEC = 10.0
# 2026-07-11: 60→200。 100 轮 × 14 端点 × 3 并发 = 4200 次 / ~25s = 1680 req/10s
# 实际用户不会这么快, 200 是给前端轮询 + 压测同时跑的安全边界。
RATE_MAX_REQ = 200  # 10s 内最多 200 次

# R-perf-026: 路径分级限频 — 重型端点(AI/SSE/复盘触发)更紧的窗口
# (path_prefix, max, window_sec)。最长前缀优先。
_PATH_TIERS: tuple[tuple[str, int, float], ...] = (
    ("/api/ai/",            30, 60.0),  # AI 端点(chat/scoring/...): 30/min
    ("/api/stream/",        4,  10.0),  # SSE 每 IP 短窗内最多 4 条
    ("/api/review/trades/", 10, 10.0),  # 复盘触发端点 — 防止连点炸队列
    # parse_trade_image 由 endpoint 内 _img_window(15/60s)独立控制,不列这里
)


def _hit_path_tier(path: str) -> tuple[int, float] | None:
    """匹配最长的 path_prefix,返回 (max_count, window_sec) 或 None。"""
    best = None
    for prefix, mx, sec in _PATH_TIERS:
        if path.startswith(prefix):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, mx, sec)
    return (best[1], best[2]) if best else None


@app.middleware("http")
async def _rate_limit_middleware(request, call_next):
    """每 IP 滑动窗口限频 + 路径分级。超过 RATE_MAX_REQ/10s → 429。"""
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    with _ip_lock:
        # R-perf-026: 字典膨胀防护 — 上限 _IP_WINDOW_MAX,超了按最旧末次命中驱逐
        if len(_ip_window) >= _IP_WINDOW_MAX:
            evict_n = len(_ip_window) - _IP_WINDOW_MAX + 64
            oldest = sorted(_ip_window.items(),
                            key=lambda kv: kv[1][-1] if kv[1] else 0)[:evict_n]
            for k, _ in oldest:
                _ip_window.pop(k, None)
        hits = _ip_window.setdefault(ip, [])
        hits[:] = [t for t in hits if now - t < RATE_WINDOW_SEC]
        if len(hits) >= RATE_MAX_REQ:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"ok": False, "error": f"rate-limited: {RATE_MAX_REQ}/{RATE_WINDOW_SEC}s per IP"},
                status_code=429,
            )
        hits.append(now)
        # 路径分级限频(叠加)
        tier = _hit_path_tier(request.url.path)
        if tier:
            mx, sec = tier
            tier_key = f"{ip}::{request.url.path}"
            tier_hits = _path_tier_hits.setdefault(tier_key, [])
            tier_hits[:] = [t for t in tier_hits if now - t < sec]
            if len(tier_hits) >= mx:
                return JSONResponse(
                    {"ok": False, "error": f"rate-limited: {mx}/{sec}s on {request.url.path}"},
                    status_code=429,
                )
            tier_hits.append(now)
            # 同样的 LRU 驱逐:防止 _path_tier_hits 跟通用 dict 一样膨胀
            if len(_path_tier_hits) >= _IP_WINDOW_MAX * 2:
                evict_n = len(_path_tier_hits) - _IP_WINDOW_MAX * 2 + 128
                oldest = sorted(_path_tier_hits.items(),
                                key=lambda kv: kv[1][-1] if kv[1] else 0)[:evict_n]
                for k, _ in oldest:
                    _path_tier_hits.pop(k, None)

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
    # R-obs-029: 结构化访问日志 — 5xx 必 warn;4xx 错误 warn;慢端点(>=2s) warn;否则 info
    path = request.url.path
    q = request.url.query or ""
    q_short = f"?{q[:80]}" if q else ""
    if resp.status_code >= 500:
        log.warning(f"[{trace_id}] {request.method} {path}{q_short} → {resp.status_code} {elapsed_ms}ms (5xx)")
    elif resp.status_code >= 400:
        log.warning(f"[{trace_id}] {request.method} {path}{q_short} → {resp.status_code} {elapsed_ms}ms")
    elif elapsed_ms >= 2000:
        log.warning(f"[{trace_id}] {request.method} {path}{q_short} → {resp.status_code} {elapsed_ms}ms (slow)")
    else:
        log.debug(f"[{trace_id}] {request.method} {path}{q_short} → {resp.status_code} {elapsed_ms}ms")
    return resp


# R-sec-024: 状态变更请求 Origin 校验 — 兜底 CORS(防预检失效 / 老浏览器 / 自定义 client)
# GET/HEAD/OPTIONS 不限;非 GET 必须 Origin 在 allow_origins 或为空(同源浏览器自带 Origin,空 = 同源或非浏览器)
@app.middleware("http")
async def _origin_check_middleware(request, call_next):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    origin = request.headers.get("origin", "").strip().rstrip("/")
    # 空 origin:curl / server-to-server / 移动客户端。允许但记录。
    if not origin:
        log.debug(f"[origin_check] {request.method} {request.url.path} 空 origin,放行")
        return await call_next(request)
    # 从 CORSMiddleware 的 allow_origins 同步拿 — 单一来源
    allowed = set()
    for o in getattr(app, "user_middleware", []):
        pass
    # CORSMiddleware 是 app.add_middleware 注册的,直接读 .allow_origins (FastAPI 内部 _origins 列表)
    allowed = _CORS_ALLOWED_ORIGINS  # 在模块加载时一次性快照
    # 也放行常见隧道域名（ngrok / cloudflared / localhost.run 等）
    _tunnel_suffixes = (".ngrok-free.dev", ".trycloudflare.com", ".lhr.life", "localhost.run")
    if origin not in allowed and not origin.startswith("http://localhost:") and not origin.startswith("http://127.0.0.1:") and not origin.endswith(_tunnel_suffixes):
        log.warning(f"[origin_check] 拒收 {request.method} {request.url.path} origin={origin!r}")
        return JSONResponse(
            {"ok": False, "error": "origin not allowed", "trace_id": getattr(request.state, "trace_id", "-")},
            status_code=403,
        )
    return await call_next(request)


# 模块加载时一次性快照 CORS allow_origins,避免每次请求都遍历 user_middleware
_CORS_ALLOWED_ORIGINS: set[str] = set([
    "http://localhost:5173",
    "http://localhost:8000",
    "http://localhost:7799",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:7799",
    *_validated_extra_origins(),
])


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
            "global": _cache_global.stats(),
            "layer": _cache_layer.stats(),
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


@app.get("/api/version")
async def api_version():
    """R3 新增:版本号 + 模块清单 (调试 + 前端 footer 用)"""
    import sys
    return {
        "version": "2.0",
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "modules": {
            "fastapi": __import__("fastapi").__version__,
            "python": sys.version.split()[0],
            "pandas": __import__("pandas").__version__ if _safe_import("pandas") else None,
            "akshare": __import__("akshare").__version__ if _safe_import("akshare") else None,
        },
        "platform": sys.platform,
    }


def _safe_import(name: str) -> bool:
    try:
        __import__(name); return True
    except Exception:
        return False


@app.post("/api/health")
async def health_post():
    """sendBeacon 走 POST(永远 POST),必须兼容。
    2026-07-12 audit 发现前端 keepalive tick 用 navigator.sendBeacon('/api/health', ...) 触发 405。"""
    return await health()


@app.get("/api/healthz")
async def healthz():
    """K8s liveness probe — 进程活着就 200。
    不做 IO / 不查 DB / 不读磁盘。用于 k8s/load balancer 检测进程崩溃。"""
    return {"ok": True, "kind": "live"}


@app.get("/api/metrics")
async def metrics():
    """R9-A: 聚合指标 — 缓存命中率 + AI 调用 + DB 健康 + 在线时长。
    给前端 debug 面板用,体量小(<5KB) 可高频轮询。"""
    # 缓存聚合
    cache_stats = []
    for c in (_cache_spot, _cache_quote, _cache_kline, _cache_fund,
             _cache_overview, _cache_global, _cache_layer):
        try:
            s = c.stats()
            cache_stats.append({
                "name": c.__class__.__name__,
                **{k: v for k, v in s.items() if k in ("size", "hits", "misses", "evictions", "hit_rate")},
            })
        except Exception:
            pass
    # AI 指标
    ai_metrics = {}
    try:
        from . import ai_client as _ai
        ai_metrics = _ai.get_metrics()
    except Exception:
        pass
    # DB 健康
    db_h = {}
    try:
        from .. import cache_db as _cdb
        db_h = _cdb.db_health()
    except Exception:
        pass
    # 在线时长
    uptime = int(time.time() - _SERVER_START_TS) if "_SERVER_START_TS" in dir() else 0
    return envelope(data={
        "uptime_sec": uptime,
        "ts": time.time(),
        "cache": cache_stats,
        "ai": ai_metrics,
        "db": db_h,
        "poller": getattr(app.state, "_poller", None) and {
            "alive": getattr(app.state._poller, "_thread", None) is not None
                    and app.state._poller._thread.is_alive(),
            "ttl": app.state._poller.ttl_seconds,
        } or None,
    })


_SERVER_START_TS = time.time()


@app.post("/api/admin/backup")
async def admin_backup(request: Request):
    """R8: 手动触发 db 备份。返回备份文件路径。"""
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头(从 env TUIXUE_ADMIN_TOKEN 读)", status_code=401)
    from .. import cache_db
    path = cache_db.backup_db()
    return envelope(data={"path": path} if path else {"error": "备份失败"})


@app.get("/api/admin/db_health")
async def admin_db_health(request: Request):
    """R8: db 健康指标(WAL/慢查询/表行数/上次 backup)"""
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头(从 env TUIXUE_ADMIN_TOKEN 读)", status_code=401)
    from .. import cache_db
    return envelope(data=cache_db.db_health())


@app.get("/api/_meta/version")
async def meta_version():
    """构建元数据 — git sha / 启动时间 / 当前时间 / pid / uptime。

    前端 build badge 用 — 用户报问题时可一眼定位是不是旧版本。
    缓存 60s 即可,服务器重启 / 重新部署会自然刷新。
    """
    import os as _os
    import subprocess as _sp
    started = getattr(app.state, "_started_at", None)
    if started is None:
        started = time.time()
        app.state._started_at = started
    sha = ""
    try:
        sha = _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
            stderr=_sp.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        pass
    return {
        "ok": True,
        "version": app.version,
        "git_sha": sha,
        "started_at": datetime.datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "uptime_sec": int(time.time() - started),
        "pid": _os.getpid(),
        "now": datetime.datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/_meta/cache_stats")
async def meta_cache_stats():
    """全量缓存状态 — 给前端 debug 页 / 压力测试用。

    返回所有 TTLCache 的 hits/miss/size + Redis/SQLite 后端状态。
    """
    try:
        _store = cache_store.get_store()
        store_stats = _store.stats()
        store_status = _store.status()
    except Exception as e:
        store_stats = {"error": str(e)[:120]}
        store_status = {"redis": False}
    return {
        "ok": True,
        "ttl_caches": {
            "spot":     _cache_spot.stats(),
            "quote":    _cache_quote.stats(),
            "kline":    _cache_kline.stats(),
            "fund":     _cache_fund.stats(),
            "overview": _cache_overview.stats(),
        },
        "redis":  store_stats,
        "redis_status": store_status,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }


@app.post("/api/_meta/cache_clear")
async def meta_cache_clear(scope: str = Query("ttl", pattern="^(ttl|redis|all)$"),
                            confirm: str = ""):
    """手动清缓存 — debug 用, 必须 confirm='YES'。

    ttl — 清全部 TTLCache (quote/fund/kline/overview/spot)
    redis — 清 Redis store
    all   — 两个都清
    """
    if confirm != "YES":
        return envelope(error="cache_clear 需 confirm=YES (R-sec-014: 防止误清触发上游 ban)", status_code=400)
    cleared = []
    if scope in ("ttl", "all"):
        for c in (_cache_spot, _cache_quote, _cache_kline, _cache_fund, _cache_overview):
            c.invalidate(confirm="YES")
        cleared.append("ttl")
    if scope in ("redis", "all"):
        try:
            _store = cache_store.get_store()
            _store.flushdb()
            cleared.append("redis")
        except Exception as e:
            return envelope(error=f"redis flush failed: {e}")
    log.warning(f"[meta] cache_clear scope={scope} confirm=YES — 大操作")
    return envelope(data={"cleared": cleared})


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

    # 2026-07-12: QR 防御 — url 必须是 http(s):// 开头才视为"可扫码"
    # 否则(老 mqtt_bridge 把 mqtt://broker... 写进 url)前端会画一个
    # 长长且扫了无效的 QR。把这种 url 清掉,让它走 sentinel 通道显示。
    if url and not (url.startswith("http://") or url.startswith("https://")):
        log.warning(f"tunnel url scheme 不合法(非 http(s)://),已清掉防 QR 污染: {url[:60]}")
        url = ""
        method = ""

    # C4: 局域网 IP — 扫所有接口 (en0/en1/en2/...),取第一个非空;全失败时 UDP connect 兜底
    lan_ip = _get_lan_ip()

    # tunnel 是否真在用:有 url_file 才算 running=true (残留进程不算)
    # 之前用 pgrep 会被冷启动中的 cloudflared 残留 / 上次失败进程误导,
    # UI 一直显示"启动中…",实际是 LAN fallback 状态
    running = bool(url)

    # 状态简化:
    # - url 存在 + 有 method → running=true,status="online"
    # - url 文件不存在 → 离线 (fallback LAN)
    # method 字段告诉前端用什么 tunnel
    tunnel_state = "online" if running else "offline"

    # 也探测后台是否真在 spawn 启动 (url_file 还没写出,但进程在跑)
    port = int(os.environ.get("TUIXUE_PORT", "7799"))
    if not running:
        try:
            import subprocess as _sp2
            cur_pid = str(os.getpid())
            # 2026-07-12 加固:加上新的 8 条 anti-sandbox 机制的进程名匹配
            for pat in (f"cloudflared tunnel --url",
                        f"ngrok http {port}",
                        f"ssh -tt -R 80:localhost:{port}",
                        f"telegram_bridge.py --port {port}",
                        f"ntfy_pipe.py --port {port}",
                        f"mqtt_bridge.py --port {port}",
                        f"trystero_host.py --port {port}",
                        f"tun_cf_client.py",
                        f"tun_paas_client.py",
                        f"tailscale serve",
                        f"tailscale funnel"):
                try:
                    # pgrep -f 会把自身命令行作为 pattern, 必须排除当前 server 进程
                    out = _sp2.check_output(
                        ["pgrep", "-f", pat], timeout=1
                    ).decode().split()
                    real = [p for p in out if p != cur_pid]
                    if real:
                        tunnel_state = "starting"   # 进程在但还没出 URL
                        break
                except Exception:
                    pass
        except Exception:
            pass

    # 2026-07-12: 也检测 sentinel 文件 (TG-bot / mqtt 是 sentinel-based,
    # 没有 HTTP URL)
    sentinels = []
    sentinels_dir = "/tmp/tuixue_tunnels"
    try:
        for name, label in (
            ("telegram-bot.ready", "Telegram 双向代理"),
            ("mqtt_bridge.ready", "MQTT 代理"),
        ):
            import os as _os
            sp = _os.path.join(sentinels_dir, name)
            if _os.path.exists(sp):
                sentinels.append({"name": label, "info": _os.path.getsize(sp) > 0})
    except Exception:
        pass

    # P1-5 · 自愈 loop 状态 (前端可见, 让用户知道 "是后台在重连, 不是坏了")
    heal = _tunnel_heal_state
    heal_info = {
        "attempts":      heal["attempts"],
        "paused_until":  heal["paused_until"],
        "paused_remaining_sec": max(0, int(heal["paused_until"] - time.time())),
        "last_method":   heal["last_method"] or method,
        "last_health_ok_ts": heal["last_health_ok_ts"],
        "last_heal_at":  heal["last_heal_at"],
    }

    return envelope(data={
        "url":        url,
        "method":     method,
        "lan_ip":     lan_ip,
        "port":       port,
        "running":    running,
        "state":      tunnel_state,         # online / starting / offline
        "sentinels":  sentinels,            # 2026-07-12 新增:TG-bot / MQTT 提示
        "heal":       heal_info,            # P1-5 · 自愈 loop 状态
        "ts":         datetime.datetime.now().isoformat(timespec="seconds"),
    })


@app.post("/api/tunnel/start")
async def tunnel_start():
    """启动多路 fallback tunnel (后台). 阻塞轮询 url_file 拿到 URL 后立刻推 TG.

    R-perf-016: 之前 open(...).read() + send_telegram(同步) 阻塞 event loop (单 65s 占 worker)
    现在: 文件读 + TG 推都在 to_thread 执行, 每 1s poll 也用作 await asyncio.sleep

    B5: 5min cooldown 防止疯狂重试拖死 worker + 65s 失败时 TG 告警给用户
    """
    import os
    import subprocess as _sp

    # ── 5min cooldown: 一次启动未结束/刚失败,不再接受新请求 ──
    global _last_tunnel_start_ts
    now = time.time()
    if _last_tunnel_start_ts and (now - _last_tunnel_start_ts) < 300:
        wait = int(300 - (now - _last_tunnel_start_ts))
        return envelope(error=f"tunnel 启动冷却中,还需 {wait}s 才可重试", data={"cooldown": True, "wait_sec": wait})
    _last_tunnel_start_ts = now

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
        # start_new_session=True 已脱离父进程,macOS 无 setsid (避开 Linux 习惯写法)
        _sp.Popen(
            ["bash", script],
            stdin=_sp.DEVNULL,
            stdout=open("/tmp/tuixue_tunnel_start.log", "a"),
            stderr=_sp.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        return envelope(error=f"启动 tunnel 失败: {e}")

    def _read_url_pair():
        """线程池里读 url + method 两个文件,避免小 IO 卡 event loop。"""
        url, method = "", ""
        if os.path.exists(url_file):
            try:
                url = open(url_file, encoding="utf-8").read().strip()
            except Exception:
                pass
        if os.path.exists(method_file):
            try:
                method = open(method_file, encoding="utf-8").read().strip()
            except Exception:
                pass
        return url, method

    # 阻塞轮询 url_file — 最多 65s, 每 1s 看一次
    deadline = time.time() + 65
    while time.time() < deadline:
        await asyncio.sleep(1)
        url, method = await to_thread(_read_url_pair)
        if url:
            # 自动推 TG (放后台线程,不等结果)
            async def _push_tg_bg():
                tg_sent, tg_err = False, ""
                try:
                    port = int(os.environ.get("TUIXUE_PORT", "7799"))
                    lan_ip = await to_thread(_get_lan_ip)
                except Exception:
                    lan_ip = "127.0.0.1"; port = 7799
                lines = [
                    "📡 退学 v3 · 外网入口", "",
                    f"🌐 公网 URL: {url}", "",
                    f"🔧 隧道方法: {method or 'unknown'}",
                    f"🏠 局域网: http://{lan_ip}:{port}/",
                    f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
                    "iPhone 浏览器直接打开公网 URL。临时隧道约 24h 后失效。",
                ]
                def _send():
                    from ..lib_common import send_telegram as _stg
                    return _stg("\n".join(lines), parse_mode="text", silent=True)
                try:
                    tg_sent = await to_thread(_send)
                    if not tg_sent:
                        tg_err = "send_telegram 返回 False (api.telegram.org 可能被 DNS 拦截)"
                except Exception as e:
                    tg_err = str(e)
                log.info(f"tunnel 启动成功 url={url} method={method} tg_sent={tg_sent}")
                return tg_sent, tg_err
            # 起 TG 后台任务,不等,先返回 url 给前端
            tg_task = asyncio.create_task(_push_tg_bg())
            tg_task.add_done_callback(lambda t: (None if t.exception() is None else log.warning(f"tg bg: {t.exception()}")))
            return envelope(data={
                "ok": True, "url": url, "method": method,
                "tg_pending": True,
                "elapsed_sec": round(65 - (deadline - time.time()), 1),
            })

    # ── B5: 全路失败告警推 TG (后台,不阻塞返错) ──
    async def _notify_failure_bg():
        def _send():
            try:
                from ..lib_common import send_telegram as _stg
                lines = [
                    "⚠️ 退学 v3 · tunnel 全部失败", "",
                    "65s 内 6 路 tunnel 全部超时（DNS 劫持到 198.18.x 是常见原因）",
                    f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "建议：检查 ~/.hermes/.env 网络配置 或稍后重试。",
                ]
                return _stg("\n".join(lines), parse_mode="text", silent=True)
            except Exception as e:
                log.warning(f"tunnel failure notify TG error: {e}")
                return False
        return await to_thread(_send)
    fail_task = asyncio.create_task(_notify_failure_bg())
    fail_task.add_done_callback(lambda t: (None if t.exception() is None else log.warning(f"tunnel fail bg: {t.exception()}")))

    return envelope(data={
        "ok": False,
        "error": "65s 内 6 路 tunnel 全部失败（网络层 DNS 劫持到 198.18.x + TLS 阻断是常见原因）。请检查 ~/.hermes/.env 网络配置或稍后重试。",
    })


def _tunnel_health_check_sync(url: str, timeout: float = 4.0) -> bool:
    """HEAD /api/health — 假活 (URL 写出但 tunnel 已死) 检测。3s 超时。"""
    try:
        import urllib.request as _ur
        req = _ur.Request(url.rstrip("/") + "/api/health", method="HEAD")
        with _ur.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


# P1-5 · HEAD 探测只在「真直连代理」域名上跑。ntfy/mqtt/tg-bot 是 sentinel 通道,
# HEAD 它们自身首页不会返 ok=true,会误判假活 → 多余自愈。集中列表 = 信任名单。
_TUNNEL_DIRECT_PROXY_SUFFIXES = (
    ".trycloudflare.com",
    ".ngrok-free.dev",
    ".ngrok.io",
    ".lhr.life",
    ".lhr.rocks",
    ".serveousercontent.com",
    ".ts.net",
    ".localhost.run",
    "127.0.0.1",
    "localhost:",
)


def _is_direct_proxy_url(url: str) -> bool:
    """只对「真代理到本机 server 的直连 URL」跑 HEAD, 其余(ntfy/mqtt/sentinel)直接视为存活。"""
    if not url:
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    try:
        from urllib.parse import urlparse as _up
        h = _up(url).hostname or ""
        for sfx in _TUNNEL_DIRECT_PROXY_SUFFIXES:
            if sfx.startswith(".") and h.endswith(sfx):
                return True
            if sfx in (h, h + ":" + str(_up(url).port or "")):
                return True
        return False
    except Exception:
        return False


def _tunnel_heal_one_attempt(timeout_sec: float = 60.0) -> tuple[bool, str, str]:
    """spawn start_tunnel_only.sh 一次, 等 URL 写出, 最长 timeout_sec。返 (ok, url, method)"""
    import subprocess as _sp

    script = os.path.join(os.path.dirname(__file__), "start_tunnel_only.sh")
    if not os.path.exists(script):
        return False, "", ""
    url_f, method_f, _ = _tunnel_files()

    # 先清旧 URL 文件 + 5min 冷却 (避免和手动 start 互相打架)
    global _last_tunnel_start_ts
    _last_tunnel_start_ts = time.time()
    for fp in (url_f, method_f):
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass

    try:
        _sp.Popen(
            ["bash", script],
            stdin=_sp.DEVNULL,
            stdout=open("/tmp/tuixue_tunnel_start.log", "a"),
            stderr=_sp.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        log.warning(f"[tunnel-heal] spawn failed: {e}")
        return False, "", ""

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(1.0)
        url, method = _read_tunnel_url_pair()
        if url and (url.startswith("http://") or url.startswith("https://")):
            return True, url, method
    return False, "", ""


async def _tunnel_heal_loop() -> None:
    """P1-5 · tunnel 自愈后台 task — 30s 一轮。

    触发条件 (debounce 5s):
      - url 文件缺失 (state=offline) 持续 ≥5s
      - url 还在但 HEAD /api/health 不通 (假活) 持续 ≥5s

    行为:
      - 单轮尝试 1 次 spawn (60s 内拿 URL 即可), 失败 attempts++
      - 成功 → reset attempts + 记 method + TG 轻量通知
      - 连续 3 次失败 → TG 推失败详情, paused_until + 30min 后再试
    """
    s = _tunnel_heal_state
    while True:
        try:
            # 1) 暂停期跳过 (避免 spam)
            if time.time() < s["paused_until"]:
                await asyncio.sleep(30)
                continue

            url, method = await to_thread(_read_tunnel_url_pair)
            healthy = False
            if _is_direct_proxy_url(url):
                # HEAD 探测 (4s 超时, 不阻塞太久) — 只在直连代理域跑
                healthy = await to_thread(_tunnel_health_check_sync, url, 4.0)
                if healthy:
                    s["last_health_ok_ts"] = time.time()
                    if method and method != s["last_method"]:
                        s["last_method"] = method
            elif url:
                # 哨兵类 URL (ntfy/mqtt/tg-bot) — 文件存在即视为存活, 不做 HEAD
                s["last_health_ok_ts"] = time.time()

            # 2) 30s 间隔
            await asyncio.sleep(30)

            # 3) 判定 — 需要自愈?
            url2, method2 = await to_thread(_read_tunnel_url_pair)
            healthy2 = False
            if _is_direct_proxy_url(url2):
                healthy2 = await to_thread(_tunnel_health_check_sync, url2, 4.0)
                if healthy2:
                    s["last_health_ok_ts"] = time.time()
                    s["attempts"] = 0
            elif url2:
                healthy2 = True   # 哨兵类 URL, 文件存在 = 存活
                s["last_health_ok_ts"] = time.time()
                s["attempts"] = 0

            if healthy2:
                continue   # 真活, 跳到下轮

            # 不健康 (无 url 或 url 不通)
            # debounce: 5s 内重启中 → skip
            now = time.time()
            if now - s["last_heal_at"] < 5:
                continue
            # 已 spawn 但还没来 → skip (start_tunnel_only 内部要 ~25s 出 url)
            if url2 and not method2:
                # url 已写但 method 没写 → 脚本还在跑中等最后一步
                continue

            # 4) 触发自愈
            log.info(f"[tunnel-heal] 不健康 (url={url2 or '<'} healthy={healthy2}) → 启动自愈, 第 {s['attempts']+1} 次")
            s["last_heal_at"] = now
            s["attempts"] += 1
            try:
                ok, new_url, new_method = await to_thread(_tunnel_heal_one_attempt, 60.0)
            except Exception as e:
                log.warning(f"[tunnel-heal] attempt 调用异常: {e}")
                ok, new_url, new_method = False, "", ""

            if ok and new_url:
                # 成功 — TG 通知 (静默, 只推一次)
                s["attempts"] = 0
                if new_method:
                    s["last_method"] = new_method
                log.info(f"[tunnel-heal] ✓ 自愈成功 url={new_url} method={new_method}")
                try:
                    def _send():
                        lines = [
                            "🔧 退学 v3 · tunnel 自愈成功", "",
                            f"🌐 新 URL: {new_url}",
                            f"🛠 机制: {new_method or '?'}",
                            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        ]
                        from ..lib_common import send_telegram as _stg
                        return _stg("\n".join(lines), parse_mode="text", silent=True)
                    await to_thread(_send)
                except Exception as e:
                    log.debug(f"[tunnel-heal] TG 推送成功通知失败 (non-fatal): {e}")
            else:
                # 失败 — 第 N 次
                log.warning(f"[tunnel-heal] ✗ 第 {s['attempts']} 次自愈失败")
                if s["attempts"] >= 3:
                    # 兜底告警 — 推 TG + 暂停 30min
                    s["paused_until"] = time.time() + 1800
                    log.warning(f"[tunnel-heal] ✗ 连续 3 次失败 → 暂停 30min + TG 告警")
                    try:
                        def _send_fail():
                            lines = [
                                "⚠️ 退学 v3 · tunnel 自愈失败", "",
                                f"已连续 3 次 (共 ~3 分钟) 重连失败",
                                f"机制优先: {s['last_method'] or '(未指定,按 start_tunnel_only 默认顺序)'}",
                                "可能原因: 全局 DNS 劫持 / 运营商封端口",
                                "恢复方式: 等 30min 后再试,或手动 /api/tunnel/stop + /api/tunnel/start",
                                f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                            ]
                            from ..lib_common import send_telegram as _stg
                            return _stg("\n".join(lines), parse_mode="text", silent=False)
                        await to_thread(_send_fail)
                    except Exception as e:
                        log.debug(f"[tunnel-heal] TG 失败告警推送失败 (non-fatal): {e}")
                    s["attempts"] = 0   # 重置, paused_until 控制频率
        except asyncio.CancelledError:
            log.info("[tunnel-heal] task 被取消 (server 退出)")
            return
        except Exception as e:
            log.warning(f"[tunnel-heal] loop 异常: {e}")
            await asyncio.sleep(30)


@app.post("/api/tunnel/stop")
async def tunnel_stop():
    """停掉所有 tunnel 进程（不动 server）。三种机制都杀: cloudflared / ngrok / ssh-reverse."""
    import os
    import subprocess as _sp
    global _last_tunnel_start_ts
    try:
        port = int(os.environ.get("TUIXUE_PORT", "7799"))
        for pat in (f"cloudflared tunnel --url",
                    f"ngrok http {port}",
                    f"ssh -tt -R 80:localhost:{port}"):
            _sp.Popen(["pkill", "-f", pat], stdin=_sp.DEVNULL,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        # 清 URL 文件 + 重置启动冷却,让"重启"能立即 start
        _last_tunnel_start_ts = 0.0
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

    lan_ip = _get_lan_ip()
    port = int(os.environ.get("TUIXUE_PORT", "7799"))

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


# R-sec-003: 删/去重旧的 /api/metrics — 之前的版本 659 是 cache+AI+DB+poller 聚合,
# 1167 是 endpoint counter. FastAPI 留后者导致聚合版永久 orphaned,前端 debug 面板只看到
# endpoint counter. 现聚合版 (line 659) 是唯一来源, 加 endpoints 维度合并进同一 response.
# (下面端点补到 659 的聚合版里)
@app.get("/api/ai/metrics")
async def ai_metrics():
    """AI 调用指标(R9)— 每个 call site 的 calls/ok/fail/retries/parse_fail/latency

    帮助定位: 1) 哪个 AI 端点延迟高 2) 是否频繁解析失败 3) 熔断状态。
    """
    from . import ai_client
    return envelope(data=ai_client.get_metrics())


@app.post("/api/admin/reset_sources")
async def reset_sources(request: Request):
    """
    重置所有数据源冷却状态 - 解决「连续失败 → 5 分钟冷却 → 全源被禁用 → screen 超时」的死循环。
    用法: curl -X POST http://localhost:7799/api/admin/reset_sources
    """
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头(从 env TUIXUE_ADMIN_TOKEN 读)", status_code=401)
    from tuixue_v3 import lib_common as lc
    result = lc.reset_source_health()
    log.info(f"🔄 数据源冷却已重置: {result}")
    return envelope(data=result)


# ───────────────────────────────────────────────────────────
# 退学心法 · 42 条铁律
# ───────────────────────────────────────────────────────────
@app.get("/api/laws")
async def laws_endpoint(request: _Request):
    """42 条铁律 + 4 大类 + 合规审计。前端 laws view 与 AI 复盘共用同一源。"""
    from .. import laws as _laws
    return json_etag_response(request, envelope(data={
        "categories": _laws.CATEGORIES,
        "koujue": _laws.KOUJUE_TEXT,
        "audit": _laws.AUDIT,
        "flat": _laws.flat_laws(),
        "summary": _laws.summary(),
    }), max_age=300)


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


def _normalize_quote(q: dict | None) -> dict:
    """统一 quote 字段 — 不同上游字段名不一致,前端不用记所有别名。

    上游字段映射（2026-07-12 整合）:
      - 价格:  最新价 / price
      - 涨跌:  涨跌幅 / change_pct  (单位 %)
      - 涨跌额:涨跌额 / chg_amt
      - 昨收:  昨收 / prev_close
      - 今开:  今开 / open
      - 最高:  最高 / high
      - 最低:  最低 / low
      - 成交量:成交量 / volume  (手)
      - 成交额:成交额 / amount  (元)
      - 换手率:换手率 / turnover_rate  (%)
      - 量比:  量比 / volume_ratio
      - 振幅:  振幅 / amplitude  (%)
      - PE:    市盈率 / 市盈率-动态 / pe  (>0 = 正常;<0 = 亏损;None = 暂无)
      - PB:    市净率 / pb
      - 总市值:总市值 / total_mcap  (亿 — 注意东财原字段是元,需要/1e8)
      - 流通市值:流通市值 / circ_mcap (亿)
    返回标准化后的 dict,带 _normalized=True 标记。
    """
    if not q:
        return {}
    out = dict(q)

    def _first(*keys):
        for k in keys:
            v = out.get(k)
            if v not in (None, "", "-", "None"):
                try:
                    fv = float(v)
                    return fv
                except (TypeError, ValueError):
                    continue
        return None

    # 价格
    if not out.get("price"):
        v = _first("最新价")
        if v is not None:
            out["price"] = v
    # 涨跌幅
    if out.get("change_pct") in (None, ""):
        v = _first("涨跌幅")
        if v is not None:
            out["change_pct"] = v
    # 涨跌额
    if out.get("chg_amt") in (None, ""):
        v = _first("涨跌额")
        if v is not None:
            out["chg_amt"] = v
    # OHLC
    for src, dst in [("昨收", "prev_close"), ("今开", "open"), ("最高", "high"), ("最低", "low")]:
        if out.get(dst) in (None, ""):
            v = _first(src)
            if v is not None:
                out[dst] = v
    # 量 / 额
    for src, dst in [("成交量", "volume"), ("成交额", "amount")]:
        if out.get(dst) in (None, ""):
            v = _first(src)
            if v is not None:
                out[dst] = v
    # 换手率
    if out.get("turnover_rate") in (None, ""):
        v = _first("换手率")
        if v is not None:
            out["turnover_rate"] = v
    # 量比
    if out.get("volume_ratio") in (None, ""):
        v = _first("量比")
        if v is not None:
            out["volume_ratio"] = v
    # 振幅
    if out.get("amplitude") in (None, ""):
        v = _first("振幅")
        if v is not None:
            out["amplitude"] = v
    # PE — 支持 市盈率 / 市盈率-动态 / pe
    if out.get("pe") in (None, ""):
        v = _first("市盈率-动态", "市盈率", "pe")
        if v is not None:
            out["pe"] = v
    # PB
    if out.get("pb") in (None, ""):
        v = _first("市净率", "pb")
        if v is not None:
            out["pb"] = v
    # 市值 — 东财返回元 → 亿
    for src, dst in [("总市值", "total_mcap"), ("流通市值", "circ_mcap")]:
        if out.get(dst) in (None, ""):
            v = _first(src)
            if v is not None:
                # 东财返回元(数量级 1e10),THS/腾讯返回亿(数量级 1e2-1e4)
                out[dst] = round(v / 1e8, 2) if abs(v) > 1e6 else round(v, 2)

    out["_normalized"] = True
    return out


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
_global_sentiment_sf = SingleFlight()
_mainlines_sf = SingleFlight()


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
            to_thread(_global_sentiment_sf.run, ("global_sentiment",), _do_fetch),
            timeout=12,
        )
    except asyncio.TimeoutError:
        log.warning("global_sentiment 超时 12s")
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
    fetch_time = rt.get("时间") or ""
    data_date = ""
    if fetch_time and len(str(fetch_time)) >= 8:
        s = str(fetch_time)
        data_date = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return {
        "code": code,
        "name": name,
        "price": _safe_float(rt.get("最新价") or rt.get("price")),
        "change_pct": _safe_float(rt.get("涨跌幅") or rt.get("change_pct")),
        "_source": rt.get("_source", ""),
        "_fetch_time": fetch_time,
        "data_date": data_date,
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

    # 4) 韩股 — KOSPI (KS11) — 优先复用 gm_data["indices"] (上面 fetch_global_sentiment
    #    已经并发抓过),只在没拿到时再走 _fetch_one (naver 缓存 120s)。2026-07-14 优化:
    #    旧版再调一次 naver 顺序拉 ~5s,dashboard 总耗时直接撞 25s 超时,UI 韩股永远 0.00%。
    kr_pct = 0.0
    kr_verdict = "cautious"
    kr_source = ""
    kr_err = ""
    kr_idx = next((i for i in (gm_data.get("indices") or [])
                  if i.get("code") == "KS11"), None)
    if kr_idx and kr_idx.get("change_pct") is not None:
        kr_pct = _safe_float(kr_idx.get("change_pct"))
        kr_source = kr_idx.get("source", "")
        kr_verdict = _verdict_from_pct(kr_pct, allow=0.5, block=-0.5)
    else:
        try:
            from . import global_markets as gm
            if hasattr(gm, "_fetch_one"):
                kr_data = gm._fetch_one("KS11", "kr") or {}
                if kr_data:
                    kr_pct = _safe_float(kr_data.get("change_pct") or kr_data.get("涨跌幅"))
                    kr_verdict = _verdict_from_pct(kr_pct, allow=0.5, block=-0.5)
                    kr_source = kr_data.get("source", "")
                else:
                    kr_err = "naver/yahoo/em 三源均未通"
        except Exception as e:
            log.warning(f"dashboard KOSPI 拉取失败: {e}")
            kr_err = str(e)
    if kr_err:
        kr_headline = f"KOSPI {kr_pct:+.2f}% · {kr_err}"
    else:
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
            "data_date": _pick_data_date(a_indices, gm_data.get("a_share_date")),
        },
        "kr": {
            "verdict": kr_verdict,
            "change_pct": kr_pct,
            "headline": kr_headline,
            "warnings": [],
            "data_date": gm_data.get("kr_date", ""),
        },
        "us": {
            "verdict": us_verdict,
            "change_pct": us_idx_pct,
            "sentiment": us_sent,
            "headline": us_headline,
            "warnings": us_warnings,
            "data_date": gm_data.get("us_date", ""),
        },
        "ts": time.time(),
    }


def _pick_data_date(indices: list[dict], fallback: str = "") -> str:
    """从指数列表中找第一个有 data_date 的,优先取最大(最近的)。
    用户反馈 (2026-07-11): 首页三市场没显示数据日期,休市日尤其需要。"""
    dates = [i.get("data_date") for i in (indices or []) if i.get("data_date")]
    if not dates:
        return fallback
    return max(dates)


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
        from .sector_taxonomy import MAINLINE_ZT_THRESHOLD, classify_sector_name
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

        # 3) 综合排序: 涨停数 × 3 + 资金净流入(亿) + 涨幅% × 0.2
        # 用户反馈 (2026-07-11): "按涨停数 + 资金净流入" 排序不准
        # fetch_hot_sectors 字段: net_inflow (THS 单位: 亿), change_pct (单位: %)
        # 东财主源 net_inflow 是元(÷1e8);THS 兜底已经是亿 — 看来源决定
        def _inflow_yi(s: dict) -> float:
            ni = float(s.get("net_inflow") or 0)
            # THS 兜底值是亿量级(<1e4);东财主源是元量级(>1e6)
            return ni / 1e8 if abs(ni) > 1e5 else ni

        def _score(s: dict) -> float:
            zt = zt_count_by_sector.get(s.get("name") or "", 0)
            inflow = _inflow_yi(s)
            pct = float(s.get("change_pct") or s.get("涨跌幅") or 0)
            return zt * 3.0 + inflow + pct * 0.2

        sectors.sort(key=_score, reverse=True)
        top5 = sectors[:5]
        total_zt = sum(zt_count_by_sector.values())

        tiles = []
        for i, s in enumerate(top5, start=1):
            tiles.append({
                "name": s.get("name") or "",
                "change_pct": float(s.get("change_pct") or s.get("涨跌幅") or 0),
                "net_inflow_yi": round(_inflow_yi(s), 2),
                "rank_flow": i,
                "zt_count": zt_count_by_sector.get(s.get("name") or "", 0),
                "taxonomy": classify_sector_name(s.get("name") or ""),
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
async def stock_kline(code: str, days: int = Query(120, ge=22, le=400)):
    code = code.strip().zfill(6)

    # P-perf: 优先读预计算K线缓存(含MA),跨进程共享
    try:
        from .. import cache_db
        pre = cache_db.daily().get_kline_pre(code, days)
        if pre is not None:
            return envelope(data={"code": code, "kline": pre, "_from": "pre_cache"})
    except Exception:
        pass

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
        # 计算 MA5/MA10/MA20/MA60 + 涨跌幅 + 量比
        closes = [r["close"] for r in rows]
        vols = [r["volume"] for r in rows]
        for i, r in enumerate(rows):
            r["ma5"]  = round(sum(closes[max(0,i-4):i+1]) / min(i+1, 5), 3) if i+1 else 0
            r["ma10"] = round(sum(closes[max(0,i-9):i+1]) / min(i+1, 10), 3) if i+1 else 0
            r["ma20"] = round(sum(closes[max(0,i-19):i+1]) / min(i+1, 20), 3) if i+1 else 0
            r["ma60"] = round(sum(closes[max(0,i-59):i+1]) / min(i+1, 60), 3) if i+1 else 0
            v5 = sum(vols[max(0,i-4):i+1]) / min(i+1, 5) if i+1 else 0
            r["vol_ratio_5d"] = round(r["volume"] / v5, 2) if v5 else 0
        # 回写预计算缓存
        try:
            from .. import cache_db as _cdb
            _cdb.daily().set_kline_pre(code_, days_, rows)
        except Exception:
            pass
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
    8 类席位分类 (5 大类 + 游资 3 档) + 资金占比 + 风险/积极信号 + 短线筛选标签.
    复用 seat_lookup.get_stock_seats + fund_flow.get_main_flow.
    ?fresh=1 — 不读缓存,重新跑 8 类分类 (2026-07-12 字典升级后页面强制刷新用)

    返回 categories[].seats[] 含 {alias, style, positive, warning, tier} —
    按用户字典 §五/§六 全部席位都挂 metadata.
    """
    from . import seat_classify
    code = code.strip().zfill(6)
    _touch_recent(code)
    _empty = {"code": code, "rows": [], "all_rows_count": 0, "last_date": None,
              "categories": [], "total_amount_wan": None,
              "intraday": {}, "risks": [], "signals": {"positive": [], "warning": []}, "tags": []}
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

        # 2) 5 日日线(本地 cache) — 优先 _130,缺则 _400 兜底
        # server.py 在 web/ 里,回退两级到 tuixue_v3/,再加 cache/
        repo_root = Path(__file__).resolve().parent.parent
        cache_dir = repo_root / "cache"
        daily_dict = {}
        for sub in (f"daily_{code}_130.json", f"daily_{code}_400.json"):
            cp = cache_dir / sub
            if cp.exists():
                try:
                    with cp.open() as f:
                        rows = json.load(f)
                    daily_dict = {r["日期"]: r for r in rows if "日期" in r}
                    break
                except Exception as e:
                    log.warning(f"读 daily cache 失败: {e}")

        # 3) 5 日涨停池(拿封成比/封单/连板) — 并行,5×200ms → ~300ms
        seal_by_date = {}
        def _pool_one(d):
            d_compact = d.replace("-", "")
            try:
                return d, msf.fetch_zt_pool(d_compact) or []
            except Exception as e:
                log.warning(f"涨停池拉取失败 {d}: {e}")
                return d, []
        from concurrent.futures import ThreadPoolExecutor as _TPE
        try:
            with _TPE(max_workers=len(recent5) or 1) as _p:
                pool_map = dict(_p.map(_pool_one, recent5))
        except Exception as e:
            log.warning(f"涨停池并行拉取异常: {e}")
            pool_map = {}
        for d in recent5:
            pool = pool_map.get(d, [])
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
                "high_5d":        round(max([r["high"] for r in rows_5 if r["high"]]), 2) if rows_5 and any(r["high"] for r in rows_5) else None,
                "low_5d":         round(min([r["low"]  for r in rows_5 if r["low"]]),  2) if rows_5 and any(r["low"]  for r in rows_5) else None,
            }

        # 5) 今日分时 tick — akshare 主源 + tencent 兜底 (DNS 劫持下 akshare 必挂)
        today_str = datetime.now().strftime("%Y-%m-%d")
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
                    "date": today_str,
                    "ticks": ticks,
                    "ticks_n": len(ticks),
                    "source": "akshare",
                }
        except Exception as e:
            log.warning(f"akshare 今日分时 tick 拉取失败: {e}")

        # akshare 失败 → tencent 1min 兜底(关键,沙箱 DNS 劫持环境必须)
        if not out.get("intraday_today") or not out["intraday_today"].get("ticks"):
            ten = _fetch_intraday_today_tencent_first(code)
            if ten and ten.get("ticks"):
                out["intraday_today"] = ten
            else:
                out["note"] = "今日分时未取到(akshare 断连, tencent 兜底失败)"

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

    # 4) efinance 5min K 兜底 (2026-07-16 新增)
    #    akshare + sina + tencent 三源全挂时启用,东财轻封装, threading + join 硬超时
    #    不带历史 date,只补今日 / 单日分时 → 仍可作为当日分钟 fallback
    try:
        import threading as _thr_ef
        ef_box = {"df": None, "err": ""}
        def _ef_run():
            try:
                import efinance as ef
                ef_box["df"] = ef.stock.get_quote_history(
                    code, beg=ymd, end=ymd, klt=5, fqt=1)
            except Exception as e:
                ef_box["err"] = f"{type(e).__name__}: {str(e)[:60]}"
        t_ef = _thr_ef.Thread(target=_ef_run, daemon=True)
        t_ef.start()
        t_ef.join(timeout=6)
        df_ef = ef_box["df"]
        if df_ef is not None and not df_ef.empty:
            ticks = []
            for _, r in df_ef.iterrows():
                t = str(r.get("时间", r.get("日期", "")))
                ticks.append({
                    "time":        t,
                    "price":       _safe_float(r.get("收盘", r.get("最新价"))),
                    "volume_hand": _safe_float(r.get("成交量")),
                    "amount":      _safe_float(r.get("成交额")),
                    "open":        _safe_float(r.get("开盘")),
                    "high":        _safe_float(r.get("最高")),
                    "low":         _safe_float(r.get("最低")),
                    "side":        "",
                })
            if ticks:
                out["ticks"] = ticks
                out["ticks_n"] = len(ticks)
                out["source"] = "efinance_5min"
                return out
        elif ef_box["err"]:
            log.info(f"efinance 5min 失败: {ef_box['err']}")
    except Exception as e:
        log.info(f"efinance 5min 兜底层异常: {e}")

    out["note"] = f"{date_str} 分时拉取失败(akshare + sina + tencent + efinance 四源全挂,可能是网络层 DNS 劫持或非交易日)"
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

    # 5 日并行:历史日期都走 sina 5min K,串行 5×300ms=1.5s → 并行 ~400ms
    from concurrent.futures import ThreadPoolExecutor as _TPE
    hist_dates = [d for d in recent5 if d != today_str]

    def _hist_one(d):
        try:
            return d, _fetch_intraday_for_date(code, d)
        except Exception as e:
            return d, {"ticks": [], "ticks_n": 0, "source": "", "note": str(e)[:60]}

    hist_results = {}
    if hist_dates:
        try:
            with _TPE(max_workers=len(hist_dates)) as _p:
                for d, r in _p.map(_hist_one, hist_dates):
                    hist_results[d] = r
        except Exception as e:
            log.warning(f"intraday_per_day 并行拉取失败: {e}")

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

        # 历史日期:从并行结果取
        sub = hist_results.get(d, {"ticks": [], "ticks_n": 0, "source": ""})
        if sub.get("ticks"):
            day_obj["ticks"] = sub["ticks"]
            day_obj["ticks_n"] = sub["ticks_n"]
            day_obj["source"] = sub["source"]
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
        # 2026-07-16: 用户反馈「新闻好像抓的不是最新的,最新的放在前面」→ 改 ctime 倒序
        # (AI 评分排序会把几天前的重磅新闻顶上来,但用户更在意时效性)
        out.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
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


@app.post("/api/limitup/per_code")
async def api_limitup_per_code(req: dict):
    """批量:每只个股对应的"今日同板块/产业链/细分 涨停股数"(多股性列多条)。

    Body: {"codes": ["000001", "600519", ...]}      limit ≤ 300 (一次最多 300 只,防滥用)
    返回: {"counts": {"000001": [{"chain": "电子", "level": "L2", "zt_count": 22,
               "is_mainline": true, "samples": ["北方华创", ...]}, ...], ...}, "ts": epoch}
    缓存 60s (今天涨停池 1 分钟内不会变,够用)。
    """
    from .sector_taxonomy import zt_chains_per_code
    from .sector_classify import get_sector
    codes_in = (req or {}).get("codes") or []
    codes = [str(c).strip().zfill(6) for c in codes_in if str(c).strip().isdigit() or str(c).strip().zfill(6).isdigit()]
    codes = [c for c in codes if len(c) == 6][:300]
    if not codes:
        return envelope(data={"counts": {}, "ts": time.time()})

    def _run():
        try:
            return zt_chains_per_code(codes, sector_lookup=get_sector)
        except Exception as e:
            log.warning(f"limitup_per_code 失败: {e}")
            return {}

    try:
        counts = await asyncio.wait_for(to_thread(_run), timeout=15)
    except asyncio.TimeoutError:
        log.warning(f"limitup_per_code 超时 15s (codes={len(codes)})")
        counts = {}
    return envelope(data={"counts": counts or {}, "ts": time.time(), "codes": len(codes)})


@app.get("/api/stock/{code}/related_news")
async def stock_related_news(code: str):
    """
    与个股相关的新闻(按 ctime 倒序,最新在前):
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
        matched.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
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
async def sectors_taxonomy(request: _Request):
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
    return json_etag_response(request, envelope(data={
        "clusters":   clusters,
        "threshold":  MAINLINE_ZT_THRESHOLD,
        "all_chains": sorted(ALL_CHAINS.keys()),
        "version":    "2026-07-11",
    }), max_age=600)


@app.get("/api/sectors/mainlines")
async def sectors_mainlines(force: bool = False):
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

    2026-07-14: 加 60s 缓存 (R4 提速 165x) — 主线一日内稳定,1min 重算足够
    """
    import time as _t
    from .sector_taxonomy import (
        detect_mainline, count_zt_by_chain, MAINLINE_ZT_THRESHOLD
    )
    from .sector_classify import get_sector
    from .. import data_layer as _dl

    _MAINLINES_CACHE = _cache_global  # 60s TTL

    if not force:
        cached = _MAINLINES_CACHE.get(("sectors_mainlines",))
        if cached is not None:
            cached["from_cache"] = True
            return envelope(data=cached)

    _sf_key = ("sectors_mainlines_sf",)

    def _calc():
        try:
            zt = _dl.fetch_limit_up_pool() or []
        except Exception:
            zt = []
        codes = [str(z.get("code") or "").zfill(6) for z in zt]
        ml = detect_mainline(zt_codes=codes, sector_lookup=get_sector, threshold=MAINLINE_ZT_THRESHOLD)
        chain_counts = count_zt_by_chain(codes, get_sector)
        return {"mainlines": ml, "chain_counts": chain_counts,
                "threshold": MAINLINE_ZT_THRESHOLD, "ts": _t.time()}

    try:
        result = await asyncio.wait_for(
            to_thread(_mainlines_sf.run, _sf_key, _calc), timeout=10,
        )
    except asyncio.TimeoutError:
        log.warning("sectors_mainlines 超时 10s")
        return envelope(error="主线拉取超时", data={
            "mainlines": [], "chain_counts": {}, "threshold": MAINLINE_ZT_THRESHOLD,
        })
    except Exception as e:
        log.warning(f"sectors_mainlines 失败: {e}")
        return envelope(error=f"主线拉取失败: {e}", data={
            "mainlines": [], "chain_counts": {}, "threshold": MAINLINE_ZT_THRESHOLD,
        })

    _MAINLINES_CACHE.set(("sectors_mainlines",), result)
    return envelope(data=result)


def _static_page_handler(filename: str):
    """统一静态页 handler:读取 web/static/{filename},带 ETag/file mtime 协商。"""
    async def _h():
        page_path = STATIC_DIR / filename
        if not page_path.is_file():
            return _Response(content=f"<h1>{filename} not found</h1>".encode(), status_code=404)
        try:
            st = page_path.stat()
            etag = _hashlib.md5(f"{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
        except Exception:
            etag = '"unknown"'
        body = page_path.read_bytes()
        return _Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-cache, must-revalidate",
                "ETag": etag,
            },
        )
    return _h


@app.get("/sector_hotspot", include_in_schema=False)
async def sector_hotspot_page():
    """热点龙头 — 2026-07-14 应用户要求删除,此 URL 仅 302 → / 避免书签失效。"""
    return _Response(status_code=302, headers={"Location": "/"})


# ═════════════════════════════════════════════════════════════════
# 全 A 风向 (个股全景) — 2026-07-12 用户反馈 #12
# ═════════════════════════════════════════════════════════════════
@app.get("/api/all_stocks/board")
async def api_all_stocks_board(
    limit:   int   = Query(0, ge=0, le=500),       # R-sec-005: 防止 0=无限 误用
    page_size: int = Query(0, ge=0, le=500),     # 同上
    offset:  int   = Query(0, ge=0, le=100_000),
    l1:      str   = "",
    l2:      str   = "",
    l3:      str   = "",
    l4:      str   = "",
    domain:  str   = "",
    sort:    str   = "amount",
    order:   str   = "desc",
    with_fund: bool = True,
):
    """个股全景快照 — 支持 offset/page_size 无限滚动。

    R16 (2026-07-13):
      - 前端按 visible*1.5 传 page_size,offset+=page_size 滚动追加
      - 后端按 (filter,sort,order) 缓存全量,offset 变化命中缓存切
      - 首次 cache miss 时 fetch_n = max(offset+ps+100, ps*3, 200), 给后续滚动预留
      - 返回 has_more / next_offset / total_available

    限频:
      - 首次单次 ~5s (fetch 200), 命中缓存切 <50ms
      - scroll 越界 (offset+ps > cached) 时下次 fetch 会扩到 ~10s
    """
    from . import all_stocks as _all

    def _load():
        try:
            return _all.board_snapshot(
                limit=int(limit or 0),
                page_size=int(page_size or 0),
                offset=int(offset or 0),
                l1=l1 or None,
                l2=l2 or None,
                l3=l3 or None,
                l4=l4 or None,
                domain=domain or None,
                sort=sort or "amount",
                order=order or "desc",
                with_fund=bool(with_fund),
            )
        except Exception as e:
            log.warning(f"all_stocks board 失败: {e}")
            return {"items": [], "count": 0, "error": str(e), "has_more": False, "next_offset": 0, "total_available": 0}

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=28)
    except asyncio.TimeoutError:
        log.warning(f"all_stocks board 超时 28s (ps={page_size}, off={offset}, l3={l3})")
        return envelope(
            error="批量行情超时 28s — 请缩小筛选或稍后再试",
            data={"items": [], "count": 0, "has_more": False, "next_offset": 0, "total_available": 0, "filters_used": {}},
        )
    return envelope(data=result)


@app.get("/api/all_stocks/filters")
async def api_all_stocks_filters():
    """4 层 + 领域 filter 全集 — 给前端 dropdown"""
    from . import all_stocks as _all

    def _load():
        return _all.filters_full()

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=4)
    except asyncio.TimeoutError:
        return envelope(error="filter 加载超时", data={"clusters": [], "industries": [], "chains": [], "l4": []})
    return envelope(data=result)


# 注: /all_stocks 已在 2026-07-14 迁入 index.html (view-all_stocks section),
#      旧独立 HTML 页删除,路由随之删除。前端侧栏 data-jump='all_stocks' 走 showView。

# ═════════════════════════════════════════════════════════════════
# 选股 (2026-07-13 · 8 规则 · 14:30 启 · SSE 推送)
#   - GET  /screener                → 静态页 (URL 不带 /api)
#   - GET  /api/screener/result     → 当前排序 + limit + 多字段排序
#   - GET  /api/screener/rule_status → 时间门 + 阈值 + 开关 + 快照日期
#   - POST /api/screener/thresholds → 用户编辑 7 项阈值
#   - POST /api/screener/toggles    → 用户切 7 个规则开关
#   - POST /api/screener/watchlist  → ⭐ 加入自选
#   - GET  /api/screener/history?date=YYYY-MM-DD → 列出该日所有快照点
#   - GET  /api/screener/replay?date=...&ts=... → 取具体快照
#   - GET  /api/screener/stream     → SSE 推送 (1s/帧)
#   - POST /api/screener/snapshot_now → 立即存一帧 (调试用)
# ═════════════════════════════════════════════════════════════════

@app.get("/screener", include_in_schema=False)
async def screener_page():
    """尾盘战法 — 2026-07-14 inline 进主 app /view-screener,此 URL 仅 302 → /#screener 避免书签失效。"""
    return _Response(status_code=302, headers={"Location": "/#screener"})


@app.get("/api/screener/rule_status")
async def api_screener_rule_status():
    try:
        from . import screener as _scr
        return envelope(data=_scr.rule_status_enriched())
    except Exception as e:
        return envelope(error=f"rule_status 失败: {e}")


@app.get("/api/screener/result")
async def api_screener_result(
    sort: str = "change_pct:desc",
    limit: int = 100,
    show_failed: bool = True,
    rules: str = "",   # 逗号分隔, eg. "change_pct,volume_ratio"
    mode: str = "multi",   # multi | simple
):
    try:
        from . import screener as _scr
        rule_list = [r.strip() for r in (rules or "").split(",") if r.strip()] or None
        if mode == "simple":
            # 拆出第一个 field + dir
            head = (sort or "change_pct:desc").split(",")[0].strip()
            if ":" in head:
                f, d = head.split(":", 1)
            else:
                f, d = head, "desc"
            data = _scr.current_results(sort=f, order=d, limit=limit, show_failed=show_failed)
        else:
            data = _scr.current_results_multi(
                sort_spec=sort, limit=limit, show_failed=show_failed, rule_filters=rule_list,
            )
        return envelope(data=data)
    except Exception as e:
        return envelope(error=f"result 失败: {e}")


class _ScreenerThresholdReq(BaseModel):
    key:   str
    value: float


@app.post("/api/screener/thresholds")
async def api_screener_thresholds(req: _ScreenerThresholdReq):
    try:
        from . import screener as _scr
        return envelope(data=_scr.set_threshold(req.key, req.value))
    except Exception as e:
        return envelope(error=f"thresholds 失败: {e}")


@app.post("/api/screener/reset-defaults")
async def api_screener_reset_defaults():
    """恢复所有阈值到出厂默认值"""
    try:
        from . import screener as _scr
        return envelope(data=_scr.reset_thresholds())
    except Exception as e:
        return envelope(error=f"reset-defaults 失败: {e}")


class _ScreenerToggleReq(BaseModel):
    rule: str
    on:   bool


@app.post("/api/screener/toggles")
async def api_screener_toggles(req: _ScreenerToggleReq):
    try:
        from . import screener as _scr
        return envelope(data=_scr.set_rule_toggle(req.rule, req.on))
    except Exception as e:
        return envelope(error=f"toggles 失败: {e}")


class _ScreenerStarReq(BaseModel):
    code: str
    name: str = ""
    tag:  str = "选股14:30"


@app.post("/api/screener/watchlist")
async def api_screener_watchlist(req: _ScreenerStarReq):
    """⭐ 加入自选 — 复用 watchlist.add()"""
    try:
        from . import watchlist as _wl
        r = _wl.add(req.code, req.name, tag=req.tag)
        return envelope(data=r)
    except Exception as e:
        return envelope(error=f"加自选失败: {e}")


@app.get("/api/screener/history")
async def api_screener_history(date: str = ""):
    try:
        from . import screener as _scr
        snapshots = _scr.list_snapshots(date or None)
        dates     = _scr.available_snapshot_dates()
        return envelope(data={"date": date or _scr._now_china().strftime("%Y-%m-%d"),
                              "count": len(snapshots), "snapshots": snapshots,
                              "available_dates": dates})
    except Exception as e:
        return envelope(error=f"history 失败: {e}")


@app.get("/api/screener/replay")
async def api_screener_replay(date: str = "", ts: float = 0):
    try:
        from . import screener as _scr
        snap = _scr.get_snapshot(date or None, ts)
        if not snap:
            return envelope(error="快照不存在", data={"items": [], "count": 0})
        return envelope(data={
            "ts":        snap.get("ts", 0),
            "iso":       snap.get("iso", ""),
            "ts_str":    snap.get("ts_str", ""),
            "items":     snap.get("items", []),
            "count":     snap.get("count", 0),
            "thresholds": snap.get("thresholds", {}),
            "toggles":    snap.get("toggles", {}),
            "is_replay": True,
        })
    except Exception as e:
        return envelope(error=f"replay 失败: {e}")


@app.post("/api/screener/snapshot_now")
async def api_screener_snapshot_now():
    try:
        from . import screener as _scr
        rec = _scr.save_snapshot(force=True)
        if not rec:
            return envelope(error="无数据可存 (_RESULT 为空)")
        return envelope(data={"ok": True, "ts": rec.get("ts"), "iso": rec.get("iso"), "count": rec.get("count")})
    except Exception as e:
        return envelope(error=f"snapshot 失败: {e}")


# R30: 重算候选池 — 用户按"⟳ 重算"按钮时真正重建
class _ScreenerRebuildReq(BaseModel):
    force: bool = True  # 默认强制重新拉行情，不走缓存

@app.post("/api/screener/rebuild")
async def api_screener_rebuild(req: _ScreenerRebuildReq = _ScreenerRebuildReq()):
    try:
        from . import screener as _scr
        _scr._schedule_rebuild(force=req.force)
        return envelope(data={"ok": True, "msg": "已提交后台重建 (1-30s 完成)"})
    except Exception as e:
        return envelope(error=f"rebuild 失败: {e}")


@app.get("/api/screener/stream")
async def api_screener_stream(_request: _Request):
    """SSE: 每秒推送 {status, payload}, 直到客户端断开"""
    from sse_starlette.sse import EventSourceResponse
    from . import screener as _scr

    async def gen():
        # 订阅 (B3: 用 helper 函数 + RLock 包裹,避免并发竞态)
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = _scr.subscribe()
        try:
            # 首帧: 优先用 last-known-good 缓存 (rebuild 期间避免空白)
            last = _scr.get_last_broadcast()
            if last:
                yield {"event": "init", "data": json.dumps(last, default=str)}
            else:
                init_status = _scr.rule_status_enriched()
                init_result = _scr.current_results_multi(sort_spec="change_pct:desc", limit=50)
                yield {"event": "init", "data": json.dumps({
                    "status": init_status,
                    "items":  init_result.get("items", []),
                    "ts":     init_result.get("ts", 0),
                }, default=str)}
            while True:
                if await _request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=2.0)
                    yield {"event": "tick", "data": json.dumps(payload, default=str)}
                except asyncio.TimeoutError:
                    # 心跳 (防止 CDN/proxy 切断)
                    yield {"event": "ping", "data": json.dumps({"ts": time.time()})}
        finally:
            _scr.unsubscribe(q)

    return EventSourceResponse(gen(), ping=15)



# ═════════════════════════════════════════════════════════════════
# 选股回测 (2026-07-14 · 一键回测 + 多窗口 + 9:30-10:00 S1/S2/S3 + open)
#   - POST /api/screener/backtest  body={"periods":[...]}  启动回测, 返 run_id
#   - GET  /api/screener/backtest?run_id=...  查状态 + 结果
#   - 同一 run_id 全局串行, 防 _EXECUTOR 并发打架
# ═════════════════════════════════════════════════════════════════
_BT_RUNS: dict[str, dict] = {}   # run_id → {status, progress, result, error, started_at, finished_at}
_BT_RUN_LOCK = threading.Lock()
_BT_RUN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bt-run")


class _BacktestReq(BaseModel):
    periods:           list[str] = []  # 子集 eg ["1周","1月"]; 空 = 跑默认
    hold_days:         int = 3         # 持仓天数 (次级 7 套退场对比)
    top_n:             int = 1         # 每日 Top N
    sample:            int = 1200      # 主板采样数 (0=全量, 慢)
    breadth_min:       int = 0         # 大盘红线 (硬底) — 全 A 红 < 该值 当天空仓 (0=禁用)
    breadth_min_soft:  int = 0         # 大盘软线 — 红盘介于 [硬, 软) 区间时只交易热门板块
    sector_hot_topn:   int = 0         # 热门板块 top N (软线叠加使用)
    sector_inflow_topn: int = 0        # 资金净流入板块 top N (amount_ratio 估)
    require_surge_label: bool = False  # 只选"次日大概率异动"标签


def _bt_period_resolver(periods: list[str]) -> list[str]:
    """接受别名 → WINDOWS 标签"""
    from . import backtest_screener as _bt
    aliases = {
        "1w": "1周", "2w": "2周", "1m": "1月", "2m": "2月", "6m": "半年",
        "1周": "1周", "2周": "2周", "1月": "1月", "2月": "2月", "半年": "半年",
    }
    out: list[str] = []
    for p in periods or []:
        k = aliases.get(p, p)
        if k and k in [w for w, _ in _bt.WINDOWS]:
            out.append(k)
    return out or [w for w, _ in _bt.WINDOWS]


def _bt_run_bg(run_id: str, period_keys: list[str], hold_days: int, top_n: int, sample: int,
               breadth_min: int = 0, breadth_min_soft: int = 0, sector_hot_topn: int = 0,
               sector_inflow_topn: int = 0, require_surge_label: bool = False) -> None:
    from . import backtest_screener as _bt
    try:
        def _cb(msg: str) -> None:
            with _BT_RUN_LOCK:
                if run_id in _BT_RUNS:
                    _BT_RUNS[run_id]["progress"] = msg
        r = _bt.run_for_frontend(
            period_keys,
            hold_days=hold_days,
            top_n=top_n,
            sample=sample,
            breadth_min=breadth_min,
            breadth_min_soft=breadth_min_soft,
            sector_hot_topn=sector_hot_topn,
            sector_inflow_topn=sector_inflow_topn,
            require_surge_label=require_surge_label,
            progress_cb=_cb,
        )
        with _BT_RUN_LOCK:
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id]["status"] = "done"
                _BT_RUNS[run_id]["result"] = r
                _BT_RUNS[run_id]["progress"] = "完成"
                _BT_RUNS[run_id]["finished_at"] = time.time()
    except Exception as e:
        log.exception(f"backtest {run_id} fail")
        with _BT_RUN_LOCK:
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id]["status"] = "error"
                _BT_RUNS[run_id]["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                _BT_RUNS[run_id]["progress"] = "出错"
                _BT_RUNS[run_id]["finished_at"] = time.time()


@app.post("/api/screener/backtest")
async def api_screener_backtest(req: _BacktestReq):
    """启动一次回测 (后台线程), 立即返 run_id"""
    period_keys = _bt_period_resolver(req.periods)
    run_id = f"bt-{int(time.time())}-{secrets.token_hex(3)}"
    with _BT_RUN_LOCK:
        if any(r.get("status") == "running" for r in _BT_RUNS.values()):
            return envelope(error="已有回测在跑, 请先等待完成", data={"running": True})
        _BT_RUNS[run_id] = {
            "status":      "running",
            "progress":    "排队中…",
            "periods":     period_keys,
            "started_at":  time.time(),
            "result":      None,
            "error":       None,
        }
    try:
        _BT_RUN_EXECUTOR.submit(
            _bt_run_bg,
            run_id,
            period_keys,
            max(1, min(req.hold_days, 10)),
            max(1, min(req.top_n, 5)),
            max(0, min(req.sample, 5000)),
            max(0, min(req.breadth_min, 5400)),
            max(0, min(req.breadth_min_soft, 5400)),
            max(0, min(req.sector_hot_topn, 50)),
            max(0, min(req.sector_inflow_topn, 50)),
            req.require_surge_label,
        )
    except Exception as e:
        with _BT_RUN_LOCK:
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id]["status"] = "error"
                _BT_RUNS[run_id]["error"] = str(e)
        return envelope(error=f"提交失败: {e}")
    return envelope(data={"ok": True, "run_id": run_id, "periods": period_keys,
                          "hold_days": req.hold_days, "top_n": req.top_n, "sample": req.sample,
                          "breadth_min": req.breadth_min,
                          "breadth_min_soft": req.breadth_min_soft,
                          "sector_hot_topn": req.sector_hot_topn,
                          "sector_inflow_topn": req.sector_inflow_topn,
                          "require_surge_label": req.require_surge_label})


@app.get("/api/screener/backtest")
async def api_screener_backtest_status(run_id: str = ""):
    """查询回测状态 + 结果"""
    with _BT_RUN_LOCK:
        r = _BT_RUNS.get(run_id)
        if not r:
            return envelope(error="run_id 不存在或已清理", data={"status": "missing"})
        out = {
            "status":      r.get("status"),
            "progress":    r.get("progress", ""),
            "periods":     r.get("periods", []),
            "started_at":  r.get("started_at", 0),
            "finished_at": r.get("finished_at", 0),
            "elapsed_sec": round((r.get("finished_at") or time.time()) - r.get("started_at", time.time()), 1),
            "error":       r.get("error"),
            "result":      r.get("result"),
        }
    return envelope(data=out)


@app.get("/api/sector/{name}")
async def api_sector(name: str):
    """板块详情 — 今日涨停 + 角色分布 + 近 5 日高发 zt
    接受板块名 (URL-encoded Chinese)，模糊匹配 hot_sectors 返回的那条。"""
    from .sector_classify import get_sector
    from .. import multi_source_fetchers as msf
    from .. import data_layer as _dl
    import urllib.parse
    import time as _t

    needle = urllib.parse.unquote(name).strip()
    if not needle:
        return envelope(error="empty sector name", data={"info": None})

    def _match(n):
        a = n.get("name", "")
        return a == needle or a in needle or needle in a

    def _load():
        try:
            hot = msf.fetch_hot_sectors(top_n_flow=40, top_n_pct=40) or []
        except Exception as e:
            log.warning(f"sector 拉 hot_sectors 失败: {e}")
            hot = []
        info = next((s for s in hot if _match(s)), None)
        if not info:
            return {"info": None, "need": needle}

        matched = info["name"]

        # 1) 今日 zt pool → 按 taxonomy.level2_sw / level3_chain 匹配
        try:
            zt = _dl.fetch_limit_up_pool() or []
        except Exception as e:
            log.warning(f"sector 拉 zt_pool 失败: {e}")
            zt = []

        today_zt = []
        for z in zt:
            code = str(z.get("code") or "").zfill(6)
            sec = get_sector(code) or {}
            tax = sec.get("taxonomy", {}) or {}
            chain = tax.get("level3_chain", "") or ""
            sw = tax.get("level2_sw", "") or ""
            # 严格匹配: sw 或 chain 精确等于板块名, 或 matched 是 chain 的子板块 (前缀 + 间隔符)
            ok = (sw == matched) or (chain == matched) \
                 or (chain and matched.startswith(chain) and len(matched) > len(chain)
                     and matched[len(chain)] in "·-— ")
            if not ok:
                continue
            today_zt.append({
                    "code":      code,
                    "name":      z.get("name", "") or sec.get("name", ""),
                    "time":      z.get("first_zt_time") or z.get("zt_time") or z.get("time", ""),
                    "streak":    int(z.get("streak") or z.get("limit_count") or 0),
                    "seal_pct":  z.get("seal_ratio_pct") or z.get("seal_pct"),
                    "sub_chain": chain,
                    "sw":        sw,
                })

        # 2) 角色分布 (基于今日 zt + 连板)
        role = {"main": 0, "second": 0, "noise": 0}
        for z in today_zt:
            streak = z["streak"]
            if streak >= 3:
                role["main"] += 1
            elif streak >= 2:
                role["second"] += 1
            else:
                role["noise"] += 1

        # 3) 近 5 日高发 zt (按 code 聚合, 过滤同 sector)
        try:
            recent = msf.fetch_recent_zt_pool(days=5) or {}
        except Exception as e:
            log.warning(f"sector 拉 recent_zt 失败: {e}")
            recent = {}
        hot_5d = []
        for code, info_r in recent.items():
            sec_name = info_r.get("sector", "")
            if sec_name and (matched in sec_name or sec_name in matched):
                hot_5d.append({
                    "code":         code,
                    "name":         info_r.get("name", ""),
                    "zt_count":     info_r.get("zt_count", 0),
                    "total_streak": info_r.get("total_streak", 0),
                    "last_date":    info_r.get("last_date", ""),
                })
        hot_5d.sort(key=lambda x: (x["zt_count"], x["total_streak"]), reverse=True)

        return {
            "info": {
                "code":         info.get("code", ""),
                "name":         matched,
                "change_pct":   info.get("change_pct", 0),
                "net_inflow_yi":info.get("net_inflow", 0),
                "rank_flow":    info.get("rank_flow"),
                "rank_pct":     info.get("rank_pct"),
                "zt_count":     len(today_zt),
            },
            "today_zt":    today_zt,
            "role_dist":   role,
            "hot_5d":      hot_5d[:20],
            "ts":          _t.time(),
        }

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=15)
        if not result.get("info"):
            return envelope(error=f"未匹配板块: {result.get('need', needle)}", data={"info": None})
        return envelope(data=result)
    except asyncio.TimeoutError:
        return envelope(error="sector 加载超时", data={"info": None})
    except Exception as e:
        return envelope(error=f"sector 失败: {e}", data={"info": None})


@app.get("/api/stock/{code}")
async def stock_overview(
    code: str,
    fresh: int = Query(0, ge=0, le=1),
    date: str = Query("", description="YYYY-MM-DD 或 YYYYMMDD; 空=今日/最新交易日"),
):
    """个股综合数据。
    ?fresh=1 — 失效 quote / fund_flow 缓存,进页面必拿最新
    ?date=YYYY-MM-DD — 历史快照模式 (2026-07-11 用户反馈日期切换不生效):
      - 实时接口无法回放 → 用 kline 当日 bar 构造伪 quote
      - fund_flow.today = None
      - fund_flow.history / seats 全部截断到 <=date
      - 返回 is_historical=true 让前端加"历史快照"标签
    """
    code = code.strip().zfill(6)
    _touch_recent(code)

    # ─── 解析 date 参数 + 判断是否历史快照模式 ───
    target_date = ""
    is_historical = False
    if date:
        d_norm = date.strip().replace("/", "-")
        if len(d_norm) == 8 and d_norm.isdigit():
            d_norm = f"{d_norm[:4]}-{d_norm[4:6]}-{d_norm[6:8]}"
        target_date = d_norm
        try:
            today_yyyymmdd = datetime.date.today().strftime("%Y-%m-%d")
            from .. import multi_source_fetchers as msf
            all_dates = msf.fetch_trade_dates()
            past = [d for d in all_dates if d <= today_yyyymmdd]
            last_trade = max(past) if past else today_yyyymmdd
            # 选了"今天"或"最近交易日"都按实时走;其他算历史
            is_historical = bool(target_date) and target_date < last_trade
        except Exception:
            is_historical = bool(target_date) and target_date < datetime.date.today().strftime("%Y-%m-%d")

    if fresh or is_historical:
        # 历史模式 cache key 必须含 date,否则切日期拿到旧缓存
        _cache_quote.invalidate(("quote", code))
        _cache_quote.invalidate(("quote_hist", code, target_date or "today"))
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

    if is_historical:
        # 历史快照:拉 kline → 用当日 bar 构造 quote;fund_flow/seats 截断到 date
        def _hist_snapshot(code_, cutoff_date):
            k = stock_kline_loader(code_, 250) or []
            k.sort(key=lambda r: r.get("date") or "")
            # 找 <= cutoff_date 最后一行 bar
            bar = None
            for row in reversed(k):
                rd = str(row.get("date") or "")[:10]
                if rd <= cutoff_date:
                    bar = row
                    break
            if not bar:
                return {}, []
            # 构造伪 quote 字段 (字段名跟 fetch_realtime 对齐)
            prev_c = 0
            for row in k:
                rd = str(row.get("date") or "")[:10]
                if rd < cutoff_date:
                    prev_c = float(row.get("close") or 0)
            op = float(bar.get("open") or 0)
            cl = float(bar.get("close") or 0)
            hi = float(bar.get("high") or 0)
            lo = float(bar.get("low") or 0)
            vol = float(bar.get("volume") or 0)
            amt = float(bar.get("amount") or 0)
            chg = (cl - prev_c) / prev_c * 100 if prev_c else 0
            # 从 hist_flow 找那天的 main_net (仅显示参考)
            main_net = None
            try:
                hf = fund_flow.get_history_flow(code_, 60)
                for r in hf:
                    if str(r.get("date", ""))[:10] == cutoff_date:
                        main_net = r.get("main_net")
                        break
            except Exception:
                pass
            pseudo = {
                "最新价": cl, "今开": op, "最高": hi, "最低": lo, "昨收": prev_c,
                "成交量": vol, "成交额": amt, "涨跌幅": round(chg, 2),
                "涨跌额": round(cl - prev_c, 2) if prev_c else 0,
                "换手率": None, "市盈率-动态": None, "量比": None,
                "总市值": None, "流通市值": None,
                "name": code_, "code": code_,
                "is_historical": True, "snapshot_date": cutoff_date,
                "main_net_proxy": main_net,
                "source": "kline_snapshot",
            }
            # 截断 kline 到 <= cutoff_date
            kk = [r for r in k if str(r.get("date") or "")[:10] <= cutoff_date]
            return pseudo, kk

        def _hist_flow(code_, cutoff_date):
            try:
                rows = fund_flow.get_history_flow(code_, 60) or []
                rows = [r for r in rows if str(r.get("date", ""))[:10] <= cutoff_date]
            except Exception:
                rows = []
            return {"code": code_, "today": None, "history": rows,
                    "is_historical": True, "snapshot_date": cutoff_date}

        def _hist_seats(code_, cutoff_date):
            try:
                sd = seat_lookup.get_stock_seats(code_, 60) or {}
                rows = sd.get("rows") or []
                rows = [r for r in rows if str(r.get("date") or "")[:10] <= cutoff_date]
                sd["rows"] = rows
                sd["is_historical"] = True
                sd["snapshot_date"] = cutoff_date
                return sd
            except Exception:
                return {"code": code_, "rows": [], "is_historical": True, "snapshot_date": cutoff_date}

        snapshot_t = to_thread(_hist_snapshot, code, target_date)
        flow_t     = to_thread(_hist_flow, code, target_date)
        seats_t    = to_thread(_hist_seats, code, target_date)
        holders_t  = to_thread(_holders, code)
        quote_t    = None  # 占位,后面接 snapshot 的 quote
        kline_t    = None
    else:
        quote_t = to_thread(_quote, code)
        flow_t  = to_thread(fund_flow.get_combined, code, 60)
        seats_t = to_thread(seat_lookup.get_stock_seats, code, 10)
        kline_t = to_thread(stock_kline_loader, code, 120)
        holders_t = to_thread(_holders, code)
        snapshot_t = None

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

    if is_historical and snapshot_t is not None:
        # 历史快照:把 snapshot 拆成 quote + kline,再用 _ok 兜底
        snap = await asyncio.wait_for(snapshot_t, timeout=8)
        snap = snap if isinstance(snap, tuple) and len(snap) == 2 else ({}, [])
        hist_quote, hist_kline = snap
        # flow/seats/holders 走独立线程
        flow_h, seats_h, holders_h = await asyncio.wait_for(asyncio.gather(
            _with_timeout(flow_t, 8),
            _with_timeout(seats_t, 4),
            _with_timeout(holders_t, 8),
        ), timeout=15)
        quote, kline = hist_quote, hist_kline
        flow = flow_h
        seats = seats_h
        holders = holders_h
    else:
        gather_coro = asyncio.gather(
            _with_timeout(quote_t, 12),   # 实时行情 — 东财冷启动可达 6-8s,周末/晚间限频 10-12s 常见
            _with_timeout(flow_t, 10),    # 资金流 (get_main_flow + get_history_flow 各 5s 兜底,合计常见 4-9s)
            _with_timeout(seats_t, 4),
            _with_timeout(kline_t, 6),
            _with_timeout(holders_t, 8),
        )
        # 2026-07-13 Round 14: 分级超时 — 前 12s 强等(常规网络),再 6s 弱等(节假日拉跨)
        # 弱等也挂 → 用上次成功结果(陈旧)兜底,不让用户 20s 空等
        try:
            quote, flow, seats, kline, holders = await asyncio.wait_for(gather_coro, timeout=12)
        except asyncio.TimeoutError:
            log.warning(f"stock/{code} 12s 弱超时 → 再给 6s 机会")
            try:
                quote, flow, seats, kline, holders = await asyncio.wait_for(gather_coro, timeout=6)
            except asyncio.TimeoutError:
                log.warning(f"stock/{code} 18s 仍超时 → 返回陈旧快照")
                stale = _STOCK_LAST_OK.get(code)
                if stale and (time.time() - stale["ts"]) < 1800:
                    age = int(time.time() - stale["ts"])
                    log.info(f"stock/{code} 返回陈旧快照 (age={age}s)")
                    return envelope(data=stale["data"], meta={"stale_seconds": age, "refreshing": True})
                # 都没有就以"全部默认值"返回,前端只显示行情空
                quote, flow, seats, kline, holders = None, None, None, None, None
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

    # 字段标准化 — 把不同上游(腾讯/东财/THS)的别名归一为前端用的字段
    quote = _normalize_quote(quote)

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

    out = {
        "code": code,
        "quote": quote or {},
        "fund_flow": flow or {"code": code, "today": None, "history": []},
        "seats": seats or {"code": code, "rows": [], "blacklisted": False,
                            "seat_count": 0, "total_lhb_rows": 0, "known_groups": []},
        "kline": kline or [],
        "holders": holders,  # 散户/主力持股 (季报,含前十大流通股东集中度)
        "main_exit": None,
        # 历史快照标记 (2026-07-11 用户反馈日期切换不生效)
        "is_historical": is_historical,
        "snapshot_date": target_date or "",
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
    }
    # 2026-07-13 Round 14: 写陈旧兜底缓存 (30min 内可复用)
    if not is_historical and quote:
        _STOCK_LAST_OK[code] = {"data": out, "ts": time.time()}
    return envelope(data=out)


# 2026-07-13 Round 14: stock_overview 陈旧兜底 — 18s 还挂就用这个
_STOCK_LAST_OK: dict[str, dict] = {}


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
⚠ 直接输出 JSON,不要 reasoning/思考过程。

【默认铁律·不可破】
- 庄股/跟风/杂股 → 回避
- 拉萨天团主导 → 回避
- 九连阳后放量跌停 → 回避
- 4 层(风控/周期/形态/分时)全过 + 风险可控 → 买
- 任一层不过 → 观望
- 主力层失败 → 回避

【三市场环境·2026-07-11 新增】
三市场环境必须同时给出,一个不好就不买。verdict 必须在三市场都未触发风险警示时才允许"买"。
- 美股环境: 纳指/标普 5 日涨跌幅 ≥ -2% 为正常;<-3% 或单日暴跌 -2% 触发风控 → 警戒
- 韩股环境: KOSPI/KOSDAQ 5 日 ≥ -2% 正常;<-3% 或单日 -2% 触发风控 → 警戒
- A 股环境: 上证/深证/创业板 5 日 ≥ -3% 正常;<-5% 或单日 -2% 触发风控 → 警戒
任一市场触发警戒 → verdict 强制 ≤ "观望",conviction ≤ 50。
三市场全正常才解锁"买"判定。

【连板 & 板块联动加成规则】
- 该股连板 ≥ 3 且板块当日有 ≥ 5 只涨停 + ≥ 2 只连板 → 主线龙头, conviction 80+
- 该股刚首板 + 板块当日涨停 ≥ 10 只 → 主线启动中, 强烈关注, conviction 60-80
- 该股 ≥ 5 连板且板块已无新增涨停 → 高位分化, 风险加大, conviction ≤ 40
- 该股 ≥ 2 连板 + 板块当日同板块连板 ≥ 3 只 → 板块联动强, conviction +10
- 该股今日未涨停 + 近 5 日无涨停 + 板块有涨停 → 杂毛, conviction ≤ 30

【AI 概念标加成规则(2026-07 主战场 = 机器人/AI/半导体)】
- 该股 ai_tag.is_main_field = false → conviction ≤ 30, 不参与主线
- 该股 ai_tag ∈ {机器人本体, 机器人零部件, 机器视觉, AI 算力, AI 芯片, AI 软件, 半导体, 高速光互联, HBM 存储, CPO, 先进封装} → 主战场, conviction ≥ 50 起
- 该股 ai_tag = 智能驾驶 → 主战场外延, conviction 40-60
- 该股 ai_tag = 新能源车 → 主战场外溢, conviction ≤ 40

【板块内角色(龙头/中军/杂毛)·必填】
- 龙头:连板 ≥ 3 且板块涨停 ≥ 5 只 / 三维共振核心标的
- 中军:中等涨幅/中等连板,跟随主线
- 杂毛:未涨停 + 无题材联动 / 拉萨天团 / 跟风 / 非主战场 → 倾向"回避"
- 默认"中军"

【4 层板块定位(必读)】
  cluster: 大科技/高端制造/消费/医药/金融/周期资源
  chain: 主线最小单位,例「人形机器人」「HBM 存储」
  sub: 细分多标签,例「谐波减速器」「800G 光模块」
  role: main(主战场龙头)/second(二线弹性)/noise(杂毛跟风)
- 同 chain 当日涨停 ≥ 15 家 → 主线,可重点关注
- taxonomy.role = noise → verdict 强制 ≤ "观望"
- taxonomy.role = noise 且 ai_tag.is_main_field = false → verdict = "回避"

【严格 JSON 输出·不许额外文字】
{"verdict":"买/观望/回避","role":"龙头/中军/杂毛","conviction":0-100,
 "layer_pass":{"L1_风控":bool,"L2_周期主线":bool,"L3_形态":bool,"L4_分时":bool},
 "rules_passed":["规则"],"rules_failed":["规则"],"key_risks":["风险"],"summary":"60字内"}"""


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
    """单批(≤15 条)→ {id: ai_dict}  (R1+R3 升级走 ai_client.call + parse_json_loose)"""
    from . import ai_client
    from .sector_classify import SW_31
    lines = ["请分析以下 {} 条 A 股财经新闻:\n".format(len(batch))]
    for n in batch:
        # R2 prompt 注入防御: 新闻标题/摘要可能含乱七八糟字符,先过清洗 + boundary
        title = (n.get("title") or "")[:120]
        intro = ai_client.cap_text(n.get("intro"), 200)
        kw = (",".join(n.get("keywords") or []))[:80]
        lines.append(f"--- id={n.get('id','')} time={n.get('ctime_str','')} media={n.get('media','')} ---")
        lines.append(f"标题:{title}")
        if intro:
            lines.append(f"摘要:{intro}")
        if kw:
            lines.append(f"关键词:{kw}")
        lines.append("")
    user_content = "\n".join(lines)
    user_content = ai_client.wrap_prompt("news", user_content, max_chars=4500)
    system_content = _build_news_ai_system()

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }
    spec = ai_client.CallSpec(
        url=base_url,
        headers=ai_client.headers(api_key),
        body=body,
        name="news",
        model=model,
        timeout=45.0,
        attempts=(1, 2),
        max_tokens_alts=(3000, 4500),
    )
    try:
        _text, parsed, _info = ai_client.call(spec)
    except ai_client.AICallError as e:
        log.warning(f"news AI batch 失败: {e}")
        return {}

    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not items:
        return {}

    out: dict[str, dict] = {}
    for it in items:
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
    """同步调用 MiniMax(M3) chat completion. 失败抛 RuntimeError.

    R1+R3+R5+R7 已统一委托 web.ai_client.call + parse_json_loose +
    normalize_ai_verdict;本函数只剩"如何拼 user_content + 取 system prompt"。
    """
    from . import ai_client

    last5 = ctx.get("kline", [])[-5:]
    fund_hist = ctx.get("fund_flow", {}).get("history", [])[-5:]
    seats = ctx.get("seats", {})
    quote = ctx.get("quote", {})
    limit_up = ctx.get("limit_up", {}) or {}
    sector = ctx.get("sector", {}) or {}
    ai_tags = sector.get("ai_tags", {}) or {}
    tax = sector.get("taxonomy") or {}
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

    # 极简 user prompt — M3 reasoning_content 长,user 越短越好,留 token 给 content
    user_content = f"""{code} 行情:{quote.get('最新价','-')} 涨跌%:{quote.get('涨跌幅','-')} 成交额:{quote.get('成交额','-')}
K线(近5日收盘):{[k.get('收盘') for k in last5]}
资金5日(主力/超大/大):{[(h.get('主力净'),h.get('超大单净'),h.get('大单净')) for h in fund_hist]}
席位组:{','.join(seats.get('known_groups',[])) or '-'} 黑名单:{seats.get('blacklisted',False)} 连板:{seats.get('lianban_count','-')}
连板摘要:{limit_up.get('summary','')}
行业:{sector.get('sw')}/{tax.get('level1_cluster')}/{tax.get('level3_chain')}/{tax.get('level4_subconcept')} role={tax.get('role')}{is_mainline_brief}
输出 JSON:verdict/role/conviction/layer_pass/rules_passed/rules_failed/key_risks/summary"""

    # 可选:全局(美/韩)情绪上下文(由 ai_scoring.score_batch 注入,key="_global_text")
    global_text = ctx.get("_global_text") or ""
    if global_text:
        user_content = f"{global_text}\n\n--- 个股 ---\n{user_content}"

    # R5 token governance: 单股 user_content 不超过 ~600 tokens
    user_content = ai_client.truncate_to_tokens(user_content, max_tokens=600)

    spec = ai_client.CallSpec(
        url=ai_client.default_url(),
        headers=ai_client.headers(api_key),
        body={
            "model": ai_client.default_model(),
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        },
        name="main_verdict",
        model=ai_client.default_model(),
        timeout=35.0,
        attempts=(1, 2),
        max_tokens_alts=(3500, 4500),
    )
    try:
        _text, parsed, _info = ai_client.call(spec)
    except ai_client.AICallError as e:
        raise RuntimeError(f"AI 调用失败 ({e})") from e
    # R7 schema 白名单 + clamp
    return ai_client.normalize_ai_verdict(parsed) if parsed else {}


def _parse_ai_json(text: str) -> dict:
    """宽松解析:AI 可能把 JSON 嵌在 ```json ... ``` 里, 也可能截断 (R3:R1+R3 升级走 ai_client.parse_json_loose)"""
    from . import ai_client
    return ai_client.parse_json_loose(text or "")


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


@app.get("/api/stock/{code}/related_stocks")
async def stock_related_stocks(code: str, limit: int = 24):
    """相关个股推荐 — 同 L3 产业链 / 同 L4 细分标签 / 同 sw。
    返回按相关性排序的个股清单(<=limit),含实时价/涨跌幅。

    优化: 一次性从 Redis 加载全 A 股 sector 缓存(避免逐只 get_sector 反复拿锁),
          再单次拉全市场实时价, in-process 匹配。
    """
    from . import sector_classify as _sc
    from .concept_taxonomy import (
        CONCEPT_L3, CONCEPT_L4, L3_TO_CLUSTER, match_concepts, concept_signature,
    )
    from .sector_taxonomy import classify_taxonomy as _classify_taxonomy

    code = code.strip().zfill(6)
    limit = max(6, min(50, limit))

    def _scan():
        # 1) 拿目标股的完整 sector(走缓存,s ≤ 50ms)
        sec = _sc.get_sector(code) or {}
        sw = sec.get("sw")
        sw_raw = sec.get("sw_raw") or ""
        csrc = sec.get("csrc") or ""
        tax = sec.get("taxonomy") or {}
        target_l3 = tax.get("level3_chain") or ""
        target_l4 = set(tax.get("level4_subconcept") or [])
        sig = concept_signature(f"{sw_raw} {csrc}")
        target_clusters = set(sig.get("by_cluster", {}).keys())

        # 2) 一次性加载全 sector 缓存(Redis 单 key,~50ms)
        all_cache = _sc._load_cache() or {}
        stocks_map = all_cache.get("stocks") or {}

        # 3) 名称表(从 stock_list_all 取) + realtime quote 缓存
        from .. import data_layer as _dl
        all_names = dict(_dl.fetch_stock_list_all() or [])

        # 4) in-process 扫描
        same_l3, same_l4, same_cluster, same_sw = [], [], [], []
        for c, info in stocks_map.items():
            if c == code:
                continue
            csw = info.get("sw") or ""
            csw_raw = info.get("sw_raw") or ""
            ccsrc_raw = info.get("csrc_raw") or ""
            tax2 = _classify_taxonomy(c, csw, sw_raw=csw_raw, csrc_raw=ccsrc_raw)
            l3_2 = tax2.get("level3_chain") or ""
            l4_2 = set(tax2.get("level4_subconcept") or [])

            # 优先:realtime quote 缓存(5s TTL)
            row = _cache_quote.get(("quote", c)) or {}
            item = {
                "code":   c,
                "name":   all_names.get(c) or c,
                "price":  _safe_float(row.get("最新价") or row.get("price")),
                "pct":    _safe_float(row.get("涨跌幅") or row.get("pct")),
                "volume": _safe_float(row.get("成交量") or row.get("volume")),
                "amount": _safe_float(row.get("成交额") or row.get("amount")),
            }

            if target_l3 and l3_2 == target_l3:
                same_l3.append(item)
            if target_l4 & l4_2:
                same_l4.append(item)
            if sw and csw == sw:
                same_sw.append(item)
            sig2 = concept_signature(f"{csw_raw} {ccsrc_raw}")
            clu2 = set(sig2.get("by_cluster", {}).keys())
            if target_clusters & clu2:
                same_cluster.append(item)

        # 5) 排序:按相关性优先级 + 涨跌幅
        def _sort_pct(arr):
            arr.sort(key=lambda x: -(x.get("pct") if x.get("pct") is not None else -999))
        for arr in (same_l3, same_l4, same_cluster, same_sw):
            _sort_pct(arr)

        result = []
        seen = {code}

        def _take(arr, label, max_n):
            cnt = 0
            for it in arr:
                if it["code"] in seen:
                    continue
                if cnt >= max_n:
                    break
                it2 = dict(it)
                it2["rel_type"] = label
                result.append(it2)
                seen.add(it["code"])
                cnt += 1
            return cnt

        _take(same_l3, "同L3产业链", max(8, limit // 3))
        _take(same_l4, "同L4细分", max(4, limit // 6))
        _take(same_cluster, "同大集群", max(4, limit // 6))
        _take(same_sw, "同申万行业", max(4, limit // 6))

        return {
            "code": code,
            "target": {
                "sw":      sw,
                "sw_raw":  sw_raw,
                "l3":      target_l3,
                "l4":      list(target_l4),
                "clusters": list(target_clusters),
                "concept_matched": sig.get("matched", []),
            },
            "groups": {
                "same_l3":      [it for it in result if it["rel_type"] == "同L3产业链"],
                "same_l4":      [it for it in result if it["rel_type"] == "同L4细分"],
                "same_cluster": [it for it in result if it["rel_type"] == "同大集群"],
                "same_sw":      [it for it in result if it["rel_type"] == "同申万行业"],
            },
            "count": len(result),
        }

    try:
        # ⚠ 不用 to_thread — 它吞异常返 None
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _scan),
            timeout=20,
        )
        return envelope(data=result)
    except asyncio.TimeoutError:
        log.warning(f"related_stocks 超时 (code={code})")
        return envelope(error="相关个股推荐超时", data={"code": code, "groups": {}, "count": 0})
    except Exception as e:
        log.exception(f"related_stocks 失败: {e}")
        return envelope(error=str(e), data={"code": code, "groups": {}, "count": 0})


def _safe_float(v, default=None):
    try:
        if v is None or v == "" or (isinstance(v, float) and (v != v)):
            return default
        return float(v)
    except Exception:
        return default


@app.get("/api/stock/{code}/ai_analysis")
async def stock_ai_analysis(code: str, date: str | None = Query(None, description="YYYYMMDD;空=今日")):
    """基于铁律的 AI 买入判断. 需配置 MINIMAX_API_KEY 环境变量.

    2026-07-11: 加 date 参数支持历史 verdict 回看(供 AI 面板历史对比条用)。
    """
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()

    # 1) 先查 SQLite 日内缓存 — 命中直接返(避免每次都打 25-35s LLM)
    from .. import cache_db as _cdb
    # date 可能是 Query 对象 (内部调用时) 或 str;统一提取
    if hasattr(date, "default"):  # Query 对象
        date = None
    elif isinstance(date, str) and date.strip():
        date = date.strip()
    else:
        date = None
    today_str = date or datetime.datetime.now().strftime("%Y%m%d")
    hit = _cdb.get_cached_ai(today_str, code, "MiniMax-M3")
    if hit:
        # R4 缓存污染防护: schema 校验,不合法 → 当未命中重算
        from . import ai_client
        if not ai_client.is_valid_cached_verdict(hit):
            log.warning(f"stock_ai_analysis 缓存污染 ({code}), 重算")
            hit = None
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
    # 三市场环境(美/韩/A股) — 给 AI 注入"一不好就不买"判断依据
    from . import global_markets as _gm
    def _global_load():
        try:
            return _gm.fetch_global_sentiment()
        except Exception:
            return None
    global_t = to_thread(_global_load)

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    # 硬超时总闸 14 秒:避免某个数据源 hang 把 AI 接口挂死
    try:
        quote, flow, seats, kline, limit_up, sector, global_ctx = await asyncio.wait_for(
            asyncio.gather(
                _wt(quote_t, 4),
                _wt(flow_t, 6),
                _wt(seats_t, 4),
                _wt(kline_t, 6),
                _wt(limit_up_t, 6),
                _wt(sector_t, 5),
                _wt(global_t, 5),
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
    global_ctx = _ok(global_ctx, None)

    ctx = {"quote": quote, "fund_flow": flow, "seats": seats, "kline": kline, "limit_up": limit_up, "sector": sector}
    # 三市场环境文本(美/韩/A股指数) — 让 AI 拿到"一不好就不买"判断依据
    if global_ctx:
        try:
            from . import global_markets as _gm2
            ctx["_global_text"] = _gm2.render_for_prompt(global_ctx, max_chars=600)
        except Exception:
            pass

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


# ═══════════════════════════════════════════════════════════════
# AI · 量化砸盘风险检测 (2026-07-11)
#   - GET /api/stock/{code}/ai_crash_risk
#   检测量化砸盘信号:盘面形态 / 虚假流动性 / 尾盘异动 /
#                   量化席位对倒 / 主力资金背离 / 融券异动
#   与铁律联动 → 输出"砸盘风险评级 + 应对建议"
# ═══════════════════════════════════════════════════════════════

# 业内已知量化席位关键词(部分代表名单,命中即算量化风险加分)
_QUANT_SEAT_KEYWORDS = [
    "华泰证券总部", "华泰证券上海", "华泰证券深圳", "华泰证券北京",
    "中金公司上海", "中金公司北京", "中金上海", "中金北京",
    "中信证券杭州", "中信证券上海", "中信证券深圳", "中信证券北京",
    "海通证券上海", "海通证券北京",
    "国泰君安上海", "国泰君安深圳",
    "招商证券上海", "招商证券深圳",
    "申万宏源上海", "申万宏源北京",
    "广发证券上海", "广发证券北京",
    "中国国际金融",
    "华泰柏瑞", "华泰证券资管",
    "高盛高华", "摩根士丹利华鑫",
    "瑞银证券上海", "瑞银证券北京",
    "JPMorgan", "Morgan Stanley", "Goldman Sachs",
]


def _detect_quant_seats(seats: dict) -> list[dict]:
    """扫描 seats.rows, 命中量化席位 → 返回 [{date,seat,amount_wan,direction,matched_kw}, ...]"""
    out = []
    for r in (seats.get("rows") or []):
        seat = r.get("seat") or ""
        for kw in _QUANT_SEAT_KEYWORDS:
            if kw in seat:
                out.append({
                    "date": r.get("date"),
                    "seat": seat,
                    "amount_wan": r.get("amount_wan"),
                    "direction": r.get("direction"),
                    "matched_kw": kw,
                })
                break
    return out


def _detect_pair_trade(seats: dict) -> list[dict]:
    """检测同席位既买又卖(对倒信号):返回 [{date,seat,buy_wan,sell_wan}, ...]"""
    if not seats.get("rows"):
        return []
    by_seat_date: dict[tuple, dict] = {}
    for r in seats["rows"]:
        seat = r.get("seat") or ""
        date = r.get("date") or ""
        k = (date, seat)
        if k not in by_seat_date:
            by_seat_date[k] = {"date": date, "seat": seat, "buy_wan": 0.0, "sell_wan": 0.0}
        amt = float(r.get("amount_wan") or 0)
        if r.get("direction") == "买入":
            by_seat_date[k]["buy_wan"] += amt
        elif r.get("direction") == "卖出":
            by_seat_date[k]["sell_wan"] += amt
    out = []
    for v in by_seat_date.values():
        if v["buy_wan"] > 0 and v["sell_wan"] > 0:
            # 双向都有:对倒特征
            out.append({
                "date": v["date"], "seat": v["seat"],
                "buy_wan": round(v["buy_wan"], 1), "sell_wan": round(v["sell_wan"], 1),
                "ratio": round(min(v["buy_wan"], v["sell_wan"]) / max(v["buy_wan"], v["sell_wan"]), 2),
            })
    return out


def _detect_fake_liquidity(kline: list[dict]) -> list[dict]:
    """检测「成交量 3 倍↑ + 涨跌幅 ≤ 3%」的虚假流动性信号(近 10 个交易日内)"""
    signals = []
    if not kline:
        return signals
    last10 = kline[-10:]
    for i in range(1, len(last10)):
        prev = last10[i - 1]
        cur = last10[i]
        v_prev = float(prev.get("成交量") or 0)
        v_cur = float(cur.get("成交量") or 0)
        pct = float(cur.get("涨跌幅") or cur.get("change_pct") or 0)
        if v_prev <= 0 or v_cur <= 0:
            continue
        v_ratio = v_cur / v_prev
        if v_ratio >= 3.0 and abs(pct) <= 3.0:
            signals.append({
                "date": cur.get("日期") or cur.get("date"),
                "volume_ratio": round(v_ratio, 2),
                "change_pct": round(pct, 2),
            })
    return signals


def _detect_late_session(intraday_today: dict | None) -> dict | None:
    """检测尾盘异动:14:30 后拉升/砸盘超过 2%"""
    if not intraday_today or not intraday_today.get("points"):
        return None
    pts = intraday_today["points"]
    # 找到 14:30 之后的数据
    late = [p for p in pts if str(p.get("time", "")) >= "14:30"]
    if len(late) < 3:
        return None
    open_price = late[0].get("price")
    close_price = late[-1].get("price")
    if not open_price or not close_price:
        return None
    pct = (close_price - open_price) / open_price * 100
    if abs(pct) >= 2.0:
        return {
            "window": "14:30 至收盘",
            "pct": round(pct, 2),
            "direction": "砸盘" if pct < 0 else "拉升",
        }
    return None


def _build_crash_risk_system_prompt() -> str:
    """量化砸盘检测 system prompt - 2026-07-11 用户指定

    框架:
    一、盘面特征(分时锯齿 / 虚假流动性 / 尾盘异动)
    二、龙虎榜席位(量化席位识别 / 对倒特征)
    三、资金流向(主力净流入背离 / 大单小单背离 / 融资融券异动)
    四、综合判定(≥3 个信号 = 高度警惕 + 退神式操作建议)
    + 必须与"系统铁律"联动(任意铁律违反 + 砸盘信号 = 强制回避)
    """
    return """你是 A 股短线量化风险分析师。任务:基于【分时/龙虎榜/资金/融资融券】四大维度,识别【量化砸盘】风险,结合系统【铁律】给出综合判定。

⚠ 直接输出 JSON,不要 reasoning/思考过程。

【系统铁律(必须联动)】
- 庄股/跟风/杂股 → 回避
- 拉萨天团主导 → 回避
- 九连阳后放量跌停 → 回避
- 量化席位对倒 → 回避(铁律 § 砸盘风险 = 回避)
- 虚假流动性(放量滞涨) → 观望
- 砸盘信号 ≥ 3 个 + 任一铁律违反 → 强制 verdict="回避"

【一、盘面特征分析(近 5-10 个交易日)】
1. 分时形态:是否「锯齿状」?日内波动是否机械化(频繁小跳动 ±0.3% 以内但密度极高)?
2. 成交量特征:是否有「成交量 3 倍↑ + 涨跌幅 ≤ 3%」的虚假流动性?
3. 尾盘异动:14:30 后或集合竞价是否无端拉升/砸盘 ≥ 2%?

【二、龙虎榜席位分析】
1. 买卖前五是否出现量化席位关键词(华泰证券总部/中金上海/中信证券杭州/海通证券上海 等)?
2. 同席位既买又卖(对倒)?呈现「买 1 亿 / 卖 1.2 亿」的对倒特征?
3. 各席位买卖金额接近(无领头羊)?

【三、资金流向与指标分析】
1. 主力资金净流入 + 股价滞涨 → 可能量化对倒
2. 大单净流出 + 小单净流入背离 → 散户接盘 / 量化出货
3. 融资融券:融券余额单周 +30%↑ + 融资买入占比高 → 量化对冲
4. 主力/超大单/大单/中单/小单 5 层资金流向是否同步?

【四、综合判定标准】
- 信号命中数 ≥ 4 → crash_risk="高", verdict="回避"
- 信号命中数 = 3 → crash_risk="中高", verdict="回避"
- 信号命中数 = 2 → crash_risk="中", verdict="观望"
- 信号命中数 = 1 → crash_risk="低", verdict="观望"
- 信号命中数 = 0 → crash_risk="无", verdict="正常"
- 若叠加铁律违反(庄股/拉萨/九连阳后放量跌停等),crash_risk 直接升级 1 级

【建议策略】
- 高 / 中高 → 立即减仓或清仓,等待砸盘结束
- 中 → 减半仓 + 止损下移 5%
- 低 → 持仓观察,不再加仓
- 无 → 正常持有,但提醒「砸盘随时可能发生,止损单必须挂好」

【输出严格 JSON·不许额外文字】
{
  "crash_risk":"高/中高/中/低/无",
  "verdict":"回避/观望/正常",
  "conviction": 0-100 (砸盘确信度, 越高越需警惕),
  "signals": [
    {"category":"盘面/席位/资金","name":"信号名","detail":"具体描述","weight":"高/中/低"}
  ],
  "signal_count": 信号命中数,
  "rule_violations": ["违反的铁律条目"],
  "funding_skew": {"main": 主力净额%, "super": 超大单%, "large": 大单%, "mid": 中单%, "small": 小单%},
  "summary": "≤80 字结论+操作建议"
}"""


def _call_minimax_crash_risk(api_key: str, code: str, ctx: dict) -> dict:
    """同步调用 MiniMax(M3) - 量化砸盘检测 (R1+R3+R7 统一走 ai_client)."""
    from . import ai_client

    quant_seats = ctx.get("quant_seats") or []
    pair_trades = ctx.get("pair_trades") or []
    fake_liq = ctx.get("fake_liquidity") or []
    late_session = ctx.get("late_session")
    kline = ctx.get("kline") or []
    flow = ctx.get("fund_flow") or {}
    flow_today = flow.get("today") or {}
    flow_hist = flow.get("history") or []
    seats = ctx.get("seats") or {}
    quote = ctx.get("quote") or {}
    sector = ctx.get("sector") or {}

    kline_10 = kline[-10:] if kline else []
    fund_5 = flow_hist[-5:] if flow_hist else []

    parts = [f"【个股】{code} {quote.get('名称','')} | 价 {quote.get('最新价','-')} 涨跌 {quote.get('涨跌幅','-')}% 成交额 {quote.get('成交额','-')}"]
    parts.append(f"行业: {sector.get('sw') or sector.get('name') or '-'}")

    parts.append("\n【一、盘面特征】")
    parts.append(f"近 10 日 K线: {[(k.get('日期'),k.get('收盘'),k.get('成交量'),k.get('涨跌幅')) for k in kline_10]}")
    if fake_liq:
        parts.append(f"⚠ 虚假流动性信号: {fake_liq}")
    if late_session:
        parts.append(f"⚠ 尾盘异动: {late_session}")
    if not fake_liq and not late_session:
        parts.append("(盘面信号无异常)")

    parts.append("\n【二、龙虎榜席位】")
    seats_rows = seats.get("rows") or []
    if seats_rows:
        if quant_seats:
            parts.append(f"⚠ 量化席位命中 {len(quant_seats)} 次:")
            for s in quant_seats[:8]:
                parts.append(f"  - {s['date']} | {s['direction']} | {s['seat']} | {s['amount_wan']} 万 | 关键词={s['matched_kw']}")
        else:
            parts.append("(无已知量化席位)")
        if pair_trades:
            parts.append(f"⚠ 同席位对倒 {len(pair_trades)} 次:")
            for p in pair_trades[:6]:
                parts.append(f"  - {p['date']} | {p['seat']} | 买 {p['buy_wan']} 万 / 卖 {p['sell_wan']} 万 | 比例 {p['ratio']}")
    else:
        parts.append("(无近 30 日龙虎榜数据)")

    parts.append("\n【三、资金流向】")
    if flow_today:
        parts.append(f"今日资金(主力/超大/大/中/小): {flow_today.get('主力净')}/{flow_today.get('超大单净')}/{flow_today.get('大单净')}/{flow_today.get('中单净')}/{flow_today.get('小单净')}")
    if fund_5:
        parts.append(f"近 5 日资金净额(主力/超大/大): {[(h.get('主力净'),h.get('超大单净'),h.get('大单净')) for h in fund_5]}")
    if not flow_today and not fund_5:
        parts.append("(资金流数据暂无)")

    if flow_today:
        parts.append(f"\n5 层资金: 主力 {flow_today.get('主力净','-')}% | 超大单 {flow_today.get('超大单净','-')}% | 大单 {flow_today.get('大单净','-')}% | 中单 {flow_today.get('中单净','-')}% | 小单 {flow_today.get('小单净','-')}%")

    parts.append("\n【请输出 JSON】crash_risk / verdict / conviction / signals / signal_count / rule_violations / funding_skew / summary")

    # R2 prompt 注入防御: 用 boundary 标签包住不可信内容
    user_content = "\n".join(parts)
    user_content = ai_client.wrap_prompt("ctx", user_content)
    # R5 token governance
    user_content = ai_client.truncate_to_tokens(user_content, max_tokens=900)

    spec = ai_client.CallSpec(
        url=ai_client.default_url(),
        headers=ai_client.headers(api_key),
        body={
            "model": ai_client.default_model(),
            "messages": [
                {"role": "system", "content": _build_crash_risk_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
        },
        name="crash_risk",
        model=ai_client.default_model(),
        timeout=35.0,
        attempts=(1, 2),
        max_tokens_alts=(3500, 4500),
    )
    try:
        _text, parsed, _info = ai_client.call(spec)
    except ai_client.AICallError as e:
        raise RuntimeError(f"crash_risk AI 调用失败 ({e})") from e
    return ai_client.normalize_crash_risk(parsed) if parsed else {}


def _parse_crash_risk_json(text: str) -> dict:
    """宽松解析 crash_risk JSON (R3 升级走 ai_client.parse_json_loose)."""
    from . import ai_client
    parsed = ai_client.parse_json_loose(text or "")
    if not parsed:
        return {
            "crash_risk": "中", "verdict": "观望", "conviction": 50,
            "signals": [{"category": "系统", "name": "AI 返回被截断",
                         "detail": (text or "")[:120], "weight": "中"}],
            "signal_count": 0, "rule_violations": [], "funding_skew": {},
            "summary": "AI 返回被截断, 已尽力恢复结构。请重试或检查日志。",
        }
    return ai_client.normalize_crash_risk(parsed)


@app.get("/api/stock/{code}/ai_crash_risk")
async def stock_ai_crash_risk(code: str, force: bool = False):
    """量化砸盘风险检测 — 复用铁律, 同时跑盘面/席位/资金三路信号预扫描,
    把"机器能算的"全部算好再喂给 LLM, 让 LLM 只做最终综合判定。
    """
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()

    # SQLite 缓存 key: crash_risk:{date}:{code}
    from .. import cache_db as _cdb
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    cache_key = f"crash_risk:{today_str}:{code}"

    if not force:
        hit = _cdb.get_cached_ai(today_str, code, "MiniMax-M3-crash")
        if hit:
            # R4 缓存污染防护
            from . import ai_client
            if not ai_client.is_valid_cached_crash(hit):
                log.warning(f"crash_risk 缓存污染 ({code}), 重算")
                hit = None
        if hit and hit.get("crash_risk") is not None:
            return envelope(data=hit)

    if not api_key:
        return envelope(error="MINIMAX_API_KEY 未配置", data={
            "crash_risk": "未知", "verdict": "观望", "conviction": 0,
            "signals": [], "signal_count": 0, "rule_violations": [],
            "funding_skew": {}, "summary": "AI 模块未配置"
        })

    # ── 1) 并行拉所有上下文 ──
    from .. import lib_common as lc
    @cached(_cache_quote, key_fn=lambda c: ("quote", c))
    def _quote(code_):
        return lc.fetch_realtime(code_)
    quote_t = to_thread(_quote, code)
    flow_t  = to_thread(fund_flow.get_combined, code, 30)
    seats_t = to_thread(seat_lookup.get_stock_seats, code, 30)
    kline_t = to_thread(stock_kline_loader, code, 30)

    from .sector_classify import get_sector as _get_sector
    sector_t = to_thread(_get_sector, code)

    # 今日分时 (尽量拿, 拿不到不影响主流程)
    async def _intraday_load(c):
        try:
            r = await asyncio.wait_for(stock_intraday_5d(c), timeout=8)
            return (r.get("data") or {}).get("intraday_today")
        except Exception:
            return None

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    try:
        quote, flow, seats, kline, sector, intraday_today = await asyncio.wait_for(
            asyncio.gather(
                _wt(quote_t, 4),
                _wt(flow_t, 6),
                _wt(seats_t, 8),
                _wt(kline_t, 6),
                _wt(sector_t, 4),
                _intraday_load(code),
            ),
            timeout=20,
        )
    except asyncio.TimeoutError:
        log.warning(f"crash_risk 上游超时 (code={code})")
        return envelope(error="crash_risk 上游数据拉取超时", data={
            "crash_risk": "未知", "verdict": "观望", "conviction": 0,
            "signals": [], "signal_count": 0, "rule_violations": [],
            "funding_skew": {}, "summary": "数据源拉取超时,请稍后重试"
        })

    def _ok(v, default):
        return default if isinstance(v, BaseException) or v is None else v
    quote = _ok(quote, {})
    flow  = _ok(flow, {"code": code, "today": None, "history": []})
    seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False, "known_groups": []})
    kline = _ok(kline, [])
    sector = _ok(sector, {"code": code, "sw": None, "ai_tags": {}})

    # ── 2) 机器预扫描:量化席位 / 对倒 / 虚假流动性 / 尾盘异动 ──
    quant_seats = _detect_quant_seats(seats)
    pair_trades = _detect_pair_trade(seats)
    fake_liq = _detect_fake_liquidity(kline)
    late_session = _detect_late_session(intraday_today)

    # 铁律快查:黑名单
    rule_violations = []
    if seats.get("blacklisted"):
        rule_violations.append("§ 该股在龙虎榜黑名单中(历史有拉萨天团/砸盘前科)")

    pre_signals = []
    if quant_seats:
        pre_signals.append(f"§ 量化席位命中 {len(quant_seats)} 次")
    if pair_trades:
        pre_signals.append(f"§ 同席位对倒 {len(pair_trades)} 次")
    if fake_liq:
        pre_signals.append(f"§ 虚假流动性信号 {len(fake_liq)} 次")
    if late_session:
        pre_signals.append(f"§ 尾盘{late_session.get('direction','异动')} {late_session.get('pct')}%")

    ctx = {
        "code": code,
        "quote": quote,
        "fund_flow": flow,
        "seats": seats,
        "kline": kline,
        "sector": sector,
        "quant_seats": quant_seats,
        "pair_trades": pair_trades,
        "fake_liquidity": fake_liq,
        "late_session": late_session,
    }

    # ── 3) 调 LLM ──
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, functools.partial(_call_minimax_crash_risk, api_key, code, ctx)),
            timeout=35,
        )
    except asyncio.TimeoutError:
        return envelope(error="crash_risk AI 调用超时", data={
            "crash_risk": "未知", "verdict": "观望", "conviction": 0,
            "signals": [{"category":"系统","name":"AI 超时","detail":"35s 超时","weight":"高"}],
            "signal_count": 0, "rule_violations": rule_violations or pre_signals,
            "funding_skew": {}, "summary": "AI 调用超时,建议重试"
        })
    except Exception as e:
        log.warning(f"crash_risk AI 失败: {e}")
        return envelope(error=f"crash_risk AI 调用失败: {e}", data={
            "crash_risk": "未知", "verdict": "观望", "conviction": 0,
            "signals": [{"category":"系统","name":"AI 异常","detail":str(e)[:80],"weight":"高"}],
            "signal_count": 0, "rule_violations": rule_violations or pre_signals,
            "funding_skew": {}, "summary": f"AI 调用失败: {type(e).__name__}"
        })

    if not result:
        return envelope(error="crash_risk AI 返回空", data={
            "crash_risk": "未知", "verdict": "观望", "conviction": 0,
            "signals": [], "signal_count": 0, "rule_violations": rule_violations or pre_signals,
            "funding_skew": {}, "summary": "AI 返回空"
        })

    # ── 4) 注入机器预扫描信号到 signals (LLM 不一定扫得全) ──
    if quant_seats and not any(s.get("name") == "量化席位" for s in result.get("signals", [])):
        result.setdefault("signals", []).append({
            "category": "席位", "name": "量化席位", "weight": "高",
            "detail": f"命中 {len(quant_seats)} 次, 代表席位: {quant_seats[0]['seat']}"
        })
    if pair_trades and not any(s.get("name") == "对倒" for s in result.get("signals", [])):
        result.setdefault("signals", []).append({
            "category": "席位", "name": "对倒", "weight": "高",
            "detail": f"{len(pair_trades)} 次同席位双向成交, 最大比例 {max((p['ratio'] for p in pair_trades), default=0):.2f}"
        })
    if fake_liq and not any(s.get("name") == "虚假流动性" for s in result.get("signals", [])):
        result.setdefault("signals", []).append({
            "category": "盘面", "name": "虚假流动性", "weight": "中",
            "detail": f"{len(fake_liq)} 次放量滞涨, 最大量比 {max((s['volume_ratio'] for s in fake_liq), default=0):.1f}x"
        })
    if late_session and not any(s.get("name") == "尾盘异动" for s in result.get("signals", [])):
        result.setdefault("signals", []).append({
            "category": "盘面", "name": "尾盘异动", "weight": "高",
            "detail": f"14:30 后{late_session['direction']} {late_session['pct']}%"
        })

    # 注入预扫描 metadata 供前端展示
    result["pre_scan"] = {
        "quant_seats": quant_seats[:5],
        "pair_trades": pair_trades[:5],
        "fake_liquidity": fake_liq[:5],
        "late_session": late_session,
        "pre_signal_count": len(pre_signals),
    }
    # 注入铁律违反(若 LLM 漏)
    if rule_violations:
        for v in rule_violations:
            if not any(r.get("name") in v or v in r.get("name", "") for r in result.get("rule_violations", [])):
                result.setdefault("rule_violations", []).append(v)

    # ── 5) 写缓存 ──
    try:
        sector_name = (sector or {}).get("sw") or (sector or {}).get("name") or ""
        _cdb.upsert_ai(today_str, code, "MiniMax-M3-crash", result, sector=sector_name)
    except Exception as e:
        log.debug(f"crash_risk cache write fail: {e}")

    return envelope(data=result)


# ═══════════════════════════════════════════════════════════════
# AI 面板 · 交互增强 (2026-07-11)
#   - GET /api/stock/{code}/ai_history     过去 N 日 verdict 演变
#   - GET /api/stock/{code}/ai_layer_detail 各层数据 popover
#   - POST /api/stock/{code}/ai_refresh    强制重跑 (失效缓存 + 重调 LLM)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/stock/{code}/ai_history")
async def api_stock_ai_history(code: str, days: int = Query(7, ge=1, le=30)):
    """过去 N 日 AI 判定历史 — 给前端「历史对比条」用。

    数据源: ai_verdict 表 (SQLite 兜底 + Redis 主用,cache_db.get_cached_ai)。
    跨日的 cache_db 不会自动返回,所以这里直接走 SQL 查近 N 日,避免缓存 TTL 干扰。
    """
    code = code.strip().zfill(6)
    from .. import cache_db as _cdb
    def _load():
        try:
            conn = _cdb._thread_conn()
            rows = conn.execute(
                "SELECT date, verdict, role, conviction, sector, ts_updated "
                "FROM ai_verdict WHERE code=? AND model=? "
                "ORDER BY date DESC LIMIT ?",
                (code, "MiniMax-M3", days),
            ).fetchall()
        except Exception as e:
            log.debug(f"ai_history SQL 失败 {code}: {e}")
            return []
        out = []
        for r in rows:
            out.append({
                "date":       r[0] or "",
                "verdict":    r[1] or "-",
                "role":       r[2] or "中军",
                "conviction": int(r[1] and r[2] and 0) or int(r[3] or 0),
                "sector":     r[4] or "",
                "ts_updated": r[5] or 0,
            })
        return out
    try:
        history = await asyncio.wait_for(to_thread(_load), timeout=4)
    except Exception:
        history = []
    if history is None:
        history = []
    return envelope(data={"code": code, "history": history, "count": len(history)})


@app.get("/api/stock/{code}/ai_layer_detail")
async def api_stock_ai_layer_detail(code: str):
    """返回 AI 4 层各自依赖的底层数据 — 给前端 popover 用。

    L1 风控: 黑名单席位 / 拉萨天团 / 9 连阳 / 主力层失败 等信号
    L2 周期主线: 三市场 verdict + 主线 chain + 当日涨停数
    L3 形态: 5/10/20 日涨跌 + 连板 + 涨停池归属 + 资金 5 日累计
    L4 分时: 今日 tick 量价 + 5 日分时形态 + 异动
    """
    code = code.strip().zfill(6)
    # 读缓存(3min TTL,纯计算)
    hit = _cache_layer.get(("ai_layer_detail", code))
    if hit:
        return envelope(data=hit)

    def _load():
        from .. import lib_common as lc
        from . import fund_flow as _ff
        from . import limit_up_context as _luc
        from .sector_classify import get_sector as _gs
        from concurrent.futures import ThreadPoolExecutor as _TPE
        out = {"code": code, "layers": {}}

        # 5 路并行 (4-6s 总耗时 vs 串行 10-15s)
        @cached(_cache_quote, key_fn=lambda c: ("quote", c))
        def _q_uncached(code_):
            return lc.fetch_realtime(code_) or {}
        def _q():
            try: return _q_uncached(code) or {}
            except Exception: return {}
        def _f():
            try: return _ff.get_combined(code, 60) or {}
            except Exception: return {}
        def _s():
            try: return _gs(code) or {}
            except Exception: return {}
        def _l():
            try: return _luc.get_limit_up_context(code, sector_name=None) or {}
            except Exception: return {"today": None, "recent_5d": [], "sector_today": []}
        def _gm():
            try:
                from . import global_markets as _gm_mod
                return _gm_mod.fetch_global_sentiment() or {}
            except Exception: return {}

        results: dict = {"q": {}, "f": {}, "s": {}, "l": {}, "gm": {}}
        try:
            _pool = _TPE(max_workers=5)
            try:
                futs = {"q": _pool.submit(_q), "f": _pool.submit(_f),
                        "s": _pool.submit(_s), "l": _pool.submit(_l),
                        "gm": _pool.submit(_gm)}
                # 2026-07-12: akshare 全面断连, 单源超时 2s(降级数据但快速返)
                for k, f in futs.items():
                    try:
                        results[k] = f.result(timeout=2)
                    except Exception as e:
                        log.debug(f"ai_layer_detail {k} 失败: {e}")
            finally:
                # 关键: wait=False 避免孤儿线程拖累整个请求(他们还在跑 socket)
                _pool.shutdown(wait=False)
        except Exception as e:
            log.warning(f"ai_layer_detail 并行拉取异常: {e}")

        quote = results.get("q") or {}
        seats = results.get("f") or {}
        today_ff = (seats or {}).get("today") or {}
        flow_hist = (seats or {}).get("history") or []
        sec = results.get("s") or {}
        sw = sec.get("sw") or "-"
        ai_tags = (sec.get("ai_tags") or {})
        tax = (sec.get("taxonomy") or {})
        l3 = tax.get("level3_chain") or ""
        luc = results.get("l") or {}
        today_lu = luc.get("today") or {}
        recent_5d_lu = luc.get("recent_5d") or []
        sector_today = luc.get("sector_today") or []
        gm = results.get("gm") or {}

        # 主线判定 (单步 1s 内,串行 OK)
        mainline_msg = ""
        mainline_cnt = 0
        try:
            from .. import data_layer as _dl
            from .sector_taxonomy import count_zt_by_chain
            _zt = _dl.fetch_limit_up_pool() or []
            _codes = [str(z.get("code") or "").zfill(6) for z in _zt]
            mainline_cnt = count_zt_by_chain(_codes, _gs).get(l3, 0) if l3 else 0
            mainline_msg = f"{l3 or '-'} 当日涨停 {mainline_cnt} 家"
        except Exception:
            mainline_msg = f"{l3 or '-'} (主线判定异常)"

        # === L1 风控 — 黑名单 / 拉萨天团 / 主力层失败 ===
        l1_rows = []
        known = (seats or {}).get("known_groups") or []
        is_black = bool((seats or {}).get("blacklisted"))
        l1_rows.append({"k": "黑名单席位", "v": "⚠ 是" if is_black else "✓ 否",
                        "ok": not is_black})
        lhasa_hit = [g for g in known if "拉萨" in g or "东财拉萨" in g]
        l1_rows.append({"k": "拉萨天团", "v": "⚠ 出现" if lhasa_hit else "✓ 无",
                        "ok": not lhasa_hit, "detail": lhasa_hit})
        # 9 连阳
        streak_lu = sum(1 for r in recent_5d_lu if r) or 0
        l1_rows.append({"k": "近 5 日涨停次数", "v": f"{streak_lu}",
                        "ok": streak_lu < 4})
        # 主力层
        main_net = float(today_ff.get("main_net") or 0)
        l1_rows.append({"k": "今日主力净额", "v": f"{main_net:+.0f} 万",
                        "ok": main_net >= 0, "detail": "主力层失败 → 整体回避"})

        out["layers"]["L1_风控"] = {"rows": l1_rows, "verdict": "通过" if all(r["ok"] for r in l1_rows) else "失败"}

        # === L2 周期主线 ===
        l2_rows = []
        gm_sent = (gm.get("sentiment") or "neutral").lower()
        us_pct = float(gm.get("sentiment_score") or 0)
        l2_rows.append({"k": "美股风险偏好", "v": f"{gm_sent} ({us_pct:+.2f}%)",
                        "ok": gm_sent != "risk_off"})
        kr_data = (gm.get("kr") or {})
        kr_pct = float(kr_data.get("change_pct") or 0) if isinstance(kr_data, dict) else 0
        l2_rows.append({"k": "韩股 KOSPI", "v": f"{kr_pct:+.2f}%",
                        "ok": kr_pct > -0.5,
                        "detail": "≤-0.5% 触发风控" if kr_pct <= -0.5 else ""})
        a_avg = float((gm.get("a_share") or {}).get("change_pct") or 0)
        l2_rows.append({"k": "A 股 6 指数均值", "v": f"{a_avg:+.2f}%",
                        "ok": a_avg > -0.5})
        l2_rows.append({"k": "所属主线 (L3)", "v": mainline_msg,
                        "ok": mainline_cnt >= 15,
                        "detail": f"threshold ≥ 15 家涨停"})
        l2_rows.append({"k": "行业归属", "v": f"{sw} · cluster={tax.get('level1_cluster', '-')}",
                        "ok": True})
        l2_rows.append({"k": "AI 概念标", "v": f"{ai_tags.get('labels') or '-'} (主战场={ai_tags.get('is_main_field', False)})",
                        "ok": bool(ai_tags.get("is_main_field"))})

        out["layers"]["L2_周期主线"] = {"rows": l2_rows, "verdict": "通过" if all(r["ok"] for r in l2_rows) else "失败"}

        # === L3 形态 — 5/10 日涨跌 + 连板 + 资金 5 日累计 ===
        l3_rows = []
        try:
            kline = stock_kline_loader(code, 30) or []
        except Exception:
            kline = []
        closes = [float(k.get("close") or 0) for k in kline if k.get("close")]
        if len(closes) >= 6:
            pct_5d = round((closes[-1] / closes[-6] - 1) * 100, 2)
        else:
            pct_5d = None
        if len(closes) >= 11:
            pct_10d = round((closes[-1] / closes[-11] - 1) * 100, 2)
        else:
            pct_10d = None
        l3_rows.append({"k": "5 日涨跌", "v": f"{pct_5d:+.2f}%" if pct_5d is not None else "—",
                        "ok": pct_5d is None or pct_5d <= 25,
                        "warn": pct_5d is not None and pct_5d > 15})
        l3_rows.append({"k": "10 日涨跌", "v": f"{pct_10d:+.2f}%" if pct_10d is not None else "—",
                        "ok": pct_10d is None or pct_10d <= 50,
                        "warn": pct_10d is not None and pct_10d > 30})
        streak = today_lu.get("连板数") or 0
        l3_rows.append({"k": "今日连板", "v": f"{streak} 板",
                        "ok": streak <= 5,
                        "warn": streak >= 4})
        l3_rows.append({"k": "板块当日涨停", "v": f"{len(sector_today)} 只 / 行业 {sw}",
                        "ok": len(sector_today) >= 3 if (streak >= 1) else True,
                        "detail": "连板龙头需板块 ≥ 3 家涨停共振"})
        # 资金 5 日累计
        net5 = sum(float(h.get("main_net") or 0) for h in flow_hist[-5:])
        l3_rows.append({"k": "主力 5 日累计净额", "v": f"{net5:+.0f} 万",
                        "ok": net5 >= 0})

        out["layers"]["L3_形态"] = {"rows": l3_rows, "verdict": "通过" if all(r["ok"] for r in l3_rows) else "失败"}

        # === L4 分时 — 今日 tick 量价 + 异动 ===
        l4_rows = []
        price = float(quote.get("最新价") or 0)
        op = float(quote.get("今开") or 0)
        hi = float(quote.get("最高") or 0)
        lo = float(quote.get("最低") or 0)
        prev_close = float(quote.get("昨收") or 0)
        if price and prev_close:
            chg = (price / prev_close - 1) * 100
        else:
            chg = 0
        l4_rows.append({"k": "当日涨跌幅", "v": f"{chg:+.2f}%",
                        "ok": abs(chg) < 9.5})
        l4_rows.append({"k": "换手率", "v": f"{quote.get('换手率') or '-'}%",
                        "ok": float(quote.get('换手率') or 0) < 10,
                        "warn": float(quote.get('换手率') or 0) > 10})
        l4_rows.append({"k": "振幅", "v": f"{((hi-lo)/prev_close*100 if prev_close else 0):+.2f}%",
                        "ok": True})
        l4_rows.append({"k": "量比", "v": f"{quote.get('量比') or '-'}",
                        "ok": float(quote.get('量比') or 0) < 5,
                        "warn": float(quote.get('量比') or 0) >= 5})
        l4_rows.append({"k": "流通市值", "v": f"{quote.get('流通市值') or '-'} 亿",
                        "ok": True})

        out["layers"]["L4_分时"] = {"rows": l4_rows, "verdict": "通过" if all(r["ok"] for r in l4_rows) else "失败"}

        out["_meta"] = {
            "quote_keys": list(quote.keys())[:8],
            "sector_keys": list(sec.keys())[:8],
            "ts": time.time(),
        }
        return out

    try:
        # 2026-07-12: 上游降级场景, 总闸 6s(5 路并行 ×2s, 加 thread 调度开销)
        data = await asyncio.wait_for(to_thread(_load), timeout=6)
    except asyncio.TimeoutError:
        log.warning(f"ai_layer_detail {code} 6s 超时")
        return envelope(error="层详情拉取超时", data={"code": code, "layers": {}})
    except Exception as e:
        log.warning(f"ai_layer_detail {code} 异常: {e}")
        return envelope(error=str(e), data={"code": code, "layers": {}})
    # 写缓存(3min TTL,纯计算结果不依赖行情秒变)
    try:
        _cache_layer.set(("ai_layer_detail", code), data)
    except Exception:
        pass
    return envelope(data=data)


@app.post("/api/stock/{code}/ai_refresh")
async def api_stock_ai_refresh(code: str):
    """强制重跑 AI (失效 Redis + SQLite 缓存),不传 force,直接清缓存然后走原始调用链。"""
    code = code.strip().zfill(6)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return envelope(error="MINIMAX_API_KEY 未配置", data={"code": code})
    from .. import cache_db as _cdb
    today_str = datetime.datetime.now().strftime("%Y%m%d")

    # 1) 失效缓存 (Redis + SQLite 都清)
    try:
        from .. import cache_store
        k = cache_store.K.AI.format(date=today_str, code=code)
        cache_store.get_store().delete(k)
    except Exception as e:
        log.debug(f"ai_refresh Redis delete 失败 {code}: {e}")
    try:
        conn = _cdb._thread_conn()
        conn.execute("DELETE FROM ai_verdict WHERE date=? AND code=? AND model=?",
                     (today_str, code, "MiniMax-M3"))
        conn.commit()
    except Exception as e:
        log.debug(f"ai_refresh SQLite delete 失败 {code}: {e}")

    # 2) 走 stock_ai_analysis 主路径 (cache 已清 → 重新调 LLM)
    return await stock_ai_analysis(code)
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
async def api_screen(req: ScreenRequest, request: Request):
    from ..screen import run_stock_screen
    from .. import data_layer as dl
    # P1-audit-2026-07-15: 重型筛选,加 admin token 防 DoS
    if not _check_admin_token(request):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "admin token required"})
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
async def api_backtest(req: BacktestRequest, request: Request):
    from ..backtest import run_backtest
    # P1-audit-2026-07-15: 重型计算,加 admin token 防 DoS
    if not _check_admin_token(request):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "admin token required"})
    # 硬超时 90s: 数据源 / LLM 在沙箱挂时不能拖死 server(2026-07-12 audit 发现)
    try:
        result = await asyncio.wait_for(
            to_thread(
                run_backtest,
                start=req.start, end=req.end,
                top_n=req.top_n, hold_days=req.hold_days,
                sell_mode=req.sell_mode, sample=req.sample,
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        log.warning(f"backtest 超时 90s ({req.start}→{req.end})")
        return envelope(error="回测超时 90s, 请缩小样本或重试", data={
            "trades": [], "stats": {"reason": "timeout"},
        })
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
async def api_review_set_settings(request: Request, payload: dict = Body({})):
    """保存复盘设置。payload: {total_capital}"""
    # P0-audit-2026-07-15: 写操作,加 admin token (否则外网隧道可污染 total_capital 拖垮所有用户复盘)
    if not _check_admin_token(request):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "admin token required"})
    try:
        if "total_capital" in payload:
            _review.set_setting("total_capital", float(payload.get("total_capital") or 0))
        cap = _review.get_setting("total_capital", 0)
        return envelope(data={"total_capital": float(cap or 0)})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.get("/api/review/integrity")
async def api_review_integrity():
    """R13: 前端 ↔ 后端 一致性校验。

    返回:
      - group_sum_cum_pnl: 把 live_trades 按 code 分组, 每组 Σ cum_pnl (前端聚合算法)
      - portfolio_total_pnl: portfolio_overview() 拿到的总盈亏 (后端单源真相)
      - discrepancy:    group_sum - portfolio_total, 浮点容差 ≤0.01 元视为一致
      - groups: [{code, name, sum_cum_pnl, held, status, n_trades, mismatch?}]
      - dirty_codes:   列出 DB code='000000' 但 name→code 反查后仍为 000000 的 (历史脏数据无法修复)
      - ok:            discrepancy 绝对值 ≤0.01 + 无 dirty → True
    """
    try:
        # R-fix-2026-07-15: 之前 limit=500,持仓 >500 笔时 group_sum 用 500 笔算 vs portfolio 用全部算,
        # discrepancy 永远不为 0,badge 必然误报。改 limit=99999 几乎覆盖所有持仓,
        # 与 _review.portfolio_overview(None) 同源 → 对账可靠。
        trades = await to_thread(_review.live_trades, limit=99999)
        # 1) portfolio 单源真值
        port = await to_thread(_review.portfolio_overview, None)
        portfolio_total = float(port.get("total_pnl") or 0)
        portfolio_realized = float(port.get("realized_pnl") or 0)
        portfolio_unrealized = float(port.get("unrealized_pnl") or 0)
        # 2) 分组聚合 (前端算法复刻)
        from collections import defaultdict
        grp: dict[str, dict] = defaultdict(lambda: {
            "name": "", "n_trades": 0, "sum_cum_pnl": 0.0, "held": 0,
            "buy_shares": 0, "sell_shares": 0, "codes_seen": set(),
        })
        for t in trades:
            code = (t.get("code") or "").strip()
            if not code:
                continue
            g = grp[code]
            g["name"] = g["name"] or t.get("name") or ""
            g["n_trades"] += 1
            g["codes_seen"].add(code)
            live = t.get("live") or {}
            g["sum_cum_pnl"] += float(live.get("cum_pnl") or 0)
            g["held"] += int(live.get("held_shares") or 0)
            if t.get("direction") == "buy":
                g["buy_shares"] += int(t.get("shares") or 0)
            else:
                g["sell_shares"] += int(t.get("shares") or 0)
        # 3) dirty 检测: DB 里 code='000000' 且无法反查
        dirty_codes: list[str] = []
        try:
            from .. import cache_db as _cdb
            conn = _cdb._thread_conn()
            codes_list = list(grp.keys())
            if codes_list:
                rs = conn.execute(
                    f"SELECT code, name FROM trades WHERE code IN ({','.join('?'*len(codes_list))}) GROUP BY code, name",
                    codes_list,
                ).fetchall()
                for r in rs:
                    if (r[0] or "").strip() in ("", "000000"):
                        dirty_codes.append({"name": r[1], "code": r[0]})
        except Exception:
            pass
        # 4) 序列化
        groups_out = []
        for code, g in sorted(grp.items()):
            groups_out.append({
                "code": code,
                "name": g["name"],
                "n_trades": g["n_trades"],
                "sum_cum_pnl": round(g["sum_cum_pnl"], 2),
                "held": g["held"],
                "buy_shares": g["buy_shares"],
                "sell_shares": g["sell_shares"],
            })
        group_sum = round(sum(g["sum_cum_pnl"] for g in groups_out), 2)
        discrepancy = round(group_sum - portfolio_total, 2)
        ok = abs(discrepancy) <= 0.01 and not dirty_codes
        return envelope(data={
            "ok": ok,
            "group_sum": group_sum,
            "portfolio_total": round(portfolio_total, 2),
            "portfolio_realized": round(portfolio_realized, 2),
            "portfolio_unrealized": round(portfolio_unrealized, 2),
            "discrepancy": discrepancy,
            "threshold": 0.01,
            "n_groups": len(groups_out),
            "groups": groups_out,
            "dirty_codes": dirty_codes,
            "recommendation": (
                "✓ 前端分组累计 = 后端 FIFO 总额" if ok
                else ("⚠ 数据脏: DB 残留无法反查的历史 placeholder, 调 DELETE /api/review/trades_all?confirm=YES 重置"
                      if dirty_codes
                      else "⚠ 分组累计 vs portfolio 不一致 — 刷新页面重试或重启后端")
            ),
        })
    except Exception as e:
        log.exception("integrity")
        return envelope(error=str(e), status_code=500)


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
async def api_review_record_trade(request: Request, payload: dict = Body({})):
    """记 1 笔或批量记多笔交易。

    payload 兼容两种形状:
    1) 单笔 (旧): {code, direction, price, shares, occurred_at?, memo?, tags?[]}
    2) 批量 (新): {trades:[{code,direction,...}, ...]} — 一次性入库多笔

    返回:
    1) 单笔:{trade_id, trade}
    2) 批量:{inserted:[{index, trade_id, trade}], errors:[{index, error, input}], total, ok}
    """
    # P1-audit-2026-07-15: 写操作,加 admin token (否则外网可灌垃圾交易污染 FIFO/portfolio/integrity)
    if not _check_admin_token(request):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "admin token required"})
    # 批量模式
    trades_in = payload.get("trades")
    if isinstance(trades_in, list) and trades_in:
        # 先于插入跑归一化:name→code 反查、total/shares 反推 price (2026-07-12)
        _refresh_name_lookup()
        norm_in = {"trades": list(trades_in)}
        trades_norm = _normalize_trades_parsed(norm_in)
        inserted: list[dict] = []
        errors: list[dict] = []
        skipped: list[dict] = []
        seen_keys: set = set()  # 批内去重
        for i, t in enumerate(trades_norm):
            if not isinstance(t, dict):
                errors.append({"index": i, "error": "trade 不是 dict", "input": t})
                continue
            try:
                _code = str(t.get("code", "")).strip().zfill(6)
                _dir = str(t.get("direction", "buy") or "buy").strip().lower()
                _price = round(float(t.get("price", 0) or 0), 3)
                _shares = int(t.get("shares", 0) or 0)
                _oa = t.get("occurred_at") or ""
                _td = t.get("trade_date") or (_oa[:10].replace("-", "") if _oa else "")
                # 1) 批内重复:同股票+方向+价格+股数+时间(精确到分钟)
                key = (_code, _dir, _price, _shares, (_oa[:16] if _oa else _td))
                if key in seen_keys:
                    skipped.append({"index": i, "reason": "批内重复", "trade": t})
                    continue
                # 2) 库内已存在
                dup_id = _review.find_duplicate_trade(_code, _dir, _price, _shares, _oa or None, _td or None)
                if dup_id:
                    skipped.append({"index": i, "reason": "数据库已存在", "trade_id": dup_id, "trade": t})
                    continue
                seen_keys.add(key)
                tid = _review.record_trade(
                    code=str(t.get("code", "")).strip(),
                    direction=_dir,
                    price=float(t.get("price", 0) or 0),
                    shares=int(t.get("shares", 0) or 0),
                    occurred_at=t.get("occurred_at"),
                    trade_date=t.get("trade_date"),
                    memo=str(t.get("memo", "") or ""),
                    tags=t.get("tags", []) or [],
                    name=t.get("name"),
                )
                inserted.append({"index": i, "trade_id": tid, "trade": _review.get_trade(tid)})
            except Exception as e:
                errors.append({"index": i, "error": str(e), "input": t})
        return envelope(data={
            "inserted": inserted, "errors": errors, "skipped": skipped,
            "total": len(trades_in), "ok": len(inserted),
            "fail": len(errors), "dup": len(skipped),
        })

    # 单笔模式 (兼容) — 也跑归一化便于 name→code 兜底
    try:
        _refresh_name_lookup()
        norm = _normalize_trade_parsed({
            "code": payload.get("code", ""),
            "direction": payload.get("direction", "buy"),
            "price": payload.get("price", 0),
            "shares": payload.get("shares", 0),
            "total_amount": payload.get("total_amount", 0),
            "name": payload.get("name", ""),
            "occurred_at": payload.get("occurred_at", ""),
            "trade_date": payload.get("trade_date", ""),
            "memo": payload.get("memo", ""),
        })
        tid = _review.record_trade(
            code=norm.get("code", "") or payload.get("code", ""),
            direction=norm.get("direction", "buy"),
            price=norm.get("price") or float(payload.get("price", 0)),
            shares=norm.get("shares") or int(payload.get("shares", 0)),
            occurred_at=payload.get("occurred_at"),
            memo=payload.get("memo", ""),
            tags=payload.get("tags", []) or [],
            name=norm.get("name") or payload.get("name") or None,
            trade_date=payload.get("trade_date") or norm.get("trade_date"),
        )
        return envelope(data={"trade_id": tid, "trade": _review.get_trade(tid)})
    except Exception as e:
        log.exception("record_trade")
        return envelope(error=str(e), status_code=400)


# ── 截图 → 自动解析交易字段 (AI vision 优先, OCR 兜底;支持多笔 + 简单推理) ─────────
_REVIEW_IMAGE_PROMPT = """你是 A 股交易截图解析助手。从截图中提取**所有**买入或卖出交易的关键字段。
允许并鼓励做"简单推理"补全缺失字段。

【字段(每一笔交易)】
- direction: "buy" 或 "sell"
- code: 6 位数字股票代码,如 600519。**截图中如只有股票名没有代码,必须用下面的【股票名速查表】反查,填 6 位数字**
- name: 中文股票名称,如 "贵州茅台"
- price: 单股成交价(数字,保留 2 位小数)。**截图中如只有"成交金额/总金额"和"成交数量/股数",就用 total_amount ÷ shares 算出 price**
- total_amount: 成交总金额(元)。**可选字段**,如果截图中能看到"成交金额"或"总金额"数字,填这里,便于后端校验
- shares: 成交股数(整数, A 股 100 的倍数)
- occurred_at: ISO 8601 日期时间,如 "2026-07-10T14:32:00"。仅含截图可见的时间;日期不可见就空字符串
- trade_date: YYYYMMDD 格式, 同 occurred_at,留空字符串表示未知
- memo: 备注/标签/手续费等附加信息(<80 字)

【规则】
1. 只输出合法 JSON,不要解释、不要 markdown 包代码块
2. **一图多笔必须全部解析**,放进 trades 数组,按时间从早到晚排序
3. **简单推理(必须做)**:
   a) 截图只有"股票名"没有代码 → 用下方【股票名速查表】反查;查不到就留空让服务端兜底
   b) 截图只有"总金额 + 股数"没有单价 → price = total_amount ÷ shares,保留 2 位小数
   c) 截图只有"单价 + 总金额"没有股数 → shares = total_amount ÷ price,向下取整到 100 倍数
   d) 截图只有"单价 + 股数"没有总金额 → total_amount = price × shares
4. 截图模糊读不出也输出空数组 trades=[]
5. 不确定宁可留空,让服务端兜底,**绝对不要编造 6 位代码**(可留空,但不可瞎填)

【输出示例 — 单笔(完整)】
{"trades":[{"direction":"buy","code":"600519","name":"贵州茅台","price":1820.50,"shares":100,"total_amount":182050,"occurred_at":"2026-07-10T14:32:00","trade_date":"20260710","memo":""}]}

【输出示例 — 只有股票名(推理补 code)】
{"trades":[{"direction":"buy","code":"600519","name":"贵州茅台","price":1820.50,"shares":100,"total_amount":0,"occurred_at":"2026-07-10T14:32:00","trade_date":"20260710","memo":""}]}

【输出示例 — 只有总价+股数(推理补 price)】
{"trades":[{"direction":"buy","code":"","name":"贵州茅台","price":1820.50,"shares":100,"total_amount":182050,"occurred_at":"2026-07-10T14:32:00","trade_date":"20260710","memo":"成交金额 182,050"}]}

【输出示例 — 多笔】
{"trades":[
  {"direction":"buy","code":"600519","name":"贵州茅台","price":1820.50,"shares":100,"total_amount":182050,"occurred_at":"2026-07-10T14:32:00","trade_date":"20260710","memo":""},
  {"direction":"buy","code":"002747","name":"埃斯顿","price":46.75,"shares":100,"total_amount":4675,"occurred_at":"2026-07-10T10:42:00","trade_date":"20260710","memo":"机器人龙头"}
]}"""


def _ocr_trade_image(content: bytes) -> dict:
    """本地 OCR 兜底 — tesseract 抽文字 + 正则匹配字段。返回 {"trades":[...]}。

    多笔解析 (2026-07-12):
    - 券商 App 历史成交通常 2 行/笔:
        ① header = [direction] [name] [total_amount] [time]
        ② detail = [price] [shares] [date]
      把行配对 → 逐笔返回 (而不是只取最显眼的一笔)
    - 抽 name 后用 _NAME_LOOKUP 反查 code
    - tesseract 把"买入"识别成 ZA/Patt/sui 等 artifact,normalize 时看是否含 "卖"
    """
    import re as _re
    import subprocess as _sp
    try:
        r = _sp.run(
            ["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6"],
            input=content, capture_output=True, timeout=20,
        )
        text = (r.stdout or b"").decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning(f"OCR 启动失败: {e}")
        return {"trades": []}

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    noise_re = _re.compile(r"^(?:<.*>|.*当日成交.*|.*历史成交|查询时间|一个月内|已展示全部|^\d{1,2}:\d{2}\s|^\d{4}-\d{2}-\d{2}$)")
    clean_lines = [l for l in lines if not noise_re.match(l)]

    header_re = _re.compile(
        r"^(?P<dir>[^\s]*?)\s+"
        r"(?P<name>[一-龥]{2,6})\s+"
        r"(?P<total>[\d,]+\.?\d{0,2})\s+"
        r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)"
        r"\s*$"
    )
    detail_re = _re.compile(
        r"^(?P<price>\d+\.\d{2,4})\s+"
        r"(?P<shares>[\d,]+)\s+"
        r"(?P<date>\d{4}-\d{2}-\d{2})"
        r"\s*$"
    )

    def _norm_dir(raw: str) -> str:
        if "卖" in raw:
            return "sell"
        return "buy"

    lk: dict[str, str] = {}
    try:
        from .review import _NAME_LOOKUP as _lk
        lk = _lk
    except Exception:
        pass

    trades: list[dict] = []
    i = 0
    while i < len(clean_lines) - 1:
        h = header_re.match(clean_lines[i])
        d = detail_re.match(clean_lines[i + 1])
        if h and d:
            try:
                total = float(h.group("total").replace(",", ""))
                price = float(d.group("price"))
                shares = int(d.group("shares").replace(",", ""))
                name = h.group("name")
                code = lk.get(name, "") or ""
                time_str = h.group("time")
                if time_str.count(":") == 1:
                    time_str += ":00"
                trades.append({
                    "direction": _norm_dir(h.group("dir")),
                    "code": code,
                    "name": name,
                    "price": price,
                    "shares": shares,
                    "total_amount": total,
                    "occurred_at": f"{d.group('date')}T{time_str}",
                    "trade_date": d.group("date").replace("-", ""),
                    "memo": "",
                })
                i += 2
                continue
            except (ValueError, KeyError):
                pass
        i += 1

    if trades:
        log.info(f"OCR 多笔解析: {len(trades)} 笔 (源文本 {len(lines)} 行)")
        return {"trades": trades}

    # ── 兜底:如果一对模式没匹配,回到单笔逻辑 ──
    out = {
        "direction": "", "code": "", "name": "",
        "price": 0.0, "shares": 0, "total_amount": 0.0,
        "occurred_at": "", "trade_date": "", "memo": "",
    }
    if "买" in text and "卖" not in text.split("买")[0]:
        out["direction"] = "buy"
    elif "卖" in text:
        out["direction"] = "sell"

    m = _re.search(r"\b([0368]\d{5})\b", text)
    if m:
        out["code"] = m.group(1)

    name_hint = _re.search(r"(?:证券名称|股票名称|名称)[::\s]*([一-龥]{2,4})", text)
    if name_hint:
        out["name"] = name_hint.group(1)
    else:
        m2 = _re.search(r"[一-龥]{2,4}", text)
        if m2:
            out["name"] = m2.group(0)

    m_amt = _re.search(r"(?:成交金额|总金额|发生金额|交易金额|金额)[::\s¥￥]*([\d,]+\.?\d{0,2})", text)
    if m_amt:
        try:
            out["total_amount"] = float(m_amt.group(1).replace(",", ""))
        except ValueError:
            pass

    m_price = _re.search(r"(?:成交价|成交价格|价格)[::\s¥￥]*([\d]+\.\d{2,3})", text)
    if m_price:
        try:
            out["price"] = float(m_price.group(1))
        except ValueError:
            pass

    m = _re.search(r"(\d{2,6})\s*股", text)
    if m:
        try:
            sh = int(m.group(1))
            if sh > 0 and sh % 100 == 0:
                out["shares"] = sh
        except ValueError:
            pass

    m = _re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out["occurred_at"] = f"T{hh:02d}:{mm:02d}:00"
    m = _re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        out["trade_date"] = f"{y}{mo}{d}"
        out["occurred_at"] = f"{y}-{mo}-{d}{out['occurred_at']}" if out["occurred_at"] else ""

    if lines:
        out["memo"] = lines[0][:80]
    return {"trades": [out] if (out["code"] or out["name"] or out["price"]) else []}


def _call_minimax_vision(api_key: str, image_b64: str, mime: str) -> dict | None:
    """调 MiniMax M3 vision 解析截图。返回 {"trades":[...]} 或 None。

    兼容老返回(单笔平铺)在 _normalize_trades_parsed 里处理。
    Prompt 会注入【股票名速查表】帮助 AI 把"只有名字"的截图反查代码。
    R1+R3 已升级走 ai_client.call + parse_json_loose。
    """
    from . import ai_client
    lookup_hint = _format_name_lookup_for_prompt(limit=80)
    # R5 token governance: vision prompt 控制在 1500 token 内
    full_prompt = ai_client.truncate_to_tokens(
        _REVIEW_IMAGE_PROMPT + "\n\n【股票名速查表 (name→code)】\n" + lookup_hint,
        max_tokens=1500,
    )
    body = {
        "model": ai_client.default_model(),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": full_prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }],
        "temperature": 0.1,
    }
    spec = ai_client.CallSpec(
        url=ai_client.default_url(),
        headers=ai_client.headers(api_key),
        body=body,
        name="vision_review",
        model=ai_client.default_model(),
        timeout=80.0,  # M3 vision + reasoning 实际 25-60s,留 buffer
        attempts=(1, 2),
        # M3 vision + reasoning_content 会消耗大量 token;给到 8000/12000 留出空间
        max_tokens_alts=(8000, 12000),
    )
    try:
        _text, parsed, _info = ai_client.call(spec)
    except ai_client.AICallError as e:
        log.warning(f"vision 网络失败: {e}")
        return None

    if isinstance(parsed, dict):
        if isinstance(parsed.get("trades"), list):
            return parsed
        # 旧的"单笔平铺"
        if parsed.get("code") or parsed.get("price"):
            return {"trades": [parsed]}
        return {"trades": []}
    if isinstance(parsed, list):
        return {"trades": parsed}
    return {"trades": []}


def _normalize_trade_parsed(d: dict) -> dict:
    """把 AI/OCR 字段洗成 record_trade 接口需要的形状。

    增强:
    1) name → code 兜底 (查 _NAME_LOOKUP, 用户的近期交易 + 自选 + 热点)
    2) total_amount + shares → price 反推
    3) total_amount + price → shares 反推 (向下 100 倍数)
    """
    raw_dir = str(d.get("direction", "")).lower()
    direction = "buy" if raw_dir in ("buy", "买", "买入") else (
        "sell" if raw_dir in ("sell", "卖", "卖出") else "buy")
    raw_code = str(d.get("code", "")).strip()
    code = raw_code.zfill(6) if raw_code else ""
    if code and not code.isdigit():
        code = ""
    price = float(d.get("price") or 0)
    if price:
        price = round(price, 2)
    shares = int(d.get("shares") or 0)
    if shares and shares % 100 != 0:
        shares = max(100, (shares // 100) * 100)
    name = str(d.get("name", "")).strip()

    # ── 推理 1: code 缺 → 用 name 反查 ──
    if not code and name:
        looked = _NAME_LOOKUP.get(name)
        if looked:
            code = looked

    # ── 推理 2: price 缺 → total / shares ──
    total = float(d.get("total_amount") or 0)
    if not price and total > 0 and shares > 0:
        price = round(total / shares, 2)
    # ── 推理 3: shares 缺 → total / price (向下 100 倍数) ──
    if not shares and total > 0 and price > 0:
        sh = int(total / price)
        # 向下取整到 100 倍数
        sh = max(100, (sh // 100) * 100) if sh > 0 else 0
        shares = sh

    # ── 推理 4: total 缺 → price × shares (R3 增强: 2026-07-12 M3 vision 不返回 total 时兜底)
    if not total and price > 0 and shares > 0:
        total = round(price * shares, 2)

    return {
        "direction": direction,
        "code": code,
        "name": name,
        "price": price,
        "shares": shares,
        "total_amount": total,
        "occurred_at": str(d.get("occurred_at", "")).strip(),
        "trade_date": str(d.get("trade_date", "")).strip(),
        "memo": str(d.get("memo", "")).strip()[:200],
    }


def _normalize_trades_parsed(parsed: dict) -> list[dict]:
    """把 {"trades":[...]} 归一化成 record_trade 接口可用的 list。"""
    items = parsed.get("trades") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(_normalize_trade_parsed(it))
    return out


def _avg_confidence(trades: list[dict]) -> float:
    """给一组归一化交易计算平均 confidence (0..1)。"""
    if not trades:
        return 0.0
    confs = []
    for t in trades:
        c = 0.0
        if t.get("code"):   c += 0.30
        if t.get("name"):   c += 0.20
        if (t.get("price") or 0) > 0: c += 0.30
        if (t.get("shares") or 0) >= 100: c += 0.10
        if t.get("occurred_at") or t.get("trade_date"): c += 0.10
        confs.append(c)
    return round(sum(confs) / len(confs), 2)


# ═══════════════════════════════════════════════════
# 股票名 → 代码 速查表 (用于 AI/OCR 缺 code 时的兜底)
# 2026-07-12: 用户反馈 — 截图中经常只有股票名,要求自动补 code
# 数据源:
#   1) 最近 90 天 trades.code + trades.name (用户自己的实际票池)
#   2) 自选股 watchlist
#   3) 全市场 (data_layer.fetch_stock_list) — 但限制 1500 只热门
# 缓存在内存 5min
# ═══════════════════════════════════════════════════
_NAME_LOOKUP: dict[str, str] = {}
_NAME_LOOKUP_TS: float = 0.0

def _refresh_name_lookup() -> dict[str, str]:
    """5 分钟内复用内存缓存。"""
    global _NAME_LOOKUP, _NAME_LOOKUP_TS
    now = time.time()
    if _NAME_LOOKUP and (now - _NAME_LOOKUP_TS) < 300:
        return _NAME_LOOKUP
    out: dict[str, str] = {}
    try:
        # 1) 用户近期 trades
        from .. import cache_db as _cdb
        conn = _cdb._thread_conn()
        rows = conn.execute(
            "SELECT code, name FROM trades WHERE name IS NOT NULL AND name!='' "
            "ORDER BY id DESC LIMIT 200"
        ).fetchall()
        for code, name in rows:
            n = (name or "").strip()
            c = (code or "").strip().zfill(6)
            # 2026-07-12: 跳过占位 "000000" — 早期失败测试的脏数据
            if n and c and c.isdigit() and c != "000000" and n not in out:
                out[n] = c
        # 2) 自选
        rows = conn.execute("SELECT code, name FROM watchlist").fetchall()
        for code, name in rows:
            n = (name or "").strip()
            c = (code or "").strip().zfill(6)
            if n and c and c.isdigit() and c != "000000" and n not in out:
                out[n] = c
    except Exception as e:
        log.debug(f"refresh name lookup (db): {e}")
    try:
        # 3) 全市场 — 兜底,取全量 (2026-07-12 修正: 长电科技/数据港/华安证券 等中小盘在前 2000 之外)
        # 必须用 fetch_stock_list_all() 不是 fetch_stock_list() — 后者过滤掉创业板 300xxx/301xxx
        # 2026-07-12: 宁德时代 300750 因 filter 被丢,导致 name→code 反查失效
        from .. import data_layer as _dl
        all_market = _dl.fetch_stock_list_all() or []
        for code, name in all_market:
            n = (name or "").strip()
            c = (code or "").strip().zfill(6)
            if n and c and c.isdigit() and n not in out:
                out[n] = c
        log.info(f"name lookup: trades/watchlist={len(out)} + market={len(all_market)}")
    except Exception as e:
        log.debug(f"refresh name lookup (market): {e}")
    _NAME_LOOKUP = out
    _NAME_LOOKUP_TS = now
    log.info(f"股票名速查表刷新: {len(out)} 条")
    return out


def _format_name_lookup_for_prompt(limit: int = 80) -> str:
    """格式化一小撮最相关的 name→code 给 AI 提示(优先近期交易+自选,前 limit 条)。"""
    lk = _refresh_name_lookup()
    if not lk:
        return "（速查表暂无可用数据 — 截图中如有股票名没有代码,留空让服务端兜底）"
    items = list(lk.items())[:limit]
    body = " | ".join(f"{name}={code}" for name, code in items)
    return f"共 {len(lk)} 条(用户近期交易+自选+市场)。最相关前 {len(items)} 条:\n{body}"


@app.post("/api/review/parse_trade_image")
async def api_review_parse_trade_image(request: Request, file: UploadFile = File(...)):
    """截图 → AI 解析(失败自动 OCR) → 返回 trade 字段列表供前端批量预填。
    返回:{ok, data:{trades:[...], source:"ai"|"ocr", confidence:0..1}}

    R-sec-015: 加单 IP 滑动窗 (15 req/60s) + 单文件 16MB 上限, 防 SCANer OOM。
    R-sec-033: 加 Content-Length 头预检 + magic bytes 嗅探, 防伪扩展名/超大声明。
    C5: 上限从 6MB → 16MB,高分屏长截图常超 6MB。
    """
    _MAX_IMG_BYTES = 16 * 1024 * 1024
    # 1) 单 IP 滑动窗限频
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    with _img_window_lock:
        hits = _img_window.setdefault(ip, [])
        hits[:] = [t for t in hits if now - t < 60]
        if len(hits) >= 15:
            log.warning(f"[parse_trade_image] rate-limit ip={ip} ({len(hits)}/60s)")
            return envelope(error=f"图片上传限频 15 张/分钟,请稍后再试", status_code=429)
        hits.append(now)

    # 2) R-sec-033: Content-Length 头预检 — 客户端声明太大直接拒(不读 body)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > _MAX_IMG_BYTES:
        return envelope(error=f"图片超过 16MB,请压缩后再试", status_code=413)

    content = await file.read()
    if not content:
        return envelope(error="空文件", status_code=400)
    if len(content) > _MAX_IMG_BYTES:
        return envelope(error=f"图片超过 16MB,请压缩后再试", status_code=413)
    mime = (file.content_type or "").split(";")[0].strip()
    if mime not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
        name = (file.filename or "").lower()
        if name.endswith(".png"):
            mime = "image/png"
        elif name.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif name.endswith(".webp"):
            mime = "image/webp"
        else:
            return envelope(error=f"不支持的图片格式: {mime or '未知'}", status_code=400)

    # 3) R-sec-033: magic bytes 嗅探 — 文件头 8 字节必须匹配声明的 mime
    # PNG: 89 50 4E 47 0D 0A 1A 0A; JPEG: FF D8 FF; WEBP: "RIFF....WEBP"
    head = content[:12]
    def _has_png(h):   return h.startswith(b"\x89PNG\r\n\x1a\n")
    def _has_jpeg(h):  return h.startswith(b"\xff\xd8\xff")
    def _has_webp(h):  return h[:4] == b"RIFF" and h[8:12] == b"WEBP"
    ok_magic = {
        "image/png":  _has_png(head),
        "image/jpeg": _has_jpeg(head),
        "image/jpg":  _has_jpeg(head),
        "image/webp": _has_webp(head),
    }.get(mime, False)
    if not ok_magic:
        log.warning(f"[parse_trade_image] magic bytes mismatch ip={ip} mime={mime} head={head[:8].hex()}")
        return envelope(error=f"文件内容与扩展名不一致 (mime={mime})", status_code=400)

    parsed_obj: dict | None = None
    source = "ai"
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if api_key:
        import base64 as _b64
        image_b64 = _b64.b64encode(content).decode("ascii")
        parsed_obj = _call_minimax_vision(api_key, image_b64, mime)
    else:
        log.info("MINIMAX_API_KEY 未配置, 走 OCR")

    # AI 没识别出任何东西 或 没配 key → OCR 兜底
    ai_empty = (not parsed_obj) or (
        isinstance(parsed_obj, dict) and not parsed_obj.get("trades")
    )
    if ai_empty:
        source = "ocr"
        try:
            parsed_obj = _ocr_trade_image(content)
        except Exception as e:
            log.warning(f"OCR 兜底失败: {e}")
            parsed_obj = {"trades": []}

    trades = _normalize_trades_parsed(parsed_obj or {})
    if not trades:
        return envelope(error="AI 与 OCR 都未识别出有效字段,请手填",
                        data={"trades": [], "source": source, "missing": True})
    # 至少 1 笔关键字段都为空 → 不算有效
    has_key = any((t["code"] or t["price"]) for t in trades)
    if not has_key:
        return envelope(error="未识别出有效交易字段,请手填",
                        data={"trades": [], "source": source, "missing": True})

    conf = _avg_confidence(trades)
    return envelope(data={"trades": trades, "source": source, "confidence": conf,
                          "filename": file.filename or ""})


@app.put("/api/review/trades/{trade_id}")
async def api_review_update_trade(trade_id: int, payload: dict):
    try:
        ok = _review.update_trade(trade_id, **payload)
        return envelope(data={"updated": ok, "trade": _review.get_trade(trade_id)})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.delete("/api/review/trades/{trade_id}")
async def api_review_delete_trade(request: Request, trade_id: int):
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头", status_code=401)
    try:
        ok = _review.delete_trade(trade_id)
        return envelope(data={"deleted": ok})
    except Exception as e:
        return envelope(error=str(e), status_code=400)


@app.delete("/api/review/positions/{code}")
async def api_review_delete_position(request: Request, code: str):
    """删除某只股票的全部交易记录(用于清理误录入 / 移出持仓)。
    不可逆 — 同时清理 trade_reviews。
    """
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头", status_code=401)
    try:
        deleted = _review.delete_trades_by_code(code)
        return envelope(data={"deleted": deleted, "code": code})
    except Exception as e:
        log.exception("delete_position")
        return envelope(error=str(e), status_code=400)


@app.delete("/api/review/trades_all")
async def api_review_delete_all_trades(request: Request, confirm: str = Query("", description="必须传 'YES' 才执行")):
    """R12-A: 一键删除所有交易记录(用于清库重测 / 重新开始)。
    不可逆 — 同时清理 trade_reviews。
    安全护栏: 必须传 confirm=YES 才执行, 否则 400。
    """
    if not _check_admin_token(request):
        return envelope(error="需要 X-Admin-Token 头", status_code=401)
    if confirm != "YES":
        return envelope(error="必须传 confirm=YES 才允许清空", status_code=400)
    try:
        from .. import cache_db as _cdb
        conn = _cdb._thread_conn()
        n_rev = conn.execute("DELETE FROM trade_reviews").rowcount
        n_trd = conn.execute("DELETE FROM trades").rowcount
        conn.commit()
        # 清 Redis AI 缓存避免污染
        try:
            from .. import cache_store as _cs
            store = _cs.get_store()
            for k in list(store.scan("*")):
                if "ai" in k.lower() or "review" in k.lower():
                    store.delete(k)
        except Exception:
            pass
        log.warning(f"[R12-A] 清空所有交易: trades={n_trd} reviews={n_rev}")
        return envelope(data={"deleted_trades": n_trd, "deleted_reviews": n_rev})
    except Exception as e:
        log.exception("delete_all_trades")
        return envelope(error=str(e), status_code=400)


@app.post("/api/review/trades/{trade_id}/review")
async def api_review_run(trade_id: int, force: bool = False, bg: BackgroundTasks = None):
    """AI 复盘:已复盘(force=False) 同步返缓存;未复盘/force=True 走后台任务,
    立刻返 {queued:true} 不阻塞前端。2026-07-14 修:之前 to_thread 同步等 75s,
    AI 一跑就锁按钮 + 占线程池,批量 39 笔一起发直接拖垮读路径。"""
    try:
        # 1) 缓存快路径:已有复盘 → 直接同步返回
        if not force:
            cached = await to_thread(_review.get_cached_review, trade_id)
            if cached:
                return envelope(data=cached)
        # 2) 真正要跑 AI → 丢后台,立即返回
        if bg is not None:
            bg.add_task(_bg_review_trade, trade_id, force)
        else:
            # 兜底(单元测试/同步调用):异步线程池启动
            import threading
            threading.Thread(target=_bg_review_trade, args=(trade_id, force), daemon=True).start()
        return envelope(data={"queued": True, "trade_id": trade_id, "status": "pending"})
    except Exception as e:
        log.exception("review_trade_dispatch")
        return envelope(error=str(e), status_code=500)


def _bg_review_trade(trade_id: int, force: bool) -> None:
    """后台跑 AI 复盘,异常一律吞掉只记日志,不污染读路径。
    注意:必须自管 _init_db,不复用任何主线程的连接句柄。"""
    try:
        log.info(f"bg_review_trade start trade={trade_id} force={force}")
        _review.review_trade(trade_id, force=force)
        log.info(f"bg_review_trade done trade={trade_id}")
    except Exception as e:
        log.exception(f"bg_review_trade failed trade={trade_id}")


@app.get("/api/review/trades/{trade_id}/status")
async def api_review_status(trade_id: int):
    """复盘状态:前端轮询判断是否完成。轻量,不调 AI。"""
    try:
        st = await to_thread(_review.get_review_status, trade_id)
        return envelope(data=st)
    except Exception as e:
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
async def api_review_next_picks(force: int = 0, relax: int = 0):
    """次日选股 + 用户错模式风险。

    R50-SPEED: 缓存 TTL 30s → 300s;
    无缓存时秒回空 picks + 后台异步算 (用户永不空等);
    有缓存时 300s 内秒回;陈旧缓存时回返 + 后台异步刷;
    加 _inflight 锁避免并发时多线程重算。

    R-relax-2026-07-14: relax=0/1/2 3 档 (默认 / 放宽 / 极宽松),每档独立缓存。
    默认 relax=0 不放宽,只有用户手动点按钮才传 1/2。
    """
    # 每 relax 档独立缓存 (放宽 / 默认 不互相污染)
    cache_key = f"relax{relax}"
    cached = _NEXT_PICKS_CACHE.get(cache_key)
    now_ts = time.time()
    if not force and cached and (now_ts - cached["ts"]) < 300:
        return envelope(data=cached["data"], meta={"cache_hit": True, "age_seconds": int(now_ts - cached["ts"]), "relax": relax})
    # 防并发:已经在跑 → 直接回陈旧 (不阻塞)
    if _NEXT_PICKS_INFLIGHT.get(cache_key):
        if cached:
            return envelope(data=cached["data"], meta={"stale_seconds": int(now_ts - cached["ts"]), "in_flight": True})
        return envelope(data={"picks": [], "user_patterns": []}, meta={"in_flight": True})

    async def _bg_compute():
        """后台异步算,完成后写缓存。"""
        _NEXT_PICKS_INFLIGHT[cache_key] = True
        try:
            # relax≥1 会走 screen(8s)→ 全A兜底(qt.gtimg curl ~5s),给 22s 让兜底跑完
            _to = 22 if relax >= 1 else 12
            result = await asyncio.wait_for(to_thread(_review.next_day_picks, relax), timeout=_to)
            if result:
                _NEXT_PICKS_CACHE[cache_key] = {"data": result, "ts": time.time()}
                log.info(f"next_picks relax={relax} 后台计算完成 ({len(result.get('picks', []))} picks)")
        except asyncio.TimeoutError:
            log.warning(f"next_picks relax={relax} 后台超时,占位缓存 30s 后允许重试")
            if not cached:
                _NEXT_PICKS_CACHE[cache_key] = {"data": {"picks": [], "user_patterns": []}, "ts": now_ts}
        except Exception as e:
            log.warning(f"next_picks relax={relax} 后台失败: {e}")
        finally:
            _NEXT_PICKS_INFLIGHT[cache_key] = False

    # 有 force → 后台重算 (不阻塞前端);立即返回:有缓存回缓存,无则空+computing。
    # 前端点按钮后秒回,随后用 force=0 轮询读缓存直到 picks 就绪。
    if force:
        if not _NEXT_PICKS_INFLIGHT.get(cache_key):
            asyncio.ensure_future(_bg_compute())
        if cached:
            return envelope(data=cached["data"], meta={"stale_seconds": int(now_ts - cached["ts"]), "computing": True, "relax": relax})
        return envelope(data={"picks": [], "user_patterns": []}, meta={"computing": True, "relax": relax})

    # 无 force 时:后台异步算,前端秒回 (陈旧/空 picks)
    asyncio.ensure_future(_bg_compute())
    if cached:
        return envelope(data=cached["data"], meta={"stale_seconds": int(now_ts - cached["ts"]), "refreshing": True, "relax": relax})
    return envelope(data={"picks": [], "user_patterns": []}, meta={"refreshing": True, "relax": relax})


_NEXT_PICKS_CACHE: dict = {}
_NEXT_PICKS_INFLIGHT: dict = {}


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


class StockHistoryRequest(BaseModel):
    code: str
    name: str | None = None


@app.get("/api/watchlist")
async def api_watchlist_list():
    """列出全部自选股 + 实时行情 + 最新 AI 建议(同日有效)。"""
    try:
        items = await asyncio.to_thread(_watchlist.list_with_ai_snapshot)
        return envelope(data={"items": items, "count": len(items)})
    except Exception as e:
        log.exception("watchlist list")
        return envelope(error=str(e), status_code=500)


@app.get("/api/trade_dates")
async def api_trade_dates(limit: int = 60):
    """返回最近 N 个交易日的 YYYY-MM-DD 列表 + 最临近交易日。

    关键:必须既含过去交易日(用于 snap/prev)又含未来(用于 next,允许小幅未来日期缓存)。
    之前 limit=60 全取了未来日期,导致用户选历史日期 snap 到未来日期上 (bug 2026-07-11)。
    """
    try:
        from .. import multi_source_fetchers as msf
        all_dates = await asyncio.to_thread(msf.fetch_trade_dates)
        if not all_dates:
            return envelope(error="交易日历不可用", data={"dates": [], "today": None, "last_trade_date": None})

        sorted_desc = sorted(all_dates, reverse=True)
        today = datetime.date.today().strftime("%Y-%m-%d")

        # 1) 过去部分:取今天及之前的最近 limit 天 (倒序)
        past_dates = [d for d in sorted_desc if d <= today][:limit]
        # 2) 未来部分:取今天之后的最近 30 天 (倒序,允许小幅缓存)
        future_dates = [d for d in sorted_desc if d > today][:30]
        # 合并:未来在前(降序)、过去在后,这样浏览器 Set/Array 都好查
        recent = future_dates + past_dates

        # 最临近且 <= 今日的交易日 (今日非交易时回退用)
        last_trade = past_dates[0] if past_dates else None

        return envelope(data={
            "dates": recent,
            "past_dates": past_dates,        # 仅过去日期,前端 snap/prev 用
            "future_dates": future_dates,    # 仅未来日期,前端 next 用
            "today": today,
            "last_trade_date": last_trade,
            "is_today_trade_day": today in all_dates,
        })
    except Exception as e:
        log.exception("trade_dates")
        return envelope(error=str(e), data={"dates": [], "today": None, "last_trade_date": None})


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


# ── 个股查询历史 (2026-07-11) — 服务端永久化,跨浏览器/跨设备同步 ──
# 之前用 localStorage 浏览器清数据就丢;现在 SQLite,清缓存/换设备/隐身
# 模式都能保留
@app.get("/api/stock_history")
async def api_stock_history_list(limit: int = Query(50, ge=1, le=200)):
    try:
        from .. import cache_db
        rows = cache_db.list_stock_history(limit=limit)
        return envelope(data={"history": rows, "count": len(rows)})
    except Exception as e:
        log.exception("stock_history list")
        return envelope(error=str(e))


@app.post("/api/stock_history")
async def api_stock_history_add(req: StockHistoryRequest):
    """记录一次个股查询(查询时/主动加自选时都触发)。"""
    try:
        code = (req.code or "").strip()
        name = (req.name or "").strip()
        if not code:
            return envelope(error="code 必填", status_code=400)
        from .. import cache_db
        cache_db.record_stock_query(code, name=name)
        rows = cache_db.list_stock_history(limit=50)
        return envelope(data={"history": rows, "count": len(rows)})
    except Exception as e:
        log.exception("stock_history add")
        return envelope(error=str(e))


@app.delete("/api/stock_history/{code}")
async def api_stock_history_remove(code: str):
    try:
        from .. import cache_db
        ok = cache_db.remove_stock_history(code)
        return envelope(data={"removed": ok})
    except Exception as e:
        log.exception("stock_history remove")
        return envelope(error=str(e))


@app.delete("/api/stock_history")
async def api_stock_history_clear():
    try:
        from .. import cache_db
        n = cache_db.clear_stock_history()
        return envelope(data={"cleared": n})
    except Exception as e:
        log.exception("stock_history clear")
        return envelope(error=str(e))


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

    # R2 prompt 注入防御: 把用户喂的 ctx 用 boundary 包住
    from . import ai_client
    user_content_safe = ai_client.wrap_prompt("ctx", user_content)
    # R5 token governance
    user_content_safe = ai_client.truncate_to_tokens(user_content_safe, max_tokens=900)

    spec = ai_client.CallSpec(
        url=ai_client.default_url(),
        headers=ai_client.headers(api_key),
        body={
            "model": ai_client.default_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content_safe},
            ],
            "temperature": 0.3,
        },
        name="watchlist",
        model=ai_client.default_model(),
        timeout=30.0,
        attempts=(1, 2),
        max_tokens_alts=(1500, 2500),
    )
    try:
        _text, parsed, _info = await asyncio.to_thread(ai_client.call, spec)
    except ai_client.AICallError as e:
        log.warning(f"watchlist AI 失败 {code}: {e}")
        return {}

    parsed = ai_client.normalize_ai_verdict(parsed) if parsed else {}
    if not parsed:
        return {}
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
    30s 内存缓存; refresh=true 强制重拉;超时/失败时回退到陈旧缓存(<=10min)
    """
    from ..dragons import score_dragons
    cache_key = f"dragons_{date or 'today'}"
    now = datetime.datetime.now()
    cached = _DRAGONS_CACHE.get(cache_key)
    # R50-SPEED: refresh=1 强制刷新也要快速 — 有缓存时先回陈旧+后台刷新,绝不阻塞
    if cached:
        age = (now - cached["ts"]).total_seconds()
        # 命中 fresh 窗口
        if not refresh and age < 180:
            return envelope(data=cached["data"])
        # 陈旧 (<10min) 或 refresh=1: 先秒回陈旧,后台刷新 — 用户永不空等 9s
        if age < 600 or refresh:
            if _DRAGONS_INFLIGHT.get(cache_key):
                return envelope(data=cached["data"], meta={"stale_seconds": int(age), "in_flight": True})
            async def _bg_refresh():
                try:
                    _DRAGONS_INFLIGHT[cache_key] = True
                    fresh = await asyncio.wait_for(to_thread(score_dragons, date), timeout=15)
                    if fresh:
                        _DRAGONS_CACHE[cache_key] = {"data": fresh, "ts": datetime.datetime.now()}
                        log.info(f"dragons 后台刷新完成 (date={date})")
                except Exception as e:
                    log.debug(f"dragons 后台刷新失败: {e}")
                finally:
                    _DRAGONS_INFLIGHT[cache_key] = False
            asyncio.ensure_future(_bg_refresh())
            return envelope(data=cached["data"], meta={"stale_seconds": int(age), "refreshing": True})
    try:
        result = await asyncio.wait_for(
            to_thread(score_dragons, date),
            timeout=15,                                       # 2026-07-12: 45→15,加陈旧缓存兜底
        )
    except asyncio.TimeoutError:
        log.warning(f"dragons 超时 15s (date={date}) → 尝试陈旧缓存")
        stale = _DRAGONS_CACHE.get(cache_key)
        if stale and (datetime.datetime.now() - stale["ts"]).total_seconds() < 600:
            log.info(f"dragons 返回陈旧缓存 ({int((datetime.datetime.now() - stale['ts']).total_seconds())}s)")
            return envelope(data=stale["data"], meta={"stale_seconds": int((datetime.datetime.now() - stale["ts"]).total_seconds())})
        return envelope(error="龙头评分超时 15s,无陈旧缓存可用", data={
            "top10": [], "all": [], "mainline": [],
            "sentiment": {"label": "-", "zt_count": 0, "max_streak": 0, "streak_dist": {}},
            "stats": {"reason": "timeout"},
        })
    if result:
        _DRAGONS_CACHE[cache_key] = {"data": result, "ts": datetime.datetime.now()}
    return envelope(data=result or {})


_DRAGONS_CACHE: dict[str, dict] = {}
_DRAGONS_INFLIGHT: dict[str, bool] = {}


@app.post("/api/optimize")
async def api_optimize(request: Request):
    from ..optimizer import run_optimize
    # P1-audit-2026-07-15: 重型优化,加 admin token 防 DoS
    if not _check_admin_token(request):
        raise HTTPException(status_code=401, detail={"ok": False, "error": "admin token required"})
    # 硬超时 120s: 优化器 10 次迭代跑完常 1-3min,沙箱数据源挂时不能拖死 server
    # (2026-07-12 audit 发现该 endpoint 之前无超时保护)
    try:
        result = await asyncio.wait_for(to_thread(run_optimize), timeout=120)
    except asyncio.TimeoutError:
        log.warning("optimize 超时 120s")
        return envelope(error="优化器超时 120s, 请稍后重试或减小迭代次数", data={
            "best_params": None, "history": [], "stats": {"reason": "timeout"},
        })
    return envelope(data=result or {})


def _check_sse_origin(request: Request) -> Response | None:
    """R-perf-031: SSE 鉴权兜底 — EventSource 无法带自定义 Header(没有 Authorization)。
    仅放行: 同源浏览器 / Origin 在白名单 / 无 Origin(server-to-server)。
    不通过则返 403 JSONResponse。
    """
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return None  # 无 origin:curl / 内网调用,放行
    if origin in _CORS_ALLOWED_ORIGINS:
        return None
    log.warning(f"[sse-auth] 拒收 SSE {request.url.path} origin={origin!r}")
    return JSONResponse(
        {"ok": False, "error": "origin not allowed for SSE", "trace_id": getattr(request.state, "trace_id", "-")},
        status_code=403,
    )


@app.get("/api/stream/optimize")
async def stream_optimize(request: Request, iterations: int | None = None):
    """SSE: 优化器实时进度推送。客户端断开 → 后台停止 (通过 cancellation)。
    旧版 POST /api/optimize 兼容保留（无进度反馈，30min 跑完才返）。
    """
    if (r := _check_sse_origin(request)) is not None:
        return r
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
async def stream_screen(request: Request, date: str | None = None, mode: str = "live"):
    """SSE:屏幕里 scan 时的进度。如果 run_stock_screen 内部没有 hook,就只推 done。

    2026-07-09: 增加 AI 打分阶段推送
      - phase="rule_done" 规则筛选完成, 准备进入 AI
      - phase="ai_done" 单只 AI 完成 (data: {code, ai})
      - phase="ai_aggregate" 综合榜完成 (data: {ranking, overall_view})
      - phase="done" 整个跑完
    """
    if (r := _check_sse_origin(request)) is not None:
        return r
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
async def stream_backtest(request: Request, req: BacktestRequest):
    """SSE:回测进度。回测 loop 内部每 20 天打 log,前端这里每 2s 轮询一次 stats。"""
    if (r := _check_sse_origin(request)) is not None:
        return r
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
    # R1-B 修复: 路径穿越防护 — 拒绝 ../ 与绝对路径 + 限定为 .json 后缀
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "invalid report name")
    p = (cfg.REPORT_DIR / name).resolve()
    try:
        p.relative_to(cfg.REPORT_DIR.resolve())
    except ValueError:
        raise HTTPException(404)
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
        # 2026-07-14: /api/hotspot 已删,板块页预热移除
        ("/api/all_stocks/board?page_size=30", 20),                  # 全 A 风向首屏 (默认成交额↓)
        # R10-perf (2026-07-15): 预热 3 个常用排序,避免用户首次切换时 cold-start
        ("/api/all_stocks/board?page_size=30&sort=change_pct", 18),  # 涨幅↓
        ("/api/all_stocks/board?page_size=30&sort=turnover", 18),    # 换手↓
        ("/api/all_stocks/board?page_size=30&sort=main_fund_inflow", 18),  # 主力净流入↓
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


async def _continuous_warmer():
    """2026-07-13 Round 10: 持续保活 — 每 60s 刷一次核心慢接口,用户永不冷启。
    沙箱 eastmoney 挂时跳过失败的; 已有 SWR 兜底保证秒回。
    """
    warmer_log = logging.getLogger("tuixue.warmer")
    import httpx as _httpx_w
    bind_host = os.environ.get("TUIXUE_PREHEAT_HOST", "127.0.0.1")
    bind_port = int(os.environ.get("TUIXUE_PREHEAT_PORT", "7799"))
    base = f"http://{bind_host}:{bind_port}"
    paths = [
        ("/api/dragons", 20),
        ("/api/market/overview", 8),
        # R18 (2026-07-14): 全 A 风向常用 sort 保活,避免切 sort 触发 5s cold-start
        ("/api/all_stocks/board?sort=amount&order=desc&page_size=30",            15),
        ("/api/all_stocks/board?sort=change_pct&order=desc&page_size=30",       15),
        ("/api/all_stocks/board?sort=turnover&order=desc&page_size=30",         15),
        ("/api/all_stocks/board?sort=main_fund_inflow&order=desc&page_size=30",  15),
    ]
    interval = 60.0
    await asyncio.sleep(10)  # 让启动预热先跑完
    while True:
        try:
            timeout = _httpx_w.Timeout(connect=2.0, read=15.0, write=5.0, pool=3.0)
            async with _httpx_w.AsyncClient(timeout=timeout, base_url=base) as client:
                for path, sec in paths:
                    try:
                        t0 = time.time()
                        r = await client.get(path)
                        ok = r.status_code == 200
                        mark = "✓" if ok else f"✗({r.status_code})"
                        warmer_log.info(f"[保活] {mark} {path} ({time.time()-t0:.1f}s)")
                    except Exception as e:
                        warmer_log.info(f"[保活失败] {path}: {type(e).__name__}")
                        # 沙箱挂时不报警,继续
        except Exception as e:
            warmer_log.info(f"[保活] 外层异常: {e}")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _on_startup_preheat():
    """uvicorn 启动后立即调 1 次预热, 不阻塞 server"""
    # R-cfg-027: 启动期依赖校验 — 缺关键 env / 不可写 / 配置冲突 立即 warn(不致命)
    try:
        _startup_dependency_check()
    except Exception as e:
        log.warning(f"[startup-deps] 校验异常: {e}")


def _startup_dependency_check() -> None:
    """校验启动期关键依赖。任一缺失只 warn 不抛 — server 仍能跑(降级)。"""
    issues: list[str] = []
    # 1) MINIMAX_API_KEY
    if not os.environ.get("MINIMAX_API_KEY", "").strip():
        issues.append("MINIMAX_API_KEY 未配置 → AI 类端点全部不可用")
    # 2) DB 路径可写
    try:
        from .. import cache_db as _cdb
        db_path = Path(_cdb.__file__).resolve().parent.parent.parent / "data" / "tuixue.db"
        if not db_path.exists():
            issues.append(f"DB 文件不存在: {db_path}")
        elif not os.access(str(db_path.parent), os.W_OK):
            issues.append(f"DB 目录不可写: {db_path.parent}")
    except Exception as e:
        issues.append(f"DB 检查失败: {e}")
    # 3) 静态目录可读
    try:
        if not STATIC_DIR.exists() or not STATIC_DIR.is_dir():
            issues.append(f"STATIC_DIR 不存在: {STATIC_DIR}")
    except Exception as e:
        issues.append(f"STATIC_DIR 检查失败: {e}")
    # 4) BACKUP_DIR 可写(如启用)
    try:
        from .. import config as _cfg
        bd = Path(getattr(_cfg, "BACKUP_DIR", STATIC_DIR.parent / "backups"))
        if bd and not os.access(str(bd), os.W_OK):
            issues.append(f"BACKUP_DIR 不可写: {bd}")
    except Exception:
        pass
    if issues:
        for line in issues:
            log.warning(f"[startup-deps] ⚠ {line}")
        log.warning(f"[startup-deps] 共 {len(issues)} 项问题,server 降级运行")
    else:
        log.info("[startup-deps] ✓ 关键依赖全部就绪")
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
    else:
        asyncio.create_task(_preheat_cache_on_startup())

    # P1-5 · tunnel 自愈 loop (30s 轮询, tunnel 死了自动重连)
    app.state._tunnel_heal_task = asyncio.create_task(_tunnel_heal_loop())
    log.info("[tunnel-heal] 后台自愈 loop 已注册")
    # 3) R3: 后台 TTL 扫描线程 (60s 一次清理过期 + 记录统计)
    import threading as _t
    def _sweeper():
        while True:
            time.sleep(60.0)
            try:
                for c in (_cache_spot, _cache_quote, _cache_kline, _cache_fund,
                         _cache_overview, _cache_global, _cache_layer):
                    n = c.sweep_expired()
                    if n:
                        log.debug(f"[cache sweep] {c.__class__.__name__} cleared {n} expired")
            except Exception as e:
                log.debug(f"[cache sweep] error: {e}")
    _t.Thread(target=_sweeper, name="ttl-sweeper", daemon=True).start()
    log.info("[R3] TTL 缓存后台扫描已启动 (60s 周期)")
    # 4) R8: 每日自动备份线程 (默认 03:00 本地时, 避开交易时段)
    def _daily_backup():
        """每天 03:00 本地时跑一次 backup_db(),失败也无所谓。"""
        import datetime as _dt
        ran_today = None
        while True:
            now = _dt.datetime.now()
            target = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if target <= now:
                target += _dt.timedelta(days=1)
            wait_sec = (target - now).total_seconds()
            # 最长 sleep 切片为 5min, 防止 server 重启间隔太长被跳过
            slept = 0.0
            while slept < wait_sec:
                chunk = min(300.0, wait_sec - slept)
                time.sleep(chunk)
                slept += chunk
            try:
                from .. import cache_db as _cdb
                path = _cdb.backup_db()
                if path:
                    log.warning(f"[R8] 每日备份完成: {path}")
                else:
                    log.warning("[R8] 每日备份失败 (backup_db 返回 None)")
            except Exception as e:
                log.warning(f"[R8] 每日备份异常: {e}")
    _t.Thread(target=_daily_backup, name="daily-backup", daemon=True).start()
    log.info("[R8] 每日备份守护已启动 (本地 03:00 触发)")
    # 2026-07-13 Round 10: 持续保活循环
    asyncio.create_task(_continuous_warmer())
    # 2026-07-13: 选股 poller (14:30-15:00 才真跑, 1s/tick)
    try:
        from . import screener as _scr
        asyncio.create_task(_scr.screener_poller_loop())
        log.warning("[选股] screener_poller_loop 已启动")
    except Exception as e:
        log.warning(f"[选股] poller 启动失败: {e}")


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

    # 把当前端口加入 CORS 白名单 — 否则非 GET 请求(如录入 POST)会被 origin 校验拦下
    _CORS_ALLOWED_ORIGINS.add(f"http://localhost:{args.port}")
    _CORS_ALLOWED_ORIGINS.add(f"http://127.0.0.1:{args.port}")

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
        # uvloop: Linux/macOS 原生加速,~60% 更高吞吐(uvloop 0.22+ 稳定)
        try:
            import uvloop
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        except ImportError:
            pass
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
async def api_stream_review(request: Request, trade_id: int):
    """SSE 流:AI 复盘进度推送。
    事件类型:
      - 'start'   复盘开始
      - 'progress' 阶段消息(build_ctx / ai_call / parse)
      - 'rules'    铁律分析片段
      - 'done'     完成(带最终结果)
      - 'error'    失败

    R-perf-017: 客户端断网立即检测 → 提前 cancel ai executor, 不浪费 worker
    """
    if (r := _check_sse_origin(request)) is not None:
        return r
    import functools as _ft
    async def event_gen():
        # 1) start
        yield {"event": "start", "data": json.dumps({"trade_id": trade_id, "ts": time.time()}, ensure_ascii=False)}
        await asyncio.sleep(0.05)
        # 客户端断开 → 直接结束,不调 AI
        if await request.is_disconnected():
            log.info(f"[stream_review] client disconnected before ai_call trade_id={trade_id}")
            return
        try:
            # 2) build context
            yield {"event": "progress", "data": json.dumps({"stage": "build_ctx", "msg": "拉盘面 K线/资金/游资..."}, ensure_ascii=False)}
            await asyncio.sleep(0.05)
            # 3) ai call (同步执行 review_trade; force=False 优先用缓存)
            loop = asyncio.get_event_loop()
            ai_task = loop.run_in_executor(
                _EXECUTOR, _ft.partial(_review.review_trade, trade_id, force=False)
            )
            # 在等待的同时轮询是否断网 → 断网就 cancel ai_task 省 worker
            while not ai_task.done():
                if await request.is_disconnected():
                    log.info(f"[stream_review] client disconnected mid-ai trade_id={trade_id}, cancelling")
                    ai_task.cancel()
                    return
                await asyncio.sleep(0.5)
            result = ai_task.result()
            if await request.is_disconnected():
                return
            # 4) rules 流式推送
            for r in result.get("rules_failed", []):
                if await request.is_disconnected():
                    return
                yield {"event": "rule_failed", "data": json.dumps(r, ensure_ascii=False)}
                await asyncio.sleep(0.05)
            for r in result.get("rules_passed", []):
                if await request.is_disconnected():
                    return
                yield {"event": "rule_passed", "data": json.dumps(r, ensure_ascii=False)}
                await asyncio.sleep(0.03)
            # 5) done
            yield {"event": "done", "data": json.dumps(result, ensure_ascii=False, default=str)}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("stream_review")
            yield {"event": "error", "data": json.dumps({"err": str(e)[:300]}, ensure_ascii=False)}
    return EventSourceResponse(event_gen(), ping=15)


# ═══════════════════════════════════════════
# /api/hotspot 已删除 (2026-07-14 应用户要求)
# 数据源 web/rotation.py 也已删除
# ═══════════════════════════════════════════


if __name__ == "__main__":
    main()
