#!/usr/bin/env bash
# 后端 API benchmark — 单接口 50 并发 100 请求, 收集 P50/P95/P99/max
# 用法: bash _bench_api.sh <endpoint> [warmup_n]
# 例: bash _bench_api.sh /api/stock/600519/core
set -e
EP=${1:-/api/stock/600519/core}
WARMUP=${2:-3}
N=${N:-100}
C=${C:-50}
BASE=${BASE:-http://localhost:7799}

echo "=== Benchmark: ${BASE}${EP} (n=${N} c=${C}) ==="

# warmup
for i in $(seq 1 $WARMUP); do
  curl -s -o /dev/null "${BASE}${EP}"
done

# 用 ab 没装就改用 hey / 自写并发
which ab >/dev/null 2>&1 || { echo "ab not installed"; exit 1; }

ab -n $N -c $C -q "${BASE}${EP}" 2>&1 | grep -E "Requests per second|Time per request|Percentage|Failed|Connection Times|---"
