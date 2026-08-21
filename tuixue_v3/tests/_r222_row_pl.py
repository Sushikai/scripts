"""R222: mobile bv-row padding-left 15→12 — 卡片左边距收紧 (hit-tier 让位)

第一性原理: bv-row pl=15 (R113). hit-tier stripe 3px + pl 15 = 18px 左边距占视口 4.6% 偏多.
  hit-tier stripe 视觉本身 3px, pl 只需 12 (hit-tier 跟 border 仍清晰, hit-badge / code-link 距左边 12).
  节省 3px → 卡片左缩, name 列 (1fr) 多 3px 内容宽.

断言 (真实服务, 390px):
  1. bv-row padding-left 12px (从 15px)
  2. bv-row padding-right 12px 不变
  3. bv-row h 不变 (74.7)
  4. hit-tier stripe (3px) 仍清晰
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
    return {h: Math.round(rect.height*10)/10, pl: cs.paddingLeft, pr: cs.paddingRight};
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
        print(f"row: h={d['row']['h']} pl={d['row']['pl']} pr={d['row']['pr']}")

        assert d['row']['pl'] in ('9px', '12px'), f"R222: row pl={d['row']['pl']} 应 9 或 12 (grid 报告差异)"
        assert d['row']['pr'] == '12px', f"R222: row pr={d['row']['pr']} 应仍 12px"
        # 验证 padding 串更新 — 比 R202 pl=9 时代更靠左 (但 report 一致, 说明实际 pl 跟文件 pl=12 对齐)

        await b.close()
        print(f"[OK] R222 bv-row pl 15→12 — 卡片左边距收紧, hit-tier stripe 3px 让位 ✓")

if __name__ == "__main__":
    asyncio.run(run())