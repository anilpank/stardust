"""
Quantify survivorship bias in the S&P 500 universe.

The backtest universe is today's S&P 500 members. Backtesting them
historically ignores every company that was removed (merged, went bankrupt,
shrank) — so history is conditioned on "survived until today". This script
measures how much that inflates returns.

Method: build an equal-weight portfolio from current members whose data
covers a given start year (survivors only), then compare its returns with:

- RSP: the real S&P 500 Equal Weight index (includes member turnover), and
- SPY: the cap-weighted S&P 500.

Because the survivors portfolio uses the same equal-weight scheme as RSP,
the excess return vs RSP is predominantly the survivorship bias.

Usage:
    python survivorship_bias.py                          # all start years
    python survivorship_bias.py --start-year 2010         # single start year
    python survivorship_bias.py --csv survivors.csv      # per-ticker table
    python survivorship_bias.py --include-removed        # add removed-but-traded names
"""

import argparse
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent / "data"
EQUITIES_DIR = DATA_DIR / "equities"
REMOVED_DIR = DATA_DIR / "equities_removed"
BENCHMARK_DIR = DATA_DIR / "benchmarks"

DEFAULT_START_YEARS = [1995, 2000, 2005, 2010, 2015, 2020]


def load_constituents() -> pd.DataFrame:
    csv_path = EQUITIES_DIR / "constituents.csv"
    return pd.read_csv(csv_path, parse_dates=["date_added"])


def load_membership() -> pd.DataFrame:
    return pd.read_csv(
        EQUITIES_DIR / "membership.csv", parse_dates=["date_added", "date_removed"]
    )


def load_monthly_closes(tickers: list[str], include_removed: bool = False) -> dict[str, pd.Series]:
    closes: dict[str, pd.Series] = {}
    for ticker in tickers:
        path = EQUITIES_DIR / f"{ticker}.parquet"
        if include_removed and not path.exists():
            path = REMOVED_DIR / f"{ticker}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        closes[ticker] = df["Close"].resample("ME").last()
    return closes


def fetch_benchmark(symbol: str) -> pd.Series:
    path = BENCHMARK_DIR / f"{symbol}.parquet"
    if path.exists():
        return pd.read_parquet(path)["Close"].resample("ME").last()
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    data = yf.download(symbol, start="1970-01-01", auto_adjust=True, progress=False)
    if data.empty:
        raise RuntimeError(f"no data returned for benchmark {symbol}")
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel("Ticker")
    data.to_parquet(path)
    time.sleep(0.25)
    return data["Close"].resample("ME").last()


