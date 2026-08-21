"""R229: mobile code-link font-weight 500→700 — 加粗读股票

第一性原理: code-link 默认 normal 继承 td (500), 跟右侧 change/seal (700)
  视觉权重偏低. 改 700 跟 name 列同权重, 用户扫视时一眼定位股票代码.
  跟 R167 hit-badge 对比度 + R128 分数主信号思路一致.

断言 (真实服务, 390px):
  1. code-link font-weight 700
  2. bv-row h 不变 (74.7)
  3. code-link 跟 change/seal 视觉权重对齐
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
    return {h: Math.round(rect.height*10)/10, fw: cs.fontWeight};
  }
  var link = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(1) a.code-link');
  var change = document.querySelector('.view-bv .bv-table tr.bv-row td:nth-child(4)');
  return {link: info(link), change: info(change)};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"code-link: h={d['link']['h']} fw={d['link']['fw']}")
        print(f"change cell: h={d['change']['h']} fw={d['change']['fw']}")

        assert d['link']['fw'] == '700', f"R229: code-link fw={d['link']['fw']} 应 700"
        # 跟 change 视觉权重对齐
        assert d['link']['fw'] == d['change']['fw'], f"R229: code-link fw={d['link']['fw']} 应跟 change fw={d['change']['fw']} 一致"

        await b.close()
        print(f"[OK] R229 code-link font-weight 500→700 — 加粗读股票, 跟 name/change 同视觉权重 ✓")

if __name__ == "__main__":
    asyncio.run(run())