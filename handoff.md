# Handoff: Stardust / Thermaltrend

## What This Project Is

An early-stage Python project called **Thermaltrend** — an event-driven backtesting and live-trading system for **testing, validating, and selecting the best performing strategies** on S&P 500 equities. Not limited to trend-following — encompasses momentum, mean reversion, factor-based, and any strategy class that can be plugged into the `Strategy` ABC. The **data acquisition layer**, **data feed**, **event queue with signal generation**, **analytics & metrics layer**, **multi-class strategy library** (MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop, Dual Momentum), and a **Streamlit dashboard** are built.

- **Remote:** https://github.com/anilpank/stardust
- **Python:** 3.13.5 (uses 3.12+ features like `list[str] | None`)
- **Platform:** macOS (darwin)
- **Working directory:** `/Users/anilverma/stardust`

## Current State

The data pipeline, event-driven engine, and analytics module are built. Data is stored as individual Parquet files in `thermaltrend/data/equities/` (S&P 500 tickers + SPY) and `thermaltrend/data/equities_removed/` (survivorship-bias backfill for removed members). A `DataFeed` class loads these files and yields bars in strict chronological order. The `EventQueue` processes MarketEvents through a Strategy to produce SignalEvents. The analytics module converts signals into simulated trades, computes performance metrics (CAGR, Sharpe, Sortino, MaxDD, win rate, confidence), and produces ranking tables with SPY buy-and-hold benchmark. Backtests can run on a **point-in-time universe** (`--universe point_in_time`) that restricts each ticker to its actual S&P 500 membership window.

| Metric | Value |
|--------|-------|
| Parquet files | 760 (502 in `data/equities/` = 501 S&P members + SPY; 258 in `data/equities_removed/`) |
| Membership stints | 1,259 rows / 1,206 tickers in `data/equities/membership.csv` (503 current, 756 removed) |
| Data range | 1970 → Aug 28 2026 (varies by ticker) |
| Columns | Open, High, Low, Close, Volume (auto-adjusted) |
| Strategies | 5 (MA Crossover, Donchian Breakout, RSI Mean Reversion, ATR Trailing Stop, Dual Momentum) |
| Total source code | ~4,800 lines across 22 modules |
| Total test code | ~6,100 lines across 26 test files (426 tests: 394 fast unit + 32 integration) |
| Git commits | 66 |

## Scripts

| Script | Purpose | Run |
|--------|---------|-----|
| `download_data.py` | Full download from Yahoo Finance (skips existing) | `cd thermaltrend && python download_data.py` |
| `update_data.py` | Incremental update (downloads only missing days) | `cd thermaltrend && python update_data.py` |
| `build_membership.py` | Build `membership.csv` (point-in-time membership stints) | `cd thermaltrend && python build_membership.py --refresh` |
| `download_removed.py` | Backfill Yahoo data for removed S&P 500 members (parallel, resumable; `--output` for custom dir) | `cd thermaltrend && python download_removed.py` |
| `removed_coverage.py` | Per-stint data coverage report for removed members | `cd thermaltrend && python removed_coverage.py` |
| `survivorship_bias.py` | Quantify survivorship bias vs real index; `--include-removed` for PIT estimate | `cd thermaltrend && python survivorship_bias.py [--include-removed]` |
| `show_start_dates.py` | Inspect data availability per ticker | `cd thermaltrend && python show_start_dates.py` |
| `feed.py` | Load Parquet files as chronological bars (CLI + library) | `cd thermaltrend && python feed.py` |
| `signals.py` | Generate trading signals from strategy (with --save) | `cd thermaltrend && python -m thermaltrend.signals --strategy ma_crossover\|donchian\|rsi_mean_reversion\|atr_trailing_stop\|dual_momentum` |
| `backtest.py` | Backtest a single strategy with full metrics (`--universe current\|point_in_time`) | `cd thermaltrend && python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01` |
| `compare_cli.py` | Compare multiple strategies side-by-side (`--universe current\|point_in_time`) | `cd thermaltrend && python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01` |
| `signal_store.py` | Persist, query, and annotate signals | `cd thermaltrend && python thermaltrend/signal_store.py list` |
| `dashboard.py` | Streamlit visual dashboard (run from repo root) | `streamlit run thermaltrend/dashboard.py` |

