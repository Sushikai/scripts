"""R133 .bv-board-badge 字号 9→10.5px + line-height 14→18px — 板块 10cm/20cm 徽章可读性.

原: .bv-board-badge (代码 td 内联的 10cm/20cm 板块徽章) 9px 完全消失,
    但承载的"一眼区分 10cm vs 20cm"是用户优先过滤信号 (memory bv-10cm-priority)。
R133: 9→10.5px (跟 R125 σ / R131 hit-badge / R132 motto-badge 同档), 容器 14→18px。
第一性原理: 10cm vs 20cm 是用户高频过滤维度 (短期 vs 中期趋势),
    badge 9px → 用户根本看不到 → 过滤信号失效。
R-2026-08-20 起源: "10cm/20cm 一眼区分" 设计意图 — 9px 不可见则违背。
断言 (mock 数据, 390px):
  - 至少 1 个 .bv-board-badge 渲染
  - font-size = 10.5px
  - line-height = 18px
  - 10cm 蓝色 + 20cm 红色区分保留
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

        # Inject 10cm + 20cm board badges
        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var b10 = document.createElement('span');
          b10.className = 'bv-board-badge bv-board-10';
          b10.textContent = '10cm';
          row.appendChild(b10);
          var b20 = document.createElement('span');
          b20.className = 'bv-board-badge bv-board-20';
          b20.textContent = '20cm';
          row.appendChild(b20);
          void document.body.offsetHeight;
          var b = document.querySelector('.bv-board-badge');
          var b10el = document.querySelector('.bv-board-10');
          var b20el = document.querySelector('.bv-board-20');
          var rect = b ? b.getBoundingClientRect() : null;
          var cs = b ? getComputedStyle(b) : null;
          return {
            count: document.querySelectorAll('.bv-board-badge').length,
            fontSize: cs ? cs.fontSize : null,
            fontWeight: cs ? cs.fontWeight : null,
            lineHeight: cs ? cs.lineHeight : null,
            height: rect ? Math.round(rect.height) : null,
            b10color: b10el ? getComputedStyle(b10el).color : null,
            b20color: b20el ? getComputedStyle(b20el).color : null,
            firstText: b ? b.textContent : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("count", 0) >= 1, f"expected ≥1 board-badge, got {m.get('count')}"
        assert m.get("fontSize") == "10.5px", f"board-badge should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"board-badge weight should be 700, got {m.get('fontWeight')}"
        assert m.get("lineHeight") == "18px", f"board-badge line-height should be 18px (was 14px), got {m.get('lineHeight')}"
        # 10cm 蓝色 rgb(138, 166, 244) / 20cm 红色 rgb(255, 138, 138)
        assert "138" in (m.get("b10color") or "") and "166" in (m.get("b10color") or ""), f"10cm color regression: {m.get('b10color')}"
        assert "255" in (m.get("b20color") or "") and "138" in (m.get("b20color") or ""), f"20cm color regression: {m.get('b20color')}"

        await browser.close()
        print(f"[OK] R133 board-badge typography — {m['fontSize']} {m['fontWeight']} lineH {m['lineHeight']} ({m['count']} badges) | 10cm {m['b10color']} / 20cm {m['b20color']}")

if __name__ == "__main__":
    asyncio.run(run())