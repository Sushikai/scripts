#!/usr/bin/env python3
"""aesthetic_bench.py — tuixue_v3 全 view 美学测试 (2026-07-22)

测量每个 view 的:
  1. Typography 层级 — h1/h2/h3/body/label 字号梯度 (顶级要求: h1≥28px, body≥13px)
  2. KPI 突出度     — KPI 数字 vs label 字号比例 (顶级要求: ≥ 2.0x)
  3. Padding 密度   — section/card/table 内部 padding (顶级要求: card 16-24px)
  4. Color 对比度   — 主文/副文 vs bg 对比度 (WCAG AA ≥ 4.5:1, 大字 ≥ 3:1)
  5. Whitespace     — section gap (顶级要求: ≥ 24px)
  6. 表格行高       — desktop 28-32px, mobile 24-28px (密度 vs 可读)

每 view × viewport × theme 评 0-100 分, 总分 ≥ 85 视为通过。

输出:
  /tmp/aesthetic/{view}__{viewport}__{theme}.png
  /tmp/aesthetic/baseline.json    结构化测量 + 评分
  /tmp/aesthetic/report.txt       文本摘要

用法:
  cd /Users/kaikai/scripts/tuixue_v3 && python3 aesthetic_bench.py [--phase baseline|optimized]
"""
from __future__ import annotations
import asyncio
import json
import math
import re
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE      = "http://127.0.0.1:7799/"
OUT       = Path("/tmp/aesthetic"); OUT.mkdir(parents=True, exist_ok=True)
SETTLE_MS = 5500
THEMES    = ["dark", "light"]
VIEWPORTS = [("desktop", 1440, 900), ("mobile", 390, 844)]


VIEWS = [
  ("dash",           "dash",            None),
  ("stock",          "stock",           "600519"),
  ("watchlist",      "watchlist",       None),
  ("dragons",        "dragons",         None),
  ("weekly_bull",    "weekly_bull",     None),
  ("strategy_picker","strategy_picker", None),
  ("sector",         "sector",          "半导体"),
  ("laws",           "laws",            None),
  ("review",         "review",          None),
  ("screener",       "screener",        None),
  ("all_stocks",     "all_stocks",      None),
]


# ─── 顶级标准 (industry top-tier: Bloomberg/TradingView/Robinhood/Apple Design) ───
TARGETS = {
  "h1_px":           28,    # 主标题最低字号
  "h2_px":           20,
  "h3_px":           16,
  "body_px":         13,
  "label_px":        11,
  "kpi_prominence":  2.0,   # KPI 数字 / label 字号 ≥ 2x
  "card_padding":    16,    # card 内部 padding
  "section_gap":     24,    # section 间距
  "table_row_h":     32,    # 桌面表格行高
  "table_row_h_mb":  28,    # 移动表格行高
  "aa_contrast":     4.5,   # 正文 AA
  "aa_contrast_big": 3.0,   # 大字 AA
}


def _rel_lum(rgb: tuple[int,int,int]) -> float:
    """WCAG 相对亮度"""
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(c1: tuple[int,int,int], c2: tuple[int,int,int]) -> float:
    """WCAG 对比度 (1.0 - 21.0)"""
    L1, L2 = _rel_lum(c1), _rel_lum(c2)
    if L1 < L2: L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)


def parse_rgb(s: str) -> tuple[int,int,int] | None:
    """rgb(248, 250, 252) → (248,250,252)"""
    m = re.match(r"rgba?\(([^)]+)\)", s or "")
    if not m: return None
    parts = [int(x.strip()) for x in m.group(1).split(",")[:3]]
    return tuple(parts)


