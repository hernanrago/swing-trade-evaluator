# Swing / Intraday Trade Evaluator

LLM-orchestrated agent that evaluates crypto perpetual futures setups across two modes:

- **Swing** — 1D/1W trend + 4H/1D structure. For multi-day position trades.
- **Intraday** — 4H/1H bias + 1H/15m structure. For same-session entries.

---

## Quickstart

```bash
# Swing evaluation (default)
python3 cli.py BTC

# Intraday evaluation
python3 cli.py BTC --mode intraday

# HTTP server
python3 app.py
```

## Railway Cron

This repo includes `railway.toml` so you can run the evaluator from a Railway Cron service.

- Default command: `python3 cli.py BTC`
- Override command with Railway env var `RAILWAY_RUN_COMMAND`

Examples:

```bash
# Run intraday for ETH every cron tick
RAILWAY_RUN_COMMAND="python3 cli.py ETH --mode intraday"

# Evaluate swing on SOL
RAILWAY_RUN_COMMAND="python3 cli.py SOL --mode swing"
```

Suggested Railway setup:

1. Create a new **Cron** service connected to this repo.
2. Set the schedule expression: `0 */2 * * *` (every 2 hours).
3. Add required env vars (`ANTHROPIC_API_KEY`, optional skill configs, and `RAILWAY_RUN_COMMAND`).
4. Deploy and check logs for each execution.

### Email notifications

The CLI sends an HTML email with results after each evaluation. Set at least one sender option:

| Variable | Description |
|---|---|
| `EMAIL_TO` | Recipient address (required) |
| `RESEND_API_KEY` | Resend API key (takes priority) |
| `EMAIL_FROM` | Sender for Resend (default: `onboarding@resend.dev`) |
| `SMTP_USER` | Gmail address (used if no Resend key) |
| `SMTP_PASSWORD` | Gmail App Password (16-char, no spaces) |

If neither `RESEND_API_KEY` nor `SMTP_USER` is set, email is silently skipped.

---

## HTTP API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Status check |
| `POST` | `/evaluations` | Evaluate a list of pairs |
| `POST` | `/evaluations/top` | Evaluate top N pairs by BingX volume |
| `GET` | `/positions` | Open positions crossed with swing evaluations |
| `GET` | `/portfolio/equity` | Realized PnL equity curve (BingX) |

### Evaluate pairs

```json
POST /evaluations
{ "pairs": ["BTC", "ETH"], "mode": "intraday" }
```

`mode` is optional (default `"swing"`). Valid values: `"swing"`, `"intraday"`.

### Top N by volume

```json
POST /evaluations/top
{ "top": 10, "mode": "swing" }
```

---

## Swing Mode Checklist

Legend: 🤖 = covered by a skill | 👤 = manual review required

### 📊 Market Context

- [ ] 🤖 Higher timeframe trend (1D / 1W) aligned with trade direction — `evaluate_tf_trend`
- [ ] 🤖 BTC dominance (BTC.D) supports the expected move — `evaluate_btc_dominance`
- [ ] 👤 No imminent macro event (CPI, FOMC, rate decision) in the next 24–48h
- [ ] 🤖 Current funding rate reviewed — very positive = bearish bias, very negative = bullish bias — `evaluate_funding_rate`
- [ ] 🤖 Open Interest trending consistently with the move — `evaluate_open_interest`

### 🕯️ Technical Setup

- [ ] 🤖 Clear market structure on 4H/1D (HH-HL for longs, LH-LL for shorts), including invalidation/range levels — `evaluate_market_structure`
- [ ] 🤖 Entry zone defined (support/resistance, FVG, OB, Fibonacci retracement) — `evaluate_entry_zone`
- [ ] 👤 Confluence of at least 2 indicators or levels at entry
- [ ] 👤 Volume confirms the setup
- [ ] 👤 No significant RSI or MACD divergence against the trade

### 💰 Risk Management

- [ ] 👤 Stop loss defined **before** entering, based on structure (not liquidation price)
- [ ] 👤 SL distance calculated — at 5x, a 20% move liquidates; SL must be well before that
- [ ] 👤 Position size calculated to risk a maximum of 1–2% of total capital
- [ ] 👤 Minimum R:R of 1:2 (ideally 1:3) verified against the target take profit
- [ ] 👤 Allocated margin does not exceed 10–15% of available account capital

