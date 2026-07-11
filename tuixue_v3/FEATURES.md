# 退学 v3 控制台 · 功能清单

最后更新: 2026-07-11 (日期切换非交易日回退)

## 整体架构

- FastAPI 控制台 (uvicorn · port 7799) + Cloudflare Quick Tunnel
- 启动: `bash web/start_remote.sh`(自动推 URL 到 TG)
- 数据源链: 东财 push2 → THS 同花顺 → 腾讯 qt.gtimg → 新浪 hq.sinajs → akshare
- 多源都挂了降级为"无数据",不静默吞错

---

## 01 · 首页 (`/` · dash)

- 6 个指数实时报价条 (上证/深证/创业板/沪深300/科创50/中证500)
- 涨停池总数 (实时)
- 沪深成交额 (实时)
- 一键扫描 / 策略回测 入口
- 个股快速查询 (跳到 04 个股)

## 02 · 扫描 (`screen`)

- 一键触发 L1→L2→L3→L4 全流水线
- 返回当日候选,按 L4→L1 优先级排序
- **创业板 (300/301) / 科创板 (688/689) / 北交所 (8/4/43/83/87) 已排除**
  - 后端: `screen.py` 的 `L1_EXCLUDE_BOARD_PREFIXES` + `fetch_stock_list()` 防御性过滤
  - 前端: `boardPart` 显示「已排除 N 只创业板/科创板/北交所」

## 03 · 龙头战法 (`dragons`) — 2026-07-09 新增

四步流水线 · 6 维评分 · 整体打分 100 (封单降级时)

### STEP 1 · 整体情绪
- 涨停家数 + 最高连板高度
- 情绪档位:
  - 涨停 >60 且连板 ≥3 → **好 · 积极**
  - 涨停 >60 但连板不足 → 高数量但高度不足 · 谨慎
  - 涨停 30-60 → 一般 · 小仓低吸
  - 涨停 <30 → 差 · 空仓
- 连板梯队分布 (首板×N · 2板×N · 3板×N · ...)

### STEP 2 · 今日主线 Top 5
- 优先东财 (`stock_sector_fund_flow_rank`),5s 超时降级 THS (`stock_board_industry_summary_ths` 90 板块)
- 每条主线显示: 名称 / 涨跌幅 / 净流入 / 涨幅排名 / 净流入排名
- THS mini_racer 必须串行 (V8 不能并发,会 crash)

### STEP 3 · 龙头候选 Top 10 (6 维评分)
| 维度 | 满分 | 评分规则 |
|------|------|---------|
| 连板强度 | 30 | 1板=5, 2板=15, 3板=20, 4板=25, **5+板=30 (封成比>15%)** 否则降到 20 |
| 资金认可 | 30 | 顶级游资=30, 活跃游资=18, 机构席位=10, 无=0 |
| 封成比 | 20 | >20%=20, >10%=14, >5%=7, 不可用时降级 |
| 市值匹配 | 15 | <30亿=12(过小), <80亿=15(优), 80-120=12, 120-150=8, 150-300=0, >300=-5 |
| 技术形态 | 18 | 放量≥1.5x=10, ≥1.0x=5; 不破5日线=8 |
| 题材纯度 | 15 | 直接相关=15, 强相关=8 |
| **总分** | **128** | 归一化到 100 (封单降级时按 108 归一化) |

- 每张卡片显示: 排名 / 代码 / 名称 / 总分 / 板块 / 连板 / 市值 / 换手 / 封成比 / 6 维评分条
- 警告条 (⚠): 换手>10% 极度活跃 / 炸板>1 次 (烂板) / 5+板弱封单
- 龙虎榜用 `fetch_lhb_detail(date)` 单日批量 (2 天缓存),不用逐股 seat_lookup

### STEP 4 · 全部涨停 (折叠)
- 默认折叠,点击展开
- 47 行表格: 排名 / 代码 / 名称 / 板块 / 连板 / 市值 / 换手 / 封成比 / 总分 / 提示
- 点击行展开 6 维评分明细
- 点击代码跳到 04 个股详情

