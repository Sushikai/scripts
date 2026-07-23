"""/api/assets:素材库(下批实装,当前返空)。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["assets"])


@router.get("/assets")
async def list_assets(request: Request):
    """占位:返空列表,前端显示"暂无素材"。"""
    return with_trace(request, {"items": [], "count": 0})