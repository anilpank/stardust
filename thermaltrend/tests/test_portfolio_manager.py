"""Tests for thermaltrend.portfolio.manager."""

import pandas as pd
import pytest

from thermaltrend.core.events import SignalDirection, SignalEvent
from thermaltrend.portfolio.config import PortfolioConfig
from thermaltrend.portfolio.manager import (
    CORE_LANE,
    REASON_BEAR_REGIME,
    REASON_BREAKER,
    REASON_CORE_TARGET_SMALL,
    REASON_DATA_END,
    SATELLITE_LANE,
    PortfolioManager,
)
from thermaltrend.portfolio.sizing import (
    REASON_BAND_FULL,
    REASON_BELOW_MIN,
    REASON_PRINCIPAL_FLOOR,
)


def _sig(ticker, date, direction, strategy_id="test_strat"):
    return SignalEvent(
        timestamp=date,
        ticker=ticker,
        direction=direction,
        strength=0.8,
        strategy_id=strategy_id,
    )


def _price_frame(ticker, dates, opens, closes, highs, lows):
    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [100000] * len(dates),
        },
        index=pd.DatetimeIndex(dates, name="date"),
    )
    df["ticker"] = ticker
    return df.set_index("ticker", append=True)


def _flat_frame(ticker, dates, price=100.0, high_pad=1.0, low_pad=1.0):
    return _price_frame(
        ticker,
        dates,
        opens=[price] * len(dates),
        closes=[price] * len(dates),
        highs=[price + high_pad] * len(dates),
        lows=[price - low_pad] * len(dates),
    )


def _cfg(**overrides):
    base = dict(
        capital=10_000.0,
        risk_per_trade=0.01,
        band_min=0.25,
        band_max=0.65,
        max_positions=5,
        max_position_pct=0.20,
        min_position_notional=1.0,
        stop_atr_multiple=2.0,
        atr_lookback=1,
        slippage_bps=0.0,
        principal_start_pct=0.50,
        ratchet_rate=0.5,
        breaker_pct=0.05,
        breaker_reopen_offset=0.01,
        core_ticker="SPY",
        core_enabled=False,
        core_target_weight=0.40,
        core_momentum_lookback=5,
        regime_lookback_days=252,
        regime_bull_threshold=0.10,
        regime_bear_threshold=-0.10,
    )
    base.update(overrides)
    return PortfolioConfig(**base)


def _dates(n=20):
    return pd.bdate_range("2026-01-01", periods=n)


def _rising(ticker, dates, start=100.0, step=0.5):
    opens = [start + step * i for i in range(len(dates))]
    return _price_frame(
        ticker,
        dates,
        opens=opens,
        closes=list(opens),
        highs=[o + 2.0 for o in opens],
        lows=[o - 1.0 for o in opens],
    )


class TestInputValidation:
    def test_empty_price_data_raises(self):
        with pytest.raises(ValueError, match="price_data is empty"):
            PortfolioManager(_cfg()).run([], pd.DataFrame())

    def test_missing_core_ticker_raises(self):
        dates = _dates()
        data = _rising("TEST", dates)
        with pytest.raises(ValueError, match="core ticker"):
            PortfolioManager(_cfg()).run([], data)


