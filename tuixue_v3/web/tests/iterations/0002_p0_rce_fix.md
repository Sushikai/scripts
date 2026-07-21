# Iteration 0002 — P0 RCE 修复

**日期**: 2026-07-16
**严重度**: P0 (未授权远程代码执行)
**CVE class**: Command Injection (CWE-78)
**触发点**: `GET /api/capital_flow?codes=...`

---

## 漏洞

### 位置
`web/server.py:8132-8170` (入口) → `web/server.py:8177+` (helper)

### 入口
```python
@app.get("/api/capital_flow")
async def api_capital_flow(codes: str = Query(..., description="逗号分隔,最多 20 只")):
    code_list = [c.strip().zfill(6) for c in codes.split(",") if c.strip()][:20]
```

只做了 `.strip().zfill(6)`,**完全没有白名单**。

### 三个注入出口
1. **东财 URL 拼参** (server.py:8192):
   ```python
   secid = ("0" if code.startswith(("6","9","5")) else "1") + "." + code
   f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=..."
   ```
   → URL 注入 (低危,但仍是 input validation 缺失)

2. **腾讯 URL 拼参** (server.py:8225):
   ```python
   f"https://qt.gtimg.cn/q=ff_{market_prefix}{code}"
   ```
   → URL 注入 (低危)

3. **akshare 子进程 RCE** (server.py:8255) ← **致命**:
   ```python
   ak_script = (
       f"..."
       f"df = ak.stock_individual_fund_flow(stock='{code}', market='{market}')\n"
       f"..."
   )
   r = subprocess.run([py, "-c", ak_script], ...)
   ```

### PoC (攻击载荷)
```bash
curl "http://host:7799/api/capital_flow?codes='%3Bimport%20os%3Bos.system('touch%20/tmp/pwned')%3B'"
```
执行后:
- subprocess 启动 `python -c "..."` 含 `import os; os.system('touch /tmp/pwned')`
- `/tmp/pwned` 文件被创建 → 任意 Python 代码在 server 权限下执行
- 公网暴露即可被利用 (ngrok / cloudflared / LAN)

### 影响
- 完全控制 server 进程 (读 env, 偷 MINIMAX_API_KEY)
- 读 /Users/kaikai/scripts/tuixue_v3 全部源码 + 数据
- 借 akshare venv 还能直接用 pip install 装后门
- 没有 admin token 鉴权保护 → 默认匿名访问

---

## 修复 (server.py:8133-8148)

### 双层白名单
**入口** (防御第一层):
```python
raw = [c.strip() for c in codes.split(",") if c.strip()][:20]
# 白名单: 只允许 1-6 位数字, 防止 URL / subprocess 注入
code_list = [c.zfill(6) for c in raw if _re.fullmatch(r"\d{1,6}", c)][:20]
```

**helper** (防御第二层, 防下游误用):
```python
def _batch_capital_helper(codes: list[str]) -> list[dict]:
    for raw in codes:
        # 二次白名单: 即便被直接调用也安全
        if not _re.fullmatch(r"\d{6}", raw):
            continue
        code = raw
        ...
```

### 为什么不只是 escape?
- `escape()` 难做对 (中英文 quote, unicode, zero-width)
- 黑名单永远有漏
- 白名单是最简单粗暴正确的方案: A 股代码就 6 位数字

---

## 验证

### 手动 PoC (修复前会写 /tmp/rce_test_pwned)
```bash
curl "http://localhost:7799/api/capital_flow?codes=000001,'%3Bimport%20os%3Bos.system('touch%20/tmp/rce_test_pwned')%3B'"
ls /tmp/rce_test_pwned  # ← 修复前会存在,修复后 No such file
```

### 自动化回归 (web/tests/regression.py)
新增 `test_api_capital_flow_rce_blocked`:
- 注入 `abc;ls /`, `';import os;os.system('touch /tmp/rce_test_pwned');'`, `../../etc/passwd` + 合法 `000001`
- 断言:返回的 flows 全部 6 位数字 + `/tmp/rce_test_pwned` 不存在

### 全量回归
```
汇总: 17 项, ✓ 17 / ✗ 0 / ! 0
```
RCE fix + 16 baseline 全过,零退化。

---

## 副产品

1. **修测试 bug**: `test_api_review_positions` → `test_api_review_portfolio_positions` (名称不一致)
2. **修 XSS test**: `test_api_stock_xss_safe` 漏 try/except, 现 404 视为安全
3. **审查结论**: 全 web/ 仅 capital_flow 一处公网 RCE 入口;
   server.py:7904 `bash -c "source ~/.hermes/env.sh"` 是固定路径,安全。
4. **二级防护**: helper 内复检白名单, 即便下游代码未来调用 helper 漏传也安全。

---

## 下一步

- [ ] 迭代 3: P1 XSS batch fix (app.js:1547-1551, 1720-1763, view-stock.js:2963+, view-other.js:2671, 2898)
- [ ] 迭代 4: 入参校验加固 (Pydantic Field + boundaries)
- [ ] 迭代 5: 性能 (debounce / raf / memory leak _reviewState.flowsTimer)
- [ ] 迭代 6+: 24 个 audit bug 剩余 23 个

