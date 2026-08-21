"""R83 TOP1 crown 不再越出卡片上边界 — 绝对定位不该侵入上方筛选条.

原 crown top:-6px 探进上方卡片/筛选条 (实测与上方元素重叠 235px²).
R83: top:-2px, crown 收在自己卡片内, 不遮挡上方可点击区域.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; padding:12px; font-family:-apple-system,'PingFang SC',sans-serif; }
.view-bv .bv-table tr.bv-row {
  display:grid; grid-template-areas:"code name change" "sector turnover streak" "rules rules rules" "seal time burst";
  grid-template-columns:auto 1fr auto; gap:2px 8px; background:#1a2029; border:1px solid #2a303a; border-radius:8px;
  padding:8px 12px; margin-bottom:6px; position:relative;
}
.view-bv .bv-table tr.bv-row > td { padding:0; border:0; }
.view-bv .bv-table td:nth-child(1){ grid-area:code; font-size:11px; color:#888; }
.view-bv .bv-table tr.bv-row.is-bv-top .bv-top-crown {
  position:absolute !important; top:-2px; left:6px; font-size:9px; font-weight:800; color:#000;
  background:#00f0ff; padding:1px 4px 1px; border-radius:4px; z-index:5; letter-spacing:.5px;
  box-shadow:0 1px 3px rgba(0,240,255,.5); pointer-events:none; line-height:1.1;
}
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div id="above" style="height:24px;background:#333">筛选条</div>
<div class="view-bv"><table class="bv-table"><tbody>
  <tr class="bv-row is-bv-top">
    <td>600123 <span class="bv-top-crown">👑 TOP1</span></td><td>兰石重装</td><td>氢能源</td><td>+9.98%</td>
    <td>8.5%</td><td>首板</td><td>1.2</td><td>14:32</td><td>—</td><td>rules</td>
  </tr>
</tbody></table></div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        ov = await page.evaluate("""() => {
          var crown = document.querySelector('.bv-top-crown').getBoundingClientRect();
          var above = document.querySelector('#above').getBoundingClientRect();
          var dx = Math.max(0, Math.min(crown.right, above.right) - Math.max(crown.left, above.left));
          var dy = Math.max(0, Math.min(crown.bottom, above.bottom) - Math.max(crown.top, above.top));
          var card = document.querySelector('tr.bv-row').getBoundingClientRect();
          return { overlap: dx * dy, crownTop: crown.top, cardTop: card.top };
        }""")
        print(f"crown overlap w/ above: {ov}")
        assert ov["overlap"] == 0, "R83: crown must not poke into element above"

        # crown must sit within (or at most 1px outside) the card's own top edge
        assert ov["crownTop"] >= ov["cardTop"] - 1, "R83: crown should be inside its own card"

        print("[OK] R83 crown stays inside its card")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
