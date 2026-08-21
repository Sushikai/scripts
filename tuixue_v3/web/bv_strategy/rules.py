"""
战法规则加载器 — 从 data/bv_rules.json 单例加载。
失败兜底: 返空 dict (前端 rules-host 显示"战法加载失败").
"""
import json, time
from functools import lru_cache
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "bv_rules.json"

_DEFAULT = {
    "name": "游资仓位管理战法",
    "up": "Bryan交易随笔",
    "version": "v1",
    "rules": [],
    "quote_corpus": [],
    "philosophy": [],
}


@lru_cache(maxsize=1)
def load_rules() -> dict:
    """单例加载战法规则 (进程级缓存, 启动时一次)。
    JSON 改动需重启 server 才生效 (后续 round 加 file mtime 失效)。
    """
    if not _RULES_PATH.exists():
        return dict(_DEFAULT)
    try:
        with _RULES_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)


def get_rules_by_category() -> dict[str, list]:
    """按 category 分组规则 — 前端规则明细面板渲染用。"""
    rules = load_rules().get("rules", [])
    out: dict[str, list] = {}
    for r in rules:
        cat = r.get("category", "未分类")
        out.setdefault(cat, []).append(r)
    return out


def match_rules_to_stock(stock_info: dict) -> list[str]:
    """根据股票字段跑一遍规则引擎, 返回命中规则 id 列表。

    stock_info 期望字段 (前端 / screener 拼好后传入):
      streak: int              — 当前连板数 (0=未涨停)
      mcap_yi: float           — 总市值 (亿)
      limit_up_30d: int        — 近 30 日涨停次数
      vol_ratio: float         — 量比
      volume_ratio: float      — 倍量 (与 vol_ratio 同义, 兼容字段)
      advance_count: int       — 大盘上涨家数 (外部注入)
      high_3d_not_higher: bool — 3 根 K 线高点未抬高 (10:40 后)
      has_buy_reason: bool     — 是否有买入理由
      profit_pct: float        — 浮盈比例
      upper_shadow_ratio: float — 上影线 / 实体 比例
      consolidation_days: int  — 横盘天数
      streak_days_ago: int     — 距最近涨停天数
      above_streak_floor: bool — 是否未跌破涨停底价
      down_days: int           — 连跌天数
      vs_market_3d: str        — outperform / underperform / neutral
      price_vs_avgline: str    — near / below_after_open / above
      failed_break_avgline_2x: bool
      open_gap_pct: float
      first_5min_vol_ratio: float
      consecutive_loss_days: int
      rule_violated: bool

    返回: ["BV01","BV03",...] — 顺序按 priority, score_weight
    """
    rules = load_rules().get("rules", [])
    out: list[str] = []
    streak = stock_info.get("streak", 0)
    advance_count = stock_info.get("advance_count", 0)
    decline_count = stock_info.get("decline_count", 0)
    consecutive_loss_days = stock_info.get("consecutive_loss_days", 0)

    for r in rules:
        rid = r["id"]
        conditions = r.get("conditions", [])
        ok = True
        for cond in conditions:
            field = cond.get("field", "")
            op = cond.get("op", "==")
            value = cond.get("value")
            actual = stock_info.get(field)
            if actual is None:
                ok = False
                break
            if op == "==" and actual != value:
                ok = False; break
            if op == ">=" and not (actual >= value):
                ok = False; break
            if op == "<=" and not (actual <= value):
                ok = False; break
            if op == ">" and not (actual > value):
                ok = False; break
            if op == "<" and not (actual < value):
                ok = False; break
        # 额外语义条件
        if ok and rid == "BV05":
            ok = (
                1 <= streak <= 2
                and stock_info.get("mcap_yi", 999) <= 100
                and stock_info.get("streak_days_ago", 99) <= 15
                and stock_info.get("above_streak_floor", False)
            )
        if ok and rid == "BV06":
            ok = (
                stock_info.get("down_days", 0) >= 2
                and stock_info.get("vs_market_3d") == "outperform"
            )
        if ok and rid == "BV07":
            ok = stock_info.get("buy_time_in_window", False)
        if ok and rid == "BV08":
            ok = (
                stock_info.get("price_vs_avgline") == "near"
                and stock_info.get("first_min_vol_pattern") == "shrink_then_support"
            )
        if ok and rid == "BV10":
            ok = (
                stock_info.get("profit_pct", 0) > 0
                and stock_info.get("set_conditional_order_by_1040", False)
            )
        if ok and rid == "BV11":
            ok = (
                stock_info.get("price_vs_avgline") == "below_after_open"
                and stock_info.get("failed_break_avgline_2x", False)
            )
        if ok and rid == "BV12":
            ok = (
                stock_info.get("open_gap_pct", 0) >= 0.01
                and stock_info.get("first_5min_vol_ratio", 0) >= 1.5
            )
        if ok and rid == "BV14":
            ok = consecutive_loss_days >= 3

        if ok:
            out.append(rid)
    return out


