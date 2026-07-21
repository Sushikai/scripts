"""loguru 分级日志。"""
from __future__ import annotations
import sys
from pathlib import Path
from loguru import logger
from ..config import DATA_DIR

_LOG_DIR = DATA_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
logger.add(str(_LOG_DIR / "app.log"), level="DEBUG", rotation="10 MB", retention="30 days", encoding="utf-8", enqueue=True)


def get_logger():
    return logger