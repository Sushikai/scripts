"""R123 .bv-rules-cell .chip + .bv-cond-chip 字号 10→11px — typography legibility 升级.

原: R105 把规则 chip tap zone 撑到 32px (HIG 最低), 但 font 仍 10px → 视觉水印感。
R123: font-size 10→11px (跟 R108 meta / R119 detail-label / R120 title-sub / R121 phase-ttl
    / R122 sector-link typography 体系 11-11.5px 一致档位)。
    R5 .bv-hit-count 12px chip 不动 (它是主操作徽章, 跟规则 chip 视觉层级清晰)。
    视觉: chip 字号 11px, 仍小于 12px ops, 不抢主戏。
断言 (mock 数据, 390px):
  - .bv-rules-cell .chip font-size = 11px
  - .bv-cond-chip font-size = 11px
  - 至少一个 chip 实际渲染 (确认 DOM 存在)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'...', score_weight:8, conditions:[], quote:'...', timestamp:'01:00' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'01:12', score_weight:10, weight:10, value:20 } },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02','BV03'], score:65,
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

        m = await page.evaluate(r"""() => {
          // 1) bv-rules-cell chip (in row card)
          var cellChip = document.querySelector('.view-bv .bv-rules-cell .chip');
          // 2) bv-cond-chip (rule detail block)
          var condChip = document.querySelector('.view-bv .bv-cond-chip');
          // 3) inject one cond chip if not present, so we can measure its style
          if (!condChip) {
            var det = document.querySelector('.bv-detail-collapse') || document.querySelector('.view-bv');
            if (det) {
              var span = document.createElement('span');
              span.className = 'bv-cond-chip chip';
              span.textContent = 'streak>=1';
              det.appendChild(span);
              condChip = span;
            }
          }
          // 4) count rules-cell chips total
          var count = document.querySelectorAll('.view-bv .bv-rules-cell .chip').length;
          // 5) tap zone (R105 should still be 32px)
          var rect = cellChip ? cellChip.getBoundingClientRect() : null;
          return {
            chipFont: cellChip ? getComputedStyle(cellChip).fontSize : null,
            condFont: condChip ? getComputedStyle(condChip).fontSize : null,
            chipCount: count,
            chipRect: rect ? {w: Math.round(rect.width), h: Math.round(rect.height)} : null,
            chipText: cellChip ? cellChip.textContent.trim().slice(0, 12) : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("chipFont") == "11px", f"rules-cell chip should be 11px (was 10), got {m.get('chipFont')}"
        assert m.get("condFont") == "11px", f"cond-chip should be 11px (was 10), got {m.get('condFont')}"
        assert m.get("chipCount", 0) >= 1, f"expected ≥1 rules-cell chip, got {m.get('chipCount')}"
        # R105 tap zone regression check
        if m.get("chipRect"):
            assert m["chipRect"]["h"] >= 32, f"tap zone regression: chip h={m['chipRect']['h']} (R105 requires ≥32)"

        await browser.close()
        print(f"[OK] R123 rule-chip typography — chip {m['chipFont']} cond {m['condFont']} (was 10) | tap zone {m['chipRect']['w']}×{m['chipRect']['h']} (R105 32px ✓)")

if __name__ == "__main__":
    asyncio.run(run())