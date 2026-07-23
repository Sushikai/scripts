"""AI provider 注册中心。

调用方:
    from backend.ai.providers import anthropic_provider, minimax_provider
    from backend.ai.client import set_provider, call_llm
    set_provider(minimax_provider)
    out = call_llm("MiniMax-Text-01", "你是编剧", "写一个 60 秒脚本")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .client import set_provider

_logger = logging.getLogger("flow.ai.providers")


# === Anthropic SDK ===
def anthropic_provider(model: str, system: str, user: str, *, timeout: float = 30.0, **_kw) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK not installed") from e

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("FLOW_ANTHROPIC_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


# === MiniMax / OpenAI 兼容 ===
def minimax_provider(model: str, system: str, user: str, *, timeout: float = 30.0, base_url: Optional[str] = None, **_kw) -> str:
    try:
        import openai
    except ImportError as e:
        raise RuntimeError("openai SDK not installed") from e

    api_key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("FLOW_MINIMAX_KEY")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set")
    base = base_url or os.environ.get("MINIMAX_BASE_URL") or "https://api.MiniMax.com/v1"

    client = openai.OpenAI(api_key=api_key, base_url=base, timeout=timeout)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        timeout=timeout,
    )
    return (resp.choices[0].message.content or "").strip()


# === 干跑 provider(测试用)===
def echo_provider(model: str, system: str, user: str, *, timeout: float = 1.0, **_kw) -> str:
    """不调外部 LLM,直接回一个固定 JSON,给 dry-run 测试用。"""
    return '{"ok": true, "echo": true, "model": "%s"}' % model


# === 注册辅助 ===
def register_default() -> str:
    """按 env 自动选 provider。返回 provider 名。"""
    if os.environ.get("MINIMAX_API_KEY") or os.environ.get("FLOW_MINIMAX_KEY"):
        set_provider(minimax_provider)
        return "minimax"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("FLOW_ANTHROPIC_KEY"):
        set_provider(anthropic_provider)
        return "anthropic"
    set_provider(echo_provider)
    _logger.warning("no AI key set; using echo_provider (test only)")
    return "echo"