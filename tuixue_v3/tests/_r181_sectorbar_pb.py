"""R181: mobile sector-bar padding-bottom 8→4 — 板块条 padding 收紧收尾

第一性原理: sector-bar padding 2+8=10px 包 32px pill — 31% 高度是 padding,
  过度留白. 8px pb 跟 R179 bv-row (6px pb) + R180 margin-bottom (4px) 节奏不一致.
  4px pb 跟全局视觉节奏 (4-6-6) 一致. sector-bar 总高 42→38, 跟 filter-bar 40 形成
  等高节奏, 首屏顶部回收 4px → 推票卡下沉 4px.

断言 (真实服务, 390px):
  1. sector-bar pb=4px (从 8px)
  2. sector-bar pt=2px 保持
  3. sector-bar 总高 38 (从 42)
  4. pill h 仍 32 (内部不变)
  5. filter-bar 40 不受影响
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
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorPill = document.querySelector('.view-bv .bv-sector-bar .bv-sector-pill');
  var filterChip = document.querySelector('.view-bv .bv-filter-bar .bv-filter-chip');
  var sCS = sectorBar ? getComputedStyle(sectorBar) : null;
  var fCS = filterBar ? getComputedStyle(filterBar) : null;
  return {
    sectorBar: sectorBar ? r(sectorBar) : null,
    sectorPad: sCS ? {pt:sCS.paddingTop, pb:sCS.paddingBottom} : null,
    sectorPill: sectorPill ? r(sectorPill) : null,
    filterBar: filterBar ? r(filterBar) : null,
    filterPad: fCS ? {pt:fCS.paddingTop, pb:fCS.paddingBottom} : null,
    filterChip: filterChip ? r(filterChip) : null,
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"sectorBar h={d['sectorBar']['h']} pt={d['sectorPad']['pt']} pb={d['sectorPad']['pb']}")
        print(f"  pill h={d['sectorPill']['h']}")
        print(f"filterBar h={d['filterBar']['h']} pt={d['filterPad']['pt']} pb={d['filterPad']['pb']}")
        print(f"  chip h={d['filterChip']['h']}")

        assert d['sectorPad']['pb'] == '4px', f"R181: sector-bar pb={d['sectorPad']['pb']} != 4px"
        assert d['sectorPad']['pt'] == '2px', f"R181: sector-bar pt={d['sectorPad']['pt']} != 2px"
        assert 36 <= d['sectorBar']['h'] <= 40, f"R181: sector-bar h={d['sectorBar']['h']} out of 36-40"
        assert d['sectorPill']['h'] == 32, f"R181: pill h={d['sectorPill']['h']} != 32"
        # filter-bar 不变 (R103 设的 38 + chip 32 + pad 4/4 = 40)
        assert d['filterBar']['h'] == 40, f"R181: filter-bar h changed {d['filterBar']['h']} != 40"

        await b.close()
        print(f"[OK] R181 sector-bar pb 8→4 — 总高 42→{d['sectorBar']['h']}px (回收 ~4px), "
              f"pill h=32 保留, filter-bar 40 不变 ✓")

if __name__ == "__main__":
    asyncio.run(run())
