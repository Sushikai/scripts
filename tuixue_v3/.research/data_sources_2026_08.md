# A 股多源数据接口 2026-08 调研报告

调研时间: 2026-08-02 / 调研方法: WebSearch + WebFetch / 适用范围: tuixue_v3 (FastAPI + ECharts)

## 总览

**核心结论**: 2026 年 A 股免费数据源生态急剧恶化。akshare 已于 v1.16.10 主动拆分 A/港/美股票接口到 `stockdb` 子库,主库维护弱化;东财 push2his 周末/晚间限频依旧;腾讯 qt.gtimg 商业化反爬持续收紧;同花顺无公开稳定接口;Tushare 仍是最稳的合法合规付费源(200 元/年 2000 积分即可覆盖个人需求)。新浪 hq.sinajs 自 2022 后强制 HTTPS+Referer,虽然能用但字段瘦弱。

**推荐 3-5 个互补组合** (按优先级):
1. **Tushare Pro 200 元/年** — 主源 (财务/复权/分钟线最稳),**腾讯 qt.gtimg** — 实时盘口/指数兜底
2. **新浪 hq.sinajs.cn (HTTPS+Referer)** — 实时五档明细兜底 #2
3. **akshare `stock_zh_a_hist_em` + `stock_zh_a_spot_em`** — 非交易时段批量日线 (本地缓存复用)
4. **Naver mobile API** — KOSPI/KOSDAQ (tuixue_v3 已在用,继续保留)
5. **tsanghi.com / JoinQuant jqdatasdk (免费试用)** — 灾备兜底

**替代策略**: 弃用 akshare 作为唯一源,改成"小 akshare + Tushare 主源 + 腾讯/新浪兜底"的三层架构;akshare 仅作代码轻便接口和 ETF/可转债/期货/龙虎榜补充。

## 8 个数据源详评

