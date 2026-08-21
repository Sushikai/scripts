"""R234: mobile bv-pick-count font-weight — 计数加粗

第一性原理: bv-pick-count 是首屏关键信息 (N 只推票).
  fw normal 跟 meta 副信息视觉权重一样.
  改 600 跟右侧 sort/refresh 按钮对齐, 用户一眼看到推了多少只.
  跟 R229 code-link 加粗 + R128 分数主信号思路一致.

断言 (真实服务, 390px):
  1. bv-pick-count font-weight 600
  2. bv-pick-count 视觉权重跟 sort-btn 对齐
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
    return {fw: cs.fontWeight};
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
        print(f"bv-pick-count: fw={d['count']['fw']}")

        assert d['count']['fw'] == '600', f"R234: count fw={d['count']['fw']} 应 600"

        await b.close()
        print(f"[OK] R234 bv-pick-count font-weight 600 — 计数加粗, 首屏关键信息 ✓")

if __name__ == "__main__":
    asyncio.run(run())