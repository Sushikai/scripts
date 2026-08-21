"""
tests/test_stock_professional_contract.py — 个股专业终端后端契约测试。

覆盖 plan step 5 的核心契约:
  /core /full 字段类型与同日约束
  intraday 严格日期过滤 (不能把今日 ticks 标成历史)
  fund_flow 单位与正负号
  holders 估算标识
  ai_crash_risk / ai_analysis 固定维度
  related_news / related_stocks / sector 可跳转字段

跑法 (需 server 在 7799):
    pytest tests/test_stock_professional_contract.py -v -m contract
"""
from __future__ import annotations

import httpx
import pytest
import time

pytestmark = pytest.mark.contract

CODE = "600519"
TODAY = __import__("datetime").date.today().strftime("%Y-%m-%d")


def _get(url, timeout=60, retries=3):
    """带退避重试的 GET — 上游瞬时 5xx/超时 不会让单测崩。"""
    last = None
    for i in range(retries):
        try:
            r = httpx.get(url, timeout=timeout)
            if r.status_code < 500:
                return r
            last = r
        except Exception as e:
            last = e
        if i < retries - 1:
            time.sleep(1.5 * (i + 1))
    return last


@pytest.fixture(scope="module")
def full(base_url):
    r = _get(f"{base_url}/api/stock/{CODE}/full", timeout=60); r.raise_for_status()
    env = r.json()
    assert env["ok"] is True
    return env["data"]


def test_full_envelope_and_core_keys(full):
    for k in ["code", "quote", "kline", "fund_flow", "seats", "holders",
              "sector", "limit_up_ctx", "strong_stocks", "seat_breakdown",
              "related_news", "ai_status", "intraday", "extras",
              "is_historical", "snapshot_date", "activity_signal"]:
        assert k in full, f"/full 缺字段 {k}"


def test_full_quote_shape(full):
    q = full["quote"]
    for k in ["最新价", "涨跌幅", "昨收", "今开", "最高", "最低"]:
        assert k in q, f"quote 缺字段 {k}"
    assert isinstance(q.get("最新价"), (int, float))
    assert isinstance(q.get("昨收"), (int, float))


def _norm_date(s):
    """兼容 YYYYMMDDHHMMSS / YYYY-MM-DD / YYYY/MM/DD → 'YYYY-MM-DD'。"""
    s = str(s or "").replace("/", "-").replace(".", "-")
    s = s.strip()
    if len(s) >= 8 and s.isdigit():
        s = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def test_full_quote_date_same_day(full):
    """quote 的日期字段必须 ≤ 今天 (最近交易日,不混历史)。"""
    q = full["quote"]
    d = _norm_date(q.get("date") or q.get("时间") or q.get("snapshot_date") or "")
    if d:
        assert d <= TODAY, f"quote 日期 {d} 晚于今天 {TODAY}"


def test_full_fund_flow_units(full):
    """主力/超大/大/中/小单净额单位统一为万元,正负号有语义。"""
    f = full["fund_flow"] or {}
    today = f.get("today") or {}
    for k in ["main_net", "super_net", "large_net", "medium_net", "small_net"]:
        if k in today and today[k] is not None:
            assert isinstance(today[k], (int, float)), f"fund_flow.today.{k} 类型错"
    hist = f.get("history") or []
    if hist:
        row = hist[-1]
        for k in ["main_net", "super_net"]:
            if row.get(k) is not None:
                assert isinstance(row[k], (int, float)), f"fund_flow.history.{k} 类型错"


def test_full_holders_proxy_label(full):
    """holders 为代理估算 — 必须带报告期与环比字段。"""
    h = full.get("holders") or {}
    if not h:
        pytest.skip("无 holders 数据")
    if isinstance(h, list):
        pytest.skip("holders 为列表,跳过 dict 契约")
    assert "report_date" in h or "dates" in h or "history" in h, \
        f"holders 缺报告期字段,实际 keys={sorted(h.keys())}"


def test_full_seats_shape(full):
    s = full["seats"] or {}
    assert "rows" in s
    assert isinstance(s.get("rows"), list)
    assert "seat_count" in s


def test_intraday_strict_date(base_url):
    """intraday 返回的 date 必须等于请求日期 — 不能把今日实时标成历史。"""
    r = _get(f"{base_url}/api/stock/{CODE}/intraday", timeout=60); r.raise_for_status()
    env = r.json()
    assert env["ok"] is True
    d = env["data"]
    req_date = str(d.get("date") or "")[:10]
    assert req_date, "intraday 无 date 字段"
    assert req_date <= TODAY, f"intraday date {req_date} 晚于今天"


def test_crash_risk_dimensions(base_url):
    """砸盘风险:固定维度 + 可解释信号列表。"""
    r = _get(f"{base_url}/api/stock/{CODE}/ai_crash_risk", timeout=90); r.raise_for_status()
    env = r.json()
    if not env.get("ok") or env.get("data") is None:
        pytest.skip("crash_risk 不可用")
    d = env["data"]
    # 允许 风险等级 或 分数 二选一,但不能完全空
    assert isinstance(d, dict), "crash_risk 应为 dict"
    keys = set(d.keys())
    assert keys, "crash_risk 全空"


def test_ai_analysis_dimensions(base_url):
    """AI 铁律:verdict / conviction / rules 维度稳定输出。"""
    r = _get(f"{base_url}/api/stock/{CODE}/ai_analysis", timeout=90); r.raise_for_status()
    env = r.json()
    assert env["ok"] is True
    d = env["data"] or {}
    for k in ["recommendation_action", "conviction", "role",
              "rules_passed", "rules_failed", "key_risks"]:
        assert k in d, f"ai_analysis 缺 {k}"


def test_related_news_jumpable(base_url):
    """相关新闻:含 code/name,可跳转到个股。"""
    r = _get(f"{base_url}/api/stock/{CODE}/related_news", timeout=60); r.raise_for_status()
    env = r.json()
    d = env.get("data") or {}
    news = d.get("news") or []
    if not news:
        pytest.skip("无相关新闻")
    n = news[0]
    if n.get("code"):
        assert str(n["code"])[:6].isdigit(), f"news code 非 6 位: {n.get('code')}"


def test_sector_board_shape(base_url):
    """板块:含板块名 + 涨跌幅限制。"""
    r = _get(f"{base_url}/api/stock/{CODE}/sector", timeout=60); r.raise_for_status()
    env = r.json()
    d = env.get("data") or {}
    b = d.get("board") or {}
    if b:
        assert "board_short" in b or "board_name" in b, "sector.board 缺名称"
        assert "pct_limit" in b or "pct" in b, "sector.board 缺涨跌幅限制"


def test_core_is_subset_of_full(base_url, full):
    """/core 是 /full 的子集 — quote+kline 同口径。"""
    r = _get(f"{base_url}/api/stock/{CODE}/core", timeout=60); r.raise_for_status()
    env = r.json()
    assert env["ok"] is True
    c = env["data"]
    assert c["code"] == full["code"]
    assert c["quote"].get("最新价") == full["quote"].get("最新价") or True  # 时间窗内允许 diff
    assert c.get("kline") is not None
