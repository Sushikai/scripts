"""Batch 1 验证: 多层缓存 P50/P95"""
import asyncio, time, json
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")  # 先关 SW, 测纯 L1/L2
        page = await ctx.new_page()
        hits_log = []
        page.on("console", lambda m: hits_log.append(m.text) if "[cache-hits]" in m.text else None)
        errs = []
        page.on("pageerror", lambda e: errs.append(f"err: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)

        # ── 第 1 步: 打开 stock 002747 (L5 网络, L4 Redis 5s 命中)
        print("=== Try 1: 首次访问 002747 (L5 network) ===")
        t0 = time.time()
        await page.goto(f"{BASE}/?code=002747", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(".view-stock:not([hidden])", timeout=8000)
        await page.wait_for_function("window._currentStockCode === '002747'", timeout=8000)
        # 等 quote 渲染
        await page.wait_for_function("""() => {
          const el = document.querySelector('#q-price');
          return el && el.textContent && el.textContent.trim() !== '—' && el.textContent.trim() !== '';
        }""", timeout=10000)
        elapsed1 = (time.time() - t0) * 1000
        print(f"  cold → warm render: {elapsed1:.0f}ms")

        # ── 第 2 步: 切到 600519 再切回 002747 (测 L1 内存 + L2 sessionStorage)
        print("\n=== Try 2: 切换 002747 → 600519 → 002747 (测 L1 mem cache) ===")
        await page.evaluate("loadStockDetail('600519')")
        await page.wait_for_function("window._currentStockCode === '600519'", timeout=8000)
        await page.wait_for_timeout(800)  # 等渲染
        await page.evaluate("loadStockDetail('002747')")
        await page.wait_for_function("window._currentStockCode === '002747'", timeout=8000)
        await page.wait_for_timeout(500)
        elapsed2 = (time.time() - t0) * 1000
        print(f"  切回后: {elapsed2:.0f}ms (累计)")

        # ── 第 3 步: leave + return (测 L2 sessionStorage)
        print("\n=== Try 3: 离 view 再回来 (测 L2 sessionStorage) ===")
        await page.evaluate("showView('dash')")
        await page.wait_for_timeout(500)
        t3 = time.time()
        await page.evaluate("showView('stock'); loadStockDetail('002747')")
        await page.wait_for_function("window._currentStockCode === '002747'", timeout=8000)
        await page.wait_for_timeout(300)
        elapsed3 = (time.time() - t3) * 1000
        print(f"  leave → return: {elapsed3:.0f}ms")

        # ── 第 4 步: 5 次连击 002747 (测内存 LRU)
        print("\n=== Try 4: 5 次连续 loadStockDetail('002747') ===")
        times = []
        for i in range(5):
            t = time.time()
            await page.evaluate(f"loadStockDetail('002747')")
            await page.wait_for_timeout(150)
            times.append((time.time() - t) * 1000)
        print(f"  5次耗时: {[f'{t:.0f}ms' for t in times]}")
        print(f"  avg: {sum(times)/len(times):.0f}ms, min: {min(times):.0f}ms, max: {max(times):.0f}ms")

        # ── 离开 view 触发命中率日志
        print("\n=== 离 view 触发命中率日志 ===")
        await page.evaluate("showView('dash')")
        await page.wait_for_timeout(500)
        for h in hits_log[-3:]:
            print(f"  {h}")

        # ── 5) 5 股全 A 风向页 切换速度 (侧证)
        print("\n=== Try 5: 100 轮 ship 测试 — 5 股切换 ===")
        codes = ['002747', '600519', '000001', '300750', '601318']
        for c in codes:
            t = time.time()
            await page.evaluate(f"loadStockDetail('{c}')")
            await page.wait_for_function(f"window._currentStockCode === '{c}'", timeout=8000)
            await page.wait_for_timeout(300)
            ms = (time.time() - t) * 1000
            print(f"  → {c}: {ms:.0f}ms")

        await page.evaluate("showView('dash')")
        await page.wait_for_timeout(500)
        for h in hits_log[-5:]:
            print(f"  {h}")

        print(f"\n=== console errors: {len(errs)} ===")
        for e in errs[:3]: print(f"  {e[:120]}")

        await browser.close()

asyncio.run(main())