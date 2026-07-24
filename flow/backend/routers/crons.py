"""/api/crons:列出用户 launchd 任务(plist + launchctl 实时状态 + 日志尾部)。

R10 加 — Dashboard 上看到所有 com.kaikai.* / com.scripts.* / com.bilibili.fan-*
这些后台脚本的当前状态、调度、上次日志,不再靠猜。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["crons"])

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
LOG_TAIL_BYTES = 256 * 1024  # 只读尾部 256K 防爆


def _list_loaded() -> dict[str, tuple[str, str]]:
    """launchctl list → {label: (pid_or_dash, exit_status)}."""
    out: dict[str, tuple[str, str]] = {}
    try:
        text = subprocess.check_output(["launchctl", "list"], timeout=2).decode(errors="ignore")
    except Exception:
        return out
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        out[label] = (pid, status)
    return out


def _tail(path: str | None) -> str:
    """读文件尾部 LOG_TAIL_BYTES 字节,返最近 3 行。失败返 ''。"""
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
            data = f.read().decode(errors="ignore")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        return "\n".join(lines[-3:]) if lines else ""
    except Exception:
        return ""


def _schedule_summary(plist: dict) -> str:
    """抽 schedule 字段翻译成人话。"""
    if "StartCalendarInterval" in plist:
        sci = plist["StartCalendarInterval"]
        if isinstance(sci, dict):
            parts = []
            if "Hour" in sci: parts.append(f"{sci['Hour']:02d}")
            if "Minute" in sci: parts.append(f"{sci['Minute']:02d}")
            if "Weekday" in sci: parts.append(f"w{sci['Weekday']}")
            return "calendar " + ":".join(parts) if parts else "calendar"
        return "calendar x N"
    if "StartInterval" in plist:
        sec = int(plist["StartInterval"])
        if sec % 3600 == 0:
            return f"every {sec // 3600}h"
        if sec % 60 == 0:
            return f"every {sec // 60}m"
        return f"every {sec}s"
    if plist.get("KeepAlive"):
        return "keepalive"
    if plist.get("RunAtLoad"):
        return "run-on-load"
    return "manual"


def _parse_plist(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def _is_relevant(label: str) -> bool:
    """只关心 kaikai/scripts/bilibili/hermes 这几类。"""
    return (
        label.startswith("com.kaikai.")
        or label.startswith("com.scripts.")
        or label.startswith("com.bilibili.fan-")
        or label.startswith("ai.openclaw.")
    )


@router.get("/crons")
async def list_crons(request: Request):
    """扫描 LaunchAgents + 查 launchctl + tail 日志。"""
    loaded = _list_loaded()
    items: list[dict] = []
    if LAUNCH_AGENTS.exists():
        for plist_path in sorted(LAUNCH_AGENTS.glob("*.plist")):
            plist = _parse_plist(plist_path)
            if not plist:
                continue
            label = plist.get("Label", plist_path.stem)
            if not _is_relevant(label):
                continue
            pid, status = loaded.get(label, ("-", "-"))
            running = pid not in ("-", "")
            items.append({
                "label": label,
                "plist": str(plist_path),
                "program_args": plist.get("ProgramArguments") or [],
                "schedule": _schedule_summary(plist),
                "keep_alive": bool(plist.get("KeepAlive", False)),
                "run_at_load": bool(plist.get("RunAtLoad", False)),
                "stdout_path": plist.get("StandardOutPath", ""),
                "stderr_path": plist.get("StandardErrorPath", ""),
                "pid": pid,
                "last_status": status,
                "running": running,
                "stdout_tail": _tail(plist.get("StandardOutPath")),
                "stderr_tail": _tail(plist.get("StandardErrorPath")),
            })
    items.sort(key=lambda x: (not x["running"], x["label"]))
    return with_trace(request, {"items": items, "count": len(items), "total_loaded": sum(1 for x in items if x["running"])})


@router.get("/crons/summary")
async def crons_summary(request: Request):
    """KPI 摘要:总数 / 运行中 / 失败 exit / 带日志。"""
    full = (await list_crons(request))["data"]
    items = full["items"]
    failed = [x for x in items if x["last_status"] not in ("-", "0", "") and not x["running"]]
    return with_trace(request, {
        "total": len(items),
        "running": sum(1 for x in items if x["running"]),
        "stopped": sum(1 for x in items if not x["running"]),
        "failed_exit": len(failed),
        "with_logs": sum(1 for x in items if x["stdout_path"] or x["stderr_path"]),
    })