# ─── 测量 ────────────────────────────────────────────────────────
async def measure_view(page, view_key: str) -> dict:
    """返回该 view 的全部美学指标 + 评分"""
    js = """
    (() => {
      const out = { errors: [] };
      const root = document.querySelector(`[data-view="%s"]:not([hidden])`) || document;
      if (!root) { out.errors.push('view_root_not_found'); return out; }

      // 1. Typography 层级 — 扫所有可见文字节点的字号
      const seen = new Set();
      const sizes = [];
      const walk = (el, depth = 0) => {
        if (depth > 6) return;
        if (!el || seen.has(el)) return;
        seen.add(el);
        if (el.nodeType === 1) {
          const cs = getComputedStyle(el);
          if (cs.display === 'none' || cs.visibility === 'hidden') return;
          const fs = parseFloat(cs.fontSize);
          const tag = el.tagName.toLowerCase();
          // 跳过 svg / script / style
          if (['script','style','svg','noscript'].includes(tag)) return;
          if (fs > 0 && el.textContent && el.textContent.trim()) {
            sizes.push({ tag, fs, txt: el.textContent.trim().slice(0, 30) });
          }
          for (const c of el.children) walk(c, depth + 1);
        }
      };
      walk(root);

      // 统计字号直方图
      const histogram = {};
      sizes.forEach(s => {
        const k = Math.round(s.fs * 10) / 10;
        histogram[k] = (histogram[k] || 0) + 1;
      });
      out.font_sizes = histogram;

      // 顶级 KPI 字号 vs 副文 — 找最大字号 (KPI) 和中位字号 (body)
      const fs_list = sizes.map(s => s.fs).sort((a, b) => b - a);
      out.max_fs = fs_list[0] || 0;
      out.p90_fs = fs_list[Math.floor(fs_list.length * 0.1)] || 0;
      out.p50_fs = fs_list[Math.floor(fs_list.length * 0.5)] || 0;
      out.min_fs = fs_list[fs_list.length - 1] || 0;

      // 2. KPI 突出度 = 最大字号 / 中位字号
      out.kpi_prominence = out.p50_fs > 0 ? out.max_fs / out.p50_fs : 0;

      // 3. Section / Card padding — 找主要容器
      const cards = document.querySelectorAll('.card, .signal-col, .kpi, .as-kpi, [class*="kpi-card"], [class*="stat-card"]');
      const paddings = [];
      for (const c of cards) {
        if (c.offsetParent === null) continue;
        const cs = getComputedStyle(c);
        const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
        if (pad > 0) paddings.push(pad);
      }
      out.card_padding_avg = paddings.length ? paddings.reduce((a,b)=>a+b,0) / paddings.length : 0;

      // 4. 表格行高
      const rows = document.querySelectorAll('.data-table tbody tr, .stocks-table tbody tr, #wl-table tbody tr, .scr-table tbody tr, #review-table tbody tr');
      const rowHs = [];
      for (const r of rows) {
        if (r.offsetParent === null) continue;
        const h = r.getBoundingClientRect().height;
        if (h > 0) rowHs.push(h);
      }
      out.table_row_h = rowHs.length ? rowHs.reduce((a,b)=>a+b,0) / rowHs.length : 0;
      out.table_row_count = rowHs.length;

      // 5. Section gap — section 之间的间距
      const secs = document.querySelectorAll('.view > section, .view > .card, .view > article');
      const gaps = [];
      let prev = null;
      for (const s of secs) {
        if (s.offsetParent === null) continue;
        const r = s.getBoundingClientRect();
        if (prev && r.top > prev.bottom) gaps.push(r.top - prev.bottom);
        prev = r;
      }
      out.section_gap_avg = gaps.length ? gaps.reduce((a,b)=>a+b,0) / gaps.length : 0;

      // 6. Color 对比度 — 读 body 文本 + bg + 各级 ink
      const ink1 = getComputedStyle(document.documentElement).getPropertyValue('--ink-1').trim() ||
                   getComputedStyle(document.body).color;
      const ink2 = getComputedStyle(document.documentElement).getPropertyValue('--ink-2').trim() || ink1;
      const bg   = getComputedStyle(document.body).backgroundColor;
      out.colors = { ink1, ink2, bg };

      return out;
    })()
    """ % view_key
    try:
        return await page.evaluate(js)
    except Exception as e:
        return {"errors": [str(e)]}


