"""峰哥粘贴链接剪切上传 wrapper。

用户粘贴任意视频 URL(B 站/抖音/YouTube/...):
  download_url → crop → generate_meta → upload

干运行(dry_run=True)返回 mock 产物,生产模式 import 原 fengge_pipeline。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.fengge_url")

FENGGE_PKG = Path("/Users/kaikai/scripts/video").resolve()
SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()
HERMES_YTDLP = "/Users/kaikai/.hermes/hermes-agent/venv/bin/yt-dlp"
SYSTEM_YTDLP = "/opt/homebrew/bin/yt-dlp"


def _which_ytdlp() -> Optional[str]:
    for p in (HERMES_YTDLP, SYSTEM_YTDLP):
        if Path(p).exists():
            return p
    import shutil
    return shutil.which("yt-dlp")


# 简单 URL 校验:支持 http(s)://,任意 host/path
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("source_url is required")
    if not _URL_RE.match(url):
        raise ValueError(f"invalid URL: {url[:80]}")
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"URL missing host: {url[:80]}")
    return url


class FenggeUrlWrapper(ToolWrapper):
    tool_id = "fengge_url"
    name = "峰哥粘贴链接"
    description = "粘贴任意视频 URL → yt-dlp 下载 → 80% 裁剪 → 上传 B 站。"
    steps = ["download_url", "crop", "generate_meta", "upload"]

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._mod = None
        self._results: dict = {}

    def _ensure_imported(self):
        if self._mod is not None:
            return
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        if str(FENGGE_PKG) not in sys.path:
            sys.path.insert(0, str(FENGGE_PKG))
        try:
            import fengge_pipeline  # noqa: F401
            self._mod = fengge_pipeline
            _logger.info("fengge_pipeline loaded from %s", FENGGE_PKG)
        except ImportError as e:
            _logger.error("failed to import fengge_pipeline: %s", e)
            self._mod = None

    async def run_step(
        self,
        step: str,
        params: dict,
        *,
        progress_cb: Callable[[float, Optional[str]], None],
        log_cb: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> dict:
        if step not in self.steps:
            raise ValueError(f"unknown step {step}; valid: {self.steps}")
        log_cb(f"fengge_url/{step} starting dry_run={self.dry_run}")

        if self.dry_run:
            return await self._run_dry(step, params, progress_cb, log_cb, is_cancelled)
        self._ensure_imported()
        if self._mod is None:
            raise RuntimeError("fengge_pipeline not importable")
        return await self._run_real(step, params, progress_cb, log_cb, is_cancelled)

    async def _run_dry(self, step, params, progress_cb, log_cb, is_cancelled):
        """dry-run:模拟每步产物,不调外部依赖。"""
        step_durations = {
            "download_url": 0.6,
            "crop": 0.4,
            "generate_meta": 0.5,
            "upload": 0.4,
        }
        secs = step_durations.get(step, 0.3)
        n = 4
        for i in range(n):
            if is_cancelled():
                raise RuntimeError("cancelled")
            await asyncio.sleep(secs / n)
            progress_cb((i + 1) / n, f"{step} {i+1}/{n}")
        artifact = f"/tmp/flow_fengge_url_{step}_output.txt"
        with open(artifact, "w") as f:
            f.write(f"dry-run output for step={step} params_keys={list(params.keys())}\n")
        return {"output": artifact, "step": step, "dry_run": True}

    async def _run_real(self, step, params, progress_cb, log_cb, is_cancelled):
        """真实模式:yt-dlp 下载 → fengge_pipeline 裁剪/上传。"""
        def _check():
            if is_cancelled():
                raise RuntimeError("cancelled")

        if step == "download_url":
            url = _validate_url(params.get("source_url", ""))
            work_dir = Path(params.get("work_dir", "/tmp/flow_fengge_url_work"))
            work_dir.mkdir(parents=True, exist_ok=True)
            ytdlp = _which_ytdlp()
            if not ytdlp:
                raise RuntimeError("yt-dlp not found")

            # 用 URL 末段做文件名,防止覆盖;yt-dlp 自己也会有 .mp4 后缀
            url_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", urlparse(url).path.strip("/"))[-60:] or "video"
            output_template = str(work_dir / f"{url_slug}.%(ext)s")

            def _d():
                _check()
                cmd = [
                    ytdlp,
                    "-f", "bestvideo[height>=1080]+bestaudio/bestvideo[height>=720]+bestaudio/best",
                    "--no-playlist",
                    "--max-filesize", "500M",
                    "-o", output_template,
                    url,
                ]
                progress_cb(0.2, "yt-dlp 启动")
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                _check()
                if proc.returncode != 0:
                    raise RuntimeError(f"yt-dlp failed: {proc.stderr[-300:]}")
                # 找下载到的实际文件
                files = sorted(
                    work_dir.glob(f"{url_slug}.*"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if not files:
                    raise RuntimeError("yt-dlp ran but no file produced")
                return files[0]
            out = await asyncio.to_thread(_d)
            progress_cb(0.9, "下载完成")
            self._results["raw_file"] = str(out)
            return {"source_url": url, "raw_file": str(out), "size_mb": round(out.stat().st_size / 1024 / 1024, 1)}

        elif step == "crop":
            raw_file = params.get("raw_file") or self._results.get("raw_file")
            if not raw_file or not Path(raw_file).exists():
                raise RuntimeError("raw_file missing for crop")
            cropped = str(Path(raw_file).with_name(Path(raw_file).stem + "_cropped.mp4"))

            def _c():
                _check()
                progress_cb(0.2, "ffmpeg 裁剪 80%")
                out = self._mod.crop_to_80(Path(raw_file), Path(cropped))
                _check()
                return out
            out = await asyncio.to_thread(_c)
            if not out:
                raise RuntimeError("crop failed")
            progress_cb(0.9, "裁剪完成")
            self._results["cropped_file"] = str(out)
            return {"cropped_file": str(out)}

        elif step == "generate_meta":
            title = params.get("title") or "粘贴链接切片"
            def _g():
                _check()
                progress_cb(0.3, "LLM 生成简介")
                desc, comment = self._mod.generate_desc_and_comment(title)
                return desc, comment
            desc, comment = await asyncio.to_thread(_g)
            self._results["description"] = desc
            self._results["comment"] = comment
            return {"title": title, "description": desc, "comment": comment}

        elif step == "upload":
            cropped_file = params.get("cropped_file") or self._results.get("cropped_file")
            title = params.get("title", "粘贴链接切片")
            desc = params.get("description") or self._results.get("description", "")
            if not cropped_file or not Path(cropped_file).exists():
                raise RuntimeError("cropped_file missing for upload")

            def _u():
                _check()
                progress_cb(0.3, "biliup 上传")
                res = self._mod.biliup_upload(cropped_file, title, desc)
                _check()
                return res
            res = await asyncio.to_thread(_u)
            new_bvid = res if isinstance(res, str) else None
            progress_cb(0.9, "上传完成")
            return {"uploaded": bool(new_bvid), "new_bvid": new_bvid, "raw": str(res)[:200]}

        raise ValueError(f"unhandled step {step}")