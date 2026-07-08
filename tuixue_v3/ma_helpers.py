"""
tuixue_v3/ma_helpers.py
独立工具：避免循环 import
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path

import pandas as pd

log = logging.getLogger("tuixue_v3.ma_helpers")


def apply_ma60_override() -> None:
    """全局 patch Layer3 的 MA60 严格性（按 cfg.L3_REQUIRE_MA60）"""
    from . import config as cfg, layer3_daily as l3
    if not getattr(cfg, "L3_REQUIRE_MA60", True):
        def relaxed(df):
            need = ["MA5", "MA10"]
            if not all(c in df.columns for c in need):
                return False, {"reason": "指标缺失"}
            last = df.iloc[-1]
            ma5 = float(last["MA5"]) if pd.notna(last["MA5"]) else 0
            ma10 = float(last["MA10"]) if pd.notna(last["MA10"]) else 0
            if ma5 <= 0 or ma10 <= 0:
                return False, {"reason": "指标为 NaN"}
            price = float(last["收盘"])
            ok = ma5 > ma10 and price > ma5
            return ok, {"MA5": round(ma5, 2), "MA10": round(ma10, 2), "price": round(price, 2)}

        l3._check_ma_alignment = relaxed
        log.info("L3 MA 检查已放宽（仅 MA5 > MA10 + price > MA5）")