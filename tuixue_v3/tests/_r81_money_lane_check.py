"""R81 资金格从窄列换到弹性列 — 信息长度决定分配空间.

R77-79 让换手格变长 (8.50%量2.412.6亿), 却挤在 auto 窄列 (由代码列定宽),
板块(短)反而占 1fr 弹性列. R81: 交换 — 资金格拿弹性列, 板块拿窄列.
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
    "sector turnover streak"
    "rules rules  rules"
    "seal  time  burst";
  grid-template-columns:auto 1fr auto;
  gap:2px 8px; background:#1a2029; border:1px solid #2a303a; border-radius:8px;
  padding:8px 12px; margin-bottom:6px; position:relative;
}
.view-bv .bv-table tr.bv-row > td { padding:0; border:0; }
.view-bv .bv-table td:nth-child(1){ grid-area:code; font-size:11px; color:#888; white-space:nowrap; }
.view-bv .bv-table td:nth-child(2){ grid-area:name; font-weight:700; font-size:13px; color:#fff; }
.view-bv .bv-table td:nth-child(3){ grid-area:sector; font-size:10px; color:#aaa;
  display:flex; align-items:baseline; gap:6px; flex-wrap:wrap; }
.view-bv .bv-table td:nth-child(4){ grid-area:change; text-align:right; font-size:15px; font-weight:700; color:#ff5757; white-space:nowrap; }
.view-bv .bv-table td:nth-child(5){ grid-area:turnover; font-size:10px; color:#aaa; white-space:nowrap; }
.view-bv .bv-table td:nth-child(6){ grid-area:streak; text-align:center; font-size:11px; color:#00f0ff; font-weight:700; white-space:nowrap; }
.view-bv .bv-table td:nth-child(7){ grid-area:seal; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(8){ grid-area:time; text-align:center; font-size:12px; font-weight:600; color:#fff; }
.view-bv .bv-table td:nth-child(9){ grid-area:burst; text-align:center; font-size:10px; color:#aaa; }
.view-bv .bv-table td:nth-child(10){ grid-area:rules; font-size:10px; padding-top:4px; border-top:1px dashed #2a303a; }
.bv-vr { display:inline-block; margin-left:4px; padding:0 3px; border-radius:3px; font-size:9px; font-weight:700;
  color:#aaa; background:rgba(128,128,128,0.12); }
.bv-vr.bv-vr-hot { color:#4ade80; background:rgba(74,222,128,0.12); }
.bv-vr.bv-vr-amt { color:#aaa; background:transparent; }
"""

CARD = """
<div class="view-bv"><table class="bv-table"><tbody>
  <tr class="bv-row">
    <td>600123</td><td>兰石重装</td>
    <td class="bv-sector"><span class="bv-sector-name">人形机器人</span><span class="bv-sector-chg">+3.2%</span></td>
    <td>+9.98%</td>
    <td>8.50%<span class="bv-vr bv-vr-hot">量2.4</span><span class="bv-vr bv-vr-amt">12.6亿</span></td>
    <td>首板</td><td>1.2</td><td>14:32</td><td>—</td><td>rules</td>
  </tr>
</tbody></table></div>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content("<!DOCTYPE html><html><head><style>" + CSS + "</style></head><body>" + CARD + "</body></html>")

        # money cell (td5) must have width >= content width → not clipped/overflow
        m = await page.evaluate("""() => {
          var td = document.querySelector('td:nth-child(5)');
          var r = td.getBoundingClientRect();
          return { scrollWidth: td.scrollWidth, clientWidth: td.clientWidth, rectW: r.width,
                   display: getComputedStyle(td).display };
        }""")
        print(f"money cell: {m}")
        assert m["scrollWidth"] <= m["clientWidth"] + 1, "R81: money cell must not overflow"
        assert m["display"] != "none"

        # money cell must be wider than the narrow code column (got the flexible lane)
        w = await page.evaluate("""() => {
          var c = document.querySelector('td:nth-child(1)').getBoundingClientRect().width;
          var m = document.querySelector('td:nth-child(5)').getBoundingClientRect().width;
          var s = document.querySelector('td:nth-child(3)').getBoundingClientRect().width;
          return { code: c, money: m, sector: s };
        }""")
        print(f"widths: {w}")
        assert w["money"] > w["sector"], "R81: money cell should get more width than sector"

        # whole row still fits 390 viewport (no horizontal overflow)
        fit = await page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        print(f"row fits 390px: {fit}")
        assert fit

        print("[OK] R81 money cell in flexible lane, sector compact")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
