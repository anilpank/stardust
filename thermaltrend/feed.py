"""
Data feed for loading Parquet files and yielding bars in chronological order.

Usage:
    from thermaltrend.feed import DataFeed

    feed = DataFeed("thermaltrend/data/equities")
    for bar in feed:
        process(bar)
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
    ):
        self.data_dir = Path(data_dir)
        self._tickers = tickers
        self._start_date = pd.Timestamp(start_date) if start_date else None
        self._end_date = pd.Timestamp(end_date) if end_date else None
        self._data = self._load()

    def _load(self) -> pd.DataFrame:
        if self._tickers:
            paths = []
            for t in self._tickers:
                p = self.data_dir / f"{t}.parquet"
                if p.exists():
                    paths.append(p)
        else:
            paths = sorted(self.data_dir.glob("*.parquet"))

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

        return combined

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
    args = parser.parse_args()

    feed = DataFeed(args.data_dir, tickers=args.tickers, start_date=args.start, end_date=args.end)
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
