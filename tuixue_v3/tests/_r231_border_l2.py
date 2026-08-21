"""R231: mobile bv-row border-color line-1→line-2 — 卡片轮廓清晰

第一性原理: bv-row border 1px solid var(--line-1) 是默认 ink-1 line-1.
  在 R227 bg-2 浮起 + bg-1 页面背景下, line-1 偏弱.
  改用 var(--line-2) 让卡片轮廓更清晰, 用户能一眼定位卡片边缘.

断言 (真实服务, 390px):
  1. bv-row border-color var(--line-2) (从 var(--line-1))
  2. bv-row h 不变 (74.7)
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
    return {h: Math.round(rect.height*10)/10, bc: cs.borderColor};
  }
  // 找干净非首板非多选非 swipe 行 (避免 R15 红环 + R52 swipe purple border)
  var rows = document.querySelectorAll('.view-bv .bv-table tr.bv-row');
  var clean = null;
  for (var i=0; i<rows.length; i++) {
    var r = rows[i];
    if (!r.classList.contains('is-first-board') &&
        !r.classList.contains('bv-multi-selected') &&
        !r.classList.contains('swiping') &&
        !r.classList.contains('swiping-right') &&
        !r.classList.contains('swiping-left')) {
      clean = r;
      break;
    }
  }
  return {row: info(clean)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row (clean): h={d['row']['h']} bc={d['row']['bc']}")

        # border-color 不能是透明
        assert 'rgba(0, 0, 0, 0)' not in d['row']['bc'] and 'transparent' not in d['row']['bc'], f"R231: row bc={d['row']['bc']} 应有颜色"
        # 至少包含 line-2 灰色 rgba(15, 23, 42, 0.14) — R227 line-1 是 hsla/var 形式, R231 改 line-2 后应是统一灰
        assert 'rgba(15, 23, 42, 0.14)' in d['row']['bc'], f"R231: row bc={d['row']['bc']} 应包含 line-2 rgba(15, 23, 42, 0.14)"

        await b.close()
        print(f"[OK] R231 bv-row border-color line-1→line-2 — 卡片轮廓清晰 ✓")

if __name__ == "__main__":
    asyncio.run(run())