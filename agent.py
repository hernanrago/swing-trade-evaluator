#!/usr/bin/env python3
"""
Swing Trade Agent — core logic.
Shared by app.py (HTTP server) and cli.py (CLI).
"""

import os
import re
import json
import logging
import subprocess
import traceback
import litellm

log = logging.getLogger(__name__)

# --- Config (override via environment variables) ---
LLM_MODEL            = os.environ.get("LLM_MODEL",             "claude-opus-4-7")
MAX_TOKENS           = int(os.environ.get("MAX_TOKENS",           "4096"))
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS",  "10"))
SUBPROCESS_TIMEOUT   = int(os.environ.get("SUBPROCESS_TIMEOUT",    "30"))

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
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_squeeze_risk",
            "description": "Detects crowded-trade risk by scoring 5 signals: funding rate, perpetual basis vs spot, long/short account ratio, OI trend, and liquidation bias. Returns crowded_side (long/short/neutral) and risk_level. High score = long crowding (long squeeze risk). Low score = short crowding (short squeeze risk). Must be called to assess whether a trade is entering a crowded position.",
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
            "name": "evaluate_market_structure",
            "description": (
                "Analyzes market structure on 4H and 1D timeframes using pivot-based swing detection. "
                "Returns HH/HL (LONG), LH/LL (SHORT), or UNDEFINED per timeframe, plus a combined "
                "conclusion, confidence level, and invalidation/range levels. "
                "Call this to assess whether market structure supports the intended trade direction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"
                    }
                },
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_entry_zone",
            "description": (
                "Evaluates whether a clear, technically justified, risk-manageable entry zone exists "
                "for the current setup. Combines support/resistance, FVG, order block, Fibonacci, "
                "range/retest/sweep context, and structure-based invalidation. Returns pass/fail rating "
                "for the checklist item only (not an entry signal)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {
                        "type": "string",
                        "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"
                    }
                },
                "required": ["pair"]
            }
        }
    },
]

SYSTEM_PROMPT = """You are a crypto swing trade analyst. Evaluate the given pair by calling all seven tools:
1. evaluate_tf_trend — gets the 1D/1W trend direction
2. evaluate_market_structure — analyzes 4H/1D swing structure (HH/HL or LH/LL) and returns invalidation levels
3. evaluate_btc_dominance — gets the BTC dominance impact
4. evaluate_funding_rate — gets the funding rate as a contrarian filter
5. evaluate_open_interest — validates whether OI backs the price move
6. evaluate_squeeze_risk — detects crowded-trade risk (long/short squeeze risk)
7. evaluate_entry_zone — validates if there is a technically acceptable, risk-manageable entry zone

### SYNTHESIS ALGORITHM

After all six tools return results, reason step-by-step inside <thinking> tags before writing the JSON.

Inside <thinking>, you MUST:
1. List each of the three directional signals and the direction it implies:
   - Structure: market_structure.conclusion → LONG / SHORT / CONFLICT / UNDEFINED
   - Trend: tf_trend.recommended_direction → LONG / SHORT
   - Dominance: btc_dominance direction → LONG / SHORT
2. Choose direction: use market_structure.conclusion as primary. If CONFLICT or UNDEFINED, fall back to tf_trend.
3. Count conflicts: how many of the three signals disagree with the chosen direction.
4. Check squeeze: does squeeze_risk.crowded_side match the chosen direction?
5. Determine aligned: true ONLY if all three signals agree with direction.
6. Determine confidence:
   - "high"     → 0 conflicts AND no squeeze match
   - "moderate" → 1 conflict OR squeeze match (but not both)
   - "low"      → 2+ conflicts OR (1 conflict AND squeeze match)
7. State your conclusion before writing the JSON.

### RESPONSE FORMAT

<thinking>
(step-by-step reasoning as described above)
</thinking>

```json
{
  "direction": "LONG" or "SHORT",
  "confidence": "high", "moderate", or "low",
  "aligned": true or false,
  "squeeze_warning": null or warning string,
  "reasoning": "3-4 sentence synthesis naming any conflicts explicitly",
  "trend_summary": "one-line summary of the trend signal",
  "structure_summary": "one-line summary of the market structure signal (4H/1D structure, invalidation level)",
  "dominance_summary": "one-line summary of the dominance signal",
  "funding_summary": "one-line summary of the funding rate signal",
  "oi_summary": "one-line summary of the open interest signal",
  "squeeze_summary": "one-line summary of the squeeze risk",
  "entry_zone_summary": "one-line summary of entry-zone quality and rating"
}
```"""

_SCRIPT_MAP = {
    "evaluate_tf_trend":          "./skills/evaluate_tf_trend.py",
    "evaluate_btc_dominance":     "./skills/evaluate_btc_dominance.py",
    "evaluate_funding_rate":      "./skills/evaluate_funding_rate.py",
    "evaluate_open_interest":     "./skills/evaluate_open_interest.py",
    "evaluate_squeeze_risk":      "./skills/evaluate_squeeze_risk.py",
    "evaluate_market_structure":  "./skills/evaluate_market_structure.py",
    "evaluate_entry_zone":        "./skills/evaluate_entry_zone.py",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def execute_skill(skill_name, params):
    """Runs a skill script via subprocess and returns parsed JSON."""
    pair = params.get("pair", "BTC").upper()
    script = _SCRIPT_MAP.get(skill_name)
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
            cwd=_BASE_DIR
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
    Orchestrates the analysis via LiteLLM, calling skills as tools.
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
            temperature=0,
            messages=messages,
            tools=TOOLS,
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        log.info("LLM response | finish_reason=%s", finish_reason)

        if finish_reason == "stop":
            log.info("Agent done | raw=%s", message.content)
            content = message.content
            # 1. Try ```json ... ``` block (preferred — CoT response format)
            match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            # 2. Try bare JSON object
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                pass
            # 3. Try first {...} block
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            log.error("Non-JSON response from LLM: %s", content)
            return {"error": "Agent returned non-JSON", "raw": content}

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