class TestBasicCycle:
    def _run(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[10], SignalDirection.SELL),
        ]
        return PortfolioManager(_cfg()).run(signals, data), dates

    def test_buy_sell_cycle(self):
        result, dates = self._run()
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.ticker == "TEST"
        assert trade.exit_reason == "signal"
        assert trade.entry_date == dates[3].to_pydatetime()
        assert trade.exit_date == dates[11].to_pydatetime()
        assert trade.entry_price == pytest.approx(101.5)
        assert trade.exit_price == pytest.approx(105.5)
        assert trade.shares == 16
        assert trade.pnl == pytest.approx(64.0)
        assert trade.stop_price == pytest.approx(95.5)

    def test_equity_and_daily_bookkeeping(self):
        result, dates = self._run()
        assert result.equity_curve.iloc[0] == 10_000.0
        assert result.metrics["equity_end"] == pytest.approx(10_064.0)
        assert result.metrics["trades_total"] == 1
        assert result.metrics["trades_open"] == 0
        assert result.metrics["band_violations"] == 0
        assert len(result.daily) == len(dates)
        for col in (
            "date", "equity", "cash", "invested", "core_invested",
            "satellite_invested", "exposure", "principal_target", "hwm",
            "breaker", "regime",
        ):
            assert col in result.daily.columns

    def test_no_breaker_no_positions_at_end(self):
        result, _ = self._run()
        assert result.breaker_events == []
        assert result.daily["breaker"].sum() == 0

    def test_month_end_ratchet_banks_half_of_gains(self):
        result, dates = self._run()
        assert len(result.ratchet_events) == 1
        event = result.ratchet_events[0]
        assert event["date"] == dates[19]
        assert event["bank"] == pytest.approx(32.0)
        assert event["principal_target"] == pytest.approx(5_032.0)
        assert result.metrics["banked_total"] == pytest.approx(32.0)
        assert result.metrics["principal_end"] == pytest.approx(5_032.0)

    def test_no_signals_produces_no_trades(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        result = PortfolioManager(_cfg()).run([], data)
        assert result.trades == []
        assert result.metrics["equity_end"] == 10_000.0
        assert len(result.daily) == len(dates)

    def test_core_ticker_signals_ignored(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("SPY", dates[2], SignalDirection.BUY),
            _sig("SPY", dates[8], SignalDirection.SELL),
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[8], SignalDirection.SELL),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert [t.ticker for t in result.trades] == ["TEST"]

    def test_duplicate_and_held_buys_skipped(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[6], SignalDirection.BUY),
            _sig("TEST", dates[10], SignalDirection.SELL),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert len(result.trades) == 1

    def test_pending_buy_at_data_end_rejected(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [_sig("TEST", dates[-1], SignalDirection.BUY)]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert result.trades == []
        assert any(r["reason"] == REASON_DATA_END for r in result.rejected)


class TestStops:
    def test_stop_fills_at_gapped_open(self):
        dates = _dates()
        opens = [100.0] * 5 + [60.0] * 15
        closes = list(opens)
        highs = [o + 1.0 for o in opens]
        lows = [o - 1.0 for o in opens]
        lows[5] = 59.0
        data = pd.concat([
            _price_frame("TEST", dates, opens, closes, highs, lows),
            _flat_frame("SPY", dates),
        ])
        signals = [_sig("TEST", dates[1], SignalDirection.BUY)]
        result = PortfolioManager(_cfg()).run(signals, data)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == pytest.approx(60.0)
        assert trade.shares == 20
        assert trade.entry_price == pytest.approx(100.0)
        assert trade.stop_price == pytest.approx(96.0)
        assert trade.pnl == pytest.approx(-800.0)

    def test_stop_not_hit_keeps_position(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[10], SignalDirection.SELL),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert result.trades[0].exit_reason == "signal"


class TestGates:
    def test_bear_regime_rejects_entry(self):
        dates = _dates()
        spy_closes = [100.0] * 5 + [89.0] * 15
        spy = _price_frame(
            "SPY", dates, spy_closes, spy_closes,
            [c + 1.0 for c in spy_closes], [c - 1.0 for c in spy_closes],
        )
        data = pd.concat([_flat_frame("TEST", dates), spy])
        signals = [_sig("TEST", dates[5], SignalDirection.BUY)]
        cfg = _cfg(regime_lookback_days=5)
        result = PortfolioManager(cfg).run(signals, data)

        assert result.trades == []
        bear = [r for r in result.rejected if r["reason"] == REASON_BEAR_REGIME]
        assert len(bear) == 1
        assert bear[0]["ticker"] == "TEST"

    def test_breaker_trip_blocks_entry_then_stays_tripped(self):
        dates = _dates()
        aaa_opens = [100.0] * 5 + [60.0] * 15
        aaa = _price_frame(
            "AAA", dates, aaa_opens, list(aaa_opens),
            [o + 1.0 for o in aaa_opens], [o - 1.0 for o in aaa_opens],
        )
        data = pd.concat([
            aaa,
            _flat_frame("BBBB", dates, price=50.0),
            _flat_frame("SPY", dates),
        ])
        signals = [
            _sig("AAA", dates[1], SignalDirection.BUY),
            _sig("BBBB", dates[5], SignalDirection.BUY),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)

        assert len(result.breaker_events) == 1
        assert result.breaker_events[0]["date"] == dates[5]
        assert result.breaker_events[0]["tripped"] is True
        assert bool(result.daily["breaker"].iloc[5]) is True
        assert bool(result.daily["breaker"].iloc[4]) is False
        assert result.metrics["breaker_days_pct"] == pytest.approx(0.75)

        blocked = [r for r in result.rejected if r["reason"] == REASON_BREAKER]
        assert len(blocked) == 1
        assert blocked[0]["ticker"] == "BBBB"
        assert result.metrics["trades_total"] == 1
        assert result.trades[0].ticker == "AAA"
        assert result.trades[0].exit_reason == "stop_loss"

    def test_breaker_cooldown_reopens_flat_cash_account(self):
        dates = _dates(30)
        aaa_opens = [100.0] * 5 + [60.0] * 25
        aaa = _price_frame(
            "AAA", dates, aaa_opens, list(aaa_opens),
            [o + 1.0 for o in aaa_opens], [o - 1.0 for o in aaa_opens],
        )
        data = pd.concat([
            aaa,
            _flat_frame("BBBB", dates, price=50.0),
            _flat_frame("SPY", dates),
        ])
        signals = [
            _sig("AAA", dates[1], SignalDirection.BUY),
            _sig("BBBB", dates[6], SignalDirection.BUY),
            _sig("BBBB", dates[25], SignalDirection.BUY),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)

        trip_days = [e["date"] for e in result.breaker_events if e["tripped"]]
        reopen_days = [e["date"] for e in result.breaker_events if not e["tripped"]]
        assert trip_days[0] == dates[5]
        assert reopen_days[0] == dates[25]

        early = [r for r in result.rejected if r["ticker"] == "BBBB"]
        assert [r["reason"] for r in early] == [REASON_BREAKER]

        assert len(result.trades) == 2
        assert result.trades[1].ticker == "BBBB"
        assert result.trades[1].entry_date == dates[26].to_pydatetime()
        assert result.trades[1].exit_reason == REASON_DATA_END


class TestSizingRejectionsThroughManager:
    def test_below_min_notional(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        signals = [_sig("TEST", dates[1], SignalDirection.BUY)]
        result = PortfolioManager(_cfg(min_position_notional=5_000.0)).run(
            signals, data
        )
        assert result.trades == []
        assert any(
            r["reason"] == REASON_BELOW_MIN for r in result.rejected
        )

    def test_band_full(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        signals = [_sig("TEST", dates[1], SignalDirection.BUY)]
        result = PortfolioManager(_cfg(band_min=0.0, band_max=0.001)).run(
            signals, data
        )
        assert result.trades == []
        assert any(r["reason"] == REASON_BAND_FULL for r in result.rejected)

    def test_principal_floor(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        signals = [_sig("TEST", dates[1], SignalDirection.BUY)]
        result = PortfolioManager(_cfg(principal_start_pct=0.995)).run(
            signals, data
        )
        assert result.trades == []
        assert any(
            r["reason"] == REASON_PRINCIPAL_FLOOR for r in result.rejected
        )


class TestSlippage:
    def test_entry_and_exit_charged(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[1], SignalDirection.BUY),
            _sig("TEST", dates[5], SignalDirection.SELL),
        ]
        result = PortfolioManager(_cfg(slippage_bps=10.0)).run(signals, data)
        trade = result.trades[0]
        assert trade.entry_price == pytest.approx(100.1)
        assert trade.exit_price == pytest.approx(99.9)
        assert trade.shares == 19
        assert trade.pnl == pytest.approx(-3.8)


class TestCoreLane:
    def _run(self):
        dates = _dates()
        spy_closes = (
            [100.0 + i for i in range(10)]
            + [109.0 - 2.0 * (i - 9) for i in range(10, 20)]
        )
        spy = _price_frame(
            "SPY", dates, spy_closes, list(spy_closes),
            [c + 1.0 for c in spy_closes], [c - 1.0 for c in spy_closes],
        )
        cfg = _cfg(core_enabled=True)
        return PortfolioManager(cfg).run([], spy), dates, spy_closes

    def test_core_buys_on_positive_momentum(self):
        result, dates, _ = self._run()
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.ticker == "SPY"
        assert trade.strategy_id == CORE_LANE
        assert trade.entry_date == dates[6].to_pydatetime()
        assert trade.entry_price == pytest.approx(106.0)
        assert trade.shares == 37

    def test_core_sells_when_momentum_turns_negative(self):
        result, dates, _ = self._run()
        trade = result.trades[0]
        assert trade.exit_reason == "signal"
        assert trade.exit_date == dates[12].to_pydatetime()
        assert trade.exit_price == pytest.approx(103.0)
        assert trade.pnl == pytest.approx(-111.0)
        assert result.metrics["core_trades"] == 1

    def test_core_history_tracks_on_off(self):
        result, dates, _ = self._run()
        assert len(result.core_history) == len(dates)
        assert result.core_history[5]["on"] is True
        assert result.core_history[10]["on"] is True
        assert result.core_history[11]["on"] is False

    def test_core_positions_are_not_satellite(self):
        result, _, _ = self._run()
        assert (result.daily["satellite_invested"] == 0.0).all()
        held = result.daily[result.daily["core_invested"] > 0.0]
        assert len(held) == 6

    def test_no_breaker_during_core_drawdown(self):
        result, _, _ = self._run()
        assert result.breaker_events == []
        assert result.daily["breaker"].sum() == 0

    def test_core_lane_rejects_when_target_too_small(self):
        dates = _dates()
        spy_closes = [100.0 + i for i in range(20)]
        spy = _price_frame(
            "SPY", dates, spy_closes, list(spy_closes),
            [c + 1.0 for c in spy_closes], [c - 1.0 for c in spy_closes],
        )
        cfg = _cfg(core_enabled=True, capital=50.0, core_target_weight=0.4)
        result = PortfolioManager(cfg).run([], spy)
        assert result.trades == []
        assert any(
            r["reason"] == REASON_CORE_TARGET_SMALL for r in result.rejected
        )

    def test_core_signals_never_enter_satellite_lane(self):
        dates = _dates()
        data = pd.concat([_flat_frame("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("SPY", dates[2], SignalDirection.BUY, strategy_id=CORE_LANE),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert result.trades == []
        assert result.rejected == []


class TestMetrics:
    def test_benchmark_and_per_year_present(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[2], SignalDirection.BUY),
            _sig("TEST", dates[10], SignalDirection.SELL),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        metrics = result.metrics
        assert "per_year" in metrics
        assert "2026" in metrics["per_year"]
        bench = metrics["benchmark"]
        assert bench["spy_total_return"] == pytest.approx(0.0)
        assert bench["excess_total_return"] == pytest.approx(
            metrics["total_return_pct"]
        )
        assert metrics["worst_12m"] is None

    def test_benchmark_uses_shared_window_when_spy_starts_late(self):
        """A delayed SPY history must not make the system's CAGR look wrong.

        SPY that begins mid-history gets compared against the system over the
        *same* window, so the two returns share one time base.
        """
        dates = _dates(30)
        spy_dates = dates[10:]
        spy_closes = [100.0 + i for i in range(len(spy_dates))]
        data = pd.concat(
            [
                _rising("TEST", dates),
                _price_frame(
                    "SPY", spy_dates, spy_closes, list(spy_closes),
                    [c + 1.0 for c in spy_closes], [c - 1.0 for c in spy_closes],
                ),
            ]
        )
        result = PortfolioManager(_cfg()).run([], data)
        bench = result.metrics["benchmark"]

        assert bench["window_start"] == str(spy_dates[0].date())
        assert bench["window_end"] == str(spy_dates[-1].date())

        spy_ret = spy_closes[-1] / spy_closes[0] - 1.0
        assert bench["spy_total_return"] == pytest.approx(spy_ret, abs=1e-4)

        eq = result.equity_curve
        sys_ret = float(eq.asof(spy_dates[-1])) / float(eq.asof(spy_dates[0])) - 1.0
        assert bench["system_total_return"] == pytest.approx(sys_ret, abs=1e-4)
        assert bench["excess_total_return"] == pytest.approx(
            sys_ret - spy_ret, abs=1e-4
        )
        years = (spy_dates[-1] - spy_dates[0]).days / 365.25
        assert bench["window_years"] == pytest.approx(years, abs=0.01)
        assert bench["excess_cagr"] == pytest.approx(
            bench["system_cagr"] - bench["spy_cagr"], abs=1e-4
        )

    def test_max_exposure_within_band(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [_sig("TEST", dates[2], SignalDirection.BUY)]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert result.metrics["max_exposure"] <= result.config.band_max + 1e-6
        assert result.metrics["avg_exposure"] >= 0.0


class TestLanes:
    def test_lane_constants(self):
        assert CORE_LANE == "core"
        assert SATELLITE_LANE == "satellite"

    def test_satellite_trade_strategy_id_preserved(self):
        dates = _dates()
        data = pd.concat([_rising("TEST", dates), _flat_frame("SPY", dates)])
        signals = [
            _sig("TEST", dates[2], SignalDirection.BUY, "custom_strat"),
            _sig("TEST", dates[10], SignalDirection.SELL, "custom_strat"),
        ]
        result = PortfolioManager(_cfg()).run(signals, data)
        assert result.trades[0].strategy_id == "custom_strat"
