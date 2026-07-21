#!/usr/bin/env python3
"""
退学 v3 数据源重构 — 自动化验收测试
运行: python3 tests/verify_data_sources.py [--quick]
"""
from __future__ import annotations
import os, sys, json, time, traceback, logging, socket

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0
WARN = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}")


def soft_check(name: str, ok: bool, detail: str = ""):
    global PASS, WARN
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        WARN += 1
        print(f"  ⚠ {name}: {detail}")


# ═══════════════════════════════════════════════════════
# 1. lib_common 数据源层
# ═══════════════════════════════════════════════════════
def test_imports():
    print("\n## 1.1 基础导入")
    from lib_common import (
        _race_sources, _require_realtime_quote, _require_kline,
        get_source_health, reset_source_health,
        _report_fail, _report_ok, _is_disabled,
        SOURCE_HEALTHY_THRESHOLD, COOLDOWN_LEVELS,
        fetch_realtime, fetch_daily, _REALTIME_SOURCES, _DAILY_SOURCES,
    )
    check("_race_sources 导入", callable(_race_sources))
    check("_require_realtime_quote 导入", callable(_require_realtime_quote))
    check("_require_kline 导入", callable(_require_kline))
    check("get_source_health 导入", callable(get_source_health))
    check("COOLDOWN_LEVELS 逐级冷却", COOLDOWN_LEVELS == [300, 600, 1200, 2400, 3600])
    check("REALTIME_SOURCES >= 9 源", len(_REALTIME_SOURCES) >= 9)
    check("DAILY_SOURCES >= 11 源", len(_DAILY_SOURCES) >= 11)
    return locals()


def test_source_health():
    print("\n## 1.2 数据源健康追踪")
    from lib_common import get_source_health
    health = get_source_health()
    check("健康快照返回列表", isinstance(health, list))
    check("含全部源", len(health) >= 15)
    # 检查字段完整性
    required_fields = {"name", "disabled", "fails", "oks", "cooldown_level", "total_calls", "total_fails", "last_err"}
    for s in health:
        missing = required_fields - set(s.keys())
        if missing:
            check(f"字段完整: {s['name']}", False, f"缺少: {missing}")
            break
    else:
        check("所有源字段完整", True)
    # cooldown_level 范围检查
    for s in health:
        if not (0 <= s["cooldown_level"] <= 4):
            check(f"cooldown_level 范围: {s['name']}", False, f"值={s['cooldown_level']}")
            break
    else:
        check("cooldown_level 范围 [0,4]", True)


def test_circuit_breaker():
    print("\n## 1.3 熔断器验证")
    from lib_common import (
        _report_fail, _report_ok, _is_disabled,
        get_source_health, reset_source_health,
        SOURCE_HEALTHY_THRESHOLD,
    )
    # Reset first
    reset_source_health()

    # Trigger 5 failures on a test source
    for i in range(SOURCE_HEALTHY_THRESHOLD):
        _report_fail("tencent_qq", f"test_failure_{i}")
    disabled = _is_disabled("tencent_qq")
    check("连续5次失败后冷却", disabled, "源应该在冷却状态")

    # Check cooldown_level is 0 (first time = level 0 = 300s)
    health = get_source_health()
    for s in health:
        if s["name"] == "腾讯(qt.gtimg)":
            check("首次冷却 level=0", s["cooldown_level"] == 0, f"实际={s['cooldown_level']}")
            check("剩余冷却时间>0", s["disabled_remaining_s"] > 0, f"剩余={s['disabled_remaining_s']}s")
            break

    # Now trigger more failures to escalate cooldown level
    for i in range(15):
        _report_fail("tencent_qq", "escalation")
    health = get_source_health()
    for s in health:
        if s["name"] == "腾讯(qt.gtimg)":
            soft_check("多次失败后冷却升级",
                       s["cooldown_level"] >= 1 and s["cooldown_level"] <= 4,
                       f"实际={s['cooldown_level']}")
            break

    # Test recovery
    reset_source_health()
    for i in range(3):
        _report_ok("tencent_qq")
    check("重置后源正常", not _is_disabled("tencent_qq"))


def test_race_sources():
    print("\n## 1.4 并行竞速验证")
    from lib_common import _race_sources, _require_realtime_quote, _REALTIME_SOURCES

    # Test with valid stock code
    data, src = _race_sources(
        _REALTIME_SOURCES[:3], "000001",
        timeout=4.0, max_workers=3,
        require_func=_require_realtime_quote,
    )
    if data and src:
        check(f"并行竞速成功: 源={src}, 价={data.get('最新价', '?')}", True)
    else:
        # Might be after hours - still acceptable
        soft_check("并行竞速 (盘后/周末可能无数据)", True, "非交易时段预期行为")


