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
import re as _re
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

import importlib as _importlib
from . import ai_client

class _LazyModule:
    def __init__(self, name: str):
        self._name = name
        self._module = None

    def _get(self):
        if self._module is None:
            self._module = _importlib.import_module(f"{__package__}.{self._name}")
        return self._module

    def __getattr__(self, attr):
        return getattr(self._get(), attr)


fund_flow = _LazyModule("fund_flow")
seat_lookup = _LazyModule("seat_lookup")
news_lookup = _LazyModule("news_lookup")
ai_chat = _LazyModule("ai_chat")
_review = _LazyModule("review")
_watchlist = _LazyModule("watchlist")
from .. import cache_store
from ._constants import (
    API_DEFAULT_TIMEOUT, API_HEALTH_TIMEOUT, API_VERSION_TIMEOUT, API_META_TIMEOUT,
    API_INTEL_TIMEOUT, API_LONG_TIMEOUT, API_AI_TIMEOUT, API_BACKTEST_STREAM_TIMEOUT,
    CACHE_TTL_SPOT, CACHE_TTL_QUOTE, CACHE_TTL_KLINE, CACHE_TTL_FUND,
    CACHE_TTL_OVERVIEW, CACHE_TTL_GLOBAL, CACHE_TTL_LAYER, CACHE_TTL_SEAT_BD,
    CACHE_TTL_INTRADAY, CACHE_TTL_SECTOR, CACHE_TTL_NEWS,
    RATE_LIMIT_DEFAULT_MAX, RATE_LIMIT_DEFAULT_WINDOW,
    RATE_LIMIT_AI_MAX, RATE_LIMIT_AI_WINDOW,
    RATE_LIMIT_BACKTEST_MAX, RATE_LIMIT_BACKTEST_WINDOW,
    STALE_QUOTE_SEC, STALE_FUND_SEC,
)

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
# R-A1 (2026-07-19): API 契约统一 — envelope helpers
# 之前 200+ 端点返裸 dict,前端必须了解每条端点字段名,违反"前端只认 envelope 协议"
# 现在所有 endpoint 返 {_envelope_ok(data, **meta)} 或 {_envelope_err(CODE, msg, status)}
# 前端 core.js api() 自动 envelope-parse,无需每端点单独 schema
# ───────────────────────────────────────────────────────────
# Error code 常量 — 跨端点稳定的 machine-readable code (用于前端分类处理)
CODE_OK              = "OK"
CODE_TIMEOUT         = "TIMEOUT"
CODE_INVALID_INPUT   = "INVALID_INPUT"
CODE_NOT_FOUND       = "NOT_FOUND"
CODE_UNAUTHORIZED    = "UNAUTHORIZED"
CODE_FORBIDDEN       = "FORBIDDEN"
CODE_UPSTREAM_FAIL   = "UPSTREAM_FAIL"
CODE_RATE_LIMITED    = "RATE_LIMITED"
CODE_INTERNAL        = "INTERNAL"
CODE_DEGRADED        = "DEGRADED"  # 部分数据可用,字段 _degraded=true 标记

# HTTP code → 我们的 error code 映射 (用于错误码标准化)
_HTTP_TO_CODE = {
    400: CODE_INVALID_INPUT,
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    408: CODE_TIMEOUT,
    422: CODE_INVALID_INPUT,
    429: CODE_RATE_LIMITED,
    500: CODE_INTERNAL,
    502: CODE_UPSTREAM_FAIL,
    503: CODE_UPSTREAM_FAIL,
    504: CODE_TIMEOUT,
}


def _envelope_ok(data: Any = None, **meta) -> dict:
    """成功信封: {ok: True, data, ...meta, ts}"""
    out: dict = {"ok": True, "ts": _now_iso()}
    if data is not None:
        out["data"] = data
    for k, v in meta.items():
        if k not in out:
            out[k] = v
    return out


def _envelope_err(code: str, message: str, **extra) -> dict:
    """错误信封: {ok: False, error: {code, message}, ts, ...extra}
    前端 api() 解析时 if (!resp.ok) throw {code, message, ...extra}"""
    err = {"code": code or CODE_INTERNAL, "message": message or "未知错误"}
    out: dict = {"ok": False, "error": err, "ts": _now_iso()}
    out.update(extra)
    return out


def _now_iso() -> str:
    """统一 ISO8601 时间戳 (秒级, +08:00) — 前端可 Date.parse 一次过"""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# R-B13 (2026-07-19): 统一分页信封
# 之前散落 {items, count, has_more, next_offset, total_available} 各端点字段名不同
# 新端点统一用 _paginated(items, total, page, page_size) → envelope.data.{items, pagination}
# 旧端点渐进迁移,前端可读 pagination.page / .page_size / .total / .has_more
def _paginated(items: list, total: int, page: int = 1, page_size: int = 50) -> dict:
    """构造分页响应: {items, pagination: {page, page_size, total, total_pages, has_more}}"""
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 0
    has_more = (page * page_size) < total
    return {
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_more": has_more,
        },
    }


# R-B16 (2026-07-19): API 版本头 — 给前端/中间件识别
_API_VERSION = "v3-100rounds-batchA"


# R-C24 (2026-07-19): log helper — 自动 [trace=X] 前缀
# 之前 158 个 log 语句很多不带 trace_id,出问题时定位难
# 新代码: log_with_trace(request).warning("xxx") 自动拼 trace_id
# 老代码逐步迁移, 不强制
def _log_with_trace(request: Request):
    """返回带 trace_id 前缀的 logger wrapper。
    用法: _log_with_trace(request).warning("用户未登录")
    输出: [trace=e586e3d091c0] [warning] 用户未登录
    """
    tid = getattr(request.state, "trace_id", "-") if request else "-"
    class _L:
        def __getattr__(self, name):
            level = getattr(log, name, None)
            if not level:
                raise AttributeError(f"log has no method {name}")
            def _f(msg, *a, **kw):
                return level(f"[trace={tid}] {msg}", *a, **kw)
            return _f
    return _L()


# ───────────────────────────────────────────────────────────
# Path-param 校验: A 股代码必须 6 位数字, 拼下游 SQL/URL/subprocess 之前必过此关
# ───────────────────────────────────────────────────────────
_CODE_RE = _re.compile(r"^\d{6}$")


def _require_valid_code(code: str) -> str:
    """必须 6 位纯数字; 否则 422. 自动 strip + zfill. 返回归一化后的 6 位 code."""
    if not isinstance(code, str):
        raise HTTPException(status_code=422, detail="股票代码必须是字符串")
    code = code.strip().zfill(6)
    if not _CODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail=f"无效的股票代码: {code!r} (需 6 位数字)")
    return code


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
    # R-A2: 用 _envelope_err() + _HTTP_TO_CODE 标准化 error.code
    status = exc.status_code
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    code = _HTTP_TO_CODE.get(status, CODE_INTERNAL)
    body = _envelope_err(code, msg, status_code=status, trace_id=getattr(request.state, 'trace_id', '-'))
    return JSONResponse(body, status_code=status)


