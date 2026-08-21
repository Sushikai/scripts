"""
个股页面全接口调试测试脚本
测试每个 endpoint: 状态码 / 关键字段完整性 / 数据类型 / 错误处理
用法: python3 test_stock_endpoints.py <code> [date]
"""
import sys, json, time, os, traceback
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

BASE = os.environ.get("TEST_BASE", "http://127.0.0.1:7799")
CODE = sys.argv[1] if len(sys.argv) > 1 else "300308"
DATE = sys.argv[2] if len(sys.argv) > 2 else "2026-07-22"

TOTAL = 0
PASS = 0
FAIL = 0
SKIP = []

def _fetch(url, timeout=10):
    r = Request(url)
    return urlopen(r, timeout=timeout)

def test(name, url, check_fn=None, timeout=10):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    start = time.time()
    try:
        resp = _fetch(url, timeout)
        elapsed = time.time() - start
        body = resp.read().decode()
        data = json.loads(body)
        status = "OK" if resp.status == 200 else f"HTTP{resp.status}"

        # Check for envelope
        payload = data.get("data") if "data" in data else data
        ok_flag = data.get("ok", True)

        issues = []
        if resp.status != 200:
            issues.append(f"非200状态码: {resp.status}")
        if not ok_flag and resp.status == 200:
            issues.append(f"ok=false: {data.get('error', 'unknown')}")

        if check_fn:
            try:
                check_fn(payload, data)
            except AssertionError as e:
                issues.append(str(e))
            except Exception as e:
                issues.append(f"check异常: {e}")

        if issues:
            FAIL += 1
            print(f"  FAIL [{elapsed*1000:.0f}ms] {name}")
            print(f"       url={url}")
            for iss in issues:
                print(f"       ISSUE: {iss}")
        else:
            PASS += 1
            print(f"  PASS [{elapsed*1000:.0f}ms] {name}")
    except HTTPError as e:
        FAIL += 1
        body = e.read().decode()[:200]
        elapsed = time.time() - start
        print(f"  FAIL [{elapsed*1000:.0f}ms] {name}")
        print(f"       url={url}")
        print(f"       HTTP {e.code}: {body}")
    except URLError as e:
        FAIL += 1
        elapsed = time.time() - start
        print(f"  FAIL [{elapsed*1000:.0f}ms] {name}")
        print(f"       url={url}")
        print(f"       网络错误: {e.reason}")
    except json.JSONDecodeError as e:
        FAIL += 1
        elapsed = time.time() - start
        print(f"  FAIL [{elapsed*1000:.0f}ms] {name}")
        print(f"       url={url}")
        print(f"       JSON解析失败: {e}")
    except Exception as e:
        FAIL += 1
        elapsed = time.time() - start
        print(f"  FAIL [{elapsed*1000:.0f}ms] {name}")
        print(f"       url={url}")
        print(f"       异常: {traceback.format_exc()[:200]}")
    return data if 'data' in locals() and not isinstance(data, BaseException) else None


def check_has_fields(*fields):
    def _check(payload, raw):
        if isinstance(payload, dict):
            for f in fields:
                if f not in payload:
                    raise AssertionError(f"缺少字段 '{f}'")
    return _check


def check_quote_fields(*fields):
    """check fields in quote sub-object"""
    def _check(payload, raw):
        q = payload.get("quote") if isinstance(payload, dict) else {}
        if not q:
            raise AssertionError("quote对象为空/缺失")
        for f in fields:
            v = q.get(f)
            if v is None:
                raise AssertionError(f"quote.{f} 为 None")
    return _check


def check_not_empty_list(field):
    def _check(payload, raw):
        items = payload.get(field) if isinstance(payload, dict) else []
        if items is None:
            raise AssertionError(f"'{field}' 为 None")
    return _check


print(f"━" * 60)
print(f"个股页全接口调试 — {CODE} ({DATE})")
print(f"BASE: {BASE}")
print(f"━" * 60)
print()

# ─── 1. 股票搜索 ───
print("─ [1] 搜索 ─")
def _check_search(payload, raw):
    results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        raise AssertionError("results 不是 list")
    if len(results) == 0:
        raise AssertionError(f"搜索 '{CODE}' 无结果 (股票列表不全?)")
test("stock/search",
     f"{BASE}/api/stock/search?q={CODE}",
     _check_search)

# ─── 2. 核心行情 ───
print("\n─ [2] 行情 ─")
test("stock/core",
     f"{BASE}/api/stock/{CODE}/core",
     check_quote_fields("最新价", "涨跌幅", "name"),
     timeout=8)

# ─── 3. 完整聚合 ───
print("\n─ [3] 完整聚合 ─")
test("stock/full (today)",
     f"{BASE}/api/stock/{CODE}/full",
     check_quote_fields("最新价", "涨跌幅", "市盈率", "总市值", "换手率", "name"),
     timeout=12)

test("stock/full (historical)",
     f"{BASE}/api/stock/{CODE}/full?date={DATE}",
     check_quote_fields("最新价", "涨跌幅"),
     timeout=12)

