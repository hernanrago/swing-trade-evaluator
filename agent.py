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
import threading
import litellm
from datetime import datetime, timedelta
import functools
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

# --- Config (override via environment variables) ---
LLM_MODEL            = os.environ.get("LLM_MODEL",             "claude-opus-4-7")
MAX_TOKENS           = int(os.environ.get("MAX_TOKENS",           "4096"))
MAX_AGENT_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS",  "10"))
SUBPROCESS_TIMEOUT   = int(os.environ.get("SUBPROCESS_TIMEOUT",    "30"))
MAX_BATCH_WORKERS    = int(os.environ.get("MAX_BATCH_WORKERS", "10"))

# --- Timed cache decorator ---
def timed_cache(seconds=300):
    """Simple TTL cache for zero-argument callables."""
    def decorator(fn):
        _cache = {}
        @functools.wraps(fn)
        def wrapper():
            now = datetime.now()
            if "v" not in _cache or now - _cache["t"] > timedelta(seconds=seconds):
                _cache["v"] = fn()
                _cache["t"] = now
            return _cache["v"]
        return wrapper
    return decorator


# --- Shared data fetchers ---
_COINGECKO_IDS = {
    "BTC": "bitcoin",    "ETH": "ethereum",   "SOL": "solana",
    "BNB": "binancecoin","XRP": "ripple",     "ADA": "cardano",
    "DOGE": "dogecoin",  "DOT": "polkadot",   "AVAX": "avalanche-2",
    "LINK": "chainlink", "ATOM": "cosmos",    "LTC": "litecoin",
    "NEAR": "near",      "UNI": "uniswap",    "MATIC": "matic-network",
}
_COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")


@timed_cache(seconds=300)
def _cached_btc_dominance():
    headers = {"x-cg-demo-api-key": _COINGECKO_API_KEY} if _COINGECKO_API_KEY else {}
    resp = requests.get("https://api.coingecko.com/api/v3/global", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()["data"]["market_cap_percentage"]["btc"]


@timed_cache(seconds=60)
def _cached_premium_index():
    """Fetches all BingX perpetual premiumIndex entries in one call (no symbol param)."""
    resp = requests.get(
        "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex",
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return {
        item["symbol"]: {
            "lastFundingRate": float(item["lastFundingRate"]),
            "markPrice":       float(item["markPrice"]),
        }
        for item in data
        if "symbol" in item and "lastFundingRate" in item and "markPrice" in item
    }


def _fetch_spot_prices_batch(pairs):
    """Fetches spot prices for all pairs in one CoinGecko call."""
    bases = [p.replace("USDT", "").upper() for p in pairs]
    ids   = [_COINGECKO_IDS[b] for b in bases if b in _COINGECKO_IDS]
    if not ids:
        return {}
    headers = {"x-cg-demo-api-key": _COINGECKO_API_KEY} if _COINGECKO_API_KEY else {}
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ",".join(ids), "vs_currencies": "usd"},
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    id_to_base = {v: k for k, v in _COINGECKO_IDS.items()}
    return {id_to_base[cg_id]: data["usd"] for cg_id, data in raw.items() if cg_id in id_to_base}


def _build_batch_context(pairs):
    """Pre-fetches all shareable data for the batch. Partial failures are swallowed."""
    ctx = {}
    try:
        ctx["btc_dominance"] = _cached_btc_dominance()
    except Exception as e:
        log.warning("Pre-fetch btc_dominance failed: %s", e)
    try:
        ctx["premium_index"] = _cached_premium_index()
    except Exception as e:
        log.warning("Pre-fetch premium_index failed: %s", e)
    try:
        ctx["spot_prices"] = _fetch_spot_prices_batch(pairs)
    except Exception as e:
        log.warning("Pre-fetch spot_prices failed: %s", e)
    return ctx


VALID_MODES = {"swing", "intraday"}

_TOOLS_SWING = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_tf_trend",
            "description": "Analyzes 1D/1W moving averages for a crypto pair and returns a trend direction recommendation (LONG or SHORT).",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol without the USDT suffix, e.g. BTC, ETH, SOL"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"}},
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
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"}},
                "required": ["pair"]
            }
        }
    },
]

