#!/usr/bin/env python3
"""
Skill: Evaluate Squeeze Risk
Detects if the market is excessively loaded on one side (crowded trade risk).
Combines: funding rate, L/S ratio, perpetual basis, OI trend, liquidation bias.

Scoring: positive score = long crowding, negative = short crowding.
"""

import os
import json
import sys
import requests

# --- Config (override via environment variables) ---
SQUEEZE_FUNDING_VERY_HIGH = float(os.environ.get("SQUEEZE_FUNDING_VERY_HIGH", "0.001"))
SQUEEZE_FUNDING_HIGH      = float(os.environ.get("SQUEEZE_FUNDING_HIGH",      "0.0003"))
SQUEEZE_FUNDING_LOW       = float(os.environ.get("SQUEEZE_FUNDING_LOW",       "-0.0003"))
SQUEEZE_FUNDING_VERY_LOW  = float(os.environ.get("SQUEEZE_FUNDING_VERY_LOW",  "-0.001"))
SQUEEZE_LSR_HIGH          = float(os.environ.get("SQUEEZE_LSR_HIGH",          "1.5"))   # L/S ratio > this = crowded long
SQUEEZE_LSR_LOW           = float(os.environ.get("SQUEEZE_LSR_LOW",           "0.67"))  # L/S ratio < this = crowded short
SQUEEZE_BASIS_HIGH        = float(os.environ.get("SQUEEZE_BASIS_HIGH",        "0.3"))   # perp premium > 0.3% = long crowding
SQUEEZE_BASIS_LOW         = float(os.environ.get("SQUEEZE_BASIS_LOW",         "-0.3"))  # perp discount < -0.3% = short crowding
SQUEEZE_LIQ_BIAS          = float(os.environ.get("SQUEEZE_LIQ_BIAS",          "65.0"))  # >65% liquidations on one side = squeeze
OI_INTERVAL               = os.environ.get("OI_INTERVAL",  "1h")
OI_WINDOW                 = int(os.environ.get("OI_WINDOW", "24"))
API_TIMEOUT               = int(os.environ.get("API_TIMEOUT", "10"))
COINGECKO_API_KEY         = os.environ.get("COINGECKO_API_KEY", "")

COINGECKO_IDS = {
    "BTC": "bitcoin",   "ETH": "ethereum",  "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple",  "ADA": "cardano",
    "DOGE": "dogecoin", "DOT": "polkadot",  "AVAX": "avalanche-2",
    "LINK": "chainlink","ATOM": "cosmos",   "LTC": "litecoin",
    "NEAR": "near",     "UNI": "uniswap",   "MATIC": "matic-network",
}

def _to_bingx_symbol(pair):
    return f"{pair.replace('USDT', '')}-USDT"

def _to_gateio_contract(pair):
    return f"{pair.replace('USDT', '')}_USDT"

def _base(pair):
    return pair.replace("USDT", "")

def fetch_funding_and_mark(pair):
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"
    resp = requests.get(url, params={"symbol": _to_bingx_symbol(pair)}, timeout=API_TIMEOUT)
    resp.raise_for_status()
    d = resp.json()["data"]
    return float(d["lastFundingRate"]), float(d["markPrice"])

def fetch_spot_price(pair):
    coin_id = COINGECKO_IDS.get(_base(pair))
    if not coin_id:
        return None
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd"},
        headers=headers, timeout=API_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()[coin_id]["usd"]

