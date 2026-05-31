#!/usr/bin/env python3
"""
DM monitor launcher - raises fd limit and runs the monitor
"""
import sys, asyncio, os
from pathlib import Path

try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < 65535:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (65535, hard))
            print(f"[OK] Raised fd limit: {soft} -> 65535")
        except Exception as e:
            print(f"[WARN] Could not raise fd limit: {e}")
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (8192, hard))
                print(f"[OK] Raised fd limit: {soft} -> 8192")
            except Exception as e2:
                print(f"[WARN] Could not raise fd limit to 8192: {e2}")
except Exception as e:
    print(f"[WARN] resource handling error: {e}")

sys.path.insert(0, str(Path(__file__).parent))
from bilibili_utils.dm.monitor import main as process_conversations

print("[INFO] Starting bilibili_dm_monitor...")
asyncio.run(process_conversations())
print("[INFO] bilibili_dm_monitor finished.")