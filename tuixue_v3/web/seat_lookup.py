"""
游资席位查询：
- 读取 seat_aliases/known_seats — 走 cache_store (Redis 永久,跨进程共享)
- 拉龙虎榜（akshare），在结果里匹配 known_seats 标记
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any

from .. import cache_store as _cs
from ..cache_store import get_store, K

log = logging.getLogger("tuixue_v3.web.seats")

# 兼容旧逻辑:首次启动若 Redis 没数据,从 data/*.json 灌入一次
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEGACY_ALIASES_FILE = DATA_DIR / "seat_aliases.json"
_LEGACY_KNOWN_FILE = DATA_DIR / "known_seats.json"
_store = get_store()


_ALIASES_CACHE: dict | None = None


def _load_aliases() -> dict:
    """加载 seat_aliases — 优先 Redis,首次 fallback 到 JSON 灌入。"""
    global _ALIASES_CACHE
    if _ALIASES_CACHE is not None:
        return _ALIASES_CACHE

    # 1) Redis 命中 → 解包后回填进程内缓存
    cached = _store.get(K.SEAT_ALIASES)
    if cached and isinstance(cached, dict) and cached.get("_flat") and cached.get("_by_key"):
        _ALIASES_CACHE = {"flat": cached["_flat"], "by_key": cached["_by_key"]}
        return _ALIASES_CACHE

    # 2) 老 JSON 灌入
    if not _LEGACY_ALIASES_FILE.exists():
        _ALIASES_CACHE = {}
        return _ALIASES_CACHE
    try:
        raw = json.loads(_LEGACY_ALIASES_FILE.read_text())
        # 展平 _top / _mid / _seat / _fund → 一张 aliases 表 (alias_name → group_key)
        flat: dict[str, str] = {}  # primary_alias → group_key
        by_key: dict[str, dict] = {}  # group_key → 完整 info(含 keywords)
        for section in ("_top", "_mid", "_seat", "_fund"):
            for gkey, info in (raw.get(section) or {}).items():
                # 加上 _ 前缀防止和真实 key 撞
                store_key = f"{section}.{gkey}"
                by_key[store_key] = info
                # 主别名 + 衍生别名 都映射到 group_key
                pa = info.get("primary_alias", gkey)
                flat[pa] = store_key
                for a in info.get("aliases", []) or []:
                    flat[a] = store_key
        _ALIASES_CACHE = {"flat": flat, "by_key": by_key}
        # 异步写回 Redis (永久) — 下一进程 / 重启后直接命中
        try:
            _store.set(K.SEAT_ALIASES, {"_flat": flat, "_by_key": by_key, "_built_at": time.time()}, ttl=0)
        except Exception:
            pass
        log.info(f"seat_aliases 已从 JSON 灌入 Redis: {len(flat)} 别名 → {len(by_key)} 组")
        return _ALIASES_CACHE
    except Exception as e:
        log.warning(f"seat_aliases.json 读取失败: {e}")
        _ALIASES_CACHE = {}
        return _ALIASES_CACHE


import time  # noqa: E402  # 用于 _built_at


def _load_known() -> dict:
    """优先 Redis,首次从 known_seats.json 灌入。保留兼容 — 新代码走 _load_aliases()。"""
    cached = _store.get(K.SEAT_KNOWN)
    if cached and isinstance(cached, dict) and (cached.get("_slots") or cached.get("黑名单")):
        return cached
    if not _LEGACY_KNOWN_FILE.exists():
        return {"_slots": {}, "黑名单": {}}
    try:
        raw = json.loads(_LEGACY_KNOWN_FILE.read_text())
        try:
            _store.set(K.SEAT_KNOWN, raw, ttl=0)
        except Exception:
            pass
        log.info("known_seats 已从 JSON 灌入 Redis")
        return raw
    except Exception as e:
        log.warning(f"known_seats.json 读取失败: {e}")
        return {"_slots": {}, "黑名单": {}}


def _match_seat(seat_name: str, known: dict = None) -> tuple[str, str] | None:
    """席位名 → (组 key, 主别名)。新版走 seat_aliases.json。
    返回: (group_key, primary_alias) — group_key 是内部组ID, primary_alias 用于 UI 展示
    """
    if not seat_name:
        return None
    aliases = _load_aliases()
    if not aliases:
        return None
    # 1) 关键字 substring 匹配(优先级最高)
    for gkey, info in aliases.get("by_key", {}).items():
        for kw in info.get("keywords", []) or []:
            if not kw:
                continue
            if kw in seat_name or seat_name in kw:
                return (gkey, info.get("primary_alias", gkey))
    # 2) 别名直匹配
    flat = aliases.get("flat", {})
    for alias, gkey in flat.items():
        if alias and (alias in seat_name or seat_name in alias):
            info = aliases["by_key"].get(gkey, {})
            return (gkey, info.get("primary_alias", alias))
    return None


def get_alias_info(group_key: str) -> dict | None:
    """group_key → 完整别名 info (primary_alias / real_name / aliases / note / tier)"""
    aliases = _load_aliases()
    return aliases.get("by_key", {}).get(group_key)


def resolve_seat_alias(seat_name: str) -> dict | None:
    """席位名 → 完整 alias 字典 (primary_alias + real_name + aliases + tier + note)
    渲染层用 — 一次拿所有展示字段
    """
    m = _match_seat(seat_name)
    if not m:
        return None
    gkey, primary = m
    info = get_alias_info(gkey) or {}
    return {
        "group_key":    gkey,
        "primary":      primary,
        "real_name":    info.get("real_name", ""),
        "aliases":      info.get("aliases", []),
        "tier":         info.get("tier", ""),
        "note":         info.get("note", ""),
        "score_bonus":  info.get("score_bonus", 0),
    }


def get_stock_seats(code: str, lookback_days: int = 30) -> dict:
    """
    读取近 N 日龙虎榜；标记 known_seats 出现的席位。
    返回 {"code", "rows":[{date,seat,group,label,direction}], "blacklisted", "seat_count"}
    """
    known = _load_known()
    blacklisted = code in (known.get("黑名单") or {})

    rows: list[dict] = []

    # 主路径：先拿有龙虎榜数据的日期列表，再按日拉席位（买入 + 卖出）
    try:
        import akshare as ak
        from datetime import datetime, timedelta

        # 1) 该股有龙虎榜明细的日期列表
        try:
            date_df = ak.stock_lhb_stock_detail_date_em(symbol=code)
        except Exception as e:
            log.warning(f"lhb_date {code} 失败: {e}")
            date_df = None

        target_dates: list[str] = []
        if date_df is not None and not date_df.empty:
            # 列名通常叫 "交易日"
            col = "交易日" if "交易日" in date_df.columns else date_df.columns[0]
            for v in date_df[col].tolist():
                d = str(v)[:10].replace("-", "")
                if d.isdigit() and len(d) == 8:
                    target_dates.append(d)

        # 只取近 N 日
        cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        target_dates = [d for d in target_dates if d >= cutoff][-lookback_days:]

        # 2) 按日拉买卖席位
        for d in target_dates:
            for flag in ("买入", "卖出"):
                try:
                    detail_df = ak.stock_lhb_stock_detail_em(symbol=code, date=d, flag=flag)
                except Exception:
                    continue
                if detail_df is None or detail_df.empty:
                    continue
                # 列名兼容
                seat_col = next((c for c in detail_df.columns if "营业部" in c), None)
                if not seat_col:
                    continue
                for _, row in detail_df.iterrows():
                    seat = str(row.get(seat_col, "") or "").strip()
                    if not seat:
                        continue
                    # 金额列：兼容多种列名(akshare 买入金额 / 卖出金额 / 成交额 → 单位是元,需 ÷1e4 转万)
                    amt_col = next((c for c in detail_df.columns if any(k in c for k in ("成交额", "金额")) and "比例" not in c), None)
                    try:
                        amt_raw = float(row.get(amt_col, 0) or 0) if amt_col else 0
                    except (ValueError, TypeError):
                        amt_raw = 0
                    # 元 → 万元
                    amt_wan = round(amt_raw / 10000.0, 2) if amt_raw else 0
                    # 部分返回里 "营业部名称" 是单字段；若有多个用 ; 分隔
                    for s in seat.split(";"):
                        s = s.strip()
                        if not s:
                            continue
                        match = _match_seat(s, known)
                        info = resolve_seat_alias(s)
                        rows.append({
                            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                            "seat": s,
                            "direction": flag,
                            "group":      match[0] if match else "",
                            "label":      match[1] if match else "",
                            "real_name":  (info or {}).get("real_name", ""),
                            "aliases":    (info or {}).get("aliases", []),
                            "tier":       (info or {}).get("tier", ""),
                            "note":       (info or {}).get("note", ""),
                            "amount_wan": amt_wan if amt_wan else None,
                        })
    except Exception as e:
        log.warning(f"ak lhb detail {code} 失败: {e}")

    # 兜底：stock_lhb_stock_statistic_em（拉不到明细时退而求其次）
    if not rows:
        try:
            import akshare as ak
            df2 = ak.stock_lhb_stock_statistic_em(symbol="近一月")
            if df2 is not None and not df2.empty:
                code_col = next((c for c in df2.columns if "代码" in c), None)
                if code_col and code in df2[code_col].astype(str).tolist():
                    row = df2[df2[code_col].astype(str) == code].iloc[0]
                    for col, direction in (("买方席位", "买"), ("卖方席位", "卖")):
                        v = row.get(col)
                        if v and isinstance(v, str):
                            for seat in v.split(";"):
                                seat = seat.strip()
                                if not seat:
                                    continue
                                match = _match_seat(seat, known)
                                info = resolve_seat_alias(seat)
                                rows.append({
                                    "date": str(row.get("日期", ""))[:10],
                                    "seat": seat,
                                    "direction": direction,
                                    "group":     match[0] if match else "",
                                    "label":     match[1] if match else "",
                                    "real_name": (info or {}).get("real_name", ""),
                                    "aliases":   (info or {}).get("aliases", []),
                                    "tier":      (info or {}).get("tier", ""),
                                    "note":      (info or {}).get("note", ""),
                                })
        except Exception as e:
            log.warning(f"ak lhb_statistic 失败: {e}")

    # 统计：按方向累计金额
    buy_total = sum((r.get("amount_wan") or 0) for r in rows if r.get("direction") == "买入")
    sell_total = sum((r.get("amount_wan") or 0) for r in rows if r.get("direction") == "卖出")
    return {
        "code": code,
        "rows": rows[:60],
        "blacklisted": blacklisted,
        "seat_count": len([r for r in rows if r.get("group")]),
        "total_lhb_rows": len(rows),
        "known_groups": list(known.get("_slots", {}).keys() if isinstance(known.get("_slots"), dict) else []),
        "buy_total_wan":  round(buy_total, 2) if buy_total else None,
        "sell_total_wan": round(sell_total, 2) if sell_total else None,
    }
