# Swing Trade Evaluator

Agent-assisted checklist for evaluating BTC perpetual futures swing trades at 5x leverage.

Run the agent against any pair:

```bash
python3 cli.py BTC
```

---

## Checklist — Long/Short BTC Perp 5x Swing Trade

Legend: 🤖 = covered by a skill | 👤 = manual review required

### 📊 Market Context

- [ ] 🤖 Higher timeframe trend (1D / 1W) aligned with trade direction — `evaluate_tf_trend`
- [ ] 🤖 BTC dominance (BTC.D) supports the expected move — `evaluate_btc_dominance`
- [ ] 👤 No imminent macro event (CPI, FOMC, rate decision) in the next 24–48h
- [ ] 🤖 Current funding rate reviewed — very positive = bearish bias, very negative = bullish bias — `evaluate_funding_rate`
- [ ] 🤖 Open Interest trending consistently with the move (OI rising with price = confirmation) — `evaluate_open_interest`

### 🕯️ Technical Setup

- [ ] 🤖 Clear market structure on 4H/1D (HH-HL for longs, LH-LL for shorts), including invalidation/range levels — `evaluate_market_structure`
- [ ] 👤 Entry zone defined (support/resistance, FVG, OB, Fibonacci retracement)
- [ ] 👤 Confluence of at least 2 indicators or levels at entry
- [ ] 👤 Volume confirms the setup (do not enter against growing volume in the opposite direction)
- [ ] 👤 No significant RSI or MACD divergence against the trade

### 💰 Risk Management

- [ ] 👤 Stop loss defined **before** entering, based on structure (not liquidation price)
- [ ] 👤 SL distance calculated — at 5x, a 20% move liquidates; SL must be well before that
- [ ] 👤 Position size calculated to risk a maximum of 1–2% of total capital
- [ ] 👤 Minimum R:R of 1:2 (ideally 1:3) verified against the target take profit
- [ ] 👤 Allocated margin does not exceed 10–15% of available account capital

### 🎯 Exit Plan

- [ ] 👤 TP1 defined at a key technical level (partial exit, e.g. 50% of position)
- [ ] 👤 TP2 / final TP defined
- [ ] 👤 Trailing stop or price-based exit criterion defined if the trade extends
- [ ] 👤 Maximum time in the trade defined (swing = days, not indefinite weeks)

### ⚙️ Operational Execution

- [ ] 👤 Entry order type: limit or market? (limit preferred to avoid slippage)
- [ ] 👤 Stop loss placed **on the platform** before stepping away from the trade
- [ ] 👤 Margin mode reviewed: **Cross vs Isolated** — for 5x swing, prefer **Isolated**
- [ ] 👤 Liquidation price visible and sufficiently far from current price
- [ ] 👤 No opposing position open in the same asset causing confusion

> **Golden rule at 5x:** Leverage amplifies mistakes as much as gains. If the setup does not have at least 7–8 items checked, do not enter.

---

## Skills

| Skill | What it evaluates |
|---|---|
| `evaluate_tf_trend` | 1D and 1W trend direction — recommends LONG or SHORT |
| `evaluate_market_structure` | 4H and 1D swing structure (HH/HL, LH/LL, or undefined), with combined conclusion, confidence, and invalidation/range levels |
| `evaluate_btc_dominance` | Whether BTC dominance supports the expected move |
| `evaluate_funding_rate` | Funding rate level and directional bias |
| `evaluate_open_interest` | OI vs price consistency — validates or weakens the directional bias |
| `evaluate_squeeze_risk` | Crowded trade detection — combines funding, L/S ratio, basis, OI, and liquidation bias |
