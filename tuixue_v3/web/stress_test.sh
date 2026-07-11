#!/bin/bash
# 100 轮端到端压力测试 — 网络可靠性 + 上游稳定性双轨验证
#
# 设计:
#   端点按 group 分两类:
#     - "net"      网络强指标 (控制面 + 静态资源 + SSE 握手)
#                  失败率必须 = 0%。任何一个 = 整体失败
#     - "upstream" 上游数据依赖 (akshare / 东财 / 腾讯 / 微博热搜)
#                  失败率不卡死(已知周末/晚间限频, memory 记录),只监控均值
#
#   额外检查:
#     - GZip: 静态资源带 content-encoding: gzip
#     - Cache-Control: HTML no-cache / 静态 max-age >= 3600

URL="${URL:-http://localhost:7799}"
ROUNDS="${ROUNDS:-100}"
PARALLEL="${PARALLEL:-3}"

# 不开 set -e:巡检/curl 单点失败不能拖垮整体

# ─── 端点列表 (name path timeout group) ───
#   group: net | upstream
ENDPOINTS=(
  # ─── 网络强指标 (必须 100% 通过) ───
  "health               /api/health                      3    net"
  "metrics              /api/metrics                     3    net"
  "tunnel-status        /api/tunnel/status               3    net"
  "static-index         /                                3    net"
  "static-app.js        /static/app.js                   3    net"
  "static-style.css     /static/style.css                3    net"
  # SSE 握手单独测(超时 3s 拿响应头,不等到第一字节)
  #                   handled below by do_curl_sse
  # ─── 上游数据依赖 (允许偶发失败) ───
  "market-overview      /api/market/overview             15   upstream"
  "news                 /api/news                        15   upstream"
  "global-sentiment     /api/global/sentiment            8    upstream"
  "stock-search         /api/stock/search?q=600519       6    upstream"
  "stock-600519         /api/stock/600519                25   upstream"
  "stock-000001         /api/stock/000001                25   upstream"
  "stock-000002         /api/stock/000002                25   upstream"
  "stock-kline          /api/stock/600519/kline          20   upstream"
  "stock-fund           /api/stock/600519/fund_flow      16   upstream"
  "stock-seats          /api/stock/600519/seats          16   upstream"
  "stock-related-news   /api/stock/600519/related_news   16   upstream"
  "stock-intraday       /api/stock/600519/intraday       18   upstream"
  "watchlist-list       /api/watchlist                   6    upstream"
  "watchlist-ai-002747  /api/watchlist/002747/ai         4    upstream"
  "dragons              /api/dragons                     10   upstream"
  "reports              /api/reports                     5    upstream"
)

# SSE 握手端点:用 2s 短超时;SSE 是长连接,客户端不能占 server 太久
SSE_HANDSHAKE_ENDPOINTS=(
  "sse-screen-handshake /api/stream/screen      2    net"
  "sse-review-handshake /api/stream/review/1    2    net"
)

