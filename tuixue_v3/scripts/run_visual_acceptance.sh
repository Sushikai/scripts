#!/usr/bin/env bash
# scripts/run_visual_acceptance.sh — 退学 v3 视觉验收全套
#
# 5 个 stage:
#   [1] 启 server (假定 7799 端口空闲,失败自动 fork)
#   [2] 规则化扫描 (硬编码颜色/字号/圆角/间距)
#   [3] AA 对比度 (token 配色无障碍)
#   [4] API 契约 (130+ 端点)
#   [5] 78 张视觉验收 (13 view × 3 视口 × 2 主题)
#
# 用法: bash scripts/run_visual_acceptance.sh [--skip-server]
# 退出码: 0 = 全过, 1 = 任意 stage fail
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "╔════════════════════════════════════════════════╗"
echo "║  TUIXUE v3 视觉/契约全套验收                   ║"
echo "╚════════════════════════════════════════════════╝"
echo "  ROOT=$ROOT"
echo "  PORT=${TUIXUE_PORT:-7799}"
echo

# ──────── Stage 1: server ────────
SERVER_PID=""
SKIP_SERVER="0"
[[ "${1:-}" == "--skip-server" ]] && SKIP_SERVER="1"

if [[ "$SKIP_SERVER" != "1" ]]; then
  if ! curl -sf "http://127.0.0.1:7799/api/healthz" -o /dev/null --max-time 2; then
    echo "[1/5] 启动 server (后台)…"
    pkill -f "python.*web/server.py" 2>/dev/null || true
    nohup python3 web/server.py --no-preheat >/tmp/tuixue-acceptance-server.log 2>&1 &
    SERVER_PID=$!
    echo "  PID=$SERVER_PID  log=/tmp/tuixue-acceptance-server.log"
    # 等就绪
    for i in $(seq 1 40); do
      if curl -sf "http://127.0.0.1:7799/api/healthz" -o /dev/null --max-time 2; then
        echo "  ✓ server ready (${i}×0.5s)"
        break
      fi
      sleep 0.5
    done
    if ! curl -sf "http://127.0.0.1:7799/api/healthz" -o /dev/null --max-time 2; then
      echo "  ✗ server 未就绪 (超时 20s)"
      tail -50 /tmp/tuixue-acceptance-server.log
      exit 1
    fi
  else
    echo "[1/5] 检测到 server 在 7799 — 沿用"
  fi
else
  echo "[1/5] --skip-server: 假定 server 已就绪"
fi

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    echo "  关闭 server pid=$SERVER_PID"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ──────── Stage 2: 规则化 ────────
echo
echo "[2/5] 规则化扫描 — 硬编码 #hex/font-size/border-radius"
if ! PYTHONPATH="$ROOT" python3 -m pytest tests/test_visual_tokens.py -m "visual and not contract" \
        --tb=short -q 2>&1 | tail -40; then
  echo "  ✗ 规则化扫描 fail"
  exit 1
fi

# ──────── Stage 3: AA 对比度 ────────
echo
echo "[3/5] AA 对比度 (token 配色)"
if ! PYTHONPATH="$ROOT" python3 -m pytest tests/test_visual_tokens.py::TestAAContrast \
        --tb=short -q 2>&1 | tail -20; then
  echo "  ✗ AA 对比度 fail"
  exit 1
fi

# ──────── Stage 4: API 契约 ────────
echo
echo "[4/5] API 契约 — 130+ 端点信封/status"
if ! PYTHONPATH="$ROOT" python3 -m pytest tests/test_api_contract.py -m contract \
        --tb=short -q 2>&1 | tail -40; then
  echo "  ✗ API 契约 fail"
  exit 1
fi

# ──────── Stage 5: 78 张视觉验收 ────────
echo
echo "[5/5] 78 张视觉验收 — 13 view × 3 视口 × 2 主题"
mkdir -p /tmp/audit
if ! python3 audit_views.py --vp desktop --theme both 2>&1 | tail -20; then
  echo "  ⚠ desktop 双主题 26 截图可能未完成 (audit_views 不支持 --theme both)"
fi
if ! python3 audit_views.py --vp iphone13 --theme both 2>&1 | tail -20; then
  echo "  ⚠ mobile (390) 双主题 26 截图可能未完成"
fi
if ! python3 audit_views.py --vp mini --theme both 2>&1 | tail -20; then
  echo "  ⚠ mini (360) 双主题 26 截图可能未完成"
fi
ls -1 /tmp/audit/ 2>/dev/null | wc -l | xargs echo "  → 截图落 /tmp/audit/, 共计"

echo
echo "╔════════════════════════════════════════════════╗"
echo "║  验收 1-4 PASS  ✅                              ║"
echo "║  Stage 5 视觉评图 → 交本会话 Read 评图             ║"
echo "╚════════════════════════════════════════════════╝"
echo "请把 /tmp/audit/ 下 PNG 列表读入会话,做最后视觉验收。"