Note: `dual_momentum` requires the benchmark ticker (SPY) in your ticker list on the CLI — it learns
the benchmark series from the event stream and produces no signals without it. The **dashboard**
injects SPY automatically (`resolve_feed_tickers()` in `dashboard.py`) for every strategy path, so
no manual selection is needed there.

All scripts accept `--tickers AAPL MSFT` for specific tickers and `--output PATH` for custom directories.

### Per-Period Breakdown

The `--period` flag shows performance broken down by time period (monthly, quarterly, yearly):

```bash
# Single strategy with monthly breakdown
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01 --period monthly

# Compare strategies with quarterly period comparison
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 --period quarterly

# Yearly breakdown
python thermaltrend/backtest.py --strategy donchian --tickers AAPL MSFT --per-ticker --period yearly
```

Library usage:

```python
from thermaltrend.analytics.metrics import compute_period_metrics
from thermaltrend.analytics.report import format_period_table, format_cross_strategy_period_table

# Per-period metrics for a single strategy
period_m = compute_period_metrics(trades, period="monthly")
print(format_period_table(period_m, "MA Crossover", "monthly"))

# Cross-strategy period comparison
print(format_cross_strategy_period_table(strategy_results, period="quarterly"))
```

## Point-in-Time Universe & Survivorship Bias

Backtesting only today's members overstates historical returns (delisted losers are missing). `survivorship_bias.py` measured this at **~2.6–4.3%/yr** on an equal-weight S&P portfolio vs the real equal-weight index (RSP). Steps taken to fix it:

**Step 2 — `membership.csv` (`build_membership.py`):** a per-stint membership table, 1,259 rows across 1,206 tickers, spanning 1957 → present. Columns: `ticker, date_added, date_removed, is_current`. Source files (Wikipedia snapshots + historic add/remove histories) live in the git-ignored `data/membership/`; rebuild with `build_membership.py --refresh` (network required). Only the merged `membership.csv` is committed.

**Step 3 — removed-member data (`download_removed.py`):** backfilled price history for the 703 tickers in membership but not in today's S&P 500, saved to `data/equities_removed/` (git-ignored `data/benchmarks/` holds the RSP/SPY reference series used by `survivorship_bias.py`). Downloader runs a `ThreadPoolExecutor(max_workers=8)` in parallel, resumes incrementally, and accepts `--workers`. Results: **259 recovered** (258 kept — the smoke-test download ESRT had no membership row and was deleted), 445 genuinely delisted with no data (e.g. ENRNQ, LEHMQ, WCOEQ, MER, JCP, KM). `removed_coverage.py` reports per-stint coverage: **155 full / 145 partial / 456 none** over 756 removed stints — only 39.7% of removed stints have any data.

