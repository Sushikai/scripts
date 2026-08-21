"""R91 战法哲学 mobile 折叠 — 次要上下文不占主屏 (推票优先).

原: 战法哲学 5 条全渲染 ~210px, 推票卡被推到 top 468 (390px 屏看不到首张卡).
R91: ≤768px 只显示第一条 + "展开 N 条 ▾" 按钮; 点击展开全部; 桌面保持全量.
"""
import asyncio
from playwright.async_api import async_playwright


def make_js(mobile, expanded):
    return f"""
    var _meta = {{ philosophy: [
      '风险控制永远是第一位的',
      '踏空也是成功的交易,只要你严格执行了交易纪律',
      '每天复盘前看一遍自己的交易规则,择时比择股更重要',
      '我只看量价,其他我什么都不看',
      '我买的是技术,卖的是纪律'
    ] }};
    var _creedExpanded = {str(expanded).lower()};
    function esc(s){{ return String(s); }}
    function renderCreed(){{
      var host = document.getElementById('host');
      var isMobile = {str(mobile).lower()};
      var list = _meta.philosophy;
      var html = '<ul class="bv-philo-list">';
      if (isMobile && !_creedExpanded) {{
        html += '<li><span class="bv-philo-dot">•</span>' + esc(list[0]) + '</li>';
        html += '</ul>';
        html += '<button class="bv-creed-more" data-creed-toggle>展开 ' + (list.length - 1) + ' 条 ▾</button>';
      }} else {{
        list.forEach(function(p){{ html += '<li><span class="bv-philo-dot">•</span>' + esc(p) + '</li>'; }});
        html += '</ul>';
        if (isMobile) html += '<button class="bv-creed-more" data-creed-toggle>收起 ▴</button>';
      }}
      host.innerHTML = html;
      var toggle = host.querySelector('[data-creed-toggle]');
      if (toggle) toggle.addEventListener('click', function(){{
        _creedExpanded = !_creedExpanded;
        renderCreed();
      }});
    }}
    window.renderCreed = renderCreed;
    renderCreed();
    """

CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.bv-philo-list { margin:0; padding-left:1.2rem; color:#eee; font-size:12px; line-height:1.7; }
.bv-philo-list li { padding:2px 0; }
.bv-philo-dot { color:#00e0ff; margin-right:6px; font-weight:700; }
.bv-creed-more { display:block; margin:6px 0 0; padding:8px 12px; border-radius:6px; background:#222; color:#00e0ff; border:1px solid #444; font-size:12px; font-weight:600; cursor:pointer; width:100%; text-align:left; }
.bv-creed-more:active { background:#2a2a2a; }
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # ── mobile collapsed ──
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(f"<html><head><style>{CSS}</style></head><body><div id='host'></div><script>{make_js(True, False)}</script></body></html>")
        st = await page.evaluate("""() => {
          var lis = document.querySelectorAll('.bv-philo-list li').length;
          var more = document.querySelector('.bv-creed-more');
          var box = more ? more.getBoundingClientRect() : null;
          return { liCount: lis, hasMore: !!more,
                   moreText: more ? more.textContent.trim() : null,
                   moreH: box ? Math.round(box.height) : 0 };
        }""")
        print(f"mobile collapsed: {st}")
        assert st["liCount"] == 1, f"R91: collapsed shows 1, got {st['liCount']}"
        assert st["hasMore"] and st["moreText"] == "展开 4 条 ▾"
        assert st["moreH"] >= 30, f"R91: more btn tap target {st['moreH']}px"

        # tap → expands
        await page.evaluate("document.querySelector('.bv-creed-more').click()")
        st2 = await page.evaluate("""() => {
          return { liCount: document.querySelectorAll('.bv-philo-list li').length,
                   moreText: document.querySelector('.bv-creed-more').textContent.trim() };
        }""")
        print(f"after tap: {st2}")
        assert st2["liCount"] == 5, f"R91: expanded shows 5, got {st2['liCount']}"
        assert st2["moreText"] == "收起 ▴"

        # ── desktop keeps full ──
        dpage = await browser.new_page(viewport={"width": 1200, "height": 800})
        await dpage.set_content(f"<html><head><style>{CSS}</style></head><body><div id='host'></div><script>{make_js(False, False)}</script></body></html>")
        dli = await dpage.evaluate("() => document.querySelectorAll('.bv-philo-list li').length")
        dbtn = await dpage.evaluate("() => !!document.querySelector('.bv-creed-more')")
        print(f"desktop: liCount={dli} hasMoreBtn={dbtn}")
        assert dli == 5, f"R91: desktop full list, got {dli}"
        assert not dbtn, "R91: desktop no toggle button"
        await dpage.close()

        await browser.close()
        print("[OK] R91 mobile creed collapsed + desktop full")


if __name__ == "__main__":
    asyncio.run(run())
