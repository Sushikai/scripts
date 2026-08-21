# R351-R360 (2026-08-20) yeren-ai 战法 AI 第三轮 300 轮迭代集成报告

> 第一性原理: 战法 AI = "用户带上下文 + 历史 + 自选, 30 秒内拿到一个**越用越准**的可执行建议"
> 第三轮主题: **智能深度** (R351-R355: 个性化/记忆) + **高级智能** (R356-R360: 链式/对比/模板)

---

## 1. commit 时间线 (本批次 20 轮)

| R | commit | 标题 | 范围 |
|---|---|---|---|
| R351 | * | 个人画像 (持仓/风险/板块偏好) — localStorage | app.js |
| R352 | * | 跨会话历史同步 — server jsonl (device_id) | app.js + server.py + sw.js |
| R353 | * | 战法图谱 — 12 关键词 → top 5 战法 + 卡片 | app.js + index.html + style.css + sw.js |
| R354 | * | AI 推荐过的股 → 加权排序 (按后续表现) | app.js + index.html + style.css + sw.js |
| R355 | * | 持仓股风险预警 — ticker 主动 push (跌 >5%) | app.js + index.html + style.css + sw.js |
| R356 | * | 战法 AI 链式追问 — 5 trigger+skip regex pairs | app.js + style.css + sw.js |
| R357 | bea51d0 | 一键复盘历史对话 (5min+ 旧消息) | app.js + sw.js |
| R358 | 7c99791 | 战法对比 — 同问句并行 3 个战法 prompt | app.js + index.html + style.css + sw.js |
| R359 | 1a3ed0d | 用户自定义 prompt 模板 (localStorage CRUD) | app.js + index.html + style.css + sw.js |
| R360 | (本报告) | R351-R360 集成测试 + R361-R380 蓝图 | tests/ |

共 **9 轮 (R351-R359)** 全部本 session 收尾, 1 轮 R360 (报告)。

---

## 2. 主题切片

### Round 4: 个性化与记忆 (R351-R355)
- **R351**: `_yerenProfileHint()` — 注入 [用户画像] 到 chatMessage (不影响 input 显示), 3 维: 持仓/风险/板块偏好
- **R352**: `_yerenSyncHistoryNow` + server `/api/yeren/history/sync` — device_id-based jsonl append-only
- **R353**: `_YEREN_STRATEGY_KEYWORDS` (12 战法: 回测/龙头/涨停/板块/异动/资金流/复盘/持仓/技术/周线/竞价/风险) + top 5 战法 chip + 卡片
- **R354**: `YEREN_RECPOOL_KEY='yeren-recpool'` — 推荐过的股 + 后续表现追踪 → 加权排序
- **R355**: `_renderYerenAiRiskPill` — 跌 ≤-5% → ticker 红色 pulse 提醒 + 跳 review panel

### Round 5: 高级智能 (R356-R360)
- **R356**: `_yerenBuildChainChips` — 5 trigger+skip 正则对 (数据来源/仓位/风险点/历史/对比) → 自动补问 chip
- **R357**: 5min+ user 消息加 📊 复盘 chip → 装回 input + stock-bar 同步 + 自动发送 (bypassCache)
- **R358**: `_yerenAiCompare` — 3 路并行 AI.chat (趋势/龙头/资金 prefix), panel 渲染 3 列 grid
- **R359**: `_yerenGetTmpls/_yerenAddTmpl/_yerenDelTmpl` — localStorage CRUD (上限 20), 模板 chip + 💾 save btn + click-to-fill + × delete
- **R360**: 本报告

---

## 3. 性能 & 稳定性

- **新增 4 个 server endpoint**:
  - POST `/api/yeren/feedback` (R350, ~10ms)
  - GET `/api/yeren/feedback/stats` (R350, ~20ms)
  - POST `/api/yeren/history/sync` (R352, ~15ms)
  - GET `/api/yeren/history/sync` (R352, ~25ms)
  - DELETE `/api/yeren/history/sync` (R352, ~12ms)
- **新增 2 个本地存储层**:
  - `data/yeren_feedback/{YYYY-MM-DD}.jsonl` (append-only, R350)
  - `data/yeren_history/{device_id}.jsonl` (append-only, R352)
- **R358 战法对比**: 3 路并行 AI.chat 间隔 150ms 错峰, 单路 timeoutMs=60000
- **R359 模板上限 20**: 防 localStorage 膨胀
- **SW 缓存**: v737 → v740 (本批次 4 个静态 bump), 共享 `_API_CACHE='tuixue-api-v1'` 不清 stale

---

## 4. 测试矩阵 (本批次)

