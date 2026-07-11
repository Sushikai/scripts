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
    """近 5 个交易日内,该股的涨停记录"""
    code = str(code).strip().zfill(6)
    records = []
    today = datetime.now()
    # 向后扫 10 天(去掉周末/节假日),找到该股所有的涨停日
    for i in range(1, 11):
        d = today - timedelta(days=i)
        d_str = _to_ymd(d)
        rows = _fetch_zt_pool(d_str)
        for row in rows:
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


def _fetch_sector_zt_today(sector_name: str) -> List[Dict]:
    """今日板块内所有涨停股（板块名 = 申万一级 或 概念）

    输入：板块名（中文，宽松匹配）
    优先匹配"所属行业"字段；用同一天的数据
    """
    if not sector_name:
        return []
    # 取最近一个有数据的日期（避免跨天）
    for offset in range(0, 4):
        d = datetime.now() - timedelta(days=offset)
        d_str = _to_ymd(d)
        rows = _fetch_zt_pool(d_str)
        if rows:
            # 取数据所在的真实日期（首次拉到非空的那天）
            data_date = d_str
            break
    else:
        return []

    matched = []
    for row in rows:
        sec = row.get("所属行业", "")
        # 宽松匹配：板块名包含 or 被包含
        if sector_name and (sector_name in sec or sec in sector_name):
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
    code = str(code).strip().zfill(6)

    # 1. 今日涨停状态(用昨天的 data 因为下午拉到的最稳)
    today_str = _to_ymd(datetime.now() - timedelta(days=1))
    zt_today_rows = _fetch_zt_pool(today_str)
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
    sector_zt = []
    if sector_name:
        sector_zt = _fetch_sector_zt_today(sector_name)

    # 4. 相关概念涨停聚合 (4 层 taxonomy: L3 产业链 / L4 细分)
    #    例: 半导体股 → L3 "存储"/"设备" / L4 "HBM"/"光刻机" 各自涨停多少只
    related_concepts: List[Dict[str, Any]] = []
    try:
        from .sector_taxonomy import classify_taxonomy
        tax = classify_taxonomy(code) or {}
        target_concepts = set()
        for lv in (tax.get("l3_chain") or []):
            target_concepts.add(lv)
        for lv in (tax.get("l4_tags") or []):
            target_concepts.add(lv)
        if target_concepts:
            pool_today = zt_today_rows  # 复用
            cnt: Dict[str, int] = {}
            samples: Dict[str, List[str]] = {}
            for row in pool_today:
                code_r = str(row.get("代码", "")).zfill(6)
                if not code_r:
                    continue
                try:
                    tax_r = classify_taxonomy(code_r) or {}
                except Exception:
                    continue
                tags = set()
                for lv in (tax_r.get("l3_chain") or []):
                    tags.add(lv)
                for lv in (tax_r.get("l4_tags") or []):
                    tags.add(lv)
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
                    "level": "L3" if c in (tax.get("l3_chain") or []) else "L4",
                    "zt_count": n,
                    "samples": samples.get(c, []),
                })
            related_concepts.sort(key=lambda x: -x["zt_count"])
    except Exception as e:
        log.debug(f"related_concepts 聚合失败 {code}: {e}")

    # 5. 总结人话
    summary = _summarize(code, today_info, recent_5d, sector_zt)

    return {
        "code": code,
        "today": today_info,
        "recent_5d": recent_5d[:5],
        "sector_today": sector_zt[:10],  # 最多返回 10 条避免过大
        "related_concepts": related_concepts[:8],  # 最多 8 个相关概念
        "sector_name_used": sector_name,
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


if __name__ == "__main__":
    # 烟雾测试
    result = get_limit_up_context("002747", "机械设备")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
