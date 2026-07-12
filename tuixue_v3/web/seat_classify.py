"""
tuixue_v3/web/seat_classify.py
席位分类字典 (用户 2026-07-12 提供)

5 大资金大类:
  1. institution    机构专用 (中线价值资金,非游资)
  2. northbound     北向资金 (外资通道,非游资)
  3. quant          量化程序化 (机器资金,非传统人工游资)
  4. retail_lhasa   散户席位 (东方财富拉萨天团,反向指标)
  5. hot_money      游资 — 进一步分 3 档:
     5.1 hot_tier1  顶级一线游资 (10 大佬,5000万~数亿,带外号)
     5.2 hot_tier2  二线区域游资 (600~2000万,无全国知名大佬外号)
     5.3 hot_tier3  三线微型大户 (几十万,偏远地市,上榜零散)
  6. unknown        兜底

每只上榜股输出:
- categories: 8 类汇总 (买入/卖出/净额/4 项占比/seat_count)
- intraday:   实时主力/散户买卖占比
- risks:      全局风险/积极标签数组
- tags:       短线筛选标签 (优质 / 规避)
- signals:    {positive:[...], warning:[...]}  单股所有席位信号汇总
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("tuixue_v3.web.seat_classify")


# ── 8 类常量 (UI 渲染顺序;游资分 3 档) ──────────
CATEGORIES: list[str] = [
    "northbound",     # 1. 北向资金
    "institution",    # 2. 机构专用
    "retail_lhasa",   # 3. 散户席位
    "quant",          # 4. 量化席位
    "hot_tier1",      # 5.1 顶级一线游资
    "hot_tier2",      # 5.2 二线区域游资
    "hot_tier3",      # 5.3 三线微型大户
    "unknown",        # 兜底
]

CATEGORY_LABEL = {
    "northbound":    "北向资金",
    "institution":   "机构专用",
    "retail_lhasa":  "散户·拉萨天团",
    "quant":         "量化程序化",
    "hot_tier1":     "顶级一线游资",
    "hot_tier2":     "二线区域游资",
    "hot_tier3":     "三线微型大户",
    "unknown":       "未知席位",
}

# 风险/积极阈值 (用户字典 §六·全局规则)
SIGNAL_THRESHOLDS = {
    # 警惕阈值
    "quant_high":          30.0,   # 量化 ≥30% → 警惕 (用户字典:买一独大 >30%)
    "lhasa_high":          10.0,   # 拉萨 ≥10% → 警惕 (≥2家或买一独大)
    "lhasa_count_warn":     2,     # 拉萨席位数量 ≥2 → 警惕
    "single_seat_dushi":   30.0,   # 单一席位 >30% 总成交 → 警惕独食
    "no_main_force":        0.0,   # 主流(机构+北向+顶级游资)无净流入
    # 积极阈值
    "main_line_converge":  15.0,   # 主流合力净买入 >15%
    "multi_hot_buy":        3,     # 买方 ≥3 家不同派系顶级游资
}


# ═══════════════════════════════════════════════════
# 席位字典 (用户 2026-07-12 提供)
# 每条: keyword (substring 命中) → metadata
# priority: 同一席位可能被多个 kw 匹配,优先级 = 字典序后写后赢
# ═══════════════════════════════════════════════════

# ── 1. 机构专用 (中线价值资金,非游资) ─────────
# 识别关键词: 席位名含「机构专用」
INSTITUTION_KEYWORDS = [
    "机构专用",
]

# ── 2. 北向资金 ─────────────────────
NORTHBOUND_KEYWORDS = [
    "沪股通专用",
    "深股通专用",
]

# ── 3. 量化席位 (机器资金,非传统人工游资) ─────
QUANT_KEYWORDS = [
    "华泰证券总部",
    "华泰证券上海分公司",      # 2026-07-12 字典未列,与 中金上海分公司 同类量化
    "中国国际金融上海分公司",   # 中金上海
    "中金财富北京宋庄路",
    "开源证券西安太华路",
    "开源证券西安西大街",
    "开源证券西安丈八一路",
    # 兜底关键词 (旧版)
    "国泰君安证券总部",
    "中金公司总部",
    "中信证券总部",
    "中信证券上海分公司",      # 量化常见变种
    "华宝证券上海浦东新区",
    "申港证券上海分公司",
]

# ── 4. 散户席位 (东方财富拉萨天团) ──────────
LASHA_REGEX = re.compile(r"东方财富证券.*拉萨.*营业部")
# 拉萨 4 大营业部
LASHA_BRANCHES = [
    "东方财富证券拉萨团结路第一",
    "东方财富证券拉萨团结路第二",
    "东方财富证券拉萨东环路第一",
    "东方财富证券拉萨东环路第二",
]


# ── 5.1 顶级一线游资 (10 大佬,资金 5000万~数亿) ─────
# 每条: keyword, alias (江湖名号), real_name, style, positive, warning
HOT_TIER1: list[dict] = [
    # 1. 章盟主 (章建平) — 顶级一线
    {
        "keywords": ["国泰君安上海江苏路", "中信杭州延安路", "国泰君安宁波彩虹北路"],
        "alias": "章盟主", "real_name": "章建平",
        "style": "主升浪大票·波段格局·极少一日游,擅长主线趋势龙头",
        "positive": "低位大额介入、多游资协同、单票5000万以上净买；持续性极强",
        "warning": "高位连续多日锁仓后放量卖出、独食占比超40%",
    },
    # 2. 赵老哥 (赵强)
    {
        "keywords": ["中国银河绍兴营业部", "浙商证券绍兴分公司"],
        "alias": "赵老哥", "real_name": "赵强",
        "style": "二板定龙头·连板接力,不及预期快速砸盘离场",
        "positive": "主线分歧日承接、低位首板重仓、多家游资共振",
        "warning": "高位加速板独买、榜单无其他资金接力",
    },
    # 3. 炒股养家
    {
        "keywords": [
            "华鑫证券上海分公司", "华鑫上海宛平南路", "华鑫上海红宝石路",
            # 兼容全名 — 实际席位名常带 "证券"
            "华鑫证券上海宛平南路", "华鑫证券上海红宝石路", "华鑫证券上海分公司",
        ],
        "alias": "炒股养家", "real_name": "网传林广昌",
        "style": "通道排一字·情绪周期拐点·分歧低吸一致卖出",
        "positive": "题材启动前排板、低位分歧介入,带动板块一字溢价",
        "warning": "高位一字板养家独买,隔日集中兑现",
    },
    # 4. 孙哥 (孙煜)
    {
        "keywords": ["中信上海溧阳路", "中信上海古北路"],
        "alias": "孙哥", "real_name": "孙煜",
        "style": "妖股连板·板块带动·趋势加速,擅长做大行情",
        "positive": "主线龙头分歧加仓、多席位协同锁仓",
        "warning": "高位连续大涨后溧阳路大额卖出",
    },
    # 5. 作手新一
    {
        "keywords": ["国泰君安南京太平南路"],
        "alias": "作手新一", "real_name": "网传严冬",
        "style": "主线20cm连板·次新·分歧扫板,分仓不独食",
        "positive": "低位首板/二板大额承接、搭配机构合力",
        "warning": "高位四板以上独买,无其他资金助攻",
    },
    # 6. 方新侠
    {
        "keywords": ["兴业证券陕西分公司", "国金深圳分公司"],
        "alias": "方新侠", "real_name": "—",
        "style": "大成交额趋势龙头·中线格局,游资机构双修",
        "positive": "赛道低位回调买入、游资机构同榜",
        "warning": "高位放量出逃、单一席位包揽买榜",
    },
    # 7. 小鳄鱼
    {
        "keywords": ["平安证券杭州曙光路", "南京大钟亭"],
        "alias": "小鳄鱼", "real_name": "—",
        "style": "连板龙头·20cm弹性小票·分歧低吸",
        "positive": "题材发酵期接力、多游资联动",
        "warning": "高位加速板独食买入",
    },
    # 8. 佛山无影脚 (正宗佛山一线)
    {
        "keywords": ["光大佛山季华六路", "国泰君安三亚迎宾路"],
        "alias": "佛山无影脚", "real_name": "—",
        "style": "首板一阳指·翘跌停·当日封板次日兑现",
        "positive": "低位首板撬板、个股逻辑扎实",
        "warning": "高位首板独买,次日直接砸盘",
    },
    # 9. 宁波桑田路
    {
        "keywords": ["国盛证券宁波桑田路"],
        "alias": "宁波桑田路", "real_name": "—",
        "style": "小盘题材·跌停撬板·分歧五板接力",
        "positive": "恐慌跌停大额承接、题材启动试错",
        "warning": "高位高标独买,无合力容易核按钮",
    },
    # 10. 陈小群
    {
        "keywords": ["中信大连黄河路", "中信西安朱雀大街"],
        "alias": "陈小群", "real_name": "—",
        "style": "大成交额趋势龙头·高标锁仓",
        "positive": "主线低位持续加仓、搭配机构",
        "warning": "连续加速后大额卖出",
    },
    # ── 旧版兜底顶级游资 (2026-07-12 字典未列入,保留兼容) ──
    {
        "keywords": ["光大证券宁波解放南路"],
        "alias": "宁波解放南", "real_name": "—",
        "style": "短线连板抱团·老牌敢死队",
        "positive": "题材发酵接力",
        "warning": "高位连板独食",
    },
    {
        "keywords": ["东吴证券无锡梁清路", "东吴证券无锡湖滨路"],
        "alias": "苏南帮", "real_name": "—",
        "style": "多席位联动·滚动 T",
        "positive": "板块协同滚动",
        "warning": "高位对倒",
    },
    {
        "keywords": ["财通证券杭州上塘路"],
        "alias": "上塘路", "real_name": "—",
        "style": "一日游·次日砸盘",
        "positive": "题材首板试错",
        "warning": "次日直接核按钮",
    },
    {
        "keywords": ["华鑫证券杭州劳动路"],
        "alias": "劳动路", "real_name": "—",
        "style": "通道派·量化混合",
        "positive": "搭配机构合力",
        "warning": "独立上榜无支撑",
    },
    {
        "keywords": ["中信建投上海大连路"],
        "alias": "陈小群(分支)", "real_name": "—",
        "style": "纯情绪高标",
        "positive": "主线龙头加仓",
        "warning": "高标断板砸盘",
    },
    {
        "keywords": ["中信上海源深路"],
        "alias": "刺客", "real_name": "—",
        "style": "埋伏派·低位冷门",
        "positive": "低位首板潜伏",
        "warning": "高位接力失利",
    },
    {
        "keywords": ["中信深圳欢乐海岸"],
        "alias": "欢乐海", "real_name": "—",
        "style": "高位接力",
        "positive": "主线龙头",
        "warning": "高位砸盘",
    },
    {
        "keywords": ["光大深圳金田路"],
        "alias": "金田路", "real_name": "—",
        "style": "短线接力",
        "positive": "主线首板",
        "warning": "高位独食",
    },
]


# ── 5.2 二线区域游资 (用户问题中两个席位归此类) ─────
# 特征: 单次上榜 600~2000 万, 区域本地大户, 仅跟风助攻, 隔日兑现
HOT_TIER2_SPECIFIC: list[dict] = [
    # 用户特别指出的两个席位
    {
        "keywords": ["方正证券股份有限公司温岭安平东路证券营业部",
                     "方正证券温岭安平东路"],
        "alias": "温岭安平东路", "real_name": "—",
        "style": "浙东台州系·千万级题材套利·隔日兑现·联动台州绍兴本地营业部,无龙头主导能力",
        "tier": "二线区域",
        "positive": "榜单同时存在≥1家顶级一线游资+机构合力,仅作为助攻资金",
        "warning": "该席位买一独大、无一线游资加持,次日冲高回落概率极高",
    },
    {
        "keywords": ["华鑫证券有限责任公司佛山南海海五路证券营业部",
                     "华鑫佛山南海海五路"],
        "alias": "佛山南海海五路", "real_name": "—",
        "style": "佛山本地跟风小资金(非正宗佛山无影脚)·小票首板套利,体量不足2000万,无板块号召力",
        "tier": "二线区域",
        "positive": "榜单同步出现光大佛山季华六路/国泰君安三亚迎宾路(正宗佛山一线)",
        "warning": "单独上榜、高位题材买入,次日抛压巨大",
    },
]


# ── 5.3 三线微型大户 / 散户混合 ─────────
# 特征: 偏远地市营业部, 单笔上榜几十万, 上榜零散无规律
HOT_TIER3_HINTS = [
    # 兜底识别: 地市营业部 + 无顶级/二线关键词 → 默认 tier3
    # (实际分类在 classify_seat 里靠 keyword 顺序兜底)
]


# ── 通用关键词 (从所有顶级 + 二线 spec 字典抽出) ──
def _flatten_keywords(*sources) -> list[str]:
    out = []
    for src in sources:
        if isinstance(src, dict):
            for kw in src.get("keywords", []):
                out.append(kw)
        elif isinstance(src, list):
            for item in src:
                if isinstance(item, dict):
                    for kw in item.get("keywords", []):
                        out.append(kw)
                elif isinstance(item, str):
                    out.append(item)
    return out


# 主索引: keyword → (category, alias_meta)
# 后注册的覆盖前面的 (Python dict 重复 key 后写后赢)
SEAT_INDEX: dict[str, tuple[str, dict]] = {}


def _register(category: str, kw: str, meta: dict) -> None:
    SEAT_INDEX[kw] = (category, meta)


def _build_seat_index() -> None:
    """构造 keyword → (category, alias_meta) 索引。

    优先级 (后写覆盖先写):
      northbound → institution → retail_lhasa → quant →
      hot_tier1 (顶级 1.0 + 旧版兜底) → hot_tier2 (具体 2 个 + 通用区域) →
      unknown (默认)
    """
    SEAT_INDEX.clear()
    # 1) 北向
    for kw in NORTHBOUND_KEYWORDS:
        _register("northbound", kw, {"alias": "北向资金", "style": "外资长线/短线套利"})
    # 2) 机构
    for kw in INSTITUTION_KEYWORDS:
        _register("institution", kw, {
            "alias": "机构专用",
            "style": "公募/社保/险资/私募自营·基本面驱动·波段锁仓·极少一日游,偏好赛道龙头",
        })
    # 3) 散户拉萨
    for kw in LASHA_BRANCHES:
        _register("retail_lhasa", kw, {
            "alias": "拉萨天团",
            "style": "线上散户集中·追高猛·恐慌踩踏·纯换手博弈,反向指标",
        })
    # 4) 量化
    for kw in QUANT_KEYWORDS:
        _register("quant", kw, {
            "alias": "量化席位",
            "style": "高频拆单·日内反复T·平铺多只小票·点火后次日砸盘",
        })
    # 5.1) 顶级一线 (用户字典 10 大佬 + 旧版兜底)
    for src in HOT_TIER1:
        for kw in src["keywords"]:
            _register("hot_tier1", kw, {
                "alias": src["alias"],
                "real_name": src.get("real_name", "—"),
                "style": src["style"],
                "positive": src.get("positive", ""),
                "warning": src.get("warning", ""),
                "tier": "顶级一线",
            })
    # 5.2) 二线区域 (用户特别指出的 2 个席位)
    for src in HOT_TIER2_SPECIFIC:
        for kw in src["keywords"]:
            _register("hot_tier2", kw, {
                "alias": src["alias"],
                "style": src["style"],
                "positive": src.get("positive", ""),
                "warning": src.get("warning", ""),
                "tier": "二线区域",
            })


_build_seat_index()


# ── 兜底: 通用二线区域模板 (用户字典 §五.2 通用补充模板) ──
# "XX证券...路/街/大道/营业部" + 单次上榜 ≥600 万 → 二线
# 实际席位名常省略 "市" 字,兼容两种
REGIONAL_BRANCH_REGEX = re.compile(
    r"^[一-龥]{2,4}证券"          # 券商简称 (国海/华泰/方正...)
    r".+?"                          # 中间任意 (城市 + 路名)
    r"(?:路|街|大道|营业部)$"        # 末尾必须是 路/街/大道/营业部
)
# 单笔上榜 ≥600 万 (万元)
REGIONAL_BRANCH_MIN_WAN = 600.0


# ── 兜底: 三线微型大户 (用户字典 §五.3) ──
# 偏远地市营业部 + 单笔上榜 <100 万 → tier3
MICRO_BRANCH_MAX_WAN = 100.0


# ── 核心: 席位名 + 金额 → 8 类之一 ─────────
def classify_seat(seat_name: str, amount_wan: float | None = None) -> str:
    """
    优先级 (按用户字典 §六·1):
      northbound → institution → retail_lhasa → quant →
      hot_tier1 → hot_tier2 (具体) → hot_tier2 (通用正则 + 金额) →
      hot_tier3 (偏远 + 金额小) → unknown
    """
    if not seat_name:
        return "unknown"
    name = str(seat_name).strip()

    # 1) 北向
    for kw in NORTHBOUND_KEYWORDS:
        if kw in name:
            return "northbound"

    # 2) 机构
    if "机构专用" in name:
        return "institution"

    # 3) 拉萨散户
    if LASHA_REGEX.search(name):
        return "retail_lhasa"

    # 4) 量化
    for kw in QUANT_KEYWORDS:
        if kw in name:
            return "quant"

    # 5.1) 顶级一线 (字典匹配 — keyword substring)
    for src in HOT_TIER1:
        for kw in src["keywords"]:
            if kw in name:
                return "hot_tier1"

    # 5.2) 二线 — 具体席位优先
    for src in HOT_TIER2_SPECIFIC:
        for kw in src["keywords"]:
            if kw in name:
                return "hot_tier2"

    # 5.2) 二线 — 通用区域模板 (XX证券XX市XX路)
    if REGIONAL_BRANCH_REGEX.match(name):
        amt = float(amount_wan or 0)
        if amt >= REGIONAL_BRANCH_MIN_WAN:
            return "hot_tier2"
        # 偏远地市 + 金额 < 100 万 → 三线
        if amt < MICRO_BRANCH_MAX_WAN:
            return "hot_tier3"

    # 兜底 — 走 seat_aliases.json (外部覆盖层) 看是否标记为顶级/中生代
    tier = _lookup_alias_tier(name)
    if tier in ("顶级", "中生代"):
        return "hot_tier1"

    return "unknown"


# ── 外部覆盖层: seat_aliases.json ──────────
_ALIAS_TIER_CACHE: dict[str, str] | None = None
_ALIAS_TIER_PATH = Path(__file__).resolve().parent.parent / "data" / "seat_aliases.json"


def _lookup_alias_tier(seat_name: str) -> str | None:
    """从 data/seat_aliases.json 读 tier 标记,用于外部覆盖层。"""
    global _ALIAS_TIER_CACHE
    if _ALIAS_TIER_CACHE is None:
        try:
            if _ALIAS_TIER_PATH.exists():
                data = json.loads(_ALIAS_TIER_PATH.read_text(encoding="utf-8"))
                cache = {}
                for group in ("_top", "_mid", "_tier2", "_tier3"):
                    for alias, meta in (data.get(group) or {}).items():
                        t = (meta.get("tier") or "").strip()
                        for kw in meta.get("keywords", []):
                            if kw and kw not in cache:
                                cache[kw] = t
                _ALIAS_TIER_CACHE = cache
            else:
                _ALIAS_TIER_CACHE = {}
        except Exception as e:
            log.debug(f"seat_aliases.json 加载失败: {e}")
            _ALIAS_TIER_CACHE = {}
    return _ALIAS_TIER_CACHE.get(seat_name)


# ── 取席位 metadata (alias / style / positive / warning) ──
def get_seat_meta(seat_name: str, amount_wan: float | None = None) -> dict | None:
    """返回 {category, alias, real_name, style, positive, warning, tier}"""
    if not seat_name:
        return None
    name = str(seat_name).strip()
    # 先按 keyword 命中
    for kw, (cat, meta) in SEAT_INDEX.items():
        if kw in name:
            return {"category": cat, **meta, "matched_keyword": kw}
    # 拉萨正则单独处理
    if LASHA_REGEX.search(name):
        return {"category": "retail_lhasa", "alias": "拉萨天团",
                "style": "线上散户集中·追高猛·恐慌踩踏·纯换手博弈",
                "matched_keyword": "拉萨"}
    # 通用区域兜底
    cat = classify_seat(name, amount_wan)
    if cat == "hot_tier2":
        return {"category": "hot_tier2", "alias": "二线区域游资",
                "style": "地市营业部·题材套利·隔日兑现·无龙头主导能力",
                "tier": "二线区域",
                "matched_keyword": "(通用区域)"}
    if cat == "hot_tier3":
        return {"category": "hot_tier3", "alias": "三线微型大户",
                "style": "偏远地市·上榜零散无规律·资金杂乱",
                "tier": "三线微型",
                "matched_keyword": "(偏远)"}
    if cat != "unknown":
        return {"category": cat}
    return {"category": "unknown", "alias": "未知席位", "matched_keyword": None}


# ── 取游资 alias (兼容旧 API) ────────
def get_hot_money_alias(seat_name: str) -> tuple[str, str] | None:
    """返回 (alias, style); 命中 tier1/2 任一关键词即返回."""
    m = get_seat_meta(seat_name)
    if m and m.get("category") in ("hot_tier1", "hot_tier2"):
        return (m.get("alias", ""), m.get("style", ""))
    return None


# ── 聚合: rows → 8 类汇总 + 占比 ────────────────
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
                       total_pct, net_pct, seat_count, seats:[{seat, direction, amount_wan, alias, style, ...}]}],
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
        cls = classify_seat_with_aliases(seat, r.get("tier", "") if use_alias_tier else "", amt)
        c = cats[cls]
        if direction == "买入":
            c["buy_wan"] += amt
        else:
            c["sell_wan"] += amt
        c["net_wan"] = round(c["buy_wan"] - c["sell_wan"], 2)
        c["seat_count"] += 1
        # 席位 metadata (含 alias / style / positive / warning)
        meta = get_seat_meta(seat, amt) or {}
        seat_entry: dict = {
            "seat": seat, "direction": direction,
            "amount_wan": round(amt, 2),
            "alias": meta.get("alias", ""),
            "style": meta.get("style", ""),
            "positive": meta.get("positive", ""),
            "warning": meta.get("warning", ""),
            "tier": meta.get("tier", ""),
        }
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
            c["net_pct"]   = round(c["net_wan"] / ta * 100, 100)
        else:
            c["buy_pct"] = c["sell_pct"] = c["total_pct"] = c["net_pct"] = None
        # seats 按金额降序
        c["seats"].sort(key=lambda x: -x["amount_wan"])
        c["seats"] = c["seats"][:8]
        c["buy_wan"]  = round(c["buy_wan"], 2)
        c["sell_wan"] = round(c["sell_wan"], 2)
        c["net_wan"]  = round(c["net_wan"], 2)
        out_cats.append(c)

    return {
        "categories":       out_cats,
        "total_amount_wan": ta,
    }


def classify_seat_with_aliases(seat_name: str, alias_tier: str = "", amount_wan: float | None = None) -> str:
    """兼容旧 API。"""
    primary = classify_seat(seat_name, amount_wan)
    if primary != "unknown":
        return primary
    if alias_tier in ("顶级", "中生代"):
        return "hot_tier1"
    if alias_tier in ("二线",):
        return "hot_tier2"
    if alias_tier in ("三线",):
        return "hot_tier3"
    return "unknown"


# ── 实时主力/散户买卖占比 (无变动) ──────────
def compute_intraday_ratios(main_flow: dict | None) -> dict:
    """
    main_flow: {super_net, big_net, mid_net, small_net} (万元, 净流入)
    主力 = 超大单 + 大单
    散户 = 中单 + 小单
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