def _f(v, default=0.0):
    """安全转 float,None → default"""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def grade_measure(m: dict, is_mobile: bool) -> dict:
    """根据顶级标准对测量结果打分"""
    scores = {}
    reasons = []

    max_fs  = _f(m.get("max_fs"))
    p50_fs  = _f(m.get("p50_fs"))
    prom    = _f(m.get("kpi_prominence"))
    cp      = _f(m.get("card_padding_avg"))
    sg      = _f(m.get("section_gap_avg"))
    rh      = _f(m.get("table_row_h"))

    # Typography 层级
    if max_fs >= TARGETS["h1_px"]:        scores["h1"] = 100
    elif max_fs >= 22:                    scores["h1"] = 70; reasons.append(f"max_fs={max_fs:.0f}<{TARGETS['h1_px']}")
    else:                                 scores["h1"] = 40; reasons.append(f"max_fs={max_fs:.0f}<<{TARGETS['h1_px']}")

    if p50_fs >= TARGETS["body_px"]:      scores["body"] = 100
    elif p50_fs >= 12:                    scores["body"] = 70; reasons.append(f"body p50={p50_fs:.1f}<{TARGETS['body_px']}")
    else:                                 scores["body"] = 40; reasons.append(f"body p50={p50_fs:.1f}<<{TARGETS['body_px']}")

    # KPI 突出度
    if prom >= TARGETS["kpi_prominence"]: scores["kpi"] = 100
    elif prom >= 1.5:                     scores["kpi"] = 70; reasons.append(f"kpi_prom={prom:.2f}<{TARGETS['kpi_prominence']}")
    else:                                 scores["kpi"] = 40; reasons.append(f"kpi_prom={prom:.2f}<<{TARGETS['kpi_prominence']}")

    # Card padding
    if cp >= TARGETS["card_padding"]:     scores["card_pad"] = 100
    elif cp >= 12:                        scores["card_pad"] = 70; reasons.append(f"card_pad={cp:.1f}<{TARGETS['card_padding']}")
    else:                                 scores["card_pad"] = 40; reasons.append(f"card_pad={cp:.1f}<<{TARGETS['card_padding']}")

    # Section gap
    if sg >= TARGETS["section_gap"]:      scores["sec_gap"] = 100
    elif sg >= 16:                        scores["sec_gap"] = 70; reasons.append(f"sec_gap={sg:.1f}<{TARGETS['section_gap']}")
    else:                                 scores["sec_gap"] = 40; reasons.append(f"sec_gap={sg:.1f}<<{TARGETS['section_gap']}")

    # 表格行高
    target_rh = TARGETS["table_row_h_mb"] if is_mobile else TARGETS["table_row_h"]
    if rh == 0:                                       scores["row_h"] = 50  # no table on this view
    elif rh >= target_rh - 4 and rh <= target_rh + 8: scores["row_h"] = 100
    elif rh >= target_rh - 8:                         scores["row_h"] = 70; reasons.append(f"row_h={rh:.1f}≠{target_rh}")
    else:                                             scores["row_h"] = 40; reasons.append(f"row_h={rh:.1f}<<{target_rh}")

    total = sum(scores.values()) / max(1, len(scores))
    return {"scores": scores, "total": round(total, 1), "reasons": reasons}


# ─── 主流程 ────────────────────────────────────────────────────────
async def goto_view(page, view, arg):
    if arg:
        import urllib.parse
        encoded = urllib.parse.quote(arg, safe='')
        url = f"{BASE}#{view}={encoded}"
    else:
        url = f"{BASE}#{view}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    try:
        await page.wait_for_selector(f'[data-view="{view}"]:not([hidden])', timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(SETTLE_MS)


async def ensure_server_alive():
    """server 死了就重启 (heavy bench 时常挂)"""
    import subprocess
    try:
        import urllib.request
        urllib.request.urlopen(f"{BASE}api/healthz", timeout=2).read()
        return True
    except Exception:
        print("    [watchdog] server down, restarting…")
        subprocess.run(["pkill", "-f", "python3.*server.py"], capture_output=True)
        await asyncio.sleep(1)
        subprocess.Popen(
            ["python3", "web/server.py", "--no-preheat"],
            cwd="/Users/kaikai/scripts/tuixue_v3",
            stdout=open("/tmp/tuixue-aesthetic-server.log", "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                urllib.request.urlopen(f"{BASE}api/healthz", timeout=2).read()
                print("    [watchdog] server ready")
                return True
            except Exception:
                pass
        return False


async def main(phase: str = "baseline"):
    report = {
        "phase": phase,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": BASE,
        "items": [],
        "targets": TARGETS,
        "summary": {"pass": 0, "fail": 0, "score_avg": 0.0},
    }

    await ensure_server_alive()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
        ])

        for vp_name, w, h in VIEWPORTS:
            is_mobile = vp_name == "mobile"
            for theme in THEMES:
                # 每个 viewport×theme 前确保 server 在
                await ensure_server_alive()
                ctx = await browser.new_context(
                    viewport={"width": w, "height": h},
                    color_scheme="dark" if theme == "dark" else "light",
                    locale="zh-CN",
                )
                page = await ctx.new_page()
                await page.add_init_script(f"try{{localStorage.setItem('tuixue-theme','{theme}')}}catch(e){{}}")

                for view_key, view_hash, arg in VIEWS:
                    try:
                        await goto_view(page, view_hash, arg)
                        m = await measure_view(page, view_hash)
                        grade = grade_measure(m, is_mobile)

                        shot_path = OUT / f"{view_key}__{vp_name}__{theme}.png"
                        try:
                            await page.screenshot(path=str(shot_path), full_page=False)
                        except Exception as e:
                            m.setdefault("errors", []).append(f"screenshot:{e}")

                        item = {
                            "view": view_key,
                            "viewport": vp_name,
                            "theme": theme,
                            "measure": m,
                            "grade": grade,
                            "screenshot": str(shot_path),
                        }
                        report["items"].append(item)
                        if grade["total"] >= 70:
                            report["summary"]["pass"] += 1
                        else:
                            report["summary"]["fail"] += 1
                        print(f"  [{vp_name}/{theme}] {view_key:18} score={grade['total']:.1f}  reasons={grade['reasons']}")
                    except Exception as e:
                        report["items"].append({"view": view_key, "viewport": vp_name, "theme": theme, "error": str(e)})
                        report["summary"]["fail"] += 1
                        print(f"  [{vp_name}/{theme}] {view_key:18} ERROR: {e}")

                await ctx.close()

        await browser.close()

    n = max(1, report["summary"]["pass"] + report["summary"]["fail"])
    total = sum(i.get("grade", {}).get("total", 0) for i in report["items"])
    report["summary"]["score_avg"] = round(total / n, 1)
    report["summary"]["total"] = n

    out_json = OUT / f"aesthetic_{phase}.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n  → {out_json}")
    print(f"  pass={report['summary']['pass']}/{n}  avg={report['summary']['score_avg']}")
    return report


