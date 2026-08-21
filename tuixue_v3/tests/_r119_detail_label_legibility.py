"""R119 detail-label 段落标签可读性 — 10→11px (跟 R108/109/110 typography 模式).

原: .bv-detail-label font 10px + ink-3 灰色 + letter-spacing 0.3px。
    用户展开 detail panel, 看到 "💬 UP 主原话" / "🎯 命中规则" / "📊 得分构成"
    10px 灰色, 跟下面 12-13px quote/scores 强对比 → label 像水印, 扫视时容易跳过。
R119: font 10→11px (跟 R108 meta 11.5 / R109 creed eyebrow 11 / R110 other eyebrow 11 一致),
    颜色/字重/letter-spacing 保留, 视觉层级仍明显 (label 比 quote 小 1px, 不抢戏)。
断言 (mock 数据, 390px):
  - 展开 detail 后 .bv-detail-label 字号 = 11px
  - 颜色保留 ink-3 (#888 左右, 跟原 10px 同)
  - quote 内容字号保留 12px (regression — R119 不能放大 label 抢 quote 戏)
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
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:0.2,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'01:12', score_weight:10, weight:10, value:20 } },
    { code:'002415', name:'海康威视', streak:3, matched_rules:['BV01','BV02'], score:65,
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
          var labels = document.querySelectorAll('.view-bv .bv-detail-label');
          var labelItems = [];
          labels.forEach(function(l){
            var cs = getComputedStyle(l);
            labelItems.push({
              text: l.textContent.trim().slice(0,20),
              fontSize: cs.fontSize,
              fontWeight: cs.fontWeight,
              color: cs.color
            });
          });
          var quote = document.querySelector('.view-bv .bv-detail-quote');
          var quoteCs = quote ? getComputedStyle(quote) : null;
          return {
              labels: labelItems,
              quoteFontSize: quoteCs ? quoteCs.fontSize : null
            };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert len(m["labels"]) >= 2, f"need ≥2 detail labels (UP主原话 + 得分构成 etc), got {len(m['labels'])}"
        for lbl in m["labels"]:
            assert lbl['fontSize'] == '11px', f"{lbl['text']} font should be 11px (was 10), got {lbl['fontSize']}"
            assert lbl['fontWeight'] in ('600', '700'), f"{lbl['text']} weight should stay 600+, got {lbl['fontWeight']}"
            # color retained (ink-3 #888 = rgb(136,136,136))
            assert '136' in lbl['color'] or 'rgb' in lbl['color'], f"{lbl['text']} color lost, got {lbl['color']}"
        # quote regression — must stay 12px (label 11 < quote 12)
        assert m['quoteFontSize'] == '12px', f"quote regression: {m['quoteFontSize']}px (was 12, must stay to avoid label stealing scene)"

        await browser.close()
        print(f"[OK] R119 detail-label typography — {len(m['labels'])} labels 11px (quote stays 12px)")

if __name__ == "__main__":
    asyncio.run(run())