def fetch_gateio_stats(pair):
    resp = requests.get(
        "https://api.gateio.ws/api/v4/futures/usdt/contract_stats",
        params={"contract": _to_gateio_contract(pair), "interval": OI_INTERVAL, "limit": OI_WINDOW + 1},
        timeout=API_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

def evaluate_squeeze_risk(pair="BTC", context=None):
    """
    Evaluates crowded-trade risk by scoring multiple market signals.
    Returns crowded_side, risk_level, and per-signal breakdown.
    """
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating squeeze risk for {pair}...", file=sys.stderr)

    score = 0
    notes = []
    mark_price = None
    funding_rate = None

    bingx_sym = _to_bingx_symbol(pair)
    pm_entry = (context or {}).get("premium_index", {}).get(bingx_sym)

    # --- Signal 1: Funding rate (weight ±2 if extreme, ±1 if elevated) ---
    try:
        if pm_entry is not None:
            funding_rate = float(pm_entry["lastFundingRate"])
            mark_price   = float(pm_entry["markPrice"])
        else:
            funding_rate, mark_price = fetch_funding_and_mark(pair)
        fp = funding_rate * 100
        if funding_rate > SQUEEZE_FUNDING_VERY_HIGH:
            score += 2
            notes.append(f"Funding very high ({fp:.4f}%/8h) → strong long crowding [+2]")
        elif funding_rate > SQUEEZE_FUNDING_HIGH:
            score += 1
            notes.append(f"Funding elevated ({fp:.4f}%/8h) → mild long crowding [+1]")
        elif funding_rate < SQUEEZE_FUNDING_VERY_LOW:
            score -= 2
            notes.append(f"Funding very negative ({fp:.4f}%/8h) → strong short crowding [-2]")
        elif funding_rate < SQUEEZE_FUNDING_LOW:
            score -= 1
            notes.append(f"Funding negative ({fp:.4f}%/8h) → mild short crowding [-1]")
        else:
            notes.append(f"Funding neutral ({fp:.4f}%/8h) [0]")
    except Exception as e:
        print(f"[!] Funding fetch failed: {e}", file=sys.stderr)

    # --- Signal 2: Perpetual basis vs spot (weight ±1) ---
    basis_pct = None
    try:
        if mark_price:
            base_sym = _base(pair)
            spot = (context or {}).get("spot_prices", {}).get(base_sym)
            if spot is None:
                spot = fetch_spot_price(pair)
            if spot:
                basis_pct = (mark_price - spot) / spot * 100
                if basis_pct > SQUEEZE_BASIS_HIGH:
                    score += 1
                    notes.append(f"Perp premium ({basis_pct:+.3f}%): longs paying up → long crowding [+1]")
                elif basis_pct < SQUEEZE_BASIS_LOW:
                    score -= 1
                    notes.append(f"Perp discount ({basis_pct:+.3f}%): perp below spot → short crowding [-1]")
                else:
                    notes.append(f"Basis neutral ({basis_pct:+.3f}%) [0]")
            else:
                notes.append(f"Basis: spot price not available for {_base(pair)}")
    except Exception as e:
        print(f"[!] Basis calculation failed: {e}", file=sys.stderr)

    # --- Signals 3, 4, 5: Gate.io (L/S ratio, OI trend, liquidations) ---
    lsr = None
    oi_change_pct = None
    long_liq_pct = None
    try:
        stats = fetch_gateio_stats(pair)
        if len(stats) >= 2:
            latest = stats[-1]
            oldest = stats[0]

            # Signal 3: Long/Short account ratio (weight ±1)
            lsr_raw = latest.get("lsr_account") or latest.get("lsr_taker")
            if lsr_raw:
                lsr = float(lsr_raw)
                if lsr > SQUEEZE_LSR_HIGH:
                    score += 1
                    notes.append(f"L/S ratio {lsr:.2f}: more longs than shorts → long crowding [+1]")
                elif lsr < SQUEEZE_LSR_LOW:
                    score -= 1
                    notes.append(f"L/S ratio {lsr:.2f}: more shorts than longs → short crowding [-1]")
                else:
                    notes.append(f"L/S ratio {lsr:.2f}: balanced positioning [0]")

            # Signal 4: OI trend (informational — amplifies risk but doesn't vote)
            oi_start = float(oldest["open_interest_usd"])
            oi_end   = float(latest["open_interest_usd"])
            if oi_start > 0:
                oi_change_pct = (oi_end - oi_start) / oi_start * 100
                if oi_change_pct > 5:
                    notes.append(f"OI rising +{oi_change_pct:.1f}%: leverage building, amplifies squeeze risk on crowded side")
                elif oi_change_pct < -5:
                    notes.append(f"OI falling {oi_change_pct:.1f}%: deleveraging underway, squeeze risk reduced")
                else:
                    notes.append(f"OI stable ({oi_change_pct:+.1f}%)")

            # Signal 5: Liquidation bias (weight ±1)
            long_liq  = sum(float(s.get("long_liq_usd",  0)) for s in stats)
            short_liq = sum(float(s.get("short_liq_usd", 0)) for s in stats)
            total_liq = long_liq + short_liq
            if total_liq > 0:
                long_liq_pct  = long_liq  / total_liq * 100
                short_liq_pct = short_liq / total_liq * 100
                if long_liq_pct > SQUEEZE_LIQ_BIAS:
                    score -= 1  # longs getting squeezed → short side winning
                    notes.append(f"Long liquidations dominate ({long_liq_pct:.0f}%): long squeeze in progress [-1 on long side]")
                elif short_liq_pct > SQUEEZE_LIQ_BIAS:
                    score += 1  # shorts getting squeezed → long side winning
                    notes.append(f"Short liquidations dominate ({short_liq_pct:.0f}%): short squeeze in progress [+1 on long side]")
                else:
                    notes.append(f"Liquidations balanced (long {long_liq_pct:.0f}% / short {short_liq_pct:.0f}%) [0]")
            else:
                notes.append("No significant liquidations in window")
    except Exception as e:
        print(f"[!] Gate.io stats failed: {e}", file=sys.stderr)

    # --- Determine crowded side and risk level ---
    if score >= 4:
        crowded_side, risk_level = "long",  "high"
        warning = f"Long side heavily crowded (score {score:+d}). High risk of long squeeze — avoid new longs."
    elif score >= 2:
        crowded_side, risk_level = "long",  "moderate"
        warning = f"Long side moderately crowded (score {score:+d}). Elevated long squeeze risk."
    elif score == 1:
        crowded_side, risk_level = "long",  "low"
        warning = f"Mild long crowding detected (score {score:+d}). Monitor but not a blocker."
    elif score <= -4:
        crowded_side, risk_level = "short", "high"
        warning = f"Short side heavily crowded (score {score:+d}). High risk of short squeeze — avoid new shorts."
    elif score <= -2:
        crowded_side, risk_level = "short", "moderate"
        warning = f"Short side moderately crowded (score {score:+d}). Elevated short squeeze risk."
    elif score == -1:
        crowded_side, risk_level = "short", "low"
        warning = f"Mild short crowding detected (score {score:+d}). Monitor but not a blocker."
    else:
        crowded_side, risk_level = "neutral", "low"
        warning = None

    reasoning = "\n".join([
        f"Squeeze Risk — {pair}",
        f"Score: {score:+d}  (positive = long crowded, negative = short crowded)",
        "",
        *[f"  • {n}" for n in notes],
        "",
        f"Crowded side: {crowded_side.upper()}",
        f"Risk level:   {risk_level.upper()}",
        *([ f"Warning: {warning}"] if warning else []),
    ])

    return {
        "pair":          pair,
        "crowded_side":  crowded_side,
        "risk_level":    risk_level,
        "score":         score,
        "warning":       warning,
        "details": {
            "funding_rate":    round(funding_rate * 100, 4) if funding_rate is not None else None,
            "basis_pct":       round(basis_pct, 3) if basis_pct is not None else None,
            "lsr":             round(lsr, 2) if lsr else None,
            "oi_change_pct":   round(oi_change_pct, 2) if oi_change_pct is not None else None,
            "long_liq_pct":    round(long_liq_pct, 1) if long_liq_pct is not None else None,
        },
        "reasoning": reasoning
    }

if __name__ == "__main__":
    pair = "BTC"
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--pair" and i + 1 < len(sys.argv) - 1:
            pair = sys.argv[i + 2]

    result = evaluate_squeeze_risk(pair)
    print(json.dumps(result, indent=2))
