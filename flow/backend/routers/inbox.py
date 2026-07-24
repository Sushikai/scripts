"""/api/inbox:聚合系统级告警 — failed jobs / 失效 cookie / cron 异常 / 缺脚本 / 磁盘。

R11 加 — 让用户在 Dashboard 看到「现在哪些事情需要关注」,
不用手动翻 5 个 view。
"""

from __future__ import annotations

import os
import subprocess
import time
from collections import Counter

from fastapi import APIRouter, Request

from .. import _constants as C
from ..db import repo as db
from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["inbox"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _disk_alerts() -> list[dict]:
    """磁盘用量告警:任意根用量 > 90% → error; > 80% → warn。"""
    alerts: list[dict] = []
    roots = [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/ai_video_project"),
        os.path.expanduser("~/ai_video_upload"),
        str(C.PROJECT_ROOT()),
    ]
    for r in roots:
        try:
            usage = subprocess.check_output(["df", "-P", r], timeout=1).decode().splitlines()
            if len(usage) >= 2:
                fields = usage[1].split()
                pct = int(fields[4].rstrip("%"))
                used_gb = int(fields[2]) / 1024 / 1024
                avail_gb = int(fields[3]) / 1024 / 1024
                if pct >= 90:
                    sev = "error"
                elif pct >= 80:
                    sev = "warn"
                else:
                    continue
                alerts.append({
                    "severity": sev,
                    "category": "disk",
                    "title": f"{r} 磁盘 {pct}%",
                    "detail": f"已用 {used_gb:.1f} GB / 剩余 {avail_gb:.1f} GB",
                    "href": None,
                })
        except Exception:
            pass
    return alerts


def _failed_jobs() -> list[dict]:
    """最近 24h failed jobs。"""
    cutoff = _now_ms() - 86400 * 1000
    out: list[dict] = []
    try:
        for proj in db.project_list(limit=500):
            for job in db.job_list_by_project(proj["id"]):
                if job.get("status") == "failed" and (job.get("finished_at") or 0) >= cutoff:
                    out.append({
                        "severity": "error",
                        "category": "job",
                        "title": f"{proj.get('name', '?')} · {job.get('step')}",
                        "detail": (job.get("error") or "")[:120],
                        "href": f"#projects/{proj['id']}",
                        "ts": job.get("finished_at"),
                    })
    except Exception:
        pass
    out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return out[:10]


def _cookie_alerts() -> list[dict]:
    """accounts 接口 → 失效 cookie。"""
    alerts: list[dict] = []
    try:
        accs = db.account_list()
    except Exception:
        accs = []
    for a in accs:
        cookie = a.get("cookie") or {}
        f = cookie.get("freshness")
        if f == "expired":
            alerts.append({
                "severity": "error",
                "category": "cookie",
                "title": f"{a.get('name')} cookie 已过期",
                "detail": cookie.get("mtime_human") or cookie.get("mtime") or "?",
                "href": "#accounts",
            })
        elif f == "stale":
            alerts.append({
                "severity": "warn",
                "category": "cookie",
                "title": f"{a.get('name')} cookie 即将过期",
                "detail": cookie.get("mtime_human") or cookie.get("mtime") or "?",
                "href": "#accounts",
            })
    return alerts


def _cron_alerts() -> list[dict]:
    """复用 crons 模块 — 失败退出 + stderr 含 ERROR/Exception。"""
    from . import crons as crons_mod
    try:
        loaded = crons_mod._list_loaded()
    except Exception:
        return []
    alerts: list[dict] = []
    if not crons_mod.LAUNCH_AGENTS.exists():
        return []
    for plist_path in crons_mod.LAUNCH_AGENTS.glob("*.plist"):
        plist = crons_mod._parse_plist(plist_path)
        if not plist:
            continue
        label = plist.get("Label", plist_path.stem)
        if not crons_mod._is_relevant(label):
            continue
        pid, status = loaded.get(label, ("-", "-"))
        running = pid not in ("-", "")
        if not running and status not in ("-", "0", ""):
            alerts.append({
                "severity": "error",
                "category": "cron",
                "title": f"{label} 退出码 {status}",
                "detail": (crons_mod._tail(plist.get("StandardErrorPath")) or "").split("\n")[-1][:120],
                "href": "#crons",
            })
        elif running and plist.get("StandardErrorPath"):
            tail = crons_mod._tail(plist.get("StandardErrorPath"))
            if any(kw in tail.lower() for kw in ("error", "exception", "traceback")):
                alerts.append({
                    "severity": "warn",
                    "category": "cron",
                    "title": f"{label} 日志有 ERROR",
                    "detail": tail.split("\n")[-1][:120],
                    "href": "#crons",
                })
    return alerts


@router.get("/inbox")
async def inbox(request: Request):
    """聚合所有告警,按 severity 排序,每类最多 10 条。"""
    items: list[dict] = []
    items.extend(_failed_jobs())
    items.extend(_cookie_alerts())
    items.extend(_cron_alerts())
    items.extend(_disk_alerts())
    items.sort(key=lambda x: (0 if x["severity"] == "error" else 1, -(x.get("ts") or 0)))
    counts = Counter(x["severity"] for x in items)
    return with_trace(request, {
        "items": items,
        "count": len(items),
        "error_count": counts.get("error", 0),
        "warn_count": counts.get("warn", 0),
        "ts": _now_ms(),
    })