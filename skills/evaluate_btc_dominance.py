#!/usr/bin/env python3
"""
Skill: Evaluate BTC Dominance
Analyzes Bitcoin dominance trend and its impact on the given pair.
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
# Dominance trend thresholds (%)
BTC_DOM_STRONG_HIGH  = float(os.environ.get("BTC_DOM_STRONG_HIGH",  "55"))  # > this → strong_increasing
BTC_DOM_MODERATE_HIGH = float(os.environ.get("BTC_DOM_MODERATE_HIGH", "50")) # > this → increasing
BTC_DOM_MODERATE_LOW  = float(os.environ.get("BTC_DOM_MODERATE_LOW",  "45")) # > this → decreasing, else strong_decreasing
# Confidence thresholds (%)
BTC_DOM_CONF_HIGH_HI = float(os.environ.get("BTC_DOM_CONF_HIGH_HI", "60"))  # > this → high confidence
BTC_DOM_CONF_HIGH_LO = float(os.environ.get("BTC_DOM_CONF_HIGH_LO", "35"))  # < this → high confidence
BTC_DOM_CONF_MOD_HI  = float(os.environ.get("BTC_DOM_CONF_MOD_HI",  "55"))  # > this → moderate confidence
BTC_DOM_CONF_MOD_LO  = float(os.environ.get("BTC_DOM_CONF_MOD_LO",  "40"))  # < this → moderate confidence
API_TIMEOUT          = int(os.environ.get("API_TIMEOUT",              "10"))
COINGECKO_API_KEY    = os.environ.get("COINGECKO_API_KEY",            "")

def get_btc_dominance():
    """Fetches BTC dominance data from CoinGecko."""
    url = "https://api.coingecko.com/api/v3/global"
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
    resp = requests.get(url, headers=headers, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data["data"]["market_cap_percentage"]["btc"]

def estimate_trend(current_dominance):
    if current_dominance > BTC_DOM_STRONG_HIGH:
        return "strong_increasing"
    elif current_dominance > BTC_DOM_MODERATE_HIGH:
        return "increasing"
    elif current_dominance > BTC_DOM_MODERATE_LOW:
        return "decreasing"
    else:
        return "strong_decreasing"

def evaluate_btc_dominance(pair="BTC"):
    """
    Evaluates BTC dominance and its impact on the given pair.
    
    For BTC: High/rising dominance = bullish (money flowing to BTC)
    For altcoins: High/rising dominance = bearish (money flowing away from altcoins)
    """
    
    pair = pair.upper()
    
    print(f"[*] Evaluating BTC dominance impact for {pair}...", file=sys.stderr)
    
    # Fetch current BTC dominance
    btc_dominance = get_btc_dominance()
    
    if btc_dominance is None:
        return {"error": "Could not fetch BTC dominance data"}
    
    # Estimate trend
    trend = estimate_trend(btc_dominance)
    
    # Determine if dominance supports LONG or SHORT for this pair
    is_btc = pair == "BTC"
    is_increasing = "increasing" in trend
    
    # Logic:
    # - BTC + increasing dominance = LONG (money flowing in)
    # - BTC + decreasing dominance = SHORT (money flowing out)
    # - Altcoin + increasing dominance = SHORT (money flowing to BTC, away from altcoins)
    # - Altcoin + decreasing dominance = LONG (money flowing to altcoins)
    
    reasoning_base = f"BTC dominance is {trend.replace('_', ' ')} ({btc_dominance:.2f}%)"
    if is_btc:
        supports_long = is_increasing
        reasoning_detail = "Money flowing INTO Bitcoin = bullish for BTC" if is_increasing else "Money flowing OUT OF Bitcoin = bearish for BTC"
    else:
        supports_long = not is_increasing
        reasoning_detail = f"Money flowing to BTC (away from {pair}) = bearish for {pair}" if is_increasing else f"Money flowing to altcoins (including {pair}) = bullish for {pair}"
    
    # Confidence based on how extreme the dominance is
    if btc_dominance > BTC_DOM_CONF_HIGH_HI or btc_dominance < BTC_DOM_CONF_HIGH_LO:
        confidence = "high"
    elif btc_dominance > BTC_DOM_CONF_MOD_HI or btc_dominance < BTC_DOM_CONF_MOD_LO:
        confidence = "moderate"
    else:
        confidence = "low"
    
    recommended_direction = "LONG" if supports_long else "SHORT"

    reasoning = f"""
BTC Dominance: {btc_dominance:.2f}%
Trend: {trend.upper()}
Impact on {pair}: {reasoning_base}

{reasoning_detail}

Recommendation: {recommended_direction}
Confidence: {confidence.upper()}
""".strip()

    return {
        "pair": pair,
        "btc_dominance": round(btc_dominance, 2),
        "trend": trend,
        "recommended_direction": recommended_direction,
        "confidence": confidence,
        "reasoning": reasoning
    }

if __name__ == "__main__":
    pair = "BTC"
    
    # Get pair from command line if provided
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]
    
    result = evaluate_btc_dominance(pair)
    print(json.dumps(result, indent=2))