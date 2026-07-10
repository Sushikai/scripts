"""
core/utils.py - 工具层
日期格式转换、字段标准化、装饰器
"""
from datetime import datetime, timedelta
from typing import Optional


def to_yyyymmdd(date) -> str:
    """'2026-07-09' / datetime / '20260709' → '20260709' (akshare 标准格式)"""
    if isinstance(date, str):
        s = date.replace("-", "").replace("/", "")
        return s
    if isinstance(date, datetime):
        return date.strftime("%Y%m%d")
    raise ValueError(f"unsupported date type: {type(date)}")


def to_iso(date) -> str:
    """任意日期 → 'YYYY-MM-DD'"""
    if isinstance(date, str):
        return date.replace("/", "-")[:10]
    if isinstance(date, datetime):
        return date.strftime("%Y-%m-%d")
    return str(date)[:10]


def normalize_code(code: str) -> str:
    """'600519.SH' / 'sh600519' / '600519' → '600519'（纯6位）"""
    s = str(code).strip()
    for prefix in ["SH", "SZ", "sh", "sz"]:
        if s.startswith(prefix):
            s = s[len(prefix):]
    for suffix in [".SH", ".SZ", ".sh", ".sz"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    return s.zfill(6)


def ymd_to_display(date) -> str:
    """'20260708' → '2026-07-08'（人看着舒服）"""
    s = to_yyyymmdd(date)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
