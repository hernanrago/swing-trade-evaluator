# Spec: `evaluate_confluence` Skill

**Date:** 2026-05-18
**Status:** Approved

---

## Problem

The swing-trade checklist requires "confluence of at least 2 indicators or levels at entry" as an explicit, auditable gate. `evaluate_entry_zone` already detects multi-type zone confluence internally (S/R, FVG, OB, Fibonacci, sweeps, etc.) and scores it — but the ≥2 rule is implicit in the score, not surfaced as a first-class pass/fail object visible in tool-call history.

The gap: `evaluate_entry_zone` can rate a zone STRONG based on a single very dominant level (e.g., major weekly S/R with no other factor). That zone passes the entry quality gate but would fail an explicit ≥2 rule. A separate skill closes that gap and makes the rule versionable and testable.

---

## Design Decision

**Option chosen: enrich `evaluate_entry_zone` + thin `evaluate_confluence` wrapper (Option 2)**

- `evaluate_entry_zone` exposes a `confluence` sub-object (families, count, passes, is_polarity_zone).
- `evaluate_confluence` imports and calls `evaluate_entry_zone`, reads that sub-object, applies the ≥2 rule, and returns a clean `{passed, count, factors, ...}` dict.
- The LLM makes an explicit tool call for each — clean checklist audit trail, visible in tool-call history.

Rejected alternatives:
- **Option 1 (no new skill):** leaves the rule in SYSTEM_PROMPT prose; not auditable.
- **Option 3 (fully independent):** re-detects all indicators from scratch; double API cost, duplicate logic.

---

## Factor Family Mapping

Nine type strings from `evaluate_entry_zone` collapse into five distinct families. Confluence count = number of **distinct families** with ≥1 hit in the selected zone. This enforces variety, not redundancy of the same signal.

```python
_CONFLUENCE_FAMILIES = {
    "support":          "horizontal_levels",
    "resistance":       "horizontal_levels",
    "range_level":      "horizontal_levels",
    "fvg":              "order_flow_imbalance",
    "order_block":      "order_flow_imbalance",
    "fibonacci":        "ratio_projection",
    "market_structure": "structural_events",
    "breakout_retest":  "structural_events",
    "liquidity_sweep":  "liquidity_events",
}
```

**Pass condition:** `count >= 2` (at least 2 distinct families represented in the zone).

**Unknown-type contract:** `KeyError` is raised intentionally if a type string is not in `_CONFLUENCE_FAMILIES`. Silent miscounting on a 5x leverage gate is the wrong failure mode — fail loudly instead. A completeness assertion in the test suite enforces that every type string `evaluate_entry_zone` can emit is a key in this map.

**Polarity zone:** when both `support` and `resistance` are present in the same zone, `is_polarity_zone: true` is set as metadata. This is a quality signal (price has been tested from both sides) but does **not** inflate the count — `support` and `resistance` both map to `horizontal_levels` and count as one family.

---

## Section 1: Changes to `evaluate_entry_zone.py`

### 1a. New module-level constants

```python
_CONFLUENCE_FAMILIES = { ... }  # canonical 9-key map above

# Process-scoped result cache. Invalidate manually for long-running contexts
# (Discord bot, scheduled worker, web service) — add TTL or LRU eviction there.
_EZ_RESULT_CACHE = {}
```

### 1b. Cache check (after candle fetch, before analysis)

```python
cache_key = (instrument, c4h[-1][0])  # instrument + last 4H bar timestamp
if cache_key in _EZ_RESULT_CACHE:
    return _EZ_RESULT_CACHE[cache_key]  # Returned by reference — callers must treat as read-only
```

The cache key uses the last 4H bar timestamp, so the entry is naturally invalidated when the bar advances. The candle fetch itself is already handled by the existing `_CANDLES_CACHE`, so this is CPU-only work being skipped.

### 1c. Confluence sub-object (added to every return path)

Happy path (zone found):
```python
families = sorted({_CONFLUENCE_FAMILIES[t] for t in best_zone["types"]})
# KeyError on unknown type is intentional — fail loudly

result["confluence"] = {
    "families":         families,
    "count":            len(families),
    "passes":           len(families) >= 2,
    "is_polarity_zone": "support" in best_zone["types"] and "resistance" in best_zone["types"],
}
_EZ_RESULT_CACHE[cache_key] = result
```

Early-return paths (no direction, no merged zones, invalid):
```python
result["confluence"] = {
    "families": [], "count": 0, "passes": False, "is_polarity_zone": False
}
# Early returns are cheap to recompute — not cached.
```

