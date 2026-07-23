"""envelope 协议:{ok, data, error, code, trace_id, ts}。

前端 api() 自动剥 envelope;失败统一处理。
"""

from __future__ import annotations

import time
from typing import Any, Optional


def ok(data: Any = None, **meta: Any) -> dict:
    """成功响应。data 字段是真正业务负载。"""
    return {
        "ok": True,
        "data": data,
        "error": None,
        "code": 0,
        "trace_id": meta.pop("trace_id", None),
        "ts": int(time.time() * 1000),
        **meta,
    }


def err(code: str, msg: str, *, status: int = 400, **meta: Any) -> tuple[int, dict]:
    """失败响应。返回 (status, body) 方便 raise HTTPException。"""
    body = {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": msg, **meta},
        "code": 1,
        "trace_id": meta.pop("trace_id", None),
        "ts": int(time.time() * 1000),
    }
    return status, body


def degraded(data: Any, reason: str) -> dict:
    """降级但不算失败:data 中带 _degraded=true 标记。"""
    if isinstance(data, dict):
        data["_degraded"] = True
        data["_degraded_reason"] = reason
    return ok(data)


def paginated(items: list, total: int, page: int, page_size: int, trace_id: str | None = None) -> dict:
    return ok(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": page * page_size < total,
        },
        trace_id=trace_id,
    )


def with_trace(request, data: Any, status: int | None = None, **meta: Any):
    """自动从 request.state.trace_id 取值,无需手动传。
    如果 status 给了,返回 JSONResponse(否则 dict 由 FastAPI 默认 200)。"""
    from fastapi.responses import JSONResponse
    tid = getattr(getattr(request, "state", None), "trace_id", None) if request else None
    body = ok(data, trace_id=tid, **meta)
    if status is not None and status != 200:
        return JSONResponse(status_code=status, content=body)
    return body


# === 错误码字典(供前端识别) ===
class Code:
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    DEGRADED = "DEGRADED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_ALREADY_DONE = "JOB_ALREADY_DONE"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    FILE_FORBIDDEN = "FILE_FORBIDDEN"