# R-A3 (2026-07-19): trace_id 中间件 — 每请求必生成,头部 X-Trace-Id 返回,
# 之前只有异常 handler 读 request.state.trace_id,但从未注入,log 都打 "-"
# R-B16 (2026-07-19): + X-API-Version 头 — 协议版本
# R-I82 (2026-07-19): 结构化 access log — JSON 一行/请求, 含 trace/method/path/status/latency_ms
@app.middleware("http")
async def _trace_id_middleware(request: Request, call_next):
    """每请求分配 8 字节 trace_id (hex=16字符), 注入 state + 响应头, 便于日志/前端调试
    写一行结构化 JSON access log (路径/状态/延迟/IP) 到 _ACCESS_LOG_PATH
    """
    incoming = (request.headers.get("x-trace-id") or "").strip()
    if incoming and len(incoming) <= 32:
        tid = incoming
    else:
        tid = uuid.uuid4().hex[:16]
    request.state.trace_id = tid
    t0 = time.monotonic()
    resp = await call_next(request)
    elapsed_ms = (time.monotonic() - t0) * 1000
    resp.headers["X-Trace-Id"] = tid
    resp.headers["X-API-Version"] = _API_VERSION
    # R-I82 结构化 access log (仅 /api/* 写入, 排除静态资源)
    if request.url.path.startswith("/api/"):
        try:
            client_ip = request.client.host if request.client else "-"
            ua = request.headers.get("user-agent", "-")[:80]
            log_line = json.dumps({
                "t": _now_iso(),
                "trace_id": tid,
                "method": request.method,
                "path": request.url.path,
                "status": resp.status_code,
                "latency_ms": round(elapsed_ms, 1),
                "ip": client_ip,
                "ua": ua,
            }, ensure_ascii=False)
            with _ACCESS_LOG_LOCK:
                with open(_ACCESS_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(log_line + "\n")
                    # 10MB cap, 超了 rotate
                    if f.tell() > 10 * 1024 * 1024:
                        try:
                            f.close()
                            os.rename(_ACCESS_LOG_PATH, _ACCESS_LOG_PATH + ".old")
                        except Exception:
                            pass
        except Exception:
            pass  # 静默失败 — 不影响主请求
    return resp


# R-I82 (2026-07-19): access log 路径 + lock
_ACCESS_LOG_PATH = os.environ.get("TUIXUE_ACCESS_LOG", str(Path(__file__).parent.parent / "access.log"))
_ACCESS_LOG_LOCK = threading.Lock()

# ───────────────────────────────────────────────────────────
# R51 (2026-07-19): 请求超时中间件 — 任一端点超过 25s 返回 503
# 之前有些慢端点(dragons 15s/screener 8s/backtest 300s)没有整体超时守卫,
# 4 worker 全被阻塞时新的请求排队等 worker → 用户看到"卡死"。
# 超时后返回 503 + envelope, 不给前端一直挂起。
# ───────────────────────────────────────────────────────────
@app.middleware("http")
async def _request_timeout_middleware(request: Request, call_next):
    """25s 硬超时 + R301 per-endpoint 延迟记录 (p50/p95/p99)。"""
    import asyncio
    path = request.url.path

    # 白名单: 策略选股/周线擒牛/回测等长任务 (各自内部有更细的超时控制)
    _long_paths = ("/api/strategies/scan", "/api/strategies/text", "/api/weekly_bull", "/api/screener/backtest", "/api/dragons")
    if path.startswith(_long_paths):
        t0 = time.monotonic()
        resp = await call_next(request)
        _record_latency(path, time.monotonic() - t0)
        return resp
    # 白名单: 回测 SSE + 文件上传 可以超过 25s
    if path.startswith("/api/backtest/") and "stream" in path:
        t0 = time.monotonic()
        resp = await call_next(request)
        _record_latency(path, time.monotonic() - t0)
        return resp
    if path.startswith("/api/review/screenshot"):
        t0 = time.monotonic()
        resp = await call_next(request)
        _record_latency(path, time.monotonic() - t0)
        return resp
    # dexin 冷路径 spot + 80只批量日线 实测 22-30s (push2 限频),需要更长预算
    # 视觉验证路径另算 (matplotlib 画图 + MiniMax vision, 单只 ~25s, Top 3 累计 70-90s)
    if path.startswith("/api/dexin/") or path.startswith("/api/admin/dexin/"):
        timeout_sec = 90.0 if "visual_verify" in path else 60.0
        t0 = time.monotonic()
        resp = await asyncio.wait_for(call_next(request), timeout=timeout_sec)
        _record_latency(path, time.monotonic() - t0)
        return resp
    # Dash 大盘+板块分时 — 5 指数并行 + 4 板块顺序 (东财 ban 并发) 累计 ~30s 冷启
    if path == "/api/dashboard/index_trend":
        t0 = time.monotonic()
        resp = await asyncio.wait_for(call_next(request), timeout=55.0)
        _record_latency(path, time.monotonic() - t0)
        return resp

    try:
        t0 = time.monotonic()
        resp = await asyncio.wait_for(call_next(request), timeout=25.0)
        _record_latency(path, time.monotonic() - t0)
        return resp
    except asyncio.TimeoutError:
        log.warning(f"[timeout] {request.method} {path} 超时 25s")
        from . import error_stats as _es
        _es.record(path, error=True, timeout=True)
        return JSONResponse(
            {"ok": False, "error": f"请求超时 25s — {path} 上游数据源可能不可用"},
            status_code=503,
            headers={"X-Timeout": "25"},
        )


def _record_latency(path: str, elapsed_sec: float) -> None:
    """记录 endpoint 延迟到 error_stats (仅 API 端点, 静默失败)。"""
    if not path.startswith("/api/"):
        return
    try:
        from . import error_stats as _es
        _es.record(path, latency_ms=elapsed_sec * 1000)
    except Exception:
        pass


async def _gather_with_fallback(
    coros_with_timeout: list[tuple],
    *,
    timeout_primary: float = 12,
    timeout_secondary: float = 6,
) -> list:
    """并发执行一批任务,每任务独立超时 + 分级总超时 + 部分失败容忍。

    coros_with_timeout: [(coro, per_task_timeout_sec), ...]
    timeout_primary: 首轮硬超时
    timeout_secondary: 首轮超时后,剩余任务再等 N 秒 (0=不再等)
    Returns: [result_or_None, ...] 与输入顺序一致
    """
    async def _run_one(coro, sec):
        if coro is None:
            return None
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None
    tasks = [asyncio.create_task(_run_one(coro, sec)) for coro, sec in coros_with_timeout]
    done, pending = await asyncio.wait(tasks, timeout=timeout_primary)
    if pending and timeout_secondary > 0:
        done2, pending2 = await asyncio.wait(pending, timeout=timeout_secondary)
        for t in pending2:
            t.cancel()
    return [t.result() if t.done() and not t.cancelled() else None for t in tasks]


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
# 注: 25s 超时中间件在 `_request_timeout_middleware` (已在文件头部注册),
#     不在此处重复注册。


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
    # 2026-07-16: ngrok 6024 警告页兜底 — 给所有响应注入 abuse_interstitial cookie
    # value=host 是 ngrok free tier 实际期望的格式 (Playwright 实测抓出来的)
    # 30 天有效期; 与 ngrok traffic policy 互补 — 任一处生效都能 bypass 6024
    # 注意: 404/401/405 等错误响应也带 Set-Cookie, 反正客户端会忽略
    host = request.headers.get("host", "")
    if host.endswith(".ngrok-free.dev") or host.endswith(".ngrok-free.app") or host.endswith(".ngrok.io"):
        resp.headers.setdefault(
            "Set-Cookie",
            f"abuse_interstitial={host}; Path=/; Max-Age=2592000; Secure; SameSite=None"
        )
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
_cache_spot    = TTLCache(default_ttl=CACHE_TTL_SPOT)
_cache_quote   = TTLCache(default_ttl=CACHE_TTL_QUOTE)
_cache_kline   = TTLCache(default_ttl=CACHE_TTL_KLINE)
_cache_fund    = TTLCache(default_ttl=CACHE_TTL_FUND)
_cache_overview = TTLCache(default_ttl=CACHE_TTL_OVERVIEW)
_cache_global  = TTLCache(default_ttl=CACHE_TTL_GLOBAL)
_cache_layer   = TTLCache(default_ttl=CACHE_TTL_LAYER)
# R48 (Batch 5): seat_bd L0 进程内 10min — 跳开 Redis 一跳, 同 worker 内 hot code < 1ms
# 即使 Redis 跨 4 worker, 进程内命中也覆盖主路径 (同一 worker 处理 stock 页)
_cache_seat_bd = TTLCache(default_ttl=CACHE_TTL_SEAT_BD)
# R53 (Batch 6): intraday per-(code,date) L0 60s — 跳 Redis, 同 worker 重复点分时秒开
_cache_intraday = TTLCache(default_ttl=CACHE_TTL_INTRADAY)
# R-opt-2026-07-19: /core L0 进程内 30s — 跳开 Redis 不可用, 冷启用户二次访问同 worker 秒返
_cache_core    = TTLCache(default_ttl=30.0)   # /core 完整响应 (per-worker)
# R61 (Batch 7): sector per-code L0 1h — 板块分类极少变 (sw/csrc/cics/gics 字典级别稳定)
_cache_sector = TTLCache(default_ttl=CACHE_TTL_SECTOR)
# R62 (Batch 7): news L0 5min — 同 worker 重复刷新闻列表秒开 (新闻数据已用 SQLite 持久化)
_cache_news = TTLCache(default_ttl=CACHE_TTL_NEWS)

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

# 端点池: 30 worker — 8 uvicorn workers × 4 并行 to_thread 足够
_EXECUTOR = ThreadPoolExecutor(max_workers=30)
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


# 2026-07-21: 陈旧数据缓存 + 降级模式基础设施
# 设计：每个关键端点维护一个最后一次成功的数据快照，
# 当数据源全部失败时，返回快照 + _degraded 标志，
# 前端据此显示降级状态而非空白/零值。
_STALE_CACHE: dict[str, dict] = {}
_STALE_CACHE_LOCK = threading.Lock()
_STALE_TTL = {
    "market_overview": 300,       # 5 分钟
    "dashboard_signal": 600,      # 10 分钟
    "hot_sectors": 300,           # 5 分钟
    "stock_core": 600,            # 10 分钟
    "stock_kline": 3600,          # 60 分钟
    "stock_fund": 1800,           # 30 分钟
    "stock_seats": 86400,         # 24 小时
    "stock_intraday": 3600,       # 60 分钟
    "stock_intraday_5d": 3600,    # 60 分钟
}


def _stale_save(key: str, data: Any):
    """保存陈旧数据缓存，供后续降级使用。"""
    with _STALE_CACHE_LOCK:
        _STALE_CACHE[key] = {"data": data, "ts": time.time()}


def _stale_load(key: str, max_age: float = 300) -> tuple[Any, float] | tuple[None, None]:
    """
    加载陈旧数据缓存。
    返回 (data, age_seconds) 或 (None, None)。
    max_age: 超过此秒数的陈旧数据不再可用。
    """
    with _STALE_CACHE_LOCK:
        entry = _STALE_CACHE.get(key)
        if entry is None:
            return None, None
        age = time.time() - entry["ts"]
        if age > max_age:
            return None, None
        return entry["data"], age


def envelope_degraded(
    data: Any = None,
    stale_key: str = "",
    stale_max_age: float = 300,
    degraded_reason: str = "data_unavailable",
    fresh: bool = False,
    **extra,
) -> dict:
    """
    生成降级信封：优先返回陈旧数据 + _degraded 标志，
    无陈旧数据时才返回原始 data + _degraded。
    fresh=True：跳过陈旧数据，直接返回降级 data。

    注意：_degraded 放在 data 内部而非 envelope 层，因为前端 api()
    会剥掉 envelope 只留 data，_degraded 必须随 data 到达渲染函数。
    """
    if not fresh and stale_key:
        stale_data, age = _stale_load(stale_key, max_age=stale_max_age)
        if stale_data is not None:
            if isinstance(stale_data, dict):
                stale_data["_degraded"] = degraded_reason
                stale_data["_stale_age_s"] = round(age)
                stale_data["_stale_ts"] = time.time() - age
            return envelope(data=stale_data, **extra)
    if isinstance(data, dict):
        data["_degraded"] = data.get("_degraded", degraded_reason)
    return envelope(data=data, **extra)


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
    """在 executor 跑同步函数,捕获异常 → None。永远不抛。
    120s 超时防止线程池满无限等待,但容忍大多数长任务。
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, functools.partial(fn, *args, **kw)),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        log.warning(f"[timeout] {fn.__name__}{args[:1]} executor 获取/执行超时 120s (池满? 瓶颈?)")
        return None
    except Exception as e:
        log.warning(f"{fn.__name__}{args[:1]} 失败: {type(e).__name__}: {e}")
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
    js_files = ["core.js", "app.js", "view-dash.js", "view-stock.js", "view-other.js"]
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
async def root(request: Request):
    """HTML 强刷:no-cache + 指纹注入 — 用户改前端,所有访问会被强制重新加载新的 ?v=xxx。"""
    body = _render_index_html()
    # R-opt-2026-07-19: ?code= 时插入 <link prefetch /core> — 浏览器 preload scanner
    # 在 JS 加载前就开始拉 /core,入 SW 缓存,冷启第一屏等 JS 就绪时 /core 已在 SW 缓存
    code = request.query_params.get("code", "")
    if code and isinstance(body, bytes):
        safe = code.strip().zfill(6) if code.isdigit() else code[:6]
        # R-opt-2026-07-19: inline /core JSON into HTML — 比 preload/css-link 快且可靠
        # 浏览器解析到 <script> 时直接设 window.__STOCK_CORE__, JS 加载后无需 fetch 即用
        _ck = ("core", safe)
        _core = _cache_core.get(_ck)
        if not _core:
            _rk = cache_store.K.STOCK_FULL.format(code=safe) + ":core"
            _core = _store_get(_rk, ttl=30)
            if _core:
                _cache_core.set(_ck, _core)
        if _core:
            _core.pop("_cache_hit", None)
            _core_json = json.dumps(_core, ensure_ascii=False).replace("</", "<\\/")
            tag = f'<script>window.__STOCK_CORE__={_core_json}</script>'
            if tag.encode() not in body:
                body = body.replace(b"</head>", tag.encode() + b"</head>", 1)
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
            # R-B17 (2026-07-19): envelope + rate-limit 头
            return JSONResponse(
                _envelope_err(CODE_RATE_LIMITED, f"IP 限频: {RATE_MAX_REQ}/{RATE_WINDOW_SEC}s"),
                status_code=429,
                headers={
                    "X-RateLimit-Limit": str(RATE_MAX_REQ),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + RATE_WINDOW_SEC)),
                },
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
                    _envelope_err(CODE_RATE_LIMITED, f"路径限频: {mx}/{sec}s on {request.url.path}"),
                    status_code=429,
                    headers={
                        "X-RateLimit-Limit": str(mx),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + sec)),
                    },
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
        # R-B11: envelope + error code (JSONResponse from module-level import)
        return JSONResponse(
            _envelope_err(CODE_INTERNAL, f"internal: {type(e).__name__}", trace_id=trace_id),
            status_code=500,
        )

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
    # R-A8: envelope — 包成 {ok:true, data:{...}}
    return _envelope_ok({
        "version": "2.0",
        "modules": {
            "fastapi": __import__("fastapi").__version__,
            "python": sys.version.split()[0],
            "pandas": __import__("pandas").__version__ if _safe_import("pandas") else None,
            "akshare": __import__("akshare").__version__ if _safe_import("akshare") else None,
        },
        "platform": sys.platform,
    })


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


@app.get("/api/sources/health")
async def sources_health():
    """2026-07-21: 数据源健康状态 — 每个源的冷却/失败/调用统计。
    返回逐源状态,供前端调试面板和自动告警使用。"""
    from .. import lib_common as _lc
    sources = _lc.get_source_health()
    disabled_count = sum(1 for s in sources if s["disabled"])
    return envelope(data={
        "sources": sources,
        "disabled_count": disabled_count,
        "total_sources": len(sources),
        "healthy": disabled_count == 0,
    })


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
async def meta_cache_stats(request: _Request):
    """全量缓存状态 — 给前端 debug 页 / 压力测试用。
    R-B11: envelope 标准化 — 把所有 _meta/* 端点统一
    """
    return json_etag_response(request, _envelope_ok(_cache_stats_snapshot()), max_age=5)


# R91 (Batch 10): /api/_meta/perf 聚合端点 — uptime / version 用
_SERVER_BOOT_TS = datetime.datetime.now()
_APP_VERSION = "v3-100rounds"


def _cache_stats_snapshot() -> dict:
    """R91 (Batch 10): 抽成 helper 给 /api/_meta/perf 复用"""
    try:
        _store = cache_store.get_store()
        store_stats = _store.stats()
        store_status = _store.status()
    except Exception as e:
        store_stats = {"error": str(e)[:120]}
        store_status = {"redis": False}
    return {
        "ttl_caches": {
            "spot":     _cache_spot.stats(),
            "quote":    _cache_quote.stats(),
            "kline":    _cache_kline.stats(),
            "fund":     _cache_fund.stats(),
            "overview": _cache_overview.stats(),
            # R50 (Batch 5): 暴露 seat_bd L0 cache stats
            "seat_bd":  _cache_seat_bd.stats(),
            # R60 (Batch 6): 暴露 intraday L0 cache stats
            "intraday": _cache_intraday.stats(),
            # R61 (Batch 7): sector per-code L0
            "sector":   _cache_sector.stats(),
            # R62 (Batch 7): news L0
            "news":     _cache_news.stats(),
        },
        "redis":  store_stats,
        "redis_status": store_status,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/_meta/error_stats")
async def meta_error_stats():
    """R80 (Batch 8): 错误率监控 — 5min 滚动窗口
    返回各端点的 (calls, errors, timeout, error_rate)
    R-A6: 用 _envelope_ok/_envelope_err 标准化
    """
    try:
        from . import error_stats as _es
        snap = _es.snapshot()
        data = {
            "stats": snap.get("endpoints", snap),
            "rss_mb": snap.get("rss_mb", 0),
        }
        return _envelope_ok(data)
    except ImportError:
        return _envelope_err(CODE_INTERNAL, "error_stats module not loaded", stats={})
    except Exception as e:
        return _envelope_err(CODE_INTERNAL, str(e)[:120], stats={})


@app.get("/api/_meta/perf")
async def meta_perf():
    """R91 (Batch 10): 聚合 perf 指标 — cache_stats + error_stats + uptime + version 一站汇总
    给前端 PerformanceObserver + 长任务 + cache hit rate + memory 上报统一一个回传端点
    R-B11: envelope 标准化
    """
    from . import error_stats as _es
    snap = _es.snapshot()
    data = {
        "error_stats": snap,
        "cache_stats": _cache_stats_snapshot(),
        "uptime_sec": int((datetime.datetime.now() - _SERVER_BOOT_TS).total_seconds()),
        "version": _APP_VERSION,
    }
    return _envelope_ok(data)


@app.get("/api/_meta/access_log_tail")
async def meta_access_log_tail(lines: int = Query(50, ge=1, le=1000)):
    """R-I82 (2026-07-19): 读 access log 末尾 N 行 — 给前端 debug 页用。
    返回每行 JSON 解析后的 dict, 最多 1000 行。
    """
    items = []
    try:
        with _ACCESS_LOG_LOCK:
            with open(_ACCESS_LOG_PATH, "r", encoding="utf-8") as f:
                # 简单 tail: 读全部 → 取最后 N 行 (文件 < 10MB)
                all_lines = f.readlines()
        for line in all_lines[-lines:]:
            line = line.strip()
            if not line: continue
            try:
                items.append(json.loads(line))
            except Exception:
                items.append({"raw": line})
    except FileNotFoundError:
        items = []
    return _envelope_ok({"items": items, "count": len(items)})


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
    """6 大指数并行拉取 + 涨停数估算。
    2026-07-21: 数据源全挂时返回陈旧数据 + _degraded 标志，
    绝不让前端看到全零指数。
    """
    async def _zt_count():
        from .. import multi_source_fetchers as msf
        today = datetime.datetime.now().strftime("%Y%m%d")
        try:
            return await asyncio.wait_for(to_thread(msf.fetch_zt_pool, today), timeout=6)
        except Exception:
            return None

    async def _indices():
        results = await asyncio.gather(*[_fetch_index(c, n) for c, n in INDICES], return_exceptions=True)
        return [r for r in results if not isinstance(r, BaseException)]

    try:
        indices_raw, zt = await asyncio.wait_for(
            asyncio.gather(_indices(), _zt_count(), return_exceptions=True),
            timeout=8,
        )
    except asyncio.TimeoutError:
        indices_raw, zt = None, None

    has_index_data = isinstance(indices_raw, list) and len(indices_raw) > 0 and any(
        (isinstance(i.get("price"), (int, float)) and i.get("price") > 0)
        for i in indices_raw if isinstance(i, dict))
    has_zt_data = isinstance(zt, list)

    if has_index_data:
        indices = indices_raw
    else:
        indices = [{"code": c, "name": n, "price": 0, "change_pct": 0, "amount": 0} for c, n in INDICES]
    zt_count = len(zt) if has_zt_data else 0

    out = {
        "indices": indices,
        "limit_up": zt_count,
        "limit_up_available": has_zt_data,
        "ts": time.time(),
    }

    if has_index_data or has_zt_data:
        _stale_save("market_overview", out)
        return envelope(data=out)

    # 全部源失败 → 陈旧数据兜底
    return envelope_degraded(
        data=out,
        stale_key="market_overview",
        stale_max_age=_STALE_TTL["market_overview"],
        degraded_reason="all_sources_failed",
        limit_up=zt_count,
        limit_up_available=False,
    )


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
        stale_data, _age = _stale_load("global_sentiment", max_age=3600)
        if stale_data:
            stale_data["_degraded"] = "stale"
            return envelope(data=stale_data)
        return envelope(error="全球情绪拉取超时", data={
            "sentiment": "neutral", "sentiment_score": 0.0,
            "indices": [], "us_leaders": [], "us_losers": [],
            "kr_leaders": [], "sector_impact": {}, "_degraded": "timeout",
        })
    except Exception as e:
        log.warning(f"global_sentiment 失败: {e}")
        stale_data, _age = _stale_load("global_sentiment", max_age=3600)
        if stale_data:
            stale_data["_degraded"] = "stale"
            return envelope(data=stale_data)
        return envelope(error=f"全球情绪失败: {e}", data={
            "sentiment": "neutral", "sentiment_score": 0.0,
            "indices": [], "us_leaders": [], "us_losers": [],
            "kr_leaders": [], "sector_impact": {}, "_degraded": "failed",
        })

    _cache_global.set(("global_sentiment",), result)
    _stale_save("global_sentiment", result)
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
_DASHBOARD_TTL = 300.0  # R-T5x (2026-07-21): 30s→300s,bench P95 11s → <100ms (走 Redis 共享)

# R-T5x (2026-07-21): 4 worker 共享 dashboard 缓存,避免冷路径重复 11s 计算
# 每 worker 进程内 dict 缓存只覆盖自己,bench 20 并发 → 4 worker × 5 = 4 次冷算
# 改走 cache_store (Redis 主,SQLite fallback),跨进程共享,二次访问秒开
import json as _json_dash
import threading as _th_dash  # R-T6x: SWR 后台刷新锁
_DASH_REDIS_KEY = "tuixue:dashboard:signal:v1"  # 300s TTL,key 内自带 ts
_DASH_BG_LOCK = _th_dash.Lock()
_DASH_BG_RUNNING = False


def _cache_obj(raw):
    """把 CacheStore 取回值统一还原成 dict/list。

    CacheStore.set() 自带 JSON 编码,直接存 dict 即可。历史上多处传了
    json.dumps().encode(),取回是字符串 "b'{...}'",json.loads 必抛 →
    缓存永不命中、每次请求全量重建。这里兼容存量脏值,还原不了就返 None。
    """
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s[:2] in ("b'", 'b"'):
        try:
            s = s[2:-1].encode().decode("unicode_escape")
        except Exception:
            return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _bg_dashboard_signal() -> None:
    """R-T6x (2026-07-22): 后台异步刷 dashboard signal。
    TTL 过期后,请求立即返旧数据,不阻塞用户。Redis 写回让下次访问秒开。
    """
    global _DASH_BG_RUNNING
    if _DASH_BG_RUNNING:
        return
    with _DASH_BG_LOCK:
        if _DASH_BG_RUNNING:
            return
        _DASH_BG_RUNNING = True

    def _run():
        global _DASH_BG_RUNNING
        try:
            sig = _build_dashboard_signal()
            if sig:
                _dashboard_cache["signal"] = sig
                _dashboard_cache["ts"] = time.time()
                try:
                    cache_store.get_store().set(_DASH_REDIS_KEY, sig, ttl=300)
                except Exception:
                    pass
        except Exception as e:
            log.debug(f"_bg_dashboard_signal: {e}")
        finally:
            _DASH_BG_RUNNING = False

    _th_dash.Thread(target=_run, name="dash-sig-bg", daemon=True).start()


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

    # 1) A股 — 6 指数走腾讯 qt.gtimg 单请求 bulk (~100ms),
    #    命中即用,miss 则落回 per-code 并行兜底。R-T6x: 4.5s → 0.1s 提 45x。
    a_indices: list[dict] = []
    a_pcts: list[float] = []
    try:
        idx_codes = [c for (c, _n) in INDICES]
        bulk = lc._index_realtime_qq_bulk(idx_codes) if hasattr(lc, "_index_realtime_qq_bulk") else {}
        if bulk:
            for (code, name) in INDICES:
                rt = bulk.get(code) or {}
                if not rt or rt.get("最新价", 0) <= 0:
                    continue
                fetch_time = rt.get("时间") or ""
                data_date = ""
                if fetch_time and len(str(fetch_time)) >= 8:
                    s = str(fetch_time)
                    data_date = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                a_indices.append({
                    "code": code,
                    "name": name,
                    "price": _safe_float(rt.get("最新价") or rt.get("price")),
                    "change_pct": _safe_float(rt.get("涨跌幅") or rt.get("change_pct")),
                    "_source": rt.get("_source", "tencent_qq_index_bulk"),
                    "_fetch_time": fetch_time,
                    "data_date": data_date,
                })
                a_pcts.append(a_indices[-1]["change_pct"])
        if not a_indices:
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

    gm_degraded = not gm_data or not gm_data.get("indices")
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
        "_degraded": "gm_unavailable" if gm_degraded else False,
    }


def _pick_data_date(indices: list[dict], fallback: str = "") -> str:
    """从指数列表中找第一个有 data_date 的,优先取最大(最近的)。
    用户反馈 (2026-07-11): 首页三市场没显示数据日期,休市日尤其需要。"""
    dates = [i.get("data_date") for i in (indices or []) if i.get("data_date")]
    if not dates:
        return fallback
    return max(dates)


@app.get("/api/dashboard/signal")
async def api_dashboard_signal(request: _Request, force: bool = False):
    """首页三市场信号面板 — A/KR/US verdict + 关键指数 + 不利新闻。
    30s 内存缓存; force=true 强制重算 (数据源冷启动或调试)。
    R-T5x (2026-07-21): 跨 worker 共享 Redis 缓存,避免 4 worker × cold path 11s × N
    """
    def _response(**payload):
        return json_etag_response(request, envelope(**payload), max_age=5)

    now = time.time()
    if not force and _dashboard_cache["signal"] is not None and (now - _dashboard_cache["ts"]) < _DASHBOARD_TTL:
        return _response(data=_dashboard_cache["signal"])
    # R-T5x: Redis 共享 — bench 20 并发 → 4 worker 全命中, 二次 < 5ms
    if not force:
        try:
            cached = cache_store.get_store().get(_DASH_REDIS_KEY)
            if cached:
                sig = _cache_obj(cached)
                if sig is not None:
                    _dashboard_cache["signal"] = sig
                    _dashboard_cache["ts"] = now
                    return _response(data=sig)
        except Exception as e:
            log.debug(f"dashboard signal redis get fail: {e}")

    # R-T6x (2026-07-22): SWR — 有任意缓存即立即返(即使过期),后台异步重建。
    # 这样 TTL 失效后用户不会撞 4s 阻塞,而是看到上一拍快照 + 后台静默更新。
    if not force and _dashboard_cache["signal"] is not None:
        if (now - _dashboard_cache["ts"]) >= _DASHBOARD_TTL:
            _bg_dashboard_signal()
        return _response(data=_dashboard_cache["signal"])
    if not force:
        try:
            cached = cache_store.get_store().get(_DASH_REDIS_KEY)
            if cached:
                sig = _cache_obj(cached)
                if sig is not None:
                    _dashboard_cache["signal"] = sig
                    _dashboard_cache["ts"] = now
                    # Redis 命中但本机超期 → 后台刷
                    return _response(data=sig)
        except Exception as e:
            log.debug(f"dashboard signal redis get fail: {e}")

    # force=true 或首次冷启 (无任何缓存) → 必须同步算,但有超时兜底
    if not hasattr(api_dashboard_signal, "_sf"):
        api_dashboard_signal._sf = SingleFlight()
    try:
        sig = await asyncio.wait_for(
            to_thread(api_dashboard_signal._sf.run, ("dash_signal",), _build_dashboard_signal),
            timeout=25,
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning(f"dashboard signal 超时 25s (now={now:.0f}, cached_ts={_dashboard_cache['ts']:.0f})")
        # 兜底:返上一次缓存(可能 None)
        return _response(error="信号计算超时", data=_dashboard_cache["signal"] or {
            "a_share": {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "kr":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "us":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
        })
    except Exception as e:
        log.warning(f"dashboard signal 异常: {e}")
        return _response(error=str(e), data=_dashboard_cache["signal"] or {
            "a_share": {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "kr":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
            "us":      {"verdict": "cautious", "change_pct": 0, "headline": "—", "warnings": []},
        })

    _dashboard_cache["signal"] = sig
    _dashboard_cache["ts"] = now
    _stale_save("dashboard_signal", sig)
    try:
        cache_store.get_store().set(_DASH_REDIS_KEY, sig, ttl=300)
    except Exception as e:
        log.debug(f"dashboard signal redis set fail: {e}")
    return _response(data=sig)


@app.get("/api/dashboard/hot_sectors")
async def api_dashboard_hot_sectors(force: bool = False):
    """今日热门板块 — 简化版:fetch_hot_sectors 拿 18 个板块(涨跌+资金净流入),
    按 (涨停数 × 2 + 涨幅% × 10) 综合排序,取 Top 5。
    避开了 score_dragons 中不稳定的小 Racer/V8 调用(2026-07-11 libmini_racer 段错误)。
    R-T5x (2026-07-21): Redis 跨 worker 共享 (30s TTL)
    """
    now = time.time()
    if not force and _dashboard_cache["hot"] is not None and (now - _dashboard_cache["ts"]) < _DASHBOARD_TTL:
        return envelope(data=_dashboard_cache["hot"])
    if not force:
        try:
            cached = cache_store.get_store().get("tuixue:dashboard:hot:v1")
            if cached:
                hot = _cache_obj(cached)
                if hot is not None:
                    _dashboard_cache["hot"] = hot
                    _dashboard_cache["ts"] = now
                    return envelope(data=hot)
        except Exception as e:
            log.debug(f"hot_sectors redis get fail: {e}")

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
        return envelope_degraded(
            data={"mainline": [], "sentiment": {"label": "—", "zt_count": 0}},
            stale_key="hot_sectors",
            stale_max_age=_STALE_TTL["hot_sectors"],
            degraded_reason="timeout",
        )
    except Exception as e:
        log.warning(f"dashboard hot_sectors 失败: {e}")
        return envelope_degraded(
            data={"mainline": [], "sentiment": {"label": "—", "zt_count": 0}},
            stale_key="hot_sectors",
            stale_max_age=_STALE_TTL["hot_sectors"],
            degraded_reason=str(e)[:100],
        )

    out["ts"] = time.time()
    if out.get("mainline"):
        _stale_save("hot_sectors", out)
    _dashboard_cache["hot"] = out
    _dashboard_cache["ts"] = now
    try:
        cache_store.get_store().set("tuixue:dashboard:hot:v1", out, ttl=300)
    except Exception as e:
        log.debug(f"hot_sectors redis set fail: {e}")
    return envelope(data=out)


# 大盘 + 板块分时走势 (首页 sparkline 网格)
_INDICES_FOR_TREND = [
    # (code, mkt_prefix, name) — code = 6 位原代码
    ("000001", "sh", "上证"),
    ("399001", "sz", "深证"),
    ("399006", "sz", "创业"),
    ("000300", "sh", "沪深300"),
    ("000905", "sh", "中证500"),
]


def _tencent_minute_one(code6: str, *, is_index: bool = False) -> list:
    """拉单只代码今日分时(腾讯). 返 [{time, price}] 或 []
    适配 6 位股票代码 (e.g. 000300) / BK 板块代码 / 大盘指数 (需 is_index=True)

    重要:大盘指数在腾讯的 mkt 规则跟股票不同:
    - sh 开头 (6/9/5) 普通股票
    - sz 开头 (0/2/3) 普通股票
    - 大盘指数如 000001/399001/000300 要显式传 is_index=True,腾讯也用 sh/sz 但匹配
      "sh000001" / "sz399001" 才是大盘而非个股
    """
    import requests as _req
    try:
        if code6.startswith(("BK", "bk")):
            mkt = "sh" if code6[2:3] == "0" else "sz"
        elif is_index or code6 in ("000001", "000300", "000905", "000852", "399001", "399006", "399905", "399903"):
            # 显式索引 (避免跟同号个股撞)
            mkt = "sh" if code6.startswith("0") else "sz"
        else:
            mkt = "sh" if code6.startswith(("6", "9", "5")) else "sz"
        url = "http://web.ifzq.gtimg.cn/appstock/app/minute/query"
        r = _req.get(url, params={"code": f"{mkt}{code6}"}, timeout=5,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        if r.status_code != 200:
            return []
        j = r.json()
        # 兼容两种结构: nested dict or list directly
        data_section = (j.get("data") or {}).get(f"{mkt}{code6}", {})
        raw = []
        if isinstance(data_section, dict):
            raw = (data_section.get("data") or {}).get("data") or []
        elif isinstance(data_section, list):
            raw = data_section
        ticks = []
        for line in raw:
            parts = line.split(" ")
            if len(parts) < 2:
                continue
            t = parts[0]
            if len(t) == 4 and t.isdigit():
                t = f"{t[:2]}:{t[2:]}:00"
            try:
                price = float(parts[1])
            except (ValueError, TypeError):
                continue
            ticks.append({"time": t, "price": price})
        return ticks
    except Exception as e:
        log.debug(f"tencent minute {code6}: {e}")
        return []


def _fetch_sector_sparkline(name: str) -> dict:
    """板块名 → 东财板块指数代码 (BK) → 今日分时 (push2.trends2 带 retry).
    关键:腾讯不支持 BK 前缀;东财 push2.trends2 secid=90.BKxxxx 直接吃板块指数
    push2 易触发 RemoteDisconnected,自动 retry 2 次 (4s/8s backoff)
    """
    import requests as _req
    try:
        url_search = "https://searchadapter.eastmoney.com/api/suggest/get"
        r = _req.get(url_search, params={"input": name, "type": 14}, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0"})
        bk_code = ""
        if r.status_code == 200:
            for item in (r.json().get("QuotationCodeTable") or {}).get("Data") or []:
                code = str(item.get("Code", ""))
                if code.startswith(("BK", "bk")):
                    bk_code = code
                    break
        if not bk_code:
            return {"name": name, "ticks": [], "open": None, "last": None, "change_pct": None, "ok": False, "note": "板块代码未找到"}
        # push2.trends2: 1min K 序列,~241/day,带 retry
        url = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
        params = {
            "secid": f"90.{bk_code}",
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": 0, "ndays": 1,
        }
        headers = {"User-Agent":"Mozilla/5.0", "Referer":"https://quote.eastmoney.com/"}
        trends = []
        for attempt in range(3):
            try:
                r = _req.get(url, params=params, timeout=5, headers=headers)
                if r.status_code == 200:
                    d = r.json().get("data") or {}
                    trends = d.get("trends") or []
                    if trends:
                        break
            except Exception as e:
                if attempt == 2:
                    log.warning(f"sector_sparkline {name} ({bk_code}) 第 3 次仍失败: {e}")
                else:
                    import time as _t
                    _t.sleep(1 + attempt)   # 1s/2s backoff (vs 2/4)
        if not trends:
            # 没有 ticks: 返空 + 前端用 change_pct 兜底 (从 hot_sectors 注入)
            return {"name": name, "bk_code": bk_code, "ticks": [], "open": None, "last": None, "change_pct": None, "ok": False, "note": "东财 API 3 次失败"}
        ticks = []
        for line in trends:
            parts = line.split(",")
            if len(parts) < 2: continue
            dt = parts[0].split(" ")
            t = dt[1] if len(dt) > 1 else ""
            try:
                price = float(parts[1])
            except (ValueError, TypeError):
                continue
            ticks.append({"time": t, "price": price})
        prices = [t["price"] for t in ticks]
        return {
            "name": name, "bk_code": bk_code,
            "ticks": ticks, "prices": prices,
            "open": prices[0], "last": prices[-1],
            "change_pct": (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] else 0,
            "ok": True,
        }
    except Exception as e:
        return {"name": name, "ok": False, "note": f"err: {e}"}


@app.get("/api/dashboard/index_trend")
async def api_dashboard_index_trend():
    """首页 sparkline 网格数据:
    indices[] = 5 大指数分时 (today minute, ~240 ticks)
    sectors[] = 4 热门板块分时 (板块指数代码 from fetch_hot_sectors)
    60s Redis cache — 切页 0ms,首屏 fallback < 100ms
    """
    import json as _json_dash
    cache_key = "tuixue:dashboard:index_trend:v1"
    try:
        cached = cache_store.get_store().get(cache_key)
        if cached:
            # cache_store 应该返回 dict;老版本 compat: 如果是 bytes/str 需 loads
            if isinstance(cached, dict):
                return envelope(data=cached)
            if isinstance(cached, (bytes, bytearray)):
                return envelope(data=_json_dash.loads(cached.decode("utf-8")))
            if isinstance(cached, str):
                # 兼容旧版 double-encode 错误: 可能开头是 b'  (repr of bytes)
                if cached.startswith("b'") or cached.startswith('b"'):
                    log.warning(f"index_trend cache 命中但格式异常 (re-encode): 清掉")
                    cache_store.get_store().delete(cache_key)
                else:
                    return envelope(data=_json_dash.loads(cached))
    except Exception as e:
        log.debug(f"index_trend redis get: {e}")

    def _fetch_index_sparkline(code6: str, name: str) -> dict:
        ticks = _tencent_minute_one(code6, is_index=True)
        if not ticks:
            return {"code": code6, "name": name, "prices": [], "open": None, "last": None, "change_pct": None, "ok": False}
        prices = [t["price"] for t in ticks]
        return {
            "code": code6, "name": name, "ticks": ticks, "prices": prices,
            "open":  prices[0], "last": prices[-1],
            "change_pct": (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] else 0,
            "ok": True,
        }

    out = {"ts": time.time(), "indices": [], "sectors": []}
    import traceback as _tb
    DEBUG_PATH = "/tmp/tuixue_index_trend_debug.log"
    def _dbg(msg):
        try:
            with open(DEBUG_PATH, "a") as _f:
                _f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception: pass
    _dbg("enter endpoint")
    # 端点总超时 7s — 移动端 iPhone 13 ngrok 31s 超时体验像"链接断了"
    # 拆解:fetch_hot_sectors ≤3s + indices ≤1s(已并行) + sectors ≤3s(单 sector ≤1.2s)
    try:
        async def _do():
            loop = asyncio.get_event_loop()
            from .. import multi_source_fetchers as msf
            try:
                hot = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: msf.fetch_hot_sectors(top_n_flow=20, top_n_pct=20)),
                    timeout=3.0,
                ) or []
            except (asyncio.TimeoutError, Exception) as e:
                log.warning(f"index_trend hot_sectors: {type(e).__name__}: {e}")
                hot = []
            rows = sorted(hot, key=lambda x: -(x.get("net_inflow_yi") or 0))[:4]

            # 5 指数并行拉
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=5) as ex:
                idx_futs = [ex.submit(_fetch_index_sparkline, c, n) for (c, _, n) in _INDICES_FOR_TREND]
                indices_out = [f.result() for f in idx_futs]
            out["indices"] = [r for r in indices_out if r]

            # sectors 串行,但单 sector ≤1.2s — 4 个仍可能跑 4.8s,但通常 1-2 个成功就够画
            sectors_out = []
            for src_sector in rows:
                nm = src_sector.get("name", "")
                if not nm: continue
                try:
                    r = await asyncio.wait_for(
                        loop.run_in_executor(None, _fetch_sector_sparkline, nm),
                        timeout=1.2,
                    )
                except asyncio.TimeoutError:
                    r = {"name": nm, "ok": False, "note": "timeout"}
                except Exception as e:
                    _dbg(f"sector {nm} fail: {e}")
                    r = {"name": nm, "ok": False, "note": str(e)}
                # 注入 hot_sectors 自带的 change_pct + net_inflow 兜底 (无 tick 也可绘)
                if r.get("change_pct") is None:
                    cp = src_sector.get("change_pct")
                    r["change_pct"] = float(cp) if cp is not None else None
                    r["net_inflow_yi"] = src_sector.get("net_inflow_yi")
                sectors_out.append(r)
            out["sectors"] = sectors_out
            _dbg(f"OK: indices={len(out['indices'])} sectors={len(out['sectors'])}")

        await asyncio.wait_for(_do(), timeout=7.0)
    except asyncio.TimeoutError:
        _dbg(f"FAIL: total endpoint timeout 7s — partial out: indices={len(out['indices'])} sectors={len(out['sectors'])}")
        log.warning("index_trend 端点总超时 7s — 返部分数据")
    except Exception as e:
        err = _tb.format_exc()
        _dbg(f"FAIL: {type(e).__name__}: {e}")
        _dbg(err[-2000:])
        log.warning(f"index_trend parallel: {type(e).__name__}: {e}")
        log.warning(f"index_trend TRACEBACK: {_tb.format_exc()}")

    # 60s TTL — 与 hot_sectors 同档
    try:
        cache_store.get_store().set(cache_key, out, ttl=60)
    except Exception as e:
        log.debug(f"index_trend redis set: {e}")
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
    code = _require_valid_code(code)

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
        stale_data, age = _stale_load(f"stock_kline:{code}", max_age=_STALE_TTL["stock_kline"])
        if stale_data:
            stale_data["_degraded"] = "stale"
            stale_data["_stale_ts"] = time.time() - age
            return envelope(data=stale_data)
        return envelope(data={"code": code, "kline": [], "_degraded": "upstream_timeout"})

    if kline:
        _stale_save(f"stock_kline:{code}", {"code": code, "kline": kline})
    return envelope(data={"code": code, "kline": kline or []})


# ── 100-R3: 全A风向 5D sparkline 端点 — 返回最近 5 日收盘 + 涨跌幅,前端画 SVG ──
# 缓存键以 (code, days=5) 走 _cache_kline,与 stock_kline 共享 TTL (120s 实际 8s)
# 主源腾讯 fqkline (1req 全 A),akshare fallback (5 日慢但稳)
def _fetch_sparkline_tencent(code: str, days: int) -> list:
    """腾讯 fqkline 拉最近 N 日 K — 单次拿全 A 不卡,但 sandbox DNS 可能挂"""
    import requests as _req
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{mkt}{code},day,,,{days+5},qfq"}
    try:
        r = _req.get(url, params=params, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        if r.status_code != 200:
            return []
        j = r.json()
        # 路径: data -> {code: {qfqday: [[date, open, close, high, low, vol, ...], ...]}}
        code_data = (j.get("data") or {}).get(f"{mkt}{code}") or {}
        rows = code_data.get("qfqday") or code_data.get("day") or []
        out = []
        for r in rows[-days:]:
            try:
                out.append({
                    "date":  str(r[0])[:10],
                    "close": float(r[2]),
                    "pct":   float(r[6]) if len(r) > 6 else 0,
                })
            except Exception:
                continue
        return out
    except Exception:
        return []


@app.get("/api/stock/{code}/sparkline")
async def stock_sparkline(code: str, days: int = Query(5, ge=3, le=10)):
    code = _require_valid_code(code)

    @cached(_cache_kline, key_fn=lambda c, d: ("spark", c, d))
    def _load(code_, days_):
        # 100-R3: 腾讯主源(快),akshare 兜底
        rows = _fetch_sparkline_tencent(code_, days_)
        if rows:
            return rows
        from .. import lib_common as lc
        df = lc.fetch_daily(code_, days=days_)
        if df is None or df.empty:
            return []
        out = []
        for _, row in df.iterrows():
            out.append({
                "date":  str(row.get("日期", ""))[:10],
                "close": _safe_float(row.get("收盘")),
                "pct":   _safe_float(row.get("涨跌幅")),
            })
        return out
    try:
        rows = await asyncio.wait_for(to_thread(_load, code, days), timeout=8)
    except asyncio.TimeoutError:
        return envelope(data={"code": code, "sparkline": [], "_degraded": "upstream_timeout"})
    return envelope(data={"code": code, "sparkline": rows or []})


# ── 100-R3: 批量 sparkline — 全A风向 N 行一次性拉,避免 N×RTT ──
@app.post("/api/stock/sparklines")
async def stock_sparklines_batch(body: dict):
    """body = {codes: ['600519','000001',...], days: 5} → {data: {code: [{date,close,pct}]}}
    缓存键以 sorted tuple 走 _cache_kline;腾讯串行 + 单 code 失败不影响其他
    """
    codes = (body or {}).get("codes") or []
    days  = int((body or {}).get("days") or 5)
    if not isinstance(codes, list) or not codes:
        return envelope(data={})
    days = max(3, min(days, 10))
    codes = [str(c).strip().zfill(6)[:6] for c in codes[:80]]  # 单次 ≤80 只

    @cached(_cache_kline, key_fn=lambda codes_, d: ("spark_batch", tuple(sorted(codes_)), d))
    def _batch(codes_, d):
        out = {}
        # 100-R3: 改串行→并行 — _EXECUTOR (20 workers) 跑并行
        def _one(c):
            try:
                rows = _fetch_sparkline_tencent(c, d)
                if not rows:
                    from .. import lib_common as lc
                    df = lc.fetch_daily(c, days=d)
                    if df is not None and not df.empty:
                        rows = []
                        for _, row in df.iterrows():
                            rows.append({
                                "date":  str(row.get("日期", ""))[:10],
                                "close": _safe_float(row.get("收盘")),
                                "pct":   _safe_float(row.get("涨跌幅")),
                            })
                return c, rows or []
            except Exception:
                return c, []
        from concurrent.futures import as_completed as _as_completed
        futs = [_EXECUTOR.submit(_one, c) for c in codes_]
        for fut in _as_completed(futs, timeout=25):
            try:
                code_, rows_ = fut.result(timeout=10)
                out[code_] = rows_
            except Exception:
                continue
        return out

    try:
        data = await asyncio.wait_for(to_thread(_batch, codes, days), timeout=28)
    except asyncio.TimeoutError:
        return envelope(data={}, error="sparkline 批量超时")
    return envelope(data=data)


@app.get("/api/stock/{code}/fund_flow")
async def stock_fund(code: str, days: int = Query(60, ge=10, le=180),
                     fresh: int = Query(0, ge=0, le=1)):
    code = _require_valid_code(code)
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
        stale_data, age = _stale_load(f"stock_fund:{code}", max_age=_STALE_TTL["stock_fund"])
        if stale_data:
            stale_data["_degraded"] = "stale"
            stale_data["_stale_ts"] = time.time() - age
            return envelope(data=stale_data)
        return envelope(data={"code": code, "today": None, "history": [], "_degraded": "upstream_timeout"})

    if flow and flow.get("today"):
        _stale_save(f"stock_fund:{code}", flow)
    return envelope(data=flow or {"code": code, "today": None, "history": []})


@app.get("/api/stock/{code}/seats")
async def stock_seats(code: str, days: int = Query(30, ge=5, le=90)):
    code = _require_valid_code(code)
    seats = await to_thread(seat_lookup.get_stock_seats, code, days)
    if seats:
        _stale_save(f"stock_seats:{code}", seats)
        return envelope(data=seats)
    stale_data, age = _stale_load(f"stock_seats:{code}", max_age=_STALE_TTL["stock_seats"])
    if stale_data:
        stale_data["_degraded"] = "stale"
        stale_data["_stale_age_s"] = round(age)
        return envelope(data=stale_data)
    return envelope(data={"code": code, "rows": [], "blacklisted": False,
                           "seat_count": 0, "total_lhb_rows": 0,
                           "known_groups": [], "_degraded": "no_data"})


@app.get("/api/stock/{code}/seat_breakdown")
async def stock_seat_breakdown(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    8 类席位分类 (5 大类 + 游资 3 档) + 资金占比 + 风险/积极信号 + 短线筛选标签.
    复用 seat_lookup.get_stock_seats + fund_flow.get_main_flow.
    ?fresh=1 — 不读缓存,重新跑 8 类分类 (2026-07-12 字典升级后页面强制刷新用)

    R42 (Batch 5): 24h Redis cache (K.SEAT_BD:{code}) — LHB 历史当日不变, 24h 兜底即可
    首次慢 (10s+) 走 akshare 限频, 之后 24h 内都是 < 10ms

    返回 categories[].seats[] 含 {alias, style, positive, warning, tier} —
    按用户字典 §五/§六 全部席位都挂 metadata.
    """
    from . import seat_classify
    code = _require_valid_code(code)
    _touch_recent(code)

    cache_key = cache_store.K.SEAT_BD.format(code=code)
    if not fresh:
        # R48 (Batch 5): L0 进程内 (跳 Redis) → L1 Redis (24h 兜底)
        l0 = _cache_seat_bd.get(("seat_bd", code))
        if l0 is not None:
            l0["_cache_hit"] = True
            l0["_cache_level"] = "l0_mem"
            return envelope(data=l0)
        cached = _store_get(cache_key, ttl=86400)
        if cached:
            cached["_cache_hit"] = True
            cached["_cache_level"] = "l1_redis"
            _cache_seat_bd.set(("seat_bd", code), cached)  # 回填 L0
            return envelope(data=cached)

    _empty = {"code": code, "rows": [], "all_rows_count": 0, "last_date": None,
              "categories": [], "total_amount_wan": None,
              "intraday": {}, "risks": [], "signals": {"positive": [], "warning": []}, "tags": []}
    try:
        breakdown = await asyncio.wait_for(
            to_thread(seat_classify.build_breakdown, code), timeout=18)
    except asyncio.TimeoutError:
        log.warning(f"seat_breakdown {code} 18s 超时(akshare 冷启/限频),降级空表")
        # R47 (Batch 5): 超时降级但保 partial — 如果有 24h cache 拿 partial 兜底
        cached = _store_get(cache_key, ttl=86400)
        if cached:
            log.info(f"seat_breakdown {code} 超时, 兜底 24h cache")
            cached["_degraded"] = "upstream_timeout_cached"
            cached["_cache_level"] = "l1_redis_fallback"
            _cache_seat_bd.set(("seat_bd", code), cached)
            return envelope(data=cached)
        return envelope(data={**_empty, "_degraded": "upstream_timeout"})
    except Exception as e:
        log.warning(f"seat_breakdown {code} 异常: {e}")
        return envelope(data={**_empty, "_degraded": "upstream_error"})

    if breakdown:
        _store_set(cache_key, breakdown, ttl=86400)
        _cache_seat_bd.set(("seat_bd", code), breakdown)  # R48: 预热 L0 给同 worker 后续访问
    return envelope(data=breakdown or _empty)


async def _warm_intraday_today_async(code: str, cache_key: str):
    """R51 (Batch 6): 后台异步拉今日分时写 L1 today cache (60s TTL)"""
    try:
        from datetime import datetime
        today_str = datetime.now().strftime("%Y-%m-%d")
        result = await asyncio.wait_for(
            to_thread(_fetch_intraday_today_tencent_first, code),
            timeout=10,
        )
        if result and result.get("ticks"):
            result["date"] = today_str
            _store_set(cache_key, {"intraday_today": result}, ttl=60)
            log.debug(f"async warm intraday today {code} OK ({len(result['ticks'])} ticks)")
    except Exception as e:
        log.debug(f"async warm intraday today {code} err: {e}")


@app.get("/api/stock/{code}/intraday_5d")
async def stock_intraday_5d(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    个股近 5 日分时 + 封成比。
    - 5 日日线 (本地 cache)
    - 5 日封成比 / 封单金额 / 连板 (涨停池)
    - 今日分时 tick (akshare stock_intraday_em)
    历史分钟 K 走东财 RemoteDisconnected 拿不到,盘中分时 tick 兜底

    R51 (Batch 6): Redis cache 双 TTL —
      - 今日分时 part: TTL 60s (盘中实时)
      - 历史 4 日 part: TTL 30min (历史不变)
      - daily_5d: TTL 5min (收盘后不变,但盘中有变化)
    """
    code = _require_valid_code(code)
    _touch_recent(code)
    from . import error_stats as _es
    _es.record("/api/stock/{code}/intraday_5d")

    cache_key_today = cache_store.K.INTRADAY_5D_TODAY.format(code=code)
    cache_key_hist  = cache_store.K.INTRADAY_5D_HIST.format(code=code)
    if not fresh:
        # 合并两段缓存: today 部分独立 60s, hist 部分独立 30min
        cached_today = _store_get(cache_key_today, ttl=60)
        cached_hist  = _store_get(cache_key_hist, ttl=1800)
        if cached_hist:
            out = dict(cached_hist)
            if cached_today:
                out["intraday_today"] = cached_today.get("intraday_today")
            out["_cache_hit"] = True
            out["_cache_level"] = "l1_redis" + ("_today" if cached_today else "_hist_only")
            # R51 (Batch 6): hist 命中但 today 缺失 → 后台异步拉 today 写 L1
            # 避免下次请求还要等 5-10s akshare
            if not cached_today:
                try:
                    from datetime import datetime
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    # 盘中才预热,非交易时段 today 永远拿不到数据
                    minute_of_day = datetime.now().hour * 60 + datetime.now().minute
                    if datetime.now().weekday() < 5 and 9*60+25 <= minute_of_day <= 15*60:
                        asyncio.create_task(_warm_intraday_today_async(code, cache_key_today))
                except Exception:
                    pass
            return envelope(data=out)

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

        # R51 (Batch 6): stage 4 完成时立即写 L1 hist — 即使 stage 5 intraday 超时, 历史段已就绪
        # 把 today 字段先去掉再写 (避免重复写)
        hist_only = {k: v for k, v in out.items() if k != "intraday_today"}
        if not fresh and (hist_only.get("daily_5d") or hist_only.get("intraday_per_day")):
            _store_set(cache_key_hist, hist_only, ttl=1800)

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

        # R-aug-01 (2026-08-01): 日期防御 — 跨日脏数据清洗
        # 多源并行无日期校验时,tick 可能混入昨日/前日 1min 数据,污染当日图
        # 仅对 today source (akshare/today) 开 allow_time_only,因为 today tick 本就只有 HH:MM:SS
        if out.get("intraday_today") and out["intraday_today"].get("ticks"):
            _src = out["intraday_today"].get("source", "")
            _allow_t = _src in ("akshare", "akshare_intraday_em", "tencent_1min", "tencent_intraday")
            _ticks_clean, _dates = _filter_intraday_ticks_for_date(
                out["intraday_today"]["ticks"], today_str.replace("-", ""),
                allow_time_only=_allow_t,
            )
            if len(_ticks_clean) < len(out["intraday_today"]["ticks"]):
                log.warning(
                    f"intraday_5d {code} today tick 过滤 {len(out['intraday_today']['ticks'])}→{len(_ticks_clean)} "
                    f"(dates={sorted(_dates)}, src={_src})"
                )
            out["intraday_today"]["ticks"] = _ticks_clean
            out["intraday_today"]["ticks_n"] = len(_ticks_clean)

        # akshare 失败 → tencent 1min 兜底(关键,沙箱 DNS 劫持环境必须)
        if not out.get("intraday_today") or not out["intraday_today"].get("ticks"):
            ten = _fetch_intraday_today_tencent_first(code)
            if ten and ten.get("ticks"):
                out["intraday_today"] = ten
            else:
                out["note"] = "今日分时未取到(akshare 断连, tencent 兜底失败)"

        # R51 (Batch 6): stage 5 完成立即写 L1 today
        if not fresh and out.get("intraday_today") and out["intraday_today"].get("ticks"):
            _store_set(cache_key_today, {"intraday_today": out["intraday_today"]}, ttl=60)

        # 6) 5 日每时 intraday(拼合多日分时,用于多日连续分时图)
        out["intraday_per_day"] = _fetch_intraday_per_day(code, recent5, out.get("intraday_today"))

        return out

    try:
        # R51 (Batch 6): 超时 30→40s — 5 日涨停池 + akshare 全冷启,周末/晚间限频时必超 30s
        # 即使超时也返回 partial — _load 已 stage 化缓存,任一阶段成功就写 L1
        result = await asyncio.wait_for(to_thread(_load), timeout=40)
    except asyncio.TimeoutError:
        log.warning(f"intraday_5d {code} 超时 40s, 尝试读 partial cache")
        # 超时兜底 — 试 L1 partial
        cached_today = _store_get(cache_key_today, ttl=60)
        cached_hist  = _store_get(cache_key_hist, ttl=1800)
        if cached_hist or cached_today:
            out = dict(cached_hist or {"code": code, "daily_5d": [], "intraday_per_day": {"days": []}})
            if cached_today:
                out["intraday_today"] = cached_today.get("intraday_today")
            out["_cache_hit"] = True
            out["_cache_level"] = "l1_redis_partial_fallback"
            out["_degraded"] = "timeout_partial"
            return envelope(data=out)
        return envelope_degraded(
            data={"code": code, "daily_5d": [], "intraday_today": None},
            stale_key=f"stock_intraday_5d:{code}",
            stale_max_age=_STALE_TTL["stock_intraday_5d"],
            degraded_reason="intraday_5d_timeout",
        )
    except Exception as e:
        log.warning(f"intraday_5d {code} 异常: {e}")
        return envelope_degraded(
            data={"code": code, "daily_5d": [], "intraday_today": None},
            stale_key=f"stock_intraday_5d:{code}",
            stale_max_age=_STALE_TTL["stock_intraday_5d"],
            degraded_reason="intraday_5d_error",
        )
    if result is None:
        return envelope_degraded(
            data={"code": code, "daily_5d": [], "intraday_today": None},
            stale_key=f"stock_intraday_5d:{code}",
            stale_max_age=_STALE_TTL["stock_intraday_5d"],
            degraded_reason="intraday_5d_failed",
        )
    # R51 (Batch 6): 写双段缓存 — 今日 60s, 历史 30min
    if not fresh and isinstance(result, dict):
        # today part
        if result.get("intraday_today"):
            _store_set(cache_key_today, {"intraday_today": result["intraday_today"]}, ttl=60)
        # hist part: 把 today 去掉再存(避免重复)
        hist_only = {k: v for k, v in result.items() if k != "intraday_today"}
        if hist_only.get("daily_5d") or hist_only.get("intraday_per_day"):
            _store_set(cache_key_hist, hist_only, ttl=1800)
    # 保存陈旧数据供降级兜底
    if isinstance(result, dict):
        _stale_save(f"stock_intraday_5d:{code}", result)
    return envelope(data=result)


def _fetch_intraday_for_date(code: str, date_str: str, prefer_source: str = "") -> dict:
    """
    多源并行糅合 intraday tick — 2026-07-16 重写。
    同时启动 akshare / tencent / sina / efinance 四源,各线程独立运行,
    超时后收集全部结果,选 tick 数最多(粒度最细)的源为主数据,
    多源均返回时打 blended 标签。
    """
    from datetime import datetime
    import threading, time as _time
    out = {"code": code, "date": date_str, "ticks": [], "ticks_n": 0, "source": "", "note": "", "prev_close": None}
    today_str = datetime.now().strftime("%Y-%m-%d")
    is_today = (date_str == today_str)
    ymd = date_str.replace("-", "")

    TIMEOUT = 12     # 总超时 12s,并行各源共享
    EARLY_EXIT_TICKS = 200  # ≥200 tick 即认为粒度够好,提前返回
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    lock = threading.Lock()
    done_early = threading.Event()
    results = []  # [{source, ticks, prev_close}, ...]

    def _push(src, ticks, pc):
        with lock:
            results.append({"source": src, "ticks": list(ticks), "prev_close": pc})
            if len(ticks) >= EARLY_EXIT_TICKS:
                done_early.set()

    # ── helper: akshare tick 提取 ──
    def _ak_parse(df, src_name):
        tcks = []
        pc = None
        for _, r in df.iterrows():
            tcks.append({
                "time":        str(r.get("时间", "")),
                "price":       _safe_float(r.get("成交价", r.get("收盘", r.get("最新价")))),
                "open":        _safe_float(r.get("开盘", 0)) or None,
                "high":        _safe_float(r.get("最高", 0)) or None,
                "low":         _safe_float(r.get("最低", 0)) or None,
                "volume_hand": _safe_float(r.get("手数", r.get("成交量"))),
                "amount":      _safe_float(r.get("成交额", 0)) or None,
                "side":        str(r.get("买卖盘性质", "")),
            })
            if r.get("昨收") is not None and pc is None:
                pc = _safe_float(r.get("昨收"))
        if pc is None and tcks:
            for t in tcks:
                if t.get("open") and t["open"] > 0:
                    pc = t["open"]
                    break
        if tcks:
            _push(src_name, tcks, pc)

    # ── 1) akshare ──
    def _ak_worker():
        try:
            import akshare as ak
            if is_today:
                df = ak.stock_intraday_em(symbol=code)
                _ak_parse(df, "akshare_intraday_em")
            else:
                try:
                    df = ak.stock_zh_a_hist_min_em(
                        symbol=code, period="1", start_date=ymd, end_date=ymd, adjust="qfq")
                    if df is not None and not df.empty:
                        _ak_parse(df, "akshare_1m")
                        return
                except Exception:
                    pass
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code, period="5", start_date=ymd, end_date=ymd, adjust="qfq")
                _ak_parse(df, "akshare_5m")
        except Exception as e:
            log.info(f"akshare 并行异常: {e}")

    # ── 2) sina 5min K ──
    def _sina_worker():
        try:
            import requests as _req, json as _json
            url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            r = _req.get(url, params={"symbol": f"{mkt}{code}", "scale": "5", "ma": "no", "datalen": "1440"},
                         timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
            if r.status_code != 200 or not r.text.strip().startswith("["):
                return
            arr = _json.loads(r.text)
            tcks, pc = [], None
            day_prefix = date_str
            for it in arr:
                day = it.get("day", "")
                if not day.startswith(day_prefix):
                    continue
                tcks.append({
                    "time":        day[11:19] if len(day) >= 19 else day,
                    "price":       _safe_float(it.get("close")),
                    "open":        _safe_float(it.get("open")) or None,
                    "high":        _safe_float(it.get("high")) or None,
                    "low":         _safe_float(it.get("low")) or None,
                    "volume_hand": _safe_float(it.get("volume")),
                    "amount":      None,
                    "side":        "",
                })
            if tcks:
                for t in tcks:
                    if t.get("open") and t["open"] > 0:
                        pc = t["open"]
                        break
                _push("sina_5m", tcks, pc)
        except Exception as e:
            log.info(f"sina 并行异常: {e}")

    # ── 3) 腾讯分钟 ──
    #   今日: minute/query 逐笔分时;历史: mkline m1 1分钟K (覆盖最近 ~5 交易日,
    #   akshare 周末/晚间限频时保证历史日仍拿 1min 而非掉到 sina 5min)
    def _tencent_worker():
        try:
            import requests as _req, json as _json
            if is_today:
                url = "http://web.ifzq.gtimg.cn/appstock/app/minute/query"
                r = _req.get(url, params={"code": f"{mkt}{code}"}, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
                if r.status_code != 200:
                    return
                j = r.json()
                raw = (j.get("data") or {}).get(f"{mkt}{code}", {}).get("data", {}).get("data") or []
                tcks = []
                for line in raw:
                    parts = line.split(" ")
                    if len(parts) < 3:
                        continue
                    t = parts[0]
                    if len(t) == 4 and t.isdigit():
                        t = f"{t[:2]}:{t[2:]}:00"
                    p = _safe_float(parts[1])
                    if p is None or p == 0 or not t:
                        continue
                    tcks.append({
                        "time": t, "price": p,
                        "volume_hand": _safe_float(parts[2]) if len(parts) > 2 else None,
                        "amount":      _safe_float(parts[3]) if len(parts) > 3 else None,
                        "open": None, "high": None, "low": None, "side": "",
                    })
                if tcks:
                    # tencent 无 prev_close,留 None
                    _push("tencent_minute", tcks, None)
            else:
                # mkline m1 行格式: [YYYYMMDDHHMM, close, open, high, low, vol手, {}, amount万]
                url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
                r = _req.get(url, params={"param": f"{mkt}{code},m1,,1600"}, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
                if r.status_code != 200:
                    return
                node = (r.json().get("data") or {}).get(f"{mkt}{code}", {}) or {}
                m1 = node.get("m1") or []
                pc = _safe_float(node.get("prec"))
                tcks = []
                for row in m1:
                    if not row or len(row) < 5:
                        continue
                    ts = str(row[0])
                    if len(ts) < 12 or ts[:8] != ymd:
                        continue
                    hhmm = ts[8:12]
                    tcks.append({
                        "time":        f"{hhmm[:2]}:{hhmm[2:]}:00",
                        "price":       _safe_float(row[1]),
                        "open":        _safe_float(row[2]) or None,
                        "high":        _safe_float(row[3]) or None,
                        "low":         _safe_float(row[4]) or None,
                        "volume_hand": _safe_float(row[5]) if len(row) > 5 else None,
                        "amount":      _safe_float(row[7]) if len(row) > 7 else None,
                        "side":        "",
                    })
                if tcks:
                    _push("tencent_m1", tcks, pc)
        except Exception as e:
            log.info(f"tencent 并行异常: {e}")

    # ── 4) efinance 5min K 兜底 ──
    def _ef_worker():
        try:
            import threading as _thr
            bx = {"df": None}
            def _run():
                try:
                    import efinance as ef
                    bx["df"] = ef.stock.get_quote_history(code, beg=ymd, end=ymd, klt=5, fqt=1)
                except Exception:
                    pass
            t = _thr.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=8)
            df = bx["df"]
            if df is None or df.empty:
                return
            tcks, pc = [], None
            for _, r in df.iterrows():
                tm = str(r.get("时间", r.get("日期", "")))
                tcks.append({
                    "time": tm, "price": _safe_float(r.get("收盘", r.get("最新价"))),
                    "volume_hand": _safe_float(r.get("成交量")),
                    "amount":      _safe_float(r.get("成交额")),
                    "open":        _safe_float(r.get("开盘")),
                    "high":        _safe_float(r.get("最高")),
                    "low":         _safe_float(r.get("最低")),
                    "side": "",
                })
            if tcks:
                for t in tcks:
                    if t.get("open") and t["open"] > 0:
                        pc = t["open"]
                        break
                _push("efinance_5m", tcks, pc)
        except Exception as e:
            log.info(f"efinance 并行异常: {e}")

    # ── 启动所有线程,轮询等待(early-exit 优化) ──
    workers = [
        threading.Thread(target=_ak_worker, daemon=True),
        threading.Thread(target=_sina_worker, daemon=True),
        threading.Thread(target=_tencent_worker, daemon=True),
        threading.Thread(target=_ef_worker, daemon=True),
    ]
    deadline = _time.time() + TIMEOUT
    for w in workers:
        w.start()
    # 轮询:每 0.5s 检查 early-exit 信号或 deadline
    while _time.time() < deadline:
        if done_early.is_set():
            break
        # 检查是否所有线程已结束
        if all(not w.is_alive() for w in workers):
            break
        _time.sleep(0.5)
    # 确保至少等所有线程启动后的第一次检查
    for w in workers:
        w.join(timeout=0.1)

    # ── 选最佳: tick 数最多 = 粒度最细 ──
    if not results:
        out["note"] = (f"{date_str} 分时拉取失败"
                       "(akshare/sina/tencent/efinance 四源并行全挂)")
        return out

    results.sort(key=lambda r: len(r["ticks"]), reverse=True)
    best = results[0]
    out["ticks"] = best["ticks"]
    out["ticks_n"] = len(best["ticks"])
    # prev_close: 优先用最佳源的;若无,从其他源补
    out["prev_close"] = best["prev_close"]
    if out["prev_close"] is None:
        for r in results:
            if r["prev_close"] is not None and r["prev_close"] > 0:
                out["prev_close"] = r["prev_close"]
                break
    # 如多源均有数据且最佳源 < 次佳源 2 倍,才糅合去重
    if len(results) >= 2:
        second = results[1]
        ratio = len(best["ticks"]) / max(len(second["ticks"]), 1)
        # 当次佳源有 60%+ 有效数据 → 糅合标记
        if ratio < 1.8:
            srcs = [r["source"] for r in results[:3]]
            out["source"] = "blended_" + "+".join(srcs)
            # 去重 merge: 按 time 补缺失 tick (但不替代主源已有数据)
            seen = {t["time"] for t in out["ticks"]}
            for r in results[1:]:
                for t in r["ticks"]:
                    if t["time"] and t["time"] not in seen:
                        out["ticks"].append(t)
                        seen.add(t["time"])
            out["ticks"].sort(key=lambda t: t.get("time", ""))
            out["ticks_n"] = len(out["ticks"])
        else:
            out["source"] = best["source"]
    else:
        out["source"] = best["source"]
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


def _filter_intraday_ticks_for_date(ticks: list, ymd_compact: str, *, allow_time_only: bool = False) -> tuple[list, set]:
    """
    按精确日期过滤 intraday ticks,strip 掉日期前缀。
    ymd_compact: "20260722" 形式
    allow_time_only: 当 ticks 来自 today source (akshare/tencent 1min) 时,tick 只有 HH:MM:SS
                     没有日期前缀,只要日期对得上 today 就放行 (否则历史 1min 都拒)

    返回:
      filtered:    只保留匹配 ymd_compact 的 tick (time 已 strip 日期前缀,只剩 HH:MM:SS)
      actual_dates: 原始 ticks 里所有能解析出的日期集合 (用于追踪污染/调试)
    """
    import re as _re
    from datetime import datetime as _dt
    out, dates = [], set()
    for t in ticks or []:
        raw = (t.get("time") if isinstance(t, dict) else "") or ""
        s = str(raw).strip()
        if not s:
            continue
        # 格式 1: "2026-07-22 09:30:00"
        if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
            d_part = s[:10].replace("-", "")
            t_part = s[10:].lstrip(" ")
            if d_part == ymd_compact:
                out.append({**t, "time": t_part} if isinstance(t, dict) else t)
            dates.add(d_part)
            continue
        # 格式 2: "202607220930" (Tencent compact 12 位 = 8-digit date + 4-digit HHMM)
        m = _re.fullmatch(r"(\d{8})(\d{4})", s)
        if m:
            d_part, hhmm = m.group(1), m.group(2)
            if d_part == ymd_compact:
                out.append({**t, "time": f"{hhmm[:2]}:{hhmm[2:4]}:00"} if isinstance(t, dict) else t)
            dates.add(d_part)
            continue
        # 格式 3: 只有 "09:30:00" — 历史源严禁放行;today 源允许
        if _re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", s):
            if allow_time_only:
                out.append(t)
            continue
        # 其它格式: 既不可信也不暴露给 caller
    return out, dates


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

        # 历史日期:从并行结果取 + 按精确日期防御过滤(历史源严禁 allow_time_only)
        sub = hist_results.get(d, {"ticks": [], "ticks_n": 0, "source": ""})
        if sub.get("ticks"):
            _ticks_clean, _dates = _filter_intraday_ticks_for_date(
                sub["ticks"], d.replace("-", ""), allow_time_only=False,
            )
            if len(_ticks_clean) < len(sub["ticks"]):
                log.warning(
                    f"intraday_per_day {code} {d} 过滤 {len(sub['ticks'])}→{len(_ticks_clean)} "
                    f"(dates={sorted(_dates)}, src={sub.get('source', '')})"
                )
            day_obj["ticks"] = _ticks_clean
            day_obj["ticks_n"] = len(_ticks_clean)
            day_obj["source"] = sub.get("source", "")
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
    code = _require_valid_code(code)
    # 归一化 date
    d = (date or "").strip()
    if not d:
        from datetime import datetime
        d = datetime.now().strftime("%Y-%m-%d")
    d = d.replace("/", "-").replace(".", "-")
    if len(d) == 8 and d.isdigit():
        d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    # R53 (Batch 6): L0 进程内 TTLCache 60s (per code+date) — 跳 Redis 走同 worker
    l0_key = ("intraday", code, d)
    l0 = _cache_intraday.get(l0_key)
    if l0 is not None:
        l0["_cache_hit"] = True
        l0["_cache_level"] = "l0_mem"
        return envelope(data=l0)

    def _load():
        return _fetch_intraday_for_date(code, d)

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=12)
    except asyncio.TimeoutError:
        return envelope_degraded(
            data={"code": code, "date": d, "ticks": [], "ticks_n": 0, "note": "超时"},
            stale_key=f"stock_intraday:{code}:{d}",
            stale_max_age=_STALE_TTL["stock_intraday"],
            degraded_reason="intraday_timeout",
        )
    except Exception as e:
        log.warning(f"intraday {code} {d} 异常: {e}")
        return envelope_degraded(
            data={"code": code, "date": d, "ticks": [], "ticks_n": 0, "note": str(e)[:200]},
            stale_key=f"stock_intraday:{code}:{d}",
            stale_max_age=_STALE_TTL["stock_intraday"],
            degraded_reason="intraday_error",
        )
    # R53: 写 L0;L1 Redis 复用 K.INTRADAY:{date}:{code} (TTL 30min 历史/盘中兜底)
    if result and result.get("ticks"):
        # R-aug-01 (2026-08-01): 日期防御 — 按精确日期过滤 tick,strip 日期前缀
        # 今日 (akshare/today) tick 只有 HH:MM:SS → allow_time_only=True
        # 历史 (sina/em hist_min) tick 有完整 YYYY-MM-DD HH:MM:SS → 严格匹配
        from datetime import datetime as _dt2
        _is_today = (d == _dt2.now().strftime("%Y-%m-%d"))
        _ticks_clean, _dates = _filter_intraday_ticks_for_date(
            result["ticks"], d.replace("-", ""), allow_time_only=_is_today,
        )
        if len(_ticks_clean) < len(result["ticks"]):
            log.warning(
                f"intraday {code} {d} 过滤 {len(result['ticks'])}→{len(_ticks_clean)} "
                f"(dates={sorted(_dates)}, src={result.get('source', '')})"
            )
        result["ticks"] = _ticks_clean
        result["ticks_n"] = len(_ticks_clean)
        # 2026-07-19: 注入支撑/压力位 (1/3 回升位 + A/B + 5日线) — 复用 weekly_bull + recovery_level 缓存
        try:
            # 复用模块级别已导入的 _recovery (line ~9301)
            rl = _recovery.analyze_recovery(code, stock_kline_loader) or {}
            # 5 日线 MA5: 直接复用 stock_kline_loader
            ma5_series = []
            try:
                daily = stock_kline_loader(code, 10) or []
                ma5_series = [k.get("ma5") for k in daily if k.get("ma5") is not None]
            except Exception:
                pass
            support_levels = {}
            if rl.get("has_signal"):
                support_levels = {
                    "A": rl.get("A"),
                    "B": rl.get("B"),
                    "level_1_3": rl.get("level_1_3"),
                    "level_1_2": rl.get("level_1_2"),
                    "level_2_3": rl.get("level_2_3"),
                }
            if ma5_series:
                support_levels["daily_ma5"] = ma5_series
            if support_levels:
                result["support_levels"] = support_levels
        except Exception:
            pass
        _cache_intraday.set(l0_key, result)
        # 历史日走 30min, 今日走 5min (盘中变化) — R51 同样的双 TTL 思路
        from datetime import datetime
        is_today = (d == datetime.now().strftime("%Y-%m-%d"))
        ttl = 300 if is_today else 1800
        cache_key = f"intraday:{d}:{code}"
        try:
            get_store().set(cache_key, result, ttl=ttl)
        except Exception:
            pass
    # 保存陈旧数据供降级兜底
    if isinstance(result, dict):
        _stale_save(f"stock_intraday:{code}:{d}", result)
    return envelope(data=result)


# ─────────────────────────────────────────────────────────────
# NEWS 模块:/api/news + /api/news/refresh + /api/news/analyze
# ─────────────────────────────────────────────────────────────
# 模块通过顶部 _LazyModule 代理在首次请求时加载

@app.get("/api/news")
async def news_list(refresh: bool = Query(False, description="是否强制刷新抓取")):
    """
    返回当前新闻缓存(含 AI 评分)。
    - refresh=true 时:重新抓取 sina,但不强制 AI 重跑
    - 新闻按 ctime 倒序,AI 评分内嵌到每条 news.ai 字段

    R62 (Batch 7): L0 5min TTLCache — 同 worker 重复刷新闻列表秒开 (下游 SQLite 已 30min TTL)
    """
    if not refresh:
        # L0 hit (5min 内秒返)
        l0 = _cache_news.get(("news_list",))
        if l0 is not None:
            l0["_cache_hit"] = True
            l0["_cache_level"] = "l0_mem"
            return envelope(data=l0)
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
        return envelope(error="news 拉取超时", data={"news": [], "count": 0, "_degraded": "timeout"})
    if not refresh and result:
        _cache_news.set(("news_list",), result)
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
    model   = ai_client.default_model()
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
        return envelope(error="news AI 超时 120s", data={"analyzed": 0, "_degraded": "ai_timeout"})
    return envelope(data=result)


@app.post("/api/news/refresh")
async def news_refresh():
    """
    强制重新抓取 + 立即跑 AI 分析(用于前端"刷新"按钮)。
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    model   = ai_client.default_model()
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
        return envelope(error="news refresh 超时", data={"fetched": 0, "analyzed": 0, "_degraded": "timeout"})
    return envelope(data=result)


@app.get("/api/stock/{code}/sector")
async def stock_sector(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    个股板块分类(交易所板块 + 4 套行业)

    R61 (Batch 7): L0 1h + L1 24h Redis 缓存 (板块字典级别稳定)
    """
    from .sector_classify import get_sector
    code = _require_valid_code(code)

    if not fresh:
        # L0 mem (同 worker 1h 内秒开)
        l0 = _cache_sector.get(("sector", code))
        if l0 is not None:
            l0["_cache_hit"] = True
            l0["_cache_level"] = "l0_mem"
            return envelope(data=l0)
        # L1 Redis 24h (跨 worker 共享)
        cache_key = cache_store.K.SECTOR_BY_CODE.format(code=code)
        cached = _store_get(cache_key, ttl=86400)
        if cached:
            cached["_cache_hit"] = True
            cached["_cache_level"] = "l1_redis"
            _cache_sector.set(("sector", code), cached)  # 回填 L0
            return envelope(data=cached)

    def _load():
        return get_sector(code)
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=10)
    except asyncio.TimeoutError:
        # R61: 超时也读 L1 兜底
        cache_key = cache_store.K.SECTOR_BY_CODE.format(code=code)
        cached = _store_get(cache_key, ttl=86400)
        if cached:
            cached["_degraded"] = "upstream_timeout_cached"
            return envelope(data=cached)
        return envelope(error="sector 超时", data={"code": code, "_degraded": "timeout"})
    if not fresh and result:
        # 写 L0 + L1
        _cache_sector.set(("sector", code), result)
        try:
            _store_set(cache_store.K.SECTOR_BY_CODE.format(code=code), result, ttl=86400)
        except Exception:
            pass
    return envelope(data=result)


# 2026-08-01: 公司画像 (营业范围 / 主营构成 / 行业地位 / 概念板块)
# 仅拉 4 个字段,不跑业绩/PE 聚合,独立 6h 缓存
@app.get("/api/stock/{code}/profile")
async def stock_profile(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    4 段信息:
      profile        — 公司档案 (名称/地址/员工/法定代表人/网站...)
      biz_breakdown  — 主营构成 (按产品/按地区) + 经营评述
      concepts_pack  — 所属概念板块 + 核心题材/行业地位
      profile_meta   — 多板块重合提示 (concepts 数)
    """
    from . import fundamentals as _fund
    code = _require_valid_code(code)

    cache_key = cache_store.K.STOCK_PROFILE.format(code=code)
    if not fresh:
        cached = _store_get(cache_key, ttl=21600)
        if cached:
            cached["_cache_hit"] = True
            cached["_cache_level"] = "l1_redis"
            return envelope(data=cached)

    def _load():
        # 复用 _fetch 函数,避免 4 路重复 IO
        profile = _fund._fetch_profile_em(code)
        biz_bd = _fund._fetch_business_breakdown_em(code)
        conc = _fund._fetch_concepts_em(code)
        return {
            "code": code,
            "profile": profile,
            "biz_breakdown": biz_bd,
            "concepts_pack": conc,
            "profile_meta": {
                "concept_count": len(conc.get("concepts") or []),
                "precise_concept_count": sum(1 for c in (conc.get("concepts") or []) if c.get("is_precise")),
                "product_count": len(biz_bd.get("by_product") or []),
                "region_count": len(biz_bd.get("by_region") or []),
                "report_date": biz_bd.get("report_date") or "",
            },
        }

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=12)
    except asyncio.TimeoutError:
        cached = _store_get(cache_key, ttl=21600)
        if cached:
            cached["_degraded"] = "upstream_timeout_cached"
            return envelope(data=cached)
        return envelope(error="profile 超时", data={"code": code, "_degraded": "timeout"})

    has_data = bool(
        result.get("profile", {}).get("name")
        or result.get("biz_breakdown", {}).get("by_product")
        or result.get("concepts_pack", {}).get("concepts")
    )
    result["has_data"] = has_data
    result["ts"] = int(time.time())

    if has_data and not fresh:
        try:
            _store_set(cache_key, result, ttl=21600)
        except Exception:
            pass
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
async def stock_related_news(code: str, fresh: int = Query(0, ge=0, le=1)):
    """
    与个股相关的新闻(按 ctime 倒序,最新在前)。

    2026-08-01 收紧相关性 (修"新闻不跟股相关"反馈):
      1) 强命中 (必须返回): 新闻 stocks 列表里有此 code
      2) 强命中: 新闻 sectors 包含该股精确 sw 二级 / 行业 csrc
      3) 名称命中: 新闻标题 / 内容里出现该股简称 (兜底, 应对 AI stocks 列表漏抓)
      4) 弱命中 (降权后置): sw_raw 子段 / l1_cluster / l3_chain 命中 — 仅在强命中 0 时才纳入前 5

    R-fix 2026-07-27: 0 命中 fallback 到最近 5 条财经要闻 (避免空白页)。
    R66 (Batch 7): 6h Redis 缓存。
    """
    from .sector_classify import get_sector
    code = _require_valid_code(code)
    cache_key = cache_store.K.RELATED_NEWS.format(code=code)
    if not fresh:
        cached = _store_get(cache_key, ttl=21600)
        if cached:
            cached["_cache_hit"] = True
            cached["_cache_level"] = "l1_redis"
            return envelope(data=cached)
    def _load():
        sec = get_sector(code)
        sw = sec.get("sw") or ""
        # 强匹配: 行业精确 + sw_raw 子段 (3 级以内)
        sw_keys_strong = []
        if sw:
            sw_keys_strong.append(sw)
        sw_raw = (sec.get("sw_raw") or "")
        # 仅取前 2 个子段 (避免 "计算机-软件-企业级服务-..." 4 层过度泛化)
        for seg in sw_raw.split("-")[:2]:
            seg = seg.strip()
            if seg and seg not in sw_keys_strong:
                sw_keys_strong.append(seg)
        csrc = (sec.get("csrc") or "").strip()
        if csrc and csrc not in sw_keys_strong:
            sw_keys_strong.append(csrc)
        # 弱匹配 (只在前 5 名里允许出现)
        sw_keys_weak = []
        l1 = ((sec.get("taxonomy") or {}).get("level1_cluster") or "").strip()
        if l1 and l1 not in sw_keys_strong:
            sw_keys_weak.append(l1)
        l3 = ((sec.get("taxonomy") or {}).get("level3_chain") or "").strip()
        if l3 and l3 not in sw_keys_strong and l3 not in sw_keys_weak:
            sw_keys_weak.append(l3)
        # 其它子段
        for seg in sw_raw.split("-")[2:]:
            seg = seg.strip()
            if seg and seg not in sw_keys_strong and seg not in sw_keys_weak:
                sw_keys_weak.append(seg)

        # 拿到该股名称用于标题/内容兜底
        stock_name = ""
        try:
            from .fundamentals import _fetch_profile_em
            prof = _fetch_profile_em(code) or {}
            stock_name = (prof.get("name") or "").strip()
        except Exception:
            stock_name = ""

        cache = news_lookup.load_cache()
        news = cache.get("news") or []
        ai = cache.get("ai") or {}
        strong_hits = []
        weak_hits = []
        for n in news:
            a = ai.get(n["id"])
            a_stocks = (a or {}).get("stocks") or []
            a_sectors = (a or {}).get("sectors") or []
            hit_reasons = []
            hit_kind = None  # 'strong' | 'weak' | None
            # 1) 强: code 命中
            if code in a_stocks:
                hit_reasons.append(f"提及{stock_name or code}")
                hit_kind = "strong"
            # 2) 强: 行业精确匹配
            if not hit_kind:
                for k in sw_keys_strong:
                    if k and k in a_sectors:
                        hit_reasons.append(f"行业={k}")
                        hit_kind = "strong"
                        break
            # 3) 名称命中: AI 漏抓但新闻里出现该股简称
            if not hit_kind and stock_name and len(stock_name) >= 3:
                title = (n.get("title") or "").strip()
                content = (n.get("content") or n.get("summary") or "").strip()
                if stock_name in title or stock_name in content:
                    hit_reasons.append(f"标题/内容含{stock_name}")
                    hit_kind = "strong"
            # 3.5) AI 没评分但标题/内容里含该股 sw 行业关键字 (如"白酒""食品饮料")
            if not hit_kind:
                title = (n.get("title") or "").strip()
                content = (n.get("content") or n.get("summary") or "").strip()
                for k in sw_keys_strong:
                    if k and len(k) >= 2 and (k in title or k in content):
                        hit_reasons.append(f"标题/内容含{k}")
                        hit_kind = "strong"
                        break
            # 4) 弱: 板块/l1/l3 命中
            if not hit_kind:
                for k in sw_keys_weak:
                    if k and k in a_sectors:
                        hit_reasons.append(f"宽口径={k}")
                        hit_kind = "weak"
                        break
            if not hit_kind:
                continue
            item = dict(n)
            item["ai"] = a or {}
            item["hit_reason"] = " · ".join(hit_reasons) if hit_reasons else "相关"
            item["_hit_kind"] = hit_kind
            (strong_hits if hit_kind == "strong" else weak_hits).append(item)
        # 排序: 强优先 (ctime 倒序) + 弱补到 5 条
        strong_hits.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
        weak_hits.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
        matched = strong_hits + weak_hits[: max(0, 5 - len(strong_hits))]
        # 彻底 0 命中 — fallback 到最近 5 条财经要闻
        degraded = False
        if not matched and news:
            matched = []
            for n in news[:5]:
                a = ai.get(n["id"])
                item = {**n, "hit_reason": "近期财经要闻", "_hit_kind": "fallback"}
                if a:
                    item["ai"] = a
                matched.append(item)
            degraded = True
        return {
            "code": code,
            "sector": sec,
            "news": matched,
            "count": len(matched),
            "_degraded_fallback": degraded,
            "_strong_count": len(strong_hits),
            "_weak_count": len(weak_hits),
        }
    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=8)
    except asyncio.TimeoutError:
        # R66: 超时兜底
        cached = _store_get(cache_key, ttl=21600)
        if cached:
            cached["_degraded"] = "upstream_timeout_cached"
            return envelope(data=cached)
        return envelope(error="related_news 超时", data={"code": code, "news": [], "_degraded": "timeout"})
    if not fresh and result:
        _store_set(cache_key, result, ttl=21600)
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
        return envelope(error="板块数据拉取超时", data={"sectors": [], "_degraded": "timeout"})
    except Exception as e:
        log.warning(f"sectors_realtime 失败: {e}")
        return envelope(error=f"板块数据失败: {e}", data={"sectors": [], "_degraded": "upstream_error"})

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

    R63 (Batch 7): 6h Redis 缓存 — 今日新闻聚合结果盘中变化慢, 6h 足够
    """
    from .sector_classify import SW_31
    cache_key = cache_store.K.SECTORS_SW_AGG
    cached = _store_get(cache_key, ttl=21600)
    if cached:
        cached["_cache_hit"] = True
        cached["_cache_level"] = "l1_redis"
        return envelope(data=cached)
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
        return envelope(error="sectors 超时", data={"sectors": [], "_degraded": "timeout"})
    if result:
        _store_set(cache_key, result, ttl=21600)
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


# Batch 2 R53 (2026-07-19): board + filters 已迁入 all_stocks.py APIRouter
# kept for backwards compatibility — delete after frontend fully migrated
from .all_stocks import router as _all_stocks_router
app.include_router(_all_stocks_router)

# ZT 涨停板溢价策略
from . import zt_screener as _zt_screener
_zt_screener.register(app)

# 得鑫量变术 四阶段量化选股
from . import dexin_screener as _dexin_screener
_dexin_screener.register(app)




# ═════════════════════════════════════════════════════════════════
# 选股回测 (2026-07-14 · 一键回测 + 多窗口 + 9:30-10:00 S1/S2/S3 + open)
#   - POST /api/screener/backtest  body={"periods":[...]}  启动回测, 返 run_id
#   - GET  /api/screener/backtest?run_id=...  查状态 + 结果
#   - 同一 run_id 全局串行, 防 _EXECUTOR 并发打架
# ═════════════════════════════════════════════════════════════════
_BT_RUNS: dict[str, dict] = {}   # R51: 已迁 cache_store, 此 dict 仅作本地 fast-path 缓存 (跨 worker 不可见)
_BT_RUN_LOCK = threading.Lock()   # R51: 本地锁 — 保护 fast-path cache + 序列化 cache_store 写
_BT_RUN_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bt-run")
_BT_RUN_TTL_SEC = 3600  # R2: GC 1 小时清理已完成回测的结果集,防 memory leak
_BT_TIMEOUT_SEC = 300  # R9: 单次回测上限 5 分钟; 超时直接终止 _BT_RUN_EXECUTOR 线程


# ═════════════════════════════════════════════════════════════════
# R51: 跨进程 state helpers (cache_store 后端, 多 worker 共享)
# ═════════════════════════════════════════════════════════════════
def _bt_k_run(run_id: str) -> str:
    return cache_store.K.BT_RUN.format(run_id=run_id)

def _bt_k_cancel(run_id: str) -> str:
    return cache_store.K.BT_CANCEL.format(run_id=run_id)

def _bt_get(run_id: str) -> dict | None:
    """跨 worker 读 BT_RUN hash (返 {field: value} 或 None)"""
    if not run_id:
        return None
    try:
        return cache_store.get_store().hgetall(_bt_k_run(run_id)) or None
    except Exception as e:
        log.debug(f"[bt-store] hgetall err: {e}")
        return None

def _bt_put_fields(run_id: str, **fields) -> bool:
    """跨 worker 写多个 fields (status/progress/result/error/started_at/finished_at/periods)"""
    if not run_id or not fields:
        return False
    try:
        store = cache_store.get_store()
        ok = True
        for k, v in fields.items():
            # 跨进程共享的 result 字段可能很大 (json.dumps + b64), 用 store 自己的编码
            if not store.hset(_bt_k_run(run_id), k, v, ttl=_BT_RUN_TTL_SEC):
                ok = False
        return ok
    except Exception as e:
        log.debug(f"[bt-store] hset err: {e}")
        return False

def _bt_init(run_id: str, **fields) -> bool:
    """初始化一个 run (status=running, periods, started_at) — 替代 _BT_RUNS[run_id] = {...}"""
    return _bt_put_fields(run_id, status="running", progress="排队中…", **fields)

def _bt_drop(run_id: str) -> None:
    """删 run hash + cancel 标记 — GC 用"""
    try:
        store = cache_store.get_store()
        store.delete(_bt_k_run(run_id))
        store.delete(_bt_k_cancel(run_id))
    except Exception as e:
        log.debug(f"[bt-store] delete err: {e}")

def _bt_is_cancelled(run_id: str) -> bool:
    """检查 run_id 是否被标记取消 — 跨 worker 可见"""
    try:
        return bool(cache_store.get_store().exists(_bt_k_cancel(run_id)))
    except Exception:
        return False

def _bt_mark_cancelled(run_id: str) -> None:
    """标记 run_id 取消 — TTL 10min 后自动清"""
    try:
        cache_store.get_store().set(_bt_k_cancel(run_id), 1, ttl=600)
    except Exception as e:
        log.debug(f"[bt-store] mark cancel err: {e}")

def _bt_lock_acquire(run_id: str, ttl: int = _BT_TIMEOUT_SEC) -> bool:
    """原子获取全局 BT 锁 — 返回 True=拿到(可启动), False=已有回测在跑"""
    try:
        return bool(cache_store.get_store().set_nx(cache_store.K.BT_LOCK, run_id, ttl=ttl))
    except Exception as e:
        log.debug(f"[bt-store] lock acquire err: {e}")
        return False

def _bt_lock_release() -> None:
    """释放全局 BT 锁 — done/error/cancel/timeout 时调用"""
    try:
        cache_store.get_store().delete(cache_store.K.BT_LOCK)
    except Exception as e:
        log.debug(f"[bt-store] lock release err: {e}")

def _bt_lock_held() -> str | None:
    """查询当前持锁 run_id (无则 None) — 用于 SSE 启动时确认"""
    try:
        v = cache_store.get_store().get(cache_store.K.BT_LOCK)
        return v if isinstance(v, str) else None
    except Exception:
        return None

def _bt_active_run_ids() -> list[str]:
    """SCAN 所有 bt:run:* — 用于 GC 巡检"""
    try:
        keys = cache_store.get_store().scan("bt:run:*")
        return [k.replace("bt:run:", "", 1) for k in keys if k.startswith("bt:run:")]
    except Exception as e:
        log.debug(f"[bt-store] scan err: {e}")
        return []

_BT_CANCELLED: set[str] = set()  # R51 兼容: 仍保留本地 set (R97 旧代码兼容),新代码用 _bt_is_cancelled() 跨进程


class _BacktestReq(BaseModel):
    periods:           list[str] = Field(default=[], max_length=12)  # R17: 上限 12 防恶意输入; 子集 eg ["1周","1月"]; 空 = 跑默认
    hold_days:         int = 3         # 持仓天数 (次级 7 套退场对比)
    top_n:             int = 1         # 每日 Top N
    sample:            int = 1200      # 主板采样数 (0=全量, 慢)
    breadth_min:       int = 0         # 大盘红线 (硬底) — 全 A 红 < 该值 当天空仓 (0=禁用)
    breadth_min_soft:  int = 0         # 大盘软线 — 红盘介于 [硬, 软) 区间时只交易热门板块
    sector_hot_topn:   int = 0         # 热门板块 top N (软线叠加使用)
    sector_inflow_topn: int = 0        # 资金净流入板块 top N (amount_ratio 估)
    require_surge_label: bool = False  # 只选"次日大概率异动"标签
    # 2026-07-17 R1: actual_10 默认关闭 — 验证发现 100 只半年需 3 分钟
    enable_actual_10:  bool = False    # 用真实 10:00 close 重算水下退场 (慢, 默认开)
    index_late_up:     bool = False    # 2026-07-17 R21: 大盘尾盘强势 (14:30-15:00 红盘)
    sector_late_up:    bool = False    # 2026-07-17 R22: 个股所在板块尾盘强势
    tail_vol_ratio_min: float = 0.0    # 2026-07-17 R23: 个股尾盘 10min 量比 ≥ X% (0=禁用)
    # 2026-07-18 R54: strategy_id 透传, 默认 baseline
    strategy_id:      str = "baseline"  # baseline | optimized
    # 2026-07-18 R57+: late_high 满格收益折算系数 (默认 1.0 = 用户原意满格)
    #   1.0 = 满格 (理想化: 9:30-10:00 拉到水上即满格卖出)
    #   0.7 = 保守 (实际可能错过部分高位, 7 折)
    #   0.5 = 极保守 (水下大幅震荡更现实, 半折)
    late_high_discount: float = 1.0
    # 2026-07-18 R57+: VWAP 严格过滤开关 (默认 False = 软通, 数据不全时跳过)
    #   True = 必须 VWAP 验证 (需要 48 根 5min K, 历史覆盖率 < 5%, 慎用)
    require_vwap_strict: bool = False
    # optimized 双策略对比: 自动跑 baseline + 主策略并排展示
    compare_to_baseline: bool = False
    # 优化策略模式: 用 OPTIMAL_PARAMS, 自动对比 BASELINE_PARAMS
    optimized_mode: bool = False
    # 2026-07-20: 1000 轮优化器 best params 覆盖 (字段白名单: top_n/hold_days/breadth_min/
    #   breadth_min_soft/sector_hot_topn/sector_inflow_topn/late_high_discount/require_vwap_strict/
    #   regime_adaptive); 缺失字段用 OPTIMAL_PARAMS 默认
    optimized_params: dict | None = None


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
               sector_inflow_topn: int = 0, require_surge_label: bool = False,
               enable_actual_10: bool = False, index_late_up: bool = False,
               sector_late_up: bool = False, tail_vol_ratio_min: float = 0.0,
               strategy_id: str = "baseline",
               late_high_discount: float = 1.0,
               require_vwap_strict: bool = False,
               compare_to_baseline: bool = False,
               optimized_mode: bool = False,
               optimized_params: dict | None = None) -> None:
    from . import backtest_screener as _bt

    # R97: progress_cb 检查取消标记,每步检查一次
    # R28: 200ms debounce — progress_cb 调用 22+ 次/回测, 锁竞争拖慢主线程
    # R51: 取消标记迁 cache_store (跨 worker 可见)
    # R-ship-2026-07-19: 解析 progress 文本 → 真实阶段 %, 写到 cache_store (前端读真值)
    import re as _re
    def _parse_progress_pct(m: str) -> int | None:
        """Map each backtest stage to a monotonic percentage."""
        s = str(m or "")
        if not s: return None
        m1 = _re.search(r"\[pct\s*=\s*(\d{1,3})\]", s)
        if m1:
            return max(0, min(100, int(m1.group(1))))
        if "对比完成" in s or s.strip() in {"回测完成", "完成"}:
            return 100
        if "日线拉取" in s:
            m_fetch = _re.search(r"日线拉取\s*(\d+)\s*/\s*(\d+)", s)
            if m_fetch:
                done, total = int(m_fetch.group(1)), max(1, int(m_fetch.group(2)))
                return 10 + round(18 * min(1.0, done / total))
        if "日线 90s 闸到" in s: return 29
        if "日线完成" in s: return 30
        if "索引完成" in s: return 34
        if "汇总" in s and "Top" not in s: return 88
        m2 = _re.match(r"\s*\[(\d)\s*/\s*(\d)[^\]]*\]\s*(.*)", s)
        if m2:
            stage, _total, detail = int(m2.group(1)), int(m2.group(2)), m2.group(3)
            if stage == 1:
                if "拉股票列表" in detail: return 5
                if "拉交易日历" in detail: return 8
                if "复用日线缓存" in detail: return 30
                if "日线" in detail: return 10
                return 12
            if stage == 2: return 36 if "验证" in detail else 38
            if stage == 3: return 32
        m_fetch_done = _re.search(r"\b(\d+)\s*/\s*(\d+)\b", s)
        if m_fetch_done and ("翻红" in s or "分析中" in s):
            done, total = int(m_fetch_done.group(1)), max(1, int(m_fetch_done.group(2)))
            return 92 + round(4 * min(1.0, done / total))
        kw_pct = [
            ("板块映射", 42), ("热门板块就绪", 48), ("资金流入板块就绪", 53),
            ("5d filter 就绪", 58),
            ("向量筛股", 60), ("尾盘叠加", 62), ("大盘尾盘强度", 64),
            ("板块尾盘强度", 66), ("板块热门 · 取", 45), ("资金流入 · 取", 50),
            ("大盘:", 68), ("硬红线", 68), ("软红线", 68),
            ("5分钟翻红", 92), ("actual_10", 97), ("翻红", 90), ("Top N", 70),
        ]
        for kw, pct in kw_pct:
            if kw in s: return pct
        if "完成" in s: return 72
        return None

    _cb_last = [0.0]
    _cb_last_msg = [""]
    _cb_last_pct = [-1]
    def _cb(msg: str) -> None:
        if _bt_is_cancelled(run_id):
            raise KeyboardInterrupt("user cancelled")
        now = time.time()
        if msg == _cb_last_msg[0] or (now - _cb_last[0]) < 0.2:
            return
        _cb_last[0] = now
        _cb_last_msg[0] = msg
        pct = _parse_progress_pct(msg)
        if pct is not None:
            pct = max(_cb_last_pct[0], pct)
        # R51: 写 cache_store, 本地 fast-path cache 同步更新
        with _BT_RUN_LOCK:
            _bt_put_fields(run_id, progress=msg, **({"progress_pct": pct} if pct is not None else {}))
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id]["progress"] = msg
                if pct is not None:
                    _BT_RUNS[run_id]["progress_pct"] = pct
                    _cb_last_pct[0] = pct
    try:
        # ── 优化策略模式: 一键跑优化策略 vs 基线策略 (不同参数) ──
        if optimized_mode:
            if _cb: _cb("[1/2 优化策略] OPTIMAL_PARAMS")
            opt_bl = _bt.run_optimized_vs_baseline(
                period_keys=period_keys,
                sample=sample,
                progress_cb=_cb,
                optimized_params=optimized_params,
            )
            r = opt_bl.get("optimized", {})
            bl = opt_bl.get("baseline")
            if bl:
                r["_baseline_result"] = bl
                r["_optimized_mode"] = True
                if _cb: _cb("对比完成 ✓")
        # optimized 双策略并跑: 一次请求出 baseline + 主策略对比
        elif compare_to_baseline and strategy_id != "baseline":
            # baseline 用原始默认参数 (并非复刻主策略的参数)
            baseline_overrides = {
                "top_n": 1, "hold_days": 3,
                "breadth_min": 0, "breadth_min_soft": 0,
                "sector_hot_topn": 0, "sector_inflow_topn": 0,
                "late_high_discount": 1.0, "require_vwap_strict": False,
                "regime_adaptive": False,
            }
            dual = _bt.run_dual_strategy(
                compare_to_baseline=True,
                baseline_overrides=baseline_overrides,
                period_keys=period_keys,
                hold_days=hold_days,
                top_n=top_n,
                sample=sample,
                breadth_min=breadth_min,
                breadth_min_soft=breadth_min_soft,
                sector_hot_topn=sector_hot_topn,
                sector_inflow_topn=sector_inflow_topn,
                require_surge_label=require_surge_label,
                enable_actual_10=enable_actual_10,
                index_late_up=index_late_up,
                sector_late_up=sector_late_up,
                tail_vol_ratio_min=tail_vol_ratio_min,
                strategy_id=strategy_id,
                late_high_discount=late_high_discount,
                require_vwap_strict=require_vwap_strict,
                progress_cb=_cb,
            )
            r = dual.get("primary", {})
            bl = dual.get("baseline")
            if bl:
                r["_baseline_result"] = bl
                if _cb: _cb("对比完成 ✓")
        else:
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
                enable_actual_10=enable_actual_10,
                index_late_up=index_late_up,
                sector_late_up=sector_late_up,
                tail_vol_ratio_min=tail_vol_ratio_min,
                strategy_id=strategy_id,
                late_high_discount=late_high_discount,
                require_vwap_strict=require_vwap_strict,
                progress_cb=_cb,
            )
        # R-ship-2026-07-19: result 顶层塞 run_id + strategy_id — 导出/分享/历史可定位
        if isinstance(r, dict):
            r["run_id"] = run_id
            r["strategy_id"] = strategy_id
        # 计算 2 万本金 → 回测后总金额
        if isinstance(r, dict):
            s = r.get("summary") or {}
            cum = float(s.get("cum_return_pct") or 0)
            r["_capital_initial"] = 20000
            r["_capital_final"] = round(20000 * (1 + cum / 100), 2)
        bl = r.get("_baseline_result") if isinstance(r, dict) else None
        if bl:
            bl_s = bl.get("summary") or {}
            bl_cum = float(bl_s.get("cum_return_pct") or 0)
            bl["_capital_initial"] = 20000
            bl["_capital_final"] = round(20000 * (1 + bl_cum / 100), 2)
        with _BT_RUN_LOCK:
            _bt_put_fields(run_id,
                status="done", result=r, progress="完成", finished_at=time.time())
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id].update({
                    "status": "done", "result": r, "progress": "完成",
                    "finished_at": time.time(),
                })
        _bt_lock_release()  # R51: 释放全局锁
        # R-ship-2026-07-19: 落历史 meta (cache_db.bt_runs, 永久保留)
        try:
            from .. import cache_db as _cdb
            finished_at = time.time()
            started_at  = float(r.get("__started_at") or (finished_at - (r.get("took_sec") or 0)))
            summary = r.get("summary") or {}
            _cdb.upsert_bt_run(
                run_id=run_id,
                ts_started=started_at,
                ts_finished=finished_at,
                strategy_id=strategy_id,
                periods=list(period_keys or []),
                params={
                    "hold_days": hold_days, "top_n": top_n, "sample": sample,
                    "breadth_min": breadth_min, "breadth_min_soft": breadth_min_soft,
                    "sector_hot_topn": sector_hot_topn, "sector_inflow_topn": sector_inflow_topn,
                    "require_surge_label": require_surge_label,
                    "enable_actual_10": enable_actual_10,
                    "index_late_up": index_late_up,
                    "sector_late_up": sector_late_up,
                    "tail_vol_ratio_min": tail_vol_ratio_min,
                    "late_high_discount": late_high_discount,
                    "require_vwap_strict": require_vwap_strict,
                    "compare_to_baseline": compare_to_baseline,
                },
                trades_count=int(r.get("trades_count") or summary.get("trades") or 0),
                took_sec=float(r.get("took_sec") or 0),
                engine_version=str(r.get("engine_version") or ""),
                summary={
                    "trades": int(summary.get("trades") or 0),
                    "win_rate_pct": summary.get("win_rate_pct"),
                    "cum_return_pct": summary.get("cum_return_pct"),
                    "max_drawdown_pct": summary.get("max_drawdown_pct"),
                },
                status="done",
            )
        except Exception as _e:
            log.debug(f"[bt-meta] upsert 失败 {run_id}: {_e}")
    except Exception as e:
        log.exception(f"backtest {run_id} fail")
        cancelled = _bt_is_cancelled(run_id)
        with _BT_RUN_LOCK:
            _bt_put_fields(run_id,
                status="error",
                error=f"{type(e).__name__}: {str(e)[:200]}",
                progress="已取消" if cancelled else "出错",
                finished_at=time.time())
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id].update({
                    "status": "error",
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                    "progress": "已取消" if cancelled else "出错",
                    "finished_at": time.time(),
                })
        if cancelled:
            # R51: 跨进程 — cache_store TTL 600s 自动清, 本地 set 仅作 fast-path
            with _BT_RUN_LOCK:
                _BT_CANCELLED.discard(run_id)
        _bt_lock_release()  # R51: 释放全局锁


