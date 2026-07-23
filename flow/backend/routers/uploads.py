"""/api/uploads:上传记录(占位,下批实装)。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["uploads"])


@router.get("/uploads")
async def list_uploads(request: Request):
    return with_trace(request, {"items": [], "count": 0})