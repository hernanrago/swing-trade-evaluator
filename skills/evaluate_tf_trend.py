#!/usr/bin/env python3
"""
Skill: Evaluate TF Trend
Analyzes 1D and 1W trend, recommends LONG or SHORT direction.
No parameters except the pair.
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
MA_SHORT_PERIOD  = int(os.environ.get("MA_SHORT_PERIOD",  "50"))
MA_LONG_PERIOD   = int(os.environ.get("MA_LONG_PERIOD",   "200"))
KLINES_LIMIT_1D  = int(os.environ.get("KLINES_LIMIT_1D",  "200"))
KLINES_LIMIT_1W  = int(os.environ.get("KLINES_LIMIT_1W",  "52"))
API_TIMEOUT      = int(os.environ.get("API_TIMEOUT",       "10"))

def _to_bingx_symbol(pair):
    """Converts BTCUSDT → BTC-USDT for BingX."""
    base = pair.replace("USDT", "")
    return f"{base}-USDT"

def fetch_klines(pair="BTCUSDT", interval="1d", limit=200):
    """Fetches OHLCV data from BingX."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/klines"
    params = {
        "symbol": _to_bingx_symbol(pair),
        "interval": interval,
        "limit": limit,
    }
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return [
        [int(c["time"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c["volume"])]
        for c in data["data"]
    ]

def calculate_trend(candles, min_required=None):
    """Analyzes candles and determines trend."""
    if min_required is None:
        min_required = MA_SHORT_PERIOD

    if not candles:
        return {"error": "No candles"}

    closes = [float(c[4]) for c in candles]

    if len(closes) < min_required:
        return {"error": f"Need at least {min_required} candles, got {len(closes)}"}

    # Moving averages
    ma_short = sum(closes[-MA_SHORT_PERIOD:]) / MA_SHORT_PERIOD if len(closes) >= MA_SHORT_PERIOD else sum(closes) / len(closes)
    ma_long  = sum(closes[-MA_LONG_PERIOD:])  / MA_LONG_PERIOD  if len(closes) >= MA_LONG_PERIOD  else sum(closes) / len(closes)

    current_price = closes[-1]

    # Determines trend
    if current_price > ma_short > ma_long:
        trend = "bullish"
        strength = "strong"
    elif current_price > ma_long and current_price > ma_short:
        trend = "bullish"
        strength = "moderate"
    elif current_price < ma_short < ma_long:
        trend = "bearish"
        strength = "strong"
    elif current_price < ma_long and current_price < ma_short:
        trend = "bearish"
        strength = "moderate"
    else:
        trend = "sideways"
        strength = "weak"

    return {
        "current_price": round(current_price, 2),
        f"ma{MA_SHORT_PERIOD}": round(ma_short, 2),
        f"ma{MA_LONG_PERIOD}": round(ma_long, 2),
        "trend": trend,
        "strength": strength,
        f"price_above_ma{MA_SHORT_PERIOD}": current_price > ma_short,
        f"price_above_ma{MA_LONG_PERIOD}": current_price > ma_long,
        f"ma{MA_SHORT_PERIOD}_above_ma{MA_LONG_PERIOD}": ma_short > ma_long,
    }

def evaluate_tf_trend(pair="BTC"):
    """
    Analyzes 1D/1W trend and recommends direction.
    Input: pair (e.g., BTC)
    Output: recommended direction (LONG or SHORT)
    """
    
    # Normalize pair
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"
    
    print(f"[*] Evaluating trend for {pair}...", file=sys.stderr)
    print(f"[*] Fetching 1D data...", file=sys.stderr)
    candles_1d = fetch_klines(pair, "1d", KLINES_LIMIT_1D)

    print(f"[*] Fetching 1W data...", file=sys.stderr)
    candles_1w = fetch_klines(pair, "1w", KLINES_LIMIT_1W)

    # Analyze trends
    trend_1d = calculate_trend(candles_1d, min_required=MA_SHORT_PERIOD)
    trend_1w = calculate_trend(candles_1w, min_required=max(MA_SHORT_PERIOD // 2, 20))
    
    if "error" in trend_1d or "error" in trend_1w:
        return {"error": f"Calculation error - 1D: {trend_1d.get('error')}, 1W: {trend_1w.get('error')}"}
    
    # Determine recommendation based on both timeframes
    bullish_1d = trend_1d["trend"] == "bullish"
    bullish_1w = trend_1w["trend"] == "bullish"
    
    # Recommendation logic:
    # - If both TFs are bullish → LONG (high confidence)
    # - If both TFs are bearish → SHORT (high confidence)
    # - If mixed → follow 1D (moderate confidence)
    # - Sideways → LONG (low confidence, wait for clarity)

    if bullish_1d and bullish_1w:
        recommended_direction = "LONG"
        confidence = "high"
        bias = "strong bullish bias on both timeframes"
    elif not bullish_1d and not bullish_1w:
        recommended_direction = "SHORT"
        confidence = "high"
        bias = "strong bearish bias on both timeframes"
    elif bullish_1d:
        recommended_direction = "LONG"
        confidence = "moderate"
        bias = "1D bullish but 1W mixed"
    else:
        recommended_direction = "SHORT"
        confidence = "moderate"
        bias = "1W bearish but 1D mixed"
    
    # Build reasoning
    reasoning = f"""
1D Trend: {trend_1d['trend'].upper()} ({trend_1d['strength']})
  Price: {trend_1d['current_price']} | MA{MA_SHORT_PERIOD}: {trend_1d[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_1d[f'ma{MA_LONG_PERIOD}']}

1W Trend: {trend_1w['trend'].upper()} ({trend_1w['strength']})
  Price: {trend_1w['current_price']} | MA{MA_SHORT_PERIOD}: {trend_1w[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_1w[f'ma{MA_LONG_PERIOD}']}

Recommendation: {recommended_direction}
Confidence: {confidence.upper()}
Bias: {bias}
""".strip()
    
    return {
        "pair": pair,
        "recommended_direction": recommended_direction,
        "confidence": confidence,
        "trend_1d": trend_1d,
        "trend_1w": trend_1w,
        "reasoning": reasoning
    }

if __name__ == "__main__":
    pair = "BTC"
    
    # Get pair from command line if provided
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]
    
    result = evaluate_tf_trend(pair)
    print(json.dumps(result, indent=2))
