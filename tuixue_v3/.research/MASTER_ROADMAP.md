# tuixue_v3 10000 轮迭代 — 综合路线图 (MASTER)

> 创建日期: 2026-08-02
> 范围: tuixue_v3 (A 股 Web 量化平台, FastAPI + ECharts)
> 目标: 4 维度 (数据/策略/性能/UI) × 10000 ship 改动, 1 轮 = 1 commit
> 输入: `.research/data_sources_2026_08.md` + `.research/quant_models_2026.md` + `.research/architecture_audit_2026_08.md`

---

## 0. 总览

### 10000 轮分布

| 维度 | 轮数 | 占比 | 核心产出 |
|------|------|------|----------|
| 数据源扩充 | 2500 | 25% | FetchRegistry + 多市场 + 多源兜底 |
| 策略/因子 | 3500 | 35% | Qlib Alpha158 + LLM 情绪 + ML 砸盘 + 板块轮动 |
| 性能与稳定性 | 2000 | 20% | 分布式优化器 + 跨日防御 + 缓存升级 |
| UI/UX 可视化 | 1500 | 15% | 移动端 + 个股页 + 龙头页 + 锦囊可视化 |
| AI/模型 | 500 | 5% | 多模型共存 (GPT/Claude/DeepSeek) |
| **合计** | **10000** | **100%** | **~100 ship 改动, 6-9 个月** |

### 优先级矩阵 (P0 / P1 / P2)

| 维度 | P0 (1-2 周) | P1 (1 月) | P2 (季度) |
|------|------------|-----------|-----------|
| 数据 | Tushare 接入 + 新浪兜底 (1000轮) | 港股/ETF/北交所 (800轮) | 雪球/同花顺/聚合 (700轮) |
| 策略 | Qlib Alpha158 + 龙虎榜因子 + DeepSeek 情绪 (1500轮) | MASTER/StockFormer + AlphaGen + 板块轮动 (1200轮) | SAC RL + PatchTST + 多agent (800轮) |
| 性能 | 跨日污染终极防御 (500轮) | 分布式优化器 + WebSocket (800轮) | 异地多活 + 跨 worker 回测 (700轮) |
| UI | 移动端 200 viewport + 个股页 100 轮 (600轮) | 龙头页/自选/全A 视觉统一 (500轮) | 自然语言 ECharts + 多账户归因 (400轮) |
| AI | ModelAdapter 多模型 (500轮) | — | — |

---

## 1. P0 — 立即开干 (1-2 周, 2500 轮)

### 1.1 数据源 (1000 轮)

| 改造 | 文件 | 收益 | 轮数 |
|------|------|------|------|
| Tushare Pro 接入 (pro_bar + daily_basic) | `lib_common.py` | 补财务/复权/历史回测 | 400 |
| 新浪 hq.sinajs (Referer + HTTPS) | `lib_common.py` | 腾讯挂掉时备援 | 300 |
| FetchRegistry 统一注册器 | `data_source_registry.py` (新) | 新源接入 1 周 → 1 小时 | 300 |

### 1.2 策略 (1500 轮)

| 改造 | 文件 | 收益 | 轮数 |
|------|------|------|------|
| Qlib Alpha158 集成 + LightGBM stacking | `web/factors/alpha158.py` (新) | 158 公式 alpha, CSI300 Sharpe 1.12 | 500 |
| 龙虎榜 + 大宗交易事件因子 (5 个) | `seat_classify.py` | ⭐ 策略胜率 +3-5% | 400 |
| DeepSeek 新闻情绪打分 | `news_lookup.py` | 日频 alpha +2% | 600 |

### 1.3 性能 (500 轮)

| 改造 | 文件 | 收益 | 轮数 |
|------|------|------|------|
| 跨日污染终极防御 (zk-style invariant) | `cache_db.py` + `web/server.py:7020` | 跨日 bug 0 容忍 | 500 |

### 1.4 AI (500 轮)

| 改造 | 文件 | 收益 | 轮数 |
|------|------|------|------|
| ModelAdapter 多模型共存 (**MiniMax 主 + DeepSeek 辅** + 本地 Qwen 兜底) | `web/ai_client.py` | 切模型 3 天 → 30 分钟 | 500 |

**P0 总计**: ~4500 轮 (4.5 ship)

---

## 2. P1 — 1 个月内 (3500 轮)

### 2.1 数据 (800 轮)

- 港股/ETF/北交所 (500)
- 公告/财报/业绩预告 (300)

### 2.2 策略 (1200 轮)

- MASTER Transformer cross-sectional head (400)
- 板块轮动因子包 (5 个) (400)
- AlphaGen LLM 自动因子挖掘 (400)

### 2.3 性能 (800 轮)

- 分布式优化器 Redis SortedSet (500)
- WebSocket 实时推送 (300)

### 2.4 UI (500 轮)

- 龙头页/自选/全A 视觉统一 (500)

---

## 3. P2 — 季度级 (4000 轮)

- 数据: 雪球/同花顺/聚合 (700)
- 策略: SAC RL 仓位 + PatchTST + 多 agent (800)
- 性能: 异地多活 + 跨 worker 回测 (700)
- UI: 自然语言 ECharts + 多账户归因 (400)
- 缓冲 + 调研 (1400)

---

## 4. 关键依赖与顺序

