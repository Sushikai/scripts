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
from .web.sector_taxonomy import classify_sector_name
from .web import weekly_bull as _wb
from .web import recovery_level as _rl

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
    净流入 = 龙虎榜 buy_total_wan − sell_total_wan (单位:万)
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
    # 净流入 5000 万门槛 = 5000 wan
    buy = float(lhb_info.get("buy_total_wan") or 0)
    sell = float(lhb_info.get("sell_total_wan") or 0)
    net = buy - sell
    if net >= 5000:
        return 20, f"龙虎榜净买{net:.0f}万"
    if net > 0:
        return 10, f"龙虎榜净买{net:.0f}万(<5000)"
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


# ─── 7) 周线擒牛命中数 (max 12) ───
def _score_weekly_bull(wb_hit: dict | None) -> tuple[float, str]:
    """周线擒牛 5 大信号命中数 → 0-12 分。
    命中 5 个 = 12 (满分, 主升前夜全到位)
    命中 4 个 = 10
    命中 3 个 = 7
    命中 2 个 = 4
    命中 1 个 = 2
    命中 0 个 = 0
    """
    if not wb_hit or not wb_hit.get("matched"):
        return 0, "周线无信号"
    n = wb_hit.get("count", 0)
    if n >= 5:
        return 12, f"周线 5/5 命中"
    if n == 4:
        return 10, f"周线 {n}/5"
    if n == 3:
        return 7, f"周线 {n}/5"
    if n == 2:
        return 4, f"周线 {n}/5"
    return 2, f"周线 {n}/5"


# ─── 8) 1/3 回升位 (max 8) ───
def _score_recovery(rl_hit: dict | None) -> tuple[float, str]:
    """三分之一回升位 → 0-8 分。
    当前价贴近 1/3 位 (±3%) = 8 (强支撑, 买点)
    1/2-2/3 区间 (强势区) = 5
    >2/3 位 (高位) = 3 (留意突破)
    <1/3 位 (弱势) = 0
    """
    if not rl_hit or not rl_hit.get("has_signal"):
        return 0, "回升位无信号"
    l13 = rl_hit.get("level_1_3") or 0
    l12 = rl_hit.get("level_1_2") or 0
    l23 = rl_hit.get("level_2_3") or 0
    cur = rl_hit.get("current_close") or 0
    if not l13 or not l12 or not l23 or not cur:
        return 0, "回升位数据不全"
    if rl_hit.get("near_support"):
        return 8, f"贴近1/3位({l13})"
    if cur >= l12:
        return 5, f"1/2-2/3区间({cur:.2f})"
    if cur >= l23:
        return 5, f"2/3位之上({cur:.2f})"
    if cur >= l13:
        return 5, f"1/3-1/2区间({cur:.2f})"
    if cur >= l13 * 0.97:
        return 3, f"1/3位附近({cur:.2f})"
    return 0, f"跌破1/3位({cur:.2f})"


