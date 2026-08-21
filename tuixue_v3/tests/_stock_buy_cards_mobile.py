#!/usr/bin/env python3
"""移动端视口测试 - 买点 3 卡 390x844 iPhone 13"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

LOCAL = "http://localhost:7799"
TEST_CODE = "002747"
RESULTS = []

def record(category, name, ok, detail=""):
    icon = "✅" if ok else "❌"
    RESULTS.append({"category": category, "name": name, "ok": ok, "detail": detail})
    print(f"  {icon} [{category}] {name}: {detail}")

def main():
    print(f"\n━━ 移动端买点卡 e2e (iPhone 13, ?code={TEST_CODE}) ━━")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
        )
        page = ctx.new_page()
        try:
            page.goto(f"{LOCAL}/?code={TEST_CODE}", wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(4000)

            for cid, name in [("q-weekly-bull-card", "周线擒牛"), ("q-recovery-card", "1/3 回升位"), ("q-ma5-rules-card", "5日线 5 原则")]:
                card = page.query_selector(f"#{cid}")
                if not card or card.is_hidden():
                    record("card", f"{name} 卡", False, "不存在或 hidden")
                    continue
                # 滚到卡片
                card.scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                box = card.bounding_box()
                in_viewport = box and box["x"] >= 0 and box["x"] + box["width"] <= 390
                record("card", f"{name} 卡", in_viewport, f"box x={box['x']:.0f} w={box['width']:.0f}")

            # 截移动端图
            try:
                Path("/tmp/buy-cards-mobile.png").unlink(missing_ok=True)
                # 滚到 3 卡附近
                page.eval_on_selector("#q-weekly-bull-card", "el => el.scrollIntoView({block:'start'})")
                page.wait_for_timeout(500)
                page.screenshot(path="/tmp/buy-cards-mobile.png", full_page=False)
                record("screenshot", "移动端截图", True, "/tmp/buy-cards-mobile.png")
            except Exception as e:
                record("screenshot", "移动端截图", False, str(e)[:60])
        except Exception as e:
            record("load", "整体测试", False, f"{type(e).__name__}: {str(e)[:80]}")
        finally:
            browser.close()

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"\n━━ 总计: {total} | 通过: {passed} | 失败: {failed} ━━")
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
