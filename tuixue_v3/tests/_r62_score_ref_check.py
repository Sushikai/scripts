"""R62 分数条平均参考线 + 相对倍率标签."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-score-bar { position: relative; display: inline-block; width: 56px; height: 4px;
                    background: rgba(128,128,128,0.25); border-radius: 2px; vertical-align: middle; }
    .bv-score-fill { display: block; height: 100%; border-radius: 2px; }
    .bv-score-avgline { position: absolute; top: -2px; bottom: -2px; width: 2px;
                        background: #fff; opacity: 0.9; pointer-events: none; }
    .bv-score-num { font-size: 10px; font-weight: 700; color: #aaa; vertical-align: middle; }
    .bv-score-rel { margin-left: 3px; font-size: 9px; font-weight: 700; vertical-align: middle; }
    .bv-score-rel.high { color: #4ade80; }
    .bv-score-rel.low  { color: #f87171; }
    """
    js = """
    // Mirror of the R62 render logic (avg computed over filtered picks)
    function renderScoreRow(sc, _avgScore) {
      var scTone = sc >= 60 ? 'strong' : (sc >= 30 ? 'mid' : 'weak');
      var _avgLeft = Math.min(100, _avgScore);
      var _scPct = Math.min(100, sc);
      var _rel = _avgScore > 0 ? (sc / _avgScore) : 0;
      var _relTxt = '';
      if (_avgScore > 0) {
        if (_rel >= 1.15) _relTxt = '<span class="bv-score-rel high">x' + _rel.toFixed(1) + ' avg</span>';
        else if (_rel <= 0.85) _relTxt = '<span class="bv-score-rel low">x' + _rel.toFixed(1) + ' avg</span>';
      }
      var html = '<div class="bv-score-bar"><div class="bv-score-fill ' + scTone + '" style="width:' + _scPct + '%"></div>';
      if (_scPct > _avgLeft) html += '<div class="bv-score-avgline" style="left:' + _avgLeft + '%"></div>';
      html += '</div><span class="bv-score-num">' + Math.round(sc) + '</span>' + _relTxt;
      return html;
    }
    window.renderScoreRow = renderScoreRow;
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Case 1: avg=50, sc=80 → high label + avgline at left 50%
        r1 = await page.evaluate("renderScoreRow(80, 50)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        avgline = await page.query_selector(".bv-score-avgline")
        assert avgline is not None, "avgline should exist when score above avg"
        left = await page.evaluate("document.querySelector('.bv-score-avgline').style.left")
        rel = await page.query_selector(".bv-score-rel")
        assert rel is not None, "relative label should exist when score 1.6x avg"
        rel_cls = await page.evaluate("document.querySelector('.bv-score-rel').className")
        rel_txt = await page.evaluate("document.querySelector('.bv-score-rel').textContent")
        print(f"case1: avgline left={left}, rel_cls={rel_cls}, rel_txt={rel_txt}")
        assert left == "50%", "avgline at avg position"
        assert "high" in rel_cls

        # Case 2: avg=50, sc=50 → no label (equal), no avgline (sc not > avg)
        r2 = await page.evaluate("renderScoreRow(50, 50)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        avgline2 = await page.query_selector(".bv-score-avgline")
        rel2 = await page.query_selector(".bv-score-rel")
        print(f"case2: avgline={avgline2 is not None}, rel={rel2 is not None}")
        assert avgline2 is None
        assert rel2 is None

        # Case 3: avg=50, sc=30 → low label
        r3 = await page.evaluate("renderScoreRow(30, 50)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        rel3 = await page.query_selector(".bv-score-rel")
        rel_cls3 = await page.evaluate("document.querySelector('.bv-score-rel').className")
        print(f"case3: rel_cls={rel_cls3}")
        assert rel3 is not None
        assert "low" in rel_cls3

        # Case 4: avg=0 (no picks) → no crash, no label
        r4 = await page.evaluate("renderScoreRow(80, 0)")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r4)
        rel4 = await page.query_selector(".bv-score-rel")
        print(f"case4: rel={rel4 is not None}")
        assert rel4 is None

        print("[OK] R62 avg reference line + relative multiple label work")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
