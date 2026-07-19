"""Performance metrics for strategy evaluation.

Three levels of metrics:
- Aggregate: strategy-level summary (CAGR, Sharpe, Sortino, etc.)
- Per-ticker: which tickers the strategy performs best/worst on
- Confidence: composite score (0.0-1.0) of how trustworthy results are
"""

import math

import numpy as np
import pandas as pd

from thermaltrend.analytics.trade_simulator import Trade

RISK_FREE_RATE = 0.04  # annualized (approx current T-bill rate)
TRADING_DAYS_PER_YEAR = 252


def compute_equity_curve(
    trades: list[Trade], start_date: pd.Timestamp, initial_capital: float = 100_000.0
) -> pd.Series:
    """Build a daily equity curve from completed trades.

    Args:
        trades: List of Trade objects.
        start_date: Start date for the equity curve.
        initial_capital: Starting portfolio value.

    Returns:
        Series indexed by date with portfolio value.
    """
    if not trades:
        return pd.Series([initial_capital], index=[start_date])

    dates = pd.date_range(start=start_date, end=trades[-1].exit_date, freq="B")
    equity = pd.Series(initial_capital, index=dates, dtype=float)

    for trade in trades:
        if trade.exit_reason == "data_end":
            continue

        entry_mask = equity.index >= pd.Timestamp(trade.entry_date)
        exit_mask = equity.index >= pd.Timestamp(trade.exit_date)

        if entry_mask.any():
            entry_idx = equity.index[entry_mask][0]
            equity.loc[entry_idx:] += trade.pnl

    return equity


def compute_aggregate_metrics(
    trades: list[Trade], equity_curve: pd.Series | None = None
) -> dict:
    """Compute strategy-level aggregate metrics.

    Returns dict with keys: cagr, sharpe, sortino, max_drawdown, calmar,
    win_rate, profit_factor, avg_trade_pnl, avg_holding_days, total_trades,
    trades_completed, trades_open, exposure_pct, avg_win, avg_loss,
    largest_win, largest_loss.
    """
    completed = [t for t in trades if t.exit_reason != "data_end"]
    open_trades = [t for t in trades if t.exit_reason == "data_end"]

    if not completed:
        return _empty_metrics(len(trades), len(open_trades))

    returns = np.array([t.pnl_pct for t in completed])
    pnls = np.array([t.pnl for t in completed])
    wins = returns[returns > 0]
    losses = returns[returns < 0]

    win_rate = len(wins) / len(completed) if completed else 0.0
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_return = float(pnls.sum())
    avg_trade_pnl = float(pnls.mean())
    avg_holding_days = float(np.mean([t.holding_days for t in completed]))

    avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
    largest_win = float(wins.max()) if len(wins) > 0 else 0.0
    largest_loss = float(losses.min()) if len(losses) > 0 else 0.0

    if equity_curve is not None and len(equity_curve) > 1:
        daily_returns = equity_curve.pct_change().dropna()
        trading_days = len(equity_curve)
        years = trading_days / TRADING_DAYS_PER_YEAR

        start_val = equity_curve.iloc[0]
        end_val = equity_curve.iloc[-1]
        total_growth = end_val / start_val if start_val > 0 else 1.0
        cagr = total_growth ** (1 / years) - 1 if years > 0 else 0.0

        excess_returns = daily_returns - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
        sharpe = (
            float(excess_returns.mean() / excess_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
            if excess_returns.std() > 0
            else 0.0
        )

        downside = daily_returns[daily_returns < 0]
        downside_std = downside.std() if len(downside) > 0 else 0.0
        sortino = (
            float(excess_returns.mean() / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))
            if downside_std > 0
            else 0.0
        )

        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_drawdown = float(drawdown.min())

        calmar = abs(cagr / max_drawdown) if max_drawdown != 0 else 0.0

        in_market_days = 0
        for t in completed:
            entry = pd.Timestamp(t.entry_date)
            exit_ = pd.Timestamp(t.exit_date)
            in_market_days += len(
                equity_curve.loc[entry:exit_]
            )
        exposure_pct = in_market_days / trading_days if trading_days > 0 else 0.0
    else:
        cagr = 0.0
        sharpe = 0.0
        sortino = 0.0
        max_drawdown = 0.0
        calmar = 0.0
        exposure_pct = 0.0

    return {
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 4),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "avg_trade_pnl": round(avg_trade_pnl, 2),
        "avg_holding_days": round(avg_holding_days, 1),
        "total_trades": len(trades),
        "trades_completed": len(completed),
        "trades_open": len(open_trades),
        "exposure_pct": round(exposure_pct, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "largest_win": round(largest_win, 4),
        "largest_loss": round(largest_loss, 4),
        "total_return": round(total_return, 2),
    }