**Step 4 — PIT engine integration:** `--universe current|point_in_time` on `backtest.py` and `compare_cli.py`, plus a **Universe** selector in the `dashboard.py` sidebar (`st.session_state["universe"]`). With `point_in_time`:
- `DataFeed(membership=..., removed_data_dir=...)` masks each ticker to its membership window, merging its removed-period data from `equities_removed/`; non-members (SPY) pass through unmasked so the Dual Momentum benchmark is unaffected.
- `TradeSimulator` gains two exit reasons: `universe_exit` (position force-closed at the last member-day close) and `delisted` (last close × a Shumway-style delisting return, **default −0.30**, applied when the ticker's data ends more than `max_delisting_gap_days` = 10 days before its membership window).
- With removed data included, the residual bias collapses: 2005 **+1.20%/yr**, 2010 **+0.87%/yr**, 2015 **+0.47%/yr**, 2020 **−0.44%/yr**. Surviving members still beat the equal-weight index roughly by the survivorship premium; the 2020 residual reflects cap-weight differences vs RSP.

Caveat: `update_data.py` only refreshes `data/equities/` — removed-member files are static until you re-run `download_removed.py`.

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

### CLI Quick Reference

```bash
# Backtest a single strategy
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01
python thermaltrend/backtest.py --strategy donchian --tickers AAPL MSFT --per-ticker --regime
python thermaltrend/backtest.py --strategy rsi_mean_reversion --ticker AAPL --params '{"period": 20}' --output result.json

# Compare strategies
python thermaltrend/compare_cli.py --tickers AAPL MSFT GOOGL --start 2023-01-01
python thermaltrend/compare_cli.py --strategies ma_crossover donchian --ticker AAPL --sort-by sharpe

# Point-in-time universe (survivorship-bias free)
python thermaltrend/backtest.py --strategy ma_crossover --universe point_in_time --start 2010-01-01
python thermaltrend/compare_cli.py --universe point_in_time --start 2010-01-01
python thermaltrend/survivorship_bias.py --include-removed

# Signal persistence
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL --save
python thermaltrend/signal_store.py list
python thermaltrend/signal_store.py show <run_id>
python thermaltrend/signal_store.py annotate <signal_id> --action acted --notes "Bought at $195"
python thermaltrend/signal_store.py pending
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
python -m thermaltrend.signals --strategy dual_momentum --tickers AAPL MSFT SPY --start 2024-01-01
```

Test files (26 files, 426 tests: 394 fast unit + 32 integration):
- `tests/test_events.py` — EventQueue, MarketEvent, SignalEvent
- `tests/test_strategy.py` — all 5 strategies incl. DualMomentumStrategy
- `tests/test_engine.py` — DataEngine integration
- `tests/test_feed.py` / `test_feed_integration.py` — DataFeed loading + real-data checks
- `tests/test_pit_universe.py` — membership masking, `universe_exit` / Shumway `delisted` force-closes, PIT CLI runs
- `tests/test_membership_tools.py` — build_membership / download_removed / removed_coverage / survivorship_bias logic
- `tests/test_download_removed_integration.py` — removed-member downloader (slow, Yahoo network, `--output` fixtures)
- `tests/test_signals.py` — signals.py CLI + formatting
- `tests/test_trade_simulator.py` — Trade simulation with ATR stops
- `tests/test_metrics.py` — Metric calculations, confidence, benchmark, per-period
- `tests/test_regime.py` — Regime detection and breakdown
- `tests/test_compare.py` — Strategy ranking and comparison
- `tests/test_report.py` — Terminal, JSON, CSV, period table output
- `tests/test_backtest.py` — Backtest CLI + library (incl. all-strategies smoke test)
- `tests/test_compare_cli.py` — Compare CLI + library
- `tests/test_signal_store.py` — Signal persistence and annotation
- `tests/test_dashboard.py` / `test_charts.py` — Dashboard registries/constants + `resolve_feed_tickers()` benchmark injection + chart builders
- `tests/test_download_data.py`, `test_update_data.py`, `test_show_start_dates.py` (+ `*_integration.py` variants) — data pipeline (integration tests pass fixture dirs via `--data-dir`, not cwd)
- `tests/test_hello.py` — import smoke tests

Pre-commit hook: `.pre-commit-config.yaml` runs `pytest -m "not slow" -q` on every `git commit`.

## Key Files to Know

| File | What it does |
|------|-------------|
| `thermaltrend/download_data.py` | Full data download with `yfinance` |
| `thermaltrend/update_data.py` | Incremental update with `gc.collect()` fix for file descriptor leak |
| `thermaltrend/show_start_dates.py` | Data availability inspector (`--data-dir` flag for custom locations) |
| `thermaltrend/feed.py` | `DataFeed` class + `Bar` dataclass — loads Parquet files, yields bars chronologically |
| `thermaltrend/backtest.py` | `run_backtest()` library function + CLI — single-strategy backtest with metrics |
| `thermaltrend/compare_cli.py` | `run_compare()` library function + CLI — multi-strategy ranking with benchmark |
| `thermaltrend/signal_store.py` | `SignalStore` class + CLI — persist, query, and annotate signals |
| `thermaltrend/signals.py` | Signal output CLI — runs strategy on data feed, outputs ranked trading signals (--save to persist) |
| `thermaltrend/dashboard.py` | Streamlit dashboard — backtests, signals, compare, Data Explorer, Compare Tickers |
| `thermaltrend/charts.py` | Plotly chart builders used by the dashboard |
| `thermaltrend/core/events.py` | Event types (`MarketEvent`, `SignalEvent`) and `EventQueue` (deque-based FIFO) |
| `thermaltrend/core/strategy.py` | Strategy ABC + `MACrossoverStrategy`, `DonchianBreakoutStrategy`, `RSIMeanReversionStrategy`, `ATRTrailingStopStrategy`, `DualMomentumStrategy` |
| `thermaltrend/core/engine.py` | `DataEngine` — main event loop connecting DataFeed → Strategy → Signals |
| `thermaltrend/analytics/trade_simulator.py` | Converts SignalEvents into simulated Trades with ATR stops, $10K sizing |
| `thermaltrend/analytics/metrics.py` | CAGR, Sharpe, Sortino, MaxDD, Calmar, win rate, confidence, per-period breakdown |
| `thermaltrend/analytics/regime.py` | Market regime detection (BULL/BEAR/SIDEWAYS) + per-regime metrics |
| `thermaltrend/analytics/compare.py` | Multi-strategy ranking with SPY buy-and-hold baseline |
| `thermaltrend/analytics/report.py` | Terminal table + JSON + CSV + period table export |
| `thermaltrend/DESIGN.md` | Design document with all design decisions |
| `thermaltrend/ARCHITECTURE.md` | Detailed architecture proposal for the full system (6 layers) |
| `thermaltrend/data/equities/constituents.csv` | S&P 500 member list with `date_added` for universe filtering |
| `thermaltrend/data/equities/membership.csv` | Point-in-time membership stints (source of truth for `--universe point_in_time`) |
| `thermaltrend/data/equities_removed/` | Parquet files for removed S&P 500 members (survivorship-bias backfill) |
| `thermaltrend/build_membership.py` | `membership.csv` generator (raw sources in git-ignored `data/membership/`) |
| `thermaltrend/download_removed.py` | Parallel, resumable removed-member data backfill (`data/equities_removed/`) |
| `thermaltrend/removed_coverage.py` | Per-stint data coverage report for removed members |
| `thermaltrend/survivorship_bias.py` | Survivorship-bias quantification (EqualWeight vs RSP/SPY; `--include-removed` for PIT) |
| `pyproject.toml` | Minimal — only defines pytest `slow` marker |
| `.pre-commit-config.yaml` | Pre-commit hook for unit tests |

## Known Issues / Gotchas

1. **File descriptor leak:** `update_data.py` needed `gc.collect()` in a `finally` block to prevent "Too many open files" errors when processing all 501 tickers. This is already fixed (commit `79bc38e`).

2. **Constituents not auto-refreshed:** `update_data.py` does NOT re-fetch the S&P 500 member list from Wikipedia. It only updates existing parquet files. If new stocks are added to the S&P 500, you need to run `download_data.py` to get them (it fetches fresh constituents from Wikipedia each time).

3. **Ticker format:** yfinance uses hyphens (e.g., `BRK-B`) instead of dots (`BRK.B`). The `download_data.py` script handles this conversion. Keep this in mind if adding new tickers.

4. **Parquet schema:** Files have a residual Pandas MultiIndex level name `Price` in metadata (yfinance artifact). The OHLCV columns are `Open, High, Low, Close, Volume` with `Date` as the index.

5. **Analytics assumes next-day execution:** Trade simulator enters/exits at next day's open. If a signal fires on the last day of data, the trade is closed at that day's close with `exit_reason="data_end"`.

6. **Bars are processed alphabetically per date:** The engine yields same-day bars in ticker-alphabetical order. A strategy comparing two tickers (e.g., Dual Momentum vs SPY) only sees the benchmark's *previous* close when evaluating a ticker earlier in the alphabet — a one-day lookback lag, not lookahead bias. Dual Momentum tests rely on this behavior; don't "fix" it casually.

7. **Dual Momentum silently no-ops without benchmark bars:** If SPY (or the configured `benchmark_ticker`) isn't in the feed, the strategy emits zero signals instead of erroring. The dashboard guards against this via `resolve_feed_tickers()`; CLI users must include SPY themselves.

8. **`update_data.py` ignores removed-member data:** It only syncs `data/equities/`, so removed-member files in `data/equities_removed/` go stale. If you need fresher removed-member prices, re-run `download_removed.py`.

9. **Membership sources are not committed:** The raw Wikipedia snapshots / add-remove histories in `data/membership/` and the benchmark series in `data/benchmarks/` are git-ignored. Only the merged `membership.csv` and parquet files are committed; rebuild membership with `build_membership.py --refresh` (network needed).

10. **Two current constituents have no data:** `constituents.csv` lists 503 members but 2 (FDXF, HONA — brand-new additions with no trading history yet) have no downloadable price data, so `data/equities/` holds 501 member parquets + SPY.

## Architecture Vision (from ARCHITECTURE.md)

The planned system has 6 layers:

1. **Data Layer** ← built (download, update, inspect scripts + `DataFeed` for event-driven consumption)
2. **Event Queue** ← built (MarketEvent, SignalEvent, EventQueue + DataEngine + MACrossoverStrategy + signals CLI)
3. **Strategy Engine** ← 5 of ~6 strategies built (MACrossoverStrategy, DonchianBreakoutStrategy, RSIMeanReversionStrategy, ATRTrailingStopStrategy, DualMomentumStrategy); factor scoring still planned
4. **Analytics & Reporting** ← built (trade simulation, metrics, regime analysis, strategy ranking, benchmark comparison)
5. **Signal Persistence** ← built (signal_store.py, backtest.py, compare_cli.py)
6. **Portfolio & Risk** (position sizing, risk management)
7. **Execution Handler** (simulated + live broker bridge)

Implemented directory structure: `thermaltrend/` with `core/` (events, strategy, engine), `analytics/` (trade_simulator, metrics, regime, compare, report), `data/`, `tests/`. Plus UI: `dashboard.py`, `charts.py`. Future: `portfolio/`, `execution/`, `utils/` subpackages.

## Dependencies

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit
```

## If Starting a New Session

- Run `git log --oneline -5` to see recent commits
- Run `pytest thermaltrend/tests/ -m "not slow" -v` to confirm tests pass (394 fast unit tests, 32 integration deselected)
- If resuming after a break, run `python thermaltrend/update_data.py` to refresh all 502 equity parquet files (last full update: Aug 28 2026)
- Run `python thermaltrend/update_data.py --tickers AAPL` to verify the data pipeline works
- Run `streamlit run thermaltrend/dashboard.py` and try the Compare tab (Dual Momentum should appear with SPY auto-added); switch the sidebar **Universe** selector to Point-in-time
- Run `python thermaltrend/feed.py` to verify the data feed loads correctly
- Run `python -m thermaltrend.signals --tickers AAPL MSFT --start 2024-01-01` to verify signal generation works
- Run `python thermaltrend/backtest.py --strategy ma_crossover --ticker AAL --universe point_in_time --start 2015-01-01` — AAL should trade only during its 2015-03-23 → 2024-09-23 membership stint
- Run `python thermaltrend/survivorship_bias.py --include-removed` to see the PIT-correction numbers
- Try the backtest:

```bash
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2024-01-01
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2024-01-01
```

- Try signal persistence:

```bash
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL --save
python thermaltrend/signal_store.py list
```

- Try the analytics library:

```python
from thermaltrend.backtest import run_backtest
result = run_backtest("ma_crossover", ["AAPL", "MSFT"], start_date="2024-01-01")
print(result["metrics"])
```

- Check `thermaltrend/DESIGN.md` if planning the next phase of development (factor scoring strategy, portfolio layer)
