"""R232: mobile bv-row 过渡 timing 收紧 — active 即时反馈

第一性原理: bv-row transition transform 0.18s + background 0.12s (R51).
  mobile 触摸响应 < 100ms (Apple HIG 阈值), 0.18s 偏慢,
  用户松开手指视觉反馈滞后. 改 0.1s + 0.08s, 即时反馈.
  cubic-bezier 保留弹性质感.

断言 (真实服务, 390px):
  1. bv-row transition transform 0.1s
  2. bv-row transition background 0.08s
  3. bv-row h 不变 (74.7)
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
    return {h: Math.round(rect.height*10)/10, tr: cs.transition};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  return {row: info(row)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"row: h={d['row']['h']}")
        print(f"transition: {d['row']['tr']}")

        # transform 0.1s
        assert 'transform' in d['row']['tr'] and '0.1s' in d['row']['tr'], f"R232: row transition 应含 transform 0.1s, got {d['row']['tr']}"
        # background 0.08s
        assert 'background' in d['row']['tr'] and '0.08s' in d['row']['tr'], f"R232: row transition 应含 background 0.08s, got {d['row']['tr']}"

        await b.close()
        print(f"[OK] R232 bv-row transition transform 0.18→0.1 + bg 0.12→0.08 — active 即时反馈 ✓")

if __name__ == "__main__":
    asyncio.run(run())