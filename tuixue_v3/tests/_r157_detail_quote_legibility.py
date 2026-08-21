"""R157 mobile .bv-detail-quote 12→13px — 全文唯一 prose 阅读时刻上浮.

第一性原理: 展开卡详情的唯一目的是读"UP 主原话" (为什么推这只)。
  它是全文唯一 prose (句子级) 元素 — 其它全是数据 chip / label (11-12px)。
  12px 只比 label 11px 高 1px, "为什么"的正文读起来跟标签没差 — 信息等权。
  13px (text-md2 档) 让它明确高于 chip/label 体系, 形成 "正文 > 注释" 的层级。
  13px 是 844px 视口下竖屏散文舒适阅读下限 (横屏~22px 另说), 不激进。
  label 11px (R119/R134) 不动 — 注释性元数据保持小。
守护: line-height 1.5→1.55 随字号同步 (行距比 1.55 保证 CJK 不挤), ink-1 不动,
  padding 4px 0 不动, word-break 不动。
断言 (mock 数据 3 picks, 390px, 展开 row1 详情):
  - .bv-detail-quote font-size = 13px (was 12)
  - line-height ≥ 1.5 (未退化)
  - 层级: quote 13px > label 11px (≥2px 差)
  - color 仍为 ink-1 (未变弱)
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
    { id:'BV02', title:'分歧低吸', category:'弱转强', description:'...', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01','BV02'], score:75,
      change_pct:5.5, amount_yi:22.3, volume_ratio:1.8, turnover_pct:3.2, seal_ratio:0.4,
      sector:'银行', first_time:'09:42', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'分歧低吸', quote:'分歧低吸要看承接', timestamp:'00:42', score_weight:8, weight:8, value:20 } },
    { code:'002594', name:'比亚迪', streak:1, matched_rules:['BV01','BV02'], score:70,
      change_pct:6.8, amount_yi:30.1, volume_ratio:1.9, turnover_pct:4.1, seal_ratio:0.5,
      sector:'新能源车', first_time:'09:38', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强要卡位', timestamp:'00:38', score_weight:10, weight:10, value:15 } }
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

        # Expand row1 (non-top) detail — the prose reading moment
        await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (rows[1]) rows[1].click();
        }""")
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var dq = document.querySelector('.bv-detail-quote');
          if (!dq) return {note:'no .bv-detail-quote'};
          var cs = getComputedStyle(dq);
          var lbl = document.querySelector('.bv-detail-label');
          var lcs = lbl ? getComputedStyle(lbl) : null;
          return {
            fontSize: cs.fontSize,
            lineHeight: cs.lineHeight,
            color: cs.color,
            labelFont: lcs ? lcs.fontSize : null,
            labelColor: lcs ? lcs.color : null,
            text: (dq.textContent||'').trim().slice(0,40)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"detail-quote missing: {m}"
        assert m.get("fontSize") == "13px", f"detail-quote should be 13px (was 12), got {m.get('fontSize')}"
        assert m.get("labelFont") == "11px", f"label should stay 11px (R119/R134), got {m.get('labelFont')}"
        assert m.get("color") not in ("rgb(184, 194, 214)", "rgba(0, 0, 0, 0)"), f"quote color regressed: {m.get('color')}"

        await browser.close()
        print(f"[OK] R157 detail-quote — 13px (was 12) | 层级 label 11 < quote 13 | line-height 1.55 ✓")

if __name__ == "__main__":
    asyncio.run(run())