```
[1] FetchRegistry (P0-1.1) ──┐
                              ├──> 所有后续接入依赖
[2] ModelAdapter (P0-1.4) ───┘
                              │
                              ↓
[3] Tushare/新浪 (P0-1.1) ──> Qlib Alpha158 (P0-1.2)
                              │
                              ↓
[4] 跨日防御 (P0-1.3) ──────> 分布式优化器 (P1-2.3)
                              │
                              ↓
[5] 板块轮动 (P1-2.2) ──────> MASTER stacking (P1-2.2)
```

---

## 5. 风险与约束

### 已识别风险 (来自 3 份子报告)

1. **A 股特殊**: T+1 / 涨跌停 / ST 摘帽 — 回测必须 mask
2. **因子衰减**: 6-12 月衰减 30-50% — 季度 IC 监控
3. **未来数据**: walk-forward 验证 + train/val/test 三段
4. **交易成本**: A 股双边 0.15-0.25% (含印花税) — 高频必实盘
5. **LLM 延迟**: 1-5s/次, 实时分钟级不可行 — T+1 决策够用
6. **PPO/RL 不稳定**: 10 run 仅 2-3 次能用 — SAC 更稳但调参敏感
7. **沙箱 DNS 劫持**: 198.18.x 全劫持, 任意 IP TLS 阻断 — cloudflared/ngrok/localhost.run 不可用
8. **工作树污染**: 当前有大量未提交改动 — 建议开独立 worktree 隔离

### 约束

- 每 ship 必走回归测试 (memory `feedback_regression_test_mandatory`)
- 每 ship 必写 memory (防止重复犯错)
- SW cache 必 bump (防凝固旧 JS)
- 每 ship 前 audit_views / audit_mobile (端到端验证)

---

## 6. 工作流约定

### 6.1 一轮 (1 ship) 标准流程

```
1. 选改造点 (按 P0/P1/P2 + 当前瓶颈)
2. 开独立 worktree (避免污染主分支)
3. 写代码 → 单测 → 集成测 → e2e 测
4. 回归测试 (mobile + desktop)
5. commit + SW bump (如前端)
6. 写 memory 记录坑 + 方案
7. 合并回主分支 (or 留 worktree)
```

### 6.2 暂停条件

- 连续 3 ship 回归失败 → 暂停, 调研根因
- 关键端点 P95 退化 > 50% → 立即回滚
- SW 缓存凝固 → 强制 bump + cache:reload
- LLM API 限频 → 切本地 Qwen 兜底

### 6.3 进度跟踪

- `.research/PROGRESS_DASHBOARD.md` (持续更新)
- 每 100 ship 一个里程碑评审
- 每 1000 ship 一个综合回顾

---

## 7. 待用户决策

- [ ] **是否开独立 worktree** (建议是, 避免污染)
- [ ] **P0 4.5 ship 是否全量做** (还是先做 1-2 ship 验证流程)
- [ ] **每月 ship 速度** (每周 2 ship / 每周 5 ship / 每天 1 ship)
- [ ] **是否启用 LLM API 预算** (DeepSeek 性价比高, GPT-4 贵)
- [ ] **Tushare 200 元/年 是否值得** (个人项目建议直接上)
- [ ] **风险偏好** (保守 vs 激进)

---

## 8. 立即可启动的下一步

如用户决策 OK, 建议立即启动:

**第 1 ship**: FetchRegistry 注册器 (300 轮)
- 新建 `data_source_registry.py` (~200 行)
- 把 12 个核心 fetch 接入
- 1 个端点试运行 (e.g. `/api/stock/{code}/sparkline`)
- 回归测试通过后 ship

预计 2-3 天完成。

---

## 9. 关键数据源/模型推荐组合 (合并 3 份子报告)

### 数据源 (最终方案 C)

```
主: Tushare Pro 200元/年 (财务/复权/分钟线)
辅: akshare stock_zh_a_spot_em (实时盘口)
备: 腾讯 qt.gtimg (实时五档, 北证 920xxx 走 _tencent_mkt helper)
兜底: 新浪 hq.sinajs (HTTPS+Referer)
国际: Naver mobile (KOSPI)
灾备: tsanghi.com / juhe.cn (凌晨批量)
```

### 模型 (P0 三件套)

```
1. Qlib Alpha158 (microsoft/qlib, 158 公式 alpha)
2. DeepSeek-V3 (新闻情绪 -1~1, confidence 0~1)
3. 龙虎榜 5 因子 (机构席位净买 / 游资席位净买 / 溢价率 / 调研次数 / 大宗折价)
```

---

## 10. 与已有 memory 的协同

已参考的 memory (按重要性排序):

- `feedback_eastmoney_weekend_outage` — 数据源限频痛点
- `feedback_tuixue_v3_capital_flow_rce` — 入口白名单重要性
- `feedback_tuixue_v3_sqlite_safe_write` — cache 写保护模式
- `feedback_tuixue_v3_R3_sources_health_view` — 已 ship 数据源看板
- `feedback_tuixue_v3_R2_degraded_endpoints_R2` — 已 ship _degraded 模式
- `feedback_tuixue_v3_tier_speedup` — Redis L1 + 顶层架构提速 (300x)
- `feedback_tuixue_v3_5x_speedup` — Redis 跨 worker 共享 (31x)
- `feedback_tuixue_v3_no_leverage` — mult=1/top_n 真实胜率
- `feedback_network_dns_hijack` — 沙箱网络限制
- `feedback_regression_test_mandatory` — 每 ship 必回归
- `feedback_self_verify` — 完成后必自验证

---

**路线图版本**: v1.0 (2026-08-02)
**下次更新**: 每完成 100 ship 更新一次
**总字数**: ~2200 字
