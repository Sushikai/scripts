#!/usr/bin/env python3
"""
tuixue_v3/news_sentiment.py
Ship 9/100 — 新闻情绪打分 (5 维)

5 维输出:
- sentiment      : -1 ~ +1  情绪极性
- confidence     :  0 ~ 1   置信度 (证据强度, 不是模型自信)
- event_type     : 枚举 12 类 (业绩/重组/监管/订单/减持/...)
- sector_impact  : -1 ~ +1  对所属板块的外溢影响
- summary        : ≤ 60 字中文摘要

两条链路:
1) LLM 链路: 走 model_adapter 主备链 (MiniMax → DeepSeek → Qwen 本地)
2) 规则链路: 关键词加权兜底 — 无 key / 熔断 / 解析失败时用, 保证永不返空

规则链路是 primary 的下界而不是摆设: A 股新闻标题高度模板化
("拟回购"/"业绩预增"/"股东减持"), 关键词命中率足够做粗筛。

2026-08-02 Ship 9 — 10000 轮迭代 P1 第四步
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 枚举 + 数据类
# ═══════════════════════════════════════════════════════

EVENT_TYPES = (
    "业绩",      # 预增/预减/年报/快报
    "重组",      # 并购/借壳/资产注入
    "监管",      # 问询函/立案/处罚/退市风险
    "订单",      # 中标/大单/合同
    "增持",      # 回购/股东增持/员工持股
    "减持",      # 股东减持/解禁
    "融资",      # 定增/可转债/配股
    "产品",      # 新品发布/技术突破/专利
    "合作",      # 战略合作/合资
    "人事",      # 高管变动/离职
    "政策",      # 行业政策/补贴/准入
    "其他",
)

_NEUTRAL_TYPE = "其他"


@dataclass
class NewsSentiment:
    """单条新闻的 5 维打分"""
    title: str = ""
    sentiment: float = 0.0            # -1 ~ +1
    confidence: float = 0.0           # 0 ~ 1
    event_type: str = _NEUTRAL_TYPE
    sector_impact: float = 0.0        # -1 ~ +1
    summary: str = ""
    source: str = "rule"              # "llm" | "rule"


# ═══════════════════════════════════════════════════════
# 规则链路 — 关键词加权
# ═══════════════════════════════════════════════════════

# (关键词, 情绪, 事件类型, 板块外溢系数)
# 外溢系数: 1.0 = 全行业共振 (政策), 0.2 = 纯个股事件 (人事)
_RULES: tuple[tuple[str, float, str, float], ...] = (
    # 强正
    ("业绩预增", 0.8, "业绩", 0.5),
    ("业绩大增", 0.8, "业绩", 0.5),
    ("扭亏为盈", 0.7, "业绩", 0.4),
    ("超预期", 0.7, "业绩", 0.5),
    ("中标", 0.6, "订单", 0.4),
    ("大额订单", 0.7, "订单", 0.5),
    ("签订合同", 0.5, "订单", 0.3),
    ("回购", 0.6, "增持", 0.3),
    ("股东增持", 0.6, "增持", 0.3),
    ("员工持股", 0.4, "增持", 0.2),
    ("技术突破", 0.7, "产品", 0.7),
    ("获批", 0.6, "产品", 0.5),
    ("新品发布", 0.4, "产品", 0.4),
    ("战略合作", 0.4, "合作", 0.3),
    ("资产注入", 0.7, "重组", 0.5),
    ("借壳", 0.6, "重组", 0.4),
    ("并购", 0.4, "重组", 0.4),
    ("政策支持", 0.7, "政策", 1.0),
    ("补贴", 0.5, "政策", 0.9),
    ("纳入", 0.5, "政策", 0.8),
    # 强负
    ("业绩预减", -0.8, "业绩", 0.5),
    ("业绩变脸", -0.9, "业绩", 0.5),
    ("亏损", -0.6, "业绩", 0.4),
    ("立案", -0.9, "监管", 0.4),
    ("处罚", -0.7, "监管", 0.4),
    ("问询函", -0.5, "监管", 0.3),
    ("退市", -1.0, "监管", 0.5),
    ("警示函", -0.6, "监管", 0.3),
    ("股东减持", -0.6, "减持", 0.3),
    ("拟减持", -0.6, "减持", 0.3),
    ("解禁", -0.4, "减持", 0.3),
    ("质押", -0.3, "减持", 0.2),
    ("定增", -0.2, "融资", 0.2),
    ("配股", -0.3, "融资", 0.2),
    ("可转债", -0.1, "融资", 0.2),
    ("高管离职", -0.4, "人事", 0.2),
    ("董事长辞职", -0.5, "人事", 0.2),
    ("终止", -0.5, "其他", 0.3),
    ("下调", -0.5, "其他", 0.4),
)

# 否定词 — 命中则把情绪翻转 (如 "不及预期" / "未中标")
_NEGATORS = ("不及", "未能", "未中", "取消", "撤回", "终止", "失败", "否决")


def _has_negator(title: str, kw: str) -> bool:
    """关键词前 6 字内出现否定词 → 视为反转

    窗口右端含关键词首字, 因为否定词常与关键词共享一个字 ("未中" + "中标")。
    只含首字而非整词, 避免 "终止" 这类既是规则词又是否定词的自我反转。
    """
    idx = title.find(kw)
    if idx < 0:
        return False
    window = title[max(0, idx - 6):idx + 1]
    return any(n in window for n in _NEGATORS)


def score_by_rule(title: str) -> NewsSentiment:
    """关键词加权打分 (无 LLM 兜底)

    多关键词命中时取绝对值最大的那条主导 event_type,
    情绪取命中项均值 (避免 "预增+减持" 这类混合消息被单边放大)。
    """
    title = (title or "").strip()
    if not title:
        return NewsSentiment(title="", summary="")

    hits: list[tuple[float, str, float]] = []
    for kw, senti, etype, spill in _RULES:
        if kw in title:
            if _has_negator(title, kw):
                senti = -senti
            hits.append((senti, etype, spill))

    if not hits:
        return NewsSentiment(
            title=title, sentiment=0.0, confidence=0.1,
            event_type=_NEUTRAL_TYPE, sector_impact=0.0,
            summary=title[:60], source="rule",
        )

    sentiment = sum(h[0] for h in hits) / len(hits)
    dominant = max(hits, key=lambda h: abs(h[0]))
    # 命中数越多置信度越高, 但单条关键词也有 0.45 的基础置信
    confidence = min(0.9, 0.45 + 0.15 * (len(hits) - 1))

    return NewsSentiment(
        title=title,
        sentiment=round(_clamp(sentiment, -1, 1), 4),
        confidence=round(confidence, 4),
        event_type=dominant[1],
        sector_impact=round(_clamp(sentiment * dominant[2], -1, 1), 4),
        summary=title[:60],
        source="rule",
    )


# ═══════════════════════════════════════════════════════
# LLM 链路 — prompt 构造 + 结果归一
# ═══════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是 A 股新闻情绪分析师。对给定新闻标题输出严格 JSON,不要任何解释文字。

字段:
- sentiment: -1~1 浮点, 对该股股价的短期影响 (负=利空)
- confidence: 0~1 浮点, 你对判断的证据强度 (标题信息不足时给低分)
- event_type: 必须是以下之一: 业绩/重组/监管/订单/增持/减持/融资/产品/合作/人事/政策/其他
- sector_impact: -1~1 浮点, 对所属板块的外溢影响 (纯个股事件接近 0, 行业政策接近 ±1)
- summary: 不超过 60 字中文摘要

只输出 JSON 对象。"""


