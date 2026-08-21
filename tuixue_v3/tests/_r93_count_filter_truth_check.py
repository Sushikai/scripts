"""R93 计数不能说谎 — 有 _pickFilter 时 count 反映过滤后可见数 (同 R87 footer 口径).

原: _ruleFilter 为空但 _pickFilter 激活时, count 显示 "(扫描 ≥50 / 命中 50)"
    而列表只有 12 只 — 用户看到 12 张卡旁边写 50 命中 = 说谎 (R87 类).
R93: _visC = _filterPicks(_picks).length; 过滤时显示 "(过滤后 12 / 全部 50)".
"""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _pickFilter = 'all';
    var _ruleFilter = null;
    var _phase = 'close';
    var _dataTs = 0;
    var _picks = [];
    for (var i = 0; i < 50; i++) {
      _picks.push({ code: '6000' + (100+i), streak: i < 12 ? 2 : 1, matched_rules: i < 12 ? ['BV01','BV02','BV03'] : ['BV01'] });
    }
    function _filterPicks(arr){
      if (_pickFilter === 'all') return arr;
      if (_pickFilter === 'streak2') return arr.filter(function(p){ return (p.streak || 0) >= 2; });
      return arr;
    }
    function renderCount(){
      var _visC = _filterPicks(_picks).length;
      var _tsStr = '';
      var _cntTxt = (_visC < _picks.length)
        ? ('(过滤后 ' + _visC + ' / 全部 ' + _picks.length + _tsStr + ')')
        : ('(扫描 ' + (_picks.length ? '>=' + _picks.length : 0) + ' / 命中 ' + _picks.length + _tsStr + ')');
      document.getElementById('count').innerHTML = _cntTxt;
    }
    window.renderCount = renderCount;
    window.setFilter = function(f){ _pickFilter = f; };
    """
    html = ("<!DOCTYPE html><html><body><div id='count'></div>"
            "<script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # no filter → "扫描 ≥50 / 命中 50"
        await page.evaluate("renderCount()")
        t0 = await page.evaluate("document.getElementById('count').textContent")
        print(f"no filter: {t0!r}")
        assert "命中 50" in t0

        # streak2 filter (12) → "过滤后 12 / 全部 50"
        await page.evaluate("setFilter('streak2')")
        await page.evaluate("renderCount()")
        t1 = await page.evaluate("document.getElementById('count').textContent")
        print(f"filter streak2: {t1!r}")
        assert "过滤后 12 / 全部 50" in t1
        assert "命中 50" not in t1, "R93: must NOT show raw 命中 when filtered"

        print("[OK] R93 count reflects filtered visibility")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
