"""回测完成后前后端验证 — 渲染回测结果 + 视觉对比"""
import asyncio, json, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
RUN_ID = "bt-1784355381-4732c9"
OUT = Path(f"/tmp/tuixue_bt_check_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        f5xx = []
        page.on("response", lambda r: f5xx.append(f"{r.status} {r.url}") if r.status >= 500 else None)

        # 1) 进入尾盘战法页 + 直接加载这个 run
        url = f"{BASE}/?view=screener&run_id={RUN_ID}"
        print(f"GET {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_selector(".view-screener:not([hidden])", timeout=8000, state="visible")
        except Exception as e:
            print(f"WARN: view 容器等不到: {e}")
        await asyncio.sleep(5)  # 让 ECharts + KPI 全部完成

        await page.screenshot(path=str(OUT / "desktop_full.png"), full_page=False)

        # 2) DOM 探针 — 验证关键数据出现在页面上
        probe = await page.evaluate("""() => {
            const root = document.querySelector('.view-screener');
            if (!root) return {view_present: false};
            const text = root.textContent || '';
            // 抓 KPI 数字 / scenario 名
            const findText = (substr) => text.includes(substr);
            return {
                view_visible: !root.hidden,
                text_len: text.length,
                has_trail_80: findText('trail_80') || findText('trail80'),
                has_water_avg: findText('water_avg') || findText('waterAvg'),
                has_force_close: findText('force_close') || findText('forceClose'),
                has_summary: findText('胜率') || findText('月度') || findText('盈亏'),
                pct_count: (text.match(/[+-]?\\d+\\.?\\d*%/g) || []).length,
                button_count: root.querySelectorAll('button').length,
                chart_count: root.querySelectorAll('canvas, [_echarts_instance_]').length,
                table_rows: root.querySelectorAll('table tbody tr').length,
                title_text: (document.querySelector('.view-screener h2, .view-screener h1, .view-screener .card-h') || {}).textContent || '',
                sample_of_text: text.slice(0, 400),
            };
        }""")
        print("\n=== DOM probe ===")
        print(json.dumps(probe, ensure_ascii=False, indent=2))

        # 3) 截图关键 section
        sections = await page.evaluate("""() => {
            const r = [];
            for (const sel of ['#bt-kpi', '.bt-kpi', '.kpi-grid', '#bt-equity', '.bt-equity', '#bt-monthly', '.bt-monthly', '#bt-scenarios', '.bt-scenarios']) {
                const el = document.querySelector(sel);
                if (el) r.push({sel, rect: el.getBoundingClientRect(), text: el.textContent.slice(0, 100)});
            }
            return r;
        }""")
        print("\n=== Sections ===")
        for s in sections:
            print(f"  {s['sel']}: rect={s['rect']}, text={s['text'][:60]}")

        # 4) 截图重点 KPI 区
        for sel, name in [('.bt-kpi', 'kpi'), ('.bt-scenarios', 'scenarios'), ('.bt-equity', 'equity'), ('.bt-monthly', 'monthly')]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.screenshot(path=str(OUT / f"section_{name}.png"))
                    print(f"  截图: section_{name}.png")
            except Exception as e:
                print(f"  跳过 {sel}: {e}")

        # 5) mobile
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "mobile_full.png"), full_page=False)
        print("  截图: mobile_full.png")

        print(f"\n=== Console errors: {len(errs)}, 5xx: {len(f5xx)} ===")
        for e in errs[:5]: print(f"  ERR: {e[:120]}")
        for f in f5xx[:3]: print(f"  5xx: {f[:120]}")

        print(f"\n截图目录: {OUT}")
        await browser.close()

asyncio.run(main())