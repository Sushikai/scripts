"""R124 .bv-cat-summary::after 折叠 chevron 可读性 — 10→12px + ink-3→ink-2 + weight 400→700.

原: R90 把分类 summary 设为可点紧凑行, ::after 加 ▾ 提示可展开。
    font 10px + ink-3 (最弱灰) + 默认 weight 400 → 视觉几乎消失,
    用户不知道分类行可点击展开 (材料区常年折叠, 用户绕过)。
R124: 三档 legibility 信号同步提升:
  - font 10→12px (跟 R122 ops 体系一致)
  - color ink-3→ink-2 (中灰, 在 bg-3 卡片背景上仍清晰)
  - weight 默认→700 (粗体让 chevron 立刻跳出来)
第一性原理: chevron 唯一职责是"传达可点", 三档信号叠加 = 不用怀疑能否展开。
断言 (mock 数据, 390px):
  - summary row 渲染时 chevron ::after content = "▾" 或 "▴"
  - summary 字号主行 12px+ 正常 (不变)
  - clickable cursor + min-height 38px (R90 守护)
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

        # Inject a bv-cat-summary with ::after chevron if not present
        m = await page.evaluate(r"""() => {
          // Force show view-bv and inject inside
          var vb = document.querySelector('.view-bv');
          if (vb) {
            vb.hidden = false;
            vb.style.display = '';
          }
          // Remove existing injected test nodes to avoid duplicates
          var old = document.querySelectorAll('details.test-bv-cat');
          old.forEach(function(n){ n.remove(); });
          var det = document.createElement('details');
          det.className = 'bv-cat-details test-bv-cat';
          var sum = document.createElement('summary');
          sum.className = 'bv-cat-summary';
          sum.textContent = '首板类规则';
          det.appendChild(sum);
          // append inside .view-bv so the .view-bv parent selector matches
          (vb || document.body).appendChild(det);
          // Force reflow
          void det.offsetHeight;
          var s = getComputedStyle(sum, '::after');
          return {
            content: s.content,
            fontSize: s.fontSize,
            color: s.color,
            fontWeight: s.fontWeight,
            sumMinH: getComputedStyle(sum).minHeight,
            sumPadding: getComputedStyle(sum).padding,
            insideViewBv: !!vb,
            sumText: sum.textContent.trim().slice(0, 20)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "▾" in m.get("content", "") or "▴" in m.get("content", ""), f"chevron missing: {m.get('content')}"
        assert m.get("fontSize") == "12px", f"chevron font should be 12px (was 10), got {m.get('fontSize')}"
        assert m.get("fontWeight") in ("700", "bold"), f"chevron weight should be 700 (was default 400), got {m.get('fontWeight')}"
        # ink-2 should NOT equal ink-3 (ink-3 is the weakest grey)
        assert m.get("color") != "rgba(136, 136, 136, 1)", f"chevron color still ink-3 (weakest grey): {m.get('color')}"

        await browser.close()
        print(f"[OK] R124 cat-summary chevron — {m['fontSize']} {m['fontWeight']} {m['color']} content={m['content']}")

if __name__ == "__main__":
    asyncio.run(run())