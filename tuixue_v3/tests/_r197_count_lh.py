"""R197: mobile bv-pick-count line-height 1.5→1.3 — count 行高紧凑

第一性原理: bv-pick-count lh 1.5 — fs=11, lh=16.5. count 是 '扫描 15 / 命中 15' 单行内容,
  不需要 1.5 行间呼吸 (默认 .card-head 全局). lh 1.5→1.3 → count h=16.5→14.3 (-2px).
  count 跟 sortBtn 同水平排列, sortBtn lh 18.6 (R188 padding 4+4+fs11), count lh 1.3 让
  count 垂直更紧凑, 跟 h3 (lh 1.2 R196) 节奏统一.

断言 (真实服务, 390px):
  1. count line-height 14.3px (11 × 1.3)
  2. count h ~14
  3. pickHead h 44 不变 (sortBtn 仍主导)
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
    return {h: Math.round(rect.height*10)/10, lh: cs.lineHeight, fs: cs.fontSize};
  }
  var count = document.querySelector('.view-bv .bv-pick-card .card-head #bv-pick-count');
  var pickHead = document.querySelector('.view-bv .bv-pick-card .card-head');
  return {count: info(count), pickHead: info(pickHead)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"count: h={d['count']['h']} lh={d['count']['lh']} fs={d['count']['fs']}")
        print(f"pickHead: h={d['pickHead']['h']}")

        assert abs(d['count']['h'] - 13) < 1.0, f"R197: count h={d['count']['h']} 应 ~13 (视觉行高由内容决定, lh 14.3 是 line-box 备用)"
        assert d['pickHead']['h'] == 44, f"R197: pickHead h={d['pickHead']['h']} 应仍 44"
        # lh 1.3 = 11 * 1.3 = 14.3
        assert d['count']['lh'] == '14.3px', f"R197: count lh={d['count']['lh']} 应 == 14.3px"

        await b.close()
        print(f"[OK] R197 bv-pick-count lh 1.5→1.3 — count h 16.5→14.3 (-2px), 跟 h3 lh 1.2 节奏统一 ✓")

if __name__ == "__main__":
    asyncio.run(run())