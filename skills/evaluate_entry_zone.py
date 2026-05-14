#!/usr/bin/env python3
"""
Skill: Evaluate Entry Zone
Checks whether a technically valid, risk-manageable entry zone exists.
"""

import os
import json
import sys
import argparse
from statistics import mean

import requests

try:
    from evaluate_market_structure import evaluate_market_structure
    _MS_AVAILABLE = True
except ImportError as e:
    evaluate_market_structure = None
    _MS_AVAILABLE = False

# --- Config (override via environment variables) ---
EZ_LOOKBACK_4H = int(os.environ.get("EZ_LOOKBACK_4H", "220"))
EZ_LOOKBACK_1D = int(os.environ.get("EZ_LOOKBACK_1D", "260"))
EZ_PIVOT_N_4H = int(os.environ.get("EZ_PIVOT_N_4H", "3"))
EZ_PIVOT_N_1D = int(os.environ.get("EZ_PIVOT_N_1D", "5"))
EZ_ZONE_TOO_WIDE_PCT = float(os.environ.get("EZ_ZONE_TOO_WIDE_PCT", "1.8"))
EZ_CHASING_PCT = float(os.environ.get("EZ_CHASING_PCT", "2.5"))
EZ_APPROACHING_MAX_PCT = float(os.environ.get("EZ_APPROACHING_MAX_PCT", "2.0"))
EZ_FAR_MIN_PCT = float(os.environ.get("EZ_FAR_MIN_PCT", "3.0"))
EZ_MERGE_EPS_PCT_4H = float(os.environ.get("EZ_MERGE_EPS_PCT_4H", "0.15"))
EZ_MERGE_EPS_PCT_1D = float(os.environ.get("EZ_MERGE_EPS_PCT_1D", "0.25"))
EZ_MAX_MERGE_WIDTH_PCT = float(os.environ.get("EZ_MAX_MERGE_WIDTH_PCT", "2.5"))
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "10"))

OKX_API = "https://www.okx.com"

_CANDLES_CACHE = {}


def _to_okx_swap(pair):
    base = pair.upper().replace("-SWAP", "").replace("-USDT", "").replace("USDT", "")
    base = base.replace("-", "").replace("_", "")
    return f"{base}-USDT-SWAP", base


def _get_candles(instrument, bar, limit):
    cache_key = (instrument, bar, limit)
    if cache_key in _CANDLES_CACHE:
        return _CANDLES_CACHE[cache_key]
    url = f"{OKX_API}/api/v5/market/candles"
    params = {"instId": instrument, "bar": bar, "limit": limit}
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error: {data.get('msg')}")
    result = list(reversed(data["data"]))
    _CANDLES_CACHE[cache_key] = result
    return result


def _normalize_zone(min_p, max_p, ztype, direction, timeframe, strength, freshness, touches, reason, metadata=None):
    min_v = float(min(min_p, max_p))
    max_v = float(max(min_p, max_p))
    mid_v = (min_v + max_v) / 2.0
    return {
        "min": round(min_v, 2),
        "max": round(max_v, 2),
        "mid": round(mid_v, 2),
        "type": ztype,
        "direction": direction,
        "timeframe": timeframe,
        "strength": float(max(0.0, min(1.0, strength))),
        "freshness": float(max(0.0, min(1.0, freshness))),
        "touch_count": int(max(0, touches)),
        "reason": reason,
        "metadata": metadata or {},
    }


def _find_pivots(candles, n):
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    out = []
    for i in range(n, len(candles) - n):
        h = highs[i]
        l = lows[i]
        if h == max(highs[i - n:i + n + 1]):
            out.append(("H", i, h))
        if l == min(lows[i - n:i + n + 1]):
            out.append(("L", i, l))
    return out


