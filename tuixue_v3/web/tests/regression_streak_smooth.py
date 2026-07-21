"""streak 10d 优化验证 — 视觉 + 缓存 + 波动可见性"""
import asyncio, json, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path(f"/tmp/tuixue_streak_v2_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        # 抓 /api/stock/.../intraday 请求
        intraday_reqs = []
        page.on("request", lambda req: intraday_reqs.append(f"{req.method} {req.url}") if "/intraday" in req.url else None)

        print("STEP 1: 打开个股页 (002747)")
        t0 = time.time()
        await page.goto(f"{BASE}/?code=002747", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(".view-stock:not([hidden])", timeout=8000, state="visible")

        # 等 streak 10d 渲染 (异步数据加载需要 10-15s)
        print("  等 streak 10d 渲染...")
        await page.wait_for_function("""() => {
            return document.querySelectorAll('[data-streak-date]').length > 0;
        }""", timeout=20000)
        elapsed_init = int((time.time() - t0) * 1000)
        print(f"  初始渲染: {elapsed_init}ms")
        await page.screenshot(path=str(OUT / "01_initial.png"), full_page=True)

        # 等预缓存完成 (10 天 × 250ms = 2.5s + 400ms 启动延迟 = ~3s)
        print("STEP 2: 等预缓存后台跑完")
        await asyncio.sleep(4)
        reqs_after_pref = len(intraday_reqs)
        print(f"  预缓存请求数: {reqs_after_pref}")
        for r in intraday_reqs[:15]:
            print(f"    {r[:120]}")

        # 截图 streak 区域
        streak_host = page.locator("#q-streak-10d").first
        if await streak_host.count() > 0:
            await streak_host.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await streak_host.screenshot(path=str(OUT / "02_streak_host.png"))

        # 抓格子颜色样本
        cell_samples = await page.evaluate("""() => {
            const cells = document.querySelectorAll('[data-streak-date]');
            const out = [];
            for (const c of cells) {
                const cs = getComputedStyle(c);
                out.push({
                    date: c.dataset.streakDate,
                    bg: cs.backgroundColor,
                    fg: cs.color,
                    text: c.textContent.trim(),
                });
            }
            return out;
        }""")
        print(f"\n=== 格子颜色 ({len(cell_samples)}) ===")
        for c in cell_samples:
            print(f"  {c['date']} | {c['text'][:30]} | bg={c['bg']} fg={c['fg']}")

        print("\nSTEP 3: 点第 1 个格子,验证秒开 (期望 < 100ms)")
        # 找 streak 第一格
        first_cell = page.locator('[data-streak-date]').first
        first_date = await first_cell.get_attribute('data-streak-date')
        print(f"  点 {first_date}")
        # 抓点击前缓存
        cache_before = await page.evaluate("() => window.intraDayCache ? window.intraDayCache.size : -1")
        print(f"  点击前 cache size: {cache_before}")

        t_click = time.time()
        await first_cell.click()
        # 等分时图渲染
        await page.wait_for_selector(".tab[data-tab='intraday'].active, .chart-tab[data-tab='intraday'].active", timeout=3000)
        # 等 chart canvas 出现
        await page.wait_for_function("""() => {
            const dom = document.querySelector('#intra-day-chart');
            return dom && dom.querySelector('canvas');
        }""", timeout=5000)
        click_elapsed = int((time.time() - t_click) * 1000)
        print(f"  点击到图表渲染: {click_elapsed}ms {'✓ 秒开' if click_elapsed < 500 else '✗ 慢'}")

        # 截图分时图 (验证 smooth:false + 包络带)
        await asyncio.sleep(2)
        intraday_chart = page.locator("#intra-day-chart").first
        if await intraday_chart.count() > 0:
            await intraday_chart.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await intraday_chart.screenshot(path=str(OUT / "03_intraday_chart.png"))
            box = await intraday_chart.bounding_box()
            print(f"  分时图: {int(box['width'])}x{int(box['height'])}")

        # STEP 4: 点另一个格子 (验证缓存 + 同样秒开)
        print("\nSTEP 4: 点第 3 个格子 (不同日期, 测切换)")
        cells = page.locator('[data-streak-date]')
        n = await cells.count()
        if n >= 3:
            third = cells.nth(2)
            third_date = await third.get_attribute('data-streak-date')
            print(f"  点 {third_date}")
            t3 = time.time()
            await third.click()
            await page.wait_for_function(f"""() => {{
                const note = document.querySelector('#intra-day-note');
                return note && (note.textContent || '').includes('{third_date}');
            }}""", timeout=5000)
            t3_elapsed = int((time.time() - t3) * 1000)
            print(f"  切换到 {third_date}: {t3_elapsed}ms")
            await asyncio.sleep(2)
            await intraday_chart.screenshot(path=str(OUT / "04_intraday_chart_v2.png"))

        # STEP 5: mobile
        print("\nSTEP 5: mobile")
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "05_mobile_full.png"), full_page=True)
        if await streak_host.count() > 0:
            await streak_host.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await streak_host.screenshot(path=str(OUT / "06_mobile_streak.png"))

        print(f"\n=== console errors: {len(errs)}, 总 intraday 请求: {len(intraday_reqs)} ===")
        for e in errs[:5]: print(f"  ERR: {e[:120]}")
        print(f"\n截图: {OUT}")
        await browser.close()

asyncio.run(main())