# ═══════════════════════════════════════════════════
# 全局信号 (用户字典 §六·2 + §六·3)
# ═══════════════════════════════════════════════════

def _main_force_net_pct(cats_by_key: dict) -> float:
    """主流 (机构 + 北向 + 顶级一线游资) 净额 占总成交 %"""
    return (
        (cats_by_key.get("institution", {}).get("net_pct") or 0) +
        (cats_by_key.get("northbound", {}).get("net_pct") or 0) +
        (cats_by_key.get("hot_tier1", {}).get("net_pct") or 0)
    )


def _unique_hot_tier1_aliases(cats_by_key: dict, side: str = "buy") -> set[str]:
    """统计顶级一线游资的不同派系 (按 alias 去重) 数。side: 'buy' / 'sell' / 'all'"""
    aliases = set()
    for s in (cats_by_key.get("hot_tier1", {}).get("seats") or []):
        if s.get("direction") == "买入" or side == "all":
            if s.get("alias"):
                aliases.add(s["alias"])
        if side == "all" and s.get("direction") == "卖出":
            if s.get("alias"):
                aliases.add(s["alias"])
    return aliases


def detect_risks(categorized: dict) -> dict:
    """
    按用户字典 §六·2/3 出全局积极/警惕信号 + 单席位独食 + 主线合力。

    返回: {
      "risks": ["⚠️ xxx", ...],          # 警惕 + 积极的混合
      "signals": {"positive": [...], "warning": [...]},
    }
    """
    cats_by_key = {c["key"]: c for c in categorized.get("categories", [])}
    risks: list[str] = []
    positive: list[str] = []
    warning: list[str] = []

    # ─── 警惕信号 ───
    # 1. 量化 ≥30% (买一独大)
    quant_total_pct = cats_by_key.get("quant", {}).get("total_pct") or 0
    if quant_total_pct >= SIGNAL_THRESHOLDS["quant_high"]:
        warning.append(f"量化主导 · 量化席位买一独大占比 {quant_total_pct:.1f}%≥30%")
    # 2. 拉萨 ≥10% 买盘 且无主流承接
    lhasa_buy_pct = cats_by_key.get("retail_lhasa", {}).get("buy_pct") or 0
    lhasa_seats = cats_by_key.get("retail_lhasa", {}).get("seats") or []
    lhasa_count = len(lhasa_seats)
    main_force = _main_force_net_pct(cats_by_key)
    if lhasa_buy_pct >= SIGNAL_THRESHOLDS["lhasa_high"] and main_force <= SIGNAL_THRESHOLDS["no_main_force"]:
        warning.append(f"散户高潮接盘 · 拉萨买入 {lhasa_buy_pct:.1f}%≥10% 且无主流承接")
    if lhasa_count >= SIGNAL_THRESHOLDS["lhasa_count_warn"]:
        warning.append(f"拉萨天团扎堆 · 买方 {lhasa_count} 家≥2 散户席位")
    # 3. 单一席位独食 (>30% 占总成交额)
    single_seat_dushi_pct = None
    single_seat_dushi_label = None
    for c in categorized.get("categories", []):
        for s in c.get("seats", []):
            ta = categorized.get("total_amount_wan")
            if ta and ta > 0 and s.get("amount_wan"):
                pct = round(s["amount_wan"] / ta * 100, 2)
                if pct > SIGNAL_THRESHOLDS["single_seat_dushi"]:
                    single_seat_dushi_pct = pct
                    single_seat_dushi_label = s.get("alias") or s["seat"]
                    warning.append(
                        f"独食风险 · {s['seat']} ({single_seat_dushi_label}) "
                        f"{s['direction']} {pct:.1f}%>30%"
                    )
                    break
        if single_seat_dushi_pct:
            break
    # 4. 同一席位同时出现买卖榜 (对倒)
    all_buy = set()
    all_sell = set()
    for c in categorized.get("categories", []):
        for s in c.get("seats", []):
            if s["direction"] == "买入":
                all_buy.add(s["seat"])
            else:
                all_sell.add(s["seat"])
    overlap = all_buy & all_sell
    if overlap:
        warning.append(f"对倒风险 · 同一席位买卖双现: {', '.join(list(overlap)[:3])}")
    # 5. 多家量化买榜第一 (无机构/顶级对冲)
    quant_seats = cats_by_key.get("quant", {}).get("seats") or []
    if len(quant_seats) >= 2 and not (cats_by_key.get("institution", {}).get("seats")
                                       or cats_by_key.get("hot_tier1", {}).get("seats")):
        warning.append(f"量化扎堆 · {len(quant_seats)} 家量化上榜且无机构/顶级对冲")
    # 6. 卖方多机构大额 + 游资独力承接无力
    inst_sell = cats_by_key.get("institution", {}).get("sell_wan", 0) or 0
    if inst_sell > 0 and cats_by_key.get("hot_tier1", {}).get("buy_wan", 0) < inst_sell * 0.5:
        warning.append(
            f"机构兑现 · 机构卖 {inst_sell:.0f} 万 > 顶级游资接盘能力"
        )
    # 7. 高位连板仅二线 / 三线游资 (无一线)
    if (cats_by_key.get("hot_tier2", {}).get("seat_count", 0) > 0
        or cats_by_key.get("hot_tier3", {}).get("seat_count", 0) > 0) \
            and cats_by_key.get("hot_tier1", {}).get("seat_count", 0) == 0:
        warning.append("高位连板 · 仅二/三线游资上榜,无一线资金,慎追")
    # 8. 北向双向同时出现 (外资分歧)
    nb_buy = cats_by_key.get("northbound", {}).get("buy_wan", 0) or 0
    nb_sell = cats_by_key.get("northbound", {}).get("sell_wan", 0) or 0
    if nb_buy > 0 and nb_sell > 0:
        warning.append(f"北向分歧 · 买卖双现 (买 {nb_buy:.0f} / 卖 {nb_sell:.0f} 万)")
    # 9. 高位多机构卖出
    nb = cats_by_key.get("institution", {})
    if (nb.get("sell_wan") or 0) > 0 and (nb.get("net_wan") or 0) < 0:
        warning.append(f"机构兑现 · 净卖出 {abs(nb.get('net_wan')):.0f} 万")

    # ─── 积极信号 ───
    # 1. 主流合力净买入 >15% (机构 + 北向 + 顶级一线)
    if main_force > SIGNAL_THRESHOLDS["main_line_converge"]:
        positive.append(f"主线合力 · 机构+北向+顶级游资净买入 {main_force:.1f}%>15%")
    # 2. ≥1 家顶级一线游资 + 机构合力
    if cats_by_key.get("hot_tier1", {}).get("seat_count", 0) >= 1 \
            and cats_by_key.get("institution", {}).get("buy_wan", 0) > 0:
        positive.append("游资+机构合力 · 顶级游资搭台机构唱戏")
    # 3. 多家不同派系顶级游资均匀买入,买一占比<30%
    unique_aliases = _unique_hot_tier1_aliases(cats_by_key, "buy")
    if len(unique_aliases) >= SIGNAL_THRESHOLDS["multi_hot_buy"]:
        positive.append(f"多顶级共振 · {len(unique_aliases)} 家不同派系游资协同")
    if (cats_by_key.get("hot_tier1", {}).get("buy_pct") or 0) > 0 \
            and (cats_by_key.get("hot_tier1", {}).get("buy_pct") or 0) < 30 \
            and len(unique_aliases) >= 2:
        positive.append("买一不独食 · 顶级游资买盘均匀分布")
    # 4. 低位 + 北向持续加仓 + 机构大额 (近 N 日均净买入 > 0 简化判断)
    if (cats_by_key.get("northbound", {}).get("net_pct") or 0) > 0 \
            and (cats_by_key.get("institution", {}).get("buy_pct") or 0) > 0:
        positive.append("北向+机构双流入")
    # 5. 顶级游资分歧日承接 / 撬跌停 (判断: 顶级 tier1 买入 + 整体卖单较散)
    hot_tier1_buy_wan = cats_by_key.get("hot_tier1", {}).get("buy_wan", 0) or 0
    if hot_tier1_buy_wan > 0:
        positive.append("顶级游资承接 · 情绪拐点信号")
    # 6. 量化占比 <15% + 主流净流入
    quant_total_pct = cats_by_key.get("quant", {}).get("total_pct") or 0
    if quant_total_pct < 15 and main_force > 0:
        positive.append("量化可控 · 主流资金主导")
    # 7. 北向连续净买入 (本次数据里只展示当日,提示"今日净买入")
    nb_net = cats_by_key.get("northbound", {}).get("net_wan", 0) or 0
    if nb_net > 0:
        positive.append(f"北向今日净买入 {nb_net:.0f} 万")
    # 8. 机构 + 游资合力 (无拉萨)
    if (main_force > 0) and (lhasa_buy_pct < SIGNAL_THRESHOLDS["lhasa_high"]):
        positive.append("机构游资合力且散户未接盘")

    # ── 整合回 risks (旧字段保留兼容) ──
    # 警惕的标 ⚠️, 积极的标 ✅
    risks = []
    for w in warning:
        risks.append(f"⚠️ {w}")
    for p in positive:
        risks.append(f"✅ {p}")

    return {"risks": risks, "signals": {"positive": positive, "warning": warning}}


