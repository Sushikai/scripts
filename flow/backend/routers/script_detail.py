"""/api/scripts/{name} 详情:依赖 (imports) + git 最近 5 commit + 末行运行日志。

R17 加 — Settings 看到每个脚本的"它用了什么 / 谁改过 / 上次跑成怎样"。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["scripts"])

from .scripts import SCRIPTS

IMPORT_RE = re.compile(r"^(?:from\s+([\w.]+)|import\s+([\w.]+))", re.MULTILINE)


def _imports(path: str, is_dir: bool = False) -> list[str]:
    """扫所有 .py,提取本地 relative import 的模块名。"""
    p = Path(path)
    out: set[str] = set()
    if is_dir:
        files = list(p.rglob("*.py"))
    else:
        files = [p] if p.suffix == ".py" else []
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for m in IMPORT_RE.finditer(text):
            mod = (m.group(1) or m.group(2) or "").split(".")[0]
            if mod and not mod.startswith("_") and mod not in ("os", "sys", "re", "json", "time", "subprocess", "pathlib", "logging", "threading", "asyncio", "collections", "functools", "typing", "dataclasses", "urllib", "http", "socket", "shutil", "tempfile", "itertools", "enum", "abc", "contextlib", "traceback", "io", "warnings", "copy", "random", "math", "datetime"):
                out.add(mod)
    return sorted(out)


def _git_log(path: str, limit: int = 5) -> list[dict]:
    """git log --oneline | --format。失败返 []。"""
    p = Path(path)
    cwd = p if p.is_dir() else p.parent
    try:
        out = subprocess.check_output(
            ["git", "log", f"-{limit}", "--format=%H|%ai|%s"],
            cwd=str(cwd), timeout=4, stderr=subprocess.DEVNULL,
        ).decode(errors="ignore").strip()
    except Exception:
        return []
    items: list[dict] = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        sha, ts, msg = line.split("|", 2)
        items.append({"sha": sha[:8], "ts": ts, "message": msg[:120]})
    return items


def _last_log_line(path: str) -> str:
    """找同名 .log 配对,返末行。无返 ''。"""
    p = Path(path)
    candidates = [
        p.with_suffix(p.suffix + ".log"),
        p.parent / (p.stem + ".log"),
        p.parent / "logs" / (p.stem + ".log"),
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            try:
                size = c.stat().st_size
                with c.open("rb") as f:
                    if size > 4096:
                        f.seek(size - 4096)
                    data = f.read().decode(errors="ignore")
                lines = [ln for ln in data.splitlines() if ln.strip()]
                return lines[-1] if lines else ""
            except Exception:
                pass
    return ""


@router.get("/scripts/{name}")
async def script_detail(name: str, request: Request):
    for s in SCRIPTS:
        if s.get("name") != name:
            continue
        path = s["path"]
        p = Path(path)
        info = {
            "name": name,
            "path": path,
            "exists": p.exists(),
            "category": s.get("category"),
            "is_dir": p.is_dir() if p.exists() else False,
            "imports": _imports(path, p.is_dir()) if p.exists() else [],
            "git_log": _git_log(path),
            "last_log_line": _last_log_line(path) if p.exists() else "",
        }
        # 文件大小
        if p.exists():
            try:
                stat = p.stat()
                info["size_bytes"] = stat.st_size
                info["mtime"] = int(stat.st_mtime * 1000)
            except Exception:
                pass
        return with_trace(request, info)
    raise HTTPException(status_code=404, detail={"code": "SCRIPT_NOT_FOUND", "message": name})