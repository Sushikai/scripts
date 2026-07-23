"""IP 维度令牌桶限频中间件。

简单内存版(per-process);生产环境可换 Redis。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import _constants as C

_logger = logging.getLogger("flow.rate_limit")


# 白名单不限频
_WHITELIST_PATHS = frozenset({"/health", "/api/health", "/"})


def _is_whitelisted(path: str) -> bool:
    return path in _WHITELIST_PATHS or path.startswith("/static/")


# 分级限频(前缀匹配)
_RULES = [
    ("/api/ai/", C.RATE_LIMIT_AI_PER_MIN),
    ("/api/jobs", C.RATE_LIMIT_JOB_CREATE_PER_MIN),
    ("/api/", C.RATE_LIMIT_DEFAULT_PER_MIN),
]


def _bucket_for(path: str) -> int:
    for prefix, limit in _RULES:
        if path.startswith(prefix):
            return limit
    return C.RATE_LIMIT_DEFAULT_PER_MIN


class _Limiter:
    def __init__(self):
        self._buckets: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, ip: str, path: str) -> tuple[bool, int]:
        """返回 (allow, retry_after_sec)。"""
        limit = _bucket_for(path)
        key = (ip, path.split("?")[0])
        now = time.time()
        with self._lock:
            q = self._buckets[key]
            # 清理过期窗口
            while q and q[0] < now - C.RATE_LIMIT_WINDOW_SEC:
                q.popleft()
            if len(q) >= limit:
                retry = int(C.RATE_LIMIT_WINDOW_SEC - (now - q[0])) + 1
                return False, retry
            q.append(now)
            return True, 0


_LIM = _Limiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_whitelisted(request.url.path):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        ok, retry = _LIM.allow(ip, request.url.path)
        if not ok:
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "data": None,
                    "error": {"code": "RATE_LIMITED", "message": f"too many requests, retry in {retry}s"},
                    "code": 1,
                    "trace_id": getattr(request.state, "trace_id", None),
                },
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)


def reset_for_tests() -> None:
    _LIM._buckets.clear()