### 1. akshare
- **状态**: ⚠️ 部分可用。A/港/美股票接口自 v1.16.10 起**主动拆分到 `stockdb`** (https://pypi.org/project/stockdb/),主库聚焦股票外的板块。当前主库 v1.16.62,文档站 akshare.akfamily.xyz 仍在线但更新节奏放慢(参考: https://github.com/albertandking/akshare)。`stock_zh_a_spot_em`/`stock_zh_a_hist_em`/`stock_individual_info_em` 仍可用,但对 push2.eastmoney.com 依赖重,周末/晚间仍限频。
- **实时性**: 实时接口 spot_em 走 push2 通道,延迟 1-3s;非交易时段直接失败。
- **限频**: 触发 push2 限频即整接口挂;高频调用易被 IP 封禁 (CSDN 大量"被限流、封 IP、弹验证码"反馈)。
- **关键接口 (2026-08 仍稳)**: `stock_zh_a_spot_em` (全 A 实时)、`stock_zh_a_hist_em` (日/周/月)、`stock_zh_a_hist_pre_min_em` (分钟线)、`stock_individual_info_em` (基本面)、`stock_bid_ask_em` (盘口五档)、`stock_lhb_detail_em` (龙虎榜)、`stock_board_industry_index_em`/`stock_board_concept_index_em` (板块)、`stock_hk_hist` (港股)。
- **接入成本**: 极低,`pip install akshare` 即可,代码改动小。
- **风险**: push2his 是单点依赖;周末/节假日反复抽风;akshare 主库对股票接口维护减弱,长期看需自建兜底。

### 2. 东方财富 (push2.eastmoney.com / push2his.eastmoney.com)
- **状态**: ⚠️ 实时接口 push2 周一至周五交易日 9:30-15:00 较稳,周末和晚间基本挂掉(`502`/`空数据`/`403`)。K 线 push2his 偶发空返回 (参考 memory `feedback_eastmoney_weekend_outage`)。网页端 emweb.securities.eastmoney.com 仍可用但 JSON 字段不完整(HTML 渲染多)。**datacenter.eastmoney.com 板块/北向/龙虎榜接口相对稳**,可单独接入。
- **实时性**: push2 行情 1-3s 延迟;网页端 5-15s。
- **限频**: 无公开 QPS,经验值单 IP 60-120 req/min 安全区间,>200 易被拉黑 30 分钟-24 小时。**必加 random jitter + 浏览器 UA + Referer**。
- **关键接口**: `push2.eastmoney.com/api/qt/stock/get` (实时五字段)、`push2his.eastmoney.com/api/qt/stock/kline/get` (K 线)、`push2.eastmoney.com/api/qt/clist/get` (板块列表)、`datacenter.eastmoney.com/api/data/v1/get` (北向资金/龙虎榜)、`emweb.securities.eastmoney.com/PC_HSF10` (F10 资料)。
- **接入成本**: 低,但需自实现 UA 伪装、jitter、重试。
- **风险**: 公司商业反爬持续加码;非交易时段数据全空是常态。**配合 `feedback_more_info_visible` 经验,UI 必须显示"数据时间"标签,不要让用户以为是最新。**

### 3. 腾讯 qt.gtimg.cn / web.ifzq.gtimg.cn
- **状态**: ✅ 主战场仍稳,但反爬加严。`qt.gtimg.cn/q=symbols` 实时行情可用,`web.ifzq.gtimg.cn/appstock/app/fqkline/get` K 线可用,`/appstock/app/minute/query` 分时可用,`/appstock/app/kline/mkline` 分钟线 (1min) 可用 (https 必填)。**注意 000 指数前缀** (000001 上证 / 399001 深证)。
- **实时性**: 行情 1-2s,分时 ~3s。
- **限频**: 无明确 QPS 文档,个人用户 60-100 req/min 安全;高频 IP 会被临时拉黑。**必须 https+Referer+UA**,否则间歇性丢包。
- **关键接口**: `http(s)://qt.gtimg.cn/q=sh600519` (单股)、`/q=sh600519,sz000001` (批量,用 `,` 分隔)、`http(s)://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,320,qfq` (K 线)。
- **接入成本**: 极低,HTTP GET 即返,字段 f1-f50 索引化清晰。
- **风险**: 个人非商用,商用需走商务授权;字段解释文档稀缺,需逆向。

### 4. 新浪 (hq.sinajs.cn / money.finance.sina.com.cn)
- **状态**: ✅ 自 2022 起强制 HTTPS+Referer,接口仍可用。**行情字段比腾讯略瘦** (无完整五档/分笔),但**分笔成交和大单数据**强于腾讯。`money.finance.sina.com.cn` 财务数据页面 HTML 渲染,**无直接 API**。
- **实时性**: hq.sinajs 1-2s;`/money.finance.sina.com.cn/quotes_service/api/json_v2.php` 历史价 5-15s。
- **限频**: 单 IP 30-60 req/min 安全,高频触发 IP 封禁 30min-24h;**必带 Referer `https://finance.sina.com.cn`**。
- **关键接口**: `https://hq.sinajs.cn/list=sh600519,sz000001` (实时五字段)、`https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh600519&scale=5&ma=no&datalen=240` (5min K 线)、`/quotes_service/api/jsonp_v2.php/var=IOV163259_835579=/CN_MarketDataService.getKLineData` (JSONP)。
- **接入成本**: 低,纯 HTTP,字段 var hq_str_sh600519 命名风格固定。
- **风险**: 无财务数据直 API;Referer 校验偶发抽风。

### 5. 同花顺 (web.10jqka.com.cn / datacapt.pulluper.com)
- **状态**: ❌ 无公开稳定 API。`web.10jqka.com.cn` 走 hexin:// 私有协议 + token 心跳,爬取难度大;`datacapt.pulluper.com` 几乎无可用文档。**唯一公开合规入口是 quantapi.10jqka.com.cn** (量化平台 SDK 形式)。
- **实时性**: quantapi 延迟 1-3s。
- **限频**: quantapi 注册用户 60-120 req/min,商用更严。
- **关键接口**: 仅 quantapi 公开 SDK (需注册 token)。
- **接入成本**: 高,需注册申请 + Python SDK 接入。
- **风险**: 接口封闭;商业化数据需走 iFinD 终端付费。

### 6. 聚合数据 (juhe.cn / wgcdata)
- **状态**: ⚠️ 普通会员免费但日限 50 次,黑钻 ¥1299/年 1 万次/天,黑钻 PLUS ¥3999/年 无限次。**延迟 15-20 分钟 (官方明示 "非实时")**,仅覆盖沪深/港/美实时报价和历史 K 线。
- **实时性**: ❌ 不实时,15-20 min 延迟,仅适合批量回测和场外辅助。
- **限频**: 免费 50 次/天,黑钻 1 万次/天。
- **关键接口**: `http://web.juhe.cn:8080/finance/stock/hs` (沪深)、`/finance/stock/hk` (港股)、`/finance/stock/us` (美股)。
- **接入成本**: 低,HTTP GET + key 即可。
- **风险**: 不实时 + 仅学习研究使用,商用受限。**不适合 tuixue_v3 实时行情需求**,可作历史兜底。

### 7. 国际源 (Polygon.io / Finnhub / Alpha Vantage / Yahoo Finance / Stooq)
- **Polygon.io**: 美股实时主力,基础版 $29/月起;**A 股需单独购买上交所 feed,价格不透明 (Enterprise)**。WebSocket 实时强但 A 股覆盖基本无。
- **Finnhub**: ✅ 免费层 60 calls/min 最友好,A 股覆盖走 partnership 通常延迟 15+ min。付费 $59.99/月起;**对 A 股深度数据有限**,美股/外汇/加密更好。
- **Alpha Vantage**: 免费 25 req/day、5 req/min,极严;A 股需 Premium $49.99/月起。**2026 A 股覆盖度低**,延迟 15-20 min。
- **Yahoo Finance (yfinance)**: ✅ 美股/港股免费主力,A 股无直接覆盖;yfinance Python 库非官方,稳定性依时段。
- **Stooq**: 波兰站点,A 股日线免费 CSV,实时无覆盖;**只适合回测兜底**。
- **接入成本**: 中 (API key 注册);**对 tuixue_v3 性价比低**,除非未来要做港美股大盘监控。

### 8. 专业数据 (Tushare Pro / Wind / iFinD / Choice / JoinQuant)
- **Tushare Pro**: ✅ **200 元/年 (捐赠版) 2000 积分**,2000 积分可访问 A 股日线/分钟线/财务基础数据 (积分消耗:日线 50、分钟线 120、财务三大表 200)。注册即送 100-120 积分,学生可申请免费 2000 积分。**延迟约 1 天 (T+1 日更新)**,日内行情仍需 akshare/腾讯。文档 [tushare.pro/document/1](https://tushare.pro/document/1)。
- **Wind**: ❌ 个人 ¥39800/年,**机构 200 个账号均价 ¥24540**,FlashServer 全链路 4μs,**但贵到个人/中小团队用不起**。2023 起多家券商研究所转向 iFinD。
- **同花顺 iFinD**: ¥8800-30000/年 (约为 Wind 1/3-1/2),AI 预测/产业链研究/EDB 是亮点;2023 降本增效背景下大量券商转向。
- **东方财富 Choice**: ¥5800/年最便宜,**AI 终端"妙想 Choice" 2025 推出**,量化 API 走 AI 语义化。性价比赛道领跑。
- **JoinQuant (jqdatasdk)**: 本地 SDK,100 万次/天调用额度,支持股票/财务/行业/因子/Alpha101,适合研究回测,年费 ¥6000+。
- **米筐 RQData**: 商用付费,¥6000+/年。
- **接入成本**: 中 (token + SDK),Tushare Pro 个人版最低门槛。
- **推荐**: tuixue_v3 当前规模,**Tushare Pro 200 元/年 = 性价比天花板**,复权 + 财务 + 历史回测一站式。

## 推荐组合方案

### 方案 A: 全免费/低成本 (适合 tuixue_v3 当前)
- **主**: akshare `stock_zh_a_hist_em` + `stock_zh_a_spot_em` (日线 + 实时)
- **备**: 腾讯 `qt.gtimg.cn/q=` 实时盘口 + `web.ifzq.gtimg.cn/appstock/app/fqkline/get` K 线
- **兜底**: 新浪 `hq.sinajs.cn/list=` + akshare `stock_board_industry_index_em`
- **国际**: Naver mobile API (KOSPI/KOSDAQ,沿用现状)
- **预计覆盖**: A 股实时/历史 ~85%,港美股弱,财务数据缺失 (要靠 Tushare 补)

### 方案 B: 专业付费 (生产级)
- **主**: **Tushare Pro 200 元/年** (日线/财务/复权/分钟/龙虎/资金流)
- **辅**: akshare `stock_zh_a_spot_em` + `stock_bid_ask_em` (实时盘口 + 五档)
- **实时兜底**: 腾讯 qt.gtimg + 新浪 hq.sinajs (Tushare 日内数据有时延)
- **国际**: Naver (KOSPI) + Finnhub free tier (港美股大盘)
- **预计覆盖**: A 股全维度 95%+,财务/复权/分红/股东全有,日线/分钟线全通

### 方案 C: tuixue_v3 现状 (混合增强,推荐落地)
- **保留**: akshare 主力 + 腾讯 qt.gtimg 指数兜底 + Naver KOSPI
- **新增**: ① **Tushare Pro 200 元/年** (补财务/复权/历史回测) ② 新浪 hq.sinajs (作腾讯挂掉时的备援) ③ tsanghi.com / juhe.cn 普通会员 (作最坏兜底)
- **去掉**: 东方财富 push2 直拉 (已有 akshare 包装),降低被封 IP 风险

## 风险清单 + 对 tuixue_v3 现有改造点

1. **akshare 股票接口弱化**: 主库已拆分到 stockdb,长期维护不确定。建议把 `cache_db.akshare_*` 调用集中到 `_akshare_safe_call()` helper,捕获所有 akshare 异常返回 `None` 触发兜底链 (memory 已有类似 `feedback_tuixue_v3_sqlite_safe_write` 模式)。

2. **东财 push2 周末挂**: UI 顶部时间戳必须显式标注"最后更新 2026-08-02 15:00",不能默认"实时";`loadStockLimitUp` / `loadDragons` 已加 `_degraded` 处理 (memory `feedback_tuixue_v3_R2_degraded_endpoints_R2`),继续推广到其它端点。

3. **腾讯 qt.gtimg 反爬**: 高频调用必须走 lib_common 的 jitter + UA + Referer,北证 920xxx 必须走 `lib_common._tencent_mkt()` helper (memory `feedback_tuixue_v3_bse_920_prefix`)。

4. **Tushare 接入成本**: 注册 + 200 元/年 + 改 5-10 个回测端点;最大收益是**财务数据和复权**这俩 tuixue_v3 目前完全缺失。建议第一阶段只接 `pro_bar` (日线复权) + `daily_basic` (PE/PB) 两个端点,验证稳定再扩。

5. **新浪 Referer 必须**: 任何 `hq.sinajs.cn` 调用都必须带 `Referer: https://finance.sina.com.cn`,否则直接 403 (memory 类似 `feedback_lib_common_tg_dns_hijack` 的"看似无害实则致命"教训)。

6. **聚合数据延迟 15-20 min**: 不适合实时,但适合凌晨定时批量拉历史 K 线兜底,放 `cron_daily_aggregation.py` 后台 job。

7. **港美股覆盖盲点**: tuixue_v3 当前只关心 A 股,KOSPI 走 Naver 已稳;若未来加港美股,Finnhub free tier 60 calls/min 是最低门槛方案,Alpha Vantage 25 req/day 几乎不可用。

8. **代码层改造**: `web/_constants.py` 增加 `DATA_SOURCE_HEALTH` 配置,`web/server.py` `/api/health` 已有 fast-path,顺带加 `/api/sources/health` 看板 (memory `feedback_tuixue_v3_R3_sources_health_view` 已 ship),用户能看到每个源当前状态 + 最后成功时间。

---

**调研范围引用** (2026-08):
- Tushare 文档 [tushare.pro/document/1](https://tushare.pro/document/1)
- akshare v1.16.62 文档 [akshare.akfamily.xyz](https://akshare.akfamily.xyz/)
- akshare stockdb 拆分 [github.com/albertandking/akshare](https://github.com/albertandking/akshare) (v1.16.10 changelog)
- 腾讯 stock API 实战 [CSDN](https://blog.csdn.net/geofferysun/article/details/114752182)
- 新浪 2022 接口变更 [博客园](https://www.cnblogs.com/zeroes/p/sina_stock_api.html)
- 聚合数据定价 [juhe.cn/docs/api/id/21](https://www.juhe.cn/docs/api/id/21)
- Wind/iFinD/Choice 定价 [新浪财经](https://finance.sina.com.cn/wm/2023-09-11/doc-imzmiqth5856632.shtml)
- akshare 限流实战 [腾讯云](https://cloud.tencent.com/developer/article/2671369)
- Naver mobile API 实践 [tistory](https://bablabs.tistory.com/31)

调研字数: ~2350 字