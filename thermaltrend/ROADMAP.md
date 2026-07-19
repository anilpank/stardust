# Thermaltrend Implementation Roadmap

**Date:** July 2026
**Context:** Updated from DESIGN.md — system goal is now to test, validate, and select the best performing strategies across multiple classes (not just trend-following).

---

## Current State

Working pipeline: **DataFeed → DataEngine → Strategy → SignalEvents → CLI**

| Component | Status |
|-----------|--------|
| Data Layer (download, update, feed) | Built |
| Event Queue (MarketEvent, SignalEvent) | Built |
| Strategy Engine (Strategy ABC + MACrossoverStrategy) | Built |
| Signals CLI (`signals.py`) | Built |
| Analytics & Metrics | Not built |
| Strategy Library (multi-class) | 1 of ~6 strategies |
| Signal Persistence | Not built |
| Portfolio & Risk | Not built |
| Execution Handler | Not built |

Source: ~870 lines across 9 modules. Tests: ~1800 lines across 12 files.

---

## Recommended Build Order

### Phase 2: Analytics & Metrics (build first)

Highest-leverage module. Without it, you can't compare strategies or know if anything works.

```
thermaltrend/
└── analytics/
    ├── __init__.py
    ├── metrics.py      # Sharpe, Sortino, CAGR, max drawdown, Calmar, win rate, profit factor
    └── report.py       # Strategy vs S&P 500 buy-and-hold, side-by-side comparison table
```

**Why first:** You already have signal output. Adding metrics means you can immediately evaluate the MA crossover against a benchmark. Every future strategy gets instant comparison for free.

---

### Phase 3: Strategy Library (expand next)

You need multiple strategies to have anything meaningful to compare. Build simplest first — each teaches something about the framework's flexibility.

| # | Strategy | Class | Why | Complexity |
|---|----------|-------|-----|------------|
| 1 | MACrossover | Trend | Built | Done |
| 2 | Donchian Breakout | Trend | Complementary to MA (entry/exit logic differs) | Low |
| 3 | RSI Mean Reversion | Mean Reversion | Tests a completely different regime (sideways markets) | Low |
| 4 | ATR Trailing Stop | Trend | Volatility-based risk management | Medium |
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

---

### Phase 2 (cont.): Signal Logging

Currently signals vanish after the CLI prints them. You need persistence to track which ones you acted on.

```
thermaltrend/
└── signal_store.py   # Save/load signals to Parquet with timestamps, metadata, action tracking
```

**What it enables:** Run signals daily, save them, annotate which ones you traded, compute actual PnL later.

---

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

---

### Phase 5: Execution Handler

- `OrderEvent` and `FillEvent` added to the event system
- Simulated execution handler with slippage and commission models
- Live broker bridge (SAXO/Revolut API)

---

### Phase 6: Live Deployment

- Paper trading on validated strategies
- Gradual transition to live capital
- Monitor for strategy degradation (performance decay over time)
- Periodic re-validation: re-run backtests on fresh data, retire strategies that no longer work

---

## Summary

```
Phase 2 (now)     →  analytics/metrics.py + report.py
Phase 3 (now)     →  strategy/ package with 2-3 new strategies
Phase 2 (cont.)   →  signal_store.py (persistence)
Phase 4 (soon)    →  portfolio/ package (position sizing, PnL)
Phase 5 (later)   →  execution/ (OrderEvent, FillEvent, simulated fills)
Phase 6 (future)  →  live broker bridge
```

**Start with:** `analytics/metrics.py` (~100 lines, immediately useful) + one new strategy (Donchian Breakout is lowest-hanging fruit — simple logic, different enough from MA crossover to be interesting). With two strategies and a metrics layer, you have the minimum viable comparison framework.
