# Handoff: Stardust / Thermaltrend

## What This Project Is

An early-stage Python project called **Thermaltrend** — an event-driven backtesting and live-trading system for **testing, validating, and selecting the best performing strategies** on S&P 500 equities. Not limited to trend-following — encompasses momentum, mean reversion, factor-based, and any strategy class that can be plugged into the `Strategy` ABC. The **data acquisition layer**, **data feed**, **event queue with signal generation**, **analytics & metrics layer**, and **multi-class strategy library** (MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop) are built.

- **Remote:** https://github.com/anilpank/stardust
- **Python:** 3.13.5 (uses 3.12+ features like `list[str] | None`)
- **Platform:** macOS (darwin)
- **Working directory:** `/Users/anilverma/stardust`

## Current State

The data pipeline, event-driven engine, and analytics module are built. Data is stored as individual Parquet files in `thermaltrend/data/equities/` (501 S&P 500 tickers + SPY). A `DataFeed` class loads these files and yields bars in strict chronological order. The `EventQueue` processes MarketEvents through a Strategy to produce SignalEvents. The analytics module converts signals into simulated trades, computes performance metrics (CAGR, Sharpe, Sortino, MaxDD, win rate, confidence), and produces ranking tables with SPY buy-and-hold benchmark.

| Metric | Value |
|--------|-------|
| Parquet files | 502 (501 S&P 500 tickers + SPY) |
| Data range | 1970 → Jul 19 2026 (varies by ticker) |
| Columns | Open, High, Low, Close, Volume (auto-adjusted) |
| Strategies | 4 (MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop) |
| Total source code | ~2,320 lines across 14 modules |
| Total test code | ~3,100 lines across 18 test files (194 unit tests) |
| Git commits | 27 |

## Scripts

| Script | Purpose | Run |
|--------|---------|-----|
| `download_data.py` | Full download from Yahoo Finance (skips existing) | `cd thermaltrend && python download_data.py` |
| `update_data.py` | Incremental update (downloads only missing days) | `cd thermaltrend && python update_data.py` |
| `show_start_dates.py` | Inspect data availability per ticker | `cd thermaltrend && python show_start_dates.py` |
| `feed.py` | Load Parquet files as chronological bars (CLI + library) | `cd thermaltrend && python feed.py` |
| `signals.py` | Generate trading signals from strategy (CLI + library) | `cd thermaltrend && python -m thermaltrend.signals --strategy ma_crossover\|donchian\|rsi_mean_reversion\|atr_trailing_stop` |

All scripts accept `--tickers AAPL MSFT` for specific tickers and `--output PATH` for custom directories.

## Analytics Usage

```python
from thermaltrend.feed import DataFeed
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy
from thermaltrend.analytics.compare import run_strategy_analysis, compare_strategies
from thermaltrend.analytics.metrics import compute_benchmark_metrics
from thermaltrend.analytics.report import format_ranking_table, format_per_ticker_table, format_regime_table
import pandas as pd

# Run multiple strategies
feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT", "GOOGL"], start_date="2023-01-01")
strategies = {
    "MA 50/200": MACrossoverStrategy(50, 200),
    "Donchian 20/10": DonchianBreakoutStrategy(20, 10),
    "RSI 14": RSIMeanReversionStrategy(14, 30.0, 70.0),
    "ATR Trailing Stop": ATRTrailingStopStrategy(20, 14, 3.0),
}

results = {}
for name, strat in strategies.items():
    engine = DataEngine(feed, strat)
    signals = engine.run()
    result = run_strategy_analysis(signals, feed._data, name)
    results[name] = {"trades": result["trades"], "equity_curve": result["equity_curve"]}
    m = result["metrics"]
    print(f"{name}: {m['total_trades']} trades, {m['win_rate']:.0%} win rate, Sharpe {m['sharpe']:.2f}")

# Compare with SPY benchmark
spy = pd.read_parquet("thermaltrend/data/equities/SPY.parquet")
bench = compute_benchmark_metrics(spy, "2023-01-01", "2026-07-19")
ranking = compare_strategies(results, benchmark_metrics=bench)
print(format_ranking_table(ranking))

# Regime analysis (per strategy)
from thermaltrend.analytics.regime import classify_regime, compute_regime_metrics
regimes = classify_regime(spy["Close"])
for name, result in results.items():
    regime_m = compute_regime_metrics(result["trades"], regimes)
    print(format_regime_table(regime_m, name))
```

## Testing

```bash
# Fast unit tests (mocked, no network) — runs on every commit via pre-commit
pytest thermaltrend/tests/ -m "not slow" -v

# Integration tests (hit Yahoo Finance)
pytest thermaltrend/tests/ -v

# Run specific strategy
python -m thermaltrend.signals --strategy donchian --tickers AAPL MSFT --start 2024-01-01
python -m thermaltrend.signals --strategy rsi_mean_reversion --tickers AAPL MSFT --start 2024-01-01
python -m thermaltrend.signals --strategy atr_trailing_stop --tickers AAPL MSFT --start 2024-01-01
```

Test files:
- `tests/test_events.py` — EventQueue, MarketEvent, SignalEvent (17 tests)
- `tests/test_strategy.py` — MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy (37 tests)
- `tests/test_engine.py` — DataEngine integration (6 tests)
- `tests/test_signals.py` — signals.py CLI + formatting (6 tests)
- `tests/test_trade_simulator.py` — Trade simulation with ATR stops (11 tests)
- `tests/test_metrics.py` — Metric calculations, confidence, benchmark (15 tests)
- `tests/test_regime.py` — Regime detection and breakdown (7 tests)
- `tests/test_compare.py` — Strategy ranking and comparison (6 tests)
- `tests/test_report.py` — Terminal, JSON, CSV output (15 tests)

