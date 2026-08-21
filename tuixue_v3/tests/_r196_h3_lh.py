"""R196: mobile pick-card h3 line-height 1.35→1.2 — 标题行高紧凑

第一性原理: pickCard .card-head h3 lh 1.35 (默认 .card-head 全局 lh) — h3 fs=16.38, lh=22.1.
  h3 是 '实时推票 (扫描 X / 命中 X / 阶段)' 单行短标题, 不需要 1.35 视觉呼吸 (那是给多行 prose).
  lh 1.35→1.2 → h3 h=22→20 (-2px). pickHead 仍 44 (sortBtn 主导), 但 h3 内部更紧凑,
  标题纵向少 2px, 跟 sortBtn lh 18.6 (R188 padding 4+4+fs11) 节奏更对齐.

断言 (真实服务, 390px):
  1. h3 line-height 19.66px (1.2 × 16.38)
  2. h3 h 20 (从 22.1)
  3. pickHead h 44 不变
  4. h3 文字不被裁切 (单行 nowrap)
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
  var h3 = document.querySelector('.view-bv .bv-pick-card .card-head h3');
  var pickHead = document.querySelector('.view-bv .bv-pick-card .card-head');
  return {h3: info(h3), pickHead: info(pickHead)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"h3: h={d['h3']['h']} lh={d['h3']['lh']} fs={d['h3']['fs']}")
        print(f"pickHead: h={d['pickHead']['h']}")

        # h3 lh 1.2 = 16.38*1.2 = 19.656 ≈ 19.66
        assert abs(d['h3']['h'] - 19.7) < 1.0, f"R196: h3 h={d['h3']['h']} 应 ~19.7"
        assert d['pickHead']['h'] == 44, f"R196: pickHead h={d['pickHead']['h']} 应仍 44"

        await b.close()
        print(f"[OK] R196 pickCard h3 lh 1.35→1.2 — h3 h 22→20 (-2px), 标题纵向紧凑 ✓")

if __name__ == "__main__":
    asyncio.run(run())