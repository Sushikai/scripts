"""R63 分数组成按贡献降序 + 权重条."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-scorepart { display: flex; align-items: center; gap: 6px; font-size: 10px; }
    .bv-scorepart-id { min-width: 48px; font-weight: 700; color: #eee; }
    .bv-scorepart-bar { flex: 1; height: 4px; border-radius: 2px; background: rgba(128,128,128,0.18); overflow: hidden; }
    .bv-scorepart-fill { display: block; height: 100%; border-radius: 2px; background: linear-gradient(90deg, rgba(0,240,255,0.25), rgba(0,240,255,0.75)); }
    .bv-scorepart-top .bv-scorepart-fill { background: linear-gradient(90deg, rgba(74,222,128,0.5), rgba(74,222,128,0.95)); }
    .bv-scorepart-top .bv-scorepart-id { color: #4ade80; }
    .bv-scorepart-val { min-width: 30px; text-align: right; font-weight: 700; color: #aaa; }
    """
    js = """
    // Mirror of R63 render logic
    function renderScoreParts(parts) {
      var mapped = (parts || []).map(function(sb){
        var c = Number(sb.contribution || sb.score || 0) || 0;
        return { id: sb.rule_id || sb.id || '?', c: c };
      });
      var maxC = 0;
      mapped.forEach(function(s){ if (s.c > maxC) maxC = s.c; });
      mapped.sort(function(a, b){ return b.c - a.c; });
      return mapped.length ? mapped.map(function(s, i){
        var w = maxC > 0 ? Math.max(12, Math.round(s.c / maxC * 100)) : 12;
        return '<div class="bv-scorepart' + (i === 0 ? ' bv-scorepart-top' : '') + '">' +
               '<span class="bv-scorepart-id">' + s.id + '</span>' +
               '<span class="bv-scorepart-bar"><span class="bv-scorepart-fill" style="width:' + w + '%"></span></span>' +
               '<span class="bv-scorepart-val">+' + Math.round(s.c) + '</span></div>';
      }).join('') : '';
    }
    window.renderScoreParts = renderScoreParts;
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        parts = [{"rule_id": "BV05", "contribution": 10},
                 {"rule_id": "BV02", "contribution": 40},
                 {"rule_id": "BV09", "contribution": 20}]
        r = await page.evaluate("(parts) => renderScoreParts(parts)", parts)
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r)

        # Sorted descending: BV02(40) → BV09(20) → BV05(10)
        ids = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-id')].map(e => e.textContent)")
        vals = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-val')].map(e => e.textContent)")
        print(f"ids={ids} vals={vals}")
        assert ids == ["BV02", "BV09", "BV05"], "should sort by contribution desc"
        assert vals == ["+40", "+20", "+10"]

        # Top one gets bv-scorepart-top
        top_cls = await page.evaluate("document.querySelector('.bv-scorepart').className")
        print(f"top cls={top_cls}")
        assert "bv-scorepart-top" in top_cls

        # Max-relative width: top = 100%, second = 50%, third = 25%
        widths = await page.evaluate("[...document.querySelectorAll('.bv-scorepart-fill')].map(e => e.style.width)")
        print(f"widths={widths}")
        assert widths[0] == "100%"
        assert widths[1] == "50%"
        assert widths[2] == "25%"

        # Empty parts → empty string, no crash
        r2 = await page.evaluate("renderScoreParts([])")
        assert r2 == "", "empty parts → no html"

        print("[OK] R63 score breakdown sorted by contribution + weight bars")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