@app.post("/api/screener/backtest")
async def api_screener_backtest(req: _BacktestReq):
    """启动一次回测 (后台线程), 立即返 run_id"""
    period_keys = _bt_period_resolver(req.periods)
    run_id = f"bt-{int(time.time())}-{secrets.token_hex(3)}"
    # R51: 跨进程互斥锁 — set_nx 原子, 避免 _BT_RUNS 多 worker race
    if not _bt_lock_acquire(run_id, ttl=_BT_TIMEOUT_SEC):
        holder = _bt_lock_held() or "unknown"
        return envelope(error="已有回测在跑, 请先等待完成", data={"running": True, "holder": holder})
    with _BT_RUN_LOCK:
        _BT_RUNS[run_id] = {
            "status":      "running",
            "progress":    "排队中…",
            "periods":     period_keys,
            "started_at":  time.time(),
            "result":      None,
            "error":       None,
        }
    # R51: 同步写 cache_store (跨 worker 可见)
    _bt_init(run_id, periods=period_keys, started_at=time.time(), result=None, error=None)
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
            req.enable_actual_10,
            req.index_late_up,
            req.sector_late_up,
            float(req.tail_vol_ratio_min or 0),
            req.strategy_id,
            float(req.late_high_discount if 0.0 < req.late_high_discount <= 1.0 else 1.0),
            req.require_vwap_strict,
            req.compare_to_baseline,
            req.optimized_mode,
            req.optimized_params,
        )
    except Exception as e:
        with _BT_RUN_LOCK:
            if run_id in _BT_RUNS:
                _BT_RUNS[run_id]["status"] = "error"
                _BT_RUNS[run_id]["error"] = str(e)
                _BT_RUNS[run_id]["finished_at"] = time.time()  # R13: 立刻 GC,不靠 1h TTL
                _BT_RUNS[run_id]["progress"] = "提交失败"
        # R51: 同步写 cache_store + 释放锁
        _bt_put_fields(run_id, status="error", error=str(e),
                       finished_at=time.time(), progress="提交失败")
        _bt_lock_release()
        return envelope(error=f"提交失败: {e}")
    return envelope(data={"ok": True, "run_id": run_id, "periods": period_keys,
                          "hold_days": req.hold_days, "top_n": req.top_n, "sample": req.sample,
                          "breadth_min": req.breadth_min,
                          "breadth_min_soft": req.breadth_min_soft,
                          "sector_hot_topn": req.sector_hot_topn,
                          "sector_inflow_topn": req.sector_inflow_topn,
                          "require_surge_label": req.require_surge_label,
                          "enable_actual_10": req.enable_actual_10,
                          "index_late_up": req.index_late_up,
                          "sector_late_up": req.sector_late_up,
                          "tail_vol_ratio_min": req.tail_vol_ratio_min})


