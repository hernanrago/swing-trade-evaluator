# Intraday Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--mode intraday` to `cli.py` and an optional `mode` field to HTTP endpoints so the evaluator runs with intraday timeframes (4H/1H bias, 1H/15m structure) and a dedicated synthesis algorithm, without touching the default swing behavior.

**Architecture:** `TOOLS`, `SYSTEM_PROMPT`, and `_MULTI_SYSTEM_PROMPT` in `agent.py` become dicts keyed by `"swing"`/`"intraday"`. `run_agent`, `execute_skill`, `run_agent_batch`, and `run_synthesis` gain a `mode` parameter (default `"swing"`). `run_agent` writes `TRADE_MODE` to `os.environ` before the agent loop so in-process and subprocess skills both pick it up. Two skills read `TRADE_MODE` inside their function body to switch timeframes.

**Tech Stack:** Python 3, Flask, LiteLLM, BingX/OKX public APIs, pytest

---

## Files Modified

- `agent.py` — TOOLS/SYSTEM_PROMPT/_MULTI_SYSTEM_PROMPT → dicts; run_agent/execute_skill/run_agent_batch/run_synthesis gain `mode` param; validation added
- `skills/evaluate_tf_trend.py` — reads `TRADE_MODE` at call time; intraday uses 4H/1H instead of 1D/1W
- `skills/evaluate_market_structure.py` — reads `TRADE_MODE` at call time; intraday uses 1H/15m instead of 4H/1D
- `cli.py` — adds `--mode` argument; prints mode-specific header
- `app.py` — `POST /evaluations` and `POST /evaluations/top` accept optional `mode` field, validate it, pass to batch/synthesis

## Files Created

- `tests/test_intraday_agent.py` — tests for mode-aware agent.py changes
- `tests/test_intraday_skills.py` — tests for timeframe switching in skills
- `tests/test_intraday_app.py` — Flask client tests for mode on HTTP endpoints

---

### Task 1: Mode-aware TOOLS, SYSTEM_PROMPT, _MULTI_SYSTEM_PROMPT and function signatures in agent.py

**Files:**
- Modify: `agent.py`
- Create: `tests/test_intraday_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intraday_agent.py`:

