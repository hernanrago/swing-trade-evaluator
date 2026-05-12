# Design: `evaluate_market_structure` skill

**Status:** Approved with minor changes
**Purpose:** Analyze market structure for the indicated crypto instrument on 4H and 1D timeframes. Detects confirmed swing highs/lows, classifies structure as HH/HL — bullish — or LH/LL — bearish — and returns a combined operative conclusion with confidence and invalidation/range levels.

---

## 1. Architecture

* **File:** `skills/evaluate_market_structure.py`
* **Pattern:** same as existing skills:

  * accepts `--pair`
  * prints JSON to stdout
  * logs diagnostics to stderr
  * configurable through environment variables
* **Data source:** OKX `/api/v5/market/candles`
* **Integration:** add to:

  * `TOOLS`
  * `_SCRIPT_MAP`
  * `SYSTEM_PROMPT`
  * final agent JSON as `structure_summary`

---

## 2. Inputs

### CLI

```bash
python skills/evaluate_market_structure.py --pair BTC
```

Supported examples:

```text
BTC
BTCUSDT
BTC-USDT
BTC-USDT-SWAP
ETH
SOLUSDT
```

---

## 3. Pair normalization

Normalize the user input into an OKX instrument.

Recommended default:

```text
BTC          → BTC-USDT-SWAP
BTCUSDT      → BTC-USDT-SWAP
BTC-USDT     → BTC-USDT-SWAP
BTC-USDT-SWAP → BTC-USDT-SWAP
```

Return both the original pair and normalized instrument:

```json
{
  "pair": "BTC",
  "instrument": "BTC-USDT-SWAP"
}
```

Reason: the checklist is for leveraged perp/swing trading, so the default should be the perpetual/swap market, not spot.

---

## 4. Environment variables

| Variable                 | Default | Description                                         |
| ------------------------ | ------: | --------------------------------------------------- |
| `MS_PIVOT_N_4H`          |     `3` | Candles required on each side to confirm a 4H pivot |
| `MS_PIVOT_N_1D`          |     `5` | Candles required on each side to confirm a 1D pivot |
| `MS_LOOKBACK`            |   `200` | Number of candles fetched per timeframe             |
| `MS_EQUAL_TOLERANCE_PCT` |  `0.05` | Tolerance for equal highs/lows, in percent          |

Recommended interpretation:

```text
0.05 = 0.05%
```

---

## 5. Timeframes

Run independently on:

```text
4H
1D
```

OKX candle bars:

```text
4H → 4H
1D → 1Dutc or 1D depending on existing project convention
```

Use the same convention already used elsewhere in the codebase.

---

# Algorithm

## Step 1 — Fetch candles

Fetch `MS_LOOKBACK` candles per timeframe from OKX.

Required candle fields:

```text
timestamp
open
high
low
close
volume
```

Sort candles oldest → newest before analysis.

If API returns insufficient data:

```json
{
  "structure": "UNDEFINED",
  "reason": "insufficient_candles"
}
```

---

## Step 2 — Pivot detection

For each candle `i`, detect confirmed pivot highs/lows using classic pivot logic.

### Exclude the last `N` candles

The last `N` candles cannot be confirmed because there is no right-side context.

### Pivot high

```text
pivot_high[i] = high[i] if high[i] is the maximum high in high[i-N : i+N+1]
```

### Pivot low

```text
pivot_low[i] = low[i] if low[i] is the minimum low in low[i-N : i+N+1]
```

### Equal high / equal low handling

Avoid duplicate pivots when multiple candles share the same maximum/minimum inside the pivot window.

Recommended rule:

```text
If multiple candles share the same maximum high in the window, keep only the most recent one.
If multiple candles share the same minimum low in the window, keep only the most recent one.
```

This prevents flat double tops/bottoms from creating duplicate pivots.

---

## Step 3 — Build alternating swing sequence

Create a sequence that strictly alternates:

```text
SH ↔ SL
```

Where:

```text
SH = swing high
SL = swing low
```

If two consecutive pivots are of the same type:

```text
SH followed by SH → keep the higher high
SL followed by SL → keep the lower low
```

If equal after tolerance:

```text
Keep the most recent pivot
```

This avoids counting clustered tops/bottoms as separate structural swings.

---

## Step 4 — Use last 4 alternating swings

Take the last 4 alternating swings.

Valid patterns:

