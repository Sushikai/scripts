"""
4 层板块分类标准 (2026-07-11)
─────────────────────────────────────────────────────────────────────
设计:
  Level1 集群 (Cluster)        — 6 大类,固定 (大科技/高端制造/消费/医药/金融/周期资源)
  Level2 申万 (Shenwan)        — 31 一级,交易/研报通用基准
  Level3 产业链 (Chain)        — 31 → 50+ 三级产业链 (主线识别最小单位)
  Level4 细分 (Sub-Concept)    — 四级技术标签 (HBM/CPO/谐波减速器...)

目的:
  - 顶部筛选 / 主线判定 / 杂毛过滤 全部走这 4 层
  - 单只股票可同时挂多个 L4 标签 (一个公司多个产品线)
  - 「主线判定」:同一 Level3 涨停 ≥ MAINLINE_ZT_THRESHOLD 家 → 当日主线
  - 「杂毛」:仅沾 L4 概念,无 L3 核心营收 → role=noise,选股降级

使用:
  from .sector_taxonomy import classify_taxonomy, detect_mainline, count_zt_by_chain

数据来源优先级:
  1) KNOWN_L4[code]      — 手工维护的核心标的(龙头/陷阱股)
  2) HEURISTIC_KEYWORDS  — 从 sw_raw / 主营业务关键词推断 L3/L4
  3) fallback            — 只填 L1+L2,L3/L4 留空
"""
from __future__ import annotations

from typing import Iterable


# ═════════════════════════════════════════════════════════════════
# Level1 — 6 大固定集群 (前端顶部筛选栏)
# ═════════════════════════════════════════════════════════════════
CLUSTERS: dict[str, dict] = {
    "大科技": {
        "color":     "#5b8def",
        "icon":      "🧠",
        "sw_set":    {"电子", "计算机", "通信", "机械设备", "国防军工", "传媒", "电力设备"},
        "desc":      "半导体 / 算力 / AI / 机器人 / 军工 / 传媒 / 电新",
    },
    "高端制造": {
        "color":     "#f59e0b",
        "icon":      "🏗️",
        "sw_set":    {"汽车", "轻工制造", "建筑材料", "建筑装饰"},
        "desc":      "整车 / 汽零 / 家居 / 建材",
    },
    "消费": {
        "color":     "#10b981",
        "icon":      "🛍️",
        "sw_set":    {"食品饮料", "家用电器", "纺织服饰", "商贸零售", "社会服务", "美容护理"},
        "desc":      "食饮 / 家电 / 纺服 / 零售 / 社服 / 美容",
    },
    "医药生物": {
        "color":     "#ec4899",
        "icon":      "💊",
        "sw_set":    {"医药生物"},
        "desc":      "创新药 / 中药 / 器械 / CXO / 医美 / IVD",
    },
    "金融": {
        "color":     "#f43f5e",
        "icon":      "🏦",
        "sw_set":    {"银行", "非银金融", "房地产"},
        "desc":      "银行 / 证券 / 保险 / 地产",
    },
    "周期资源": {
        "color":     "#a16207",
        "icon":      "⛏️",
        "sw_set":    {"有色金属", "煤炭", "钢铁", "基础化工", "石油石化", "环保", "公用事业", "农林牧渔", "交通运输", "综合"},
        "desc":      "有色 / 煤炭 / 钢铁 / 化工 / 石化 / 公用 / 农业",
    },
}

CLUSTER_ORDER = ["大科技", "高端制造", "消费", "医药生物", "金融", "周期资源", "其他"]


def sw_to_cluster(sw: str | None) -> str:
    sw = (sw or "").strip()
    if not sw:
        return "其他"
    for c, info in CLUSTERS.items():
        if sw in info["sw_set"]:
            return c
    return "其他"


