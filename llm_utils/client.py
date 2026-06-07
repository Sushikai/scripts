"""
LLM 统一接口 — Ollama + MiniMax 自动切换
"""
from __future__ import annotations

import os
import re
import requests
from pathlib import Path

# ── 环境变量加载 ────────────────────────────────────────────
_env_paths = [
    Path.home() / ".hermes/instances/movie_narrator/.env",
    Path.home() / ".openclaw/credentials/minimax.env",
    Path.home() / ".hermes/.env",
]
for _env_path in _env_paths:
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            if _line.strip() and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.strip().split("=", 1)
                os.environ.setdefault(_k, _v)

# ── Ollama 配置 ──────────────────────────────────────────────
OLLAMA_BASE = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODELS = ["qwen2.5:32b-instruct-q4_K_M", "gemma3:4b", "deepseek-r1:1.5b"]

# ── MiniMax 配置 ─────────────────────────────────────────────
MINIMAX_BASE = "https://api.minimax.chat/v1/chat/completions"
MINIMAX_MODEL = "MiniMax-M2.7"

def _load_minimax_key() -> str:
    return os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")

# ── Ollama 调用 ──────────────────────────────────────────────
def call_ollama(prompt: str, system: str = "", model: str | None = None) -> str | None:
    models = [model] if model else OLLAMA_MODELS
    for _model in models:
        try:
            resp = requests.post(
                OLLAMA_BASE,
                headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                json={
                    "model": _model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "temperature": 0.85,
                    "max_tokens": 100,
                    "stream": False
                },
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                content = (data.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
                if content:
                    return content
                reasoning = data.get('choices', [{}])[0].get('message', {}).get('reasoning', '')
                if reasoning:
                    txt = reasoning.split("|")[-1].strip()
                    txt = re.sub(r'\[.*?\]\s*', '', txt).strip()
                    return txt[:100] if txt else None
        except:
            pass
    return None

# ── MiniMax 调用 ─────────────────────────────────────────────
def call_minimax(prompt: str, system: str = "", max_tokens: int = 300) -> str | None:
    key = _load_minimax_key()
    if not key:
        return None
    try:
        resp = requests.post(
            MINIMAX_BASE,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": MINIMAX_MODEL,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "temperature": 0.85,
                "max_tokens": max_tokens,
                "stream": False
            },
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            base_resp = data.get('base_resp', {})
            if base_resp.get('status_code') != 0:
                return None
            content = (data.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
            if not content:
                content = data.get('choices', [{}])[0].get('message', {}).get('reasoning_content', '') or ''
            return content if content else None
    except:
        pass
    return None

# ── 统一接口：优先 Ollama，失败用 MiniMax ──────────────────
def generateReply(prompt: str, system: str = "") -> str | None:
    result = call_ollama(prompt, system)
    if result:
        return result
    return call_minimax(prompt, system)