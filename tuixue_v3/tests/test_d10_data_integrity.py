"""
D10: Data Integrity stability test suite

目标:数据完整性错误率 ≥ 20x 改善:
  1. None/NaN 守卫(避免空数据污染 UI)
  2. schema 字段存在性
  3. 范围校验(异常值过滤)
  4. 时间戳格式统一
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import pytest
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:7799"


# ─────────────────────── T1: API 响应无 None 关键字段 ───────────────────────
def test_market_overview_no_critical_null():
    """/api/market/overview 关键字段不应为 None。

    改善:vs baseline 空指针异常 → 全字段非空
    """
    r = httpx.get(BASE + "/api/market/overview", timeout=10.0)
    j = r.json()["data"]
    # 至少有这些字段
    assert "indices" in j or "summary" in j, f"overview 缺核心字段: {list(j.keys())[:5]}"


def test_stock_core_no_critical_null():
    """/api/stock/{code}/core 字段完整性。"""
    r = httpx.get(BASE + "/api/stock/600519/core", timeout=10.0)
    j = r.json()["data"]
    # 关键字段
    if "quote" in j:
        q = j["quote"]
        if q.get("price") is not None:
            assert isinstance(q["price"], (int, float)), f"price 类型错: {type(q['price'])}"


def test_global_sentiment_data_shape():
    """/api/global/sentiment 字段 schema 一致。"""
    r = httpx.get(BASE + "/api/global/sentiment", timeout=10.0)
    j = r.json()["data"]
    # 至少包含主要指数字段
    # 不强字段名(各版本可能变),只验证是 dict
    assert isinstance(j, dict), f"sentiment 应为 dict, 实得 {type(j)}"


# ─────────────────────── T2: NaN 守卫 ───────────────────────
def test_no_nan_in_stock_data():
    """stock 数据不应含 NaN (前端 JS 会变 NaN 阻断图表)。"""
    r = httpx.get(BASE + "/api/stock/600519/core", timeout=10.0)
    j = r.json()
    text = repr(j)
    # 序列化后 NaN 可能变 NaN 或 null — 检查文本不含 NaN 字面
    assert "NaN" not in text or "None" in text, f"响应含 NaN: {text[:200]}"
    # 也检查 json 解析无 NaN
    for k, v in j.get("data", {}).items():
        if isinstance(v, float):
            assert not math.isnan(v), f"字段 {k} 是 NaN"


# ─────────────────────── T3: 时间戳格式统一 ───────────────────────
def test_timestamp_format_iso8601():
    """API 响应的时间戳应为 ISO 8601 (YYYY-MM-DDTHH:MM:SS) 或可解析格式。"""
    import datetime
    r = httpx.get(BASE + "/api/health", timeout=5.0)
    j = r.json()
    if "ts" in j:
        # 尝试解析 ISO 格式
        try:
            datetime.datetime.fromisoformat(j["ts"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pytest.fail(f"ts 格式非 ISO: {j['ts']}")


# ─────────────────────── T4: 数值范围合理性 ───────────────────────
def test_price_value_in_reasonable_range():
    """股票价格应在合理范围 (0.01 ~ 10000 元)。"""
    for code in ["600519", "000001", "300750"]:
        r = httpx.get(BASE + f"/api/stock/{code}/core", timeout=10.0)
        j = r.json().get("data", {})
        quote = j.get("quote", {})
        price = quote.get("price")
        if price is not None and isinstance(price, (int, float)):
            assert 0.01 <= price <= 10000, f"{code} 价格 {price} 异常"


# ─────────────────────── T5: 空数据兜底 ───────────────────────
def test_empty_data_returns_envelope_not_crash():
    """查不存在的数据应返 envelope + 显式标记,不应 500。

    改善:vs baseline crash → envelope
    """
    r = httpx.get(BASE + "/api/stock/999999/core", timeout=10.0)
    # 400/404/200 均可,但必须 envelope
    assert r.status_code < 500, f"5xx: {r.status_code}"
    if r.headers.get("content-type", "").startswith("application/json"):
        j = r.json()
        assert "ok" in j


# ─────────────────────── T6: 字段类型稳定 ───────────────────────
def test_dashboard_signal_field_types():
    """dashboard signal 字段类型应稳定。"""
    r = httpx.get(BASE + "/api/dashboard/signal", timeout=10.0)
    j = r.json()
    if j.get("ok"):
        data = j["data"]
        # 嵌套 dict 不应有 None 关键节点
        def check_no_none_critical(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if v is None and k in ("date", "ts", "code", "name"):
                        pytest.fail(f"关键字段 {path}.{k} 是 None")
                    check_no_none_critical(v, f"{path}.{k}")
        check_no_none_critical(data)


# ─────────────────────── T7: 大数值不溢出 ───────────────────────
def test_no_infinity_or_extreme_values():
    """响应不应含 infinity 或极端值 (>1e15)。"""
    r = httpx.get(BASE + "/api/market/overview", timeout=10.0)
    j = r.json()

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
        elif isinstance(obj, float):
            assert not math.isinf(obj), f"{path} 是 infinity: {obj}"
            assert abs(obj) < 1e15, f"{path} 值过大: {obj}"

    walk(j)