```python
# tests/test_intraday_agent.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


def test_tools_is_dict_with_swing_and_intraday():
    from agent import TOOLS
    assert isinstance(TOOLS, dict)
    assert "swing" in TOOLS
    assert "intraday" in TOOLS


def test_swing_tools_has_seven_entries():
    from agent import TOOLS
    assert len(TOOLS["swing"]) == 7


def test_intraday_tools_has_seven_entries():
    from agent import TOOLS
    assert len(TOOLS["intraday"]) == 7


def test_system_prompt_is_dict_with_swing_and_intraday():
    from agent import SYSTEM_PROMPT
    assert isinstance(SYSTEM_PROMPT, dict)
    assert "swing" in SYSTEM_PROMPT
    assert "intraday" in SYSTEM_PROMPT


def test_intraday_system_prompt_contains_bias_summary():
    from agent import SYSTEM_PROMPT
    assert "bias_summary" in SYSTEM_PROMPT["intraday"]


def test_swing_system_prompt_contains_trend_summary():
    from agent import SYSTEM_PROMPT
    assert "trend_summary" in SYSTEM_PROMPT["swing"]


def test_multi_system_prompt_is_dict():
    from agent import _MULTI_SYSTEM_PROMPT
    assert isinstance(_MULTI_SYSTEM_PROMPT, dict)
    assert "swing" in _MULTI_SYSTEM_PROMPT
    assert "intraday" in _MULTI_SYSTEM_PROMPT


def test_valid_modes_constant_exists():
    from agent import VALID_MODES
    assert "swing" in VALID_MODES
    assert "intraday" in VALID_MODES


def test_run_agent_invalid_mode_raises_value_error():
    from agent import run_agent
    with pytest.raises(ValueError, match="Unknown mode"):
        run_agent("BTC", mode="scalp")


def test_run_agent_uses_intraday_tools_when_mode_intraday():
    """litellm.completion must receive intraday tools, not swing tools."""
    stop_resp = MagicMock()
    stop_resp.choices[0].finish_reason = "stop"
    stop_resp.choices[0].message.content = (
        '{"direction":"LONG","confidence":"high","aligned":true,'
        '"squeeze_warning":null,"reasoning":"r","bias_summary":"b",'
        '"structure_summary":"s","entry_zone_summary":"e","funding_summary":"f",'
        '"oi_summary":"o","squeeze_summary":"sq","dominance_summary":"d"}'
    )
    stop_resp.choices[0].message.tool_calls = None

    with patch("agent.litellm.completion", return_value=stop_resp) as mock_llm:
        from agent import run_agent, TOOLS
        run_agent("BTC", mode="intraday")

    call_kwargs = mock_llm.call_args.kwargs
    assert call_kwargs["tools"] is TOOLS["intraday"]


def test_run_agent_sets_trade_mode_env():
    """os.environ['TRADE_MODE'] must equal mode during execution."""
    observed = {}
    stop_resp = MagicMock()
    stop_resp.choices[0].finish_reason = "stop"
    stop_resp.choices[0].message.content = (
        '{"direction":"LONG","confidence":"high","aligned":true,'
        '"squeeze_warning":null,"reasoning":"r","bias_summary":"b",'
        '"structure_summary":"s","entry_zone_summary":"e","funding_summary":"f",'
        '"oi_summary":"o","squeeze_summary":"sq","dominance_summary":"d"}'
    )
    stop_resp.choices[0].message.tool_calls = None

    original_completion = None

    def capture_env(*args, **kwargs):
        observed["TRADE_MODE"] = os.environ.get("TRADE_MODE")
        return stop_resp

    with patch("agent.litellm.completion", side_effect=capture_env):
        from agent import run_agent
        run_agent("BTC", mode="intraday")

    assert observed["TRADE_MODE"] == "intraday"


def test_execute_skill_passes_trade_mode_to_subprocess():
    """Subprocess env must include TRADE_MODE=intraday when mode=intraday."""
    completed = MagicMock(returncode=0, stdout='{"ok":true}', stderr="")
    with patch("agent._skill_fn", return_value=None), \
         patch("agent.subprocess.run", return_value=completed) as mock_sub:
        from agent import execute_skill
        execute_skill("evaluate_tf_trend", {"pair": "BTC"}, mode="intraday")

    call_kwargs = mock_sub.call_args.kwargs
    assert call_kwargs["env"]["TRADE_MODE"] == "intraday"


def test_run_agent_batch_propagates_mode():
    """run_agent must be called with the mode passed to run_agent_batch."""
    calls = []

    def fake_agent(pair, mode="swing", context=None):
        calls.append(mode)
        return {"direction": "LONG", "confidence": "high"}

    with patch("agent._build_batch_context", return_value={}), \
         patch("agent.run_agent", side_effect=fake_agent):
        from agent import run_agent_batch
        run_agent_batch(["BTC", "ETH"], mode="intraday")

    assert all(m == "intraday" for m in calls)


def test_run_synthesis_uses_intraday_multi_prompt():
    """litellm.completion must receive the intraday multi-prompt."""
    resp = MagicMock()
    resp.choices[0].message.content = '[{"rank":1,"pair":"BTC","direction":"LONG","confidence":"high","aligned":true,"squeeze_warning":null,"summary":"ok"}]'

    with patch("agent.litellm.completion", return_value=resp) as mock_llm:
        from agent import run_synthesis, _MULTI_SYSTEM_PROMPT
        run_synthesis([{"pair": "BTC", "direction": "LONG"}], mode="intraday")

    system_msg = mock_llm.call_args.kwargs["messages"][0]["content"]
    assert system_msg == _MULTI_SYSTEM_PROMPT["intraday"]
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd /Users/hernan.rago/etc/swing-trade-evaluator
python -m pytest tests/test_intraday_agent.py -v 2>&1 | head -60
```

