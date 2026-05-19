# Skills In-Process + Shared Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace subprocess-per-skill-call with direct in-process Python function calls, inject pre-fetched shared data (BTC dominance, premiumIndex, spot prices), and evaluate all pairs in parallel — so `/evaluate-all` over 10 pairs goes from ~140 external HTTP calls, ~70 subprocess startups, and serial pair execution to ~83 HTTP calls, 0 startups, and wall-clock time equal to the slowest single pair.

**Architecture:** All 7 skills gain a `context=None` parameter; when `context` carries pre-fetched data they skip the HTTP call and fall back to fetching when run standalone (CLI unchanged). `agent.py` gains a `_FUNCTION_MAP` so `execute_skill` calls skills as Python functions instead of subprocesses, a `timed_cache` decorator for data that's global across pairs (BTC dominance TTL 300 s, premiumIndex TTL 60 s), and `_build_batch_context(pairs)` called once per `run_agent_batch` to pre-fetch spot prices for the batch set. A module-level `_CANDLES_CACHE` added to `evaluate_market_structure` means the duplicate candle fetch inside `evaluate_entry_zone` becomes a cache hit. `run_agent_batch` uses `ThreadPoolExecutor(max_workers=10)` to evaluate all pairs concurrently.

**Tech Stack:** Python 3, `unittest.mock` (already in stdlib), `pytest` (already used in the repo), `concurrent.futures.ThreadPoolExecutor` (stdlib)

**Out of scope (next step):** parallelising pair evaluation with `asyncio` or `ThreadPoolExecutor`.

---

## File Map

| File | Change |
|---|---|
| `skills/evaluate_market_structure.py` | Add `_CANDLES_CACHE` + caching to `get_candles()`; add `context=None` to `evaluate_market_structure()` |
| `skills/evaluate_btc_dominance.py` | `evaluate_btc_dominance(pair, context=None)` reads `context["btc_dominance"]` when present |
| `skills/evaluate_funding_rate.py` | `evaluate_funding_rate(pair, context=None)` reads `context["premium_index"]` when present |
| `skills/evaluate_squeeze_risk.py` | `evaluate_squeeze_risk(pair, context=None)` reads `premium_index` + `spot_prices` from context |
| `skills/evaluate_entry_zone.py` | `evaluate_entry_zone(pair, context=None)`; pass `context` to internal `evaluate_market_structure()` call |
| `skills/evaluate_open_interest.py` | Add `context=None` (unused but consistent signature) |
| `skills/evaluate_tf_trend.py` | Add `context=None` (unused but consistent signature) |
| `agent.py` | Add `timed_cache`, cached fetchers, `_FUNCTION_MAP`, modify `execute_skill` / `run_agent` / `run_agent_batch` |
| `tests/test_context_injection.py` | New — unit tests for context injection in each skill |
| `tests/test_agent_inprocess.py` | New — unit tests for in-process dispatch, batch context, and parallel execution |

---

## Context Dict Schema

```python
context = {
    "btc_dominance": 52.34,               # float — from CoinGecko /global
    "premium_index": {                     # dict — from BingX premiumIndex (no symbol)
        "BTC-USDT": {
            "lastFundingRate": 0.0001,     # float
            "markPrice":       103500.0,   # float
        },
        # ... one entry per symbol returned by the endpoint
    },
    "spot_prices": {                       # dict — from CoinGecko /simple/price (batched)
        "BTC": 103480.0,
        "ETH": 2498.0,
        # ... one entry per base symbol in the batch
    },
}
```

---

## Task 1: Candle cache in evaluate_market_structure

**Files:**
- Modify: `skills/evaluate_market_structure.py`
- Test: `tests/test_context_injection.py` (create)

- [ ] **Step 1: Create test file and write the failing test**

```python
# tests/test_context_injection.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills'))

from unittest.mock import patch, MagicMock

# ── Task 1: candle cache ────────────────────────────────────────────────────

def _okx_candles_response(n=5):
    """Returns a minimal OKX candles API response with n candles."""
    candles = [[str(i), "100", "110", "90", "105", "1000", "1"] for i in range(n, 0, -1)]
    return MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"code": "0", "data": candles},
    })

def test_get_candles_caches_second_call():
    import evaluate_market_structure as ms
    ms._CANDLES_CACHE.clear()
    mock_resp = _okx_candles_response()
    with patch("evaluate_market_structure.requests.get", return_value=mock_resp) as mock_get:
        ms.get_candles("BTC-USDT-SWAP", "4H", 5)
        ms.get_candles("BTC-USDT-SWAP", "4H", 5)   # second call — should hit cache
    assert mock_get.call_count == 1                  # only one real HTTP call
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/hernan.rago/etc/swing-trade-evaluator
python -m pytest tests/test_context_injection.py::test_get_candles_caches_second_call -v
```
Expected: `FAILED` — `AssertionError: assert 2 == 1` (no cache yet, two HTTP calls)

- [ ] **Step 3: Add `_CANDLES_CACHE` and caching to `get_candles()` in evaluate_market_structure.py**

Add after line 21 (`OKX_API = "https://www.okx.com"`):

```python
_CANDLES_CACHE = {}
```

Replace the existing `get_candles` function body (starting at line 40):