@app.get("/api/screener/backtest")
async def api_screener_backtest_status(run_id: str = ""):
    """查询回测状态 + 结果 (R51: 跨 worker 可见 — 走 cache_store)"""
    # R51: 优先 cache_store (跨 worker 可见), 本地 dict 仅 fast-path
    r = _bt_get(run_id)
    if not r:
        # R14: status=missing 时不设 error (ok=true, status="missing"),
        # 前端可读 status 判断,无需处理 envelope.error
        return envelope(data={"status": "missing", "result": None})
    started  = float(r.get("started_at")  or 0)
    finished = float(r.get("finished_at") or 0)
    out = {
        "status":      r.get("status"),
        "progress":    r.get("progress", ""),
        "periods":     r.get("periods", []),
        "started_at":  started,
        "finished_at": finished,
        "elapsed_sec": round((finished or time.time()) - started, 1) if started else 0,
        "error":       r.get("error"),
        "result":      r.get("result"),
    }
    return envelope(data=out)


# ─────────── R2: 1 小时 GC + R9: 5 分钟超时 (后台监控线程) ───────────
def _bt_gc_loop() -> None:
    """每 60s 巡检一次 BT runs (cache_store 后端, 跨 worker 共享):
       - 完成/失败 status 且 finished_at > 1h → 删 (本地 fast-path + cache_store)
       - running status 且 started_at > _BT_TIMEOUT_SEC → 标 timeout
    """
    while True:
        try:
            now = time.time()
            # R51: SCAN cache_store 拿所有活跃 run_id (跨 worker 可见)
            active_ids = _bt_active_run_ids()
            with _BT_RUN_LOCK:
                for rid in active_ids:
                    rec = _bt_get(rid) or {}
                    finished = float(rec.get("finished_at") or 0)
                    started  = float(rec.get("started_at")  or 0)
                    status   = rec.get("status")
                    if status in ("done", "error"):
                        if finished and (now - finished) > _BT_RUN_TTL_SEC:
                            log.debug(f"[bt-gc] rid={rid} 已过期, GC")
                            _bt_drop(rid)
                            _BT_RUNS.pop(rid, None)
                    elif status == "running":
                        if started and (now - started) > _BT_TIMEOUT_SEC:
                            err_msg = f"超时 (>{_BT_TIMEOUT_SEC}s) — 可能 actual_10 重算太长; 试试加 enable_actual_10=false 或缩 sample"
                            _bt_put_fields(rid, status="error", error=err_msg,
                                           progress="超时", finished_at=now)
                            if rid in _BT_RUNS:
                                _BT_RUNS[rid].update({
                                    "status": "error", "error": err_msg,
                                    "progress": "超时", "finished_at": now,
                                })
                            log.warning(f"[bt-gc] rid={rid} 超时终止")
                            _bt_lock_release()  # R51: 释放全局锁, 允许下次启动
        except Exception as e:
            log.debug(f"[bt-gc] err: {e}")
        time.sleep(60)


