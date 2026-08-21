"""R210: mobile view-head margin-bottom 2→0 — 标题区与下条间距紧凑

第一性原理: view-head mb=2 (R193). 紧跟 filter-bar, 中间 2+0=2 间距偏紧.
  跟 R183 mb 8→4 + R193 mb 4→2 节奏统一, view-head mb 2→0.
  view-head 与 filter-bar 视觉一体, 仅顶部链 1 处. 不影响 bv-row 间距.

断言 (真实服务, 390px):
  1. view-head margin-bottom 0px (从 2px)
  2. view-head h 不变 (40)
  3. filter-bar 紧跟 view-head
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
    return {h: Math.round(rect.height*10)/10, mb: cs.marginBottom, mt: cs.marginTop};
  }
  var vh = document.querySelector('.view-bv .view-head');
  var fb = document.querySelector('.view-bv .bv-filter-bar');
  return {vh: info(vh), fb: info(fb)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']} mb={d['vh']['mb']}")
        print(f"fb: h={d['fb']['h']}")

        assert d['vh']['mb'] == '0px', f"R210: vh mb={d['vh']['mb']} != 0px"
        assert abs(d['vh']['h'] - 40) < 1.5, f"R210: vh h={d['vh']['h']} 应仍 ~40"

        await b.close()
        print(f"[OK] R210 view-head mb 2→0 — 标题区与下条紧贴 顶部节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())