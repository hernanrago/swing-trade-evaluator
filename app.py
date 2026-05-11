#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator.
HTTP interface — agent logic lives in agent.py.
"""

import os
import logging
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import litellm

from agent import run_agent, LLM_MODEL, MAX_TOKENS, MAX_AGENT_ITERATIONS, SUBPROCESS_TIMEOUT

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting | model=%s port=%d", LLM_MODEL, port)
    app.run(host="0.0.0.0", port=port, debug=False)