def compute_per_ticker_metrics(trades: list[Trade]) -> dict[str, dict]:
    """Compute metrics broken down by ticker.

    Returns dict mapping ticker -> aggregate metrics dict.
    """
    ticker_trades: dict[str, list[Trade]] = {}
    for trade in trades:
        ticker_trades.setdefault(trade.ticker, []).append(trade)

    results = {}
    for ticker, t_trades in sorted(ticker_trades.items()):
        completed = [t for t in t_trades if t.exit_reason != "data_end"]
        if not completed:
            results[ticker] = {"total_trades": len(t_trades), "status": "no_completed_trades"}
            continue

        returns = np.array([t.pnl_pct for t in completed])
        pnls = np.array([t.pnl for t in completed])
        wins = returns[returns > 0]
        losses = returns[returns < 0]

        win_rate = len(wins) / len(completed)
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        total_days = sum(t.holding_days for t in completed)
        avg_days = total_days / len(completed)

        results[ticker] = {
            "total_trades": len(t_trades),
            "trades_completed": len(completed),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
            "avg_trade_pnl": round(float(pnls.mean()), 2),
            "total_pnl": round(float(pnls.sum()), 2),
            "avg_holding_days": round(avg_days, 1),
            "avg_win": round(float(wins.mean()), 4) if len(wins) > 0 else 0.0,
            "avg_loss": round(float(losses.mean()), 4) if len(losses) > 0 else 0.0,
        }

    return results


def compute_confidence(
    trades: list[Trade], min_trades: int = 30, min_tickers: int = 5
) -> float:
    """Composite confidence score (0.0 - 1.0) for strategy results.

    Based on:
    - Sample size: enough trades to be statistically meaningful
    - Win rate consistency: is it consistently positive?
    - Return distribution: are returns clustered or wildly erratic?
    - Ticker diversity: does it work across multiple tickers?

    0.0 = no confidence (too few trades or erratic)
    1.0 = high confidence (many consistent trades across tickers)
    """
    completed = [t for t in trades if t.exit_reason != "data_end"]
    if not completed:
        return 0.0

    scores = []

    n_trades = len(completed)
    trade_score = min(n_trades / min_trades, 1.0)
    scores.append(trade_score * 0.3)

    returns = np.array([t.pnl_pct for t in completed])
    win_rate = (returns > 0).mean()
    wr_score = 1.0 - abs(win_rate - 0.55) * 2
    wr_score = max(0.0, min(1.0, wr_score))
    scores.append(wr_score * 0.25)

    if len(returns) > 1:
        cv = abs(returns.std() / returns.mean()) if returns.mean() != 0 else 2.0
        consistency_score = max(0.0, 1.0 - cv / 3.0)
    else:
        consistency_score = 0.0
    scores.append(consistency_score * 0.25)

    unique_tickers = len({t.ticker for t in completed})
    ticker_score = min(unique_tickers / min_tickers, 1.0)
    scores.append(ticker_score * 0.2)

    return round(sum(scores), 4)


def _empty_metrics(total_trades: int, trades_open: int) -> dict:
    """Return empty metrics dict when no completed trades exist."""
    return {
        "cagr": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
        "calmar": 0.0,
        "win_rate": 0.0,
        "profit_factor": None,
        "avg_trade_pnl": 0.0,
        "avg_holding_days": 0.0,
        "total_trades": total_trades,
        "trades_completed": 0,
        "trades_open": trades_open,
        "exposure_pct": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "total_return": 0.0,
    }


def compute_benchmark_metrics(spy_data: pd.DataFrame, start: str, end: str) -> dict:
    """Compute buy-and-hold metrics for SPY as benchmark.

    Args:
        spy_data: DataFrame with Open, Close columns, datetime index.
        start: Start date string.
        end: End date string.

    Returns:
        Dict with cagr, sharpe, sortino, max_drawdown, calmar.
    """
    if spy_data.empty or len(spy_data) < 2:
        return _empty_metrics(0, 0)

    mask = (spy_data.index >= start) & (spy_data.index <= end)
    data = spy_data.loc[mask]

    if data.empty or len(data) < 2:
        return _empty_metrics(0, 0)

    closes = data["Close"]
    returns = closes.pct_change().dropna()

    start_price = float(closes.iloc[0])
    end_price = float(closes.iloc[-1])
    years = len(closes) / TRADING_DAYS_PER_YEAR

    cagr = (end_price / start_price) ** (1 / years) - 1 if years > 0 else 0.0

    excess = returns - RISK_FREE_RATE / TRADING_DAYS_PER_YEAR
    sharpe = (
        float(excess.mean() / excess.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
        if excess.std() > 0
        else 0.0
    )

    downside = returns[returns < 0]
    down_std = downside.std() if len(downside) > 0 else 0.0
    sortino = (
        float(excess.mean() / down_std * math.sqrt(TRADING_DAYS_PER_YEAR))
        if down_std > 0
        else 0.0
    )

    cummax = closes.cummax()
    dd = (closes - cummax) / cummax
    max_dd = float(dd.min())
    calmar = abs(cagr / max_dd) if max_dd != 0 else 0.0

    return {
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_dd, 4),
        "calmar": round(calmar, 4),
        "win_rate": None,
        "profit_factor": None,
        "avg_trade_pnl": None,
        "avg_holding_days": None,
        "total_trades": None,
        "trades_completed": None,
        "trades_open": None,
        "exposure_pct": 1.0,
        "avg_win": None,
        "avg_loss": None,
        "largest_win": None,
        "largest_loss": None,
        "total_return": round(end_price / start_price - 1, 4),
    }
