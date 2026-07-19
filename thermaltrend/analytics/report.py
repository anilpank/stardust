"""Report generation for analytics results.

Outputs strategy analysis as:
- Terminal table (pretty-printed)
- JSON export (full results)
- CSV export (trade-level data)
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from thermaltrend.analytics.trade_simulator import Trade


def format_ranking_table(df: pd.DataFrame) -> str:
    """Format a ranking DataFrame as a readable terminal table."""
    if df.empty:
        return "No strategies to compare."

    lines = []
    lines.append("")
    lines.append("Strategy Ranking")
    lines.append("=" * 100)

    header = f"{'#':>3s}  {'Strategy':<20s}  {'CAGR':>8s}  {'Sharpe':>8s}  {'Sortino':>8s}  {'MaxDD':>8s}  {'Calmar':>8s}  {'WinRate':>8s}  {'PF':>8s}  {'Trades':>7s}  {'Conf':>6s}"
    lines.append(header)
    lines.append("-" * 100)

    for idx, row in df.iterrows():
        cagr = _fmt_pct(row.get("cagr"))
        sharpe = _fmt_float(row.get("sharpe"))
        sortino = _fmt_float(row.get("sortino"))
        maxdd = _fmt_pct(row.get("max_drawdown"))
        calmar = _fmt_float(row.get("calmar"))
        winrate = _fmt_pct(row.get("win_rate"))
        pf = _fmt_float(row.get("profit_factor"))
        trades = _fmt_int(row.get("total_trades"))
        conf = _fmt_float(row.get("confidence"))

        name = str(row.get("strategy", ""))
        if name == "S&P 500 B&H":
            lines.append("-" * 100)

        lines.append(
            f"{idx:>3d}  {name:<20s}  {cagr:>8s}  {sharpe:>8s}  {sortino:>8s}  "
            f"{maxdd:>8s}  {calmar:>8s}  {winrate:>8s}  {pf:>8s}  {trades:>7s}  {conf:>6s}"
        )

    lines.append("=" * 100)
    lines.append("")
    lines.append("CAGR = Compound Annual Growth Rate | Sharpe = Risk-adj return | Sortino = Downside-only risk-adj")
    lines.append("MaxDD = Max Drawdown | Calmar = CAGR/MaxDD | WinRate = % profitable trades | PF = Profit Factor")
    lines.append("Conf = Confidence score (0-1, based on sample size, consistency, ticker diversity)")
    lines.append("")

    return "\n".join(lines)


def format_per_ticker_table(per_ticker: dict[str, dict], strategy_name: str) -> str:
    """Format per-ticker metrics as a terminal table."""
    if not per_ticker:
        return "No per-ticker data."

    lines = []
    lines.append(f"\nPer-Ticker Performance: {strategy_name}")
    lines.append("=" * 90)
    lines.append(
        f"{'Ticker':<8s}  {'Trades':>7s}  {'WinRate':>8s}  {'PF':>8s}  "
        f"{'Avg PnL':>10s}  {'Total PnL':>12s}  {'Avg Days':>9s}"
    )
    lines.append("-" * 90)

    sorted_tickers = sorted(
        per_ticker.items(),
        key=lambda x: x[1].get("total_pnl", 0),
        reverse=True,
    )

    for ticker, m in sorted_tickers:
        if m.get("status") == "no_completed_trades":
            lines.append(f"{ticker:<8s}  {'N/A':>7s}")
            continue

        lines.append(
            f"{ticker:<8s}  {m['trades_completed']:>7d}  "
            f"{_fmt_pct(m.get('win_rate')):>8s}  "
            f"{_fmt_float(m.get('profit_factor')):>8s}  "
            f"${m['avg_trade_pnl']:>9.2f}  "
            f"${m['total_pnl']:>11.2f}  "
            f"{m['avg_holding_days']:>9.1f}"
        )

    lines.append("=" * 90)
    lines.append("")

    return "\n".join(lines)


def format_regime_table(regime_metrics: dict[str, dict], strategy_name: str) -> str:
    """Format regime breakdown as a terminal table."""
    if not regime_metrics:
        return "No regime data."

    lines = []
    lines.append(f"\nRegime Breakdown: {strategy_name}")
    lines.append("=" * 75)
    lines.append(
        f"{'Regime':<12s}  {'Trades':>7s}  {'WinRate':>8s}  {'Avg PnL':>10s}  {'Total PnL':>12s}  {'Avg Days':>9s}"
    )
    lines.append("-" * 75)

    for regime in ["bull", "bear", "sideways"]:
        m = regime_metrics.get(regime, {})
        trades = m.get("total_trades", 0)
        if trades == 0:
            lines.append(f"{regime.upper():<12s}  {'0':>7s}")
            continue

        lines.append(
            f"{regime.upper():<12s}  {trades:>7d}  "
            f"{_fmt_pct(m.get('win_rate')):>8s}  "
            f"${m.get('avg_trade_pnl', 0):>9.2f}  "
            f"${m.get('total_pnl', 0):>11.2f}  "
            f"{m.get('avg_holding_days', 0):>9.1f}"
        )

    lines.append("=" * 75)
    lines.append("")

    return "\n".join(lines)


def format_signals_table(signals: list, strategy_name: str) -> str:
    """Format signals as a readable table (compatible with existing signals.py)."""
    if not signals:
        return "No signals found."

    lines = [
        f"Signals ({strategy_name})",
        f"{'Date':12s} {'Ticker':8s} {'Direction':10s} {'Strength':10s}",
        "-" * 45,
    ]
    for s in sorted(signals, key=lambda x: x.strength, reverse=True):
        lines.append(
            f"{s.timestamp.date()!s:12s} {s.ticker:8s} {s.direction.value:10s} "
            f"{s.strength:>10.4f}"
        )
    return "\n".join(lines)


def export_json(results: dict, filepath: Path) -> None:
    """Export full results as JSON."""
    serializable = {}
    for key, value in results.items():
        if key == "trades":
            serializable[key] = [_trade_to_dict(t) for t in value]
        elif key == "equity_curve" and hasattr(value, "to_dict"):
            serializable[key] = {
                str(k): round(v, 2) for k, v in value.to_dict().items()
            }
        elif key == "per_ticker":
            serializable[key] = value
        else:
            serializable[key] = value

    with open(filepath, "w") as f:
        json.dump(serializable, f, indent=2, default=str)


def export_trades_csv(trades: list[Trade], filepath: Path) -> None:
    """Export trade-level data as CSV."""
    if not trades:
        return

    rows = []
    for t in trades:
        rows.append({
            "ticker": t.ticker,
            "entry_date": t.entry_date,
            "entry_price": t.entry_price,
            "exit_date": t.exit_date,
            "exit_price": t.exit_price,
            "direction": t.direction.value,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "holding_days": t.holding_days,
            "exit_reason": t.exit_reason,
            "strategy_id": t.strategy_id,
            "shares": t.shares,
            "stop_price": t.stop_price,
        })

    df = pd.DataFrame(rows)
    df.to_csv(filepath, index=False)


def _trade_to_dict(trade: Trade) -> dict[str, Any]:
    """Convert a Trade to a JSON-serializable dict."""
    return {
        "ticker": trade.ticker,
        "entry_date": str(trade.entry_date),
        "entry_price": trade.entry_price,
        "exit_date": str(trade.exit_date),
        "exit_price": trade.exit_price,
        "direction": trade.direction.value,
        "pnl": trade.pnl,
        "pnl_pct": trade.pnl_pct,
        "holding_days": trade.holding_days,
        "exit_reason": trade.exit_reason,
        "strategy_id": trade.strategy_id,
        "shares": trade.shares,
        "stop_price": trade.stop_price,
    }


def _fmt_pct(val: float | None, decimals: int = 1) -> str:
    if val is None or (isinstance(val, float) and (val != val)):
        return "N/A"
    return f"{val * 100:.{decimals}f}%"


def _fmt_float(val: float | None, decimals: int = 2) -> str:
    if val is None or (isinstance(val, float) and (val != val)):
        return "N/A"
    return f"{val:.{decimals}f}"


def _fmt_int(val: int | None) -> str:
    if val is None or (isinstance(val, float) and (val != val)):
        return "N/A"
    return str(int(val)) if isinstance(val, float) and val == val else str(val)
