"""R192: mobile view-head pt/pb 6→4 — 顶部标题区上下各收 2px

第一性原理: view-head padding 6/12 (R185 设, 覆盖 480px 全局 8/12) — pt 6 + pb 6 = 12 垂直 padding.
  filter-bar padding 4/0/2/0 (R182) — pt 4. sector-bar padding 2/0/4/0 (R181) — pb 4.
  view-head pt 6 比 filter-bar pt 4 多 2, 不一致. pt 6→4 让顶部三段 pt 节奏统一 4-4-4.
  pb 6→4 让 view-head 总高 48→44 (-4px) — 推票卡更早进首屏.
  刷新按钮 tap zone 不受影响 (refresh-btn 仍 h=32, view-actions pb=4 由 view-head pt/pb 决定但 refresh-btn padding 仍独立).

断言 (真实服务, 390px):
  1. view-head padding-top 4px (从 6px)
  2. view-head padding-bottom 4px (从 6px)
  3. view-head padding-left/right 12px 不变
  4. view-head h 44 (从 48)
  5. refresh-btn h 32 不变
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
  var vh = document.querySelector('.view-bv .view-head');
  var refresh = document.querySelector('.view-bv .view-head .btn-refresh');
  return {vh: info(vh), refresh: info(refresh)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']} pt={d['vh']['pt']} pb={d['vh']['pb']} pl={d['vh']['pl']} pr={d['vh']['pr']}")
        print(f"refresh: h={d['refresh']['h']}")

        assert d['vh']['pt'] == '4px', f"R192: vh pt={d['vh']['pt']} != 4px"
        assert d['vh']['pb'] == '4px', f"R192: vh pb={d['vh']['pb']} != 4px"
        assert d['vh']['pl'] == '12px', f"R192: vh pl={d['vh']['pl']} != 12px"
        assert d['vh']['pr'] == '12px', f"R192: vh pr={d['vh']['pr']} != 12px"
        assert d['vh']['h'] == 44, f"R192: vh h={d['vh']['h']} 应 == 44 (回收 4px)"
        assert d['refresh']['h'] == 32, f"R192: refresh h={d['refresh']['h']} 应仍 32"

        await b.close()
        print(f"[OK] R192 view-head pt/pb 6→4 — view-head 48→44 (-4px), 顶部三段 pt 节奏统一 4-4-4 ✓")

if __name__ == "__main__":
    asyncio.run(run())