# ═══════════════════════════════════════════
# 工具: 拉涨停股日线 (并行, 5s 总超时)
# ═══════════════════════════════════════════
def _fetch_tech_data(codes: list[str]) -> dict[str, dict]:
    """返回 {code: {volume_ratio, ma5_dist_pct, wb_hit, rl_hit}}
    2026-07-08: 严格只用 SQLite 缓存 (cache_db), 跳过未命中 (不再回退 dl.fetch_daily)
    2026-07-19: +wb_hit (周线擒牛) + rl_hit (1/3 回升位) — 注入 stock_kline_loader
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

    # 周线擒牛 + 1/3 回升位 — 从 web.server 注入 stock_kline_loader (避免相对导入)
    try:
        from .web import server as _srv
        _loader = _srv.stock_kline_loader
    except Exception:
        _loader = None

    def _wb_one(code: str):
        try:
            return code, _wb.analyze_one(code, _loader)
        except Exception as e:
            log.debug(f"wb {code} 失败: {e}")
            return code, None

    def _rl_one(code: str):
        try:
            return code, _rl.analyze_recovery(code, _loader)
        except Exception as e:
            log.debug(f"rl {code} 失败: {e}")
            return code, None

    ex = ThreadPoolExecutor(max_workers=min(16, max(1, len(codes))))
    try:
        # 1) 技术面 (优先, fast)
        futs_tech = {ex.submit(_one, c): c for c in codes}
        tech_map: dict[str, dict] = {}
        for fut in futs_tech:
            try:
                code, data = fut.result(timeout=2)
                if data:
                    tech_map[code] = data
            except Exception:
                continue
        # 2) 周线擒牛 + 回升位 (并行, 3s 总超时) — Top 30 节省时间
        top_codes = codes[:30]
        futs_wb = {ex.submit(_wb_one, c): c for c in top_codes}
        futs_rl = {ex.submit(_rl_one, c): c for c in top_codes}
        all_extra = list(futs_wb.keys()) + list(futs_rl.keys())
        for fut in all_extra:
            try:
                code, data = fut.result(timeout=3)
                if data is None:
                    continue
                tech_map.setdefault(code, {})
                if fut in futs_wb:
                    tech_map[code]["wb_hit"] = data
                else:
                    tech_map[code]["rl_hit"] = data
            except Exception:
                continue
        out.update(tech_map)
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
    # summary lhb(per-stock 龙虎榜净买额/买卖额) + Top30 per-stock seat 明细
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
            # summary 字段: 代码/名称/上榜日/解读/龙虎榜净买额/龙虎榜买入额/龙虎榜卖出额/...
            # 单位是 元 → 转万
            rows_iter = (lhb_rows if isinstance(lhb_rows, list)
                         else lhb_rows.to_dict("records") if hasattr(lhb_rows, "to_dict")
                         else [])
            for r in rows_iter:
                c = str(r.get("代码", r.get("code", "")) or "").zfill(6)
                if not c or c not in codes:
                    continue
                # 兼容多种字段名
                def _amt(*keys):
                    for k in keys:
                        v = r.get(k)
                        if v is not None:
                            try: return float(v) / 1e4
                            except (ValueError, TypeError): pass
                    return 0
                buy_w = _amt("龙虎榜买入额", "买入额", "buy_amount")
                sell_w = _amt("龙虎榜卖出额", "卖出额", "sell_amount")
                net_w = _amt("龙虎榜净买额", "净买额", "net_amount") or (buy_w - sell_w)
                reason = str(r.get("上榜原因", r.get("reason", "")) or "")
                # summary 自带"解读"= "西藏自治区资金卖出" 等
                comment = str(r.get("解读", r.get("comment", "")) or "")
                lhb_map[c] = {
                    "buy_total_wan": round(buy_w, 2),
                    "sell_total_wan": round(sell_w, 2),
                    "net_total_wan": round(net_w, 2),
                    "reason": reason[:80],
                    "comment": comment[:80],
                    "known_groups": [],
                    "labels": [],
                    "total_lhb_rows": 1,
                }
    except Exception as e:
        log.warning(f"[dragons] 龙虎榜批量失败: {e}")
    log.info(f"[dragons] 龙虎榜 summary 已收 {len(lhb_map)}/{len(codes)}")

    # 3b) Top 30 用 per-stock seat_lookup 拿席位 + 江湖别名(精补)
    if hasattr(seat_lookup, "_match_seat"):
        known = seat_lookup._load_known() if hasattr(seat_lookup, "_load_known") else {}
        top_codes = codes[:30]
        log.info(f"[dragons] Top{len(top_codes)} per-stock 席位精补")

        def _fetch_seats(code):
            try:
                # 直接用 seat_lookup 的单股接口 (已有 ~1s 缓存)
                return code, seat_lookup.get_stock_seats(code, lookback_days=10)
            except Exception:
                return code, None

        ex2 = ThreadPoolExecutor(max_workers=8)
        try:
            futures = {ex2.submit(_fetch_seats, c): c for c in top_codes}
            for fut in futures:
                try:
                    code, data = fut.result(timeout=8)
                except Exception:
                    continue
                if not data or not data.get("rows"):
                    continue
                groups = set()
                labels = []
                seen = set()
                for r in data["rows"]:
                    sn = r.get("seat", "")
                    if not sn or sn in seen:
                        continue
                    seen.add(sn)
                    m = seat_lookup._match_seat(sn, known)
                    if m:
                        groups.add(m[0])
                        if m[1] and m[1] not in labels:
                            labels.append(m[1])
                # 合并到 lhb_map(groups/labels 优先,summary 数值保留)
                base = lhb_map.get(code, {})
                if groups:
                    base["known_groups"] = list(groups)
                    base["labels"] = labels[:5]
                    base["total_lhb_rows"] = (base.get("total_lhb_rows") or 0) + len(data["rows"])
                lhb_map[code] = base
        finally:
            ex2.shutdown(wait=False, cancel_futures=True)
        log.info(f"[dragons] Top{len(top_codes)} 席位精补完成,共 {sum(1 for c in lhb_map if lhb_map[c].get('known_groups'))} 只带席位组")

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
        # 2026-07-19: 周线擒牛 + 1/3 回升位 (新 2 维, 仅 Top30 有数据)
        s_wb, note_wb = _score_weekly_bull(tech.get("wb_hit"))
        s_rl, note_rl = _score_recovery(tech.get("rl_hit"))

        # 总分归一化: max 148 (128 + 12 + 8), 封单降级 max 128 (108 + 12 + 8)
        raw_total = s_streak + s_funding + s_seal + s_cap + s_tech + s_mainline + s_wb + s_rl
        if seal_degraded:
            norm_total = round(raw_total / 128 * 100, 1)
        else:
            norm_total = round(raw_total / 148 * 100, 1)

        # 周线擒牛命中清单 (前端 chip 用)
        wb_hit = tech.get("wb_hit") or {}
        wb_matched = wb_hit.get("matched") or []
        wb_reasons = wb_hit.get("reasons") or {}

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
            "taxonomy": classify_sector_name(sector),
            "seat_aliases": (lhb_map.get(code) or {}).get("labels", [])[:5],
            "wb_hits": {
                "matched": wb_matched,
                "count": len(wb_matched),
                "reasons": wb_reasons,
            },
            "rl_hit": tech.get("rl_hit") or {},
            "score_breakdown": {
                "连板强度": {"pts": s_streak, "note": note_streak, "max": 30},
                "资金认可": {"pts": s_funding, "note": note_funding, "max": 30},
                "封成比":   {"pts": s_seal, "note": note_seal,
                            "max": 20, "degraded": seal_degraded},
                "市值匹配": {"pts": s_cap, "note": note_cap, "max": 15},
                "技术形态": {"pts": s_tech, "note": note_tech, "max": 18},
                "题材纯度": {"pts": s_mainline, "note": note_mainline, "max": 15},
                "周线擒牛": {"pts": s_wb, "note": note_wb, "max": 12},
                "回升位":   {"pts": s_rl, "note": note_rl, "max": 8},
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
            "taxonomy": classify_sector_name(s.get("name")),
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

    # 10) 昨日涨停 (轻量)
    yesterday_date = _prev_trade_date(date)
    yesterday_all_raw = []
    if yesterday_date:
        try:
            yesterday_all_raw = msf.fetch_zt_pool(yesterday_date) or []
        except Exception as e:
            log.warning(f"[dragons] 昨日涨停池失败: {e}")
    yesterday_all = []
    # v234b (2026-07-28): 补"今日表现"字段 — change_pct (今日涨幅) / is_zt_today (今日是否涨停) / change_type (连板/晋级/晋级失败/大面/震荡)
    # 需要今日 spot 数据, 但 score_dragons 主体已用过 to_thread 拉过 zt_pool; 这里另起一次小 fetch, 失败降级
    y_spot_map: dict[str, dict] = {}
    try:
        y_spot_map = msf.fetch_spot_a_full(8) or {}
    except Exception as e:
        log.debug(f"[dragons] 昨日表补 spot 失败 (降级): {e}")

    for z in yesterday_all_raw:
        seal_pct = (float(z.get("limit_order_amount", 0) or 0)
                    / max(float(z.get("amount", 1) or 1), 1) * 100)
        y_streak = int(z.get("streak", 1) or 1)
        y_mcap = float(z.get("market_cap", 0) or 0) / 1e8
        # v234 (2026-07-27): 昨日表加 概念/总分,与今日表拉齐 — sector→taxonomy,启发式 score
        y_score = round(
            min(60, y_streak * 15)
            + min(20, seal_pct * 0.4 if seal_pct > 0 else 0)
            + (15 if 30 <= y_mcap <= 300 else 5), 1)
        y_code = str(z.get("code", "")).zfill(6)
        y_spot = y_spot_map.get(y_code) or {}
        try:
            y_change_pct = float(y_spot.get("涨跌幅", 0) or 0)
        except Exception:
            y_change_pct = 0.0
        # 涨停判定: 涨幅 ≥ 9.5% (主板 10% / 创板 20% / 北证 30%, 但实际多数在 9.5-11%)
        is_zt_today = y_change_pct >= 9.5
        # 进阶分类 (3 段式)
        if is_zt_today:
            # 今日涨停 → 晋级 (streak 至少 +1)
            y_change_type = "晋级" if (y_streak + 1) >= 2 else "连板"
        elif y_change_pct >= 5.0:
            y_change_type = "高开高走"
        elif y_change_pct >= 0.0:
            y_change_type = "震荡"
        elif y_change_pct >= -5.0:
            y_change_type = "回调"
        else:
            y_change_type = "大面"
        yesterday_all.append({
            "code": y_code,
            "name": str(z.get("name", "")),
            "sector": str(z.get("sector", "")),
            "streak": y_streak,
            "market_cap_yi": round(y_mcap, 1),
            "turnover_pct": round(float(z.get("turnover_pct", 0) or 0), 1),
            "seal_ratio_pct": round(seal_pct, 1) if seal_pct > 0 else None,
            "taxonomy": classify_sector_name(z.get("name") or z.get("sector", "")),
            "score_total": y_score,
            # v234b 新增字段
            "change_pct": round(y_change_pct, 2),
            "is_zt_today": is_zt_today,
            "change_type": y_change_type,
        })
    log.info(f"[dragons] 昨日涨停 {len(yesterday_all)} 只 ({yesterday_date})")

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
        "yesterday_all": yesterday_all,
        "yesterday_date": yesterday_date or "",
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


def _prev_trade_date(date_str: str) -> str | None:
    """返回 date_str 的前一个交易日 (YYYYMMDD)"""
    try:
        raw = msf.fetch_trade_dates() or set()
        # msf 返回 YYYY-MM-DD 格式, 统一转 YYYYMMDD, 降序 (最新在前)
        dates = sorted(
            (str(d).replace("-", "")[:8] for d in raw if len(str(d).replace("-", "")[:8]) == 8),
            reverse=True,
        )
        idx = None
        for i, d in enumerate(dates):
            if d == date_str:
                idx = i
                break
        if idx is not None and idx + 1 < len(dates):
            return dates[idx + 1]
        if idx is not None and idx == len(dates) - 1:
            return None
        # date_str not in list → 取列表中最接近且 < date_str 的
        for d in dates:
            if d < date_str:
                return d
    except Exception as e:
        log.warning(f"[dragons] _prev_trade_date({date_str}) 失败: {e}")
    return None


def _empty_result(date: str, reason: str, t0) -> dict:
    return {
        "date": date,
        "sentiment": {"label": "—", "action": "—", "zt_count": 0,
                      "max_streak": 0, "streak_dist": {}},
        "mainline": [],
        "top10": [],
        "all": [],
        "decisions": {"overall": reason, "plays": [], "dips": [], "avoids": []},
        "top_mainline": [],
        "yesterday_all": [],
        "yesterday_date": "",
        "stats": {"total_zt": 0, "lhb_loaded": 0, "tech_loaded": 0,
                  "seal_degraded": 0,
                  "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
                  "reason": reason},
        "ts": datetime.now().isoformat(),
    }
