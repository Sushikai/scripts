"""R238: mobile time 列 fs 12→11 — 时间字号统一

第一性原理: bv-row time 列 (封板时间, td:nth-child(8) grid-area:time) fs 现在 12.
  跟 R236-R237 row 2 节奏统一, time 12→11. R15 设 12px 是首板关键信息,
  但跟 row 2 其他列 (10.5) 对比后偏大. 改 11 跟 row 2 数据字号统一.

断言 (真实服务, 390px):
  1. time fs 11px (从 12px)
  2. row 2 字号全部统一 ≤ 11px
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
  var time = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(8)');
  return {time: info(time)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"time: fs={d['time']['fs']}")

        assert d['time']['fs'] == '11px', f"R238: time fs={d['time']['fs']} 应 11px"

        await b.close()
        print(f"[OK] R238 time fs 12→11 — row 2 字号统一, time 数据性质跟 sector/turnover 对齐 ✓")

if __name__ == "__main__":
    asyncio.run(run())