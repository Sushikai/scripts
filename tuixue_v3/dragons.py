"""
tuixue_v3/dragons.py
龙头战法评分（6 维：连板/资金/封单/市值/技术/题材 + 换手率参考）
按用户 2026-07-08 详细规则:
  - 资金认可: 顶级游资=30, 净流入>5000万=20
  - 人气/封单: 封成比>10%=20, >20%=30 (用户允许降级)
  - 市值: <80亿=15, 80-150=8
  - 技术形态: 放量>1.5倍=10, 不破5日线=8
  - 题材纯度: 直接相关=15, 强相关=8
  - 换手率: 7-10% 高度活跃加分, >10% 极度活跃警告
  - 5板+ 需极强封单（封成比>15%）才安全

输入: 今日涨停池 + 主线板块 + 龙虎榜 + 日线（放量/MA5）
输出: Top 10 龙头 + 全涨停评分 + 板块归属 + 整体情绪 + 主线
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime

from . import config as cfg
from . import data_layer as dl
from . import multi_source_fetchers as msf
from .web import seat_lookup

log = logging.getLogger("tuixue_v3.dragons")


# ═══════════════════════════════════════════
# 评分维度 (满分 100，6 维)
# ═══════════════════════════════════════════
# 连板 30 + 资金 30 + 封单 20 + 市值 15 + 技术 18 + 题材 15 = 128
# 封单降级时按 (连板+资金+市值+技术+题材) = 108 归一化
# 技术细分: 放量 10 + 不破 5 日线 8 = 18


# ─── 1) 连板强度 (max 30) ───
def _score_streak(streak: int, seal_ratio_pct: float | None) -> tuple[float, str]:
    """连板: 1板=5, 2板=15, 3板=20, 4板=25, 5+板=30 (但 5+ 板需封成比>15% 才有 30，否则降到 20)"""
    if streak <= 1:
        return 5, "首板"
    if streak == 2:
        return 15, "2板"
    if streak == 3:
        return 20, "3板"
    if streak == 4:
        return 25, "4板"
    # 5+
    if seal_ratio_pct and seal_ratio_pct >= 15:
        return 30, f"{streak}板(强封单)"
    return 20, f"{streak}板(封单弱)"


# ─── 2) 资金认可 (max 30) ───
def _score_funding(lhb_info: dict | None) -> tuple[float, str]:
    """
    用户规则: 顶级游资=30, 净流入>5000万=20
    取较高者 (不可叠加)
    """
    if not lhb_info:
        return 0, "无龙虎榜"
    groups = lhb_info.get("known_groups", []) or []
    if "顶级游资" in groups:
        return 30, "顶级游资买入"
    if "活跃游资" in groups:
        return 18, "活跃游资"
    if "机构席位" in groups:
        return 10, "机构席位"
    rows = lhb_info.get("total_lhb_rows", 0)
    return 0, f"龙虎榜{rows}条(无主力)"


# ─── 3) 封成比 (max 20, 可降级) ───
def _score_seal(limit_order_amount: float, amount: float) -> tuple[float, str, bool]:
    """用户规则: 封成比>10%=20, >20%=30(但总分上限 20 已是 max, >20% 给满分)
    用户允许"封单降级为可选项" — 没数据时返回 degraded=True
    """
    if not limit_order_amount or not amount or amount <= 0:
        return 0, "封单不可用", True
    ratio = limit_order_amount / amount
    if ratio >= 0.20:
        return 20, f"封成比{ratio*100:.1f}%>20%", False
    if ratio >= 0.10:
        return 14, f"封成比{ratio*100:.1f}%>10%", False
    if ratio >= 0.05:
        return 7, f"封成比{ratio*100:.1f}%>5%", False
    return 0, f"封成比{ratio*100:.1f}%<5%(弱)", False


# ─── 4) 市值匹配 (max 15) ───
def _score_cap(market_cap_yi: float) -> tuple[float, str]:
    """用户规则: <80亿=15, 80-150=8
    超 150 → 不爆发出局 (0 或负)
    """
    if market_cap_yi < 0:
        return 0, "市值未知"
    if market_cap_yi < 30:
        return 12, f"{market_cap_yi:.0f}亿<30(过小)"
    if market_cap_yi < 80:
        return 15, f"{market_cap_yi:.0f}亿(优)"
    if market_cap_yi <= 120:
        return 12, f"{market_cap_yi:.0f}亿(80-120优)"
    if market_cap_yi <= 150:
        return 8, f"{market_cap_yi:.0f}亿(120-150)"
    if market_cap_yi <= 300:
        return 0, f"{market_cap_yi:.0f}亿(150-300,难爆发)"
    return -5, f"{market_cap_yi:.0f}亿>300(太大)"


# ─── 5) 技术形态 (max 18) ───
def _score_tech(volume_ratio: float | None, ma5_dist_pct: float | None) -> tuple[float, str]:
    """用户规则: 放量>1.5倍=10, 不破5日线=8
    放量: 当日成交额/前 5 日均量
    不破 5 日线: 收盘价 > MA5
    """
    s = 0.0
    parts = []
    if volume_ratio is not None and volume_ratio >= 1.5:
        s += 10
        parts.append(f"放量{volume_ratio:.1f}x")
    elif volume_ratio is not None and volume_ratio >= 1.0:
        s += 5
        parts.append(f"微放量{volume_ratio:.1f}x")
    if ma5_dist_pct is not None and ma5_dist_pct >= 0:
        s += 8
        parts.append(f"站上5日线(+{ma5_dist_pct:.1f}%)")
    elif ma5_dist_pct is not None:
        parts.append(f"破5日线({ma5_dist_pct:.1f}%)")
    return s, " + ".join(parts) if parts else "技术数据不全"


# ─── 6) 题材纯度 (max 15) ───
def _score_mainline(sector: str, mainline_names: list[str]) -> tuple[float, str]:
    """用户规则: 直接相关=15, 强相关=8
    双向 substring 匹配: 涨停股.板块 vs 主线名
    """
    if not sector or not mainline_names:
        return 0, "板块/主线未知"
    for m in mainline_names:
        if not m:
            continue
        if m in sector or sector in m:
            # 完全相同或包含 → 直接相关
            if m == sector:
                return 15, f"主线:{m[:10]}(直接)"
            return 8, f"主线:{m[:10]}(强相关)"
    return 0, f"({sector[:10]} 非主线)"


# ─── 换手率参考分 (不计入总分) ───
def _turnover_note(turnover_pct: float) -> str:
    """用户规则: 7-10% 高度活跃加分(参考), >10% 极度活跃(警告)"""
    if turnover_pct < 1:
        return f"换手{turnover_pct:.1f}%(冷门)"
    if turnover_pct < 3:
        return f"换手{turnover_pct:.1f}%(正常)"
    if turnover_pct < 7:
        return f"换手{turnover_pct:.1f}%(相对活跃)"
    if turnover_pct < 10:
        return f"换手{turnover_pct:.1f}%(高度活跃)"
    return f"换手{turnover_pct:.1f}%⚠(极度活跃,警惕出货)"


# ═══════════════════════════════════════════
# 工具: 拉涨停股日线 (并行, 5s 总超时)
# ═══════════════════════════════════════════
def _fetch_tech_data(codes: list[str]) -> dict[str, dict]:
    """返回 {code: {volume_ratio, ma5_dist_pct}}
    2026-07-08: 严格只用 SQLite 缓存 (cache_db), 跳过未命中 (不再回退 dl.fetch_daily)
    """
    out: dict[str, dict] = {}
    if not codes:
        return out
    from . import cache_db
    db = cache_db.daily()

    def _one(code: str):
        try:
            df = db.get(code, 30)
            if df is None or df.empty or len(df) < 6:
                return code, None
            amt_col = None
            for c in ("成交额", "成交额(元)", "amount"):
                if c in df.columns:
                    amt_col = c
                    break
            close_col = "收盘" if "收盘" in df.columns else None
            if not amt_col or not close_col:
                return code, None
            today_amt = float(df[amt_col].iloc[-1])
            avg5_amt = float(df[amt_col].iloc[-6:-1].mean()) if len(df) >= 6 else 0
            vol_ratio = today_amt / avg5_amt if avg5_amt > 0 else None
            ma5 = float(df[close_col].iloc[-5:].mean())
            close = float(df[close_col].iloc[-1])
            ma5_dist = (close - ma5) / ma5 * 100 if ma5 > 0 else None
            return code, {"volume_ratio": vol_ratio, "ma5_dist_pct": ma5_dist}
        except Exception as e:
            log.debug(f"tech data {code} 失败: {e}")
            return code, None

    ex = ThreadPoolExecutor(max_workers=min(16, len(codes)))
    try:
        futures = {ex.submit(_one, c): c for c in codes}
        for fut in futures:
            try:
                code, data = fut.result(timeout=2)
                if data:
                    out[code] = data
            except Exception:
                continue
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out


# ═══════════════════════════════════════════
# 龙头评分主入口
# ═══════════════════════════════════════════
def score_dragons(date_str: str | None = None) -> dict:
    """
    龙头战法评分:
    1) 拉今日涨停池 (msf.fetch_zt_pool — 东财)
    2) 拉主线板块 (msf.fetch_hot_sectors — 优先东财, 5s 兜底 THS)
    3) 拉龙虎榜 (seat_lookup 并行, 5s)
    4) 拉技术面 (data_layer.fetch_daily 并行, 5s)
    5) 6 维评分 + 归一化
    6) 排序 → Top 10 + 全涨停
    """
    t0 = datetime.now()
    date = date_str or datetime.now().strftime("%Y%m%d")

    # 1) 涨停池 (东财主源)
    log.info(f"[dragons] 拉涨停池 {date}")
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            zt_pool = ex.submit(msf.fetch_zt_pool, date).result(timeout=10) or []
    except Exception as e:
        log.warning(f"[dragons] 涨停池失败: {e}")
        zt_pool = []
    if not zt_pool:
        return _empty_result(date, "涨停池为空", t0)
    log.info(f"[dragons] 涨停池 {len(zt_pool)} 只")

    # 2) 主线 (东财 → THS 兜底)
    log.info("[dragons] 拉主线板块")
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            hot_sectors = ex.submit(msf.fetch_hot_sectors,
                                    top_n_flow=cfg.HOT_SECTOR_TOP_FLOW,
                                    top_n_pct=cfg.HOT_SECTOR_TOP_PCT).result(timeout=25) or []
        finally:
            ex.shutdown(wait=False)
    except Exception as e:
        log.warning(f"[dragons] 主线失败: {e}")
        hot_sectors = []
    mainline_names = [s.get("name", "") for s in hot_sectors[:10] if s.get("name")]
    log.info(f"[dragons] 主线 Top10: {mainline_names[:5]}")

    # 3) 龙虎榜 (单日批量, 2 天缓存 — 替代逐股 seat_lookup)
    codes = [z["code"] for z in zt_pool if z.get("code")]
    log.info(f"[dragons] 拉 {len(codes)} 只龙虎榜 (批量)")
    lhb_map: dict[str, dict] = {}
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            lhb_rows = ex.submit(msf.fetch_lhb_detail, date).result(timeout=10)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        if lhb_rows is not None:
            known = seat_lookup._load_known() if hasattr(seat_lookup, "_load_known") else {}
            # 把当天龙虎榜按 code 聚合, 标记 known_seats
            from collections import defaultdict
            by_code: dict[str, list] = defaultdict(list)
            for r in (lhb_rows if isinstance(lhb_rows, list) else lhb_rows.to_dict("records") if hasattr(lhb_rows, "to_dict") else []):
                c = str(r.get("代码", r.get("code", "")) or "").zfill(6)
                seat = str(r.get("营业部名称", r.get("seat", "")) or "")
                direction = str(r.get("类型", r.get("direction", "")) or "")
                if c and seat:
                    by_code[c].append({"seat": seat, "direction": direction})
            # 标记 known_seats 并简化给 scoring
            for c in codes:
                entries = by_code.get(c, [])
                if not entries:
                    continue
                groups = set()
                labels = []
                for e in entries:
                    sn = e["seat"]
                    match = seat_lookup._match_seat(sn, known) if hasattr(seat_lookup, "_match_seat") else None
                    if match:
                        groups.add(match[0])
                        labels.append(match[1])
                lhb_map[c] = {
                    "known_groups": list(groups),
                    "labels": labels[:5],
                    "total_lhb_rows": len(entries),
                }
    except Exception as e:
        log.warning(f"[dragons] 龙虎榜批量失败: {e}")
    log.info(f"[dragons] 龙虎榜已收 {len(lhb_map)}/{len(codes)}")

    # 4) 技术面 (并行, 4s)
    log.info(f"[dragons] 拉 {len(codes)} 只技术面")
    tech_map = _fetch_tech_data(codes)
    log.info(f"[dragons] 技术面已收 {len(tech_map)}/{len(codes)}")

    # 5) 评分
    scored: list[dict] = []
    seal_degraded_count = 0
    for z in zt_pool:
        code = z.get("code", "")
        name = z.get("name", "")
        streak = int(z.get("streak", 1) or 1)
        sector = z.get("sector", "")
        market_cap_yi = float(z.get("market_cap", 0) or 0) / 1e8
        limit_order_amount = float(z.get("limit_order_amount", 0) or 0)
        amount = float(z.get("amount", 0) or 0)
        turnover_pct = float(z.get("turnover_pct", 0) or 0)
        first_time = str(z.get("first_time", ""))
        burst_count = int(z.get("burst_count", 0) or 0)
        tech = tech_map.get(code, {})
        vol_ratio = tech.get("volume_ratio")
        ma5_dist = tech.get("ma5_dist_pct")
        seal_ratio_pct = (limit_order_amount / amount * 100) if amount > 0 else None

        s_streak, note_streak = _score_streak(streak, seal_ratio_pct)
        s_funding, note_funding = _score_funding(lhb_map.get(code))
        s_seal, note_seal, seal_degraded = _score_seal(limit_order_amount, amount)
        if seal_degraded:
            seal_degraded_count += 1
        s_cap, note_cap = _score_cap(market_cap_yi)
        s_tech, note_tech = _score_tech(vol_ratio, ma5_dist)
        s_mainline, note_mainline = _score_mainline(sector, mainline_names)

        # 总分归一化: max 128, 封单降级 max 108
        raw_total = s_streak + s_funding + s_seal + s_cap + s_tech + s_mainline
        if seal_degraded:
            norm_total = round(raw_total / 108 * 100, 1)
        else:
            norm_total = round(raw_total / 128 * 100, 1)

        scored.append({
            "code": code,
            "name": name,
            "sector": sector,
            "streak": streak,
            "market_cap_yi": round(market_cap_yi, 1),
            "limit_order_amount_yi": round(limit_order_amount / 1e8, 2),
            "amount_yi": round(amount / 1e8, 2),
            "seal_ratio_pct": round(seal_ratio_pct, 1) if seal_ratio_pct is not None else None,
            "first_time": first_time,
            "burst_count": burst_count,
            "turnover_pct": round(turnover_pct, 1),
            "is_mainline": any(m and (m in sector or sector in m) for m in mainline_names),
            "score_breakdown": {
                "连板强度": {"pts": s_streak, "note": note_streak, "max": 30},
                "资金认可": {"pts": s_funding, "note": note_funding, "max": 30},
                "封成比":   {"pts": s_seal, "note": note_seal,
                            "max": 20, "degraded": seal_degraded},
                "市值匹配": {"pts": s_cap, "note": note_cap, "max": 15},
                "技术形态": {"pts": s_tech, "note": note_tech, "max": 18},
                "题材纯度": {"pts": s_mainline, "note": note_mainline, "max": 15},
            },
            "score_total": norm_total,
            "warnings": _build_warnings(turnover_pct, streak, seal_ratio_pct, burst_count),
        })

    # 排序
    scored.sort(key=lambda x: x["score_total"], reverse=True)
    for i, s in enumerate(scored, 1):
        s["rank"] = i

    # 6) 整体情绪 (用户规则: 涨停家数 + 最高连板)
    max_streak = max((z.get("streak", 1) or 1) for z in zt_pool) if zt_pool else 0
    zt_count = len(zt_pool)
    if zt_count > 60 and max_streak >= 3:
        sentiment_label = "好 · 可积极选股"
        sentiment_action = "积极"
    elif zt_count > 60:
        sentiment_label = "高数量但高度不足 · 谨慎"
        sentiment_action = "谨慎"
    elif 30 <= zt_count <= 60:
        sentiment_label = "一般 · 小仓低吸"
        sentiment_action = "小仓"
    elif zt_count < 30:
        sentiment_label = "差 · 建议空仓"
        sentiment_action = "空仓"
    else:
        sentiment_label = "一般"
        sentiment_action = "观望"
    if max_streak >= 5:
        sentiment_label += f" · 有{max_streak}板(极高)"

    # 7) 主线 (前 5)
    mainline_top = [
        {
            "name": s.get("name"),
            "code": s.get("code"),
            "change_pct": s.get("change_pct", 0),
            "net_inflow_yi": s.get("net_inflow", 0),
            "rank_flow": s.get("rank_flow"),
            "rank_pct": s.get("rank_pct"),
        }
        for s in hot_sectors[:5]
    ]

    # 8) 连板梯队 (用户: 完整梯队 = 首板/2板/3板都有)
    streak_dist = {}
    for s in scored:
        n = s["streak"]
        streak_dist[n] = streak_dist.get(n, 0) + 1

    # 9) 决策建议 (STEP 4) — 按用户规则分类
    decisions = _build_decisions(scored[:10], sentiment_label, max_streak, zt_count)
    # 主线选股 TOP 3 (主线内得分最高)
    top_mainline = [s for s in scored if s.get("is_mainline")][:3]

    return {
        "date": date,
        "sentiment": {
            "label": sentiment_label,
            "action": sentiment_action,
            "zt_count": zt_count,
            "max_streak": max_streak,
            "streak_dist": streak_dist,
        },
        "mainline": mainline_top,
        "top10": scored[:10],
        "all": scored,
        "decisions": decisions,
        "top_mainline": top_mainline,
        "stats": {
            "total_zt": zt_count,
            "lhb_loaded": len(lhb_map),
            "tech_loaded": len(tech_map),
            "seal_degraded": seal_degraded_count,
            "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
        },
        "ts": datetime.now().isoformat(),
    }


def _build_warnings(turnover: float, streak: int, seal_pct: float | None, burst: int) -> list[str]:
    warns = []
    if turnover > 10:
        warns.append(f"换手{turnover:.1f}%>10%极度活跃,警惕出货")
    if burst > 1:
        warns.append(f"炸板{burst}次(烂板)")
    if streak >= 5 and (seal_pct is None or seal_pct < 15):
        warns.append(f"{streak}板但封单弱,风险高")
    return warns


def _build_decisions(top10: list[dict], sentiment_label: str, max_streak: int, zt_count: int) -> list[dict]:
    """STEP 4 决策建议:
    - 打板候选 (尾盘): 高封成比 + 高分 + 低换手 (没出货)
    - 低吸候选 (次日): 主线股 + 强题材 + 中等连板 (回调后介入)
    - 回避: 警告多 + 烂板
    """
    plays, dips, avoids = [], [], []
    for s in top10:
        seal = s.get("seal_ratio_pct") or 0
        score = s.get("score_total", 0)
        turnover = s.get("turnover_pct", 0)
        streak = s.get("streak", 0)
        warns = s.get("warnings", [])
        mainline = s.get("is_mainline", False)
        # 打板: 封成>20% + 评分>=50 + 换手<10% + 不在主线 (打板要打"异军突起")
        if seal > 20 and score >= 50 and turnover < 10 and not warns:
            plays.append({
                "code": s["code"], "name": s["name"], "sector": s["sector"],
                "score": score, "seal_pct": seal,
                "reason": f"封成{seal:.0f}%强 · 评分{score} · 换手{turnover}%未出货"
            })
        # 低吸 (次日早盘): 主线股 + 评分>=45 + 连板<=4 + 无警告
        elif mainline and score >= 45 and streak <= 4 and len(warns) <= 1:
            dips.append({
                "code": s["code"], "name": s["name"], "sector": s["sector"],
                "score": score, "streak": streak,
                "reason": f"{s['sector']}主线股 · {streak}板 · 评分{score} · 次日低吸"
            })
        elif len(warns) >= 2:
            avoids.append({
                "code": s["code"], "name": s["name"], "sector": s["sector"],
                "score": score,
                "reason": "; ".join(warns)
            })

    overall_advice = ""
    if "空仓" in sentiment_label:
        overall_advice = "情绪差,建议空仓等待"
    elif "积极" in sentiment_label and max_streak >= 5:
        overall_advice = f"情绪好且有{max_streak}板高度,积极选股,可小仓打板 + 次日主线低吸"
    elif "积极" in sentiment_label:
        overall_advice = "情绪好,可积极选股,优先主线龙头"
    elif "小仓" in sentiment_label:
        overall_advice = "情绪一般,小仓低吸主线,不追高"
    else:
        overall_advice = "情绪谨慎,观望为主"

    return {
        "overall": overall_advice,
        "plays": plays[:3],   # 打板候选 ≤ 3
        "dips": dips[:3],     # 低吸候选 ≤ 3
        "avoids": avoids[:3], # 回避 ≤ 3
    }


def _empty_result(date: str, reason: str, t0) -> dict:
    return {
        "date": date,
        "sentiment": {"label": "—", "action": "—", "zt_count": 0,
                      "max_streak": 0, "streak_dist": {}},
        "mainline": [],
        "top10": [],
        "all": [],
        "stats": {"total_zt": 0, "lhb_loaded": 0, "tech_loaded": 0,
                  "seal_degraded": 0,
                  "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
                  "reason": reason},
        "ts": datetime.now().isoformat(),
    }
