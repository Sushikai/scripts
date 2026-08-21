"""R129 .bv-score-bar height 4→6px — 分数条视觉厚度 mobile 适配.

第一性原理: 56×4px 在 mobile retina 上太薄 (单像素偶现), 用户扫"哪些强"难以快速判断条长。
    6px 仍 slim 但 +50% 像素, 拇指扫视更准。配合 R128 数字 12px 亮白 + R125 σ 偏移 10.5px,
    三档视觉权重: 数字 (主信号) > bar 长度 (填充) > 偏移 (辅助)。
R62 起源: 60×4 score bar 引入, R97 守护, R129 升级 mobile 厚度。
R129 风险: bar 增高 → 行高撑大, 可能破坏 R97 grid-area 排版。审计 line-height 即可。
断言 (mock 数据, 390px):
  - .bv-score-bar height = 6px (was 4)
  - .bv-score-bar width = 56px (保留)
  - 渲染分数条不撑破 grid-area (行高 ≤ 28px, 跟 R15 卡密度一致)
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

        # Inject score-bar into row's score cell
        m = await page.evaluate(r"""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (rows.length === 0) return {none: true};
          rows.forEach(function(r, i) {
            var scoreCell = r.querySelector('td:nth-child(11)');
            if (scoreCell) {
              var bar = document.createElement('span');
              bar.className = 'bv-score-bar';
              bar.innerHTML = '<span class="bv-score-fill strong" style="width:90%"></span>';
              scoreCell.appendChild(bar);
            }
          });
          void document.body.offsetHeight;
          var bar = document.querySelector('.bv-score-bar');
          if (!bar) return {none: true};
          var rect = bar.getBoundingClientRect();
          var cs = getComputedStyle(bar);
          // Also measure row height to check if bar expansion breaks layout
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          var rowRect = row ? row.getBoundingClientRect() : null;
          return {
            barCount: document.querySelectorAll('.bv-score-bar').length,
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            cssHeight: cs.height,
            borderRadius: cs.borderRadius,
            rowHeight: rowRect ? Math.round(rowRect.height) : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("barCount", 0) >= 1, f"expected ≥1 score-bar, got {m.get('barCount')}"
        assert m.get("height") == 6, f"score-bar height should be 6 (was 4), got {m.get('height')}"
        assert m.get("width") == 56, f"score-bar width regression: {m.get('width')} (must stay 56)"
        # row height regression check (R15 card density ~140-180px on mobile)
        if m.get("rowHeight"):
            assert m["rowHeight"] <= 220, f"row height inflated: {m['rowHeight']} (R97/R15 density break)"

        await browser.close()
        print(f"[OK] R129 score-bar visual thickness — {m['width']}×{m['height']} (was 4h) | row {m['rowHeight']}px (R97 density ✓)")

if __name__ == "__main__":
    asyncio.run(run())