def build_prompt(titles: list[str]) -> tuple[str, str]:
    """构造 (system, user) — 批量打分, 每行一条

    Returns:
        (system_prompt, user_prompt)
    """
    lines = [f"{i + 1}. {t}" for i, t in enumerate(titles)]
    user = (
        "请对以下新闻逐条打分,输出 JSON 数组,顺序与输入一致:\n"
        + "\n".join(lines)
    )
    return _SYSTEM_PROMPT, user


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _coerce_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_llm_result(d: dict, title: str = "") -> NewsSentiment:
    """LLM 原始 dict → NewsSentiment (越界裁剪 + 枚举白名单)

    LLM 经常返回 event_type="业绩预增" 这种子类, 用前缀匹配收敛到枚举。
    """
    if not isinstance(d, dict):
        return score_by_rule(title)

    etype = str(d.get("event_type", "") or "").strip()
    if etype not in EVENT_TYPES:
        etype = next((t for t in EVENT_TYPES if t in etype), _NEUTRAL_TYPE)

    summary = str(d.get("summary", "") or "").strip()[:60]

    return NewsSentiment(
        title=title or str(d.get("title", "") or ""),
        sentiment=round(_clamp(_coerce_float(d.get("sentiment")), -1, 1), 4),
        confidence=round(_clamp(_coerce_float(d.get("confidence")), 0, 1), 4),
        event_type=etype,
        sector_impact=round(_clamp(_coerce_float(d.get("sector_impact")), -1, 1), 4),
        summary=summary or (title or "")[:60],
        source="llm",
    )


