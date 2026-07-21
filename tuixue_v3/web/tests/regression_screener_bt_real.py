"""回测前后端验证 — 真实流程: 打开 → 展开回测面板 → 配置 → 开始 → 等完成 → 截图"""
import asyncio, json, time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path(f"/tmp/tuixue_bt_real_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type in ("error", "warning", "log") else None)
        f5xx = []
        page.on("response", lambda r: f5xx.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        # R100: 抓所有 /api/screener/backtest 请求
        bt_reqs = []
        page.on("request", lambda req: bt_reqs.append(f"{req.method} {req.url}") if "/api/screener/backtest" in req.url else None)
        bt_resps = []
        page.on("response", lambda r: bt_resps.append(f"{r.status} {r.url}") if "/api/screener/backtest" in r.url else None)

        print("STEP 1: 打开尾盘战法页")
        await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(".view-screener:not([hidden])", timeout=8000, state="visible")
        await asyncio.sleep(2)

        print("STEP 2: 展开回测面板 (点 #backtest-panel h3)")
        await page.click("#backtest-panel h3")
        await asyncio.sleep(1)
        await page.screenshot(path=str(OUT / "01_after_expand.png"), full_page=False)

        # 验证按钮可见
        is_visible = await page.is_visible("#bt-run")
        print(f"  #bt-run visible: {is_visible}")

        print("STEP 3: 设 sample=300 + 勾选所有 periods")
        await page.evaluate("""() => {
            const sample = document.querySelector('#bt-sample');
            if (sample) { sample.value = '300'; sample.dispatchEvent(new Event('change', {bubbles:true})); }
            // 确保所有 period 都勾上 (默认只有半年)
            for (const cb of document.querySelectorAll('input[name="bt-p"]')) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', {bubbles:true}));
            }
        }""")

        print("STEP 4: 点 #bt-run")
        await page.click("#bt-run")
        t_start = time.time()

        print("STEP 5: 等回测完成")
        done = False
        last_prog = ""
        while time.time() - t_start < 360:
            await asyncio.sleep(3)
            state = await page.evaluate("""() => ({
                progress_text: document.querySelector('#bt-progress')?.textContent || '',
                run_disabled: document.querySelector('#bt-run')?.disabled,
                cancel_hidden: document.querySelector('#bt-cancel')?.hidden,
            })""")
            elapsed = int(time.time() - t_start)
            prog = state['progress_text'][:50]
            if prog != last_prog:
                print(f"  [{elapsed:3d}s] {prog} (run.disabled={state['run_disabled']})")
                last_prog = prog
            if state['run_disabled'] is False and state['cancel_hidden'] is True and ('完成' in state['progress_text'] or '失败' in state['progress_text']):
                done = True
                break

        print(f"完成: {done} (用时 {int(time.time()-t_start)}s)")
        await asyncio.sleep(4)

        print("STEP 6: 截图")
        await page.screenshot(path=str(OUT / "02_desktop_full.png"), full_page=True)
        await page.screenshot(path=str(OUT / "02_desktop_viewport.png"), full_page=False)

        # KPI + scenarios + monthly 抓取
        probe = await page.evaluate("""() => {
            const text = document.querySelector('.view-screener')?.textContent || '';
            const kpis = [];
            for (const k of document.querySelectorAll('.kpi, .bt-kpi, [class*="kpi-card"]')) {
                if (k.offsetWidth > 0 && k.offsetHeight > 0) kpis.push(k.textContent.replace(/\\s+/g,' ').trim().slice(0, 100));
            }
            const rows = [];
            for (const r of document.querySelectorAll('#bt-scenarios-9-host tr, table tbody tr')) {
                if (r.offsetWidth > 0) rows.push(r.textContent.replace(/\\s+/g,' ').trim().slice(0, 150));
            }
            const m = [];
            for (const r of document.querySelectorAll('#bt-monthly tbody tr')) {
                if (r.offsetWidth > 0) m.push(r.textContent.replace(/\\s+/g,' ').trim());
            }
            return {kpis, rows: rows.slice(0, 15), monthly: m.slice(0, 16), text_len: text.length};
        }""")

        print(f"\n=== Probe (text_len={probe['text_len']}) ===")
        print("KPI cards:")
        for k in probe['kpis'][:12]:
            print(f"  {k[:100]}")
        print(f"\nScenario rows ({len(probe['rows'])}):")
        for r in probe['rows'][:10]:
            print(f"  {r[:140]}")
        print(f"\nMonthly ({len(probe['monthly'])}):")
        for m in probe['monthly'][:14]:
            print(f"  {m[:90]}")

        print("\nSTEP 7: 关键 section 截图")
        for sel, name in [('.bt-kpi', 'kpi'), ('#bt-scenarios-9-host', 'scenarios'), ('#bt-equity-chart', 'equity'), ('#bt-monthly', 'monthly'), ('#bt-exit-host', 'exit')]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    box = await el.bounding_box()
                    if box and box['width'] > 0:
                        await el.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        await el.screenshot(path=str(OUT / f"section_{name}.png"))
                        print(f"  ✓ {name}")
                    else:
                        print(f"  ✗ {name} 不可见")
            except Exception as e:
                print(f"  ✗ {sel}: {type(e).__name__}")

        print("\nSTEP 8: mobile")
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "03_mobile_full.png"), full_page=True)

        print(f"\n=== console errors: {len(errs)}, 5xx: {len(f5xx)} ===")
        for e in errs[:8]: print(f"  ERR: {e[:160]}")
        for f in f5xx[:5]: print(f"  5xx: {f[:120]}")
        print(f"\n=== BT API requests: {len(bt_reqs)} ===")
        for r in bt_reqs[:10]: print(f"  {r[:140]}")
        print(f"\n=== BT API responses: {len(bt_resps)} ===")
        for r in bt_resps[:10]: print(f"  {r[:140]}")
        print(f"\n截图: {OUT}")
        await browser.close()

asyncio.run(main())