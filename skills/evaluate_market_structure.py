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


def get_candles(instrument, bar, limit):
    """Fetch candles from OKX. Returns list sorted oldest → newest, or {"error": ...}."""
    url = f"{OKX_API}/api/v5/market/candles"
    params = {"instId": instrument, "bar": bar, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            return {"error": f"OKX API error: {data.get('msg')}"}
        # OKX returns newest first → reverse for oldest first
        return list(reversed(data["data"]))
    except Exception as e:
        return {"error": f"OKX candles error: {e}"}


def find_pivots(candles, n):
    """
    Detect confirmed pivot highs and lows.
    A pivot high at i: high[i] is the maximum of high[i-n:i+n+1],
      and no candle j > i in the window shares the same maximum (keep most recent).
    Excludes last n candles (no right-side confirmation).
    Returns list of {"type": "SH"|"SL", "price": float, "timestamp": str, "index": int},
    sorted by index (oldest first).
    """
    highs = [float(c[2]) for c in candles]
    lows  = [float(c[3]) for c in candles]
    timestamps = [c[0] for c in candles]
    pivots = []

    for i in range(n, len(candles) - n):
        window_start = i - n
        window_end   = i + n + 1  # exclusive

        # Pivot high: max in window, no later equal in right half
        max_high = max(highs[window_start:window_end])
        if highs[i] == max_high:
            no_later_equal = not any(
                highs[j] == max_high for j in range(i + 1, window_end)
            )
            if no_later_equal:
                pivots.append({
                    "type": "SH",
                    "price": highs[i],
                    "timestamp": timestamps[i],
                    "index": i,
                })

        # Pivot low: min in window, no later equal in right half
        min_low = min(lows[window_start:window_end])
        if lows[i] == min_low:
            no_later_equal = not any(
                lows[j] == min_low for j in range(i + 1, window_end)
            )
            if no_later_equal:
                pivots.append({
                    "type": "SL",
                    "price": lows[i],
                    "timestamp": timestamps[i],
                    "index": i,
                })

    pivots.sort(key=lambda x: x["index"])
    return pivots
