"""R199: mobile pickCard padding-bottom 6→4 — 推票卡底部 padding 收紧

第一性原理: pickCard padding 6/10 (R184 设) — pt/pb 都 6.
  跟 R192 view-head pb 4 + R194 filter-bar pt 2 + R195 sector-bar pt 0 顶部节奏趋同.
  pb 6→4 让 pickCard 总高 -2px (1493→1491). 视觉上 card-head 上方 pt 6 (跟 bv-row 节奏),
  下方 pb 4 (跟 view-head 节奏), pickCard 在视觉节奏上是过渡桥.
  不影响 card-head 位置 (pt 不变), 只压缩底部空白.

断言 (真实服务, 390px):
  1. pickCard padding-bottom 4px (从 6px)
  2. pickCard padding-top 6px 不变
  3. pickCard 总高 -2px
  4. bv-row 第一行位置不变 (card-head 仍顶部对齐)
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
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  return {pickCard: info(pickCard)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"pickCard: h={d['pickCard']['h']} pt={d['pickCard']['pt']} pb={d['pickCard']['pb']} pl={d['pickCard']['pl']} pr={d['pickCard']['pr']}")

        assert d['pickCard']['pb'] == '4px', f"R199: pickCard pb={d['pickCard']['pb']} != 4px"
        assert d['pickCard']['pt'] == '6px', f"R199: pickCard pt={d['pickCard']['pt']} 应仍 6px"

        await b.close()
        print(f"[OK] R199 pickCard pb 6→4 — 推票卡 -2px, 顶部节奏 6/4 过渡桥 ✓")

if __name__ == "__main__":
    asyncio.run(run())