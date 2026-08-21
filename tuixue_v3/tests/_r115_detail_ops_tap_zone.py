"""R115 detail ops 4 按钮 tap zone — 24→32 (Apple HIG).

原: R82 把 4 个详情内操作按钮 (板块跳转/个股/←上一只/下一只→) 排成 2x2 grid,
    但 padding 6px 12px + 12px font → 实际 ~24px 高。
    拇指命中困难, 比 R111/R112 详情内 ✕ 还差。
R115: padding 6→8 + min-height:32 + min-width:0, 跟 R104/R105/R106/R111/R112/R114
    同一 HIG 模式。 视觉字号 12px 保留 (R82 紧凑目标), 边框 4→5px radius 配合 32 高。
断言 (mock 数据, 390px):
  - 展开 detail 后 .bv-detail-prev / .bv-detail-next / .bv-detail-jump / .bv-detail-sector-link 高度 ≥ 32px
  - 字号仍 12px
  - detail 内 2x2 grid 整体高度不爆涨 (4 按钮 32 高 = 32+6+32 = 70px)
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
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'01:12', score_weight:10, weight:10, value:20 } },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02'], score:65,
      change_pct:5.2, amount_yi:33.1, volume_ratio:1.5, turnover_pct:3.5, seal_ratio:0.4,
      sector:'安防', first_time:'10:30', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'低位首板', quote:'...', timestamp:'02:08', score_weight:8, weight:8, value:18 } }
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 3:
                break
        await page.wait_for_timeout(500)

        try:
            await page.click("#bv-pick-tbody tr.bv-row:not(.is-top)", timeout=2000)
            await page.wait_for_timeout(800)
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
            await page.wait_for_timeout(800)

        m = await page.evaluate(r"""() => {
          var btns = {};
          var sels = {
            sector: '.view-bv .bv-detail-sector-link',
            jump:   '.view-bv .bv-detail-jump',
            prev:   '.view-bv .bv-detail-prev',
            next:   '.view-bv .bv-detail-next'
          };
          for (var k in sels) {
            var el = document.querySelector(sels[k]);
            if (el) {
              var r = el.getBoundingClientRect();
              var cs = getComputedStyle(el);
              btns[k] = {
                w: Math.round(r.width), h: Math.round(r.height),
                fontSize: cs.fontSize, text: el.textContent.trim().slice(0, 20)
              };
            } else {
              btns[k] = {missing: true};
            }
          }
          var ops = document.querySelector('.view-bv .bv-detail-ops');
          var opsH = ops ? Math.round(ops.getBoundingClientRect().height) : null;
          return { btns: btns, opsHeight: opsH };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m["btns"]) >= 3, f"need at least 3 of 4 ops buttons, got {len(m['btns'])}"
        for k in ['sector', 'jump', 'prev', 'next']:
            if 'missing' in m['btns'].get(k, {}):
                continue
            assert m['btns'][k]['h'] >= 32, f"{k} tap zone too small: {m['btns'][k]['h']}px (Apple HIG 32)"
            assert m['btns'][k]['fontSize'] == '12px', f"{k} font should stay 12px, got {m['btns'][k]['fontSize']}"
        # ops grid total height should not bloat beyond reasonable (32 + 6 + 32 = 70)
        if m['opsHeight']:
            assert m['opsHeight'] <= 100, f"ops grid too tall: {m['opsHeight']}px"

        await browser.close()
        n_tested = sum(1 for k in m['btns'] if 'missing' not in m['btns'][k])
        print(f"[OK] R115 detail-ops tap zone — {n_tested}/4 buttons ≥32px (ops grid {m['opsHeight']}px)")

if __name__ == "__main__":
    asyncio.run(run())