### 1d. Test isolation

Add an `autouse=True` pytest fixture that calls `_EZ_RESULT_CACHE.clear()` between tests. Prevents consecutive tests sharing the same instrument + hardcoded timestamp from silently returning each other's cached results.

---

## Section 2: New `skills/evaluate_confluence.py`

Thin wrapper — no indicator detection, no API calls. All detection logic stays in `evaluate_entry_zone`.

```
File: skills/evaluate_confluence.py
Function: evaluate_confluence(pair="BTC", context=None) -> dict
CLI: python3 skills/evaluate_confluence.py --pair BTC
```

**Logic:**
1. Import `evaluate_entry_zone` from `evaluate_entry_zone`.
2. Call `evaluate_entry_zone(pair, context=context)` — cache hit on second call (free).
3. Read `ez["confluence"]` — always present per the contract above.
4. Return:

```python
{
    "skill":             "evaluate_confluence",
    "symbol":            base,
    "passed":            confluence["passes"],
    "count":             confluence["count"],
    "factors":           confluence["families"],
    "is_polarity_zone":  confluence["is_polarity_zone"],
    "entry_zone_rating": ez.get("rating"),
    "summary":           "...",  # see below
}
```

**Summary strings:**
- Pass: `"Confluence confirmed: {count} distinct factor families ({', '.join(families)})."`
- Fail: `"Confluence gate fails: {count} factor family ({', '.join(families) or 'none'}) — need ≥2."`
- Polarity suffix (appended when true): `" Zone is a polarity level (support + resistance from both sides)."`

**Error handling:** if `evaluate_entry_zone` returns `{"error": ...}`, propagate as `{..., "error": ..., "passed": False}`. No special-casing needed for NOT_FOUND/INVALID — those paths return `confluence.count=0, passes=false`.

**No `_CONFLUENCE_FAMILIES` logic in this file.** The skill never touches the type-to-family mapping. That invariant stays entirely in `evaluate_entry_zone.py`.

---

## Section 3: `agent.py` Wire-up

### TOOLS entry

```python
{
    "type": "function",
    "function": {
        "name": "evaluate_confluence",
        "description": (
            "Checks whether the entry zone satisfies the ≥2 distinct indicator-family "
            "confluence requirement. Counts distinct factor families "
            "(horizontal_levels, order_flow_imbalance, ratio_projection, "
            "structural_events, liquidity_events) present in the zone. "
            "Returns passed=true if count≥2. Call this after evaluate_entry_zone."
        ),
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
```

### `_SCRIPT_MAP` addition

```python
"evaluate_confluence": "./skills/evaluate_confluence.py",
```

### `_get_function_map()` addition

```python
from evaluate_confluence import evaluate_confluence
# add to returned dict:
"evaluate_confluence": evaluate_confluence,
```

### `SYSTEM_PROMPT` update

Add item 8:
```
8. evaluate_confluence — confirms ≥2 distinct indicator families at the entry zone
   (call after evaluate_entry_zone; passed=true is required for a high-confidence setup)
```

Add to response JSON format:
```json
"confluence_summary": "one-line summary of factor families hit and pass/fail verdict"
```

---

## Completeness Assertion (Tests)

```python
from evaluate_entry_zone import _CONFLUENCE_FAMILIES

ALL_EMITTED_TYPES = {
    "support", "resistance", "range_level",
    "fvg", "order_block",
    "fibonacci",
    "market_structure", "breakout_retest",
    "liquidity_sweep",
}

def test_confluence_families_complete():
    assert ALL_EMITTED_TYPES == set(_CONFLUENCE_FAMILIES.keys()), (
        "Mismatch: a type is emitted by evaluate_entry_zone but missing from _CONFLUENCE_FAMILIES, "
        "or _CONFLUENCE_FAMILIES has a stale key. Update both together."
    )
```

---

## Summary of Files Changed

| File | Change |
|---|---|
| `skills/evaluate_entry_zone.py` | Add `_CONFLUENCE_FAMILIES`, `_EZ_RESULT_CACHE`, result cache logic, `confluence` sub-object on all return paths |
| `skills/evaluate_confluence.py` | New file — thin wrapper, ~60 lines |
| `agent.py` | Add tool schema, `_SCRIPT_MAP` entry, `_FUNCTION_MAP` import, `SYSTEM_PROMPT` item 8, `confluence_summary` in response format |
| `tests/` | Completeness assertion + autouse cache-clear fixture |
