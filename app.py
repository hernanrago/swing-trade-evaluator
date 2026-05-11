#!/usr/bin/env python3
"""
API Server for Swing Trade Evaluator (v4 - Multi-provider)
LiteLLM orchestrates the analysis, supporting Anthropic, OpenAI, Gemini, and more.
"""

import os
import json
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import litellm

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
    }
]

SYSTEM_PROMPT = """You are a crypto swing trade analyst. Evaluate the given pair by calling both tools:
1. evaluate_tf_trend — gets the 1D/1W trend direction
2. evaluate_btc_dominance — gets the BTC dominance impact

After receiving both results, respond ONLY with a JSON object. No markdown, no explanation outside the JSON:
{
  "direction": "LONG" or "SHORT",
  "confidence": "high", "moderate", or "low",
  "aligned": true if both signals agree, false if they conflict,
  "reasoning": "2-3 sentence synthesis of both signals",
  "trend_summary": "one-line summary of the trend signal",
  "dominance_summary": "one-line summary of the dominance signal"
}"""


def execute_skill(skill_name, params):
    """Runs a skill script via subprocess and returns parsed JSON."""
    pair = params.get("pair", "BTC").upper()

    script_map = {
        "evaluate_tf_trend": "./evaluate-tf-trend.py",
        "evaluate_btc_dominance": "./evaluate-btc-dominance.py",
    }

    script = script_map.get(skill_name)
    if not script:
        return {"error": f"Unknown skill: {skill_name}"}

    try:
        result = subprocess.run(
            ["python3", script, "--pair", pair],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode != 0:
            return {"error": f"Skill error: {result.stderr.strip()}"}
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON from skill: {e}"}
    except Exception as e:
        return {"error": str(e)}


def run_agent(pair):
    """
    LiteLLM orchestrates the analysis by calling skills as tools.
    Returns the parsed JSON recommendation dict.
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze {pair} for swing trading direction."}
    ]

    for _ in range(MAX_AGENT_ITERATIONS):
        response = litellm.completion(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS,
            messages=messages,
            tools=TOOLS,
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "stop":
            try:
                return json.loads(message.content)
            except json.JSONDecodeError:
                return {"error": "Agent returned non-JSON", "raw": message.content}

        elif finish_reason == "tool_calls":
            messages.append(message)
            for tc in message.tool_calls:
                skill_result = execute_skill(tc.function.name, json.loads(tc.function.arguments))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(skill_result)
                })

        else:
            return {"error": f"Unexpected finish reason: {finish_reason}"}

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
        recommendation = run_agent(pair)

        if "error" in recommendation:
            return jsonify(recommendation), 500

        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "pair": pair,
            "recommendation": recommendation
        })

    except litellm.AuthenticationError:
        return jsonify({"error": "Invalid API key for the configured provider"}), 500
    except litellm.RateLimitError:
        return jsonify({"error": "Rate limit hit — retry later"}), 429
    except litellm.APIError as e:
        return jsonify({"error": f"LLM API error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
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