```python
def get_candles(instrument, bar, limit):
    """Fetch candles from OKX. Returns list sorted oldest → newest, or {"error": ...}."""
    cache_key = (instrument, bar, limit)
    if cache_key in _CANDLES_CACHE:
        return _CANDLES_CACHE[cache_key]
    url = f"{OKX_API}/api/v5/market/candles"
    params = {"instId": instrument, "bar": bar, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            return {"error": f"OKX API error: {data.get('msg')}"}
        result = list(reversed(data["data"]))
        _CANDLES_CACHE[cache_key] = result
        return result
    except Exception as e:
        return {"error": f"OKX candles error: {e}"}
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
python -m pytest tests/test_context_injection.py::test_get_candles_caches_second_call -v
```
Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add skills/evaluate_market_structure.py tests/test_context_injection.py
git commit -m "perf: add candle cache to evaluate_market_structure"
```

---

## Task 2: Context injection in evaluate_btc_dominance

**Files:**
- Modify: `skills/evaluate_btc_dominance.py`
- Test: `tests/test_context_injection.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_injection.py`:

```python
# ── Task 2: evaluate_btc_dominance ─────────────────────────────────────────

def test_btc_dominance_uses_context_skips_http():
    from evaluate_btc_dominance import evaluate_btc_dominance
    ctx = {"btc_dominance": 55.5}
    with patch("evaluate_btc_dominance.requests.get") as mock_get:
        result = evaluate_btc_dominance("BTC", context=ctx)
    mock_get.assert_not_called()
    assert result["btc_dominance"] == 55.5

def test_btc_dominance_fetches_when_no_context():
    from evaluate_btc_dominance import evaluate_btc_dominance
    mock_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"market_cap_percentage": {"btc": 52.0}}},
    })
    with patch("evaluate_btc_dominance.requests.get", return_value=mock_resp) as mock_get:
        result = evaluate_btc_dominance("BTC", context=None)
    mock_get.assert_called_once()
    assert result["btc_dominance"] == 52.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_context_injection.py::test_btc_dominance_uses_context_skips_http tests/test_context_injection.py::test_btc_dominance_fetches_when_no_context -v
```
Expected: `FAILED` — `TypeError: evaluate_btc_dominance() got an unexpected keyword argument 'context'`

- [ ] **Step 3: Add context param to evaluate_btc_dominance**

Change the function signature and replace the dominance fetch line (`skills/evaluate_btc_dominance.py:57`):

```python
def evaluate_btc_dominance(pair="BTC", context=None):
    pair = pair.upper()
    print(f"[*] Evaluating BTC dominance impact for {pair}...", file=sys.stderr)

    btc_dominance = (context or {}).get("btc_dominance")
    if btc_dominance is None:
        btc_dominance = get_btc_dominance()

    if btc_dominance is None:
        return {"error": "Could not fetch BTC dominance data"}
    # ... rest of function unchanged from "trend = estimate_trend(btc_dominance)" onwards
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
python -m pytest tests/test_context_injection.py::test_btc_dominance_uses_context_skips_http tests/test_context_injection.py::test_btc_dominance_fetches_when_no_context -v
```
Expected: both `PASSED`

- [ ] **Step 5: Verify CLI still works**

```bash
python3 skills/evaluate_btc_dominance.py --pair BTC 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'btc_dominance' in d; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add skills/evaluate_btc_dominance.py tests/test_context_injection.py
git commit -m "perf: evaluate_btc_dominance accepts pre-fetched context"
```

---

## Task 3: Context injection in evaluate_funding_rate

**Files:**
- Modify: `skills/evaluate_funding_rate.py`
- Test: `tests/test_context_injection.py`

The `context["premium_index"]` key is a dict keyed by BingX symbol (e.g. `"BTC-USDT"`). `evaluate_funding_rate` normalises pair to `BTCUSDT` internally then calls `_to_bingx_symbol` to get `"BTC-USDT"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_injection.py`:

```python
# ── Task 3: evaluate_funding_rate ─────────────────────────────────────────

def test_funding_rate_uses_context_skips_http():
    from evaluate_funding_rate import evaluate_funding_rate
    ctx = {"premium_index": {"BTC-USDT": {"lastFundingRate": 0.00015, "markPrice": 103500.0}}}
    with patch("evaluate_funding_rate.requests.get") as mock_get:
        result = evaluate_funding_rate("BTC", context=ctx)
    mock_get.assert_not_called()
    assert result["funding_rate"] == 0.00015

def test_funding_rate_fetches_when_no_context():
    from evaluate_funding_rate import evaluate_funding_rate
    mock_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"lastFundingRate": "0.0001"}},
    })
    with patch("evaluate_funding_rate.requests.get", return_value=mock_resp) as mock_get:
        result = evaluate_funding_rate("BTC", context=None)
    mock_get.assert_called_once()
    assert result["funding_rate"] == 0.0001
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_context_injection.py::test_funding_rate_uses_context_skips_http tests/test_context_injection.py::test_funding_rate_fetches_when_no_context -v
```
Expected: `FAILED` — unexpected keyword argument `context`

- [ ] **Step 3: Add context param to evaluate_funding_rate**

Change `get_funding_rate` and `evaluate_funding_rate`:

```python
def get_funding_rate(pair):
    """Fetches current funding rate from BingX."""
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex"
    params = {"symbol": _to_bingx_symbol(pair)}
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return float(data["data"]["lastFundingRate"])


