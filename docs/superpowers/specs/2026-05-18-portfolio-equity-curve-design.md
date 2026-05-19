# Portfolio Equity Curve Endpoint — Design Spec

**Date:** 2026-05-18
**Status:** Approved

## Goal

Add a `GET /portfolio/equity` endpoint that returns an equity curve for the user's perpetual futures portfolio, based on realized PnL from closed positions over the last N days.

## Data Source

BingX Income API — `GET /openApi/swap/v2/user/income` with `incomeType=REALIZED_PNL`.

Each record represents one closed position and includes: timestamp, symbol, income amount. This maps 1:1 to the desired granularity (one data point per trade close), requiring no aggregation.

Funding fees and commissions are excluded intentionally — for swing trading timeframes (days), they are negligible and their inclusion would add data points that don't correspond to trade closes, breaking the 1:1 mapping.

## Endpoint Contract

```
GET /portfolio/equity?days=30
```

- `days` — optional query param, default `30`, maximum `90`
- Requires `BINGX_API_KEY` and `BINGX_API_SECRET` env vars; returns `503` if absent
- Stateless — computed fresh from BingX on every request, no local storage

### Response

```json
{
  "timestamp": "2026-05-18T10:00:00",
  "period_days": 30,
  "equity_curve": [
    {
      "time": "2026-04-20T14:23:00",
      "symbol": "BTC-USDT",
      "pnl": 45.23,
      "cumulative_pnl": 45.23
    },
    {
      "time": "2026-04-22T09:11:00",
      "symbol": "ETH-USDT",
      "pnl": -12.10,
      "cumulative_pnl": 33.13
    }
  ],
  "summary": {
    "total_pnl": 234.50,
    "trade_count": 12,
    "win_rate": 0.75,
    "best_trade": 120.00,
    "worst_trade": -45.00
  }
}
```

`equity_curve` is sorted chronologically. `cumulative_pnl` is the running total from the first trade in the period.

### Error responses

| Code | Condition |
|---|---|
| 400 | `days` out of range (< 1 or > 90) |
| 503 | Missing API credentials |
| 502 | BingX returned a non-zero error code |
| 500 | Unhandled exception |

## Implementation

All code lives in `app.py`, following existing patterns.

### `_fetch_realized_pnl(days: int) -> list[dict]`

- Computes `startTime = now_ms - days * 86400 * 1000`
- Calls `_bingx_signed_get("/openApi/swap/v2/user/income", params)` with `incomeType=REALIZED_PNL`
- Paginates using `endTime` shifting: if response returns 1000 records (the API max), re-requests with `endTime = earliest_record_time - 1` until fewer than 1000 records are returned or `startTime` is reached
- Returns list of raw income records filtered to `>= startTime`

### `GET /portfolio/equity` route

1. Validate `days` param (1–90)
2. Check credentials; return 503 if missing
3. Call `_fetch_realized_pnl(days)`
4. Sort records by timestamp ascending
5. Compute `cumulative_pnl` as running sum
6. Compute summary stats: `total_pnl`, `trade_count`, `win_rate`, `best_trade`, `worst_trade`
7. Return JSON response

### No new dependencies

Uses only `_bingx_signed_get()` and stdlib (`datetime`). No database, no new packages.

## Out of Scope

- Funding fees / commissions in the equity curve
- Persistence or caching between requests
- Per-pair breakdown (can be added later as a separate endpoint)
- Time bucketing by day (daily granularity endpoint can be added later)
