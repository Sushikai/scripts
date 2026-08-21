"""R114 quote-ts 视频时间戳锚点 tap zone — 14→32 (Apple HIG).

原: R65 设计 padding 0 + font 10px → 实际 ~14px 高 (line-height)。
    用户展开 detail 看到战法原文 + 时间戳锚点 '00:35' 想点跳视频,
    14px 拇指命中极难 (远低于 Apple HIG 32)。
R114: padding 0→10 + line-height 1 + min-height:32 + min-width:0,
    inline-flex 垂直居中, 视觉字号保留 10px 不变 (不挤其他文字),
    tap zone 撑到 32+。 跟 R104/R105/R106/R111/R112 一致模式。
断言 (mock 数据, 390px):
  - 展开 detail 后 .bv-quote-ts 高度 ≥ 32px (Apple HIG)
  - 字号仍 10px (不放大)
  - 颜色/边框保留 (R65 视觉不变)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'01:12', score_weight:10, weight:10, value:20 } },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02'], score:65,
      change_pct:5.2, amount_yi:33.1, volume_ratio:1.5, turnover_pct:3.5, seal_ratio:0.4,
      sector:'安防', first_time:'10:30', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'低位首板', quote:'低位首板强调开盘抢筹', timestamp:'02:08', score_weight:8, weight:8, value:18 } }
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

        # expand second row (R101 keeps top-1 collapsed on mobile)
        try:
            await page.click("#bv-pick-tbody tr.bv-row:not(.is-top)", timeout=2000)
            await page.wait_for_timeout(800)
        except Exception:
            await page.evaluate("""() => {
                var tbody = document.getElementById('bv-pick-tbody');
                var row = document.querySelector('#bv-pick-tbody tr.bv-row:not(.is-top)');
                if (tbody && row) {
                    var ev = new MouseEvent('click', {bubbles:true, cancelable:true});
                    Object.defineProperty(ev, 'target', {value: row, enumerable:true});
                    tbody.onclick(ev);
                }
            }""")
            await page.wait_for_timeout(800)

        m = await page.evaluate(r"""() => {
          var ts = document.querySelector('.view-bv .bv-quote-ts');
          if (!ts) return {none:true};
          var r = ts.getBoundingClientRect();
          var cs = getComputedStyle(ts);
          return {
            w: Math.round(r.width), h: Math.round(r.height),
            fontSize: cs.fontSize, lineHeight: cs.lineHeight,
            color: cs.color, bg: cs.backgroundColor,
            border: cs.borderTopWidth + ' ' + cs.borderTopStyle + ' ' + cs.borderTopColor,
            text: ts.textContent.trim()
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "none" not in m, f"quote-ts not found in detail"
        assert m["h"] >= 32, f"tap zone too small: {m['h']}px (Apple HIG 32)"
        assert m["fontSize"] == "10px", f"font should stay 10px, got {m['fontSize']}"
        # color/bg preserved from R65
        assert "251" in m["color"] or "fb923c" in m["color"] or "rgb(251" in m["color"], \
            f"orange color should be preserved, got {m['color']}"

        await browser.close()
        print(f"[OK] R114 quote-ts tap zone — {m['w']}×{m['h']}px (font {m['fontSize']})")

if __name__ == "__main__":
    asyncio.run(run())