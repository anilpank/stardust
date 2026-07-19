# Architecture Proposal for Thermaltrend

## 1. Core Design Decision: Event-Driven, Not Vectorized

For **production-grade**, event-driven is the right call. Vectorized backtests (VectorBT style) are fast for prototyping but introduce structural risks: lookahead bias, unrealistic fill assumptions, and a "backtest-to-live gap." Since you're building for production, the event loop ensures your backtester processes data the same way a live system would.

That said, you could add a **vectorized mode** later for rapid parameter sweeps during research — but the core engine should be event-driven.

---

## 2. High-Level Architecture (6 Layers)

```
┌─────────────────────────────────────────────────────┐
│                   THERMALTREND                       │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐│
│  │  Data     │──▶│  Event   │──▶│    Strategy      ││
│  │  Layer    │   │  Queue   │   │    Engine        ││
│  └──────────┘   └──────────┘   └──────────────────┘│
│       │              │                   │           │
│       │              ▼                   ▼           │
│       │         ┌──────────┐   ┌──────────────────┐│
│       │         │Portfolio │◀──│   Execution      ││
│       │         │ & Risk   │   │   Handler        ││
│       │         └──────────┘   └──────────────────┘│
│       │              │                              │
│       ▼              ▼                              │
│  ┌──────────────────────────────────────────────┐   │
│  │         Analytics & Reporting Layer          │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 3. Proposed Directory Structure

```
thermaltrend/
├── pyproject.toml
├── src/
│   └── thermaltrend/
│       ├── __init__.py
│       ├── core/                    # Event engine, base classes
│       │   ├── events.py            # Event types (Market, Signal, Order, Fill)
│       │   ├── engine.py            # Main event loop
│       │   └── base.py              # Abstract base classes
│       ├── data/                    # Data ingestion & management
│       │   ├── provider.py          # Data source abstraction
│       │   ├── feed.py              # Data feed / handler
│       │   ├── cache.py             # Local caching (Parquet/SQLite)
│       │   └── adjustments.py       # Corporate actions, splits, dividends
│       ├── strategy/                # Strategy logic
│       │   ├── base.py              # Strategy ABC
│       │   ├── signals.py           # Signal generation (indicators)
│       │   └── trend_following/     # Strategy implementations
│       │       ├── ma_crossover.py
│       │       ├── breakout.py
│       │       └── adaptive.py
│       ├── portfolio/               # Position & risk management
│       │   ├── manager.py           # Position tracking, PnL
│       │   ├── risk.py              # Risk rules, drawdown limits
│       │   └── sizing.py            # Position sizing (Kelly, vol-target, etc.)
│       ├── execution/               # Order simulation
│       │   ├── handler.py           # Fill simulation
│       │   ├── slippage.py          # Slippage models
│       │   └── commission.py        # Commission models
│       ├── analytics/               # Performance measurement
│       │   ├── metrics.py           # Sharpe, Sortino, max DD, Calmar, etc.
│       │   └── report.py            # Tearsheet / HTML report generation
│       └── utils/
│           ├── config.py            # Configuration management
│           └── logging.py           # Structured logging
├── tests/
│   ├── test_engine.py
│   ├── test_strategy.py
│   ├── test_portfolio.py
│   └── test_execution.py
├── notebooks/                       # Research notebooks
│   └── 01_research.ipynb
└── configs/
    └── default.yaml                 # Default backtest configuration
