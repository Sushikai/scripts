"""access_log 中间件:每行 JSON,带 trace_id + 延迟。"""

from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .. import _constants as C

_LOGGER_NAME = "flow.access"
_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger:
        return _logger
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        h = RotatingFileHandler(
            C.ACCESS_LOG_PATH(),
            maxBytes=C.ACCESS_LOG_MAX_BYTES,
            backupCount=C.ACCESS_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(h)
    _logger = logger
    return logger


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        try:
            response: Response = await call_next(request)
            status = response.status_code
        except Exception as exc:
            status = 500
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self._log(request, status, latency_ms, str(exc)[:120])
            raise
        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._log(request, status, latency_ms, None)
        return response

    def _log(self, request: Request, status: int, latency_ms: float, err: str | None) -> None:
        line = {
            "ts": int(time.time() * 1000),
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "trace_id": getattr(request.state, "trace_id", ""),
            "ip": request.client.host if request.client else "-",
            "ua": (request.headers.get("user-agent", "-") or "-")[:120],
        }
        if err:
            line["err"] = err
        try:
            _get_logger().info(json.dumps(line, ensure_ascii=False))
        except Exception:
            pass  # 永远不能因为日志挂