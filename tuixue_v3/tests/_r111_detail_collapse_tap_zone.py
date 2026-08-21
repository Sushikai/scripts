"""R111 详情内 ✕ 收起按钮 tap zone — 22→32px.

原: R59 设计 padding 2px + font 11px → 实际 ~22px 高。
    用户展开 detail 后要找回 ✕ 收起, 22px 拇指命中困难。
R111: padding 2→6 + min-height:32 + min-width:0 防全局 button 40px 强制拉宽。
    视觉字号不变 (11px), tap zone 撑到 32+, 跟 R104/R105/R106 一致。
断言 (mock 数据, 390px):
  - 展开 detail 后 .bv-detail-collapse 高度 ≥ 32px
  - 字号仍 11px (不放大)
  - detail-row 整体不显著增长 (padding-top/bottom 不变)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02'], score:65,
      change_pct:5.2, amount_yi:33.1, volume_ratio:1.5, turnover_pct:3.5, seal_ratio:0.4,
      sector:'安防', first_time:'10:30', phase:'close', burst_count:1 }
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
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)

        # expand second row (NOT is-top, which R101 keeps collapsed on mobile)
        try:
            await page.click("#bv-pick-tbody tr.bv-row:not(.is-top)", timeout=2000)
            await page.wait_for_timeout(500)
        except Exception:
            await page.evaluate("""() => {
                var tbody = document.getElementById('bv-pick-tbody');
                var row = document.querySelector('#bv-pick-tbody tr.bv-row:not(.is-top)');
                if (tbody && row) {
                    var ev = new MouseEvent('click', {bubbles:true, cancelable:true});
                    Object.defineProperty(ev, 'target', {value: row, enumerable:true});
                    tbody.onclick(ev);
                }
            }""")
            await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var drs = document.querySelectorAll('tr.bv-detail-row');
          var hidden = [];
          drs.forEach(function(dr){ hidden.push(dr.hasAttribute('hidden')); });
          var visibleDr = null;
          drs.forEach(function(dr){ if (!dr.hasAttribute('hidden')) visibleDr = dr; });
          var btn = visibleDr ? visibleDr.querySelector('.bv-detail-collapse') : null;
          if (!btn) return {none:true, drs: drs.length, hiddenList: hidden};
          var r = btn.getBoundingClientRect();
          var cs = getComputedStyle(btn);
          return {
            btn: { w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize, text: btn.textContent.trim() },
            detailRowH: visibleDr ? Math.round(visibleDr.getBoundingClientRect().height) : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "btn" in m, f"detail-collapse button not found"
        assert m["btn"]["h"] >= 32, f"tap zone too small: {m['btn']['h']}px (Apple HIG 32)"
        assert m["btn"]["fontSize"] == "11px", f"font should stay 11px, got {m['btn']['fontSize']}"
        # detail row should not bloat massively (R111 only adds to button, not the row padding)
        if m["detailRowH"]:
            assert m["detailRowH"] <= 400, f"detail row too tall: {m['detailRowH']}px"

        await browser.close()
        print(f"[OK] R111 detail-collapse tap zone — {m['btn']['w']}×{m['btn']['h']}px")

if __name__ == "__main__":
    asyncio.run(run())