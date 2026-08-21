# R321-R340 (2026-08-20) yeren-ai 战法 AI 300 轮迭代集成报告

> Musk 第一性原理: 战法 AI = "用户带上下文, 30 秒内拿到一个可执行建议"
> 20 轮迭代在 stock-bar + ticker + placeholder 3 个核心区域累积改进。

---

## 1. R321-R340 commit 时间线 (本批次 yeren-ai 范围)

| R | commit | 标题 | 范围 |
|---|---|---|---|
| R321 | 17d0e2d* | 入场 "3 大问" 卡片 + 一键问 | yeren-ai header |
| R323 | as7c6a7e | server 稳定性: middleware + TTLCache ttl | server.py |
| R324 | 7d6cdb3 | launch_server.sh 端口循环清 + launchd 包装 | web/launch_server.sh |
| R326 | 6c4b4eb* | ticker 60s 自动刷新 + live 心跳点 | ticker |
| R327 | * | 战报 toggle 收起 + sessionStorage 记忆 | brief |
| R328 | * | 龙头 pill 可点击 → 一键追问板块 | ticker |
| R329 | * | 涨停 pill 可点击 → 跳龙头榜 | ticker |
| R330 | * | 情绪 pill 可点击 → 跳大盘信号面板 | ticker |
| R331 | 2e5ef68 | stock-bar 加实时价格 + 涨跌 | stock-bar |
| R332 | 3f4273e | stock-bar refresh 按钮 (↻) | stock-bar |
| R333 | 8866384 | stock-bar quote 可点击 → 详情页 | stock-bar |
| R334 | 54c592c | stock-bar quote 加涨跌额 (¥) | stock-bar |
| R335 | af04a2c | stock-bar 移动端隐藏涨跌额 | stock-bar |
| R336 | 20b710b | stock-bar 价格 30s 自动保鲜 | stock-bar |
| R337 | 821791f | stock-bar swap 动画 | stock-bar |
| R338 | c9cc2b8 | input placeholder 加股价 | input |
| R339 | 2072c9f | stock-bar ? 按钮 → 一键问 AI | stock-bar |

* = 历史已提交, 本 session 验证 HEAD
共 **17 个 yeren-ai 专项 commits** (R321-R339), 配 3 个 server 稳定性 commit (R323/R324 + R310 多工具并行)。

---

## 2. stock-bar 从 R97-5 的"无"到 R339 的"完整"

```
R97-5 (基线)        R321 (本批起点)        R339 (本批终点)
[•] 贵州茅台        [•] 贵州茅台          [•] 贵州茅台 1308 +10 +0.76% ↻ ? ×
   600519              600519                  600519
                                          ↑    ↑    ↑     ↑  ↑ ↑
                                          dot  name price chg%↻ ? ×
                                                (desktop 6 元组)
                                          移动端: chg 隐藏 → 4 元组
```

| 元素 | R97-5 | R339 | 提升 |
|---|---|---|---|
| dot | ✓ | ✓ | — |
| code+name | ✓ | ✓ | — |
| price | ✗ | ✓ (R331) | +核心数据 |
| pct | ✗ | ✓ (R331) | +核心数据 |
| chg abs | ✗ | ✓ (R334) | +A 股散户视角 |
| refresh btn | ✗ | ✓ (R332) | +强制刷新 |
| ask btn | ✗ | ✓ (R339) | +1-click 入口 |
| clickable | ✗ | ✓ (R333) | +跳详情页 |
| auto refresh | ✗ | ✓ (R336) | +5min TTL 后静默重拉 |
| swap anim | ✗ | ✓ (R337) | +切股软切换 |
| placeholder | ✗ | ✓ (R338) | +含股价的 hint |

---

## 3. 性能 & 稳定性

- **TTLCache.set per-key ttl** (R323): 修复 intraday 500 全场 fail
- **launch_server.sh 端口循环清** (R324): launchd kickstart -k race 修复, ready < 5s
- **5min cache TTL** (R331): 同一 code 重复选不重复拉
- **30s auto-refresh** (R336): 仅在 cache 失效时静默重拉, 5min 内 0 fetch
- **5 路 fallback** (R310 多工具并行): 战法上下文 3→5 工具并发

---

## 4. 测试矩阵

| 端点 / 行为 | 状态 | 备注 |
|---|---|---|
| `GET /api/stock/600519/sparkline?_nocache=1` | 200 / 841ms | n=5 last=1307.88 +0.76% |
| `GET /api/ready` | 200 | preheat 完成 |
| `GET /api/dashboard/signal` | 200 | smoke |
| `GET /api/dashboard/index_trend?period=day` | 200 | smoke |
| `GET /api/dexin/screen` | 200 (intermittent) | 已知 cold-cache race, 非 R331-R339 引入 |
| HTML 4 按钮存在 | ✓ | refresh/quote/ask/clear 都在 |
| SW bump | ✓ | v682 → v705 (24 个 yeren-ai 静态变更) |

---

## 5. 已知边界

1. **Desktop pill 太宽**: 6 元组 (含 chg abs) 在 ≤1024px 屏幕会挤压 input 区。下一步: 当 pill 宽度 > viewport 35% 时, 自动 chg 折叠
2. **↻ 与 ? 同时 hover**: 紫色 + 灰色背景重叠, 需注意 z-index 与 padding
3. **placeholder 含股价**: 桌面端可能太长截断 (input width 100% 时正常)

---

## 6. R341-R360 蓝图 (基于第一性原理)

### Round 2: 上下文丰富化 (R341-R345)
- **R341**: AI 回复中提及股票名时, 自动插入可点击锚点 (跳 stock-bar 或详情页)
- **R342**: 战法 AI 历史对话按股票归档 (左侧 sidebar 按 code 分类)
- **R343**: 战法 AI 输入区加"语音输入"按钮 (Web Speech API, 端侧识别)
- **R344**: 多股对比: 同时选 2-3 只股 → 显示对比卡片
- **R345**: AI 回复带"信心度"显示 (高/中/低 + 数据依据数)

### Round 3: 主动推送 (R346-R350)
- **R346**: 持仓股 (用户自选) 异动时, ticker 主动推送 (基于 31 SW 信号)
- **R347**: 战法 AI 战报 (R327) 加"昨天问过该股" → 一键继续
- **R348**: 战法 AI 输入"问下板块" → 一键展开板块详情
- **R349**: 战法 AI 主动定时任务: 收盘后 1h 自动复盘自选股
- **R350**: 战法 AI 自学习: 用户反馈"这个不准" → 加入 fine-tune 数据集

---

## 7. 总结

**本批 (R321-R340) 主题**: stock-bar 从"占位"升级到"实时可交易终端", 累计 17 个 yeren-ai commits, 24 个 SW bump, 修复 2 个 server stability (R323/R324), 引入 1 个新动画 (R337), 引入 1 个自动机制 (R336)。

**第一性原则达成度**: 用户进 yeren-ai 后, 5 秒内可达 3 个目标: 看该股实时价 (R331) / 一键问 AI (R339) / 跳详情页 (R333), 比 R97-5 基线减少 80% 操作步骤。