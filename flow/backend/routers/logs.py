"""/api/log:日志查看 + SSE 流(占位)。"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from ..envelope import with_trace

router = APIRouter(prefix="/api/log", tags=["logs"])


@router.get("/recent")
async def recent(request: Request, limit: int = 100):
    """最近日志:读取 access.log 末尾 N 行。"""
    from .. import _constants as C
    from pathlib import Path
    log_path = Path(C.ACCESS_LOG_PATH())
    lines: list[str] = []
    if log_path.exists():
        try:
            text = log_path.read_text(errors="ignore")
            all_lines = text.splitlines()[-limit:]
            lines = all_lines
        except Exception:
            pass
    return with_trace(request, {"lines": lines, "count": len(lines)})


async def _stream_generator():
    """SSE 心跳 + 行广播(简单实现:每 2s 读 access.log 末尾)。"""
    from .. import _constants as C
    from pathlib import Path
    log_path = Path(C.ACCESS_LOG_PATH())
    last_size = log_path.stat().st_size if log_path.exists() else 0
    while True:
        try:
            if log_path.exists():
                cur_size = log_path.stat().st_size
                if cur_size > last_size:
                    with open(log_path, "r", errors="ignore") as f:
                        f.seek(last_size)
                        new = f.read(cur_size - last_size)
                    for line in new.splitlines():
                        yield {"event": "message", "data": json.dumps({"line": line, "ts": int(time.time() * 1000)})}
                    last_size = cur_size
            yield {"event": "heartbeat", "data": "{}"}
        except Exception:
            yield {"event": "heartbeat", "data": "{}"}
        import asyncio
        await asyncio.sleep(2)


@router.get("/stream")
async def stream(request: Request):
    return EventSourceResponse(_stream_generator())