# ═════════════════════════════════════════════════════════════════
# Level3 — 产业链赛道 (主线识别最小单位)
# 与 L2 (申万) 是多对一关系: 一个 sw 可以挂多条 L3
# ═════════════════════════════════════════════════════════════════
TECH_CHAINS: dict[str, dict] = {
    # —— 电子 (L2=电子) ——
    "半导体芯片":   {
        "sw": "电子",
        "desc": "半导体全产业链 (设计/制造/封测/设备/材料)",
        "l4": ["前道设备", "刻蚀机", "薄膜沉积", "光刻机", "检测设备",
               "光刻胶", "靶材", "电子特气", "抛光液",
               "算力GPU", "CPU", "MCU", "存储主控", "射频芯片", "车规芯片", "EDA"],
    },
    "存储":         {
        "sw": "电子",
        "desc": "DRAM / NAND / HBM / 模组",
        "l4": ["DRAM", "NAND", "HBM", "存储模组", "存储配套材料"],
    },
    "PCB":          {
        "sw": "电子",
        "desc": "高速 PCB / 载板 / 柔性板 / 铜箔",
        "l4": ["高速AI服务器PCB", "IC载板", "柔性PCB", "铜箔"],
    },
    "被动元件":     {
        "sw": "电子",
        "desc": "电容/电阻/显示/光学",
        "l4": ["电容电阻", "MiniLED", "OLED", "面板", "光学元件"],
    },
    # —— 计算机 (L2=计算机) ——
    "AI算力基建":   {
        "sw": "计算机",
        "desc": "服务器 / 液冷 / IDC / 算力租赁",
        "l4": ["智算服务器", "液冷", "IDC机房", "光存储", "算力租赁"],
    },
    "大模型软件":   {
        "sw": "计算机",
        "desc": "操作系统 / 数据库 / 大模型 / Agent",
        "l4": ["操作系统", "数据库", "通用大模型", "行业垂类模型", "AI Agent工具"],
    },
    "行业数字化":   {
        "sw": "计算机",
        "desc": "工业软件 / 金融 IT / 信安",
        "l4": ["工业软件", "金融IT", "政务数字化", "信息安全"],
    },
    "消费终端":     {
        "sw": "计算机",
        "desc": "PC / 智能终端 / 可穿戴",
        "l4": ["PC", "智能终端", "可穿戴"],
    },
    # —— 通信 (L2=通信) ——
    "高速光互联":   {
        "sw": "通信",
        "desc": "800G/1.6T/CPO/硅光/相干",
        "l4": ["800G光模块", "1.6T光模块", "CPO共封装", "硅光", "EML光源", "相干光", "高速光纤"],
    },
    "通信设备":     {
        "sw": "通信",
        "desc": "交换 / 路由 / 基站 / 卫星",
        "l4": ["交换机", "路由器", "基站", "卫星通信", "6G"],
    },
    # —— 机械设备 (L2=机械设备) ——
    "人形机器人":   {
        "sw": "机械设备",
        "desc": "RV/谐波/伺服/控制器/视觉",
        "l4": ["RV减速器", "谐波减速器", "伺服电机", "控制器", "丝杠", "六维传感器",
               "机器视觉", "整机本体", "机器人集成"],
    },
    "通用自动化":   {
        "sw": "机械设备",
        "desc": "机床 / 锂电 / 光伏设备",
        "l4": ["数控机床", "锂电设备", "光伏设备"],
    },
    # —— 国防军工 (L2=国防军工) ——
    "军工电子":     {
        "sw": "国防军工",
        "desc": "军工半导体 / 卫星导航 / 军用算力",
        "l4": ["军工半导体", "射频芯片", "卫星导航", "军用算力"],
    },
    # —— 传媒 (L2=传媒) ——
    "AI传媒":       {
        "sw": "传媒",
        "desc": "AIGC / 数字人 / 游戏 AI",
        "l4": ["AIGC图文", "AIGC视频", "数字人", "游戏AI"],
    },
    # —— 电力设备 (L2=电力设备) ——
    "AI电源储能":   {
        "sw": "电力设备",
        "desc": "AI 电源 / 储能变流器 / 高压快充",
        "l4": ["AI电源", "储能变流器", "高压快充", "算力供电模组"],
    },
}