_bt_gc_thread = threading.Thread(target=_bt_gc_loop, daemon=True, name="bt-gc")
_bt_gc_thread.start()
log.info(f"[bt] GC + 超时监控线程已启动 (TTL={_BT_RUN_TTL_SEC}s, TIMEOUT={_BT_TIMEOUT_SEC}s)")


# ─────────── R4: SSE 推送进度 (替代 1.5s poll) ───────────
@app.get("/api/screener/backtest/stream")
async def api_screener_backtest_stream(run_id: str = ""):
    """SSE 推送 run_id 的状态变更 (idle → running → done/error). 客户端可继续轮询 /api/screener/backtest?run_id=... 取最终结果.
       R51: 跨 worker — 即使 POST 在 worker A,SSE 也可连 worker B 读到一致状态。"""
    import asyncio as _asyncio

    async def _gen():
        last_progress = None
        last_status   = None
        for _ in range(1200):  # 20 分钟上限
            rec = _bt_get(run_id)  # R51: 跨 worker 读
            if not rec:
                yield "event: missing\ndata: {}\n\n"
                return
            started  = float(rec.get("started_at") or 0)
            finished = float(rec.get("finished_at") or 0)
            cur = {
                "status":   rec.get("status"),
                "progress": rec.get("progress", ""),
                "progress_pct": int(rec.get("progress_pct") or 0),  # R-ship-2026-07-19: 真 % (阶段解析)
                "started_at":  started,
                "finished_at": finished,
                "elapsed_sec": round((finished or time.time()) - started, 1) if started else 0,
                "has_result": bool(rec.get("result")),
            }
            if cur["status"] != last_status or cur["progress"] != last_progress:
                import json as _json
                yield f"data: {_json.dumps(cur, ensure_ascii=False)}\n\n"
                last_status   = cur["status"]
                last_progress = cur["progress"]
            if cur["status"] in ("done", "error", "missing"):
                return
            await _asyncio.sleep(1.0)  # R23: 0.5s → 1s, 减少服务端 poll 次数

    from fastapi.responses import StreamingResponse
    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─────────── R97: 取消运行中的回测 ───────────
@app.post("/api/screener/backtest/cancel")
async def api_screener_backtest_cancel(run_id: str = ""):
    """标记 run_id 为 cancelled; _bt_run_bg 的 _cb 检查到后抛 KeyboardInterrupt.
       R51: 取消标记迁 cache_store (跨 worker 可见)"""
    if not run_id:
        return envelope(error="缺少 run_id")
    rec = _bt_get(run_id)  # R51: 跨 worker 读
    if not rec:
        return envelope(error="run_id 不存在")
    if rec.get("status") != "running":
        return envelope(error=f"状态 {rec.get('status')} 不可取消")
    _bt_mark_cancelled(run_id)  # R51: 写 cache_store (跨 worker 可见)
    _bt_put_fields(run_id, progress="正在取消…")
    with _BT_RUN_LOCK:
        _BT_CANCELLED.add(run_id)  # 本地 fast-path
        if run_id in _BT_RUNS:
            _BT_RUNS[run_id]["progress"] = "正在取消…"
    log.info(f"[bt-cancel] rid={run_id} 标记取消")
    return envelope(data={"ok": True, "run_id": run_id})


# ─────────── R-ship-2026-07-19: 历史回测列表 + meta 重看 ───────────
@app.get("/api/screener/backtest/runs")
async def api_screener_backtest_runs(limit: int = 20):
    """最近 N 条回测元数据 (永久保留, cache_db.bt_runs)."""
    try:
        from .. import cache_db as _cdb
        runs = _cdb.list_bt_runs(limit=limit)
    except Exception as e:
        log.exception("list_bt_runs")
        return envelope(error=str(e), data={"runs": []})
    return envelope(data={"runs": runs})


@app.get("/api/screener/backtest/meta")
async def api_screener_backtest_meta(run_id: str = ""):
    """单条 meta — 用于历史重看 (result 是否还在 cache_store 1h TTL 内)."""
    if not run_id:
        return envelope(error="缺少 run_id", data={"meta": None})
    try:
        from .. import cache_db as _cdb
        meta = _cdb.get_bt_meta(run_id)
    except Exception as e:
        return envelope(error=str(e), data={"meta": None})
    if not meta:
        return envelope(data={"meta": None, "available": False})
    # 检查 cache_store 内 result 是否还在 (1h TTL)
    rec = _bt_get(run_id)
    has_result = bool(rec and rec.get("result"))
    meta["available"] = has_result
    return envelope(data={"meta": meta})


@app.delete("/api/screener/backtest/runs")
async def api_screener_backtest_delete(run_id: str = ""):
    """删一条历史 meta (不影响 cache_store TTL)."""
    if not run_id:
        return envelope(error="缺少 run_id")
    try:
        from .. import cache_db as _cdb
        ok = _cdb.delete_bt_run(run_id)
    except Exception as e:
        return envelope(error=str(e))
    return envelope(data={"ok": ok, "run_id": run_id})


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
    code = _require_valid_code(code)
    _touch_recent(code)
    from . import error_stats as _es
    _es.record("/api/stock/{code}")

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
        # flow/seats/holders 走独立线程, return_exceptions=True 容忍个别失败
        _hist_results = await asyncio.wait_for(asyncio.gather(
            _with_timeout(flow_t, 8),
            _with_timeout(seats_t, 4),
            _with_timeout(holders_t, 8),
            return_exceptions=True,
        ), timeout=15)
        def _ok(v, default): return default if isinstance(v, BaseException) or v is None else v
        flow_h, seats_h, holders_h = _ok(_hist_results[0], None), _ok(_hist_results[1], None), _ok(_hist_results[2], None)
        quote, kline = hist_quote, hist_kline
        flow = flow_h
        seats = seats_h
        holders = holders_h
    else:
        # 2026-07-19 R301: 用 _gather_with_fallback 替代 asyncio.gather + 二重 await,
        # 避免 coroutine 重复 await 的 RuntimeError + 超时时不会取消已完成子任务
        _g_results = await _gather_with_fallback([
            (quote_t, 12), (flow_t, 10), (seats_t, 4), (kline_t, 6), (holders_t, 8),
        ], timeout_primary=12, timeout_secondary=6)
        quote, flow, seats, kline, holders = _g_results
        if any(r is None for r in _g_results):
            log.warning(f"stock/{code} 部分上游超时,尝试陈旧快照兜底")
            stale = _STOCK_LAST_OK.get(code)
            if all(r is None for r in _g_results) and stale and (time.time() - stale["ts"]) < 1800:
                age = int(time.time() - stale["ts"])
                log.info(f"stock/{code} 全部超时 → 返回陈旧快照 (age={age}s)")
                return envelope(data=stale["data"], meta={"stale_seconds": age, "refreshing": True})
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


# ════════════════════════════════════════════════════════════════════════════
# R21 (Batch 3): /api/stock/{code}/core — Critical 子集,1.5s 强超时,只返渲染必需字段
# 设计: 让首屏在 200ms 内拿到 quote + name + 5 KPI + kline (短),其余字段走 /full 后台拉
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/stock/{code}/core")
async def stock_core(request: _Request, code: str, date: str = Query("")):
    """Critical 子集 — quote + name + 5 KPI + 短 kline (30 天),1.5s 强超时。
    用来让首屏 200ms 内出价 + K 线,其余 (seats / sector / lu_ctx / strong_stocks /
    seat_breakdown / related_news / ai_status / intraday) 走 /full 后台异步 patch。
    """
    from .. import lib_common as _lc
    from .. import data_layer as _dl
    from . import error_stats as _es
    _es.record("/api/stock/{code}/core")

    code = _require_valid_code(code)
    _touch_recent(code)

    # R-opt-2026-07-19: L0 进程内缓存 (30s) — 优先于 Redis, 同一 worker 重复点击秒返
    _core_cache_key = ("core", code)
    _core_cached = _cache_core.get(_core_cache_key)
    if _core_cached:
        _core_cached["_cache_hit"] = True
        return json_etag_response(request, envelope(data=_core_cached), max_age=5)

    # L1: Redis 30s 缓存 — 配合启动预热 + 持续保活, 跨 worker 共享
    cache_key = cache_store.K.STOCK_FULL.format(code=code) + ":core"
    cached = _store_get(cache_key, ttl=30)
    if cached:
        _cache_core.set(_core_cache_key, cached)
        cached["_cache_hit"] = True
        return json_etag_response(request, envelope(data=cached), max_age=5)

    # 只跑 quote + kline (30)
    async def _fetch_quote():
        q = _cache_quote.get(("quote", code))
        if q is not None:
            return q
        q = await to_thread(_lc.fetch_realtime, code)
        if q: _cache_quote.set(("quote", code), q)
        return q
    quote_t = _fetch_quote()
    kline_t = to_thread(stock_kline_loader, code, 30)

    async def _wt(coro, sec):
        try:
            return await asyncio.wait_for(coro, timeout=sec)
        except Exception:
            return None

    results = await asyncio.wait_for(
        asyncio.gather(_wt(quote_t, 1.2), _wt(kline_t, 1.2)),
        timeout=1.5,
    )
    quote = results[0] or {}
    kline = results[1] or []

    # name fallback
    if not quote.get("name") or (isinstance(quote.get("name"), str) and quote["name"].isdigit()):
        try:
            for c, n in _dl.fetch_stock_list_all() or []:
                if c == code:
                    quote["name"] = n
                    break
        except Exception:
            pass
    if not quote.get("name"):
        quote["name"] = code

    quote = _normalize_quote(quote)
    is_partial = quote.get("最新价") is None or quote.get("最新价") == 0

    out = {
        "code": code,
        "quote": quote,
        "kline": kline,
        "ts": time.time(),
        "_partial": is_partial,
    }

    if not is_partial:
        _stale_save(f"stock_core:{code}", out)
        _store_set(cache_key, out, ttl=30)
        _cache_core.set(_core_cache_key, out)
    else:
        # 部分数据或全部失败 → 试陈旧缓存
        stale_data, age = _stale_load(f"stock_core:{code}", max_age=_STALE_TTL["stock_core"])
        if stale_data is not None:
            stale_data["_degraded"] = "stale"
            stale_data["_stale_ts"] = time.time() - age
            stale_data["_partial"] = True
            return json_etag_response(request, envelope(data=stale_data), max_age=5)
        out["_degraded"] = "data_unavailable"

    asyncio.create_task(_warm_full_for_core(code))
    return json_etag_response(request, envelope(data=out), max_age=5)


async def _warm_full_for_core(code: str):
    # Pre-warm /full so Phase 2 parallel call hits singleflight cache
    try:
        await asyncio.sleep(0.05)
        await _singleflight_stock_full(code, "")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# R-fix-2026-07-18 A1+A2: /api/stock/{code}/full — 单端点预聚合个股页全部数据
# 设计目标:
#   - 一次 fetch 拿全 quote + kline + fund_flow + seats + sector + limit_up_ctx +
#     strong_stocks + seat_breakdown + related_news + ai_status
#   - 服务端 asyncio.gather 并行,per-task timeout,部分失败容忍
#   - Redis 5s 缓存 (K.STOCK_FULL:{code}) — 行情 5s 内复用,跨进程共享
#   - Single-flight 合并并发拉取,避免同一 code 在 5s 窗口内被 N 个请求各打一次
#   - ai_status 只读不触发 LLM (LLM fire-and-forget 走 A5 单独处理)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/stock/{code}/full")
async def stock_full(request: _Request, code: str, fresh: int = Query(0, ge=0, le=1), date: str = Query("")):
    """个股页单端点预聚合 (5s 缓存)。

    返回结构 (与 stock_overview data 字段对齐 + 扩展):
      {
        code, name,
        quote, kline, fund_flow, seats, holders,        # 与 stock_overview 相同
        sector,                                           # /api/stock/{code}/sector
        limit_up_ctx,                                     # /api/stock/{code}/limit_up_context
        strong_stocks,                                    # /api/stock/{code}/strong_stocks
        seat_breakdown,                                   # /api/stock/{code}/seat_breakdown (8 类席位 + 标签)
        related_news,                                     # /api/stock/{code}/related_news
        ai_status,                                        # {ready, cached, model, ts, verdict, summary}
        intraday,                                         # 今日分时 (凑齐首屏 4 张卡)
        extras, is_historical, snapshot_date,             # 与 stock_overview 相同
        _partial, _degraded_fields, _cache_hit, ts,
      }
    """
    code = _require_valid_code(code)
    _touch_recent(code)
    from . import error_stats as _es
    _es.record("/api/stock/{code}/full")

    cache_key = cache_store.K.STOCK_FULL.format(code=code)
    if not fresh:
        cached = _store_get(cache_key, ttl=5)
        if cached:
            cached["_cache_hit"] = True
            # R7 (Batch 1): Cache-Control max-age=5 + swr=60 — 客户端 (含 SW / 浏览器)
            # 5s 内直接用本地缓存,5-60s 内 stale 返 + 后台 revalidate
            return json_etag_response(request, envelope(data=cached), max_age=5)

    # Single-flight: 同一 code 5s 窗口内并发合并 (避免雪崩打 akshare)
    try:
        out = await _singleflight_stock_full(code, date)
    except Exception as _e:
        _es.record("/api/stock/{code}/full", error=True)
        raise
    out["_cache_hit"] = False

    # 写 Redis 5s
    _store_set(cache_key, out, ttl=5)
    # R7 (Batch 1): cold path 也带 Cache-Control + ETag — 客户端 5s 内复用,304 省带宽
    return json_etag_response(request, envelope(data=out), max_age=5)


def _singleflight_stock_full(code: str, date: str):
    """合并并发:同一 code 5s 内的多个请求共享同一份预聚合结果。

    用 SingleFlight (process-local),Redis 已经做了 5s 跨进程去重,
    这里主要是防 redis miss + N 个请求同时触发 _build_full 的开销。
    """
    key = ("stock_full", code)
    inflight = _STOCK_FULL_INFLIGHT.get(key)
    if inflight is not None and not inflight.done():
        return inflight
    fut = asyncio.ensure_future(_build_stock_full(code, date))
    _STOCK_FULL_INFLIGHT[key] = fut
    fut.add_done_callback(lambda f: _STOCK_FULL_INFLIGHT.pop(key, None))
    return fut


_STOCK_FULL_INFLIGHT: dict = {}


async def _build_stock_full(code: str, date: str) -> dict:
    """真实预聚合:asyncio.gather 并行 + per-task timeout + 部分失败容忍"""
    from . import sector_classify as _sc
    from . import news_lookup as _nl
    from . import limit_up_context as _lu_ctx
    from . import seat_classify as _seat_clf
    from .sector_taxonomy import classify_taxonomy as _classify_tax
    from .strategy_picker import compute_ma5_principles as _compute_ma5_principles
    import datetime as _dt

    target_date = (date or "").strip().replace("/", "-")
    today_yyyymmdd = _dt.date.today().strftime("%Y-%m-%d")
    is_historical = bool(target_date) and target_date < today_yyyymmdd

    # ─── 子任务定义 ────────────────────────────────────────────────────────────
    # 1) 主包 (quote + kline + flow + seats + holders) — 复用 stock_overview 的 _quote / 加载器
    from .. import lib_common as lc
    from .. import data_layer as dl

    @cached(_cache_quote, key_fn=lambda c: ("quote", c))
    def _quote(c):
        return lc.fetch_realtime(c)

    def _holders(c):
        from . import holder_lookup
        return holder_lookup.fetch_holder_info(c)

    # 历史快照模式: 主包按 stock_overview 同样逻辑 (kline → 构造伪 quote + 截断 seats)
    if is_historical:
        def _hist_snapshot(code_, cutoff_date):
            k = stock_kline_loader(code_, 250) or []
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
            ps = _quote_hist(code_, cutoff_date) or {}
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

        def _quote_hist(code_, cutoff_date):
            return _quote(code_) or {}  # fallback 实在拿不到就用实时

        def _hist_flow(code_, cutoff_date):
            try:
                f = fund_flow.get_combined(code_, 60) or {}
                today_v = (f.get("today") or {})
                if today_v:
                    today_d = str(today_v.get("date") or "")[:10]
                    if today_d > cutoff_date:
                        f["today"] = None
                hist = f.get("history") or []
                f["history"] = [r for r in hist if str(r.get("date") or "")[:10] <= cutoff_date]
                return f
            except Exception:
                return {"code": code_, "today": None, "history": []}

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
        quote_t    = None
        kline_t    = None
    else:
        quote_t   = to_thread(_quote, code)
        flow_t    = to_thread(fund_flow.get_combined, code, 60)
        seats_t   = to_thread(seat_lookup.get_stock_seats, code, 10)
        kline_t   = to_thread(stock_kline_loader, code, 120)
        holders_t = to_thread(_holders, code)
        snapshot_t = None

    # 2) sector / limit_up / strong / news / seat_breakdown / ai_status (并行预拉)
    sector_t  = to_thread(_sc.get_sector, code)
    lu_t      = to_thread(_lu_ctx.get_limit_up_context, code, None)  # 先不带 sector,获取原始
    strong_t  = to_thread(_fetch_strong_rows_global)  # 全局共享 (5min)
    seat_bd_t = to_thread(_seat_clf.build_breakdown, code)
    news_t    = to_thread(_nl.load_cache)
    ai_t      = to_thread(_get_ai_status, code, today_yyyymmdd)
    intraday_t = to_thread(_fetch_intraday_today, code)
    # 2026-08-01: 公司画像 (营业范围/主营构成/概念板块/行业地位) — 走 Redis 6h 缓存,IO 极小
    profile_t = to_thread(_fetch_profile_pack, code)

    # 3) per-task timeout handled by _gather_with_fallback
    # 4) 一次性 gather 所有任务 (主包 + 子包), 4s 强等 + 2s 弱等
    # 2026-07-19 R301: 用 _gather_with_fallback 避免 coroutine 重复 await + 超时不取消已完成任务
    _g_results = await _gather_with_fallback([
        (snapshot_t, 3) if snapshot_t else (quote_t, 2),
        (flow_t, 2),
        (seats_t, 1.5),
        (kline_t, 2) if kline_t else (snapshot_t, 3),
        (holders_t, 2),
        (sector_t, 1.5),
        (lu_t, 2),
        (strong_t, 1.5),
        (seat_bd_t, 1.5),
        (news_t, 1.5),
        (ai_t, 1),
        (intraday_t, 2),
        (profile_t, 2.5),  # 13) 公司画像 — 4 个 EM 接口,2.5s 软上限
    ], timeout_primary=4, timeout_secondary=2)
    if all(r is None for r in _g_results):
        log.warning(f"stock_full {code} 全部超时 → 部分降级")
        stale = _STOCK_LAST_OK.get(code)
        if stale and (time.time() - stale["ts"]) < 1800:
            age = int(time.time() - stale["ts"])
            out = dict(stale["data"])
            out.update({"_partial": True, "_degraded_fields": ["flow", "seats", "sector", "lu", "strong", "seat_bd", "news", "ai"], "_stale_seconds": age, "ts": time.time()})
            return out
        _g_results = (None,) * 13
    results = _g_results

    # 5) 解构结果 (主包)
    if is_historical and snapshot_t is not None:
        snap = results[0] if isinstance(results[0], tuple) else ({}, [])
        quote, kline = snap
        flow = results[1]
        seats = results[2]
        holders = results[3]
    else:
        quote = results[0]
        flow = results[1]
        seats = results[2]
        kline = results[3]
        holders = results[4]

    sector      = results[5]
    lu_ctx      = results[6]
    strong      = results[7]
    seat_bd     = results[8]
    news_raw    = results[9]
    ai_status   = results[10]
    intraday    = results[11]
    profile     = results[12] or {}

    # 6) news 过滤 (按 sector + code 命中)
    related_news = _filter_news_for_stock(news_raw, code, sector)

    # 7) 补 sectorName 给 lu_ctx (如子包未带 sector,基于 sector 字段补一次)
    sector_name = (sector or {}).get("sw") or (sector or {}).get("csrc") or (sector or {}).get("gics") or ""
    if lu_ctx and sector_name and not lu_ctx.get("sector_name_used"):
        # lu_ctx 已 get_limit_up_context(code, None),若需要 sector_name 增强,这里覆盖
        try:
            lu_ctx = _lu_ctx.get_limit_up_context(code, sector_name)
        except Exception:
            pass

    # 8) strong_stocks 包装 (与 /api/stock/{code}/strong_stocks 格式一致)
    if strong is not None and isinstance(strong, list):
        # 计算本股的 tax (在主线程外可能已 cache 在 _STRONG_TAX_CACHE)
        tax_l1, tax_l2, tax_l3, tax_l4 = "", sector_name, "", []
        tax_key = f"tax:{code}"
        tax_cached = _STRONG_TAX_CACHE.get(tax_key)
        if tax_cached and (time.time() - tax_cached.get("ts", 0)) < 3600:
            tax_l1, tax_l2, tax_l3, tax_l4 = tax_cached["l1"], tax_cached["l2"], tax_cached["l3"], tax_cached["l4"]
        else:
            try:
                tax = _classify_tax(code, sector_name or None) or {}
                tax_l1 = (tax.get("level1_cluster") or "").strip()
                tax_l2 = (tax.get("level2_sw") or sector_name or "").strip()
                tax_l3 = (tax.get("level3_chain") or "").strip()
                tax_l4 = list(tax.get("level4_subconcept") or [])
                _STRONG_TAX_CACHE[tax_key] = {"l1": tax_l1, "l2": tax_l2, "l3": tax_l3, "l4": tax_l4, "ts": time.time()}
            except Exception:
                pass
        strong_stocks = {
            "rows": strong,
            "count": len(strong),
            "code": code,
            "tax_l1": tax_l1,
            "tax_l2": tax_l2,
            "tax_l3": tax_l3,
            "tax_l4": tax_l4,
            "ts": time.time(),
        }
    else:
        strong_stocks = {"rows": [], "count": 0, "code": code, "tax_l1": "", "tax_l2": sector_name, "tax_l3": "", "tax_l4": []}

    # 9) 兜底默认值 (与 stock_overview 一致)
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
    sector = _ok(sector, {"code": code})
    lu_ctx = _ok(lu_ctx, {"code": code, "today": None, "recent_5d": [], "sector_today": [], "summary": ""})
    seat_bd = _ok(seat_bd, {"code": code, "rows": [], "categories": [], "intraday": {}, "risks": [], "signals": {"positive": [], "warning": []}, "tags": []})
    related_news = _ok(related_news, [])
    ai_status = _ok(ai_status, {"ready": False, "cached": False, "model": ai_client.default_model(), "ts": 0})
    intraday = _ok(intraday, {"code": code, "minutes": [], "date": today_yyyymmdd})

    # 10) name 字段修正
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
        quote["name"] = code

    # 11) 计算 extras (与 stock_overview 同样的逻辑)
    quote = _normalize_quote(quote)
    price = float(quote.get("最新价") or 0)
    high = float(quote.get("最高") or 0)
    low = float(quote.get("最低") or 0)
    open_p = float(quote.get("今开") or 0)
    prev_close = float(quote.get("昨收") or 0)
    amplitude = ((high - low) / prev_close * 100) if (high and low and prev_close) else 0

    kline5 = (kline or [])[-5:] if kline else []
    pct_5d = None
    pct_20d = None
    vol_5d_avg = None
    streak_history = []
    if kline5:
        closes_5 = [float(k.get("close") or 0) for k in kline5]
        if closes_5[0] and closes_5[-1]:
            pct_5d = round((closes_5[-1] / closes_5[0] - 1) * 100, 2)
        if kline:
            closes_all = [float(k.get("close") or 0) for k in kline]
            if len(closes_all) >= 20 and closes_all[-20] and closes_all[-1]:
                pct_20d = round((closes_all[-20] / closes_all[-1] - 1) * 100, 2)  # 修正:20天前→今天
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
                streak_history.append({"date": k.get("date"), "change_pct": round(chg, 2), "limit_pct": int(limit_pct * 100)})
            prev_c = cl

    # 12) 5日线5原则 #3-#5 状态
    ma5_principles = _compute_ma5_principles(kline or [])

    # 12) 5日线5原则 #3-#5 状态
    ma5_principles = _compute_ma5_principles(kline or [])

    is_kc = code.startswith(("300", "301", "688"))
    limit_pct = 0.20 if is_kc else 0.10
    limit_up_price = round(prev_close * (1 + limit_pct), 2) if prev_close else None
    limit_dn_price = round(prev_close * (1 - limit_pct), 2) if prev_close else None

    out = {
        "code": code,
        "name": quote.get("name") or code,
        "quote": quote or {},
        "fund_flow": flow,
        "seats": seats,
        "kline": kline or [],
        "holders": holders,
        "main_exit": None,
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
        "ma5_principles":    ma5_principles,
        "is_historical":   is_historical,
        "snapshot_date":   target_date or "",
        # ─── 新增子包 ────────────────────────────────────────────────────────
        "sector":          sector,
        "limit_up_ctx":    lu_ctx,
        "strong_stocks":   strong_stocks,
        "seat_breakdown":  seat_bd,
        "related_news":    related_news,
        "ai_status":       ai_status,
        "intraday":        intraday,
        # 2026-08-01: 公司画像 (营业范围/主营/概念/行业地位) — 走 Redis 6h
        "profile":         profile,
        # ─── 状态 ──────────────────────────────────────────────────────────────
        "_partial":        False,
        "_degraded_fields": [],
        "ts":              time.time(),
    }
    # 写陈旧兜底
    if not is_historical and quote:
        _STOCK_LAST_OK[code] = {"data": out, "ts": time.time()}
    return out


def _fetch_profile_pack(code: str) -> dict:
    """公司画像 4 件套 — 拉 EM 4 个接口后整合,Redis 6h 缓存。

    复用 _fetch 函数避免重复 IO,失败返回空 dict 不阻塞主路径。
    """
    cache_key = cache_store.K.STOCK_PROFILE.format(code=code)
    cached = _store_get(cache_key, ttl=21600)
    if cached:
        return cached
    try:
        from . import fundamentals as _fund
        profile = _fund._fetch_profile_em(code)
        biz_bd = _fund._fetch_business_breakdown_em(code)
        conc = _fund._fetch_concepts_em(code)
        result = {
            "code": code,
            "profile": profile,
            "biz_breakdown": biz_bd,
            "concepts_pack": conc,
            "profile_meta": {
                "concept_count": len(conc.get("concepts") or []),
                "precise_concept_count": sum(1 for c in (conc.get("concepts") or []) if c.get("is_precise")),
                "product_count": len(biz_bd.get("by_product") or []),
                "region_count": len(biz_bd.get("by_region") or []),
                "report_date": biz_bd.get("report_date") or "",
            },
            "ts": int(time.time()),
        }
        has_data = bool(profile.get("name") or biz_bd.get("by_product") or conc.get("concepts"))
        if has_data:
            _store_set(cache_key, result, ttl=21600)
        return result
    except Exception as e:
        log.debug(f"_fetch_profile_pack {code} fail: {e}")
        return {"code": code, "profile": {}, "biz_breakdown": {}, "concepts_pack": {}, "profile_meta": {}, "_degraded": "fetch_failed"}


def _fetch_intraday_today(code: str) -> dict:
    """今日分时 — 复用 _fetch_intraday_for_date 逻辑"""
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        minutes = _fetch_intraday_for_date(code, today) or []
        return {"code": code, "date": today, "minutes": minutes}
    except Exception:
        return {"code": code, "minutes": [], "date": ""}