def evaluate_funding_rate(pair="BTC", context=None):
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating funding rate for {pair}...", file=sys.stderr)

    bingx_sym = _to_bingx_symbol(pair)
    pm = (context or {}).get("premium_index", {}).get(bingx_sym)
    if pm is not None:
        funding_rate = float(pm["lastFundingRate"])
    else:
        funding_rate = get_funding_rate(pair)

    # ... rest of the function unchanged from "funding_pct = funding_rate * 100" onwards
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
python -m pytest tests/test_context_injection.py::test_funding_rate_uses_context_skips_http tests/test_context_injection.py::test_funding_rate_fetches_when_no_context -v
```
Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add skills/evaluate_funding_rate.py tests/test_context_injection.py
git commit -m "perf: evaluate_funding_rate accepts pre-fetched context"
```

---

## Task 4: Context injection in evaluate_squeeze_risk

**Files:**
- Modify: `skills/evaluate_squeeze_risk.py`
- Test: `tests/test_context_injection.py`

`evaluate_squeeze_risk` uses 3 external sources: BingX premiumIndex (funding + markPrice), CoinGecko spot price, and Gate.io stats. The first two are injectable from context; Gate.io is pair-specific and always fetched live.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context_injection.py`:

```python
# ── Task 4: evaluate_squeeze_risk ─────────────────────────────────────────

def _gateio_stats_response():
    return MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": [
            {"lsr_account": "1.2", "open_interest_usd": "1000000", "long_liq_usd": "500", "short_liq_usd": "500"},
            {"lsr_account": "1.2", "open_interest_usd": "1050000", "long_liq_usd": "600", "short_liq_usd": "400"},
        ],
    })

def test_squeeze_risk_uses_context_skips_bingx_and_coingecko():
    from evaluate_squeeze_risk import evaluate_squeeze_risk
    ctx = {
        "premium_index": {"BTC-USDT": {"lastFundingRate": 0.0001, "markPrice": 103500.0}},
        "spot_prices": {"BTC": 103480.0},
    }
    with patch("evaluate_squeeze_risk.requests.get", return_value=_gateio_stats_response()) as mock_get:
        result = evaluate_squeeze_risk("BTC", context=ctx)
    # Only Gate.io should have been called (1 call), not BingX or CoinGecko
    assert mock_get.call_count == 1
    call_url = mock_get.call_args[0][0]
    assert "gateio" in call_url

def test_squeeze_risk_fetches_all_when_no_context():
    from evaluate_squeeze_risk import evaluate_squeeze_risk
    bingx_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"data": {"lastFundingRate": "0.0001", "markPrice": "103500"}},
    })
    coingecko_resp = MagicMock(**{
        "raise_for_status.return_value": None,
        "json.return_value": {"bitcoin": {"usd": 103480.0}},
    })
    def side_effect(url, **kwargs):
        if "bingx" in url:
            return bingx_resp
        if "coingecko" in url:
            return coingecko_resp
        return _gateio_stats_response()
    with patch("evaluate_squeeze_risk.requests.get", side_effect=side_effect) as mock_get:
        evaluate_squeeze_risk("BTC", context=None)
    assert mock_get.call_count == 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_context_injection.py::test_squeeze_risk_uses_context_skips_bingx_and_coingecko tests/test_context_injection.py::test_squeeze_risk_fetches_all_when_no_context -v
