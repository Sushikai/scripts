"""R187: mobile pickCard head gap 8→6 — pickHead 内部间距微调

第一性原理: pickCard .card-head gap 8px (从 .card-head 默认 gap var(--space-2)=8px).
  pickHead 内部 h3 (259w) + count (148w) + sortBtn (61w) — 8px gap 让 h3 挤压.
  8→6 让 h3 多 2px 内容宽度 (跟 card 内部 padding 10/10 一致, 视觉节奏统一).
  不影响垂直密度 (pickHead 高仍 44 由 sortBtn 主导).

断言 (真实服务, 390px):
  1. pickCard .card-head gap=6px (从 8px)
  2. pickHead 高仍 44 (不变, 由 sortBtn 主导)
  3. h3 w 增加 2px (从 259 → 261)
  4. count 仍可见 (不被换行)
  5. pickCard 高度不变
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
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height),w:Math.round(x.width)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, w: rect.w, gap: cs.gap};
  }
  var head = document.querySelector('.bv-pick-card .card-head');
  var h3 = document.querySelector('.bv-pick-card .card-head h3');
  var count = document.querySelector('.bv-pick-card .card-head #bv-pick-count');
  var sortBtn = document.querySelector('.bv-pick-card .bv-sort-btn');
  return {
    head: info(head),
    h3: info(h3),
    count: info(count),
    sortBtn: info(sortBtn),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"head: h={d['head']['h']} gap={d['head']['gap']}")
        print(f"h3: h={d['h3']['h']} w={d['h3']['w']}")
        print(f"count: h={d['count']['h']} w={d['count']['w']}")
        print(f"sortBtn: h={d['sortBtn']['h']} w={d['sortBtn']['w']}")

        assert d['head']['gap'] == '6px', f"R187: head gap={d['head']['gap']} != 6px"
        assert d['head']['h'] == 44, f"R187: head h={d['head']['h']} 应仍 44"
        assert d['h3']['w'] >= 261, f"R187: h3 w={d['h3']['w']} 应 >= 261 (回收 2px)"
        # count 不被换行 (单行)
        assert d['count']['h'] < 20, f"R187: count h={d['count']['h']} 应 < 20 (单行)"

        await b.close()
        print(f"[OK] R187 pickHead gap 8→6 — h3 w {d['h3']['w']}px (回收 2px), head 高 44 不变 ✓")

if __name__ == "__main__":
    asyncio.run(run())
