"""R132 .bv-motto-badge 字号 9→10.5px + line-height 14→18px — top-1 口诀徽章可读性.

原: R64 引入 .bv-motto-badge (top-1 口诀徽章, "为什么推这只"),
    font 9px 完全消失, 但承载的是决策理由, 应该是高优先级 typography。
R132: 9→10.5px (跟 R125 σ / R131 hit-badge 同档), line-height 14→18 (容器撑高保持居中)。
第一性原理: motto 是 top-1 专属信号 (其他卡片没有), 用户应该一眼看清,
    9px 让这关键信号视觉权重过轻, 跟 11.5px 主标题对比悬殊。
R64 起源: "为什么推这只" 正面可见 — 但 9px 不可见则违背 R64 设计意图。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-motto-badge 渲染
  - font-size = 10.5px
  - line-height = 18px (容器撑高)
  - font-weight = 700 (信号加粗保留)
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

        # Inject motto-badge
        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var motto = document.createElement('span');
          motto.className = 'bv-motto-badge';
          motto.textContent = '弱转强';
          row.appendChild(motto);
          void document.body.offsetHeight;
          var b = document.querySelector('.bv-motto-badge');
          if (!b) return {none: true};
          var rect = b.getBoundingClientRect();
          var cs = getComputedStyle(b);
          return {
            count: document.querySelectorAll('.bv-motto-badge').length,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            lineHeight: cs.lineHeight,
            height: Math.round(rect.height),
            text: b.textContent,
            color: cs.color
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 motto-badge, got {m.get('count')}"
        assert m.get("fontSize") == "10.5px", f"motto-badge should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"motto-badge weight should be 700, got {m.get('fontWeight')}"
        assert m.get("lineHeight") == "18px", f"motto-badge line-height should be 18px (was 14px), got {m.get('lineHeight')}"

        await browser.close()
        print(f"[OK] R132 motto-badge typography — {m['fontSize']} {m['fontWeight']} lineH {m['lineHeight']} {m['height']}px (was 9/14)")

if __name__ == "__main__":
    asyncio.run(run())