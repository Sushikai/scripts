"""trace_id 中间件:每个请求自动注入 8 字节 hex,响应头带回。"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class TraceIdMiddleware(BaseHTTPMiddleware):
    """X-Trace-Id 注入 + 写入 request.state.trace_id。"""

    HEADER = "X-Trace-Id"

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.HEADER, "").strip()
        trace_id = incoming if len(incoming) >= 8 and incoming.replace("-", "").isalnum() else secrets.token_hex(4)
        request.state.trace_id = trace_id
        response: Response = await call_next(request)
        response.headers[self.HEADER] = trace_id
        return response