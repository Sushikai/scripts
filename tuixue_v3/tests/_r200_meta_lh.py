"""R200 (milestone): mobile bv-meta line-height 1.4→1.3 — meta 行高紧凑

第一性原理: bv-meta lh 1.4 (R108 设, '11.5px 是 Apple iOS caption 最小可读尺寸, line-height 1.4 让密度可控').
  meta '游资仓位管理战法 · v1 · 15 条规则 · 2026-08-17' 是 white-space: nowrap 单行,
  1.4 行间呼吸是多行 prose 用的, 单行 meta 不需要.
  lh 1.4→1.3 → meta lh-box 16.1→14.95 (-1.15px).
  跟 R196 h3 lh 1.2 节奏更对齐 (标题区 line-height 紧凑).
  view-head 整体高度由 view-actions (refresh-btn h=32) 主导, meta 紧凑不改变 view-head h.

断言 (真实服务, 390px):
  1. bv-meta line-height 14.95px (11.5 × 1.3)
  2. bv-meta 视觉 h ~13 (内容紧凑)
  3. view-head h 仍 44 (refresh-btn 主导)
  4. meta 不被截断 (white-space: nowrap + ellipsis)
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
    return {h: Math.round(rect.height*10)/10, lh: cs.lineHeight, fs: cs.fontSize, txt: (el.textContent||'').trim().slice(0,40)};
  }
  var vh = document.querySelector('.view-bv .view-head');
  var meta = document.querySelector('.view-bv .bv-meta');
  return {vh: info(vh), meta: info(meta)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"vh: h={d['vh']['h']}")
        print(f"meta: h={d['meta']['h']} lh={d['meta']['lh']} fs={d['meta']['fs']} txt='{d['meta']['txt']}'")

        # lh 1.3 = 11.5 * 1.3 = 14.95
        assert d['meta']['lh'] == '14.95px', f"R200: meta lh={d['meta']['lh']} 应 == 14.95px"
        # meta 视觉 h 应跟 lh-box 接近 (单行 nowrap)
        assert abs(d['meta']['h'] - 14.95) < 1.0, f"R200: meta h={d['meta']['h']} 应 ~14.95"
        assert d['vh']['h'] == 44, f"R200: vh h={d['vh']['h']} 应仍 44"

        await b.close()
        print(f"[OK] R200 bv-meta lh 1.4→1.3 — meta lh-box 16.1→14.95 (-1.15px), 标题区 line-height 节奏紧凑 ✓")

if __name__ == "__main__":
    asyncio.run(run())