Pre-commit hook: `.pre-commit-config.yaml` runs `pytest -m "not slow" -q` on every `git commit`.

## Key Files to Know

| File | What it does |
|------|-------------|
| `thermaltrend/download_data.py` | Full data download with `yfinance` |
| `thermaltrend/update_data.py` | Incremental update with `gc.collect()` fix for file descriptor leak |
| `thermaltrend/show_start_dates.py` | Data availability inspector |
| `thermaltrend/feed.py` | `DataFeed` class + `Bar` dataclass — loads Parquet files, yields bars chronologically |
| `thermaltrend/signals.py` | Signal output CLI — runs strategy on data feed, outputs ranked trading signals |
| `thermaltrend/core/events.py` | Event types (`MarketEvent`, `SignalEvent`) and `EventQueue` (deque-based FIFO) |
| `thermaltrend/core/strategy.py` | Strategy ABC + `MACrossoverStrategy`, `DonchianBreakoutStrategy`, `RSIMeanReversionStrategy`, `ATRTrailingStopStrategy` |
| `thermaltrend/core/engine.py` | `DataEngine` — main event loop connecting DataFeed → Strategy → Signals |
| `thermaltrend/analytics/trade_simulator.py` | Converts SignalEvents into simulated Trades with ATR stops, $10K sizing |
| `thermaltrend/analytics/metrics.py` | CAGR, Sharpe, Sortino, MaxDD, Calmar, win rate, profit factor, confidence score |
| `thermaltrend/analytics/regime.py` | Market regime detection (BULL/BEAR/SIDEWAYS) + per-regime metrics |
| `thermaltrend/analytics/compare.py` | Multi-strategy ranking with SPY buy-and-hold baseline |
| `thermaltrend/analytics/report.py` | Terminal table + JSON + CSV export |
| `thermaltrend/DESIGN.md` | Design document with all design decisions |
| `thermaltrend/ARCHITECTURE.md` | Detailed architecture proposal for the full system (6 layers) |
| `thermaltrend/data/equities/constituents.csv` | S&P 500 member list with `date_added` for universe filtering |
| `pyproject.toml` | Minimal — only defines pytest `slow` marker |
| `.pre-commit-config.yaml` | Pre-commit hook for unit tests |

## Known Issues / Gotchas

1. **File descriptor leak:** `update_data.py` needed `gc.collect()` in a `finally` block to prevent "Too many open files" errors when processing all 501 tickers. This is already fixed (commit `79bc38e`).

2. **Constituents not auto-refreshed:** `update_data.py` does NOT re-fetch the S&P 500 member list from Wikipedia. It only updates existing parquet files. If new stocks are added to the S&P 500, you need to run `download_data.py` to get them (it fetches fresh constituents from Wikipedia each time).

3. **Ticker format:** yfinance uses hyphens (e.g., `BRK-B`) instead of dots (`BRK.B`). The `download_data.py` script handles this conversion. Keep this in mind if adding new tickers.

4. **Parquet schema:** Files have a residual Pandas MultiIndex level name `Price` in metadata (yfinance artifact). The OHLCV columns are `Open, High, Low, Close, Volume` with `Date` as the index.

5. **Analytics assumes next-day execution:** Trade simulator enters/exits at next day's open. If a signal fires on the last day of data, the trade is closed at that day's close with `exit_reason="data_end"`.

## Architecture Vision (from ARCHITECTURE.md)

The planned system has 6 layers:

1. **Data Layer** ← built (download, update, inspect scripts + `DataFeed` for event-driven consumption)
2. **Event Queue** ← built (MarketEvent, SignalEvent, EventQueue + DataEngine + MACrossoverStrategy + signals CLI)
3. **Strategy Engine** ← 4 of ~6 strategies built (MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy); dual momentum, factor scoring still planned
4. **Analytics & Reporting** ← built (trade simulation, metrics, regime analysis, strategy ranking, benchmark comparison)
5. **Execution Handler** (simulated + live broker bridge)
6. **Portfolio & Risk** (position sizing, risk management)

Implemented directory structure: `thermaltrend/` with `core/` (events, strategy, engine), `analytics/` (trade_simulator, metrics, regime, compare, report), `data/`, `tests/`. Future: `portfolio/`, `execution/`, `utils/` subpackages.

## Dependencies

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit
```

## If Starting a New Session

- Run `git log --oneline -5` to see recent commits
- Run `pytest thermaltrend/tests/ -m "not slow" -v` to confirm tests pass (194 unit tests)
- Run `python thermaltrend/update_data.py --tickers AAPL` to verify the data pipeline works
- Run `python thermaltrend/feed.py` to verify the data feed loads correctly
- Run `python -m thermaltrend.signals --tickers AAPL MSFT --start 2024-01-01` to verify signal generation works
- Try the analytics:

```python
from thermaltrend.feed import DataFeed
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import MACrossoverStrategy
from thermaltrend.analytics.compare import run_strategy_analysis

feed = DataFeed("thermaltrend/data/equities", tickers=["AAPL", "MSFT"], start_date="2024-01-01")
strategy = MACrossoverStrategy(fast_period=20, slow_period=50)
engine = DataEngine(feed, strategy)
signals = engine.run()
result = run_strategy_analysis(signals, feed._data, "MA 20/50")
print(result["metrics"])
```

- Check `thermaltrend/DESIGN.md` if planning the next phase of development (dual momentum, factor scoring strategies, signal persistence)
