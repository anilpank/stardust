# Handoff: Stardust / Thermaltrend

## What This Project Is

An early-stage Python project called **Thermaltrend** — planned to become an event-driven backtesting and live-trading system for trend-following strategies on S&P 500 equities. The **data acquisition layer** and **data feed** are built.

- **Remote:** https://github.com/anilpank/stardust
- **Python:** 3.13.5 (uses 3.12+ features like `list[str] | None`)
- **Platform:** macOS (darwin)
- **Working directory:** `/Users/anilverma/stardust`

## Current State

The data pipeline is complete: download, update, and inspect daily OHLCV data for all S&P 500 stocks. Data is stored as individual Parquet files in `thermaltrend/data/equities/`. A `DataFeed` class loads these files and yields bars in strict chronological order for event-driven backtesting.

| Metric | Value |
|--------|-------|
| Parquet files | 501 tickers |
| Data range | 1970 → Jul 19 2026 (varies by ticker) |
| Columns | Open, High, Low, Close, Volume (auto-adjusted) |
| Total source code | ~470 lines across 4 modules |
| Total test code | ~1100 lines across 9 test files |
| Git commits | 20 |

## Scripts

| Script | Purpose | Run |
|--------|---------|-----|
| `download_data.py` | Full download from Yahoo Finance (skips existing) | `cd thermaltrend && python download_data.py` |
| `update_data.py` | Incremental update (downloads only missing days) | `cd thermaltrend && python update_data.py` |
| `show_start_dates.py` | Inspect data availability per ticker | `cd thermaltrend && python show_start_dates.py` |
| `feed.py` | Load Parquet files as chronological bars (CLI + library) | `cd thermaltrend && python feed.py` |

All scripts accept `--tickers AAPL MSFT` for specific tickers and `--output PATH` for custom directories.

## Testing

```bash
# Fast unit tests (mocked, no network) — runs on every commit via pre-commit
pytest thermaltrend/tests/ -m "not slow" -v

# Integration tests (hit Yahoo Finance)
pytest thermaltrend/tests/ -v
```

Pre-commit hook: `.pre-commit-config.yaml` runs `pytest -m "not slow" -q` on every `git commit`.

## Key Files to Know

| File | What it does |
|------|-------------|
| `thermaltrend/download_data.py` | Full data download with `yfinance` |
| `thermaltrend/update_data.py` | Incremental update with `gc.collect()` fix for file descriptor leak |
| `thermaltrend/show_start_dates.py` | Data availability inspector |
| `thermaltrend/feed.py` | `DataFeed` class + `Bar` dataclass — loads Parquet files, yields bars chronologically for event-driven backtesting |
| `thermaltrend/ARCHITECTURE.md` | Detailed design doc for the full system (6-layer event-driven architecture) |
| `thermaltrend/data/equities/constituents.csv` | S&P 500 member list with `date_added` for universe filtering |
| `pyproject.toml` | Minimal — only defines pytest `slow` marker |
| `.pre-commit-config.yaml` | Pre-commit hook for unit tests |

## Known Issues / Gotchas

1. **File descriptor leak:** `update_data.py` needed `gc.collect()` in a `finally` block to prevent "Too many open files" errors when processing all 501 tickers. This is already fixed (commit `79bc38e`).

2. **Constituents not auto-refreshed:** `update_data.py` does NOT re-fetch the S&P 500 member list from Wikipedia. It only updates existing parquet files. If new stocks are added to the S&P 500, you need to run `download_data.py` to get them (it fetches fresh constituents from Wikipedia each time).

3. **Ticker format:** yfinance uses hyphens (e.g., `BRK-B`) instead of dots (`BRK.B`). The `download_data.py` script handles this conversion. Keep this in mind if adding new tickers.

4. **Parquet schema:** Files have a residual Pandas MultiIndex level name `Price` in metadata (yfinance artifact). The OHLCV columns are `Open, High, Low, Close, Volume` with `Date` as the index.

## Architecture Vision (from ARCHITECTURE.md)

The planned system has 6 layers:

1. **Data Layer** ← built (download, update, inspect scripts + `DataFeed` for event-driven consumption)
2. **Event Queue** (MarketEvent, SignalEvent, OrderEvent, FillEvent)
3. **Strategy Engine** (trend-following: MA crossover, Donchian breakout, ATR trailing stop, etc.)
4. **Execution Handler** (simulated + live broker bridge)
5. **Portfolio & Risk** (position sizing, risk management)
6. **Analytics & Reporting**

Proposed directory structure: `src/thermaltrend/` with `core/`, `data/`, `strategy/`, `portfolio/`, `execution/`, `analytics/`, `utils/` — **not yet implemented**, code currently lives flat under `thermaltrend/`.

## Dependencies

```
pip install pandas numpy yfinance requests pyarrow pytest pre-commit
```

## If Starting a New Session

- Run `git log --oneline -5` to see recent commits
- Run `pytest thermaltrend/tests/ -m "not slow" -v` to confirm tests pass (61 unit tests)
- Run `python thermaltrend/update_data.py --tickers AAPL` to verify the data pipeline works
- Run `python thermaltrend/feed.py` to verify the data feed loads correctly
- Check `thermaltrend/ARCHITECTURE.md` if planning the next phase of development
