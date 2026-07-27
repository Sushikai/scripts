#!/usr/bin/env python3
"""audit_views.py — tuixue_v3 全 view 端到端巡检 (2026-07-20)

跑全 12 view × 2 viewport × 2 theme = 48 张截图,捕获:
- 浏览器 console errors
- HTTP ≥ 400 网络失败
- KPI 卡 "—" / "加载中" / undefined 空值
- 关键 selector 不存在 (DOM 缺失)

输出:
  /tmp/audit/{view}__{viewport}__{theme}.png   48 张
  /tmp/audit/report.json                       结构化汇总

用法:
  cd /Users/kaikai/scripts/tuixue_v3 && python3 audit_views.py
"""
from __future__ import annotations
import asyncio
import json
import re
import time
import urllib.parse
from pathlib import Path
from playwright.async_api import async_playwright

BASE      = "http://127.0.0.1:7799/"
OUT       = Path("/tmp/audit"); OUT.mkdir(parents=True, exist_ok=True)
SETTLE_MS = 6500     # 数据加载等待
THEMES    = ["dark", "light"]
VIEWPORTS = [("desktop", 1440, 900), ("mobile", 390, 844)]

# (id, hash, arg, asserts_dict)
# asserts: {"sel_key": "#css-selector"} → 存在即 ok
#          {"min_key": ("#css-selector", min_count)} → 至少 N 个
#          {"not_text_key": ("#css-selector", bad_text_regex)} → 文本不匹配
#          {"contains_key": ("#css-selector", "substr")} → 文本含 substring
VIEWS = [
  ("dash",           "dash",            None,           {
      "kpi_pct":   "#sig-a-pct",
      "kpi_card":  ".signal-col",
      "nav":       "#sidebar",
  }),
  ("stock",          "stock",           "600519",       {
      "hero":       "#qh-name",
      "hero_code":  "#qh-code",
      "kline":      "#kline-chart",
      "intraday":   "#intra-day-chart",
      "name_chk":   ("contains", "#qh-name", "贵州茅台"),
  }),
  ("watchlist",      "watchlist",       None,           {
      "rows":   "#wl-tbody tr",
      "addbar": ".wl-addbar",
  }),
  ("optimize",       "optimize",        None,           {
      "run_btn":   "#run-optimize",
      "progress":  "#opt-progress-bar",
      "progress_text": "#opt-progress-text",
  }),
  ("dragons",        "dragons",         None,           {
      "sentiment": "#dragons-sentiment-label",
      "top10":     "#dragons-top10",
  }),
  ("weekly_bull",    "weekly_bull",     None,           {
      "chips":  "#weekly-bull-chips",
      "status": "#weekly-bull-status",
      "list":   "#weekly-bull-list",
  }),
  ("strategy_picker","strategy_picker", None,           {
      "filters": "#sp-filters",
      "list":    "#sp-list",
  }),
  ("sector",         "sector",          "半导体",        {
      "name":  "#sector-name",
      "roles": "#sector-roles",
  }),
  ("laws",           "laws",            None,           {
      "categories": "#laws-categories",
      "koujue":     "#laws-koujue",
  }),
  ("review",         "review",          None,           {
      "capbar": "#review-capbar",
      "table":  "#review-table",
  }),
  ("screener",       "screener",        None,           {
      "card":       ".view-screener:not([hidden]) #zt-mount .card",
      "run_btn":    "#zt-bt-run",
      "start_in":   "#zt-bt-start",
  }),
  ("all_stocks",     "all_stocks",      None,           {
      "kpis":     "#as-kpis",
      "filter":   ".as-filter-card",
      "tbody":    "#as-stocks-tbody",
  }),
  ("dexin",          "dexin",           None,           {
      "tabs":      "#dexin-tabs",
      "tab_min":   ("min", ".dexin-tab", 5),
      "meta":      "#dexin-meta",
      "meta_txt":  ("not_text", "#dexin-meta", r"^\s*—\s*$"),
      "disclaimer":"#dexin-disclaimer",
  }),
  # ai-review 跳过 — 需 trade_id 实参
]

BAD_TEXT_RE = re.compile(r"^\s*$|加载失败|^—+$|^N/A$|undefined", re.I)

def check_selector_value(v):
    """解析断言规则值"""
    return v


async def goto_view(page, view, arg):
    if arg:
        # 中文 arg URL-encode
        encoded = urllib.parse.quote(arg, safe='')
        url = f"{BASE}#{view}={encoded}"
    else:
        url = f"{BASE}#{view}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    try:
        await page.wait_for_selector(f'[data-view="{view}"]:not([hidden])', timeout=8000)
    except Exception:
        # 部分 view (sector / stock) 可能延迟 mount,继续等 settle
        pass
    await page.wait_for_timeout(SETTLE_MS)


