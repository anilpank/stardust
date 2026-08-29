"""Tests for the point-in-time universe (survivorship-bias correction)."""

from pathlib import Path

import pandas as pd
import pytest

from thermaltrend.analytics.compare import run_strategy_analysis
from thermaltrend.analytics.trade_simulator import TradeSimulator
from thermaltrend.core.events import SignalDirection, SignalEvent
from thermaltrend.feed import DataFeed


def _ohlcv(ticker: str, dates, closes) -> Path:
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [100_000] * len(closes),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    return df


def _write_parquet(tmp_path: Path, ticker: str, df: pd.DataFrame) -> Path:
    out = tmp_path / f"{ticker}.parquet"
    df.to_parquet(out)
    return out


def _membership_csv(tmp_path: Path, rows: list[dict]) -> Path:
    df = pd.DataFrame(
        rows,
        columns=["ticker", "name", "date_added", "date_removed", "is_current"],
    )
    path = tmp_path / "membership.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def _make_signal(ticker, date, direction, strategy_id="pit_strat"):
    return SignalEvent(
        timestamp=date,
        ticker=ticker,
        direction=direction,
        strength=0.8,
        strategy_id=strategy_id,
    )


class TestDataFeedPointInTime:
    def test_restricts_bars_to_membership_window(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=60)
        closes = [100.0 + i for i in range(60)]
        _write_parquet(tmp_path, "AAA", _ohlcv("AAA", dates, closes))

        members = _membership_csv(
            tmp_path,
            [
                {"ticker": "AAA", "name": "A", "date_added": "2026-01-01",
                 "date_removed": "2026-02-13", "is_current": False},
            ],
        )

        feed = DataFeed(tmp_path, tickers=["AAA"], membership=members)
        hist = feed.get_ticker_history("AAA")

        assert hist.index.max() <= pd.Timestamp("2026-02-13")
        assert hist.index.min() >= pd.Timestamp("2026-01-01")
        assert len(hist) < 60

    def test_supports_multiple_stints_and_reentry(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=80)
        closes = [100.0 + i for i in range(80)]
        _write_parquet(tmp_path, "BBB", _ohlcv("BBB", dates, closes))

        members = _membership_csv(
            tmp_path,
            [
                {"ticker": "BBB", "name": "B", "date_added": "2026-01-01",
                 "date_removed": "2026-02-13", "is_current": False},
                {"ticker": "BBB", "name": "B", "date_added": "2026-03-01",
                 "date_removed": None, "is_current": True},
            ],
        )

        feed = DataFeed(tmp_path, tickers=["BBB"], membership=members)
        hist = feed.get_ticker_history("BBB")

        kept = hist.index
        gap = kept[(kept > pd.Timestamp("2026-02-13")) & (kept < pd.Timestamp("2026-03-01"))]
        assert gap.empty
        assert (kept <= pd.Timestamp("2026-02-13")).any()
        assert (kept >= pd.Timestamp("2026-03-01")).any()
        dropped_mid_gap = pd.bdate_range("2026-02-16", periods=10)
        assert all(d not in kept for d in dropped_mid_gap)

    def test_non_member_tickers_pass_through(self, tmp_path):
        dates = pd.bdate_range("2026-01-01", periods=30)
        _write_parquet(tmp_path, "SPY", _ohlcv("SPY", dates, [100.0] * 30))
        _write_parquet(tmp_path, "AAA", _ohlcv("AAA", dates, [50.0] * 30))

        members = _membership_csv(
            tmp_path,
            [
                {"ticker": "AAA", "name": "A", "date_added": "2026-01-15",
                 "date_removed": None, "is_current": True},
            ],
        )

        feed = DataFeed(tmp_path, tickers=["SPY", "AAA"], membership=members)

        assert len(feed.get_ticker_history("SPY")) == 30
        assert len(feed.get_ticker_history("AAA")) < 30

    def test_loads_removed_ticker_from_removed_dir(self, tmp_path):
        primary = tmp_path / "equities"
        removed = tmp_path / "equities_removed"
        primary.mkdir()
        removed.mkdir()
        dates = pd.bdate_range("2026-01-01", periods=40)
        _write_parquet(removed, "REM", _ohlcv("REM", dates, [100.0] * 40))

        members = _membership_csv(
            tmp_path,
            [
                {"ticker": "REM", "name": "R", "date_added": "2026-01-01",
                 "date_removed": None, "is_current": True},
            ],
        )

        feed = DataFeed(
            primary, tickers=["REM"],
            membership=members, removed_data_dir=removed,
        )

        assert feed.tickers == ["REM"]
        assert len(feed) == 40

    def test_missing_membership_csv_raises(self, tmp_path):
        with pytest.raises(ValueError):
            DataFeed(tmp_path, membership=tmp_path / "nope.csv")


