"""安全工具:path traversal 防护 / token 鉴权。"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import Header, HTTPException, Request

from . import _constants as C


def safe_path_under(allowed_roots: tuple, user_path: str) -> Path:
    """解析 user_path 并验证落在 allowed_roots 任一根下(防 ../traversal)。

    Raises HTTPException(403) 如果越界。
    """
    p = Path(user_path).expanduser().resolve()
    for root in allowed_roots:
        try:
            root_resolved = Path(root).expanduser().resolve()
            p.relative_to(root_resolved)  # 抛 ValueError 如果不在 root 下
            return p
        except (ValueError, OSError):
            continue
    raise HTTPException(status_code=403, detail={"code": "FILE_FORBIDDEN", "message": f"path {user_path} not under any allowed root"})


def safe_path_for_file_route(request_path: str) -> Path:
    """/api/file/{path:path} 专用:用 _constants.ALLOWED_FS_ROOTS。"""
    return safe_path_under(C.ALLOWED_FS_ROOTS(), request_path)


def is_under(root: Path, target: Path) -> bool:
    """target 是否在 root 下(已 resolve)。"""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def require_local_token(authorization: str | None = Header(None)) -> None:
    """简易本地 token 鉴权(给前端 header 写 token)。

    未传或不对 → 401。开发期可关。
    """
    if not C.LOCAL_TOKEN:
        return  # 未配置就放行(开发模式)
    if not authorization:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "missing Authorization header"})
    token = authorization.strip()
    if token.startswith("Bearer "):
        token = token[7:]
    if not hmac.compare_digest(token, C.LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "invalid token"})


def client_ip(request: Request) -> str:
    """取客户端真实 IP(支持 X-Forwarded-For)。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"