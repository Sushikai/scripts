#!/usr/bin/env python3
"""SW 关 vs 开 — 验证 SW 是否 view-stock.js 失败的根因"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def run(label, disable_sw, rounds=5):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        results = []
        for i in range(rounds):
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                service_workers="block" if disable_sw else "allow",
            )
            page = await ctx.new_page()

            failures = []
            page.on("response", lambda r: failures.append({"url": r.url, "status": r.status}) if r.status >= 400 else None)
            page.on("requestfailed", lambda r: failures.append({
                "url": r.url, "reason": str(r.failure)[:80]
            }))

            await page.goto(BASE, wait_until="commit", timeout=60000)
            await page.wait_for_timeout(4000)
            await page.evaluate("""
                () => {
                    const tk = document.querySelector('.tk-clickable');
                    if (tk) tk.click();
                }
            """)
            await page.wait_for_timeout(7000)
            view_state = await page.evaluate("() => ({view: _currentViewName, visible: document.querySelector('.view:not([hidden])')?.dataset?.view})")
            vs_fail = [f for f in failures if 'view-stock' in f.get('url', '')]
            results.append((view_state, len(vs_fail)))
            print(f"[{label} round {i+1}] view_state={view_state} view-stock failures: {len(vs_fail)}")
            for f in vs_fail[:2]:
                print(f"    {f}")
            await ctx.close()
        await browser.close()
        return results


async def main():
    print("=== SW BLOCK (simulate no-SW) ===")
    await run("no-SW", disable_sw=True)
    print("\n=== SW ALLOW ===")
    await run("SW", disable_sw=False)


if __name__ == "__main__":
    asyncio.run(main())