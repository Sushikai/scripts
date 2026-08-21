"""全 view 视觉巡检 — desktop 1280 + mobile 390,每页截图 + console error 检查。

输出: flow_audit/ 目录,R1-desktop-dashboard.png 等。
"""

import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8810"
OUT = Path("/Users/kaikai/scripts/flow/flow_audit")
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [
    ("desktop", 1280, 800),
    ("mobile", 390, 844),
]

VIEWS = [
    ("dashboard", "#dashboard"),
    ("new", "#new"),
    ("projects", "#projects"),
    ("comments", "#comments"),
    ("accounts", "#accounts"),
    ("uploads", "#uploads"),
    ("logs", "#logs"),
    ("library", "#library"),
    ("settings", "#settings"),
]


def get_health() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if not get_health():
        print(f"!! {BASE}/api/health unreachable"); sys.exit(1)
    issues = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for vp_name, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            errors = []
            page.on("console", lambda msg: errors.append((msg.type, msg.text)) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(("pageerror", str(exc))))
            for name, route in VIEWS:
                page.goto(BASE + "/" + route, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # let async loads settle
                page.screenshot(path=str(OUT / f"R1-{vp_name}-{name}.png"), full_page=False)
                # 截当前 hash 验证
                h = page.evaluate("window.location.hash")
                if h != route:
                    issues.append(f"{vp_name} {name}: hash mismatch {h} != {route}")
            # 检查 console errors
            if errors:
                # 过滤掉 favicon / 已知噪声
                real = [(t, m) for t, m in errors if "favicon" not in m.lower() and "404" not in m]
                if real:
                    print(f"⚠️ {vp_name} console errors:")
                    for t, m in real[:5]:
                        print(f"  [{t}] {m[:160]}")
                    issues.extend([f"{vp_name}: {t} {m[:120]}" for t, m in real])
                else:
                    print(f"  ✓ {vp_name}: 0 real errors ({len(errors)} noise)")
            else:
                print(f"  ✓ {vp_name}: 0 console errors")
            ctx.close()
        browser.close()
    # 写报告
    report = OUT / "report.json"
    report.write_text(json.dumps({"issues": issues, "shots": len(VIEWS) * len(VIEWPORTS)}, indent=2, ensure_ascii=False))
    print(f"\n✅ {len(VIEWS) * len(VIEWPORTS)} 截图 → {OUT}")
    if issues:
        print(f"⚠️  {len(issues)} issues:")
        for it in issues[:10]:
            print(f"  - {it}")
        sys.exit(1)
    print("✅ 0 issues")


if __name__ == "__main__":
    main()