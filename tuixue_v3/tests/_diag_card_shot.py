"""诊断截图: 渲染一张真实数据卡片, 肉眼找下一处摩擦."""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; padding:12px; font-family: -apple-system,'PingFang SC',sans-serif; }
.view-bv .bv-table, .view-bv .bv-table tbody { display:block; width:100%; }
.view-bv .bv-table tr.bv-row {
  display:grid;
  grid-template-areas:
    "code  name  change"
    "turnover sector streak"
    "rules rules  rules"
    "seal  time  burst";
  grid-template-columns:auto 1fr auto;
  gap:2px 8px;
  background:#1a2029; border:1px solid #2a303a; border-radius:8px;
  padding:8px 12px; margin-bottom:6px; width:100%; box-sizing:border-box; position:relative;
}
.view-bv .bv-table tr.bv-row > td { padding:0; border:0; }
.view-bv .bv-table td:nth-child(1){ grid-area:code; font-weight:500; font-size:11px; color:#888; }
.view-bv .bv-table td:nth-child(2){ grid-area:name; font-weight:700; font-size:13px; color:#fff; }
.view-bv .bv-table td:nth-child(3){ grid-area:sector; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(4){ grid-area:change; text-align:right; font-size:15px; font-weight:700; color:#ff5757; }
.view-bv .bv-table td:nth-child(5){ grid-area:turnover; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(6){ grid-area:streak; text-align:center; font-size:11px; color:#00f0ff; font-weight:700; }
.view-bv .bv-table td:nth-child(7){ grid-area:seal; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(8){ grid-area:time; text-align:center; font-size:12px; font-weight:600; color:#fff; }
.view-bv .bv-table td:nth-child(9){ grid-area:burst; text-align:center; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(10){ grid-area:rules; font-size:10px; padding-top:4px; border-top:1px dashed #2a303a; position:relative; }
.bv-motto-badge { display:inline-block; margin-left:4px; padding:0 5px; border-radius:3px; font-size:9px; font-weight:700;
  color:#22d3ee; background:rgba(34,211,238,0.12); max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle; }
.bv-vr { display:inline-block; margin-left:4px; padding:0 3px; border-radius:3px; font-size:9px; font-weight:700;
  color:#aaa; background:rgba(128,128,128,0.12); }
.bv-vr.bv-vr-hot { color:#4ade80; background:rgba(74,222,128,0.12); }
.bv-vr.bv-vr-cold { color:#94a3b8; background:rgba(148,163,184,0.10); }
.bv-vr.bv-vr-amt { color:#aaa; background:transparent; }
.bv-hit-badge { position:absolute; top:-22px; right:0; min-width:22px; height:18px; padding:0 5px; border-radius:9px;
  background:#00f0ff; color:#000; font-size:10px; font-weight:700; line-height:18px; text-align:center;
  box-shadow:0 1px 4px rgba(0,0,0,0.4); pointer-events:none; }
.bv-hit-badge.hot { background:#ff5757; color:#fff; }
.bv-chip { display:inline-block; margin-right:4px; padding:1px 5px; border-radius:4px; font-size:9px;
  background:rgba(0,240,255,0.12); color:#7dd3fc; }
.bv-chip.off { opacity:.5; }
.bv-sector-chg.bv-pos { color:#4ade80; }
"""

# 用一套接近真实的 3 卡数据 (涵盖 巨量首板 / 弱转强 / 炸板缩量)
CARD = """
<div class="view-bv"><table class="bv-table"><tbody>
  <tr class="bv-row">
    <td>600123</td><td>兰石重装 <span class="bv-motto-badge">弱转强</span></td>
    <td class="bv-sector"><span>氢能源</span> <span class="bv-sector-chg bv-pos">+3.2%</span></td>
    <td class="bv-pos">+9.98%</td>
    <td>8.50%<span class="bv-vr bv-vr-hot" title="量比 2.40">量2.4</span><span class="bv-vr bv-vr-amt" title="成交额 12.60 亿">12.6亿</span></td>
    <td>首板</td>
    <td>1.2</td><td>14:32</td><td>—</td>
    <td class="bv-rules-cell"><span class="bv-hit-badge hot">4</span><span class="bv-chip">BV02弱转强</span><span class="bv-chip">BV07放量</span><span class="bv-chip off">BV09缩量</span></td>
  </tr>
  <tr class="bv-row">
    <td>300456</td><td>赛微电子</td>
    <td class="bv-sector"><span>半导体</span> <span class="bv-sector-chg bv-pos">+2.1%</span></td>
    <td class="bv-pos">+20.0%</td>
    <td>14.2%<span class="bv-vr bv-vr-hot" title="量比 3.10">量3.1</span><span class="bv-vr bv-vr-amt" title="成交额 28.90 亿">28.9亿</span></td>
    <td>2板</td>
    <td>0.5</td><td>09:47</td><td>1</td>
    <td class="bv-rules-cell"><span class="bv-hit-badge hot">3</span><span class="bv-chip">BV01首板</span><span class="bv-chip">BV06加速</span></td>
  </tr>
  <tr class="bv-row">
    <td>002230</td><td>科大讯飞</td>
    <td class="bv-sector"><span>AI</span> <span class="bv-sector-chg bv-neg">-0.8%</span></td>
    <td class="bv-neg">-2.10%</td>
    <td>2.10%<span class="bv-vr bv-vr-cold" title="量比 0.60">量0.6</span><span class="bv-vr bv-vr-amt" title="成交额 5.20 亿">5.2亿</span></td>
    <td>—</td>
    <td>0.0</td><td>—</td><td>—</td>
    <td class="bv-rules-cell"><span class="bv-hit-badge">1</span><span class="bv-chip">BV12尾盘</span></td>
  </tr>
</tbody></table></div>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 640}, has_touch=True)
        await page.set_content("<!DOCTYPE html><html><head><style>" + CSS + "</style></head><body>" + CARD + "</body></html>")
        await page.screenshot(path="tests/_r80_card_shot.png", full_page=True)
        # 检测视觉重叠: 右上角 score badge(top:-22) 是否与 streak chip 重叠
        overlap = await page.evaluate("""() => {
          var badge = document.querySelector('.bv-hit-badge.hot');
          var streak = document.querySelector('td:nth-child(6)');
          if(!badge || !streak) return null;
          var b = badge.getBoundingClientRect(), s = streak.getBoundingClientRect();
          var dx = Math.max(0, Math.min(b.right,s.right) - Math.max(b.left,s.left));
          var dy = Math.max(0, Math.min(b.bottom,s.bottom) - Math.max(b.top,s.top));
          return {badge:{t:b.top,b:b.bottom,l:b.left,r:b.right}, streak:{t:s.top,b:s.bottom,l:s.left,r:s.right}, overlap: dx*dy};
        }""")
        print("badge vs streak overlap:", overlap)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
