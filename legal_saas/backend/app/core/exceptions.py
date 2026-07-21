"""统一异常类 + 全局 handler。"""
from __future__ import annotations
from fastapi import Request
from fastapi.responses import JSONResponse


class BusinessError(Exception):
    def __init__(self, message: str, code: str = "BIZ_ERROR", status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(BusinessError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, code="NOT_FOUND", status_code=404)


class AuthError(BusinessError):
    def __init__(self, message: str = "未授权"):
        super().__init__(message, code="UNAUTHORIZED", status_code=401)


class APIError(BusinessError):
    def __init__(self, message: str = "外部 API 错误"):
        super().__init__(message, code="API_ERROR", status_code=502)


async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "code": exc.code, "message": exc.message},
    )