#!/usr/bin/env python3
"""
Skill: Evaluate Open Interest
Analyzes OI vs price consistency over a window to validate directional bias.
OI source: Binance Futures (openInterestHist). Price source: Binance Futures (klines).
"""

import os
import json
import sys
import argparse
import requests

# --- Config (override via environment variables) ---
OI_INTERVAL       = os.environ.get("OI_INTERVAL",    "1h")   # period for OI and price window
OI_WINDOW         = int(os.environ.get("OI_WINDOW",  "24"))   # number of periods to look back
OI_SIGNIFICANT    = float(os.environ.get("OI_SIGNIFICANT",    "0.02"))  # 2% OI change = significant
PRICE_SIGNIFICANT = float(os.environ.get("PRICE_SIGNIFICANT", "0.01"))  # 1% price change = significant
API_TIMEOUT       = int(os.environ.get("API_TIMEOUT", "10"))

BINANCE_FAPI = "https://fapi.binance.com"

def _to_binance_symbol(pair):
    if not pair.endswith("USDT"):
        return pair.upper() + "USDT"
    return pair.upper()

def get_oi_history(pair):
    """Fetches historical OI from Binance Futures (oldest → newest)."""
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
    params = {
        "symbol": _to_binance_symbol(pair),
        "period": OI_INTERVAL,
        "limit":  OI_WINDOW + 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        records = resp.json()
        return sorted(records, key=lambda x: int(x["timestamp"]))
    except Exception as e:
        return {"error": f"Binance OI API error: {e}"}

def get_klines(pair):
    """Fetches recent klines from Binance Futures (oldest → newest)."""
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {
        "symbol":   _to_binance_symbol(pair),
        "interval": OI_INTERVAL,
        "limit":    OI_WINDOW + 1,
    }
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # Each kline: [open_time, open, high, low, close, ...]
        return sorted(data, key=lambda x: int(x[0]))
    except Exception as e:
        return {"error": f"Binance klines API error: {e}"}

def evaluate_open_interest(pair="BTC"):
    """
    Validates directional bias by checking OI vs price consistency.

    Price up + OI up → new longs entering → LONG validated
    Price down + OI up → new shorts entering → SHORT validated
    Price up + OI down → short covering, not new demand → LONG weak
    Price down + OI down → long liquidation, not new selling → SHORT weak
    """
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating open interest for {pair}...", file=sys.stderr)

    candles = get_klines(pair)
    if isinstance(candles, dict) and "error" in candles:
        return candles
    if len(candles) < 2:
        return {"error": "Insufficient price data"}

    oi_records = get_oi_history(pair)
    if isinstance(oi_records, dict) and "error" in oi_records:
        return oi_records
    if len(oi_records) < 2:
        return {"error": "Insufficient OI data"}

    # Binance klines: [open_time, open, high, low, close, ...]
    price_start = float(candles[0][4])
    price_end   = float(candles[-1][4])
    price_change_pct = ((price_end - price_start) / price_start * 100) if price_start > 0 else 0

    # Binance OI hist: {"sumOpenInterestValue": "...", "timestamp": ..., ...}
    oi_start = float(oi_records[0]["sumOpenInterestValue"])
    oi_end   = float(oi_records[-1]["sumOpenInterestValue"])
    oi_change_pct = ((oi_end - oi_start) / oi_start * 100) if oi_start > 0 else 0

    price_up = price_change_pct > 0
    oi_up    = oi_change_pct > 0

    price_significant = abs(price_change_pct) >= PRICE_SIGNIFICANT * 100
    oi_significant    = abs(oi_change_pct)    >= OI_SIGNIFICANT    * 100

    if price_up and oi_up:
        signal = "LONG"
        strength = "strong" if price_significant and oi_significant else "moderate"
        interpretation = "Price rising with new positions entering — bullish move validated."
        note = "Real demand backing the move: new money is entering on the long side."
    elif not price_up and oi_up:
        signal = "SHORT"
        strength = "strong" if price_significant and oi_significant else "moderate"
        interpretation = "Price falling with new positions entering — bearish move validated."
        note = "Real selling pressure backing the move: new money is entering on the short side."
    elif price_up and not oi_up:
        signal = "LONG"
        strength = "weak"
        interpretation = "Price rising but OI falling — likely short covering, not new buying."
        note = "Less reliable: upward pressure may come from shorts exiting, not longs entering."
    else:
        signal = "SHORT"
        strength = "weak"
        interpretation = "Price falling but OI falling — likely long liquidation, not new shorting."
        note = "Less reliable: downward pressure may come from longs being forced out, not new shorts."

    window_label = f"{OI_WINDOW}×{OI_INTERVAL}"
    reasoning = "\n".join([
        f"Price change ({window_label}): {price_change_pct:+.2f}%  (${price_start:,.0f} → ${price_end:,.0f})",
        f"OI change    ({window_label}): {oi_change_pct:+.2f}%  (${oi_start:,.0f} → ${oi_end:,.0f})",
        "",
        interpretation,
        note,
        "",
        f"Signal: {signal} ({strength})",
    ])

    return {
        "pair":              pair,
        "window":            window_label,
        "price_change_pct":  round(price_change_pct, 2),
        "oi_change_pct":     round(oi_change_pct, 2),
        "price_direction":   "up" if price_up else "down",
        "oi_direction":      "up" if oi_up else "down",
        "signal":            signal,
        "strength":          strength,
        "interpretation":    interpretation,
        "reasoning":         reasoning
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Open Interest vs Price consistency.")
    parser.add_argument("--pair", type=str, default="BTC", help="Trading pair (e.g., BTC or BTCUSDT)")
    args = parser.parse_args()

    result = evaluate_open_interest(args.pair)
    print(json.dumps(result, indent=2))