def _make_price_data(ticker, dates, opens, closes):
    df = pd.DataFrame(
        {
            "Open": opens,
            "High": [o + 2 for o in opens],
            "Low": [o - 1 for o in opens],
            "Close": closes,
            "Volume": [100000] * len(dates),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    df["ticker"] = ticker
    df = df.set_index("ticker", append=True)
    df.index.names = ["date", "ticker"]
    return df


class TestTradeSimulatorMembership:
    def _membership(self, removed: str):
        return pd.DataFrame(
            {
                "ticker": ["TEST"],
                "name": ["Test"],
                "date_added": [pd.Timestamp("2026-01-01")],
                "date_removed": [pd.Timestamp(removed)],
                "is_current": [False],
            }
        )

    def test_force_closes_position_at_universe_exit(self):
        # Removal 2026-02-05 sits between the BUY (day 2) and a late SELL
        # (day 26): the position must be force-closed at the removal date.
        dates = pd.bdate_range("2026-01-01", periods=30)
        opens = [100.0 + i * 0.5 for i in range(30)]
        closes = [100.5 + i * 0.5 for i in range(30)]
        price_data = _make_price_data("TEST", dates, opens, closes)
        membership = self._membership("2026-02-05")

        sim = TradeSimulator(stop_atr_multiple=0.0)
        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[26], SignalDirection.SELL),
        ]
        trades = sim.simulate(signals, price_data, membership=membership)

        assert len(trades) == 1
        trade = trades[0]
        assert trade.exit_reason == "universe_exit"
        assert pd.Timestamp(trade.exit_date) <= pd.Timestamp("2026-02-05")
        assert pd.Timestamp(trade.exit_date) >= pd.Timestamp("2026-01-05")

    def test_open_position_flushed_at_universe_exit_without_sell(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        opens = [100.0 + i * 0.5 for i in range(30)]
        closes = [100.5 + i * 0.5 for i in range(30)]
        price_data = _make_price_data("TEST", dates, opens, closes)
        membership = self._membership("2026-02-05")

        sim = TradeSimulator(stop_atr_multiple=0.0)
        signals = [_make_signal("TEST", dates[2], SignalDirection.BUY)]
        trades = sim.simulate(signals, price_data, membership=membership)

        assert len(trades) == 1
        assert trades[0].exit_reason == "universe_exit"

    def test_no_removals_keeps_data_end_reason(self):
        dates = pd.bdate_range("2026-01-01", periods=20)
        opens = [100.0 + i for i in range(20)]
        closes = [100.5 + i for i in range(20)]
        price_data = _make_price_data("TEST", dates, opens, closes)

        sim = TradeSimulator(stop_atr_multiple=0.0)
        signals = [_make_signal("TEST", dates[2], SignalDirection.BUY)]
        trades = sim.simulate(signals, price_data)

        assert len(trades) == 1
        assert trades[0].exit_reason == "data_end"

    def test_delisting_return_applied_on_data_gap(self):
        dates = pd.bdate_range("2026-01-01", periods=15)
        opens = [100.0 + i for i in range(15)]
        closes = [100.5 + i for i in range(15)]
        price_data = _make_price_data("TEST", dates, opens, closes)
        membership = self._membership("2026-06-01")

        sim = TradeSimulator(stop_atr_multiple=0.0)
        signals = [_make_signal("TEST", dates[2], SignalDirection.BUY)]
        trades = sim.simulate(
            signals, price_data, membership=membership, delisting_return=-0.30
        )

        assert len(trades) == 1
        trade = trades[0]
        assert trade.exit_reason == "delisted"
        assert trade.exit_price == pytest.approx(closes[-1] * 0.70, rel=1e-9)

    def test_run_strategy_analysis_forwards_membership(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        opens = [100.0 + i * 0.5 for i in range(30)]
        closes = [100.5 + i * 0.5 for i in range(30)]
        price_data = _make_price_data("TEST", dates, opens, closes)
        membership = self._membership("2026-02-05")

        signals = [
            _make_signal("TEST", dates[2], SignalDirection.BUY),
            _make_signal("TEST", dates[26], SignalDirection.SELL),
        ]
        result = run_strategy_analysis(
            signals, price_data, "test", membership=membership
        )

        assert any(t.exit_reason == "universe_exit" for t in result["trades"])


def test_run_backtest_point_in_time(tmp_path, monkeypatch):
    import thermaltrend.backtest as bt

    equities = tmp_path / "equities"
    removed = tmp_path / "equities_removed"
    equities.mkdir()
    removed.mkdir()

    dates = pd.bdate_range("2026-01-01", periods=60)
    closes = [100.0] * 10 + [100.0 + 0.5 * i for i in range(50)]
    _write_parquet(removed, "REM", _ohlcv("REM", dates, closes))

    members = _membership_csv(
        equities,
        [
            {"ticker": "REM", "name": "R", "date_added": "2026-01-01",
             "date_removed": "2026-02-13", "is_current": False},
        ],
    )

    monkeypatch.setattr(bt, "DEFAULT_MEMBERSHIP_PATH", str(members))
    monkeypatch.setattr(bt, "DEFAULT_REMOVED_DATA_DIR", str(removed))

    result = bt.run_backtest(
        strategy_name="ma_crossover",
        tickers=["REM"],
        start_date="2026-01-01",
        params={"fast_period": 2, "slow_period": 5},
        data_dir=str(equities),
        universe="point_in_time",
    )

    assert len(result["trades"]) >= 1
    assert any(t.exit_reason == "universe_exit" for t in result["trades"])
    assert all(
        pd.Timestamp(t.exit_date) <= pd.Timestamp("2026-02-13")
        for t in result["trades"]
        if t.exit_reason == "universe_exit"
    )