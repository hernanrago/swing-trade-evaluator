#!/usr/bin/env python3
"""
Skill: Evaluate Open Interest
Analyzes OI consistency with price movement to validate or question a directional bias.
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
OI_INTERVAL       = os.environ.get("OI_INTERVAL",       "1h")    # period for OI and price window
OI_WINDOW         = int(os.environ.get("OI_WINDOW",     "24"))    # number of periods to look back
OI_SIGNIFICANT    = float(os.environ.get("OI_SIGNIFICANT",    "0.02"))  # 2% OI change = significant
PRICE_SIGNIFICANT = float(os.environ.get("PRICE_SIGNIFICANT", "0.01"))  # 1% price change = significant
API_TIMEOUT       = int(os.environ.get("API_TIMEOUT",   "10"))

def _to_bingx_symbol(pair):
    base = pair.replace("USDT", "")
    return f"{base}-USDT"

def get_current_oi(pair):
    """Fetches current OI snapshot from BingX."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/openInterest"
    resp = requests.get(url, params={"symbol": _to_bingx_symbol(pair)}, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(data.get("msg", "OI unavailable"))
    return float(data["data"]["openInterest"])

def get_klines(pair):
    """Fetches recent klines from BingX for price + volume analysis."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/klines"
    params = {
        "symbol": _to_bingx_symbol(pair),
        "interval": OI_INTERVAL,
        "limit": OI_WINDOW + 1
    }
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["data"]

def evaluate_open_interest(pair="BTC"):
    """
    Validates directional bias using current OI level + volume-confirmed price move.

    Price up + high volume → real buying demand → LONG validated
    Price down + high volume → real selling pressure → SHORT validated
    Price move + low volume → weak conviction, signal is less reliable
    """
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating open interest for {pair}...", file=sys.stderr)

    candles = get_klines(pair)
    if len(candles) < 2:
        return {"error": "Insufficient price data"}

    oi_usd = get_current_oi(pair)

    # Price and volume over window
    price_start = float(candles[0]["close"])
    price_end   = float(candles[-1]["close"])
    price_change_pct = (price_end - price_start) / price_start * 100

    volumes = [float(c["volume"]) for c in candles]
    avg_volume   = sum(volumes) / len(volumes)
    recent_volume = volumes[-1]
    volume_ratio  = recent_volume / avg_volume if avg_volume > 0 else 1.0

    price_up = price_change_pct > 0
    high_volume = volume_ratio >= 1.0

    price_significant = abs(price_change_pct) >= PRICE_SIGNIFICANT * 100

    if price_up and high_volume:
        signal = "LONG"
        strength = "strong" if price_significant else "moderate"
        interpretation = "Price rising with above-average volume — buying demand validated."
        note = "Participation backs the move: volume confirms real demand on the long side."
    elif not price_up and high_volume:
        signal = "SHORT"
        strength = "strong" if price_significant else "moderate"
        interpretation = "Price falling with above-average volume — selling pressure validated."
        note = "Participation backs the move: volume confirms real pressure on the short side."
    elif price_up and not high_volume:
        signal = "LONG"
        strength = "weak"
        interpretation = "Price rising but volume below average — low conviction move."
        note = "Less reliable: price move is not backed by strong participation."
    else:
        signal = "SHORT"
        strength = "weak"
        interpretation = "Price falling but volume below average — low conviction move."
        note = "Less reliable: selling not backed by strong participation."

    window_label = f"{OI_WINDOW}×{OI_INTERVAL}"
    reasoning = "\n".join([
        f"Price change ({window_label}): {price_change_pct:+.2f}%  (${price_start:,.0f} → ${price_end:,.0f})",
        f"Volume ratio (last vs avg):  {volume_ratio:.2f}x",
        f"Current OI: ${oi_usd:,.0f}",
        "",
        interpretation,
        note,
        "",
        f"Signal: {signal} ({strength})",
    ])

    return {
        "pair": pair,
        "window": window_label,
        "price_change_pct": round(price_change_pct, 2),
        "volume_ratio":     round(volume_ratio, 2),
        "oi_usd":           round(oi_usd, 0),
        "price_direction":  "up" if price_up else "down",
        "high_volume":      high_volume,
        "signal":           signal,
        "strength":         strength,
        "interpretation":   interpretation,
        "reasoning":        reasoning
    }

if __name__ == "__main__":
    pair = "BTC"

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]

    result = evaluate_open_interest(pair)
    print(json.dumps(result, indent=2))
