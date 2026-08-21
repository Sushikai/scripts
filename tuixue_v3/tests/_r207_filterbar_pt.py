"""R207: mobile filter-bar padding-top 2→0 — 过滤条顶部 padding 收紧

第一性原理: filter-bar pt=2 pb=0 (R201), chip h=32.
  跟 R195 sector-bar pt 0 节奏统一 (顶部两 bar 都是 pt=0 紧贴上沿), pt 2→0.
  filter-bar 34→32 (-2px). view-head pb 2 + filter-bar pt 0 = 2px 间距, 视觉节奏可接受.

断言 (真实服务, 390px):
  1. filter-bar padding-top 0px (从 2px)
  2. filter-bar padding-bottom 0px 不变
  3. filter-bar h 32 (从 34)
  4. chip h 32 不变 (R106 tap zone)
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
  var fb = document.querySelector('.view-bv .bv-filter-bar');
  var chip = document.querySelector('.view-bv .bv-filter-chip');
  return {fb: info(fb), chip: info(chip)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"fb: h={d['fb']['h']} pt={d['fb']['pt']} pb={d['fb']['pb']}")
        print(f"chip: h={d['chip']['h']}")

        assert d['fb']['pt'] == '0px', f"R207: fb pt={d['fb']['pt']} != 0px"
        assert d['fb']['pb'] == '0px', f"R207: fb pb={d['fb']['pb']} 应仍 0px"
        assert abs(d['fb']['h'] - 32) < 1.5, f"R207: fb h={d['fb']['h']} 应 ~32"
        assert d['chip']['h'] == 32, f"R207: chip h={d['chip']['h']} 应仍 32"

        await b.close()
        print(f"[OK] R207 filter-bar pt 2→0 — filter-bar 34→32 (-2px) 顶部两 bar pt=0 节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())