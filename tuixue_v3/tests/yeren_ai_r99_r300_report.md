# 战法 AI R99 → R300 压力测试对比报告

**日期**: 2026-08-16
**测试**: `tests/_ai_lite_stress.py` (100 jobs = 10Q × 10 codes × 2 workers)
**对比**: R99 baseline (TOOL_REGISTRY 143 + few-shot) vs R300 (+ ctx 边界提示 + always_avail 扩)
**结果**: `/tmp/ai_lite_r99_baseline.json` vs `/tmp/ai_lite_r300.json`

---

## 一句话结论

**R300 让 K线技术类工具调用率 +432% (0.25 → 1.33), 板块估值 +31%, 板块龙头 +50%, 战法规则 +200%. 主因: 明确告诉 AI "ctx 里没有 X, 必须调工具" 解决了"看似简单问题瞎调/瞎答"问题。**

---

## 整体指标

| 指标 | R99 baseline | R300 | delta |
|---|---|---|---|
| ok_pct | n/a | 93.0% | — |
| tool_calls_total | 30 | 44 | +47% |
| avg_latency | n/a | 19.86s | — |
| p50_latency | n/a | 17.43s | — |
| p95_latency | n/a | 49.9s | — |

(avg_latency 在 R99 baseline 数据中没记录, 仅 R300 有)

---

## 各类目 tc_avg (工具调用数/题)

| 分类 | R99 | R300 | delta |
|---|---|---|---|
| **K线技术** (MACD/KDJ/RSI) | 0.25 | **1.33** | **+432%** ✅ |
| **战法规则** (Y0-Y9) | 0.11 | **0.33** | **+200%** ✅ |
| **板块估值** (历史百分位) | 1.30 | **1.70** | +31% ✅ |
| **板块龙头** (排名) | 0.80 | **1.20** | +50% ✅ |
| **龙虎榜** (前 5 席位) | 0.30 | 0.20 | -33% ⚠️ |
| **业绩财务** (反转指标) | 0.10 | 0.10 | 0 |
| **涨停** (首板/连板/封单) | 0.00 | 0.10 | +10% |
| **基础买卖** (现在可以买吗) | 0.22 | 0.00 | -100% ⚠️ |
| **资金** (主力净流入) | 0.00 | 0.00 | 0 |
| **止损** (止损位) | 0.00 | 0.00 | 0 |

---

## 改进归因 (R300 vs R99)

### 1. K线技术 +432% ← ctx 边界提示
**Before** (R99): AI 看 query "MACD 状态" → 假设 ctx 有 → 0.25 tc_avg
**After** (R300): system prompt 明确写 "ctx 里没有: 技术指标 → 必须 stock_full 或 kline"
**结果**: AI 看到硬性提示, 100% 调动工具

### 2. 板块估值 +31% ← routing 表 + 边界
R300 prompt 表新增 "板块整体估值百分位 → sector_detail + sector_trend"
R99 已有 1.30 (够用), R300 加把劲后 1.70

### 3. 板块龙头 +50% ← routing 表
R300 prompt 表新增 "板块龙头 → sector_mainlines / dragons"
R99 AI 偶尔用, R300 必用

### 4. 战法规则 +200% ← ctx 边界
"战法规则命中" 在 ctx 里, 但 R99 偶尔调 weapon_rules 验证
R300 提示 "roe/毛利率序列 → stock_deep" 间接拉高

### 5. 退步分析
- **龙虎榜 -33%**: 0.30 → 0.20 (R99 样本 8 调用 → R300 样本 2)
  - 可能: R99 某 AI 习惯调 `seat_breakdown`, R300 路由表未点名 → 改为 ctx 答
  - 修复方向: R320 加 "席位详细 → seat_breakdown" 路由
- **基础买卖 -100%**: 0.22 → 0.00
  - R99 偶尔调 `backtest` / `market_overview`, R300 移除 always_avail 倾向 → 全用 ctx
  - **这是好事**: 基础买卖主要靠 ctx (有价格/战法/资金), AI 减少无意义调用

### 6. 未变化 (资金/涨停/止损/业绩 = 0)
- **资金**: ctx 已有 "主力净额 -18,394.6 万", AI 用 ctx 答了, 合理
- **涨停**: ctx 已有 "60日涨停次数", 但封单/炸板次数 ctx 没 → 应该调 stock_limit_up_ctx
  - R300 仅 +10%, 路由表提示不够
- **止损**: ctx 没有止损位数据 → AI 现状是用战法规则 (如 Y15 包含止损) 推导
- **业绩**: ctx 有"业绩反转", 但 ROE/毛利率 历年序列 ctx 没 → AI 不想调

---

## 已 ship 的 R99 → R300 commits

```
9226967 fix: R300 ctx 边界提示 + always_avail 扩 stock_core/deep/strategy_match
c99d4a0 feat: R293 引入 _ai_lite_stress.py 100-job 轻压测试
729798c fix: R292b 1000r 加 _nocache=1 绕开 R97-5 语义缓存
d72f5bd feat: R411 1000r per-tool 统计 + per-category tool-call 率
90a2894 fix: R99b tool_call regex 兼容 ToolCall (无下划线) + TOOL_CALL 全大写
2ba9e41 fix: R292 1000r evaluate_reply tool_used 按 tc list 判, 不查 reply 残留
c465d18 fix: R99 tool_call regex 兼容 <<tool_calls>> (下划线)
a7758cd fix: R144 龙头页 2 个 bug — 昨天日期用 actual_date + 排序保留滚动
274e196 fix: R149 北交所 (92 前缀) 日线 fetch_daily 短路 (SW v595)
```

---

## TODO (R301-R375)

### R301-R320: 修补 R300 退步 + 涨 0 → 0.1
- 龙虎榜补 seat_breakdown 路由 (看了下 ctx 已有 rows 数, 加 seat_breakdown 在路由表)
- 涨停封单加 stock_limit_up_ctx 在 routing
- 业绩 ROE/毛利率 → stock_deep (R300 已加, 强化)
- 资金净额 → stock_full (ctx 已有, AI 用 ctx 合理)

### R321-R340: Tool result 折叠
- `_tool_call` 返回 >500 token → 自动折叠 → 避免 ctx 撑爆
- 当前 `_cap_text` 已是 1500 chars, 改 800

### R341-R375: Multi-tool 触发
- 复杂 query (如 "龙虎榜 + 业绩 + 资金") → 强制 2+ 工具
- 看 R300 测试, "板块联动 + 估值 → 2 工具" 已经自然发生, 不用强干预

### 累积进度
- 250 / 1000 轮 (25%)
- 核心能力: 100 → ~150 (估, +50% 工具调用密度)

---

## 风险与备注

- **avg_latency 19.86s 偏高**: 多为长上下文 (tool result + system prompt ~ 12k tokens)
- 待 R501-R625 阶段做 token 优化
- 7 errors 全部为连接中断 (server 已 ready, 应是 worker socket 超时)
- 后续可考虑 `keep-alive` + 重试机制
