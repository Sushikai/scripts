"""
个股全景 (全 A 风向) — 批量聚合 + 4 层 taxonomy
──────────────────────────────────────────────────────
设计:
  - 默认 200 只,筛选后 2000
  - 沙箱 5400 全量拉实时不可行,采用 zt_pool + recent_zt + 全 A 补全策略
  - 有 filter 时: 先 sector 富集 → 过滤 → 再 realtime (避免空跑)
  - ThreadPoolExecutor 并发 fetch_realtime / fetch_main_fund_flow
  - 失败单股单独标 "—",不抛整体

数据流:
  fetch_limit_up_pool(today)      ~50-100 只 (今日涨停)
  + fetch_recent_zt_pool(3日)     ~300-500 只
  + fetch_stock_list_all()        全 5530 只 (名称补全)
  → universe
  → 按 filter 路径分流:
     - 有 filter: sector 富集 + filter → realtime (仅候选)
     - 无 filter: realtime (前 N 只)
  → 排序 + 截断 Top N

2026-07-12 · 用户反馈 #12: 新增"全 A 风向"页,4 层联动
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Query

log = logging.getLogger(__name__)


_EXECUTOR = ThreadPoolExecutor(max_workers=24, thread_name_prefix="all_stocks")


# ═════════════════════════════════════════════════════════════════
# 0) quote 短期缓存 (R18 2026-07-14)
# 同一进程内多 worker 同时拉同一只 code,只让一个出去抓上游,其余等结果。
# TTL 5s 跟 server._cache_quote 一致 — 5s 内页面切 filter 复用,跨秒拉一次。
# ═════════════════════════════════════════════════════════════════
_QUOTE_TTL = 10.0  # R7-perf: 5→10,减少 eastmoney 拉取频率,筛选页 10s 报价足够新鲜
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_QUOTE_CACHE_LOCK = threading.Lock()
_QUOTE_INFLIGHT: dict[str, threading.Event] = {}
_QUOTE_INFLIGHT_LOCK = threading.Lock()
_QUOTE_INFLIGHT_RESULT: dict[str, dict] = {}


def _quote_cache_get(code: str) -> dict | None:
    now = time.monotonic()
    with _QUOTE_CACHE_LOCK:
        entry = _QUOTE_CACHE.get(code)
        if not entry:
            return None
        ts, data = entry
        if now - ts > _QUOTE_TTL:
            _QUOTE_CACHE.pop(code, None)
            return None
        return data


def _quote_cache_set(code: str, data: dict) -> None:
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE[code] = (time.monotonic(), data)


def _quote_cache_invalidate(code: str) -> None:
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE.pop(code, None)


# ═════════════════════════════════════════════════════════════════
# 1) 静态 filter 全集
# ═════════════════════════════════════════════════════════════════
def filters_full() -> dict[str, Any]:
    """返回前端 filter dropdown 用的全集"""
    from . import sector_classify as _sc
    from . import sector_taxonomy as _st

    clusters = []
    for cname in _st.CLUSTER_ORDER:
        if cname == "其他":
            continue
        info = _st.CLUSTERS.get(cname, {})
        clusters.append({
            "name":    cname,
            "color":   info.get("color", "#888"),
            "icon":    info.get("icon", ""),
            "desc":    info.get("desc", ""),
            "sw_set":  sorted(info.get("sw_set", set())),
        })

    chains = []
    l4_set: set[str] = set()
    for cname, info in _st.ALL_CHAINS.items():
        l4_list = info.get("l4") or []
        for x in l4_list:
            l4_set.add(x)
        chains.append({
            "name": cname,
            "sw":   info.get("sw", ""),
            "l4":   l4_list,
        })

    # R18 (2026-07-14): domains 从 ai_tags.labels 实时聚出 — 前端不再硬编码
    domains = _collect_domains()

    return {
        "clusters":   clusters,
        "industries": list(_sc.SW_31),
        "chains":     chains,
        "l4":         sorted(l4_set),
        "domains":    domains,
    }


# ═════════════════════════════════════════════════════════════════
# 1.5) domain 列表从 universe 的 ai_tags.labels 聚合
# ═════════════════════════════════════════════════════════════════
_DOMAINS_CACHE: dict = {"data": None, "ts": 0.0}
_DOMAINS_TTL = 1800.0  # 30 分钟 — labels 变更频率低


def _collect_domains() -> list[str]:
    """从 universe 一次性 enrich 取 ai_tags.labels 全集。30 分钟缓存。"""
    now = time.time()
    if _DOMAINS_CACHE["data"] and (now - _DOMAINS_CACHE["ts"]) < _DOMAINS_TTL:
        return _DOMAINS_CACHE["data"]

    try:
        universe, _ = _build_universe()
        # 只 enrich 已活跃的 (ZT 当日 + 3 日 + 前 300 活跃),取 labels 全集
        codes = list(universe.keys())[:800]
        sec_map = _enrich_sector_bulk(codes)
        labels: set[str] = set()
        for sec in sec_map.values():
            for lab in (sec.get("ai_tags") or {}).get("labels") or []:
                if lab and isinstance(lab, str):
                    labels.add(lab.strip())
        out = sorted(labels)
        _DOMAINS_CACHE["data"] = out
        _DOMAINS_CACHE["ts"] = now
        return out
    except Exception as e:
        log.debug(f"_collect_domains 失败: {e}")
        return _DOMAINS_CACHE.get("data") or []


# ═════════════════════════════════════════════════════════════════
# 2) 行情抓取 helpers
# ═════════════════════════════════════════════════════════════════
def _safe_rt(code: str) -> dict:
    # R18: 先查进程内 quote 缓存(5s),避免重复多源 fallback
    hit = _quote_cache_get(code)
    if hit is not None:
        return hit

    from .. import lib_common as _lc
    out: dict[str, Any] = {"_ok": False}
    try:
        rt = _lc.fetch_realtime(code) or {}
        price   = rt.get("最新价") or 0
        prev    = rt.get("昨收") or 0
        change_amt = rt.get("涨跌额") or 0
        # R18 fix: 腾讯源不返回"涨跌额",但有 最新价+昨收 → 直接算 (避免排序全是 0)
        if not change_amt and price and prev:
            try:
                change_amt = round(float(price) - float(prev), 4)
            except (TypeError, ValueError):
                change_amt = 0
        out["price"]         = price
        out["change_amt"]    = change_amt
        out["change_pct"]    = rt.get("涨跌幅") or 0
        out["turnover"]      = rt.get("换手率") or 0
        out["volume_ratio"]  = rt.get("量比") or 0
        out["amplitude"]     = rt.get("振幅") or 0
        amount = rt.get("成交额") or 0
        try:
            out["amount_yi"] = float(amount) / 1e8 if amount else 0
        except (TypeError, ValueError):
            out["amount_yi"] = 0
        mcap = rt.get("总市值") or 0
        try:
            out["mcap_yi"] = float(mcap) / 1e8 if mcap else 0
        except (TypeError, ValueError):
            out["mcap_yi"] = 0
        out["pe_ttm"]        = rt.get("市盈率") or rt.get("市盈率(动)") or 0
        out["_ok"]           = True
        out["_rt_src"]       = rt.get("_source", "")
    except Exception as e:
        log.debug(f"fetch_realtime {code} 失败: {e}")

    if out.get("_ok"):
        _quote_cache_set(code, out)
    return out


def _safe_fund(code: str) -> float:
    from .. import lib_common as _lc
    try:
        f = _lc.fetch_main_fund_flow(code) or {}
        return float(f.get("main_net") or 0)
    except Exception:
        return 0


def _read_quote_cache(codes: list[str]) -> dict[str, dict]:
    """R1 (2026-07-19) 第一性原理重构: board 永远走缓存 — O(1) 读,0 网络。
    不在 board 路径上 fetch realtime,那是 poller 的活。

    缓存层次 (4 workers 共享):
      L0: TTLCache (5s, 进程内, 0ms)
      L1: Redis K.QUOTE (5s, 跨进程, ~0.1ms/code)

    poller 预热 top universe + zt_pool → Redis + 当前 worker TTLCache。
    全部 miss 的 code 标记 _ok=False,但不阻塞整体响应。
    """
    from . import server as _srv
    from .. import cache_store as _cs
    cache = _srv._cache_quote
    out: dict[str, dict] = {}
    redis_miss: list[str] = []

    for c in codes:
        q = cache.get(("quote", c))
        if q:
            _out_one(out, c, q)
        else:
            redis_miss.append(c)

    # L1: Redis bulk fallback
    if redis_miss:
        try:
            store = _cs.get_store()
            redis_keys = [_cs.K.QUOTE.format(code=c) for c in redis_miss]
            redis_vals = store.mget(redis_keys)
            for c in redis_miss:
                rk = _cs.K.QUOTE.format(code=c)
                raw = redis_vals.get(rk)
                if raw and isinstance(raw, dict):
                    cache.set(("quote", c), raw)  # 写回 TTLCache,本 worker 下次命中
                    _out_one(out, c, raw)
                else:
                    out[c] = {"_ok": False}
        except Exception as e:
            log.debug(f"_read_quote_cache Redis fallback 失败: {e}")
            for c in redis_miss:
                out[c] = {"_ok": False}

    return out


def _out_one(out: dict[str, dict], code: str, q: dict) -> None:
    """把 quote dict 转成 board 行格式,写入 out dict。"""
    price = q.get("最新价") or 0
    prev  = q.get("昨收") or 0
    change_amt = q.get("涨跌额") or 0
    if not change_amt and price and prev:
        try:
            change_amt = round(float(price) - float(prev), 4)
        except (TypeError, ValueError):
            change_amt = 0
    try:
        amount_yi = float(q.get("成交额") or 0) / 1e8
    except (TypeError, ValueError):
        amount_yi = 0
    try:
        mcap_yi = float(q.get("总市值") or 0) / 1e8
    except (TypeError, ValueError):
        mcap_yi = 0
    out[code] = {
        "_ok":          True,
        "_rt_src":      q.get("_source", "cache"),
        "price":        price,
        "change_amt":   change_amt,
        "change_pct":   q.get("涨跌幅") or 0,
        "turnover":     q.get("换手率") or 0,
        "volume_ratio": q.get("量比") or 0,
        "amplitude":    q.get("振幅") or 0,
        "amount_yi":    amount_yi,
        "mcap_yi":      mcap_yi,
        "pe_ttm":       q.get("市盈率") or q.get("市盈率(动)") or 0,
    }


def _read_fund_cache(codes: list[str]) -> dict[str, float]:
    """R1: fund 同样走缓存,不在 board 路径上 fetch。
    _cache_fund 60s TTL,poller 可选预热 (默认不预热,接受冷门 code fund=0)。
    """
    from . import server as _srv
    cache = _srv._cache_fund
    out: dict[str, float] = {}
    for c in codes:
        v = cache.get(("fund", c))
        out[c] = float(v) if v is not None else 0.0
    return out


def _trigger_warm_async(codes: list[str], priority_codes: list[str] | None = None) -> None:
    """R1: board 响应后,异步补热 cache miss 的 codes — 后台 fire-and-forget,不等结果。
    priority_codes 排在前(通常是被用户当前 filter 命中的)。
    poller 的下一轮 tick 自然会抓,但这里直接 fire 让 cold path 也有 ~5s 内补齐。
    """
    if not codes:
        return
    # 用 polling 风格的"写 cache 让下次命中" 路径 — 简单:把 codes 塞进 server._recent_codes
    # poller 下一轮会自动 fetch
    try:
        from . import server as _srv
        now = time.time()
        with _srv._recent_codes_lock if hasattr(_srv, '_recent_codes_lock') else _NullCtx():
            for c in (priority_codes or []) + list(codes):
                if c and len(c) == 6 and c.isdigit():
                    _srv._recent_codes[c] = now
    except Exception as e:
        log.debug(f"_trigger_warm_async 失败: {e}")


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


# 保留旧函数兼容:被外部 import 时不报错。但 board_snapshot 不再用。
def _bulk_rt(codes: list[str], overall_timeout: float = 12.0) -> dict[str, dict]:
    """[已废弃 R1] 旧并发 realtime 抓取。改走 _read_quote_cache + poller 预热。
    保留仅供外部 import 兼容,内部不再调用。
    """
    return _read_quote_cache(codes)


def _bulk_fund(codes: list[str], overall_timeout: float = 10.0) -> dict[str, float]:
    """[已废弃 R1] 旧并发 fund 抓取。改走 _read_fund_cache。
    """
    return _read_fund_cache(codes)


def _enrich_sector_bulk(codes: list[str], name_map: dict[str, str] | None = None) -> dict[str, dict]:
    """批量 sector 富集 — 一次性读缓存 + 解析,避免逐只 IO。

    sector_classify._load_cache() 每次都会 disk read + JSON parse,
    5530 只 × per-call IO 极慢。这里直接复用它的 _lock + 一次性 read。

    R5 fix: cache miss 不再返空 dict,而是用 stock name 跑 heuristic (classify_sector_name)
    给一个弱分类,避免 5400 只里 5300 只 taxonomy 直接被 filter 踢掉。
    """
    from . import sector_classify as _sc
    from . import sector_taxonomy as _st
    out: dict[str, dict] = {}
    try:
        with _sc._lock:
            cache = _sc._load_cache()
            stocks = cache.get("stocks", {})
            built_at = cache.get("_meta", {}).get("built_at") or 0
            fresh = (time.time() - built_at) < _sc.CACHE_TTL
        for c in codes:
            hit = stocks.get(c)
            if hit and fresh:
                # 走 _format_sector 走 4 层 taxonomy
                sw = hit.get("sw", "")
                out[c] = _sc._format_sector(
                    c, _sc.detect_board(c), sw,
                    hit.get("source") or "cache",
                    fresh=False,
                    sw_raw=hit.get("sw_raw") or "",
                    csrc_raw=hit.get("csrc_raw") or "",
                )
            else:
                # R5: cache miss → 用 name heuristic 兜底
                nm = (name_map or {}).get(c, "") or ""
                if nm:
                    tax = _st.classify_sector_name(nm)
                    if tax and (tax.get("l1") or tax.get("l2") or tax.get("l3") or tax.get("l4")):
                        # heuristic 命中,合成最小可用 sector dict
                        l1 = tax["l1"] or ""
                        out[c] = {
                            "code": c,
                            "board": _sc.detect_board(c),
                            "sw": tax.get("l2") or None,
                            "sw_raw": None,
                            "csrc": None, "cics": None, "gics": None,
                            "ai_tags": {"tags": [], "labels": [], "is_main_field": False},
                            "taxonomy": {
                                "level1_cluster": l1,
                                "level2_sw":      tax.get("l2") or "",
                                "level3_chain":   tax.get("l3") or "",
                                "level4_subconcept": tax.get("l4") or [],
                                "role":           tax.get("role") or "",
                                "source":         tax.get("l3_source") or "heur",
                                "noise_reason":   "",
                                "cluster_color":  tax.get("l1_color") or "#888",
                            },
                            "source": "heur_cache_miss",
                            "fresh":  False,
                        }
                        continue
                    # R5b: sector_name heuristic 没命中 → 试 concept_taxonomy (400+ L4 关键词)
                    try:
                        from .concept_taxonomy import match_concepts as _mc, L3_TO_CLUSTER as _L3C
                        cpt = _mc(nm)
                        if cpt:
                            c_l3, c_l4s = cpt[0]  # 取第一个匹配的概念
                            c_cluster = _L3C.get(c_l3, "")
                            # 推导 level2_sw: 看 sector_taxonomy 里同链名是否有 SW
                            c_sw = _st.l3_to_sw(c_l3) or ""
                            c_color = _st.CLUSTERS.get(c_cluster, {}).get("color", "#888") if hasattr(_st, 'CLUSTERS') else "#888"
                            # concept 集群 → sector 集群映射
                            _CC_MAP = {
                                "半导体芯片产业链": "大科技", "AI算力全链条": "大科技",
                                "通信板块": "大科技", "计算机软件信创": "大科技",
                                "机器人高端制造": "大科技", "前沿未来科技": "大科技",
                                "传媒数字科技": "大科技",
                                "新能源": "高端制造", "军工": "大科技",
                                "地产基建": "高端制造", "公用事业": "周期资源",
                                "周期资源": "周期资源", "消费": "消费",
                                "医药生物": "医药生物", "金融": "金融", "农林牧渔": "周期资源",
                            }
                            c_l1 = _CC_MAP.get(c_cluster, "其他")
                            c_color = _st.CLUSTERS.get(c_l1, {}).get("color", "#888")
                            out[c] = {
                                "code": c,
                                "board": _sc.detect_board(c),
                                "sw": c_sw or None,
                                "sw_raw": None,
                                "csrc": None, "cics": None, "gics": None,
                                "ai_tags": {"tags": [], "labels": [], "is_main_field": False},
                                "taxonomy": {
                                    "level1_cluster":    c_l1,
                                    "level2_sw":         c_sw,
                                    "level3_chain":      c_l3,
                                    "level4_subconcept": c_l4s,
                                    "role":              "",
                                    "source":            "concept_heur",
                                    "noise_reason":      "",
                                    "cluster_color":     c_color,
                                },
                                "source": "heur_cache_miss",
                                "fresh":  False,
                            }
                            continue
                    except Exception:
                        pass
                out[c] = {}
    except Exception as e:
        log.debug(f"_enrich_sector_bulk 失败: {e}")
    return out


# ═════════════════════════════════════════════════════════════════
# 3) universe 构造
# ═════════════════════════════════════════════════════════════════
_UNIVERSE_CACHE: dict = {"data": None, "ts": 0.0}
_UNIVERSE_TTL = 300.0  # 5 分钟 (R4-perf: 180→300,涨停池影响不大但减少 universe 重构建)

# 2026-07-12: board_snapshot 结果缓存 — 同 (filter,sort,order) 组合 120s 内复用
# R4-perf: 60→120,减少 50% 后端 cold-start
_BOARD_CACHE: dict = {}
_BOARD_TTL = 120.0

# R-2026-07-16: KPI hero 用聚合 stats — board_snapshot 一次计算并随缓存复用
_EMPTY_STATS = {
    "up": 0, "down": 0, "flat": 0, "limit_up": 0,
    "total_main_fund_wan": 0, "total_amount_yi": 0, "total_mcap_yi": 0,
    "avg_change_pct": 0.0, "median_change_pct": 0.0,
    "max_change_pct": 0.0, "min_change_pct": 0.0,
}


def _compute_board_stats(items: list[dict]) -> dict:
    """从 items 算 KPI — items 已经是过滤+排序后的全部结果 (R16 缓存),
    不论 fetch_n 上限。所以这里的统计覆盖 filter 后全部候选。
    """
    return _aggregate_items(items)


def _aggregate_items(items: list[dict]) -> dict:
    """用 list[dict] 算所有 KPI 一次。

    items 每行: code/name/price/change_pct/amount_yi/mcap_yi/
                main_fund_inflow_wan/zt_today/zt_recent。
    返回: KPI dict + count (items 条数,前端用作「统计基于多少只」透明披露)。
    """
    if not items:
        return dict(_EMPTY_STATS)

    up = down = flat = limit_up = 0
    sum_fund = sum_amount = sum_mcap = 0.0
    pcts: list[float] = []
    for r in items:
        pct = float(r.get("change_pct") or 0)
        if pct >= 0.01:
            up += 1
        elif pct <= -0.01:
            down += 1
        else:
            flat += 1
        if r.get("zt_today"):
            limit_up += 1
        sum_fund += float(r.get("main_fund_inflow_wan") or 0)
        sum_amount += float(r.get("amount_yi") or 0)
        sum_mcap += float(r.get("mcap_yi") or 0)
        pcts.append(pct)

    pcts.sort()
    n = len(pcts)
    avg = sum(pcts) / n if n else 0.0
    if n % 2 == 0:
        median = (pcts[n // 2 - 1] + pcts[n // 2]) / 2
    else:
        median = pcts[n // 2]

    return {
        "up":                  up,
        "down":                down,
        "flat":                flat,
        "limit_up":            limit_up,
        "limit_down":          sum(1 for r in items if r.get("change_pct", 0) <= -9.7),
        "total_main_fund_wan": sum_fund,
        "total_amount_yi":     sum_amount,
        "total_mcap_yi":       sum_mcap,
        "avg_change_pct":      avg,
        "median_change_pct":   median,
        "max_change_pct":      pcts[-1] if pcts else 0.0,
        "min_change_pct":      pcts[0] if pcts else 0.0,
        "stats_source_count":  len(items),
    }


def _aggregate_universe_from_cache(
    candidate_codes: list[str],
    universe: dict[str, dict],
) -> dict:
    """R1-25 (2026-07-17): 用缓存 quote 跨 filter 后**所有**候选聚合 KPI。

    设计:不走 fetch_realtime 网络 — 全部从 _cache_quote TTLCache 读 (warm 后 O(1))。
    5400 只读 O(5400) ≈ 30ms,不会卡前端。冷启时大多 code 没缓存,
    缺数据的 code 不计入桶 (跟 limit_up 不一致也无所谓 — limit_up 用 universe.zt_today)。

    Returns: KPI dict + stats_source_count (实际拿到 quote 的 code 数)
    """
    if not candidate_codes:
        return dict(_EMPTY_STATS, stats_source_count=0)

    from . import server as _srv
    cache_quote = _srv._cache_quote

    up = down = flat = limit_up = limit_down = 0
    sum_fund = sum_amount = sum_mcap = 0.0
    pcts: list[float] = []
    count_real = 0

    for code in candidate_codes:
        u = universe.get(code) or {}
        if u.get("zt_today"):
            limit_up += 1

        q = cache_quote.get(("quote", code))
        if not q:
            continue
        count_real += 1

        pct = float(q.get("change_pct") or 0)
        if pct >= 0.01:
            up += 1
        elif pct <= -0.01:
            down += 1
        else:
            flat += 1
        if pct <= -9.7:
            limit_down += 1

        sum_amount += float(q.get("amount_yi") or 0)
        mcap_v = q.get("mcap_yi") or 0
        sum_mcap += float(mcap_v)
        pcts.append(pct)

    # main_fund_inflow 不在 quote 缓存里,从 fund 缓存读
    for code in candidate_codes:
        f = cache_quote.get(("fund", code))
        if f is not None:
            try:
                sum_fund += float(f)
            except Exception:
                pass

    if pcts:
        pcts.sort()
        n = len(pcts)
        avg = sum(pcts) / n
        if n % 2 == 0:
            median = (pcts[n // 2 - 1] + pcts[n // 2]) / 2
        else:
            median = pcts[n // 2]
    else:
        avg = median = 0.0

    return {
        "up":                  up,
        "down":                down,
        "flat":                flat,
        "limit_up":            limit_up,
        "limit_down":          limit_down,
        "total_main_fund_wan": sum_fund,
        "total_amount_yi":     sum_amount,
        "total_mcap_yi":       sum_mcap,
        "avg_change_pct":      avg,
        "median_change_pct":   median,
        "max_change_pct":      pcts[-1] if pcts else 0.0,
        "min_change_pct":      pcts[0] if pcts else 0.0,
        "stats_source_count":  count_real,
    }


def _fetch_today_zt() -> dict[str, dict]:
    from .. import data_layer as _dl
    out: dict[str, dict] = {}
    try:
        today_zt = _dl.fetch_limit_up_pool(None) or []
        for z in today_zt:
            c = (z.get("code") or "").strip()
            if c:
                out[c] = {"name": z.get("name", ""), "zt_today": True, "zt_recent": 0}
    except Exception as e:
        log.debug(f"zt_pool fetch 失败: {e}")
    return out


def _fetch_recent_zt() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        from . import multi_source_fetchers as _msf
        recent = _msf.fetch_recent_zt_pool(days=3) or {}
        for c, info in recent.items():
            out[c] = {"name": info.get("name", ""), "zt_recent": info.get("zt_count", 0)}
    except Exception as e:
        log.debug(f"recent_zt 失败: {e}")
    return out


def _build_universe() -> tuple[dict[str, dict], dict[str, str]]:
    """返回 (universe, name_map)
    universe: code → {name, zt_today, zt_recent}
    name_map: code → name

    2026-07-12 Round 5: zt 涨停池仅是 enrich 标记,沙箱 eastmoney 挂时会各卡 30s。
    改为并发 + 4s 硬预算,拿不到就跳过,stock_list_all(本地 24h 缓存)保证 universe 不空。
    另加陈旧兜底:TTL 过期后若拉不到新数据,继续用上次的 universe。
    """
    now = time.time()
    if _UNIVERSE_CACHE["data"] and (now - _UNIVERSE_CACHE["ts"]) < _UNIVERSE_TTL:
        return _UNIVERSE_CACHE["data"]

    from concurrent.futures import as_completed
    from .. import data_layer as _dl
    universe: dict[str, dict] = {}

    # zt 池并发拉,整体 4s 预算 — 拿不到就跳过,不阻塞主流程
    fut_today = _EXECUTOR.submit(_fetch_today_zt)
    fut_recent = _EXECUTOR.submit(_fetch_recent_zt)
    zt_budget = 4.0
    zt_deadline = time.time() + zt_budget
    for fut in (fut_today, fut_recent):
        remaining = max(0.05, zt_deadline - time.time())
        try:
            part = fut.result(timeout=remaining) or {}
        except Exception:
            part = {}
        for c, info in part.items():
            if c not in universe:
                universe[c] = {"name": info.get("name", ""),
                               "zt_today": info.get("zt_today", False),
                               "zt_recent": info.get("zt_recent", 0)}
            else:
                if info.get("zt_today"):
                    universe[c]["zt_today"] = True
                if info.get("zt_recent"):
                    universe[c]["zt_recent"] = info.get("zt_recent", 0)

    all_codes = _dl.fetch_stock_list_all() or []
    name_map = {c: n for c, n in all_codes}
    for c, n in all_codes:
        if c not in universe:
            universe[c] = {"name": n, "zt_today": False, "zt_recent": 0}

    # 补名字 (zt 池可能名字空)
    for c, u in universe.items():
        if not u.get("name"):
            u["name"] = name_map.get(c, "")

    # 陈旧兜底:如果本次 stock_list 也挂了(universe 极小),复用上次好数据
    if len(universe) < 100 and _UNIVERSE_CACHE["data"]:
        log.warning(f"universe 构建异常 ({len(universe)} 只) → 复用上次缓存")
        return _UNIVERSE_CACHE["data"]

    _UNIVERSE_CACHE["data"] = (universe, name_map)
    _UNIVERSE_CACHE["ts"] = time.time()
    return universe, name_map


# ═════════════════════════════════════════════════════════════════
# 3.5) 全市场快照 (2026-07-21 顶级架构核心)
# ───────────────────────────────────────────────────────────────
# 旧架构: poller 逐只 fetch_realtime → 5400 只永远暖不满 → board 只能显示
#         缓存里的 ~12 只 (基准实测)。所谓"全 A"根本不是全 A。
# 新架构: push2delay 一次批量拉全市场 5540 只全字段 (~1.4s),预富集 taxonomy
#         (~0.23s) 合并成完整 board 行,进程内缓存。board 从内存读 → 全 5540
#         只瞬时可用,筛选/排序纯内存 O(5540) < 50ms。
# ═════════════════════════════════════════════════════════════════
_MARKET_SNAP: dict = {"rows": None, "ts": 0.0}
_MARKET_LOCK = threading.Lock()
_MARKET_REFRESHING = threading.Event()  # 后台刷新中标志,防重复起线程

# taxonomy 分类缓存 (慢变,日级):与 15s 报价刷新解耦。
# key = frozenset(codes) 的规模签名不现实,直接按 code 数 + 30min TTL 判定复用。
_TAX_SNAP: dict = {"map": None, "ts": 0.0, "n": 0}
_TAX_TTL = 1800.0  # 30min


def _get_tax_map(codes: list[str], name_map: dict) -> dict:
    """全市场 taxonomy 富集,30min 缓存。code 数变化 >2% 或过期才重算。"""
    snap = _TAX_SNAP
    now = time.time()
    n = len(codes)
    if (snap["map"] is not None
            and (now - snap["ts"]) < _TAX_TTL
            and snap["n"]
            and abs(n - snap["n"]) <= max(50, snap["n"] * 0.02)):
        return snap["map"]
    m = _enrich_sector_bulk(codes, name_map)
    if m:
        snap["map"] = m
        snap["ts"] = now
        snap["n"] = n
    return snap["map"] or m


def _market_ttl() -> float:
    """交易时段 15s (盘口活),盘后 180s。"""
    from datetime import datetime
    now = datetime.now()
    if now.weekday() >= 5:
        return 180.0
    mod = now.hour * 60 + now.minute
    # 9:15 集合竞价 ~ 15:05 收盘缓冲
    if 9 * 60 + 15 <= mod <= 15 * 60 + 5:
        return 15.0
    return 180.0


def _build_market_rows() -> dict[str, dict]:
    """拉全市场 bulk 快照 + universe zt 标记 + taxonomy 预富集 → 完整 board 行。
    返回 {code: row}。row 含全部报价字段 + zt 标记 + 4 层 taxonomy + domain。
    """
    from .. import multi_source_fetchers as _msf
    t0 = time.time()

    # Redis 共享原始快照 — 8 workers 只让一个真去抓 push2delay,其余读 blob。
    # 盘中 15s TTL,盘后 180s。避免多 worker 同时打爆 push2delay 被限频。
    raw: dict[str, dict] = {}
    store = None
    try:
        from .. import cache_store as _cs
        store = _cs.get_store()
        if store:
            blob = store.get("spot:all_full")
            if blob and isinstance(blob, dict) and blob.get("rows"):
                raw = blob["rows"]
    except Exception as e:
        log.debug(f"_build_market_rows Redis 读失败: {e}")

    if not raw:
        raw = _msf.fetch_spot_a_full() or {}
        if raw and store:
            try:
                store.set("spot:all_full", {"rows": raw}, ttl=int(_market_ttl()))
            except Exception as e:
                log.debug(f"_build_market_rows Redis 写失败: {e}")

    universe, name_map = _build_universe()

    # bulk 失败 → 空,调用方走旧 per-code 缓存兜底
    if not raw:
        log.warning("_build_market_rows: bulk 快照空,board 退回缓存路径")
        return {}

    # taxonomy 预富集 — 慢变 (日级),缓存 30min,避免每次 15s 刷新都重算 5540 只。
    # 热路径 (quotes) 与冷路径 (taxonomy) 分离:bg refresh 只合并新报价 + 复用缓存分类。
    codes = list(raw.keys())
    sec_map = _get_tax_map(codes, name_map)

    rows: dict[str, dict] = {}
    for c, q in raw.items():
        u = universe.get(c, {})
        sec = sec_map.get(c) or {}
        tax = sec.get("taxonomy") or {}
        ai_tags = sec.get("ai_tags") or {}
        price = q.get("最新价") or 0
        prev = q.get("昨收") or 0
        change_amt = q.get("涨跌额") or 0
        if not change_amt and price and prev:
            change_amt = round(price - prev, 4)
        rows[c] = {
            "code":         c,
            "name":         q.get("name") or u.get("name") or name_map.get(c, ""),
            "price":        price,
            "change_amt":   change_amt,
            "change_pct":   q.get("涨跌幅") or 0,
            "turnover":     q.get("换手率") or 0,
            "volume_ratio": q.get("量比") or 0,
            "amplitude":    q.get("振幅") or 0,
            "amount_yi":    round((q.get("成交额") or 0) / 1e8, 4),
            "mcap_yi":      round((q.get("总市值") or 0) / 1e8, 4),
            "pe_ttm":       q.get("市盈率") or 0,
            "pb":           q.get("市净率") or 0,
            "main_fund_inflow_wan": 0,  # 由 board 从 fund 缓存补 (可选)
            "zt_today":     u.get("zt_today", False),
            "zt_recent":    int(u.get("zt_recent", 0) or 0),
            "domain":       list(ai_tags.get("labels") or []),
            "taxonomy": {
                "l1":        tax.get("level1_cluster", "") or "",
                "l1_color":  tax.get("cluster_color", "#888"),
                "l2":        tax.get("level2_sw", "") or "",
                "l3":        tax.get("level3_chain", "") or "",
                "l3_source": tax.get("source", ""),
                "l4":        list(tax.get("level4_subconcept") or []),
                "role":      tax.get("role", "") or "",
            },
            "_ok": bool(price),
        }

    log.info(f"_build_market_rows: {len(rows)} 只就绪 ({time.time()-t0:.2f}s)")
    return rows


def _do_build_into_snap(force: bool = False) -> dict[str, dict]:
    """在锁内实际构建并写入快照。返回最新 rows。
    非 force 时,进锁后再检查一次新鲜度 — 防惊群下重复 build。
    """
    with _MARKET_LOCK:
        snap = _MARKET_SNAP
        if not force and snap["rows"] is not None and (time.time() - snap["ts"]) < _market_ttl():
            return snap["rows"]
        rows = _build_market_rows()
        if rows:
            snap["rows"] = rows
            snap["ts"] = time.time()
        return snap["rows"] or {}


def _bg_refresh() -> None:
    """后台线程刷新快照 (stale-while-revalidate),不阻塞请求。"""
    if _MARKET_REFRESHING.is_set():
        return
    _MARKET_REFRESHING.set()

    def _run():
        try:
            n = len(_do_build_into_snap(force=True))
            if n:
                log.info(f"_bg_refresh: 快照后台刷新 {n} 只")
        except Exception as e:
            log.warning(f"_bg_refresh 失败: {e}")
        finally:
            _MARKET_REFRESHING.clear()

    threading.Thread(target=_run, name="market-snap-refresh", daemon=True).start()


def _get_market_rows(force: bool = False) -> dict[str, dict]:
    """进程内全市场快照。stale-while-revalidate:
      - 有快照 (即使过期) → 立即返回,过期则后台异步刷新,永不阻塞请求。
      - 首次冷启 (快照为空) → 同步 build 一次 (仅这一次会阻塞 ~1.4s)。
      - force=True (poller) → 同步 build,保证 poller tick 后数据最新。
    """
    snap = _MARKET_SNAP
    now = time.time()
    if force:
        return _do_build_into_snap(force=True)
    if snap["rows"] is not None:
        # 有数据就先返回;过期则后台刷新 (不阻塞本次请求)
        if (now - snap["ts"]) >= _market_ttl():
            _bg_refresh()
        return snap["rows"]
    # 冷启:必须同步 build 一次 (双检锁防惊群)
    return _do_build_into_snap(force=False)


def refresh_market_snapshot() -> int:
    """poller 调用:强制刷新全市场快照。返回行数。"""
    rows = _get_market_rows(force=True)
    return len(rows)


# ═════════════════════════════════════════════════════════════════
# 4) board 快照主入口
# ═════════════════════════════════════════════════════════════════
def _norm_list(v) -> list[str]:
    """统一接 str / list / CSV / None → 非空字符串 list。
    R2 fix: 前端 multi-select 用 .join(',') 拼成 CSV 传进来,后端要解析。
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if "," in s:
        return [x.strip() for x in s.split(",") if x.strip()]
    return [s]


