"""/api/accounts:账号健康(占位,下批实装)。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts")
async def list_accounts(request: Request):
    return with_trace(request, {"items": [], "count": 0})