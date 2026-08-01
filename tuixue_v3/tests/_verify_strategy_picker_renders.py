#!/usr/bin/env python3
"""
验证策略选股器 (#strategy_picker) 真实渲染数据
==============================================
按当前 view-strategy_picker.js DOM (.sp-card div cards),
确认选了 wb+rl+ma5 (or mode) 后, 卡片正确渲染而非空状态。
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

LOCAL = "http://localhost:7799"
RESULTS = []


def record(category, name, ok, detail=""):
    icon = "PASS" if ok else "FAIL"
    RESULTS.append({"category": category, "name": name, "ok": ok, "detail": detail})
    print(f"  [{icon}] {category}/{name}: {detail}")


def main():
    print(f"\n== 策略选股器验证 ({LOCAL}/#strategy_picker) ==")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text[:120]}")
                if msg.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:120]}"))

        try:
            t0 = time.time()
            page.goto(f"{LOCAL}/#strategy_picker", wait_until="domcontentloaded", timeout=20_000)
            record("load", "页面跳转", True, f"{time.time()-t0:.1f}s")

            page.wait_for_function(
                "() => typeof window.loadStrategyPicker === 'function'",
                timeout=10_000,
            )
            page.evaluate("window.loadStrategyPicker(false)")
            record("trigger", "loadStrategyPicker() 调用", True, "")

            page.wait_for_function(
                "() => document.querySelectorAll('#sp-list .sp-card, #sp-list .empty-state').length > 0",
                timeout=30_000,
            )
            record("render", "sp-list DOM 渲染", True, "")

            cards = page.eval_on_selector_all("#sp-list .sp-card", "els => els.length")
            empty = page.eval_on_selector_all("#sp-list .empty-state", "els => els.length")
            record("data", "卡片数 > 0", cards > 0,
                   f"cards={cards} empty-state={empty}")

            # 截图
            try:
                page.screenshot(path="/tmp/strategy_picker_v2.png", full_page=False)
                record("screenshot", "截图", True, "/tmp/strategy_picker_v2.png")
            except Exception as e:
                record("screenshot", "截图", False, str(e)[:60])

            # 验证首卡片 chip + code link
            if cards > 0:
                first_card = page.query_selector("#sp-list .sp-card")
                code = first_card.get_attribute("data-code") if first_card else None
                has_link = page.query_selector_all("#sp-list .sp-card .stock-link")
                has_wl_btn = page.query_selector_all("#sp-list .sp-card .wl-toggle-btn")
                has_chips = page.eval_on_selector_all(
                    "#sp-list .sp-card .sp-strat-chip", "els => els.length"
                )
                record("first_card", "含 code+link",
                       code is not None and len(has_link) > 0,
                       f"code={code} links={len(has_link)} chips={has_chips} ⭐={len(has_wl_btn)}")
                record("chips", "首卡片 ≥ 1 个 chip", has_chips > 0, f"chips={has_chips}")

                # 验证: code 点击跳转 (用新 page 不抢当前)
                if has_link:
                    test_page = ctx.new_page()
                    try:
                        test_page.goto(
                            f"{LOCAL}/#strategy_picker",
                            wait_until="domcontentloaded", timeout=10_000,
                        )
                        test_page.wait_for_function(
                            "() => document.querySelectorAll('#sp-list .sp-card').length > 0",
                            timeout=20_000,
                        )
                        test_link = test_page.query_selector("#sp-list .sp-card .stock-link")
                        if test_link:
                            code_target = test_link.get_attribute("data-code")
                            test_link.click()
                            try:
                                test_page.wait_for_url(
                                    f"**stock={code_target}*",
                                    timeout=10_000,
                                )
                                record("nav", "code → 跳个股", True,
                                       f"code={code_target} url_ok")
                            except Exception:
                                cur_url = test_page.url
                                record("nav", "code → 跳个股",
                                       "stock=" in cur_url,
                                       f"code={code_target} url={cur_url[-50:]}")
                    except Exception as e:
                        record("nav", "code → 跳个股", False, str(e)[:80])
                    finally:
                        test_page.close()

            # Console 错误
            critical = [e for e in console_errors if "strategy" in e.lower()
                        or "TypeError" in e or "ReferenceError" in e]
            record("console", "无 JS 错误", len(critical) == 0,
                   f"total={len(console_errors)}, critical={len(critical)}")

        except Exception as e:
            record("fatal", f"测试异常: {type(e).__name__}", False, str(e)[:120])
        finally:
            ctx.close()
            browser.close()

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\n== 总 {total} 项 · 通过 {passed} · 失败 {total-passed} ==")
    sys.exit(0 if total == passed else 1)


if __name__ == "__main__":
    main()
