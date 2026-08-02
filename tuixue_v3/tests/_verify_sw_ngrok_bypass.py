"""
tests/_verify_sw_ngrok_bypass.py — 验证 SW v296 ngrok bypass 逻辑 (LAN localhost)
LAN 上 SW 不会注入 header (regex 仅匹配 ngrok 域),但能验证 SW 注册 + cache bump
ngrok bypass 实际生效: 通过 curl 测试 ngrok URL + skip header 返回 app HTML
"""
from __future__ import annotations
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

NGROK_URL = "https://study-tuition-nylon.ngrok-free.dev/"
LAN_URL = "http://192.168.101.50:7799/"
OUT = Path("/tmp/verify_sw_ngrok_bypass")
OUT.mkdir(exist_ok=True)


def curl_ngrok_with_skip(retries=3) -> tuple[int, str]:
    """curl ngrok URL 带 skip header, 返回 (status, first_300_chars)"""
    for i in range(retries):
        r = subprocess.run(
            ["curl", "-si", "--max-time", "15",
             "-H", "ngrok-skip-browser-warning: 1",
             "-A", "tuixue-v3-mobile/1.0",
             NGROK_URL],
            capture_output=True, text=True
        )
        out = r.stdout
        status = 0
        if out.startswith("HTTP/"):
            try:
                status = int(out.split(" ")[1])
            except:
                pass
        if status == 200 and len(out) > 500:
            return status, out[:1000]
        time.sleep(2)
    return status, out[:1000]


def curl_ngrok_no_skip(retries=3) -> tuple[int, str]:
    """curl ngrok URL 不带 skip header, 用 iPhone Safari UA"""
    for i in range(retries):
        r = subprocess.run(
            ["curl", "-si", "--max-time", "15",
             "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
             NGROK_URL],
            capture_output=True, text=True
        )
        out = r.stdout
        status = 0
        if out.startswith("HTTP/"):
            try:
                status = int(out.split(" ")[1])
            except:
                pass
        if status == 200 and len(out) > 200:
            return status, out[:1000]
        time.sleep(2)
    return status, out[:1000]


def verify_sw_on_lan():
    """LAN 上验证 SW v296 已注册 + 新 fetch handler 生效"""
    print(f"[1] SW v296 LAN 验证")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 用 localhost:7799 避免 origin 不匹配问题
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()

        page.goto("http://localhost:7799/", wait_until="networkidle", timeout=20000)
        time.sleep(5)  # 等 SW 注册完成 + 激活

        sw_info = page.evaluate("""async () => {
          if (!('serviceWorker' in navigator)) return null;
          const reg = await navigator.serviceWorker.getRegistration();
          return reg ? {
            scope: reg.scope,
            active_script_url: reg.active && reg.active.scriptURL,
            controller: !!navigator.serviceWorker.controller
          } : null;
        }""")
        print(f"  SW: {sw_info}")

        # 验证 SW 源文件内容包含 v296
        if sw_info and sw_info.get("active_script_url"):
            sw_text = page.evaluate("""async (url) => {
              const r = await fetch(url);
              return await r.text();
            }""", sw_info["active_script_url"])
            has_v296 = "v296" in sw_text and "skip-browser-warning" in sw_text
            print(f"  SW text length: {len(sw_text)}")
            print(f"  SW v296 marker: {has_v296}")
            if has_v296:
                print(f"  ✓ SW v296 ngrok-bypass 已激活")

        page.screenshot(path=str(OUT / "sw_lan.png"))
        ctx.close()
        browser.close()


def main():
    print(f"=== ngrok bypass 验证 (SW v296) ===\n")

    # 1) SW 在 LAN 上的部署状态
    try:
        verify_sw_on_lan()
    except Exception as e:
        print(f"  SW 验证异常: {e}")

    print()

    # 2) ngrok URL 带/不带 skip header 的对比
    print(f"[2] ngrok URL bypass header 验证")
    print(f"  - 不带 skip header (iPhone Safari UA):")
    status1, body1 = curl_ngrok_no_skip()
    is_6024 = "ngrok-error-code" in body1 or "ERR_NGROK_6024" in body1
    print(f"    HTTP {status1}, is_6024={is_6024}")

    print(f"  - 带 skip header (tuixue-v3-mobile UA):")
    status2, body2 = curl_ngrok_with_skip()
    is_app = "<!doctype html>" in body2.lower() and "退学" in body2
    has_6024 = "ERR_NGROK_6024" in body2
    print(f"    HTTP {status2}, is_app={is_app}, has_6024_header={has_6024}")

    print()
    print(f"=== 验收 ===")
    print(f"ngrok 6024 (无 header): {'✓ 确认触发' if is_6024 else '✗ 未触发 (说明 ngrok 域 bypass)'}")
    print(f"ngrok app (带 header): {'✓ 直接进 app' if is_app else '✗ 仍被拦'}")


if __name__ == "__main__":
    main()