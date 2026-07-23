"""/api/projects 路由:CRUD + 详情。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("")
async def create_project(request: Request, body: dict):
    tool_id = body.get("tool_id")
    name = body.get("name") or "未命名"
    params = body.get("params") or {}
    if not tool_id:
        raise HTTPException(status_code=400, detail={"code": "BAD_REQUEST", "message": "tool_id required"})
    p = db.project_create(tool_id, name, params, meta=body.get("meta"))
    return with_trace(request, p)


@router.get("")
async def list_projects(request: Request, tool_id: Optional[str] = None, status: Optional[str] = None, limit: int = Query(50, le=200)):
    items = db.project_list(tool_id=tool_id, status=status, limit=limit)
    return with_trace(request, {"items": items, "count": len(items)})


@router.get("/{project_id}")
async def get_project(request: Request, project_id: str):
    p = db.project_get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": project_id})
    jobs = db.job_list_by_project(project_id)
    return with_trace(request, {"project": p, "jobs": jobs})