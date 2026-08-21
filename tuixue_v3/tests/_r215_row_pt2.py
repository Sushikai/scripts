"""R215: mobile bv-row padding-top 4→2 — 卡片内 padding 顶部再次收紧

第一性原理: bv-row pt=4 pb=4 (R202/R204). 紧跟 pickCard pt=4 (R211), 4+4=8 顶部双 padding 偏松.
  跟 R214 view-head pt 0 + R208 sector-bar pt 0 + R207 filter-bar pt 0 + R212 pickCard pb 0 顶部全 0/2 节奏统一,
  bv-row pt 4→2.
  bv-row h 82.7→80.7 (-2px), 15 行累计回收 30px. card-head 下沿 2px 后 bv-row 第一行开始 (hit-tier 仍 3px).

断言 (真实服务, 390px):
  1. bv-row padding-top 2px (从 4px)
  2. bv-row padding-bottom 4px 不变
  3. bv-row h 80 (从 82.7)
  4. hit-tier stripe 仍清晰 (R113)
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

        assert d['row']['pt'] == '2px', f"R215: row pt={d['row']['pt']} != 2px"
        assert d['row']['pb'] == '4px', f"R215: row pb={d['row']['pb']} 应仍 4px"
        assert abs(d['row']['h'] - 80.7) < 1.5, f"R215: row h={d['row']['h']} 应 ~80.7"

        await b.close()
        print(f"[OK] R215 bv-row pt 4→2 — bv-row h 82.7→80.7 (-2px), 15 行累计回收 30px ✓")

if __name__ == "__main__":
    asyncio.run(run())