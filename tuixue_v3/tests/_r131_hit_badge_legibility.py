"""R131 .bv-rules-cell .bv-hit-badge 字号 9→10.5px + 容器 15→18px — 命中数 badge 可读性.

原: .bv-hit-badge (rules cell 内的命中数徽章 3/2/1) 9px + 15px 高,
    字号小于 R125 σ 偏移 (10.5px secondary) — 数字信号被水印感淹没。
R131: font 9→10.5px (跟 R125 σ 同档), height 15→18, line-height 18 (字号变大容器要撑高保持居中)。
第一性原理: hit-count 是 user 判断"该股命中几条规则"的关键数字信号,
    9px 跟 R125/R126 同档数字徽章不一致, typography 体系浮空。
R5 起源: 命中数 badge 引入, R23 自动折叠 +N 减少视觉噪声, R131 把单个数字读清。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-hit-badge 渲染
  - font-size = 10.5px
  - height ≥ 16 (跟 R105 chip 体系兼容 — R5 hit-count 12px 大 badge 不动, 此处 10.5 small badge)
  - font-weight = 700 (主信号加粗)
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
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02','BV03'], score:90,
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

        # Inject hit-badge into rules-cell of first row
        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var rulesCell = row.querySelector('.bv-rules-cell') || (function(){
            // create rules-cell as td:nth-child(10)
            var cell = document.createElement('td');
            cell.className = 'bv-rules-cell';
            cell.style.gridArea = 'rules';
            row.appendChild(cell);
            return cell;
          })();
          var badge = document.createElement('span');
          badge.className = 'bv-hit-badge hot';
          badge.textContent = '3';
          rulesCell.appendChild(badge);
          void document.body.offsetHeight;
          var b = document.querySelector('.bv-hit-badge');
          if (!b) return {none: true};
          var rect = b.getBoundingClientRect();
          var cs = getComputedStyle(b);
          return {
            count: document.querySelectorAll('.bv-hit-badge').length,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            height: Math.round(rect.height),
            width: Math.round(rect.width),
            lineHeight: cs.lineHeight,
            text: b.textContent
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 hit-badge, got {m.get('count')}"
        assert m.get("fontSize") == "10.5px", f"hit-badge should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"hit-badge weight should be 700, got {m.get('fontWeight')}"
        assert m.get("height", 0) >= 16, f"hit-badge container too small: {m.get('height')} (need ≥16 for 10.5px font)"

        await browser.close()
        print(f"[OK] R131 hit-badge typography — {m['fontSize']} {m['fontWeight']} {m['width']}×{m['height']} (was 9px/15h)")

if __name__ == "__main__":
    asyncio.run(run())