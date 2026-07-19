# Design Document: Thermaltrend Event-Driven Trading System

**Author:** Anil
**Date:** July 2026
**Status:** Phase 1-2 Complete (Data Layer + Event Queue + Signal Generation)

---

## 1. Motivation

### Why Build This?

The goal is a personal trading system to **test, validate, and select the best performing strategies** on S&P 500 equities — not limited to trend-following, but encompassing any strategy class (momentum, mean reversion, factor-based, statistical arbitrage, etc.). The workflow is:

1. **Backtest** — run multiple strategy types on historical data, measure performance
2. **Compare & validate** — rank strategies by risk-adjusted returns, robustness, and consistency
3. **Signal generation** — run validated strategies daily, see ranked signals
4. **Manual execution** — act on strongest signals via SAXO/Revolut
5. **Future: automate** — eventually connect to broker APIs

The key requirement is **trustworthiness**. A backtest that says "you'd have made 40% annually" is worthless if it contains subtle lookahead bias. The system must process data exactly the way a live system would — bar by bar, no future information leaking into past decisions.

### Strategy-Agnostic Design

The system is deliberately designed to be strategy-agnostic. The `Strategy` ABC accepts a `MarketEvent` and returns a `SignalEvent` — any strategy that fits this interface can be plugged in. The goal is to run a broad universe of strategies, identify the ones that perform best on S&P 500 equities, and trade only those that survive rigorous out-of-sample validation.

---

## 2. Architecture Overview

```
DataFeed ──▶ EventQueue ──▶ Strategy ──▶ SignalEvents ──▶ CLI / User
(Parquet)    (deque)        (MACross)    (ranked)        (manual trade)
```

Six layers planned, two built:

| Layer | Status | What |
|-------|--------|------|
| Data Layer | Built | Download, update, inspect Parquet files; `DataFeed` yields chronological bars |
| Event Queue | Built | `MarketEvent` → `SignalEvent` flow via `EventQueue`; `DataEngine` orchestrates |
| Strategy Engine | Partial | `Strategy` ABC + `MACrossoverStrategy`; needs multi-class strategy library |
| Execution Handler | Planned | Simulated fills, slippage models, live broker bridge |
| Portfolio & Risk | Planned | Position sizing, risk rules, PnL tracking |
| Analytics | Planned | Sharpe, max drawdown, tearsheet, benchmark comparison, **strategy ranking & selection** |

---

## 3. Design Decisions

### 3.1 Event-Driven Over Vectorized

**Decision:** Event-driven architecture (bar-by-bar processing).

**Alternatives considered:**
- **Vectorized backtest** (VectorBT style) — processes all bars as a single matrix operation
- **Hybrid** — vectorized for research, event-driven for production

**Why event-driven:**
- Vectorized backtests silently assume you can look at the entire dataset at once. A "golden cross" signal computed on the full price series is technically correct but would be impossible to detect in real-time — you'd need to already know the future prices.
- Event-driven processes one bar at a time. The strategy at time T can only see data up to T. This is the same constraint as live trading.
- The same engine code works for backtest (feeding historical bars) and live (feeding real-time data). No "backtest-to-live gap."
- Easier to add realistic execution — slippage, commissions, partial fills all plug into the event flow naturally.

**Trade-off:** Vectorized backtests are 10-100x faster for parameter sweeps. We may add a vectorized research mode later for rapid prototyping, but the core engine stays event-driven.

### 3.2 Deque-Based FIFO Queue

**Decision:** `collections.deque` for the event queue.

**Alternatives considered:**
- **`heapq`** — priority queue sorted by timestamp
- **`asyncio.Queue`** — for async/concurrent processing
- **Custom event bus** — publish/subscribe pattern

**Why deque:**
- DataFeed yields bars in strict chronological order. Events are generated in timestamp order. A FIFO is sufficient — no re-sorting needed.
- `deque.popleft()` is O(1), same as `heapq.heappop`, but simpler and faster in practice (no comparison overhead on event objects).
- If we later need priority within a single timestamp (e.g., process all MarketEvents before Signals on the same bar), we can add a `priority` field and switch to `heapq` with minimal changes.

