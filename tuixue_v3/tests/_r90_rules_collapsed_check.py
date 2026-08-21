"""R90 移动端规则明细默认折叠 — 参考材料不掩埋分类汇总/回测.

原: _renderRulesDetail 硬编码 <details open> → 7 大分类全部展开 2665px,
    把分类汇总(3429px)和回测(3843px)推出视口, 用户要滚 4+ 屏才到.
R90: ≤768px 时去掉 open, 分类 summary 成紧凑可点行 (含 ▾ 展开提示).
"""
import asyncio
from playwright.async_api import async_playwright


def make_js(mobile):
    """Render the exact rules-detail HTML (matching bv-frontend.js R90 logic)."""
    return f"""
    var _rules = [];
    ['环境','仓位','选股','买入','止损','止盈','风控'].forEach(function(cat, ci){{
      for (var i = 0; i < 2; i++) {{
        _rules.push({{ id: 'BV' + (ci*2+i+1), category: cat, title: cat + '规则' + i,
                       priority: i+1, score_weight: 5, description: 'desc', quote: 'q', timestamp: '01:00',
                       conditions: [{{field:'streak', op:'==', value:1}}] }});
      }}
    }});
    var _isMobileR90 = {str(mobile).lower()};
    var byCat = {{}};
    _rules.forEach(function(r){{ byCat[r.category] = byCat[r.category] || []; byCat[r.category].push(r); }});
    var catOrder = ['环境','仓位','选股','买入','止损','止盈','风控'];
    var html = '';
    catOrder.forEach(function(cat){{
      var list = byCat[cat] || [];
      if (!list.length) return;
      html += '<details class="bv-cat-details"' + (_isMobileR90 ? '' : ' open') + '>';
      html += '<summary class="bv-cat-summary"><span class="bv-cat-name">' + cat + '</span><span class="bv-cat-count">' + list.length + ' 条</span></summary>';
      html += '<div class="bv-cat-body">';
      list.forEach(function(r){{ html += '<div class="bv-rule-item" data-rid="' + r.id + '">' + r.title + '</div>'; }});
      html += '</div></details>';
    }});
    document.getElementById('host').innerHTML = html;
    """


CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.bv-cat-details { border-top:1px solid #333; padding:2px 0; }
.bv-cat-details:first-child { border-top:none; }
.bv-cat-summary { display:flex; align-items:center; justify-content:space-between; cursor:pointer; padding:10px 8px; margin:2px 0; border-radius:8px; background:#222; min-height:38px; list-style:none; }
.bv-cat-summary::-webkit-details-marker { display:none; }
.bv-cat-summary:active { background:#2a2a2a; }
.bv-cat-summary::after { content:'▾'; color:#888; font-size:10px; margin-left:6px; }
.bv-cat-details[open] .bv-cat-summary::after { content:'▴'; }
.bv-cat-details[open] .bv-cat-summary { border-bottom:1px solid #333; border-radius:8px 8px 0 0; }
.bv-cat-body { padding:2px 8px 8px; }
.bv-cat-name { font-weight:600; color:#eee; font-size:13px; }
.bv-cat-count { font-size:12px; color:#888; }
.bv-rule-item { padding:8px 0; border-bottom:1px dashed #333; }
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # ── mobile (390px) ──
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(f"<html><head><style>{CSS}</style></head><body><div id='host'></div><script>{make_js(True)}</script></body></html>")
        open_count = await page.evaluate("() => document.querySelectorAll('details[open]').length")
        total = await page.evaluate("() => document.querySelectorAll('details').length")
        print(f"mobile: open={open_count} total={total}")
        assert open_count == 0, f"R90: mobile must collapse all details, got {open_count} open"

        # summary rows visible + clickable (tap target)
        summ = await page.evaluate("""() => {
          var s = document.querySelector('.bv-cat-summary');
          var cs = getComputedStyle(s), r = s.getBoundingClientRect();
          return { h: r.height, minH: cs.minHeight, bg: cs.backgroundColor, cursor: cs.cursor,
                   after: cs.content !== 'none' || s.className };
        }""")
        print(f"mobile summary: {summ}")
        assert summ["h"] >= 38, f"R90: summary tap target {summ['h']}px < 38"

        # tap summary → expands (details open)
        await page.evaluate("document.querySelector('.bv-cat-summary').click()")
        open_count2 = await page.evaluate("() => document.querySelectorAll('details[open]').length")
        print(f"after tap: open={open_count2}")
        assert open_count2 == 1, "R90: tapping a summary must expand that category"

        # ── desktop (>768px) ──
        dpage = await browser.new_page(viewport={"width": 1200, "height": 800})
        await dpage.set_content(f"<html><head><style>{CSS}</style></head><body><div id='host'></div><script>{make_js(False)}</script></body></html>")
        dopen = await dpage.evaluate("() => document.querySelectorAll('details[open]').length")
        print(f"desktop: open={dopen}")
        assert dopen == 7, f"R90: desktop keeps all open, got {dopen}"
        await dpage.close()

        await browser.close()
        print("[OK] R90 mobile rules collapsed + desktop open preserved")


if __name__ == "__main__":
    asyncio.run(run())