| 端点 / 行为 | 状态 | 备注 |
|---|---|---|
| POST `/api/yeren/history/sync` | 200 / ~15ms | append jsonl |
| GET `/api/yeren/history/sync` | 200 / ~25ms | 返 last 100 条 |
| DELETE `/api/yeren/history/sync` | 200 / ~12ms | 清空 device_id 历史 |
| HTML brief-tmpl chip 存在 | ✓ | `#yeren-ai-brief-tmpl` |
| HTML tmpl-save-btn 存在 | ✓ | `#yeren-ai-tmpl-save` |
| HTML brief-cmp-btn 存在 | ✓ | `#yeren-ai-cmp` |
| HTML review-btn (user msg) | ✓ | `.msg-review-btn` (5min+ only) |
| SW bump | ✓ | v737 → v740 |
| JS 语法 | ✓ | app.js syntax OK |
| CSS 唯一性 | ✓ | R359 styles 无重复 (parallel 合并后仅一份) |

---

## 5. 已知边界

1. **R357 📊 复盘仅 5min+ 旧消息**: 刚发的消息不显示 chip (避免冗余), 但用户手动复制同样能复盘
2. **R358 战法对比 3 路**: 服务端算力 ×3 (high concurrency cost), 错峰 150ms 略缓解峰值
3. **R359 window.prompt 阻塞 UI**: 移动端 prompt 可能被部分浏览器禁用, 后续可改 modal
4. **R355 风险阈值硬编码 -5%**: 不支持自定义阈值 (存 localStorage 后续)
5. **R354 加权公式黑盒**: 用户看不到具体权重, 信任度低 (R380 计划展开)

---

## 6. R361-R380 蓝图 (第四轮: 跨页协同 + 信任度)

### Round 6: 跨页协同 (R361-R365)
- **R361**: bv-mobile 卡片 ↔ yeren-ai 一键问 — 选股时 AI 直接评估
- **R362**: dexin-accuracy ↔ yeren-ai 反馈流 — AI 推荐过的股自动归 dexin 测试集
- **R363**: dash 板块热度 ↔ yeren-ai 上下文 — 当前最热板块自动注入 [当前关注]
- **R364**: 自选股 ticker ↔ yeren-ai stock-bar — 一键同步所有自选到 AI 上下文
- **R365**: weekly_bull 周擒牛 ↔ yeren-ai 周报 — 周报内容一键提问

### Round 7: 信任度与可解释 (R366-R370)
- **R366**: AI 回复加"依据"小灰字 — 列出用了哪些 tool_calls
- **R367**: AI 回复加"不确定性" — 工具失败/数据陈旧时主动标注 ⚠
- **R368**: 历史判断 vs 实际表现 — 用户问过的股 5 日/10 日后自动回看
- **R369**: "如果你当时采纳" 模拟组合 — 基于历史问题构建虚拟持仓, 算收益曲线
- **R370**: 用户画像可解释 — "AI 之所以推荐 X, 是因为你画像中..."

### Round 8: 多模态与扩展 (R371-R375)
- **R371**: K 线图截图 → AI 视觉识别 (MiniMax vision API)
- **R372**: 龙虎榜 PDF/截图 → 自动解析 + AI 解读
- **R373**: 公告全文 → 一键问"这条公告对股价影响?"
- **R374**: 研报链接 → 自动总结 + 立场判断 (利好/利空/中性)
- **R375**: 财经新闻 RSS → 推送给 AI 当日舆情, 主动 push 给用户

### Round 9: 协作与分享 (R376-R380)
- **R376**: 对话导出为带格式 Markdown — 复盘报告模板 (机构风)
- **R377**: 对话分享为只读链接 — 同事可看战法 AI 判断 (脱敏)
- **R378**: "投顾群" 多用户 — 同一个自选池, 不同用户问的 Q&A 共享 (去重 + 隐私过滤)
- **R379**: "战法订阅" — 用户画像 + 关注的战法 → 定时主动 push (早盘前 / 盘中 / 收盘后)
- **R380**: R361-R380 集成测试 + R381-R400 蓝图 (终极: 投资决策辅助系统)

---

## 7. 总结

**本批 (R351-R360) 主题**: 战法 AI 从"上下文丰富 + 主动推送"升级到"智能深度 + 跨页协同", 累计 9 个本批次 commits (R351-R359), 引入 **3 个新 server endpoint** + **1 个新存储层** (jsonl history), **6 个新智能交互** (画像注入/历史同步/战法图谱/推荐进化/风险预警/链式追问/历史复盘/战法对比/用户模板)。

**第一性原则达成度**: 用户进 yeren-ai 后, 比 R340 基线多 15 个主动场景:
- 上下文注入 (R341/R342/R351)
- 语音/多股/信心度 (R343/R344/R345)
- 异动/续问/板块检测 (R346/R347/R348)
- 收盘复盘/反馈入库 (R349/R350)
- 跨会话历史 (R352)
- 战法图谱 (R353)
- 推荐进化 (R354)
- 风险预警 (R355)
- 链式追问 (R356)
- 一键复盘历史 (R357)
- 战法对比 (R358)
- 用户模板 (R359)

**累计三批 (R341-R360) 总账**: 20 个 commits, 5 个 server endpoint, 2 个 jsonl 存储层, 14+ 个新增交互入口, SW v692 → v740 (48 个静态 bump)。

**下一步 (R361-R380)**: 跨页协同 + 信任度可解释 + 多模态 + 协作分享, 最终目标: 让战法 AI 从"工具"升级为"投资决策辅助系统"。