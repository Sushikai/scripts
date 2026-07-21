"""API v1 routes."""
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")

# 子模块 import 时会调用 v1_router.include_router(...) 自动聚合