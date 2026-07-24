"""fengge_url 粘贴链接剪切上传 wrapper 测试。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.wrappers.fengge_url import FenggeUrlWrapper, _validate_url


def test_validate_url_accepts_https():
    assert _validate_url("https://www.bilibili.com/video/BV1abc") == "https://www.bilibili.com/video/BV1abc"
    assert _validate_url("http://example.com/v?id=1") == "http://example.com/v?id=1"


def test_validate_url_rejects_empty():
    with pytest.raises(ValueError, match="required"):
        _validate_url("")
    with pytest.raises(ValueError, match="required"):
        _validate_url("   ")


def test_validate_url_rejects_non_url():
    with pytest.raises(ValueError, match="invalid URL"):
        _validate_url("not a url")
    with pytest.raises(ValueError, match="invalid URL"):
        _validate_url("ftp://example.com")  # ftp 不被允许


def test_validate_url_strips_whitespace():
    assert _validate_url("  https://youtu.be/abc  ") == "https://youtu.be/abc"


def test_wrapper_metadata():
    w = FenggeUrlWrapper(dry_run=True)
    assert w.tool_id == "fengge_url"
    assert w.name == "峰哥粘贴链接"
    assert w.steps == ["download_url", "crop", "generate_meta", "upload"]


@pytest.mark.asyncio
async def test_dry_run_download_url():
    w = FenggeUrlWrapper(dry_run=True)
    progress = []
    logs = []
    def prog(p, msg):
        progress.append((p, msg))
    def log(m):
        logs.append(m)

    result = await w.run_step(
        "download_url",
        {"source_url": "https://www.bilibili.com/video/BV1test"},
        progress_cb=prog,
        log_cb=log,
        is_cancelled=lambda: False,
    )
    assert result["dry_run"] is True
    assert result["step"] == "download_url"
    assert progress[-1][0] == 1.0
    assert Path(result["output"]).exists()


@pytest.mark.asyncio
async def test_dry_run_all_steps():
    w = FenggeUrlWrapper(dry_run=True)
    params = {"source_url": "https://youtu.be/abc", "title": "测试标题"}

    for step in w.steps:
        r = await w.run_step(
            step, params,
            progress_cb=lambda p, m: None,
            log_cb=lambda m: None,
            is_cancelled=lambda: False,
        )
        assert r["dry_run"] is True
        assert r["step"] == step


@pytest.mark.asyncio
async def test_cancel_during_dry_run():
    w = FenggeUrlWrapper(dry_run=True)
    cancelled = [False]
    def is_cancelled():
        return cancelled[0]
    async def run_it():
        return await w.run_step(
            "download_url",
            {"source_url": "https://example.com/v"},
            progress_cb=lambda p, m: None,
            log_cb=lambda m: None,
            is_cancelled=is_cancelled,
        )
    task = asyncio.create_task(run_it())
    await asyncio.sleep(0.05)
    cancelled[0] = True
    with pytest.raises(RuntimeError, match="cancelled"):
        await task


@pytest.mark.asyncio
async def test_real_mode_requires_source_url():
    w = FenggeUrlWrapper(dry_run=False)
    with pytest.raises(ValueError, match="source_url is required"):
        await w.run_step(
            "download_url",
            {"source_url": ""},
            progress_cb=lambda p, m: None,
            log_cb=lambda m: None,
            is_cancelled=lambda: False,
        )


def test_which_ytdlp_returns_string_when_available():
    """至少有一个 yt-dlp 候选存在(或返回 None)。只断言返回类型。"""
    from backend.wrappers.fengge_url import _which_ytdlp
    r = _which_ytdlp()
    assert r is None or isinstance(r, str) and Path(r).exists()


def test_registry_lists_fengge_url():
    from backend.wrappers import builtin, registry
    builtin.register_builtin(registry.register)
    tools = registry.list_tools()
    ids = [t["tool_id"] for t in tools]
    assert "fengge_url" in ids
    meta = next(t for t in tools if t["tool_id"] == "fengge_url")
    assert meta["name"] == "峰哥粘贴链接"
    assert meta["steps"] == ["download_url", "crop", "generate_meta", "upload"]