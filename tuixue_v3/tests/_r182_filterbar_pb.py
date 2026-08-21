"""R182: mobile filter-bar padding-bottom 4→2 — 顶部过滤条 pb 紧凑收尾

第一性原理: filter-bar padding 4+4=8px 包 32px chip — 25% 高度是 padding, 跟 R181
  改后的 sector-bar (2+4=6px, 19%) 不一致. filter-bar pb 4→2 跟 sector-bar pt 2 一致,
  让过滤条总高 40→38, 跟 sector-bar 38 完全等高 — 顶部双条形成"过滤 38 + 板块 38"
  完美对称节奏, 首屏再回收 2px.

断言 (真实服务, 390px):
  1. filter-bar pb=2px (从 4px)
  2. filter-bar pt=4px 保持 (R103 设的)
  3. filter-bar 总高 38 (从 40)
  4. chip h 仍 32
  5. sector-bar 38 不受影响
  6. 视觉节奏: filter-bar.h === sector-bar.h (38 === 38)
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
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var filterChip = document.querySelector('.view-bv .bv-filter-bar .bv-filter-chip');
  var sectorPill = document.querySelector('.view-bv .bv-sector-bar .bv-sector-pill');
  var fCS = filterBar ? getComputedStyle(filterBar) : null;
  var sCS = sectorBar ? getComputedStyle(sectorBar) : null;
  return {
    filterBar: filterBar ? r(filterBar) : null,
    filterPad: fCS ? {pt:fCS.paddingTop, pb:fCS.paddingBottom, pl:fCS.paddingLeft, pr:fCS.paddingRight} : null,
    filterChip: filterChip ? r(filterChip) : null,
    sectorBar: sectorBar ? r(sectorBar) : null,
    sectorPad: sCS ? {pt:sCS.paddingTop, pb:sCS.paddingBottom} : null,
    sectorPill: sectorPill ? r(sectorPill) : null,
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"filterBar h={d['filterBar']['h']} pt={d['filterPad']['pt']} pb={d['filterPad']['pb']}")
        print(f"  chip h={d['filterChip']['h']}")
        print(f"sectorBar h={d['sectorBar']['h']} pt={d['sectorPad']['pt']} pb={d['sectorPad']['pb']}")
        print(f"  pill h={d['sectorPill']['h']}")

        assert d['filterPad']['pb'] == '2px', f"R182: filter-bar pb={d['filterPad']['pb']} != 2px"
        assert d['filterPad']['pt'] == '4px', f"R182: filter-bar pt={d['filterPad']['pt']} != 4px"
        assert d['filterBar']['h'] == 38, f"R182: filter-bar h={d['filterBar']['h']} != 38"
        assert d['filterChip']['h'] == 32, f"R182: chip h={d['filterChip']['h']} != 32"
        # sector-bar 不变 (R181 后 38)
        assert d['sectorBar']['h'] == 38, f"R182: sector-bar h={d['sectorBar']['h']} != 38"
        # 等高节奏 ✓
        assert d['filterBar']['h'] == d['sectorBar']['h'], f"R182: 节奏不一致 {d['filterBar']['h']} vs {d['sectorBar']['h']}"

        await b.close()
        print(f"[OK] R182 filter-bar pb 4→2 — 总高 40→38px (跟 sector-bar 38 等高), "
              f"chip h=32 保留, 顶部双条 38/38 等高节奏 ✓")

if __name__ == "__main__":
    asyncio.run(run())
