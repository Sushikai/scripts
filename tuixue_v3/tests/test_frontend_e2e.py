#!/usr/bin/env python3
"""
tuixue_v3 端到端前端测试
========================
模拟真实用户行为:
1. 打开首页 (本地 + 远程 ngrok)
2. 等待 JS 加载 + 检查 console 错误
3. 模拟点击关键按钮 (选股 / 个股 / AI 分析)
4. 验证关键数据是否显示
5. 检查所有静态资源 200
6. 检查关键 API 端点

退出码:
  0 = 全部通过
  1 = 有失败 (详情见报告)
"""
import sys, os, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LOCAL = "http://localhost:7799"
REMOTE = "https://nondepreciative-florentino-mimically.ngrok-free.dev"
TEST_CODE = "002747"
RESULTS = []

def record(category, name, ok, detail=""):
    icon = "✅" if ok else "❌"
    RESULTS.append({"category": category, "name": name, "ok": ok, "detail": detail})
    print(f"  {icon} [{category}] {name}: {detail}")

def test_page_load(p, url, label):
    """打开页面，检查是否真的加载出来"""
    print(f"\n━━ {label} ({url}) ━━")
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    )
    page = context.new_page()

    console_errors = []
    network_errors = []
    page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type in ("error",) else None)
    page.on("requestfailed", lambda req: network_errors.append(f"{req.method} {req.url} - {req.failure}"))
    page.on("response", lambda resp: network_errors.append(f"{resp.status} {resp.url}") if resp.status >= 400 else None)

    try:
        t0 = time.time()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        load_time = time.time() - t0
        record("page", f"{label} 加载", resp is not None and resp.status == 200, f"HTTP {resp.status if resp else '?'} | {load_time:.1f}s")

        # 检查页面是否有真内容
        title = page.title()
        body_text = page.evaluate("() => document.body.innerText")
        has_real_content = "退学" in body_text or "操作台" in body_text or len(body_text) > 200
        record("page", f"{label} 渲染内容", has_real_content, f"title='{title[:30]}' body_len={len(body_text)}")

        # 检查关键 JS 加载
        app_loaded = page.evaluate("() => typeof window !== 'undefined' && document.querySelectorAll('button').length > 0")
        btn_count = page.evaluate("() => document.querySelectorAll('button').length")
        record("page", f"{label} JS 执行", app_loaded, f"按钮数={btn_count}")

        # ngrok 警告页检测
        is_warning = "ERR_NGROK" in body_text or "ngrok-skip-browser-warning" in body_text
        record("page", f"{label} 非警告页", not is_warning, "ngrok warning page" if is_warning else "real content")

        # console 错误
        if console_errors:
            for e in console_errors[:3]:
                record("page", f"{label} console", False, e[:80])
        else:
            record("page", f"{label} console", True, "no errors")

    except PWTimeout as e:
        record("page", f"{label} 加载", False, f"超时: {str(e)[:80]}")
    except Exception as e:
        record("page", f"{label} 加载", False, f"{type(e).__name__}: {str(e)[:80]}")
    finally:
        browser.close()
    return network_errors

def test_api_endpoints():
    """直接测所有关键 API"""
    print(f"\n━━ API 端点测试 ━━")
    import requests
    apis = [
        ("GET", "/api/health", None),
        ("GET", "/api/laws", None),
        ("GET", "/api/metrics", None),
        ("GET", "/api/market/overview", None),
        ("GET", f"/api/stock/{TEST_CODE}", None),
        ("GET", "/static/app.js", None),
        ("GET", "/static/style.css", None),
        ("POST", "/api/screen", {"mode":"live","top_n":3,"pool_size":5}),
        ("GET", f"/api/stock/{TEST_CODE}/ai_analysis", None),
    ]
    for method, ep, body in apis:
        try:
            t0 = time.time()
            if method == "GET":
                r = requests.get(LOCAL + ep, timeout=15)
            else:
                r = requests.post(LOCAL + ep, json=body, timeout=90)
            elapsed = time.time() - t0
            ok = r.status_code == 200
            detail = f"HTTP {r.status_code} | {elapsed:.1f}s | {len(r.content)}B"
            if ok:
                try:
                    j = r.json()
                    if j.get("ok"):
                        detail += f" | data_ok"
                except: pass
            record("api", f"{method} {ep}", ok, detail)
        except Exception as e:
            record("api", f"{method} {ep}", False, f"{type(e).__name__}: {str(e)[:60]}")

def test_frontend_interactions():
    """用 playwright 模拟用户点击"""
    print(f"\n━━ 前端交互测试 ━━")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width":1280,"height":800}).new_page()
        try:
            page.goto(LOCAL, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)  # 等 JS 完全跑

            # 找输入框 - 股票代码
            inputs = page.query_selector_all("input")
            record("ui", "找到输入框", len(inputs) > 0, f"input 数={len(inputs)}")

            # 尝试找搜索框 (placeholder / id / name)
            search_input = None
            for sel in ['input[placeholder*="代码"]', 'input[placeholder*="股票"]', 'input[type="search"]', 'input[id*="search"]', 'input[id*="code"]']:
                el = page.query_selector(sel)
                if el:
                    search_input = el
                    break
            if not search_input and inputs:
                search_input = inputs[0]

            if search_input:
                search_input.fill(TEST_CODE)
                record("ui", "填入代码", True, f"code={TEST_CODE}")

                # 找按钮并点击
                btns = page.query_selector_all("button")
                # 找文字含"分析"/"AI"/"查询"的按钮
                click_target = None
                for btn in btns:
                    text = btn.inner_text()
                    if any(k in text for k in ["AI", "分析", "查询", "搜索", "开始", "选股"]):
                        click_target = btn
                        break
                if click_target:
                    record("ui", "找到按钮", True, f"'{click_target.inner_text()[:20]}'")
                    click_target.click()
                    # 等响应
                    page.wait_for_timeout(3000)
                    record("ui", "点击后无错误", not page.evaluate("() => !!document.querySelector('.error, [class*=error]')"), "ok")
                else:
                    record("ui", "找到可点击按钮", False, "未找到 AI/分析 按钮")
            else:
                record("ui", "找到搜索框", False, "无 input")
        except PWTimeout as e:
            record("ui", "交互测试", False, f"超时: {str(e)[:60]}")
        except Exception as e:
            record("ui", "交互测试", False, f"{type(e).__name__}: {str(e)[:60]}")
        finally:
            browser.close()

def main():
    print("════════════════════════════════════════════════════════")
    print("  tuixue_v3 端到端前端测试")
    print(f"  本地: {LOCAL}")
    print(f"  远程: {REMOTE}")
    print("════════════════════════════════════════════════════════")

    # 1. API 端点 (最先, 快速发现问题)
    test_api_endpoints()

    # 2. 本地页面
    with sync_playwright() as p:
        test_page_load(p, LOCAL, "本地")
        test_page_load(p, REMOTE, "远程")

    # 3. 前端交互
    test_frontend_interactions()

    # 总结
    print("\n════════════════════════════════════════════════════════")
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"  总计: {total} | 通过: {passed} | 失败: {failed}")
    print("════════════════════════════════════════════════════════")

    if failed > 0:
        print("\n❌ 失败的项:")
        for r in RESULTS:
            if not r["ok"]:
                print(f"   [{r['category']}] {r['name']}: {r['detail']}")
        print(f"\n退出码 1 (有 {failed} 项失败)")
        sys.exit(1)
    else:
        print("\n✅ 全部通过!")
        sys.exit(0)

if __name__ == "__main__":
    main()