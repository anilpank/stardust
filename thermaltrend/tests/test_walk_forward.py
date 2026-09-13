"""Tests for thermaltrend/walk_forward.py — walk-forward validation."""

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from thermaltrend.analytics.trade_simulator import Trade
from thermaltrend.core.events import SignalDirection
from thermaltrend.walk_forward import (
    _coerce_params,
    _select_best,
    _segment_metrics,
    _trades_in_window,
    build_windows,
    expand_grid,
    format_walk_forward_table,
    run_walk_forward,
)


def _make_parquet(tmp_path, ticker, dates_closes):
    df = pd.DataFrame(
        {"Open": [c for _, c in dates_closes],
         "High": [c + 1 for _, c in dates_closes],
         "Low": [c - 1 for _, c in dates_closes],
         "Close": [c for _, c in dates_closes],
         "Volume": [1000] * len(dates_closes),
         "ticker": ticker},
        index=pd.DatetimeIndex([d for d, _ in dates_closes], name="Date"),
    )
    df.to_parquet(tmp_path / f"{ticker}.parquet")


def _make_wave(n=400, base=100.0):
    """Oscillating drift so trend strategies produce signals."""
    rng = np.random.default_rng(7)
    rets = rng.normal(0.35 / 252, 0.02, n)
    return base * np.cumprod(1 + rets)


def _make_trade(**kw):
    defaults = dict(
        ticker="A",
        entry_date=datetime(2021, 1, 4),
        entry_price=100.0,
        exit_date=datetime(2021, 2, 1),
        exit_price=110.0,
        direction=SignalDirection.BUY,
        pnl=1000.0,
        pnl_pct=0.1,
        holding_days=28,
        exit_reason="signal",
        strategy_id="test",
        shares=100,
        stop_price=0.0,
    )
    defaults.update(kw)
    for key in ("entry_date", "exit_date"):
        if isinstance(defaults[key], str):
            defaults[key] = pd.Timestamp(defaults[key]).to_pydatetime()
    return Trade(**defaults)


class TestExpandGrid:
    def test_single_param(self):
        assert expand_grid({"a": [1, 2]}) == [{"a": 1}, {"a": 2}]

    def test_cartesian_product(self):
        grid = expand_grid({"a": [1, 2], "b": [10, 20]})
        assert len(grid) == 4
        assert {"a": 2, "b": 20} in grid

    def test_tuple_values_accepted(self):
        grid = expand_grid({"a": (1, 2, 3)})
        assert grid == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_empty_grid_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            expand_grid({})

    def test_empty_values_raises(self):
        with pytest.raises(ValueError, match="non-empty list"):
            expand_grid({"a": []})


class TestCoerceParams:
    def test_float_coerced_for_int_param(self):
        assert _coerce_params("ma_crossover", {"slow_period": 200.0}) == {
            "slow_period": 200
        }

    def test_string_coerced(self):
        assert _coerce_params("ma_crossover", {"fast_period": "10"}) == {
            "fast_period": 10
        }

    def test_int_coerced_for_float_param(self):
        assert _coerce_params("rsi_mean_reversion", {"oversold": 30}) == {
            "oversold": 30.0
        }

    def test_string_coerced_for_float_param(self):
        assert _coerce_params("rsi_mean_reversion", {"oversold": "30"}) == {
            "oversold": 30.0
        }

    def test_non_integer_float_raises(self):
        with pytest.raises(ValueError, match="expects an int"):
            _coerce_params("ma_crossover", {"slow_period": 199.5})

    def test_unknown_param_raises(self):
        with pytest.raises(ValueError, match="Unknown parameter"):
            _coerce_params("ma_crossover", {"bogus": 10})

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            _coerce_params("nonexistent", {})


