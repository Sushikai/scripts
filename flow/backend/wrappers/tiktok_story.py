"""TikTok 故事流水线 wrapper。

包装 tiktok_story_bili 三件套:tiktok_story_reupload / youtube_story_reupload / upload_bili。
干运行返回 mock 产物,生产模式 import 原模块。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.tiktok_story")

TIKTOK_PKG = Path("/Users/kaikai/scripts/tiktok_story_bili").resolve()
SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()


class TikTokStoryWrapper(ToolWrapper):
    tool_id = "tiktok_story"
    name = "TikTok 故事"
    description = "TikTok/YouTube 搜索→下载→字幕烧录→裁剪→B站+抖音上传。"
    steps = ["fetch", "download", "subtitle", "crop", "upload_bili", "upload_douyin"]

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._mod_tt = None
        self._mod_yt = None
        self._mod_up = None
        self._results: dict = {}

    def _ensure_imported(self):
        if self._mod_tt is not None:
            return
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        if str(TIKTOK_PKG) not in sys.path:
            sys.path.insert(0, str(TIKTOK_PKG))
        try:
            import tiktok_story_reupload  # noqa: F401
            import youtube_story_reupload  # noqa: F401
            import upload_bili  # noqa: F401
            self._mod_tt = tiktok_story_reupload
            self._mod_yt = youtube_story_reupload
            self._mod_up = upload_bili
            _logger.info("tiktok_story_bili loaded from %s", TIKTOK_PKG)
        except ImportError as e:
            _logger.error("failed to import tiktok_story_bili: %s", e)

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
        log_cb(f"tiktok_story/{step} starting dry_run={self.dry_run}")

        if self.dry_run:
            return await self._run_dry(step, params, progress_cb, log_cb, is_cancelled)
        self._ensure_imported()
        if self._mod_tt is None:
            raise RuntimeError("tiktok_story_bili not importable")
        return await self._run_real(step, params, progress_cb, log_cb, is_cancelled)

    async def _run_dry(self, step, params, progress_cb, log_cb, is_cancelled):
        step_durations = {
            "fetch": 0.4,
            "download": 0.6,
            "subtitle": 0.4,
            "crop": 0.3,
            "upload_bili": 0.4,
            "upload_douyin": 0.4,
        }
        secs = step_durations.get(step, 0.3)
        n = 4
        for i in range(n):
            if is_cancelled():
                raise RuntimeError("cancelled")
            await asyncio.sleep(secs / n)
            progress_cb((i + 1) / n, f"{step} {i+1}/{n}")
        artifact = f"/tmp/flow_tiktok_story_{step}_output.txt"
        with open(artifact, "w") as f:
            f.write(f"dry-run output for step={step} params={params}\n")
        return {"output": artifact, "step": step, "dry_run": True}

    async def _run_real(self, step, params, progress_cb, log_cb, is_cancelled):
        def _check():
            if is_cancelled():
                raise RuntimeError("cancelled")

        if step == "fetch":
            source = params.get("source", "tiktok")  # tiktok | youtube
            keyword = params.get("keyword")
            mod = self._mod_tt if source == "tiktok" else self._mod_yt

            def _f():
                _check()
                progress_cb(0.2, f"搜索 {source}")
                kws = keyword or "story"
                items = mod.search_tiktok(kws, count=5) if source == "tiktok" else mod.search_youtube(kws, count=5)
                _check()
                progress_cb(0.7, "打分")
                scored = []
                for it in items[:10]:
                    it["score"] = mod.score_tiktok_video(it) if source == "tiktok" else mod.score_youtube_video(it)
                    scored.append(it)
                scored.sort(key=lambda x: x.get("score", 0), reverse=True)
                return scored[:5]
            items = await asyncio.to_thread(_f)
            self._results["fetched"] = items
            self._results["source"] = source
            return {"source": source, "items": items, "count": len(items)}

        elif step == "download":
            fetched = params.get("items") or self._results.get("fetched", [])
            source = params.get("source") or self._results.get("source", "tiktok")
            mod = self._mod_tt if source == "tiktok" else self._mod_yt
            if not fetched:
                raise RuntimeError("no fetched items to download")
            chosen = fetched[0]
            vid = chosen.get("id") or chosen.get("video_id")
            work_dir = Path(params.get("work_dir", "/tmp/flow_tiktok_work"))
            work_dir.mkdir(parents=True, exist_ok=True)

            def _d():
                _check()
                progress_cb(0.3, f"下载 {vid}")
                if source == "tiktok":
                    out, _ = mod.download_tiktok_video(vid, work_dir)
                else:
                    out = mod.download_youtube_video(vid, work_dir)
                _check()
                progress_cb(0.9, "完成")
                return out
            out = await asyncio.to_thread(_d)
            if not out:
                raise RuntimeError(f"download failed for {vid}")
            self._results["raw_file"] = str(out)
            self._results["video_id"] = vid
            return {"video_id": vid, "raw_file": str(out)}

        elif step == "subtitle":
            raw_file = params.get("raw_file") or self._results.get("raw_file")
            vid = params.get("video_id") or self._results.get("video_id", "")
            if not raw_file:
                raise RuntimeError("raw_file missing for subtitle")
            source = self._results.get("source", "tiktok")
            mod = self._mod_tt if source == "tiktok" else self._mod_yt

            def _s():
                _check()
                progress_cb(0.3, "字幕烧录")
                out = mod.burn_subtitle(Path(raw_file), vid)
                _check()
                progress_cb(0.9, "完成")
                return out
            out = await asyncio.to_thread(_s)
            if not out:
                # 没有字幕是允许的(YouTube 无字幕)
                return {"skipped": True, "raw_file": raw_file}
            self._results["subtitled_file"] = str(out)
            return {"subtitled_file": str(out)}

        elif step == "crop":
            raw_file = (
                params.get("raw_file")
                or self._results.get("subtitled_file")
                or self._results.get("raw_file")
            )
            if not raw_file or not Path(raw_file).exists():
                raise RuntimeError("raw_file missing for crop")
            cropped = str(Path(raw_file).with_name(Path(raw_file).stem + "_cropped.mp4"))
            mod = self._mod_tt  # crop_video is in tiktok_story_reupload

            def _c():
                _check()
                progress_cb(0.3, "ffmpeg 裁剪")
                out = mod.crop_video(Path(raw_file), Path(cropped))
                _check()
                progress_cb(0.9, "完成")
                return out
            out = await asyncio.to_thread(_c)
            if not out:
                raise RuntimeError("crop failed")
            self._results["cropped_file"] = str(out)
            return {"cropped_file": str(out)}

        elif step == "upload_bili":
            cropped_file = params.get("cropped_file") or self._results.get("cropped_file")
            title = params.get("title", "TikTok 故事")
            desc = params.get("description", "")
            if not cropped_file or not Path(cropped_file).exists():
                raise RuntimeError("cropped_file missing for bili upload")

            def _u():
                _check()
                progress_cb(0.3, "biliup")
                res = self._mod_up.biliup_upload(cropped_file, title, desc)
                _check()
                progress_cb(0.9, "完成")
                return res
            res = await asyncio.to_thread(_u)
            return {"uploaded": bool(res), "bvid": res if isinstance(res, str) else None}

        elif step == "upload_douyin":
            cropped_file = params.get("cropped_file") or self._results.get("cropped_file")
            title = params.get("title", "TikTok 故事")
            desc = params.get("description", "")
            if not cropped_file or not Path(cropped_file).exists():
                raise RuntimeError("cropped_file missing for douyin upload")

            def _u():
                _check()
                progress_cb(0.3, "douyin")
                ok = self._mod_up.douyin_upload_sync(cropped_file, title, desc)
                _check()
                progress_cb(0.9, "完成")
                return ok
            ok = await asyncio.to_thread(_u)
            return {"uploaded": bool(ok)}

        raise ValueError(f"unhandled step {step}")