```text
SH1 → SL1 → SH2 → SL2
SL1 → SH1 → SL2 → SH2
```

If fewer than 4 alternating swings exist:

```json
{
  "structure": "UNDEFINED",
  "reason": "insufficient_swings"
}
```

---

## Step 5 — Classify highs and lows

Compare the last two swing highs and last two swing lows.

### Tolerance

Use:

```text
tolerance = MS_EQUAL_TOLERANCE_PCT / 100
```

### High classification

```text
SH2 > SH1 * (1 + tolerance) → HH
SH2 < SH1 * (1 - tolerance) → LH
otherwise → EH
```

Where:

```text
HH = higher high
LH = lower high
EH = equal high
```

### Low classification

```text
SL2 > SL1 * (1 + tolerance) → HL
SL2 < SL1 * (1 - tolerance) → LL
otherwise → EL
```

Where:

```text
HL = higher low
LL = lower low
EL = equal low
```

---

## Step 6 — Per-timeframe structure

```text
HH + HL → LONG
LH + LL → SHORT
Any other combination → UNDEFINED
```

Examples:

```text
HH + LL → UNDEFINED
LH + HL → UNDEFINED
HH + EL → UNDEFINED
EH + HL → UNDEFINED
EH + EL → UNDEFINED
```

---

## Step 7 — Invalidation and range levels

### LONG

Invalidation is the last confirmed higher low:

```text
invalidation = last SL
```

Meaning:

```text
A close below this level breaks bullish structure.
```

### SHORT

Invalidation is the last confirmed lower high:

```text
invalidation = last SH
```

Meaning:

```text
A close above this level breaks bearish structure.
```

### UNDEFINED

Do not invent an invalidation level.

Return:

```json
{
  "invalidation": null,
  "range_high": "most recent relevant swing high",
  "range_low": "most recent relevant swing low"
}
```

Meaning:

```text
The market is structurally undefined/ranging between recent swing extremes.
```

---

# Combined conclusion

Use the per-timeframe results.

| 4H        | 1D        | Conclusion | Confidence |
| --------- | --------- | ---------- | ---------- |
| LONG      | LONG      | LONG       | high       |
| SHORT     | SHORT     | SHORT      | high       |
| LONG      | UNDEFINED | LONG       | moderate   |
| SHORT     | UNDEFINED | SHORT      | moderate   |
| UNDEFINED | LONG      | LONG       | moderate   |
| UNDEFINED | SHORT     | SHORT      | moderate   |
| LONG      | SHORT     | CONFLICT   | low        |
| SHORT     | LONG      | CONFLICT   | low        |
| UNDEFINED | UNDEFINED | UNDEFINED  | low        |

---

## Reasoning generation

The skill should return a concise explanation.

Examples:

### LONG high confidence

```text
Both 4H and 1D show HH/HL structure. 4H bullish structure is valid while price closes above 80,500. 1D bullish structure is valid while price closes above 78,000.
```

### SHORT high confidence

```text
Both 4H and 1D show LH/LL structure. 4H bearish structure is valid while price closes below 85,000. 1D bearish structure is valid while price closes below 89,000.
```

### Conflict

```text
4H shows HH/HL bullish structure, while 1D shows LH/LL bearish structure. Timeframes are not aligned, so the setup is structurally conflicted.
```

### Undefined

```text
Both 4H and 1D are structurally undefined. Recent swings do not confirm HH/HL or LH/LL.
```

---

# Output JSON

## Recommended schema