def detect_support_resistance_zones(candles, direction, timeframe, pivot_n):
    pivots = _find_pivots(candles, pivot_n)
    levels = []
    prices = [float(c[4]) for c in candles]
    avg_price = mean(prices[-80:]) if len(prices) >= 80 else mean(prices)
    cluster_eps = avg_price * (0.003 if timeframe == "4H" else 0.005)

    for typ, _, p in pivots:
        levels.append(("resistance" if typ == "H" else "support", p))

    used = [False] * len(levels)
    zones = []
    for i, (lvl_type, p) in enumerate(levels):
        if used[i]:
            continue
        cluster = [p]
        used[i] = True
        for j in range(i + 1, len(levels)):
            if used[j] or levels[j][0] != lvl_type:
                continue
            if abs(levels[j][1] - p) <= cluster_eps:
                cluster.append(levels[j][1])
                used[j] = True
        if len(cluster) < 2:
            continue
        mn, mx = min(cluster), max(cluster)
        zone_dir = "LONG" if lvl_type == "support" else "SHORT"
        if zone_dir != direction:
            continue
        strength = min(1.0, 0.45 + 0.08 * len(cluster))
        fresh_idx = max(idx for t, idx, price in pivots if ((lvl_type == "resistance" and t == "H") or (lvl_type == "support" and t == "L")) and mn - cluster_eps <= price <= mx + cluster_eps)
        freshness = max(0.1, 1.0 - (len(candles) - 1 - fresh_idx) / max(1, len(candles)))
        zones.append(_normalize_zone(
            mn - cluster_eps * 0.35,
            mx + cluster_eps * 0.35,
            lvl_type,
            direction,
            timeframe,
            strength,
            freshness,
            len(cluster),
            f"Clustered {lvl_type} from {len(cluster)} pivot touches",
        ))
    return zones


def detect_fair_value_gaps(candles, direction, timeframe):
    out = []
    n = len(candles)
    for i in range(2, n):
        c1 = candles[i - 2]
        c3 = candles[i]
        h1, l1 = float(c1[2]), float(c1[3])
        h3, l3 = float(c3[2]), float(c3[3])
        if direction == "LONG" and h1 < l3:
            gap_min, gap_max = h1, l3
            close_now = float(candles[-1][4])
            fill = (close_now - gap_min) / (gap_max - gap_min) if gap_max > gap_min else 1.0
            if fill >= 1.05:
                continue
            freshness = max(0.1, 1.0 - (n - 1 - i) / n)
            out.append(_normalize_zone(gap_min, gap_max, "fvg", direction, timeframe, 0.66, freshness, 0,
                                       "Bullish 3-candle imbalance", {"fill_ratio": max(0.0, min(1.5, fill))}))
        if direction == "SHORT" and l1 > h3:
            gap_min, gap_max = h3, l1
            close_now = float(candles[-1][4])
            fill = (gap_max - close_now) / (gap_max - gap_min) if gap_max > gap_min else 1.0
            if fill >= 1.05:
                continue
            freshness = max(0.1, 1.0 - (n - 1 - i) / n)
            out.append(_normalize_zone(gap_min, gap_max, "fvg", direction, timeframe, 0.66, freshness, 0,
                                       "Bearish 3-candle imbalance", {"fill_ratio": max(0.0, min(1.5, fill))}))
    return out[-6:]


def detect_order_blocks(candles, direction, timeframe):
    out = []
    n = len(candles)
    for i in range(2, n - 2):
        o = float(candles[i][1]); h = float(candles[i][2]); l = float(candles[i][3]); c = float(candles[i][4])
        nxt = float(candles[i + 1][4]); nxt2 = float(candles[i + 2][4])
        if direction == "LONG" and c < o and nxt2 > c * 1.01:
            invalid = l
            out.append(_normalize_zone(l, h, "order_block", direction, timeframe, 0.72,
                                       max(0.1, 1.0 - (n - 1 - i) / n), 0,
                                       "Bullish OB before bullish displacement",
                                       {"invalidation_level": invalid, "bos_confirmed": nxt > h}))
        if direction == "SHORT" and c > o and nxt2 < c * 0.99:
            invalid = h
            out.append(_normalize_zone(l, h, "order_block", direction, timeframe, 0.72,
                                       max(0.1, 1.0 - (n - 1 - i) / n), 0,
                                       "Bearish OB before bearish displacement",
                                       {"invalidation_level": invalid, "bos_confirmed": nxt < l}))
    return out[-5:]


