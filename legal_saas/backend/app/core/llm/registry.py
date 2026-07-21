"""LLM 注册表 — 根据用户配置返回对应的 adapter 实例。"""
from __future__ import annotations
from .base import BaseLLM
from .minimax import MiniMaxLLM
from ..security import decrypt

# 配置 key 常量
CFG_API_KEY = "minimax_api_key"
CFG_BASE_URL = "minimax_base_url"
CFG_MODEL = "minimax_model"


def list_models() -> list[str]:
    """支持切换的模型列表。"""
    return [
        "MiniMax-M3",
        "MiniMax-M2",
        "claude-3-5-sonnet-20241022",
        "claude-3-haiku-20240307",
        "deepseek-chat",
        "deepseek-reasoner",
        "gpt-4o",
        "gpt-4o-mini",
    ]


def get_llm_from_config() -> BaseLLM | None:
    """从系统配置读 API key / model / base_url,返回 LLM 实例。未配置返回 None。"""
    from app.db.database import query_one
    cfg = {r["key"]: r["value_encrypted"] for r in query("SELECT key, value_encrypted FROM system_config")}
    api_key = decrypt(cfg.get(CFG_API_KEY, ""))
    if not api_key:
        return None
    base_url = decrypt(cfg.get(CFG_BASE_URL, "")) or "https://api.MiniMax.io/v1"
    model = decrypt(cfg.get(CFG_MODEL, "")) or "MiniMax-M3"
    return MiniMaxLLM(api_key=api_key, base_url=base_url, model=model)


def get_llm(api_key: str, base_url: str = "", model: str = "") -> MiniMaxLLM:
    """临时构造 LLM(用于测试)。"""
    return MiniMaxLLM(
        api_key=api_key,
        base_url=base_url or "https://api.MiniMax.io/v1",
        model=model or "MiniMax-M3",
    )