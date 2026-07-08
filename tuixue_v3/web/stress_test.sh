#!/bin/bash
# 10 轮端到端压力测试
# 每轮跑一组端点,记录响应时间 / HTTP 状态 / 错误信息
# 最后汇总稳定性指标

set -e

URL="http://localhost:7799"
ROUNDS=10

# 端点列表 (name path timeout_sec)
ENDPOINTS=(
  "health           /api/health                 3"
  "static-index     /                           3"
  "static-app.js    /static/app.js              3"
  "static-style.css /static/style.css           3"
  "market-overview  /api/market/overview        20"
  "stock-search     /api/stock/search?q=600519  20"
  "stock-600519     /api/stock/600519           25"
  "stock-000001     /api/stock/000001           25"
  "stock-kline      /api/stock/600519/kline     8"
  "stock-fund       /api/stock/600519/fund_flow 12"
  "stock-seats      /api/stock/600519/seats     12"
  "reports          /api/reports                3"
  "metrics          /api/metrics                3"
)

# 模拟 3 个并发用户(IP 不同,服务端用 host,但限频按 connection)
# 用 curl --interface 或不同 source IP 太复杂;直接 n 个并行 curl
TOTAL_ENDPOINTS=${#ENDPOINTS[@]}

run_round() {
  local round=$1
  local out_file="/tmp/stress_r${round}.tsv"
  : > "$out_file"

  for entry in "${ENDPOINTS[@]}"; do
    IFS=' ' read -r name path timeout <<< "$entry"
    local t0_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    local code=$(curl -s --max-time "$timeout" -o /dev/null -w "%{http_code}" "${URL}${path}" 2>&1)
    local t1_ms=$(python3 -c "import time;print(int(time.time()*1000))")
    local dt=$((t1_ms - t0_ms))
    printf "%s\t%s\t%s\t%s\n" "$name" "$path" "$code" "$dt" >> "$out_file"
  done

  # 一轮跑完打印摘要
  echo "── 第 $round 轮 ──"
  awk -F'\t' '
    NF==4 {
      n[$1]++; code[$1"|"$3]++; sum[$1]+=$4; if($4>max[$1]) max[$1]=$4; if(min[$1]==""||$4<min[$1]) min[$1]=$4;
    }
    END {
      printf "%-18s %6s %8s %8s %8s\n","name","n","ok","avg_ms","max_ms"
      for(k in n) {
        ok=0; for(c in code) { split(c,a,"|"); if(a[1]==k && a[2]~/^2/) ok+=code[c] }
        avg = sum[k]/n[k]
        printf "%-18s %6d %8d %8.0f %8d\n", k, n[k], ok, avg, max[k]
      }
    }' "$out_file" | sort
  echo ""
}

echo "═══════════════════════════════════════════════════════════════"
echo "  退学 v3 · 压力测试  ·  $ROUNDS 轮 × $TOTAL_ENDPOINTS 端点"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 健康预热
for i in 1 2 3; do
  curl -s --max-time 3 "${URL}/api/health" > /dev/null
done

for r in $(seq 1 $ROUNDS); do
  run_round $r
done

# 最终汇总
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  全程汇总"
echo "═══════════════════════════════════════════════════════════════"
cat /tmp/stress_r*.tsv | awk -F'\t' '
  {
    n[$1]++; code[$1"|"$3]++; sum[$1]+=$4; if($4>max[$1]) max[$1]=$4; if(min[$1]==""||$4<min[$1]) min[$1]=$4;
  }
  END {
    total=0; ok=0; err=0
    printf "%-18s %6s %6s %6s %8s %8s %6s\n","name","calls","ok","err","avg_ms","max_ms","err_%"
    for(k in n) {
      c_err=0
      for(c in code) {
        split(c,a,"|")
        if(a[1]==k) {
          if(a[2]~/^2/) ok_count[k]+=code[c]
          else c_err+=code[c]
        }
      }
      avg = sum[k]/n[k]
      err_pct = c_err*100/n[k]
      printf "%-18s %6d %6d %6d %8.0f %8d %6.1f%%\n", k, n[k], ok_count[k]+0, c_err, avg, max[k], err_pct
      total += n[k]
      ok += ok_count[k]+0
      err += c_err
    }
    printf "\n总计 %d 次 · 成功 %d · 失败 %d · 整体成功率 %.1f%%\n", total, ok, err, ok*100/total
  }' | sort

rm -f /tmp/stress_r*.tsv
