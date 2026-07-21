"""Batch 3 /core 实测 — 详细看 console + 时间"""
import asyncio, time
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        logs = []
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text[:200]}"))
        page.on("pageerror", lambda e: logs.append(f"PAGE_ERR: {e}"))

        # 先导航触发 SW 激活
        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # 测 /core 端点直接调用 (绕过 UI)
        print("=== 直接调 /api/stock/002747/core ===")
        result = await page.evaluate("""async (base) => {
          const t0 = performance.now();
          const r = await fetch(base + '/api/stock/002747/core');
          const t1 = performance.now();
          const j = await r.json();
          return {
            network_ms: t1 - t0,
            status: r.status,
            has_data: !!(j && j.data),
            has_quote: !!(j && j.data && j.data.quote),
            price: j && j.data && j.data.quote && j.data.quote.price,
            ok: j && j.ok,
          };
        }""", BASE)
        print(f"  {result}")

        # 模拟 loadStockDetail cold path
        print("\n=== 模拟 loadStockDetail 完整流程 ===")
        # 等 SW 激活
        await page.goto(f"{BASE}/?code=002747", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_function("window._currentStockCode === '002747'", timeout=10000)

        # 等 /core 渲染完成 (Phase 1)
        print("\n=== 等 #q-price 填值 (Phase 1 触发点) ===")
        t = time.time()
        await page.wait_for_function("""() => {
          const el = document.querySelector('#q-price');
          if (!el) return false;
          const txt = (el.textContent || '').trim();
          return txt && txt !== '—' && txt !== '';
        }""", timeout=10000)
        print(f"  Phase 1 渲染完成: {(time.time()-t)*1000:.0f}ms")

        # 等完整 (Phase 2)
        await page.wait_for_timeout(3000)
        all_logs = [l for l in logs if 'cache' in l.lower() or 'core' in l.lower() or 'patch' in l.lower() or 'sse' in l.lower() or 'stock' in l.lower() or 'err' in l.lower()]
        print(f"\n=== 相关 logs ({len(all_logs)}) ===")
        for l in all_logs[-20:]:
            print(f"  {l}")

        await browser.close()

asyncio.run(main())