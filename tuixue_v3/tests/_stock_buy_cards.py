#!/usr/bin/env python3
"""
个股页 3 个买点策略卡 e2e 验证
=============================
1. 打开 ?code=002747
2. 等 JS 完成
3. 检查 #q-weekly-bull-card / #q-recovery-card / #q-ma5-rules-card 渲染
4. 检查卡片内容 (chips / levels / 5 条原则)
5. 检查 console 无 JS 错误
"""
import sys, os, time
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
    print(f"\n━━ 个股页 3 个买点卡 e2e ({LOCAL}/?code={TEST_CODE}) ━━")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1200})
        page = ctx.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text[:120]}") if msg.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:120]}"))

        try:
            t0 = time.time()
            page.goto(f"{LOCAL}/?code={TEST_CODE}", wait_until="domcontentloaded", timeout=20_000)
            load_time = time.time() - t0
            record("load", f"页面加载", True, f"{load_time:.1f}s")

            # 等 JS 跑 + 异步 loader 完成
            page.wait_for_timeout(4000)

            # 1) 买点 1 · 周线擒牛卡
            card1 = page.query_selector("#q-weekly-bull-card")
            card1_visible = card1 and not card1.is_hidden()
            record("card", "周线擒牛卡存在", card1 is not None, "")
            record("card", "周线擒牛卡 visible", card1_visible, "")

            if card1_visible:
                body1 = card1.inner_text()
                # 不管命中 0/几, 都应该有 标题 + 周收盘 + 5W MA 三种之一
                has_meta = "周收盘" in body1 or "周涨跌" in body1 or "未命中" in body1
                record("card", "周线擒牛卡有内容", has_meta, f"text len={len(body1)}")

            # 2) 买点 2 · 1/3 回升位卡
            card2 = page.query_selector("#q-recovery-card")
            card2_visible = card2 and not card2.is_hidden()
            record("card", "1/3 回升位卡存在", card2 is not None, "")
            record("card", "1/3 回升位卡 visible", card2_visible, "")

            if card2_visible:
                body2 = card2.inner_text()
                has_recovery = "1/3" in body2 or "回升" in body2 or "A" in body2 or "B" in body2 or "未找到" in body2
                record("card", "1/3 回升位卡有内容", has_recovery, f"text len={len(body2)}")

            # 3) 买点 3 · 5日线 5 原则 (静态)
            card3 = page.query_selector("#q-ma5-rules-card")
            card3_visible = card3 and not card3.is_hidden()
            record("card", "5日线 5 原则卡存在", card3 is not None, "")
            record("card", "5日线 5 原则卡 visible", card3_visible, "")

            if card3_visible:
                li_count = page.eval_on_selector_all("#q-ma5-rules-body li", "els => els.length")
                record("card", "5 原则 li 数 = 5", li_count == 5, f"got {li_count}")
                first_li_text = page.eval_on_selector("#q-ma5-rules-body li:first-child", "el => el.innerText")
                has_rule1 = "不放量大阳线" in first_li_text
                record("card", "第 1 原则 '不放量大阳线'", has_rule1, "")

            # 4) Console 无 JS 错误
            critical_errors = [e for e in console_errors if "weekly_bull" in e.lower() or "recovery" in e.lower() or "TypeError" in e or "ReferenceError" in e]
            record("console", "无 JS 错误", len(critical_errors) == 0, f"total={len(console_errors)}, critical={len(critical_errors)}")
            if critical_errors:
                for e in critical_errors[:3]:
                    print(f"     · {e}")

            # 5) 截图
            try:
                Path("/tmp/buy-cards.png").unlink(missing_ok=True)
                page.screenshot(path="/tmp/buy-cards.png", full_page=True)
                record("screenshot", "截图保存", True, "/tmp/buy-cards.png")
            except Exception as e:
                record("screenshot", "截图保存", False, str(e)[:60])

        except Exception as e:
            record("load", "整体测试", False, f"{type(e).__name__}: {str(e)[:80]}")
        finally:
            browser.close()

    # 总结
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"\n━━ 总计: {total} | 通过: {passed} | 失败: {failed} ━━")
    if failed > 0:
        for r in RESULTS:
            if not r["ok"]:
                print(f"   ❌ [{r['category']}] {r['name']}: {r['detail']}")
        sys.exit(1)
    else:
        print("✅ 全部通过!")
        sys.exit(0)

if __name__ == "__main__":
    main()