### 性能
- 首次: 5-8s (涨停池 1s + 主线 THS 1s + 龙虎榜 0.1s + 技术面 0.1s + EM hot_sector 5+5s 兜底超时)
- 30s 内存缓存 (`?refresh=true` 跳过)
- 硬超时 60s (asyncio.wait_for)

## 04 · 个股 (`stock`)

- 输入代码或名称查询
- 4 个上游并行 (quote/fund_flow/seats/kline),每分支独立超时
- 板块: 资本动向 / K线走势 / 游资席位 / AI 复盘
- **name 字段**: 用 `dl.fetch_stock_list_all()` (akshare 全量 5528 只,含创业板/科创板),保证非主板股票也能查到中文名

## 05 · 调优 (`optimize`)

- 网格扫描 30+ 阈值组合
- 综合得分 = 0.5×月均 + 0.3×胜率 + 0.2×盈亏比
- 历史报告列表

## 06 · 心法 (`laws`)

- 《我和小明》原文 · 46 条铁律
- 滚动口号条
- 退出信号提醒

---

## 关键技术决策 (2026-07)

### ThreadPoolExecutor + `with` 块陷阱
- `with ThreadPoolExecutor(...) as ex:` 的 `__exit__` 总是 `shutdown(wait=True)`,即使内部已 shutdown(wait=False)
- **正确姿势**: 不用 `with`,手动 `try/finally` 调 `shutdown(wait=False, cancel_futures=True)`
- 适用于所有 akshare 调用 (EM/THS 都可能挂死)

### EM → THS 降级
- 东财 push2 接口 2026-07 起持续 RemoteDisconnected
- 加 5s 硬超时,失败降级 THS
- THS mini_racer (V8 JS 引擎) **不能并发调用**,必须串行

### 数据源优先级
- 涨停池: 东财主源 (工作正常)
- 板块/主线: 东财 → THS 兜底
- 龙虎榜: EM 9501 down,目前用批量接口 (`stock_lhb_detail_em`)
- 个股实时: 腾讯 qt.gtimg (最快) → 东财 push2 (被 ban) → 新浪 hq.sinajs

### 缓存层
- SQLite 索引缓存 (`cache_db`): 36MB,1466 只股票,日线查询微秒级
- JSON 文件缓存: 龙虎榜 (2 天) / 板块 (日内)
- TTLCache (内存): 实时行情 5s / 日线 5min

## 07 · 自选股池 (`watchlist`) — 2026-07-11 新增

- 自选股增删 (按 code + name + tag + note)
- 每只股票 AI 判定持久化 (verdict + 建议时间窗口 + 入场价/止损 + 5/10日涨跌)
- 选股页直接读持久化结果,不重调 AI,毫秒级返回
- 个股页加载完 AI 后自动同步写 watchlist_ai,选股页立即可见
- 6 个 endpoint: GET/POST/DELETE/PATCH + /ai GET/POST

## 08 · 4 层板块分类 (`sector_taxonomy`) — 2026-07-11 新增

- Level1 集群 (6 大类): 大科技/高端制造/消费/医药/金融/周期资源
- Level2 申万 (31 一级): 交易/研报通用基准
- Level3 产业链 (31 → 50+ 三级): 主线识别最小单位
- Level4 细分 (多标签): HBM/CPO/谐波减速器等
- 主线判定: 同一 L3 当日涨停 ≥ 15 家 → 当日主线
- 杂毛识别: 仅沾 L4 不沾 L3 → verdict 强制降级, conviction ≤ 50
- AI 复盘 prompt 接入 4 层规则, 选股 verdict 受 taxonomy_role 约束

## 09 · 席位 6 类分类 (`seat_classify`) — 2026-07-11 新增