# 非科技 L3 一并定义,用于完整覆盖
OTHER_CHAINS: dict[str, dict] = {
    "新能源车":     {"sw": "汽车",         "desc": "整车 / 锂电",        "l4": ["整车", "锂电池", "电控"]},
    "汽车零部件":   {"sw": "汽车",         "desc": "汽零",              "l4": ["传统汽零", "智能驾驶", "汽车电子"]},
    "创新药":       {"sw": "医药生物",     "desc": "创新药产业链",       "l4": ["靶点发现", "ADC", "GLP-1", "CGT"]},
    "医疗器械":     {"sw": "医药生物",     "desc": "IVD / 高值耗材",     "l4": ["IVD", "高值耗材", "医疗设备"]},
    "CXO":          {"sw": "医药生物",     "desc": "CRO/CDMO/CMO",       "l4": ["CRO", "CDMO", "CMO"]},
    "中药":         {"sw": "医药生物",     "desc": "中药",               "l4": ["品牌中药", "中药OTC", "中药材"]},
    "医美":         {"sw": "医药生物",     "desc": "医美",               "l4": ["玻尿酸", "胶原蛋白", "能量源器械"]},
    "白酒":         {"sw": "食品饮料",     "desc": "白酒/啤酒",          "l4": ["高端白酒", "次高端白酒", "啤酒"]},
    "家电":         {"sw": "家用电器",     "desc": "白电/黑电/小家电",   "l4": ["白电", "黑电", "厨房小家电"]},
    "煤炭":         {"sw": "煤炭",         "desc": "动力煤/焦煤",        "l4": ["动力煤", "焦煤"]},
    "锂电材料":     {"sw": "有色金属",     "desc": "锂/钴/镍",           "l4": ["锂矿", "钴", "镍", "电池铝箔"]},
    "工业金属":     {"sw": "有色金属",     "desc": "铜/铝/铅/锌",        "l4": ["铜", "铝", "铅锌"]},
    "小金属":       {"sw": "有色金属",     "desc": "稀土/钨/锗/铟",      "l4": ["稀土", "钨", "锗", "铟"]},
    "化工新材料":   {"sw": "基础化工",     "desc": "新材料",             "l4": ["半导体材料", "新能源材料", "碳纤维"]},
    "银行":         {"sw": "银行",         "desc": "国有/股份/城商",    "l4": ["国有大行", "股份行", "城商行"]},
    "证券保险":     {"sw": "非银金融",     "desc": "券商/保险",          "l4": ["头部券商", "中小券商", "人寿", "财险"]},
}

ALL_CHAINS: dict[str, dict] = {**TECH_CHAINS, **OTHER_CHAINS}


def l3_to_sw(l3: str) -> str | None:
    """L3 chain → 对应的 L2 申万。"""
    info = ALL_CHAINS.get(l3) or TECH_CHAINS.get(l3) or OTHER_CHAINS.get(l3)
    return info.get("sw") if info else None


def l3_for_sw(sw: str | None) -> list[str]:
    """给定 sw,返回所有可挂的 L3 名(空集合)。"""
    if not sw:
        return []
    return [k for k, v in ALL_CHAINS.items() if v.get("sw") == sw]


