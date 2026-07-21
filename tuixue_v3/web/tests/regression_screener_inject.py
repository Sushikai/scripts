"""回测前后端验证 — 注入已完成结果渲染 UI (跳过 5min 实际跑)
原方案: 真实点击开始 → 等 SSE 完成 (300s+, 易 SSE 终态错误)
本方案: API 拉已完成 run_id → 直接调 btRenderV4 渲染 → 截图所有 section
"""
import asyncio, json, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
RUN_ID = "bt-1784355381-4732c9"  # 6 周期完整跑通, 186 笔, 已存 result
OUT = Path(f"/tmp/tuixue_bt_inject_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)


def fetch_result():
    raw = urllib.request.urlopen(f"{BASE}/api/screener/backtest?run_id={RUN_ID}").read()
    return json.loads(raw)


async def main():
    result_data = fetch_result()
    result = result_data['data']['result']
    print(f"fetched result: trades={result['summary']['trades']} months={len(result['monthly'])} scenarios={list(result['scenarios'].keys())[:8]}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        f5xx = []
        page.on("response", lambda r: f5xx.append(f"{r.status} {r.url}") if r.status >= 500 else None)

        print("STEP 1: 打开尾盘战法页")
        await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(".view-screener:not([hidden])", timeout=8000, state="visible")
        await asyncio.sleep(2)

        print("STEP 2: 展开回测面板")
        await page.click("#backtest-panel h3")
        await asyncio.sleep(1)

        print("STEP 3: 注入已完成的 result 进 UI")
        injected = await page.evaluate("""(result) => {
            try {
                // 标记 BT_RUN_ID 让前端知道有 run (可选, 防 cancel-btn 误显)
                window.BT_RUN_ID = 'bt-1784355381-4732c9';
                // 直接调 btRenderV4
                if (typeof btRenderV4 !== 'function') {
                    return {ok: false, error: 'btRenderV4 不存在 (可能脚本还没加载)'};
                }
                btRenderV4(result);
                // 隐藏 cancel (因为已经 done)
                const cancel = document.querySelector('#bt-cancel');
                if (cancel) cancel.hidden = true;
                const run = document.querySelector('#bt-run');
                if (run) run.disabled = false;
                const prog = document.querySelector('#bt-progress');
                if (prog) { prog.textContent = '完成 · 注入渲染'; prog.classList.remove('running'); }
                return {ok: true};
            } catch (e) {
                return {ok: false, error: e.message, stack: e.stack?.slice(0, 300)};
            }
        }""", result)
        print(f"  inject: {injected}")
        await asyncio.sleep(4)  # 让 ECharts 全部画完

        print("STEP 4: 探针 + 截图")
        probe = await page.evaluate("""() => {
            const root = document.querySelector('.view-screener');
            const text = root?.textContent || '';
            const kpis = [];
            for (const k of document.querySelectorAll('.bt-kpi, .kpi-grid .kpi, [class*="kpi-card"], .kpi')) {
                if (k.offsetWidth > 50 && k.offsetHeight > 30) {
                    kpis.push({cls: k.className.slice(0, 30), text: k.textContent.replace(/\\s+/g, ' ').trim().slice(0, 120)});
                }
            }
            const rows = [];
            for (const r of document.querySelectorAll('#bt-scenarios-9-host tr, .scenarios-host tr')) {
                if (r.offsetWidth > 0) rows.push(r.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200));
            }
            const m = [];
            for (const r of document.querySelectorAll('#bt-monthly tbody tr')) {
                if (r.offsetWidth > 0) m.push(r.textContent.replace(/\\s+/g, ' ').trim());
            }
            const exitBd = [];
            for (const r of document.querySelectorAll('#bt-exit-host tr, .exit-host tr')) {
                if (r.offsetWidth > 0) exitBd.push(r.textContent.replace(/\\s+/g, ' ').trim().slice(0, 150));
            }
            return {text_len: text.length, kpis: kpis.slice(0, 15), rows: rows.slice(0, 12), monthly: m.slice(0, 16), exit: exitBd.slice(0, 8)};
        }""")

        print(f"\n=== Probe (text_len={probe['text_len']}) ===")
        print("KPI cards:")
        for k in probe['kpis']:
            print(f"  [{k['cls'][:25]}] {k['text'][:110]}")
        print(f"\nScenarios ({len(probe['rows'])}):")
        for r in probe['rows']:
            print(f"  {r[:160]}")
        print(f"\nMonthly ({len(probe['monthly'])}):")
        for m in probe['monthly']:
            print(f"  {m[:90]}")
        print(f"\nExit breakdown ({len(probe['exit'])}):")
        for e in probe['exit']:
            print(f"  {e[:120]}")

        # 截图: 整页 + 各 section
        print("\nSTEP 5: 截图")
        await page.screenshot(path=str(OUT / "desktop_full.png"), full_page=True)
        await page.screenshot(path=str(OUT / "desktop_viewport.png"), full_page=False)
        for sel, name in [('.bt-kpi', 'kpi'), ('#bt-scenarios-9-host', 'scenarios'),
                          ('#bt-equity-chart', 'equity'), ('#bt-monthly', 'monthly'),
                          ('#bt-exit-host', 'exit'), ('#bt-windows-host', 'windows'),
                          ('#bt-sector-host', 'sector')]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    box = await el.bounding_box()
                    if box and box['width'] > 10 and box['height'] > 10:
                        await el.scroll_into_view_if_needed()
                        await asyncio.sleep(0.4)
                        await el.screenshot(path=str(OUT / f"section_{name}.png"))
                        print(f"  ✓ section_{name}.png ({int(box['width'])}x{int(box['height'])})")
            except Exception as e:
                print(f"  ✗ {sel}: {type(e).__name__}")

        print("\nSTEP 6: mobile")
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "mobile_full.png"), full_page=True)

        print(f"\n=== console errors: {len(errs)}, 5xx: {len(f5xx)} ===")
        for e in errs[:8]: print(f"  ERR: {e[:160]}")
        for f in f5xx[:5]: print(f"  5xx: {f[:120]}")
        print(f"\n截图: {OUT}")
        await browser.close()

asyncio.run(main())