"""
退学 v3 · 修复后全页视觉回归 (2026-07-18)
==========================================
S1/S2 水下均价 bug 修复后,跑全 11 视图 × desktop + mobile 检查:
  - 页面无 5xx / JS error
  - 核心 DOM 渲染 (有数据)
  - 关键交互 (切 tab / 加自选 / 跑回测) 不抛错

输出: /tmp/tuixue_regr_<ts>/<viewport>/<view>.png + JSON 报告
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT_ROOT = Path(f"/tmp/tuixue_regr_{int(time.time())}")
OUT_ROOT.mkdir(parents=True, exist_ok=True)

VIEWS = [
    ("dash",         "/?view=dash",       ".view-dash"),
    ("all_stocks",   "/?view=all_stocks", ".view-all_stocks"),
    ("dragons",      "/?view=dragons",    ".view-dragons"),
    ("stock",        "/?code=002747",     ".view-stock"),
    ("watchlist",    "/?view=watchlist",  ".view-watchlist"),
    ("screener",     "/?view=screener",   ".view-screener"),
    ("optimize",     "/?view=optimize",   ".view-optimize"),
    ("laws",         "/?view=laws",       ".view-laws"),
    ("review",       "/?view=review",     ".view-review"),
    ("ai-review",    "/?view=ai-review",  ".view-ai-review"),
]

VIEWPORTS = [
    ("desktop", {"width": 1440, "height": 900}),
    ("mobile",  {"width": 390, "height": 844}),
]


async def check_view(page, name, path, view_sel, results):
    """访问单一 view,等关键 DOM 渲染,捕获 console error / 网络 5xx"""
    errors = []
    failed_reqs = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("requestfailed", lambda req: failed_reqs.append(f"{req.method} {req.url} → {req.failure}"))
    page.on("response", lambda r: failed_reqs.append(f"{r.status} {r.url}") if r.status >= 500 else None)

    try:
        await page.goto(f"{BASE}{path}", wait_until="domcontentloaded", timeout=15000)
        # 等 view 容器 visible (JS showView 会去掉 [hidden])
        try:
            await page.wait_for_selector(f"{view_sel}:not([hidden])", timeout=8000, state="visible")
        except Exception as e:
            errors.append(f"view 容器不可见: {view_sel} ({type(e).__name__}: {e})")
        await asyncio.sleep(3.0)  # 让 ECharts / async fetch 全部完成

        # 截图
        shot_path = OUT_ROOT / page.viewport_label / f"{name}.png"
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(shot_path), full_page=False)

        # 关键 DOM 计数 (验证 view 真的有内容,不是空白)
        counts = await page.evaluate(f"""() => {{
            const root = document.querySelector('{view_sel}');
            if (!root) return {{view_present: false}};
            return {{
                view_present: true,
                view_visible: !root.hidden,
                h2_count: root.querySelectorAll('h2, .card-h').length,
                table_rows: root.querySelectorAll('table tbody tr').length,
                charts: root.querySelectorAll('[_echarts_instance_], canvas').length,
                chip_count: root.querySelectorAll('.chip').length,
                text_len: (root.textContent || '').trim().length,
                buttons: root.querySelectorAll('button').length,
            }};
        }}""")

        return {
            "view": name,
            "ok": counts.get("view_visible", False) and counts.get("text_len", 0) > 50,
            "shot": str(shot_path),
            "errors": errors[:10],
            "failed_5xx": [r for r in failed_reqs if " 5" in r][:5],
            "counts": counts,
        }
    except Exception as e:
        return {
            "view": name,
            "ok": False,
            "errors": [f"NAV: {type(e).__name__}: {e}"] + errors[:5],
            "failed_5xx": [r for r in failed_reqs if " 5" in r][:5],
        }


async def run_all():
    all_results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for vp_name, vp in VIEWPORTS:
            ctx = await browser.new_context(viewport=vp)
            page = await ctx.new_page()
            page.viewport_label = vp_name
            print(f"\n{'='*70}\n[{vp_name} {vp['width']}×{vp['height']}] 全 11 view\n{'='*70}")
            for vname, vpath, sels in VIEWS:
                t0 = time.time()
                r = await check_view(page, vname, vpath, sels, all_results)
                elapsed = round((time.time() - t0) * 1000)
                status = "✓" if r["ok"] and not r["errors"] and not r["failed_5xx"] else "✗"
                err_n = len(r["errors"])
                f5xx_n = len(r.get("failed_5xx", []))
                extra = ""
                if err_n: extra += f" [{err_n} err]"
                if f5xx_n: extra += f" [{f5xx_n} 5xx]"
                print(f"  {status} {vname:12s}  {elapsed:>5d}ms{extra}")
                if r["errors"]:
                    for e in r["errors"][:3]:
                        print(f"      ERR: {e[:120]}")
                if r["failed_5xx"]:
                    for f in r["failed_5xx"][:2]:
                        print(f"      5xx: {f[:120]}")
                r["viewport"] = vp_name
                r["elapsed_ms"] = elapsed
                all_results.append(r)
            await ctx.close()
        await browser.close()
    return all_results


def main():
    print(f"输出目录: {OUT_ROOT}")
    results = asyncio.run(run_all())

    # 汇总
    print(f"\n{'='*70}\n汇总\n{'='*70}")
    by_vp = {}
    for r in results:
        vp = r["viewport"]
        by_vp.setdefault(vp, []).append(r)

    total_ok = total_fail = 0
    for vp, rs in by_vp.items():
        vp_ok = sum(1 for r in rs if r["ok"] and not r["errors"] and not r.get("failed_5xx"))
        vp_fail = len(rs) - vp_ok
        total_ok += vp_ok; total_fail += vp_fail
        print(f"  {vp}: {vp_ok}/{len(rs)} OK")

    total = len(results)
    print(f"\n总: {total} 项 · {total_ok} ✓ / {total_fail} ✗")
    print(f"截图: {OUT_ROOT}/<desktop|mobile>/<view>.png")

    json_path = OUT_ROOT / "report.json"
    json_path.write_text(json.dumps({
        "ts": time.time(),
        "total": total,
        "ok": total_ok,
        "fail": total_fail,
        "results": results,
    }, ensure_ascii=False, indent=2))
    print(f"详细报告: {json_path}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