# ─── 4. 分时图 ───
print("\n─ [4] 分时图 ─")
def _check_intraday(payload, raw):
    if isinstance(payload, dict):
        # ticks 可能在非交易时段为空,但字段必须存在
        if payload.get("code") != CODE:
            raise AssertionError(f"code mismatch")
test("intraday (today)",
     f"{BASE}/api/stock/{CODE}/intraday?date={DATE}",
     _check_intraday,
     timeout=8)

test("intraday (prev day)",
     f"{BASE}/api/stock/{CODE}/intraday?date=2026-07-21",
     _check_intraday,
     timeout=8)

def _check_intraday_5d(payload, raw):
    if not isinstance(payload, dict):
        raise AssertionError(f"payload 不是 dict (got {type(payload).__name__} = {payload})")
    if payload.get("code") != CODE:
        raise AssertionError("code mismatch")
test("intraday_5d",
     f"{BASE}/api/stock/{CODE}/intraday_5d",
     _check_intraday_5d,
     timeout=8)

# ─── 5. K线 ───
print("\n─ [5] K线 ─")
def _check_kline(payload, raw):
    kline = payload.get("kline") if isinstance(payload, dict) else None
    if kline is None:
        raise AssertionError("kline 字段为 None")
    if not isinstance(kline, list):
        raise AssertionError(f"kline 不是 list (got {type(kline).__name__})")
test("kline (120d)",
     f"{BASE}/api/stock/{CODE}/kline?days=120",
     _check_kline,
     timeout=8)

# ─── 6. 资金流向 ───
print("\n─ [6] 资金流向 ─")
test("fund_flow (60d)",
     f"{BASE}/api/stock/{CODE}/fund_flow?days=60",
     check_has_fields("code"),
     timeout=8)

# ─── 7. 游资席位 ───
print("\n─ [7] 游资席位 ─")
test("seats",
     f"{BASE}/api/stock/{CODE}/seats",
     check_has_fields("code"),
     timeout=8)

test("seat_breakdown",
     f"{BASE}/api/stock/{CODE}/seat_breakdown",
     check_has_fields("code"),
     timeout=8)

# ─── 8. 连板/涨停关联 ───
print("\n─ [8] 连板/涨停 ─")
test("limit_up_context",
     f"{BASE}/api/stock/{CODE}/limit_up_context",
     check_has_fields("code"),
     timeout=8)

test("strong_stocks",
     f"{BASE}/api/stock/{CODE}/strong_stocks",
     check_not_empty_list("rows"),
     timeout=8)

# ─── 9. 新闻 ───
print("\n─ [9] 新闻 ─")
test("related_news",
     f"{BASE}/api/stock/{CODE}/related_news",
     timeout=8)

# ─── 10. 角色/策略 ───
print("\n─ [10] 角色/策略 ─")
test("role",
     f"{BASE}/api/stock/{CODE}/role",
     check_has_fields("code"),
     timeout=8)

test("strategy_match",
     f"{BASE}/api/stock/{CODE}/strategy_match",
     timeout=10)

# ─── 11. 交易日历 ───
print("\n─ [11] 交易日历 ─")
test("trade_dates",
     f"{BASE}/api/trade_dates?limit=60",
     timeout=8)

# ─── 12. 板块 ───
print("\n─ [12] 板块 ─")
test("sectors/sw",
     f"{BASE}/api/sectors/sw",
     timeout=8)

stock_sector_url = f"{BASE}/api/stock/{CODE}/sector"
test(f"sector ({CODE})",
     stock_sector_url,
     check_has_fields("code"),
     timeout=8)

# ─── 13. 大盘信号 ───
print("\n─ [13] 大盘/全局 ─")
test("dashboard/signal",
     f"{BASE}/api/dashboard/signal",
     timeout=30)

# ─── 14. SSE stream (仅测连接, 不测断流) ───
print("\n─ [14] SSE stream ─")
try:
    # SSE 永不关闭 — 只读第一帧 (最多等 5s)
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", 7799))
    sock.sendall(f"GET /api/stock/{CODE}/stream HTTP/1.1\r\nHost: 127.0.0.1:7799\r\nConnection: close\r\n\r\n".encode())
    first_chunk = b""
    deadline = time.time() + 4
    while time.time() < deadline and len(first_chunk) < 4096:
        try:
            d = sock.recv(4096)
            if not d:
                break
            first_chunk += d
            if b"event:" in first_chunk and b"data:" in first_chunk:
                break
        except socket.timeout:
            break
    sock.close()
    if b"event:" in first_chunk and b"data:" in first_chunk:
        print(f"  PASS stream — SSE 首帧已就绪")
        PASS += 1
    else:
        print(f"  FAIL stream — 首帧无SSE事件, raw={first_chunk[:100]}")
        FAIL += 1
    TOTAL += 1
except Exception as e:
    print(f"  FAIL stream — {e}")
    FAIL += 1
    TOTAL += 1

# ─── 汇总 ───
print()
print(f"━" * 60)
print(f"结果: {PASS}/{TOTAL} 通过, {FAIL} 失败")
if SKIP:
    print(f"跳过: {len(SKIP)}")
print(f"━" * 60)

# 退出码
sys.exit(0 if FAIL == 0 else 1)
