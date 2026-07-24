"""/api/uploads:从 job artifacts 中聚合真实上传记录(fengge.upload / tiktok_story.upload_bili 等)。"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Query, Request

from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["uploads"])

UPLOAD_STEPS = {"upload", "upload_bili", "upload_douyin"}


@router.get("/uploads")
async def list_uploads(
    request: Request,
    platform: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    """聚合所有 project 的 upload step 产物 → 上传记录列表。"""
    items = []
    for proj in db.project_list(limit=200):
        for job in db.job_list_by_project(proj["id"]):
            if job.get("step") not in UPLOAD_STEPS:
                continue
            arts = job.get("artifacts") or {}
            # 兼容多种 key 名
            bvid = arts.get("new_bvid") or arts.get("bvid") or arts.get("vid_id") or ""
            if not bvid and job.get("status") != "done":
                continue
            plat = _detect_platform(proj.get("tool_id", ""), arts, job.get("step", ""))
            if platform and plat != platform:
                continue
            items.append({
                "platform": plat,
                "account": _detect_account(proj.get("params", {})),
                "vid_id": bvid,
                "project_id": proj["id"],
                "project_name": proj["name"],
                "tool_id": proj["tool_id"],
                "status": job.get("status", "unknown"),
                "created_at": job.get("finished_at") or job.get("started_at") or 0,
            })
    items.sort(key=lambda x: x["created_at"] or 0, reverse=True)
    items = items[:limit]
    return with_trace(request, {"items": items, "count": len(items)})


def _detect_platform(tool_id: str, arts: dict, step: str) -> str:
    if step == "upload_bili" or "bili" in step:
        return "bilibili"
    if step == "upload_douyin" or "douyin" in step:
        return "douyin"
    if tool_id in ("fengge", "fengge_url", "info_gap"):
        return "bilibili"
    if tool_id == "tiktok_story":
        return "bilibili"
    return "unknown"


def _detect_account(params: dict) -> str:
    # 从 params 中读账号 ID(若有)
    for k in ("account", "account_id", "user", "username"):
        if params.get(k):
            return str(params[k])
    return "default"