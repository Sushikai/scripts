"""R96: 100-slot deterministic visual verification schedule."""
# 10 categories × 10 slots = 100 iterations
# order: start with user's reported bug, then expand to functional coverage

CODES = ["002716", "300750", "600519", "002594", "000858", "002460", "601318", "002230", "300059", "600276"]

EDGE_QUERIES = [
    "",                                              # empty
    "   ",                                           # whitespace only
    "!@#$%^&*()_+={}[]|:;\"'<>,.?/~`",              # special chars
    "你好" * 200,                                    # 1000+ chars
    "🚀🔥 推荐股票 + https://example.com/yeren 🚀", # emoji + URL
    "002716",                                        # code only
    "   002716 现在可以买吗   ",                     # leading/trailing spaces
    "002716 \n \t \r 现在可以买吗",                  # whitespace mix
    "<script>alert(1)</script> 002716",              # xss attempt
    "查询" * 50,                                     # 100 chars repeat
]

LONG_QUERY = (
    "002716 最近走势怎么样?从多个维度分析: 1. 技术面 (BOLL/MACD/KDJ 状态) "
    "2. 资金面 (主力净流入/特大单/北向) 3. 战法 (周线擒牛是否触发?妖股基因?) "
    "4. 板块联动 (贵金属板块整体强度) 5. 风险点 (高位放量/主力出货) 6. 操作建议"
)

QUERIES = [
    # ── 01-10 USER_BUG ──
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": False},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": False},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": False},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": False},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": False},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "USER_BUG", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    # ── 11-20 MULTI_TOOL ──
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": False},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": False},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": False},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": False},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": False},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": True},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": True},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": True},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": True},
    {"cat": "MULTI_TOOL", "q": "推荐三只得鑫票 周线擒牛 主力 涨停", "code": "002716", "fb": True},
    # ── 21-30 SINGLE ──
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": False},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": False},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": False},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": False},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": False},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": True},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": True},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": True},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": True},
    {"cat": "SINGLE", "q": "002716 现在可以买吗", "code": "002716", "fb": True},
    # ── 31-40 CAT_3 ──
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": False},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": False},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": False},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": False},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": False},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": True},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": True},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": True},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": True},
    {"cat": "CAT_3", "q": "周线擒牛+主力+涨停", "code": "002716", "fb": True},
    # ── 41-50 CTX_0 ──
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": False},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": False},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": False},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": False},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": False},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": True},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": True},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": True},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": True},
    {"cat": "CTX_0", "q": "OBV/同比/特大单/压力位", "code": "002716", "fb": True},
    # ── 51-60 R313_KEYWORD ──
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": False},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": False},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": False},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": False},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": False},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": True},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": True},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": True},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": True},
    {"cat": "R313", "q": "封成比?封单?炸板?", "code": "002716", "fb": True},
    # ── 61-70 MULTIMODAL ──
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": False},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": False},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": False},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": False},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": False},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": True},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": True},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": True},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": True},
    {"cat": "MULTIMODAL", "q": LONG_QUERY, "code": "002716", "fb": True},
    # ── 71-80 EDGE ──
    {"cat": "EDGE", "q": EDGE_QUERIES[0], "code": "002716", "fb": False},  # empty
    {"cat": "EDGE", "q": EDGE_QUERIES[1], "code": "002716", "fb": False},  # whitespace
    {"cat": "EDGE", "q": EDGE_QUERIES[2], "code": "002716", "fb": False},  # special chars
    {"cat": "EDGE", "q": EDGE_QUERIES[3], "code": "002716", "fb": False},  # 1000+ chars
    {"cat": "EDGE", "q": EDGE_QUERIES[4], "code": "002716", "fb": False},  # emoji+URL
    {"cat": "EDGE", "q": EDGE_QUERIES[5], "code": "002716", "fb": True},   # code only
    {"cat": "EDGE", "q": EDGE_QUERIES[6], "code": "002716", "fb": True},   # leading/trailing
    {"cat": "EDGE", "q": EDGE_QUERIES[7], "code": "002716", "fb": True},   # whitespace mix
    {"cat": "EDGE", "q": EDGE_QUERIES[8], "code": "002716", "fb": True},   # xss
    {"cat": "EDGE", "q": EDGE_QUERIES[9], "code": "002716", "fb": True},   # 100 chars repeat
    # ── 81-90 STRESS (5 codes rotation) ──
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "300750", "fb": False},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "600519", "fb": False},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "002594", "fb": False},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "000858", "fb": False},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "002460", "fb": False},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "300750", "fb": True},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "600519", "fb": True},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "002594", "fb": True},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "000858", "fb": True},
    {"cat": "STRESS", "q": "可以买吗?给个明确建议", "code": "002460", "fb": True},
    # ── 91-100 RETRY_FORCE (always T) ──
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
    {"cat": "RETRY_FORCE", "q": "推荐三只得鑫票", "code": "002716", "fb": True},
]

assert len(QUERIES) == 100, f"expected 100, got {len(QUERIES)}"
