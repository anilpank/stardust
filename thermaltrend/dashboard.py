"""
Streamlit dashboard for Thermaltrend strategy analysis.

Usage:
    streamlit run thermaltrend/dashboard.py
    streamlit run thermaltrend/dashboard.py --server.port 8501
"""

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import pandas as pd
import streamlit as st

from thermaltrend.analytics.metrics import compute_benchmark_metrics, compute_equity_curve, compute_period_metrics
from thermaltrend.analytics.regime import classify_regime, compute_regime_metrics
from thermaltrend.charts import (
    drawdown_chart,
    equity_curve,
    normalized_price_overlay,
    ohlcv_chart,
    per_ticker_bar,
    period_heatmap,
    pnl_distribution,
    price_with_signals,
    regime_bar,
    strategy_comparison_bar,
)
from thermaltrend.compare_cli import run_compare
from thermaltrend.core.engine import DataEngine
from thermaltrend.core.strategy import (
    ATRTrailingStopStrategy,
    DonchianBreakoutStrategy,
    MACrossoverStrategy,
    RSIMeanReversionStrategy,
)
from thermaltrend.feed import DataFeed
from thermaltrend.signal_store import SignalStore

DEFAULT_DATA_DIR = str(Path(__file__).parent / "data" / "equities")

STRATEGY_REGISTRY = {
    "MA 50/200": MACrossoverStrategy,
    "Donchian 20/10": DonchianBreakoutStrategy,
    "RSI 14": RSIMeanReversionStrategy,
    "ATR Trail 20/14/3": ATRTrailingStopStrategy,
}

STRATEGY_DEFAULTS = {
    "MA 50/200": {"fast_period": 50, "slow_period": 200},
    "Donchian 20/10": {"entry_period": 20, "exit_period": 10},
    "RSI 14": {"period": 14, "oversold": 30.0, "overbought": 70.0},
    "ATR Trail 20/14/3": {"entry_period": 20, "atr_period": 14, "atr_multiple": 3.0},
}

STRATEGY_DESCRIPTIONS = {
    "MA 50/200": "Buys when the 50-day moving average crosses above the 200-day (golden cross). Sells on the reverse (death cross). Best for trending markets.",
    "Donchian 20/10": "Buys when price breaks above the 20-day high. Sells when it drops below the 10-day low. Captures breakouts with quick exits.",
    "RSI 14": "Buys when RSI bounces off oversold (30). Sells when it drops from overbought (70). Best for range-bound markets.",
    "ATR Trail 20/14/3": "Buys on 20-day breakout, exits via ATR-based trailing stop that ratchets up. Lets winners run while protecting gains.",
}

ALL_TICKERS = sorted(p.stem for p in Path(DEFAULT_DATA_DIR).glob("*.parquet") if p.stem != "SPY")


def _metric_card(label: str, value: str, delta: str | None = None, delta_color: str = "normal"):
    st.metric(label=label, value=value, delta=delta, delta_color=delta_color)


def _traffic_light(value: float, good: float, warn: float, invert: bool = False) -> str:
    if invert:
        if value <= good:
            return "good"
        elif value <= warn:
            return "neutral"
        return "bad"
    else:
        if value >= good:
            return "good"
        elif value >= warn:
            return "neutral"
        return "bad"