# ── 短线筛选标签 (用户字典 §六·2/3 简易版) ──
def screen_tags(categorized: dict) -> list[str]:
    """优质 / 规避标签,UI 显示用"""
    cats_by_key = {c["key"]: c for c in categorized.get("categories", [])}
    tags: list[str] = []
    main_force = _main_force_net_pct(cats_by_key)
    quant_total_pct = cats_by_key.get("quant", {}).get("total_pct") or 0
    lhasa_buy_pct = cats_by_key.get("retail_lhasa", {}).get("buy_pct") or 0
    hot_tier1_seats = cats_by_key.get("hot_tier1", {}).get("seat_count", 0) or 0
    inst_seats = cats_by_key.get("institution", {}).get("seat_count", 0) or 0

    # 优质
    if main_force > SIGNAL_THRESHOLDS["main_line_converge"] and quant_total_pct < 20:
        tags.append("✅ 优质 · 主线合力 + 量化可控")
    if hot_tier1_seats >= 1 and inst_seats >= 1 and main_force > 0:
        tags.append("✅ 优质 · 游资机构合力")
    # 规避
    if quant_total_pct >= SIGNAL_THRESHOLDS["quant_high"]:
        tags.append("❌ 规避 · 量化主导")
    if lhasa_buy_pct >= SIGNAL_THRESHOLDS["lhasa_high"] and main_force <= 0:
        tags.append("❌ 规避 · 拉萨扎堆无主流")
    if cats_by_key.get("hot_tier2", {}).get("seat_count", 0) >= 1 \
            and hot_tier1_seats == 0 \
            and cats_by_key.get("institution", {}).get("seat_count", 0) == 0:
        tags.append("❌ 规避 · 仅二线游资无主流")
    return tags


