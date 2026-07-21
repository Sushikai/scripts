"""
/api/v1/config 端点 — 配置读写 + LLM 连通性测试。
GET:  返回脱敏后的公开配置
PATCH: 批量更新(MiniMax Key / Base URL / Model)
POST /test: 用临时传入的 key/model 测试连通性(不保存)
POST /test_saved: 用已保存的配置测试
"""
from __future__ import annotations
import time
import asyncio
from fastapi import APIRouter, Body
from pydantic import BaseModel
from app.services.config_service import get_all_public, update_batch
from app.core.llm.registry import get_llm, get_llm_from_config, list_models
from app.api.v1 import router as v1_router

# 复用 v1_router(自带 /api/v1 prefix),只需再加 /config
router = APIRouter(prefix="/config", tags=["config"])
v1_router.include_router(router)


class ConfigUpdate(BaseModel):
    minimax_api_key: str | None = None
    minimax_base_url: str | None = None
    minimax_model: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    statute_api_key: str | None = None
    statute_api_base: str | None = None
    active_role: str | None = None


class TestRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.MiniMax.io/v1"
    model: str = "MiniMax-M3"


@router.get("")
def list_config():
    """返回所有公开配置(密钥脱敏)。"""
    return {"ok": True, "data": get_all_public()}


@router.patch("")
def update_config(payload: ConfigUpdate):
    """批量更新配置。"""
    update_batch(payload.model_dump(exclude_none=True))
    return {"ok": True, "data": get_all_public()}


@router.get("/models")
def available_models():
    """返回可选模型列表。"""
    return {"ok": True, "data": list_models()}


@router.post("/test")
async def test_connection(req: TestRequest):
    """用临时传入的 key/model 测试连通性(不保存)。"""
    if not req.api_key or len(req.api_key) < 8:
        return {"ok": False, "code": "INVALID_KEY", "message": "API Key 长度不足", "data": None}
    llm = get_llm(api_key=req.api_key, base_url=req.base_url, model=req.model)
    t0 = time.time()
    try:
        resp = await asyncio_wait_with_timeout(llm.test("你好"), timeout=20)
        dt = (time.time() - t0) * 1000
        return {
            "ok": True,
            "data": {
                "latency_ms": round(dt, 1),
                "model": resp.model,
                "reply": resp.content,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
            },
        }
    except Exception as e:
        dt = (time.time() - t0) * 1000
        return {
            "ok": False,
            "code": "TEST_FAILED",
            "message": str(e),
            "data": {"latency_ms": round(dt, 1)},
        }


@router.post("/test_saved")
async def test_saved_config():
    """用已保存的配置测试连通性。"""
    llm = get_llm_from_config()
    if not llm:
        return {"ok": False, "code": "NOT_CONFIGURED", "message": "尚未配置 API Key"}
    t0 = time.time()
    try:
        resp = await asyncio_wait_with_timeout(llm.test("你好,请回复 OK"), timeout=20)
        dt = (time.time() - t0) * 1000
        return {
            "ok": True,
            "data": {
                "latency_ms": round(dt, 1),
                "model": resp.model,
                "reply": resp.content,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
            },
        }
    except Exception as e:
        dt = (time.time() - t0) * 1000
        return {
            "ok": False,
            "code": "TEST_FAILED",
            "message": str(e),
            "data": {"latency_ms": round(dt, 1)},
        }


async def asyncio_wait_with_timeout(coro, timeout: float):
    """带超时跑的协程。"""
    return await asyncio.wait_for(coro, timeout=timeout)