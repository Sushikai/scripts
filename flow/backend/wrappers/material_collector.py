"""素材采集库 wrapper。

包装 material_collector 包:web 爬取(抖音/B 站/小红书)/ADB/处理(去重+打标+OCR)/导出到 flow.assets。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from .registry import ToolWrapper

_logger = logging.getLogger("flow.wrappers.material_collector")

MAT_PKG = Path("/Users/kaikai/scripts/material_collector").resolve()
SCRIPTS_ROOT = Path("/Users/kaikai/scripts").resolve()


class MaterialCollectorWrapper(ToolWrapper):
    tool_id = "material_collector"
    name = "素材采集库"
    description = "多源(抖音/B站/小红书/ADB)→ 去重+打标 → flow.assets 资产库。"
    steps = ["web_collect", "adb_collect", "process", "export_assets"]

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._mod = None
        self._db = None
        self._results: dict = {}

    def _ensure_imported(self):
        if self._mod is not None:
            return
        if str(SCRIPTS_ROOT) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_ROOT))
        if str(MAT_PKG) not in sys.path:
            sys.path.insert(0, str(MAT_PKG))
        try:
            import collector.collector_core as core  # noqa: F401
            import database.materials_db as db  # noqa: F401
            self._mod = core
            self._db = db
            _logger.info("material_collector loaded from %s", MAT_PKG)
        except ImportError as e:
            _logger.error("failed to import material_collector: %s", e)

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
        log_cb(f"material_collector/{step} starting dry_run={self.dry_run}")

        if self.dry_run:
            return await self._run_dry(step, params, progress_cb, log_cb, is_cancelled)
        self._ensure_imported()
        if self._mod is None:
            raise RuntimeError("material_collector not importable")
        return await self._run_real(step, params, progress_cb, log_cb, is_cancelled)

    async def _run_dry(self, step, params, progress_cb, log_cb, is_cancelled):
        step_durations = {
            "web_collect": 0.5,
            "adb_collect": 0.5,
            "process": 0.4,
            "export_assets": 0.3,
        }
        secs = step_durations.get(step, 0.3)
        n = 4
        for i in range(n):
            if is_cancelled():
                raise RuntimeError("cancelled")
            await asyncio.sleep(secs / n)
            progress_cb((i + 1) / n, f"{step} {i+1}/{n}")
        artifact = f"/tmp/flow_material_collector_{step}_output.txt"
        with open(artifact, "w") as f:
            f.write(f"dry-run output for step={step} params={params}\n")
        return {"output": artifact, "step": step, "dry_run": True}

    async def _run_real(self, step, params, progress_cb, log_cb, is_cancelled):
        def _check():
            if is_cancelled():
                raise RuntimeError("cancelled")

        if step == "web_collect":
            platforms = params.get("platforms", ["douyin", "bilibili"])
            keyword = params.get("keyword", "热门")
            count_per = int(params.get("count_per_platform", 10))

            def _w():
                _check()
                results = []
                for i, plat in enumerate(platforms):
                    _check()
                    progress_cb((i + 0.5) / len(platforms), f"爬 {plat}")
                    try:
                        collector = self._mod.create_collector(plat, keyword)
                        items = collector.collect(count=count_per)
                    except Exception as e:
                        _logger.warning("collect %s failed: %s", plat, e)
                        items = []
                    for it in items:
                        results.append({
                            "platform": plat,
                            "keyword": keyword,
                            "raw_text": getattr(it, "text", ""),
                            "video_title": getattr(it, "title", ""),
                            "video_bvid": getattr(it, "bvid", ""),
                            "source_url": getattr(it, "url", ""),
                            "timestamp": getattr(it, "timestamp", ""),
                        })
                return results
            items = await asyncio.to_thread(_w)
            self._results["raw_items"] = items
            return {"items": items[:50], "count": len(items)}

        elif step == "adb_collect":
            keyword = params.get("keyword", "热门")
            def _a():
                _check()
                progress_cb(0.3, "ADB 模拟点击")
                # ADB 慢且易失败,只跑 5 个示意
                items = []
                try:
                    from collector.adb_controller import AdbController
                    adb = AdbController()
                    for i in range(5):
                        _check()
                        text = adb.scrape_current_subtitle()
                        items.append({"platform": "adb", "keyword": keyword, "raw_text": text or ""})
                except Exception as e:
                    _logger.warning("adb collect failed: %s", e)
                return items
            items = await asyncio.to_thread(_a)
            existing = self._results.get("raw_items", [])
            self._results["raw_items"] = existing + items
            return {"items": items, "count": len(items)}

        elif step == "process":
            raw = params.get("items") or self._results.get("raw_items", [])
            if not raw:
                return {"processed": [], "count": 0}
            def _p():
                _check()
                processed = []
                seen_hash = set()
                from database.materials_db import get_database
                db = get_database()
                for i, item in enumerate(raw):
                    _check()
                    text = item.get("raw_text", "")
                    h = str(hash(text)) if text else f"empty-{i}"
                    if h in seen_hash:
                        continue
                    seen_hash.add(h)
                    progress_cb((i + 1) / max(len(raw), 1), f"处理 {i+1}/{len(raw)}")
                    try:
                        pid = db.insert_material({
                            "platform": item.get("platform", "unknown"),
                            "keyword": item.get("keyword", ""),
                            "raw_text": text,
                            "video_title": item.get("video_title", ""),
                            "video_bvid": item.get("video_bvid", ""),
                            "source_url": item.get("source_url", ""),
                            "timestamp": item.get("timestamp", ""),
                            "content_hash": h,
                        })
                        processed.append({"id": pid, "platform": item.get("platform"), "hash": h})
                    except Exception as e:
                        _logger.warning("insert material failed: %s", e)
                return processed
            processed = await asyncio.to_thread(_p)
            self._results["processed"] = processed
            return {"processed": processed, "count": len(processed)}

        elif step == "export_assets":
            processed = params.get("processed") or self._results.get("processed", [])
            # 把 processed 写一份进 flow.assets(JSON dump)
            import json
            out_path = Path(params.get("out_path", "/tmp/flow_material_assets.json"))
            def _e():
                _check()
                progress_cb(0.3, f"导出 {len(processed)} 条")
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(processed, f, ensure_ascii=False, indent=2)
                _check()
                progress_cb(0.9, "完成")
                return str(out_path)
            path = await asyncio.to_thread(_e)
            return {"exported_path": path, "count": len(processed)}

        raise ValueError(f"unhandled step {step}")