**Trade-off:** If events arrive out of order (e.g., from multiple live data feeds), deque won't handle re-ordering. That's a future concern — for daily bars, order is guaranteed.

### 3.3 Frozen Dataclasses for Events

**Decision:** All events are `@dataclass(frozen=True)`.

**Alternatives considered:**
- **Mutable dataclasses** — simpler, no boilerplate
- **Named tuples** — immutable but no defaults, no metadata
- **Pydantic models** — validation, but heavy dependency

**Why frozen dataclasses:**
- Events flow through multiple consumers (Strategy, Portfolio, Execution). Immutability prevents one handler from accidentally mutating an event another handler is still processing.
- Each event gets a unique `UUID` for linking (SignalEvent → OrderEvent → FillEvent chain).
- `metadata: dict` field on SignalEvent allows strategies to attach arbitrary data (indicator values, confidence factors) without changing the event schema.

**Trade-off:** Frozen objects can't be modified after creation. If we need to enrich events (e.g., Portfolio adds position sizing info), we create a new event rather than mutating the existing one. Slightly more memory, but safer.

### 3.4 Proper Package Structure

**Decision:** Made `thermaltrend/` a proper Python package with `__init__.py`.

**Alternatives considered:**
- **Flat scripts** — each `.py` file is self-contained, no `__init__.py`
- **`src/` layout** — `src/thermaltrend/` with pyproject.toml

**Why proper package:**
- Cross-module imports (`from thermaltrend.core.events import MarketEvent`) require the parent directory to be a package.
- Existing tests used bare imports (`from feed import Bar`), which only worked because pytest happened to add `thermaltrend/` to sys.path. This was fragile — it broke when we added `__init__.py`.
- Proper packages are the standard for any project that will be installed, shared, or have inter-module dependencies.

**Trade-off:** Bare imports (`from feed import Bar`) are slightly more convenient for quick scripts. We updated all existing tests to use full package imports for consistency.

### 3.5 MACrossover as First Strategy (Baseline)

**Decision:** 50/200 Simple Moving Average crossover as the initial strategy — not because it's the best, but because it's the simplest to validate.

**Alternatives considered:**
- **Donchian Channel Breakout** — buy on new 20-day highs, sell on channel low
- **ATR Trailing Stop** — trend entry with volatility-based exit
- **RSI-based** — mean reversion
- **Momentum ranking** — relative strength across universe
- **Factor models** — value, quality, low-vol

**Why MACrossover first:**
- Simplest possible signal — golden cross / death cross. Easy to verify correctness by hand.
- Well-understood: decades of academic and practitioner evidence.
- Produces both BUY and SELL signals, exercising both code paths.
- Serves as a **baseline** — all future strategies (across any class) will be compared against it.

**Trade-off:** MA crossover is slow — it lags the trend by the lookback period. It whipsaws in sideways markets. But that's exactly why it's a useful baseline: a strategy must beat a simple MA crossover to justify its complexity.

### 3.6 Signals CLI for Manual Trading

**Decision:** `signals.py` as a command-line tool that outputs ranked signals.

**Alternatives considered:**
- **Web dashboard** — Flask/Streamlit app with interactive charts
- **Jupyter notebook** — interactive but not automatable
- **Email/Telegram alerts** — push notifications
- **Database storage** — signals saved to SQLite, queried later

**Why CLI:**
- Matches the current workflow: run daily, see signals, decide manually.
- Zero infrastructure — no web server, no database, no dependencies.
- Scriptable — can add to cron job or run manually each morning.
- The output is a ranked table — no complex visualization needed yet.

**Trade-off:** CLI doesn't persist signals or track which ones were acted on. That's the next step (signal logging). A web dashboard would be nicer for visual analysis but adds significant complexity.

### 3.7 Parquet for Data Storage

**Decision:** Individual Parquet files per ticker, not a database.