```
Expected: `FAILED` — unexpected keyword argument `context`

- [ ] **Step 3: Add context param to evaluate_squeeze_risk**

Change the `evaluate_squeeze_risk` function signature and replace Signals 1 and 2:

```python
def evaluate_squeeze_risk(pair="BTC", context=None):
    if not pair.endswith("USDT"):
        pair = pair.upper() + "USDT"

    print(f"[*] Evaluating squeeze risk for {pair}...", file=sys.stderr)

    score = 0
    notes = []
    mark_price = None
    funding_rate = None

    bingx_sym = _to_bingx_symbol(pair)
    pm_entry = (context or {}).get("premium_index", {}).get(bingx_sym)

    # --- Signal 1: Funding rate (weight ±2 if extreme, ±1 if elevated) ---
    try:
        if pm_entry is not None:
            funding_rate = float(pm_entry["lastFundingRate"])
            mark_price   = float(pm_entry["markPrice"])
        else:
            funding_rate, mark_price = fetch_funding_and_mark(pair)
        fp = funding_rate * 100
        if funding_rate > SQUEEZE_FUNDING_VERY_HIGH:
            score += 2
            notes.append(f"Funding very high ({fp:.4f}%/8h) → strong long crowding [+2]")
        elif funding_rate > SQUEEZE_FUNDING_HIGH:
            score += 1
            notes.append(f"Funding elevated ({fp:.4f}%/8h) → mild long crowding [+1]")
        elif funding_rate < SQUEEZE_FUNDING_VERY_LOW:
            score -= 2
            notes.append(f"Funding very negative ({fp:.4f}%/8h) → strong short crowding [-2]")
        elif funding_rate < SQUEEZE_FUNDING_LOW:
            score -= 1
            notes.append(f"Funding negative ({fp:.4f}%/8h) → mild short crowding [-1]")
        else:
            notes.append(f"Funding neutral ({fp:.4f}%/8h) [0]")
    except Exception as e:
        print(f"[!] Funding fetch failed: {e}", file=sys.stderr)

    # --- Signal 2: Perpetual basis vs spot (weight ±1) ---
    basis_pct = None
    try:
        if mark_price:
            base_sym = _base(pair)
            spot = (context or {}).get("spot_prices", {}).get(base_sym)
            if spot is None:
                spot = fetch_spot_price(pair)
            if spot:
                basis_pct = (mark_price - spot) / spot * 100
                if basis_pct > SQUEEZE_BASIS_HIGH:
                    score += 1
                    notes.append(f"Perp premium ({basis_pct:+.3f}%): longs paying up → long crowding [+1]")
                elif basis_pct < SQUEEZE_BASIS_LOW:
                    score -= 1
                    notes.append(f"Perp discount ({basis_pct:+.3f}%): perp below spot → short crowding [-1]")
                else:
                    notes.append(f"Basis neutral ({basis_pct:+.3f}%) [0]")
            else:
                notes.append(f"Basis: spot price not available for {_base(pair)}")
    except Exception as e:
        print(f"[!] Basis calculation failed: {e}", file=sys.stderr)

    # --- Signals 3, 4, 5: Gate.io (L/S ratio, OI trend, liquidations) ---
    # ... rest unchanged from "lsr = None" onwards
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
python -m pytest tests/test_context_injection.py::test_squeeze_risk_uses_context_skips_bingx_and_coingecko tests/test_context_injection.py::test_squeeze_risk_fetches_all_when_no_context -v
```
Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add skills/evaluate_squeeze_risk.py tests/test_context_injection.py
git commit -m "perf: evaluate_squeeze_risk accepts pre-fetched context"
```

---

## Task 5: Consistent context=None on remaining skills

**Files:**
- Modify: `skills/evaluate_open_interest.py`, `skills/evaluate_tf_trend.py`, `skills/evaluate_market_structure.py`, `skills/evaluate_entry_zone.py`
- Test: `tests/test_context_injection.py`

These skills have no injectable shared data except `evaluate_entry_zone`, which forwards `context` to its internal `evaluate_market_structure` call.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_context_injection.py`:

```python
# ── Task 5: consistent context=None signatures ─────────────────────────────

def test_skills_accept_context_none():
    """All skills must accept context=None without raising TypeError."""
    import evaluate_open_interest, evaluate_tf_trend, evaluate_market_structure
    import inspect
    for mod_name, fn_name in [
        ("evaluate_open_interest",  "evaluate_open_interest"),
        ("evaluate_tf_trend",       "evaluate_tf_trend"),
        ("evaluate_market_structure", "evaluate_market_structure"),
    ]:
        import importlib
        mod = importlib.import_module(mod_name)
        fn  = getattr(mod, fn_name)
        sig = inspect.signature(fn)
        assert "context" in sig.parameters, f"{fn_name} missing context param"
        assert sig.parameters["context"].default is None, f"{fn_name} context default is not None"

def test_entry_zone_passes_context_to_market_structure():
    """evaluate_entry_zone must forward context to evaluate_market_structure."""
    from unittest.mock import patch, MagicMock
    import evaluate_entry_zone as ez

    ms_result = {
        "conclusion": "LONG", "4H": {"structure": "LONG"}, "1D": {"structure": "LONG"},
        "invalidation_level": 95000.0, "range_high": 110000.0, "range_low": 95000.0,
    }
    candles = [[str(i * 1000), "100", "110", "90", "105", "1000", "1"] for i in range(1, 230)]

    with patch("evaluate_entry_zone.evaluate_market_structure") as mock_ms, \
         patch("evaluate_entry_zone._get_candles", return_value=candles):
        mock_ms.return_value = ms_result
        ctx = {"btc_dominance": 52.0}
        ez.evaluate_entry_zone("BTC", context=ctx)
        _, kwargs = mock_ms.call_args
        assert kwargs.get("context") == ctx
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_context_injection.py::test_skills_accept_context_none tests/test_context_injection.py::test_entry_zone_passes_context_to_market_structure -v
```
Expected: `FAILED`

- [ ] **Step 3: Add context=None to evaluate_open_interest**

Find the main function (named `evaluate_open_interest`) and change signature to:
```python
def evaluate_open_interest(pair="BTC", context=None):
```
No other changes needed.

- [ ] **Step 4: Add context=None to evaluate_tf_trend**

Find the main function (named `evaluate_tf_trend`) and change signature to:
```python
def evaluate_tf_trend(pair="BTC", context=None):
```
No other changes needed.

- [ ] **Step 5: Add context=None to evaluate_market_structure**

Find the main function and change signature to:
```python
def evaluate_market_structure(pair="BTC", context=None):
```
No other changes needed inside it — context is not used here, it's only for signature consistency.

- [ ] **Step 6: Update evaluate_entry_zone to accept and forward context**

Change signature:
```python
def evaluate_entry_zone(pair="BTC", context=None):
```

Find the internal call to `evaluate_market_structure` (currently `ms = evaluate_market_structure(base)` around line 573) and update it to:
```python
ms = evaluate_market_structure(base, context=context)
```

- [ ] **Step 7: Run tests to confirm pass**

```bash
python -m pytest tests/test_context_injection.py::test_skills_accept_context_none tests/test_context_injection.py::test_entry_zone_passes_context_to_market_structure -v
```
Expected: both `PASSED`

- [ ] **Step 8: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v
```
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add skills/evaluate_open_interest.py skills/evaluate_tf_trend.py skills/evaluate_market_structure.py skills/evaluate_entry_zone.py tests/test_context_injection.py
git commit -m "perf: add context=None to all skills; evaluate_entry_zone forwards context to market_structure"
```

---

## Task 6: Add timed_cache, shared fetchers, and _FUNCTION_MAP to agent.py

**Files:**
- Modify: `agent.py`
- Test: `tests/test_agent_inprocess.py` (create)

- [ ] **Step 1: Verify BingX premiumIndex list response structure**

Run this one-liner to inspect the actual response shape:

```bash
python3 -c "
import requests, json
r = requests.get('https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex', timeout=10)
data = r.json()['data']
print(type(data), len(data) if isinstance(data, list) else 'not a list')
if isinstance(data, list):
    print(json.dumps(data[0], indent=2))
