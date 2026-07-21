"""
web/limit_up_context.py - 个股连板 & 板块涨停上下文

数据源:akshare.stock_zt_pool_em(涨停池 + 连板数字段)
       akshare.stock_zt_pool_strong_em(强势股)

输入:股票代码(如 '002747')
输出:
  - today: 今日是否涨停、连板数
  - recent_5d: 近 5 个交易日的涨停记录(去 zt_pool 历史)
  - sector: 该股所属板块今日所有涨停股清单(需要 sector 信息)
  - sector_consecutive: 板块内连板股清单
  - summary: 人话总结(板块热度/连板梯队强度)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path
import json

import requests as _requests

log = logging.getLogger("tuixue_v3.web.limit_up_ctx")

# 复用 v3 项目已有的封装
try:
    import akshare as ak
except ImportError:
    log.warning("akshare 未安装, limit_up_context 功能受限")

# 简单缓存(同进程内)
_CACHE: Dict[str, Any] = {}
_CACHE_TS: Dict[str, float] = {}
_CACHE_TTL = 300  # 5 分钟


def _cache_get(key: str):
    ts = _CACHE_TS.get(key, 0)
    if datetime.now().timestamp() - ts < _CACHE_TTL:
        return _CACHE.get(key)
    return None


def _cache_set(key: str, value):
    _CACHE[key] = value
    _CACHE_TS[key] = datetime.now().timestamp()


def _to_ymd(d) -> str:
    """datetime/str → YYYYMMDD"""
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "").replace("/", "")[:8]


def _fetch_zt_pool(date: str) -> List[Dict]:
    """拉某天的涨停池(YYYYMMDD 格式)"""
    key = f"zt_pool:{date}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        df = ak.stock_zt_pool_em(date=date)
        rows = df.to_dict("records") if len(df) > 0 else []
    except Exception as e:
        log.warning(f"zt_pool {date} 拉取失败: {e}")
        rows = []
    _cache_set(key, rows)
    return rows


def _fetch_recent_5d_zt_for_code(code: str) -> List[Dict]:
    """近 5 个交易日内,该股的涨停记录
    优化 (2026-07-15): 旧版循环 10 天找 5 个涨停,akshare 每 ~100ms 一次,冷启总 1.4s+;
    新版并行拉最近 5 个交易日涨停池 + 早停 (找到 5 个 OR 扫完 5 个交易日即停)。
    """
    code = str(code).strip().zfill(6)
    today = datetime.now()
    # 并行拉最近 5 个交易日涨停池 — ThreadPoolExecutor 防 akshare 阻塞
    from concurrent.futures import ThreadPoolExecutor
    dates: List[str] = []
    rows_by_date: Dict[str, List[Dict]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {}
        for i in range(1, 8):  # 8 天日历日 = 5 个交易日 (扣周末)
            d = today - timedelta(days=i)
            d_str = _to_ymd(d)
            dates.append(d_str)
            futs[pool.submit(_fetch_zt_pool, d_str)] = d_str
        for fut, d_str in futs.items():
            try:
                rows_by_date[d_str] = fut.result(timeout=3.0) or []
            except Exception:
                rows_by_date[d_str] = []

    records = []
    for d_str in dates:
        for row in rows_by_date.get(d_str, []):
            if str(row.get("代码", "")).zfill(6) == code:
                records.append({
                    "date": d_str,
                    "连板数": row.get("连板数", 0),
                    "封单金额": row.get("封板资金", 0),
                    "首次封板时间": row.get("首次封板时间", ""),
                    "所属行业": row.get("所属行业", ""),
                    "涨停统计": row.get("涨停统计", ""),
                })
        if len(records) >= 5:
            break
    return sorted(records, key=lambda x: x["date"], reverse=True)


def _fetch_sector_zt_today(sector_name: str, from_rows: List[Dict] = None, from_date: str = "") -> List[Dict]:
    """今日板块内所有涨停股（板块名 = 申万一级 或 概念）

    输入：板块名（中文，宽松匹配）
    优先匹配"所属行业"字段；用同一天的数据

    Args:
      sector_name: 板块名
      from_rows:   外部传入已拉到的涨停池 (保证与 today_info 同一天, 2026-07-15 修复跨天)
      from_date:   对应的日期 (YYYYMMDD)
    """
    if not sector_name:
        return []
    if from_rows is not None:
        rows = from_rows
        data_date = from_date
    else:
        # 取最近一个有数据的日期（避免跨天）
        for offset in range(0, 4):
            d = datetime.now() - timedelta(days=offset)
            d_str = _to_ymd(d)
            rows = _fetch_zt_pool(d_str)
            if rows:
                data_date = d_str
                break
        else:
            return []

    # 添加 SW_31 归一化: 把任意来源的行业名统一到申万一级
    _normalized_sector = None
    try:
        from .sector_classify import normalize_to_sw as _nsw
        _normalized_sector = _nsw(sector_name)
    except Exception:
        pass

    matched = []
    for row in rows:
        sec = row.get("所属行业", "")
        # 策略: 优先 SW_31 归一化后精确匹配, 再退到宽松子串匹配
        is_match = False
        if sector_name and sec:
            if _normalized_sector:
                try:
                    norm_sec = _nsw(sec)
                    if norm_sec and norm_sec == _normalized_sector:
                        is_match = True
                except Exception:
                    pass
            if not is_match:
                is_match = (sector_name in sec or sec in sector_name)
        # 2026-07-17: 修 — 之前 is_match 算了但 matched.append 无条件执行,
        # 导致任何 sector_name 一进, 全涨停池都进 sector_today,板块龙头错乱.
        if is_match:
            matched.append({
                "日期": data_date,
                "代码": row.get("代码", ""),
                "名称": row.get("名称", ""),
                "连板数": row.get("连板数", 0),
                "封单金额": row.get("封板资金", 0),
                "涨跌幅": row.get("涨跌幅", 0),
                "所属行业": sec,
            })
    # 按连板数倒序
    return sorted(matched, key=lambda x: -x.get("连板数", 0))


def get_limit_up_context(code: str, sector_name: str = None) -> Dict[str, Any]:
    """单只股票的连板 & 板块涨停上下文

    Args:
        code: 6 位股票代码
        sector_name: 该股所属板块(如 '机械设备');None 时只能用行业匹配

    Returns:
        {
          'code': '002747',
          'today': {'is_zhangting': True/False, '连板数': 3, '涨停时间': '09:30', ...} or None,
          'recent_5d': [ {date, 连板数, 涨停统计, ...}, ... ],
          'sector_today': [板块内今日所有涨停股清单],
          'summary': '人话',
        }
    """
    # 2026-07-17 性能修复: 入参级缓存 (避免个股页 2-3 次重复拉涨停池)
    # 之前 _CACHE 仅缓存 _fetch_zt_pool(date),但 get_limit_up_context 每次都跑 5 个交易日
    # 并行 akshare + sector 匹配 + L3/L4 cross-tag + leadership 算分,2-5s/次,股票切股时严重卡。
    cache_key = f"{str(code).strip().zfill(6)}|{(sector_name or '').strip()}"
    cached = _CTX_CACHE.get(cache_key)
    if cached and (datetime.now().timestamp() - cached["_ts"]) < _CTX_CACHE_TTL:
        return cached["data"]

    result = _get_limit_up_context_impl(code, sector_name)
    _CTX_CACHE[cache_key] = {"data": result, "_ts": datetime.now().timestamp()}
    return result


# 入参级缓存 (2026-07-17): 单只股票 + 板块组合 5 分钟内复用
_CTX_CACHE: Dict[str, Any] = {}
_CTX_CACHE_TTL = 300  # 5 分钟 (与 _CACHE_TTL 对齐)


def _get_limit_up_context_impl(code: str, sector_name: str = None) -> Dict[str, Any]:
    """get_limit_up_context 真实实现 (无缓存)"""
    code = str(code).strip().zfill(6)

    # 1. 今日涨停状态 — 优先今天的数据, 没拿到再 fallback 到昨天
    #    2026-07-15 修复: 旧版 hard-code 昨天, 但 sector_today 走"最近非空", 经常跨天,
    #    导致 today_info.streak 与 sector_max_streak 不在同一交易日, 龙头判断错位。
    today_str = None
    zt_today_rows = []
    for offset in range(0, 5):  # 0=今天, 1=昨天, ...最多回退 5 天找涨停池
        d = datetime.now() - timedelta(days=offset)
        d_str = _to_ymd(d)
        rows = _fetch_zt_pool(d_str)
        if rows:
            today_str = d_str
            zt_today_rows = rows
            break
    today_info = None
    for row in zt_today_rows:
        if str(row.get("代码", "")).zfill(6) == code:
            today_info = {
                "date": today_str,
                "is_zhangting": True,
                "连板数": row.get("连板数", 0),
                "封单金额": row.get("封板资金", 0),
                "首次封板时间": row.get("首次封板时间", ""),
                "最后封板时间": row.get("最后封板时间", ""),
                "炸板次数": row.get("炸板次数", 0),
                "涨停统计": row.get("涨停统计", ""),
                "所属行业": row.get("所属行业", ""),
                "成交额": row.get("成交额", 0),
                "流通市值": row.get("流通市值", 0),
            }
            break

    # 2. 近 5 个交易日涨停记录
    recent_5d = _fetch_recent_5d_zt_for_code(code) if today_info else []

    # 3. 板块涨停清单(需要在 ai_analysis 外部传入 sector)
    #    2026-07-15: 传入 today 涨停池 + 日期, 保证与 today_info 同一天,
    #    避免 sector_max_streak 与 today.streak 跨天错位 (如 today=2板/昨天, sector=3板/今天)。
    sector_zt = []
    if sector_name:
        sector_zt = _fetch_sector_zt_today(sector_name, from_rows=zt_today_rows, from_date=today_str or "")

    # 4. 相关概念涨停聚合 (4 层 taxonomy: L3 产业链 / L4 细分)
    #    例: 半导体股 → L3 "存储"/"设备" / L4 "HBM"/"光刻机" 各自涨停多少只
    related_concepts: List[Dict[str, Any]] = []
    tax: Dict[str, Any] = {}
    try:
        from .sector_taxonomy import classify_taxonomy
        from .concept_taxonomy import match_concepts as _match_concepts
        # 2026-07-15: 把 sector_name(sw) 传进去, 让 L1/L2 集群归类生效
        # 否则 600519 → L1="其他" / L2="", fallback 失效
        tax = classify_taxonomy(code, sector_name) or {}
        target_concepts = set()
        lv3_str = tax.get("level3_chain") or ""
        if lv3_str:
            target_concepts.add(lv3_str)
        for lv in (tax.get("level4_subconcept") or []):
            target_concepts.add(lv)
        # 补充 concept_taxonomy 概念: 用主叫代码的名称来匹配概念关键词
        try:
            stock_name = next((r.get("名称", "") for r in zt_today_rows if str(r.get("代码", "")).zfill(6) == code), "")
            if stock_name:
                for l3, l4s in _match_concepts(stock_name):
                    target_concepts.add(l3)
                    for l4 in l4s:
                        target_concepts.add(l4)
        except Exception:
            pass
        if target_concepts:
            pool_today = zt_today_rows  # 复用
            cnt: Dict[str, int] = {}
            samples: Dict[str, List[str]] = {}
            for row in pool_today:
                code_r = str(row.get("代码", "")).zfill(6)
                if not code_r:
                    continue
                try:
                    tax_r = classify_taxonomy(code_r, None) or {}
                except Exception:
                    continue
                tags = set()
                lv3 = tax_r.get("level3_chain") or ""
                if lv3: tags.add(lv3)
                for lv in (tax_r.get("level4_subconcept") or []):
                    tags.add(lv)
                # 补充 concept_taxonomy 标签
                try:
                    row_name = row.get("名称", "")
                    if row_name:
                        for cl3, cl4s in _match_concepts(row_name):
                            tags.add(cl3)
                            for cl4 in cl4s:
                                tags.add(cl4)
                except Exception:
                    pass
                if not tags:
                    continue
                matched = tags & target_concepts
                if code_r == code:
                    matched = target_concepts  # 当前股自身也算入每个概念
                for c in matched:
                    cnt[c] = cnt.get(c, 0) + 1
                    samples.setdefault(c, [])
                    if len(samples[c]) < 3:
                        samples[c].append(f"{code_r} {row.get('名称', '')}".strip())
            # 当前股本身的概念都按 1 计 (上面已经处理)
            for c, n in cnt.items():
                related_concepts.append({
                    "concept": c,
                    "level": "L3" if c == (tax.get("level3_chain") or "") else "L4",
                    "zt_count": n,
                    "samples": samples.get(c, []),
                })
            related_concepts.sort(key=lambda x: -x["zt_count"])
    except Exception as e:
        log.debug(f"related_concepts 聚合失败 {code}: {e}")

    # 5. 总结人话
    summary = _summarize(code, today_info, recent_5d, sector_zt)

    # 6. 股性 (题材活跃度 + 该股对应多个 L3/L4 概念)
    stock_nature = _compute_stock_nature(code, today_info, recent_5d)

    # 7. 龙头判定 (当前股在板块内的位置 + 板块龙头股)
    leadership = _compute_leadership(code, today_info, sector_zt)

    # 8. 板块联动 fallback 用的 L1 / L2 分类 (从 tax dict 直接拿) — 2026-07-15
    try:
        taxonomy_l1 = (tax.get("level1_cluster") or "").strip()
        taxonomy_l2 = (tax.get("level2_sw") or sector_name or "").strip()
        taxonomy_color = (tax.get("cluster_color") or "#888").strip()
    except Exception:
        taxonomy_l1 = taxonomy_l2 = taxonomy_color = ""

    return {
        "code": code,
        "today": today_info,
        "recent_5d": recent_5d[:5],
        "sector_today": _enrich_sector_zt_taxonomy(sector_zt[:10]),
        "related_concepts": related_concepts[:8],  # 最多 8 个相关概念
        "sector_name_used": sector_name,
        "taxonomy_l1": taxonomy_l1,
        "taxonomy_l2": taxonomy_l2,
        "taxonomy_color": taxonomy_color,
        "stock_nature": stock_nature,
        "leadership": leadership,
        "summary": summary,
    }


def _summarize(code: str, today: Optional[dict], recent_5d: List[dict], sector_zt: List[dict]) -> str:
    """人话总结"""
    parts = []

    if today:
        lb = today.get("连板数", 0)
        if lb >= 5:
            parts.append(f"🔥 {lb}连板, 高位龙头, 注意分歧风险")
        elif lb >= 3:
            parts.append(f"🔥 {lb}连板, 强势龙头")
        elif lb >= 2:
            parts.append(f"⚡ 2连板, 次新龙头候选")
        elif lb == 1:
            parts.append(f"✓ 首板涨停")
    else:
        parts.append(f"今昨两日未涨停")

    if recent_5d:
        n = len(recent_5d)
        parts.append(f"近 5 个交易日累计涨停 {n} 次")

    if sector_zt:
        n_sector = len(sector_zt)
        n_lianban = sum(1 for x in sector_zt if x.get("连板数", 0) >= 2)
        if n_sector >= 5:
            parts.append(f"🔥 板块当日涨停 {n_sector} 只, 连板 {n_lianban} 只, 板块热度强")
        elif n_sector >= 2:
            parts.append(f"⚡ 板块当日涨停 {n_sector} 只, 热度中等")
        elif n_sector >= 1:
            parts.append(f"板块当日涨停 {n_sector} 只, 热度一般")
        else:
            parts.append(f"板块今日无涨停")

    return " | ".join(parts) if parts else "暂无数据"


def _compute_stock_nature(code: str, today: Optional[dict], recent_5d: List[dict]) -> dict:
    """股性(题材活跃度) — 一支股对应多个概念,这里的 tier 给一个综合档位。

    数据:
      - recent_5d: 近 5 个交易日涨停记录 (最多 5 条)
      - today: 当日涨停 + 连板数 (可能 None)

    评分 (0-100):
      - 涨停次数权重 60%: 5d 内涨停数 n_5d
        - n=0 → 0; n=1 → 30; n=2 → 50; n=3 → 65; n=4 → 75; n=5+ → 85
      - max 连板权重 40%: max_streak (today + recent_5d)
        - 1 → 0; 2 → 10; 3 → 20; 4 → 30; 5+ → 40

    分档:
      - 妖股  (≥ 75)  连板 ≥ 4 或 5d ≥ 4 涨停
      - 活跃  (≥ 50)  连板 ≥ 2 或 5d ≥ 2 涨停
      - 一般  (≥ 20)  当日首板 或 5d 仅 1 涨停
      - 死股  (< 20)  5d 无涨停且当日未涨停

    返回里同时塞入该股的 L3 产业链 + L4 细分概念(一支股可对应多个),
    前端直接 chip 化渲染。
    """
    code = str(code).strip().zfill(6)
    today_lb = (today or {}).get("连板数", 0) or 0
    zt_5d = len(recent_5d or [])
    recent_streaks = [(r or {}).get("连板数", 0) or 0 for r in (recent_5d or [])]
    max_streak = max([today_lb] + recent_streaks) if (today_lb or recent_streaks) else 0

    # 5d 涨停次数得分
    if   zt_5d == 0: sc_n = 0
    elif zt_5d == 1: sc_n = 30
    elif zt_5d == 2: sc_n = 50
    elif zt_5d == 3: sc_n = 65
    elif zt_5d == 4: sc_n = 75
    else:            sc_n = 85

    # 连板最高度得分
    if   max_streak >= 5: sc_s = 40
    elif max_streak >= 4: sc_s = 30
    elif max_streak >= 3: sc_s = 20
    elif max_streak >= 2: sc_s = 10
    else:                 sc_s = 0

    score = sc_n + sc_s

    if   score >= 75: tier = "妖股"
    elif score >= 50: tier = "活跃"
    elif score >= 20: tier = "一般"
    else:             tier = "死股"

    # 拉该股的概念标签 (L3 产业链 / L4 细分 / L2 申万)
    concepts: List[Dict[str, str]] = []
    try:
        from .sector_taxonomy import classify_taxonomy
        tax = classify_taxonomy(code, None) or {}
        sw  = tax.get("level2_sw") or ""
        l3  = tax.get("level3_chain") or ""
        l4_list = list(tax.get("level4_subconcept") or [])
        cluster = tax.get("level1_cluster") or ""
        if sw:
            concepts.append({"name": sw, "level": "L2", "role": "申万"})
        if l3:
            concepts.append({"name": l3, "level": "L3", "role": "产业链"})
        for tag in l4_list:
            concepts.append({"name": tag, "level": "L4", "role": "细分"})
    except Exception as e:
        log.debug(f"stock_nature 概念拉取失败 {code}: {e}")

    # 涨停统计字段 (e.g. "11/7") — 来自 akshare 当日字段,只当日有
    today_ztj = (today or {}).get("涨停统计", "") or ""

    # reason 文案
    if today_lb >= 2:
        reason = f"今日 {today_lb} 板, 近 5d 涨停 {zt_5d} 次"
    elif today:
        reason = f"今日首板, 近 5d 涨停 {zt_5d} 次"
    elif zt_5d > 0:
        reason = f"今日未涨停, 近 5d 涨停 {zt_5d} 次"
    else:
        reason = "近 5 个交易日无涨停"

    return {
        "tier":         tier,
        "score":        score,
        "zt_count_5d":  zt_5d,
        "max_streak":   max_streak,
        "today_streak": today_lb,
        "today_ztj":    today_ztj,
        "concepts":     concepts,
        "concept_count": len(concepts),
        "reason":       reason,
    }


def _enrich_sector_zt_taxonomy(rows: List[dict]) -> List[dict]:
    """给板块当日涨停列表的每只股票补 L2/L3/L4 产业链标签 (2026-07-17 修)

    之前前端拿不到概念是因为 sector_today 只返行业名 (所属行业), 没有 L2/L3/L4。
    现在后端用 classify_taxonomy 给每只股打 4 层标签, 前端直接 chip 渲染。
    classify_taxonomy 是 in-memory dict 查表, 10 只 <5ms, 不影响性能。
    """
    if not rows:
        return rows
    try:
        from .sector_taxonomy import classify_taxonomy
    except Exception:
        return rows
    out = []
    for r in rows:
        code = str(r.get("代码") or "").strip().zfill(6)
        sec = r.get("所属行业") or ""
        tax = classify_taxonomy(code, sec) or {}
        out.append({
            **r,
            "taxonomy": {
                "level1_cluster":    tax.get("level1_cluster", ""),
                "level2_sw":         tax.get("level2_sw", sec),
                "level3_chain":      tax.get("level3_chain", ""),
                "level4_subconcept": list(tax.get("level4_subconcept") or []),
                "cluster_color":     tax.get("cluster_color", "#888"),
            },
        })
    return out


def _compute_leadership(code: str, today: Optional[dict], sector_zt: List[dict]) -> dict:
    """龙头判定 — 当前股在板块内的位置 + 板块龙头股信息。

    Args:
      code: 当前股代码 (用于排除自己,定位板块龙头)
      today: 当前股今日涨停详情 (None = 未涨停)
      sector_zt: 板块内当日所有涨停股清单

    返回:
      {
        role:                高位龙头 | 强势龙头 | 板块龙头 | 次新龙头 | 梯队成员 | 首板 | —
        streak:              int 当前连板
        sector_max_streak:   int 板块内最高连板
        is_top_in_sector:    bool 是否当前板块最高连板
        sector_zt_count:     int 板块涨停股数
        sector_leader:       {code, name, streak} 板块内最高连板的股 (排除当前股)
        reason:              str
      }
    """
    code = str(code or "").strip().zfill(6)
    streak = (today or {}).get("连板数", 0) or 0
    sector_leader = None
    if sector_zt:
        sector_max_streak = max((x.get("连板数", 0) or 0) for x in sector_zt)
        sector_zt_count   = len(sector_zt)
        # 找板块龙头 = 连板最高(≥2) 且不是当前股 (若最高就是当前股,这里返 None,前端用 leader.role 替代)
        candidates = [x for x in sector_zt
                      if (x.get("连板数", 0) or 0) == sector_max_streak
                      and str(x.get("代码", "")).zfill(6) != code
                      and (x.get("连板数", 0) or 0) >= 2]
        if candidates:
            top = candidates[0]
            sector_leader = {
                "code":   str(top.get("代码", "")).zfill(6),
                "name":   top.get("名称", ""),
                "streak": top.get("连板数", 0) or 0,
                "封单金额": top.get("封单金额", 0) or 0,
            }
    else:
        sector_max_streak = 0
        sector_zt_count   = 0
    is_top = (streak > 0 and streak >= sector_max_streak and sector_zt_count > 0)

    if streak >= 5:
        role = "高位龙头"
        reason = f"🔥 {streak} 板, 高位, 板块内最高 {sector_max_streak} 板, 注意分歧风险"
    elif streak >= 3:
        role = "强势龙头"
        reason = f"🔥 {streak} 板, 板块内最高 {sector_max_streak} 板"
    elif streak >= 2:
        if is_top:
            role = "板块龙头"
            detail = f" ({sector_zt_count} 只涨停中)" if sector_zt_count > 1 else ""
            reason = f"👑 {streak} 板, 板块当日最高{detail}"
        else:
            role = "次新龙头"
            reason = f"⚡ {streak} 板, 板块最高 {sector_max_streak} 板"
    elif streak == 1:
        if sector_zt_count >= 5:
            role = "板块联动首板"
            reason = f"✓ 首板, 板块当日 {sector_zt_count} 只涨停, 联动强"
        elif sector_zt_count >= 2:
            role = "板块首板"
            reason = f"✓ 首板, 板块当日 {sector_zt_count} 只涨停"
        else:
            role = "首板"
            reason = "✓ 首板 (板块内无其他涨停)"
    else:
        role = "—"
        reason = "今日未涨停"

    return {
        "role":              role,
        "streak":            streak,
        "sector_max_streak": sector_max_streak,
        "sector_zt_count":   sector_zt_count,
        "is_top_in_sector":  is_top,
        "sector_leader":     sector_leader,
        "reason":            reason,
    }


if __name__ == "__main__":
    # 烟雾测试
    result = get_limit_up_context("002747", "机械设备")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
