#!/usr/bin/env python3
"""
Skill: Evaluate Open Interest
Analyzes OI vs price consistency over a window to validate directional bias.
OI source: Bybit (historical). Price source: BingX (klines).
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
OI_INTERVAL       = os.environ.get("OI_INTERVAL",    "1h")   # period for OI and price window
OI_WINDOW         = int(os.environ.get("OI_WINDOW",  "24"))   # number of periods to look back
OI_SIGNIFICANT    = float(os.environ.get("OI_SIGNIFICANT",    "0.02"))  # 2% OI change = significant
PRICE_SIGNIFICANT = float(os.environ.get("PRICE_SIGNIFICANT", "0.01"))  # 1% price change = significant
API_TIMEOUT       = int(os.environ.get("API_TIMEOUT", "10"))

def _to_bingx_symbol(pair):
    base = pair.replace("USDT", "")
    return f"{base}-USDT"

def _to_gateio_contract(pair):
    base = pair.replace("USDT", "")
    return f"{base}_USDT"

def get_oi_history(pair):
    """Fetches historical OI from Gate.io (oldest → newest)."""
    url = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"
    params = {
        "contract": _to_gateio_contract(pair),
        "interval": OI_INTERVAL,
        "limit":    OI_WINDOW + 1
    }
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    records = resp.json()
    return records  # Gate.io returns oldest first

def get_klines(pair):
    """Fetches recent klines from BingX."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/klines"
    params = {
        "symbol":   _to_bingx_symbol(pair),
        "interval": OI_INTERVAL,
        "limit":    OI_WINDOW + 1
    }
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["data"]

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
    if len(candles) < 2:
        return {"error": "Insufficient price data"}

    oi_records = get_oi_history(pair)
    if len(oi_records) < 2:
        return {"error": "Insufficient OI data"}

    price_start = float(candles[0]["close"])
    price_end   = float(candles[-1]["close"])
    price_change_pct = (price_end - price_start) / price_start * 100

    oi_start = float(oi_records[0]["open_interest_usd"])
    oi_end   = float(oi_records[-1]["open_interest_usd"])
    oi_change_pct = (oi_end - oi_start) / oi_start * 100

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
    pair = "BTC"

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]

    result = evaluate_open_interest(pair)
    print(json.dumps(result, indent=2))