def _filter_news_for_stock(news_cache, code, sector) -> list:
    """从 news_lookup 全量 cache 中过滤与本股相关的新闻 (与 stock_related_news 同步)。

    2026-08-01 收紧: 强命中 (code 命中 / 行业精确) 优先, 宽口径 (l1/l3) 仅作前 5 补位。
    """
    try:
        if not news_cache:
            return []
        sw = (sector or {}).get("sw") or ""
        # 强匹配
        sw_keys_strong = []
        if sw:
            sw_keys_strong.append(sw)
        sw_raw = ((sector or {}).get("sw_raw") or "")
        for seg in sw_raw.split("-")[:2]:
            seg = seg.strip()
            if seg and seg not in sw_keys_strong:
                sw_keys_strong.append(seg)
        csrc = ((sector or {}).get("csrc") or "").strip()
        if csrc and csrc not in sw_keys_strong:
            sw_keys_strong.append(csrc)
        # 弱匹配
        sw_keys_weak = []
        l1 = (((sector or {}).get("taxonomy") or {}).get("level1_cluster") or "").strip()
        if l1 and l1 not in sw_keys_strong:
            sw_keys_weak.append(l1)
        l3 = (((sector or {}).get("taxonomy") or {}).get("level3_chain") or "").strip()
        if l3 and l3 not in sw_keys_strong and l3 not in sw_keys_weak:
            sw_keys_weak.append(l3)
        for seg in sw_raw.split("-")[2:]:
            seg = seg.strip()
            if seg and seg not in sw_keys_strong and seg not in sw_keys_weak:
                sw_keys_weak.append(seg)
        # 名称兜底
        stock_name = ""
        try:
            from .fundamentals import _fetch_profile_em
            prof = _fetch_profile_em(code) or {}
            stock_name = (prof.get("name") or "").strip()
        except Exception:
            stock_name = ""
        news = news_cache.get("news") or []
        ai = news_cache.get("ai") or {}
        strong_hits = []
        weak_hits = []
        for n in news:
            a = ai.get(n.get("id"))
            a_stocks = (a or {}).get("stocks") or []
            a_sectors = (a or {}).get("sectors") or []
            hit_reasons = []
            hit_kind = None
            if code in a_stocks:
                hit_reasons.append(f"提及{stock_name or code}")
                hit_kind = "strong"
            if not hit_kind:
                for k in sw_keys_strong:
                    if k and k in a_sectors:
                        hit_reasons.append(f"行业={k}")
                        hit_kind = "strong"
                        break
            if not hit_kind and stock_name and len(stock_name) >= 3:
                title = (n.get("title") or "").strip()
                content = (n.get("content") or n.get("summary") or "").strip()
                if stock_name in title or stock_name in content:
                    hit_reasons.append(f"标题/内容含{stock_name}")
                    hit_kind = "strong"
            if not hit_kind:
                for k in sw_keys_weak:
                    if k and k in a_sectors:
                        hit_reasons.append(f"宽口径={k}")
                        hit_kind = "weak"
                        break
            if not hit_kind:
                continue
            item = {**n, "ai": a, "hit_reason": " · ".join(hit_reasons)}
            item["_hit_kind"] = hit_kind
            (strong_hits if hit_kind == "strong" else weak_hits).append(item)
        strong_hits.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
        weak_hits.sort(key=lambda x: x.get("ctime") or 0, reverse=True)
        matched = strong_hits + weak_hits[: max(0, 5 - len(strong_hits))]
        # 0 命中 fallback — 最近 5 条要闻 (避免个股页空白)
        if not matched and news:
            for n in news[:5]:
                a = ai.get(n.get("id"))
                item = {**n, "hit_reason": "近期财经要闻", "_hit_kind": "fallback"}
                if a:
                    item["ai"] = a
                matched.append(item)
        return matched[:8]
    except Exception:
        return []


def _get_ai_status(code: str, trade_date: str) -> dict:
    """只读 AI 状态 — 不触发 LLM。

    返回:
      - ready:   bool (有缓存即可用)
      - cached:  bool (来自缓存)
      - model:   模型名
      - ts:      最后更新时间
      - verdict: AI verdict (有缓存时填)
      - summary: 摘要 (有缓存时填)
    """
    try:
        from . import cache_db as _cdb
        ai = _cdb.get_cached_ai(trade_date, code, ai_client.default_model()) or {}
        if ai and ai.get("verdict"):
            return {
                "ready": True,
                "cached": True,
                "model": ai_client.default_model(),
                "ts": int(ai.get("ts_updated") or 0),
                "verdict": ai.get("verdict") or "",
                "summary": ai.get("summary") or "",
                "conviction": ai.get("conviction") or 0,
            }
    except Exception as e:
        log.warning(f"ai_status read fail: {e}")
    return {"ready": False, "cached": False, "model": ai_client.default_model(), "ts": 0, "verdict": "", "summary": ""}


# ─── Redis K.STOCK_FULL helpers ────────────────────────────────────────────────

def _store_get(key: str, ttl: int = 5):
    """从 Redis 读缓存,带 ttl (秒) 新鲜度判断。失败返 None 不影响主路径。

    简化:Redis SET 时已带 ex=ttl,过期自动删除。我们只 get 后看是否还在,
    但 Redis 过期键直接 None → 自动透传到主路径再 build。
    """
    try:
        return cache_store.get_store().get(key)
    except Exception:
        return None


def _store_set(key: str, value, ttl: int = 5) -> bool:
    """写 Redis 缓存,失败静默。"""
    try:
        cache_store.get_store().set(key, value, ttl=ttl)
        return True
    except Exception:
        return False


def stock_kline_loader(code: str, days: int = 120) -> list[dict]:
    from .. import lib_common as lc
    # R-fix 2026-07-26: pre_cache 优先 + 内层 stale 兜底 — 上游数据源全挂时仍能保证 10 日涨跌格子 + K 线图有数据
    try:
        from .. import cache_db as _cdb_loader
        _pre = _cdb_loader.daily().get_kline_pre(code, days)
        if _pre is not None and len(_pre) >= 5:
            return _pre
    except Exception:
        pass
    @cached(_cache_kline, key_fn=lambda c, d: ("kline", c, d))
    def _load(code_, days_):
        df = lc.fetch_daily(code_, days=days_)
        if df is None or df.empty:
            # R-fix 2026-07-26: 数据源全挂 → 内层 stale 兜底,免得 10 日格子/K线双双空白
            try:
                from .. import cache_db as _cdb_inner
                stale_pre = _cdb_inner.daily().get_kline_pre(code_, days_)
                if stale_pre:
                    log.debug(f"stock_kline_loader {code_}/{days_} 上游空 → 用 stale pre_cache ({len(stale_pre)} 条)")
                    return stale_pre
            except Exception:
                pass
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


# ════════════════════════════════════════════════════════════════════════════════
# R-fix-2026-07-18 B1+B2: 个股 SSE 增量推送
#   - GET /api/stock/{code}/stream
#   每 1s 推 quote_patch (价格/chg/volume hash 变化时);每 2s 扫 ai_ready / SSE
#   长连接保持,前端 EventSource 订阅,_currentStockStream 切股时 close
# ════════════════════════════════════════════════════════════════════════════════
@app.get("/api/stock/{code}/stream")
async def stock_stream(code: str, request: _Request):
    """SSE: 个股页增量推送。
    事件类型:
      - quote_patch : quote hash 变化时推 (R38 剪裁到 ~10 字段, 1Hz)
      - ai_ready    : 后台 LLM 完成时推 (从 cache_store ai_ready:{code} 读)
      - intraday_tick : 分时新分钟 (启动时即刻推,后续每 60s 拉一次)
      - ping        : 心跳 (10s 无事时)

    R34 (Batch 4): 背压控制 — channel queue maxsize=128, 满了丢老的 (不阻塞 SSE 生成)
    R38 (Batch 4): payload diff — quote 30 字段剪到 10 个 SSE 必需字段
    """
    code = _require_valid_code(code)
    from sse_starlette.sse import EventSourceResponse

    # R38: SSE payload 剪裁 — 前端 _patchStockRealtime 只读这几个字段
    _SSE_QUOTE_FIELDS = (
        "最新价", "price", "涨跌额", "涨跌幅", "change_pct", "chg_amt",
        "成交量", "成交额", "换手率", "时间", "name",
    )

    def _trim_quote(q):
        if not q or not isinstance(q, dict): return q
        out = {}
        for k in _SSE_QUOTE_FIELDS:
            if k in q: out[k] = q[k]
        return out

    last_quote_hash = None
    last_ai_ts_seen = 0
    last_intraday_push = 0
    t_start = time.time()

    async def gen():
        nonlocal last_quote_hash, last_ai_ts_seen, last_intraday_push
        try:
            # 首帧: 立即推送当前 quote (让前端第一帧就能 patch 价格)
            # 三源: 1) _cache_quote (realtime_poller 写,TTL 5s)
            #       2) Redis K.QUOTE
            #       3) Redis K.STOCK_FULL:{code} 兜底,/full 已聚合 quote 5s 内必新鲜
            def _read_quote():
                q = _cache_quote.get(("quote", code)) if _cache_quote else None
                if q and q.get("最新价") is not None: return q
                try:
                    from .. import cache_store as _cs_sse2
                    q2 = _cs_sse2.get_store().get(_cs_sse2.K.QUOTE.format(code=code))
                    if q2 and isinstance(q2, dict) and q2.get("最新价") is not None: return q2
                    # 兜底: STOCK_FULL 已含 quote,/full 端点每 5s rebuild
                    full = _cs_sse2.get_store().get(_cs_sse2.K.STOCK_FULL.format(code=code))
                    if full and isinstance(full, dict):
                        fq = full.get("quote")
                        if fq and fq.get("最新价") is not None: return fq
                except Exception:
                    pass
                return None
            q_now = _read_quote()
            if q_now:
                yield {"event": "quote_patch", "data": json.dumps({
                    "code": code, "quote": _trim_quote(q_now), "ts": time.time()
                }, default=str)}
                last_quote_hash = hash((q_now.get("最新价"), q_now.get("涨跌额"), q_now.get("成交量")))
            yield {"event": "ready", "data": json.dumps({"code": code, "ts": time.time()})}
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
                pushed_any = False
                # ── 1) quote_patch (1Hz, 剪裁后 ~250B vs 之前 ~600B)
                try:
                    q_now = _read_quote()
                    if q_now:
                        h = hash((q_now.get("最新价"), q_now.get("涨跌额"), q_now.get("成交量")))
                        if h != last_quote_hash:
                            yield {"event": "quote_patch", "data": json.dumps({
                                "code": code, "quote": _trim_quote(q_now), "ts": time.time()
                            }, default=str)}
                            last_quote_hash = h
                            pushed_any = True
                except Exception:
                    pass
                # ── 2) ai_ready (cache_store key 监听)
                try:
                    from .. import cache_store as _cs_sse
                    ai_data = _cs_sse.get_store().get(f"tx3:ai_ready:{code}")
                    if ai_data and isinstance(ai_data, dict):
                        ai_ts = ai_data.get("ts", 0)
                        if ai_ts > last_ai_ts_seen and ai_ts > t_start - 5:
                            yield {"event": "ai_ready", "data": json.dumps({
                                "code": code,
                                "verdict": ai_data.get("verdict"),
                                "summary": ai_data.get("summary"),
                                "conviction": ai_data.get("conviction"),
                                "ts": ai_ts,
                            }, default=str)}
                            last_ai_ts_seen = ai_ts
                            pushed_any = True
                except Exception:
                    pass
                # ── 3) 心跳 (无事时每 10s 一次)
                if not pushed_any and (time.time() - last_intraday_push > 10):
                    yield {"event": "ping", "data": json.dumps({"ts": time.time()})}
                    last_intraday_push = time.time()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"[stock-stream] {code} gen err: {e}")

    return EventSourceResponse(gen(), ping=15)


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
    code = _require_valid_code(code)

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
            "sector_today": [], "summary": "查询超时", "_degraded": "timeout",
        })
    except Exception as e:
        log.warning(f"limit_up_context 失败: {e}")
        return envelope(error=f"查询失败: {e}", data={
            "code": code, "today": None, "recent_5d": [],
            "sector_today": [], "summary": "查询失败", "_degraded": "fetch_failed",
        })


@app.get("/api/stock/{code}/strong_stocks")
async def stock_strong_stocks(code: str, sector: str | None = None):
    """
    强势股清单 — 个股页面「连板 · 近期涨停 / 强势股」卡片用。

    数据源: akshare.stock_zt_pool_strong_em (全市场近 N 日多次涨停 / 60 日新高强势股,通常 50-60 只)
    返回:
      - rows:    全量强势股 [ {代码,名称,涨跌幅,最新价,涨停价,成交额,流通市值,总市值,
                                换手率,涨速,是否新高,量比,涨停统计,入选理由,所属行业} ]
      - tax_l2/l3/l4: 当前股的 L2/L3/L4 标签 (前端按此过滤匹配"同板块"强势股)
      - date_used: 实际查询日期 (今日或上一交易日)

    缓存策略 (2026-07-17 修复冷启动慢):
      - **rows 全局共享缓存** (不按 code): 全市场 60 只强势股每日变,5 分钟一份够用
      - **tax_l3/l4 按 code 缓存**: 单只股的板块分类变化慢,1 小时一份
      - 这避免每个 code 都触发 akshare.stock_zt_pool_strong_em() 重拉 (单次 1-5s)

    Threading 硬超时保护 sandbox DNS 劫持 hang
    """
    code = _require_valid_code(code)
    import datetime as _dt
    from .sector_taxonomy import classify_taxonomy as _classify_tax

    # 1) 全局 rows 缓存 (所有 code 共享,5 分钟)
    now = time.time()
    rows_cached = _STRONG_ROWS_CACHE.get("rows_global")
    date_used = today_compact = _dt.datetime.now().strftime("%Y%m%d")
    if rows_cached and (now - rows_cached.get("ts", 0)) < 300:
        rows = rows_cached["rows"]
        date_used = rows_cached.get("date_used", today_compact)
    else:
        # 冷启 → 拉一次 (4s 硬超时),5 分钟内所有 code 复用
        rows = _fetch_strong_rows_global()
        _STRONG_ROWS_CACHE["rows_global"] = {
            "rows": rows, "ts": now, "date_used": date_used
        }

    # 2) 按 code 缓存 tax (1 小时,板块分类变化慢)
    tax_cache_key = f"tax:{code}"
    tax_cached = _STRONG_TAX_CACHE.get(tax_cache_key)
    if tax_cached and (now - tax_cached.get("ts", 0)) < 3600:
        tax_l1, tax_l2, tax_l3, tax_l4 = tax_cached["l1"], tax_cached["l2"], tax_cached["l3"], tax_cached["l4"]
    else:
        try:
            tax = _classify_tax(code, sector or None) or {}
            tax_l1 = (tax.get("level1_cluster") or "").strip()
            tax_l2 = (tax.get("level2_sw") or sector or "").strip()
            tax_l3 = (tax.get("level3_chain") or "").strip()
            tax_l4 = list(tax.get("level4_subconcept") or [])
        except Exception:
            tax_l1 = tax_l2 = ""; tax_l3 = ""; tax_l4 = []
        _STRONG_TAX_CACHE[tax_cache_key] = {
            "l1": tax_l1, "l2": tax_l2, "l3": tax_l3, "l4": tax_l4, "ts": now
        }

    payload = {
        "rows": rows,
        "count": len(rows),
        "date_used": date_used,
        "code": code,
        "tax_l1": tax_l1,
        "tax_l2": tax_l2,
        "tax_l3": tax_l3,
        "tax_l4": tax_l4,
        "ts": now,
    }
    return envelope(data=payload)


def _fetch_strong_rows_global() -> list:
    """全局拉一次 akshare.stock_zt_pool_strong_em (5 天回退),返回标准化 rows。"""
    import datetime as _dt
    today_compact = _dt.datetime.now().strftime("%Y%m%d")
    rows: list = []
    date_used = today_compact

    def _query_ak(date_str: str):
        import akshare as ak
        df = ak.stock_zt_pool_strong_em(date=date_str)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            out.append({
                "code":     str(r.get("代码", "")).zfill(6),
                "name":     str(r.get("名称", "")),
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "price":    float(r.get("最新价", 0) or 0),
                "limit_price": float(r.get("涨停价", 0) or 0),
                "amount":   float(r.get("成交额", 0) or 0),
                "circ_mv":  float(r.get("流通市值", 0) or 0),
                "total_mv": float(r.get("总市值", 0) or 0),
                "turnover_pct": float(r.get("换手率", 0) or 0),
                "speed":    float(r.get("涨速", 0) or 0),
                "is_new_high": str(r.get("是否新高", "否")) in ("是", "True", "true", "1"),
                "vol_ratio": float(r.get("量比", 0) or 0),
                "zt_stats": str(r.get("涨停统计", "") or ""),
                "reason":   str(r.get("入选理由", "") or ""),
                "industry": str(r.get("所属行业", "") or ""),
            })
        return out

    for offset in range(0, 5):
        d = _dt.datetime.now() - _dt.timedelta(days=offset)
        d_str = d.strftime("%Y%m%d")
        if d.weekday() >= 5 and offset == 0:
            continue
        try:
            fut = _EXECUTOR.submit(_query_ak, d_str)
            rows = fut.result(timeout=4) or []
            if rows:
                date_used = d_str
                break
        except Exception as e:
            log.warning(f"strong_stocks akshare {d_str} 失败: {e}")
            rows = []
            continue
    return rows


# 进程内缓存 (2026-07-17 修复冷启动):
# rows_global 是全市场 60 只共享 (5min),按 code 切股的 tax 是 1h
_STRONG_ROWS_CACHE: dict = {}
_STRONG_TAX_CACHE: dict = {}


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

    code = _require_valid_code(code)
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


# R-fix-2026-07-18 A5: 后台跑 AI verdict,不阻塞前端,完成后写 cache_db 让前端 SSE/snapshot 看到
async def _background_ai_task(code: str, date: str | None, run_id: str) -> None:
    """fire-and-forget LLM 任务,完成时 1) 写 cache_db  2) 推 SSE (B 阶段用)。

    失败 / 超时不报错 — 日志即可,主路径已经走 cache_db.get_cached_ai 兜底。
    """
    from .. import cache_db as _cdb
    from . import ai_client
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    today_str = date or datetime.datetime.now().strftime("%Y%m%d")
    t0 = time.time()
    try:
        log.info(f"[ai-bg] start {run_id} (code={code}, date={today_str})")
        # 检查缓存 — 已经存在就跳过
        hit = _cdb.get_cached_ai(today_str, code, ai_client.default_model())
        if hit and ai_client.is_valid_cached_verdict(hit):
            log.info(f"[ai-bg] skip {run_id} — already cached")
            return
        if not api_key:
            log.warning(f"[ai-bg] {run_id} skip — no MINIMAX_API_KEY")
            return
        # 复用 stock_ai_analysis 主路径 (内部有缓存 + 数据采集 + LLM)
        # 直接调 _call_minimax 一行就够 (避免再走缓存检查的双重 round-trip)
        # 组装 ctx
        from .. import lib_common as lc
        from . import limit_up_context as _limit_up_ctx
        from .sector_classify import get_sector as _get_sector
        def _q(): return lc.fetch_realtime(code)
        q_t = to_thread(_q)
        f_t = to_thread(fund_flow.get_combined, code, 60)
        s_t = to_thread(seat_lookup.get_stock_seats, code, 10)
        k_t = to_thread(stock_kline_loader, code, 60)
        def _lu(): return _limit_up_ctx.get_limit_up_context(code, sector_name=None)
        def _sc(): return _get_sector(code)
        lu_t = to_thread(_lu)
        sc_t = to_thread(_sc)
        async def _wt(coro, sec):
            try: return await asyncio.wait_for(coro, timeout=sec)
            except Exception: return None
        try:
            quote, flow, seats, kline, lu_, sector_ = await asyncio.wait_for(
                asyncio.gather(_wt(q_t, 4), _wt(f_t, 6), _wt(s_t, 4), _wt(k_t, 6), _wt(lu_t, 6), _wt(sc_t, 5)),
                timeout=14,
            )
        except asyncio.TimeoutError:
            log.warning(f"[ai-bg] {run_id} upstream timeout")
            return
        def _ok(v, default): return default if isinstance(v, BaseException) or v is None else v
        quote = _ok(quote, {})
        flow = _ok(flow, {"code": code, "today": None, "history": []})
        seats = _ok(seats, {"code": code, "rows": [], "blacklisted": False})
        kline = _ok(kline, [])
        lu_ = _ok(lu_, {"code": code, "today": None, "recent_5d": [], "sector_today": []})
        sector_ = _ok(sector_, {"code": code, "sw": None})
        if (not kline) and (not quote):
            log.warning(f"[ai-bg] {run_id} upstream empty")
            return
        ctx = {"quote": quote, "fund_flow": flow, "seats": seats, "kline": kline, "limit_up": lu_, "sector": sector_}
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_EXECUTOR, functools.partial(_call_minimax, api_key, code, ctx)),
                timeout=35,
            )
        except asyncio.TimeoutError:
            log.warning(f"[ai-bg] {run_id} LLM 35s timeout")
            return
        if not result:
            log.warning(f"[ai-bg] {run_id} LLM empty")
            return
        # 写 cache_db
        sector_name = (sector_ or {}).get("sw") or ""
        _cdb.upsert_ai(today_str, code, ai_client.default_model(), result, sector=sector_name)
        log.info(f"[ai-bg] {run_id} done in {time.time()-t0:.1f}s, verdict={result.get('verdict')}")
        # 通知前端 — 通过 Redis pub (B 阶段 SSE 订阅会用上)
        try:
            from .. import cache_store as _cs_pub
            _cs_pub.get_store().set(f"ai_ready:{code}", {"ts": time.time(), "verdict": result.get("verdict"), "summary": result.get("summary")[:100], "conviction": result.get("conviction")}, ttl=600)
        except Exception:
            pass
    except Exception as e:
        log.exception(f"[ai-bg] {run_id} fail: {e}")


@app.get("/api/stock/{code}/ai_analysis")
@app.post("/api/stock/{code}/ai_analysis")
async def stock_ai_analysis(code: str, date: str | None = Query(None, description="YYYYMMDD;空=今日"),
                            background: int = Query(0, description="1=fire-and-forget,立刻返,不阻塞前端")):
    """基于铁律的 AI 买入判断. 需配置 MINIMAX_API_KEY 环境变量.

    2026-07-11: 加 date 参数支持历史 verdict 回看。
    2026-07-18 A5: 加 background=1 — 前端 fire-and-forget 触发,后台任务跑 LLM 写 cache_db,
                  立刻返 {queued: True, run_id} 不阻塞前端。
    """
    code = _require_valid_code(code)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    # 函数内 import 会让名字变 local — 所有用到的 import 必须早于此函数第一行引用
    # 之前 ai_client.default_model() 在 line 7852 引用,但 from . import ai_client 在 7855,触发 UnboundLocalError
    from . import ai_client  # noqa

    # R-fix-2026-07-18 A5: background=1 → 后台 fire-and-forget 跑 LLM,立刻返
    # 防抖:同一只 5 分钟内最多 1 次,避免每次切页面都打 LLM
    if background:
        from .. import cache_store as _cs_ai
        lock_key = f"ai_bg_lock:{code}"
        got = _cs_ai.get_store().set_nx(lock_key, datetime.datetime.now().isoformat(), ttl=300)
        if not got:
            return envelope(data={"queued": False, "reason": "debounced", "code": code})
        run_id = f"{code}-{datetime.datetime.now().strftime('%H%M%S%f')}"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_background_ai_task(code, date, run_id))
        except RuntimeError:
            log.warning("[ai-bg] no running loop, sync fallback")
            try:
                await _background_ai_task(code, date, run_id)
            except Exception as e:
                log.warning(f"[ai-bg] sync fallback fail: {e}")
        return envelope(data={"queued": True, "run_id": run_id, "code": code, "eta_sec": 25})

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
    hit = _cdb.get_cached_ai(today_str, code, ai_client.default_model())
    if hit:
        # R4 缓存污染防护: schema 校验,不合法 → 当未命中重算
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
        _cdb.upsert_ai(today_str, code, ai_client.default_model(), result, sector=sector_name)
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


# ════════════════════════════════════════════════════════════════════════════════
# R-fix-2026-07-30: /api/stock/{code}/deep_analysis
#   深度分析: 公司业务范畴 + 业绩跳变 + 持仓盈亏 + 技术位置 + 同业 PE + AI 5 类动作建议
#   模式:
#     - background=1 (默认) : fire-and-forget 后台跑, 立刻返 queued/run_id
#     - background=0        : 同步, 总超时 8s (4 路并发 EM API)
#     - 命中缓存 (24h TTL)   : 立刻返 from_cache=True
# ════════════════════════════════════════════════════════════════════════════════

_DEEP_ANALYSIS_CACHE_PREFIX = "tuixue:stock:deep_analysis:v1"

# Cache single-flight 锁 — 防 5min 内同 code 重复触发 (跟 A5 模式对齐)
_deep_lock_key = lambda code: f"deep_bg_lock:{code}"

# 后台任务运行状态查询 — 写 cache_store, key=tuixue:deep_run:{run_id}:result
_DEEP_RUN_RESULT_PREFIX = "tuixue:deep_run:"


async def _do_deep_analysis(code: str, current_price: float | None = None) -> dict:
    """6 路并发的 deep-analysis 主函数 — 每个 fetcher 都有 try/except 兜底。"""
    from . import fundamentals as _fund
    from . import tech_position as _tech
    from . import holding_position as _hold

    async def _a(call, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, call, *args)

    fund_t = asyncio.create_task(_a(_fund.fetch_fundamentals, code))
    hold_t = asyncio.create_task(_a(_hold.get_holding_view, code, current_price or 0))
    # tech_position 需要 K 线 — 复用 stock_kline_loader
    from .server import stock_kline_loader  # self-ref, 但 stock_kline_loader 是 module-level 函数
    async def _tech_call():
        try:
            rows = stock_kline_loader(code, 250) or []
            return _tech.compute_tech_position(rows, current_price=current_price)
        except Exception as e:
            log.debug(f"tech_position {code} fail: {e}")
            return {"has_data": False, "trend_label": "无数据"}
    tech_t = asyncio.create_task(_tech_call())

    fund = await fund_t
    hold = await hold_t
    tech = await tech_t

    # 同行 PE 偏离 — fund 已包含 industry_sw,后续补 sect_pe_avg
    return {
        "code": code,
        "ts": int(time.time()),
        "fundamentals": fund,
        "holding": hold,
        "tech_position": tech,
        "from_cache": False,
    }


def _deep_default_no_action() -> dict:
    """LLM 不可用时的兜底 deep verdict — 默认 '继续持有' + 0 分。"""
    return {
        "verdict": "观望",
        "recommendation_action": "继续持有",
        "profit_taking_score": 50,
        "conviction": 0,
        "layer_pass": {},
        "rules_passed": [],
        "rules_failed": [],
        "key_risks": [],
        "summary": "AI 暂不可用, 仅基于业务/业绩/技术/持仓维度展示。",
        "holding_advice": {"stop_loss": "", "target_price": "", "horizon_days": 0, "rationale": ""},
        "ts_updated": time.time(),
    }


async def _deep_llm_verdict(code: str, data: dict, timeout: float = 20.0) -> dict:
    """基于 6 路数据调 LLM 做综合判定,失败回退 _deep_default_no_action。"""
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        return _deep_default_no_action()

    fund = data.get("fundamentals", {}) or {}
    hold = data.get("holding", {}) or {}
    tech = data.get("tech_position", {}) or {}
    profile = fund.get("profile", {}) or {}
    financials = fund.get("financials", []) or []
    jump = fund.get("earnings_jump", {}) or {}

    lines = [
        "你是 A 股深度分析助手。基于下方多维数据,给出该股的综合判定与操作建议。",
        "",
        "【公司概况】",
        f"  行业(SW): {profile.get('industry_sw', '-')}",
        f"  行业(CSRC): {profile.get('industry_csrc', '-')}",
        f"  员工: {profile.get('emp_num', '-')}",
        f"  业务摘要: {(profile.get('business_summary') or profile.get('business_scope') or '-')[:200]}",
    ]
    if financials:
        lines.append("【近几期业绩】")
        for f in financials[:4]:
            lines.append(
                f"  {f.get('period','-')} 营收 {f.get('revenue_yi','-')} 亿 "
                f"(YoY {f.get('revenue_yoy_pct','-')}%) "
                f"净利 {f.get('netprofit_yi','-')} 亿 "
                f"(YoY {f.get('netprofit_yoy_pct','-')}%) "
                f"ROE {f.get('roe_pct','-')}%"
            )
    if jump and jump.get("jump"):
        lines.append(f"【业绩跳变】⚠ {', '.join(jump.get('reasons', [])[:3])}")

    lines.append("【技术位置】")
    lines.append(
        f"  趋势: {tech.get('trend_label', '-')}  "
        f"60日位置: {tech.get('pct_position_60d', '-')}%  "
        f"52周位置: {tech.get('pct_position_252d', '-')}%  "
        f"60日高点回撤: {tech.get('pullback_from_60d_high_pct', '-')}%"
    )
    lines.append(
        f"  MA5乖离: {tech.get('bias_ma5', '-')}%  "
        f"MA20乖离: {tech.get('bias_ma20', '-')}%  "
        f"突破带: {'是' if tech.get('breakout_zone') else '否'}  "
        f"支撑带: {'是' if tech.get('support_zone') else '否'}"
    )

    lines.append("【持仓状态】")
    if hold.get("has_position"):
        lines.append(
            f"  成本 ¥{hold.get('avg_cost', '-')}  现价 ¥{hold.get('last_price', '-')}  "
            f"浮盈 {hold.get('unrealized_pnl_pct', '-')}%  "
            f"持有 {hold.get('days_held', 0)} 天"
        )
    else:
        lines.append("  无持仓")

    lines.append("")
    lines.append("【输出要求】严格输出 JSON,不要 markdown 围栏:")
    lines.append('{"recommendation_action":"加仓|继续持有|减仓|清仓|观望",')
    lines.append(' "profit_taking_score":0-100,')
    lines.append(' "conviction":0-100,')
    lines.append(' "summary":"≤120字综合判断,包含核心逻辑与风险点",')
    lines.append(' "key_risks":["风险1","风险2"],')
    lines.append(' "holding_advice":{"stop_loss":"如 12.50","target_price":"如 18.00","horizon_days":30,"rationale":"≤40字"}}')

    user_content = "\n".join(lines)
    user_content_safe = ai_client.wrap_prompt("ctx", user_content)
    user_content_safe = ai_client.truncate_to_tokens(user_content_safe, max_tokens=800)

    spec = ai_client.CallSpec(
        url=ai_client.default_url(),
        headers=ai_client.headers(api_key),
        body={
            "model": ai_client.default_model(),
            "messages": [
                {"role": "system", "content": "你是 A 股深度分析助手。基于基本面/技术面/持仓数据给出综合操作建议。必须严格输出 JSON。"},
                {"role": "user", "content": user_content_safe},
            ],
            "temperature": 0.3,
        },
        name="deep_analysis",
        model=ai_client.default_model(),
        timeout=timeout,
        attempts=(1, 2),
        max_tokens_alts=(600, 1200),
    )
    try:
        _text, parsed, _info = await asyncio.get_event_loop().run_in_executor(
            _SCORING_EXECUTOR, ai_client.call, spec
        )
        if parsed and isinstance(parsed, dict):
            parsed["ts_updated"] = time.time()
            return parsed
    except Exception as e:
        log.warning(f"_deep_llm_verdict {code} LLM fail: {e}")

    return _deep_default_no_action()


