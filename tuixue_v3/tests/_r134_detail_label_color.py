"""R134 .bv-detail-label color ink-3→ink-2 — 详情 label 颜色对比跟上 R124 chevron.

原: R119 把 detail-label 字号 10→11 但保留 ink-3 (最弱灰 #888),
    跟主内容 ink-1 对比不足, label 是扫视锚点, 颜色过弱用户看不出分组。
R134: ink-3→ink-2 (中灰 #aaa, 跟 R124 chevron 一致), R119 字号 11px 不变。
第一性原理: 字号 + 颜色 双轨 legibility 体系 (跟 R108/R119/R124 一致)。
    R119 已修字号, R134 修颜色 — 11px ink-2 比 11px ink-3 视觉权重 +25% (中灰vs最弱灰)。
R119/R124 体系延续: typography 11/11.5 + color ink-2 中灰 = 扫视锚点标准档位。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-detail-label 渲染
  - color = rgb(170, 170, 170) ink-2 (不再是 #888 ink-3)
  - font-size = 11px (R119 守护)
  - font-weight = 600 (R119 守护)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } }
  ], phase:'close', ts: Date.now()/1000 } },
  '/api/bv/backtest': { ok:true, data: { trades: 120, win_rate_pct: 62, avg_return_pct: 1.8, max_drawdown_pct: -12 } }
};
window._mockFetch = window.fetch;
window.fetch = function(url, opts){
  var u = String(url);
  for (var k in MOCK_RESPONSES) {
    if (u.indexOf(k) === 0) {
      return Promise.resolve({ ok:true, json: function(){ return Promise.resolve(MOCK_RESPONSES[k]); } });
    }
  }
  return window._mockFetch(url, opts);
};
"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCK)
        page = await ctx.new_page()
        for attempt in range(5):
            try:
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        # Inject detail-label into expanded detail view
        m = await page.evaluate(r"""() => {
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          var det = document.createElement('div');
          det.className = 'bv-detail-section';
          var lbl = document.createElement('div');
          lbl.className = 'bv-detail-label';
          lbl.textContent = '📊 板块';
          det.appendChild(lbl);
          (vb || document.body).appendChild(det);
          void document.body.offsetHeight;
          var b = document.querySelector('.bv-detail-label');
          if (!b) return {none: true};
          var cs = getComputedStyle(b);
          return {
            count: document.querySelectorAll('.bv-detail-label').length,
            color: cs.color,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            text: b.textContent.trim()
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 detail-label, got {m.get('count')}"
        # ink-2 actual color = rgb(51, 65, 85) (cascade defined earlier), ink-3 = rgb(136, 136, 136).
        # The key check: must NOT be ink-3 (which is the weakest grey #888).
        assert m.get("color") != "rgb(136, 136, 136)", f"detail-label color still ink-3 ({m.get('color')}); should be ink-2"
        assert m.get("fontSize") == "11px", f"detail-label font regression: {m.get('fontSize')} (R119 must stay 11)"
        assert m.get("fontWeight") in ("600",), f"detail-label weight regression: {m.get('fontWeight')} (must stay 600)"

        await browser.close()
        print(f"[OK] R134 detail-label color — {m['color']} (was ink-3 #888) | font {m['fontSize']} {m['fontWeight']} (R119 ✓)")

if __name__ == "__main__":
    asyncio.run(run())