# ─── 跨模块快捷筛选 preset (2026-07-21) ─────────────────────────────
# 全 A 筛选按钮结合其他页面功能:纯内存谓词,秒级命中。
# 数据源:快照内已有字段 (zt/换手/量比/涨跌幅/市值) + 可选跨模块信号集合。
_PRESET_PREDICATES = {
    "zt":        lambda r: bool(r.get("zt_today")),                       # 今日涨停
    "lianban":   lambda r: int(r.get("zt_recent") or 0) >= 1,             # 近期连板/涨停
    "strong":    lambda r: float(r.get("change_pct") or 0) >= 5,          # 强势 (>+5%)
    "surge_vol": lambda r: float(r.get("volume_ratio") or 0) >= 2,        # 放量 (量比≥2)
    "hot_turn":  lambda r: float(r.get("turnover") or 0) >= 10,           # 高换手 (≥10%)
    "big_amp":   lambda r: float(r.get("amplitude") or 0) >= 7,           # 大振幅 (≥7%)
    "small_cap": lambda r: 0 < float(r.get("mcap_yi") or 0) <= 100,       # 小市值 (≤100亿)
    "mid_cap":   lambda r: 100 < float(r.get("mcap_yi") or 0) <= 500,     # 中盘
    "big_cap":   lambda r: float(r.get("mcap_yi") or 0) > 500,            # 大盘 (>500亿)
    "active":    lambda r: float(r.get("amount_yi") or 0) >= 10,          # 活跃 (成交≥10亿)
    "up":        lambda r: float(r.get("change_pct") or 0) > 0,           # 上涨
    "down":      lambda r: float(r.get("change_pct") or 0) < 0,           # 下跌
}


