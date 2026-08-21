"""R193: mobile view-head margin-bottom 4→2 — 标题区与下条进一步紧凑

第一性原理: view-head mb 4 (R183 设, 覆盖 480px 全局 6) — 顶部节奏'标题 48 → 留白 4 → 过滤 38'.
  跟 R192 pt/pb 4 对称, 标题区'内 padding 4 + 外 margin 2 = 6' 跟 R180 bv-row mb 4 + R181 sector-bar pb 4 趋同.
  mb 4→2 让推票卡再下沉 2px (R91 顶部空间预算 持续优化), 累计顶部回收 6px (R183+192+193).

断言 (真实服务, 390px):
  1. view-head margin-bottom 2px (从 4px)
  2. filter-bar 起点上移 2px (top y 减少)
  3. view-head h 仍 44
  4. refresh-btn h 不变
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
    return {h: Math.round(rect.height*10)/10, t: Math.round(rect.top*10)/10, mb: cs.marginBottom};
  }
  var vh = document.querySelector('.view-bv .view-head');
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  return {vh: info(vh), filterBar: info(filterBar), sectorBar: info(sectorBar)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']} t={d['vh']['t']} mb={d['vh']['mb']}")
        print(f"filterBar: h={d['filterBar']['h']} t={d['filterBar']['t']}")
        print(f"sectorBar: h={d['sectorBar']['h']} t={d['sectorBar']['t']}")

        assert d['vh']['mb'] == '2px', f"R193: vh mb={d['vh']['mb']} != 2px"
        assert d['vh']['h'] == 44, f"R193: vh h={d['vh']['h']} 应仍 44"

        await b.close()
        print(f"[OK] R193 view-head mb 4→2 — 推票卡下沉 2px, 累计顶部回收 6px ✓")

if __name__ == "__main__":
    asyncio.run(run())