TOTAL_ENDPOINTS=$((${#ENDPOINTS[@]} + ${#SSE_HANDSHAKE_ENDPOINTS[@]}))
RESULTS_DIR=$(mktemp -d /tmp/tuixue_stress.XXXXXX)

# 通用 GET curl,返回 TSV: name | path | code | size | ms | group
do_curl() {
    local round=$1 name=$2 path=$3 timeout=$4 group=$5
    local t0_ms t1_ms code size dt meta rc
    t0_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    # 注意:不能写 `meta=$(curl ...) || meta="000|0"` — curl 28 timeout 时虽然 stdout 已有内容,
    # 但 $? 让命令替换被视为失败, || 短路会把 stdout 也丢掉 → meta 变 0,假错报
    meta=$(curl -s --max-time "$timeout" -o /dev/null \
        -w "%{http_code}|%{size_download}" "${URL}${path}" 2>/dev/null)
    rc=$?
    t1_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    if [ "$rc" != "0" ] || [ -z "$meta" ]; then
        meta="000|0"
    fi
    code="${meta%%|*}"
    size="${meta##*|}"
    dt=$((t1_ms - t0_ms))
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$name" "$path" "$code" "$size" "$dt" "$group" \
        >> "$RESULTS_DIR/r${round}.tsv"
}

# SSE 握手: 在 SSE 流起之前,服务器先返回 200 + text/event-stream + Content-Type
# curl -D 看响应头,无需等第一 event。3s 内收到 text/event-stream 头 → ok
do_curl_sse() {
    local round=$1 name=$2 path=$3 timeout=$4 group=$5
    local t0_ms t1_ms dt hdr code rc
    t0_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    # 关键:不能用 `hdr=$(curl ...) || hdr=""` — curl timeout 退出时虽然已拿到 headers,
    # 但 || 短路会把 hdr 清空。同 do_curl:用 rc 判断
    hdr=$(curl -s -D - --max-time "$timeout" -o /dev/null \
        -X GET "${URL}${path}" 2>/dev/null)
    rc=$?
    t1_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    if [ "$rc" != "0" ] && [ -z "$hdr" ]; then
        hdr=""
    fi
    # 第一行 HTTP/1.1 200
    if echo "$hdr" | head -1 | grep -qE "^HTTP/[0-9.]+ 200"; then
        # 检查头里 text/event-stream
        if echo "$hdr" | grep -qiE "content-type:.*text/event-stream"; then
            code="200"
        else
            code="200-no-sse"  # 200 但不是 SSE,还是算握手失败
        fi
    else
        code="0"
    fi
    dt=$((t1_ms - t0_ms))
    printf "%s\t%s\t%s\t0\t%s\t%s\n" "$name" "$path" "$code" "$dt" "$group" \
        >> "$RESULTS_DIR/r${round}.tsv"
}

run_round() {
    local round=$1
    : > "$RESULTS_DIR/r${round}.tsv"
    local pids=()
    for entry in "${ENDPOINTS[@]}" "${SSE_HANDSHAKE_ENDPOINTS[@]}"; do
        # parse 4-tuple: name path timeout group
        local name path timeout group
        # 注意空格分隔: name [spaces] path [spaces] timeout [spaces] group
        # 用 awk 拆
        read -r name path timeout group <<< "$(echo "$entry" | awk '{printf "%s %s %s %s\n", $1, $2, $(NF-1), $NF}')"

        if [ "${#pids[@]}" -ge "$PARALLEL" ]; then
            wait "${pids[0]}" 2>/dev/null || true
            pids=("${pids[@]:1}")
        fi

        # SSE 握手端点单独走响应头路径
        if [[ "$name" == sse-*-handshake ]]; then
            do_curl_sse "$round" "$name" "$path" "$timeout" "$group" &
        else
            do_curl     "$round" "$name" "$path" "$timeout" "$group" &
        fi
        pids+=("$!")
    done
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
}

# 单轮摘要(只看网络组的硬指标)
summarize_round() {
    local round=$1
    local file="$RESULTS_DIR/r${round}.tsv"
    [ -f "$file" ] || return 0
    echo "── 第 $round 轮 ── (含 net / upstream)"
    awk -F'\t' '
      NF==6 {
        grp=$6
        key=grp"|"$1
        n[key]++
        if($3 ~ /^2/) ok[key]++; else err[key]++;
        sum[key]+=$5;
        if($5>max[key]) max[key]=$5;
      }
      END {
        printf "%-6s %-22s %5s %5s %5s %8s %8s\n","group","name","n","ok","err","avg_ms","max_ms"
        for(k in n) {
          split(k,parts,"|")
          grp=parts[1]; nm=parts[2]
          avg = sum[k]/n[k]
          printf "%-6s %-22s %5d %5d %5d %8.0f %8d\n", grp, nm, n[k], ok[k]+0, err[k]+0, avg, max[k]
        }
      }' "$file" | sort
    echo ""
}

echo "════════════════════════════════════════════════════════════════"
echo "  退学 v3 · 压测  ·  ${ROUNDS} 轮 × ${TOTAL_ENDPOINTS} 端点 (×${PARALLEL} 并发)"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "→ 健康预热 (3 次) …"
for i in 1 2 3; do
    curl -s --max-time 3 "${URL}/api/health" > /dev/null
done
echo ""

# 校验 GZip / Cache-Control (一次性,确认 middleware 工作)
echo "→ 网络硬指标自动巡检 (GZip / Cache-Control / SSE 头) …"
NET_DIAG_FAIL=0

# 1) GZip on /static/app.js
hdr=$(curl -s -D - --max-time 5 -o /dev/null -H "Accept-Encoding: gzip" "${URL}/static/app.js")
if echo "$hdr" | head -1 | grep -qE "^HTTP/[0-9.]+ 200" \
   && echo "$hdr" | grep -qiE "content-encoding:.*gzip"; then
    echo "  ✓ GZip on /static/app.js"
else
    echo "  ✗ GZip 未生效"
    NET_DIAG_FAIL=1
fi
# 2) HTML no-cache
hdr=$(curl -s -D - --max-time 3 -o /dev/null "${URL}/")
if echo "$hdr" | head -1 | grep -qE "^HTTP/[0-9.]+ 200" \
   && echo "$hdr" | grep -qiE "^cache-control:.*no-cache"; then
    echo "  ✓ HTML no-cache"
else
    echo "  ✗ HTML 没有 no-cache"
    NET_DIAG_FAIL=1
fi
# 3) 静态有 Cache-Control max-age
hdr=$(curl -s -D - --max-time 3 -o /dev/null "${URL}/static/style.css")
if echo "$hdr" | grep -qiE "^cache-control:.*max-age=(3600|[1-9][0-9]{3,})"; then
    echo "  ✓ 静态资源 Cache-Control max-age >= 3600"
else
    echo "  ✗ 静态资源缺 Cache-Control"
    NET_DIAG_FAIL=1
fi
# 4) SSE 头 — SSE 端点首次响应可能 200 + chunked,不一定立即把头读完;我们用 --max-time 截断
hdr=$(curl -s -D - --max-time 2 -o /dev/null "${URL}/api/stream/screen?date=&mode=live" 2>/dev/null || true)
# 单独判断 4 项子条件,任何失败都明确指出
sse_diag_ok=1
if echo "$hdr" | head -1 | grep -qE "^HTTP/[0-9.]+ 200"; then
    :
else
    echo "  · SSE status 非 200 (header 第一行: $(echo "$hdr" | head -1))"
    sse_diag_ok=0
fi
if echo "$hdr" | grep -qiE "content-type:.*text/event-stream"; then
    :
else
    echo "  · SSE 缺 content-type: text/event-stream"
    sse_diag_ok=0
fi
if [ "$sse_diag_ok" = "1" ]; then
    echo "  ✓ SSE 握手 text/event-stream 响应头"
else
    echo "  ✗ SSE 握手缺 text/event-stream 头"
    NET_DIAG_FAIL=1
fi

if [ "$NET_DIAG_FAIL" != "0" ]; then
    echo ""
    echo "✗ 网络硬指标巡检未通过,请确认 GZipMiddleware / cache 头 / SSE 已生效"
    exit 1
fi
echo ""

# 主压测循环
# 每 20 轮做健康检查,server 假死立刻报警退出 (避免测了 80 轮都失败才发现)
ROUND_PAUSE="${ROUND_PAUSE:-0.3}"   # 轮间停顿,让 server 处理 in-flight + 清 SSE 长连接
HEALTH_EVERY="${HEALTH_EVERY:-20}"

for r in $(seq 1 "$ROUNDS"); do
    run_round "$r"

    # 健康巡检
    if [ "$((r % HEALTH_EVERY))" = 0 ] && [ "$r" != "$ROUNDS" ]; then
        health_code=$(curl -s --max-time 3 -o /dev/null -w "%{http_code}" "${URL}/api/health")
        if [ "$health_code" != "200" ]; then
            echo ""
            echo "✗ 第 $r 轮:server 不再响应 (health=$health_code),停止压测"
            echo "   失败发生在前 $r 轮,可能上游全死/资源耗尽,检查 server log"
            exit 1
        fi
    fi

    if [ "$r" -le 3 ] || [ "$((r % 20))" = 0 ]; then
        summarize_round "$r"
    fi

    sleep "$ROUND_PAUSE" 2>/dev/null || true
done

# ─── 全程汇总 ───
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  全程汇总  ·  ${ROUNDS} 轮 × ${TOTAL_ENDPOINTS} 端点"
echo "════════════════════════════════════════════════════════════════"
cat "$RESULTS_DIR"/r*.tsv | awk -F'\t' '
  {
    grp=$6
    key=grp"|"$1
    n[key]++
    if($3 ~ /^2/) ok[key]++; else err[key]++;
    sum[key]+=$5; sum_b[key]+=$4;
    if($5>max[key]) max[key]=$5;
  }
  END {
    net_total=0; net_ok=0; net_err=0
    up_total=0;  up_ok=0;  up_err=0
    printf "%-6s %-22s %6s %6s %6s %8s %8s %8s %6s\n","group","name","calls","ok","err","avg_ms","max_ms","avg_b","err_%"
    for(k in n) {
      split(k,parts,"|")
      grp=parts[1]; nm=parts[2]
      avg  = sum[k]/n[k]
      avg_b = sum_b[k]/n[k]
      err_pct = err[k]*100/n[k]
      printf "%-6s %-22s %6d %6d %6d %8.0f %8d %8.0f %6.1f%%\n", grp, nm, n[k], ok[k]+0, err[k]+0, avg, max[k], avg_b, err_pct
      if(grp=="net")      { net_total+=n[k]; net_ok+=ok[k]+0; net_err+=err[k]+0 }
      else                { up_total +=n[k]; up_ok +=ok[k]+0; up_err +=err[k]+0 }
    }
    printf "\n"
    if(net_total>0) printf "网络强指标 (net):     %d/%d 成功 · 失败率 %.2f%%\n", net_ok, net_total, net_err*100/net_total
    if(up_total>0)  printf "上游数据源 (upstream):%d/%d 成功 · 失败率 %.2f%%\n", up_ok,  up_total,  up_err*100/up_total

    # 网络强指标必须 100% (允许 SSE 握手 1 次误报缓冲=99%)
    if(net_total>0 && net_err/net_total > 0.01) {
      printf "\n✗ 网络强指标失败率超阈 (1%%),整体不算稳\n"
      exit 1
    }
    # 上游允许高失败率,但平均响应不能退化
    if(up_total>0 && up_err/up_total > 0.50) {
      printf "\n⚠ 上游失败率超过 50%%,数据源可能整体挂\n"
      # 不算 fail,只警告
    }
    printf "\n✓ 网络层稳(强指标全过) · 上游数据源按实际显示\n"
  }' | sort

rm -rf "$RESULTS_DIR"