```

---

## 4. Key Components in Detail

### Event Queue (Core Nervous System)

```python
# Events flow chronologically:
MarketEvent → SignalEvent → OrderEvent → FillEvent
```

- **MarketEvent**: New bar arrived (OHLCV + metadata)
- **SignalEvent**: Strategy generated a buy/sell/hold signal
- **OrderEvent**: Portfolio decided to place an order (with sizing)
- **FillEvent**: Execution handler simulated the fill (with slippage/costs)

The queue ensures **strictly sequential processing** — no future data leaks.

### Data Layer

- **Source abstraction**: Pluggable providers (Yahoo Finance, Alpha Vantage, Polygon.io, or local CSV/Parquet)
- **Corporate actions handling**: Adjusted close computed from raw prices (not pre-adjusted) to avoid lookahead bias
- **Local cache**: Parquet files keyed by ticker + date range, so you only download once
- **S&P 500 constituent management**: Track actual constituents over time (survivorship bias fix) using a historical constituent list

### Strategy Engine

- **Interface**: `Strategy.on_market_bar(bar) -> Optional[SignalEvent]`
- **Stateful**: Strategy maintains internal state (e.g., moving average windows, position tracking)
- **Multi-timeframe**: Support for daily strategy logic with intraday data if needed
- **Indicator library**: Technical indicators (SMA, EMA, ATR, RSI, Donchian channels, etc.) that compute incrementally (no lookahead)

### Portfolio & Risk Management

- **Position sizing**: Volatility-targeted sizing, fixed-fractional, Kelly criterion
- **Risk rules**: Max drawdown circuit breaker, max position size, sector concentration limits, correlation-based exposure limits
- **PnL tracking**: Mark-to-market each bar, track realized/unrealized PnL

### Execution Handler

- **Realistic fills**: Model slippage (fixed, percentage, or volume-based)
- **Commission modeling**: Per-share, per-trade, or percentage-based
- **Order types**: Market, limit, stop-loss, trailing stop
- **Partial fills**: Support for large orders that don't fill entirely in one bar

### Analytics

- **Core metrics**: Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor, CAGR, volatility
- **Benchmark comparison**: Alpha, beta, information ratio vs S&P 500
- **Report generation**: HTML tearsheet (similar to Pyfolio output)
- **Walk-forward analysis**: Rolling out-of-sample validation

---

## 5. Critical Design Decisions

| Decision | Options | Recommendation |
|---|---|---|
| **Event-driven vs vectorized core** | Event-driven / Vectorized / Hybrid | Event-driven core, vectorized research mode later |
| **Data format** | CSV / Parquet / SQLite / DuckDB | Parquet (fast, columnar, compresses well) |
| **Universe management** | Static list / Dynamic constituents | Dynamic with historical S&P 500 constituent data |
| **Timeframe** | Daily only / Multi-timeframe | Daily primary, with infrastructure for intraday |
| **Config format** | YAML / TOML / Python dicts | YAML for backtest configs, pyproject.toml for project |
| **Existing framework dependency** | Build from scratch / Extend Backtrader / Use Nautilus | Build core engine from scratch (cleaner, full control) |
| **Concurrency** | Single-threaded / Multiprocessing | Single engine + multiprocessing for parameter sweeps |

---

## 6. Strategy Universe (Multi-Class)

The system is strategy-agnostic. Strategies across multiple classes can be plugged into the same engine:

### Trend Following
1. **Moving Average Crossover** (SMA/EMA golden cross)
2. **Donchian Channel Breakout** (new highs -> entry, channel low -> exit)
3. **ATR Trailing Stop** (trend entry + volatility-based trailing stop)
4. **Adaptive Moving Average** (Kaufman's KAMA)

### Momentum
5. **Dual Momentum** (absolute + relative momentum across sector ETFs)
6. **Sector Rotation** (momentum-based ranking across SPDR sectors)
7. **RSI Momentum** (RSI-based trend continuation entries)

### Mean Reversion
8. **RSI Mean Reversion** (oversold/overbought reversals)
9. **Bollinger Band Bounce** (revert to mean after band touch)

### Factor-Based
10. **Simple Factor Scoring** (value, quality, low-vol composite rank)

### Baseline
- **S&P 500 Buy-and-Hold** — benchmark to beat

All strategies share the same `Strategy` ABC. The comparison framework (Phase 4) will rank them by risk-adjusted return, robustness, and consistency.

---

## 7. Tech Stack

```
Python 3.12+
pandas / numpy          # Core data manipulation
pyarrow / polars        # Parquet I/O, optional high-perf DataFrame
ta-lib or pandas-ta     # Technical indicators (or build your own)
pyyaml                  # Config files
plotly / matplotlib     # Visualization
pytest                  # Testing
```

---

## 8. Phased Implementation Plan

| Phase | Scope | Deliverable |
|---|---|---|
| **P1** | Core engine + data layer + 1 strategy | Runnable backtest on daily S&P 500 data |
| **P2** | Portfolio management + execution + metrics | Full performance report |
| **P3** | Multi-class strategy library (trend, momentum, mean reversion, factor) | Broad strategy universe for comparison |
| **P4** | Strategy comparison + walk-forward + survivorship bias fix | Rank strategies, select top performers, validate out-of-sample |
| **P5** | Advanced features (multi-timeframe, live data feed) | Bridge to live trading |
| **P6** | Live deployment + monitoring | Paper trading → live capital, strategy degradation tracking |

---

## Open Questions

1. **Build from scratch vs. extend an existing framework?** (Lean toward building the core engine — gives full control)
2. **Daily bars only, or intraday support from the start?**
3. **How important is the backtest-to-live bridge?** (Would you eventually trade these strategies live?)
4. **Single strategy backtest or multi-strategy portfolio comparison from day one?**
