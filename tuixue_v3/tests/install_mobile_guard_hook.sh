#!/usr/bin/env bash
# 装 git pre-commit hook: 改 web/static/*.{css,html,js} → 自动跑 mobile guard
# 不强制 hook: git commit --no-verify 可跳过
set -e
cd "$(dirname "$0")/.."

GIT_DIR=$(git rev-parse --git-dir)
HOOK="$GIT_DIR/hooks/pre-commit"
mkdir -p "$GIT_DIR/hooks"
if [ -f "$HOOK" ]; then
    if grep -q "mobile_guard" "$HOOK"; then
        echo "pre-commit hook already has mobile_guard, 跳过"
        exit 0
    fi
    echo "⚠ $HOOK 已存在,备份到 $HOOK.bak"
    cp "$HOOK" "$HOOK.bak"
fi

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# mobile regression guard — 自动跑 (改 web/static/* 时)
changed=$(git diff --cached --name-only -- 'web/static/style.css' 'web/static/index.html' 'web/static/app.js' 'web/static/core.js' 'web/static/zt-frontend.js' 'web/static/view-other.js' 'web/static/view-*.js')
if [ -n "$changed" ]; then
    echo "[pre-commit] mobile guard checking:"
    echo "$changed" | sed 's/^/  /'
    # 检查 server 是否在跑
    if ! curl -sf -m 3 http://localhost:7799/api/health > /dev/null 2>&1; then
        echo "[pre-commit] ⚠ server 7799 不可达,跳过 mobile guard (启动 server 后重跑 commit)"
        exit 0
    fi
    python3 tests/mobile_guard.py --once --viewport 390 || {
        echo "[pre-commit] ✗ mobile regression FAIL"
        echo "  报告见 /tmp/mobile_guard/report.json"
        echo "  失败截图见 web/static/audit/mobile_fail/"
        echo "  绕过: git commit --no-verify"
        exit 1
    }
fi
EOF

chmod +x "$HOOK"
echo "✓ pre-commit hook 装好: $HOOK"
echo "  改 web/static/*.{css,html,js} 时自动跑 mobile_guard"
echo "  跳过: git commit --no-verify"