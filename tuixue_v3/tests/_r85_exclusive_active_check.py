"""R85 互斥选择只能有一个高亮 — 板块过滤生效时筛选条不显示"全部"active.

原: sector 过滤时, sector pill 青色 + 筛选条"全部"青色, 两个 active 矛盾.
R85: 板块生效 → 筛选条全不亮; 清除 → 全部重置.
"""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _ruleFilter = null;
    var _pickFilter = 'all';
    window.getState = function(){ return { rule: _ruleFilter, pick: _pickFilter }; };

    // 模拟 R24 sector 点击 (R85 新版: 板块生效时筛选条全不亮)
    window.sectorToggle = function(key){
      if (_pickFilter === 'sector:' + key) { _pickFilter = 'all'; }
      else { _pickFilter = 'sector:' + key; }
      var bar = document.getElementById('bv-filter-bar');
      if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
        var isAll = c.getAttribute('data-filter') === 'all';
        c.classList.toggle('is-active', _pickFilter === 'all' && isAll);
      });
    };

    // 模拟 R85 clear link: 清除全部
    window.clearAll = function(){
      _ruleFilter = null;
      _pickFilter = 'all';
      var bar = document.getElementById('bv-filter-bar');
      if (bar) bar.querySelectorAll('.bv-filter-chip').forEach(function(c){
        c.classList.toggle('is-active', c.getAttribute('data-filter') === 'all');
      });
      var sb = document.getElementById('bv-sector-bar');
      if (sb) sb.querySelectorAll('.bv-sector-pill.is-active').forEach(function(p){
        p.classList.remove('is-active');
      });
    };
    """
    html = """<!DOCTYPE html><html><body>
      <div id="bv-filter-bar">
        <button class="bv-filter-chip is-active" data-filter="all">全部</button>
        <button class="bv-filter-chip" data-filter="hot">命中 ≥3</button>
      </div>
      <div id="bv-sector-bar"><span class="bv-sector-pill" data-sector-key="氢能源">氢能源</span></div>
      <script>""" + js + """</script></body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Initial: all active
        a0 = await page.evaluate("document.querySelector('[data-filter=all]').className")
        assert "is-active" in a0

        # Sector filter ON → "all" must NOT be active (single source of active)
        await page.evaluate("sectorToggle('氢能源')")
        a1 = await page.evaluate("document.querySelector('[data-filter=all]').className")
        st1 = await page.evaluate("getState()")
        print(f"after sector ON: all cls={a1} state={st1}")
        assert "is-active" not in a1, "R85: sector active → filter-bar '全部' must not be active"
        assert st1["pick"] == "sector:氢能源"

        # Sector toggle again → back to all, 'all' re-lit
        await page.evaluate("sectorToggle('氢能源')")
        a2 = await page.evaluate("document.querySelector('[data-filter=all]').className")
        print(f"after sector OFF: all cls={a2}")
        assert "is-active" in a2

        # Rule + sector both set → clearAll resets everything
        await page.evaluate("""() => { var j = window; j.sectorToggle('氢能源'); j.clearAll(); }""")
        st2 = await page.evaluate("getState()")
        a3 = await page.evaluate("document.querySelector('[data-filter=all]').className")
        print(f"after clearAll: state={st2} all cls={a3}")
        assert st2 == {"rule": None, "pick": "all"}
        assert "is-active" in a3

        print("[OK] R85 exclusive active state (sector vs filter-bar 'all')")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
