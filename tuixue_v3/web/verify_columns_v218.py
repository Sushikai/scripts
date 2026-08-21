"""Verify 联动 + 风险 + 角色 + 量价 columns on iPhone 13 at view-all_stocks."""
import sys, json
from playwright.sync_api import sync_playwright

URL = "https://study-tuition-nylon.ngrok-free.dev/"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            is_mobile=True, has_touch=True,
            extra_http_headers={},
        )
        # Bypass 6024 cookie (per user instruction) — set on BOTH bare host and parent domain
        for d in (".ngrok-free.dev", "study-tuition-nylon.ngrok-free.dev"):
            ctx.add_cookies([{
                "name": "abuse_interstitial", "value": "study-tuition-nylon.ngrok-free.dev",
                "domain": d, "path": "/",
                "secure": True, "sameSite": "None",
            }])
        # Visit a noop endpoint first to ensure cookie is accepted before main page
        page = ctx.new_page()
        try:
            page.goto("about:blank")
        except Exception:
            pass
        cookies = ctx.cookies("https://study-tuition-nylon.ngrok-free.dev/")
        print(f"Cookies set: {[c.get('name')+'='+c.get('value')+' domain='+c.get('domain') for c in cookies]}")

        console_errors = []
        page_errors = []
        reqs = []
        page.on("console", lambda m: console_errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("request", lambda r: reqs.append(f"{r.method} {r.url} cookies={r.headers.get('cookie','')[:120]}"))
        page.on("response", lambda r: None)

        print(f"Navigating to {URL}...")
        last_err = None
        for attempt in range(3):
            try:
                page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                break
            except Exception as e:
                last_err = e
                print(f"goto attempt {attempt+1} failed: {e}; retrying...")
                page.wait_for_timeout(1500)
        else:
            raise last_err

        # First clear SW caches (old v217 with TDZ bug may be active)
        print("Clearing SW caches...")
        try:
            page.evaluate("""
              async () => {
                const ks = await caches.keys();
                for (const k of ks) { await caches.delete(k); }
                if ('serviceWorker' in navigator) {
                  const regs = await navigator.serviceWorker.getRegistrations();
                  for (const r of regs) { try { await r.unregister(); } catch(e){} }
                }
                return ks;
              }
            """)
        except Exception as e:
            print(f"SW clear warning: {e}")

        print("Reloading after SW clear...")
        for attempt in range(3):
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                break
            except Exception as e:
                print(f"reload attempt {attempt+1} failed: {e}; retrying...")
                page.wait_for_timeout(2000)
        else:
            print("Reload failed after 3 attempts; continuing")
        page.wait_for_timeout(3000)

        print("Navigating to 全A view (all_stocks)...")
        # Click via JS — sidebar sections may not be <a>
        clicked_hash = page.evaluate("""
          () => {
            const candidates = Array.from(document.querySelectorAll('[data-view], .nav-item, .sidebar *, .tabbar *'));
            const t = candidates.find(a => {
              const dv = a.getAttribute && a.getAttribute('data-view');
              const txt = (a.innerText||'').trim();
              return dv === 'all_stocks' || /全\\s*A/.test(txt) || /all_stocks/.test((a.getAttribute && a.getAttribute('href'))||'');
            });
            if (t) { t.click(); return {tag: t.tagName, dv: t.getAttribute('data-view'), txt: (t.innerText||'').slice(0,60)}; }
            // fallback: set hash and dispatch
            location.hash = '#all_stocks';
            return null;
          }
        """)
        print(f"Clicked: {clicked_hash}")
        # wait for actual data rows (not 加载中…)
        try:
            page.wait_for_function("""
              () => {
                const tbl = document.querySelector('.view-all_stocks table');
                if (!tbl) return false;
                const trs = tbl.querySelectorAll('tbody tr');
                if (trs.length < 5) return false;
                const first = trs[0];
                return !/加载中/.test(first.innerText);
              }
            """, timeout=15000)
            print("Data loaded")
        except Exception as e:
            print(f"Wait timeout: {e}")
        page.wait_for_timeout(1500)

        # Detect which view is currently visible
        view_state = page.evaluate("""
          () => {
            const views = Array.from(document.querySelectorAll('.view, [id^="view-"]'));
            const visible = views.filter(v => {
              const cs = getComputedStyle(v);
              return cs.display !== 'none' && !v.hasAttribute('hidden');
            }).map(v => ({id: v.id, cls: v.className}));
            return {visible, total: views.length, hash: location.hash,
                    url: location.href, title: document.title,
                    bodyLen: document.body ? document.body.innerHTML.length : 0,
                    scripts: Array.from(document.scripts).map(s=>s.src).slice(0,10),
                    rootIds: Array.from(document.body ? document.body.children : []).map(c=>c.id||c.tagName).slice(0,10)};
          }
        """)
        print(f"View state: {json.dumps(view_state, ensure_ascii=False)}")

        # Probe columns via DOM
        result = page.evaluate(r"""
          () => {
            const out = {rows: 0, cols: {}, first5: {}, colIndex: {}, diag: {}};
            const root = document.querySelector('.view-all_stocks') || document.querySelector('#view-all_stocks') || document;
            out.diag.rootCls = root.className;
            out.diag.rootId = root.id;
            // Find any table — there may be multiple
            const tables = Array.from(root.querySelectorAll('table'));
            out.diag.tableCount = tables.length;
            // Look for headers in any table
            const allThs = Array.from(root.querySelectorAll('thead th')).map((th,i)=>({
              i, text: (th.innerText||'').trim(), dataCol: th.getAttribute('data-col')||''
            }));
            out.diag.allHeaders = allThs.slice(0, 30);
            // Also check for any data-col cells directly (mobile cards?)
            const dcells = Array.from(root.querySelectorAll('[data-col]')).slice(0,10).map(el => ({
              tag: el.tagName, dataCol: el.getAttribute('data-col'), text: (el.innerText||'').slice(0,40)
            }));
            out.diag.dataColCells = dcells;
            // Pick the first table that has data
            let tbl = tables.find(t => t.querySelector('tbody tr')) || tables[0];
            if (!tbl) return {...out, error: 'no table'};
            const ths = Array.from(tbl.querySelectorAll('thead th')).map((th,i)=>({
              i, text: (th.innerText||'').trim(), dataCol: th.getAttribute('data-col')||''
            }));
            const wanted = ['联动','风险','角色','量价'];
            for (const w of wanted) {
              const hit = ths.find(t => t.text === w || t.text.includes(w) || t.dataCol === w);
              out.colIndex[w] = hit ? hit.i : -1;
            }
            const tbodies = tbl.querySelectorAll('tbody');
            let body = null;
            for (const tb of tbodies) { if (tb.querySelector('tr')) { body = tb; break; } }
            if (!body) return {...out, error: 'no rows'};
            const trs = Array.from(body.querySelectorAll('tr')).slice(0, 5);
            out.rows = trs.length;
            for (const w of wanted) {
              const idx = out.colIndex[w];
              out.first5[w] = trs.map(tr => {
                if (idx < 0) return null;
                const td = tr.children[idx];
                return td ? (td.innerText||'').trim() : null;
              });
            }
            out.headers = ths;
            out.firstRowCells = trs[0] ? Array.from(trs[0].children).slice(0, 20).map(c => (c.innerText||'').slice(0,20)) : [];
            return out;
          }
        """)

        print("=== RESULT ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("=== CONSOLE ERRORS ===")
        for e in console_errors[:20]:
            print(e)
        print("=== PAGE ERRORS ===")
        for e in page_errors[:20]:
            print(e)
        print("=== FIRST REQUESTS ===")
        for r in reqs[:8]:
            print(r)

        # Quick screenshot
        try:
            page.screenshot(path="/tmp/v218_verify.png", full_page=False)
            print("Screenshot saved /tmp/v218_verify.png")
        except Exception as e:
            print(f"Screenshot failed: {e}")

        browser.close()
        return result, console_errors, page_errors

if __name__ == "__main__":
    main()