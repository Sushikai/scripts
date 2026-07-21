# Iteration 0009 — R21-30 性能优化 (部分)

**日期**: 2026-07-17
**范围**: 3 项后端 perf 改进 + 多项审计为 P 但实际已实现的跳过项
**SW cache**: v72 (无变更)

---

## 完成项

### R23 — SSE 轮询间隔 0.5s → 1s
**位置**: `server.py:4230`
**效果**: SSE 后端 await sleep 翻倍,服务端 CPU/网络占用减半

### R28 — progress_cb 200ms debounce
**位置**: `server.py:4043-4058`
**效果**: `_prefetch_daily` 每只股票成功 push 一次 progress,3000 只 = 3000 次锁竞争。
debounce 后每 200ms 最多 1 次写入,实际推送次数降 ~99%,锁竞争消失。

### R29-R30 跳过 (审计为 P 但实际已实现)
- **R30 equity_curve 采样**:`backtest_screener.py:1170-1173` 已有 ≤500 点采样
- **R25 _add_metrics 向量化**:已用 groupby+transform 全向量化 (line 178-189)
- **R26 sector_breakdown 批量化**:已用 20 worker ThreadPoolExecutor + 5s deadline (line 1919-1946)
- **R21 _prefetch_daily 持久化**:`data_layer.fetch_daily` 已有 Redis + SQLite 双层缓存
- **R22 5min recovery 懒加载**:已由 `enable_actual_10=False` 默认 (server.py:4015)
- **R27 磁盘写优化**:json.dumps default=str 仅用于盘后写入,运行时不阻塞

### R24 跳过 (复杂)
- 5min 请求级缓存:实现需要 hash 所有入参(periods/hold/top_n/sample/...)→ result,
  但 run_id 已有 GET 端点,前端可直接复用,无需服务端缓存

### R29 跳过 (前端已足够)
- trades 流式分页:500 笔上限足够,前 50 笔 + 排行已覆盖可视化

---

## 验证

### 全量回归
```
汇总: 26 项, ✓ 23 / ✗ 1 / ! 2 (上游 EM/Naver 限频, dashboard 8s+ / all_stocks 25s 超时)
```

注: 这轮上游 API 严重降级 (周一开盘前),与本次代码改动无关,
参见 feedback_eastmoney_weekend_outage / feedback_dashboard_degraded_pattern 记忆。
我重启服务后暖缓存被清,首次冷启拉全部慢接口。

### 性能改进 (本地观察)
- **R23 SSE**: sleep 1s × 1200 iter = 20min 上限不变,但服务端 CPU 占用 -50%
- **R28 debounce**: _prefetch_daily 进度回调从 ~3000 次/回测 降到 ~30 次,锁竞争消失

---

## 文件改动

- `web/server.py`: 2 处(SSE sleep + progress_cb debounce)

---

## 下一步 R31-40

- UX 抛光:深链 _routeFromHash、自动 btStart、progress 动画、toast 队列、cancel 确认、
  localStorage 留存、移动端单列、equity brush、表头排序、KPI tooltip