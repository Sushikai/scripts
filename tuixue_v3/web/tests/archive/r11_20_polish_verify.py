#!/usr/bin/env /Users/kaikai/.hermes/hermes-agent/venv/bin/python3
"""R11-20 Batch 1 视觉专业度验证 · iPhone 13 (390x844)

验证 7 项 CSS 改动:
 1. 行 hover translateX(1px)           — matrix(1,0,0,1,1,0)
 2. 列分隔线 border-right 1px         — hsla(212,15%,60%,0.08)
 3. chip-l1 渐变                        — linear-gradient(135deg
 4. chip hover translateY(-1px)       — matrix(1,0,0,1,0,-1)
 5. sticky-left 阴影过渡              — box-shadow .15s
 6. scrollbar thumb 样式存在          — matchedRules grep
 7. kpi-group hover translateY(-2px) — matrix(1,0,0,1,0,-2)

输出 PASS/FAIL + 截图 2 张.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://study-tuition-nylon.ngrok-free.dev/#all_stocks"
ART = "/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/r11_20_polish"
Path(ART).mkdir(parents=True, exist_ok=True)


async def main():
    results = []
    def _r(name, ok, snippet, extra=""):
        status = "PASS" if ok else "FAIL"
        results.append({"item": name, "ok": ok, "snippet": snippet, "extra": extra})
        print(f"  [{status}] {name}")
        if snippet:
            print(f"          {snippet}")
        if extra:
            print(f"          {extra}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                        "Mobile/15E148 Safari/604.1"),
        )

        # ngrok interstitial bypass — cookie + ngrok-skip-browser-warning header
        await ctx.add_cookies([{
            "name": "abuse_interstitial",
            "value": "study-tuition-nylon.ngrok-free.dev",
            "domain": ".ngrok-free.dev",
            "path": "/",
        }])

        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [JS error] {e}"))
        # Extra belt-and-suspenders: send the skip header explicitly
        await page.set_extra_http_headers({
            "ngrok-skip-browser-warning": "1",
            "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                           "Mobile/15E148 Safari/604.1"),
        })

        print("[nav] opening 全A page...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)

        # Wait for table to render with REAL data (not just placeholder tr)
        # - 8+ rows AND
        # - first row has chip-l1 OR no "加载中" text in tbody
        try:
            await page.wait_for_function(
                """() => {
                    const rows = document.querySelectorAll('.view-all_stocks .stocks-table tbody tr');
                    if (rows.length < 8) return false;
                    const first = rows[0];
                    const html = first.innerHTML || '';
                    // either first row has data (code-link) or all rows are skeleton
                    if (html.includes('code-link')) return true;
                    // fallback: also accept rows that contain '加载中' if total row count is reached via "no-emit"
                    return document.querySelectorAll('.view-all_stocks .stocks-table tbody tr .code-link').length >= 6;
                }""",
                timeout=35000,
            )
            print("  ✓ table data rendered")
        except Exception as e:
            print(f"  [warn] table data may not be fully rendered: {e}")
        # extra settle time after data paint
        await page.wait_for_timeout(2500)

        # ── 截图 1: full page ──
        await page.screenshot(path=f"{ART}/01_full_page.png", full_page=False)
        print(f"[shot] {ART}/01_full_page.png")

        # 1. 行 hover translateX(1px)
        print("\n[1/7] 行 hover translateX(1px)...")
        tr_box = await page.evaluate("""() => {
            const tr = document.querySelector('.view-all_stocks .stocks-table tbody tr:first-child');
            if (!tr) return null;
            const r = tr.getBoundingClientRect();
            return { x: r.x, y: r.y, w: r.width, h: r.height };
        }""")
        if tr_box and tr_box["w"] > 0:
            await page.mouse.move(tr_box["x"] + tr_box["w"]/2,
                                  tr_box["y"] + tr_box["h"]/2)
            await page.wait_for_timeout(280)
            transform = await page.evaluate("""() => {
                const tr = document.querySelector('.view-all_stocks .stocks-table tbody tr:first-child');
                return tr ? getComputedStyle(tr).transform : null;
            }""")
            ok = transform == "matrix(1, 0, 0, 1, 1, 0)"
            _r("row hover translateX(1px)", ok, f"transform = {transform}")
        else:
            _r("row hover translateX(1px)", False, "no visible <tr>")

        # 2. 列分隔线 border-right (any visible td in first row)
        print("\n[2/7] td border-right ...")
        border_info = await page.evaluate("""() => {
            const tr = document.querySelector('.view-all_stocks .stocks-table tbody tr:first-child');
            if (!tr) return { found: false, reason: 'no tr' };
            const tds = [...tr.querySelectorAll('td')];
            if (tds.length < 2) return { found: false, reason: 'tr has <2 td', n: tds.length };
            // find first td with width > 0 and not display:none
            for (const td of tds) {
                const r = td.getBoundingClientRect();
                if (r.width > 4 && r.height > 4) {
                    const cs = getComputedStyle(td);
                    return {
                        found: true,
                        borderRight: cs.borderRight,
                        borderRightWidth: cs.borderRightWidth,
                        borderRightStyle: cs.borderRightStyle,
                        borderRightColor: cs.borderRightColor,
                        dataCol: td.getAttribute('data-col'),
                    };
                }
            }
            return { found: false, reason: 'no visible td', n: tds.length };
        }""")
        if border_info.get("found"):
            br = border_info["borderRight"]
            width_ok = "1px" in border_info["borderRightWidth"]
            style_ok = "solid" in border_info["borderRightStyle"]
            color_ok = "0.08" in border_info["borderRightColor"]
            ok = width_ok and style_ok and color_ok
            _r("td border-right 1px solid 0.08-alpha", ok, f"{br}",
                extra=f"w={border_info['borderRightWidth']} s={border_info['borderRightStyle']} c={border_info['borderRightColor']} data-col={border_info['dataCol']}")
        else:
            _r("td border-right", False, border_info.get("reason", "?"))

        # 3. chip-l1 background (relaxed — accept any non-zero rect)
        print("\n[3/7] chip-l1 background 渐变...")
        chip_bg = await page.evaluate("""() => {
            const chips = document.querySelectorAll('.view-all_stocks .chip-l1');
            if (chips.length === 0) return { found: false, count: 0 };
            // pick first with width>0
            for (const chip of chips) {
                const r = chip.getBoundingClientRect();
                if (r.width > 4 && r.height > 4) {
                    const cs = getComputedStyle(chip);
                    return { found: true, bg: cs.backgroundImage || cs.background,
                             text: chip.textContent, count: chips.length,
                             y: r.y };
                }
            }
            // fallback: just return first chip's bg (may be display:none)
            const c0 = chips[0];
            const cs0 = getComputedStyle(c0);
            return { found: true, bg: cs0.backgroundImage || cs0.background,
                     text: c0.textContent, count: chips.length,
                     y: c0.getBoundingClientRect().y };
        }""")
        if chip_bg.get("found"):
            bg = chip_bg["bg"]
            ok = "linear-gradient(135deg" in bg
            _r("chip-l1 has linear-gradient(135deg", ok, f"bg = {bg[:200]}",
                extra=f"count={chip_bg.get('count')} text='{chip_bg.get('text')}'")
        else:
            _r("chip-l1 gradient", False, "no chip-l1 on page")

        # 4. chip:hover translateY(-1px)  — debug+find any in-viewport chip
        print("\n[4/7] chip:hover translateY(-1px)...")
        # Scroll the table container down so first chip row enters viewport
        await page.evaluate("""() => {
            // scroll the table-wrap to top
            const wrap = document.querySelector('.view-all_stocks .stocks-table');
            if (wrap) wrap.scrollIntoView({block:'start'});
        }""")
        await page.wait_for_timeout(800)

        chip_box = await page.evaluate("""() => {
            // pick chip-l1 chip that's WITHIN the stocks-table tbody
            // (the filter chips above aren't .chip-l1 — only data rows have them)
            const all = document.querySelectorAll('.view-all_stocks .stocks-table tbody .chip');
            const debug = [];
            let pickedFirst = null;
            for (let i = 0; i < all.length; i++) {
                const c = all[i];
                const r = c.getBoundingClientRect();
                const par = c.parentElement;
                if (debug.length < 5) {
                    debug.push({ i, x: Math.round(r.x), y: Math.round(r.y),
                                 w: Math.round(r.width), h: Math.round(r.height),
                                 txt: c.textContent.slice(0, 8),
                                 cls: c.className.slice(0, 30),
                                 dataPri: par ? par.getAttribute('data-priority') : null,
                                 tdDisp: par ? getComputedStyle(par).display : '?' });
                }
                // pick first visible (not display:none, well in viewport)
                if (!pickedFirst && r.width > 4 && r.height > 4 &&
                    getComputedStyle(par).display !== 'none' &&
                    r.x >= 10 && r.x + r.width <= 380 &&
                    r.y >= 100 && r.y + r.height <= 800) {
                    pickedFirst = c;
                }
            }
            if (pickedFirst) {
                const r = pickedFirst.getBoundingClientRect();
                return { x: r.x + r.width/2, y: r.y + r.height/2,
                         w: r.width, h: r.height,
                         text: pickedFirst.textContent,
                         cls: pickedFirst.className.slice(0, 40),
                         debug, total: all.length };
            }
            return { debug, total: all.length };
        }""")
        if chip_box and (chip_box.get("w", 0) or 0) > 0:
            # Re-fetch the rect AFTER scroll (may have shifted), then hover
            fresh = await page.evaluate("""(target) => {
                const chips = document.querySelectorAll('.view-all_stocks .stocks-table tbody .chip');
                let best = null, bestDist = 9999;
                for (const c of chips) {
                    const r = c.getBoundingClientRect();
                    const cx = r.x + r.width/2, cy = r.y + r.height/2;
                    const d = Math.hypot(cx - target.x, cy - target.y);
                    if (d < bestDist) { bestDist = d; best = c; }
                }
                if (!best) return null;
                const r = best.getBoundingClientRect();
                return { x: r.x + r.width/2, y: r.y + r.height/2,
                         w: r.width, h: r.height,
                         text: best.textContent, cls: best.className };
            }""", chip_box)
            if not fresh or fresh["w"] <= 0:
                _r("chip:hover translateY(-1px)", False, "no rect after fresh query", extra=str(chip_box.get("debug", []))[:200])
            else:
                # move mouse out first to ensure clean hover state
                await page.mouse.move(0, 0)
                await page.wait_for_timeout(80)
                # move mouse IN with steps to fire hover
                await page.mouse.move(fresh["x"], fresh["y"], steps=8)
                await page.wait_for_timeout(450)
                transform = await page.evaluate("""(box) => {
                    const chips = document.querySelectorAll('.view-all_stocks .stocks-table tbody .chip');
                    let best = null, bestDist = 9999;
                    for (const c of chips) {
                        const r = c.getBoundingClientRect();
                        const cx = r.x + r.width/2, cy = r.y + r.height/2;
                        const d = Math.hypot(cx - box.x, cy - box.y);
                        if (d < bestDist) { bestDist = d; best = c; }
                    }
                    if (!best) return null;
                    return { t: getComputedStyle(best).transform,
                             text: best.textContent, dist: bestDist,
                             hov: best.matches(':hover'),
                             rect: best.getBoundingClientRect() };
                }""", fresh)
                ok = transform and transform["t"] == "matrix(1, 0, 0, 1, 0, -1)"
                _r("chip:hover translateY(-1px)", ok,
                    f"transform = {transform['t'] if transform else 'None'}",
                    extra=f"matches(:hover)={transform['hov'] if transform else '?'} dist={transform['dist'] if transform else '?'} text='{transform['text'] if transform else '?'}' cls='{(transform and fresh.get('cls','')) or ''}'" if transform else "")
                chip_box_used = fresh
        else:
            dbg = chip_box.get("debug", []) if isinstance(chip_box, dict) else []
            dbg_str = " ; ".join(f"#{d['i']}({d['x']},{d['y']},{d['w']}x{d['h']},'{d['txt']}',pri={d.get('dataPri','?')})" for d in dbg[:5])
            _r("chip:hover translateY(-1px)", False,
                f"no in-viewport chip · debug: {dbg_str}")

        # 5. sticky-left 阴影过渡
        print("\n[5/7] sticky-left transition contains box-shadow .15s...")
        sticky_info = await page.evaluate("""() => {
            const td = document.querySelector('.view-all_stocks .stocks-table tbody td.sticky-left');
            if (!td) return null;
            return getComputedStyle(td).transition;
        }""")
        if sticky_info is not None:
            ok_box = "box-shadow" in sticky_info
            ok_dur = "0.15s" in sticky_info or ".15s" in sticky_info
            ok = ok_box and ok_dur
            _r("sticky-left transition box-shadow .15s", ok, f"transition = {sticky_info}",
                extra=f"box-shadow={ok_box} dur_match={ok_dur}")
        else:
            _r("sticky-left transition", False, "no .sticky-left td")

        # 6. scrollbar thumb rule exists in matchedRules
        print("\n[6/7] .table-wrap::-webkit-scrollbar-thumb style...")
        scrollbar_rule = await page.evaluate("""() => {
            const sheets = [...document.styleSheets];
            for (const s of sheets) {
                let rules;
                try { rules = s.cssRules; } catch (e) { continue; }
                if (!rules) continue;
                for (const r of rules) {
                    const txt = r.cssText || '';
                    if (txt.includes('table-wrap') && txt.includes('webkit-scrollbar-thumb')) {
                        return txt;
                    }
                    if (r.cssRules) {
                        for (const sr of r.cssRules) {
                            const t2 = sr.cssText || '';
                            if (t2.includes('webkit-scrollbar-thumb') &&
                                (t2.includes('hsla(') || t2.includes('rgba('))) {
                                return t2;
                            }
                        }
                    }
                }
            }
            return null;
        }""")
        ok = scrollbar_rule is not None and "webkit-scrollbar-thumb" in scrollbar_rule
        _r("scrollbar thumb rule exists", ok, (scrollbar_rule or "NOT FOUND")[:300])

        # 7. kpi-group hover translateY(-2px) — fresh query, try multiple cards
        print("\n[7/7] kpi-group hover translateY(-2px)...")
        # Scroll back to top to ensure KPIs are in viewport
        await page.evaluate("window.scrollTo(0,0)")
        await page.wait_for_timeout(400)
        kpi_box = await page.evaluate("""() => {
            const ks = document.querySelectorAll('.view-all_stocks #as-kpis .kpi-group');
            for (const k of ks) {
                const r = k.getBoundingClientRect();
                if (r.width > 4 && r.height > 4 &&
                    r.x >= 10 && r.x + r.width <= 380 &&
                    r.y >= 100 && r.y + r.height <= 700) {
                    return { x: r.x + r.width/2, y: r.y + r.height/2,
                             w: r.width, h: r.height };
                }
            }
            // fallback: any positive
            for (const k of ks) {
                const r = k.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return { x: r.x + r.width/2, y: r.y + r.height/2,
                             w: r.width, h: r.height };
                }
            }
            return null;
        }""")
        if kpi_box and kpi_box["w"] > 0:
            # fresh re-query to ensure mouse hits
            await page.mouse.move(0, 0)
            await page.wait_for_timeout(80)
            await page.mouse.move(kpi_box["x"], kpi_box["y"], steps=8)
            await page.wait_for_timeout(450)
            transform = await page.evaluate("""(box) => {
                const ks = document.querySelectorAll('.view-all_stocks #as-kpis .kpi-group');
                let best = null, bestDist = 9999;
                for (const k of ks) {
                    const r = k.getBoundingClientRect();
                    const cx = r.x + r.width/2, cy = r.y + r.height/2;
                    const d = Math.hypot(cx - box.x, cy - box.y);
                    if (d < bestDist) { bestDist = d; best = k; }
                }
                if (!best) return null;
                return { t: getComputedStyle(best).transform,
                         hov: best.matches(':hover'), dist: bestDist };
            }""", kpi_box)
            ok = transform and transform["t"] == "matrix(1, 0, 0, 1, 0, -2)"
            _r("kpi-group:hover translateY(-2px)", ok,
                f"transform = {transform['t'] if transform else 'None'}",
                extra=f"matches(:hover)={transform['hov'] if transform else '?'} dist={transform['dist'] if transform else '?'}")
        else:
            _r("kpi-group:hover", False, "no visible .kpi-group in #as-kpis")

        # ── 截图 2: zoomed 前 3 行 (列分隔线 + hover) ──
        await page.evaluate("""() => {
            const wrap = document.querySelector('.view-all_stocks .stocks-table');
            if (wrap) wrap.scrollIntoView({block:'start'});
        }""")
        await page.wait_for_timeout(400)
        # re-hover row 1 for visible state in screenshot
        tr_box2 = await page.evaluate("""() => {
            const tr = document.querySelector('.view-all_stocks .stocks-table tbody tr:first-child');
            if (!tr) return null;
            const r = tr.getBoundingClientRect();
            return { x: r.x, y: r.y, w: r.width, h: r.height };
        }""")
        if tr_box2 and tr_box2["w"] > 0:
            await page.mouse.move(tr_box2["x"] + tr_box2["w"]/2,
                                  tr_box2["y"] + tr_box2["h"]/2)
            await page.wait_for_timeout(250)
        await page.screenshot(path=f"{ART}/02_zoom_rows_hover.png", full_page=False)
        print(f"\n[shot] {ART}/02_zoom_rows_hover.png")

        await browser.close()

    # summary
    print(f"\n{'='*50}\n  R11-20 POLISH VERIFICATION SUMMARY\n{'='*50}")
    passed = sum(1 for r in results if r["ok"])
    print(f"  {passed}/{len(results)} PASS")
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['item']}")

    import json
    with open(f"{ART}/results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nsaved to {ART}/results.json")


asyncio.run(main())