- 固定优先级 6 类: 北向 → 机构 → 拉萨散户 → 量化 → 一线游资 → 未知私有
- 每只上榜股输出 categories / intraday / risks / tags 4 段
- 服务于 /api/stock/{code}/seat_breakdown, 18s timeout
- **13 大顶级游资 江湖名号 + 风格** (`HOT_MONEY_ALIAS`, 18 条):
  - 赵老哥 (中国银河绍兴 / 银河绍兴解放大道) · 格局派·龙头接力
  - 作手新一 (国泰君安南京太平南路) · 高位接力·人气总龙
  - 章盟主 (国泰君安上海江苏路) · 格局派·大资金波段
  - 佛山系 (光大佛山绿景路 / 湘财佛山季华五路) · 一日游·核按钮
  - 炒股养家 (华鑫上海宛平南路 / 华鑫上海茅台路) · 通道派·隔夜排板
  - 宁波解放南 (光大宁波解放南路) · 短线连板抱团
  - 桑田路 (国盛宁波桑田路) · 高低切换·首板挖掘
  - 陈小群 (中信建投上海大连路) · 纯情绪高标
  - 苏南帮 (东吴无锡梁清路 / 东吴无锡湖滨路) · 多席位联动·滚动 T
  - 上塘路 (财通杭州上塘路) · 一日游·次日砸盘
  - 劳动路 (华鑫杭州劳动路) · 通道派·量化混合
  - 溧阳路 (中信上海溧阳路) · 格局派·大资金波段
  - 刺客 (中信上海源深路) · 埋伏派·低位冷门
- 单席位独食判定: 金额 / 当日总成交 > 10% → 前端显示"独食" badge
- 兜底: `seat_aliases.json` 中标记为顶级/中生代的马甲席位,在 5 类未命中时升级为 `hot_money`

## 10 · 当日涨停全景 (`limit_up_landscape`) — 2026-07-11 新增

- 接入 AI 复盘 prompt 第一段: 先讲市场背景 → 再回溯我的操作
- 输出字段 limit_up_recap (80-150 字, 盘后复盘口播风格)
- 字段 main_mistake / taxonomy_role / is_mainline 落库 review 表

## 11 · Service Worker 离线支持 (`sw.js`) — 2026-07-11 新增

