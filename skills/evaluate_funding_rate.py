#!/usr/bin/env python3
"""
Skill: Evaluate Funding Rate
Analyzes the perpetual funding rate and its contrarian implications for the given pair.
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
# Funding rate thresholds (expressed as decimal, e.g. 0.001 = 0.1% per 8h)
FUNDING_VERY_HIGH = float(os.environ.get("FUNDING_VERY_HIGH", "0.001"))   # > this → crowded long,  strong SHORT warning
FUNDING_HIGH      = float(os.environ.get("FUNDING_HIGH",      "0.0003"))  # > this → elevated long, mild SHORT bias
FUNDING_LOW       = float(os.environ.get("FUNDING_LOW",       "-0.0003")) # < this → elevated short, mild LONG bias
FUNDING_VERY_LOW  = float(os.environ.get("FUNDING_VERY_LOW",  "-0.001"))  # < this → crowded short, strong LONG warning
API_TIMEOUT       = int(os.environ.get("API_TIMEOUT",          "10"))

def _to_bingx_symbol(pair):
    base = pair.replace("USDT", "")
    return f"{base}-USDT"

def get_funding_rate(pair):
    """Fetches current funding rate from BingX."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"
    params = {"symbol": _to_bingx_symbol(pair)}
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return float(data["data"]["lastFundingRate"])

def evaluate_funding_rate(pair="BTC"):
    """
    Evaluates funding rate as a contrarian filter.

    High positive funding → crowded long → bearish warning (contrarian SHORT)
    High negative funding → crowded short → bullish warning (contrarian LONG)
    Neutral funding → no crowding signal
    """
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating funding rate for {pair}...", file=sys.stderr)

    funding_rate = get_funding_rate(pair)
    funding_pct = funding_rate * 100  # for display

    if funding_rate > FUNDING_VERY_HIGH:
        status = "very_positive"
        contrarian_bias = "SHORT"
        confidence = "high"
        warning = "Crowded long — high risk of long squeeze"
        detail = (f"Funding at {funding_pct:.4f}%/8h is very elevated. "
                  f"Longs are paying a high premium to hold positions. "
                  f"Contrarian signal: avoid new longs, short squeeze risk is low but long squeeze risk is high.")
    elif funding_rate > FUNDING_HIGH:
        status = "positive"
        contrarian_bias = "SHORT"
        confidence = "moderate"
        warning = "Elevated long interest — mild crowding"
        detail = (f"Funding at {funding_pct:.4f}%/8h shows elevated long interest. "
                  f"Use as a filter: avoid chasing longs, the trade may be crowded.")
    elif funding_rate < FUNDING_VERY_LOW:
        status = "very_negative"
        contrarian_bias = "LONG"
        confidence = "high"
        warning = "Crowded short — high risk of short squeeze"
        detail = (f"Funding at {funding_pct:.4f}%/8h is very negative. "
                  f"Shorts are paying a high premium to hold positions. "
                  f"Contrarian signal: avoid new shorts, short squeeze risk is high.")
    elif funding_rate < FUNDING_LOW:
        status = "negative"
        contrarian_bias = "LONG"
        confidence = "moderate"
        warning = "Elevated short interest — mild crowding"
        detail = (f"Funding at {funding_pct:.4f}%/8h shows elevated short interest. "
                  f"Use as a filter: avoid chasing shorts, the trade may be crowded.")
    else:
        status = "neutral"
        contrarian_bias = "NEUTRAL"
        confidence = "low"
        warning = None
        detail = (f"Funding at {funding_pct:.4f}%/8h is within normal range. "
                  f"No crowding signal — funding is not a meaningful filter here.")

    reasoning_lines = [
        f"Funding Rate: {funding_pct:.4f}%/8h",
        f"Status: {status.upper()}",
        "",
        detail,
        "",
        f"Contrarian Bias: {contrarian_bias}",
        f"Confidence: {confidence.upper()}",
    ]
    if warning:
        reasoning_lines.append(f"Warning: {warning}")

    return {
        "pair": pair,
        "funding_rate": round(funding_rate, 6),
        "funding_rate_pct": round(funding_pct, 4),
        "status": status,
        "contrarian_bias": contrarian_bias,
        "confidence": confidence,
        "warning": warning,
        "reasoning": "\n".join(reasoning_lines)
    }

if __name__ == "__main__":
    pair = "BTC"

    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]

    result = evaluate_funding_rate(pair)
    print(json.dumps(result, indent=2))
