"""R241: mobile burst 列 fw 紧跟 row 2 节奏 — 炸板字号 11→10.5

第一性原理: bv-row burst 列 (td:nth-child(9) grid-area:burst) 现在 fs 11.
  跟 R236-R238 row 2 节奏统一, burst fs 11→10.5.
  burst 数据性质跟 sector/turnover/streak/seal/time 一样次要.

断言 (真实服务, 390px):
  1. burst fs 10.5px (从 11px)
  2. bv-row h 不变
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    return {fs: cs.fontSize};
  }
  var burst = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(9)');
  return {burst: info(burst)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"burst: fs={d['burst']['fs']}")

        assert d['burst']['fs'] == '10.5px', f"R241: burst fs={d['burst']['fs']} 应 10.5px"

        await b.close()
        print(f"[OK] R241 burst fs 11→10.5 — 炸板字号统一 row 2 ✓")

if __name__ == "__main__":
    asyncio.run(run())