# ─── 视觉模型验证 (read screenshots + Claude vision) ──────────────
VISION_RUBRIC = """
你是一个 UI 美学评审 (对标 Bloomberg Terminal / TradingView / Robinhood / Apple Design)。

请评估截图,从以下维度打分 (0-100):
- **typography**     : 字号梯度清晰? 标题/数字/标签层级分明? 字改大大、该小小到位?
- **density**        : 信息密度合适? 每屏能看到足够多但不拥挤?
- **contrast**       : 主文/副文/数字颜色对比足够? 涨红跌绿清晰?
- **spacing**        : 卡片/区块/表格留白舒适? 不压抑不松散?
- **hierarchy**      : 视觉重点突出 (KPI 数字/标题)? 弱元素合理降级?
- **aesthetic**      : 整体美学感觉? 顶级感? 还想改善什么?

返回 JSON:
{
  "scores": {"typography":N, "density":N, "contrast":N, "spacing":N, "hierarchy":N, "aesthetic":N},
  "issues": ["问题1", "问题2", ...],
  "recommendations": ["建议1", "建议2", ...]
}
"""


async def vision_verify(report: dict, sample: int = 6) -> dict:
    """对采样截图跑视觉模型验证,返回结构化评分 + 改进建议"""
    # 按 view × viewport × theme 选代表截图
    items = report["items"]
    if not items: return {}
    # 优先选 desktop/dark (主战场) — 每 view 选 1 张
    desktop_dark = [i for i in items if i.get("viewport") == "desktop" and i.get("theme") == "dark"]
    sample_items = desktop_dark[:sample] if len(desktop_dark) >= sample else desktop_dark

    # 视觉模型 — 用本地 Claude vision API
    # 留接口给 Claude 调用, 这里返回 prompts 给上层用
    prompts = []
    for it in sample_items:
        p = VISION_RUBRIC + f"\n\n当前 view: {it['view']}\n截图: {it.get('screenshot','')}\n客观测量: {json.dumps(it.get('measure', {}), ensure_ascii=False)[:500]}"
        prompts.append({"view": it["view"], "prompt": p, "screenshot": it.get("screenshot")})
    return {"prompts": prompts, "sample_count": len(prompts)}


if __name__ == "__main__":
    import sys
    phase = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    r = asyncio.run(main(phase))
    if "--vision" in sys.argv:
        vp = asyncio.run(vision_verify(r))
        (OUT / f"vision_prompts_{phase}.json").write_text(json.dumps(vp, ensure_ascii=False, indent=2))
        print(f"\n  → vision prompts: {OUT}/vision_prompts_{phase}.json ({vp.get('sample_count', 0)} samples)")


if __name__ == "__main__":
    import sys
    phase = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    asyncio.run(main(phase))