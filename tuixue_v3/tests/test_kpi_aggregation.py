"""
tests/test_kpi_aggregation.py — R96 全 A 风向 KPI 口径修复单测

覆盖 _aggregate_items() 4 类边界 + ST/suspended/ex_st 分桶:
  1) 非 ST 涨停 (pct=9.8)
  2) ST 涨停 (pct=5.1, name="*ST A")
  3) 停牌 (pct=0, amount_yi=0, price>0)
  4) 非 ST 跌停 (pct=-9.8)
  5) ST 平盘 (pct=0, amount_yi=0.2, name="ST B")
  6) 微涨 (pct=0.005) — 应入 flat
  7) 普通涨/跌
  8) 一字板 pct=10.0 非 ST — 涨停

跑法:
  cd /Users/kaikai/scripts/tuixue_v3
  PYTHONPATH=. python3 -m pytest tests/test_kpi_aggregation.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 跟 conftest.py 一致: 让 `from tuixue_v3.web.all_stocks import _aggregate_items` 可解析
ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PARENT = ROOT.parent
sys.path.insert(0, str(PACKAGE_PARENT))

from tuixue_v3.web.all_stocks import (  # noqa: E402
    _aggregate_items,
    _aggregate_universe_from_cache,
    _is_st,
    _is_suspended,
    _EMPTY_STATS,
)


# ──────────────────────────────────────────────────────────────────────
# 1) _is_st / _is_suspended 单元
# ──────────────────────────────────────────────────────────────────────

class TestStSuspendedHelpers:
    def test_st_detect_basic(self):
        assert _is_st("ST 京蓝")
        assert _is_st("*ST 中迪")
        assert _is_st("ST龙元")
        assert _is_st("退市银鸽")
        assert not _is_st("中际旭创")
        assert not _is_st("")

    def test_suspended_detect(self):
        # pct=0, amount=0, price>0 = 停牌
        assert _is_suspended(0.0, 0.0, 3.0) is True
        # 价格缺失 — 不算停牌 (新股/数据缺失)
        assert _is_suspended(0.0, 0.0, 0.0) is False
        # 有成交 — 不算停牌
        assert _is_suspended(0.0, 0.5, 3.0) is False
        # 微涨 — 不算停牌
        assert _is_suspended(0.005, 0.0, 3.0) is False
        # 跌停 — 不算停牌 (pct<0)
        assert _is_suspended(-9.8, 1.0, 5.0) is False


# ──────────────────────────────────────────────────────────────────────
# 2) _aggregate_items 5 类样本核心分桶
# ──────────────────────────────────────────────────────────────────────

class TestAggregateCoreBuckets:
    @pytest.fixture
    def sample_items(self):
        """5 类核心样本 + 2 类边界"""
        return [
            # 非 ST 涨停 (pct=9.8, 几乎涨停)
            {"code": "1", "name": "A", "price": 10.0, "change_pct": 9.8,
             "amount_yi": 1.0, "mcap_yi": 100, "main_fund_inflow_wan": 0},
            # ST 涨停 (pct=5.1, ST 5% 限制)
            {"code": "2", "name": "*ST 京蓝", "price": 5.0, "change_pct": 5.1,
             "amount_yi": 0.1, "mcap_yi": 50, "main_fund_inflow_wan": 0},
            # 停牌 (pct=0, amount_yi=0, price>0)
            {"code": "3", "name": "C", "price": 3.0, "change_pct": 0.0,
             "amount_yi": 0.0, "mcap_yi": 0, "main_fund_inflow_wan": 0},
            # 非 ST 跌停 (pct=-9.8)
            {"code": "4", "name": "D", "price": 7.0, "change_pct": -9.8,
             "amount_yi": 0.5, "mcap_yi": 70, "main_fund_inflow_wan": 0},
            # ST 平盘 (pct=0, amount>0)
            {"code": "5", "name": "ST 南都", "price": 4.0, "change_pct": 0.0,
             "amount_yi": 0.2, "mcap_yi": 40, "main_fund_inflow_wan": 0},
            # 微涨 (pct=0.005) — 应入 flat
            {"code": "6", "name": "E", "price": 10.0, "change_pct": 0.005,
             "amount_yi": 0.1, "mcap_yi": 50, "main_fund_inflow_wan": 0},
            # 正常涨
            {"code": "7", "name": "F", "price": 10.0, "change_pct": 2.5,
             "amount_yi": 0.1, "mcap_yi": 50, "main_fund_inflow_wan": 0},
            # 一字板 pct=10.0 非 ST
            {"code": "8", "name": "G", "price": 10.0, "change_pct": 10.0,
             "amount_yi": 1.0, "mcap_yi": 100, "main_fund_inflow_wan": 0},
        ]

    def test_limit_up_includes_st(self, sample_items):
        """非 ST 涨停 2 (A, G) + ST 涨停 1 (京蓝) = 3"""
        s = _aggregate_items(sample_items)
        assert s["limit_up"] == 3, f"expected 3, got {s['limit_up']}"

    def test_limit_up_ex_st_excludes_st(self, sample_items):
        """剔 ST 后: 只 A + G = 2"""
        s = _aggregate_items(sample_items)
        assert s["limit_up_ex_st"] == 2, f"expected 2, got {s['limit_up_ex_st']}"

    def test_suspended_count(self, sample_items):
        """只 C 是停牌"""
        s = _aggregate_items(sample_items)
        assert s["suspended"] == 1

    def test_up_down_flat_full_scope(self, sample_items):
        """全口径 (含 ST, 不含停牌):
           up = A(9.8) + 京蓝(5.1) + F(2.5) + G(10.0) = 4
           down = D(-9.8) = 1
           flat = 微涨(0.005) + ST平盘(0) = 2
           C 停牌单独 suspended=1
        """
        s = _aggregate_items(sample_items)
        assert s["up"] == 4, f"expected up=4, got {s['up']}"
        assert s["down"] == 1, f"expected down=1, got {s['down']}"
        assert s["flat"] == 2, f"expected flat=2, got {s['flat']}"

    def test_up_down_flat_ex_st(self, sample_items):
        """剔 ST + 剔停牌口径:
           up_ex_st = A + F + G = 3
           flat_ex_st = 微涨(0.005) = 1
           down_ex_st = D = 1
        """
        s = _aggregate_items(sample_items)
        assert s["up_ex_st"] == 3
        assert s["flat_ex_st"] == 1
        assert s["down_ex_st"] == 1

    def test_limit_down(self, sample_items):
        """D 是非 ST 跌停 = 1"""
        s = _aggregate_items(sample_items)
        assert s["limit_down"] == 1

    def test_stats_source_count(self, sample_items):
        s = _aggregate_items(sample_items)
        assert s["stats_source_count"] == 8

    def test_avg_pct_includes_all(self, sample_items):
        """平均 pct 应包含停牌 (pct=0) — 跟东财 app 一致"""
        s = _aggregate_items(sample_items)
        avg = s["avg_change_pct"]
        expected = (9.8 + 5.1 + 0.0 - 9.8 + 0.0 + 0.005 + 2.5 + 10.0) / 8
        assert abs(avg - expected) < 0.001

    def test_empty_items_returns_empty_stats(self):
        s = _aggregate_items([])
        assert s == _EMPTY_STATS or s["up"] == 0


# ──────────────────────────────────────────────────────────────────────
# 3) _aggregate_universe_from_cache 中文 key fix 验证
# ──────────────────────────────────────────────────────────────────────

class TestUniverseFromCacheChineseKeys:
    """验证 R96-fix: 原 q.get("change_pct") 应改为 q.get("涨跌幅") 等中文 key"""

    def test_chinese_keys_read_correctly(self, monkeypatch):
        # 模拟 cache_quote 返回中文 key dict
        fake_cache = {
            ("quote", "1"): {
                "最新价": 10.0, "涨跌幅": 9.8, "成交额": 100_000_000,  # 1 亿
                "总市值": 10_000_000_000,  # 100 亿
            },
            ("quote", "2"): {
                "最新价": 5.0, "涨跌幅": 5.1, "成交额": 10_000_000,
                "总市值": 5_000_000_000,
            },
            ("quote", "3"): {
                "最新价": 3.0, "涨跌幅": 0.0, "成交额": 0, "总市值": 0,
            },
        }
        # 模拟 universe dict (提供 name)
        universe = {
            "1": {"name": "A"},
            "2": {"name": "*ST 京蓝"},
            "3": {"name": "C"},
        }

        # monkeypatch _cache_quote
        from tuixue_v3.web import server as _srv
        monkeypatch.setattr(_srv, "_cache_quote", _FakeCache(fake_cache))

        s = _aggregate_universe_from_cache(["1", "2", "3"], universe)
        # 1 是非 ST 涨停, 2 是 ST 涨停, 3 是停牌
        assert s["limit_up"] == 2, f"limit_up should be 2 (A non-ST + ST), got {s['limit_up']}"
        assert s["limit_up_ex_st"] == 1
        assert s["suspended"] == 1
        # amount_yi: 1 + 0.1 + 0 (停牌) = 1.1 亿
        assert abs(s["total_amount_yi"] - 1.1) < 0.01
        # mcap_yi: 100 + 50 + 0 = 150 亿
        assert abs(s["total_mcap_yi"] - 150) < 0.01
        # stats_source_count 实际取到 3
        assert s["stats_source_count"] == 3


class _FakeCache:
    """mock _cache_quote.get(('quote', code)) 接口"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


