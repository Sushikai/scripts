"""R80 命中数 badge 不再与连板 chip 重叠 — 信息位不被污染.

原 badge absolute top:-22px right:0 → 与右上角连板 chip (首板) 抢同一位置.
R80: badge 改规则行内嵌, 连板独占右上角.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; padding:12px; font-family:-apple-system,'PingFang SC',sans-serif; }
.view-bv .bv-table, .view-bv .bv-table tbody { display:block; width:100%; }
.view-bv .bv-table tr.bv-row {
  display:grid;
  grid-template-areas:
    "code  name  change"
    "turnover sector streak"
    "rules rules  rules"
    "seal  time  burst";
  grid-template-columns:auto 1fr auto;
  gap:2px 8px; background:#1a2029; border:1px solid #2a303a; border-radius:8px;
  padding:8px 12px; margin-bottom:6px; position:relative;
}
.view-bv .bv-table tr.bv-row > td { padding:0; border:0; }
.view-bv .bv-table td:nth-child(1){ grid-area:code; font-size:11px; color:#888; }
.view-bv .bv-table td:nth-child(2){ grid-area:name; font-weight:700; font-size:13px; color:#fff; }
.view-bv .bv-table td:nth-child(3){ grid-area:sector; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(4){ grid-area:change; text-align:right; font-size:15px; font-weight:700; color:#ff5757; }
.view-bv .bv-table td:nth-child(5){ grid-area:turnover; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(6){ grid-area:streak; text-align:center; font-size:11px; color:#00f0ff; font-weight:700;
  background:rgba(0,240,255,0.16); border-radius:4px; padding:2px 6px; display:inline-block; margin:0 auto; }
.view-bv .bv-table td:nth-child(7){ grid-area:seal; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(8){ grid-area:time; text-align:center; font-size:12px; font-weight:600; color:#fff; }
.view-bv .bv-table td:nth-child(9){ grid-area:burst; text-align:center; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(10){ grid-area:rules; font-size:10px; padding-top:4px; border-top:1px dashed #2a303a; }
/* R80: inline badge, not absolute */
.view-bv .bv-rules-cell .bv-hit-badge {
  display:inline-block; vertical-align:middle; min-width:16px; height:15px; padding:0 4px; margin-right:4px;
  border-radius:4px; background:#00f0ff; color:#000; font-size:9px; font-weight:700; line-height:15px; text-align:center;
}
.view-bv .bv-rules-cell .bv-hit-badge.hot { background:#ff5757; color:#fff; }
.bv-chip { display:inline-block; margin-right:4px; padding:1px 5px; border-radius:4px; font-size:9px;
  background:rgba(0,240,255,0.12); color:#7dd3fc; }
"""

CARD = """
<div class="view-bv"><table class="bv-table"><tbody>
  <tr class="bv-row">
    <td>600123</td><td>兰石重装</td><td>氢能源</td><td>+9.98%</td>
    <td>8.5%</td><td>首板</td><td>1.2</td><td>14:32</td><td>—</td>
    <td class="bv-rules-cell"><span class="bv-hit-badge hot">4</span><span class="bv-chip">BV02弱转强</span><span class="bv-chip">BV07放量</span><span class="bv-chip">BV09缩量</span></td>
  </tr>
</tbody></table></div>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content("<!DOCTYPE html><html><head><style>" + CSS + "</style></head><body>" + CARD + "</body></html>")

        # R80: badge must NOT be absolutely positioned (inline now)
        pos = await page.evaluate("getComputedStyle(document.querySelector('.bv-hit-badge')).position")
        print(f"badge position = {pos}")
        assert pos != "absolute"

        # R80: badge bounding box must NOT overlap streak chip box
        overlap = await page.evaluate("""() => {
          var b = document.querySelector('.bv-hit-badge').getBoundingClientRect();
          var s = document.querySelector('td:nth-child(6)').getBoundingClientRect();
          var dx = Math.max(0, Math.min(b.right,s.right) - Math.max(b.left,s.left));
          var dy = Math.max(0, Math.min(b.bottom,s.bottom) - Math.max(b.top,s.top));
          return dx * dy;
        }""")
        print(f"badge × streak overlap = {overlap}px²")
        assert overlap == 0

        # R80: badge inside rules cell, flows inline before chips
        inline = await page.evaluate("""() => {
          var badge = document.querySelector('.bv-hit-badge');
          var cell = document.querySelector('.bv-rules-cell');
          var b = badge.getBoundingClientRect(), c = cell.getBoundingClientRect();
          return { insideCell: b.top >= c.top && b.bottom <= c.bottom && b.left >= c.left && b.right <= c.right,
                   display: getComputedStyle(badge).display };
        }""")
        print(f"badge inline-in-cell = {inline}")
        assert inline["insideCell"]
        assert inline["display"] == "inline-block"

        print("[OK] R80 hit-badge no longer overlaps streak chip")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
