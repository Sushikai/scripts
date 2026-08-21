"""R245 prep: 探针顶部 203px 的构成 — 每块元素高度, 找冗余

第一性原理: 首屏 844px 中 top 区吃 203px (24%), 只为 8 张卡的扫描服务.
  顶区是"导航", 卡片是"内容" — 导航不该占内容的 1/4.
  本探针逐块量高, 找出还能压的冗余.
"""
import asyncio, json
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
  var out = {};
  var nodes = ['view-bv > .view-head', '.view-bv .bv-header', '.view-bv .bv-filter-bar',
              '.view-bv .bv-sector-bar', '.view-bv .bv-pick-head',
              '.view-bv .bv-meta', '.view-bv .bv-title', '.view-bv .bv-pick-card'];
  nodes.forEach(function(sel){
    var el = document.querySelector(sel);
    if (!el) { out[sel] = 'MISSING'; return; }
    var r = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    out[sel] = {top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height),
                mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom};
  });
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(600)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            if isinstance(v, dict):
                print(f"{k}\n  top={v['top']} bottom={v['bottom']} h={v['h']} mb={v['mb']} pt={v['pt']} pb={v['pb']}")
            else:
                print(f"{k}: {v}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
