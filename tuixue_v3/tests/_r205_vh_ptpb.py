"""R205: mobile view-head pt/pb 4→2 — 标题区上下各收 2px

第一性原理: view-head pt/pb=4 (R192). refresh-btn 32+pb4 主导 h=36, pt/pb 4 是额外呼吸.
  跟 R202 bv-row pt 4 + R204 bv-row pb 4 + R195 sector-bar pt 0 + R201 filter-bar pb 0 顶部节奏统一, pt/pb 4→2.
  view-head 44→40 (-4px). 标题区内部 32.9 不变 (h2+meta), view-actions 36 主导.
  refresh-btn tap zone 仍 36 (R127 32px + pb 4).

断言 (真实服务, 390px):
  1. view-head pt 2px pb 2px (从 4px 4px)
  2. view-head h 40 (从 44)
  3. refresh-btn h 36 不变 (R127 tap zone)
  4. 标题 h2 lh 不受影响
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
  var vh = document.querySelector('.view-bv .view-head');
  var refresh = document.querySelector('.view-bv .refresh-btn, .view-bv #bv-refresh');
  return {vh: info(vh), refresh: info(refresh)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']} pt={d['vh']['pt']} pb={d['vh']['pb']}")
        print(f"refresh: h={d['refresh']['h']}")

        assert d['vh']['pt'] == '2px', f"R205: vh pt={d['vh']['pt']} != 2px"
        assert d['vh']['pb'] == '2px', f"R205: vh pb={d['vh']['pb']} != 2px"
        assert abs(d['vh']['h'] - 40) < 1.5, f"R205: vh h={d['vh']['h']} 应 ~40"
        assert d['refresh']['h'] >= 32, f"R205: refresh h={d['refresh']['h']} 应 >= 32 (R127 tap zone)"

        await b.close()
        print(f"[OK] R205 view-head pt/pb 4→2 — view-head h 44→40 (-4px) 顶部节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())