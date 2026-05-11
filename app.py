#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator (v4 - Multi-provider)
LiteLLM orchestrates the analysis, supporting Anthropic, OpenAI, Gemini, and more.
"""

import os
import re
import json
import logging
import subprocess
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import litellm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# --- Config (override via environment variables) ---
LLM_MODEL            = os.environ.get("LLM_MODEL",            "claude-opus-4-7")
MAX_TOKENS           = int(os.environ.get("MAX_TOKENS",          "4096"))
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "10"))
SUBPROCESS_TIMEOUT   = int(os.environ.get("SUBPROCESS_TIMEOUT",   "30"))

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_tf_trend",
            "description": "Analyzes 1D/1W moving averages for a crypto pair and returns a trend direction recommendation (LONG or SHORT).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol without the USDT suffix, e.g. BTC, ETH, SOL"
                    }
                },
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_btc_dominance",
            "description": "Fetches BTC dominance from CoinGecko and returns its directional impact on the given pair. Rising dominance is bullish for BTC and bearish for altcoins.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"
                    }
                },
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_funding_rate",
            "description": "Fetches the current perpetual funding rate for a pair and returns a contrarian bias. High positive funding means crowded longs (bearish warning). High negative funding means crowded shorts (bullish warning). Use as a filter, not a standalone signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"
                    }
                },
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_open_interest",
            "description": "Analyzes OI change vs price change over a configurable window to validate directional bias. OI rising with price = new positions backing the move (strong signal). OI falling with price = likely liquidations or covering (weak signal). Use as participation validator, not standalone signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"
                    }
                },
                "required": ["pair"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are a crypto swing trade analyst. Evaluate the given pair by calling all four tools:
1. evaluate_tf_trend — gets the 1D/1W trend direction
2. evaluate_btc_dominance — gets the BTC dominance impact
3. evaluate_funding_rate — gets the funding rate as a contrarian filter
4. evaluate_open_interest — validates whether OI backs the price move

After receiving all results, respond ONLY with a JSON object. No markdown, no explanation outside the JSON:
{
  "direction": "LONG" or "SHORT",
  "confidence": "high", "moderate", or "low",
  "aligned": true if trend and dominance agree, false if they conflict,
  "funding_warning": null or a short warning string if funding is extreme,
  "reasoning": "2-3 sentence synthesis of all four signals",
  "trend_summary": "one-line summary of the trend signal",
  "dominance_summary": "one-line summary of the dominance signal",
  "funding_summary": "one-line summary of the funding rate signal",
  "oi_summary": "one-line summary of the open interest signal"
}"""


def execute_skill(skill_name, params):
    """Runs a skill script via subprocess and returns parsed JSON."""
    pair = params.get("pair", "BTC").upper()

    script_map = {
        "evaluate_tf_trend": "./evaluate-tf-trend.py",
        "evaluate_btc_dominance": "./evaluate-btc-dominance.py",
        "evaluate_funding_rate": "./evaluate-funding-rate.py",
        "evaluate_open_interest": "./evaluate-open-interest.py",
    }

    script = script_map.get(skill_name)
    if not script:
        log.error("Unknown skill: %s", skill_name)
        return {"error": f"Unknown skill: {skill_name}"}

    log.info("Skill %s | pair=%s", skill_name, pair)
    try:
        result = subprocess.run(
            ["python3", script, "--pair", pair],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode != 0:
            log.error("Skill %s failed: %s", skill_name, result.stderr.strip())
            return {"error": f"Skill error: {result.stderr.strip()}"}
        parsed = json.loads(result.stdout)
        log.info("Skill %s OK | result=%s", skill_name, json.dumps(parsed))
        return parsed
    except json.JSONDecodeError as e:
        log.error("Skill %s returned invalid JSON: %s", skill_name, e)
        return {"error": f"Invalid JSON from skill: {e}"}
    except Exception as e:
        log.error("Skill %s exception: %s", skill_name, traceback.format_exc())
        return {"error": str(e)}


def run_agent(pair):
    """
    LiteLLM orchestrates the analysis by calling skills as tools.
    Returns the parsed JSON recommendation dict.
    """
    log.info("Agent start | pair=%s model=%s", pair, LLM_MODEL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze {pair} for swing trading direction."}
    ]

    for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
        log.info("Iteration %d/%d", iteration, MAX_AGENT_ITERATIONS)
        response = litellm.completion(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS,
            messages=messages,
            tools=TOOLS,
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        log.info("LLM response | finish_reason=%s", finish_reason)

        if finish_reason == "stop":
            log.info("Agent done | raw=%s", message.content)
            try:
                return json.loads(message.content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', message.content, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
                log.error("Non-JSON response from LLM: %s", message.content)
                return {"error": "Agent returned non-JSON", "raw": message.content}

        elif finish_reason == "tool_calls":
            messages.append(message)
            for tc in message.tool_calls:
                args = json.loads(tc.function.arguments)
                log.info("Tool call: %s | args=%s", tc.function.name, args)
                skill_result = execute_skill(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(skill_result)
                })

        else:
            log.error("Unexpected finish reason: %s", finish_reason)
            return {"error": f"Unexpected finish reason: {finish_reason}"}

    log.error("Agent did not converge after %d iterations", MAX_AGENT_ITERATIONS)
    return {"error": f"Agent did not converge after {MAX_AGENT_ITERATIONS} iterations"}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "swing-trade-evaluator-api",
        "orchestrator": LLM_MODEL,
        "max_tokens": MAX_TOKENS,
        "max_agent_iterations": MAX_AGENT_ITERATIONS,
        "subprocess_timeout": SUBPROCESS_TIMEOUT
    })


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
            return jsonify({"error": "Missing 'pair' parameter"}), 400

        pair = data["pair"].upper()
        log.info("POST /evaluate | pair=%s", pair)
        recommendation = run_agent(pair)

        if "error" in recommendation:
            log.error("Evaluation failed | %s", recommendation)
            return jsonify(recommendation), 500

        log.info("Evaluation OK | pair=%s direction=%s confidence=%s",
                 pair, recommendation.get("direction"), recommendation.get("confidence"))
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "pair": pair,
            "recommendation": recommendation
        })

    except litellm.AuthenticationError:
        log.error("Authentication error — check API key for model: %s", LLM_MODEL)
        return jsonify({"error": "Invalid API key for the configured provider"}), 500
    except litellm.RateLimitError:
        log.warning("Rate limit hit")
        return jsonify({"error": "Rate limit hit — retry later"}), 429
    except litellm.APIError as e:
        log.error("LLM API error: %s", traceback.format_exc())
        return jsonify({"error": f"LLM API error: {e}"}), 500
    except Exception as e:
        log.error("Unhandled exception: %s", traceback.format_exc())
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting | model=%s max_tokens=%d max_iterations=%d port=%d",
             LLM_MODEL, MAX_TOKENS, MAX_AGENT_ITERATIONS, port)
    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║   Swing Trade Evaluator API Server (v4 - Multi-provider)  ║
    ║                                                           ║
    ║   Running on: http://0.0.0.0:{port}                      ║
    ║   Health: http://0.0.0.0:{port}/health                   ║
    ║   Evaluate: POST http://0.0.0.0:{port}/evaluate          ║
    ║                                                           ║
    ║   Orchestrator: {LLM_MODEL}                         ║
    ║   Skills: evaluate-tf-trend, evaluate-btc-dominance      ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    app.run(host="0.0.0.0", port=port, debug=False)
