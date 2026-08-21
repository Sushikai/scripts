#!/usr/bin/env python3
"""R300 commit guard — 只 stage 含 R300 标记的 hunks, 其他外部改动不动.

用法:
  python3 tests/r300_commit.py "visual: R300-N ..."
  (等价于 git add 部分文件 + git commit, 但只提交本轮 R300 hunk)
"""
import subprocess, sys, tempfile, os, re

FILES = ["web/static/style.css", "web/static/tokens.css"]
# 测试文件允许: R300 测试配套
TEST_FILES = ["tests/test_visual_tokens.py"]

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print("CMD FAIL:", cmd, r.stderr[:500])
    return r.stdout

def main():
    msg = sys.argv[1] if len(sys.argv) > 1 else "visual: R300 hunk"
    all_files = FILES + TEST_FILES
    full = run(f"git diff -- {' '.join(all_files)}")
    if not full.strip():
        print("NO DIFF")
        return
    # 解析成 file-blocks, 每个 block 内只保留含 R300 的 hunks
    blocks = re.split(r"(?=^diff --git )", full, flags=re.M)
    kept = []
    for b in blocks:
        if not b.strip():
            continue
        # 分离 header (diff --git .. index .. --- +++ ) 与 hunks
        lines = b.splitlines(keepends=True)
        header = []
        i = 0
        # 收集 header 直到第一个 @@
        while i < len(lines) and not lines[i].startswith("@@ "):
            header.append(lines[i])
            i += 1
        # 剩余按 @@ 切 hunk
        body = "".join(lines[i:])
        hunks = re.split(r"(?=^@@ )", body, flags=re.M)
        selected = []
        for h in hunks:
            if not h.strip():
                continue
            if "R300" in h:
                selected.append(h)
        if selected:
            kept.append("".join(header) + "".join(selected))
    if not kept:
        print("NO R300 HUNKS — 不提交")
        sys.exit(1)
    patch = "\n".join(k.rstrip("\n") for k in kept) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(patch)
        fname = f.name
    # 校验 patch 可应用
    r = subprocess.run(f"git apply --check --cached {fname}", shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print("PATCH CHECK FAIL:", r.stderr[:500])
        print(patch[:800])
        os.unlink(fname)
        sys.exit(1)
    subprocess.run(f"git apply --cached {fname}", shell=True, check=True)
    os.unlink(fname)
    # 提交 (仅 index)
    print("STAGED HUNKS (含 R300):")
    for l in patch.splitlines():
        if l.startswith(("+", "-")):
            print("  " + l[:120])
    r = subprocess.run(f"git commit -m '{msg}'", shell=True, capture_output=True, text=True)
    print(r.stdout[-400:])
    print(r.stderr[-400:])

if __name__ == "__main__":
    main()