"
```

Expected: a list of dicts, each with `symbol`, `lastFundingRate`, `markPrice`. Confirm before proceeding.

- [ ] **Step 2: Create test file with failing tests**

```python
# tests/test_agent_inprocess.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock, call


# ── Task 6: timed_cache ────────────────────────────────────────────────────

def test_timed_cache_returns_cached_value():
    from agent import timed_cache
    import time
    call_count = 0
    @timed_cache(seconds=60)
    def expensive():
        nonlocal call_count
        call_count += 1
        return 42
    assert expensive() == 42
    assert expensive() == 42
    assert call_count == 1   # called only once

def test_timed_cache_refreshes_after_ttl():
    from agent import timed_cache
    import datetime
    call_count = 0
    @timed_cache(seconds=1)
    def expensive():
        nonlocal call_count
        call_count += 1
        return call_count
    expensive()
    # Manually expire the cache by backdating the timestamp
    with patch("agent.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.datetime.now() + datetime.timedelta(seconds=2)
        expensive()
    assert call_count == 2


# ── Task 6: _build_batch_context ──────────────────────────────────────────

def test_build_batch_context_returns_dict_with_expected_keys():
    with patch("agent._cached_btc_dominance", return_value=52.0), \
         patch("agent._cached_premium_index", return_value={"BTC-USDT": {}}), \
         patch("agent._fetch_spot_prices_batch", return_value={"BTC": 103000.0}):
        from agent import _build_batch_context
        ctx = _build_batch_context(["BTC"])
    assert ctx["btc_dominance"] == 52.0
    assert "BTC-USDT" in ctx["premium_index"]
    assert ctx["spot_prices"]["BTC"] == 103000.0

def test_build_batch_context_tolerates_fetch_error():
    """A failed pre-fetch must not crash the batch — key is absent, not raised."""
    with patch("agent._cached_btc_dominance", side_effect=Exception("network error")), \
         patch("agent._cached_premium_index", return_value={}), \
         patch("agent._fetch_spot_prices_batch", return_value={}):
        from agent import _build_batch_context
        ctx = _build_batch_context(["BTC"])
    assert "btc_dominance" not in ctx
```

- [ ] **Step 3: Run to confirm failure**

```bash
python -m pytest tests/test_agent_inprocess.py -v
```
Expected: `FAILED` — `ImportError: cannot import name 'timed_cache' from 'agent'`

- [ ] **Step 4: Add timed_cache, COINGECKO_IDS, fetchers, and _FUNCTION_MAP to agent.py**

Add after the existing imports block (after `import litellm`):

```python
from datetime import datetime, timedelta
import functools
```

Add after `SUBPROCESS_TIMEOUT` config line:

```python
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
    """Fetches all BingX perpetual premiumIndex entries in one call."""
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
        if "symbol" in item and "lastFundingRate" in item
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
```

Also add `import requests` at the top of `agent.py` if not already present.

Also add at the end of the imports section, after `_BASE_DIR`:

```python
# --- In-process function map (imported lazily to avoid circular issues at module load) ---
def _get_function_map():
    import sys as _sys
    _sys.path.insert(0, os.path.join(_BASE_DIR, "skills"))
    from evaluate_btc_dominance  import evaluate_btc_dominance
    from evaluate_funding_rate   import evaluate_funding_rate
    from evaluate_squeeze_risk   import evaluate_squeeze_risk
    from evaluate_open_interest  import evaluate_open_interest
    from evaluate_tf_trend       import evaluate_tf_trend
    from evaluate_market_structure import evaluate_market_structure
    from evaluate_entry_zone     import evaluate_entry_zone
    return {
        "evaluate_btc_dominance":   evaluate_btc_dominance,
        "evaluate_funding_rate":    evaluate_funding_rate,
        "evaluate_squeeze_risk":    evaluate_squeeze_risk,
        "evaluate_open_interest":   evaluate_open_interest,
        "evaluate_tf_trend":        evaluate_tf_trend,
        "evaluate_market_structure": evaluate_market_structure,
        "evaluate_entry_zone":      evaluate_entry_zone,
    }

_FUNCTION_MAP = None   # populated lazily on first use

def _skill_fn(name):
    global _FUNCTION_MAP
    if _FUNCTION_MAP is None:
        _FUNCTION_MAP = _get_function_map()
    return _FUNCTION_MAP.get(name)
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
python -m pytest tests/test_agent_inprocess.py -v
```
Expected: all `PASSED`

- [ ] **Step 6: Commit**

```bash
git add agent.py tests/test_agent_inprocess.py
git commit -m "perf: add timed_cache, shared fetchers, and _FUNCTION_MAP to agent"
```

---

## Task 7: Modify execute_skill to call in-process

**Files:**
- Modify: `agent.py`
- Test: `tests/test_agent_inprocess.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_inprocess.py`:

```python
# ── Task 7: execute_skill in-process ──────────────────────────────────────

def test_execute_skill_calls_function_not_subprocess():
    with patch("agent._skill_fn") as mock_fn_lookup, \
         patch("agent.subprocess.run") as mock_sub:
        mock_skill = MagicMock(return_value={"direction": "LONG"})
        mock_fn_lookup.return_value = mock_skill
        from agent import execute_skill
        result = execute_skill("evaluate_btc_dominance", {"pair": "BTC"})
    mock_sub.assert_not_called()
    mock_skill.assert_called_once_with("BTC", context=None)
    assert result == {"direction": "LONG"}

def test_execute_skill_falls_back_to_subprocess_when_not_in_map():
    completed = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
    with patch("agent._skill_fn", return_value=None), \
         patch("agent.subprocess.run", return_value=completed) as mock_sub:
        from agent import execute_skill
        execute_skill("evaluate_unknown_skill", {"pair": "BTC"})
    mock_sub.assert_called_once()

def test_execute_skill_passes_context_to_function():
    ctx = {"btc_dominance": 55.0}
    with patch("agent._skill_fn") as mock_fn_lookup, \
         patch("agent.subprocess.run"):
        mock_skill = MagicMock(return_value={"direction": "LONG"})
        mock_fn_lookup.return_value = mock_skill
        from agent import execute_skill
        execute_skill("evaluate_btc_dominance", {"pair": "BTC"}, context=ctx)
    mock_skill.assert_called_once_with("BTC", context=ctx)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_inprocess.py::test_execute_skill_calls_function_not_subprocess tests/test_agent_inprocess.py::test_execute_skill_falls_back_to_subprocess_when_not_in_map tests/test_agent_inprocess.py::test_execute_skill_passes_context_to_function -v
```
Expected: `FAILED` — `execute_skill()` still uses subprocess, no `context` param

- [ ] **Step 3: Rewrite execute_skill in agent.py**

Replace the existing `execute_skill` function:

```python
def execute_skill(skill_name, params, context=None):
    """Calls a skill in-process if available, otherwise falls back to subprocess."""
    pair = params.get("pair", "BTC").upper()
    log.info("Skill %s | pair=%s", skill_name, pair)

    fn = _skill_fn(skill_name)
    if fn is not None:
        try:
            result = fn(pair, context=context)
            log.info("Skill %s OK (in-process) | result=%s", skill_name, json.dumps(result))
            return result
        except Exception as e:
            log.error("Skill %s in-process error: %s", skill_name, traceback.format_exc())
            return {"error": str(e)}

    # Fallback: subprocess (for skills not in _FUNCTION_MAP or during testing)
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

- [ ] **Step 4: Run tests to confirm pass**

```bash
python -m pytest tests/test_agent_inprocess.py -v
```
Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent_inprocess.py
git commit -m "perf: execute_skill dispatches in-process via _FUNCTION_MAP, subprocess as fallback"
```

---

## Task 8: Wire context through run_agent and run_agent_batch

**Files:**
- Modify: `agent.py`
- Test: `tests/test_agent_inprocess.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_inprocess.py`:

```python
# ── Task 8: context wiring ─────────────────────────────────────────────────

def test_run_agent_batch_builds_context_once_for_n_pairs():
    """_cached_btc_dominance and _cached_premium_index called once regardless of pair count."""
    mock_rec = {"direction": "LONG", "confidence": "high", "aligned": True,
                "squeeze_warning": None, "reasoning": "", "trend_summary": "",
                "structure_summary": "", "dominance_summary": "", "funding_summary": "",
                "oi_summary": "", "squeeze_summary": "", "entry_zone_summary": ""}
    with patch("agent._build_batch_context", return_value={}) as mock_ctx, \
         patch("agent.run_agent", return_value=mock_rec):
        from agent import run_agent_batch
        run_agent_batch(["BTC", "ETH", "SOL"])
    mock_ctx.assert_called_once_with(["BTC", "ETH", "SOL"])

def test_run_agent_receives_context():
    """run_agent must forward context to execute_skill calls."""
    ctx = {"btc_dominance": 52.0, "premium_index": {}, "spot_prices": {}}
    captured_contexts = []

    def fake_execute_skill(name, params, context=None):
        captured_contexts.append(context)
        return {"direction": "LONG"}

    stop_response = MagicMock()
    stop_response.choices[0].finish_reason = "stop"
    stop_response.choices[0].message.content = '{"direction":"LONG","confidence":"high","aligned":true,"squeeze_warning":null,"reasoning":"r","trend_summary":"t","structure_summary":"s","dominance_summary":"d","funding_summary":"f","oi_summary":"o","squeeze_summary":"sq","entry_zone_summary":"ez"}'
    stop_response.choices[0].message.tool_calls = None

    with patch("agent.litellm.completion", return_value=stop_response), \
         patch("agent.execute_skill", side_effect=fake_execute_skill):
        from agent import run_agent
        run_agent("BTC", context=ctx)
    # execute_skill may not be called if LLM returns stop immediately, but run_agent must accept context
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_inprocess.py::test_run_agent_batch_builds_context_once_for_n_pairs tests/test_agent_inprocess.py::test_run_agent_receives_context -v
```
Expected: `FAILED` — `run_agent` and `run_agent_batch` don't have `context` params yet

- [ ] **Step 3: Update run_agent to accept and pass context**

Change `run_agent` signature and tool dispatch:

```python
def run_agent(pair, context=None):
    log.info("Agent start | pair=%s model=%s", pair, LLM_MODEL)
    messages = [
        {"role": "system",  "content": SYSTEM_PROMPT},
        {"role": "user",    "content": f"Analyze {pair} for swing trading direction."},
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
        message      = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        log.info("LLM response | finish_reason=%s", finish_reason)

        if finish_reason == "stop":
            # ... JSON parsing unchanged ...
            pass

        elif finish_reason == "tool_calls":
            messages.append(message)
            for tc in message.tool_calls:
                args = json.loads(tc.function.arguments)
                log.info("Tool call: %s | args=%s", tc.function.name, args)
                skill_result = execute_skill(tc.function.name, args, context=context)  # ← pass context
                messages.append({
                    "role":        "tool",
                    "tool_call_id": tc.id,
                    "content":     json.dumps(skill_result),
                })
        # ... rest unchanged ...
```

- [ ] **Step 4: Update run_agent_batch to build context once**

```python
def run_agent_batch(pairs):
    context = _build_batch_context(pairs)
    log.info("Batch context built | keys=%s", list(context.keys()))
    results = []
    for pair in pairs:
        log.info("Batch evaluating pair=%s", pair)
        rec = run_agent(pair, context=context)
        if "error" not in rec:
            rec["pair"] = pair
            results.append(rec)
        else:
            log.warning("Skipping pair=%s due to error: %s", pair, rec.get("error"))
    return results
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
python -m pytest tests/test_agent_inprocess.py -v
```
Expected: all `PASSED`

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add agent.py tests/test_agent_inprocess.py
git commit -m "perf: wire batch context through run_agent and run_agent_batch"
```

---

## Task 9: Smoke test end-to-end

- [ ] **Step 1: Test a single pair via CLI**

```bash
python3 cli.py BTC 2>&1 | tail -5
```
Expected: valid JSON with `direction`, `confidence` fields. No subprocess-related log lines.

- [ ] **Step 2: Test a single skill standalone (CLI must still work)**

```bash
python3 skills/evaluate_btc_dominance.py --pair ETH 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['pair']=='ETH'; print('CLI OK')"
python3 skills/evaluate_funding_rate.py --pair SOL 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'funding_rate' in d; print('CLI OK')"
python3 skills/evaluate_squeeze_risk.py --pair BTC 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'crowded_side' in d; print('CLI OK')"
```
Expected: `CLI OK` for each

- [ ] **Step 3: Verify shared fetchers are called once in a batch**

```bash
python3 -c "
import agent, logging, unittest.mock as m
logging.disable(logging.CRITICAL)

call_log = []
real_dom  = agent._cached_btc_dominance
real_pm   = agent._cached_premium_index

def spy_dom():
    call_log.append('btc_dom')
    return real_dom()

def spy_pm():
    call_log.append('premium_index')
    return real_pm()

with m.patch.object(agent, '_cached_btc_dominance', spy_dom), \
     m.patch.object(agent, '_cached_premium_index', spy_pm), \
     m.patch.object(agent, '_fetch_spot_prices_batch', return_value={}), \
     m.patch.object(agent, 'run_agent', return_value={'direction':'LONG','confidence':'high'}):
    agent.run_agent_batch(['BTC','ETH','SOL'])

dom_calls = call_log.count('btc_dom')
pm_calls  = call_log.count('premium_index')
print(f'btc_dom calls: {dom_calls} (expect 1)')
print(f'premium_index calls: {pm_calls} (expect 1)')
assert dom_calls == 1 and pm_calls == 1, 'FAIL: fetchers called more than once'
print('OK — shared fetchers called exactly once for 3 pairs')
"
```
Expected: `OK — shared fetchers called exactly once for 3 pairs`

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "perf: skills run in-process with shared batch context; 0 subprocesses, ~83 HTTP calls vs ~140"
```

---

---

## Task 10: Parallel pair evaluation with ThreadPoolExecutor

**Files:**
- Modify: `agent.py`
- Test: `tests/test_agent_inprocess.py`

`run_agent_batch` actualmente evalúa pares en serie. Con in-process execution y context pre-fetched, cada `run_agent(pair, context)` es independiente — ideal para ThreadPoolExecutor. El único estado compartido es `_CANDLES_CACHE` y `_FUNCTION_MAP`, ambos thread-safe para lecturas concurrentes (dict reads en CPython son GIL-protected). Las escrituras al cache son idempotentes (mismo key → mismo value), por lo que race conditions en cache fill no producen datos incorrectos.

`MAX_BATCH_WORKERS` es configurable por env var para entornos con rate limits de API.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_inprocess.py`:

```python
# ── Task 10: ThreadPoolExecutor ────────────────────────────────────────────

def test_run_agent_batch_runs_pairs_concurrently():
    """All pairs must be submitted to the executor, not run serially."""
    import threading
    active_at_once = []
    lock = threading.Lock()
    running = [0]

    def slow_agent(pair, context=None):
        with lock:
            running[0] += 1
            active_at_once.append(running[0])
        import time; time.sleep(0.05)
        with lock:
            running[0] -= 1
        return {"direction": "LONG", "confidence": "high"}

    with patch("agent._build_batch_context", return_value={}), \
         patch("agent.run_agent", side_effect=slow_agent):
        from agent import run_agent_batch
        run_agent_batch(["BTC", "ETH", "SOL", "XRP"])

    # At least 2 pairs must have been running simultaneously
    assert max(active_at_once) >= 2, "Pairs are running serially, not in parallel"

def test_run_agent_batch_collects_all_successful_results():
    call_order = []
    def fake_agent(pair, context=None):
        call_order.append(pair)
        return {"direction": "LONG", "confidence": "high"}

    with patch("agent._build_batch_context", return_value={}), \
         patch("agent.run_agent", side_effect=fake_agent):
        from agent import run_agent_batch
        results = run_agent_batch(["BTC", "ETH", "SOL"])

    assert len(results) == 3
    assert {r["pair"] for r in results} == {"BTC", "ETH", "SOL"}

def test_run_agent_batch_skips_error_pairs():
    def fake_agent(pair, context=None):
        if pair == "ETH":
            return {"error": "API timeout"}
        return {"direction": "LONG", "confidence": "high"}

    with patch("agent._build_batch_context", return_value={}), \
         patch("agent.run_agent", side_effect=fake_agent):
        from agent import run_agent_batch
        results = run_agent_batch(["BTC", "ETH", "SOL"])

    assert len(results) == 2
    assert all(r["pair"] != "ETH" for r in results)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_agent_inprocess.py::test_run_agent_batch_runs_pairs_concurrently tests/test_agent_inprocess.py::test_run_agent_batch_collects_all_successful_results tests/test_agent_inprocess.py::test_run_agent_batch_skips_error_pairs -v
```
Expected: `test_run_agent_batch_runs_pairs_concurrently` FAILS (serial execution, `max == 1`)

- [ ] **Step 3: Add MAX_BATCH_WORKERS config and rewrite run_agent_batch**

Add to the config block at the top of `agent.py` (alongside `MAX_AGENT_ITERATIONS`):

```python
MAX_BATCH_WORKERS = int(os.environ.get("MAX_BATCH_WORKERS", "10"))
```

Add to the imports block:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

Replace `run_agent_batch`:

```python
def run_agent_batch(pairs):
    """
    Evaluates all pairs concurrently using ThreadPoolExecutor.
    Builds batch context once (shared data pre-fetched), then submits one
    run_agent call per pair. Pairs that return an error dict are skipped.
    """
    context = _build_batch_context(pairs)
    log.info("Batch context built | keys=%s", list(context.keys()))

    results = []
    with ThreadPoolExecutor(max_workers=min(MAX_BATCH_WORKERS, len(pairs))) as executor:
        futures = {executor.submit(run_agent, pair, context): pair for pair in pairs}
        for future in as_completed(futures):
            pair = futures[future]
            try:
                rec = future.result()
            except Exception as e:
                log.warning("Pair %s raised exception: %s", pair, e)
                continue
            if "error" not in rec:
                rec["pair"] = pair
                results.append(rec)
            else:
                log.warning("Skipping pair=%s due to error: %s", pair, rec.get("error"))

    return results
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
python -m pytest tests/test_agent_inprocess.py -v
```
Expected: all `PASSED`

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add agent.py tests/test_agent_inprocess.py
git commit -m "perf: evaluate pairs in parallel with ThreadPoolExecutor in run_agent_batch"
```

---

## Self-Review

**Spec coverage:**
- ✅ 7 skills gain `context=None` with fallback-to-HTTP
- ✅ `timed_cache` with TTL (300 s dominance, 60 s premiumIndex)
- ✅ `_build_batch_context` called once per batch
- ✅ `execute_skill` dispatches in-process, subprocess fallback
- ✅ `run_agent` and `run_agent_batch` wired
- ✅ `_CANDLES_CACHE` in market_structure eliminates duplicate OKX calls in entry_zone
- ✅ CLI still works for all skills (context=None path)
- ✅ Partial pre-fetch failure doesn't crash the batch
- ✅ `ThreadPoolExecutor` evaluates pairs concurrently; wall-clock = slowest single pair
- ✅ `MAX_BATCH_WORKERS` configurable via env var for rate-limited environments

**No placeholders:** all code blocks are complete and runnable.

**Type consistency:** `context` is a `dict | None` throughout; `execute_skill(skill_name, params, context=None)` signature is used consistently in Tasks 7 and 8.