async def _deep_analysis_task(code: str, run_id: str) -> None:
    """后台任务 — 跑 6 路并发 + 调 LLM + 写 cache。"""
    cache_key = f"{_DEEP_ANALYSIS_CACHE_PREFIX}:{code}"
    res_key = f"{_DEEP_RUN_RESULT_PREFIX}{run_id}:result"
    lock_key = _deep_lock_key(code)
    try:
        result = await asyncio.wait_for(_do_deep_analysis(code), timeout=20.0)
        # LLM 综合判定 (失败回退 _deep_default_no_action)
        try:
            verdict = await asyncio.wait_for(_deep_llm_verdict(code, result, timeout=18.0), timeout=20.0)
        except Exception as e:
            log.warning(f"_deep_analysis_task {code} LLM timeout/error: {e}")
            verdict = _deep_default_no_action()
        result.update(verdict)
        # 1) 写 run_id → result key (供前端轮询)
        try:
            cache_store.get_store().set(res_key, result, ttl=1800)
        except Exception:
            pass
        # 2) 写主缓存 (24h TTL — 跟 stock_ai_verdict 一致)
        try:
            cache_store.get_store().set(cache_key, result, ttl=86400)
        except Exception:
            pass
        # 3) 释放锁
        try:
            cache_store.get_store().delete(lock_key)
        except Exception:
            pass
        log.info(f"_deep_analysis_task {code} done, run_id={run_id}")
    except Exception as e:
        # 失败也要释放锁 + 写一个 degraded result 给前端轮询
        try:
            cache_store.get_store().set(res_key, {"ready": False, "run_id": run_id, "error": str(e)[:200]}, ttl=600)
        except Exception:
            pass
        try:
            cache_store.get_store().delete(lock_key)
        except Exception:
            pass
        log.warning(f"_deep_analysis_task {code} fetch fail: {e}")


@app.get("/api/stock/{code}/deep_analysis")
async def stock_deep_analysis(code: str, background: int = Query(1, description="1=fire-and-forget 后台跑,0=同步"),
                                refresh: int = Query(0, description="1=强制清缓存重跑")):
    """个股深度分析 — 业务 / 业绩 / 持仓 / 技术 / 同业 PE / AI 5 类动作建议。"""
    code = _require_valid_code(code)
    cache_key = f"{_DEEP_ANALYSIS_CACHE_PREFIX}:{code}"

    # 1) 强制刷新 → 先清缓存
    if refresh:
        try:
            cache_store.get_store().delete(cache_key)
        except Exception:
            pass

    # 2) 缓存命中 — 立刻返
    try:
        cached = cache_store.get_store().get(cache_key)
        if cached:
            if isinstance(cached, dict):
                return envelope(data={**cached, "from_cache": True})
            if isinstance(cached, (bytes, str)):
                import json as _jd
                parsed = _jd.loads(cached) if isinstance(cached, (bytes, bytearray)) else _jd.loads(str(cached))
                if isinstance(parsed, dict):
                    return envelope(data={**parsed, "from_cache": True})
    except Exception as e:
        log.debug(f"deep_analysis cache get fail: {e}")

    # 3) background=1 (默认) — fire-and-forget 后台跑
    if background:
        from .. import cache_store as _cs_bg
        lock_key = _deep_lock_key(code)
        got = _cs_bg.get_store().set_nx(lock_key, datetime.datetime.now().isoformat(), ttl=240)
        if not got:
            return envelope(data={"queued": False, "reason": "debounced", "code": code})
        run_id = f"deep-{code}-{datetime.datetime.now().strftime('%H%M%S%f')}"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_deep_analysis_task(code, run_id))
        except RuntimeError:
            log.warning("[deep-bg] no running loop, sync fallback")
            try:
                await _deep_analysis_task(code, run_id)
            except Exception as e:
                log.warning(f"[deep-bg] sync fallback fail: {e}")
        return envelope(data={"queued": True, "run_id": run_id, "code": code, "eta_sec": 8})

    # 4) background=0 — 同步路径, 25s 兜底 (数据 + LLM)
    try:
        result = await asyncio.wait_for(_do_deep_analysis(code), timeout=10.0)
        # LLM 综合判定 (失败回退 _deep_default_no_action)
        try:
            verdict = await asyncio.wait_for(_deep_llm_verdict(code, result, timeout=12.0), timeout=14.0)
        except Exception as e:
            log.warning(f"deep_analysis sync LLM fail {code}: {e}")
            verdict = _deep_default_no_action()
        result.update(verdict)
        # 同步结果也写缓存 — 但 24h TTL 太长, 改 30min (同步路径通常有实时诉求)
        try:
            cache_store.get_store().set(cache_key, result, ttl=1800)
        except Exception:
            pass
        return envelope(data=result)
    except asyncio.TimeoutError:
        return envelope(error="deep_analysis 超时", data={"_degraded": True, "from_cache": False})


@app.get("/api/stock/{code}/deep_analysis/result")
async def stock_deep_analysis_result(code: str, run_id: str = Query(...)):
    """查询后台 deep_analysis 任务结果 — 写到 cache_store 24h TTL。"""
    code = _require_valid_code(code)
    res_key = f"{_DEEP_RUN_RESULT_PREFIX}{run_id}:result"
    try:
        v = cache_store.get_store().get(res_key)
        if v:
            if isinstance(v, dict):
                return envelope(data=v)
            import json as _jd
            return envelope(data=_jd.loads(v) if isinstance(v, (bytes, bytearray)) else _jd.loads(str(v)))
    except Exception as e:
        log.debug(f"deep_analysis result get fail: {e}")
    # 也可检查 deep_analysis 缓存 (如果任务完成写入了)
    cache_key = f"{_DEEP_ANALYSIS_CACHE_PREFIX}:{code}"
    try:
        c = cache_store.get_store().get(cache_key)
        if c and isinstance(c, dict):
            return envelope(data={**c, "from_cache": True, "run_id": run_id})
    except Exception:
        pass
    return envelope(data={"ready": False, "run_id": run_id})


@app.get("/api/stock/{code}/ai_crash_risk")
async def stock_ai_crash_risk(code: str, force: bool = False):
    """量化砸盘风险检测 — 复用铁律, 同时跑盘面/席位/资金三路信号预扫描,
    把"机器能算的"全部算好再喂给 LLM, 让 LLM 只做最终综合判定。
    """
    code = _require_valid_code(code)
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()

    # SQLite 缓存 key: crash_risk:{date}:{code}
    from .. import cache_db as _cdb
    from . import ai_client
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    cache_key = f"crash_risk:{today_str}:{code}"

    if not force:
        hit = _cdb.get_cached_ai(today_str, code, f"{ai_client.default_model()}-crash")
        if hit:
            # R4 缓存污染防护
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
                _wt(_intraday_load(code), 8),
                return_exceptions=True,
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
        _cdb.upsert_ai(today_str, code, f"{ai_client.default_model()}-crash", result, sector=sector_name)
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
    code = _require_valid_code(code)
    from .. import cache_db as _cdb
    def _load():
        try:
            conn = _cdb._thread_conn()
            rows = conn.execute(
                "SELECT date, verdict, role, conviction, sector, ts_updated "
                "FROM ai_verdict WHERE code=? AND model=? "
                "ORDER BY date DESC LIMIT ?",
                (code, ai_client.default_model(), days),
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
    code = _require_valid_code(code)
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
    code = _require_valid_code(code)
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
        _cdb.safe_write(lambda conn: (
            conn.execute("DELETE FROM ai_verdict WHERE date=? AND code=? AND model=?",
                         (today_str, code, ai_client.default_model())),
            conn.commit(),
        ))
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
        # R11: 走 envelope 而非 HTTPException detail,前端可统一解析 {ok, error}
        return envelope(error="admin token required")
    # 硬超时 90s: 数据源 / LLM 在沙箱挂时不能拖死 server(2026-07-12 audit 发现)
    try:
        # R20: 用 _LONG_EXECUTOR 而非通用 to_thread (后者走 _EXECUTOR 20 worker,
        # 长任务会占满影响其他快端点)
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                _LONG_EXECUTOR,
                functools.partial(
                    run_backtest,
                    start=req.start, end=req.end,
                    top_n=req.top_n, hold_days=req.hold_days,
                    sell_mode=req.sell_mode, sample=req.sample,
                ),
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        log.warning(f"backtest 超时 90s ({req.start}→{req.end})")
        return envelope(error="回测超时 90s, 请缩小样本或重试", data={
            "trades": [], "stats": {"reason": "timeout"},
        })
    except Exception as e:  # R12: 任何异常都走 envelope, 避免 500 抛 FastAPI default
        log.exception(f"backtest 异常: {e}")
        return envelope(error=f"回测异常: {e}", data={
            "trades": [], "stats": {"reason": "exception"},
        })
    return envelope(data=result or {})


# ───────────────────────────────────────────────────────────
# 复盘系统 (2026-07-10)
# ───────────────────────────────────────────────────────────
# 模块通过顶部 _LazyModule 代理在首次请求时加载

# ───────────────────────────────────────────────────────────
# AI 对话框 (2026-07-10)
# ───────────────────────────────────────────────────────────
# 模块通过顶部 _LazyModule 代理在首次请求时加载

# ───────────────────────────────────────────────────────────
# 自选股池 + AI 建议 (2026-07-11)
# ───────────────────────────────────────────────────────────
# 模块通过顶部 _LazyModule 代理在首次请求时加载

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

        def _do(conn):
            n_rev = conn.execute("DELETE FROM trade_reviews").rowcount
            n_trd = conn.execute("DELETE FROM trades").rowcount
            conn.commit()
            return n_rev, n_trd

        n_rev, n_trd = _cdb.safe_write(_do)
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
    """列出全部自选股 + 实时行情 + 最新 AI 建议(同日有效)。
    R-T5x (2026-07-21): Redis 跨 worker 共享 8s 缓存 — bench 20 并发秒开
    """
    _WL_KEY = "tuixue:watchlist:v1"
    # 1) Redis 快速检查 (跨 4 worker 共享, < 5ms)
    # 注意: CacheStore 自带 JSON 编解码,存 dict 即可。曾传 json.dumps().encode()
    # 进去,取出来变成字符串 "b'{...}'" → json.loads 必抛 → 缓存永不命中,
    # 每次请求都全量重建 (P95 13s)。
    try:
        cached = cache_store.get_store().get(_WL_KEY)
        if isinstance(cached, dict) and cached.get("items") is not None:
            return envelope(data=cached)
    except Exception:
        pass
    # 1.5) TTL 刚过期时先返陈旧值 — 否则多 worker 同时重建
    try:
        stale = cache_store.get_store().get_swr(_WL_KEY)
        if isinstance(stale, dict) and stale.get("items") is not None:
            asyncio.create_task(_warm_watchlist_async(_WL_KEY))
            return envelope(data=stale)
    except Exception:
        pass
    try:
        items = await asyncio.to_thread(_watchlist.list_with_ai_snapshot)
        result = {"items": items, "count": len(items)}
        # 2) 写 Redis 60s — 加长避免 4 worker 频繁 race (实测 20s 时 30% 请求撞冷启 1s+)
        try:
            cache_store.get_store().set(_WL_KEY, result, ttl=60)
        except Exception:
            pass
        return envelope(data=result)
    except Exception as e:
        log.exception("watchlist list")
        return envelope(error=str(e), status_code=500)


async def _warm_watchlist_async(cache_key: str):
    """返陈旧值后台重建自选股快照,下次请求即命中新值。"""
    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(_watchlist.list_with_ai_snapshot), timeout=30)
        cache_store.get_store().set(cache_key, {"items": items, "count": len(items)}, ttl=60)
    except Exception as e:
        log.debug(f"watchlist 后台重建失败: {e}")


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
        # 失效 GET 缓存 → 用户加股立即在 sidebar 可见,不再等 60s
        try: cache_store.get_store().delete("tuixue:watchlist:v1")
        except Exception: pass
        return envelope(data={"item": row})
    except Exception as e:
        log.exception("watchlist add")
        return envelope(error=str(e), status_code=400)


@app.delete("/api/watchlist/{code}")
async def api_watchlist_remove(code: str):
    try:
        ok = _watchlist.remove(code)
        # 失效 GET 缓存 → 用户删股立即在 sidebar 可见
        try: cache_store.get_store().delete("tuixue:watchlist:v1")
        except Exception: pass
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
    code = _require_valid_code(code)
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
            _cdb.upsert_ai(trade_date, code, ai_client.default_model(), result, sector=sector_name)
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
    # R-T5x: 进程内没缓存时尝试 Redis 跨 worker 共享
    if not cached:
        try:
            rcache = cache_store.get_store().get(f"tuixue:dragons:{cache_key}:v1")
            if rcache:
                _decoded = _cache_obj(rcache)
                if _decoded is not None:
                    cached = {"data": _decoded, "ts": now}
                    _DRAGONS_CACHE[cache_key] = cached
        except Exception as e:
            log.debug(f"dragons redis get fail: {e}")
    # R50-SPEED: refresh=1 强制刷新也要快速 — 有缓存时先回陈旧+后台刷新,绝不阻塞
    if cached:
        age = (now - cached["ts"]).total_seconds()
        # 命中 fresh 窗口
        if not refresh and age < 180:
            return envelope(data=cached["data"])
        # 陈旧 (<10min) 或 refresh=1: 先秒回陈旧,后台刷新 — 用户永不空等 9s
        if age < 600 or refresh:
            stale_data = dict(cached["data"])
            stale_data["_degraded"] = "stale"
            stale_data["_stale_age_s"] = int(age)
            if _DRAGONS_INFLIGHT.get(cache_key):
                return envelope(data=stale_data, meta={"stale_seconds": int(age), "in_flight": True})
            async def _bg_refresh():
                try:
                    _DRAGONS_INFLIGHT[cache_key] = True
                    fresh = await asyncio.wait_for(to_thread(score_dragons, date), timeout=90)
                    if fresh:
                        _DRAGONS_CACHE[cache_key] = {"data": fresh, "ts": datetime.datetime.now()}
                        # R-T5x: 同步写 Redis
                        try:
                            cache_store.get_store().set(f"tuixue:dragons:{cache_key}:v1", fresh, ttl=180)
                        except Exception:
                            pass
                        log.info(f"dragons 后台刷新完成 (date={date})")
                except Exception as e:
                    log.debug(f"dragons 后台刷新失败: {e}")
                finally:
                    _DRAGONS_INFLIGHT[cache_key] = False
            asyncio.ensure_future(_bg_refresh())
            return envelope(data=stale_data, meta={"stale_seconds": int(age), "refreshing": True})
    try:
        result = await asyncio.wait_for(
            to_thread(score_dragons, date),
            timeout=90,                                       # 2026-07-29: 30→90 (东财限频 + tech fetch 81只并行偶发超 60s, 留 buffer)
        )
    except asyncio.TimeoutError:
        log.warning(f"dragons 超时 90s (date={date}) → 尝试陈旧缓存")
        stale = _DRAGONS_CACHE.get(cache_key)
        if stale and (datetime.datetime.now() - stale["ts"]).total_seconds() < 600:
            stale_data = dict(stale["data"])
            stale_data["_degraded"] = "stale"
            stale_data["_stale_age_s"] = int((datetime.datetime.now() - stale["ts"]).total_seconds())
            log.info(f"dragons 返回陈旧缓存 ({stale_data['_stale_age_s']}s)")
            return envelope(data=stale_data, meta={"stale_seconds": stale_data['_stale_age_s']})
        return envelope(error="龙头评分超时 30s,无陈旧缓存可用", data={
            "top10": [], "all": [], "mainline": [],
            "sentiment": {"label": "-", "zt_count": 0, "max_streak": 0, "streak_dist": {}},
            "stats": {"reason": "timeout"}, "_degraded": "dragons_timeout",
        })
    if result:
        _DRAGONS_CACHE[cache_key] = {"data": result, "ts": datetime.datetime.now()}
        # R-T5x (2026-07-21): 写 Redis 共享 (TTL=180s = fresh 窗口)
        try:
            cache_store.get_store().set(f"tuixue:dragons:{cache_key}:v1", result, ttl=180)
        except Exception as e:
            log.debug(f"dragons redis set fail: {e}")
        return envelope(data=result)
    return envelope(error="龙头评分返回空", data={"top10": [], "all": [], "mainline": [],
        "sentiment": {"label": "-", "zt_count": 0, "max_streak": 0, "streak_dist": {}},
        "stats": {"reason": "empty_result"}, "_degraded": "dragons_empty",
    })


_DRAGONS_CACHE: dict[str, dict] = {}
_DRAGONS_INFLIGHT: dict[str, bool] = {}
# R-T5x: 启动时尝试从 Redis 预热 in-process 缓存, 加速二次访问
def _warm_dragons_from_redis():
    try:
        store = cache_store.get_store()
        for d in ("today",):
            cached = store.get(f"tuixue:dragons:dragons_{d}:v1")
            if cached:
                _decoded = _cache_obj(cached)
                if _decoded is not None:
                    _DRAGONS_CACHE[f"dragons_{d}"] = {"data": _decoded, "ts": datetime.datetime.now()}
    except Exception as e:
        log.debug(f"dragons redis warm fail: {e}")


# ───────────────────────────────────────────────────────────
# 周线擒牛 - 5 大信号检测 (买点分析策略)
# ───────────────────────────────────────────────────────────
from . import weekly_bull as _weekly_bull

_WB_CACHE: dict = {}
_WB_INFLIGHT: bool = False
_WB_TTL_FRESH = 300   # 5min 内返原值
_WB_TTL_STALE = 600   # 10min 内返陈旧 + 后台刷新
_WB_KEY = "weekly_bull:v1"


@app.get("/api/weekly_bull")
async def api_weekly_bull(pattern: str = "", refresh: bool = False):
    """周线擒牛全市场扫描 (5 大买点信号)。

    ?pattern=KEY — 仅返回命中该 pattern 的股 (可选 sanxing_taodi/zhanwen_5w/...)
    ?refresh=1   — 强制后台重扫, 秒返陈旧
    """
    import time as _t
    now = _t.time()
    cached = _WB_CACHE.get(_WB_KEY)
    if cached:
        age = now - cached["ts"]
        if not refresh and age < _WB_TTL_FRESH:
            data = cached["data"]
        elif age < _WB_TTL_STALE or refresh:
            # 秒返陈旧 + 后台刷新
            if not _WB_INFLIGHT:
                async def _bg():
                    global _WB_INFLIGHT
                    _WB_INFLIGHT = True
                    try:
                        fresh = await asyncio.wait_for(
                            to_thread(_weekly_bull.scan_universe, None, 8),
                            timeout=25,
                        )
                        if fresh:
                            _WB_CACHE[_WB_KEY] = {"data": fresh, "ts": _t.time()}
                            log.info(f"[weekly_bull] 后台刷新完成, 命中 {fresh.get('matched_count', 0)}")
                    except asyncio.TimeoutError:
                        log.warning("[weekly_bull] 后台刷新超时 25s")
                    except Exception as e:
                        log.warning(f"[weekly_bull] 后台刷新失败: {e}")
                    finally:
                        _WB_INFLIGHT = False
                asyncio.ensure_future(_bg())
            data = cached["data"]
        else:
            data = cached["data"]
    else:
        # cold: 同步扫, 但用陈旧兜底
        try:
            data = await asyncio.wait_for(
                to_thread(_weekly_bull.scan_universe, None, 8),
                timeout=35,
            )
            _WB_CACHE[_WB_KEY] = {"data": data, "ts": now}
        except asyncio.TimeoutError:
            log.warning("[weekly_bull] cold scan 超时 35s — 用陈旧兜底")
            stale = _WB_CACHE.get(_WB_KEY, {}).get("data")
            if stale:
                stale["_stale"] = True
                return envelope(data=stale, meta={"_stale": True})
            return envelope(error="周线擒牛扫描超时 (无陈旧兜底)", data={
                "signals": [], "by_pattern": {}, "total_scanned": 0,
                "matched_count": 0, "took_ms": 0,
            })
        except Exception as e:
            log.warning(f"[weekly_bull] cold scan 失败: {type(e).__name__}: {e}")
            return envelope(error=f"周线擒牛扫描失败: {type(e).__name__}", data={
                "signals": [], "by_pattern": {}, "total_scanned": 0,
                "matched_count": 0, "took_ms": 0,
            })

    # 应用 pattern 过滤
    if pattern and pattern in _weekly_bull.PATTERNS:
        signals = [s for s in data.get("signals", []) if pattern in s.get("matched", [])]
        out = {
            "signals": signals,
            "by_pattern": {pattern: [s["code"] for s in signals]},
            "total_scanned": data.get("total_scanned", 0),
            "matched_count": len(signals),
            "ts": data.get("ts"),
            "took_ms": data.get("took_ms"),
            "_pattern_filter": pattern,
        }
    else:
        out = data

    meta = {}
    if cached:
        meta["cache_age_sec"] = int(now - cached["ts"])
        if cached.get("_inflight") or _WB_INFLIGHT:
            meta["refreshing"] = True
    return envelope(data=out, meta=meta or None)


@app.get("/api/stock/{code}/weekly_bull")
async def api_stock_weekly_bull(code: str):
    """个股周线擒牛单股分析 — 单股 cache 1h (用户多次访问/切股均命中)"""
    code = _require_valid_code(code)
    # L1 Redis 1h
    cache_key = cache_store.K.WEEKLY_BULL_ONE.format(code=code)
    cached = _store_get(cache_key, ttl=3600)
    if cached:
        return envelope(data=cached, meta={"_cache": "redis"})
    try:
        # 直接传 stock_kline_loader 进去避免 executor 内 import 失败
        result = await asyncio.wait_for(
            to_thread(_weekly_bull.analyze_one, code, stock_kline_loader),
            timeout=6,
        )
        # to_thread 失败返回 None
        if result is None:
            return envelope(error="周线擒牛分析超时", data={"code": code, "_skip": True, "_err": "to_thread None"})
        if not result.get("_skip"):
            _store_set(cache_key, result, ttl=3600)
        return envelope(data=result)
    except Exception as e:
        return envelope(error=f"周线擒牛单股失败: {e}", data={"code": code, "_skip": True, "_err": str(e)[:80]})


# ───────────────────────────────────────────────────────────
# 三分之一回升位 — 计算策略 (买点分析策略 #2)
# ───────────────────────────────────────────────────────────
from . import recovery_level as _recovery

# 单股 1h Redis 缓存 (复用 weekly_bull key 命名空间)
_RECOVERY_CACHE_KEY_TPL = "recovery_level:{code}"


@app.get("/api/stock/{code}/recovery_level")
async def api_stock_recovery_level(code: str):
    """个股 1/3 回升位分析 — 单股 cache 1h"""
    code = _require_valid_code(code)
    cache_key = _RECOVERY_CACHE_KEY_TPL.format(code=code)
    cached = _store_get(cache_key, ttl=3600)
    if cached:
        return envelope(data=cached, meta={"_cache": "redis"})
    try:
        result = await asyncio.wait_for(
            to_thread(_recovery.analyze_recovery, code, stock_kline_loader),
            timeout=6,
        )
        if result is None:
            return envelope(error="回升位分析超时", data={"code": code, "_skip": True, "_err": "to_thread None"})
        if not result.get("_skip"):
            _store_set(cache_key, result, ttl=3600)
        return envelope(data=result)
    except Exception as e:
        return envelope(error=f"回升位单股失败: {e}", data={"code": code, "_skip": True, "_err": str(e)[:80]})


# ───────────────────────────────────────────────────────────
# 个股策略匹配度 — 3 策略综合评分 (2026-07-19)
# ───────────────────────────────────────────────────────────

@app.get("/api/stock/{code}/strategy_match")
async def api_stock_strategy_match(code: str):
    """个股 3 策略综合匹配度评分 (0-100)。

    复用 strategy_picker.analyze_one + _score_signal,
    与策略选股器页面的评分体系一致。
    """
    code = _require_valid_code(code)
    cache_key = f"strategy_match:{code}"
    cached = _store_get(cache_key, ttl=3600)
    if cached:
        return envelope(data=cached, meta={"_cache": "redis"})
    try:
        from . import strategy_picker as _spicker
        result = await asyncio.wait_for(
            to_thread(_spicker.analyze_one, code, stock_kline_loader),
            timeout=12,
        )
        if result is None:
            log.warning(f"strategy_match {code} to_thread None (analyze_one 内部异常)")
            return envelope(error="策略分析内部错误", data={"code": code, "score": {"total": 0, "wb": 0, "rl": 0, "ma5": 0, "max": 100, "breakdown": []}, "matched_count": 0, "matched_keys": []})
        score = _spicker._score_signal(result)
        out = {
            "code": code,
            "score": score,
            "matched_keys": result.get("matched_keys", []),
            "matched_count": result.get("matched_count", 0),
            "wb": result.get("wb"),
            "rl": result.get("rl"),
            "ma5": result.get("ma5"),
            "ma5_principles": result.get("ma5_principles"),
        }
        _store_set(cache_key, out, ttl=3600)
        return envelope(data=out)
    except asyncio.TimeoutError:
        log.warning(f"strategy_match {code} 超时 12s")
        return envelope(error="策略匹配度分析超时", data={"code": code, "score": {"total": 0, "wb": 0, "rl": 0, "ma5": 0, "max": 100, "breakdown": []}, "matched_count": 0, "matched_keys": []})
    except Exception as e:
        log.exception(f"strategy_match {code}")
        return envelope(error=f"策略匹配度失败: {e}", data={"code": code, "score": {"total": 0, "wb": 0, "rl": 0, "ma5": 0, "max": 100, "breakdown": []}, "matched_count": 0, "matched_keys": []})


# ───────────────────────────────────────────────────────────
# 策略选股器 — 3 大策略全市场扫描 (2026-07-19)
# ───────────────────────────────────────────────────────────
from . import strategy_picker as _spicker

# 全市场扫描 Redis 10min cache
_SPICKER_KEY = "strategy_picker:v1"


@app.get("/api/strategies/scan")
async def api_strategies_scan(
    wb_min: int = 1,
    rl_near: bool = True,
    ma5: bool = True,
    mode: str = "and",
    min_matched: int = 1,
    refresh: bool = False,
):
    """3 大策略全市场扫描 — 周线擒牛 + 1/3 回升位 + 5日线放量

    Query:
      wb_min: 周线至少命中 N/5 (0-5, 默认 1)
      rl_near: 是否要求接近 1/3 回升位 (默认 true)
      ma5: 是否要求 5日线放量 (默认 true)
      mode: and (全满足, 默认) / or (任一)
      min_matched: 最少满足数 (1-3, 默认 1)
      refresh=1: 强制刷新
    """
    from . import error_stats as _es
    _es.record("/api/strategies/scan")

    # 1) L0 Redis 10min cache — hit 直接返
    cache_key = f"{_SPICKER_KEY}:{wb_min}:{int(rl_near)}:{int(ma5)}:{mode}:{min_matched}"
    if not refresh:
        cached = _store_get(cache_key, ttl=600)  # 10min
        if cached:
            cached["_cache_hit"] = True
            return envelope(data=cached, meta={"_cache": "redis"})

    # 2) cold scan (cap 150, 8 workers, kline 复用, ~15-30s)
    try:
        result = await asyncio.wait_for(
            to_thread(
                _spicker.scan_strategies,
                None,
                wb_min,
                rl_near,
                ma5,
                mode,
                min_matched,
                8,  # max_workers
            ),
            timeout=50,
        )
    except asyncio.TimeoutError:
        stale = _store_get(cache_key, ttl=86400)
        if stale:
            log.warning(f"策略选股 50s 超时 → 用 stale ({stale.get('ts', '?')})")
            stale["_stale"] = True
            return envelope(data=stale, meta={"_stale": True})
        return envelope(error="策略选股扫描超时 (无 stale 兜底)", data={"_skip": True})
    except Exception as e:
        return envelope(error=f"策略选股扫描失败: {e}", data={"_skip": True, "_err": str(e)[:120]})

    if result and not result.get("_skip"):
        _store_set(cache_key, result, ttl=600)
    return envelope(data=result or {"_skip": True})