class TestBuildWindows:
    def test_non_overlapping_test_windows(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=10)
        starts = [w["test_start"] for w in windows]
        assert all(b > a for b, a in zip(starts[1:], starts))

    def test_chronological_order(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=100, test_days=50,
                                step_days=50, warmup_days=10)
        assert windows == sorted(windows, key=lambda w: w["test_start"])

    def test_warmup_extends_before_train(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=30)
        w = windows[-1]  # freshest window — never clipped by data start
        warm_idx = dates.get_loc(w["warmup_start"])
        train_idx = dates.get_loc(w["train_start"])
        assert train_idx - warm_idx == 30

    def test_warmup_clipped_at_data_start(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=30)
        w = windows[0]
        warm_idx = dates.get_loc(w["warmup_start"])
        assert warm_idx == 0

    def test_train_ends_day_before_test(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=10)
        w = windows[0]
        assert dates.get_loc(w["test_start"]) - dates.get_loc(w["train_end"]) == 1

    def test_last_window_uses_freshest_data(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=10)
        assert windows[-1]["test_end"] == dates[-1]

    def test_insufficient_data_returns_empty(self):
        dates = pd.bdate_range("2020-01-01", periods=50)
        windows = build_windows(dates, train_days=60, test_days=40,
                                step_days=40, warmup_days=10)
        assert windows == []

    def test_empty_dates(self):
        assert build_windows([]) == []


