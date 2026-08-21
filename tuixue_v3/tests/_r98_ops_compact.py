"""R98 主操作卡片可点, 卡内按钮是次级快捷 — 三个操作按钮统一 28px 不继承全局 40px.

原: 全局 @media(max-width:720px) button { min-height:40px; padding:8px 12px }
    泄漏进卡内操作按钮:
      - 📈 jump 42px (应 28)
      - ♥ wl 40px (应 28)
      - 💬 ai 58px, 且缺 position:absolute → 掉进隐式 grid 格, 呈 54×58 blob 在卡片左下
    (R95/R96 同一全局泄漏第三次击中本页)
R98: 三个按钮 min-height:0 !important + padding:0 + box-sizing:border-box → 统一 28px;
     💬 补 position:absolute top:4 right:70 (位于 📈 左, 与 ♥ 相邻)
断言 (mock 数据):
  - 📈 jump 高度 ≤ 30
  - ♥ wl 高度 ≤ 30
  - 💬 ai 高度 ≤ 30
  - 三个按钮都在卡片内 (top ≥ row.top, bottom ≤ row.bottom)
  - 💬 不在左下 blob (top < row 中间)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'回封', category:'回封', description:'炸板回封', score_weight:6, conditions:[], quote:'...', timestamp:'00:03' },
    { id:'BV04', title:'卡位', category:'卡位', description:'板块卡位', score_weight:7, conditions:[], quote:'...', timestamp:'00:04' },
    { id:'BV05', title:'二板分歧', category:'分歧', description:'二板分歧转一致', score_weight:9, conditions:[], quote:'...', timestamp:'00:05' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV03','BV04','BV05'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:20,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 }
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
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break

        m = await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (!rows.length) return { picks: 0 };
          var c1 = rows[0];
          var R = function(el){ var r = el.getBoundingClientRect();
            return {l:Math.round(r.left), r:Math.round(r.right), t:Math.round(r.top), b:Math.round(r.bottom), w:Math.round(r.width), h:Math.round(r.height)} };
          var rowR = c1.getBoundingClientRect();
          var out = { picks: rows.length, rowTop: Math.round(rowR.top), rowBottom: Math.round(rowR.bottom),
            rowMid: Math.round(rowR.top + rowR.height/2) };
          [['.bv-jump-btn','jump'],['.bv-wl-btn','wl'],['.bv-ai-btn','ai']].forEach(function(s){
            var el = c1.querySelector(s[0]);
            if (!el) { out[s[1]] = 'MISSING'; return; }
            var r = el.getBoundingClientRect();
            out[s[1]] = { R: R(el), mh: getComputedStyle(el).minHeight, pos: getComputedStyle(el).position };
          });
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["picks"] > 0
        for k in ("jump", "wl", "ai"):
            assert m[k] != "MISSING", f"{k} button missing"
            assert m[k]["R"]["h"] <= 30, f"{k} height {m[k]['R']['h']} should be ≤30"
            assert m[k]["R"]["t"] >= m["rowTop"], f"{k} top should be inside card"
            assert m[k]["R"]["b"] <= m["rowBottom"], f"{k} bottom should be inside card"
        # 💬 should not be a bottom-left blob — its top should be in the upper half
        assert m["ai"]["R"]["t"] < m["rowMid"], f"💬 should not be bottom blob (top {m['ai']['R']['t']} >= mid {m['rowMid']})"

        await browser.close()
        print("[OK] R98 op buttons compact (28px) + 💬 properly positioned")

if __name__ == "__main__":
    asyncio.run(run())