@app.get("/api/strategies/text")
async def api_strategies_text():
    """心法页策略文字 — 5 大周线信号 + 1/3 回升位 完整文字说明, 给 laws 页用"""
    return envelope(data={
        "groups": [
            {
                "id": "weekly_bull",
                "name": "周线擒牛 · 5 大买点信号",
                "intro": "主力可以骗你一天两天,但很难骗你几周。周线里面藏的是大资金真正的动作。",
                "summary": "在周线级别, 用 5 个量化信号识别'主力在建仓 / 趋势转强 / 即将拉升'的临界点。命中任一信号都是值得复盘的候选标的。",
                "patterns": [
                    {
                        "id": "sanxing_taodi",
                        "name": "三星探底",
                        "short": "跌很久后低位 3 周小十字星 + 站稳 5W 均线",
                        "detail": "长期下跌后低位横盘,周线连续出现 3 根实体很小的十字星 (实体 / 收盘 < 3%),同时股价稳稳站在 5 周均线上方。空头力量衰竭,资金开始慢慢进货,主升前夜。",
                        "when": "适合做左侧布局, 信号出现后耐心等放量阳线确认再加重仓",
                    },
                    {
                        "id": "zhanwen_5w",
                        "name": "站稳 5 周线",
                        "short": "放量阳线突破 5W 均线 + 不再创新低",
                        "detail": "前面一直站不上 5 周均线,突然出现放量阳线,实体稳稳站在 5 周线上方,同时本周低点 ≥ 前 3 周最低点 (不再创新低)。趋势由弱转强的明确信号。",
                        "when": "波段行情的起爆点, 适合突破后回踩 5W 不破时介入",
                    },
                    {
                        "id": "tupo_pingtai",
                        "name": "突破震荡平台",
                        "short": "周收盘突破前 4-5 周高点",
                        "detail": "前面是跌了很久后开始横盘磨底,突然一周的收盘价突破前 4-5 周的高点,说明资金主动往上做。这是主升浪启动前最重要的信号之一。激进版: 突破前 3 周最高收盘价即可上车。",
                        "when": "主升浪启动信号, 突破后追涨胜率最高",
                    },
                    {
                        "id": "junxian_fangxiang",
                        "name": "均线方向 (5W + 20W 双线向上)",
                        "short": "5周 + 20周均线均向上拐头 + 楼梯排列 + 量能递增",
                        "detail": "只看 5W 金叉不够,要看 20W。当 5W 和 20W 同时向上拐头,均线呈楼梯状排列 (5W 在上, 20W 在下),且成交量温和放大,说明有资金持续进场 1-2 周以上。",
                        "when": "趋势确认信号, 一旦形成往往能走一段像样的趋势, 关键是要敢拿",
                    },
                    {
                        "id": "zhouxian_duiliang",
                        "name": "周线堆量",
                        "short": "连续 3 周以上成交量温和放大如小山",
                        "detail": "日线成交量容易做假,周线难。连续 3 周以上成交量温和放大, 看起来像座小山, 说明资金悄悄进场。此时若股价回调但量缩, 是洗盘特征, 是低风险介入机会。",
                        "when": "适合在缩量回踩关键均线不破时介入, 不要被日线洗出去",
                    },
                ],
            },
            {
                "id": "recovery_level",
                "name": "三分之一回升位 · 计算策略",
                "intro": "一个看起来很普通的计算方法,可以帮你判断一只票的重要支撑/压力位。",
                "summary": "找上一轮上涨的最低点 A 和最高点 B, 计算 (B-A)/3 + A 得到 1/3 回升位, 这是历史回踩中最容易企稳的关键支撑区。",
                "patterns": [
                    {
                        "id": "method",
                        "name": "1/3 回升位公式",
                        "short": "支撑位 = (B - A) / 3 + A",
                        "detail": (
                            "三步计算:\n"
                            "  第一步: 找到上一轮上涨行情的最低点, 记作 A\n"
                            "  第二步: 找到这一轮上涨的最高点, 记作 B\n"
                            "  第三步: 用 B 减 A, 得到的数值除以 3, 再加上 A\n"
                            "得到的结果, 就是重点关注的参考支撑区域。\n\n"
                            "示例: 最低点 A=7.9, 最高点 B=12.28, "
                            "(12.28-7.9)/3 + 7.9 = 1.46 + 7.9 = 9.36。\n"
                            "实际回踩到 9.36 附近后, 股价逐渐企稳并展开新一轮上涨。"
                        ),
                        "extension": (
                            "延伸: 同样算法可计算多个参考位\n"
                            "  1/3 位: A + (B-A)/3   ← 主支撑 (强势回调通常在这里止跌)\n"
                            "  1/2 位: A + (B-A)/2   ← 强弱分界 (跌破则趋势转弱)\n"
                            "  2/3 位: A + (B-A)*2/3 ← 偏强支撑 (回调到此已属强势)"
                        ),
                        "when": "回踩到 1/3 位附近 + 出现企稳 K 线 (十字星 / 锤子线 / 缩量) 是低风险介入窗口",
                    },
                ],
            },
        ],
        "version": "1.0",
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    })


# ─────────────────────────────────────────────────────────────
# 个股角色判定: 龙头 / 中军 / 杂毛 / 未分类
# ─────────────────────────────────────────────────────────────
_ROLE_CACHE_KEY = "stock_role:{code}"  # 24h Redis (板块龙头变化慢)

@app.get("/api/stock/{code}/role")
async def api_stock_role(code: str):
    """判定个股在所属板块里的角色 (龙头/中军/杂毛)。
    判定逻辑:
      - 龙头: 该板块最近 5 日涨停数 ≥ 3 OR 板块 Top1 by 涨幅 + 大资金
      - 中军: 板块内市值大 + 涨幅 ≥ 板块均 + 不在龙头
      - 杂毛: 其他
      - 未分类: 板块信息缺失
    缓存 24h (板块归属变化极慢)。
    """
    code = _require_valid_code(code)
    cached = _store_get(_ROLE_CACHE_KEY.format(code=code), ttl=86400)
    if cached:
        return envelope(data=cached, meta={"_cache": "redis"})

    try:
        from . import seat_lookup
        # 板块归属
        sector_name = ""
        try:
            from .sector_taxonomy import classify_sector_name as _csn
            sector_name = _csn("") or ""  # 简化: 用最近的 dragon 板块
        except Exception:
            pass
        # 复用 dragons 缓存里的 top10 + 板块信息
        from .server import _DRAGONS_CACHE  # type: ignore
        dragons = (_DRAGONS_CACHE.get("dragons_today") or {}).get("data", {})
        mainline = dragons.get("mainline") or []
        all_zt = dragons.get("all") or []
        # 找本股的板块
        my_sector = ""
        for z in all_zt:
            if z.get("code") == code:
                my_sector = z.get("sector", "")
                break
        if not my_sector:
            out = {"code": code, "role": "未分类", "sector": "",
                   "reason": "不在今日涨停池或非活跃标的",
                   "explanation": "未找到板块归属"}
            _store_set(_ROLE_CACHE_KEY.format(code=code), out, ttl=86400)
            return envelope(data=out)

        # 同板块股
        same_sector = [z for z in all_zt if (z.get("sector") or "").strip() == my_sector.strip()]
        same_sector.sort(key=lambda x: -float(x.get("score_total") or 0))
        # 龙头 = 同板块 Top1-2 by score, 或 Top1 by streak + 资金强
        role = "杂毛"
        reason = ""
        rank_in_sector = 0
        for i, z in enumerate(same_sector):
            if z.get("code") == code:
                rank_in_sector = i + 1
                break
        if rank_in_sector == 1:
            # Top1 by score
            top1 = same_sector[0]
            s_streak = int(top1.get("streak") or 1)
            s_funding_pts = (top1.get("score_breakdown") or {}).get("资金认可", {}).get("pts", 0)
            if s_streak >= 3 or s_funding_pts >= 20:
                role = "龙头"
                reason = f"{my_sector} 板块 Top1, 连板 {s_streak}, 资金 {s_funding_pts}/30"
            else:
                role = "龙头"
                reason = f"{my_sector} 板块 Top1 (评分 {top1.get('score_total')})"
        elif rank_in_sector <= 3 and rank_in_sector > 0:
            # Top2-3 → 中军或次龙头
            role = "中军"
            reason = f"{my_sector} 板块 Top{rank_in_sector} (评分 {same_sector[rank_in_sector-1].get('score_total')})"
        else:
            # 其他 = 杂毛
            role = "杂毛"
            reason = f"{my_sector} 板块 第 {rank_in_sector} 名 (评分 {same_sector[rank_in_sector-1].get('score_total') if same_sector else '?'})"

        out = {
            "code": code,
            "role": role,
            "sector": my_sector,
            "rank_in_sector": rank_in_sector,
            "sector_size": len(same_sector),
            "reason": reason,
            "explanation": (
                "龙头 = 板块内 Top1 by 综合评分, 主升浪代表" if role == "龙头" else
                "中军 = 板块内 Top2-3, 跟随龙头但市值/资金更稳" if role == "中军" else
                "杂毛 = 板块内第 4 名及之后, 容易被洗出, 慎参与"
            ),
        }
        _store_set(_ROLE_CACHE_KEY.format(code=code), out, ttl=86400)
        return envelope(data=out)
    except Exception as e:
        return envelope(data={"code": code, "role": "未分类", "sector": "",
                              "reason": str(e)[:80], "explanation": "板块角色判定失败"})



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



# ═════════════════════════════════════════════════════════════════
# 2026-07-20: 优化器 1000 轮迭代 API (start / state / stream / stop)
# 取代旧 10 次 grid, 用 web.backtest_optimizer.run_optimization 持续跑
# 状态全部 cache_store 持久化, 多次会话可续跑
# ═════════════════════════════════════════════════════════════════
import threading as _threading_opt

_OPTIM_RUNNING_LOCK = _threading_opt.Lock()
_OPTIM_STOP_FLAG = False
_OPTIM_LAST_RESULT = None


def _persist_optimizer_state(state_dict: dict) -> None:
    """保存到 cache_store (TTL 7 天, 跨进程共享)"""
    from .. import cache_store as _cs
    s = _cs.get_store()
    # _encode 内部已 json.dumps,直接传 dict 即可 (不要预 encode)
    s.set(_cs.K.OPTIMIZER_STATE, state_dict, ttl=86400 * 7)


def _persist_optimizer_best(best: dict) -> None:
    from .. import cache_store as _cs
    s = _cs.get_store()
    s.set(_cs.K.OPTIMIZER_BEST, best, ttl=86400 * 30)


def _persist_optimizer_stream(msg: str) -> None:
    """追加一行 progress 到 list (50 条 cap, 给前端 SSE 失败 fallback polling 用)"""
    from .. import cache_store as _cs
    s = _cs.get_store()
    try:
        key = _cs.K.OPTIMIZER_STREAM
        raw = s.get(key)
        lst = raw if isinstance(raw, list) else (json.loads(raw) if raw else [])
        if not isinstance(lst, list):
            lst = []
        lst.append({"t": time.time(), "msg": msg[:500]})
        lst = lst[-50:]
        s.set(key, lst, ttl=86400)
    except Exception:
        pass


def _optim_run_thread(max_iterations: int) -> None:
    """后台线程跑优化器, 状态/cache_store/SSE 三路推送"""
    global _OPTIM_STOP_FLAG, _OPTIM_LAST_RESULT
    from .. import cache_store as _cs
    from .backtest_optimizer import run_optimization as _run_opt

    _OPTIM_STOP_FLAG = False
    s = _cs.get_store()
    s.set(_cs.K.OPTIMIZER_LOCK, b"1", ttl=86400 * 7)

    # 初始化 state
    state_dict = {
        "status": "running",
        "iteration": 0,
        "max_iterations": max_iterations,
        "best_score": None,
        "best_params": None,
        "baseline_score": None,
        "baseline_cum": None,
        "baseline_wr": None,
        "history_summary": [],
        "started_at": time.time(),
        "last_update": time.time(),
    }
    _persist_optimizer_state(state_dict)
    _persist_optimizer_stream(f"[启动] max_iterations={max_iterations}")

    def _cb(msg: str) -> None:
        if _OPTIM_STOP_FLAG:
            raise KeyboardInterrupt("优化器停止")
        _persist_optimizer_stream(msg)
        # 解析 "[i/max] ..." 更新 iteration 计数 + ETA
        try:
            import re as _re2
            m2 = _re2.match(r"\s*\[(\d+)\s*/\s*(\d+)[^\]]*\]\s*(.*)", msg)
            if m2:
                state_dict["iteration"] = int(m2.group(1))
                state_dict["max_iterations"] = int(m2.group(2))
                state_dict["last_update"] = time.time()
                # ETA: 平均每 iter 耗时 = (now - started_at) / iteration
                if state_dict["iteration"] > 0:
                    elapsed = state_dict["last_update"] - state_dict["started_at"]
                    avg_per_iter = elapsed / state_dict["iteration"]
                    eta_s = max(0, (state_dict["max_iterations"] - state_dict["iteration"]) * avg_per_iter)
                    state_dict["eta_sec"] = int(eta_s)
            if "新最佳" in msg:
                # 解析 score=数字
                m_sc = _re2.search(r"score=([-\d.]+)", msg)
                if m_sc:
                    state_dict["best_score"] = float(m_sc.group(1))
                _persist_optimizer_state(state_dict)
            elif "✓" in msg or "新最佳" in msg or "异常" in msg:
                _persist_optimizer_state(state_dict)
        except Exception:
            pass

    try:
        opt_state = _run_opt(
            strategy_id="WIN_RATE_V2",
            period_keys=["半年"],
            max_iterations=max_iterations,
            progress_cb=_cb,
        )

        # 完成后写入 state
        history_summary = [{
            "iteration": h["iteration"],
            "score": h["score"],
            "cum_return": h["cum_return"],
            "win_rate": h["win_rate"],
            "delta_cum": h.get("delta_cum"),
            "delta_wr": h.get("delta_wr"),
            "trades": h["trades"],
        } for h in (opt_state.history[-100:] if hasattr(opt_state, "history") else [])]

        state_dict = {
            "status": "done",
            "iteration": opt_state.iteration,
            "max_iterations": opt_state.max_iterations,
            "best_score": opt_state.best_score,
            "best_params": dict(opt_state.best_params) if opt_state.best_params else None,
            "baseline_score": getattr(opt_state, "baseline_score", None),
            "baseline_cum": getattr(opt_state, "baseline_cum", None),
            "baseline_wr": getattr(opt_state, "baseline_wr", None),
            "history_summary": history_summary,
            "started_at": getattr(opt_state, "started_at", time.time()),
            "last_update": time.time(),
        }
        _persist_optimizer_state(state_dict)

        # best_params 写 OPTIMIZER_BEST (前端 ⭐ 优化策略按钮会用)
        if opt_state.best_result:
            best = {
                "params": dict(opt_state.best_params),
                "score": opt_state.best_score,
                "summary": opt_state.best_result.get("summary") or {},
                "scenario_trail_80": (opt_state.best_result.get("scenarios") or {}).get("trail_80") or {},
                "completed_at": time.time(),
                "iterations": opt_state.iteration,
            }
            _persist_optimizer_best(best)
            _OPTIM_LAST_RESULT = best
            _persist_optimizer_stream(f"[完成] {opt_state.iteration} 轮 · best_score={opt_state.best_score:.2f}")
    except KeyboardInterrupt:
        state_dict["status"] = "stopped"
        state_dict["last_update"] = time.time()
        _persist_optimizer_state(state_dict)
        _persist_optimizer_stream("[停止] 用户中断")
    except Exception as e:
        state_dict["status"] = "error"
        state_dict["error"] = str(e)[:500]
        state_dict["last_update"] = time.time()
        _persist_optimizer_state(state_dict)
        _persist_optimizer_stream(f"[错误] {e}")
    finally:
        s.delete(_cs.K.OPTIMIZER_LOCK)


@app.post("/api/optimize/start")
async def api_optimize_start(request: Request, max_iterations: int = 50):
    """启动优化器 (后台线程) — 默认 50 轮/批"""
    from .. import cache_store as _cs
    s = _cs.get_store()
    if s.get(_cs.K.OPTIMIZER_LOCK):
        return envelope(error="优化器已在运行", data={"status": "running"})
    t = _threading_opt.Thread(target=_optim_run_thread, args=(max_iterations,), daemon=True)
    t.start()
    return envelope(data={"status": "started", "max_iterations": max_iterations})


@app.post("/api/optimize/stop")
async def api_optimize_stop(request: Request):
    global _OPTIM_STOP_FLAG
    _OPTIM_STOP_FLAG = True
    return envelope(data={"status": "stopping"})


@app.get("/api/optimize/state")
async def api_optimize_state():
    """读当前优化器状态 + best_params (前端轮询/SSE fallback)"""
    from .. import cache_store as _cs
    s = _cs.get_store()
    raw = s.get(_cs.K.OPTIMIZER_STATE)
    if isinstance(raw, dict):
        state = raw
    elif raw:
        try:
            state = json.loads(raw)
        except Exception:
            state = {"status": "error", "raw": str(raw)[:200]}
    else:
        state = {"status": "idle"}
    best_raw = s.get(_cs.K.OPTIMIZER_BEST)
    if isinstance(best_raw, dict):
        best = best_raw
    elif best_raw:
        try:
            best = json.loads(best_raw)
        except Exception:
            best = None
    else:
        best = None
    return envelope(data={"state": state, "best": best})


@app.get("/api/optimize/stream")
async def api_optimize_stream(request: Request):
    """SSE 推送: optimizer_state 变化 + progress 消息"""
    if (r := _check_sse_origin(request)) is not None:
        return r
    from .. import cache_store as _cs
    from sse_starlette.sse import EventSourceResponse

    s = _cs.get_store()

    async def gen():
        last_state_raw = ""
        last_msg_count = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 1. state diff
                raw = s.get(_cs.K.OPTIMIZER_STATE)
                if raw and raw != last_state_raw:
                    last_state_raw = raw
                    state = json.loads(raw)
                    yield {"event": "state", "data": json.dumps(state, default=str)}
                    if state.get("status") == "done":
                        # 推一次 best 然后退出
                        best_raw = s.get(_cs.K.OPTIMIZER_BEST)
                        if best_raw:
                            yield {"event": "best", "data": best_raw.decode() if isinstance(best_raw, bytes) else best_raw}
                        break
                # 2. progress diff
                stream_raw = s.get(_cs.K.OPTIMIZER_STREAM)
                if stream_raw:
                    lst = json.loads(stream_raw)
                    if len(lst) > last_msg_count:
                        for m in lst[last_msg_count:]:
                            yield {"event": "progress", "data": json.dumps(m)}
                        last_msg_count = len(lst)
                yield {"event": "ping", "data": json.dumps({"t": time.time()})}
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            pass

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

    # 1) 进程内直暖 /core (热门股, 不经过 HTTP, 填 _cache_core + Redis)
    _hot_cores = ["000001","600519","000858","002594","300750","002747","000977","000524"]
    await asyncio.gather(*[_warm_core_local(code, pre_log) for code in _hot_cores], return_exceptions=True)

    # 2) HTTP 预热非 /core 大端点
    bind_host = os.environ.get("TUIXUE_PREHEAT_HOST", "127.0.0.1")
    bind_port = int(os.environ.get("TUIXUE_PREHEAT_PORT", "7799"))
    base = f"http://{bind_host}:{bind_port}"
    paths = [
        ("/api/market/overview", 8),
        ("/api/dragons",         20),
        ("/api/sectors/sw",      8),
        ("/api/weekly_bull",     25),
        ("/api/stock/002747/weekly_bull", 6),
        ("/api/laws",            5),
        ("/api/all_stocks/board?page_size=30", 20),
        ("/api/all_stocks/board?page_size=30&sort=change_pct", 18),
        ("/api/all_stocks/board?page_size=30&sort=turnover", 18),
        ("/api/all_stocks/board?page_size=30&sort=main_fund_inflow", 18),
    ]

    # 等 server 真起来了再发
    await asyncio.sleep(1.5)
    timeout = _httpx.Timeout(connect=3.0, read=25.0, write=10.0, pool=5.0)
    async with _httpx.AsyncClient(timeout=timeout, base_url=base) as client:
        async def _warm_one(path):
            t0 = asyncio.get_event_loop().time()
            try:
                r = await client.get(path)
                ok = r.status_code == 200
                mark = "✓" if ok else f"✗({r.status_code})"
                pre_log.info(f"[预热] {mark} {path} ({asyncio.get_event_loop().time()-t0:.2f}s)")
            except Exception as e:
                pre_log.warning(f"[预热失败] {path}: {type(e).__name__}: {e}")
        # 并发预热,不阻塞
        await asyncio.gather(*[_warm_one(p) for p, _ in paths], return_exceptions=True)

    pre_log.info("[启动预热] 完成 → 慢接口秒开")


async def _warm_core_local(code: str, warmer_log=None):
    """进程内直接暖 /core 缓存 (不经过 HTTP)。"""
    _log = warmer_log or log
    ck = ("core", code)
    # 30s 内已缓存 → 跳过
    if _cache_core.get(ck) is not None:
        return
    from .. import lib_common as _lc
    t0 = time.time()
    try:
        quote, kline = await asyncio.gather(
            to_thread(_lc.fetch_realtime, code),
            to_thread(stock_kline_loader, code, 30),
        )
        quote = _normalize_quote(quote or {})
        if not quote.get("name"):
            from .. import data_layer as _dl
            for c, n in (_dl.fetch_stock_list_all() or []):
                if c == code: quote["name"] = n; break
            if not quote.get("name"): quote["name"] = code
        out = {"code": code, "quote": quote, "kline": kline or [], "ts": time.time(), "_partial": quote == {}}
        _cache_core.set(ck, out)
        cache_key = cache_store.K.STOCK_FULL.format(code=code) + ":core"
        _store_set(cache_key, out, ttl=30)
        _log.info(f"[core-warm] ✓ {code} ({time.time()-t0:.1f}s)")
    except Exception as e:
        _log.info(f"[core-warm] ✗ {code}: {type(e).__name__}")


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
        ("/api/weekly_bull", 25),
        ("/api/market/overview", 8),
        ("/api/all_stocks/board?sort=amount&order=desc&page_size=30",            15),
        ("/api/all_stocks/board?sort=change_pct&order=desc&page_size=30",       15),
        ("/api/all_stocks/board?sort=turnover&order=desc&page_size=30",         15),
        ("/api/all_stocks/board?sort=main_fund_inflow&order=desc&page_size=30",  15),
    ]
    interval = 30.0  # R-opt: 30s 匹配 /core TTL, 热门股 Redis 永不过期
    await asyncio.sleep(10)  # 让启动预热先跑完
    # 热门 /core 股票列表 (进程内直暖, 不经过 HTTP)
    _hot_cores = ["000001","600519","000858","002594","300750"]
    timeout = _httpx_w.Timeout(connect=2.0, read=15.0, write=5.0, pool=3.0)
    async with _httpx_w.AsyncClient(timeout=timeout, base_url=base) as client:
        while True:
            try:
                # 进程内暖 /core (无网络开销,并行)
                await asyncio.gather(*[_warm_core_local(code, warmer_log) for code in _hot_cores], return_exceptions=True)
                # HTTP 路径: 非 /core 的大端点 (并行)
                async def _warm_http(path):
                    try:
                        t0 = time.time()
                        r = await client.get(path)
                        ok = r.status_code == 200
                        mark = "✓" if ok else f"✗({r.status_code})"
                        warmer_log.info(f"[保活] {mark} {path} ({time.time()-t0:.1f}s)")
                    except Exception as e:
                        warmer_log.info(f"[保活失败] {path}: {type(e).__name__}")
                await asyncio.gather(*[_warm_http(p) for p, _ in paths], return_exceptions=True)
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
    # R32 (2026-07-19): 8 worker leader election — 只有 leader 启动网络密集型后台任务
    # 用 cache_store.set_nx (Redis SET NX / SQLite fallback), TTL=60s 自动释放
    _is_leader = False
    try:
        from .. import cache_store as _cs
        _store = _cs.get_store()
        if _store.set_nx("tx3:leader:bg", os.getpid(), ttl=60):
            _is_leader = True
            log.info(f"[leader] PID {os.getpid()} 当选后台任务 leader")
        else:
            log.info(f"[leader] PID {os.getpid()} 跟随模式 (后台任务由 leader worker 运行)")
    except Exception:
        _is_leader = True  # 降级: 所有 worker 各自跑 (旧行为)

    if _is_leader:
        # 1) 实时抓取 poller
        try:
            from . import _realtime_poller
            _poller = _realtime_poller.RealtimePoller(
                _recent_codes_provider=_prune_recent,
                cache_quote=_cache_quote,
                ttl_seconds=15,
                max_codes_per_tick=200,
            )
            _poller.start()
            app.state._poller = _poller
            log.warning(f"[实时抓取] poller 已启动 (TTL {_poller.ttl_seconds}s, thread={_poller._thread.name if _poller._thread else 'NONE'})")
        except Exception as e:
            log.warning(f"[实时抓取] poller 启动失败: {e}")
            import traceback
            log.warning(traceback.format_exc())
        # 2) 数据预热
        if getattr(app, "_skip_preheat", False):
            log.info("[启动预热] 已跳过 (--no-preheat)")
        else:
            asyncio.create_task(_preheat_cache_on_startup())
        # P1-5 · tunnel 自愈
        # 2026-07-26: ngrok 现由 launchd com.kaikai.tuixue.ngrok (KeepAlive) 独占管理,
        # tunnel_keepalive.sh 只监视 + 写 tunnel_url.txt。in-server heal-loop 会跟它抢
        # 同一个文件 + 每 30s spawn start_tunnel_only.sh (14 路 fallback), 泄漏 ssh 隧道
        # (serveo/localhost.run) 并把 URL 覆盖成 serveo,导致手机链接反复失效。
        # 默认关闭; 只有显式 TUIXUE_INSERVER_TUNNEL_HEAL=1 才启用 (无 launchd 的裸机模式)。
        if os.environ.get("TUIXUE_INSERVER_TUNNEL_HEAL") == "1":
            app.state._tunnel_heal_task = asyncio.create_task(_tunnel_heal_loop())
            log.info("[tunnel-heal] 后台自愈 loop 已注册 (TUIXUE_INSERVER_TUNNEL_HEAL=1)")
        else:
            app.state._tunnel_heal_task = None
            log.info("[tunnel-heal] 跳过 (launchd 管理 ngrok, 设 TUIXUE_INSERVER_TUNNEL_HEAL=1 可启用裸机自愈)")
        # 2026-07-13: 选股 poller
        try:
            from . import screener as _scr
            asyncio.create_task(_scr.screener_poller_loop())
            log.warning("[选股] screener_poller_loop 已启动")
        except Exception as e:
            log.warning(f"[选股] screener_poller_loop 启动失败: {e}")
    else:
        app.state._poller = None

    # 所有 worker 都必须运行:
    # 3) R3: 后台 TTL 扫描线程 (60s 一次清理过期 + 记录统计)
    import threading as _t
    def _sweeper():
        while True:
            time.sleep(60.0)
            try:
                for c in (_cache_spot, _cache_quote, _cache_kline, _cache_fund,
                         _cache_overview, _cache_global, _cache_layer, _cache_core):
                    n = c.sweep_expired()
                    if n:
                        log.debug(f"[cache sweep] {c.__class__.__name__} cleared {n} expired")
            except Exception as e:
                log.debug(f"[cache sweep] error: {e}")
    _t.Thread(target=_sweeper, name="ttl-sweeper", daemon=True).start()
    # log.info("[R3] TTL 缓存后台扫描已启动 (60s 周期)")
    # 4) R8: 每日自动备份线程 (默认 03:00 本地时, 避开交易时段)
    def _daily_backup():
        import datetime as _dt
        ran_today = None
        while True:
            now = _dt.datetime.now()
            target = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if target <= now:
                target += _dt.timedelta(days=1)
            wait_sec = (target - now).total_seconds()
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
    # 2026-07-13 Round 10: 持续保活循环
    asyncio.create_task(_continuous_warmer())


def main():
    import uvicorn
    import argparse
    # 加载 ~/.hermes/env.sh (MINIMAX_API_KEY 等)
    _env_sh = Path.home() / ".hermes" / "env.sh"
    if _env_sh.exists():
        try:
            import subprocess
            # B-12: argv 传递路径,避免 $HOME 劫持导致 RCE (f-string 进 -c 是代码异味)
            r = subprocess.run(
                ["bash", "-c", "source \"$1\" && env -0", "_", str(_env_sh)],
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
        # hypercorn asyncio.serve() 不支持 workers 参数 (忽略 config.workers),
        # 导致 1 worker 串行处理请求. 远端隧道下 1 个慢请求堵住所有并发 → 127 个 503/502/404.
        # → 改用 uvicorn workers=4 (http/1.1, 但 ngrok 隧道用 HTTP/2 无意义)
        print(f"  · {runner_name} 不支持多 worker,回落 uvicorn ·  keep-alive 300s")
        use_h2 = False

    if not use_h2:
        # R51: workers=4 恢复 — _BT_RUNS 已迁 cache_store (Redis 共享), 跨 worker 状态一致
        # R102: workers=8 — 仪表盘 9+ 并行请求 + 慢端点(watchlist/hot_sectors)会占死 4 个 worker
        print(f"  · {runner_name} (HTTP/1.1) ·  8 workers ·  keep-alive 300s (R102 worker pool)")
        print()
        uvicorn.run(
            "tuixue_v3.web.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="warning",
            # 7 → 减少端口耗尽 (macOS ~16K 临时端口, 8 worker × 200 poller 占满)
            # R103: workers=4 — R102 的 workers=8 导致端口耗尽 "Can't assign requested address"
            workers=4,
            timeout_keep_alive=300,
        )
        return

    import asyncio
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except ImportError:
        pass
    cfg = HCConfig()
    cfg.bind = [f"{args.host}:{args.port}"]
    cfg.loglevel = "warning"
    cfg.keep_alive_timeout = 300
    cfg.h2 = True
    cfg.alpn_protocols = ["h2", "http/1.1"]
    cfg.workers = 1
    print(f"  ⚡ {runner_name} (HTTP/2 + h2 多路复用) · 1 worker · keep-alive 300s")
    print()
    asyncio.run(hc_serve(app, cfg))


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
    code = _require_valid_code(code)

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

    安全: codes 在拼接到 curl URL / Python -c 之前必须严格白名单 (数字 0-9),
    否则下方 helper 里的 f-string 会让 `'; import os; ...'` 直接注入到 akshare 子进程 → RCE。
    """
    raw = [c.strip() for c in codes.split(",") if c.strip()][:20]
    # 白名单: 只允许 1-6 位数字, 防止 URL / subprocess 注入
    code_list = [c.zfill(6) for c in raw if _re.fullmatch(r"\d{1,6}", c)][:20]
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

    防御性: 此函数假设 caller 已做白名单; 再做一次保险,防止下游直接调用时漏检。
    """
    out = []
    for raw in codes:
        # 二次白名单: 即便被直接调用也安全
        if not _re.fullmatch(r"\d{6}", raw):
            continue
        code = raw
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
