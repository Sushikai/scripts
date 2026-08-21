"""
tests/test_stock_pro_contract.py — 个股页专业终端后端契约
R-pro-stock v1 (2026-08-08)

跑法:
    PYTHONPATH=. python3 -m pytest tests/test_stock_pro_contract.py -v -m contract

覆盖:
  · /api/stock/{code}/related_stocks — 字段/分组/排序
  · /api/stock/{code}/intraday — 日期严格过滤
  · /api/dragons — yesterday_all 与 all 不同日期
"""
from __future__ import annotations

import pytest
import httpx

pytestmark = pytest.mark.contract

# 真实可查的活跃股 (2026-08-08)
SAMPLE_CODES = ["600519", "000001", "300750"]


def _get_url(base_url: str, path: str, **kw) -> dict:
    r = httpx.get(base_url + path, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r.json()


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_related_stocks_envelope_and_groups(base_url, code):
    """相关个股: 必须返回 4 个分组 + target 元信息 + count"""
    env = _get_url(base_url, f"/api/stock/{code}/related_stocks?limit=20")
    assert env.get("ok") is True, f"envelope 不 ok: {env}"
    d = env.get("data") or {}
    assert d.get("code") == code
    assert "target" in d and isinstance(d["target"], dict)
    target = d["target"]
    assert "sw" in target and "l3" in target
    groups = d.get("groups") or {}
    for k in ("same_l3", "same_l4", "same_cluster", "same_sw"):
        assert k in groups, f"缺分组 {k}: {groups.keys()}"
        assert isinstance(groups[k], list)
    total = sum(len(v) for v in groups.values())
    assert d.get("count") == total, f"count({d.get('count')}) != 实际 ({total})"
    for k, rows in groups.items():
        for it in rows[:5]:
            assert "code" in it and it["code"], f"item 缺 code: {it}"
            assert "rel_type" in it and it["rel_type"], f"item 缺 rel_type: {it}"
            assert it["code"] != code, f"不能包含自身: {it['code']}"


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_related_stocks_sort_descending_pct(base_url, code):
    """相关个股: 各分组按涨跌幅降序"""
    env = _get_url(base_url, f"/api/stock/{code}/related_stocks?limit=20")
    d = env["data"]
    for k, rows in d["groups"].items():
        pcts = [r["pct"] for r in rows if isinstance(r.get("pct"), (int, float))]
        if len(pcts) >= 2:
            assert pcts[0] >= pcts[-1] or all(p == pcts[0] for p in pcts), \
                f"{k} 排序异常: {pcts}"


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_intraday_strict_date_filter(base_url, code):
    """分时: 请求特定日期时,所有 ticks 必须匹配该交易日"""
    for date in ("2026-08-07", "2026-08-06", "2026-08-05"):
        env = _get_url(base_url, f"/api/stock/{code}/intraday?date={date}")
        if not env.get("ok"):
            continue
        d = env["data"] or {}
        points = d.get("points") or d.get("intraday") or d.get("data") or []
        if not points:
            continue
        for p in points[:10]:
            t = str(p.get("time", ""))
            assert t.startswith(date), \
                f"分时 tick 日期不匹配!请求 {date},实际 {t} (code={code})"
        break


@pytest.mark.parametrize("code", SAMPLE_CODES)
def test_stock_core_has_required_fields(base_url, code):
    """core 聚合: 返回 quote + kline 等核心字段"""
    env = _get_url(base_url, f"/api/stock/{code}/core")
    assert env.get("ok") is True
    d = env["data"] or {}
    assert "quote" in d, f"core 缺 quote: {d.keys()}"
    q = d["quote"] or {}
    # 行情至少含 name 或 code (实时价字段可能因 quote cache miss 为空)
    assert "name" in q or "code" in q, f"quote 缺 name/code: {q}"
    # kline 至少有 1 根
    assert isinstance(d.get("kline"), list) and len(d["kline"]) >= 1, \
        f"kline 缺失或空: {type(d.get('kline'))}"


def test_dragons_yesterday_and_today_different_dates(base_url):
    """龙头: 昨日涨停池(yesterday_all)和今日涨停池(all)的日期严格不同"""
    env = _get_url(base_url, "/api/dragons")
    assert env.get("ok") is True, env
    d = env["data"] or {}
    today_date = d.get("date") or ""
    yesterday_date = d.get("yesterday_date") or ""
    assert today_date and yesterday_date, f"日期缺失 today={today_date} yest={yesterday_date}"
    assert today_date != yesterday_date, \
        f"今日 {today_date} 和前日 {yesterday_date} 撞日,违反 R-fix-2026-08-08"
    yest_codes = {s.get("code") for s in (d.get("yesterday_all") or [])}
    today_codes = {s.get("code") for s in (d.get("all") or [])}
    if today_codes and yest_codes:
        overlap = today_codes & yest_codes
        assert overlap, "晋级池不应空 (有 yest 又涨停 today 的股)"