def _render_metric_cards(m: dict, confidence: float):
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        cagr_pct = m["cagr"] * 100
        st.metric("Annual Return (CAGR)", f"{cagr_pct:.1f}%",
                   delta=f"vs 4% risk-free" if cagr_pct > 4 else None)

    with col2:
        st.metric("Sharpe Ratio", f"{m['sharpe']:.2f}",
                   delta="Risk-adjusted",
                   delta_color="off")

    with col3:
        maxdd = m["max_drawdown"] * 100
        st.metric("Max Drawdown", f"{maxdd:.1f}%",
                   delta_color="inverse")

    with col4:
        win_pct = m["win_rate"] * 100
        st.metric("Win Rate", f"{win_pct:.0f}%")

    col5, col6, col7, col8 = st.columns(4)

    with col5:
        pf = m["profit_factor"]
        pf_str = f"{pf:.2f}" if pf is not None else "N/A"
        st.metric("Profit Factor", pf_str)

    with col6:
        st.metric("Avg Trade P&L", f"${m['avg_trade_pnl']:,.0f}",
                   delta_color="off")

    with col7:
        st.metric("Total Trades", str(m["total_trades"]),
                   delta=f"{m['trades_completed']} closed, {m['trades_open']} open",
                   delta_color="off")

    with col8:
        conf_pct = confidence * 100
        st.metric("Confidence", f"{conf_pct:.0f}%",
                   delta="Statistical trust",
                   delta_color="off")


def _render_jargon_free_summary(m: dict, strategy_name: str):
    cagr_pct = m["cagr"] * 100
    maxdd_pct = m["max_drawdown"] * 100
    sharpe = m["sharpe"]
    win_rate = m["win_rate"] * 100

    if sharpe >= 1.0:
        quality = "strong"
        quality_color = "green"
    elif sharpe >= 0.5:
        quality = "decent"
        quality_color = "orange"
    else:
        quality = "weak"
        quality_color = "red"

    if win_rate >= 55:
        wr_word = "wins more often than it loses"
    elif win_rate >= 45:
        wr_word = "wins and loses about equally"
    else:
        wr_word = "loses more often than it wins"

    if maxdd_pct > -15:
        risk = "moderate"
    elif maxdd_pct > -25:
        risk = "elevated"
    else:
        risk = "high"

    st.markdown(f"""
    <div style="background: rgba(28,33,39,0.6); border-radius: 10px; padding: 18px 22px; margin-bottom: 18px; border-left: 4px solid {'#54A24B' if quality == 'strong' else '#EECA3B' if quality == 'decent' else '#E45756'}">
        <div style="font-size: 15px; color: #e0e0e0; line-height: 1.6;">
            <strong>{strategy_name}</strong> delivered
            <span style="color: {'#54A24B' if cagr_pct > 0 else '#E45756'}; font-weight:bold;">{cagr_pct:+.1f}% annual return</span>
            with a <span style="color: {quality_color}; font-weight:bold;">{quality}</span> risk-adjusted profile (Sharpe {sharpe:.2f}).
            It <strong>{wr_word}</strong> ({win_rate:.0f}% win rate),
            with {risk} downside risk ({maxdd_pct:.1f}% worst drawdown).
        </div>
    </div>
    """, unsafe_allow_html=True)


