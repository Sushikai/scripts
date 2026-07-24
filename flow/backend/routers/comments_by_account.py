"""/api/comments/by-target:按 fan_hunter 目标 (uid + uname) 聚合互动统计。

R19 加 — "评论功能 显示更多信息":Top 20 用户被点赞/回复/关注/DM 数 +
首次/末次互动时间 + 该用户互动过的视频。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from fastapi import APIRouter, Query, Request

from ..envelope import with_trace
from .comments import _read_actions_tail

router = APIRouter(prefix="/api/comments", tags=["comments-by-target"])


@router.get("/by-target")
async def by_target(request: Request, limit: int = Query(20, le=100)):
    """Top N 用户被互动统计 (按 fan_hunter_actions.jsonl 尾部)。"""
    items = _read_actions_tail(limit=5000)
    # 按 uid 聚合
    by_uid: dict[str, dict] = {}
    for a in items:
        uid = str(a.get("uid") or "")
        if not uid:
            continue
        rec = by_uid.setdefault(uid, {
            "uid": uid,
            "uname": a.get("uname") or "?",
            "by_action": Counter(),
            "first_seen": a.get("timestamp"),
            "last_seen": a.get("timestamp"),
            "videos": set(),
            "total": 0,
        })
        rec["by_action"][a.get("action", "?")] += 1
        rec["total"] += 1
        ts = a.get("timestamp") or ""
        if ts and ts < rec["first_seen"]:
            rec["first_seen"] = ts
        if ts and ts > rec["last_seen"]:
            rec["last_seen"] = ts
        bv = a.get("bvid") or ""
        if bv:
            rec["videos"].add(bv)
    out = []
    for r in by_uid.values():
        out.append({
            "uid": r["uid"],
            "uname": r["uname"],
            "total": r["total"],
            "likes": r["by_action"].get("like", 0),
            "replies": r["by_action"].get("reply", 0),
            "follows": r["by_action"].get("follow", 0),
            "dms": r["by_action"].get("dm", 0),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "video_count": len(r["videos"]),
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return with_trace(request, {
        "items": out[:limit],
        "count": len(out),
        "limit": limit,
    })


@router.get("/by-target/{uid}")
async def target_detail(uid: str, request: Request):
    """单个 uid 的完整互动记录。"""
    items = _read_actions_tail(limit=5000)
    records = [a for a in items if str(a.get("uid") or "") == uid]
    by_action = Counter(a.get("action", "?") for a in records)
    videos: dict[str, dict] = {}
    for a in records:
        bv = a.get("bvid") or ""
        if not bv:
            continue
        v = videos.setdefault(bv, {"bvid": bv, "title": a.get("video_title") or "?", "actions": Counter()})
        v["actions"][a.get("action", "?")] += 1
    video_list = sorted(videos.values(), key=lambda x: sum(x["actions"].values()), reverse=True)
    for v in video_list:
        v["actions"] = dict(v["actions"])
    return with_trace(request, {
        "uid": uid,
        "uname": records[0].get("uname", "?") if records else "?",
        "total": len(records),
        "by_action": dict(by_action),
        "videos": video_list,
        "records": records[:30],
    })