# ═════════════════════════════════════════════════════════════════
# Level4 — 手工已知的个股 4 层定位 (主战场核心股 + 杂毛陷阱股)
# role: "main" 主线龙头 | "second" 二线弹性 | "noise" 杂毛跟风
# source: "known"  手工
# ═════════════════════════════════════════════════════════════════
KNOWN_L4: dict[str, dict] = {
    # ── 机器人 (L2=机械设备, L3=人形机器人)
    "002747": {"l3": "人形机器人",   "l4": ["整机本体"],       "role": "main",  "sw": "机械设备"},
    "300024": {"l3": "人形机器人",   "l4": ["整机本体"],       "role": "main",  "sw": "机械设备"},
    "002472": {"l3": "人形机器人",   "l4": ["RV减速器"],       "role": "main",  "sw": "机械设备"},
    "002979": {"l3": "人形机器人",   "l4": ["伺服电机"],       "role": "main",  "sw": "机械设备"},
    "300124": {"l3": "人形机器人",   "l4": ["伺服电机"],       "role": "main",  "sw": "机械设备"},
    "002415": {"l3": "人形机器人",   "l4": ["机器视觉"],       "role": "main",  "sw": "机械设备"},
    # ── 算力 / 服务器 / 通信
    "688686": {"l3": "半导体芯片",   "l4": ["存储主控"],       "role": "main",  "sw": "电子"},
    "000977": {"l3": "AI算力基建",   "l4": ["智算服务器"],     "role": "main",  "sw": "计算机"},
    "000063": {"l3": "通信设备",     "l4": ["基站"],           "role": "main",  "sw": "通信"},
    "300308": {"l3": "高速光互联",   "l4": ["800G光模块"],    "role": "main",  "sw": "通信"},
    # ── 智驾
    "002920": {"l3": "汽车零部件",   "l4": ["智能驾驶"],       "role": "main",  "sw": "汽车"},
    "603290": {"l3": "半导体芯片",   "l4": ["车规芯片"],       "role": "second", "sw": "电子"},
    # ── 杂毛 (用户 2026-07-06 标注)
    "603286": {"l3": "汽车零部件",   "l4": ["汽车电子"],       "role": "noise", "sw": "汽车",
               "noise_reason": "汽车电子,非机器人本体"},
    "603338": {"l3": "通用自动化",   "l4": ["高空作业平台"],   "role": "noise", "sw": "机械设备",
               "noise_reason": "工程机械外溢,与机器人沾边但非主业"},
}


# ═════════════════════════════════════════════════════════════════
# Level4 启发式推断 (sw_raw 关键词 → L3+L4)
# 命中率有限,UNKNOWN 状态 fallback 到「仅 L1+L2」
# ═════════════════════════════════════════════════════════════════
_HEUR_L3: list[tuple[str, str]] = [
    # (keyword, l3 chain)
    ("光刻胶",        "半导体芯片"),
    ("半导体",        "半导体芯片"),
    ("集成电路",      "半导体芯片"),
    ("芯片",          "半导体芯片"),
    ("GPU",           "半导体芯片"),
    ("HBM",           "存储"),
    ("存储",          "存储"),
    ("DRAM",          "存储"),
    ("PCB",           "PCB"),
    ("印制电路",      "PCB"),
    ("服务器",        "AI算力基建"),
    ("IDC",           "AI算力基建"),
    ("机柜",          "AI算力基建"),
    ("光模块",        "高速光互联"),
    ("CPO",           "高速光互联"),
    ("硅光",          "高速光互联"),
    ("光通信",        "高速光互联"),
    ("800G",          "高速光互联"),
    ("1.6T",          "高速光互联"),
    ("减速器",        "人形机器人"),
    ("谐波",          "人形机器人"),
    ("伺服",          "人形机器人"),
    ("机器人",        "人形机器人"),
    ("机器视觉",      "人形机器人"),
    ("锂电池",        "新能源车"),
    ("动力电池",      "新能源车"),
    ("整车",          "新能源车"),
    ("新能源车",      "新能源车"),
    ("储能",          "AI电源储能"),
    ("充电桩",        "AI电源储能"),
    ("创新药",        "创新药"),
    ("CRO",           "CXO"),
    ("CDMO",          "CXO"),
    ("医疗器械",      "医疗器械"),
    ("IVD",           "医疗器械"),
    ("券商",          "证券保险"),    # 必须排在「银行」前 — sw_raw "非银行金融" 才不会误命中银行
    ("证券",          "证券保险"),
    ("保险",          "证券保险"),
    ("银行",          "银行"),
    ("白酒",          "白酒"),
    ("啤酒",          "白酒"),
    ("稀土",          "小金属"),
    ("锂矿",          "锂电材料"),
    ("钴",            "锂电材料"),
    ("工业软件",      "行业数字化"),
    ("信息安全",      "行业数字化"),
    ("大模型",        "大模型软件"),
    ("AI Agent",      "大模型软件"),
    ("数字人",        "AI传媒"),
    ("游戏",          "AI传媒"),
    ("AIGC",          "AI传媒"),
    ("卫星",          "军工电子"),
    ("导航",          "军工电子"),
    ("军工",          "军工电子"),
]


