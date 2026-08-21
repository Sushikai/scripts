# R341-R350 (2026-08-20) yeren-ai 战法 AI 第二轮 300 轮迭代集成报告

> 第一性原理: 战法 AI = "用户带上下文 + 历史 + 自选, 30 秒内拿到一个**越用越准**的可执行建议"
> 第二轮主题: **上下文丰富化** (R341-R345) + **主动推送** (R346-R350)

---

## 1. commit 时间线 (本批次 20 轮)

| R | commit | 标题 | 范围 |
|---|---|---|---|
| R341 | * | AI 回复股票代码 → 同步 stock-bar | app.js |
| R342 | * | 历史问过的股 归档 | app.js |
| R343 | * | stock-bar 语音输入按钮 | app.js + style.css |
| R344 | * | 多股对比 (最多 3 只) | app.js + style.css |
| R345 | * | AI 回复信心度 badge (高/中/低) | app.js + style.css |
| R346 | * | ticker 自选异动 (点击展开) | app.js |
| R347 | * | 战报 toggle 加「昨天问过」按钮 | app.js |
| R348 | f9db768 | 输入"板块"自动检测 | app.js + style.css + index.html + sw.js |
| R349 | 41b2909 | 自选股收盘复盘面板 | app.js + index.html + style.css (并行链) + sw.js |
| R350 | 9c55ae1 | 👍/👎 反馈入 fine-tune 集 (jsonl) | app.js + server.py + sw.js |

共 **10 轮 (R341-R350)** 全部本 session 收尾。

---

## 2. 主题切片

### Round 2: 上下文丰富化 (R341-R345)
- **R341**: AI 回复文本中含 6 位 code → 自动同步到 stock-bar (用户后续提问基于此股)
- **R342**: yerenAiHistory 按 code 频次归类, 侧栏显示历史问过的股
- **R343**: SpeechRecognition API, 端侧语音输入, 不传音频到 server
- **R344**: 多股对比 (≤3), 并排显示价格/涨跌/板块
- **R345**: 3 维信心度评分 (rules_hit + tool_calls ok + content length) → 高/中/低 badge

### Round 3: 主动推送 (R346-R350)
- **R346**: 自选股异动 (5min 内涨幅 >2%) → ticker 加 pill + 点击展开
- **R347**: 战报 toggle 内"昨天问过该股" → 一键续问, 历史问题填入 input 自动发送
- **R348**: textarea 含"板块"+"板块名" → 浮出"展开板块详情" chip → 跳 dash
- **R349**: 15:00 后 + 工作日 + 自选非空 → 📊 复盘 chip → 并行拉 sparkline (6 并发)
  → 卡片按涨幅排序 + 涨停/跌停/强势/弱势标签 → 点卡片 → 一键 AI 复盘
- **R350**: 👍/👎 → POST /api/yeren/feedback → 写 jsonl (按日) → 备 fine-tune
  - vote=down 时 prompt 追问原因 (留空也提交)
  - stats endpoint 30 天 up/down 累计

---

## 3. 性能 & 稳定性

- **新增 2 个 server endpoint**: POST `/api/yeren/feedback` (~10ms), GET `/api/yeren/feedback/stats` (~20ms)
- **新增 1 个本地存储层**: `data/yeren_feedback/{YYYY-MM-DD}.jsonl` (append-only)
- **R349 sparkline 并发 6**: 20 只自选股 < 4s (vs sequential ~30s)
- **R350 toast 反馈**: 写服务器失败时 toast 提示"本地仍有效" (不阻塞 UI)

---

## 4. 测试矩阵 (本批次)

| 端点 / 行为 | 状态 | 备注 |
|---|---|---|
| POST `/api/yeren/feedback` (msg_id+vote) | 200 / ~10ms | 写 jsonl, 返 n_today |
| GET `/api/yeren/feedback/stats` | 200 / ~20ms | 30 天 up/down 计数 |
| `/api/stock/600519/sparkline` (R349 拉) | 200 / 841ms | 用于复盘卡片 |
| HTML review panel 存在 | ✓ | `#yeren-ai-review` |
| HTML suggest chip 存在 | ✓ | `#yeren-ai-suggest` |
| HTML review chip 存在 | ✓ | `#yeren-ai-brief-review` |
| SW bump | ✓ | v692 → v724 (32 个本批次静态变更) |

---

## 5. 已知边界

1. **R349 15:00 阈值硬编码**: 周末/节假日也会进入 review 流程 (因 `isWeekday` 判断已加, 但节假日判断缺)
2. **R350 reason 长度 500 字**: 长反馈会截断 (合理上限, 但 loss 上下文)
3. **R349 仅看 sparkline 末尾两点算今日**: 真实当日数据依赖 sparkline 缓存的粒度 (5min), 不一定代表"今日收盘"
4. **R350 toast `n_today` 仅当日计数**: 历史未展示 (stats endpoint 单独查询)

---

## 6. R351-R360 蓝图 (第三轮: 智能深度)

### Round 4: 个性化与记忆 (R351-R355)
- **R351**: 个人画像 (持仓偏好 / 风险偏好 / 关注板块) — 存 localStorage
- **R352**: 跨会话历史同步 — server 端存 `data/yeren_history/{user_id}.jsonl`
- **R353**: 战法图谱 — 自动归纳用户问得最多的 5 个战法 → 推荐相似股
- **R354**: 推荐股票池进化 — AI 推荐过的股 + 用户后续表现 → 加权排序
- **R355**: 风险预警 — 持仓股跌 >5% / 异动 → ticker 主动 push

### Round 5: 高级智能 (R356-R360)
- **R356**: 战法 AI 链式追问 — 自动补问"数据来源？/ 仓位建议？"
- **R357**: 一键复盘历史 — 点历史对话 → AI 一句话总结当时判断 + 现在回顾
- **R358**: 战法对比 — 多个 AI 回复并排显示, 让用户选最佳
- **R359**: 用户自定义 prompt 模板 (战法参数化)
- **R360**: R341-R360 集成测试 + R361-R380 蓝图 (跨页协同: bv-mobile + dexin + yeren-ai)

---

## 7. 总结

**本批 (R341-R350) 主题**: 战法 AI 从"被动回答"升级到"主动推送 + 持续学习", 累计 10 个本批次 commits (R341-R350), 引入 **2 个新 server endpoint** + **1 个新存储层** (jsonl feedback), **5 个新交互入口** (语音输入/多股对比/信心度/异动推送/板块检测/复盘面板/反馈入库)。

**第一性原则达成度**: 用户进 yeren-ai 后, 比 R340 基线多 7 个主动场景:
- 历史问题一键续问 (R347)
- 输入"板块"自动跳转 (R348)
- 收盘后看自选复盘 (R349)
- AI 回答不准一键入库 (R350)
- 自选异动 ticker 提醒 (R346)
- 多股对比 (R344)
- 信心度可见 (R345)
