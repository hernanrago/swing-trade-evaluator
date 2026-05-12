# Design: evaluate_market_structure skill

**Date:** 2026-05-12
**Status:** Approved

## Purpose

Analyze market structure for a crypto pair on 4H and 1D timeframes. Identifies swing highs/lows, classifies the sequence as HH/HL (bullish) or LH/LL (bearish), determines a per-timeframe structure, and produces a combined operative conclusion with an invalidation level.

## Architecture

- **File:** `skills/evaluate_market_structure.py`
- **Pattern:** identical to existing skills — accepts `--pair`, prints JSON to stdout, logs to stderr, configured via env vars
- **Data source:** OKX `/api/v5/market/candles` (same endpoint already used by `evaluate_open_interest`)
- **Registration:** added to `TOOLS`, `_SCRIPT_MAP`, and `SYSTEM_PROMPT` in `agent.py`

### Env vars

| Variable | Default | Description |
|---|---|---|
| `MS_PIVOT_N` | `3` | Candles required on each side to confirm a pivot |
| `MS_LOOKBACK` | `200` | Number of candles fetched per timeframe |

## Algorithm

Runs independently for 4H and 1D.

### Step 1 — Pivot detection

```
pivot_high[i] = high[i]  if  high[i] == max(high[i-N : i+N+1])
pivot_low[i]  = low[i]   if  low[i]  == min(low[i-N  : i+N+1])
```

The last N candles are excluded — they cannot be confirmed pivots (no right-side context). This matches TradingView's standard pivot behavior.

### Step 2 — Alternating swing sequence

Build a list that strictly alternates SH ↔ SL. When two consecutive pivots are the same type, keep only the more extreme one (higher high, lower low). This prevents a double top from being counted as two separate swing highs.

### Step 3 — Classification using the last 4 points

Take the last 4 alternating swings: SH1 → SL1 → SH2 → SL2 (or SL1 → SH1 → SL2 → SH2 depending on which type comes first).

```
SH2 > SH1 → HH    SH2 < SH1 → LH
SL2 > SL1 → HL    SL2 < SL1 → LL

HH + HL → LONG
LH + LL → SHORT
HH + LL or LH + HL → UNDEFINED
```

If fewer than 4 alternating swings are found in the lookback window, the timeframe returns UNDEFINED.

### Step 4 — Invalidation level

| Structure | Invalidation |
|---|---|
| LONG | Price of the last HL — a close below this breaks the bullish structure |
| SHORT | Price of the last LH — a close above this breaks the bearish structure |
| UNDEFINED | Price of the most recent swing extreme opposite to the tentative bias |

### Step 5 — Combined conclusion

| 4H | 1D | Conclusion | Confidence |
|---|---|---|---|
| LONG | LONG | LONG | high |
| SHORT | SHORT | SHORT | high |
| LONG | UNDEFINED | LONG | moderate |
| SHORT | UNDEFINED | SHORT | moderate |
| UNDEFINED | LONG | LONG | moderate |
| UNDEFINED | SHORT | SHORT | moderate |
| LONG | SHORT | CONFLICT | low |
| SHORT | LONG | CONFLICT | low |
| UNDEFINED | UNDEFINED | UNDEFINED | low |

## Output JSON

```json
{
  "pair": "BTCUSDT",
  "4h": {
    "structure": "LONG",
    "higher_high": true,
    "higher_low": true,
    "last_swing_high": 85000,
    "last_swing_low": 80500,
    "invalidation": 80500
  },
  "1d": {
    "structure": "LONG",
    "higher_high": true,
    "higher_low": true,
    "last_swing_high": 89000,
    "last_swing_low": 78000,
    "invalidation": 78000
  },
  "conclusion": "LONG",
  "confidence": "high",
  "reasoning": "Both 4H and 1D show HH/HL structure. Last 4H invalidation at $80,500 — structure holds above that level. Last 1D invalidation at $78,000."
}
```

## Error handling

- API errors return `{"error": "..."}` and do not crash — consistent with all existing skills
- If a single timeframe has insufficient swings, it returns `structure: UNDEFINED` and the combined conclusion degrades to moderate or low confidence
- Division by zero and empty list edge cases guarded explicitly

## Integration into agent.py

Add to `TOOLS`:
```python
{
  "type": "function",
  "function": {
    "name": "evaluate_market_structure",
    "description": "Analyzes market structure on 4H and 1D timeframes using pivot-based swing detection. Returns HH/HL (LONG), LH/LL (SHORT), or UNDEFINED per timeframe, plus a combined conclusion, confidence level, and invalidation price.",
    "parameters": {
      "type": "object",
      "properties": {
        "pair": {"type": "string", "description": "Cryptocurrency symbol, e.g. BTC, ETH, SOL"}
      },
      "required": ["pair"]
    }
  }
}
```

Add to `SYSTEM_PROMPT`: instruct the LLM to call `evaluate_market_structure` and include its output as `structure_summary` in the final JSON response.
