"""/api/accounts:B 站多账号 cookie 健康检查(基于文件系统,不调外部 API)。

只读文件系统 + 解析 cookie 行数 + 文件 mtime + 大小,不发起网络请求。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request

from ..envelope import with_trace

router = APIRouter(prefix="/api", tags=["accounts"])


# 硬编码 4 个 cookie 文件路径(生产模式 + 备用 cookie)
ACCOUNTS = [
    {
        "id": "100w",
        "name": "20岁还没赚够100w",
        "cookie_path": "/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt",
        "platform": "bilibili",
        "role": "primary",
    },
    {
        "id": "travel",
        "name": "20岁还没开始环球旅行",
        "cookie_path": "/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt",
        "platform": "bilibili",
        "role": "primary",
    },
    {
        "id": "rain",
        "name": "那那天下雨了",
        "cookie_path": "/Users/kaikai/scripts/tiktok_story_bili/那那天下雨了_cookies.txt",
        "platform": "bilibili",
        "role": "secondary",
    },
    {
        "id": "leaf",
        "name": "风走了叶落",
        "cookie_path": "/Users/kaikai/scripts/tiktok_story_bili/风走了叶落_cookies.txt",
        "platform": "bilibili",
        "role": "secondary",
    },
]


def _check_cookie_file(path_str: str) -> dict:
    """检查 cookie 文件状态:存在性/mtime/size/行数。"""
    p = Path(path_str)
    info = {
        "exists": False,
        "mtime": None,
        "size_bytes": 0,
        "line_count": 0,
        "cookie_count": 0,
        "freshness": "unknown",  # fresh | stale | expired
        "age_days": None,
    }
    if not p.exists():
        info["freshness"] = "missing"
        return info
    stat = p.stat()
    info["exists"] = True
    info["mtime"] = int(stat.st_mtime * 1000)
    info["size_bytes"] = stat.st_size
    age_sec = time.time() - stat.st_mtime
    info["age_days"] = round(age_sec / 86400, 1)
    # 7 天内算 fresh,7-30 stale,30+ expired
    if age_sec < 7 * 86400:
        info["freshness"] = "fresh"
    elif age_sec < 30 * 86400:
        info["freshness"] = "stale"
    else:
        info["freshness"] = "expired"
    # 数行数和 cookie
    try:
        text = p.read_text(errors="ignore")
        lines = [l for l in text.splitlines() if l.strip()]
        info["line_count"] = len(lines)
        # Netscape 格式每行一个 cookie,或者 JSON 格式一个 cookie
        if text.lstrip().startswith("{"):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    info["cookie_count"] = len([k for k in data.keys() if not k.startswith("_")])
            except Exception:
                info["cookie_count"] = 0
        else:
            # Netscape 格式:跳过注释行
            cookie_lines = [l for l in lines if not l.startswith("#")]
            info["cookie_count"] = len(cookie_lines)
    except Exception:
        pass
    return info


@router.get("/accounts")
async def list_accounts(request: Request):
    """列出 4 个 B 站账号 + cookie 健康信息。"""
    items = []
    for acc in ACCOUNTS:
        cookie_info = _check_cookie_file(acc["cookie_path"])
        # status: ok / warn / bad
        status = "ok"
        if not cookie_info["exists"]:
            status = "bad"
        elif cookie_info["freshness"] == "expired":
            status = "bad"
        elif cookie_info["freshness"] == "stale":
            status = "warn"
        elif cookie_info["cookie_count"] == 0:
            status = "warn"
        items.append({
            **acc,
            "status": status,
            "cookie": cookie_info,
        })
    return with_trace(request, {"items": items, "count": len(items)})


@router.get("/accounts/{account_id}")
async def account_detail(request: Request, account_id: str):
    for acc in ACCOUNTS:
        if acc["id"] == account_id:
            cookie_info = _check_cookie_file(acc["cookie_path"])
            return with_trace(request, {**acc, "cookie": cookie_info})
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": account_id})