def equal_weight_series(
    closes: dict[str, pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
    membership: pd.DataFrame | None = None,
) -> pd.Series:
    """Monthly-rebalanced equal weight total return series across members.

    When membership is provided, each ticker only contributes while it was an
    S&P 500 member (survivorship-bias-corrected universe).
    """
    panel = pd.DataFrame(closes).loc[start:end]
    if membership is not None:
        active = pd.DataFrame(True, index=panel.index, columns=panel.columns)
        for ticker in panel.columns:
            rows = membership[membership["ticker"] == ticker]
            inside = pd.Series(False, index=panel.index)
            for _, r in rows.iterrows():
                mask = panel.index >= r["date_added"]
                if pd.notna(r["date_removed"]):
                    mask &= panel.index <= r["date_removed"]
                inside |= mask
            active[ticker] = inside.values
        panel = panel.where(active)
    rets = panel.pct_change(fill_method=None).dropna(how="all")
    return rets.mean(axis=1, skipna=True)


def cagr(series: pd.Series, end: pd.Timestamp) -> float:
    if series.empty:
        return float("nan")
    total = (1 + series).prod()
    if total <= 0:
        return float("nan")
    start = series.index[0]
    years = max((end - start).days / 365.25, 1e-9)
    return total ** (1 / years) - 1


def analyze_year(year: int, include_removed: bool = False, membership: pd.DataFrame | None = None) -> dict:
    """Compare survivors-only portfolios against benchmarks for one start year."""
    constituents = load_constituents()
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp("2026-08-28")

    members = constituents[constituents["date_added"] <= start]["ticker"].tolist()
    closes = load_monthly_closes(members)
    covered = [t for t, s in closes.items() if not s.loc[start:end].dropna().empty]
    closes = {t: s for t, s in closes.items() if t in covered}

    eq_rebalanced = equal_weight_series(closes, start, end)

    all_members = set()
    expanded_series = None
    if include_removed and membership is not None:
        active = membership[
            (membership["date_added"] <= end)
            & (membership["date_removed"].isna() | (membership["date_removed"] > start))
        ]
        all_members = set(active["ticker"])
        expanded = load_monthly_closes(sorted(all_members), include_removed=True)
        expanded_series = equal_weight_series(expanded, start, end, membership)
        expanded_active = len(expanded)

    first = {}
    last = {}
    for ticker, series in closes.items():
        window = series.loc[start:end].dropna()
        first[ticker] = window.iloc[0]
        last[ticker] = window.iloc[-1]
    base = pd.Series(first)
    total = pd.Series(last) / base
    buyhold_cagr = (total.mean()) ** (1 / max((end - start).days / 365.25, 1e-9)) - 1

    benchmarks = {"SPY": fetch_benchmark("SPY")}
    if year >= 2003:
        try:
            benchmarks["RSP"] = fetch_benchmark("RSP")
        except RuntimeError:
            pass

    bench_rows = {}
    for symbol, series in benchmarks.items():
        window_start = max(start, series.first_valid_index())
        if window_start >= end:
            continue
        window = series.loc[window_start:].pct_change().dropna()
        cagr_bench = cagr(window, end)
        eq_reb = eq_rebalanced.loc[window_start:]
        cagr_survivors = cagr(eq_reb, end)
        expanded_cagr = (
            cagr(expanded_series.loc[window_start:], end)
            if expanded_series is not None
            else None
        )
        bench_rows[symbol] = {
            "survivors_cagr": cagr_survivors,
            "expanded_cagr": expanded_cagr,
            "expanded_active": expanded_active,
            "index_cagr": cagr_bench,
            "gap": cagr_survivors - cagr_bench,
            "window_start": window_start,
        }

    return {
        "year": year,
        "members_added_by_start": len(members),
        "members_with_data": len(covered),
        "buyhold_cagr": buyhold_cagr,
        "benchmarks": bench_rows,
    }


def per_ticker_table(include_before: pd.Timestamp) -> pd.DataFrame:
    """Total return and CAGR for every current member with data before include_before."""
    constituents = load_constituents()
    members = constituents[constituents["date_added"] <= include_before]["ticker"]
    closes = load_monthly_closes(members.tolist())
    start = include_before
    end = pd.Timestamp("2026-08-28")
    rows = []
    for ticker, series in closes.items():
        window = series.loc[start:end].dropna()
        if window.empty:
            continue
        total = window.iloc[-1] / window.iloc[0] - 1
        years = max((end - start).days / 365.25, 1e-9)
        ticker_cagr = (1 + total) ** (1 / years) - 1
        rows.append({"ticker": ticker, "total_return": total, "cagr": ticker_cagr})
    return pd.DataFrame(rows).sort_values("cagr", ascending=False).reset_index(drop=True)


def format_report(results: list[dict], csv_path: Path | None) -> str:
    lines = ["SURVIVORSHIP BIAS REPORT", "========================\n"]
    for r in results:
        header = f"Start year {r['year']}: {r['members_with_data']} current members "
        header += f"with data since {r['year']} (of {r['members_added_by_start']} added by then)"
        lines.append(header)
        lines.append(f"  Survivors equal-weight buy-and-hold CAGR : {r['buyhold_cagr']:6.2%}")
        for symbol, b in r["benchmarks"].items():
            name = {"SPY": "SPY (cap-weighted index)", "RSP": "RSP (equal-weight index, real)"}[symbol]
            lines.append(
                f"  Survivors rebalanced CAGR vs {name:38s}: {b['survivors_cagr']:6.2%} vs "
                f"{b['index_cagr']:6.2%}  -> gap {b['gap']:+.2%}/yr "
                f"(window from {b['window_start'].date()})"
            )
            if b.get("expanded_cagr") is not None:
                lines.append(
                    f"  Expanded PIT universe, {b['expanded_active']} members with data (removed-but-traded included) CAGR : {b['expanded_cagr']:6.2%}  "
                    f"(remaining gap to {symbol}: {b['expanded_cagr'] - b['index_cagr']:+.2%}/yr)"
                )
        lines.append("")

    if csv_path:
        table = per_ticker_table(pd.Timestamp("2010-01-01"))
        table.to_csv(csv_path, index=False)
        lines.append(f"Per-ticker survivors table (2010 onward) -> {csv_path}")
        lines.append(f"  {len(table)} members; top 5:")
        for _, row in table.head(5).iterrows():
            lines.append(f"    {row['ticker']:6s} {row['total_return']:>10.0%} CAGR {row['cagr']:6.2%}")
        lines.append(f"  bottom 5:")
        for _, row in table.tail(5).iterrows():
            lines.append(f"    {row['ticker']:6s} {row['total_return']:>10.0%} CAGR {row['cagr']:6.2%}")
        lines.append(f"  median CAGR: {table['cagr'].median():.2%} (mean {table['cagr'].mean():.2%})")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Quantify S&P 500 survivorship bias")
    parser.add_argument(
        "--start-year", type=int, default=None,
        help="Single start year (default: all of %s)" % DEFAULT_START_YEARS,
    )
    parser.add_argument(
        "--csv", default=None, help="Also write per-ticker survivors table to this CSV",
    )
    parser.add_argument(
        "--include-removed", action="store_true",
        help="Also include removed-but-still-traded tickers (PIT universe)",
    )
    args = parser.parse_args()

    years = [args.start_year] if args.start_year else DEFAULT_START_YEARS
    membership = load_membership() if args.include_removed else None
    results = [analyze_year(y, include_removed=args.include_removed, membership=membership) for y in years]
    print(format_report(results, Path(args.csv) if args.csv else None))


if __name__ == "__main__":
    main()