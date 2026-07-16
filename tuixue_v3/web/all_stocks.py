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

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

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


def _bulk_rt(codes: list[str], overall_timeout: float = 12.0) -> dict[str, dict]:
    """并发拉所有 code 的实时行情，整体超时。返回时已完成的入结果，未完成的标 _ok=False。

    2026-07-12 Round 6: 早退 — 一旦命中 ≥97% 立即返回,不等 2~3 只死代码(北交所退市股)
    在多源重试链上耗满整个 overall_timeout。poller 缓存命中的会秒回。
    """
    from concurrent.futures import as_completed
    if not codes:
        return {}
    out: dict[str, dict] = {c: {"_ok": False} for c in codes}
    futs = {_EXECUTOR.submit(_safe_rt, c): c for c in codes}
    total = len(codes)
    early_exit_at = max(total - 3, int(total * 0.97))  # 命中这么多就够,不等尾部死股
    done = 0
    try:
        for f in as_completed(futs, timeout=overall_timeout):
            try:
                r = f.result(timeout=0.1) or {}
                out[futs[f]] = r
                if r.get("_ok"):
                    done += 1
            except Exception:
                pass
            if done >= early_exit_at:
                break
    except Exception:
        pass
    return out


def _bulk_fund(codes: list[str], overall_timeout: float = 10.0) -> dict[str, float]:
    from concurrent.futures import as_completed
    if not codes:
        return {}
    out: dict[str, float] = {c: 0.0 for c in codes}
    futs = {_EXECUTOR.submit(_safe_fund, c): c for c in codes}
    try:
        for f in as_completed(futs, timeout=overall_timeout):
            try:
                r = f.result(timeout=0.1) or 0.0
                out[futs[f]] = r
            except Exception:
                pass
    except Exception:
        pass
    return out


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
    """从过滤+排序后的 items 一次算出 KPI hero 所需的全部聚合指标。

    数值类 (main_fund_inflow_wan / amount_yi / mcap_yi) 直接累加;
    涨跌家数按 change_pct 严格分桶 (>=0.01 算涨, <=-0.01 算跌, 其余算平);
    涨停 = zt_today=True (来自 universe 涨停池);
    分位 (median) 用 1 次 sort (items 通常 ≤ 2000, 完全可接受)。
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
        "total_main_fund_wan": sum_fund,
        "total_amount_yi":     sum_amount,
        "total_mcap_yi":       sum_mcap,
        "avg_change_pct":      avg,
        "median_change_pct":   median,
        "max_change_pct":      pcts[-1] if pcts else 0.0,
        "min_change_pct":      pcts[0] if pcts else 0.0,
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

    rt_map = _bulk_rt(rt_codes, overall_timeout=10.0)
    log.info(f"all_stocks: realtime 命中 {sum(1 for r in rt_map.values() if r.get('_ok'))}/{len(rt_codes)}")

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

    # 主资金 (按需,只对最终 N 只) — 2026-07-12: 沙箱网络挂时,超时 10→5s 防止整体卡死
    if with_fund and rows:
        # R16: 拉 fetch_n 而非 target_limit, 覆盖 offset 范围内全部可见行
        fund_codes = [r["code"] for r in rows[:fetch_n]]
        fund_map = _bulk_fund(fund_codes, overall_timeout=5.0)
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
    stats = _compute_board_stats(items) if items else _EMPTY_STATS
    if total_rt_ok > 0:
        _BOARD_CACHE[cache_key] = {
            "data": items,
            "ts":   time.time(),
            "total_universe":  total_universe,
            "total_candidates": total_candidates,
            "stats": stats,
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
    return None