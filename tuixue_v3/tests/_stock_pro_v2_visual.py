#!/usr/bin/env python3
"""
tests/_stock_pro_v2_visual.py — 个股专业终端视觉验收截图 (plan step 6)。

桌面 1280×900 + 手机 390×844 双视口,截 7 组关键画面:
  1. 首屏 (default tab=kline)
  2. 分时 (今日 + 日期标签)
  3. K线 + 指标切换 (MA/MACD/KDJ/BOLL 各一张)
  4. 资金流向
  5. 风险 (砸盘风险)
  6. AI 铁律
  7. 相关个股
  8. 手机端首屏 (mobile 布局)

产物写到 /tmp/stock_pro_v2_shots/{desktop,mobile}/ 下,并输出一份 HTML 索引方便肉眼复查。
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:7799"
CODE = "600519"
OUT = Path("/tmp/stock_pro_v2_shots")
DESKTOP = (1280, 900)
MOBILE = (390, 844)

TABS = {
    "flow": "资金流向",
    "seats": "游资席位",
    "holders": "散户/主力",
    "crash": "砸盘风险",
    "ai": "AI 铁律",
    "news": "新闻",
    "sectors": "板块",
    "related": "相关个股",
}


def _wait_for(page, cond, secs=15, step=300):
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            if cond():
                return True
        except Exception:
            pass
        page.wait_for_timeout(step)
    return False


def _shot(page, name, dir_):
    p = dir_ / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    return p


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    desk = OUT / "desktop"
    mob = OUT / "mobile"
    desk.mkdir(exist_ok=True)
    mob.mkdir(exist_ok=True)
    manifest = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])

        # ── 桌面 ──
        ctx = browser.new_context(viewport={"width": DESKTOP[0], "height": DESKTOP[1]},
                                  service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text[:150]}")
                if m.type == "error" else None)
        page.goto(f"{BASE}/?code={CODE}#stock", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".view-stock", timeout=15000)
        _wait_for(page, lambda: page.locator("#q-price").inner_text().strip() not in ("", "—"), 20)
        # 等 kline 首绘
        _wait_for(page, lambda: page.evaluate(
            "typeof _klineChartDrawn !== 'undefined' && _klineChartDrawn"), 20)

        # 1. 首屏 (default kline)
        page.wait_for_timeout(1500)
        _shot(page, "01_home", desk)
        manifest.append(("desktop", "01_home", "首屏 (K线默认)"))

        # 2. 分时
        page.locator('.chart-tab[data-tab="intraday"]').first.click()
        page.wait_for_timeout(2500)
        _shot(page, "02_intraday", desk)
        manifest.append(("desktop", "02_intraday", "分时"))

        # 3. K线 + 4 指标
        page.locator('.chart-tab[data-tab="kline"]').first.click()
        page.wait_for_timeout(1500)
        for ind in ["ma", "macd", "kdj", "boll"]:
            page.locator(f'#kline-indicators .kt-chip[data-ind="{ind}"]').first.click()
            page.wait_for_timeout(1000)
            _shot(page, f"03_kline_{ind}", desk)
            manifest.append(("desktop", f"03_kline_{ind}", f"K线 {ind.upper()}"))

        # 4-8. 其余 tab
        for key, label in TABS.items():
            page.locator(f'.chart-tab[data-tab="{key}"]').first.click()
            page.wait_for_timeout(2200)
            _shot(page, f"0{4 + list(TABS).index(key)}0_tab_{key}", desk)
            manifest.append(("desktop", f"0{4 + list(TABS).index(key)}0_tab_{key}", label))

        print(f"[desktop] console errors: {errors[:5] if errors else 'none'}")
        ctx.close()

        # ── 手机 390×844 ──
        ctx2 = browser.new_context(viewport={"width": MOBILE[0], "height": MOBILE[1]},
                                   device_scale_factor=2, is_mobile=True,
                                   service_workers="block")
        page2 = ctx2.new_page()
        errs2 = []
        page2.on("pageerror", lambda e: errs2.append(f"PAGEERROR: {e}"))
        page2.on("console", lambda m: errs2.append(f"{m.type}: {m.text[:150]}")
                 if m.type == "error" else None)
        page2.goto(f"{BASE}/?code={CODE}#stock", wait_until="domcontentloaded", timeout=30000)
        page2.wait_for_selector(".view-stock", timeout=15000)
        _wait_for(page2, lambda: page2.locator("#q-price").inner_text().strip() not in ("", "—"), 20)
        _wait_for(page2, lambda: page2.evaluate(
            "typeof _klineChartDrawn !== 'undefined' && _klineChartDrawn"), 20)
        page2.wait_for_timeout(1500)
        _shot(page2, "m01_home", mob)
        manifest.append(("mobile", "m01_home", "手机首屏 (K线默认)"))

        # 手机: 分时 + K线指标 + 相关个股 (关键决策路径)
        page2.locator('.chart-tab[data-tab="intraday"]').first.click()
        page2.wait_for_timeout(2500)
        _shot(page2, "m02_intraday", mob)
        manifest.append(("mobile", "m02_intraday", "手机分时"))

        page2.locator('.chart-tab[data-tab="kline"]').first.click()
        page2.wait_for_timeout(1500)
        page2.locator('#kline-indicators .kt-chip[data-ind="macd"]').first.click()
        page2.wait_for_timeout(1000)
        _shot(page2, "m03_kline_macd", mob)
        manifest.append(("mobile", "m03_kline_macd", "手机K线 MACD"))

        # 手机 ≤480px 隐藏 news/sectors/related (P1 设计),验证 ai/crash 可见
        page2.locator('.chart-tab[data-tab="ai"]').first.click()
        page2.wait_for_timeout(2200)
        _shot(page2, "m04_ai", mob)
        manifest.append(("mobile", "m04_ai", "手机AI铁律"))

        page2.locator('.chart-tab[data-tab="crash"]').first.click()
        page2.wait_for_timeout(2200)
        _shot(page2, "m05_crash", mob)
        manifest.append(("mobile", "m05_crash", "手机砸盘风险"))

        print(f"[mobile] console errors: {errs2[:5] if errs2 else 'none'}")
        ctx2.close()
        browser.close()

    # HTML 索引
    html = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<title>个股专业终端视觉验收</title>",
            "<style>body{font-family:monospace;background:#111;color:#ddd;margin:24px}",
            "img{max-width:420px;border:1px solid #333;margin:6px;background:#000}",
            ".row{display:flex;flex-wrap:wrap;align-items:flex-start}</style></head><body>",
            "<h1>个股专业终端视觉验收 · 截图索引</h1>"]
    groups = {}
    for kind, name, label in manifest:
        groups.setdefault(kind, []).append((name, label))
    for kind, items in groups.items():
        html.append(f"<h2>{kind}</h2><div class='row'>")
        for name, label in items:
            html.append(f"<div><div>{label}</div><img src='{kind}/{name}.png'></div>")
        html.append("</div>")
    html.append("</body></html>")
    (OUT / "index.html").write_text("\n".join(html))
    print(f"\n截图索引: {OUT}/index.html")
    print(f"共 {len(manifest)} 张截图")


if __name__ == '__main__':
    main()
