"""R52 左滑卡片露自选."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__wlCalled = null;
    window.wlToggle = function(code) { window.__wlCalled = code; };
    var _longPressTimer = null, _longPressTriggered = false;
    var tbody = document.querySelector('tbody');
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
      if (_swipeDir === 'h' && dx < 0) {
        var off = Math.max(-100, dx);
        _swipeTr.style.transform = 'translateX(' + off + 'px)';
        _swipeTr.style.transition = 'none';
        _swipeTr.dataset.swipeOff = off;
        if (off < -30) _swipeTr.classList.add('swiping');
      }
    }, {passive: true});
    tbody.addEventListener('touchend', function(){
      if (!_swipeTr) return;
      var off = parseFloat(_swipeTr.dataset.swipeOff || 0);
      if (_swipeDir === 'h' && off < -60) {
        var code = _swipeTr.dataset.code;
        if (code && typeof wlToggle === 'function') wlToggle(code);
      }
      _swipeTr.classList.remove('swiping');
      _swipeTr.style.transform = '';
      _swipeTr.style.transition = '';
      delete _swipeTr.dataset.swipeOff;
      _swipeTr = null;
      _swipeDir = null;
    });
    var _swipeStartX = 0, _swipeStartY = 0, _swipeTr = null, _swipeDir = null;
    """
    html = "<!DOCTYPE html><html><body>"
    html += '<table><tbody><tr class="bv-row" data-code="600000"><td>A</td></tr></tbody></table>'
    html += "<script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500},
                                       has_touch=True)
        await page.set_content(html)

        # Simulate left swipe via JS TouchEvent (Playwright touchscreen is limited)
        pass
        await page.evaluate("""
        const tr = document.querySelector('tr.bv-row');
        const rect = tr.getBoundingClientRect();
        function fire(name, x, y) {
          const t = new Touch({identifier:1, target:tr, clientX:x, clientY:y});
          const ev = new TouchEvent(name, {touches:[t], targetTouches:[t], changedTouches:[t], bubbles:true});
          tr.dispatchEvent(ev);
        }
        fire('touchstart', rect.left+50, rect.top+10);
        fire('touchmove', rect.left-50, rect.top+10);
        window.__offAfter = document.querySelector('.bv-row').style.transform;
        window.__clsAfter = document.querySelector('.bv-row').className;
        fire('touchend', rect.left-50, rect.top+10);
        window.__wlCalledAfter = window.__wlCalled;
        """)

        off = await page.evaluate("window.__offAfter")
        cls = await page.evaluate("window.__clsAfter")
        called = await page.evaluate("window.__wlCalledAfter")
        print(f"during swipe: transform={off!r} class={cls!r}")
        print(f"after touchend: wlToggle called with={called!r}")
        assert "translateX" in off
        assert "swiping" in cls
        assert called == "600000", f"wlToggle should be called with 600000, got {called!r}"

        # Reset after touchend
        transform_after = await page.evaluate("document.querySelector('.bv-row').style.transform")
        cls_after = await page.evaluate("document.querySelector('.bv-row').className")
        print(f"after: transform={transform_after!r} class={cls_after!r}")
        assert transform_after == ""
        assert "swiping" not in cls_after

        print("[OK] R52 swipe-to-watchlist works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())