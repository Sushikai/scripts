"""峰哥切片流水线 wrapper。

干运行(dry_run=True)返回 mock 产物,生产模式 import 原 fengge_pipeline。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.fengge")

FENGGE_PKG = Path("/Users/kaikai/scripts/video").resolve()
SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()


class FenggeWrapper(ToolWrapper):
    tool_id = "fengge"
    name = "峰哥切片"
    description = "B站推荐→下载→80%裁剪→LLM 简介→上传 B站→引流评论。"
    steps = ["fetch_candidates", "download", "crop", "generate_meta", "upload"]

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
        log_cb(f"fengge/{step} starting dry_run={self.dry_run}")

        if self.dry_run:
            return await self._run_dry(step, params, progress_cb, log_cb, is_cancelled)
        self._ensure_imported()
        if self._mod is None:
            raise RuntimeError("fengge_pipeline not importable")
        return await self._run_real(step, params, progress_cb, log_cb, is_cancelled)

    async def _run_dry(self, step, params, progress_cb, log_cb, is_cancelled):
        """dry-run:模拟每步产物,不调外部依赖。"""
        step_durations = {
            "fetch_candidates": 0.4,
            "download": 0.6,
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
        artifact = f"/tmp/flow_fengge_{step}_output.txt"
        with open(artifact, "w") as f:
            f.write(f"dry-run output for step={step} params={params}\n")
        return {"output": artifact, "step": step, "dry_run": True}

    async def _run_real(self, step, params, progress_cb, log_cb, is_cancelled):
        """真实模式:调用 fengge_pipeline 的具体函数。"""
        def _check():
            if is_cancelled():
                raise RuntimeError("cancelled")

        if step == "fetch_candidates":
            def _f():
                _check()
                progress_cb(0.2, "推荐 feed")
                rec = self._mod.get_recommended_videos(limit=50)
                _check()
                progress_cb(0.7, "打分排序")
                top = []
                for v in rec[:20]:
                    v["score"] = self._mod.score_video(v.get("stats", {}), v.get("pubdate", 0))
                    top.append(v)
                top.sort(key=lambda x: x.get("score", 0), reverse=True)
                return top[:10]
            cands = await asyncio.to_thread(_f)
            self._results["candidates"] = cands
            return {"candidates": cands, "count": len(cands)}

        elif step == "download":
            cands = params.get("candidates") or self._results.get("candidates", [])
            chosen = cands[0] if cands else {}
            bvid = chosen.get("bvid")
            if not bvid:
                raise RuntimeError("no candidate bvid to download")
            work_dir = Path(params.get("work_dir", "/tmp/flow_fengge_work"))
            work_dir.mkdir(parents=True, exist_ok=True)

            def _d():
                _check()
                progress_cb(0.3, f"下载 {bvid}")
                out = self._mod.download_video(bvid, work_dir)
                _check()
                progress_cb(0.9, "完成")
                return out
            out = await asyncio.to_thread(_d)
            if not out:
                raise RuntimeError(f"download failed for {bvid}")
            self._results["raw_file"] = str(out)
            return {"bvid": bvid, "raw_file": str(out)}

        elif step == "crop":
            raw_file = params.get("raw_file") or self._results.get("raw_file")
            if not raw_file or not Path(raw_file).exists():
                raise RuntimeError("raw_file missing for crop")
            cropped = str(Path(raw_file).with_name(Path(raw_file).stem + "_cropped.mp4"))

            def _c():
                _check()
                progress_cb(0.2, "ffmpeg 裁剪")
                out = self._mod.crop_to_80(Path(raw_file), Path(cropped))
                _check()
                progress_cb(0.9, "完成")
                return out
            out = await asyncio.to_thread(_c)
            if not out:
                raise RuntimeError("crop failed")
            self._results["cropped_file"] = str(out)
            return {"cropped_file": str(out)}

        elif step == "generate_meta":
            title = params.get("title") or "峰哥切片"
            def _g():
                _check()
                progress_cb(0.3, "LLM 生成")
                desc, comment = self._mod.generate_desc_and_comment(title)
                return desc, comment
            desc, comment = await asyncio.to_thread(_g)
            return {"title": title, "description": desc, "comment": comment}

        elif step == "upload":
            cropped_file = params.get("cropped_file") or self._results.get("cropped_file")
            title = params.get("title", "峰哥切片")
            desc = params.get("description", "")
            if not cropped_file or not Path(cropped_file).exists():
                raise RuntimeError("cropped_file missing for upload")

            def _u():
                _check()
                progress_cb(0.3, "biliup 上传")
                res = self._mod.biliup_upload(cropped_file, title, desc)
                _check()
                progress_cb(0.9, "完成")
                return res
            res = await asyncio.to_thread(_u)
            new_bvid = res if isinstance(res, str) else None
            return {"uploaded": bool(new_bvid), "new_bvid": new_bvid, "raw": str(res)[:200]}

        raise ValueError(f"unhandled step {step}")