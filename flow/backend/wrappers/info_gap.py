"""信息差流水线 wrapper:7 步包装 info_gap_pipeline 的类。

dry_run=True 时跳过外部 API/ffmpeg,只返回 mock 产物,给测试用。
生产模式直接 import 信息差模块,调对应方法。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.info_gap")

# info_gap_pipeline 路径
INFO_GAP_PKG = Path("/Users/kaikai/scripts/info_gap_pipeline").resolve()
SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()


class InfoGapWrapper(ToolWrapper):
    tool_id = "info_gap"
    name = "信息差流水线"
    description = "7 步:研究→脚本→配音→素材→合成→风格对比→上传 B 站。"
    steps = ["research", "script", "voice", "materials", "compose", "style_diff", "upload"]

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._pipeline = None
        self._results: dict = {}

    def _ensure_imported(self):
        """懒加载 info_gap_pipeline 包(避免每次 import 开销)。"""
        if self._pipeline is not None:
            return
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        if str(INFO_GAP_PKG) not in sys.path:
            sys.path.insert(0, str(INFO_GAP_PKG))
        try:
            from info_gap_pipeline.main import InfoGapPipeline
            self._pipeline_class = InfoGapPipeline
            _logger.info("info_gap_pipeline loaded from %s", INFO_GAP_PKG)
        except ImportError as e:
            _logger.error("failed to import info_gap_pipeline: %s", e)
            self._pipeline_class = None

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
        log_cb(f"info_gap/{step} starting dry_run={self.dry_run}")

        if self.dry_run:
            return await self._run_dry(step, params, progress_cb, log_cb, is_cancelled)

        # 生产模式:走真实流水线
        self._ensure_imported()
        if self._pipeline_class is None:
            raise RuntimeError("info_gap_pipeline not importable")
        return await self._run_real(step, params, progress_cb, log_cb, is_cancelled)

    async def _run_dry(self, step, params, progress_cb, log_cb, is_cancelled):
        """dry-run:模拟每步产物,不调外部依赖。"""
        step_durations = {
            "research": 0.3,
            "script": 0.5,
            "voice": 0.8,
            "materials": 0.6,
            "compose": 0.4,
            "style_diff": 0.2,
            "upload": 0.3,
        }
        secs = step_durations.get(step, 0.3)
        n = 4
        for i in range(n):
            if is_cancelled():
                raise RuntimeError("cancelled")
            await asyncio.sleep(secs / n)
            progress_cb((i + 1) / n, f"{step} {i+1}/{n}")
        artifact = f"/tmp/flow_info_gap_{step}_output.txt"
        with open(artifact, "w") as f:
            f.write(f"dry-run output for step={step} params={params}\n")
        return {"output": artifact, "step": step, "dry_run": True}

    async def _run_real(self, step, params, progress_cb, log_cb, is_cancelled):
        """真实模式:import InfoGapPipeline 并执行对应 step。

        注意:原流水线是同步的,我们在 to_thread 里跑避免阻塞事件循环。
        """
        self._ensure_imported()
        from datetime import datetime
        pipe = self._pipeline_class(date=datetime.now())

        def _check():
            if is_cancelled():
                raise RuntimeError("cancelled")

        if step == "research":
            def _r():
                _check()
                topics = pipe._step_research()
                _check()
                return topics
            topics = await asyncio.to_thread(_r)
            return {"topics": topics[:7] if topics else [], "count": len(topics or [])}

        elif step == "script":
            topics = params.get("topics") or self._results.get("research_topics", [])
            def _s():
                _check()
                scripts = pipe._step_script_gen(topics)
                return scripts
            scripts = await asyncio.to_thread(_s)
            return {"scripts": scripts[:3] if scripts else []}

        elif step == "voice":
            scripts = params.get("scripts") or self._results.get("script_scripts", [])
            def _v():
                _check()
                paths = pipe._step_voiceover(scripts)
                return paths
            paths = await asyncio.to_thread(_v)
            return {"audio_paths": paths[:3] if paths else []}

        elif step == "materials":
            scripts = params.get("scripts") or self._results.get("script_scripts", [])
            def _d():
                _check()
                paths = pipe._step_download(scripts)
                return paths
            paths = await asyncio.to_thread(_d)
            return {"video_paths": paths[:3] if paths else []}

        elif step == "compose":
            video_paths = params.get("video_paths") or []
            audio_paths = params.get("audio_paths") or []
            scripts = params.get("scripts") or []
            def _c():
                _check()
                out = pipe._step_compile(video_paths, audio_paths, scripts)
                return out
            out = await asyncio.to_thread(_c)
            return {"final_video": str(out)}

        elif step == "style_diff":
            video_path = params.get("video_path")
            def _sd():
                _check()
                res = pipe._step_compare_with_reference(video_path)
                return res
            res = await asyncio.to_thread(_sd)
            return {"comparison": res}

        elif step == "upload":
            video_path = params.get("video_path")
            script = params.get("script", {})
            def _u():
                _check()
                avid = pipe._step_upload(video_path, script)
                return avid
            avid = await asyncio.to_thread(_u)
            return {"avid": avid}

        raise ValueError(f"unhandled step {step}")