async def eval_asserts(page, asserts):
    """返回 dict{ key: 'ok'|'MISSING'|'BAD_TEXT'|'OK_MATCH'|str_count }"""
    out = {}
    for key, val in asserts.items():
        try:
            if isinstance(val, str):
                # 简单 selector 存在性
                el = await page.query_selector(val)
                out[key] = "ok" if el else "MISSING"
            elif isinstance(val, tuple):
                op = val[0]
                if op == "min":
                    # ("min", "#css", N)
                    cnt = await page.locator(val[1]).count()
                    out[key] = f"{cnt}/{val[2]}" if cnt >= val[2] else f"MISSING({cnt}<{val[2]})"
                elif op == "not_text":
                    # ("not_text", "#css", bad_regex)
                    txt = (await page.text_content(val[1]) or "").strip()
                    out[key] = "ok" if not re.search(val[2], txt) else f"BAD:{txt[:30]}"
                elif op == "contains":
                    # ("contains", "#css", substr)
                    txt = (await page.text_content(val[1]) or "").strip()
                    out[key] = "ok" if val[2] in txt else f"MISS:{txt[:30]}"
                else:
                    out[key] = f"UNK_OP:{op}"
            else:
                out[key] = f"BAD_VAL:{type(val).__name__}"
        except Exception as e:
            out[key] = f"ERR:{type(e).__name__}"
    return out


async def collect_empty_kpis(page):
    """扫所有 .qc-value / .signal-metric / .kpi 文本,标记空值"""
    bad = []
    for sel in [".qc-value", ".signal-metric span", ".kpi .v", ".signal-pct", ".kpi-value"]:
        try:
            els = await page.query_selector_all(sel)
            for el in els[:30]:
                t = (await el.text_content() or "").strip()
                if BAD_TEXT_RE.match(t):
                    bad.append({"sel": sel, "text": t[:40]})
        except Exception:
            pass
    return bad


async def main():
    report = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": BASE,
        "items": [],
        "summary": {"pass": 0, "fail": 0, "error": 0},
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
        ])

        for vp_name, w, h in VIEWPORTS:
            for theme in THEMES:
                ctx = await browser.new_context(
                    viewport={"width": w, "height": h},
                    color_scheme="dark" if theme == "dark" else "light",
                    locale="zh-CN",
                )
                page = await ctx.new_page()

                # pre-seed theme via localStorage (index.html inline script 同步读取它)
                await page.add_init_script(f"try{{localStorage.setItem('tuixue-theme','{theme}')}}catch(e){{}}")

                console_sink = []
                network_sink = []

                def on_console(msg):
                    if msg.type == "error":
                        try:
                            console_sink.append(f"[{msg.type}] {msg.text[:200]}")
                        except Exception:
                            pass
                page.on("console", on_console)

                async def on_resp(resp):
                    try:
                        if resp.status >= 400:
                            network_sink.append(f"{resp.status} {resp.url[:200]}")
                    except Exception:
                        pass
                page.on("response", lambda r: asyncio.create_task(on_resp(r)))

                for vid, vhash, arg, asserts in VIEWS:
                    sub = {"view": vid, "arg": arg, "viewport": vp_name, "theme": theme}
                    try:
                        # 重新载入主页 + 用 hash 触发 (确保 view-enter 走真实 fetch)
                        await goto_view(page, vhash, arg)

                        results = await eval_asserts(page, asserts)
                        empty = await collect_empty_kpis(page)

                        png = OUT / f"{vid}__{vp_name}__{theme}.png"
                        await page.screenshot(path=str(png), full_page=True)

                        sub.update({
                            "screenshot": str(png),
                            "asserts": results,
                            "empty_kpis": empty[:10],
                            "console_errors": list(console_sink),
                            "network_errors": list(network_sink),
                        })

                        # 状态判定: 任何 MISSING / BAD / ERR 即 fail
                        bad_asserts = [
                            v for v in results.values()
                            if v != "ok" and not (isinstance(v, str) and v.startswith(f"{0}"))
                        ]
                        # 简化: 任何含 MISSING / BAD / ERR 的判 fail
                        is_fail = any(
                            "MISSING" in str(v) or "BAD" in str(v) or "ERR" in str(v) or str(v) == "MISSING"
                            for v in results.values()
                        )
                        sub["status"] = "fail" if is_fail else "pass"
                        report["summary"]["pass" if sub["status"] == "pass" else "fail"] += 1
                        console_sink.clear()
                        network_sink.clear()
                    except Exception as e:
                        sub.update({"status": "error", "error": str(e)[:200]})
                        report["summary"]["error"] += 1
                        console_sink.clear()
                        network_sink.clear()

                    report["items"].append(sub)
                    print(f"{vp_name:7s} {theme:5s} {vid:16s} → {sub.get('status'):5s}  asserts={sub.get('asserts', {})}")

                await ctx.close()
        await browser.close()

    report["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    s = report["summary"]
    print(f"\n报告: {OUT/'report.json'}")
    print(f"  pass={s['pass']}  fail={s['fail']}  error={s['error']}")


if __name__ == "__main__":
    asyncio.run(main())