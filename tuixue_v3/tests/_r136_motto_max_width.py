"""R136 .bv-motto-badge max-width 120→100px — mobile 卡片宽度适配, 长 motto 不溢出.

原: R64 设置 max-width 120px (desktop 假设), mobile 卡片总宽 390-24-30 (padding+border) ≈ 336px,
    code link 60px + name 13px + sector 11px + turnover 双行 + seal + hit-badge + 4 操作按钮 (32×4 + gap),
    实测 name td 约 80-100px 后空间给 motto, 120px 容易溢出卡到 sector/turnover。
R136: max-width 120→100px (R132 字号 10.5px 已加, R136 收宽度确保 ellipsis 触发)。
    overflow:hidden + text-overflow:ellipsis + white-space:nowrap 已存在 (R64 守护)。
第一性原理: motto 是 top-1 专属信号, 长 motto 截断 + ellipsis 比溢出覆盖 sector 强。
    100px × 10.5px ≈ 9-10 个汉字, 战法口诀通常 <8 字, 极少截断。
断言 (mock 数据, 390px):
  - motto-badge max-width = 100px (was 120)
  - overflow:hidden + text-overflow:ellipsis + white-space:nowrap 都保留
  - 长 motto (>10 字) 实际宽度被截到 100px, 不溢出卡片
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

        m = await page.evaluate(r"""() => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row');
          if (!row) return {none: true};
          var longMotto = document.createElement('span');
          longMotto.className = 'bv-motto-badge';
          longMotto.textContent = '高位分歧转一致预期回封涨停';
          row.appendChild(longMotto);
          void document.body.offsetHeight;
          var b = document.querySelector('.bv-motto-badge');
          if (!b) return {none: true};
          var cs = getComputedStyle(b);
          var rect = b.getBoundingClientRect();
          return {
            maxWidth: cs.maxWidth,
            overflow: cs.overflow,
            textOverflow: cs.textOverflow,
            whiteSpace: cs.whiteSpace,
            fontSize: cs.fontSize,
            actualWidth: Math.round(rect.width),
            actualHeight: Math.round(rect.height),
            text: b.textContent
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("maxWidth") == "100px", f"motto max-width should be 100px (was 120), got {m.get('maxWidth')}"
        assert m.get("overflow") == "hidden", f"motto overflow regression: {m.get('overflow')} (must stay hidden)"
        assert m.get("textOverflow") == "ellipsis", f"motto text-overflow regression: {m.get('textOverflow')}"
        assert m.get("whiteSpace") == "nowrap", f"motto white-space regression: {m.get('whiteSpace')}"
        # 长 motto (12字) 实际宽度应被截到 ≤110 (含 padding)
        assert m.get("actualWidth", 0) <= 115, f"long motto not truncated: width={m.get('actualWidth')} (should be ≤115)"

        await browser.close()
        print(f"[OK] R136 motto max-width — {m['maxWidth']} (was 120) | long motto actual {m['actualWidth']}px (truncated ✓)")

if __name__ == "__main__":
    asyncio.run(run())