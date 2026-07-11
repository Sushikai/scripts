"""
tuixue_v3/web/seat_classify.py
席位 6 类分类 + 资金占比计算 (按用户规格 v1)

固定优先级 (匹配成功即返回, 不再向下匹配):
1. northbound     北向资金 (沪股通专用 / 深股通专用)
2. institution    机构资金 (机构专用)
3. retail_lhasa   拉萨散户 (东方财富证券.*拉萨.*营业部)
4. quant          量化资金 (总部级 + 华宝/申港)
5. hot_money      一线游资 (顶级 + 中生代 + 部分席位型)
6. unknown        未知私有席位 (兜底)

每只上榜股输出:
- categories: 6 类汇总 (买入/卖出/净额/4 项占比/seat_count)
- intraday:   实时主力/散户买卖占比
- risks:      风险标签数组
- tags:       短线筛选标签 (优质 / 规避)
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("tuixue_v3.web.seat_classify")


# ── 6 类常量 (UI 渲染顺序, 与 seats 数组对应) ──────────
CATEGORIES: list[str] = [
    "northbound",
    "institution",
    "retail_lhasa",
    "quant",
    "hot_money",
    "unknown",
]

CATEGORY_LABEL = {
    "northbound":   "北向资金",
    "institution":  "机构资金",
    "retail_lhasa": "拉萨散户",
    "quant":        "量化资金",
    "hot_money":    "一线游资",
    "unknown":      "未知席位",
}

# 风险阈值 (规格 §三·模块3)
RISK_THRESHOLDS = {
    "quant_high":           25.0,   # 量化≥25% 砸盘风险
    "lhasa_high":           10.0,   # 拉萨≥10% 且无主流
    "single_seat_dushi":    10.0,   # 单一席位>10% 独食
    "main_line_converge":   15.0,   # 主线合力净买入>15%
}


# ── 量化席位列表 (规格 §二·标签4) ─────────────────
QUANT_KEYWORDS = [
    "华泰证券总部",
    "国泰君安证券总部",
    "中金公司总部",
    "中信证券总部",
    "开源证券西安西大街",
    "华宝证券上海浦东新区",
    "申港证券上海分公司",
]


# ── 拉萨散户正则 (规格 §二·标签3) ─────────────────
LASHA_REGEX = re.compile(r"东方财富证券.*拉萨.*营业部")


# ── 一线游资关键词 (规格 §二·标签5 + 用户 2026-07-11 13 大顶级清单) ───
# 每条: 营业部关键词 (用于 hot_money 分类命中)
HOT_MONEY_KEYWORDS = [
    # 1. 赵老哥 (赵强)
    "中国银河证券绍兴", "银河绍兴解放大道",
    # 2. 作手新一
    "国泰君安南京太平南路",
    # 3. 章盟主 (章建平)
    "国泰君安上海江苏路",
    # 4. 佛山系 (佛山无影脚)
    "光大证券佛山绿景路", "湘财佛山季华五路",
    # 5. 炒股养家
    "华鑫证券上海宛平南路", "华鑫上海茅台路",
    # 6. 宁波解放南 (敢死队老牌)
    "光大证券宁波解放南路",
    # 7. 桑田路
    "国盛证券宁波桑田路", "国盛宁波桑田路",
    # 8. 陈小群
    "中信建投上海大连路",
    # 9. 苏南帮
    "东吴证券无锡梁清路", "东吴证券无锡湖滨路",
    # 10. 上塘路 (情绪核按钮)
    "财通证券杭州上塘路",
    # 11. 劳动路
    "华鑫证券杭州劳动路",
    # 12. 溧阳路 (孙哥)
    "中信证券上海溧阳路",
    # 13. 刺客 (源深路)
    "中信上海源深路",
    # 旧版兜底关键词 (保留)
    "中信西安朱雀大街",
    "中信深圳欢乐海岸",
    "中信北京呼家楼",
    "光大深圳金田路",
    "中投无锡清扬路",
]


# ── 13 大顶级游资 江湖名号 + 风格 (用户 2026-07-11 提供) ──
# 格式: 关键词片段 → (江湖名号, 风格标签)
# 风格: 格局派 / 一日游 / 核按钮 / 通道派 / 高位接力 / 埋伏派
HOT_MONEY_ALIAS: list[tuple[str, str, str]] = [
    ("中国银河证券绍兴", "赵老哥", "格局派·龙头接力"),
    ("银河绍兴解放大道", "赵老哥", "格局派·龙头接力"),
    ("国泰君安南京太平南路", "作手新一", "高位接力·人气总龙"),
    ("国泰君安上海江苏路", "章盟主", "格局派·大资金波段"),
    ("光大证券佛山绿景路", "佛山系", "一日游·核按钮"),
    ("湘财佛山季华五路", "佛山系", "一日游·核按钮"),
    ("华鑫证券上海宛平南路", "炒股养家", "通道派·隔夜排板"),
    ("华鑫上海茅台路", "炒股养家", "通道派·隔夜排板"),
    ("光大证券宁波解放南路", "宁波解放南", "短线连板抱团"),
    ("国盛证券宁波桑田路", "桑田路", "高低切换·首板挖掘"),
    ("国盛宁波桑田路", "桑田路", "高低切换·首板挖掘"),
    ("中信建投上海大连路", "陈小群", "纯情绪高标"),
    ("东吴证券无锡梁清路", "苏南帮", "多席位联动·滚动 T"),
    ("东吴证券无锡湖滨路", "苏南帮", "多席位联动·滚动 T"),
    ("财通证券杭州上塘路", "上塘路", "一日游·次日砸盘"),
    ("华鑫证券杭州劳动路", "劳动路", "通道派·量化混合"),
    ("中信证券上海溧阳路", "溧阳路", "格局派·大资金波段"),
    ("中信上海源深路", "刺客", "埋伏派·低位冷门"),
]


def get_hot_money_alias(seat_name: str) -> tuple[str, str] | None:
    """返回 (江湖名号, 风格标签); 命中 HOT_MONEY_ALIAS 任一关键词即返回."""
    if not seat_name:
        return None
    name = str(seat_name).strip()
    for kw, alias, style in HOT_MONEY_ALIAS:
        if kw in name:
            return (alias, style)
    return None


# ── 核心: 席位名 → 6 类之一 (固定优先级) ─────────
def classify_seat(seat_name: str) -> str:
    """
    优先级 (按规格 §二, 顺序不可颠倒):
      northbound → institution → retail_lhasa → quant → hot_money → unknown
    """
    if not seat_name:
        return "unknown"
    name = str(seat_name).strip()

    # 1. 北向 (沪股通专用 / 深股通专用)
    if "沪股通专用" in name or "深股通专用" in name:
        return "northbound"

    # 2. 机构 (机构专用)
    if name == "机构专用":
        return "institution"

    # 3. 拉萨散户 (东方财富证券.*拉萨.*营业部)
    if LASHA_REGEX.search(name):
        return "retail_lhasa"

    # 4. 量化 (总部级 + 华宝/申港)
    for kw in QUANT_KEYWORDS:
        if kw in name:
            return "quant"

    # 5. 一线游资 (规格列表精确匹配)
    for kw in HOT_MONEY_KEYWORDS:
        if kw in name:
            return "hot_money"

    # 6. 兜底 (可叠加 seat_aliases.json: tier=顶级/中生代 → hot_money)
    # 优先级严格按规格 — 上面 5 个没匹配上, 即使在别名表里是顶级, 也归 unknown
    # 这样保证"按顺序正则匹配, 匹配成功打上唯一分类标签, 未匹配统一归为【未知私有席位】"
    return "unknown"


# ── 兜底补充: 允许 seat_aliases.json 的 tier=顶级/中生代 升级为 hot_money ─
# (用于更广覆盖, 但只在前面 5 类没匹配时才生效)
def classify_seat_with_aliases(seat_name: str, alias_tier: str = "") -> str:
    """
    在 classify_seat 的 5 类都没匹配上时,如果别名表标记为顶级/中生代 → hot_money.
    避免遗漏资深游资的新马甲席位.
    """
    primary = classify_seat(seat_name)
    if primary != "unknown":
        return primary
    if alias_tier in ("顶级", "中生代"):
        return "hot_money"
    return "unknown"


# ── 聚合: rows → 6 类汇总 + 占比 ────────────────
def categorize_rows(
    rows: list[dict],
    total_amount_wan: float | None,
    *,
    use_alias_tier: bool = True,
) -> dict:
    """
    rows: [{direction: '买入'|'卖出', seat, amount_wan, tier (optional)}]
    total_amount_wan: 个股当日总成交额 (万元), 用于算占比. None/0 时占比为 None.

    返回: {
      "categories": [{key, label, buy_wan, sell_wan, net_wan, buy_pct, sell_pct,
                       total_pct, net_pct, seat_count, seats:[{seat, direction, amount_wan}]}],
      "total_amount_wan": float|None,
    }
    """
    cats: dict[str, dict] = {
        k: {"key": k, "label": CATEGORY_LABEL[k],
            "buy_wan": 0.0, "sell_wan": 0.0, "net_wan": 0.0,
            "seat_count": 0, "seats": []}
        for k in CATEGORIES
    }
    for r in rows or []:
        seat = (r.get("seat") or "").strip()
        if not seat:
            continue
        direction = r.get("direction") or ""
        if direction not in ("买入", "卖出"):
            continue
        amt = r.get("amount_wan")
        try:
            amt = float(amt) if amt is not None else 0.0
        except (TypeError, ValueError):
            amt = 0.0
        if amt < 0:
            log.debug(f"seat_classify: 脏数据金额 {amt}, 跳过 seat={seat}")
            continue
        cls = classify_seat_with_aliases(seat, r.get("tier", "") if use_alias_tier else "")
        c = cats[cls]
        if direction == "买入":
            c["buy_wan"] += amt
        else:
            c["sell_wan"] += amt
        c["net_wan"] = round(c["buy_wan"] - c["sell_wan"], 2)
        c["seat_count"] += 1
        # 游资席位附 江湖名号 + 风格 (前端显示用)
        alias_info = get_hot_money_alias(seat) if cls == "hot_money" else None
        seat_entry: dict = {"seat": seat, "direction": direction,
                            "amount_wan": round(amt, 2)}
        if alias_info:
            seat_entry["alias"] = alias_info[0]
            seat_entry["style"] = alias_info[1]
        c["seats"].append(seat_entry)

    # 占比 (相对当日总成交额)
    ta = float(total_amount_wan) if total_amount_wan and total_amount_wan > 0 else None
    out_cats = []
    for k in CATEGORIES:
        c = cats[k]
        if ta:
            c["buy_pct"]   = round(c["buy_wan"] / ta * 100, 2)
            c["sell_pct"]  = round(c["sell_wan"] / ta * 100, 2)
            c["total_pct"] = round((c["buy_wan"] + c["sell_wan"]) / ta * 100, 2)
            c["net_pct"]   = round(c["net_wan"] / ta * 100, 2)
        else:
            c["buy_pct"] = c["sell_pct"] = c["total_pct"] = c["net_pct"] = None
        # seats 数组按金额降序
        c["seats"].sort(key=lambda x: -x["amount_wan"])
        c["seats"] = c["seats"][:8]
        # 浮点保留
        c["buy_wan"]  = round(c["buy_wan"], 2)
        c["sell_wan"] = round(c["sell_wan"], 2)
        c["net_wan"]  = round(c["net_wan"], 2)
        out_cats.append(c)

    return {
        "categories":       out_cats,
        "total_amount_wan": ta,
    }


# ── 实时主力/散户买卖占比 (规格 §三·模块2) ──────────
def compute_intraday_ratios(main_flow: dict | None) -> dict:
    """
    main_flow: {super_net, big_net, mid_net, small_net} (万元, 净流入)
    主力 = 超大单 + 大单
    散户 = 中单 + 小单

    返回:
      main_buy_pct, main_sell_pct, retail_buy_pct, retail_sell_pct, main_net_pct
      说明: 由于 akshare 只给净额, 这里用 abs(净) 作为买卖估算 —
            主力净额 = 主力买 - 主力卖 → 假设两侧对称
            main_buy = (|main_net| + main_net) / 2 ; main_sell = (|main_net| - main_net) / 2
      这个近似在主力和散户净流向一致时误差小. 数据源直给买卖分时再升级.
    """
    if not main_flow:
        return {
            "main_buy_pct": None, "main_sell_pct": None,
            "retail_buy_pct": None, "retail_sell_pct": None,
            "main_net_pct": None,
            "source": None,
        }
    try:
        super_n = float(main_flow.get("super_net") or 0)
        big_n   = float(main_flow.get("big_net") or 0)
        mid_n   = float(main_flow.get("mid_net") or 0)
        small_n = float(main_flow.get("small_net") or 0)
    except (TypeError, ValueError):
        return {
            "main_buy_pct": None, "main_sell_pct": None,
            "retail_buy_pct": None, "retail_sell_pct": None,
            "main_net_pct": None,
            "source": None,
        }

    main_net = super_n + big_n
    retail_net = mid_n + small_n

    # 拆买卖: (abs + signed) / 2 法
    def _split(net):
        a = abs(net)
        return ((a + net) / 2.0, (a - net) / 2.0)

    main_buy,   main_sell   = _split(main_net)
    retail_buy, retail_sell = _split(retail_net)

    total = main_buy + main_sell + retail_buy + retail_sell
    if total <= 0:
        return {
            "main_buy_pct": None, "main_sell_pct": None,
            "retail_buy_pct": None, "retail_sell_pct": None,
            "main_net_pct": None,
            "source": main_flow.get("source"),
        }
    return {
        "main_buy_pct":   round(main_buy / total * 100, 2),
        "main_sell_pct":  round(main_sell / total * 100, 2),
        "retail_buy_pct": round(retail_buy / total * 100, 2),
        "retail_sell_pct":round(retail_sell / total * 100, 2),
        "main_net_pct":   round(main_net / total * 100, 2),
        "source":         main_flow.get("source"),
    }


# ── 风险标记 (规格 §三·模块3) ─────────────────
def detect_risks(categorized: dict) -> list[str]:
    """
    categorized: categorize_rows 的输出
    返回风险标签数组 (中文短语)
    """
    risks: list[str] = []
    cats_by_key = {c["key"]: c for c in categorized.get("categories", [])}

    quant_total_pct = cats_by_key.get("quant", {}).get("total_pct") or 0
    if quant_total_pct >= RISK_THRESHOLDS["quant_high"]:
        risks.append(f"⚠️ 高量化风险 · 量化成交占比 {quant_total_pct:.1f}%≥25%")

    lhasa_buy_pct = cats_by_key.get("retail_lhasa", {}).get("buy_pct") or 0
    if lhasa_buy_pct >= RISK_THRESHOLDS["lhasa_high"]:
        # 检查主流是否净流入 (机构+北向+一线游资 净额 > 0)
        main_pcts = (
            (cats_by_key.get("institution", {}).get("net_pct") or 0) +
            (cats_by_key.get("northbound", {}).get("net_pct") or 0) +
            (cats_by_key.get("hot_money", {}).get("net_pct") or 0)
        )
        if main_pcts <= 0:
            risks.append(f"⚠️ 散户高潮接盘 · 拉萨买入 {lhasa_buy_pct:.1f}%≥10% 且无主流承接")

    # 单一席位独食 (>10% 占总成交额)
    for c in categorized.get("categories", []):
        for s in c.get("seats", []):
            single_pct = None
            if categorized.get("total_amount_wan"):
                single_pct = round(s["amount_wan"] / categorized["total_amount_wan"] * 100, 2)
            if single_pct and single_pct > RISK_THRESHOLDS["single_seat_dushi"]:
                risks.append(f"⚠️ 独食风险 · {s['seat']} {s['direction']} {single_pct:.1f}%>10%")
                break  # 一个类别只报一次
        else:
            continue
        break

    main_line_net_pct = (
        (cats_by_key.get("institution", {}).get("net_pct") or 0) +
        (cats_by_key.get("northbound", {}).get("net_pct") or 0) +
        (cats_by_key.get("hot_money", {}).get("net_pct") or 0)
    )
    if main_line_net_pct > RISK_THRESHOLDS["main_line_converge"]:
        risks.append(f"✅ 主线合力 · 机构+北向+游资净买入 {main_line_net_pct:.1f}%>15%")

    return risks


# ── 短线筛选标签 (规格 §补充业务逻辑) ─────────────
def screen_tags(categorized: dict) -> list[str]:
    """
    优质: 机构+北向+游资合力净流入 + 量化占比 < 20%
    规避: 量化 ≥ 25% 或 拉萨高占比且无主流承接
    """
    tags: list[str] = []
    cats_by_key = {c["key"]: c for c in categorized.get("categories", [])}

    main_line_net_pct = (
        (cats_by_key.get("institution", {}).get("net_pct") or 0) +
        (cats_by_key.get("northbound", {}).get("net_pct") or 0) +
        (cats_by_key.get("hot_money", {}).get("net_pct") or 0)
    )
    quant_total_pct = cats_by_key.get("quant", {}).get("total_pct") or 0
    lhasa_buy_pct = cats_by_key.get("retail_lhasa", {}).get("buy_pct") or 0

    if main_line_net_pct > 0 and quant_total_pct < 20.0:
        tags.append("✅ 优质 · 主线合力 + 量化可控")
    if quant_total_pct >= RISK_THRESHOLDS["quant_high"]:
        tags.append("❌ 规避 · 量化主导")
    if lhasa_buy_pct >= RISK_THRESHOLDS["lhasa_high"] and main_line_net_pct <= 0:
        tags.append("❌ 规避 · 拉萨扎堆无主流")
    return tags


# ── 一站式: 拉龙虎榜 + 实时资金 → 输出全部结构 ──────
def build_breakdown(code: str) -> dict:
    """
    拉取:
    - seat_lookup.get_stock_seats(code, lookback_days=10)  → 近期席位
    - fund_flow.get_main_flow(code)                        → 今日实时主力
    - data_layer.fetch_daily(code, 1)                      → 当日总成交额
    输出 6 类汇总 + 实时主力散户占比 + 风险 + 短线标签
    """
    from . import seat_lookup, fund_flow
    try:
        from .. import data_layer
    except Exception:
        data_layer = None

    seats_data = seat_lookup.get_stock_seats(code, lookback_days=10)
    rows = seats_data.get("rows", []) or []
    # 只取最近 1 天的席位做"当日"分类 (避免 lookback 把历史席位都算进来)
    if rows:
        last_date = max((r.get("date") or "") for r in rows)
        rows_today = [r for r in rows if r.get("date") == last_date]
    else:
        rows_today = []

    # 当日总成交额 (从行情拿 — 优先 main_flow.total_amount_wan, 否则日线 amount 兜底)
    main_flow = fund_flow.get_main_flow(code) or {}
    total_amount_wan = main_flow.get("total_amount_wan")
    if not total_amount_wan and data_layer is not None:
        try:
            df = data_layer.fetch_daily(code, 1)
            if df is not None and not df.empty:
                row = df.iloc[-1]
                amt = float(row.get("成交额", 0) or 0)
                total_amount_wan = round(amt / 1e4, 2) if amt else None
        except Exception:
            pass

    categorized = categorize_rows(rows_today, total_amount_wan)
    intraday = compute_intraday_ratios(main_flow)
    risks = detect_risks(categorized)
    tags = screen_tags(categorized)

    return {
        "code":             code,
        "rows":             rows_today,
        "all_rows_count":   len(rows),
        "last_date":        rows_today[0].get("date") if rows_today else None,
        "categories":       categorized["categories"],
        "total_amount_wan": categorized["total_amount_wan"],
        "intraday":         intraday,
        "risks":            risks,
        "tags":             tags,
        "fetch_ts":         rows_today[0].get("date") if rows_today else None,
    }