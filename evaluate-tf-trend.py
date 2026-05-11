#!/usr/bin/env python3
"""
Skill: Evaluate TF Trend
Analyzes 1D and 1W trend, recommends LONG or SHORT direction.
No parameters except the pair.
"""

import json
import sys
import requests

def _to_yahoo_symbol(pair):
    """Converts BTCUSDT → BTC-USD for Yahoo Finance."""
    base = pair.replace("USDT", "").replace("USD", "")
    return f"{base}-USD"

def _to_yahoo_interval(interval):
    """Converts Binance interval to Yahoo Finance interval and range."""
    if interval == "1d":
        return "1d", "1y"
    if interval == "1w":
        return "1wk", "2y"
    return "1d", "1y"

def fetch_klines(pair="BTCUSDT", interval="1d", limit=200):
    """Fetches OHLCV data from Yahoo Finance, returned in Binance candle format."""
    symbol = _to_yahoo_symbol(pair)
    yf_interval, yf_range = _to_yahoo_interval(interval)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    params = {"interval": yf_interval, "range": yf_range}
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    candles = [
        [
            ts * 1000,
            o, h, l, c, v
        ]
        for ts, o, h, l, c, v in zip(
            timestamps,
            quote["open"], quote["high"], quote["low"], quote["close"], quote["volume"]
        )
        if None not in (o, h, l, c)
    ]
    return candles[-limit:]

def calculate_trend(candles, min_required=50):
    """Analyzes candles and determines trend."""
    if not candles:
        return {"error": "No candles"}
    
    closes = [float(c[4]) for c in candles]
    
    if len(closes) < min_required:
        return {"error": f"Need at least {min_required} candles, got {len(closes)}"}
    
    # Moving averages
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
    
    current_price = closes[-1]
    
    # Determines trend
    if current_price > ma50 > ma200:
        trend = "bullish"
        strength = "strong"
    elif current_price > ma200 and current_price > ma50:
        trend = "bullish"
        strength = "moderate"
    elif current_price < ma50 < ma200:
        trend = "bearish"
        strength = "strong"
    elif current_price < ma200 and current_price < ma50:
        trend = "bearish"
        strength = "moderate"
    else:
        trend = "sideways"
        strength = "weak"
    
    return {
        "current_price": round(current_price, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "trend": trend,
        "strength": strength,
        "price_above_ma50": current_price > ma50,
        "price_above_ma200": current_price > ma200,
        "ma50_above_ma200": ma50 > ma200
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
    candles_1d = fetch_klines(pair, "1d", 200)
    
    print(f"[*] Fetching 1W data...", file=sys.stderr)
    candles_1w = fetch_klines(pair, "1w", 52)
    
    # Analyze trends
    trend_1d = calculate_trend(candles_1d, min_required=50)
    trend_1w = calculate_trend(candles_1w, min_required=20)
    
    if "error" in trend_1d or "error" in trend_1w:
        return {"error": f"Calculation error - 1D: {trend_1d.get('error')}, 1W: {trend_1w.get('error')}"}
    
    # Determine recommendation based on both timeframes
    bullish_1d = trend_1d["trend"] == "bullish"
    bullish_1w = trend_1w["trend"] == "bullish"
    
    # Recommendation logic:
    # - If both TFs are bullish → LONG (high confidence)
    # - If both TFs are bearish → SHORT (high confidence)
    # - If mixed → LONG by default (moderate confidence)
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
  Price: {trend_1d['current_price']} | MA50: {trend_1d['ma50']} | MA200: {trend_1d['ma200']}
  Price > MA50: {trend_1d['price_above_ma50']} | Price > MA200: {trend_1d['price_above_ma200']}

1W Trend: {trend_1w['trend'].upper()} ({trend_1w['strength']})
  Price: {trend_1w['current_price']} | MA50: {trend_1w['ma50']} | MA200: {trend_1w['ma200']}
  Price > MA50: {trend_1w['price_above_ma50']} | Price > MA200: {trend_1w['price_above_ma200']}

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
