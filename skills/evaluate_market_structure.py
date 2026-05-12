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
        return pair
    # Strip known suffixes and separators, then rebuild
    base = pair.replace("-USDT", "").replace("USDT", "").replace("-", "").replace("_", "")
    return f"{base}-USDT-SWAP"
