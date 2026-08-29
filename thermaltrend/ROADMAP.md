# Thermaltrend Implementation Roadmap

**Date:** July 2026 (updated August 2026)
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
| Strategy Library (multi-class) | 5 of ~6 strategies |
| Backtest CLI (`backtest.py`) | Built |
| Compare CLI (`compare_cli.py`) | Built |
| Signal Persistence (`signal_store.py`) | Built |
| Per-Period Breakdown (monthly/quarterly/yearly) | Built |
| Streamlit Dashboard (`dashboard.py` + `charts.py`) | Built (Dual Momentum supported — benchmark auto-injected via `resolve_feed_tickers()`); Universe selector in sidebar |
| Point-in-Time Universe (`--universe point_in_time` + `membership.csv`) | Built (restricts trades to actual S&P 500 membership windows) |
| Removed-Member Data (`data/equities_removed/` + `download_removed.py`) | Built (258 removed members backfilled; 445 genuinely delisted) |
| Survivorship-Bias Tooling (`survivorship_bias.py`, `removed_coverage.py`) | Built (bias ~2.6–4.3%/yr survivors-only; ±<1%/yr residual with PIT) |
| Portfolio & Risk | Not built |
| Execution Handler | Not built |

Source: ~4,800 lines across 22 modules. Tests: ~5,300 lines across 24 files (388 tests; 360 fast unit + 28 integration).

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
- Point-in-time universe: positions force-closed at the member's last S&P 500 day (`universe_exit`) or at a Shumway-style delisting return (−30% default) when price data runs out before removal (`delisted`, `max_delisting_gap_days` default 10)
- Per-ticker performance breakdown
- Market regime analysis (BULL/BEAR/SIDEWAYS)
- Confidence score (0.0–1.0) based on sample size, consistency, ticker diversity
- SPY buy-and-hold benchmark comparison

**CLI tools:**
- `backtest.py` — single-strategy backtest with metrics, per-ticker breakdown, regime analysis, JSON/CSV export; `--universe current|point_in_time`
- `compare_cli.py` — multi-strategy comparison with ranking table and SPY benchmark; `--universe current|point_in_time`
- `signal_store.py` — list, show, annotate, and query saved signal runs
- `signals.py --save` — persist signals directly from the signal generation tool
- `build_membership.py` — rebuild `membership.csv` (point-in-time member stints)
- `download_removed.py` — backfill price data for removed S&P 500 members (parallel, resumable)
- `removed_coverage.py` — per-stint data coverage report for removed members
- `survivorship_bias.py` — quantify survivorship bias vs the real equal-weight index (`--include-removed` for the PIT estimate)

---

## Recommended Build Order (Updated)

### Phase 3: Strategy Library (5 of 6 built)

You need multiple strategies to have anything meaningful to compare. Build simplest first — each teaches something about the framework's flexibility.

| # | Strategy | Class | Why | Complexity |
|---|----------|-------|-----|------------|
| 1 | MACrossover | Trend | Built | Done |
| 2 | Donchian Breakout | Trend | Complementary to MA (entry/exit logic differs) | Done |
| 3 | RSI Mean Reversion | Mean Reversion | Tests a completely different regime (sideways markets) | Done |
| 4 | ATR Trailing Stop | Trend | Volatility-based risk management | Done |
| 5 | Dual Momentum | Momentum | Absolute + relative momentum vs benchmark (SPY). Benchmark series learned from event stream — CLI needs SPY in the ticker list; the dashboard injects it automatically. Default lookback 126 days. | Done |
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

Note: strategies currently live in `thermaltrend/core/strategy.py`. Splitting them
into a `strategy/` subpackage is optional refactoring — the registry pattern in
`signals.py`, `backtest.py`, `compare_cli.py`, and `dashboard.py` makes each new
strategy immediately available everywhere.

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
Phase 3 (nearly done) → 5 of 6 strategies built; factor scorer next
Phase 3a (done)     →  per-period breakdown (monthly/quarterly/yearly) in metrics + report + CLI
Phase 3b (done)     →  Streamlit dashboard (dashboard.py, charts.py): Overview, Trades, Per-Ticker,
                       Regime, Signals, Compare, Saved Runs, Data Explorer, Compare Tickers tabs;
                       Dual Momentum fully supported (SPY benchmark auto-injected into every feed)
Phase 3c (done)     →  survivorship-bias correction: membership.csv + data/equities_removed/ +
                       --universe point_in_time (Shumway delisting exits; bias quantified
                       ~2.6–4.3%/yr survivors-only → ±<1%/yr residual with PIT universe)
Phase 4 (next)      →  portfolio/ package (position sizing, PnL)
Phase 5 (later)     →  execution/ (OrderEvent, FillEvent, simulated fills)
Phase 6 (future)    →  live broker bridge
```

**Next up:** Simple Factor Scoring (multi-signal composite rank) — the last strategy in Phase 3. Then Phase 4: the `portfolio/` package (position sizing, risk limits).
