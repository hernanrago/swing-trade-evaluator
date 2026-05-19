# Intraday Mode — Design Spec

**Date:** 2026-05-19  
**Scope:** Add `--mode intraday` to the swing-trade-evaluator without creating a new project. Infrastructure (agent loop, Flask, batch, subprocess execution) is shared. Only tools, prompts, and skill timeframes differ per mode.

---

## Goals

- `cli.py BTC --mode intraday` runs an intraday evaluation
- `POST /evaluate {"pair":"BTC","mode":"intraday"}` works on the HTTP API
- Existing swing behavior is unchanged (default mode = `"swing"`)
- Skills `evaluate_tf_trend` and `evaluate_market_structure` adapt their timeframes via `TRADE_MODE` env var
- Remaining 5 skills (`evaluate_funding_rate`, `evaluate_open_interest`, `evaluate_squeeze_risk`, `evaluate_entry_zone`, `evaluate_btc_dominance`) are unchanged

---

## Architecture

### Mode routing in `agent.py`

`TOOLS` and `SYSTEM_PROMPT` become dicts keyed by mode:

```python
TOOLS = {
    "swing":    [...],   # current 7 tools, unchanged
    "intraday": [...],   # same 7 tools, descriptions updated for 4H/1H/15m context
}

SYSTEM_PROMPT = {
    "swing":    "...",   # current prompt, unchanged
    "intraday": "...",   # new prompt with intraday synthesis algorithm
}
```

`run_agent(pair, mode="swing", context=None)` selects `TOOLS[mode]` and `SYSTEM_PROMPT[mode]`. The loop body is untouched.

`run_agent_batch(pairs, mode="swing")` propagates mode to each `run_agent` call.

`execute_skill(skill_name, params, context, mode)` injects `TRADE_MODE` into the subprocess env:

```python
env = {**os.environ, "TRADE_MODE": mode}
```

For in-process skill calls, `run_agent` sets `os.environ["TRADE_MODE"] = mode` once before the agent loop starts. This is safe because: (a) a single `run_agent` call is sequential, and (b) within a batch all concurrent pairs share the same mode. Skills must read `TRADE_MODE` inside the function body (not at module import time) so the value is picked up at call time.

### Skills — timeframe switching

Both skills read `TRADE_MODE` at the top of their config block:

```python
TRADE_MODE = os.environ.get("TRADE_MODE", "swing")
```

**`evaluate_tf_trend`:**

| Mode | Fast TF | Slow TF |
|---|---|---|
| `swing` | `1D` | `1W` |
| `intraday` | `4H` | `1H` |

**`evaluate_market_structure`:**

| Mode | Primary TF | Confirmation TF |
|---|---|---|
| `swing` | `4H` | `1D` |
| `intraday` | `1H` | `15m` |

No other skills change.

### `cli.py`

```
python3 cli.py BTC                   # swing (default)
python3 cli.py BTC --mode intraday   # intraday
```

Output header:
- `SWING TRADE EVALUATION` (unchanged)
- `INTRADAY TRADE EVALUATION`

### `app.py` / HTTP API

`POST /evaluate` accepts optional `mode` field (default `"swing"`):

```json
{"pair": "BTC", "mode": "intraday"}
```

`GET /health` unchanged.

---

## Intraday `SYSTEM_PROMPT`

### Role and tools

Same 7 tools, called in the same order. Tool descriptions updated to reference intraday timeframes (4H/1H bias, 1H/15m structure) and intraday semantics (session levels, trade location, time-based invalidation).

### Synthesis algorithm

Replaces the swing "structure + trend + dominance alignment" check with the intraday golden rule from the spec:

> Do not enter unless **bias**, **level**, **trigger**, **stop**, **target**, and **invalidation** are all clear.  
> If price is in the middle of a range, skip the trade.

Step-by-step inside `<thinking>`:

1. **Bias** — `evaluate_tf_trend` (4H/1H): bullish / bearish / range / no-trade
2. **Structure trigger** — `evaluate_market_structure` (1H/15m): BOS/CHOCH, sweep, reclaim, range break, retest
3. **Entry location** — `evaluate_entry_zone`: near liquidity/S&R/FVG/VWAP, not mid-range
4. **Crowding filter** — `evaluate_squeeze_risk` + `evaluate_funding_rate`: is there squeeze risk?
5. **Participation** — `evaluate_open_interest`: OI backing the move?
6. **Dominance** — `evaluate_btc_dominance`: secondary context only, not a blocker

Confidence logic:

| Condition | Confidence |
|---|---|
| Bias clear + structure trigger + entry location all pass, no squeeze | `high` |
| 1 of the three fails OR squeeze warning | `moderate` |
| 2+ fail OR (1 fails AND squeeze) | `low` |

### Output JSON (intraday additions)

```json
{
  "direction": "LONG" | "SHORT",
  "confidence": "high" | "moderate" | "low",
  "aligned": true | false,
  "squeeze_warning": null | "string",
  "reasoning": "3-4 sentence synthesis",
  "bias_summary": "4H/1H bias direction and strength",
  "structure_summary": "1H/15m trigger: BOS/CHOCH/sweep/retest, invalidation level",
  "entry_zone_summary": "location quality: near liquidity/S&R/VWAP vs mid-range",
  "funding_summary": "funding rate signal",
  "oi_summary": "OI participation signal",
  "squeeze_summary": "squeeze risk signal",
  "dominance_summary": "BTC dominance as secondary context"
}
```

`trend_summary` (swing field) is replaced by `bias_summary` in intraday mode.

---

## Batch synthesis (`_MULTI_SYSTEM_PROMPT`)

`run_synthesis` ranks a list of per-pair evaluations. The multi-pair prompt currently references swing fields (`trend_summary`, `structure_summary`). For intraday mode it must reference `bias_summary` and the updated `structure_summary`.

Same pattern: `_MULTI_SYSTEM_PROMPT` becomes a dict keyed by mode. `run_synthesis(evaluations, mode="swing")` selects the right prompt.

---

## Validation

- `TRADE_MODE` must be `"swing"` or `"intraday"`. Unknown values raise `ValueError` in agent.py.
- `mode` parameter on HTTP endpoint validated against the same set; returns 400 on unknown mode.

---

## Out of scope

- New intraday-specific skills (`evaluate_session_levels`, `evaluate_vwap_context`, `evaluate_liquidity_zones`, etc.) — future work
- UI changes
- Persisted trade journals
