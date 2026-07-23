"""/api/tools:返回所有已注册 wrapper 的元数据,前端用于新建项目页。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..envelope import with_trace
from ..wrappers.registry import list_tools

router = APIRouter(prefix="/api", tags=["tools"])


@router.get("/tools")
async def tools_meta(request: Request):
    return with_trace(request, {"tools": list_tools(), "count": len(list_tools())})


@router.get("/tools/{tool_id}")
async def tool_detail(request: Request, tool_id: str):
    from ..wrappers.registry import get_wrapper
    try:
        w = get_wrapper(tool_id)
        return with_trace(request, {
            "tool_id": w.tool_id,
            "name": w.name,
            "description": w.description,
            "steps": w.steps,
        })
    except KeyError:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"code": "TOOL_NOT_FOUND", "message": tool_id})