"""R194: mobile filter-bar pt 4→2 — 顶部过滤条与标题间距节奏

第一性原理: filter-bar pt 4 (R103 设) — chip h=32 + pt=4 + pb=2 = 38 高.
  view-head pt 4 + mb 2 (R192+R193) = 6 上下节奏. filter-bar pt 4 比它内部需要的还多 2.
  pt 4→2 让 filter-bar 总高 38→36 (-2px), 跟 sector-bar 38 等高不再 (sector 仍 38).
  filter chip tap zone 不受影响 (chip h=32 独立, pt 不影响热区).

断言 (真实服务, 390px):
  1. filter-bar padding-top 2px (从 4px)
  2. filter-bar padding-bottom 2px 不变
  3. filter-bar h 36 (从 38)
  4. filter chip h 32 不变
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
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var chip = document.querySelector('.view-bv .bv-filter-chip');
  return {filterBar: info(filterBar), chip: info(chip)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"filterBar: h={d['filterBar']['h']} pt={d['filterBar']['pt']} pb={d['filterBar']['pb']}")
        print(f"chip: h={d['chip']['h']}")

        assert d['filterBar']['pt'] == '2px', f"R194: filterBar pt={d['filterBar']['pt']} != 2px"
        assert d['filterBar']['pb'] == '2px', f"R194: filterBar pb={d['filterBar']['pb']} 应仍 2px"
        assert d['filterBar']['h'] == 36, f"R194: filterBar h={d['filterBar']['h']} 应 == 36"
        assert d['chip']['h'] == 32, f"R194: chip h={d['chip']['h']} 应仍 32"

        await b.close()
        print(f"[OK] R194 filter-bar pt 4→2 — filter-bar 38→36 (-2px), 顶部链再紧凑 ✓")

if __name__ == "__main__":
    asyncio.run(run())