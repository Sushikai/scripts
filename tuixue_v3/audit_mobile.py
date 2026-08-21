"""
手机端逐 view 扫描脚本:
- iPhone 13 viewport (390x844)
- 走遍 8 个 view
- 检测: topbar/main 重叠 / 横向滚动 / 图表高度 / KPI 是否空白 / 控制台错误
- 输出 JSON + 简短文本报告
"""
import asyncio
from playwright.async_api import async_playwright
import json
import sys

VIEWS = [
    ("dashboard", "/#dash"),
    ("all_stocks", "/#all_stocks"),
    ("watchlist", "/#watchlist"),
    ("review", "/#review"),
    ("weekly_bull", "/#weekly_bull"),
    ("screener", "/#screener"),
    ("stock", "/#stock=300308"),
    ("sector", "/#sector?name=通信设备"),
]


async def audit_view(page, name, url):
    """对一个 view 跑完整检测,返回 dict"""
    out = {"name": name, "url": url}
    try:
        await page.goto(f"http://127.0.0.1:7799{url}", wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        out["error"] = f"navigate timeout: {e}"
        return out
    # 网络尽量跑完
    await page.wait_for_timeout(3500)
    # 错误数
    console_errs = []
    page.on("console", lambda m: console_errs.append((m.type, m.text)) if m.type == "error" else None)
    # 测量
    metrics = await page.evaluate("""() => {
        const r = (sel) => {
            const el = document.querySelector(sel);
            return el ? el.getBoundingClientRect() : null;
        };
        const rect = (x) => x ? `${Math.round(x.top)}-${Math.round(x.bottom)} h=${Math.round(x.height)}` : 'null';
        const tb = r('header.topbar');
        const mn = r('main');
        const tabs = document.querySelectorAll('[role="tab"], .tab, [class*="tab-"]');
        const charts = document.querySelectorAll('[_echarts_instance_], canvas');
        const views = document.querySelectorAll('[class*="view"]');
        const visibleView = Array.from(views).find(v => {
            const cs = getComputedStyle(v);
            return cs.display !== 'none' && !v.hidden;
        });
        const hero = r('#q-main, [class*="hero"], .hero, [class*="kpi"]');
        const dashCards = r('[class*="card"], .card');
        return {
            viewport: { w: window.innerWidth, h: window.innerHeight },
            scrollW: document.body.scrollWidth,
            docW: document.documentElement.clientWidth,
            hScroll: document.body.scrollWidth > document.documentElement.clientWidth,
            topbar: rect(tb),
            main: rect(mn),
            topbarMainOverlap: (tb && mn) ? Math.max(0, tb.bottom - mn.top) : 0,
            visibleView: visibleView ? `${visibleView.id || visibleView.className.slice(0,40)} (${rect(visibleView.getBoundingClientRect())})` : 'none',
            tabCount: tabs.length,
            chartCount: charts.length,
            zeroHeightCharts: Array.from(charts).filter(c => {
                const r = c.getBoundingClientRect();
                if (r.height < 5) {
                    // 排除 hidden tab pane 内的图表
                    let p = c.parentElement;
                    while (p) {
                        if (p.hidden || getComputedStyle(p).display === 'none') return false;
                        p = p.parentElement;
                    }
                    return true;
                }
                return false;
            }).length,
            heroExists: !!hero,
            dashCardCount: document.querySelectorAll('[class*="card"]').length,
            kpiEmptyCount: Array.from(document.querySelectorAll('[class*="kpi"], .qc-value, .kpi-value')).filter(el => {
                const t = (el.innerText||'').trim();
                return t === '—' || t === '-' || t === '' || t === 'undefined';
            }).length,
        };
    }""")
    out["metrics"] = metrics
    out["console_errors"] = console_errs[:5]
    out["issues"] = []
    m = metrics
    if m.get("hScroll"):
        out["issues"].append(f"⚠ horizontal scroll ({m['scrollW']} > {m['docW']})")
    if m.get("topbarMainOverlap", 0) > 5:
        out["issues"].append(f"⚠ topbar 主内容重叠 {m['topbarMainOverlap']}px")
    if m.get("zeroHeightCharts", 0) > 0:
        out["issues"].append(f"⚠ {m['zeroHeightCharts']} 个图表 0 高度")
    if m.get("kpiEmptyCount", 0) > 8:
        out["issues"].append(f"⚠ {m['kpiEmptyCount']} 个 KPI 显示空")
    if m.get("visibleView") == "none":
        out["issues"].append("⚠ 当前 view 不可见 (white page?)")
    if len(console_errs) > 0:
        out["issues"].append(f"⚠ {len(console_errs)} 控制台错误")
    return out


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        )
        page = await ctx.new_page()
        results = []
        for name, url in VIEWS:
            r = await audit_view(page, name, url)
            results.append(r)
            print(f"--- {name} ({url}) ---")
            print(f"  issues: {len(r['issues'])}")
            for iss in r['issues']:
                print(f"    {iss}")
            if 'metrics' in r:
                print(f"  metrics: topbar={r['metrics'].get('topbar')} | main={r['metrics'].get('main')}")
        await browser.close()
        # 写 JSON
        with open("/tmp/mobile_audit.json", "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        # 汇总
        print("\n=== 汇总 ===")
        total_iss = sum(len(r['issues']) for r in results)
        print(f"issues 总数: {total_iss}")
        for r in results:
            if r['issues']:
                print(f"  {r['name']}: {len(r['issues'])}")


if __name__ == "__main__":
    asyncio.run(main())
