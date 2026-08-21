"""R53 右滑露跳个股."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__gotoCalled = null;
    window.gotoStock = function(code) { window.__gotoCalled = code; };
    var tbody = document.querySelector('tbody');
    var _swipeStartX = 0, _swipeStartY = 0, _swipeTr = null, _swipeDir = null;
    tbody.addEventListener('touchstart', function(ev){
      var tr = ev.target.closest('tr.bv-row');
      if (!tr || !tr.dataset.code) return;
      _swipeStartX = ev.touches[0].clientX;
      _swipeStartY = ev.touches[0].clientY;
      _swipeTr = tr;
      _swipeDir = null;
    }, {passive: true});
    tbody.addEventListener('touchmove', function(ev){
      if (!_swipeTr) return;
      var t = ev.touches[0];
      var dx = t.clientX - _swipeStartX;
      var dy = t.clientY - _swipeStartY;
      if (!_swipeDir) {
        if (Math.abs(dx) < 12 && Math.abs(dy) < 12) return;
        _swipeDir = (Math.abs(dx) > Math.abs(dy)) ? 'h' : 'v';
      }
      if (_swipeDir === 'h' && dx > 0) {
        var offR = Math.min(100, dx);
        _swipeTr.style.transform = 'translateX(' + offR + 'px)';
        _swipeTr.style.transition = 'none';
        _swipeTr.dataset.swipeOff = offR;
        if (offR > 30) _swipeTr.classList.add('swiping-right');
      }
    }, {passive: true});
    tbody.addEventListener('touchend', function(){
      if (!_swipeTr) return;
      var off = parseFloat(_swipeTr.dataset.swipeOff || 0);
      if (_swipeDir === 'h' && off > 60) {
        var codeR = _swipeTr.dataset.code;
        if (codeR && typeof gotoStock === 'function') gotoStock(codeR);
      }
      _swipeTr.classList.remove('swiping-right');
      _swipeTr.style.transform = '';
      _swipeTr.style.transition = '';
      delete _swipeTr.dataset.swipeOff;
      _swipeTr = null;
      _swipeDir = null;
    });
    """
    html = "<!DOCTYPE html><html><body>"
    html += '<table><tbody><tr class="bv-row" data-code="600036"><td>D</td></tr></tbody></table>'
    html += "<script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500}, has_touch=True)
        await page.set_content(html)

        await page.evaluate("""
        const tr = document.querySelector('tr.bv-row');
        const rect = tr.getBoundingClientRect();
        function fire(name, x, y) {
          const t = new Touch({identifier:1, target:tr, clientX:x, clientY:y});
          const ev = new TouchEvent(name, {touches:[t], targetTouches:[t], changedTouches:[t], bubbles:true});
          tr.dispatchEvent(ev);
        }
        fire('touchstart', rect.left+50, rect.top+10);
        fire('touchmove', rect.left+150, rect.top+10);
        window.__off = document.querySelector('.bv-row').style.transform;
        window.__cls = document.querySelector('.bv-row').className;
        fire('touchend', rect.left+150, rect.top+10);
        """)

        off = await page.evaluate("window.__off")
        cls = await page.evaluate("window.__cls")
        called = await page.evaluate("window.__gotoCalled")
        print(f"during right-swipe: transform={off!r} class={cls!r}")
        print(f"after: gotoStock called with={called!r}")
        assert "translateX(100px)" in off
        assert "swiping-right" in cls
        assert called == "600036"

        # Reset
        t_after = await page.evaluate("document.querySelector('.bv-row').style.transform")
        c_after = await page.evaluate("document.querySelector('.bv-row').className")
        print(f"after: transform={t_after!r} class={c_after!r}")
        assert t_after == ""
        assert "swiping-right" not in c_after

        print("[OK] R53 right-swipe-to-goto works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())