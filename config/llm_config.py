"""MiniMax/LLM API 配置"""
import os
from pathlib import Path

# 尝试加载 .env_minimax
_env_file = Path("/Users/kaikai/.env_minimax")
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

MINIMAX_CONFIG = {
    "baseUrl": os.getenv("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic"),
    "apiKey": os.getenv("MINIMAX_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN", ""),
    "model": "MiniMax-M2.7",
    "timeout": 60,
    "retry_times": 3,
}