**Alternatives considered:**
- **SQLite** — single file, SQL queries, ACID transactions
- **DuckDB** — analytical queries on Parquet files directly
- **CSV** — simple, human-readable
- **Polars** — faster DataFrame operations

**Why Parquet:**
- Columnar format — fast reads for OHLCV data (you rarely need all columns).
- Compressed — 501 tickers of daily data from 1970 fits in ~50MB.
- Native pandas/pyarrow support — no extra dependencies.
- Each ticker as a separate file — easy to update (just overwrite one file), easy to inspect, no locking issues.

**Trade-off:** No cross-ticker queries without loading all files. DuckDB could query across Parquet files directly, but adds a dependency. We load everything into a combined DataFrame at startup anyway.

---

## 4. What We Built and Why

### 4.1 Data Acquisition Layer (`download_data.py`, `update_data.py`)

**Why separate download and update scripts:**
- First download is slow (501 tickers × Yahoo Finance API). Incremental update only fetches missing days — 10x faster for daily use.
- `download_data.py` fetches fresh S&P 500 constituents from Wikipedia each time. `update_data.py` only updates existing files — it never adds new tickers.
- Clear separation: download = "get everything", update = "keep it current."

**Why `gc.collect()` in update_data.py:**
- yfinance opens file descriptors for each download. After processing 501 tickers, the process hits the OS limit ("Too many open files"). The `gc.collect()` in a `finally` block forces garbage collection of the yfinance objects.
- This was a real bug discovered during testing. Fix was in commit `79bc38e`.

### 4.2 DataFeed (`feed.py`)

**Why load everything into a combined DataFrame:**
- The DataFeed loads all Parquet files at construction time into a MultiIndex DataFrame `[date, ticker]`.
- This allows fast lookups: `get_bars_for_date(date)` is O(1) via index, not O(n) scan.
- Memory trade-off: with 501 tickers × ~50 years of daily data, this is ~50MB in memory. Acceptable for backtesting.

**Why `Bar` dataclass instead of raw DataFrame rows:**
- Typed fields (`open`, `close`, `volume`) prevent column name typos.
- Immutable-ish (dataclass, not frozen — because we iterate over rows).
- Clean API: `bar.ticker` is clearer than `row["ticker"]`.

### 4.3 Event Queue (`core/events.py`)

**Why `MarketEvent` wraps `Bar`:**
- `Bar` is a data transport object (DTO) — it carries raw data from the feed.
- `MarketEvent` is a domain event — it carries the same data but with a `timestamp` field and is part of the event system.
- Separation of concerns: DataFeed doesn't know about events; the engine converts Bars to MarketEvents.