def calculate_fibonacci_retracements(candles, direction, timeframe, pivot_n):
    pivots = _find_pivots(candles, pivot_n)
    highs = [(i, p) for t, i, p in pivots if t == "H"]
    lows = [(i, p) for t, i, p in pivots if t == "L"]
    if len(highs) < 1 or len(lows) < 1:
        return []

    if direction == "LONG":
        low_i, low_p = max(lows, key=lambda x: x[0])
        high_candidates = [x for x in highs if x[0] > low_i]
        if not high_candidates:
            return []
        _, high_p = high_candidates[-1]
        diff = high_p - low_p
        if diff <= 0:
            return []
        zmin = high_p - diff * 0.618
        zmax = high_p - diff * 0.382
    else:
        high_i, high_p = max(highs, key=lambda x: x[0])
        low_candidates = [x for x in lows if x[0] > high_i]
        if not low_candidates:
            return []
        _, low_p = low_candidates[-1]
        diff = high_p - low_p
        if diff <= 0:
            return []
        zmin = low_p + diff * 0.382
        zmax = low_p + diff * 0.618

    return [_normalize_zone(zmin, zmax, "fibonacci", direction, timeframe, 0.58, 0.85, 0,
                            "0.382-0.618 retracement zone")]


def detect_range_levels(candles, direction, timeframe):
    recent = candles[-40:] if len(candles) >= 40 else candles
    highs = [float(c[2]) for c in recent]
    lows = [float(c[3]) for c in recent]
    r_high = max(highs)
    r_low = min(lows)
    width = (r_high - r_low) / r_low * 100 if r_low > 0 else 0
    if width > 8.0:
        return []
    span = (r_high - r_low) * 0.12
    if direction == "LONG":
        return [_normalize_zone(r_low, r_low + span, "range_level", direction, timeframe, 0.5, 0.7, 1,
                                "Range low area")]
    return [_normalize_zone(r_high - span, r_high, "range_level", direction, timeframe, 0.5, 0.7, 1,
                            "Range high area")]


def detect_breakout_retests(candles, direction, timeframe, pivot_n):
    pivots = _find_pivots(candles, pivot_n)
    closes = [float(c[4]) for c in candles]
    out = []
    for t, i, p in pivots[-25:]:
        if direction == "LONG" and t == "H":
            later = closes[i + 1:]
            if not later:
                continue
            broke = any(x > p * 1.001 for x in later)
            retested = any(abs(x - p) / p <= 0.003 for x in later[-16:])
            if broke and retested:
                out.append(_normalize_zone(p * 0.9985, p * 1.0015, "breakout_retest", direction, timeframe,
                                           0.63, 0.75, 1, "Broken resistance retested as support"))
        if direction == "SHORT" and t == "L":
            later = closes[i + 1:]
            if not later:
                continue
            broke = any(x < p * 0.999 for x in later)
            retested = any(abs(x - p) / p <= 0.003 for x in later[-16:])
            if broke and retested:
                out.append(_normalize_zone(p * 0.9985, p * 1.0015, "breakout_retest", direction, timeframe,
                                           0.63, 0.75, 1, "Broken support retested as resistance"))
    return out[-4:]


