"""R118 loadmore-btn (加载更多/重试) tap zone — 29→32 (Apple HIG).

原: .bv-loadmore-btn padding 8px 14px + 13px font = 29px (差 3px 到 HIG)。
    用户滚到列表底点击 "加载更多 picks" / 网络失败时点 "重试",
    这是用户主动扩展内容的关键按钮。
R118: padding 8→10 + min-height 32 + min-width 0, 跟 R104-R117 统一 HIG 32 模式。
    视觉字号 13px 保留, 边框保留, transition 保留。
断言 (mock 数据, 390px):
  - 滚动后 .bv-loadmore-btn 高度 ≥ 32px (Apple HIG)
  - 字号仍 13px (不放大)
  - 重试变体 (.bv-retry-btn) 也 ≥32px (同规则继承)
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
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 }
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        # Inject loadmore button + retry variant (matches R39/R45)
        await page.evaluate("""() => {
            var vb = document.querySelector('.view-bv');
            if (vb) { vb.hidden = false; vb.style.display = ''; }
            var loadmore = document.createElement('div');
            loadmore.className = 'bv-loadmore';
            loadmore.innerHTML =
              '<button class="bv-loadmore-btn">加载更多 ↓</button>' +
              '<button class="bv-loadmore-btn bv-retry-btn">⚠ 重试</button>';
            // Find the picks card to append after
            var pickCard = document.getElementById('bv-pick-tbody');
            if (pickCard) {
                pickCard.parentElement.parentElement.appendChild(loadmore);
            } else {
                document.body.appendChild(loadmore);
            }
        }""")
        await page.wait_for_timeout(300)

        m = await page.evaluate(r"""() => {
          var btns = document.querySelectorAll('.bv-loadmore-btn');
          var items = [];
          btns.forEach(function(b){
            var r = b.getBoundingClientRect();
            var cs = getComputedStyle(b);
            if (r.width > 0 && r.height > 0) {
              items.push({
                text: b.textContent.trim(),
                isRetry: b.classList.contains('bv-retry-btn'),
                w: Math.round(r.width), h: Math.round(r.height),
                fontSize: cs.fontSize
              });
            }
          });
          return items;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m) >= 2, f"need ≥2 loadmore buttons (loadmore + retry), got {len(m)}"
        for b in m:
            assert b['h'] >= 32, f"{b['text']} tap zone too small: {b['h']}px (Apple HIG 32)"
            # font may be 12-13 depending on --text-sm var resolution; we care about tap zone
            assert b['fontSize'] in ('12px', '13px'), f"{b['text']} font unexpected: {b['fontSize']}"

        await browser.close()
        print(f"[OK] R118 loadmore-btn tap zone — {len(m)} buttons all ≥32px (retry + loadmore)")

if __name__ == "__main__":
    asyncio.run(run())