**Why no `OrderEvent` or `FillEvent` yet:**
- The user's workflow is manual. They see signals and place orders themselves.
- Building OrderEvent/FillEvent now would be speculative — we don't know the exact shape until we need automated execution.
- YAGNI (You Aren't Gonna Need It) applies here.

### 4.4 Strategy Engine (`core/strategy.py`)

**Why abstract base class:**
- `Strategy` is an ABC with a single method: `on_market(event) -> SignalEvent | None`.
- Forces all strategies to have the same interface. The engine doesn't need to know which strategy it's running.
- Easy to add new strategies: just subclass `Strategy` and implement `on_market()`.

**Why strategy maintains internal state:**
- `MACrossoverStrategy` stores price history per ticker (`_prices: dict[str, list[float]]`).
- This is necessary — MAs need lookback windows. A stateless strategy can only do instant decisions (e.g., "close > 200-day high → buy").
- State is per-ticker, so the same strategy instance handles multiple tickers independently.

### 4.5 DataEngine (`core/engine.py`)

**Why a separate Engine class:**
- The engine orchestrates: Feed → MarketEvent → Strategy → SignalEvent.
- Keeps the event loop in one place. Strategy doesn't know about the feed; Feed doesn't know about the strategy.
- Easy to swap strategies or feeds without changing the engine.

**Why events are processed per-date, not globally:**
- All bars for a given date are loaded, converted to MarketEvents, and put in the queue.
- The queue is drained for that date before moving to the next date.
- This ensures all tickers for a given date are processed together — important for cross-ticker strategies (e.g., sector rotation).

### 4.6 Signals CLI (`signals.py`)

**Why signals are sorted by strength descending:**
- The user wants to see the strongest signals first — these are the highest-conviction trades.
- Strength is normalized to [0, 1] based on MA separation relative to price.

**Why filtering by direction and min-strength:**
- User may only want BUY signals (not SELL).
- User may want to filter out weak signals (strength < 0.5) that aren't worth acting on.

---

## 5. Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Daily bars only | Can't detect intraday patterns | Sufficient for initial validation; intraday support planned for P6 |
| No signal persistence | Signals from previous days are lost | Next step: save signals to Parquet |
| Single strategy | Can't combine signals or compare strategies | Strategy library expansion planned in P3; strategy comparison in P4 |
| No benchmark comparison | Can't tell if strategy beats S&P 500 | Planned in Analytics layer (P4) |
| No position sizing | Signals don't include "how much to buy" | Planned in Portfolio layer |
| No walk-forward validation | Overfitting risk not addressed | Planned in P4 — rolling out-of-sample windows |
| `gc.collect()` workaround | File descriptor leak is a symptom, not root cause | Acceptable for now; could switch to session-based yfinance usage |

---

## 6. What's Next

### Phase 2: Signal Logging + Portfolio Tracking
- Save signals to Parquet with timestamp, direction, strength, metadata
- Track which signals were acted on (manual annotation)
- Record manual trades for PnL calculation

### Phase 3: Strategy Expansion (Multi-Class)
Implement strategies across multiple classes to cast a wide net:
- **Trend Following:** Donchian Channel Breakout, ATR Trailing Stop, Adaptive MA (KAMA)
- **Momentum:** Dual Momentum (absolute + relative), Sector Rotation, RSI momentum
- **Mean Reversion:** RSI-based entries, Bollinger Band bounce
- **Factor-Based:** Simple value/quality/momentum factor scoring
- All strategies share the same `Strategy` ABC interface — plug and play.

### Phase 4: Backtesting Framework + Strategy Comparison
- Compute Sharpe, Sortino, max drawdown, CAGR, Calmar, win rate, profit factor
- Compare each strategy vs S&P 500 buy-and-hold and vs each other
- Walk-forward analysis (rolling out-of-sample validation)
- **Strategy selection:** rank strategies by risk-adjusted return, robustness, and consistency across market regimes
- Identify the top N strategies to trade; discard the rest

### Phase 5: Automated Execution
- Add `OrderEvent` and `FillEvent` to the event system
- Simulated execution handler with slippage and commission models
- Live broker bridge (SAXO/Revolut API)

### Phase 6: Live Deployment
- Run validated strategies on a paper trading account
- Gradually transition to live trading with real capital
- Monitor for strategy degradation (performance decay over time)
- Periodic re-validation: re-run backtests on fresh data, retire strategies that no longer work

---

## 7. Key Takeaways

1. **Start with the simplest thing that works.** The event queue is a deque. The strategy is a moving average. The CLI outputs a table. We can always make it more complex later.

2. **Immutability prevents subtle bugs.** Frozen dataclasses mean events can't be accidentally mutated as they flow through the system.

3. **Separation of concerns pays off.** DataFeed doesn't know about strategies. Strategies don't know about execution. The engine just connects them. This makes each piece independently testable and replaceable.

4. **Build for manual first, automate later.** The signal CLI is the interface today. When automation is needed, the same engine produces the same signals — we just add an execution handler instead of printing them.

5. **Trust requires evidence.** Event-driven architecture isn't the simplest approach, but it's the only one that gives confidence that backtest results would replicate in live trading.

6. **Strategy-agnostic beats strategy-specific.** By keeping the `Strategy` ABC generic, we can test any strategy class — trend, momentum, mean reversion, factor-based — without changing the engine. The best strategies will emerge from rigorous comparison, not from choosing a single approach upfront.