def detect_liquidity_sweeps(candles, direction, timeframe):
    if len(candles) < 8:
        return []
    out = []
    prev = candles[-8:-1]
    last = candles[-1]
    prev_high = max(float(c[2]) for c in prev)
    prev_low = min(float(c[3]) for c in prev)
    lo = float(last[3]); hi = float(last[2]); close = float(last[4]); opn = float(last[1])
    if direction == "LONG" and lo < prev_low and close > prev_low:
        out.append(_normalize_zone(prev_low * 0.998, prev_low * 1.0015, "liquidity_sweep", direction, timeframe,
                                   0.67, 0.95, 1, "Sweep below prior low with reclaim"))
    if direction == "SHORT" and hi > prev_high and close < prev_high:
        out.append(_normalize_zone(prev_high * 0.9985, prev_high * 1.002, "liquidity_sweep", direction, timeframe,
                                   0.67, 0.95, 1, "Sweep above prior high with rejection"))
    if direction == "LONG" and close > opn and (close - lo) > (hi - close):
        pass
    return out


def derive_market_structure_zone(ms, direction):
    out = []
    for tf in ("4h", "1d"):
        tfv = ms.get(tf, {})
        if tfv.get("structure") not in ("LONG", "SHORT"):
            continue
        if tfv.get("structure") != direction:
            continue
        inv = tfv.get("invalidation")
        last_sl = tfv.get("last_swing_low")
        last_sh = tfv.get("last_swing_high")
        if direction == "LONG" and last_sl and last_sh and last_sh > last_sl:
            span = (last_sh - last_sl) * 0.2
            out.append(_normalize_zone(last_sl + span * 0.2, last_sl + span, "market_structure", direction,
                                       tf.upper(), 0.7, 0.8, 1, "Higher-low structure zone",
                                       {"invalidation_level": inv}))
        if direction == "SHORT" and last_sl and last_sh and last_sh > last_sl:
            span = (last_sh - last_sl) * 0.2
            out.append(_normalize_zone(last_sh - span, last_sh - span * 0.2, "market_structure", direction,
                                       tf.upper(), 0.7, 0.8, 1, "Lower-high structure zone",
                                       {"invalidation_level": inv}))
    return out


def _zone_distance_pct(current_price, zone):
    if zone["min"] <= current_price <= zone["max"]:
        return 0.0
    edge = zone["max"] if current_price > zone["max"] else zone["min"]
    return abs(current_price - edge) / current_price * 100 if current_price > 0 else 0


def merge_overlapping_zones(zones):
    if not zones:
        return []
    zones = sorted(zones, key=lambda z: (z["direction"], z["timeframe"], z["min"]))
    merged = []

    for z in zones:
        placed = False
        for m in merged:
            if m["direction"] != z["direction"]:
                continue
            has_1d = ("1D" in m["timeframes"]) or (z["timeframe"] == "1D")
            eps = EZ_MERGE_EPS_PCT_1D if has_1d else EZ_MERGE_EPS_PCT_4H
            close_mid = abs(m["mid"] - z["mid"]) / m["mid"] * 100 <= eps if m["mid"] else False
            overlap = max(m["min"], z["min"]) <= min(m["max"], z["max"])
            if not (overlap or close_mid):
                continue

            new_min = min(m["min"], z["min"])
            new_max = max(m["max"], z["max"])
            new_mid = (new_min + new_max) / 2.0
            new_width_pct = (new_max - new_min) / new_mid * 100 if new_mid > 0 else 0

            if new_width_pct > EZ_MAX_MERGE_WIDTH_PCT:
                continue

            m["min"] = round(new_min, 2)
            m["max"] = round(new_max, 2)
            m["mid"] = round(new_mid, 2)
            m["strength"] = min(1.0, (m["strength"] + z["strength"]) / 2.0 + 0.05)
            m["freshness"] = max(m["freshness"], z["freshness"])
            m["touch_count"] += z["touch_count"]
            m["reason"] = f"{m['reason']}; {z['reason']}"
            m["types"].add(z["type"])
            m["timeframes"].add(z["timeframe"])
            m["source_zones"].append(z)
            placed = True
            break
        if not placed:
            merged.append({
                "min": z["min"],
                "max": z["max"],
                "mid": z["mid"],
                "direction": z["direction"],
                "strength": z["strength"],
                "freshness": z["freshness"],
                "touch_count": z["touch_count"],
                "reason": z["reason"],
                "types": {z["type"]},
                "timeframes": {z["timeframe"]},
                "source_zones": [z],
                "metadata": z.get("metadata", {}).copy(),
            })

    return merged


