"""R201: mobile filter-bar padding-bottom 2→0 — 过滤条底部 padding 收紧

第一性原理: filter-bar pt=2 pb=2 h=36. chip h=32 + padding 2+2=36.
  跟 R195 sector-bar pt=0 + chip h=32 + pb=4=36 节奏不对称 — sector pb=4 是给下方呼吸,
  filter-bar 没有下方 bar 直接呼吸需求 (sector-bar 紧贴下沿).
  filter-bar pb 2→0 → chip 紧贴 filter-bar 下沿 (h=32+2=34), sector-bar 紧贴下沿 (sector-bar pb=4 给 sector-bar → pickCard 呼吸).
  filter-bar h 36→34 (-2px).
  chip tap zone h=32 不变, 仍 ≥32 Apple HIG 最低.

断言 (真实服务, 390px):
  1. filter-bar padding-bottom 0px (从 2px)
  2. filter-bar padding-top 2px 不变
  3. filter-bar h 34 (从 36)
  4. chip h 32 不变
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

        assert d['filterBar']['pb'] == '0px', f"R201: filterBar pb={d['filterBar']['pb']} != 0px"
        assert d['filterBar']['pt'] == '2px', f"R201: filterBar pt={d['filterBar']['pt']} 应仍 2px"
        assert d['filterBar']['h'] == 34, f"R201: filterBar h={d['filterBar']['h']} 应 == 34"
        assert d['chip']['h'] == 32, f"R201: chip h={d['chip']['h']} 应仍 32"

        await b.close()
        print(f"[OK] R201 filter-bar pb 2→0 — filter-bar 36→34 (-2px), 顶部两 bar 节奏 pt 2/pt 0 协调 ✓")

if __name__ == "__main__":
    asyncio.run(run())