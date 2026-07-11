"""
股票板块分类 — 交易所市场板块 + 4 套权威行业分类
- 交易所板块：按代码前缀判定（无需外部依赖）
- 行业分类：4 套官方标准的"标准名集合" + 静态映射表 + akshare 兜底
- 缓存 → cache_store (Redis 主用 + SQLite fallback) TTL 24h
- 启动时若 Redis 没数据,从 data/sector_cache.json 灌入一次(老用户兼容)
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests as _requests

from .. import cache_store as _cs
from ..cache_store import get_store

log = logging.getLogger("tuixue_v3.web.sector")

# 兼容旧逻辑:启动时若 Redis 没数据,从老 sector_cache.json 灌入一次
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEGACY_CACHE_FILE = DATA_DIR / "sector_cache.json"
CACHE_TTL = 24 * 3600  # 行业映射 1 天刷新一次（变化极少）
_store = get_store()
_SECTOR_KEY = "sector:map"


def _legacy_bootstrap() -> dict:
    """从老 sector_cache.json 一次性灌入 Redis。"""
    if not _LEGACY_CACHE_FILE.exists():
        return {"_meta": {"built_at": 0, "source": ""}, "stocks": {}}
    try:
        return json.loads(_LEGACY_CACHE_FILE.read_text())
    except Exception as e:
        log.warning(f"老 sector_cache.json 读取失败: {e}")
        return {"_meta": {"built_at": 0, "source": ""}, "stocks": {}}


# ── 1) 交易所市场板块（4 个）───────────────────────────────────
# 沪60/沪688/深000/深300/北交所8
def detect_board(code: str) -> dict:
    """
    按代码前缀判定所属板块。
    返回 {board, board_name, prefix, mkt, pct_limit, capital_floor_wan}
    """
    c = (code or "").strip().zfill(6)
    if c.startswith(("600", "601", "603", "605")):
        return {"board": "sh_main",   "board_name": "沪市主板", "prefix": "沪",
                "mkt": "sh", "pct_limit": 10, "capital_floor_wan": 0}
    if c.startswith("688"):
        return {"board": "sh_star",   "board_name": "科创板",   "prefix": "沪",
                "mkt": "sh", "pct_limit": 20, "capital_floor_wan": 50}
    if c.startswith(("000", "001", "002", "003")):
        return {"board": "sz_main",   "board_name": "深市主板/中小板", "prefix": "深",
                "mkt": "sz", "pct_limit": 10, "capital_floor_wan": 0}
    if c.startswith("300"):
        return {"board": "sz_chinext","board_name": "创业板",   "prefix": "深",
                "mkt": "sz", "pct_limit": 20, "capital_floor_wan": 10}
    if c.startswith(("8", "9", "43", "83", "87")):
        return {"board": "bj",        "board_name": "北交所",   "prefix": "京",
                "mkt": "bj", "pct_limit": 30, "capital_floor_wan": 50}
    return {"board": "other", "board_name": "其他", "prefix": "?",
            "mkt": "?", "pct_limit": 10, "capital_floor_wan": 0}


# ── 2) 4 套权威行业分类的标准名集合 ────────────────────────────
CSRC_19 = [  # 证监会 19 一级行业 (JR/T 0020-2024)
    "农林牧渔", "采矿业", "制造业", "电力热力燃气", "建筑业", "批发零售",
    "交通运输", "住宿餐饮", "信息技术", "金融业", "房地产", "租赁商务",
    "科研技术", "水利环境", "居民服务", "教育", "卫生", "文体娱乐", "综合",
]

SW_31 = [  # 申万 31 一级行业 (2021 版)
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "家用电器",
    "食品饮料", "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输",
    "房地产", "商贸零售", "社会服务", "综合", "建筑材料", "建筑装饰",
    "电力设备", "国防军工", "计算机", "传媒", "通信", "银行",
    "非银金融", "汽车", "机械设备", "煤炭", "石油石化", "环保", "美容护理",
]

# 别名（旧版/东财/同花顺/手工常用名 → 申万 2021 标准名）
SW_ALIASES = {
    "电气设备": "电力设备",   # 东财沿用旧版申万叫法
    "新能源":   "电力设备",
    "电气机械": "电力设备",
    "电源设备": "电力设备",
    "储能":     "电力设备",
    "锂电池":   "电力设备",
    "光伏":     "电力设备",
    "半导体":   "电子",
    "芯片":     "电子",
    "集成电路": "电子",
    "元件":     "电子",
    "光学光电子": "电子",
    "消费电子": "电子",
    "证券":     "非银金融",
    "保险":     "非银金融",
    "多元金融": "非银金融",
}

CICS_11 = [  # 中证 CICS 11 (ETF/指数标准)
    "能源", "原材料", "工业", "可选消费", "主要消费",
    "医药卫生", "金融", "信息技术", "通信服务", "公用事业", "房地产",
]

GICS_11 = [  # GICS 全球行业 (外资/北向)
    "能源", "原材料", "工业", "非必需消费品", "必需消费品",
    "医疗保健", "金融", "信息技术", "通信服务", "公用事业", "房地产",
]


# 申万 → 其他 3 套的近似映射（手工维护，仅做粗对齐；AI 会基于此自动选）
SW_TO_OTHER = {
    "农林牧渔":     {"csrc": "农林牧渔",     "cics": "主要消费",   "gics": "必需消费品"},
    "基础化工":     {"csrc": "制造业",       "cics": "原材料",     "gics": "原材料"},
    "钢铁":         {"csrc": "制造业",       "cics": "原材料",     "gics": "原材料"},
    "有色金属":     {"csrc": "制造业",       "cics": "原材料",     "gics": "原材料"},
    "电子":         {"csrc": "制造业",       "cics": "信息技术",   "gics": "信息技术"},
    "家用电器":     {"csrc": "制造业",       "cics": "可选消费",   "gics": "非必需消费品"},
    "食品饮料":     {"csrc": "制造业",       "cics": "主要消费",   "gics": "必需消费品"},
    "纺织服饰":     {"csrc": "制造业",       "cics": "可选消费",   "gics": "非必需消费品"},
    "轻工制造":     {"csrc": "制造业",       "cics": "可选消费",   "gics": "非必需消费品"},
    "医药生物":     {"csrc": "制造业",       "cics": "医药卫生",   "gics": "医疗保健"},
    "公用事业":     {"csrc": "电力热力燃气", "cics": "公用事业",   "gics": "公用事业"},
    "交通运输":     {"csrc": "交通运输",     "cics": "工业",       "gics": "工业"},
    "房地产":       {"csrc": "房地产",       "cics": "房地产",     "gics": "房地产"},
    "商贸零售":     {"csrc": "批发零售",     "cics": "可选消费",   "gics": "非必需消费品"},
    "社会服务":     {"csrc": "住宿餐饮",     "cics": "可选消费",   "gics": "非必需消费品"},
    "综合":         {"csrc": "综合",         "cics": "工业",       "gics": "工业"},
    "建筑材料":     {"csrc": "制造业",       "cics": "原材料",     "gics": "原材料"},
    "建筑装饰":     {"csrc": "建筑业",       "cics": "工业",       "gics": "工业"},
    "电力设备":     {"csrc": "制造业",       "cics": "工业",       "gics": "工业"},
    "国防军工":     {"csrc": "制造业",       "cics": "工业",       "gics": "工业"},
    "计算机":       {"csrc": "信息技术",     "cics": "信息技术",   "gics": "信息技术"},
    "传媒":         {"csrc": "文体娱乐",     "cics": "通信服务",   "gics": "通信服务"},
    "通信":         {"csrc": "信息技术",     "cics": "通信服务",   "gics": "通信服务"},
    "银行":         {"csrc": "金融业",       "cics": "金融",       "gics": "金融"},
    "非银金融":     {"csrc": "金融业",       "cics": "金融",       "gics": "金融"},
    "汽车":         {"csrc": "制造业",       "cics": "可选消费",   "gics": "非必需消费品"},
    "机械设备":     {"csrc": "制造业",       "cics": "工业",       "gics": "工业"},
    "煤炭":         {"csrc": "采矿业",       "cics": "能源",       "gics": "能源"},
    "石油石化":     {"csrc": "采矿业",       "cics": "能源",       "gics": "能源"},
    "环保":         {"csrc": "水利环境",     "cics": "公用事业",   "gics": "公用事业"},
    "美容护理":     {"csrc": "制造业",       "cics": "主要消费",   "gics": "必需消费品"},
}


# ── 3) 行业映射缓存（股票 → 申万行业） ────────────────────────
_lock = threading.Lock()


def _load_cache() -> dict:
    """从 Redis 读;若空则从老 sector_cache.json 灌入一次。"""
    data = _store.get(_SECTOR_KEY)
    if data and isinstance(data, dict):
        return data
    legacy = _legacy_bootstrap()
    if legacy.get("stocks"):
        _store.set(_SECTOR_KEY, legacy, ttl=CACHE_TTL)
        log.info(f"已从老 sector_cache.json 灌入 {len(legacy['stocks'])} 只行业映射")
    return legacy or {"_meta": {"built_at": 0, "source": ""}, "stocks": {}}


def _save_cache(cache: dict) -> None:
    _store.set(_SECTOR_KEY, cache, ttl=CACHE_TTL)


def _fetch_industry_em(code: str) -> dict | None:
    """
    东财 f10 API 直接拉取（sandbox 网络下可用，akshare 被 DNS 劫持时这个能顶）：
    返回 {"sw_raw": "食品饮料-饮料-白酒", "csrc_raw": "制造业-...", "security_type": "..."}
    """
    board = detect_board(code)
    if board["mkt"] == "sh":
        em_code = f"SH{code}"
    elif board["mkt"] == "sz":
        em_code = f"SZ{code}"
    elif board["mkt"] == "bj":
        em_code = f"BJ{code}"
    else:
        em_code = f"SH{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_code}"
    try:
        r = _requests.get(url, timeout=6, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://emweb.securities.eastmoney.com/",
        })
        if r.status_code != 200:
            return None
        jb = (r.json().get("jbzl") or [{}])[0]
        return {
            "sw_raw":         (jb.get("EM2016") or "").strip(),         # 申万: "食品饮料-饮料-白酒"
            "csrc_raw":       (jb.get("INDUSTRYCSRC1") or "").strip(),   # 证监会: "制造业-酒、饮料..."
            "security_type":  (jb.get("SECURITY_TYPE") or "").strip(),   # 上交所主板A股 / 深交所创业板等
        }
    except Exception as e:
        log.info(f"eastmoney f10 {code} 失败: {e}")
        return None


def _fetch_industry_akshare(code: str) -> str | None:
    """akshare stock_individual_info_em — 返回 (行业) 字段"""
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        if df is None or df.empty:
            return None
        # DataFrame 形如 item/value
        if "item" in df.columns and "value" in df.columns:
            row = df[df["item"].astype(str).str.contains("行业", na=False)]
            if not row.empty:
                v = str(row.iloc[0]["value"]).strip()
                return v if v else None
        # 兜底：找含"行业"的任意位置
        for col in df.columns:
            for _, r in df.iterrows():
                val = str(r.get(col, ""))
                if any(k in val for k in ["行业", "industry"]):
                    other = [c for c in df.columns if c != col]
                    if other:
                        return str(r.get(other[0], "")).strip() or None
        return None
    except Exception as e:
        log.info(f"akshare 个股行业 {code} 失败: {e}")
        return None


# ── 行业名 → 申万 31 标准化 ────────────────────────────────────
def normalize_to_sw(raw: str) -> str | None:
    """
    把任意来源的行业名（akshare/同花顺/东财/手工）归一到申万 31 之一。
    规则：精确匹配 → 别名 → 子串包含 → 关键词命中。
    """
    if not raw:
        return None
    s = raw.strip()
    # 精确匹配
    if s in SW_31:
        return s
    # 别名匹配
    if s in SW_ALIASES:
        return SW_ALIASES[s]
    # 子串包含（先按更长关键词优先，避免"电气设备"被"设备"吃掉）
    for sw in sorted(SW_31, key=lambda x: -len(x)):
        if sw in s or s in sw:
            return sw
    # 别名子串
    for alias, sw in SW_ALIASES.items():
        if alias in s:
            return sw
    # 关键词（akshare 行业返回的常见名称）
    keyword_map = {
        "白酒":     "食品饮料",
        "啤酒":     "食品饮料",
        "乳品":     "食品饮料",
        "证券":     "非银金融",
        "保险":     "非银金融",
        "多元金融": "非银金融",
        "汽车整车": "汽车",
        "汽车零部件": "汽车",
        "新能源车": "汽车",
        "动力电池": "电力设备",
        "锂电池":   "电力设备",
        "光伏":     "电力设备",
        "风电":     "电力设备",
        "半导体":   "电子",
        "芯片":     "电子",
        "集成电路": "电子",
        "消费电子": "电子",
        "元件":     "电子",
        "光学光电子": "电子",
        "通信设备": "通信",
        "通信服务": "通信",
        "运营商":   "通信",
        "互联网":   "计算机",
        "软件开发": "计算机",
        "IT 服务":  "计算机",
        "计算机":   "计算机",
        "医疗器械": "医药生物",
        "化学制药": "医药生物",
        "中药":     "医药生物",
        "生物制品": "医药生物",
        "医药商业": "医药生物",
        "医疗服务": "医药生物",
        "创新药":   "医药生物",
        "银行":     "银行",
        "全国性银行": "银行",
        "区域性银行": "银行",
        "地产开发": "房地产",
        "物业管理": "房地产",
        "煤炭开采": "煤炭",
        "石油开采": "石油石化",
        "石化":     "石油石化",
        "化学纤维": "基础化工",
        "化学原料": "基础化工",
        "化学制品": "基础化工",
        "农化制品": "基础化工",
        "塑料":     "基础化工",
        "钢铁":     "钢铁",
        "普钢":     "钢铁",
        "特钢":     "钢铁",
        "黄金":     "有色金属",
        "工业金属": "有色金属",
        "稀有金属": "有色金属",
        "锂":       "有色金属",
        "钴":       "有色金属",
        "军工":     "国防军工",
        "航空装备": "国防军工",
        "航天装备": "国防军工",
        "地面兵装": "国防军工",
        "船舶制造": "国防军工",
        "环保设备": "环保",
        "环境治理": "环保",
        "家用电器": "家用电器",
        "白色家电": "家用电器",
        "黑色家电": "家用电器",
        "建材":     "建筑材料",
        "水泥":     "建筑材料",
        "玻璃":     "建筑材料",
        "装修":     "建筑装饰",
        "工程":     "建筑装饰",
        "纺织":     "纺织服饰",
        "服装":     "纺织服饰",
        "造纸":     "轻工制造",
        "包装":     "轻工制造",
        "家居":     "轻工制造",
        "文娱":     "传媒",
        "游戏":     "传媒",
        "影视":     "传媒",
        "出版":     "传媒",
        "广告":     "传媒",
        "零售":     "商贸零售",
        "贸易":     "商贸零售",
        "社服":     "社会服务",
        "旅游":     "社会服务",
        "酒店":     "社会服务",
        "餐饮":     "社会服务",
        "教育":     "社会服务",
        "美容":     "美容护理",
        "化妆品":   "美容护理",
        "机场":     "交通运输",
        "航空":     "交通运输",
        "港口":     "交通运输",
        "高速":     "交通运输",
        "铁路":     "交通运输",
        "物流":     "交通运输",
        "航运":     "交通运输",
        "电力":     "公用事业",
        "燃气":     "公用事业",
        "水务":     "公用事业",
        "电网":     "公用事业",
        "综合":     "综合",
        "农业":     "农林牧渔",
        "林业":     "农林牧渔",
        "牧业":     "农林牧渔",
        "渔业":     "农林牧渔",
        "饲料":     "农林牧渔",
        "种植":     "农林牧渔",
        "工程机械": "机械设备",
        "专用设备": "机械设备",
        "通用设备": "机械设备",
        "自动化":   "机械设备",
        "仪器仪表": "机械设备",
    }
    for kw, sw in keyword_map.items():
        if kw in s:
            return sw
    return None


def get_sector(code: str, force_refresh: bool = False) -> dict:
    """
    返回某只股票的完整板块分类(2026-07-11 起含 4 层 taxonomy)。
    {
      code, board: {...},
      sw, sw_raw, csrc, cics, gics: 旧字段 (保留兼容)
      ai_tags: 旧 AI 概念标
      taxonomy: {level1_cluster, level2_sw, level3_chain, level4_subconcept, role, source, ...}  ← 新
      source, fresh
    }
    """
    code = code.strip().zfill(6)
    board = detect_board(code)

    with _lock:
        cache = _load_cache()
        stocks = cache.setdefault("stocks", {})
        hit = stocks.get(code)

        if not force_refresh and hit and (time.time() - (cache.get("_meta", {}).get("built_at") or 0) < CACHE_TTL):
            sw = hit.get("sw")
            sw_raw = hit.get("sw_raw") or ""
            csrc_raw = hit.get("csrc_raw") or ""
            return _format_sector(code, board, sw, hit.get("source") or "cache",
                                 fresh=False, sw_raw=sw_raw, csrc_raw=csrc_raw)

        # 主：eastmoney f10（沙箱可达）；兜底 akshare
        sw = None
        source = "unknown"
        raw_sw = ""
        raw_csrc = ""
        em = _fetch_industry_em(code)
        if em:
            raw_sw = em.get("sw_raw", "")
            raw_csrc = em.get("csrc_raw", "")
            sw = normalize_to_sw(raw_sw)
            source = "eastmoney"
        if not sw:
            ak_raw = _fetch_industry_akshare(code)
            if ak_raw:
                sw = normalize_to_sw(ak_raw)
                if sw:
                    raw_sw = ak_raw
                    source = "akshare"

        stocks[code] = {
            "sw_raw":   raw_sw,
            "csrc_raw": raw_csrc,
            "sw":       sw or "",
            "source":   source,
            "updated_at": int(time.time()),
        }
        cache["_meta"] = {"built_at": int(time.time()), "source": source}
        _save_cache(cache)
        return _format_sector(code, board, sw, source, fresh=True, sw_raw=raw_sw, csrc_raw=raw_csrc)


def _format_sector(code: str, board: dict, sw: str | None, source: str, fresh: bool,
                   sw_raw: str = "", csrc_raw: str = "") -> dict:
    others = SW_TO_OTHER.get(sw, {}) if sw else {}
    # 4 层板块分类 (2026-07-11) — Level1 集群 / Level2 申万 / Level3 产业链 / Level4 细分
    # 失败/缺失时所有 taxonomy 字段也照样返回,前端按 source=default 判断弱化样式
    try:
        from .sector_taxonomy import classify_taxonomy
        taxonomy = classify_taxonomy(code, sw, sw_raw=sw_raw, csrc_raw=csrc_raw)
    except Exception as e:
        log.debug(f"taxonomy classify 失败 {code}: {e}")
        taxonomy = {
            "level1_cluster": "", "level2_sw": "", "level3_chain": "",
            "level4_subconcept": [], "role": "", "source": "default",
            "noise_reason": "", "cluster_color": "#888",
        }
    return {
        "code":   code,
        "board":  board,
        "sw":     sw or None,
        "sw_raw": sw_raw or None,       # 2026-07-11 — 上游取具体 sw 二级 / 三级 (如"半导体-集成电路")
        "csrc":   others.get("csrc"),
        "cics":   others.get("cics"),
        "gics":   others.get("gics"),
        "ai_tags": classify_ai_tag(code, sw, others.get("csrc")),  # 旧 AI 概念标
        "taxonomy": taxonomy,           # 4 层分类 (新增)
        "source": source,
        "fresh":  fresh,
    }


# ────────────────────────────────────────────────────────────
# AI 概念标 — 退学战场 (2026-07 主战场: 机器人/AI)
# 思路:用 申万行业 + 证监会行业 推断该股属于 AI 哪个子方向
# 维护成本低:行业分类标准稳定,这里只需要维护一张表
# ────────────────────────────────────────────────────────────

# 主战场标签(显示用:🏷️ + 中文)
AI_TAG_DEFS = {
    "robot_main":     "机器人本体",        # 真龙头
    "robot_part":     "机器人零部件",      # 减速器/伺服/控制器
    "robot_vision":   "机器视觉",          # 视觉/3D/激光雷达
    "ai_compute":     "AI 算力",           # 服务器/HPC/推理芯片
    "ai_chip":        "AI 芯片",           # GPU/CPU/SoC
    "ai_software":    "AI 软件",           # 大模型应用/算法
    "auto_intelligent": "智能驾驶",        # 智驾/智驾舱/雷达
    "semi":           "半导体",             # 设备/材料/IP
    "ev":             "新能源车",           # 锂电/整车(纯汽车,不属机器人)
    "tradition":      "传统行业",          # 不沾 AI 概念
    "unknown":        "未分类",
}

# 申万行业 / CSRC → 标签映射
SW_TO_AI_TAGS = {
    # 机器人本体
    "机械设备":   ["robot_main", "robot_part"],   # 埃斯顿/双环传动在这里
    "汽车":      ["auto_intelligent", "ev"],      # 汽车板块里可能有智驾
    "电子":      ["ai_chip", "semi"],             # 半导体/AI 芯片
    "计算机":    ["ai_compute", "ai_software"],   # 浪潮信息/用友
    "通信":      ["ai_compute"],                  # 光模块/算力网络
    "国防军工":  ["robot_main"],                   # 军用机器人
    "电力设备":  ["robot_part", "ev"],             # 锂电/光伏/储能(都跟机器人外溢沾边)
    "家用电器":  ["robot_part"],                   # 部分家电有机器人外延
    "传媒":      ["ai_software"],                  # AI 内容生成
    "医药生物":  ["ai_software"],                  # AI 制药
    "建筑材料":  ["tradition"],
    "房地产":    ["tradition"],
    "银行":      ["tradition"],
    "非银金融":  ["tradition"],
    "商贸零售":  ["tradition"],
    "纺织服饰":  ["tradition"],
    "轻工制造":  ["tradition"],
    "公用事业":  ["tradition"],
    "交通运输":  ["tradition"],
    "煤炭":      ["tradition"],
    "石油石化":  ["tradition"],
    "环保":      ["tradition"],
    "美容护理":  ["tradition"],
    "食品饮料":  ["tradition"],
    "农林牧渔":  ["tradition"],
    "基础化工":  ["tradition"],
    "钢铁":      ["tradition"],
    "有色金属":  ["tradition"],
    "建筑装饰":  ["tradition"],
    "社会服务":  ["tradition"],
    "综合":      ["tradition"],
}


def classify_ai_tag(code: str, sw: str | None, csrc: str | None) -> dict:
    """根据申万/CSRC 行业推断 AI 概念标

    Returns:
        {
          "tags": ["robot_main", "robot_part"],   # 内部 ID
          "labels": ["机器人本体", "机器人零部件"],  # 显示名
          "is_main_field": true|false,             # 是否属于当前主战场
        }
    """
    code = str(code).strip().zfill(6)
    tags = list(SW_TO_AI_TAGS.get(sw, ["unknown"]))

    # 主板(非科创/创业)/价格低/小盘股不是主战场
    # 简化:看 sw 一级(如果 sw 是 unknown/unknown 之上是 tradition/auto 等)
    is_main_field = bool(set(tags) & {
        "robot_main", "robot_part", "robot_vision",
        "ai_compute", "ai_chip", "ai_software",
        "auto_intelligent", "semi",
    })

    # 个股特例:已知龙头股(基于 MEMORY + 既有认知)
    KNOWN_DRAGONS = {
        # 机器人本体
        "002747": ["robot_main"],                # 埃斯顿(机器人本体龙头)
        "300024": ["robot_main"],                # 机器人(SIASUN)
        # 机器人零部件
        "002472": ["robot_part"],                # 双环传动(减速器)
        "002979": ["robot_part"],                # 雷赛智能(伺服)
        "300124": ["robot_part"],                # 汇川技术(伺服/工控)
        # 机器视觉
        "002415": ["robot_vision"],              # 海康威视
        "688686": ["ai_chip"],                   # 兆易创新(AI 芯片)
        # AI 算力
        "000977": ["ai_compute"],                # 浪潮信息
        "000063": ["ai_compute"],                # 中兴通讯
        "300308": ["ai_compute"],                # 中际旭创
        # 智能驾驶
        "002920": ["auto_intelligent"],          # 德赛西威
        "603290": ["auto_intelligent"],          # 斯达半导
        # ⚠️ 非机器人主线踩坑股 — Arthur 2026-07-06 确认
        # 沪市主板/汽车电子/汽车零部件，跟机器人本体不沾边
        "603286": ["auto_intelligent"],          # 日盈电子(汽车电子,非机器人本体)
        "603338": ["auto_intelligent"],          # 浙江鼎力(工程机械,外溢)
    }
    if code in KNOWN_DRAGONS:
        tags = KNOWN_DRAGONS[code]
        is_main_field = True

    # 转 label
    labels = [AI_TAG_DEFS.get(t, t) for t in tags]

    return {
        "tags": tags,
        "labels": labels,
        "is_main_field": is_main_field,
        "sw_used": sw,
    }


def bulk_get_sector(codes: list[str]) -> dict[str, dict]:
    """批量取（命中缓存即返回，不触发抓取）"""
    out: dict[str, dict] = {}
    for c in codes:
        out[c] = get_sector(c, force_refresh=False)
    return out


# ── 给 AI 用的标准名清单（用于 news AI prompt） ────────────────
def sw_industry_choices_text() -> str:
    """拼成可在 prompt 中粘贴的纯文本清单"""
    lines = []
    for i, sw in enumerate(SW_31, 1):
        lines.append(f"  {i:>2}. {sw}")
    return "\n".join(lines)