# ──────────────────────────────────────────────────────────────────────
# 4) 真实场景压力测试 — 模拟 push2 全 5548 只
# ──────────────────────────────────────────────────────────────────────

class TestPush2FullSnapshotParity:
    """对比 push2 真值 vs server 算法:
    - 真 up ≈ 3859, flat ≈ 130, down ≈ 1216
    - 真 涨停 ≈ 125 (非 ST 115 + ST 10)
    - 真 停牌 ≈ 343
    """

    def test_regression_known_totals(self):
        # 模拟 push2 全 5548 只 — 用已知总数合成
        # up=3859, flat=130 (真平盘), down=1216, suspended=343, ST涨停=10
        items = []
        for _ in range(3859):
            items.append({"code": "x", "name": "A", "price": 10.0,
                          "change_pct": 1.5, "amount_yi": 0.5, "mcap_yi": 100,
                          "main_fund_inflow_wan": 0})
        for _ in range(1216):
            items.append({"code": "x", "name": "A", "price": 10.0,
                          "change_pct": -1.5, "amount_yi": 0.5, "mcap_yi": 100,
                          "main_fund_inflow_wan": 0})
        for _ in range(130):
            items.append({"code": "x", "name": "A", "price": 10.0,
                          "change_pct": 0.0, "amount_yi": 0.5, "mcap_yi": 100,
                          "main_fund_inflow_wan": 0})
        for _ in range(343):
            items.append({"code": "x", "name": "A", "price": 10.0,
                          "change_pct": 0.0, "amount_yi": 0.0, "mcap_yi": 100,
                          "main_fund_inflow_wan": 0})
        # ST 涨停 10 只
        for _ in range(10):
            items.append({"code": "x", "name": "*ST 京蓝", "price": 5.0,
                          "change_pct": 5.1, "amount_yi": 0.1, "mcap_yi": 50,
                          "main_fund_inflow_wan": 0})

        s = _aggregate_items(items)
        # 全口径: up=3859 + ST涨停10 = 3869
        assert s["up"] == 3869
        # flat=130
        assert s["flat"] == 130
        # down=1216
        assert s["down"] == 1216
        # suspended=343
        assert s["suspended"] == 343
        # limit_up: ST 涨停 10 + 非 ST 涨停 0 = 10
        assert s["limit_up"] == 10
        # limit_up_ex_st: 0 (没非 ST 涨停)
        assert s["limit_up_ex_st"] == 0
        # ex_st: up_ex_st = 3859 (剔 ST 涨停的 10)
        assert s["up_ex_st"] == 3859
        # down_ex_st = 1216
        assert s["down_ex_st"] == 1216
        # flat_ex_st = 130 (停牌已剔)
        assert s["flat_ex_st"] == 130