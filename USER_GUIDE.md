# Thermaltrend User Guide

**For traders with minimal programming experience.**

This guide walks you through setting up and using Thermaltrend — a system that backtests trading strategies on S&P 500 stocks and generates daily buy/sell signals.

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [One-Time Setup](#2-one-time-setup)
3. [Updating Your Data](#3-updating-your-data)
4. [Running Your First Backtest](#4-running-your-first-backtest)
5. [Comparing Strategies](#5-comparing-strategies)
6. [Getting Daily Signals](#6-getting-daily-signals)
7. [Understanding the Output](#7-understanding-the-output)
8. [Common Workflows](#8-common-workflows)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What This System Does

Thermaltrend helps you answer three questions:

1. **Which strategies have worked historically?** — Backtest 4 different strategies on any S&P 500 stock and see their performance metrics.
2. **Which strategy works best right now?** — Compare strategies side-by-side and see which one has the best risk-adjusted returns.
3. **What should I trade today?** — Generate ranked buy/sell signals based on validated strategies.

The 4 built-in strategies are:

| Strategy | What It Does | Best In |
|----------|-------------|---------|
| **MA Crossover** (50/200) | Buys when short-term average crosses above long-term average | Trending markets |
| **Donchian Breakout** (20/10) | Buys when price breaks above 20-day high | Breakout/trending markets |
| **RSI Mean Reversion** (14) | Buys when stock is oversold and starts recovering | Sideways/choppy markets |
| **ATR Trailing Stop** (20/14/3) | Buys on breakout, exits via volatility-adaptive trailing stop | Trending markets with volatility |

You don't need to understand how these work — the system runs them automatically and tells you which performs best.

---

## 2. One-Time Setup

### Prerequisites

- **Python 3.12 or newer** installed on your computer
- **Internet connection** (for downloading stock data)

### Step 1: Open a Terminal

- **Mac:** Press `Cmd + Space`, type "Terminal", press Enter
- **Windows:** Press `Win + R`, type "cmd", press Enter

### Step 2: Navigate to the Project

```bash
cd /path/to/stardust
```

(If you cloned from GitHub, this is wherever you put the `stardust` folder.)

### Step 3: Install Dependencies

```bash
pip install pandas numpy yfinance requests pyarrow pytest
```

You should see "Successfully installed..." messages. If you see errors, make sure Python 3.12+ is installed (`python --version`).

### Step 4: Download Stock Data

This downloads historical data for all 500+ S&P 500 stocks. It takes 15-30 minutes on first run (it only needs to be done once):

```bash
cd thermaltrend
python download_data.py
```

You'll see progress like:
```
Downloading AAPL... saved (56 years)
Downloading MSFT... saved (56 years)
Downloading GOOGL... saved (20 years)
...
```

After this completes, you have all the data you need. Future updates take only 1-2 minutes (see Section 3).

### Step 5: Verify It Works

```bash
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2024-01-01
```

You should see performance metrics. If this works, you're ready to go.

---

## 3. Updating Your Data

Stock data changes every day. To keep your data current, run this **once per day** (ideally before market open or after close):

```bash
cd thermaltrend
python update_data.py
```

This only downloads the missing days (takes 1-2 minutes). It will not re-download everything.

---

## 4. Running Your First Backtest

A backtest asks: "If I had used this strategy on this stock, how much money would I have made or lost?"

### Basic Backtest

```bash
cd thermaltrend
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2024-01-01
```

This shows you:
- Total trades made
- Win rate (% of trades that were profitable)
- Return on $10,000 invested per trade
- Risk metrics (how volatile the returns were)

### Test Multiple Stocks

```bash
python thermaltrend/backtest.py --strategy ma_crossover --tickers AAPL MSFT GOOGL --start 2024-01-01
```

### See Per-Stock Performance

```bash
python thermaltrend/backtest.py --strategy ma_crossover --tickers AAPL MSFT GOOGL --start 2024-01-01 --per-ticker
```

This shows which stocks the strategy works best on and which it struggles with.

### See Performance by Time Period

```bash
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01 --period monthly
```

This breaks down results by month — so you can see if the strategy worked consistently or only in certain periods.

Options: `--period monthly`, `--period quarterly`, `--period yearly`

### See Performance by Market Condition

```bash
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2023-01-01 --regime
```

This shows how the strategy performed during bull markets, bear markets, and sideways (flat) markets.

### All Together

```bash
python thermaltrend/backtest.py --strategy ma_crossover --tickers AAPL MSFT --start 2023-01-01 --per-ticker --regime --period quarterly
```

### Available Strategies

| Command | Strategy |
|---------|----------|
| `--strategy ma_crossover` | Moving Average Crossover (50/200) |
| `--strategy donchian` | Donchian Channel Breakout (20/10) |
| `--strategy rsi_mean_reversion` | RSI Mean Reversion (14-period) |
| `--strategy atr_trailing_stop` | ATR Trailing Stop (20/14/3) |

---

## 5. Comparing Strategies

The most powerful feature: run all strategies on the same stocks and see which performs best.

### Compare All Strategies

```bash
python thermaltrend/compare_cli.py --tickers AAPL MSFT GOOGL --start 2023-01-01
```

This produces a ranking table showing each strategy's:
- CAGR (annual return)
- Sharpe ratio (return per unit of risk — higher is better)
- Max Drawdown (worst peak-to-trough loss)
- Win Rate (% of winning trades)
- Confidence score (how reliable the results are)

The S&P 500 buy-and-hold is included as a benchmark — strategies must beat this to be worth using.

### Compare Specific Strategies

```bash
python thermaltrend/compare_cli.py --strategies ma_crossover donchian --ticker AAPL --start 2023-01-01
```

### See Which Strategy Won Each Period

```bash
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 --period quarterly
```

This shows a side-by-side quarterly comparison — the `*` marks the winner in each period.

### Sort by Different Metrics

```bash
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 --sort-by sharpe
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 --sort-by win_rate
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 --sort-by cagr
```

---

## 6. Getting Daily Signals

After validating a strategy through backtesting, you can use it to generate daily trading signals.

### Generate Signals

```bash
cd thermaltrend
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT GOOGL
```

This shows today's signals ranked by conviction:

```
Date         Ticker   Direction  Strength
--------------------------------------------------
2026-07-25   AAPL     BUY        0.8234
2026-07-25   MSFT     SELL       0.6512
```

- **BUY** = The strategy says buy this stock
- **SELL** = The strategy says sell this stock
- **Strength** (0 to 1) = How confident the signal is. Higher = stronger signal.

### Filter by Minimum Strength

Only show strong signals:

```bash
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT --min-strength 0.5
```

### Only Show BUY Signals

```bash
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT --direction BUY
```

### Save Signals for Later Review

```bash
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT --save
```

This saves signals so you can review them later:

```bash
python thermaltrend/signal_store.py list          # See all saved signal runs
python thermaltrend/signal_store.py show <run_id> # See signals from a specific run
python thermaltrend/signal_store.py pending        # See signals not yet acted on
```

### Mark a Signal as Acted On

If you followed a signal and bought a stock:

```bash
python thermaltrend/signal_store.py annotate <signal_id> --action acted --notes "Bought 50 shares at $195"
```

---

## 7. Understanding the Output

### Key Metrics Explained

| Metric | What It Means | Good | Okay | Bad |
|--------|--------------|------|------|-----|
| **CAGR** | Annual return on your money | > 15% | 8-15% | < 8% |
| **Sharpe** | Return per unit of risk (higher = better risk-adjusted returns) | > 1.0 | 0.5-1.0 | < 0.5 |
| **Max Drawdown** | Worst peak-to-trough loss you'd experience | > -15% | -15% to -25% | < -25% |
| **Win Rate** | % of trades that made money | > 55% | 45-55% | < 45% |
| **Profit Factor** | Gross profit / gross loss (higher = more profit per dollar of loss) | > 1.5 | 1.0-1.5 | < 1.0 |
| **Confidence** | How reliable the backtest results are (0 to 1) | > 0.7 | 0.4-0.7 | < 0.4 |

**Important:** A strategy with a high Sharpe but low Confidence is not trustworthy — it might have just gotten lucky on a few trades.

### Signal Strength

| Strength | Interpretation |
|----------|---------------|
| 0.7 - 1.0 | Strong signal — high conviction |
| 0.4 - 0.7 | Moderate signal — worth watching |
| 0.0 - 0.4 | Weak signal — probably skip |

### Regime Labels

| Regime | What It Means |
|--------|--------------|
| **BULL** | S&P 500 is up >10% over the past year — market is trending up |
| **BEAR** | S&P 500 is down >10% over the past year — market is trending down |
| **SIDEWAYS** | S&P 500 is between -10% and +10% — market is flat/choppy |

---

## 8. Common Workflows

### Morning Routine (2 minutes)

1. **Update data:**
   ```bash
   cd thermaltrend && python update_data.py
   ```

2. **Get today's signals:**
   ```bash
   python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT GOOGL --min-strength 0.5 --direction BUY
   ```

3. **Act on strong BUY signals** via your broker (SAXO, Revolut, etc.)

### Weekly Review (10 minutes)

1. **Check which strategy is currently winning:**
   ```bash
   python thermaltrend/compare_cli.py --tickers AAPL MSFT GOOGL --start 2025-01-01
   ```

2. **Review your saved signals:**
   ```bash
   python thermaltrend/signal_store.py pending
   ```

### Monthly Deep Dive (30 minutes)

1. **Backtest each strategy on your portfolio:**
   ```bash
   python thermaltrend/backtest.py --strategy ma_crossover --tickers AAPL MSFT --start 2024-01-01 --per-ticker --regime --period monthly
   ```

2. **Compare strategies head-to-head:**
   ```bash
   python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2024-01-01 --period quarterly
   ```

3. **Decide if any strategy should be retired** (low confidence, poor recent performance)

---

## 9. Troubleshooting

### "No data found" error

Run `python update_data.py` to make sure your data is current.

### "Unknown strategy" error

Check the strategy name. Available: `ma_crossover`, `donchian`, `rsi_mean_reversion`, `atr_trailing_stop`

### Signals seem wrong

- Make sure you have enough data. Strategies need 200+ days of history before producing reliable signals.
- Check the date range: `--start 2024-01-01` means "use data from 2024 onward." Use `--start 2020-01-01` for a longer backtest.

### Table is too wide / hard to read

Try redirecting output to a file:

```bash
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01 > results.txt
```

Then open `results.txt` in any text editor.

### How do I know which strategy to use?

1. Run `compare_cli.py` on your portfolio stocks
2. Look at the **Confidence** column — only trust strategies with Confidence > 0.7
3. Look at the **Sharpe** ratio — higher is better
4. Look at the **Win Rate** — above 55% is good
5. If a strategy has low Confidence, it means there aren't enough trades to trust the results — try a longer date range or more tickers

---

## Quick Reference Card

```bash
# === DAILY ===
python update_data.py                                              # Update stock data
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL MSFT --min-strength 0.5  # Today's signals

# === BACKTEST ===
python thermaltrend/backtest.py --strategy ma_crossover --ticker AAPL --start 2024-01-01       # Basic backtest
python thermaltrend/backtest.py --strategy donchian --tickers AAPL MSFT --per-ticker           # Per-stock breakdown
python thermaltrend/backtest.py --strategy rsi_mean_reversion --ticker AAPL --regime           # By market condition
python thermaltrend/backtest.py --strategy atr_trailing_stop --ticker AAPL --period monthly    # By time period

# === COMPARE ===
python thermaltrend/compare_cli.py --tickers AAPL MSFT --start 2023-01-01                     # Which strategy wins?
python thermaltrend/compare_cli.py --tickers AAPL MSFT --period quarterly                      # By quarter

# === SIGNALS ===
python thermaltrend/signals.py --strategy ma_crossover --tickers AAPL --save                   # Save signals
python thermaltrend/signal_store.py list                                                        # List saved runs
python thermaltrend/signal_store.py pending                                                     # Pending signals
```
