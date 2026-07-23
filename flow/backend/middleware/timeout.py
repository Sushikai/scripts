"""慢请求 timeout 中间件:SSE/长任务白名单,其它按 endpoint 超时。"""

from __future__ import annotations

import asyncio
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import _constants as C

_logger = logging.getLogger("flow.timeout")


# 白名单:长任务/流式端点
_WHITELIST_PREFIXES = (
    "/api/job/",  # SSE 进度流
    "/api/backtest/",
    "/api/stream/",
)


def _is_whitelisted(path: str) -> bool:
    return any(path.startswith(p) for p in _WHITELIST_PREFIXES)


class TimeoutMiddleware(BaseHTTPMiddleware):
    """对非白名单 endpoint 套 asyncio.timeout,超时返 504。"""

    async def dispatch(self, request: Request, call_next):
        if _is_whitelisted(request.url.path):
            return await call_next(request)
        try:
            async with asyncio.timeout(C.TIMEOUT_DEFAULT()):
                return await call_next(request)
        except asyncio.TimeoutError:
            _logger.warning("request timeout %s", request.url.path)
            return JSONResponse(
                status_code=504,
                content={
                    "ok": False,
                    "data": None,
                    "error": {"code": "TIMEOUT", "message": f"request exceeded {C.TIMEOUT_DEFAULT()}s"},
                    "code": 1,
                    "trace_id": getattr(request.state, "trace_id", None),
                },
            )