Expected: multiple FAILED / ImportError (TOOLS is not yet a dict, VALID_MODES doesn't exist).

- [ ] **Step 3: Add VALID_MODES and convert TOOLS to a dict in agent.py**

In `agent.py`, after the `_SCRIPT_MAP` block (line ~303), add the constant and restructure TOOLS.

Replace the existing `TOOLS = [...]` list (lines 120–250) with:

```python
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
```

- [ ] **Step 4: Convert SYSTEM_PROMPT to a dict in agent.py**

Replace the existing `SYSTEM_PROMPT = """..."""` string (lines 252–301) with:

```python
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
```

- [ ] **Step 5: Convert _MULTI_SYSTEM_PROMPT to a dict in agent.py**

Replace the existing `_MULTI_SYSTEM_PROMPT = """..."""` string (lines 489–523) with:

```python
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
```

- [ ] **Step 6: Update run_agent signature and body in agent.py**

Replace the `def run_agent(pair, context=None):` function with:

```python
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
```

- [ ] **Step 7: Update execute_skill, run_agent_batch, run_synthesis signatures in agent.py**

Replace `def execute_skill(skill_name, params, context=None):` with:

```python
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
```

Replace `def run_agent_batch(pairs):` with:

```python
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
```

Replace `def run_synthesis(evaluations):` with:

```python
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
```

- [ ] **Step 8: Run tests to confirm they all pass**

```bash
python -m pytest tests/test_intraday_agent.py tests/test_agent_inprocess.py -v 2>&1 | tail -30
```

Expected: all PASSED (including existing tests — defaulting to "swing" keeps them green).

- [ ] **Step 9: Commit**

```bash
git add agent.py tests/test_intraday_agent.py
git commit -m "feat: mode-aware TOOLS/SYSTEM_PROMPT and mode param on agent functions"
```

---

### Task 2: evaluate_tf_trend — TRADE_MODE-based timeframe switching

**Files:**
- Modify: `skills/evaluate_tf_trend.py`
- Create: `tests/test_intraday_skills.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intraday_skills.py`:

```python
# tests/test_intraday_skills.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

import pytest
from unittest.mock import patch, call


def _fake_candles(n=200):
    return [[i, 100.0, 105.0, 95.0, 102.0, 1000.0] for i in range(n)]


def test_tf_trend_swing_mode_fetches_1d_and_1w(monkeypatch):
    """In swing mode, fetch_klines must be called with '1d' and '1w' intervals."""
    monkeypatch.setenv("TRADE_MODE", "swing")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles) as mock_fetch:
        import evaluate_tf_trend
        evaluate_tf_trend.evaluate_tf_trend("BTC")

    intervals = [c.args[1] for c in mock_fetch.call_args_list]
    assert "1d" in intervals
    assert "1w" in intervals


def test_tf_trend_intraday_mode_fetches_4h_and_1h(monkeypatch):
    """In intraday mode, fetch_klines must be called with '4h' and '1h' intervals."""
    monkeypatch.setenv("TRADE_MODE", "intraday")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles) as mock_fetch:
        import evaluate_tf_trend
        evaluate_tf_trend.evaluate_tf_trend("BTC")

    intervals = [c.args[1] for c in mock_fetch.call_args_list]
    assert "4h" in intervals
    assert "1h" in intervals
    assert "1d" not in intervals
    assert "1w" not in intervals


def test_tf_trend_intraday_result_has_trend_4h_and_trend_1h(monkeypatch):
    """Intraday result must use trend_4h / trend_1h keys."""
    monkeypatch.setenv("TRADE_MODE", "intraday")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles):
        import evaluate_tf_trend
        result = evaluate_tf_trend.evaluate_tf_trend("BTC")

    assert "trend_4h" in result
    assert "trend_1h" in result
    assert "trend_1d" not in result
    assert "trend_1w" not in result


def test_tf_trend_swing_result_has_trend_1d_and_trend_1w(monkeypatch):
    """Swing result must keep trend_1d / trend_1w keys."""
    monkeypatch.setenv("TRADE_MODE", "swing")
    candles = _fake_candles(200)

    with patch("evaluate_tf_trend.fetch_klines", return_value=candles):
        import evaluate_tf_trend
        result = evaluate_tf_trend.evaluate_tf_trend("BTC")

    assert "trend_1d" in result
    assert "trend_1w" in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_intraday_skills.py::test_tf_trend_intraday_mode_fetches_4h_and_1h \
                 tests/test_intraday_skills.py::test_tf_trend_intraday_result_has_trend_4h_and_trend_1h -v
```

Expected: FAILED (skill ignores TRADE_MODE and always fetches 1d/1w).

- [ ] **Step 3: Update evaluate_tf_trend to read TRADE_MODE at call time**

Replace the `evaluate_tf_trend` function body in `skills/evaluate_tf_trend.py`:

```python
def evaluate_tf_trend(pair="BTC", context=None):
    """
    Analyzes trend and recommends direction.
    In swing mode: uses 1D (fast) and 1W (slow) timeframes.
    In intraday mode: uses 4H (fast) and 1H (slow) timeframes.
    """
    trade_mode = os.environ.get("TRADE_MODE", "swing")

    if trade_mode == "intraday":
        fast_tf, fast_limit, fast_label = "4h", KLINES_LIMIT_1D, "4H"
        slow_tf, slow_limit, slow_label = "1h", KLINES_LIMIT_1D, "1H"
    else:
        fast_tf, fast_limit, fast_label = "1d", KLINES_LIMIT_1D, "1D"
        slow_tf, slow_limit, slow_label = "1w", KLINES_LIMIT_1W, "1W"

    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating trend for {pair} (mode={trade_mode})...", file=sys.stderr)
    print(f"[*] Fetching {fast_label} data...", file=sys.stderr)
    candles_fast = fetch_klines(pair, fast_tf, fast_limit)

    print(f"[*] Fetching {slow_label} data...", file=sys.stderr)
    candles_slow = fetch_klines(pair, slow_tf, slow_limit)

    trend_fast = calculate_trend(candles_fast, min_required=MA_SHORT_PERIOD)
    trend_slow = calculate_trend(candles_slow, min_required=max(MA_SHORT_PERIOD // 2, 20))

    if "error" in trend_fast or "error" in trend_slow:
        return {"error": f"Calculation error - {fast_label}: {trend_fast.get('error')}, {slow_label}: {trend_slow.get('error')}"}

    bullish_fast = trend_fast["trend"] == "bullish"
    bullish_slow = trend_slow["trend"] == "bullish"

    if bullish_fast and bullish_slow:
        recommended_direction = "LONG"
        confidence = "high"
        bias = f"strong bullish bias on both timeframes"
    elif not bullish_fast and not bullish_slow:
        recommended_direction = "SHORT"
        confidence = "high"
        bias = f"strong bearish bias on both timeframes"
    elif bullish_fast:
        recommended_direction = "LONG"
        confidence = "moderate"
        bias = f"{fast_label} bullish but {slow_label} mixed"
    else:
        recommended_direction = "SHORT"
        confidence = "moderate"
        bias = f"{slow_label} bearish but {fast_label} mixed"

    reasoning = f"""
{fast_label} Trend: {trend_fast['trend'].upper()} ({trend_fast['strength']})
  Price: {trend_fast['current_price']} | MA{MA_SHORT_PERIOD}: {trend_fast[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_fast[f'ma{MA_LONG_PERIOD}']}

{slow_label} Trend: {trend_slow['trend'].upper()} ({trend_slow['strength']})
  Price: {trend_slow['current_price']} | MA{MA_SHORT_PERIOD}: {trend_slow[f'ma{MA_SHORT_PERIOD}']} | MA{MA_LONG_PERIOD}: {trend_slow[f'ma{MA_LONG_PERIOD}']}

Recommendation: {recommended_direction}
Confidence: {confidence.upper()}
Bias: {bias}
""".strip()

    fast_key = f"trend_{fast_label.lower().replace('h', 'h')}"
    slow_key = f"trend_{slow_label.lower().replace('h', 'h')}"

    return {
        "pair": pair,
        "recommended_direction": recommended_direction,
        "confidence": confidence,
        fast_key: trend_fast,
        slow_key: trend_slow,
        "reasoning": reasoning
    }
```

Note: `fast_key` / `slow_key` produce `"trend_4h"`, `"trend_1h"`, `"trend_1d"`, `"trend_1w"` from the label strings (`"4H"` → `"trend_4h"`, `"1D"` → `"trend_1d"`, etc). The label lowercasing handles this:
- `"4H".lower()` = `"4h"` → key `"trend_4h"` ✓
- `"1D".lower()` = `"1d"` → key `"trend_1d"` ✓
- `"1H".lower()` = `"1h"` → key `"trend_1h"` ✓
- `"1W".lower()` = `"1w"` → key `"trend_1w"` ✓

So `fast_key = f"trend_{fast_label.lower()}"` and `slow_key = f"trend_{slow_label.lower()}"` (simplify, drop the unnecessary replace):

```python
    fast_key = f"trend_{fast_label.lower()}"
    slow_key = f"trend_{slow_label.lower()}"
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_intraday_skills.py -k "tf_trend" -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add skills/evaluate_tf_trend.py tests/test_intraday_skills.py
git commit -m "feat: evaluate_tf_trend switches timeframes based on TRADE_MODE"
```

---

### Task 3: evaluate_market_structure — TRADE_MODE-based timeframe switching

**Files:**
- Modify: `skills/evaluate_market_structure.py`
- Modify: `tests/test_intraday_skills.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_intraday_skills.py`:

```python
from evaluate_market_structure import get_candles, find_pivots, build_swing_sequence, classify_timeframe


def _make_okx_candle(ts, high, low):
    mid = str((high + low) / 2)
    return [str(ts), mid, str(high), str(low), mid, "1000", "1000", "1000000", "1"]


def _okx_mock_resp(n=200, high=85000, low=84000):
    candles = [_make_okx_candle(i * 1000, high + i, low + i) for i in range(n)]
    candles.reverse()  # OKX returns newest first
    mock = MagicMock()
    mock.json.return_value = {"code": "0", "data": candles}
    mock.raise_for_status = MagicMock()
    return mock


def test_market_structure_swing_uses_4h_and_1d(monkeypatch):
    """In swing mode, OKX is queried with bars '4H' and '1D'."""
    monkeypatch.setenv("TRADE_MODE", "swing")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()) as mock_get:
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        evaluate_market_structure.evaluate_market_structure("BTC")

    bars = [c.kwargs["params"]["bar"] for c in mock_get.call_args_list]
    assert "4H" in bars
    assert "1D" in bars
    assert "1H" not in bars
    assert "15m" not in bars


def test_market_structure_intraday_uses_1h_and_15m(monkeypatch):
    """In intraday mode, OKX is queried with bars '1H' and '15m'."""
    monkeypatch.setenv("TRADE_MODE", "intraday")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()) as mock_get:
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        evaluate_market_structure.evaluate_market_structure("BTC")

    bars = [c.kwargs["params"]["bar"] for c in mock_get.call_args_list]
    assert "1H" in bars
    assert "15m" in bars
    assert "4H" not in bars
    assert "1D" not in bars


def test_market_structure_intraday_result_has_1h_and_15m_keys(monkeypatch):
    """Intraday result dict must have '1h' and '15m' keys, not '4h'/'1d'."""
    monkeypatch.setenv("TRADE_MODE", "intraday")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()):
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        result = evaluate_market_structure.evaluate_market_structure("BTC")

    assert "1h" in result
    assert "15m" in result
    assert "4h" not in result
    assert "1d" not in result


def test_market_structure_swing_result_has_4h_and_1d_keys(monkeypatch):
    """Swing result dict must keep '4h' and '1d' keys."""
    monkeypatch.setenv("TRADE_MODE", "swing")

    with patch("evaluate_market_structure.requests.get", return_value=_okx_mock_resp()):
        import evaluate_market_structure
        evaluate_market_structure._CANDLES_CACHE.clear()
        result = evaluate_market_structure.evaluate_market_structure("BTC")

    assert "4h" in result
    assert "1d" in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_intraday_skills.py -k "market_structure" -v
```

Expected: FAILED (skill ignores TRADE_MODE and always uses 4H/1D).

- [ ] **Step 3: Update generate_reasoning to accept timeframe labels**

In `skills/evaluate_market_structure.py`, replace the `generate_reasoning` function signature and body:

```python
def generate_reasoning(tf_primary, tf_confirm, conclusion, confidence,
                        primary_label="4H", confirm_label="1D"):
    """Generate a concise operative reasoning string."""
    s_prim = tf_primary["structure"]
    s_conf = tf_confirm["structure"]
    inv_prim = tf_primary.get("invalidation")
    inv_conf = tf_confirm.get("invalidation")

    def fmt(v):
        return f"${v:,.0f}" if v is not None else "N/A"

    if conclusion == "LONG" and confidence == "high":
        return (
            f"Both {primary_label} and {confirm_label} show HH/HL structure. "
            f"{primary_label} bullish structure valid while price closes above {fmt(inv_prim)}. "
            f"{confirm_label} bullish structure valid while price closes above {fmt(inv_conf)}."
        )
    elif conclusion == "SHORT" and confidence == "high":
        return (
            f"Both {primary_label} and {confirm_label} show LH/LL structure. "
            f"{primary_label} bearish structure valid while price closes below {fmt(inv_prim)}. "
            f"{confirm_label} bearish structure valid while price closes below {fmt(inv_conf)}."
        )
    elif conclusion in ("LONG", "SHORT") and confidence == "moderate":
        if s_prim != "UNDEFINED":
            dominant, inv = primary_label, inv_prim
            structure_str = s_prim
        else:
            dominant, inv = confirm_label, inv_conf
            structure_str = s_conf
        direction = "bullish" if conclusion == "LONG" else "bearish"
        word = "above" if conclusion == "LONG" else "below"
        return (
            f"{dominant} shows {structure_str} structure ({direction}); "
            f"the other timeframe is structurally undefined. "
            f"Structure valid while price closes {word} {fmt(inv)}."
        )
    elif conclusion == "CONFLICT":
        return (
            f"{primary_label} shows {s_prim} structure while {confirm_label} shows {s_conf} structure. "
            f"Timeframes conflict — no structural edge. Wait for alignment."
        )
    else:
        return f"Both {primary_label} and {confirm_label} are structurally undefined. Recent swings do not confirm HH/HL or LH/LL."
```

- [ ] **Step 4: Update evaluate_market_structure to read TRADE_MODE and use dynamic timeframes**

Replace the `evaluate_market_structure` function body:

```python
def evaluate_market_structure(pair="BTC", context=None):
    """
    Full analysis: fetch candles, detect swings, classify structure.
    In swing mode: uses 4H (primary) and 1D (confirmation).
    In intraday mode: uses 1H (primary) and 15m (confirmation).
    """
    trade_mode = os.environ.get("TRADE_MODE", "swing")

    if trade_mode == "intraday":
        timeframes = [("1H", "1h", MS_PIVOT_N_4H), ("15m", "15m", MS_PIVOT_N_1D)]
    else:
        timeframes = [("4H", "4h", MS_PIVOT_N_4H), ("1D", "1d", MS_PIVOT_N_1D)]

    instrument = _normalize_pair(pair)
    base = instrument.replace("-USDT-SWAP", "")
    print(f"[*] Evaluating market structure for {instrument} (mode={trade_mode})...", file=sys.stderr)

    results = {}
    for bar, label, n in timeframes:
        candles = get_candles(instrument, bar, MS_LOOKBACK)
        if isinstance(candles, dict) and "error" in candles:
            return {"pair": base, "instrument": instrument, "error": candles["error"]}

        pivots   = find_pivots(candles, n)
        sequence = build_swing_sequence(pivots, MS_EQUAL_TOLERANCE_PCT)
        tf_result = classify_timeframe(sequence, MS_EQUAL_TOLERANCE_PCT)
        results[label] = tf_result
        print(f"[*] {bar}: structure={tf_result['structure']}", file=sys.stderr)

    primary_bar, primary_label, _ = timeframes[0]
    confirm_bar, confirm_label, _ = timeframes[1]

    conclusion, confidence = combine_conclusions(
        results[primary_label]["structure"], results[confirm_label]["structure"]
    )
    reasoning = generate_reasoning(
        results[primary_label], results[confirm_label],
        conclusion, confidence,
        primary_bar, confirm_bar,
    )

    return {
        "pair":          base,
        "instrument":    instrument,
        primary_label:   results[primary_label],
        confirm_label:   results[confirm_label],
        "conclusion":    conclusion,
        "confidence":    confidence,
        "reasoning":     reasoning,
    }
```

- [ ] **Step 5: Run all skill tests to confirm they pass**

```bash
python -m pytest tests/test_intraday_skills.py tests/test_market_structure.py -v
```

Expected: all PASSED. The existing normalize/get_candles/pivot tests remain green because they don't call `evaluate_market_structure` directly.

- [ ] **Step 6: Commit**

```bash
git add skills/evaluate_market_structure.py tests/test_intraday_skills.py
git commit -m "feat: evaluate_market_structure switches timeframes based on TRADE_MODE"
```

---

### Task 4: cli.py — --mode flag and mode-specific header

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Update cli.py**

Replace the entire `cli.py` with:

```python
#!/usr/bin/env python3
"""
CLI entry point for the Swing/Intraday Trade Evaluator.
Agent logic lives in agent.py.
"""

import json
import argparse
from dotenv import load_dotenv
load_dotenv()
from agent import run_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate crypto trade direction")
    parser.add_argument("pair", help="Cryptocurrency pair (e.g., BTC, ETH, SOL)")
    parser.add_argument(
        "--mode",
        choices=["swing", "intraday"],
        default="swing",
        help="Evaluation mode: swing (default) or intraday",
    )
    args = parser.parse_args()

    result = run_agent(args.pair.upper(), mode=args.mode)

    header = "INTRADAY TRADE EVALUATION" if args.mode == "intraday" else "SWING TRADE EVALUATION"
    print("\n" + "=" * 70)
    print(header)
    print("=" * 70)
    print(json.dumps(result, indent=2))
```

- [ ] **Step 2: Smoke-test argparse (no LLM call)**

```bash
python cli.py --help
```

Expected: shows `--mode {swing,intraday}` in the usage.

- [ ] **Step 3: Commit**

```bash
git add cli.py
git commit -m "feat: cli.py --mode flag with intraday header"
```

---

### Task 5: app.py — mode param on /evaluations and /evaluations/top

**Files:**
- Modify: `app.py`
- Create: `tests/test_intraday_app.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intraday_app.py`:

```python
# tests/test_intraday_app.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_evals():
    return [{"pair": "BTC", "direction": "LONG", "confidence": "high", "aligned": True,
             "squeeze_warning": None, "reasoning": "r", "bias_summary": "b",
             "structure_summary": "s", "entry_zone_summary": "e",
             "funding_summary": "f", "oi_summary": "o", "squeeze_summary": "sq",
             "dominance_summary": "d"}]


def _mock_ranked():
    return [{"rank": 1, "pair": "BTC", "direction": "LONG", "confidence": "high",
             "aligned": True, "squeeze_warning": None, "summary": "ok"}]


def test_evaluations_passes_mode_intraday_to_batch(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        resp = client.post("/evaluations", json={"pairs": ["BTC"], "mode": "intraday"})

    assert resp.status_code == 200
    mock_batch.assert_called_once_with(["BTC"], mode="intraday")


def test_evaluations_defaults_to_swing(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        resp = client.post("/evaluations", json={"pairs": ["BTC"]})

    assert resp.status_code == 200
    mock_batch.assert_called_once_with(["BTC"], mode="swing")


def test_evaluations_returns_400_for_unknown_mode(client):
    resp = client.post("/evaluations", json={"pairs": ["BTC"], "mode": "scalp"})
    assert resp.status_code == 400
    assert "mode" in resp.get_json()["error"].lower()


def test_evaluations_passes_mode_to_synthesis(client):
    with patch("app.run_agent_batch", return_value=_mock_evals()), \
         patch("app.run_synthesis", return_value=_mock_ranked()) as mock_synth:
        client.post("/evaluations", json={"pairs": ["BTC"], "mode": "intraday"})

    mock_synth.assert_called_once_with(_mock_evals(), mode="intraday")


def test_evaluations_top_passes_mode_to_batch(client):
    mock_tickers = [{"symbol": "BTC-USDT", "quoteVolume": "1000000"}]

    with patch("app.requests.get") as mock_get, \
         patch("app.run_agent_batch", return_value=_mock_evals()) as mock_batch, \
         patch("app.run_synthesis", return_value=_mock_ranked()):
        ticker_resp = mock_get.return_value
        ticker_resp.raise_for_status = lambda: None
        ticker_resp.json.return_value = {"data": mock_tickers}

        resp = client.post("/evaluations/top", json={"top": 1, "mode": "intraday"})

    assert resp.status_code == 200
    _, kwargs = mock_batch.call_args
    assert kwargs.get("mode") == "intraday"


def test_evaluations_top_returns_400_for_unknown_mode(client):
    resp = client.post("/evaluations/top", json={"mode": "scalp"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_intraday_app.py -v 2>&1 | head -40
```

Expected: FAILED (run_agent_batch/run_synthesis called without mode keyword; no validation logic).

- [ ] **Step 3: Add mode validation and propagation to /evaluations in app.py**

In `app.py`, update the imports line to also import `VALID_MODES`:

```python
from agent import run_agent, run_agent_batch, run_synthesis, execute_skill, LLM_MODEL, MAX_TOKENS, MAX_AGENT_ITERATIONS, SUBPROCESS_TIMEOUT, VALID_MODES
```

In the `evaluations()` function, after the `pairs = [p.upper() for p in pairs]` line, add:

```python
        mode = data.get("mode", "swing")
        if mode not in VALID_MODES:
            return {"error": f"Unknown mode '{mode}' — must be one of {sorted(VALID_MODES)}"}, 400

        log.info("POST /evaluations | pairs=%s mode=%s", pairs, mode)
```

Remove the existing `log.info("POST /evaluations | pairs=%s", pairs)` line.

Change the batch and synthesis calls:

```python
        evals = run_agent_batch(pairs, mode=mode)
        if not evals:
            return {"error": "All evaluations failed"}, 502

        ranked = run_synthesis(evals, mode=mode)
```

- [ ] **Step 4: Add mode to /evaluations/top in app.py**

In the `evaluations_top()` function, after `top_n = int(data.get("top", 10))`, add:

```python
        mode = data.get("mode", "swing")
        if mode not in VALID_MODES:
            return {"error": f"Unknown mode '{mode}' — must be one of {sorted(VALID_MODES)}"}, 400

        log.info("POST /evaluations/top | top=%d mode=%s", top_n, mode)
```

Remove the existing `log.info("POST /evaluations/top | top=%d", top_n)` line.

Change the batch and synthesis calls:

```python
        evals = run_agent_batch(pairs, mode=mode)
        if not evals:
            return {"error": "All evaluations failed"}, 502

        ranked = run_synthesis(evals, mode=mode)
```

- [ ] **Step 5: Run all tests to confirm everything passes**

```bash
python -m pytest tests/test_intraday_app.py tests/test_intraday_agent.py tests/test_intraday_skills.py tests/test_agent_inprocess.py tests/test_market_structure.py -v 2>&1 | tail -30
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_intraday_app.py
git commit -m "feat: /evaluations and /evaluations/top accept mode param with validation"
```

---

### Task 6: Final integration smoke test

- [ ] **Step 1: Run the full test suite**

```bash
python -m pytest tests/ -v 2>&1 | tail -40
```

Expected: all PASSED, no regressions.

- [ ] **Step 2: Quick CLI smoke test (no LLM call — just confirm argparse and imports work)**

```bash
python -c "
import os; os.environ['TRADE_MODE'] = 'intraday'
import sys; sys.path.insert(0, 'skills')
from evaluate_tf_trend import evaluate_tf_trend
from evaluate_market_structure import evaluate_market_structure
print('Imports OK')
print('TRADE_MODE:', os.environ.get('TRADE_MODE'))
"
```

Expected: `Imports OK`, `TRADE_MODE: intraday`

- [ ] **Step 3: Verify api.http examples still work**

Add intraday examples to `api.http`:

```http
### Evaluar pares en modo intraday
POST {{baseUrl}}/evaluations
Content-Type: application/json

{
  "pairs": ["BTC", "ETH"],
  "mode": "intraday"
}

### Top 5 en modo intraday
POST {{baseUrl}}/evaluations/top
Content-Type: application/json

{
  "top": 5,
  "mode": "intraday"
}
```

- [ ] **Step 4: Commit**

```bash
git add api.http
git commit -m "docs: add intraday mode examples to api.http"
```
