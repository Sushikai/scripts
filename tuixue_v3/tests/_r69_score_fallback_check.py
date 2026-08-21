"""R69 breakdown 空但 score>0 → matched_rules 权重兜底."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-scorepart { display: flex; align-items: center; gap: 6px; font-size: 10px; }
    .bv-scorepart-id { min-width: 48px; font-weight: 700; color: #eee; }
    .bv-scorepart-bar { flex: 1; height: 4px; border-radius: 2px; background: rgba(128,128,128,0.18); overflow: hidden; }
    .bv-scorepart-fill { display: block; height: 100%; border-radius: 2px; background: rgba(0,240,255,0.5); }
    .bv-scorepart-val { min-width: 30px; text-align: right; font-weight: 700; color: #aaa; }
    .dim { color: #888; font-size: 11px; }
    """
    js = """
    var _rulesById = { BV02: {score_weight: 20}, BV05: {score_weight: 30} };
    function renderScoreSection(sc, breakdown, matchedRules) {
      var scoreHtml = '';
      if (breakdown && breakdown.length) {
        var mapped = breakdown.map(function(sb){
          var c = Number(sb.contribution || 0) || 0;
          return { id: sb.rule_id || '?', c: c };
        });
        mapped.sort(function(a,b){ return b.c - a.c; });
        scoreHtml = mapped.map(function(s){
          return '<div class="bv-scorepart"><span class="bv-scorepart-id">' + s.id + '</span>' +
                 '<span class="bv-scorepart-val">+' + Math.round(s.c) + '</span></div>';
        }).join('');
      }
      var fallback = '';
      if (!scoreHtml && sc > 0 && matchedRules && matchedRules.length) {
        fallback = matchedRules.map(function(rid){
          var rw = _rulesById[rid] || {};
          return '<div class="bv-scorepart"><span class="bv-scorepart-id">' + rid + '</span>' +
                 '<span class="bv-scorepart-val">w' + (rw.score_weight || '?') + '</span></div>';
        }).join('');
      }
      return scoreHtml || fallback || '<span class="dim">— 暂无 —</span>';
    }
    window.renderScoreSection = renderScoreSection;
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # 1) breakdown present → uses it (sorted desc)
        r1 = await page.evaluate("renderScoreSection(60, [{rule_id:'BV05',contribution:30},{rule_id:'BV02',contribution:20}], ['BV02','BV05'])")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        ids1 = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-id')].map(e => e.textContent)")
        print(f"breakdown ids={ids1}")
        assert ids1 == ["BV05", "BV02"], "breakdown sorted desc"

        # 2) breakdown empty + score>0 + matched → weight fallback
        r2 = await page.evaluate("renderScoreSection(60, [], ['BV02','BV05'])")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        ids2 = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-id')].map(e => e.textContent)")
        vals2 = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-val')].map(e => e.textContent)")
        print(f"fallback ids={ids2} vals={vals2}")
        assert ids2 == ["BV02", "BV05"]
        assert "w20" in vals2[0] and "w30" in vals2[1]

        # 3) breakdown empty + score=0 → "暂无"
        r3 = await page.evaluate("renderScoreSection(0, [], [])")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        txt3 = await page.evaluate("document.querySelector('#host').textContent")
        print(f"zero score txt={txt3}")
        assert "暂无" in txt3

        print("[OK] R69 score section falls back to rule weights when breakdown missing")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