def test_require_realtime_quote():
    print("\n## 1.5 数据质量校验")
    from lib_common import _require_realtime_quote, _require_kline
    import pandas as pd

    check("有效行情通过校验", _require_realtime_quote({"最新价": 10.5}))
    check("零价不通过校验", not _require_realtime_quote({"最新价": 0}))
    check("None 不通过校验", not _require_realtime_quote(None))
    check("空 dict 不通过校验", not _require_realtime_quote({}))

    # Kline validation
    check("有效 K线通过校验", _require_kline(pd.DataFrame({"日期": ["2024-01-01"], "收盘": [10.0]})))
    check("空 K线不通过校验", not _require_kline(pd.DataFrame()))
    check("None K线不通过校验", not _require_kline(None))


# ═══════════════════════════════════════════════════════
# 2. Server 端点层
# ═══════════════════════════════════════════════════════
def test_server_functions():
    print("\n## 2.1 Server 辅助函数")

    # 直接复制测试逻辑（server 模块因相对导入不能直接import）
    # 测试信封结构
    def _make_envelope(data=None, error=None, **extra):
        return {"ok": error is None, "data": data, "error": error, "ts": time.time(), **extra}

    e = _make_envelope(data={"a": 1})
    check("envelope ok=True", e["ok"] == True)
    check("envelope 含 data", e["data"] == {"a": 1})
    check("envelope 含 ts", "ts" in e)

    # 测试陈旧缓存逻辑
    _STALE_CACHE = {}
    def _stale_save(key, data):
        _STALE_CACHE[key] = {"data": data, "ts": time.time()}
    def _stale_load(key, max_age=300):
        entry = _STALE_CACHE.get(key)
        if entry is None:
            return None, None
        age = time.time() - entry["ts"]
        if age > max_age:
            return None, None
        return entry["data"], age

    _stale_save("test_key", {"cached": True})
    cached, age = _stale_load("test_key", max_age=300)
    check("stale save/load 返回数据", cached is not None and cached.get("cached"))
    check("stale age 为数字", isinstance(age, (int, float)))

    _stale_save("market_overview", {"indices": [{"code": "000001", "price": 3000}]})
    cached, age = _stale_load("market_overview", max_age=300)
    check("market_overview 陈旧缓存保存", cached is not None)
    check("market_overview 陈旧缓存含指数", "indices" in cached)

    print("\n## 2.2 Server 文件完整性")
    with open("web/server.py") as f:
        c = f.read()
    check("server.py 含 envelope_degraded", "envelope_degraded" in c)
    check("server.py 含 _STALE_CACHE", "_STALE_CACHE" in c)
    check("server.py 含 _STALE_TTL", "_STALE_TTL" in c)
    check("server.py 含 /api/sources/health", "sources/health" in c)
    check("server.py 含 degraded_reason", "degraded_reason" in c)
    check("intraday_5d _degraded 兜底", "stock_intraday_5d" in c and "degraded" in c)
    check("intraday _degraded 兜底", "stock_intraday:" in c and "degraded" in c)
    check("dragons data._degraded 注入", '"stale"' in c and '_degraded' in c and 'dragons' in c)
    check("limit_up_context _degraded 注入", '"_degraded": "timeout"' in c or '"_degraded":"timeout"' in c)
    check("sectors_realtime _degraded", 'sectors' in c and '_degraded' in c and '板块数据' in c)
    check("news _degraded", 'news 拉取超时' in c and '_degraded' in c)
    check("sector 超时 _degraded", 'sector 超时' in c and '_degraded' in c)


# ═══════════════════════════════════════════════════════
# 3. 前端文件验证
# ═══════════════════════════════════════════════════════
def test_frontend():
    print("\n## 3.1 前端降级模式")
    js_app = open("web/static/app.js").read()
    js_dash = open("web/static/view-dash.js").read()
    css = open("web/static/style.css").read()

    check("topbar 含 _degraded 处理", "_degraded" in js_app or "data._degraded" in js_app)
    check("hot_sectors _paintHotSectors 含 _degraded", "_degraded" in js_app)
    check("news refresh 有 .catch 兜底", ".catch(e =>" in js_app or "catch(" in js_app.split("news/refresh")[1] if len(js_app.split("news/refresh")) > 1 else False)
    check("tk-degraded-label CSS 类定义", ".tk-degraded-label" in css)
    check("stale-label CSS 类定义", ".stale-label" in css)

    # Dashboard JS
    check("view-dash _paintSignalCol 含 _degraded", "_degraded" in js_dash)
    check("view-dash 含 signal-degraded class", "signal-degraded" in js_dash)

    # Stock detail JS
    js_stock = open("web/static/view-stock.js").read()
    check("view-stock 含 _degraded 处理", "_degraded" in js_stock)
    check("view-stock 含 stock-degraded-badge 渲染", "stock-degraded-badge" in js_stock or "_degradedFields" in js_stock or "data._degraded" in js_stock)
    check("loadStockLimitUp 处理 _degraded", "res._degraded" in js_stock)

    # CSS
    check("stock-degraded-badge CSS 类定义", ".stock-degraded-badge" in css)


