"""cache key 命名规范:统一前缀 + 命名空间。"""

from __future__ import annotations


def dashboard() -> str:
    return "flow:dashboard:summary"


def tools_meta() -> str:
    return "flow:tools:meta"


def account_check(name: str, platform: str) -> str:
    return f"flow:account:check:{platform}:{name}"


def thumb(asset_id: str) -> str:
    return f"flow:thumb:{asset_id}"