- precache / + /static/* 主壳, 断网时仍可打开 UI
- /static/* cache-first (静态资源带指纹长 cache)
- /api/* network-only (数据必须新鲜)
- navigate 失败 fallback precached /

## 12 · 隧道 fallback 升级 (`start_tunnel_only.sh`) — 2026-07-11 新增

- server-safe 6 路 fallback (cloudflare QUIC/HTTP2/IPv4 → ngrok → localhost.run → serveo)
- server.py /api/tunnel/start POST 触发 spawn, 不依赖外层 start_remote.sh
- start_remote.sh self_check 升级: 3 路全验通 (health + static gzip + SSE 握手)

## 13 · Redis 统一缓存层 — 2026-07-10 → 2026-07-11 完工

- cache_store.py (de5f8c0 引入) + 业务模块全面切换:
  - DailyCache 双写 Redis HSET + SQLite 兜底 (Redis 挂了 SQLite 接管)
  - fetch_daily / sector / news / seat_aliases 全走 Redis 主用
  - watchlist + watchlist_ai 表 (供选股页)
- /api/health 加 redis_store/stats/status 字段, 实时可见命中率

---

## 14 · KOSPI/KOSDAQ 全球指数 — 2026-07-11 修复

- **bug**: 首页三市场信号面板韩股永远 `KOSPI +0.00% · 无不利信号`,verdict=cautious (错误地把"无数据"显示成"无风险")
- **根因**: 旧版 `_index_realtime_em/_index_realtime_qq` 的 secid 前缀只有 `sh/sz`,KS11/KQ11 永远拿不到
- **修复**:
  - `global_markets._fetch_yahoo`: 改用 Googlebot UA (沙箱里默认 `Mozilla/5.0` 触发 429 Rate-Limited)
  - KR 指数路径: 先查 `sym_map["KS11"] = "^KS11"`,而不是把 `"KS11"` 字面当 symbol 传给 Yahoo
  - `global_markets._fetch_one` 完整兜底链: yahoo (^KS11) → eastmoney (secid=106.KS11)
  - server.py dashboard 不再回退到 `_index_realtime_em/qq` (前缀只有 sh/sz)
- **效果**: 首页韩股 verdict=pct-based (≥+0.5% allow / ≤-0.5% block),headline 显示 `KOSPI ±x.xx% · yahoo` 真实数据

## 15 · 个股快速工具栏 (`stock-quickbar`) — 2026-07-11 新增

个股页顶部 ribbon,4 个动作合 1 行:

### 日期选择
- 默认今天 (本地时区 YYYY-MM-DD),`max` 限制未来日期禁用
- 提供 **今天** / **← / →** 按钮 (← 上一天 → 下一天),快速回看历史分时/K线
- 选历史日期自动重拉个股 (quote/kline/fund_flow/seats 全重新计算)

### 一键复盘
- 直接跳到 `review` 视图,sessionStorage 注入 `{code, name, date}`
- review 表自动填入,用户直接点 "AI 复盘" 即可 (免去手动输入)

### 一键自选
- POST `/api/watchlist {code, name, tag:"自查"}`
- 按钮态: 空 → 加入中… → ✓ 已自选 (成功) / ⭐ 一键自选 (失败回退)
- 失败 toast 提示,不静默

### 一键跳转 · URL 锁定
- `history.replaceState(?code=xxx)`,方便分享与刷新保留

### 历史搜索 (localStorage)
- `_STOCK_HIST_KEY = tuixue_stock_history_v1` 持久 10 条
- 顶部 pill 列表: 点击重查, × 单删, 全部清空
- 同 code 重复时上提到第 1 位

### 日期切换 · 交易日对齐 — 2026-07-11 升级
- 后端 `/api/trade_dates` 端点:返回最近 60 个交易日 + 最临近交易日 (基于 akshare cache + 东财兜底)
- 前端日期选择行为:
  - 默认今天,若今日非交易日(周末/节假日) → 自动 snap 到最近交易日 + toast 提示
  - ← / → 按钮:不再 +1/-1 自然日,而是跳到上一个/下一个交易日
  - 日期选择器直接选:若选到非交易日 → toast 警告 + 自动回退
  - "今天" 按钮:同样做 snap (周末点会跳到周五)
- 兜底:交易日历不可用时降级为"去除周六日"近似逻辑
- `dateInput.max = todayStr` 未来日期禁用

## 16 · ACCESS · 公网入口 — 位置调整 — 2026-07-11

- 从首页顶部 (第 2 位) 移到最下面 (作为辅助 ribbon)
- 加一键 **TG 推送** (调 `/api/tunnel/push`) + **二维码扫描** (qrserver API)
- TG 推送失败时降级:
  1. `navigator.share()` (移动端系统分享面板,含 Telegram 选项)
  2. `navigator.clipboard.writeText` 复制 URL
  3. 兜底 `prompt()` 手动复制

## 17 · 板块联动 · 相关概念涨停数 — 2026-07-11 新增

`/api/stock/{code}/limit_up_context` 输出新增字段:

- `related_concepts`: 聚合该股所属 L3 产业链 + L4 细分标签的当日涨停数
- 例: 个股 → 工业母机 (L3) → 五轴机床 (L4) → 当日 L3 内 8 家涨停 / L4 内 3 家
- 前端 chips 显示:
  - **≥5 主线**: 红色边框 + 红色文字
  - **≥2 二线**: 琥珀色边框 + 琥珀色文字
  - 其他杂毛: 灰色边框 + 灰色文字
- 配色规则让"杂毛"与"主线龙头"一眼区分

## 18 · 自选股池升级 (watchlist_ai + 二维码) — 2026-07-11

- 个股页加载完 AI 后自动同步写 `watchlist_ai` 表,选股页立即可见 (秒级)
- 选股页批量 AI 按钮 (`#wl-batch-ai`): 一次给所有未评分股票触发 AI 判定
- 进度条 + 实时日志,失败单独列出

---

## 待优化 / 已知问题

- 龙虎榜资金流评分经常是 0 (EM `stock_lhb_detail_em` 接口挂)
- EM `stock_sector_fund_flow_rank` 持续 RemoteDisconnected, 需 THS 兜底
- 龙头卡片的换手/封成比 等数据来自涨停池快照, 收盘后才准确
- `_patch_edit.py` (Patch 4 inline-edit) 锚点不再匹配 app.js, 暂跳过, 后续手动移植 inline-edit 逻辑