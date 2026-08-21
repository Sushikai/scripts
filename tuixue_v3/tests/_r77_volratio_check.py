"""R77 换手格加量比小字 — 资金放大/缩量核心信号."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-vr { display: inline-block; margin-left: 4px; padding: 0 3px; border-radius: 3px;
             font-size: 9px; font-weight: 700; color: #aaa; background: rgba(128,128,128,0.12); }
    .bv-vr.bv-vr-hot { color: #4ade80; background: rgba(74,222,128,0.12); }
    .bv-vr.bv-vr-cold { color: #94a3b8; background: rgba(148,163,184,0.10); }
    """
    js = """
    function fmtNum(v, d) { return Number(v).toFixed(d); }
    function renderTurnoverCell(turnover, volRatio) {
      var vr = Number(volRatio || 0) || 0;
      var vrCls = vr >= 1.5 ? ' bv-vr-hot' : (vr >= 1.0 ? '' : ' bv-vr-cold');
      return fmtNum(turnover, 2) + '%' + (vr > 0 ? '<span class="bv-vr' + vrCls + '" title="量比 ' + vr.toFixed(2) + '">量' + vr.toFixed(1) + '</span>' : '');
    }
    window.renderTurnoverCell = renderTurnoverCell;
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # High vol ratio 2.4 → hot label 量2.4
        r1 = await page.evaluate("renderTurnoverCell(8.5, 2.4)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        vr1 = await page.query_selector(".bv-vr")
        assert vr1 is not None
        txt1 = await page.evaluate("document.querySelector('.bv-vr').textContent")
        cls1 = await page.evaluate("document.querySelector('.bv-vr').className")
        print(f"hot: {txt1} cls={cls1}")
        assert txt1 == "量2.4"
        assert "bv-vr-hot" in cls1

        # Normal 1.0 → no tone
        r2 = await page.evaluate("renderTurnoverCell(5.0, 1.0)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        cls2 = await page.evaluate("document.querySelector('.bv-vr').className")
        print(f"normal cls={cls2}")
        assert "bv-vr-hot" not in cls2 and "bv-vr-cold" not in cls2

        # Cold 0.5 → cold tone
        r3 = await page.evaluate("renderTurnoverCell(2.0, 0.5)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        cls3 = await page.evaluate("document.querySelector('.bv-vr').className")
        print(f"cold cls={cls3}")
        assert "bv-vr-cold" in cls3

        # No vol ratio → no label, just turnover
        r4 = await page.evaluate("renderTurnoverCell(5.0, 0)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r4)
        vr4 = await page.query_selector(".bv-vr")
        print(f"no-vol label={vr4 is not None} text={r4}")
        assert vr4 is None

        print("[OK] R77 vol ratio mini label hot/normal/cold")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
