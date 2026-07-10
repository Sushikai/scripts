# Events Hub - 近期大事件中心

5 个模块聚合 A 股近期事件。**akshare 数据源 + SQLite 缓存 + 重试容错**。

## 模块清单

| # | 模块 | 接口 | 状态 |
|---|---|---|---|
| 1 | A股日历 | 交易日历/分红/解禁/财报披露 | ✅ |
| 2 | 宏观日历 | 财经日历/CPI/PMI/LPR | ⚠️ 财经日历被 push2 接口封 |
| 3 | 板块/题材 | 行业/概念/资金流 | ⚠️ 被 push2 接口封 |
| 4 | 个股事件 | 资金流/解禁/公告 | ⚠️ 资金流被封 |
| 5 | 涨停潮 | 涨停池/炸板/强势股 | ✅ |

## 已知问题（外部）

`push2.eastmoney.com` (东财部分接口) 间歇性 RemoteDisconnected/超时。
**但涨停池/强势股/解禁/财报** 这些 _em 接口是好的。

## 使用

```bash
cd /Users/kaikai/scripts/events_hub
python3 tests/test_e2e.py   # 端到端测试
```

## 数据缓存

`data/events_cache.db` - SQLite
TTL 规则：
- 涨停类 / 资金流：30 分钟
- 财经日历：1 小时
- 交易日历 / 解禁 / 分红：24 小时

## 项目结构

```
events_hub/
├── core/
│   ├── utils.py    # 日期/代码标准化
│   └── cache.py    # SQLite 缓存层
├── sources/
│   ├── a_calendar.py      # 模块1
│   ├── macro_calendar.py  # 模块2
│   ├── sector_event.py    # 模块3
│   ├── stock_event.py     # 模块4
│   └── limit_up.py        # 模块5
├── api/                   # 待开发（FastAPI）
├── data/events_cache.db
├── tests/test_e2e.py
├── docs/
└── README.md
```

## 后续

1. FastAPI 暴露 5 个 endpoint
2. 与 tuixue_v3 联动（新闻事件 → 板块情绪信号）
3. 定时调度：盘前/盘中/盘后自动刷新