_TOOLS_INTRADAY = [
    {
        "type": "function",
        "function": {
            "name": "evaluate_tf_trend",
            "description": "Analyzes 4H/1H moving averages for a crypto pair to determine intraday directional bias (bullish, bearish, or range). Call this first to establish the higher-timeframe bias before looking for entries.",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol without the USDT suffix, e.g. BTC, ETH, SOL"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_btc_dominance",
            "description": "Fetches BTC dominance from CoinGecko. Use as secondary context only — do not use as a blocker for intraday trades.",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_funding_rate",
            "description": "Fetches the current perpetual funding rate for a pair. High positive funding means crowded longs (bearish warning). High negative funding means crowded shorts (bullish warning). Use as a filter.",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_open_interest",
            "description": "Analyzes OI change vs price change to validate directional participation. OI rising with price = capital backing the intraday move. Use to confirm or deny whether the move has real participation.",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_squeeze_risk",
            "description": "Detects crowded-trade risk by scoring funding, basis, L/S ratio, OI trend, and liquidation bias. Returns crowded_side and risk_level. Must be called to assess whether an intraday entry is crowded.",
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_market_structure",
            "description": (
                "Analyzes intraday market structure on 1H and 15m timeframes using pivot-based swing detection. "
                "Returns HH/HL (bullish), LH/LL (bearish), or UNDEFINED per timeframe, plus a combined "
                "conclusion, confidence level, and invalidation/range levels. "
                "Use to identify structural triggers (BOS/CHOCH, sweep, reclaim, retest) and assess trade location."
            ),
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"}},
                "required": ["pair"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_entry_zone",
            "description": (
                "Evaluates whether a clear, technically justified, risk-manageable intraday entry zone exists. "
                "Combines S/R, FVG, order block, VWAP, range/retest/sweep context, and structure-based invalidation. "
                "Returns pass/fail rating. Fail if price is in the middle of a range."
            ),
            "parameters": {
                "type": "object",
                "properties": {"pair": {"type": "string", "description": "Cryptocurrency symbol or instrument, e.g. BTC, ETH, SOL, BTCUSDT, BTC-USDT-SWAP"}},
                "required": ["pair"]
            }
        }
    },
]

TOOLS = {
    "swing":    _TOOLS_SWING,
    "intraday": _TOOLS_INTRADAY,
}

_SYSTEM_PROMPT_SWING = """You are a crypto swing trade analyst. Evaluate the given pair by calling all seven tools:
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

_SYSTEM_PROMPT_INTRADAY = """You are a crypto intraday trade analyst. Evaluate the given pair by calling all seven tools:
1. evaluate_tf_trend — gets the 4H/1H bias (bullish/bearish/range)
2. evaluate_market_structure — analyzes 1H/15m structure (BOS/CHOCH, sweep, retest) and returns invalidation levels
3. evaluate_btc_dominance — gets BTC dominance as secondary context
4. evaluate_funding_rate — gets the funding rate as a contrarian filter
5. evaluate_open_interest — validates whether OI backs the price move
6. evaluate_squeeze_risk — detects crowded-trade risk
7. evaluate_entry_zone — validates if there is a technically acceptable entry zone near a level

### THE INTRADAY GOLDEN RULE

Do not enter unless bias, level, trigger, stop, target, and invalidation are all clear.
If price is in the middle of a range, skip the trade.

### SYNTHESIS ALGORITHM

After all seven tools return results, reason step-by-step inside <thinking> tags before writing the JSON.

Inside <thinking>, you MUST:
1. Bias (4H/1H): evaluate_tf_trend → bullish / bearish / range / no-trade
2. Structure trigger (1H/15m): evaluate_market_structure → BOS/CHOCH, sweep, reclaim, range break, retest; note invalidation level
3. Entry location: evaluate_entry_zone → near liquidity/S&R/FVG/VWAP (pass) vs mid-range (fail)
4. Crowding filter: evaluate_squeeze_risk + evaluate_funding_rate → squeeze risk present?
5. Participation: evaluate_open_interest → OI backing the move?
6. Dominance: evaluate_btc_dominance → secondary context only, not a blocker
7. Choose direction: bias and structure trigger must agree. If range or undefined, direction = the triggered side.
8. Determine confidence:
   - "high"     → bias clear + structure trigger + entry location all pass, no squeeze
   - "moderate" → 1 of the three fails OR squeeze warning (but not both)
   - "low"      → 2+ fail OR (1 fail AND squeeze)
9. State conclusion before writing JSON.

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
  "reasoning": "3-4 sentence synthesis naming the key trigger and any risks",
  "bias_summary": "one-line summary of 4H/1H bias direction and strength",
  "structure_summary": "one-line summary of 1H/15m trigger: BOS/CHOCH/sweep/retest, invalidation level",
  "entry_zone_summary": "one-line summary of entry-zone location quality and rating",
  "funding_summary": "one-line summary of the funding rate signal",
  "oi_summary": "one-line summary of the open interest signal",
  "squeeze_summary": "one-line summary of the squeeze risk",
  "dominance_summary": "one-line summary of BTC dominance as secondary context"
}
```"""

SYSTEM_PROMPT = {
    "swing":    _SYSTEM_PROMPT_SWING,
    "intraday": _SYSTEM_PROMPT_INTRADAY,
}

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

# --- In-process function map ---
def _get_function_map():
    import sys as _sys
    skills_dir = os.path.join(_BASE_DIR, "skills")
    if skills_dir not in _sys.path:
        _sys.path.insert(0, skills_dir)
    from evaluate_btc_dominance    import evaluate_btc_dominance
    from evaluate_funding_rate     import evaluate_funding_rate
    from evaluate_squeeze_risk     import evaluate_squeeze_risk
    from evaluate_open_interest    import evaluate_open_interest
    from evaluate_tf_trend         import evaluate_tf_trend
    from evaluate_market_structure import evaluate_market_structure
    from evaluate_entry_zone       import evaluate_entry_zone
    return {
        "evaluate_btc_dominance":    evaluate_btc_dominance,
        "evaluate_funding_rate":     evaluate_funding_rate,
        "evaluate_squeeze_risk":     evaluate_squeeze_risk,
        "evaluate_open_interest":    evaluate_open_interest,
        "evaluate_tf_trend":         evaluate_tf_trend,
        "evaluate_market_structure": evaluate_market_structure,
        "evaluate_entry_zone":       evaluate_entry_zone,
    }

_FUNCTION_MAP = None
_FUNCTION_MAP_LOCK = threading.Lock()

def _skill_fn(name):
    global _FUNCTION_MAP
    if _FUNCTION_MAP is None:
        with _FUNCTION_MAP_LOCK:
            if _FUNCTION_MAP is None:
                _FUNCTION_MAP = _get_function_map()
    return _FUNCTION_MAP.get(name)


def execute_skill(skill_name, params, context=None, mode="swing"):
    """Calls a skill in-process if available, otherwise falls back to subprocess."""
    pair = params.get("pair", "BTC").upper()
    log.info("Skill %s | pair=%s mode=%s", skill_name, pair, mode)

    fn = _skill_fn(skill_name)
    if fn is not None:
        try:
            result = fn(pair, context=context)
            log.info("Skill %s OK (in-process) | result=%s", skill_name, json.dumps(result))
            return result
        except Exception as e:
            log.error("Skill %s in-process error: %s", skill_name, traceback.format_exc())
            return {"error": str(e)}

    # Fallback: subprocess (for skills not in _FUNCTION_MAP)
    script = _SCRIPT_MAP.get(skill_name)
    if not script:
        log.error("Unknown skill: %s", skill_name)
        return {"error": f"Unknown skill: {skill_name}"}
    try:
        result = subprocess.run(
            ["python3", script, "--pair", pair],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=_BASE_DIR,
            env={**os.environ, "TRADE_MODE": mode},
        )
        if result.returncode != 0:
            log.error("Skill %s failed: %s", skill_name, result.stderr.strip())
            return {"error": f"Skill error: {result.stderr.strip()}"}
        parsed = json.loads(result.stdout)
        log.info("Skill %s OK (subprocess) | result=%s", skill_name, json.dumps(parsed))
        return parsed
    except json.JSONDecodeError as e:
        log.error("Skill %s returned invalid JSON: %s", skill_name, e)
        return {"error": f"Invalid JSON from skill: {e}"}
    except Exception as e:
        log.error("Skill %s exception: %s", skill_name, traceback.format_exc())
        return {"error": str(e)}


def run_agent(pair, mode="swing", context=None):
    """
    Orchestrates the analysis via LiteLLM, calling skills as tools.
    Returns the parsed JSON recommendation dict.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Must be one of {sorted(VALID_MODES)}")
    os.environ["TRADE_MODE"] = mode

    log.info("Agent start | pair=%s mode=%s model=%s", pair, mode, LLM_MODEL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT[mode]},
        {"role": "user", "content": f"Analyze {pair} for {'intraday' if mode == 'intraday' else 'swing'} trading direction."}
    ]

    for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
        log.info("Iteration %d/%d", iteration, MAX_AGENT_ITERATIONS)
        response = litellm.completion(
            model=LLM_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=0,
            messages=messages,
            tools=TOOLS[mode],
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        log.info("LLM response | finish_reason=%s", finish_reason)

        if finish_reason == "stop":
            log.info("Agent done | raw=%s", message.content)
            content = message.content
            match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                pass
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
                skill_result = execute_skill(tc.function.name, args, context=context, mode=mode)
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


def run_agent_batch(pairs, mode="swing"):
    """
    Evaluates all pairs concurrently using ThreadPoolExecutor.
    """
    context = _build_batch_context(pairs)
    log.info("Batch context built | keys=%s mode=%s", list(context.keys()), mode)

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_BATCH_WORKERS, len(pairs))) as executor:
        futures = {executor.submit(run_agent, pair, mode, context): pair for pair in pairs}
        for future in as_completed(futures):
            pair = futures[future]
            try:
                rec = future.result()
            except Exception as e:
                log.warning("Pair %s raised exception: %s", pair, e)
                continue
            if "error" not in rec:
                results.append({**rec, "pair": pair})
            else:
                log.warning("Skipping pair=%s due to error: %s", pair, rec.get("error"))

    return results


_MULTI_SYSTEM_PROMPT_SWING = """You are a crypto swing trade analyst. Below is an array of evaluations for multiple pairs. Each entry contains the full analysis for one symbol.

```json
{evaluations}
```

### YOUR TASK

Review the array and produce a ranked summary:

1. Rank the pairs by swing trading quality: direction, confidence, aligned, squeeze_warning.
2. Identify the top 3 opportunities (best LONG candidates and best SHORT candidates).
3. Flag any pairs with high squeeze risk.
4. Highlight conflicts between signals per pair.

### RESPONSE FORMAT

<thinking>
(rank by opportunity quality, note key conflicts, note squeeze warnings)
</thinking>

```json
[
  {
    "rank": 1,
    "pair": "BTCUSDT",
    "direction": "LONG",
    "confidence": "high",
    "aligned": true,
    "squeeze_warning": null,
    "summary": "2-3 sentence synthesis"
  },
  ...
]
```"""

_MULTI_SYSTEM_PROMPT_INTRADAY = """You are a crypto intraday trade analyst. Below is an array of intraday evaluations for multiple pairs. Each entry contains the full intraday analysis for one symbol.

```json
{evaluations}
```

### YOUR TASK

Review the array and produce a ranked summary:

1. Rank the pairs by intraday trading quality: direction, confidence, aligned, squeeze_warning.
2. Identify the top 3 intraday opportunities (best LONG and SHORT candidates).
3. Flag any pairs with high squeeze risk.
4. Highlight pairs where bias or structure trigger is unclear or mid-range.

### RESPONSE FORMAT

<thinking>
(rank by opportunity quality, note key conflicts, note squeeze warnings)
</thinking>

```json
[
  {
    "rank": 1,
    "pair": "BTCUSDT",
    "direction": "LONG",
    "confidence": "high",
    "aligned": true,
    "squeeze_warning": null,
    "summary": "2-3 sentence synthesis"
  },
  ...
]
```"""

_MULTI_SYSTEM_PROMPT = {
    "swing":    _MULTI_SYSTEM_PROMPT_SWING,
    "intraday": _MULTI_SYSTEM_PROMPT_INTRADAY,
}


def run_synthesis(evaluations, mode="swing"):
    """
    Sends a batch of per-pair evaluations to the LLM for ranked synthesis.
    Returns a list of ranked recommendation dicts.
    """
    log.info("Synthesis start | num_pairs=%d mode=%s", len(evaluations), mode)

    for eval_item in evaluations:
        if "pair" not in eval_item:
            eval_item["pair"] = "UNKNOWN"

    messages = [
        {"role": "system", "content": _MULTI_SYSTEM_PROMPT[mode]},
        {"role": "user", "content": f"Synthesize the following {len(evaluations)} evaluations:\n\n{json.dumps(evaluations, indent=2)}"}
    ]

    response = litellm.completion(
        model=LLM_MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        messages=messages,
    )

    content = response.choices[0].message.content
    log.info("Synthesis done | raw=%s", content[:200])

    match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    log.error("Synthesis returned non-JSON: %s", content)
    return {"error": "Synthesis returned non-JSON", "raw": content}
