#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator.
HTTP interface — agent logic lives in agent.py.
"""

import os
import hmac
import hashlib
import time
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
load_dotenv()
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import litellm

from agent import run_agent, run_agent_batch, run_synthesis, execute_skill, LLM_MODEL, MAX_TOKENS, MAX_AGENT_ITERATIONS, SUBPROCESS_TIMEOUT, VALID_MODES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.json.ensure_ascii = False
CORS(app)

BINGX_API_KEY    = os.environ.get("BINGX_API_KEY", "")
BINGX_API_SECRET = os.environ.get("BINGX_API_SECRET", "")


def _bingx_signed_get(path, params=None):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signature = hmac.new(
        BINGX_API_SECRET.encode(),
        query_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    url = f"https://open-api.bingx.com{path}?{query_string}&signature={signature}"
    resp = requests.get(url, headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _fetch_realized_pnl(days: int) -> list[dict]:
    start_ms = int(time.time() * 1000) - days * 86_400 * 1000
    end_ms   = int(time.time() * 1000)
    records  = []
    while True:
        data = _bingx_signed_get("/openApi/swap/v2/user/income", {
            "incomeType": "REALIZED_PNL",
            "startTime":  start_ms,
            "endTime":    end_ms,
            "limit":      1000,
        })
        if data.get("code") != 0:
            raise ValueError(f"BingX error: {data.get('msg', 'unknown')}")
        batch = data.get("data") or []
        records.extend(batch)
        if len(batch) < 1000:
            break
        times = [int(r["time"]) for r in batch if "time" in r]
        if not times:
            break
        end_ms = min(times) - 1
        if end_ms < start_ms:
            break
    return [r for r in records if int(r["time"]) >= start_ms]


def _compute_risk_levels(side, mark_price, structure):
    if not structure or "error" in structure:
        return None
    if mark_price <= 0:
        return None
    tf4h = structure.get("4h", {})
    tf1d = structure.get("1d", {})
    if side == "LONG":
        sl  = tf4h.get("invalidation") or tf1d.get("invalidation") or tf4h.get("last_swing_low")
        tp1 = tf4h.get("last_swing_high")
        tp2 = tf1d.get("last_swing_high")
        sl  = sl  if sl  and sl  < mark_price else None
        tp1 = tp1 if tp1 and tp1 > mark_price else None
        tp2 = tp2 if tp2 and tp2 > mark_price and (tp1 is None or tp2 > tp1) else None
    else:  # SHORT
        sl  = tf4h.get("invalidation") or tf1d.get("invalidation") or tf4h.get("last_swing_high")
        tp1 = tf4h.get("last_swing_low")
        tp2 = tf1d.get("last_swing_low")
        sl  = sl  if sl  and sl  > mark_price else None
        tp1 = tp1 if tp1 and tp1 < mark_price else None
        tp2 = tp2 if tp2 and tp2 < mark_price and (tp1 is None or tp2 < tp1) else None
    if sl is None:
        return None
    callback_pct = round(abs(sl - mark_price) / mark_price * 100, 2)
    breakeven_trigger = round((mark_price + tp2) / 2, 2) if tp2 else None
    note = None
    if tp1 is None:
        if tp2:
            note = (
                f"Sin TP1 estructural en 4H. "
                f"Usá SL fijo en {round(sl, 2)}. "
                f"Target: {round(tp2, 2)} (1D). "
                f"Mové el SL a breakeven cuando el precio llegue a {breakeven_trigger}."
            )
        else:
            note = f"Sin TPs estructurales disponibles. Usá SL fijo en {round(sl, 2)} y gestioná manualmente."
    return {
        "sl":                    round(sl,  2),
        "tp1":                   round(tp1, 2) if tp1 else None,
        "tp2":                   round(tp2, 2) if tp2 else None,
        "trailing_activation":   round(tp1, 2) if tp1 else None,
        "trailing_callback_pct": callback_pct,
        "note":                  note,
    }


def _suggested_action(alignment, evaluation, risk_levels=None):
    if alignment == "unavailable" or not evaluation:
        return "monitor"
    confidence = evaluation.get("confidence", "low")
    squeeze    = bool(evaluation.get("squeeze_warning"))
    if alignment == "aligned":
        if confidence == "high" and not squeeze:
            # Downgrade add → hold when there's no clear nearby TP
            has_tp1 = risk_levels and risk_levels.get("tp1") is not None
            return "add" if has_tp1 else "hold"
        if confidence == "high" and squeeze:
            return "hold"
        if confidence == "moderate":
            return "hold"
        return "reduce"
    # conflict
    if confidence == "high":
        return "close"
    if confidence == "moderate":
        return "reduce"
    return "monitor"


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "swing-trade-evaluator-api",
        "orchestrator": LLM_MODEL,
        "max_tokens": MAX_TOKENS,
        "max_agent_iterations": MAX_AGENT_ITERATIONS,
        "subprocess_timeout": SUBPROCESS_TIMEOUT
    }


@app.route("/evaluations", methods=["POST"])
def evaluations():
    """
    Evaluates a list of pairs and returns a ranked synthesis.
    Input:  { "pairs": ["BTC", "ETH", "SOL"] }
    Output: { "timestamp": "...", "ranked": [...], "evaluations": [...] }
    """
    try:
        data = request.get_json() or {}
        pairs = data.get("pairs")
        if not pairs or not isinstance(pairs, list):
            return {"error": "Missing or invalid 'pairs' parameter — expected a non-empty list"}, 400

        pairs = [p.upper() for p in pairs]
        mode = data.get("mode", "swing")
        if mode not in VALID_MODES:
            return {"error": f"Unknown mode '{mode}' — must be one of {sorted(VALID_MODES)}"}, 400
        log.info("POST /evaluations | pairs=%s mode=%s", pairs, mode)

        evals = run_agent_batch(pairs, mode=mode)
        if not evals:
            return {"error": "All evaluations failed"}, 502

        ranked = run_synthesis(evals, mode=mode)

        ranked_list = ranked if isinstance(ranked, list) else []
        log.info("evaluations OK | evaluated=%d ranked=%d", len(evals), len(ranked_list))
        return {
            "timestamp": datetime.now().isoformat(),
            "high_confidence": [r for r in ranked_list if r.get("confidence") == "high"],
            "ranked": ranked_list,
            "evaluations": evals,
        }

    except litellm.AuthenticationError:
        log.error("Authentication error — check API key for model: %s", LLM_MODEL)
        return {"error": "Invalid API key for the configured provider"}, 500
    except litellm.RateLimitError:
        log.warning("Rate limit hit")
        return {"error": "Rate limit hit — retry later"}, 429
    except litellm.APIError as e:
        log.error("LLM API error: %s", traceback.format_exc())
        return {"error": f"LLM API error: {e}"}, 500
    except Exception as e:
        log.error("Unhandled exception: %s", traceback.format_exc())
        return {"error": str(e)}, 500


@app.route("/evaluations/top", methods=["POST"])
def evaluations_top():
    """
    Fetches top N pairs by quoteVolume from BingX and returns a ranked synthesis.
    Input:  { "top": 10 }  (optional, default 10)
    Output: { "timestamp": "...", "ranked": [...], "evaluations": [...] }
    """
    try:
        data = request.get_json() or {}
        top_n = int(data.get("top", 10))
        mode = data.get("mode", "swing")
        if mode not in VALID_MODES:
            return {"error": f"Unknown mode '{mode}' — must be one of {sorted(VALID_MODES)}"}, 400
        log.info("POST /evaluations/top | top=%d mode=%s", top_n, mode)

        resp = requests.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker", timeout=10)
        resp.raise_for_status()
        tickers = resp.json().get("data", [])
        if not tickers:
            return {"error": "No ticker data returned from BingX"}, 502

        usdt_tickers = [t for t in tickers if "-USDT" in t.get("symbol", "")]
        sorted_tickers = sorted(usdt_tickers, key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)

        seen = set()
        top_symbols = []
        for t in sorted_tickers:
            base = t["symbol"].split("-USDT")[0]
            if base not in seen:
                seen.add(base)
                top_symbols.append(t["symbol"])
            if len(top_symbols) >= top_n:
                break

        pairs = [s.replace("-USDT-SWAP", "") for s in top_symbols]
        log.info("Top %d pairs by quoteVolume: %s", top_n, pairs)

        evals = run_agent_batch(pairs, mode=mode)
        if not evals:
            return {"error": "All evaluations failed"}, 502

        ranked = run_synthesis(evals, mode=mode)

        ranked_list = ranked if isinstance(ranked, list) else []
        log.info("evaluations/top OK | evaluated=%d ranked=%d", len(evals), len(ranked_list))
        return {
            "timestamp": datetime.now().isoformat(),
            "high_confidence": [r for r in ranked_list if r.get("confidence") == "high"],
            "ranked": ranked_list,
            "evaluations": evals,
        }

    except Exception as e:
        log.error("Unhandled exception: %s", traceback.format_exc())
        return {"error": str(e)}, 500


@app.route("/positions", methods=["GET"])
def positions():
    """
    Fetches open perpetual futures positions from BingX and crosses them with
    a fresh evaluation for each pair.
    Output: { "timestamp": "...", "positions": [...] }
    """
    if not BINGX_API_KEY or not BINGX_API_SECRET:
        return {"error": "BINGX_API_KEY and BINGX_API_SECRET must be set"}, 503

    try:
        log.info("GET /positions")
        data = _bingx_signed_get("/openApi/swap/v2/user/positions")

        if data.get("code") != 0:
            log.error("BingX positions error: %s", data)
            return {"error": f"BingX error: {data.get('msg', 'unknown')}"}, 502

        raw = [p for p in data.get("data", []) if float(p.get("positionAmt", 0)) != 0]

        if not raw:
            return {"timestamp": datetime.now().isoformat(), "positions": []}

        pairs = list({p["symbol"].split("-USDT")[0] for p in raw})
        evals = run_agent_batch(pairs, mode="swing")
        eval_map = {e["pair"]: e for e in evals}

        # Fetch structure for each pair in parallel (candles already cached from run_agent_batch)
        structure_map = {}
        with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
            futures = {executor.submit(execute_skill, "evaluate_market_structure", {"pair": p}, mode="swing"): p for p in pairs}
            for future in as_completed(futures):
                p = futures[future]
                try:
                    structure_map[p] = future.result()
                except Exception:
                    structure_map[p] = None

        result = []
        for pos in raw:
            base       = pos["symbol"].split("-USDT")[0]
            side       = pos.get("positionSide", "").upper()
            mark_price = float(pos.get("markPrice", 0))
            evaluation = eval_map.get(base)
            structure  = structure_map.get(base)

            if evaluation and "direction" in evaluation:
                alignment = "aligned" if evaluation["direction"] == side else "conflict"
            else:
                alignment = "unavailable"

            risk_levels = _compute_risk_levels(side, mark_price, structure)

            result.append({
                "symbol":           pos["symbol"],
                "side":             side,
                "size":             float(pos.get("positionAmt", 0)),
                "entry_price":      float(pos.get("avgPrice", 0)),
                "mark_price":       mark_price,
                "unrealized_pnl":   float(pos.get("unrealizedProfit", 0)),
                "leverage":         int(pos.get("leverage", 1)),
                "signal_direction": evaluation.get("direction")       if evaluation else None,
                "signal_confidence":evaluation.get("confidence")      if evaluation else None,
                "squeeze_warning":  evaluation.get("squeeze_warning") if evaluation else None,
                "alignment":        alignment,
                "risk_levels":      risk_levels,
                "suggested_action": _suggested_action(alignment, evaluation, risk_levels),
            })

        log.info("positions OK | open=%d evaluated=%d", len(result), len(evals))
        return {
            "timestamp": datetime.now().isoformat(),
            "positions": result,
        }

    except Exception as e:
        log.error("Unhandled exception: %s", traceback.format_exc())
        return {"error": str(e)}, 500


@app.route("/portfolio/equity", methods=["GET"])
def portfolio_equity():
    if not BINGX_API_KEY or not BINGX_API_SECRET:
        return {"error": "BINGX_API_KEY and BINGX_API_SECRET must be set"}, 503

    try:
        days = int(request.args.get("days", 30))
    except (ValueError, TypeError):
        return {"error": "'days' must be an integer"}, 400

    if days < 1 or days > 90:
        return {"error": "'days' must be between 1 and 90"}, 400

    log.info("GET /portfolio/equity | days=%d", days)

    try:
        records = _fetch_realized_pnl(days)
    except ValueError as e:
        return {"error": str(e)}, 502
    except Exception as e:
        log.error("portfolio_equity unhandled: %s", traceback.format_exc())
        return {"error": str(e)}, 500

    records.sort(key=lambda r: int(r["time"]))

    cumulative = 0.0
    equity_curve = []
    for r in records:
        pnl = round(float(r["income"]), 2)
        cumulative = round(cumulative + pnl, 2)
        equity_curve.append({
            "time":           datetime.fromtimestamp(int(r["time"]) / 1000).isoformat(),
            "symbol":         r.get("symbol", ""),
            "pnl":            pnl,
            "cumulative_pnl": cumulative,
        })

    pnl_values = [p["pnl"] for p in equity_curve]
    trade_count = len(pnl_values)
    wins = sum(1 for p in pnl_values if p > 0)
    summary = {
        "total_pnl":   round(cumulative, 2),
        "trade_count": trade_count,
        "win_rate":    round(wins / trade_count, 4) if trade_count else 0.0,
        "best_trade":  round(max(pnl_values), 2) if pnl_values else 0.0,
        "worst_trade": round(min(pnl_values), 2) if pnl_values else 0.0,
    }

    log.info("portfolio/equity OK | days=%d trades=%d total_pnl=%s", days, trade_count, summary["total_pnl"])
    return {
        "timestamp":    datetime.now().isoformat(),
        "period_days":  days,
        "equity_curve": equity_curve,
        "summary":      summary,
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting | model=%s port=%d", LLM_MODEL, port)
    app.run(host="0.0.0.0", port=port, debug=False)
