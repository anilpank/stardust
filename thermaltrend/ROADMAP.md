# Thermaltrend Implementation Roadmap

**Date:** July 2026
**Context:** Updated from DESIGN.md — system goal is to test, validate, and select the best performing strategies across multiple classes.

---

## Current State

Working pipeline: **DataFeed → DataEngine → Strategy → SignalEvents → TradeSimulator → Analytics**

| Component | Status |
|-----------|--------|
| Data Layer (download, update, feed) | Built |
| Event Queue (MarketEvent, SignalEvent) | Built |
| Strategy Engine (Strategy ABC + MACrossoverStrategy) | Built |
| Signals CLI (`signals.py`) | Built |
| Analytics & Metrics | Built |
| Strategy Library (multi-class) | 4 of ~6 strategies |
| Backtest CLI (`backtest.py`) | Built |
| Compare CLI (`compare_cli.py`) | Built |
| Signal Persistence (`signal_store.py`) | Built |
| Portfolio & Risk | Not built |
| Execution Handler | Not built |

Source: ~2,800 lines across 17 modules. Tests: ~3,400 lines across 21 files (223 unit tests).

---

## What's Built (Analytics)

```
thermaltrend/analytics/
├── __init__.py
├── trade_simulator.py   # SignalEvent pairs → simulated trades with ATR stops
├── metrics.py           # CAGR, Sharpe, Sortino, MaxDD, Calmar, win rate, confidence
├── regime.py            # Market regime detection (BULL/BEAR/SIDEWAYS)
├── compare.py           # Multi-strategy ranking table with SPY B&H baseline
└── report.py            # Terminal table + JSON + CSV export
```

**Analytics features:**
- $10K fixed position sizing per trade
- 2× ATR (14-day) stop loss, configurable
- Entry/exit at next day's open (no lookahead bias)
- Unmatched BUYs closed at last price, flagged as `data_end`
- Per-ticker performance breakdown
- Market regime analysis (BULL/BEAR/SIDEWAYS)
- Confidence score (0.0–1.0) based on sample size, consistency, ticker diversity
- SPY buy-and-hold benchmark comparison

**CLI tools:**
- `backtest.py` — single-strategy backtest with metrics, per-ticker breakdown, regime analysis, JSON/CSV export
- `compare_cli.py` — multi-strategy comparison with ranking table and SPY benchmark
- `signal_store.py` — list, show, annotate, and query saved signal runs
- `signals.py --save` — persist signals directly from the signal generation tool

---

## Recommended Build Order (Updated)

### Phase 3: Strategy Library (build next)

You need multiple strategies to have anything meaningful to compare. Build simplest first — each teaches something about the framework's flexibility.

| # | Strategy | Class | Why | Complexity |
|---|----------|-------|-----|------------|
| 1 | MACrossover | Trend | Built | Done |
| 2 | Donchian Breakout | Trend | Complementary to MA (entry/exit logic differs) | Done |
| 3 | RSI Mean Reversion | Mean Reversion | Tests a completely different regime (sideways markets) | Done |
| 4 | ATR Trailing Stop | Trend | Volatility-based risk management | Done |
| 5 | Dual Momentum | Momentum | Cross-asset relative strength | Medium |
| 6 | Simple Factor Scoring | Factor | Multi-signal composite rank | Medium-High |

```
thermaltrend/
└── strategy/
    ├── __init__.py
    ├── ma_crossover.py        # Move existing MACrossoverStrategy here
    ├── donchian_breakout.py
    ├── rsi_mean_reversion.py
    ├── atr_trailing_stop.py
    ├── dual_momentum.py
    └── factor_scorer.py
```

The existing `Strategy` ABC is already clean — each new strategy is just a new file implementing `on_market(event) -> SignalEvent | None`. The signals CLI already has a `--strategy` flag with a registry dict, so adding a strategy name there makes it immediately CLI-runnable.

### Phase 2 (cont.): Signal Logging ← Built

```python
from thermaltrend.signal_store import SignalStore

store = SignalStore()
run_id = store.save(signals, "ma_crossover", ["AAPL", "MSFT"])
store.annotate(signal_id, "acted", notes="Entered at $195")
pending = store.get_pending_signals()
```

Saved to `thermaltrend/data/signals/` (one Parquet per run) and `thermaltrend/data/actions/` (annotations).

### Phase 3: Strategy Library (4 of 6 built)

You need multiple strategies to have anything meaningful to compare. Build simplest first — each teaches something about the framework's flexibility.

| # | Strategy | Class | Why | Complexity |
|---|----------|-------|-----|------------|
| 1 | MACrossover | Trend | Built | Done |
| 2 | Donchian Breakout | Trend | Complementary to MA (entry/exit logic differs) | Done |
| 3 | RSI Mean Reversion | Mean Reversion | Tests a completely different regime (sideways markets) | Done |
| 4 | ATR Trailing Stop | Trend | Volatility-based risk management | Done |
| 5 | Dual Momentum | Momentum | Cross-asset relative strength | Medium |
| 6 | Simple Factor Scoring | Factor | Multi-signal composite rank | Medium-High |

```
thermaltrend/
└── strategy/
    ├── __init__.py
    ├── ma_crossover.py        # Move existing MACrossoverStrategy here
    ├── donchian_breakout.py
    ├── rsi_mean_reversion.py
    ├── atr_trailing_stop.py
    ├── dual_momentum.py
    └── factor_scorer.py
```

The existing `Strategy` ABC is already clean — each new strategy is just a new file implementing `on_market(event) -> SignalEvent | None`. The signals CLI already has a `--strategy` flag with a registry dict, so adding a strategy name there makes it immediately CLI-runnable.

### Phase 4: Portfolio & Execution (for actual trading)

This bridges "signals" to "trades" — even if execution is manual, you need position sizing.

```
thermaltrend/
└── portfolio/
    ├── __init__.py
    ├── manager.py     # Track open positions, PnL, mark-to-market
    ├── risk.py        # Max position size, max drawdown circuit breaker
    └── sizing.py      # Volatility-targeted or fixed-fractional sizing
```

### Phase 5: Execution Handler

- `OrderEvent` and `FillEvent` added to the event system
- Simulated execution handler with slippage and commission models
- Live broker bridge (SAXO/Revolut API)

### Phase 6: Live Deployment

- Paper trading on validated strategies
- Gradual transition to live capital
- Monitor for strategy degradation (performance decay over time)
- Periodic re-validation: re-run backtests on fresh data, retire strategies that no longer work

---

## Summary

```
Phase 2 (done)      →  analytics/metrics.py + report.py + trade_simulator.py + regime.py + compare.py
Phase 2 cont (done) →  signal_store.py + backtest.py + compare_cli.py (persistence + CLI tools)
Phase 3 (partial)   →  4 of 6 strategies built; dual momentum, factor scorer next
Phase 4 (next)      →  portfolio/ package (position sizing, PnL)
Phase 5 (later)     →  execution/ (OrderEvent, FillEvent, simulated fills)
Phase 6 (future)    →  live broker bridge
```

**Next up:** Dual Momentum (cross-asset relative strength introduces a new dimension beyond single-ticker analysis) + Factor Scoring (multi-signal composite rank).
