"""Reusable Plotly chart components for the Streamlit dashboard.

Each function returns a Plotly Figure object that can be rendered with st.plotly_chart().
All charts use a consistent dark-themed style matching the dashboard aesthetic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from thermaltrend.analytics.trade_simulator import Trade

COLORS = {
    "blue": "#4C78A8",
    "green": "#54A24B",
    "red": "#E45756",
    "orange": "#EECA3B",
    "purple": "#B279A2",
    "cyan": "#72B7B2",
    "pink": "#FF9DA6",
    "grey": "#9D755D",
    "bull": "#54A24B",
    "bear": "#E45756",
    "sideways": "#EECA3B",
}

LAYOUT_DEFAULTS = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="monospace", size=12),
    margin=dict(l=50, r=20, t=40, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)


def _apply_layout(fig: go.Figure, title: str, height: int = 400) -> go.Figure:
    fig.update_layout(**LAYOUT_DEFAULTS, title=dict(text=title, font=dict(size=16)))
    fig.update_layout(height=height)
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    return fig


def equity_curve(equity: pd.Series, benchmark: pd.Series | None = None, title: str = "Equity Curve") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.values,
        mode="lines", name="Strategy",
        line=dict(color=COLORS["blue"], width=2),
        fill="tozeroy", fillcolor="rgba(76,120,168,0.1)",
    ))
    if benchmark is not None and len(benchmark) > 1:
        fig.add_trace(go.Scatter(
            x=benchmark.index, y=benchmark.values,
            mode="lines", name="S&P 500 B&H",
            line=dict(color=COLORS["grey"], width=1.5, dash="dot"),
        ))
    fig.update_layout(yaxis_title="Portfolio Value ($)")
    return _apply_layout(fig, title)


def drawdown_chart(equity: pd.Series, title: str = "Drawdown") -> go.Figure:
    cummax = equity.cummax()
    dd = (equity - cummax) / cummax * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values,
        mode="lines", name="Drawdown",
        line=dict(color=COLORS["red"], width=1.5),
        fill="tozeroy", fillcolor="rgba(228,87,86,0.2)",
    ))
    fig.update_layout(yaxis_title="Drawdown (%)")
    return _apply_layout(fig, title, height=250)


def price_with_signals(ticker_df: pd.DataFrame, signals: list, title: str | None = None) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3], vertical_spacing=0.03,
    )
    fig.add_trace(go.Candlestick(
        x=ticker_df.index, open=ticker_df["Open"], high=ticker_df["High"],
        low=ticker_df["Low"], close=ticker_df["Close"],
        increasing_line_color=COLORS["bull"], decreasing_line_color=COLORS["bear"],
        name="Price",
    ), row=1, col=1)

    buys = [s for s in signals if s.direction.value == "BUY"]
    sells = [s for s in signals if s.direction.value == "SELL"]

    if buys:
        fig.add_trace(go.Scatter(
            x=[s.timestamp for s in buys],
            y=[ticker_df.loc[pd.Timestamp(s.timestamp.date()), "Low"] * 0.98
               for s in buys if pd.Timestamp(s.timestamp.date()) in ticker_df.index],
            mode="markers", name="BUY",
            marker=dict(symbol="triangle-up", size=12, color=COLORS["bull"]),
        ), row=1, col=1)

    if sells:
        fig.add_trace(go.Scatter(
            x=[s.timestamp for s in sells],
            y=[ticker_df.loc[pd.Timestamp(s.timestamp.date()), "High"] * 1.02
               for s in sells if pd.Timestamp(s.timestamp.date()) in ticker_df.index],
            mode="markers", name="SELL",
            marker=dict(symbol="triangle-down", size=12, color=COLORS["bear"]),
        ), row=1, col=1)

    vol_colors = [
        COLORS["bull"] if c >= o else COLORS["bear"]
        for c, o in zip(ticker_df["Close"], ticker_df["Open"])
    ]
    fig.add_trace(go.Bar(
        x=ticker_df.index, y=ticker_df["Volume"],
        name="Volume", marker_color=vol_colors, opacity=0.4,
    ), row=2, col=1)

    fig.update_layout(xaxis_rangeslider_visible=False)
    fig.update_yaxes(title_text="Price ($)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    label = title or f"Price & Signals"
    return _apply_layout(fig, label, height=500)


def pnl_distribution(trades: list[Trade], title: str = "Trade P&L Distribution") -> go.Figure:
    completed = [t for t in trades if t.exit_reason != "data_end"]
    if not completed:
        fig = go.Figure()
        fig.add_annotation(text="No completed trades", showarrow=False, font=dict(size=20))
        return _apply_layout(fig, title, height=300)

    pnls = [t.pnl for t in completed]
    colors = [COLORS["bull"] if p > 0 else COLORS["bear"] for p in pnls]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=list(range(len(pnls))),
        y=pnls,
        marker_color=colors,
        name="P&L",
    ))
    fig.update_layout(
        xaxis_title="Trade #",
        yaxis_title="P&L ($)",
        showlegend=False,
    )
    return _apply_layout(fig, title, height=300)


def per_ticker_bar(per_ticker: dict[str, dict], metric: str = "total_pnl", title: str | None = None) -> go.Figure:
    tickers_data = {k: v for k, v in per_ticker.items() if v.get("status") != "no_completed_trades"}
    if not tickers_data:
        fig = go.Figure()
        fig.add_annotation(text="No completed trades", showarrow=False, font=dict(size=20))
        return _apply_layout(fig, title or "Per-Ticker Performance", height=300)

    sorted_items = sorted(tickers_data.items(), key=lambda x: x[1].get(metric, 0), reverse=True)
    labels = [t[0] for t in sorted_items]
    values = [t[1].get(metric, 0) for t in sorted_items]
    colors = [COLORS["bull"] if v > 0 else COLORS["bear"] for v in values]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=values, marker_color=colors, name=metric))
    y_title = "Total P&L ($)" if metric == "total_pnl" else metric.replace("_", " ").title()
    fig.update_layout(xaxis_title="Ticker", yaxis_title=y_title, showlegend=False)
    label = title or f"Per-Ticker {y_title}"
    return _apply_layout(fig, label, height=350)


def regime_bar(regime_metrics: dict[str, dict], title: str = "Performance by Market Regime") -> go.Figure:
    regimes = ["bull", "bear", "sideways"]
    labels = []
    pnls = []
    colors = []
    for r in regimes:
        m = regime_metrics.get(r, {})
        pnl = m.get("total_pnl", 0)
        trades = m.get("total_trades", 0)
        if trades > 0:
            labels.append(r.upper())
            pnls.append(pnl)
            colors.append(COLORS.get(r, COLORS["grey"]))

    if not labels:
        fig = go.Figure()
        fig.add_annotation(text="No regime data", showarrow=False, font=dict(size=20))
        return _apply_layout(fig, title, height=300)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=pnls, marker_color=colors, name="Total P&L"))
    fig.update_layout(xaxis_title="Regime", yaxis_title="Total P&L ($)", showlegend=False)
    return _apply_layout(fig, title, height=300)


def strategy_comparison_bar(ranking_df: pd.DataFrame, metric: str = "sharpe", title: str | None = None) -> go.Figure:
    if ranking_df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No data", showarrow=False, font=dict(size=20))
        return _apply_layout(fig, title or "Strategy Comparison", height=300)

    df = ranking_df.copy()
    is_benchmark = df["strategy"] == "S&P 500 B&H"
    colors = [COLORS["grey"] if b else COLORS["blue"] for b in is_benchmark]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["strategy"], y=df[metric],
        marker_color=colors, name=metric.replace("_", " ").title(),
    ))

    if metric == "max_drawdown":
        fig.update_layout(yaxis_title=f"{metric.replace('_', ' ').title()} (%)")
    else:
        fig.update_layout(yaxis_title=metric.replace("_", " ").title())
    fig.update_layout(xaxis_title="Strategy", showlegend=False, xaxis_tickangle=-15)
    label = title or f"Strategy Comparison: {metric.replace('_', ' ').title()}"
    return _apply_layout(fig, label, height=350)


def period_heatmap(period_metrics: dict[str, dict], title: str = "Monthly P&L Heatmap") -> go.Figure:
    if not period_metrics:
        fig = go.Figure()
        fig.add_annotation(text="No period data", showarrow=False, font=dict(size=20))
        return _apply_layout(fig, title, height=300)

    labels = sorted(period_metrics.keys())
    pnls = [period_metrics[l].get("total_pnl", 0) for l in labels]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=pnls,
        marker_color=[COLORS["bull"] if p > 0 else COLORS["bear"] for p in pnls],
        name="Total P&L",
    ))
    fig.update_layout(xaxis_title="Period", yaxis_title="Total P&L ($)", showlegend=False)
    fig.update_xaxes(tickangle=-45)
    return _apply_layout(fig, title, height=350)