### 🎯 Exit Plan

- [ ] 👤 TP1 defined at a key technical level (partial exit)
- [ ] 👤 TP2 / final TP defined
- [ ] 👤 Trailing stop or price-based exit criterion defined
- [ ] 👤 Maximum time in the trade defined (swing = days, not indefinite weeks)

### ⚙️ Operational Execution

- [ ] 👤 Entry order type: limit or market? (limit preferred to avoid slippage)
- [ ] 👤 Stop loss placed **on the platform** before stepping away
- [ ] 👤 Margin mode: prefer **Isolated** for 5x swing
- [ ] 👤 Liquidation price visible and sufficiently far from current price
- [ ] 👤 No opposing position open in the same asset

> **Golden rule at 5x:** Leverage amplifies mistakes as much as gains. If the setup does not have at least 7–8 items checked, do not enter.

---

## Intraday Mode Checklist

### 📊 Bias (4H / 1H)

- [ ] 🤖 4H/1H bias is directionally clear (bullish or bearish — not range) — `evaluate_tf_trend`
- [ ] 🤖 BTC dominance noted as secondary context — `evaluate_btc_dominance`
- [ ] 🤖 Funding rate not extreme in the same direction as the trade — `evaluate_funding_rate`

### 🕯️ Structural Trigger (1H / 15m)

- [ ] 🤖 BOS, CHOCH, sweep, or retest identified on 1H/15m — `evaluate_market_structure`
- [ ] 🤖 Entry zone near a key level (S/R, FVG, VWAP) — not mid-range — `evaluate_entry_zone`
- [ ] 🤖 OI backing the directional move — `evaluate_open_interest`
- [ ] 🤖 No high squeeze risk on the entry side — `evaluate_squeeze_risk`

### 💰 Risk Management

- [ ] 👤 Invalidation level clearly defined (structure-based)
- [ ] 👤 Stop placed below/above the structural trigger, not the signal candle
- [ ] 👤 R:R minimum 1:2 against the session's next key level
- [ ] 👤 No open macro event in the next 2–4h

> **Intraday golden rule:** Do not enter unless bias, level, trigger, stop, target, and invalidation are all clear. If price is in the middle of a range, skip the trade.

---

## Skills

| Skill | Swing timeframes | Intraday timeframes | What it evaluates |
|---|---|---|---|
| `evaluate_tf_trend` | 1D / 1W | 4H / 1H | Trend direction via moving averages — recommends LONG or SHORT |
| `evaluate_market_structure` | 4H / 1D | 1H / 15m | Swing structure (HH/HL, LH/LL), conclusion, confidence, invalidation levels |
| `evaluate_btc_dominance` | — | — | Whether BTC dominance supports the expected move |
| `evaluate_funding_rate` | — | — | Funding rate level and contrarian bias |
| `evaluate_open_interest` | — | — | OI vs price consistency — validates or weakens directional bias |
| `evaluate_squeeze_risk` | — | — | Crowded trade detection — funding, L/S ratio, basis, OI, liquidation bias |
| `evaluate_entry_zone` | — | — | Entry-zone quality — S/R, FVG, OB, Fibonacci, range/retest/sweep, structural invalidation |

Skills marked `—` use the same logic regardless of mode.

---

## Architecture

```
cli.py / app.py  →  agent.py (run_agent, mode)  →  LiteLLM + Claude  →  skills/*.py
```

- `agent.py` holds `TOOLS` and `SYSTEM_PROMPT` as dicts keyed by `"swing"` / `"intraday"`.
- Skills read `TRADE_MODE` from the environment at call time to switch timeframes.
- `run_agent(pair, mode="swing")` is the single orchestration entry point.

## Data Sources

| Source | Used by |
|---|---|
| BingX | `evaluate_tf_trend`, klines for trend; portfolio equity curve |
| OKX | `evaluate_market_structure`, candles |
| Bybit | `evaluate_funding_rate`, `evaluate_squeeze_risk`, `evaluate_open_interest` |
| CoinGecko | `evaluate_btc_dominance` |

All sources are public, unauthenticated endpoints (except BingX portfolio/positions which require API keys).
