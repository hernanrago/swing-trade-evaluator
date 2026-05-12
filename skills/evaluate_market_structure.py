#!/usr/bin/env python3
"""
Skill: Evaluate Market Structure
Detects swing highs/lows on 4H and 1D timeframes using classic pivot logic.
Classifies structure as LONG (HH/HL), SHORT (LH/LL), or UNDEFINED.
"""

import os
import json
import sys
import argparse
import requests

# --- Config (override via environment variables) ---
MS_PIVOT_N_4H          = int(os.environ.get("MS_PIVOT_N_4H",          "3"))
MS_PIVOT_N_1D          = int(os.environ.get("MS_PIVOT_N_1D",          "5"))
MS_LOOKBACK            = int(os.environ.get("MS_LOOKBACK",            "200"))
MS_EQUAL_TOLERANCE_PCT = float(os.environ.get("MS_EQUAL_TOLERANCE_PCT", "0.05"))
API_TIMEOUT            = int(os.environ.get("API_TIMEOUT",             "10"))

OKX_API = "https://www.okx.com"


def _normalize_pair(pair):
    """Normalize any pair format to an OKX USDT perpetual swap instrument."""
    pair = pair.strip().upper()
    if pair.endswith("-SWAP"):
        pair = pair[:-5]  # strip "-SWAP"
    if pair.endswith("-USDT"):
        pair = pair[:-5]  # strip "-USDT"
    # Remove any remaining separators (e.g. underscores)
    base = pair.replace("-", "").replace("_", "")
    # If still has USDT at the end (e.g. "BTCUSDT"), strip it
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}-USDT-SWAP"
