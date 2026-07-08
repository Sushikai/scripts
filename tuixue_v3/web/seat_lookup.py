"""
游资席位查询：
- 读取 data/known_seats.json（你手动维护的 P1-P6 席位质量评分）
- 拉龙虎榜（akshare），在结果里匹配 known_seats 标记
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("tuixue_v3.web.seats")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_known() -> dict:
    p = DATA_DIR / "known_seats.json"
    if not p.exists():
        return {"_slots": {}, "黑名单": {}}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning(f"known_seats.json 读取失败: {e}")
        return {"_slots": {}, "黑名单": {}}


def _match_seat(seat_name: str, known: dict) -> tuple[str, str] | None:
    """席位名 → (组, 标签)"""
    slots = known.get("_slots", {}) or {}
    for group, members in slots.items():
        names = []
        if isinstance(members, dict):
            names = list(members.keys())
        elif isinstance(members, list):
            names = members
        for n in names:
            if not n:
                continue
            if n in seat_name or seat_name in n:
                return (group, n)
    return None


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
                    # 部分返回里 "营业部名称" 是单字段；若有多个用 ; 分隔
                    for s in seat.split(";"):
                        s = s.strip()
                        if not s:
                            continue
                        match = _match_seat(s, known)
                        rows.append({
                            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
                            "seat": s,
                            "direction": flag,
                            "group": match[0] if match else "",
                            "label": match[1] if match else "",
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
                                rows.append({
                                    "date": str(row.get("日期", ""))[:10],
                                    "seat": seat,
                                    "direction": direction,
                                    "group": match[0] if match else "",
                                    "label": match[1] if match else "",
                                })
        except Exception as e:
            log.warning(f"ak lhb_statistic 失败: {e}")

    return {
        "code": code,
        "rows": rows[:60],
        "blacklisted": blacklisted,
        "seat_count": len([r for r in rows if r.get("group")]),
        "total_lhb_rows": len(rows),
        "known_groups": list(known.get("_slots", {}).keys() if isinstance(known.get("_slots"), dict) else []),
    }
