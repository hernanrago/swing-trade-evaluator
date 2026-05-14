#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator.
HTTP interface — agent logic lives in agent.py.
"""

import os
import logging
import traceback
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import litellm

from agent import run_agent, run_agent_batch, run_synthesis, LLM_MODEL, MAX_TOKENS, MAX_AGENT_ITERATIONS, SUBPROCESS_TIMEOUT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)


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


@app.route("/evaluate", methods=["POST"])
def evaluate():
    """
    Main evaluation endpoint.
    Input:  { "pair": "BTC" }
    Output: { "timestamp": "...", "pair": "BTC", "recommendation": { ... } }
    """
    try:
        data = request.get_json()
        if not data or "pair" not in data:
            return {"error": "Missing 'pair' parameter"}, 400

        pair = data["pair"].upper()
        log.info("POST /evaluate | pair=%s", pair)
        recommendation = run_agent(pair)

        if "error" in recommendation:
            log.error("Evaluation failed | %s", recommendation)
            return recommendation, 500

        log.info("Evaluation OK | pair=%s direction=%s confidence=%s",
                 pair, recommendation.get("direction"), recommendation.get("confidence"))
        return {
            "timestamp": datetime.now().isoformat(),
            "pair": pair,
            "recommendation": recommendation
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


@app.route("/evaluate-all", methods=["POST"])
def evaluate_all():
    """
    Fetches top 10 pairs by quoteVolume from BingX ticker endpoint,
    evaluates each, and returns a ranked synthesis.
    Input:  {} (no payload required)
    Output: { "timestamp": "...", "ranked": [...] }
    """
    try:
        log.info("POST /evaluate-all")

        url = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        tickers = raw.get("data", [])
        if not tickers:
            return {"error": "No ticker data returned from BingX"}, 502

        usdt_tickers = [t for t in tickers if "-USDT" in t.get("symbol", "")]
        sorted_tickers = sorted(
            usdt_tickers,
            key=lambda x: float(x.get("quoteVolume", 0) or 0),
            reverse=True
        )
        seen = set()
        top10 = []
        for t in sorted_tickers:
            base = t["symbol"].split("-USDT")[0]
            if base not in seen:
                seen.add(base)
                top10.append(t["symbol"])
            if len(top10) >= 10:
                break

        log.info("Top 10 unique base pairs by quoteVolume: %s", top10)

        pairs = [t.replace("-USDT-SWAP", "") for t in top10]
        evaluations = run_agent_batch(pairs)

        if not evaluations:
            return {"error": "All evaluations failed"}, 502

        ranked = run_synthesis(evaluations)

        log.info("evaluate-all OK | evaluated=%d ranked=%d", len(evaluations), len(ranked) if isinstance(ranked, list) else 0)
        return {
            "timestamp": datetime.now().isoformat(),
            "ranked": ranked if isinstance(ranked, list) else [],
            "evaluations": evaluations
        }

    except Exception as e:
        log.error("Unhandled exception: %s", traceback.format_exc())
        return {"error": str(e)}, 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting | model=%s port=%d", LLM_MODEL, port)
    app.run(host="0.0.0.0", port=port, debug=False)
