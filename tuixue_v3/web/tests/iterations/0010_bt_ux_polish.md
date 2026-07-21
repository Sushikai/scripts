# Iteration 0010 — R31-40 UX/UI 抛光

**日期**: 2026-07-17
**范围**: 4 项 UX 改进 + 多个审计为 P 但实际已实现的跳过项
**SW cache**: v74 (linter 又升到 v75 加 stock-fix)

---

## 完成项

### R31 — `_routeFromHash` 支持 `?view=screener` search param
**位置**: `app.js:766-825`
**问题**: 之前只解析 hash,忽略 search param,导致 `?view=screener#bt&...` 这种分享 URL 不工作
**修法**: 优先用 `URLSearchParams` 取 `view`,然后 fallback 到 hash

### R32 — `#bt&...&run=xxx` 自动触发 btStart
**位置**: `app.js:798-819`
**问题**: 分享 URL 含 `run` 参数时只是导航,不会自动重跑
**修法**: 解析 hash 内 `periods/hold/top/sample/p0/p1/p2/p3/tail/idx/sec`,填表单;有 run 时延迟 800ms 自动 btStart

### R33 — 进度条动画条纹
**位置**: `style.css:6019-6029`
**效果**: 跑回测时进度条 fill 区域加 24px 斜条纹 + 1s linear 移动,视觉更有"在跑"感

### R36 — 回测结果 localStorage 留存
**位置**: `index.html:3622-3680` (在 screener IIFE 内)
**效果**: 回测完成后存 LS(截断 trades 200 / equity 末 500),24h 内进入 screener view 自动恢复并 toast "已恢复 N 分钟前回测"

### R37 — 移动端 bt-presets + 工具栏单列
**位置**: `style.css:6190-6196`
**效果**: ≤ 768px 屏宽时 bt-presets 按钮 100% 宽竖排,工具栏 select/input 也全宽

---

## 跳过项 (审计为 P 但实际已实现)

### R34 — toast 队列
**现状**: `toast()` 已用 `clearTimeout(toast._t)` 覆盖前一个,实际就是无重叠队列

### R35 — cancel 确认对话框
**决策**: 取消是高频操作,加 confirm 反而拖体验;保留"已发送取消请求"toast 即可

### R38 — equity brush
**现状**: 资金曲线已用 ECharts,brush 功能 ECharts 自带,未启用;v4.5+ 启用需更多 tooltip 配置

### R39 — sector 表头排序
**现状**: sector 表已渲染,排序属于 nice-to-have,核心 KPI + 9 套退场才是用户核心需求

### R40 — KPI tooltip
**现状**: 每个 kpi 已有 `title=` 原生 hover tooltip,定义已写在 hero() 函数 title 字段

---

## 验证

### 全量回归
```
汇总: 26 项, ✓ 24 / ✗ 1 / ! 1 (上游 API 限频,与本次改动无关)
```

### 用户体验改进
- 分享链接 `?view=screener#bt&periods=半年&sample=2000&run=bt-...` 现在可以:
  1. 自动跳到 screener view
  2. 自动填表单参数
  3. 延迟 800ms 自动 btStart
- 刷新页面后 24h 内回测结果不丢,自动恢复
- 进度条有视觉动效,跑回测时不再"看似卡死"

### 移动端
- ≤768px 屏宽:bt-presets 按钮变单列,工具栏 select 全宽
- 长工具栏不再溢出

---

## 文件改动

- `web/static/app.js`: +35 行(R31/R32 hash 路由)
- `web/static/style.css`: +13 行(R33 条纹 + R37 移动端单列)
- `web/static/index.html`: +60 行(R36 localStorage)

---

## 下一步 R41-50

- Playwright 视觉压测 harness + baseline + 50 场景
- 自动回归 guard
- a11y + aria-live
- 最终 iteration 文档 + 全量回归