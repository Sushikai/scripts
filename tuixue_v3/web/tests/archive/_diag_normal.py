"""诊断脚本: 跑 normal 场景但全程 capture console + network"""
import asyncio
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path("/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/diag")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    msgs = []
    reqs = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text[:300]}"))
        page.on("pageerror", lambda e: msgs.append(f"[pageerror] {e}"))
        page.on("request", lambda r: reqs.append(f"{r.method} {r.url}") if "/api/screener/backtest" in r.url else None)
        page.on("response", lambda r: reqs.append(f"< {r.status} {r.url}") if "/api/screener/backtest" in r.url else None)
        page.on("websocket", lambda ws: msgs.append(f"[ws] {ws.url}"))
        page.on("requestfailed", lambda r: msgs.append(f"[reqfail] {r.url} {r.failure}"))

        # Go + clear LS + reload
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.evaluate("""async () => {
            if (navigator.serviceWorker) {
                const regs = await navigator.serviceWorker.getRegistrations();
                for (const r of regs) { try { await r.unregister(); } catch {} }
            }
            localStorage.clear();
        }""")
        await ctx.clear_cookies()
        await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded")
        await page.wait_for_selector('.view-screener:not([hidden])', timeout=10000)
        await page.evaluate("document.getElementById('backtest-panel')?.classList.remove('collapsed')")

        # 设置参数
        await page.evaluate("""() => {
            const f = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
            f('bt-sample', '500');
            document.querySelectorAll('input[name="bt-p"]').forEach(cb => { cb.checked = cb.value === '半年'; });
        }""")

        print("→ Click bt-run")
        await page.click('#bt-run')
        # Capture state every 5s for 60s
        for i in range(12):
            await asyncio.sleep(5)
            state = await page.evaluate("""() => {
                const t = (id) => document.getElementById(id)?.textContent?.trim().slice(0,80) || null;
                return {
                    elapsed: t('bt-elapsed'),
                    progress: t('bt-progress'),
                    run_btn_disabled: !!document.querySelector('#bt-run')?.disabled,
                    run_id_in_dom: !!(window.btRunId || document.body.dataset.runId),
                    has_kpi: !!document.getElementById('bt-kpis')?.innerHTML.trim(),
                };
            }""")
            print(f"  +{(i+1)*5}s  elapsed={state['elapsed']!r}  progress={state['progress']!r}  btn_disabled={state['run_btn_disabled']}")
            if state.get('has_kpi'):
                print(f"  ✓ KPI 已渲染, 退出")
                break

        await page.screenshot(path=str(OUT / f"diag_{int(time.time())}.png"))

        await browser.close()

    print("\n=== console msgs (last 30) ===")
    for m in msgs[-30:]:
        print(m)
    print("\n=== api/screener/backtest reqs ===")
    for r in reqs:
        print(r)


asyncio.run(main())