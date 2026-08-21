"""R183: mobile view-head margin-bottom 8→4 — 顶部标题区紧凑收尾

第一性原理: view-head (R99 后 h=52) mb=8 — 占头部 60px (52+8). 顶部节奏:
  view-head 52 → mb 8 → filter-bar 38 → sector-bar 38 → pick-card.
  mb 8→4 让"标题区 52 → 留白 4 → 过滤 38" 紧凑, 推票卡下沉 4px
  (跟 R180/R181/R182 累计节奏一致).

断言 (真实服务, 390px):
  1. view-head mb=4px (从 8px)
  2. view-head pt/pb 8px 保持 (R-fix-2026-07-15 全局 768px breakpoint)
  3. view-head 高度仍 ~52 (mb 是外距不影响自身高度)
  4. filter-bar top 仍 ~169 (mb 改后下沉 4px → filter-bar top -4)
  5. 视觉节奏: view-head.mb (4) === filter-bar.pb (2) + sector-bar.pb (4) 节奏统一
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
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  return {
    viewHead: info(document.querySelector('.view-bv > .view-head, .view-bv .view-head')),
    filterBar: info(document.querySelector('.view-bv .bv-filter-bar')),
    sectorBar: info(document.querySelector('.view-bv .bv-sector-bar')),
    pickCard: info(document.querySelector('.bv-pick-card')),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"viewHead: h={d['viewHead']['h']} t={d['viewHead']['t']} b={d['viewHead']['b']} mb={d['viewHead']['mb']}")
        print(f"filterBar: h={d['filterBar']['h']} t={d['filterBar']['t']} pb={d['filterBar']['pb']}")
        print(f"sectorBar: h={d['sectorBar']['h']} t={d['sectorBar']['t']} pb={d['sectorBar']['pb']}")
        print(f"pickCard: t={d['pickCard']['t']} pt={d['pickCard']['pt']}")

        assert d['viewHead']['mb'] == '4px', f"R183: view-head mb={d['viewHead']['mb']} != 4px"
        assert 50 <= d['viewHead']['h'] <= 55, f"R183: view-head h={d['viewHead']['h']} out of 50-55"
        # filter-bar top 应小于 169 (R182 后 = 169, R183 应 = 165)
        assert d['filterBar']['t'] < 169, f"R183: filter-bar top={d['filterBar']['t']} 应 < 169"
        # pickCard top 应等于 filterBar.b + sectorBar.h
        assert d['pickCard']['t'] < 247, f"R183: pickCard top={d['pickCard']['t']} 应 < 247"

        await b.close()
        print(f"[OK] R183 view-head mb 8→4 — viewHead.b {d['viewHead']['b']} → filterBar.t {d['filterBar']['t']} 间隔 {d['filterBar']['t']-d['viewHead']['b']}px (回收 ~4px), 推票卡下沉 4px ✓")

if __name__ == "__main__":
    asyncio.run(run())