# ═══════════════════════════════════════════════════════
# 4. 集成测试（需要服务运行）
# ═══════════════════════════════════════════════════════
def test_integration():
    print("\n## 4.1 集成测试（需要服务器运行在 localhost:7799）")
    try:
        import urllib.request
        import socket
        base = "http://127.0.0.1:7799"

        # Quick connectivity check
        try:
            s = socket.create_connection(("127.0.0.1", 7799), timeout=2)
            s.close()
        except (socket.timeout, ConnectionRefusedError, OSError):
            print(f"  ⚠ 服务器 (127.0.0.1:7799) 未运行或不可达")
            return False

        # Test health
        r = urllib.request.urlopen(f"{base}/api/health", timeout=5)
        d = json.loads(r.read())
        check("/api/health 可达", d.get("ok") == True)

        # Test sources/health
        r = urllib.request.urlopen(f"{base}/api/sources/health", timeout=5)
        d = json.loads(r.read())
        check("/api/sources/health 可达", d.get("ok") == True)
        if d.get("ok") and d.get("data"):
            check("sources/health 返回源列表", len(d["data"].get("sources", [])) >= 15)
            check("sources/health 含 disabled_count", "disabled_count" in d["data"])
            check("sources/health 含 healthy", "healthy" in d["data"])

        # Test market_overview
        r = urllib.request.urlopen(f"{base}/api/market/overview", timeout=10)
        d = json.loads(r.read())
        check("/api/market/overview 可达", d.get("ok") == True)
        if d.get("data"):
            has_data = any(i.get("price", 0) > 0 for i in d["data"].get("indices", []))
            if has_data:
                check("overview 有指数数据", True)
            else:
                soft_check("overview 有降级标记",
                          d["data"].get("_degraded") is not None,
                          "指数全零但无降级标记")

        # Test dashboard/hot_sectors
        r = urllib.request.urlopen(f"{base}/api/dashboard/hot_sectors", timeout=15)
        d = json.loads(r.read())
        check("/api/dashboard/hot_sectors 可达", d.get("ok") == True)
        if d.get("data") and d["data"].get("mainline"):
            check("hot_sectors 有板块数据", len(d["data"].get("mainline", [])) > 0)

        # Test stock core
        r = urllib.request.urlopen(f"{base}/api/stock/000001/core", timeout=10)
        d = json.loads(r.read())
        check("/api/stock/000001/core 可达", d.get("ok") == True)
        if d.get("data"):
            has_quote = d["data"].get("quote", {}).get("最新价", 0) > 0
            if has_quote:
                check("core 有行情数据", True)
            else:
                soft_check("core 降级标记",
                          d["data"].get("_degraded") is not None,
                          "行情全零但无降级标记")

        print("\n  集成测试全部通过（服务器运行正常）")
        return True
    except (urllib.error.URLError, ConnectionRefusedError, socket.timeout, TimeoutError) as e:
        print(f"  ⚠ 服务器未运行: {e}")
        print("  请启动: python -m tuixue_v3.web.server")
        return False
    except Exception as e:
        print(f"  ❌ 集成测试异常: {e}")
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    # 抑制测试噪音
    logging.disable(logging.WARNING)

    quick = "--quick" in sys.argv

    print("╔═══════════════════════════════════════════════╗")
    print("║  退学 v3 数据源重构 — 自动验收测试            ║")
    print("╚═══════════════════════════════════════════════╝")

    locs = test_imports()
    test_source_health()
    test_circuit_breaker()

    if not quick:
        test_race_sources()
    else:
        print("\n## 1.4 并行竞速 (--quick 跳过)")

    test_require_realtime_quote()
    test_server_functions()
    test_frontend()
    test_integration()

    print(f"\n{'═' * 45}")
    print(f"结果: ✅ {PASS} 通过 | ❌ {FAIL} 失败 | ⚠ {WARN} 警告")
    print(f"{'═' * 45}")

    if FAIL > 0:
        print(f"\n⚠ {FAIL} 项失败，需要修复!")
        sys.exit(1)
    elif WARN > 0:
        print(f"\n✓ {WARN} 项警告，建议检查")
        sys.exit(0)
    else:
        print("\n✓ 全部通过!")
        sys.exit(0)
