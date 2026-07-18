"""Batch 1 SW 层验证 — 启用 SW, 第二次访问 /full 走 SW 缓存"""
import asyncio, time
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})  # 不 block SW
        page = await ctx.new_page()

        # 等 SW 激活
        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_function("""async () => {
          const reg = await navigator.serviceWorker.getRegistration();
          return reg && reg.active && reg.active.state === 'activated';
        }""", timeout=10000)
        print("SW activated ✓")

        # 第 1 次访问 002747 /full (L5 network, server 端 Redis 5s 命中)
        # 走 SW fetch handler,SW 不命中(空)→ 网络 → 写 cache
        print("\n=== Try 1: 首次 /full (SW miss → network → write cache) ===")
        t = time.time()
        await page.evaluate("""async () => {
          const r = await fetch('/api/stock/002747/full');
          return await r.json();
        }""")
        print(f"  /full: {(time.time()-t)*1000:.0f}ms")

        # 第 2 次: SW 命中(5min 内)
        print("\n=== Try 2: 立即重试 /full (SW hit) ===")
        t = time.time()
        r = await page.evaluate("""async () => {
          const r = await fetch('/api/stock/002747/full');
          return { status: r.status, headers: [...r.headers] };
        }""")
        print(f"  /full: {(time.time()-t)*1000:.0f}ms status={r['status']}")

        # 第 3 次: 验证 SW 返回的 cache 真的在
        print("\n=== Try 3: 通过 SW 内 caches.match 直接拿 ===")
        cache_info = await page.evaluate("""async () => {
          const cache = await caches.open('tuixue-v3-shell-v96');
          const matched = await cache.match('/api/stock/002747/full');
          return matched ? { ok: true, status: matched.status, bodyLen: (await matched.clone().text()).length } : { ok: false };
        }""")
        print(f"  SW cache: {cache_info}")

        await browser.close()

asyncio.run(main())