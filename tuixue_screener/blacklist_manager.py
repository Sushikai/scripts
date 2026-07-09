#!/usr/bin/env python3
"""
tuixue_screener/blacklist_manager.py
黑名单管理工具：手动添加 / 删除 / 查看永久拉黑池。

止损离场的亏损杂毛标的加入黑名单后，永久不会再进入选股结果。
"""

import json
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
BL_FILE = ROOT / "blacklist.json"


def load():
    if not BL_FILE.exists():
        return {"blacklist": [], "history": []}
    return json.loads(BL_FILE.read_text())


def save(data):
    BL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def list_all():
    data = load()
    print(f"\n黑名单总数: {len(data.get('blacklist', []))}")
    for i, code in enumerate(data.get("blacklist", []), 1):
        # 找最近一次拉黑原因
        reason = ""
        for h in reversed(data.get("history", [])):
            if h.get("code") == code:
                reason = h.get("reason", "")
                break
        print(f"  {i:>3}. {code}  |  {reason}")
    return data


def add(code, reason="止损离场"):
    data = load()
    if code in data["blacklist"]:
        print(f"  {code} 已在黑名单中")
        return
    data["blacklist"].append(code)
    data.setdefault("history", []).append({
        "code": code,
        "reason": reason,
        "at": datetime.now().isoformat(),
    })
    save(data)
    print(f"  ✅ 已添加 {code}: {reason}")


def remove(code):
    data = load()
    if code not in data["blacklist"]:
        print(f"  {code} 不在黑名单中")
        return
    data["blacklist"].remove(code)
    save(data)
    print(f"  ✅ 已移除 {code}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="退学战法黑名单管理")
    sub = parser.add_subparsers(dest="cmd", help="子命令")

    p_list = sub.add_parser("list", help="查看黑名单")
    p_add = sub.add_parser("add", help="添加黑名单")
    p_add.add_argument("code", help="股票代码")
    p_add.add_argument("--reason", default="止损离场", help="拉黑原因")
    p_rm = sub.add_parser("remove", help="移除黑名单")
    p_rm.add_argument("code", help="股票代码")

    args = parser.parse_args()
    if args.cmd == "list" or args.cmd is None:
        list_all()
    elif args.cmd == "add":
        add(args.code, args.reason)
    elif args.cmd == "remove":
        remove(args.code)
    else:
        parser.print_help()