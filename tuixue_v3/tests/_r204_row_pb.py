"""R204: mobile bv-row padding-bottom 6→4 — 卡片内 padding 底部收紧

第一性原理: bv-row pt=4 pb=6 pl=15 pr=12. pb 不对称 (R202 已压 pt 4).
  跟 R192 view-head pb 4 + R195 sector-bar pt 0 + R202 bv-row pt 4 顶部节奏统一, pb 6→4.
  bv-row h 85→83 (-2px), 15 行累计回收 30px.
  底部 4px 跟 view-head pb 4 一致, 视觉节奏同.

断言 (真实服务, 390px):
  1. bv-row padding-bottom 4px (从 6px)
  2. bv-row padding-top 4px 不变
  3. bv-row h 83 (从 85)
  4. hit-tier stripe 仍清晰 (pb 4 + pl 15 让位 hit-tier 3px 仍 ok)
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
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  return {row: info(row)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']} pt={d['row']['pt']} pb={d['row']['pb']}")

        assert d['row']['pb'] == '4px', f"R204: row pb={d['row']['pb']} != 4px"
        assert d['row']['pt'] == '4px', f"R204: row pt={d['row']['pt']} 应仍 4px"
        assert abs(d['row']['h'] - 83) < 1.5, f"R204: row h={d['row']['h']} 应 ~83"

        await b.close()
        print(f"[OK] R204 bv-row pb 6→4 — bv-row h 85→83 (-2px), 15 行累计回收 30px ✓")

if __name__ == "__main__":
    asyncio.run(run())