def _render_signal_table(signals_df: pd.DataFrame):
    if signals_df.empty:
        st.info("No signals found.")
        return

    display = signals_df.copy()
    display["date"] = pd.to_datetime(display["timestamp"]).dt.strftime("%Y-%m-%d")
    display["direction"] = display["direction"].map({"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"})
    display["strength_pct"] = (display["strength"] * 100).round(1).astype(str) + "%"
    display = display[["date", "ticker", "direction", "strength_pct", "strategy_name"]].copy()
    display.columns = ["Date", "Ticker", "Direction", "Strength", "Strategy"]

    buy_mask = display["Direction"] == "BUY"
    sell_mask = display["Direction"] == "SELL"

    def color_direction(val):
        if val == "BUY":
            return "color: #54A24B; font-weight: bold"
        elif val == "SELL":
            return "color: #E45756; font-weight: bold"
        return ""

    styled = display.style.applymap(color_direction, subset=["Direction"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


def page_overview(result: dict):
    m = result["metrics"]
    equity = result["equity_curve"]

    _render_jargon_free_summary(m, result["strategy_name"])
    _render_metric_cards(m, result["confidence"])

    st.subheader("Equity Curve")
    col1, col2 = st.columns([3, 1])
    with col1:
        benchmark_eq = None
        try:
            spy = pd.read_parquet(Path(__file__).parent / "data" / "equities" / "SPY.parquet")
            if len(equity) > 1 and equity.index[0] is not None:
                bench_m = compute_benchmark_metrics(
                    spy, str(equity.index[0].date()), str(equity.index[-1].date())
                )
                first_entry = min(t.entry_date for t in result["trades"]) if result["trades"] else equity.index[0]
                benchmark_eq = compute_equity_curve(
                    result["trades"], pd.Timestamp(first_entry), initial_capital=100_000
                )
        except Exception:
            pass
        st.plotly_chart(equity_curve(equity, benchmark_eq), use_container_width=True)
    with col2:
        st.plotly_chart(drawdown_chart(equity), use_container_width=True)

    signals = result.get("signals", [])
    trades = result.get("trades", [])
    tickers_in_result = sorted({t.ticker for t in trades}) if trades else []
    if signals and tickers_in_result:
        st.subheader("Price & Signals")
        selected_signal_ticker = st.selectbox(
            "Ticker", tickers_in_result, key="overview_signal_ticker",
        )
        try:
            ticker_df = _load_ticker_data(selected_signal_ticker)
            if not ticker_df.empty:
                ticker_signals = [s for s in signals if s.ticker == selected_signal_ticker]
                st.plotly_chart(
                    price_with_signals(ticker_df, ticker_signals, title=f"{selected_signal_ticker} — Price & Signals"),
                    use_container_width=True,
                )
        except Exception:
            pass

    st.subheader("P&L Distribution")
    st.plotly_chart(pnl_distribution(trades), use_container_width=True)

    if trades:
        period_m = compute_period_metrics(trades, period="monthly")
        if period_m:
            st.subheader("Monthly Performance")
            st.plotly_chart(period_heatmap(period_m), use_container_width=True)


def page_trades(result: dict):
    trades = result["trades"]
    completed = [t for t in trades if t.exit_reason != "data_end"]
    open_trades = [t for t in trades if t.exit_reason == "data_end"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Completed", len(completed))
    col2.metric("Still Open", len(open_trades))
    total_pnl = sum(t.pnl for t in completed)
    col3.metric("Total P&L", f"${total_pnl:,.0f}",
                delta_color="inverse")

    if not completed:
        st.info("No completed trades to display.")
        return

    rows = []
    for t in completed:
        rows.append({
            "Ticker": t.ticker,
            "Entry Date": t.entry_date.strftime("%Y-%m-%d"),
            "Entry $": f"${t.entry_price:.2f}",
            "Exit Date": t.exit_date.strftime("%Y-%m-%d"),
            "Exit $": f"${t.exit_price:.2f}",
            "P&L": f"${t.pnl:,.2f}",
            "P&L %": f"{t.pnl_pct * 100:.1f}%",
            "Days": t.holding_days,
            "Exit Reason": t.exit_reason,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def page_per_ticker(result: dict):
    per_ticker = result.get("per_ticker", {})
    if not per_ticker:
        st.info("No per-ticker data available.")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(per_ticker_bar(per_ticker, "total_pnl"), use_container_width=True)
    with col2:
        st.plotly_chart(per_ticker_bar(per_ticker, "win_rate"), use_container_width=True)


def page_regime(result: dict):
    try:
        spy = pd.read_parquet(Path(__file__).parent / "data" / "equities" / "SPY.parquet")
        regimes = classify_regime(spy["Close"])
        regime_m = compute_regime_metrics(result["trades"], regimes)
    except Exception as e:
        st.warning(f"Could not compute regime analysis: {e}")
        return

    st.subheader("Market Regime Breakdown")

    regime_data = []
    for r in ["bull", "bear", "sideways"]:
        m = regime_m.get(r, {})
        trades = m.get("total_trades", 0)
        regime_data.append({
            "Regime": r.upper(),
            "Trades": trades,
            "Win Rate": f"{m.get('win_rate', 0) * 100:.0f}%" if trades > 0 else "-",
            "Avg P&L": f"${m.get('avg_trade_pnl', 0):,.0f}" if trades > 0 else "-",
            "Total P&L": f"${m.get('total_pnl', 0):,.0f}" if trades > 0 else "-",
            "Avg Days": f"{m.get('avg_holding_days', 0):.0f}" if trades > 0 else "-",
        })

    st.dataframe(pd.DataFrame(regime_data), use_container_width=True, hide_index=True)
    st.plotly_chart(regime_bar(regime_m), use_container_width=True)


def page_signals(strategy_name: str, tickers: list[str], start: str, end: str):
    st.subheader("Generate & View Signals")

    col1, col2 = st.columns([1, 1])
    with col1:
        min_strength = st.slider("Minimum signal strength", 0.0, 1.0, 0.0, 0.05)
    with col2:
        direction_filter = st.selectbox("Direction filter", ["All", "BUY", "SELL"])

    if st.button("Generate Signals", type="primary", key="gen_signals"):
        with st.spinner("Running strategy..."):
            strategy_cls = STRATEGY_REGISTRY[strategy_name]
            strategy = strategy_cls(**STRATEGY_DEFAULTS[strategy_name])
            feed = DataFeed(DEFAULT_DATA_DIR, tickers=tickers, start_date=start, end_date=end)

            if len(feed) == 0:
                st.error("No data found for the selected tickers and date range.")
                return

            engine = DataEngine(feed, strategy)
            signals = engine.run()

        filtered = [
            s for s in signals
            if s.strength >= min_strength
            and (direction_filter == "All" or s.direction.value == direction_filter)
        ]

        st.success(f"Found {len(filtered)} signals from {len(signals)} total")

        if filtered:
            signals_df = pd.DataFrame([{
                "id": str(s.id),
                "timestamp": s.timestamp,
                "ticker": s.ticker,
                "direction": s.direction.value,
                "strength": s.strength,
                "strategy_name": strategy_name,
            } for s in filtered])
            _render_signal_table(signals_df)

            if st.button("Save Signals to Store"):
                store = SignalStore()
                run_id = store.save(
                    signals=filtered,
                    strategy_name=strategy_name,
                    tickers=tickers,
                    start_date=start,
                    end_date=end,
                )
                st.success(f"Signals saved! Run ID: `{run_id}`")


def page_compare(tickers: list[str], start: str, end: str):
    st.subheader("Compare Strategies")

    sort_by = st.selectbox(
        "Rank by",
        ["sharpe", "cagr", "sortino", "max_drawdown", "calmar", "win_rate"],
        index=0,
    )

    if st.button("Run Comparison", type="primary", key="run_compare"):
        with st.spinner("Running all strategies..."):
            ranking, results = run_compare(
                tickers=tickers,
                start_date=start,
                end_date=end,
                sort_by=sort_by,
            )

        st.success("Comparison complete")

        st.dataframe(
            ranking.style.format({
                "cagr": "{:.1%}",
                "sharpe": "{:.2f}",
                "sortino": "{:.2f}",
                "max_drawdown": "{:.1%}",
                "calmar": "{:.2f}",
                "win_rate": "{:.0%}",
                "profit_factor": "{:.2f}",
                "confidence": "{:.0%}",
            }),
            use_container_width=True,
        )

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(strategy_comparison_bar(ranking, "sharpe"), use_container_width=True)
        with col2:
            st.plotly_chart(strategy_comparison_bar(ranking, "cagr"), use_container_width=True)

        if "max_drawdown" in ranking.columns:
            st.plotly_chart(strategy_comparison_bar(ranking, "max_drawdown",
                                                     title="Strategy Comparison: Max Drawdown"), use_container_width=True)


def page_saved_signals():
    st.subheader("Saved Signal Runs")
    store = SignalStore()
    runs = store.list_runs()

    if not runs:
        st.info("No saved signal runs yet.")
        return

    rows = []
    for run in runs:
        tickers_str = ",".join(run.tickers[:3])
        if len(run.tickers) > 3:
            tickers_str += f"+{len(run.tickers) - 3}"
        rows.append({
            "Run ID": run.run_id[:20] + "...",
            "Strategy": run.strategy_name,
            "Tickers": tickers_str,
            "Signals": run.signal_count,
            "Saved At": run.run_timestamp.strftime("%Y-%m-%d %H:%M"),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    selected_run = st.selectbox("Select a run to view", [r.run_id for r in runs],
                                 format_func=lambda x: x[:20] + "...")
    if selected_run:
        df = store.load_run(selected_run)
        if not df.empty:
            _render_signal_table(df)


DATE_PRESETS = {
    "1M": 30, "3M": 90, "6M": 180, "1Y": 365,
    "3Y": 3 * 365, "5Y": 5 * 365, "10Y": 10 * 365, "Max": None,
}


def _load_ticker_data(ticker: str) -> pd.DataFrame:
    path = Path(DEFAULT_DATA_DIR) / f"{ticker}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df = df.dropna(subset=["Close"])
    return df


def _filter_by_preset(df: pd.DataFrame, preset: str) -> pd.DataFrame:
    days = DATE_PRESETS.get(preset)
    if days is None or df.empty:
        return df
    cutoff = df.index[-1] - pd.Timedelta(days=days)
    return df[df.index >= cutoff]


def page_data_explorer():
    st.subheader("Data Explorer")

    explorer_ticker = st.selectbox("Ticker", ALL_TICKERS, index=0, key="explorer_ticker")
    preset = st.radio("Period", list(DATE_PRESETS.keys()), index=5, horizontal=True, key="explorer_preset")
    use_custom = st.checkbox("Custom date range", key="explorer_custom")
    if use_custom:
        custom_start = st.date_input("Start", value=pd.Timestamp("2020-01-01").date(), key="explorer_start")
        custom_end = st.date_input("End", value=pd.Timestamp.today().date(), key="explorer_end")

    df = _load_ticker_data(explorer_ticker)
    if df.empty:
        st.error(f"No data found for {explorer_ticker}")
        return

    if use_custom:
        df = df[(df.index >= pd.Timestamp(custom_start)) & (df.index <= pd.Timestamp(custom_end))]
    else:
        df = _filter_by_preset(df, preset)

    if df.empty:
        st.info("No data for the selected period")
        return

    last_close = df["Close"].iloc[-1]
    high_52w = df["High"].iloc[-min(252, len(df)):].max()
    low_52w = df["Low"].iloc[-min(252, len(df)):].min()
    avg_vol = df["Volume"].iloc[-min(252, len(df)):].mean()
    total_return = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Last Close", f"${last_close:,.2f}")
    c2.metric("52W High", f"${high_52w:,.2f}")
    c3.metric("52W Low", f"${low_52w:,.2f}")
    c4.metric("Avg Volume", f"{avg_vol:,.0f}")
    c5.metric("Total Return", f"{total_return:+.1f}%")

    st.plotly_chart(ohlcv_chart(df, explorer_ticker), use_container_width=True)

    with st.expander("Raw Data"):
        display_df = df.copy()
        display_df.index = display_df.index.strftime("%Y-%m-%d")
        st.dataframe(display_df, use_container_width=True)


def page_compare_tickers():
    st.subheader("Compare Tickers")

    compare_tickers = st.multiselect(
        "Tickers to Compare", ALL_TICKERS,
        default=["AAPL", "MSFT", "GOOGL"], key="compare_tickers",
    )
    cmp_preset = st.radio("Period", list(DATE_PRESETS.keys()), index=5, horizontal=True, key="cmp_preset")
    cmp_custom = st.checkbox("Custom date range", key="cmp_custom")
    if cmp_custom:
        cmp_start = st.date_input("Start", value=pd.Timestamp("2020-01-01").date(), key="cmp_start")
        cmp_end = st.date_input("End", value=pd.Timestamp.today().date(), key="cmp_end")

    if not compare_tickers:
        st.info("Select at least one ticker in the sidebar")
        return

    ticker_dfs = {}
    for t in compare_tickers:
        df = _load_ticker_data(t)
        if not df.empty:
            if cmp_custom:
                df = df[(df.index >= pd.Timestamp(cmp_start)) & (df.index <= pd.Timestamp(cmp_end))]
            else:
                df = _filter_by_preset(df, cmp_preset)
            if not df.empty:
                ticker_dfs[t] = df

    if not ticker_dfs:
        st.info("No data for the selected tickers and period")
        return

    st.plotly_chart(normalized_price_overlay(ticker_dfs), use_container_width=True)

    rows = []
    for name, df in ticker_dfs.items():
        total_ret = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        daily_ret = df["Close"].pct_change().dropna()
        volatility = daily_ret.std() * (252 ** 0.5) * 100 if len(daily_ret) > 1 else 0
        years = len(df) / 252
        cagr = ((df["Close"].iloc[-1] / df["Close"].iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
        cummax = df["Close"].cummax()
        max_dd = ((df["Close"] - cummax) / cummax).min() * 100
        avg_vol = df["Volume"].mean()
        rows.append({
            "Ticker": name,
            "Total Return": f"{total_ret:+.1f}%",
            "CAGR": f"{cagr:+.1f}%",
            "Volatility": f"{volatility:.1f}%",
            "Max Drawdown": f"{max_dd:.1f}%",
            "Avg Volume": f"{avg_vol:,.0f}",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def main():
    st.set_page_config(
        page_title="Thermaltrend Dashboard",
        page_icon="thermaltrend",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
    .stMetric > div { background: rgba(28,33,39,0.6); border-radius: 8px; padding: 12px; }
    [data-testid="stSidebar"] { background-color: #1a1d23; }
    </style>
    """, unsafe_allow_html=True)

    strategy_name = "MA 50/200"
    tickers = ["AAPL", "MSFT", "GOOGL"]
    start = pd.Timestamp("2023-01-01").date()
    end = pd.Timestamp("2026-07-01").date()
    params = {}

    with st.sidebar:
        st.title("Thermaltrend")
        st.caption("Strategy Analysis Dashboard")
        st.divider()

        tab_choice = st.radio(
            "Navigation",
            ["Overview", "Trades", "Per-Ticker", "Regime", "Signals", "Compare", "Saved Runs",
             "Data Explorer", "Compare Tickers"],
            label_visibility="collapsed",
        )

        st.divider()

        if tab_choice in ("Data Explorer", "Compare Tickers"):
            st.info("Use the controls on the main page to configure this view.")
        else:
            strategy_name = st.selectbox("Strategy", list(STRATEGY_REGISTRY.keys()), index=0)
            st.caption(STRATEGY_DESCRIPTIONS[strategy_name])

            tickers = st.multiselect(
                "Tickers",
                ALL_TICKERS,
                default=["AAPL", "MSFT", "GOOGL"],
            )

            col1, col2 = st.columns(2)
            with col1:
                start = st.date_input("Start", value=pd.Timestamp("2023-01-01").date())
            with col2:
                end = st.date_input("End", value=pd.Timestamp("2026-07-01").date())

            st.divider()

            with st.expander("Strategy Parameters", expanded=False):
                params = {}
                if strategy_name == "MA 50/200":
                    params["fast_period"] = st.number_input("Fast MA", 5, 200, 50)
                    params["slow_period"] = st.number_input("Slow MA", 20, 500, 200)
                elif strategy_name == "Donchian 20/10":
                    params["entry_period"] = st.number_input("Entry Period", 5, 100, 20)
                    params["exit_period"] = st.number_input("Exit Period", 5, 50, 10)
                elif strategy_name == "RSI 14":
                    params["period"] = st.number_input("RSI Period", 5, 50, 14)
                    params["oversold"] = st.number_input("Oversold", 10, 40, 30)
                    params["overbought"] = st.number_input("Overbought", 60, 90, 70)
                elif strategy_name == "ATR Trail 20/14/3":
                    params["entry_period"] = st.number_input("Entry Period", 5, 100, 20)
                    params["atr_period"] = st.number_input("ATR Period", 5, 50, 14)
                    params["atr_multiple"] = st.number_input("ATR Multiple", 1.0, 5.0, 3.0, 0.5)

            run_backtest = tab_choice not in ("Compare", "Saved Runs")

            if run_backtest and st.button("Run Analysis", type="primary", use_container_width=True):
                if not tickers:
                    st.sidebar.error("Select at least one ticker")
                    return

                st.session_state["running"] = True
                st.session_state["result"] = None

                with st.spinner(f"Running {strategy_name} on {len(tickers)} tickers..."):
                    try:
                        strategy_cls = STRATEGY_REGISTRY[strategy_name]
                        strategy = strategy_cls(**(params if params else STRATEGY_DEFAULTS[strategy_name]))
                        feed = DataFeed(DEFAULT_DATA_DIR, tickers=tickers,
                                        start_date=str(start), end_date=str(end))

                        if len(feed) == 0:
                            st.error("No data found. Check your tickers and date range.")
                            return

                        engine = DataEngine(feed, strategy)
                        signals = engine.run()

                        from thermaltrend.analytics.compare import run_strategy_analysis
                        result = run_strategy_analysis(signals, feed._data, strategy_name)
                        st.session_state["result"] = result
                        st.session_state["running"] = False
                    except Exception as e:
                        st.error(f"Error: {e}")
                        st.session_state["running"] = False
                        return

    if tab_choice == "Saved Runs":
        page_saved_signals()
    elif tab_choice == "Compare":
        if not tickers:
            st.info("Select tickers in the sidebar to compare strategies.")
        else:
            page_compare(tickers, str(start), str(end))
    elif tab_choice == "Signals":
        if not tickers:
            st.info("Select tickers in the sidebar to generate signals.")
        else:
            page_signals(strategy_name, tickers, str(start), str(end))
    elif tab_choice == "Data Explorer":
        page_data_explorer()
    elif tab_choice == "Compare Tickers":
        page_compare_tickers()
    else:
        result = st.session_state.get("result")
        if result is None:
            st.markdown("### Welcome to Thermaltrend")
            st.markdown(
                "Configure your strategy and tickers in the sidebar, then click **Run Analysis**."
            )

            st.markdown("---")
            st.markdown("#### Quick Start")
            st.markdown("""
            1. Pick a **strategy** from the sidebar (MA Crossover is a good start)
            2. Select **tickers** (AAPL, MSFT, GOOGL are pre-selected)
            3. Set your **date range** (defaults to 2023-2026)
            4. Click **Run Analysis**
            5. Explore results across the tabs: Overview, Trades, Per-Ticker, Regime
            """)

            st.markdown("---")
            st.markdown("#### What Each Strategy Does")
            for name, desc in STRATEGY_DESCRIPTIONS.items():
                st.markdown(f"**{name}**: {desc}")
        else:
            if tab_choice == "Overview":
                page_overview(result)
            elif tab_choice == "Trades":
                page_trades(result)
            elif tab_choice == "Per-Ticker":
                page_per_ticker(result)
            elif tab_choice == "Regime":
                page_regime(result)
            elif tab_choice == "Signals":
                if not tickers:
                    st.info("Select tickers in the sidebar.")
                else:
                    page_signals(strategy_name, tickers, str(start), str(end))


if __name__ == "__main__":
    main()