```json
{
  "pair": "BTC",
  "instrument": "BTC-USDT-SWAP",
  "4h": {
    "structure": "LONG",
    "high_structure": "HH",
    "low_structure": "HL",
    "higher_high": true,
    "higher_low": true,
    "lower_high": false,
    "lower_low": false,
    "last_swing_high": 85000,
    "last_swing_low": 80500,
    "invalidation": 80500,
    "range_high": null,
    "range_low": null,
    "last_swings": [
      {"type": "SH", "price": 82000, "timestamp": "2026-05-01T00:00:00Z"},
      {"type": "SL", "price": 78000, "timestamp": "2026-05-03T00:00:00Z"},
      {"type": "SH", "price": 85000, "timestamp": "2026-05-06T00:00:00Z"},
      {"type": "SL", "price": 80500, "timestamp": "2026-05-08T00:00:00Z"}
    ],
    "reason": null
  },
  "1d": {
    "structure": "LONG",
    "high_structure": "HH",
    "low_structure": "HL",
    "higher_high": true,
    "higher_low": true,
    "lower_high": false,
    "lower_low": false,
    "last_swing_high": 89000,
    "last_swing_low": 78000,
    "invalidation": 78000,
    "range_high": null,
    "range_low": null,
    "last_swings": [
      {"type": "SH", "price": 81000, "timestamp": "2026-04-10T00:00:00Z"},
      {"type": "SL", "price": 73000, "timestamp": "2026-04-15T00:00:00Z"},
      {"type": "SH", "price": 89000, "timestamp": "2026-04-25T00:00:00Z"},
      {"type": "SL", "price": 78000, "timestamp": "2026-05-02T00:00:00Z"}
    ],
    "reason": null
  },
  "conclusion": "LONG",
  "confidence": "high",
  "reasoning": "Both 4H and 1D show HH/HL structure. 4H bullish structure is valid while price closes above 80,500. 1D bullish structure is valid while price closes above 78,000."
}
```

---

## Undefined timeframe example

```json
{
  "structure": "UNDEFINED",
  "high_structure": "HH",
  "low_structure": "LL",
  "higher_high": true,
  "higher_low": false,
  "lower_high": false,
  "lower_low": true,
  "last_swing_high": 85000,
  "last_swing_low": 78000,
  "invalidation": null,
  "range_high": 85000,
  "range_low": 78000,
  "last_swings": [
    {"type": "SL", "price": 80000, "timestamp": "2026-05-01T00:00:00Z"},
    {"type": "SH", "price": 83000, "timestamp": "2026-05-03T00:00:00Z"},
    {"type": "SL", "price": 78000, "timestamp": "2026-05-05T00:00:00Z"},
    {"type": "SH", "price": 85000, "timestamp": "2026-05-07T00:00:00Z"}
  ],
  "reason": "mixed_structure"
}
```

---

# Error handling

## API error

Return:

```json
{
  "pair": "BTC",
  "instrument": "BTC-USDT-SWAP",
  "error": "OKX API error: ..."
}
```

Do not crash.

## Invalid pair

Return:

```json
{
  "pair": "INVALID",
  "instrument": null,
  "error": "Unable to normalize or fetch instrument"
}
```

## Insufficient candles

Per timeframe:

```json
{
  "structure": "UNDEFINED",
  "reason": "insufficient_candles"
}
```

## Insufficient swings

Per timeframe:

```json
{
  "structure": "UNDEFINED",
  "reason": "insufficient_swings"
}
```

## Edge cases to guard

```text
- Empty API response
- Non-numeric candle values
- Duplicate timestamps
- Candles returned newest → oldest
- Missing high/low values
- Fewer than 2 swing highs or 2 swing lows
- Equal highs/equal lows within tolerance
- Division by zero
```

---

# Integration into `agent.py`

## Add to `TOOLS`

```python
{
    "type": "function",
    "function": {
        "name": "evaluate_market_structure",
        "description": (
            "Analyzes market structure on 4H and 1D timeframes using "
            "pivot-based swing detection. Returns HH/HL (LONG), "
            "LH/LL (SHORT), or UNDEFINED per timeframe, plus a combined "
            "conclusion, confidence level, and invalidation/range levels."
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
}
```

## Add to `_SCRIPT_MAP`

```python
_SCRIPT_MAP = {
    # ...
    "evaluate_market_structure": "skills/evaluate_market_structure.py",
}
```

## Add to `SYSTEM_PROMPT`

```text
When evaluating a trade setup, call evaluate_market_structure for the indicated instrument. Include its output in the final JSON response as structure_summary. Use the returned conclusion, confidence, timeframe structures, invalidation levels, and reasoning when assessing whether market structure supports the trade direction.
```

---

# Recommended final decision

```text
Implement v1 with:
- Pivot clásico
- N separado por timeframe: 4H=3, 1D=5
- Últimos 4 swings alternados
- Tolerancia para equal highs/lows
- UNDEFINED con range_high/range_low
- last_swings incluido para auditoría
- Normalización a OKX swap instrument
```

Leave for v2:

```text
- ATR filter for swing significance
- Conservative mode using last 6 swings
- Aggressive mode using last 3 swings
- Multi-exchange data fallback
- Optional spot/perp selection
```
