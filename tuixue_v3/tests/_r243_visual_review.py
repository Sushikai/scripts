"""R243: visual review — 截屏全紧凑卡片 + 采集各行高/布局探针

第一性原理: R224-R242 连续 18 轮微调 (padding/字号/边框) 后,
  先用真实截屏 + 布局数据做视觉审计, 再定 R243 的具体改动.
  避免盲目继续调参 — 先看整卡实际长什么样.

输出:
  1. tests/_r243_shot.png — 390px 全卡截屏
  2. stdout: 每行 bv-row 高 / row1/2/3 内格 top 坐标 / 总高度
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
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = {rows: []};
  var first = rows[0];
  if (!first) return out;
  out.table_w = first.closest('.bv-table').offsetWidth;
  var cs = getComputedStyle(first);
  out.row = {
    h: first.offsetHeight,
    pl: cs.paddingLeft, pr: cs.paddingRight,
    pt: cs.paddingTop, pb: cs.paddingBottom,
    radius: cs.borderRadius,
    gap: cs.gap, bg: cs.backgroundColor, border: cs.borderColor
  };
  // 各 td 的 grid-area + 尺寸
  var tds = first.querySelectorAll('td');
  var cells = [];
  tds.forEach(function(td, i){
    var r = td.getBoundingClientRect();
    var cs2 = getComputedStyle(td);
    cells.push({
      n: i+1, area: cs2.gridArea,
      x: Math.round(r.x - first.getBoundingClientRect().x),
      y: Math.round(r.y - first.getBoundingClientRect().y),
      w: Math.round(r.width), h: Math.round(r.height),
      fs: cs2.fontSize, fw: cs2.fontWeight, ls: cs2.letterSpacing,
      text: (td.textContent||'').trim().slice(0,14)
    });
  });
  out.cells = cells;
  // 前 5 行高
  for (var i=0; i<Math.min(5, rows.length); i++) {
    out.rows.push({i: i, h: rows[i].offsetHeight, fb: rows[i].classList.contains('is-first-board')});
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        # 滚到卡片区顶部
        await page.evaluate("() => { var el=document.querySelector('.view-bv .bv-table'); if(el) el.scrollIntoView({block:'start'}); }")
        await page.wait_for_timeout(500)
        await page.screenshot(path="tests/_r243_shot.png", clip={"x":0, "y":0, "width":390, "height":844})
        d = await page.evaluate(PROBE)
        print(f"table_w={d['table_w']}")
        print(f"row h={d['row']['h']} pl={d['row']['pl']} pr={d['row']['pr']} pt={d['row']['pt']} pb={d['row']['pb']} radius={d['row']['radius']} gap={d['row']['gap']} bg={d['row']['bg']}")
        print("cells:")
        for c in d['cells']:
            print(f"  td{c['n']:>2} {c['area']:<14} x={c['x']:>3} y={c['y']:>3} w={c['w']:>3} h={c['h']:>3} fs={c['fs']:<7} fw={c['fw']:<4} '{c['text']}'")
        print("heights:")
        for r in d['rows']:
            print(f"  row{r['i']}={r['h']} fb={r['fb']}")
        await b.close()
        print(f"[OK] R243 visual review 截屏 → tests/_r243_shot.png")

if __name__ == "__main__":
    asyncio.run(run())
