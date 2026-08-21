# R320 视觉验证报告

**日期**: 2026-08-18
**目标**: 用户报"推荐三只得鑫票 我发这个就没回啊 前端挂了" — 视觉验证 R317+R318+R319 是否真的修复
**结论**: **2 个真 bug 被视觉验证发现并修复** (R320 daee403),系统稳定性问题独立于 R320

---

## 视觉验证发现 (R320 修复)

### Bug 1: 承诺文本 "我先用战法工具扫一下" 没触发 R317 hint

**视觉证据** (`tests/r96_iter/iter_0001_full.png`):
> AI bubble: "得鑫"我猜您是想说"得心(顺手/优质)的票 — 我先用战法工具扫一下全市场,再按规则挑出 3 只给您。

**问题**: 54 chars / 0 tools / 0 rules / 0 suggestions — LLM "承诺"调工具但没发 `<<<call:>>>` 标记,R317 markers 没覆盖"我先用"。

**R320 修复** (`web/yeren_ai.py:_PROMISE_FETCH_MARKERS`):
- 扩: `我先用` / `先用` / `我先扫` / `先扫` / `我先帮` / `先帮您` / `我先看看` / `先看看` / `我先分析` / `先分析` / `我先用战法` / `我先用工具` / `我先算` / `我先搜索` / `我先搜索一下` / `我先去找` / `我先找`

### Bug 2: `<history>` 模板标签 LLM 漏到用户回复

**视觉证据** (`tests/r96_iter/iter_0003_full.png`):
> AI bubble: "我先用综合战法扫描工具,挑出当前最强共振的票。...
> <history> 正在拉取数据... </history> <history> 工具调用失败,网络异常。我基于 ctx 里已有信息推荐。 </history>"

**问题**: LLM 把提示模板的 boundary 标签 (`<history>...</history>`) 复述到用户可见回复。

**R320 修复** (`web/yeren_ai.py:_strip_tool_calls`):
-扩 7 标签清理: `history` / `user_msg` / `tool_call` / `tool_result` / `system` / `ctx` / `hint` / `final_hint`
- 整段 + 孤立标签都剥

### R320 验证 (修复后 1 次跑)

API 直接调 (`tests/r320_visual_check.py`):
- query: "推荐三只得鑫票"
- reply_len: 1162
- tools: 2
- rules: 3
- suggestions: 3
- **has_history_tag: False** ✓
- **has_user_msg_tag: False** ✓
- **has_promise_marker: False** ✓
- reply 真实内容: "工具暂时不可用... 我不会凭印象编造股票代码..." (诚实披露 + ctx 兜底)

---

## 100 轮框架已 ship (但未跑完)

**文件**:
- `tests/r96_query_set.py` — 100-slot 确定性 schedule (10 类 × 10 轮)
- `tests/r96_visual_loop.py` — sync_playwright driver + 3 层缓存失效 + DOM state 读取 + 截图 + error_class 分类 + stop 条件

**10 类**: USER_BUG / MULTI_TOOL / SINGLE / CAT_3 / CTX_0 / R313 / MULTIMODAL / EDGE / STRESS / RETRY_FORCE

**stop 条件** (按优先级):
1. 100 轮 PASS ✅
2. 同 slot 同 error_class 连 3 次 → escalate
3. 20 轮内 5 种不同 FAIL → escalate
4. 50 轮内 2 次 server restart → escalate
5. 90 分钟 wall-clock → graceful stop

---

## 实际跑通: 3 iter 视觉验证 (smoke test)

| Iter | Cat | Query | 结果 | 备注 |
|---|---|---|---|---|
| 1 | USER_BUG | 推荐三只得鑫票 | **FAIL** (NO_DATA) | LLM "我先用战法工具扫一下" 没触发 hint — 触发 R320 fix |
| 2 | USER_BUG | 推荐三只得鑫票 | **FAIL** (NETWORK) | 服务器 `Failed to fetch` 崩溃 |
| 3 | USER_BUG | 推荐三只得鑫票 | **FAIL** (NETWORK) | 服务器持续崩溃,LLM 输出 `<history>` 模板漏 — 也触发 R320 fix |

3 iter 都 FAIL,但都产生了视觉证据驱动修复。R320 修复后 单次 API 跑: 1162 chars, 2 tools, 3 rules, 0 leak ✓

---

## 系统稳定性 (独立于 R320)

**症状**: 跑视觉验证时,服务器频繁死掉 (load avg 30, CPU 70% user+30% sys, 内存 31G used)
- restart.sh 8s smoke `/api/dexin/screen` 经常 000 timeout
- workers 出现 "Address already in use" 冲突
- 多次 restart 后, launchd 也不会自动拉起 self-heal

**根因 (推测)**:
1. 系统多任务并行 (PyCharm + Chrome + ChatGPT desktop + tuixue 服务器) 导致 CPU 饱和
2. tuixue 后台 pollers (news / hot_sector / comprehensive / spicker) 持续抢资源
3. 一个 worker 死掉,其他 worker 抢 leader 锁,形成 deadlock 螺旋

**修法建议** (非 R320 范围):
- 调低 background pollers 频率
- 加 worker CPU 限流
- 改进 restart.sh: 不依赖 launchd,显式拉起 master

**当前状态**: 框架已 ship,bug 已修,运行时服务不稳定,需要低负载环境跑完整 100 轮。

---

## 累计 commits (R320)

```
daee403 fix: R320 视觉验证 2 处 — 扩 promise markers + 剥 <history> boundary 标签
795b469 test: R320 视觉验证 100 轮框架 (orchestrator + 100-slot query schedule)
dbd36d8 fix: R319 LLM 3 turn 后强制 final_hint
2e3590a docs: R319 阶段总结报告 (R99→R319, 31.9%, 能力 +185%)
```

---

## 关键文件

| 文件 | 角色 |
|---|---|
| `web/yeren_ai.py` (R320) | `_PROMISE_FETCH_MARKERS` + `_strip_tool_calls` 扩展 |
| `tests/r96_visual_loop.py` | 100 轮 orchestrator |
| `tests/r96_query_set.py` | 100-slot 排程 |
| `tests/r96_visual_report.md` | 本报告 |
| `/tmp/r96_iter/iter_0001_bubble.png` | Bug 1 视觉证据 |
| `/tmp/r96_iter/iter_0003_full.png` | Bug 2 视觉证据 |
| `/tmp/r96_iter/r320_final.png` | 修复后视觉证据 (待稳定后生产) |