def classify_current_price_status(current_price, zone, direction, invalidation_level):
    dist = _zone_distance_pct(current_price, zone)
    status = "FAR_FROM_ZONE"
    chasing = False

    if zone["min"] <= current_price <= zone["max"]:
        status = "INSIDE_ZONE"
    elif dist <= EZ_APPROACHING_MAX_PCT:
        status = "APPROACHING_ZONE"
    elif dist >= EZ_FAR_MIN_PCT:
        status = "FAR_FROM_ZONE"

    if direction == "LONG" and current_price > zone["max"] * (1 + EZ_CHASING_PCT / 100):
        status = "CHASING"
        chasing = True
    if direction == "SHORT" and current_price < zone["min"] * (1 - EZ_CHASING_PCT / 100):
        status = "CHASING"
        chasing = True

    if invalidation_level:
        if direction == "LONG" and current_price < invalidation_level:
            status = "INVALIDATED"
        if direction == "SHORT" and current_price > invalidation_level:
            status = "INVALIDATED"

    return {
        "status": status,
        "distance_to_zone_percent": round(dist, 2),
        "is_chasing": chasing,
    }


def score_entry_zone_quality(zone, direction, current_price, ms_direction):
    types = zone["types"]
    score = 0
    warnings = []
    invalid_reasons = []

    if "support" in types or "resistance" in types:
        score += 2
    if "fvg" in types:
        score += 2
    if "order_block" in types:
        score += 2
    if "fibonacci" in types:
        score += 1
    if "range_level" in types:
        score += 1
    if "breakout_retest" in types:
        score += 1
    if "liquidity_sweep" in types:
        score += 1
    if "market_structure" in types or ms_direction == direction:
        score += 1

    invalidation_level = None
    for src in zone["source_zones"]:
        lvl = src.get("metadata", {}).get("invalidation_level")
        if lvl is not None:
            if invalidation_level is None:
                invalidation_level = lvl
            elif direction == "LONG":
                invalidation_level = min(invalidation_level, lvl)
            else:
                invalidation_level = max(invalidation_level, lvl)
    if invalidation_level is not None:
        score += 1
        if direction == "LONG" and invalidation_level >= zone["min"]:
            score -= 3
            warnings.append("Invalidation is inside or above the entry zone")
            invalid_reasons.append("Invalidation is not structurally below the LONG zone")
        if direction == "SHORT" and invalidation_level <= zone["max"]:
            score -= 3
            warnings.append("Invalidation is inside or below the entry zone")
            invalid_reasons.append("Invalidation is not structurally above the SHORT zone")
    else:
        score -= 3
        warnings.append("No clean invalidation level")
        invalid_reasons.append("No logical stop loss exists")

    width_pct = (zone["max"] - zone["min"]) / zone["mid"] * 100 if zone["mid"] > 0 else 0
    if width_pct > EZ_ZONE_TOO_WIDE_PCT:
        score -= 2
        warnings.append("Entry zone too wide")

    dist = _zone_distance_pct(current_price, zone)
    if dist > 2.5:
        score -= 2
        warnings.append("Price far from zone")

    if zone["freshness"] < 0.3 or zone["strength"] < 0.35:
        score -= 2
        warnings.append("Weak or old zone")

    if direction == "LONG" and current_price > zone["max"] * (1 + EZ_CHASING_PCT / 100):
        score -= 2
        warnings.append("Price chasing")
    if direction == "SHORT" and current_price < zone["min"] * (1 - EZ_CHASING_PCT / 100):
        score -= 2
        warnings.append("Price chasing")

    if ms_direction not in ("UNDEFINED", "CONFLICT", direction):
        score -= 3
        warnings.append("Zone conflicts with 4H or 1D market structure")
        invalid_reasons.append("Directional contradiction")

    return {
        "score": score,
        "warnings": warnings,
        "invalid_reasons": invalid_reasons,
        "invalidation_level": invalidation_level,
        "width_pct": round(width_pct, 2),
    }


