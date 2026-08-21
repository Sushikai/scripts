
## 2026-08-09 早晨 - 个股详情紧凑化 ✅

### 问题
- 个股页 `.quote-bento` 14 个 `.quote-cell` 卡，每卡 72px 高
- 换手率/量比/振幅 3 个独立卡片 = 216px 高 = 半个屏幕
- 信息密度极低（每个参数只显示 1 个数字 + 1 行 sub）

### 解决
- **前端 HTML**: 把 3 个独立卡合并为 1 个 `.quote-cell-compact`（内含 3 个 `.qcc-mini` cell）
- **CSS**: 新增 `.qcc-mini-value` 字号 16px、mobile 14px；`.qcc-mini-label` 9.5px
- **JS setVal**: 兼容 mini-cell（自动识别 class）
- **后端 `_derive_activity_signal`**: 综合 换手率/量比/振幅 给出 1 个 LABEL + score 0-100
  - 异常放量（turnover>20 && vol_ratio>3）
  - 高活跃（turnover>10 || vol_ratio>2）
  - 高波动（amp>7）
  - 低迷（turnover<1 && vol_ratio<0.7）
  - 活跃 / 常温
- **响应字段**: `activity_signal: {label, color, score}` 加到 stock_full/stock_overview 两处
- **前端读 signal**: 优先用 `data.activity_signal.label`，fallback 旧 if-elif
- **SW cache**: bump 到 `v354-compact-tri-20260809` 强制刷新
- **index.html 备份**: `index.html.bak.20260809`、`style.css.bak.20260809`、`app.js.bak.20260809`

### 验证
- 603286 日盈电子 → `常温 score=13.6`（换手 2.35 + 量比 None + 振幅 3.25）
- 002747 埃斯顿 → `高活跃 score=70.0`（换手 10.43 + 量比 261.17 + 振幅 4.34）
- 服务 HTTP 200，PID 重新拉起在 7799

### 节省空间
- 手机端 14 卡 → 12 独立 + 1 紧凑组 + 14 紧凑组子卡 = 13 卡 视觉
- 高度：3 个独立卡 216px → 1 紧凑组 ~90px = 节省 126px = 半个屏幕的 1/3

## 2026-08-09 06:44 - v2: 14 卡 → 5 卡分组聚合 ✅

### 用户反馈 (Arthur)
"我说的是所有这些卡片 你能联想吗" — 14 个独立卡片每个占 1/2 屏幕，每个只有 1 个数字 + 1 行 sub，信息密度太低

### 改造 (v2)
- 14 个独立 quote-cell → 5 个分组聚合卡 (4 分组 + 1 主力净流大卡)
- ① 行情组: 高/低/开/昨收 + 振幅 + 换手 + 量比 (7 指标)
- ② 趋势组: 5d / 20d / 涨停价 / 跌停价 (4 指标)
- ③ 估值组: 总市值 / 流通 / PE / PB (4 指标)
- ④ 资金组: 成交量 / 成交额 / 龙虎席位 (3 指标)
- CSS 新增 `.quote-cell-group` + `.qcg-grid` (2/3/4 列) + `.qcg-value` (15px, mobile 13px)
- HTML 字段拆解: q-hi/q-lo/q-open/q-prev (旧 q-hl), q-amt (独立), q-cmcap (独立)
- JS: setVal 兼容 `.qcg-value` / `.qcc-mini-value` / `.qc-value` 三种 class 自动识别
- 后端已发的 activity_signal 仍然在用 (活跃度 sub)

### 节省空间
- 移动端 14 卡 (~500px) → 5 分组卡 (~250px) = 节省 50%
- 桌面端 14 卡 6 列 → 5 卡 (1 大 4 小) 同样信息密度更高

### 验证
- 5 个分组卡 ID 全部存在 (qc-quote-group/trend-group/value-group/flow-group + qc-main-card large)
- API 字段全: 换手/振幅/最高/最低/今开/昨收/市值/流通/PE/PB 都有数据
- 603286 日盈电子 → activity_signal: 常温 score=13.6
- Playwright 截图视觉确认: 移动端/桌面端均紧凑

### 备份
- index.html.bak2.20260809 (v2 起点)
- index.html.bak.20260809 (v1 起点)

## 2026-08-09 10:00 - ngrok 链接崩了 ✅ 修复

### 症状 (Arthur 08:24 反馈)
- `https://study-tuition-nylon.ngrok-free.dev` 访问无响应
- ngrok agent 进程在跑, tunnels API 显示 alive
- 实际请求 10s 超时 (HTTP 000)

### 排查路径
1. ngrok agent 日志显示 `connection reset by peer` 到 3.113.109.144
2. ngrok edge server (Akari 出口 IP 160.248.69.190) 被 ban
3. cloudflared quick tunnel 测试 — `connection reset by peer` 到 104.16.231.132, 也被 ban
4. bore.pub TCP 隧道唯一可用, 但 bore 端口会变 (服务端随机分配)
5. **真正根因**: yaogu_survey.py 有 git merge 冲突未解决, server.py import 报错, **7799 服务挂了** (用户看到的 "ngrok 崩了" 实际是后端挂了)

### 修复
1. **server.py** 加 try/except 包 yaogu_screener import (防止整个服务被单文件 syntax error 拖死)
2. **重启 7799**: HTTP 200, 0.005s 首字节
3. **卸 ngrok plist**: 完全弃用 ngrok + cloudflared (Akari 出口 IP 被 ban)
4. **新 bore-fixed plist**: launchd 守护 `/tmp/bore local 7799 --to bore.pub`
5. **新 bore-url-sync plist**: 每 10s 检查 bore log, 写 URL_FILE 第 1 行
6. mobile-link-keepalive 会自动 TG 通知新 URL

### 教训
- **不要让未完成代码 (git merge 冲突) 阻塞整个服务启动** → 关键 import 必须 try/except
- **Akari 出口 IP (160.248.69.190) 周期性被 ban** (8/5 股票源, 8/9 ngrok/cloudflared API)
  → bore TCP 隧道是 fallback, 但端口会变
- **bore 服务端拒绝指定端口 (-p)**: 大部分端口被占, 自动分配才是稳定方案

### 当前状态
- 7799 服务 HTTP 200, 0.005s 响应
- bore-fixed 端口: 14384 (会变, 看 URL_FILE 第 1 行)
- bore-url-sync 每 10s 同步, mobile-link-keepalive 自动 TG
- ngrok plist 已删, cloudflared plist 已删
