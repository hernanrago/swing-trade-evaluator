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

def evaluate_tf_trend(pair="BTC", context=None):
    """
    Analyzes trend and recommends direction.
    In swing mode: uses 1D (fast) and 1W (slow) timeframes.
    In intraday mode: uses 4H (fast) and 1H (slow) timeframes.
    Reads TRADE_MODE from os.environ at call time.
    """
    trade_mode = os.environ.get("TRADE_MODE", "swing")

    if trade_mode == "intraday":
        fast_tf, fast_limit, fast_label = "4h", KLINES_LIMIT_1D, "4H"
        slow_tf, slow_limit, slow_label = "1h", KLINES_LIMIT_1D, "1H"
    else:
        fast_tf, fast_limit, fast_label = "1d", KLINES_LIMIT_1D, "1D"
        slow_tf, slow_limit, slow_label = "1w", KLINES_LIMIT_1W, "1W"

    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating trend for {pair} (mode={trade_mode})...", file=sys.stderr)
    print(f"[*] Fetching {fast_label} data...", file=sys.stderr)
    candles_fast = fetch_klines(pair, fast_tf, fast_limit)

    print(f"[*] Fetching {slow_label} data...", file=sys.stderr)
    candles_slow = fetch_klines(pair, slow_tf, slow_limit)

    trend_fast = calculate_trend(candles_fast, min_required=MA_SHORT_PERIOD)
    trend_slow = calculate_trend(candles_slow, min_required=max(MA_SHORT_PERIOD // 2, 20))

    if "error" in trend_fast or "error" in trend_slow:
        return {"error": f"Calculation error - {fast_label}: {trend_fast.get('error')}, {slow_label}: {trend_slow.get('error')}"}

    bullish_fast = trend_fast["trend"] == "bullish"
    bullish_slow = trend_slow["trend"] == "bullish"

    if bullish_fast and bullish_slow:
        recommended_direction = "LONG"
        confidence = "high"
        bias = "strong bullish bias on both timeframes"
    elif not bullish_fast and not bullish_slow:
        recommended_direction = "SHORT"
        confidence = "high"
        bias = "strong bearish bias on both timeframes"
    elif bullish_fast:
        recommended_direction = "LONG"
        confidence = "moderate"
        bias = f"{fast_label} bullish but {slow_label} mixed"
    else:
        recommended_direction = "SHORT"
        confidence = "moderate"
        bias = f"{slow_label} bearish but {fast_label} mixed"

    reasoning = f"""
{fast_label} Trend: {trend_fast['trend'].upper()} ({trend_fast['strength']})
  Price: {trend_fast['current_price']} | MA{MA_SHORT_PERIOD}: {trend_fast[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_fast[f'ma{MA_LONG_PERIOD}']}

{slow_label} Trend: {trend_slow['trend'].upper()} ({trend_slow['strength']})
  Price: {trend_slow['current_price']} | MA{MA_SHORT_PERIOD}: {trend_slow[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_slow[f'ma{MA_LONG_PERIOD}']}

Recommendation: {recommended_direction}
Confidence: {confidence.upper()}
Bias: {bias}
""".strip()

    fast_key = f"trend_{fast_label.lower()}"
    slow_key = f"trend_{slow_label.lower()}"

    return {
        "pair": pair,
        "recommended_direction": recommended_direction,
        "confidence": confidence,
        fast_key: trend_fast,
        slow_key: trend_slow,
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
