"""R79 换手格加成交额 — 量比是倍率, 成交额是绝对资金体量.

地量涨停 vs 巨量涨停是两种完全不同的局面, 必须一眼区分.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
.view-bv .bv-table tr.bv-row { display: grid; grid-template-columns: auto 1fr auto; padding: 8px 12px; }
.view-bv .bv-table td:nth-child(5) { grid-area: turnover; font-size: 10px; color: var(--ink-2, #888); }
.bv-vr { display: inline-block; margin-left: 4px; padding: 0 3px; border-radius: 3px;
         font-size: 9px; font-weight: 700; color: #aaa; background: rgba(128,128,128,0.12); }
.bv-vr.bv-vr-hot { color: #4ade80; background: rgba(74,222,128,0.12); }
.bv-vr.bv-vr-cold { color: #94a3b8; background: rgba(148,163,184,0.10); }
.bv-vr.bv-vr-amt { color: #aaa; background: transparent; }
"""

JS = """
function fmtNum(v, d) { return Number(v).toFixed(d); }
function renderTurnoverCell(p) {
  var vr = Number(p.vol_ratio || 0) || 0;
  var vrCls = vr >= 1.5 ? ' bv-vr-hot' : (vr >= 1.0 ? '' : ' bv-vr-cold');
  var amt = Number(p.amount_yi || 0) || 0;
  var amtTxt = amt > 0 ? (amt >= 1 ? fmtNum(amt, 1) + '亿' : '<0.1亿') : '';
  return fmtNum(p.turnover_pct, 2) + '%' +
    (vr > 0 ? '<span class="bv-vr' + vrCls + '" title="量比 ' + vr.toFixed(2) + '">量' + vr.toFixed(1) + '</span>' : '') +
    (amtTxt ? '<span class="bv-vr bv-vr-amt" title="成交额 ' + amt.toFixed(2) + ' 亿">' + amtTxt + '</span>' : '');
}
window.renderTurnoverCell = renderTurnoverCell;
"""

HTML = "<!DOCTYPE html><html><head><style>" + CSS + "</style></head><body>" \
       "<div id='host'></div><script>" + JS + "</script></body></html>"


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML)

        # Big-cap limit-up: 8.5% turnover, vol 2.4, 12.6亿 → 量2.4 12.6亿
        r1 = await page.evaluate("renderTurnoverCell({turnover_pct:8.5, vol_ratio:2.4, amount_yi:12.6})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = '<table><tbody><tr><td>' + h + '</td></tr></tbody></table>'; }", r1)
        txt1 = await page.evaluate("document.querySelector('td').innerText")
        amt1 = await page.query_selector(".bv-vr-amt")
        print(f"big-cap: {txt1!r}")
        assert "量2.4" in txt1 and "12.6亿" in txt1
        assert amt1 is not None

        # Small-cap low amount: 0.56亿 → <0.1亿 label (sub-yi handling)
        r2 = await page.evaluate("renderTurnoverCell({turnover_pct:3.1, vol_ratio:1.2, amount_yi:0.56})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = '<table><tbody><tr><td>' + h + '</td></tr></tbody></table>'; }", r2)
        txt2 = await page.evaluate("document.querySelector('td').innerText")
        print(f"small-cap: {txt2!r}")
        assert "<0.1亿" in txt2

        # No amount → no amt label
        r3 = await page.evaluate("renderTurnoverCell({turnover_pct:5.0, vol_ratio:1.0, amount_yi:0})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = '<table><tbody><tr><td>' + h + '</td></tr></tbody></table>'; }", r3)
        amt3 = await page.query_selector(".bv-vr-amt")
        print(f"no-amt label={amt3 is not None}")
        assert amt3 is None

        # Both money signals + distinct classes (money density readable at a glance)
        r4 = await page.evaluate("renderTurnoverCell({turnover_pct:6.2, vol_ratio:3.1, amount_yi:8.4})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = '<table><tbody><tr><td>' + h + '</td></tr></tbody></table>'; }", r4)
        cls4 = await page.evaluate("Array.from(document.querySelectorAll('.bv-vr')).map(e => e.className).join('|')")
        print(f"both-signals classes: {cls4}")
        assert "bv-vr-hot" in cls4 and "bv-vr-amt" in cls4

        print("[OK] R79 amount_yi in turnover cell (地量/巨量可区分)")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