def _heuristic_from_text(txt: str | None) -> tuple[str | None, list[str]]:
    """从 sw_raw / csrc_raw / 名称里命中启发式关键词。
    返回 (l3_chain, [l4_sub])。都没命中 → (None, [])。
    """
    if not txt:
        return None, []
    s = str(txt)
    for kw, l3 in _HEUR_L3:
        if kw in s:
            l4: list[str] = []
            info = ALL_CHAINS.get(l3)
            if info:
                # 只保留 sw_raw 里出现的 L4;不靠 kw 反推 (避免误填整条链)
                for sub in info.get("l4", []):
                    if sub and sub in s:
                        l4.append(sub)
            if not l4:
                l4 = [kw]
            return l3, l4[:4]  # 最多 4 个
    return None, []


# ═════════════════════════════════════════════════════════════════
# 主线识别 — 同 L3 涨停 ≥ N 家 → 主线
# ═════════════════════════════════════════════════════════════════
MAINLINE_ZT_THRESHOLD = 15  # 用户标准:同一产业链当日涨停 ≥15 家 → 主线


def count_zt_by_chain(zt_codes: Iterable[str],
                      sector_lookup=None) -> dict[str, int]:
    """统计当日涨停池中,每个 L3 chain 出现几次。

    参数:
      zt_codes: 当日涨停代码列表
      sector_lookup: callable(code) -> dict 或 sector dict;缺省用本地导入
    返回: {l3_chain: zt_count}
    """
    counts: dict[str, int] = {}
    if sector_lookup is None:
        try:
            from .sector_classify import get_sector
            sector_lookup = get_sector
        except Exception:
            return counts

    for code in zt_codes:
        try:
            sec = sector_lookup(code)
            tax = (sec or {}).get("taxonomy") or {}
            l3 = tax.get("level3_chain") or ""
            if not l3:
                continue
            counts[l3] = counts.get(l3, 0) + 1
        except Exception:
            continue
    return counts


def detect_mainline(zt_codes: Iterable[str] | None = None,
                    chain_counts: dict[str, int] | None = None,
                    sector_lookup=None,
                    threshold: int = MAINLINE_ZT_THRESHOLD) -> list[dict]:
    """
    Returns:
      [
        {"chain": "人形机器人", "zt_count": 22, "is_mainline": True, "rank": 1},
        ...
      ]
    按 zt_count 倒序,只保留 ≥ threshold 的主线。
    """
    counts = chain_counts or count_zt_by_chain(zt_codes or [], sector_lookup)
    items = sorted(counts.items(), key=lambda x: -x[1])
    out = []
    rank = 0
    for chain, n in items:
        if n < threshold:
            break
        rank += 1
        info = ALL_CHAINS.get(chain) or {}
        cluster = sw_to_cluster(info.get("sw"))
        out.append({
            "chain":       chain,
            "zt_count":    n,
            "is_mainline": True,
            "rank":        rank,
            "sw":          info.get("sw", ""),
            "cluster":     cluster,
            "desc":        info.get("desc", ""),
        })
    return out


