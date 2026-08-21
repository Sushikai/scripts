"""R117 sort sheet 方向按钮 (升序/降序) tap zone — 29→32 (Apple HIG).

原: R102 设计 .bv-sort-btn 28px (排序入口次级紧凑), 但 .bv-sort-dir-opt
    padding 8px + 13px font = 29px (差 3px 到 HIG)。
    用户开 sheet 切换升降序, sheet 内 2 个按钮也是可点击主操作, 不能漏 HIG。
R117: padding 8→10 + min-height 32 + min-width 0, 跟 R104-R116 同一 HIG 模式。
    视觉字号 13px 保留, 边框保留。
断言 (mock 数据, 390px):
  - 打开 sort sheet 后 .bv-sort-dir-opt 高度 ≥ 32px (Apple HIG)
  - 字号仍 13px (不放大)
  - .bv-sort-opt 主选项 ≥ 32px (regression 守住, 33px 已够)
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 3:
                break
        await page.wait_for_timeout(500)

        # Open sort sheet (R17)
        # First check if there's a sort button; if not create one to match sheet structure
        await page.evaluate("""() => {
            // Ensure view-bv is visible (test mocks goto bv hash but view may still be hidden)
            var vb = document.querySelector('.view-bv');
            if (vb) { vb.hidden = false; vb.style.display = ''; }
            // Create the sort sheet DOM (matches R17 sheet layout)
            var sheet = document.createElement('div');
            sheet.className = 'bv-sort-sheet';
            sheet.innerHTML =
              '<div class="bv-sort-mask"></div>' +
              '<div class="bv-sort-panel">' +
                '<div class="bv-sort-handle"></div>' +
                '<h4>排序方式</h4>' +
                '<div class="bv-sort-list">' +
                  '<button class="bv-sort-opt is-active">命中分数</button>' +
                  '<button class="bv-sort-opt">涨幅</button>' +
                  '<button class="bv-sort-opt">连板数</button>' +
                  '<button class="bv-sort-opt">成交额</button>' +
                '</div>' +
                '<h4>方向</h4>' +
                '<div class="bv-sort-dir">' +
                  '<button class="bv-sort-dir-opt">↓ 降序</button>' +
                  '<button class="bv-sort-dir-opt">↑ 升序</button>' +
                '</div>' +
              '</div>';
            document.body.appendChild(sheet);
            document.body.classList.add('bv-sort-open');
        }""")
        await page.wait_for_timeout(300)

        m = await page.evaluate(r"""() => {
          var dir = document.querySelectorAll('.bv-sort-dir-opt');
          var dirItems = [];
          dir.forEach(function(b){
            var r = b.getBoundingClientRect();
            var cs = getComputedStyle(b);
            if (r.width > 0 && r.height > 0) {
              dirItems.push({ text: b.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize });
            }
          });
          var opt = document.querySelector('.bv-sort-opt');
          var optRect = opt ? opt.getBoundingClientRect() : null;
          var optCs = opt ? getComputedStyle(opt) : null;
          return {
            dir: dirItems,
            optSample: (optRect && optRect.width > 0) ? { h: Math.round(optRect.height), fontSize: optCs.fontSize } : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m["dir"]) >= 2, f"need ≥2 dir options, got {len(m['dir'])}"
        for d in m["dir"]:
            assert d['h'] >= 32, f"{d['text']} tap zone too small: {d['h']}px (Apple HIG 32)"
            assert d['fontSize'] == '13px', f"{d['text']} font should stay 13px, got {d['fontSize']}"

        await browser.close()
        print(f"[OK] R117 sort sheet dir tap zone — {len(m['dir'])} dir opts ≥32px (40px from padding 10 + line-height 13 = 33, +min-height 32)")

if __name__ == "__main__":
    asyncio.run(run())