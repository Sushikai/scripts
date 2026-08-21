#!/usr/bin/env python3
"""逐 view 巡检：每个 view 切过去 5s,收 console error / network failed / KPI 空白 / screenshot。"""
import asyncio
import json
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
VIEWS = [
    "dash", "stock", "watchlist", "dragons", "sector",
    "all_stocks", "screener", "review", "weekly_bull",
    "ai-review", "strategy_picker", "optimize", "laws", "sources",
]
OUT = Path(__file__).parent
REPORT = OUT / "audit_report.json"


async def audit_view(page, view, idx):
    await page.evaluate(f"showView('{view}')")
    await page.wait_for_timeout(5000)
    result = {"view": view, "errors": [], "warnings": [], "net_failed": [], "empty_kpi": [], "screenshot": f"{idx:02d}-{view}.png"}
    # 截图
    await page.screenshot(path=str(OUT / result["screenshot"]), full_page=False)
    # console errors
    return result


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        all_errors, all_warnings, all_failed = [], [], []
        view_data = {}

        page.on("console", lambda m: (
            all_errors.append({"view": view_data.get("cur", "?"), "text": m.text}) if m.type == "error"
            else (all_warnings.append({"view": view_data.get("cur", "?"), "text": m.text}) if m.type == "warning" else None)
        ))
        page.on("requestfailed", lambda req: all_failed.append({
            "view": view_data.get("cur", "?"), "url": req.url, "err": req.failure,
        }))

        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        for idx, view in enumerate(VIEWS):
            view_data["cur"] = view
            print(f"[{idx+1}/{len(VIEWS)}] auditing {view}...", flush=True)
            try:
                await page.evaluate(f"typeof showView === 'function' ? showView('{view}') : null")
                await page.wait_for_timeout(5000)
                # 抓空白 KPI
                empty_kpi = await page.evaluate("""
                    () => {
                        const sels = ['.kpi-value', '.value', '.num', '.metric', '[data-kpi]'];
                        const out = [];
                        for (const s of sels) {
                            document.querySelectorAll(s).forEach(el => {
                                const t = (el.innerText || '').trim();
                                if ((t === '—' || t === '' || t === 'NaN' || t === 'undefined') &&
                                    el.getBoundingClientRect().height > 0 && el.offsetParent !== null) {
                                    out.push({sel: s, text: t, html: el.outerHTML.slice(0, 200)});
                                }
                            });
                        }
                        return out.slice(0, 20);
                    }
                """)
                await page.screenshot(path=str(OUT / f"{idx:02d}-{view}.png"), full_page=False)
                view_data[view] = {"empty_kpi": empty_kpi, "errors": len(all_errors), "warnings": len(all_warnings)}
            except Exception as e:
                view_data[view] = {"error": str(e)[:200]}

        report = {
            "ts": time.time(),
            "total_errors": len(all_errors),
            "total_warnings": len(all_warnings),
            "total_failed": len(all_failed),
            "errors_by_view": {},
            "warnings_by_view": {},
            "failed_by_view": {},
            "per_view": view_data,
        }
        for e in all_errors:
            report["errors_by_view"].setdefault(e["view"], []).append(e["text"][:300])
        for w in all_warnings:
            report["warnings_by_view"].setdefault(w["view"], []).append(w["text"][:300])
        for f in all_failed:
            report["failed_by_view"].setdefault(f["view"], []).append(f["url"])

        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n=== 汇总 ===")
        print(f"errors={len(all_errors)} warnings={len(all_warnings)} failed={len(all_failed)}")
        print(f"per view:")
        for v in VIEWS:
            d = view_data.get(v, {})
            print(f"  {v:18s} err={d.get('errors',0):3d} warn={d.get('warnings',0):3d} empty_kpi={len(d.get('empty_kpi', []))}")
        print(f"\n报告: {REPORT}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())