"""Market regime detection and per-regime performance analysis.

Classifies market conditions into BULL / BEAR / SIDEWAYS based on
S&P 500 trailing performance, then breaks down strategy metrics by regime.
"""

from enum import Enum

import numpy as np
import pandas as pd

from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.analytics.metrics import compute_aggregate_metrics


class MarketRegime(Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"


def classify_regime(
    spx_closes: pd.Series,
    lookback_days: int = 252,
    bull_threshold: float = 0.10,
    bear_threshold: float = -0.10,
) -> pd.Series:
    """Classify each date into a market regime based on trailing SPX performance.

    Args:
        spx_closes: Series of SPX close prices, datetime-indexed.
        lookback_days: Trailing window for return calculation (default 252 = 1 year).
        bull_threshold: Return above which is classified as BULL (default 10%).
        bear_threshold: Return below which is classified as BEAR (default -10%).

    Returns:
        Series of MarketRegime values, same index as input.
    """
    trailing_return = spx_closes.pct_change(lookback_days)

    regimes = pd.Series(MarketRegime.SIDEWAYS, index=spx_closes.index)
    regimes[trailing_return > bull_threshold] = MarketRegime.BULL
    regimes[trailing_return < bear_threshold] = MarketRegime.BEAR

    return regimes


def compute_regime_metrics(
    trades: list[Trade],
    regimes: pd.Series,
) -> dict[str, dict]:
    """Break down strategy metrics by market regime.

    Args:
        trades: List of Trade objects.
        regimes: Series of MarketRegime values indexed by date.

    Returns:
        Dict mapping regime name -> metrics dict.
    """
    regime_trades: dict[str, list[Trade]] = {r.value: [] for r in MarketRegime}

    for trade in trades:
        trade_date = pd.Timestamp(trade.entry_date)
        if trade_date in regimes.index:
            regime = regimes.loc[trade_date]
            regime_trades[regime.value].append(trade)
        else:
            nearest = regimes.index[regimes.index.get_indexer([trade_date], method="pad")]
            if len(nearest) > 0:
                regime = regimes.loc[nearest[0]]
                regime_trades[regime.value].append(trade)

    results = {}
    for regime_name, r_trades in regime_trades.items():
        if not r_trades:
            results[regime_name] = {
                "total_trades": 0,
                "cagr": 0.0,
                "sharpe": 0.0,
                "win_rate": 0.0,
                "avg_trade_pnl": 0.0,
                "total_pnl": 0.0,
            }
            continue

        completed = [t for t in r_trades if t.exit_reason != "data_end"]
        if not completed:
            results[regime_name] = {
                "total_trades": len(r_trades),
                "cagr": 0.0,
                "sharpe": 0.0,
                "win_rate": 0.0,
                "avg_trade_pnl": 0.0,
                "total_pnl": 0.0,
            }
            continue

        returns = np.array([t.pnl_pct for t in completed])
        pnls = np.array([t.pnl for t in completed])
        win_rate = (returns > 0).mean()
        avg_pnl = float(pnls.mean())
        total_pnl = float(pnls.sum())

        avg_holding = np.mean([t.holding_days for t in completed])

        results[regime_name] = {
            "total_trades": len(r_trades),
            "trades_completed": len(completed),
            "win_rate": round(float(win_rate), 4),
            "avg_trade_pnl": round(avg_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_holding_days": round(float(avg_holding), 1),
        }

    return results