def compute_score(matched_ids: list[str], stock_info: dict | None = None) -> tuple[float, int, int]:
    """根据命中规则算总分 — 加权和 + 量化 bonus, max 100。

    R-fix 2026-08-19: 之前只用命中规则加权, 所有涨停股都命中相同 3 条 → score 全部 25.8
      现在加量化 bonus: streak 多寡/seal 强弱/first_time 早晚, 让 score 有区分度
    公式: weighted_sum (规则命中) + bonus (量化) → 归一化到 100

    返回: (score, matched_count, weighted_sum)
    """
    if not matched_ids:
        return 0.0, 0, 0
    rules_by_id = {r["id"]: r for r in load_rules().get("rules", [])}
    weighted_sum = sum(rules_by_id.get(rid, {}).get("score_weight", 0) for rid in matched_ids)
    matched_count = len(matched_ids)

    # R-fix 2026-08-19: 量化 bonus (基于真实数据, 让 score 多样化)
    bonus = 0.0
    if stock_info and isinstance(stock_info, dict):
        streak = int(stock_info.get("streak", 0) or 0)
        seal = float(stock_info.get("seal_ratio", 0) or 0)
        first_time = str(stock_info.get("first_time", "") or "")
        turnover = float(stock_info.get("turnover_pct", 0) or 0)
        burst = int(stock_info.get("burst_count", 0) or 0)
        # 1) streak bonus: 1板+5, 2板+10, 3板+15, 4板+20, 5+ 板+25
        bonus += min(25, streak * 5)
        # 2) seal bonus: 强封单 0.15+ 给分, 0.3+ 满分 20
        if seal >= 0.15:
            bonus += min(20, (seal - 0.15) * 80)  # 0.15→0, 0.40→20
        # 3) first_time bonus: 越早封板分越高 (09:25=15, 10:40=0)
        try:
            ft = first_time.strip()
            if ":" in ft:
                hh, mm = int(ft.split(":")[0]), int(ft.split(":")[1])
            elif len(ft) >= 4 and ft[:4].isdigit():
                hh, mm = int(ft[:2]), int(ft[2:4])
            else:
                hh, mm = 99, 99  # 未知时间不给分
            minutes = hh * 60 + mm
            if minutes <= 9 * 60 + 30:
                bonus += 15  # 集合竞价涨停 +15
            elif minutes <= 10 * 60:
                bonus += 10
            elif minutes <= 10 * 60 + 30:
                bonus += 5
        except Exception:
            pass
        # 4) turnover bonus: 换手 >= 5% 给分
        if turnover >= 5:
            bonus += min(10, (turnover - 5) * 2)
        # 5) burst_count penalty: 炸板次数 (减分)
        if burst > 0:
            bonus -= min(15, burst * 5)

    total = weighted_sum + bonus
    # 归一化 0-100 (规则 max ~178, bonus max ~80, total max ~258)
    score = min(100.0, round(total * 100.0 / 258.0, 1))
    return score, matched_count, weighted_sum


def get_meta() -> dict:
    """战法 meta — 给前端顶部展示用。"""
    r = load_rules()
    return {
        "name": r.get("name", ""),
        "up": r.get("up", ""),
        "version": r.get("version", "v1"),
        "rule_count": len(r.get("rules", [])),
        "summary": r.get("summary", ""),
        "extracted_at": r.get("extracted_at", ""),
        "bvid": r.get("bvid", ""),
    }