def _crossmod_signal_codes(signal: str) -> set[str] | None:
    """跨模块信号 → code 集合 (结合其他页面)。命中缓存则秒回,否则 None (不阻塞)。
    signal ∈ {screener(尾盘战法), weekly_bull(周线擒牛), strong_stocks(强势股)}。
    只读已算好的缓存,绝不在 board 路径触发重算。
    """
    try:
        if signal == "screener":
            # 尾盘战法 = screener 优化策略当前命中池 (只读缓存,不重算)
            from . import screener as _scr
            codes = _scr.passing_codes("optimized")
            if not codes:
                codes = _scr.passing_codes("baseline")
            return codes or None
        # 其余信号后续接入 (weekly_bull / strategy_picker 缓存)
    except Exception as e:
        log.debug(f"_crossmod_signal_codes {signal} err: {e}")
    return None


def board_snapshot(limit: int = 0,
                   page_size: int = 0,
                   offset: int = 0,
                   l1=None,
                   l2=None,
                   l3=None,
                   l4=None,
                   domain=None,
                   sort: str = "amount",
                   order: str = "desc",
                   with_fund: bool = True,
                   preset: str = "",
                   signal: str = "",
                   min_pct=None, max_pct=None,
                   min_turnover=None, min_amount_yi=None,
                   min_mcap_yi=None, max_mcap_yi=None,
                   min_vr=None) -> dict[str, Any]:
    """全 A 快照 — 内存全市场行 (5540 只) 过滤+排序+切片。

    2026-07-21 顶级重构: 走 _get_market_rows() 内存快照,不再逐只 fetch。
    快照空时 (bulk 挂) 退回 _board_snapshot_legacy per-code 缓存路径。

    新增跨模块筛选:
      preset  - 快捷预设 (zt/lianban/strong/surge_vol/hot_turn/small_cap/...)
      signal  - 跨模块信号集合 (screener 尾盘战法 等)
      min_pct/max_pct/min_turnover/min_amount_yi/min_mcap_yi/max_mcap_yi/min_vr - 数值区间
    """
    t0 = time.time()
    ps = int(page_size or limit or 30)
    ps = max(1, min(ps, 500))
    offset = max(0, int(offset or 0))
    l1_list     = _norm_list(l1)
    l2_list     = _norm_list(l2)
    l3_list     = _norm_list(l3)
    l4_list     = _norm_list(l4)
    domain_list = _norm_list(domain)
    preset_list = _norm_list(preset)
    signal_list = _norm_list(signal)
    sort  = sort or "amount"
    order = order or "desc"

    rows_map = _get_market_rows()
    if not rows_map:
        # bulk 挂 → 退回旧 per-code 缓存路径
        return _board_snapshot_legacy(
            limit=limit, page_size=page_size, offset=offset,
            l1=l1, l2=l2, l3=l3, l4=l4, domain=domain,
            sort=sort, order=order, with_fund=with_fund)

    def _rng(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    f_min_pct, f_max_pct = _rng(min_pct), _rng(max_pct)
    f_min_turn = _rng(min_turnover)
    f_min_amt = _rng(min_amount_yi)
    f_min_mcap, f_max_mcap = _rng(min_mcap_yi), _rng(max_mcap_yi)
    f_min_vr = _rng(min_vr)

    # 跨模块信号 code 集合 (可选, AND)
    signal_codes: set[str] | None = None
    for sg in signal_list:
        s = _crossmod_signal_codes(sg)
        if s is not None:
            signal_codes = s if signal_codes is None else (signal_codes & s)

    # 一次线性扫描过滤全市场
    filtered: list[dict] = []
    for c, r in rows_map.items():
        tax = r.get("taxonomy") or {}
        if l1_list and (tax.get("l1") or "") not in l1_list:
            continue
        if l2_list and (tax.get("l2") or "") not in l2_list:
            continue
        if l3_list and (tax.get("l3") or "") not in l3_list:
            continue
        if l4_list:
            l4s = tax.get("l4") or []
            if not any(x in l4s for x in l4_list):
                continue
        if domain_list:
            labels = r.get("domain") or []
            if not any(x in labels for x in domain_list):
                continue
        if signal_codes is not None and c not in signal_codes:
            continue
        # preset (AND over 多个)
        if preset_list and not all(_PRESET_PREDICATES.get(p, lambda _r: True)(r) for p in preset_list):
            continue
        pct = float(r.get("change_pct") or 0)
        if f_min_pct is not None and pct < f_min_pct:
            continue
        if f_max_pct is not None and pct > f_max_pct:
            continue
        if f_min_turn is not None and float(r.get("turnover") or 0) < f_min_turn:
            continue
        if f_min_amt is not None and float(r.get("amount_yi") or 0) < f_min_amt:
            continue
        if f_min_mcap is not None and float(r.get("mcap_yi") or 0) < f_min_mcap:
            continue
        if f_max_mcap is not None and float(r.get("mcap_yi") or 0) > f_max_mcap:
            continue
        if f_min_vr is not None and float(r.get("volume_ratio") or 0) < f_min_vr:
            continue
        filtered.append(r)

    has_filter = bool(l1_list or l2_list or l3_list or l4_list or domain_list
                      or preset_list or signal_list or f_min_pct is not None
                      or f_max_pct is not None or f_min_turn is not None
                      or f_min_amt is not None or f_min_mcap is not None
                      or f_max_mcap is not None or f_min_vr is not None)

    # 可选:主力资金从 fund 缓存补 (O(n) 读,不 fetch)
    if with_fund and filtered:
        fund_map = _read_fund_cache([r["code"] for r in filtered])
        for r in filtered:
            fv = fund_map.get(r["code"])
            if fv:
                r["main_fund_inflow_wan"] = fv

    # 排序
    key_fn = _sort_key_fn(sort)
    if key_fn:
        filtered.sort(key=key_fn, reverse=(order != "asc"))

    total_available = len(filtered)
    stats = _aggregate_items(filtered)
    stats["stats_total_count"] = total_available
    stats["limit_up"] = sum(1 for r in filtered if r.get("zt_today"))

    page = filtered[offset:offset + ps]
    has_more = (offset + len(page)) < total_available

    return {
        "items":           page,
        "count":           len(page),
        "offset":          offset,
        "next_offset":     offset + len(page),
        "has_more":        has_more,
        "total_available": total_available,
        "ts":              _MARKET_SNAP["ts"],
        "took_ms":         int((time.time() - t0) * 1000),
        "sort":            sort,
        "order":           order,
        "cache_hit":       (time.time() - _MARKET_SNAP["ts"]) < _market_ttl(),
        "snapshot_age_s":  round(time.time() - _MARKET_SNAP["ts"], 1),
        "filters_used": {
            "l1": l1_list, "l2": l2_list, "l3": l3_list, "l4": l4_list,
            "domain": domain_list, "preset": preset_list, "signal": signal_list,
            "limit": ps,
        },
        "total_rt_ok":      len(rows_map),
        "total_candidates": total_available,
        "total_universe":   len(rows_map),
        "stats":            stats,
    }


def _board_snapshot_legacy(limit: int = 0,
                   page_size: int = 0,
                   offset: int = 0,
                   l1=None,
                   l2=None,
                   l3=None,
                   l4=None,
                   domain=None,
                   sort: str = "amount",
                   order: str = "desc",
                   with_fund: bool = True) -> dict[str, Any]:
    """
    Args:
      limit     - (legacy) 直接 cap,与 page_size 二选一,默认 0
      page_size - 单页大小;前端按 visible*1.5 传 (≈30)
      offset    - 跳过前 N 条;首屏 0,滚动追加 += page_size
      l1/l2/l3/l4/domain - 4 层 + 领域 filter (支持 str | list | CSV)
      sort      - change_pct | turnover | amount | volume_ratio | main_fund_inflow | mcap
      order     - asc | desc
      with_fund - 是否拉主力净额

    R16 (2026-07-13): 加 offset/page_size 支持无限滚动。
       - 缓存按 (filter, sort, order, with_fund) 命中后切片,offset 变化不 miss
       - 首次 cache miss 时 fetch_n = max(offset+page_size+100, page_size*3, 200), 后续滚动多在缓存里切
       - 返回 has_more / next_offset / total_available

    R4 联动: 各层独立判定,OR over 同一层内 (电子 OR 计算机)、AND across 不同层
    """
    t0 = time.time()
    ps = int(page_size or limit or 30)
    ps = max(1, min(ps, 500))
    offset = max(0, int(offset or 0))
    l1_list     = _norm_list(l1)
    l2_list     = _norm_list(l2)
    l3_list     = _norm_list(l3)
    l4_list     = _norm_list(l4)
    domain_list = _norm_list(domain)
    has_filter  = bool(l1_list or l2_list or l3_list or l4_list or domain_list)
    sort  = sort or "amount"
    order = order or "desc"

    # R16: 缓存 key 不含 offset/page_size — 同 filter+sort+order 一次构建,所有分页复用
    # R18: 去 with_fund — 总是 fetch fund flow (5s,低成本),前端不再传 with_fund=False
    cache_key = (tuple(l1_list), tuple(l2_list), tuple(l3_list), tuple(l4_list), tuple(domain_list), sort, order)
    now = time.time()
    cached = _BOARD_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _BOARD_TTL:
        full = cached["data"]
        total_available = len(full)
        page = full[offset:offset + ps]
        has_more = (offset + len(page)) < total_available
        return {
            "items":           page,
            "count":           len(page),
            "offset":          offset,
            "next_offset":     offset + len(page),
            "has_more":        has_more,
            "total_available": total_available,
            "ts":              cached["ts"],
            "took_ms":         int((time.time() - t0) * 1000),
            "sort":            sort,
            "order":           order,
            "cache_hit":       True,
            "filters_used": {
                "l1": l1_list, "l2": l2_list, "l3": l3_list, "l4": l4_list,
                "domain": domain_list, "limit": ps,
            },
            "total_universe":  cached.get("total_universe", 0),
            "total_candidates": cached.get("total_candidates", 0),
            "stats":           cached.get("stats") or _EMPTY_STATS,
            "stats_total_count": cached.get("stats_total_count", 0),
        }

    universe, name_map = _build_universe()
    log.info(f"all_stocks: universe {len(universe)} 只")

    sec_map: dict[str, dict] = {}
    rt_codes: list[str] = []
    # R16: 实际拉取的 realtime 数 = max(offset+ps+100, ps*3, 200), 给后续滚动预留 buffer
    # R18: 首屏底线 200→150,缩短 cold-start 3-4s → 2.5-3s
    fetch_n = min(max(offset + ps + 100, ps * 3, 150), 2000)

    if has_filter:
        # 有 filter: 先 sector 富集 → 过滤 → 再 realtime
        sec_codes_all = list(universe.keys())
        sec_map = _enrich_sector_bulk(sec_codes_all)

        candidate_codes = []
        for c in sec_codes_all:
            sec = sec_map.get(c) or {}
            tax = sec.get("taxonomy") or {}
            ai_tags = sec.get("ai_tags") or {}
            # R2: 任一层多值 → 命中其一即可 (OR);不同层 AND
            if l1_list:
                if (tax.get("level1_cluster") or "") not in l1_list:
                    continue
            if l2_list:
                if (tax.get("level2_sw") or "") not in l2_list:
                    continue
            if l3_list:
                if (tax.get("level3_chain") or "") not in l3_list:
                    continue
            if l4_list:
                l4_stock = tax.get("level4_subconcept") or []
                # 命中任一即可
                if not any(x in l4_stock for x in l4_list):
                    continue
            if domain_list:
                labels = ai_tags.get("labels") or []
                if not any(x in labels for x in domain_list):
                    continue
            candidate_codes.append(c)

        log.info(f"all_stocks: filter 后 {len(candidate_codes)} 只")
        # R16: 取 realtime 按 fetch_n (而非 target_limit), 覆盖滚动 + buffer
        rt_codes = candidate_codes[:fetch_n]
    else:
        # R3 fix: 无 filter 时,按活跃度取 top N — zt_today > zt_recent > 普通股
        # 原逻辑取 dict 前 N = zt_pool + recent_zt + 000xxx 低代码老股,严重偏
        def _activity(c):
            u = universe.get(c, {})
            return (
                1 if u.get("zt_today") else 0,
                int(u.get("zt_recent", 0)) if u.get("zt_recent") else 0,
            )
        active_codes = sorted(universe.keys(), key=_activity, reverse=True)
        rt_codes = active_codes[:fetch_n]

    rt_map = _read_quote_cache(rt_codes)
    cache_hit = sum(1 for r in rt_map.values() if r.get("_ok"))
    log.info(f"all_stocks: cache 命中 {cache_hit}/{len(rt_codes)} (filter={has_filter}, sort={sort})")

    # R1: cache miss 的 codes 异步触发 poller 下一轮预热 — 不阻塞 board 响应
    miss_codes = [c for c in rt_codes if not rt_map.get(c, {}).get("_ok")]
    if miss_codes:
        _trigger_warm_async(miss_codes, priority_codes=rt_codes[:50])

    # 构造行
    rows = []
    for c in rt_codes:
        rt = rt_map.get(c) or {}
        if not rt.get("_ok"):
            continue
        u = universe.get(c, {})
        rows.append({
            "code":         c,
            "name":         u.get("name") or name_map.get(c, ""),
            "price":        rt.get("price", 0),
            "change_amt":   rt.get("change_amt", 0),
            "change_pct":   rt.get("change_pct", 0),
            "turnover":     rt.get("turnover", 0),
            "volume_ratio": rt.get("volume_ratio", 0),
            "amplitude":    rt.get("amplitude", 0),
            "amount_yi":    rt.get("amount_yi", 0),
            "mcap_yi":      rt.get("mcap_yi", 0),
            "pe_ttm":       rt.get("pe_ttm", 0),
            "zt_today":     u.get("zt_today", False),
            "zt_recent":    u.get("zt_recent", 0),
        })

    # 没 filter 时,按成交额预排
    if not has_filter:
        rows.sort(key=lambda r: r.get("amount_yi", 0), reverse=True)

    # 主资金: 走 _cache_fund (60s TTL),poller 可选预热。R1 不阻塞网络。
    if with_fund and rows:
        fund_codes = [r["code"] for r in rows[:fetch_n]]
        fund_map = _read_fund_cache(fund_codes)
        for r in rows:
            r["main_fund_inflow_wan"] = fund_map.get(r["code"], 0)

    # R1-perf (2026-07-15): 无 filter 路径也批量 sector 富集,避免逐行 _safe_get_sector (150+ 次磁盘读)
    if not has_filter and rows:
        row_codes = [r["code"] for r in rows]
        sec_map = _enrich_sector_bulk(row_codes)

    # 构造 items (合并 sec_map + rt)
    items: list[dict] = []
    for r in rows:
        c = r["code"]
        sec = sec_map.get(c) or {}
        tax = sec.get("taxonomy") or {}
        ai_tags = sec.get("ai_tags") or {}
        items.append({
            "code":   c,
            "name":   r.get("name", ""),
            "price":         r.get("price", 0),
            "change_amt":    r.get("change_amt", 0),
            "change_pct":    r.get("change_pct", 0),
            "turnover":      r.get("turnover", 0),
            "volume_ratio":  r.get("volume_ratio", 0),
            "amplitude":     r.get("amplitude", 0),
            "amount_yi":     r.get("amount_yi", 0),
            "mcap_yi":       r.get("mcap_yi", 0),
            "pe_ttm":        r.get("pe_ttm", 0),
            "main_fund_inflow_wan": r.get("main_fund_inflow_wan", 0),
            "zt_today":      r.get("zt_today", False),
            "zt_recent":     r.get("zt_recent", 0),
            "domain":        list(ai_tags.get("labels") or []),
            "taxonomy": {
                "l1":        tax.get("level1_cluster", "") or "",
                "l1_color":  tax.get("cluster_color", "#888"),
                "l2":        tax.get("level2_sw", "") or "",
                "l3":        tax.get("level3_chain", "") or "",
                "l3_source": tax.get("source", ""),
                "l4":        list(tax.get("level4_subconcept") or []),
                "role":      tax.get("role", "") or "",
            },
        })

    # 排序
    key_fn = _sort_key_fn(sort)
    if key_fn:
        items.sort(key=key_fn, reverse=(order != "asc"))

    # R16: 不再截断到 target_limit — 全量入缓存,offset 在缓存里切
    # 但 items 仍可能超过 fetch_n (rows 没截),截到 fetch_n 防 OOM
    items = items[:fetch_n]

    total_rt_ok = sum(1 for r in rt_map.values() if r.get("_ok"))
    total_candidates = len(rows)
    total_universe = len(universe)

    # R16: 缓存整个排序后 list,供后续分页切片
    # R-2026-07-16: stats — 一次计算所有 KPI (up/down/limit_up/资金流入/成交额/市值/平均涨幅),
    #               缓存到 cache_entry['stats'], 后续分页切片直接复用
    # R1-25 (2026-07-17): stats 现在覆盖**全 fetch_n** (realtime 拿到的所有股票, 2000 只),
    #               而不是只覆盖 page-size=30 的可见行。覆盖比例 stats_source_count/5529
    #               给前端透明披露。
    stats_base = _aggregate_items(items) if items else dict(_EMPTY_STATS)
    # R1-25: 把 universe 全量数放进去,前端能显示"X / Y 已聚合"
    stats = dict(stats_base)
    stats["stats_total_count"] = len(universe) if not has_filter else len(candidate_codes)
    # R1-25: 把 limit_up 数 (来自 universe.zt_today) 用 filter 后的 candidate_codes 重算
    # 无 filter = 全 universe 5529;有 filter = 175 (电子板块) 等,语义清晰
    if has_filter and candidate_codes:
        stats["limit_up"] = sum(1 for c in candidate_codes if (universe.get(c) or {}).get("zt_today"))
    elif universe:
        stats["limit_up"] = sum(1 for u in universe.values() if u.get("zt_today"))
    if total_rt_ok > 0:
        _BOARD_CACHE[cache_key] = {
            "data": items,
            "ts":   time.time(),
            "total_universe":  total_universe,
            "total_candidates": total_candidates,
            "stats": stats,
            "stats_total_count": stats.get("stats_total_count", 0),
        }

    # 本次请求的页 = items[offset:offset+ps]
    page = items[offset:offset + ps]
    total_available = len(items)
    has_more = (offset + len(page)) < total_available

    return {
        "items":           page,
        "count":           len(page),
        "offset":          offset,
        "next_offset":     offset + len(page),
        "has_more":        has_more,
        "total_available": total_available,
        "ts":              time.time(),
        "took_ms":         int((time.time() - t0) * 1000),
        "sort":            sort,
        "order":           order,
        "cache_hit":       False,
        "filters_used": {
            "l1": l1_list, "l2": l2_list, "l3": l3_list, "l4": l4_list,
            "domain": domain_list, "limit": ps,
        },
        "total_rt_ok":     total_rt_ok,
        "total_candidates": total_candidates,
        "total_universe":  total_universe,
        "stats":           stats,
    }


def _safe_get_sector(code: str) -> dict | None:
    try:
        from . import sector_classify as _sc
        return _sc.get_sector(code, force_refresh=False)
    except Exception:
        return None


def _sort_key_fn(sort: str):
    # R26-60 (2026-07-17): 全列可排序 — code/name/PE/L1/L2/L3/L4 都支持
    if sort == "change_pct":
        return lambda r: r.get("change_pct", 0)
    if sort == "change_amt":                                       # R18: 涨跌额排序
        return lambda r: r.get("change_amt", 0)
    if sort == "turnover":
        return lambda r: r.get("turnover", 0)
    if sort == "amount":
        return lambda r: r.get("amount_yi", 0)
    if sort == "volume_ratio":
        return lambda r: r.get("volume_ratio", 0)
    if sort == "main_fund_inflow":
        return lambda r: r.get("main_fund_inflow_wan", 0)
    if sort == "mcap":
        return lambda r: r.get("mcap_yi", 0)
    if sort == "amplitude":
        return lambda r: r.get("amplitude", 0)
    # R26-60: 字符串列排序 (code 用字符串字典序; name 中文用 Unicode)
    if sort == "code":
        return lambda r: str(r.get("code", "") or "")
    if sort == "name":
        return lambda r: str(r.get("name", "") or "")
    if sort == "pe":
        def _pe_key(r):
            v = r.get("pe_ttm", 0)
            try:
                fv = float(v)
            except (TypeError, ValueError):
                fv = 0.0
            # 负 PE (亏损) 排到末尾:用 (is_negative, abs) 元组
            return (fv < 0, abs(fv))
        return _pe_key
    if sort == "zt_today":
        def _zt_key(r):
            # 涨停股在前, 排序字段 (1, code) 让并列按代码稳定排序
            return (not bool(r.get("zt_today")), str(r.get("code", "")))
        return _zt_key
    # R26-60: taxonomy 列 (l1/l2/l3/l4) — 字符串排序,空值排最后
    def _tax(sort_key):
        def _k(r):
            tax = r.get("taxonomy") or {}
            v = tax.get(sort_key) or ""
            if isinstance(v, list):
                v = v[0] if v else ""
            return (v == "", str(v))
        return _k
    if sort in ("l1", "l2", "l3"):
        return _tax(sort)
    return None


# ═════════════════════════════════════════════════════════════════
# Batch 2 R53 (2026-07-19): APIRouter — 从 server.py 拆分到 domain
# ═════════════════════════════════════════════════════════════════
router = APIRouter(prefix="/api/all_stocks", tags=["stock"])


@router.get("/board")
async def board(
    limit:   int   = Query(0, ge=0, le=500),
    page_size: int = Query(0, ge=0, le=500),
    offset:  int   = Query(0, ge=0, le=100_000),
    l1:      str   = "",
    l2:      str   = "",
    l3:      str   = "",
    l4:      str   = "",
    domain:  str   = "",
    sort:    str   = "amount",
    order:   str   = "desc",
    with_fund: bool = True,
    preset:  str   = "",
    signal:  str   = "",
    min_pct: str   = "",
    max_pct: str   = "",
    min_turnover: str = "",
    min_amount_yi: str = "",
    min_mcap_yi: str = "",
    max_mcap_yi: str = "",
    min_vr:  str   = "",
):
    """个股全景快照 — 支持 offset/page_size 无限滚动 + 跨模块筛选。"""
    from .server import envelope, to_thread

    def _load():
        try:
            return board_snapshot(
                limit=int(limit or 0),
                page_size=int(page_size or 0),
                offset=int(offset or 0),
                l1=l1 or None,
                l2=l2 or None,
                l3=l3 or None,
                l4=l4 or None,
                domain=domain or None,
                sort=sort or "amount",
                order=order or "desc",
                with_fund=bool(with_fund),
                preset=preset or "",
                signal=signal or "",
                min_pct=min_pct or None,
                max_pct=max_pct or None,
                min_turnover=min_turnover or None,
                min_amount_yi=min_amount_yi or None,
                min_mcap_yi=min_mcap_yi or None,
                max_mcap_yi=max_mcap_yi or None,
                min_vr=min_vr or None,
            )
        except Exception as e:
            log.warning(f"board 失败: {e}")
            return {"items": [], "count": 0, "error": str(e), "has_more": False, "next_offset": 0, "total_available": 0}

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=15)
    except asyncio.TimeoutError:
        log.warning(f"board 超时 15s (ps={page_size}, off={offset}, l3={l3})")
        return envelope(
            error="批量行情超时 15s — 请缩小筛选或稍后再试",
            data={"items": [], "count": 0, "has_more": False, "next_offset": 0, "total_available": 0, "filters_used": {}},
        )
    return envelope(data=result)


@router.get("/filters")
async def filters():
    """4 层 + 领域 filter 全集 — 给前端 dropdown"""
    from .server import envelope, to_thread

    def _load():
        return filters_full()

    try:
        result = await asyncio.wait_for(to_thread(_load), timeout=4)
    except asyncio.TimeoutError:
        return envelope(error="filter 加载超时", data={"clusters": [], "industries": [], "chains": [], "l4": []})
    return envelope(data=result)