def _score_to_rating(score, width_pct=0):
    if score >= 8:
        rating = "STRONG"
    elif score >= 5:
        rating = "ACCEPTABLE"
    elif score >= 2:
        rating = "WEAK"
    else:
        rating = "NOT_FOUND"

    if width_pct > 5.0:
        rating = "NOT_FOUND"
    elif width_pct > 3.0:
        if rating == "STRONG":
            rating = "ACCEPTABLE"
        elif rating == "ACCEPTABLE":
            rating = "WEAK"

    return rating


def _find_narrow_subzone(zone, current_price, direction, max_width_pct=2.0):
    source_zones = zone.get("source_zones", [])
    if len(source_zones) < 2:
        return None

    candidates = []
    for sz in source_zones:
        sz_min = sz.get("min", 0)
        sz_max = sz.get("max", 0)
        sz_mid = (sz_min + sz_max) / 2
        sz_width = (sz_max - sz_min) / sz_mid * 100 if sz_mid > 0 else 0

        if sz_width > max_width_pct:
            continue

        if direction == "LONG" and current_price < sz_min:
            continue
        if direction == "SHORT" and current_price > sz_max:
            continue

        candidates.append({
            "min": round(sz_min, 2),
            "max": round(sz_max, 2),
            "mid": round(sz_mid, 2),
            "width_pct": round(sz_width, 2),
            "type": sz.get("type", "unknown"),
            "reason": sz.get("reason", ""),
            "invalidation_level": sz.get("metadata", {}).get("invalidation_level"),
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["width_pct"])
    return candidates[0]


def evaluate_entry_zone(pair="BTC", context=None):
    instrument, base = _to_okx_swap(pair)
    print(f"[*] Evaluating entry zone for {instrument}...", file=sys.stderr)

    try:
        c4h = _get_candles(instrument, "4H", EZ_LOOKBACK_4H)
        c1d = _get_candles(instrument, "1D", EZ_LOOKBACK_1D)
    except Exception as e:
        return {"skill": "evaluate_entry_zone", "symbol": base, "error": str(e)}

    current_price = float(c4h[-1][4])

    if not _MS_AVAILABLE or evaluate_market_structure is None:
        return {
            "skill": "evaluate_entry_zone",
            "symbol": base,
            "error": "evaluate_market_structure not available",
            "passed": False,
            "rating": "INVALID",
            "confidence": 0.0,
            "summary": "Cannot evaluate entry zone without market structure data.",
        }

    ms = evaluate_market_structure(base, context=context)
    ms_direction = ms.get("conclusion", "UNDEFINED")

    if ms_direction not in ("LONG", "SHORT"):
        return {
            "skill": "evaluate_entry_zone",
            "symbol": base,
            "trade_direction": ms_direction,
            "passed": False,
            "rating": "INVALID",
            "confidence": 0.1,
            "entry_zone": None,
            "current_price_status": {
                "status": "NO_CLEAR_DIRECTION",
                "distance_to_zone_percent": 0.0,
                "is_chasing": False,
            },
            "technical_reasoning": [
                f"Market structure is {ms_direction} - no clear directional bias.",
                "Cannot define valid entry zone without clear trend direction.",
            ],
            "risk_notes": ["No clear market structure to define trade direction."],
            "invalidation": {"level": 0, "reason": "No clear direction"},
            "warnings": ["No clear market structure - rejecting trade setup"],
            "summary": f"Market structure ({ms_direction}) does not provide clear direction for entry.",
        }

    direction = ms_direction

    zones = []
    zones += detect_support_resistance_zones(c4h, direction, "4H", EZ_PIVOT_N_4H)
    zones += detect_support_resistance_zones(c1d, direction, "1D", EZ_PIVOT_N_1D)
    zones += detect_fair_value_gaps(c4h, direction, "4H")
    zones += detect_fair_value_gaps(c1d, direction, "1D")
    zones += detect_order_blocks(c4h, direction, "4H")
    zones += detect_order_blocks(c1d, direction, "1D")
    zones += calculate_fibonacci_retracements(c4h, direction, "4H", EZ_PIVOT_N_4H)
    zones += calculate_fibonacci_retracements(c1d, direction, "1D", EZ_PIVOT_N_1D)
    zones += detect_range_levels(c4h, direction, "4H")
    zones += detect_range_levels(c1d, direction, "1D")
    zones += detect_breakout_retests(c4h, direction, "4H", EZ_PIVOT_N_4H)
    zones += detect_breakout_retests(c1d, direction, "1D", EZ_PIVOT_N_1D)
    zones += detect_liquidity_sweeps(c4h, direction, "4H")
    zones += detect_liquidity_sweeps(c1d, direction, "1D")
    zones += derive_market_structure_zone(ms, direction)

    merged = merge_overlapping_zones(zones)
    if not merged:
        return {
            "skill": "evaluate_entry_zone",
            "symbol": base,
            "trade_direction": direction,
            "passed": False,
            "rating": "NOT_FOUND",
            "confidence": 0.2,
            "entry_zone": {
                "min": 0,
                "max": 0,
                "mid": 0,
                "type": [],
                "timeframes": [],
                "source_zones": [],
            },
            "current_price_status": {
                "status": "FAR_FROM_ZONE",
                "distance_to_zone_percent": 0.0,
                "is_chasing": False,
            },
            "technical_reasoning": ["No clear support/resistance, FVG, OB, Fib, retest, range, or sweep confluence zone found."],
            "risk_notes": ["Risk cannot be clearly defined without a valid zone."],
            "invalidation": {"level": 0, "reason": "No candidate zone"},
            "warnings": ["No clean invalidation level"],
            "summary": "No clear entry zone found.",
        }

    scored = []
    for z in merged:
        s = score_entry_zone_quality(z, direction, current_price, ms_direction)
        scored.append((z, s))

    scored.sort(key=lambda x: (x[1]["score"], len(x[0]["types"]), x[0]["freshness"], x[0]["strength"]), reverse=True)
    best_zone, meta = scored[0]

    status = classify_current_price_status(current_price, best_zone, direction, meta["invalidation_level"])
    rating = _score_to_rating(meta["score"], meta["width_pct"])

    invalid_override = False
    invalid_reasons = list(meta["invalid_reasons"])
    if status["status"] == "INVALIDATED":
        invalid_override = True
        invalid_reasons.append("Zone has been invalidated")
    if ms_direction not in ("UNDEFINED", "CONFLICT", direction):
        invalid_override = True
    if status["status"] == "CHASING" and len(best_zone["types"]) <= 1:
        invalid_override = True

    if invalid_override:
        rating = "INVALID"

    passed = rating in ("STRONG", "ACCEPTABLE")
    confidence = max(0.2, min(0.95, 0.45 + 0.05 * meta["score"]))

    width = meta["width_pct"]
    zone_min = best_zone["min"]
    zone_max = best_zone["max"]
    zone_mid = best_zone["mid"]
    zone_types = ", ".join(sorted(best_zone["types"]))

    if rating == "STRONG":
        summary = f"LONG {zone_types} zone (${zone_min:,.0f}-${zone_max:,.0f}, {width:.1f}%). Strong confluence, clear risk. Price {status['status'].lower()}."
    elif rating == "ACCEPTABLE":
        warnings_str = f" - {len(warnings)} warning(s)" if warnings else ""
        summary = f"{direction} {zone_types} zone (${zone_min:,.0f}-${zone_max:,.0f}, {width:.1f}%). Acceptable but{warnings_str}. Price {status['status'].lower()}."
    elif rating == "WEAK":
        summary = f"{direction} {zone_types} zone (${zone_min:,.0f}-${zone_max:,.0f}, {width:.1f}%). Weak confluence or wide zone - limited reliability."
    elif rating == "INVALID":
        summary = f"Zone invalid for checklist. Reason: {invalid_reasons[0] if invalid_reasons else 'structure/risk issues'}."
    else:
        summary = "No clear entry zone found."

    reasoning = [
        f"Selected {direction} zone with {len(best_zone['types'])} confluence type(s): {', '.join(sorted(best_zone['types']))}.",
        f"Zone width: {meta['width_pct']:.2f}% and distance from price: {status['distance_to_zone_percent']:.2f}%.",
        f"Market structure context: {ms_direction}.",
    ]
    if invalid_reasons:
        reasoning.append("Invalidation concerns: " + "; ".join(dict.fromkeys(invalid_reasons)) + ".")

    risk_notes = []
    if meta["invalidation_level"] is not None:
        inv = meta["invalidation_level"]
        zone_min = best_zone["min"]
        zone_max = best_zone["max"]

        if direction == "LONG" and inv < zone_min:
            risk_notes.append(f"Stop-loss should be placed below zone at ${inv:,.2f}.")
        elif direction == "LONG" and inv >= zone_min:
            risk_notes.append(f"WARNING: Invalidation level (${inv:,.2f}) is inside/above the zone - risk may not be well-defined.")
        elif direction == "SHORT" and inv > zone_max:
            risk_notes.append(f"Stop-loss should be placed above zone at ${inv:,.2f}.")
        elif direction == "SHORT" and inv <= zone_max:
            risk_notes.append(f"WARNING: Invalidation level (${inv:,.2f}) is inside/below the zone - risk may not be well-defined.")
    else:
        risk_notes.append("No clear invalidation level - risk cannot be defined.")
    risk_notes.append("This skill validates zone quality only; it does not signal trade entry.")

    warnings = list(dict.fromkeys(meta["warnings"]))
    if status["status"] == "CHASING" and "Price chasing" not in warnings:
        warnings.append("Price chasing")
    if status["status"] == "FAR_FROM_ZONE" and "Price far from zone" not in warnings:
        warnings.append("Price far from zone")

    subzone = _find_narrow_subzone(best_zone, current_price, direction, max_width_pct=2.0)

    return {
        "skill": "evaluate_entry_zone",
        "symbol": base,
        "trade_direction": direction,
        "passed": passed,
        "rating": rating,
        "confidence": round(confidence, 2),
        "entry_zone": {
            "min": best_zone["min"],
            "max": best_zone["max"],
            "mid": best_zone["mid"],
            "type": sorted(best_zone["types"]),
            "timeframes": sorted(best_zone["timeframes"]),
            "source_zones": best_zone["source_zones"],
        },
        "current_price_status": status,
        "technical_reasoning": reasoning,
        "risk_notes": risk_notes,
        "invalidation": {
            "level": round(meta["invalidation_level"], 2) if meta["invalidation_level"] is not None else 0,
            "reason": "Derived from structure/OB/FVG" if meta["invalidation_level"] is not None else "No clean structural invalidation",
        },
        "warnings": warnings,
        "summary": summary,
        "alternative_subzone": subzone,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate entry-zone quality for a swing setup.")
    parser.add_argument("--pair", type=str, default="BTC", help="Trading pair (e.g., BTC, ETH, BTCUSDT)")
    args = parser.parse_args()
    result = evaluate_entry_zone(args.pair)
    print(json.dumps(result, indent=2))