# ── 一站式: 拉龙虎榜 + 实时资金 → 输出全部结构 ──────
def build_breakdown(code: str) -> dict:
    """
    拉取:
    - seat_lookup.get_stock_seats(code, lookback_days=10)  → 近期席位
    - fund_flow.get_main_flow(code)                        → 今日实时主力
    - data_layer.fetch_daily(code, 1)                      → 当日总成交额
    输出 8 类汇总 + 实时主力散户占比 + 风险/积极信号 + 短线标签 + signals 拆分
    """
    from . import seat_lookup, fund_flow
    try:
        from .. import data_layer
    except Exception:
        data_layer = None

    seats_data = seat_lookup.get_stock_seats(code, lookback_days=10)
    rows = seats_data.get("rows", []) or []
    # 只取最近 1 天做"当日"分类 (避免 lookback 把历史席位都算进来)
    if rows:
        last_date = max((r.get("date") or "") for r in rows)
        rows_today = [r for r in rows if r.get("date") == last_date]
    else:
        rows_today = []

    # 当日总成交额
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
    risk_pack = detect_risks(categorized)
    tags = screen_tags(categorized)

    return {
        "code":             code,
        "rows":             rows_today,
        "all_rows_count":   len(rows),
        "last_date":        rows_today[0].get("date") if rows_today else None,
        "categories":       categorized["categories"],
        "total_amount_wan": categorized["total_amount_wan"],
        "intraday":         intraday,
        "risks":            risk_pack["risks"],
        "signals":          risk_pack["signals"],
        "tags":             tags,
        "fetch_ts":         rows_today[0].get("date") if rows_today else None,
    }