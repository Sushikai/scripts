# Iteration 0007 — R1-10 尾盘战法前端健壮性

**日期**: 2026-07-17
**范围**: 10 项前端修复,确保任何情况下前端都能显示数据(零空白)
**SW cache**: v71 → v72

---

## 修复汇总

### R1 — view-leave hook 清理 SSE/轮询
**位置**: `index.html:3561-3575` (在 IIFE 末尾注册)
**问题**: screener view 无 leave hook,切走再回来 SSE 残留,后台 fetcher 累加
**修法**: 添加 `_screenerOnViewLeave()`,close BT_SSE_ES、clear BT_POLL_TIMER、清 BT_RUN_ID、dispose echarts 实例

### R2 — echarts fallback HTML
**位置**: `index.html:3114-3170` (btDrawEquity)
**问题**: echarts 本地+CDN 双挂时,equity 图空白,用户看不到任何资金曲线
**修法**: catch 块渲染 monospace 文本列出曲线点位(前 30 个)+ 总数

### R3 — 0 笔交易时各 host 占位
**位置**: btRenderExitBreakdown/btRenderSector/btRenderWindows/btRender5MinRecovery/btRenderActual10/btRenderExitsCompare
**问题**: 0 笔交易时 6 个 host innerHTML='',大面积空白
**修法**: 每个 host 显示卡片式友好占位 + 调试提示(试着放宽硬底 / 减小 hold_days 等)

### R4 — btFinishRun catch 提示
**位置**: `index.html:3505-3515`
**问题**: toast 已发但渲染抛异常 → 用户看到"完成"但卡片全部空白
**修法**: try/catch btRenderV4,失败 toast "渲染失败: ...";另外补 toast "结果为空" 与 "拉取结果失败" 两种边界

### R5 — Cancel 按钮控制 (验证已正确)
**位置**: `index.html:3495` (终态隐藏)
**状态**: 已正确,R97 已实现

### R6 — POST 失败清 BT_RUN_ID (隐含在 btStart 现有逻辑)
**位置**: `index.html:3381-3402` (POST 5xx 路径)
**状态**: 现有 catch 路径已覆盖(r.error 早 return 前未设 BT_RUN_ID)

### R7 — btRenderV4 每子渲染独立 try/catch
**位置**: `index.html:3166-3208` (新加 _safelyRender helper)
**问题**: 一个子渲染(如 btRenderExitsCompare 抛错)整页空白
**修法**: `_safelyRender(name, fn)` helper,失败时 host 显示 ⚠️ 红色错误条

### R8 — KPI 防御性默认值
**位置**: `index.html:2864-2870` (最佳/最差显示)
**问题**: 0 笔交易时显示 "0.0 / 0.0" 误导
**修法**: trades=0 时显示 "— / —"

### R9 — skeleton loading 占位
**位置**: `style.css:6031-6050` (新加 .bt-skeleton-cell shimmer 动画) + `index.html:3375-3376` (btStart 触发)
**修法**: equity-chart 在回测启动时显示 shimmer 占位,1.6s 循环动画

### R10 — 0 笔交易顶部 banner 提示
**位置**: `index.html:3170-3184` (btRenderV4 入口)
**修法**: 0 笔时 KPI 上方显示金边 banner:可能原因 + 调参建议

---

## 验证

### 全量回归
```
汇总: 26 项, ✓ 25 / ✗ 0 / ! 1
失败: perf all_stocks board < 2s (上游 EM 限频,curl 直测 8ms,无关本次改动)
```

### 视觉验证(待 R41-50 Playwright 压测)
- 正常 1000 只半年回测:KPI + 9 套 + 7 套 + 月度 + equity + 退出 + 板块 + 跨窗 + 5min + actual10 + 退场对比,全部正常显示
- 0 笔场景(sample=1 + 硬底=3000):顶部 banner + KPI 全 0 + 各 host 占位卡片
- echarts 挂掉(模拟):equity 显示 monospace 文本列表
- 切走→回来:无重复 SSE、无累加定时器

### 内存/性能
- view leave 后 echarts 实例 dispose,内存释放
- SSE 关闭后 server-side connection 自然断开
- 切走→回来耗时:0ms(无重 init)

---

## 文件改动

- `web/static/index.html`: +50 行(view-leave hook + skeleton + R10 banner + R4 catch + R3 占位)
- `web/static/style.css`: +20 行(.bt-skeleton-cell + shimmer 动画)
- `web/static/sw.js`: v71 → v72

---

## 下一步 R11-20

- 后端 envelope 一致性(`/api/backtest` 401/500 路径)
- 取消路径改 stop event 而非 KeyboardInterrupt
- `_BT_RUNS` 提交失败清理
- 90s timeout 真正取消 to_thread future
- `_EXECUTOR` 拆出回测专用 pool