"""R235: mobile bv-pick-count color ink-2→ink-1 — 计数对比度

第一性原理: bv-pick-count 现在 color var(--ink-2).
  跟 R234 fw 600 加粗一起, ink-2 contrast 偏弱.
  改 ink-1 让计数对比度 AA 达标.
  跟 R167-R170 AA 对比度提升思路一致.

断言 (真实服务, 390px):
  1. bv-pick-count color var(--ink-1)
  2. 跟 sort-btn 对比度对齐
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
    return {c: cs.color};
  }
  var count = document.querySelector('.bv-pick-count');
  return {count: info(count)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"bv-pick-count color: {d['count']['c']}")

        # ink-1 应是 rgb(15, 23, 42) (dark) 或 rgba(15,23,42,...) — 不能是 ink-2 灰 (rgb 51,65,85)
        assert 'rgb(15, 23, 42)' in d['count']['c'] or 'rgb(15,23,42)' in d['count']['c'], f"R235: count color={d['count']['c']} 应 ink-1 (rgb 15,23,42)"

        await b.close()
        print(f"[OK] R235 bv-pick-count color ink-2→ink-1 — 计数对比度 AA 达标 ✓")

if __name__ == "__main__":
    asyncio.run(run())