"""R181 纯 _miniMd cost — 在浏览器隔离 200 次调用 (40 unique 内容 × 5 次), 测总耗时.

BEFORE: 200 调全部跑 40+ regex pass.
AFTER : 唯一内容 ≤ 40 条 → 40 次全跑 + 160 次 cache hit, 理论提速 ~80%.
"""
import json, time
from playwright.sync_api import sync_playwright

def sample_msg(i):
    kw = "买入 止损 支撑位 龙虎榜 涨停 MACD KDJ 人工智能 半导体 北向资金"
    names = ['茅台','宁德时代','中芯国际','药明康德','比亚迪','隆基绿能','中国中免','恒瑞医药']
    return f"""## 第{i}只 · {names[i % len(names)]}

**结论**: 可以买入, 目标价 128.50 元, 止损位 115.20 元, 持有期 10-15 天。
- 涨停 2026-08-19 放量突破 前高 124.80, 封板 2.5 亿
- 龙虎榜 净买入 1.2 亿, 北向资金 加仓 3200 万
- MACD 金叉, KDJ 超买 80, RSI 62, BOLL 中轨上方

| 维度 | 数值 | 信号 |
|------|------|------|
| 主力资金 | +4800 万 | 强 |
| 换手率 | 5.2% | 活跃 |
| 市值 | 1.8 万亿 | 大盘 |

{kw} 提示: 建议关注 5日线 支撑, 跌破 120.10 减仓, 风险警告 注意回调。
人工智能 板块 半日热度 +3.5%, 半导体 龙头 净利增速 +45.3%。
"""

def run(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 800, "height": 600})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        for _ in range(60):
            if page.evaluate("() => typeof window._miniMd === 'function'"): break
            time.sleep(0.5)
        page.evaluate("() => { window._miniMdCache && window._miniMdCache.clear(); }")
        # 准备 40 条内容
        contents = [sample_msg(i) for i in range(40)]
        # 注入这 40 条到页内
        page.evaluate(f"() => {{ window._testContents = {json.dumps(contents)}; }}")
        # 200 次调用: 5 次循环 40 条内容
        out = page.evaluate("""() => {
            const t0 = performance.now();
            for (let r = 0; r < 5; r++) {
                for (let i = 0; i < 40; i++) {
                    window._miniMd(window._testContents[i]);
                }
            }
            const t1 = performance.now();
            // 第 6 轮: 100% cache hit
            for (let r = 0; r < 1; r++) {
                for (let i = 0; i < 40; i++) {
                    window._miniMd(window._testContents[i]);
                }
            }
            const t2 = performance.now();
            return {
                first_200_ms: +(t1 - t0).toFixed(2),
                next_40_ms: +(t2 - t1).toFixed(2),
                speedup: +((t1 - t0) / (t2 - t1)).toFixed(1)
            };
        }""")
        b.close()
        return out

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))