def score_titles(titles: list[str], *, use_llm: bool = True) -> list[NewsSentiment]:
    """批量打分入口 — LLM 优先, 任何失败降级到规则链路

    Args:
        titles: 新闻标题列表
        use_llm: False 时直接走规则链路 (测试 / 离线)

    Returns:
        与 titles 等长的 NewsSentiment 列表 (永不返空, 永不抛错)
    """
    if not titles:
        return []
    if not use_llm:
        return [score_by_rule(t) for t in titles]

    try:
        results = _score_via_llm(titles)
        if results and len(results) == len(titles):
            return results
        logger.warning("news_sentiment: LLM 返回 %d 条 != 输入 %d 条, 降级规则链路",
                       len(results or []), len(titles))
    except Exception as e:
        logger.warning("news_sentiment: LLM 链路失败 (%s), 降级规则链路", e)

    return [score_by_rule(t) for t in titles]


def _score_via_llm(titles: list[str]) -> list[NewsSentiment]:
    """走 model_adapter 主备链调用 LLM"""
    from tuixue_v3 import model_adapter
    from tuixue_v3.web import ai_client

    system, user = build_prompt(titles)
    spec = model_adapter.call_with_fallback(
        system=system, user=user, name="news_sentiment",
        temperature=0.2, max_tokens=2000,
    )
    _raw, parsed, _info = ai_client.call(ai_client.CallSpec(
        url=spec.url, headers=spec.headers, body=spec.body,
        model=spec.model, name=spec.name, timeout=spec.timeout,
    ))

    items = parsed if isinstance(parsed, list) else parsed.get("results", parsed.get("data"))
    if not isinstance(items, list):
        return []
    return [normalize_llm_result(d, titles[i] if i < len(titles) else "")
            for i, d in enumerate(items)]


# ═══════════════════════════════════════════════════════
# 聚合 — 多条新闻 → 单只股票的情绪分
# ═══════════════════════════════════════════════════════

def aggregate(items: list[NewsSentiment]) -> dict:
    """多条新闻聚合成个股情绪分

    用 confidence 加权而非简单平均 — 一条"立案调查"(conf 0.9)
    不该被三条"参加行业论坛"(conf 0.1)稀释掉。
    """
    if not items:
        return {"sentiment": 0.0, "confidence": 0.0, "sector_impact": 0.0,
                "count": 0, "top_event": _NEUTRAL_TYPE}

    total_w = sum(i.confidence for i in items)
    if total_w <= 0:
        sentiment = sum(i.sentiment for i in items) / len(items)
        sector_impact = sum(i.sector_impact for i in items) / len(items)
    else:
        sentiment = sum(i.sentiment * i.confidence for i in items) / total_w
        sector_impact = sum(i.sector_impact * i.confidence for i in items) / total_w

    top = max(items, key=lambda i: abs(i.sentiment) * i.confidence)

    return {
        "sentiment": round(_clamp(sentiment, -1, 1), 4),
        "confidence": round(total_w / len(items), 4),
        "sector_impact": round(_clamp(sector_impact, -1, 1), 4),
        "count": len(items),
        "top_event": top.event_type,
    }


def to_dict_list(items: list[NewsSentiment]) -> list[dict]:
    """NewsSentiment 列表 → dict 列表 (JSON 序列化)"""
    return [
        {
            "title": i.title,
            "sentiment": i.sentiment,
            "confidence": i.confidence,
            "event_type": i.event_type,
            "sector_impact": i.sector_impact,
            "summary": i.summary,
            "source": i.source,
        }
        for i in items
    ]
