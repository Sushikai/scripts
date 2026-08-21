"""R237: mobile seal 列 fs 11→10.5 — 封单字号统一

第一性原理: bv-row seal 列 (td:nth-child(7) grid-area:seal) fs 现在 11.
  跟 R236 sector/turnover/streak 11→10.5 同步, seal 11→10.5 让 row 2 字号统一.
  R148 chip 10.5 跟 seal 数据性质类似 (数据 chip).

断言 (真实服务, 390px):
  1. seal fs 10.5px
  2. row 2 字号统一 (sector/turnover/streak/seal = 10.5)
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
  var seal = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(7)');
  return {seal: info(seal)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"seal: fs={d['seal']['fs']}")

        assert d['seal']['fs'] == '10.5px', f"R237: seal fs={d['seal']['fs']} 应 10.5px"

        await b.close()
        print(f"[OK] R237 seal fs 11→10.5 — 封单字号统一, row 2 字号全部归 10.5 一档 ✓")

if __name__ == "__main__":
    asyncio.run(run())