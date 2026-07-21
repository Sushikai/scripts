"""
/api/v1/auth/* 端点 — 注册/登录/登出/刷新/当前用户。

状态:代码完整,UI 未暴露。`register` 默认关闭,需 LEGAL_REGISTER_OPEN=true 才允许。
"""
from __future__ import annotations
import os
import time
from fastapi import APIRouter, Body, Header, Request
from pydantic import BaseModel
from app.services import user_service
from app.core.auth import hash_token
from app.api.v1 import router as v1_router


router = APIRouter(prefix="/auth", tags=["auth"])
v1_router.include_router(router)


# ── Request 模型 ────────────────────────────
class LoginReq(BaseModel):
    username: str
    password: str


class RegisterReq(BaseModel):
    username: str
    password: str
    email: str | None = None
    display_name: str | None = None


class RefreshReq(BaseModel):
    refresh_token: str


class LogoutReq(BaseModel):
    refresh_token: str | None = None


# ── 工具 ────────────────────────────────────
def _client_meta(request: Request) -> tuple[str | None, str | None]:
    return (
        request.headers.get("x-forwarded-for") or (request.client.host if request.client else None),
        request.headers.get("user-agent"),
    )


# ── 端点 ────────────────────────────────────
@router.post("/login")
async def login(req: LoginReq, request: Request):
    ip, ua = _client_meta(request)
    result = user_service.login(req.username, req.password, ip=ip, user_agent=ua)
    if not result["ok"]:
        # 不暴露具体原因(防爆破)
        return {"ok": False, "code": "LOGIN_FAILED", "message": result.get("message", "登录失败")}
    return {"ok": True, "data": result}


@router.post("/logout")
async def logout(req: LogoutReq, authorization: str | None = Header(None)):
    access = None
    if authorization and authorization.lower().startswith("bearer "):
        access = authorization[7:]
    revoked = user_service.logout(refresh_token=req.refresh_token, access_token=access)
    return {"ok": True, "data": {"revoked": revoked}}


@router.post("/refresh")
async def refresh(req: RefreshReq, request: Request):
    ip, _ = _client_meta(request)
    result = user_service.refresh(req.refresh_token, ip=ip)
    if not result:
        return {"ok": False, "code": "INVALID_REFRESH", "message": "refresh token 无效或已过期"}
    return {"ok": True, "data": result}


@router.get("/me")
async def me(authorization: str | None = Header(None)):
    """返回当前用户。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"ok": False, "code": "NOT_AUTHENTICATED", "message": "未登录"}
    token = authorization[7:]
    user = user_service.current_user_from_token(token)
    if not user:
        return {"ok": False, "code": "INVALID_TOKEN", "message": "token 无效或已过期"}
    return {
        "ok": True,
        "data": {
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email"),
            "role": user["role"],
            "display_name": user.get("display_name"),
            "last_login_at": user.get("last_login_at"),
        },
    }


@router.post("/register")
async def register(req: RegisterReq, request: Request):
    """
    注册端点(默认关闭)。
    开启:LEGAL_REGISTER_OPEN=true 且关闭单用户模式(LEGAL_SINGLE_USER=false)。
    """
    if os.environ.get("LEGAL_REGISTER_OPEN", "false").lower() != "true":
        return {"ok": False, "code": "REGISTER_CLOSED", "message": "注册未开放(预埋功能)"}
    try:
        user = user_service.create_user(
            username=req.username,
            password=req.password,
            email=req.email,
            display_name=req.display_name,
            role="user",
        )
        return {"ok": True, "data": {"username": user["username"], "role": user["role"]}}
    except ValueError as e:
        return {"ok": False, "code": "VALIDATION", "message": str(e)}


@router.post("/change_password")
async def change_password(
    payload: dict = Body(...),
    authorization: str | None = Header(None),
):
    """当前用户改密。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"ok": False, "code": "NOT_AUTHENTICATED", "message": "未登录"}
    token = authorization[7:]
    user = user_service.current_user_from_token(token)
    if not user:
        return {"ok": False, "code": "INVALID_TOKEN", "message": "token 无效"}
    old_pwd = payload.get("old_password", "")
    new_pwd = payload.get("new_password", "")
    if not old_pwd or not new_pwd:
        return {"ok": False, "code": "VALIDATION", "message": "旧密码/新密码不能为空"}
    # 校验旧密码
    from app.core.auth import verify_password
    if not verify_password(old_pwd, user["password_hash"], user["salt"]):
        return {"ok": False, "code": "BAD_OLD_PASSWORD", "message": "旧密码错误"}
    # 校验新密码强度
    from app.core.auth import validate_password, hash_password
    err = validate_password(new_pwd)
    if err:
        return {"ok": False, "code": "VALIDATION", "message": err}
    new_hash, new_salt = hash_password(new_pwd)
    from app.db.database import execute
    execute("UPDATE users SET password_hash = ?, salt = ?, updated_at = ? WHERE id = ?",
            (new_hash, new_salt, int(time.time()), user["id"]))
    # 强制撤销其他所有 token
    execute("UPDATE auth_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (int(time.time()), user["id"]))
    return {"ok": True, "data": {"message": "密码已更新,其他登录已撤销"}}