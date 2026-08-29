"""
Data feed for loading Parquet files and yielding bars in chronological order.

Usage:
    from thermaltrend.feed import DataFeed

    feed = DataFeed("thermaltrend/data/equities")
    for bar in feed:
        process(bar)

Point-in-time universe:
    pass ``membership`` (path to membership.csv or a DataFrame) plus
    ``removed_data_dir`` to restrict every index member to the windows it was
    actually in the S&P 500 (fixing survivorship bias). Tickers that are not
    in the membership table (e.g. the SPY benchmark) pass through unfiltered.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


@dataclass
class Bar:
    ticker: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class DataFeed:
    def __init__(
        self,
        data_dir: str | Path,
        tickers: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        membership: str | Path | pd.DataFrame | None = None,
        removed_data_dir: str | Path | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.removed_data_dir = Path(removed_data_dir) if removed_data_dir else None
        self._tickers = tickers
        self._start_date = pd.Timestamp(start_date) if start_date else None
        self._end_date = pd.Timestamp(end_date) if end_date else None
        self._membership = self._load_membership(membership)
        self._data = self._load()

    @staticmethod
    def _load_membership(
        membership: str | Path | pd.DataFrame | None,
    ) -> pd.DataFrame | None:
        if membership is None:
            return None
        if isinstance(membership, pd.DataFrame):
            return membership
        path = Path(membership)
        if not path.exists():
            raise ValueError(f"membership CSV not found: {path}")
        return pd.read_csv(path, parse_dates=["date_added", "date_removed"])

    def _bases(self) -> list[Path]:
        bases = [self.data_dir]
        if self.removed_data_dir:
            bases.append(self.removed_data_dir)
        return bases

    def _resolve_paths(self) -> list[Path]:
        if self._tickers:
            paths = []
            for t in self._tickers:
                for base in self._bases():
                    p = base / f"{t}.parquet"
                    if p.exists():
                        paths.append(p)
                        break
        else:
            paths = sorted(self.data_dir.glob("*.parquet"))
            if self.removed_data_dir and self.removed_data_dir.exists():
                existing = {p.stem for p in paths}
                paths += sorted(
                    p
                    for p in self.removed_data_dir.glob("*.parquet")
                    if p.stem not in existing
                )
        return paths

    def _load(self) -> pd.DataFrame:
        paths = self._resolve_paths()

        if not paths:
            return pd.DataFrame()

        frames = []
        for path in paths:
            df = pd.read_parquet(path)
            df["ticker"] = path.stem
            frames.append(df)

        combined = pd.concat(frames)
        combined = combined.set_index("ticker", append=True)
        combined.index.names = ["date", "ticker"]
        combined.sort_index(inplace=True)

        if self._start_date:
            combined = combined[
                combined.index.get_level_values("date") >= self._start_date
            ]
        if self._end_date:
            combined = combined[
                combined.index.get_level_values("date") <= self._end_date
            ]

        if self._membership is not None:
            combined = self._filter_membership(combined)

        return combined

    def _filter_membership(self, combined: pd.DataFrame) -> pd.DataFrame:
        """Keep only bars that fall inside a membership stint for each ticker.

        Tickers absent from the membership table (e.g. the SPY benchmark ETF)
        are kept untouched. Afterwards the frame is re-sorted by (date, ticker).
        """
        if combined.empty:
            return combined

        m = self._membership
        frames = []
        for ticker, grp in combined.groupby(level="ticker", sort=False):
            rows = m[m["ticker"] == ticker]
            if rows.empty:
                frames.append(grp)
                continue
            dates = grp.index.get_level_values("date")
            keep = pd.Series(False, index=dates)
            for _, r in rows.iterrows():
                mask = dates >= r["date_added"]
                if pd.notna(r["date_removed"]):
                    mask &= dates <= r["date_removed"]
                keep |= mask
            frames.append(grp[keep.values])

        filtered = pd.concat(frames)
        filtered.sort_index(inplace=True)
        return filtered

    def __iter__(self):
        for (date, ticker), row in self._data.iterrows():
            yield Bar(
                ticker=ticker,
                date=date.to_pydatetime(),
                open=row["Open"],
                high=row["High"],
                low=row["Low"],
                close=row["Close"],
                volume=int(row["Volume"]),
            )

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        n_tickers = len(self.tickers)
        n_dates = len(self.dates)
        return f"DataFeed({n_tickers} tickers, {n_dates} dates, {len(self)} bars)"

    def get_bars_for_date(self, date: str | datetime) -> list[Bar]:
        date = pd.Timestamp(date)
        try:
            date_bars = self._data.loc[date]
        except KeyError:
            return []
        return [
            Bar(
                ticker=ticker,
                date=date.to_pydatetime(),
                open=row["Open"],
                high=row["High"],
                low=row["Low"],
                close=row["Close"],
                volume=int(row["Volume"]),
            )
            for ticker, row in date_bars.iterrows()
        ]

    def get_ticker_history(self, ticker: str) -> pd.DataFrame:
        try:
            return self._data.xs(ticker, level="ticker")
        except KeyError:
            return pd.DataFrame()

    @property
    def tickers(self) -> list[str]:
        if self._data.empty:
            return []
        return self._data.index.get_level_values("ticker").unique().tolist()

    @property
    def dates(self) -> list[datetime]:
        if self._data.empty:
            return []
        return (
            self._data.index.get_level_values("date").unique().sort_values().tolist()
        )

    @property
    def membership(self) -> pd.DataFrame | None:
        """The loaded point-in-time membership table (or None)."""
        return self._membership

    @property
    def shape(self) -> tuple[int, int]:
        return self._data.shape


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Inspect the data feed")
    parser.add_argument(
        "--tickers", nargs="+", default=None, help="Filter to specific tickers"
    )
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--date", default=None, help="Show all bars for a specific date"
    )
    parser.add_argument(
        "--ticker-history", default=None, help="Show full history for a ticker"
    )
    parser.add_argument("--head", type=int, default=None, help="Show first N bars")
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).parent / "data" / "equities"),
        help="Directory containing Parquet files",
    )
    parser.add_argument(
        "--removed-data-dir",
        default=None,
        help="Directory with Parquet files for removed S&P 500 members",
    )
    parser.add_argument(
        "--membership",
        default=None,
        help="membership.csv for point-in-time filtering",
    )
    args = parser.parse_args()

    feed = DataFeed(
        args.data_dir,
        tickers=args.tickers,
        start_date=args.start,
        end_date=args.end,
        membership=args.membership,
        removed_data_dir=args.removed_data_dir,
    )
    print(feed)

    if args.date:
        bars = feed.get_bars_for_date(args.date)
        print(f"\nBars for {args.date} ({len(bars)} tickers):")
        for bar in bars:
            print(f"  {bar.ticker:6s}  O={bar.open:>10.2f}  H={bar.high:>10.2f}  L={bar.low:>10.2f}  C={bar.close:>10.2f}  V={bar.volume:>12,}")

    if args.ticker_history:
        df = feed.get_ticker_history(args.ticker_history)
        if df.empty:
            print(f"\nNo data for {args.ticker_history}")
        else:
            print(f"\n{args.ticker_history} history ({len(df)} rows):")
            print(df.to_string())

    if args.head:
        print(f"\nFirst {args.head} bars:")
        for i, bar in enumerate(feed):
            if i >= args.head:
                break
            print(f"  {bar.date.date()}  {bar.ticker:6s}  C={bar.close:>10.2f}  V={bar.volume:>12,}")


if __name__ == "__main__":
    main()