class TestSegmentMetrics:
    def test_filters_by_exit_date_inclusive(self):
        t1 = _make_trade(exit_date="2021-01-15")
        t2 = _make_trade(exit_date="2021-02-15")
        inside = _trades_in_window([t1, t2], "2021-01-01", "2021-02-01")
        assert inside == [t1]

    def test_boundary_trade_goes_to_later_segment(self):
        border = _make_trade(exit_date="2021-01-31")
        assert _trades_in_window([border], "2021-01-31", "2021-02-28") == [border]

    def test_segment_metrics_completed_only(self):
        m = _segment_metrics([_make_trade(exit_date="2021-01-15")],
                             "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 1
        assert m["total_trades"] == 1

    def test_empty_segment(self):
        m = _segment_metrics([], "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 0
        assert m["sharpe"] == 0.0

    def test_data_end_not_completed(self):
        t = _make_trade(exit_date="2021-01-15", exit_reason="data_end")
        m = _segment_metrics([t], "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 0
        assert m["trades_open"] == 1

    def test_sparse_segment_risk_metrics_nan(self):
        t = _make_trade(exit_date="2021-01-15")  # 1 trade < 5-trade threshold
        m = _segment_metrics([t], "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 1
        assert m["sharpe"] != m["sharpe"]  # NaN
        assert m["sortino"] != m["sortino"]
        assert m["calmar"] != m["calmar"]

    def test_four_trade_segment_risk_metrics_nan(self):
        trades = [
            _make_trade(ticker=f"T{i}", entry_date="2021-01-04",
                        exit_date="2021-01-15")
            for i in range(4)
        ]
        m = _segment_metrics(trades, "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 4
        assert m["sharpe"] != m["sharpe"]

    def test_sufficient_trades_keep_risk_metrics(self):
        trades = [
            _make_trade(ticker=f"T{i}", entry_date="2021-01-04",
                        exit_date="2021-01-15", pnl=100.0 * (1 if i % 2 == 0 else -1))
            for i in range(8)
        ]
        m = _segment_metrics(trades, "2021-01-01", "2021-01-31")
        assert m["trades_completed"] == 8
        assert m["sharpe"] == m["sharpe"]  # not NaN


class TestSelectBest:
    def test_picks_highest_sharpe(self):
        pairs = [
            ({"a": 1}, {"sharpe": 0.5, "trades_completed": 4}),
            ({"a": 2}, {"sharpe": 1.2, "trades_completed": 6}),
        ]
        assert _select_best(pairs, "sharpe")[0] == {"a": 2}

    def test_skips_combos_with_no_train_trades(self):
        pairs = [
            ({"a": 1}, {"sharpe": 3.0, "trades_completed": 0}),
            ({"a": 2}, {"sharpe": 0.4, "trades_completed": 5}),
        ]
        assert _select_best(pairs, "sharpe")[0] == {"a": 2}

    def test_max_drawdown_picks_least_negative(self):
        pairs = [
            ({"a": 1}, {"max_drawdown": -0.4, "trades_completed": 4}),
            ({"a": 2}, {"max_drawdown": -0.1, "trades_completed": 6}),
        ]
        assert _select_best(pairs, "max_drawdown")[0] == {"a": 2}

    def test_missing_metric_defaults_zero_tie_keeps_first(self):
        pairs = [
            ({"a": 1}, {"trades_completed": 4}),
            ({"a": 2}, {"trades_completed": 6}),
        ]
        assert _select_best(pairs, "sharpe")[0] == {"a": 1}


class TestRunWalkForward:
    def _setup(self, tmp_path, n=400):
        dates = pd.bdate_range("2020-01-01", periods=n)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _make_wave(n=n))))
        return dates

    def test_single_combo_deterministic_params(self, tmp_path):
        self._setup(tmp_path)
        result = run_walk_forward(
            "ma_crossover", ["TEST"], start_date="2020-01-01",
            grid={"fast_period": [5], "slow_period": [20]},
            train_days=60, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(tmp_path),
        )

        assert not result.rows.empty
        assert result.strategy_name == "ma_crossover"
        assert {"is_sharpe", "oos_sharpe", "oos_cagr", "decay_sharpe"}.issubset(
            result.rows.columns
        )
        expected = {"fast_period": 5, "slow_period": 20}
        assert result.params_by_window == [expected] * len(result.rows)
        assert result.metrics["total_trades"] >= 0
        assert len(result.equity_curve) > 0

    def test_multi_combo_choices_come_from_grid(self, tmp_path):
        self._setup(tmp_path)
        grid = {"fast_period": [3, 10], "slow_period": [15, 30]}
        result = run_walk_forward(
            "ma_crossover", ["TEST"],
            grid=grid,
            train_days=80, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(tmp_path),
        )
        for combo in result.params_by_window:
            assert combo["fast_period"] in (3, 10)
            assert combo["slow_period"] in (15, 30)

    def test_oos_trades_are_inside_test_windows(self, tmp_path):
        self._setup(tmp_path)
        result = run_walk_forward(
            "ma_crossover", ["TEST"],
            grid={"fast_period": [5], "slow_period": [20]},
            train_days=60, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(tmp_path),
        )
        segments = [
            (w["test_start"], w["test_end"]) for w in result.windows
        ]
        for t in result.trades:
            exit_dt = pd.Timestamp(t.exit_date)
            assert any(start <= exit_dt <= end for start, end in segments)

    def test_requires_grid(self, tmp_path):
        self._setup(tmp_path, n=100)
        with pytest.raises(ValueError, match="grid is required"):
            run_walk_forward("ma_crossover", ["TEST"], data_dir=str(tmp_path))

    def test_unknown_strategy_raises(self, tmp_path):
        self._setup(tmp_path, n=100)
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_walk_forward("nope", ["TEST"], grid={"a": [1]},
                             data_dir=str(tmp_path))

    def test_unknown_objective_raises(self, tmp_path):
        self._setup(tmp_path, n=100)
        with pytest.raises(ValueError, match="Unknown objective"):
            run_walk_forward("ma_crossover", ["TEST"], grid={"fast_period": [5]},
                             objective="nope", data_dir=str(tmp_path))

    def test_not_enough_data_raises(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=30)
        _make_parquet(tmp_path, "TEST", list(zip(dates, [100.0] * 30)))
        with pytest.raises(ValueError, match="Not enough data"):
            run_walk_forward(
                "ma_crossover", ["TEST"], grid={"fast_period": [5]},
                train_days=60, test_days=40, data_dir=str(tmp_path),
            )

    def test_no_data_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No data found"):
            run_walk_forward("ma_crossover", ["NOPE"], grid={"fast_period": [5]},
                             data_dir=str(tmp_path))

    def test_unknown_grid_param_raises(self, tmp_path):
        self._setup(tmp_path, n=100)
        with pytest.raises(ValueError, match="Unknown parameter"):
            run_walk_forward("ma_crossover", ["TEST"], grid={"bogus": [1, 2]},
                             data_dir=str(tmp_path))

    def test_point_in_time_universe(self, tmp_path, monkeypatch):
        import thermaltrend.walk_forward as wf

        equities = tmp_path / "equities"
        removed = tmp_path / "equities_removed"
        equities.mkdir()
        removed.mkdir()

        dates = pd.bdate_range("2020-01-01", periods=400)
        closes = _make_wave(n=400)
        _make_parquet(equities, "TEST", list(zip(dates, closes)))

        members = tmp_path / "membership.csv"
        pd.DataFrame(
            {
                "ticker": ["TEST"],
                "name": ["T"],
                "date_added": [pd.Timestamp("2020-03-02")],
                "date_removed": [pd.Timestamp("2021-06-01")],
                "is_current": [False],
            }
        ).to_csv(members, index=False)

        monkeypatch.setattr(wf, "DEFAULT_MEMBERSHIP_PATH", str(members))
        monkeypatch.setattr(wf, "DEFAULT_REMOVED_DATA_DIR", str(removed))

        result = run_walk_forward(
            "ma_crossover", ["TEST"], start_date="2020-01-01",
            grid={"fast_period": [5], "slow_period": [20]},
            train_days=60, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(equities), universe="point_in_time",
        )

        assert not result.rows.empty
        assert all(
            pd.Timestamp(t.exit_date) <= pd.Timestamp("2021-06-01")
            for t in result.trades
            if t.exit_reason == "universe_exit"
        )

    def test_warns_when_warmup_too_short(self, tmp_path):
        self._setup(tmp_path)
        with pytest.warns(UserWarning, match="warmup_days"):
            run_walk_forward(
                "ma_crossover", ["TEST"],
                grid={"fast_period": [5], "slow_period": [20]},
                train_days=100, test_days=40, step_days=40,
                warmup_days=5, data_dir=str(tmp_path),
            )

    def test_decay_dict_structure(self, tmp_path):
        self._setup(tmp_path)
        result = run_walk_forward(
            "ma_crossover", ["TEST"],
            grid={"fast_period": [5], "slow_period": [20]},
            train_days=60, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(tmp_path),
        )
        for metric in ("cagr", "sharpe", "sortino", "calmar"):
            assert metric in result.decay
            entry = result.decay[metric]
            assert list(entry) == ["is_mean", "oos_mean", "decay"]
            assert all(isinstance(v, float) for v in entry.values())
        assert result.decay["cagr"]["decay"] == (
            result.decay["cagr"]["oos_mean"] - result.decay["cagr"]["is_mean"]
        )


class TestFormatTable:
    def test_returns_rows_and_summary(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=400)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _make_wave(n=400))))
        result = run_walk_forward(
            "ma_crossover", ["TEST"], grid={"fast_period": [5], "slow_period": [20]},
            train_days=60, test_days=40, step_days=40, warmup_days=40,
            data_dir=str(tmp_path),
        )
        out = format_walk_forward_table(result)
        assert "Walk-Forward: ma_crossover" in out
        assert "Overall OOS:" in out
        assert "IS→OOS decay" in out
        assert "→" in out

    def test_sparse_metrics_rendered_as_na(self):
        from thermaltrend.walk_forward import WalkForwardResult

        rows = pd.DataFrame(
            [
                {
                    "window": "2020-01-01 → 2020-03-01",
                    "best_params": "fast_period=5, slow_period=20",
                    "is_sharpe": float("nan"),
                    "oos_sharpe": float("nan"),
                    "oos_cagr": 0.01,
                    "oos_max_drawdown": -0.05,
                    "oos_total_trades": 2,
                }
            ]
        )
        result = WalkForwardResult(
            strategy_name="ma_crossover",
            tickers=["TEST"],
            grid=[{"fast_period": 5, "slow_period": 20}],
            windows=[{"test_start": pd.Timestamp("2020-01-01"),
                      "test_end": pd.Timestamp("2020-03-01")}],
            rows=rows,
            metrics={"cagr": 0.01, "sharpe": float("nan"), "sortino": float("nan"),
                     "max_drawdown": -0.05, "calmar": float("nan"),
                     "trades_completed": 2},
            equity_curve=pd.Series([100000.0]),
            trades=[],
            params_by_window=[{"fast_period": 5, "slow_period": 20}],
            decay={"sharpe": {"is_mean": float("nan"), "oos_mean": float("nan"),
                              "decay": float("nan")}},
        )
        out = format_walk_forward_table(result)
        assert out.count("N/A") >= 3
        assert "N/A = fewer than 5 completed trades" in out


class TestCLI:
    def test_cli_runs_end_to_end(self, tmp_path):
        dates = pd.bdate_range("2020-01-01", periods=400)
        _make_parquet(tmp_path, "TEST", list(zip(dates, _make_wave(n=400))))
        out_json = tmp_path / "wf.json"

        script = Path(__file__).resolve().parent.parent / "walk_forward.py"
        import subprocess
        import sys

        payload = json.dumps({"fast_period": [5], "slow_period": [20]})
        result = subprocess.run(
            [
                sys.executable, str(script),
                "--strategy", "ma_crossover", "--tickers", "TEST",
                "--start", "2020-01-01",
                "--grid", payload,
                "--train-days", "60", "--test-days", "40",
                "--step-days", "40", "--warmup-days", "40",
                "--data-dir", str(tmp_path),
                "--output", str(out_json),
            ],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "Walk-Forward: ma_crossover" in result.stdout
        assert out_json.exists()