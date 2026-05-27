"""LLM 配置 — MiniMax（Anthropic兼容接口）+ Ollama fallback"""
import os
from pathlib import Path

# ── 尝试从 .env 文件加载 API Key ───────────────────────────────
_env_paths = [
    Path("/Users/kaikai/.hermes/instances/movie_narrator/.env"),
    Path("/Users/kaikai/.openclaw/credentials/minimax.env"),
    Path("/Users/kaikai/.hermes/.env"),
]
for _env_path in _env_paths:
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            if _line.strip() and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.strip().split("=", 1)
                os.environ.setdefault(_k, _v)
                # 同步 ANTHROPIC_AUTH_TOKEN（兼容旧代码）
                if _k == "MINIMAX_API_KEY" and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
                    os.environ["ANTHROPIC_AUTH_TOKEN"] = _v

# ── MiniMax 配置（Anthropic兼容）───────────────────────────────
MINIMAX_CONFIG = {
    "baseUrl": os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic"),
    "apiKey": os.getenv("MINIMAX_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN", "")),
    "model": os.getenv("ANTHROPIC_MODEL", "MiniMax-M2.7"),
    "timeout": 60,
    "retry_times": 3,
}

# ── Ollama fallback 配置 ─────────────────────────────────────
OLLAMA_CONFIG = {
    "base_url": "http://localhost:11434",
    "model": "qwen2.5:7b",
    "timeout": 120,
    "retry_times": 3,
}