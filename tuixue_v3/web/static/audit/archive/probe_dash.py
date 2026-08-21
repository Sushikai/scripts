#!/usr/bin/env python3
"""深入查 dash 问题：把 dash 单独抓 30s,收集每个 API 的 request+response"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent
REPORT = OUT / "dash_probe.json"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        api_log = []
        page.on("response", lambda r: api_log.append({
            "url": r.url,
            "status": r.status,
            "ms": int(time.time() * 1000),
            "ok": r.ok,
        }))
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error", "warning") else None)

        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 单独切到 dash
        await page.evaluate("showView('dash')")
        await page.wait_for_timeout(8000)

        # 抓 dom 状态
        sigs = await page.evaluate("""
            () => {
                const get = (id) => {
                    const el = document.getElementById(id);
                    if (!el) return null;
                    return { id, text: el.innerText.trim().slice(0, 100), html: el.innerHTML.slice(0, 200) };
                };
                return {
                    a:  get('sig-a-verdict'),
                    a_pct: get('sig-a-pct'),
                    a_head: get('sig-a-head'),
                    kr: get('sig-kr-verdict'),
                    kr_pct: get('sig-kr-pct'),
                    kr_head: get('sig-kr-head'),
                    us: get('sig-us-verdict'),
                    us_pct: get('sig-us-pct'),
                    us_head: get('sig-us-head'),
                    hot_tiles: document.getElementById('hot-sectors-tiles')?.innerHTML?.length || 0,
                    hot_sub: document.getElementById('hot-sectors-sub')?.textContent?.trim() || '',
                    ticker_count: document.querySelectorAll('.tk-item').length,
                };
            }
        """)
        await page.screenshot(path=str(OUT / "dash_only.png"), full_page=False)
        print("=== DOM ===")
        print(json.dumps(sigs, ensure_ascii=False, indent=2))
        print("\n=== Console errors/warnings ===")
        for e in err_log[:30]:
            print(f"[{e['type']}] {e['text']}")
        print("\n=== API calls (dash 期间) ===")
        # 只看 dash 相关的
        dash_apis = [a for a in api_log if "/api/" in a["url"]]
        for a in dash_apis:
            ok = "✓" if a["ok"] else "✗"
            print(f"  {ok} {a['status']}  {a['url'].replace(BASE, '')}")
        print(f"\nTotal API calls: {len(api_log)}, errors: {sum(1 for a in dash_apis if not a['ok'])}")
        REPORT.write_text(json.dumps({
            "dom": sigs,
            "err_log": err_log,
            "api_log": api_log,
        }, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())