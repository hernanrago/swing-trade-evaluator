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
_CANDLES_CACHE = {}


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
    cache_key = (instrument, bar, limit)
    if cache_key in _CANDLES_CACHE:
        return _CANDLES_CACHE[cache_key]
    url = f"{OKX_API}/api/v5/market/candles"
    params = {"instId": instrument, "bar": bar, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            return {"error": f"OKX API error: {data.get('msg')}"}
        result = list(reversed(data["data"]))
        _CANDLES_CACHE[cache_key] = result
        return result
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


def build_swing_sequence(pivots, tolerance_pct):
    """
    Build a strictly alternating SH/SL sequence from raw pivots.
    Consecutive same-type: keep more extreme (higher SH, lower SL).
    Equal within tolerance: keep most recent.
    """
    tol = tolerance_pct / 100
    sequence = []

    for pivot in pivots:
        if not sequence:
            sequence.append(pivot)
            continue

        last = sequence[-1]
        if pivot["type"] != last["type"]:
            sequence.append(pivot)
        else:
            if pivot["type"] == "SH":
                if pivot["price"] > last["price"] * (1 + tol):
                    sequence[-1] = pivot   # strictly higher → replace
                elif pivot["price"] >= last["price"] * (1 - tol):
                    sequence[-1] = pivot   # within tolerance → keep most recent
                # else: last is clearly higher → keep last
            else:  # SL
                if pivot["price"] < last["price"] * (1 - tol):
                    sequence[-1] = pivot   # strictly lower → replace
                elif pivot["price"] <= last["price"] * (1 + tol):
                    sequence[-1] = pivot   # within tolerance → keep most recent
                # else: last is clearly lower → keep last

    return sequence


def classify_timeframe(swings, tolerance_pct):
    """
    Classify market structure using last 4 alternating swings.
    Returns dict with structure, classification booleans, invalidation/range levels, last_swings.
    """
    tol = tolerance_pct / 100

    _empty = {
        "structure": "UNDEFINED", "high_structure": None, "low_structure": None,
        "higher_high": False, "higher_low": False,
        "lower_high": False, "lower_low": False,
        "last_swing_high": None, "last_swing_low": None,
        "invalidation": None, "range_high": None, "range_low": None,
        "last_swings": [], "reason": "insufficient_swings",
    }

    if len(swings) < 4:
        return _empty

    last4 = swings[-4:]
    highs_in_seq = [s for s in last4 if s["type"] == "SH"]
    lows_in_seq  = [s for s in last4 if s["type"] == "SL"]

    if len(highs_in_seq) < 2 or len(lows_in_seq) < 2:
        return {**_empty, "last_swings": [
            {"type": s["type"], "price": s["price"], "timestamp": s["timestamp"]} for s in last4
        ]}

    sh1, sh2 = highs_in_seq[0], highs_in_seq[1]
    sl1, sl2 = lows_in_seq[0],  lows_in_seq[1]

    if sh2["price"] > sh1["price"] * (1 + tol):
        high_struct = "HH"
    elif sh2["price"] < sh1["price"] * (1 - tol):
        high_struct = "LH"
    else:
        high_struct = "EH"

    if sl2["price"] > sl1["price"] * (1 + tol):
        low_struct = "HL"
    elif sl2["price"] < sl1["price"] * (1 - tol):
        low_struct = "LL"
    else:
        low_struct = "EL"

    if high_struct == "HH" and low_struct == "HL":
        structure = "LONG"
    elif high_struct == "LH" and low_struct == "LL":
        structure = "SHORT"
    else:
        structure = "UNDEFINED"

    last_sh = next(s for s in reversed(swings) if s["type"] == "SH")
    last_sl = next(s for s in reversed(swings) if s["type"] == "SL")

    if structure == "LONG":
        invalidation = last_sl["price"]
        range_high, range_low, reason = None, None, None
    elif structure == "SHORT":
        invalidation = last_sh["price"]
        range_high, range_low, reason = None, None, None
    else:
        invalidation = None
        range_high = last_sh["price"]
        range_low  = last_sl["price"]
        reason = "mixed_structure"

    last_swings_out = [
        {"type": s["type"], "price": s["price"], "timestamp": s["timestamp"]}
        for s in last4
    ]

    return {
        "structure":       structure,
        "high_structure":  high_struct,
        "low_structure":   low_struct,
        "higher_high":     high_struct == "HH",
        "higher_low":      low_struct == "HL",
        "lower_high":      high_struct == "LH",
        "lower_low":       low_struct == "LL",
        "last_swing_high": last_sh["price"],
        "last_swing_low":  last_sl["price"],
        "invalidation":    invalidation,
        "range_high":      range_high,
        "range_low":       range_low,
        "last_swings":     last_swings_out,
        "reason":          reason,
    }


_CONCLUSION_TABLE = {
    ("LONG",      "LONG"):      ("LONG",      "high"),
    ("SHORT",     "SHORT"):     ("SHORT",     "high"),
    ("LONG",      "UNDEFINED"): ("LONG",      "moderate"),
    ("SHORT",     "UNDEFINED"): ("SHORT",     "moderate"),
    ("UNDEFINED", "LONG"):      ("LONG",      "moderate"),
    ("UNDEFINED", "SHORT"):     ("SHORT",     "moderate"),
    ("LONG",      "SHORT"):     ("CONFLICT",  "low"),
    ("SHORT",     "LONG"):      ("CONFLICT",  "low"),
    ("UNDEFINED", "UNDEFINED"): ("UNDEFINED", "low"),
}


def combine_conclusions(struct_primary, struct_confirm):
    """Returns (conclusion, confidence) from per-timeframe structures."""
    return _CONCLUSION_TABLE.get((struct_primary, struct_confirm), ("UNDEFINED", "low"))


def generate_reasoning(tf_primary, tf_confirm, conclusion, confidence,
                        primary_label="4H", confirm_label="1D"):
    """Generate a concise operative reasoning string."""
    s4 = tf_primary["structure"]
    s1 = tf_confirm["structure"]
    inv4 = tf_primary.get("invalidation")
    inv1 = tf_confirm.get("invalidation")

    def fmt(v):
        return f"${v:,.0f}" if v is not None else "N/A"

    if conclusion == "LONG" and confidence == "high":
        return (
            f"Both {primary_label} and {confirm_label} show HH/HL structure. "
            f"{primary_label} bullish structure valid while price closes above {fmt(inv4)}. "
            f"{confirm_label} bullish structure valid while price closes above {fmt(inv1)}."
        )
    elif conclusion == "SHORT" and confidence == "high":
        return (
            f"Both {primary_label} and {confirm_label} show LH/LL structure. "
            f"{primary_label} bearish structure valid while price closes below {fmt(inv4)}. "
            f"{confirm_label} bearish structure valid while price closes below {fmt(inv1)}."
        )
    elif conclusion in ("LONG", "SHORT") and confidence == "moderate":
        if s4 != "UNDEFINED":
            dominant, inv = primary_label, inv4
            structure_str = s4
        else:
            dominant, inv = confirm_label, inv1
            structure_str = s1
        direction = "bullish" if conclusion == "LONG" else "bearish"
        word = "above" if conclusion == "LONG" else "below"
        return (
            f"{dominant} shows {structure_str} structure ({direction}); "
            f"the other timeframe is structurally undefined. "
            f"Structure valid while price closes {word} {fmt(inv)}."
        )
    elif conclusion == "CONFLICT":
        return (
            f"{primary_label} shows {s4} structure while {confirm_label} shows {s1} structure. "
            f"Timeframes conflict — no structural edge. Wait for alignment."
        )
    else:
        return f"Both {primary_label} and {confirm_label} are structurally undefined. Recent swings do not confirm HH/HL or LH/LL."


def evaluate_market_structure(pair="BTC", context=None):
    """
    Full analysis: fetch candles, detect swings, classify structure.
    In swing mode: uses 4H (primary) and 1D (confirmation).
    In intraday mode: uses 1H (primary) and 15m (confirmation).
    Reads TRADE_MODE from os.environ at call time.
    """
    trade_mode = os.environ.get("TRADE_MODE", "swing")

    if trade_mode == "intraday":
        timeframes = [("1H", "1h", MS_PIVOT_N_4H), ("15m", "15m", MS_PIVOT_N_1D)]
    else:
        timeframes = [("4H", "4h", MS_PIVOT_N_4H), ("1D", "1d", MS_PIVOT_N_1D)]

    instrument = _normalize_pair(pair)
    base = instrument.replace("-USDT-SWAP", "")
    print(f"[*] Evaluating market structure for {instrument} (mode={trade_mode})...", file=sys.stderr)

    results = {}
    for bar, label, n in timeframes:
        candles = get_candles(instrument, bar, MS_LOOKBACK)
        if isinstance(candles, dict) and "error" in candles:
            return {"pair": base, "instrument": instrument, "error": candles["error"]}

        pivots   = find_pivots(candles, n)
        sequence = build_swing_sequence(pivots, MS_EQUAL_TOLERANCE_PCT)
        tf_result = classify_timeframe(sequence, MS_EQUAL_TOLERANCE_PCT)
        results[label] = tf_result
        print(f"[*] {bar}: structure={tf_result['structure']}", file=sys.stderr)

    primary_bar, primary_label, _ = timeframes[0]
    confirm_bar, confirm_label, _ = timeframes[1]

    conclusion, confidence = combine_conclusions(
        results[primary_label]["structure"], results[confirm_label]["structure"]
    )
    reasoning = generate_reasoning(
        results[primary_label], results[confirm_label],
        conclusion, confidence,
        primary_bar, confirm_bar,
    )

    return {
        "pair":          base,
        "instrument":    instrument,
        primary_label:   results[primary_label],
        confirm_label:   results[confirm_label],
        "conclusion":    conclusion,
        "confidence":    confidence,
        "reasoning":     reasoning,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate market structure on 4H and 1D.")
    parser.add_argument("--pair", type=str, default="BTC",
                        help="Trading pair (e.g., BTC, BTCUSDT, BTC-USDT-SWAP)")
    args = parser.parse_args()

    result = evaluate_market_structure(args.pair)
    print(json.dumps(result, indent=2))
