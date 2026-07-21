"""
web/_constants.py
全站常量集中 — TTL/超时/阈值/魔法数字
R-G67 (2026-07-19): 之前散落 server.py / cache_store.py / cache_db.py / *.py 200+ 魔法数字
现在统一 import 源,避免重复定义和不一致。
"""
from __future__ import annotations

# ────────────────────────────────────────────────────────────
# HTTP / API 超时 (秒)
# ────────────────────────────────────────────────────────────
API_DEFAULT_TIMEOUT = 8.0           # 默认 fetch 超时
API_HEALTH_TIMEOUT = 2.0            # /api/health keepalive
API_VERSION_TIMEOUT = 2.0           # /api/version
API_META_TIMEOUT = 3.0              # /api/_meta/*
API_INTEL_TIMEOUT = 10.0            # /api/intel/*
API_LONG_TIMEOUT = 25.0             # 长任务硬上限 (被 _request_timeout_middleware 强制)
API_AI_TIMEOUT = 30.0               # AI 调用
API_BACKTEST_STREAM_TIMEOUT = 600.0 # SSE 回测流

# ────────────────────────────────────────────────────────────
# TTLCache 默认 TTL (秒) — server.py _cache_* 实例
# ────────────────────────────────────────────────────────────
CACHE_TTL_SPOT = 60.0           # 全市场股票列表
CACHE_TTL_QUOTE = 5.0           # 实时行情 (盘口活)
CACHE_TTL_KLINE = 300.0         # 日线
CACHE_TTL_FUND = 60.0           # 资金流
CACHE_TTL_OVERVIEW = 15.0       # 大盘指数
CACHE_TTL_GLOBAL = 60.0         # 全球情绪
CACHE_TTL_LAYER = 600.0         # AI 层详情
CACHE_TTL_SEAT_BD = 600.0       # 8 类席位分类
CACHE_TTL_INTRADAY = 60.0       # 单股分时
CACHE_TTL_SECTOR = 3600.0       # 个股板块
CACHE_TTL_NEWS = 300.0          # 新闻

# cache_store.py Redis 默认 TTL
REDIS_TTL_DAILY = 4 * 3600       # 日线 4h
REDIS_TTL_INTRADAY = 30 * 60     # 分时 30min
REDIS_TTL_QUOTE = 5              # 行情 5s
REDIS_TTL_FUND = 60              # 资金流 60s
REDIS_TTL_SEAT_BD = 24 * 3600    # 席位分类 24h
REDIS_TTL_KLINE = 5 * 60         # K线 5min
REDIS_TTL_GLOBAL = 60            # 全球情绪
REDIS_TTL_AI = 6 * 3600          # AI 结果 6h
REDIS_TTL_CAPITAL = 60           # 资金 60s
REDIS_TTL_STOCKLIST = 24 * 3600  # 股票列表 24h

# ────────────────────────────────────────────────────────────
# 限频 (per IP)
# ────────────────────────────────────────────────────────────
RATE_LIMIT_DEFAULT_MAX = 60       # 默认 60 req/min
RATE_LIMIT_DEFAULT_WINDOW = 60    # 秒
RATE_LIMIT_AI_MAX = 20            # AI 端点 20 req/min
RATE_LIMIT_AI_WINDOW = 60
RATE_LIMIT_BACKTEST_MAX = 5       # 回测 5 req/min
RATE_LIMIT_BACKTEST_WINDOW = 60

# ────────────────────────────────────────────────────────────
# 交易阈值 (业务规则)
# ────────────────────────────────────────────────────────────
LIMIT_UP_PCT = 0.095              # 涨停 9.5% (科创板/创业板 19.5% 见 _is_limit_up_20)
LIMIT_DOWN_PCT = -0.095           # 跌停 -9.5%
STALE_QUOTE_SEC = 5               # 行情 5s 内算新鲜
STALE_FUND_SEC = 60               # 资金 60s 内算新鲜
STALE_DAILY_HOURS = 18            # 日线收盘 18h 后认为过期

# ────────────────────────────────────────────────────────────
# 内存/性能阈值
# ────────────────────────────────────────────────────────────
MEMORY_PROBE_INTERVAL_SEC = 300   # 内存探针 5min
MEMORY_WARN_MB = 200              # >200MB 警告
PERF_LONGTASK_MS = 100            # >100ms 主线程阻塞视为长任务
RENDER_SKELETON_MIN_MS = 200      # skeleton 至少显示 200ms (避免闪烁)
TOP_PROGRESS_SHOW_DELAY_MS = 400  # 顶部进度条 400ms 后才显示 (避免短请求闪)
# ────────────────────────────────────────────────────────────
# 尾盘战法 v149 加权排序 (业务规则)
# ────────────────────────────────────────────────────────────
# 加权得分 = pass_count + V2_WEIGHT · v2_score
# 1 个 v2_score (≈0.5) ≈ V2_WEIGHT/2 个规则;默认 5.0 意味着多因子略高于 8 条规则的地位
V2_WEIGHT = 5.0
