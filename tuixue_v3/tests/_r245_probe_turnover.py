"""R245 prep: 探针 turnover 格内容是否溢出/裁剪 — 换手率+量比+成交额三合一

第一性原理: R77-R79 把 换手率 + 量比 + 成交额 三个决策信号塞进 turnover 格.
  R243 probe 显示该格 w=72, 内容 '12.55%量1.05.2亿' ≈ 15 glyphs at 10.5px
  ≈ 150px — 72px 放不下. 若 scrollWidth > clientWidth, 信号被视觉裁剪,
  用户读不到量比/成交额 = R78/R79 白做.
  本探针量 scrollWidth vs clientWidth + 子元素真实宽度, 判断是否裁剪.
"""
import asyncio, json
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
  var out = [];
  for (var i=0; i<Math.min(5, rows.length); i++) {
    var td = rows[i].querySelector('td:nth-child(5)');
    if (!td) continue;
    var cs = getComputedStyle(td);
    var r = td.getBoundingClientRect();
    var spans = [];
    td.querySelectorAll('span').forEach(function(s){
      var sr = s.getBoundingClientRect();
      spans.push({cls: (s.className||'').slice(0,22), text: (s.textContent||'').trim(),
                  w: Math.round(sr.width), left: Math.round(sr.left - r.left)});
    });
    var textNode = Array.from(td.childNodes).filter(function(n){ return n.nodeType===3; }).map(function(n){ return n.textContent.trim(); }).join('');
    out.push({
      text: (td.textContent||'').trim(),
      textNode: textNode,
      clientW: Math.round(r.width), scrollW: td.scrollWidth,
      clipped: td.scrollWidth > Math.round(r.width) + 1,
      fs: cs.fontSize, whiteSpace: cs.whiteSpace, overflow: cs.overflow,
      spans: spans
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for row in d:
            print(f"row: text='{row['text']}' clientW={row['clientW']} scrollW={row['scrollW']} clipped={row['clipped']} ws={row['whiteSpace']} ovf={row['overflow']} fs={row['fs']}")
            for s in row['spans']:
                print(f"   span '{s['text']}' cls={s['cls']} w={s['w']} left={s['left']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
