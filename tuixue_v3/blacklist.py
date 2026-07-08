"""
tuixue_v3/blacklist.py
黑名单机制：止损离场的亏损杂毛个股 → 永久拉黑，不再进入任何选股结果。
持久化到 blacklist.json。
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime
from pathlib import Path

from . import config as cfg

log = logging.getLogger("tuixue_v3.blacklist")

BLACKLIST_FILE = cfg.CACHE_DIR / "blacklist.json"


def _load() -> dict:
    if not BLACKLIST_FILE.exists():
        return {"codes": {}, "version": 1}
    try:
        return json.loads(BLACKLIST_FILE.read_text())
    except Exception as e:
        log.warning(f"读黑名单失败: {e}")
        return {"codes": {}, "version": 1}


def _save(data: dict) -> None:
    data["updated_at"] = datetime.now().isoformat()
    BLACKLIST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def is_blacklisted(code: str) -> bool:
    """检查 code 是否在黑名单"""
    data = _load()
    return code in data.get("codes", {})


def add_to_blacklist(code: str, name: str = "", reason: str = "", pnl_pct: float = 0.0) -> None:
    """止损离场后调用：永久加入黑名单"""
    data = _load()
    if code in data["codes"]:
        return
    data["codes"][code] = {
        "name": name,
        "reason": reason,
        "pnl_pct": round(pnl_pct, 2),
        "added_at": datetime.now().isoformat(),
    }
    _save(data)
    log.warning(f"🚫 {code} {name} 永久拉黑: {reason} 亏损 {pnl_pct:.2f}%")


def get_all() -> dict:
    return _load().get("codes", {})


def get_count() -> int:
    return len(_load().get("codes", {}))


def remove(code: str) -> bool:
    """手动解除（一般不调用）"""
    data = _load()
    if code in data["codes"]:
        del data["codes"][code]
        _save(data)
        return True
    return False


def filter_blacklist(codes: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """批量过滤：返回 (幸存, 被拉黑的code列表)"""
    data = _load()
    bl = data.get("codes", {})
    survived, blocked = [], []
    for code, name in codes:
        if code in bl:
            blocked.append(code)
        else:
            survived.append((code, name))
    return survived, blocked