# ═════════════════════════════════════════════════════════════════
# 主调用入口 — 给单只股票算 4 层标签
# ═════════════════════════════════════════════════════════════════
def classify_taxonomy(code: str, sw: str | None,
                      sw_raw: str = "",
                      csrc_raw: str = "") -> dict:
    """返回 4 层 + role 信息(供 sector_classify._format_sector 嵌进 ai_tags 之外的 taxonomy 字段)。

    字段:
      level1_cluster       str                  6 大集群之一 / "其他"
      level2_sw            str                  申万一级 / ""
      level3_chain         str                  产业链 / ""
      level4_subconcept    list[str]            细分(允许多)/ []
      role                 str                  "main" | "second" | "noise" | ""
      source               str                  "known" | "heur" | "default"
      noise_reason         str (optional)       杂毛原因
      cluster_color        str                  集群对应颜色 (#hex),前端 chip 用
    """
    code = str(code or "").strip().zfill(6)
    sw = (sw or "").strip() or None
    cluster = sw_to_cluster(sw)

    # 1) 已知标的优先
    if code in KNOWN_L4:
        k = KNOWN_L4[code]
        return {
            "level1_cluster":    cluster,
            "level2_sw":         sw or "",
            "level3_chain":      k["l3"],
            "level4_subconcept": list(k.get("l4") or []),
            "role":              k.get("role", ""),
            "source":            "known",
            "noise_reason":      k.get("noise_reason", ""),
            "cluster_color":     CLUSTERS.get(cluster, {}).get("color", "#888"),
        }

    # 2) 启发式
    heur_text = " ".join([sw_raw or "", csrc_raw or ""])
    l3, l4_list = _heuristic_from_text(heur_text)
    if l3:
        sw_effective = sw or l3_to_sw(l3) or ""
        cluster2 = sw_to_cluster(sw_effective) if sw_effective else cluster
        return {
            "level1_cluster":    cluster2,
            "level2_sw":         sw_effective,
            "level3_chain":      l3,
            "level4_subconcept": l4_list,
            "role":              "",
            "source":            "heur",
            "noise_reason":      "",
            "cluster_color":     CLUSTERS.get(cluster2, {}).get("color", "#888"),
        }

    # 3) 兜底 — 只填 L1+L2
    return {
        "level1_cluster":    cluster,
        "level2_sw":         sw or "",
        "level3_chain":      "",
        "level4_subconcept": [],
        "role":              "",
        "source":            "default",
        "noise_reason":      "",
        "cluster_color":     CLUSTERS.get(cluster, {}).get("color", "#888"),
    }


# ═════════════════════════════════════════════════════════════════
# 给前端 / AI 用的格式化工具
# ═════════════════════════════════════════════════════════════════
def taxonomy_tree() -> dict:
    """返回前端可消费的完整 6 集群 → SW → L3 → L4 嵌套树。
    静态数据,无外部依赖。
    """
    by_cluster: dict[str, dict] = {}
    for cname in CLUSTER_ORDER:
        if cname == "其他":
            continue
        cinfo = CLUSTERS.get(cname, {})
        tree_sw: dict[str, dict] = {}
        for sw in sorted(cinfo.get("sw_set", set())):
            chains_for_sw = [k for k, v in ALL_CHAINS.items() if v.get("sw") == sw]
            tree_sw[sw] = {
                "chains": chains_for_sw,
                "by_chain": {k: ALL_CHAINS[k] for k in chains_for_sw},
            }
        by_cluster[cname] = {
            "color": cinfo.get("color", "#888"),
            "icon":  cinfo.get("icon", ""),
            "desc":  cinfo.get("desc", ""),
            "sw_set": sorted(cinfo.get("sw_set", set())),
            "industries": tree_sw,
        }
    return by_cluster


def fmt_taxonomy_short(tax: dict) -> str:
    """给 AI / 前端紧凑文本: '科技·电子·半导体芯片·算力GPU'"""
    parts = [
        tax.get("level1_cluster") or "其他",
        tax.get("level2_sw") or "",
        tax.get("level3_chain") or "",
    ]
    sub = tax.get("level4_subconcept") or []
    if sub:
        parts.append("/".join(sub[:3]))
    return "·".join(p for p in parts if p)


def fmt_taxonomy_full(tax: dict) -> str:
    """给 AI 长文本 4 层 + role。"""
    if not tax:
        return "(板块未分类)"
    lines = [
        f"  Level1 集群: {tax.get('level1_cluster') or '其他'}",
        f"  Level2 申万: {tax.get('level2_sw') or '(空)'}",
        f"  Level3 产业链: {tax.get('level3_chain') or '(空)'}",
        f"  Level4 细分: {tax.get('level4_subconcept') or '(空)'}",
        f"  角色(role): {tax.get('role') or '—'}",
    ]
    noise = tax.get("noise_reason")
    if noise:
        lines.append(f"  ⚠ 杂毛原因: {noise}")
    src = tax.get("source")
    if src:
        lines.append(f"  分类来源: {src}")
    return "\n".join(lines)
