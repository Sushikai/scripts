"""
Trace the "AI推荐→个股页刷不出来" bug
- 启动 headless chromium 独立 profile
- 进 #yeren-ai, 等加载, 截图
- 找 .stock-code-link 第一个, 点击
- 等待 5s, 收集所有 /api/stock/* 请求状态、响应时间
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))

from playwright.sync_api import sync_playwright

os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', os.path.expanduser('~/.cache/ms-playwright'))

TRACE_DIR = '/tmp/tx3-trace'
os.makedirs(TRACE_DIR, exist_ok=True)


def main():
    out = []
    with sync_playwright() as p:
        # 独立 profile 不能影响主浏览器 (per memory)
        browser = p.chromium.launch_persistent_context(
            user_data_dir='/tmp/.pw-tx3-aitostock',
            headless=True,
            args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
            viewport={'width': 1440, 'height': 900},
            ignore_https_errors=True,
        )
        page = browser.pages[0]
        api_calls = []
        page.on('request', lambda req: (
            api_calls.append({'evt': 'REQ', 'url': req.url, 'method': req.method, 'ts': time.time()})
            if ('/api/' in req.url and 'metrics' not in req.url and 'trace' not in req.url)
            else None))
        page.on('response', lambda resp: (
            api_calls.append({'evt': 'RES', 'url': resp.url, 'status': resp.status, 'ttfb_ms': resp.request.timing.get('responseEnd', 0) if hasattr(resp.request, 'timing') else None, 'ts': time.time()})
            if ('/api/' in resp.url and 'metrics' not in resp.url and 'trace' not in resp.url)
            else None))
        page.on('console', lambda msg: print(f'[CONSOLE.{msg.type}] {msg.text}'[:300]))
        page.on('pageerror', lambda exc: print(f'[PAGEERROR] {exc}'[:300]))

        # 1) 启动, 默认 dash
        print('1) 打开 #dash')
        page.goto('http://localhost:7799/', wait_until='networkidle', timeout=30000)
        time.sleep(1.5)
        page.screenshot(path=f'{TRACE_DIR}/01_dash.png')

        # 2) 切到 yeren-ai
        print('2) 切到 yeren-ai')
        page.evaluate("location.hash = '#yeren-ai'")
        time.sleep(2.5)
        page.screenshot(path=f'{TRACE_DIR}/02_yeren.png')

        # 3) 等首页建议或输入'今天什么主线'
        # 找到输入框
        msg_in = page.query_selector('#yeren-ai-input') or page.query_selector('textarea') or page.query_selector('input[type="text"]')
        if msg_in:
            msg_in.fill('今天什么主线? 推一只股票')
            time.sleep(0.3)
            page.screenshot(path=f'{TRACE_DIR}/03_filled.png')
            # 找发送按钮
            send = page.query_selector('#yeren-ai-send') or page.query_selector('button.yeren-ai-send') or page.query_selector('button.send')
            if not send:
                btns = page.query_selector_all('button')
                send = next((b for b in btns if b.is_visible() and ('发送' in (b.inner_text() or '') or 'ask' in (b.inner_text() or '').lower())), None)
            if send:
                print('  找到 send 按钮,点击')
                before = len(api_calls)
                send.click()
                # 等 35s 拿回复
                time.sleep(35)
                page.screenshot(path=f'{TRACE_DIR}/04_reply.png', full_page=True)
                # 找链接
                link = page.query_selector('.stock-code-link')
                if link:
                    code = link.get_attribute('data-code') or '?'
                    print(f'4) 找到 .stock-code-link data-code={code}')
                    # 等页面稳定
                    time.sleep(0.5)
                    api_calls.clear()
                    print('5) 点击链接 → 切到 stock 页')
                    # 标记 click 前
                    before_click = page.evaluate('window._currentStockCode')
                    print(f'   click前 _currentStockCode={before_click}')
                    link.click()
                    # 等 8s
                    time.sleep(8)
                    page.screenshot(path=f'{TRACE_DIR}/05_after_click.png', full_page=True)
                    after = page.evaluate('window._currentStockCode')
                    cur_view = page.evaluate('window._currentViewName')
                    loc_hash = page.evaluate('location.hash')
                    stock_code_field = page.evaluate("() => (document.getElementById('stock-code') && document.getElementById('stock-code').value) || ''")
                    print(f'   click后 _currentStockCode={after} _currentViewName={cur_view} hash={loc_hash} #stock-code={stock_code_field}')
                    # 收集本次点击触发的 API
                    click_calls = [c for c in api_calls if 'stock/' in c['url'] and not c['url'].endswith('/metrics')]
                    stock_calls = [c for c in click_calls if c['evt'] == 'REQ']
                    print(f'   click后发出的 /api/stock 请求数: {len(set(c["url"] for c in stock_calls))}')
                    for c in click_calls[:80]:
                        print(f'     {c["evt"]} {c.get("status", "")} {c["url"][:120]}')
                else:
                    print('!! 没找到 .stock-code-link — AI 回复里没推荐股票')
                    page.screenshot(path=f'{TRACE_DIR}/04_no_link.png', full_page=True)
            else:
                print('!! 没找到 send 按钮')
                page.screenshot(path=f'{TRACE_DIR}/03_no_send.png', full_page=True)
        else:
            print('!! 没找到输入框')
            page.screenshot(path=f'{TRACE_DIR}/02_no_input.png')

        # 写 trace
        with open(f'{TRACE_DIR}/trace.json', 'w') as f:
            json.dump(api_calls, f, indent=2, ensure_ascii=False)
        print(f'api_calls 总数: {len(api_calls)}')

        browser.close()

if __name__ == '__main__':
    main()
