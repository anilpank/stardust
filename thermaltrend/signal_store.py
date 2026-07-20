"""
Signal persistence store — save, query, and annotate trading signals.

Signals are saved as Parquet files (one per run). Actions (what you traded)
are tracked separately in an actions file.

Usage:
    python thermaltrend/signal_store.py list
    python thermaltrend/signal_store.py show <run_id>
    python thermaltrend/signal_store.py show --from 2026-07-01 --to 2026-07-20
    python thermaltrend/signal_store.py annotate <signal_id> --action acted --notes "Bought at $195"
    python thermaltrend/signal_store.py pending
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

DEFAULT_SIGNALS_DIR = str(Path(__file__).parent / "data" / "signals")
DEFAULT_ACTIONS_DIR = str(Path(__file__).parent / "data" / "actions")


@dataclass
class SignalRun:
    """Metadata for a saved signal run."""

    run_id: str
    strategy_name: str
    tickers: list[str]
    start_date: str | None
    end_date: str | None
    params: dict
    run_timestamp: datetime
    signal_count: int


class SignalStore:
    """Persist and query trading signals.

    Signals are stored as one Parquet file per run in ``signals_dir``.
    Actions (annotations for which signals were acted on) are stored in
    a single ``actions.parquet`` in ``actions_dir``.
    """

    def __init__(
        self,
        signals_dir: str | Path = DEFAULT_SIGNALS_DIR,
        actions_dir: str | Path = DEFAULT_ACTIONS_DIR,
    ):
        self.signals_dir = Path(signals_dir)
        self.actions_dir = Path(actions_dir)
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        self.actions_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        signals: list,
        strategy_name: str,
        tickers: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        params: dict | None = None,
    ) -> str:
        """Save a list of SignalEvents to a new run file.

        Returns the run_id.
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
        run_timestamp = datetime.now()

        rows = []
        for sig in signals:
            rows.append({
                "id": str(sig.id),
                "timestamp": sig.timestamp,
                "ticker": sig.ticker,
                "direction": sig.direction.value,
                "strength": sig.strength,
                "strategy_id": sig.strategy_id,
                "metadata": str(sig.metadata) if sig.metadata else "{}",
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "strategy_name": strategy_name,
                "tickers": ",".join(tickers),
                "start_date": start_date or "",
                "end_date": end_date or "",
                "params": str(params) if params else "{}",
            })

        if not rows:
            # Save empty run for record-keeping
            rows.append({
                "id": "",
                "timestamp": run_timestamp,
                "ticker": "",
                "direction": "",
                "strength": 0.0,
                "strategy_id": strategy_name,
                "metadata": "{}",
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "strategy_name": strategy_name,
                "tickers": ",".join(tickers),
                "start_date": start_date or "",
                "end_date": end_date or "",
                "params": str(params) if params else "{}",
            })

        df = pd.DataFrame(rows)
        filepath = self.signals_dir / f"{run_id}.parquet"
        df.to_parquet(filepath, index=False)

        return run_id

    def list_runs(self) -> list[SignalRun]:
        """List all saved signal runs, newest first."""
        runs = []
        for path in self.signals_dir.glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                if df.empty:
                    continue
                first = df.iloc[0]
                # Filter out empty placeholder rows
                valid = df[df["id"] != ""]
                runs.append(SignalRun(
                    run_id=first["run_id"],
                    strategy_name=first["strategy_name"],
                    tickers=first["tickers"].split(",") if first["tickers"] else [],
                    start_date=first["start_date"] or None,
                    end_date=first["end_date"] or None,
                    params=eval(first["params"]) if first["params"] else {},
                    run_timestamp=first["run_timestamp"],
                    signal_count=len(valid),
                ))
            except Exception:
                continue
        runs.sort(key=lambda r: r.run_timestamp, reverse=True)
        return runs

    def load_run(self, run_id: str) -> pd.DataFrame:
        """Load signals for a specific run."""
        path = self.signals_dir / f"{run_id}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Run not found: {run_id}")
        df = pd.read_parquet(path)
        return df[df["id"] != ""]  # exclude empty placeholders

    def load_all_signals(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        strategy: str | None = None,
        ticker: str | None = None,
        direction: str | None = None,
    ) -> pd.DataFrame:
        """Load and filter signals across all runs."""
        frames = []
        for path in self.signals_dir.glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                df = df[df["id"] != ""]
                if not df.empty:
                    frames.append(df)
            except Exception:
                continue

        if not frames:
            return pd.DataFrame()

        all_signals = pd.concat(frames, ignore_index=True)

        if from_date:
            all_signals = all_signals[all_signals["timestamp"] >= from_date]
        if to_date:
            all_signals = all_signals[all_signals["timestamp"] <= to_date]
        if strategy:
            all_signals = all_signals[all_signals["strategy_name"] == strategy]
        if ticker:
            all_signals = all_signals[all_signals["ticker"] == ticker]
        if direction:
            all_signals = all_signals[all_signals["direction"] == direction.upper()]

        return all_signals.sort_values("timestamp", ascending=False).reset_index(drop=True)

    def _actions_path(self) -> Path:
        return self.actions_dir / "actions.parquet"

    def _load_actions(self) -> pd.DataFrame:
        path = self._actions_path()
        if path.exists():
            return pd.read_parquet(path)
        return pd.DataFrame(columns=["signal_id", "action", "action_date", "notes"])

    def _save_actions(self, df: pd.DataFrame) -> None:
        df.to_parquet(self._actions_path(), index=False)

    def annotate(
        self,
        signal_id: str,
        action: str,
        notes: str = "",
    ) -> None:
        """Annotate a signal with an action (acted/skipped/pending)."""
        if action not in ("acted", "skipped", "pending"):
            raise ValueError(f"action must be 'acted', 'skipped', or 'pending', got '{action}'")

        actions = self._load_actions()
        new_row = pd.DataFrame([{
            "signal_id": signal_id,
            "action": action,
            "action_date": datetime.now(),
            "notes": notes,
        }])

        # Remove existing annotation for this signal, then add new one
        if not actions.empty:
            actions = actions[actions["signal_id"] != signal_id]
        actions = pd.concat([actions, new_row], ignore_index=True)
        self._save_actions(actions)

    def get_actions(self, signal_ids: list[str] | None = None) -> pd.DataFrame:
        """Get actions, optionally filtered by signal IDs."""
        actions = self._load_actions()
        if signal_ids:
            actions = actions[actions["signal_id"].isin(signal_ids)]
        return actions

    def get_pending_signals(self) -> pd.DataFrame:
        """Get signals that have not been annotated yet."""
        all_signals = self.load_all_signals()
        if all_signals.empty:
            return pd.DataFrame()

        actions = self._load_actions()
        acted_ids = set(actions["signal_id"].tolist()) if not actions.empty else set()

        pending = all_signals[~all_signals["id"].isin(acted_ids)]
        return pending.sort_values("timestamp", ascending=False).reset_index(drop=True)


def _format_runs_table(runs: list[SignalRun]) -> str:
    """Format list of runs as a terminal table."""
    if not runs:
        return "No saved runs."

    lines = [
        "",
        "Saved Signal Runs",
        "=" * 95,
        f"{'Run ID':<30s}  {'Strategy':<20s}  {'Tickers':<15s}  {'Signals':>7s}  {'Saved At':<20s}",
        "-" * 95,
    ]
    for run in runs:
        tickers_str = ",".join(run.tickers[:3])
        if len(run.tickers) > 3:
            tickers_str += f"+{len(run.tickers) - 3}"
        lines.append(
            f"{run.run_id:<30s}  {run.strategy_name:<20s}  {tickers_str:<15s}  "
            f"{run.signal_count:>7d}  {run.run_timestamp.strftime('%Y-%m-%d %H:%M'):<20s}"
        )
    lines.append("=" * 95)
    lines.append("")
    return "\n".join(lines)


def _format_signals_table(df: pd.DataFrame) -> str:
    """Format signals DataFrame as a terminal table."""
    if df.empty:
        return "No signals found."

    lines = [
        "",
        f"{'Date':12s} {'Ticker':8s} {'Direction':10s} {'Strength':>10s}  {'Strategy':<20s}  {'ID':<36s}",
        "-" * 110,
    ]
    for _, row in df.iterrows():
        ts = row["timestamp"]
        date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        lines.append(
            f"{date_str:12s} {row['ticker']:8s} {row['direction']:10s} "
            f"{row['strength']:>10.4f}  {row['strategy_name']:<20s}  {row['id']:<36s}"
        )
    lines.append("")
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Signal store — save, query, annotate signals")
    sub = parser.add_subparsers(dest="command")

    # list
    sub.add_parser("list", help="List all saved runs")

    # show
    show_p = sub.add_parser("show", help="Show signals from a run or date range")
    show_p.add_argument("run_id", nargs="?", default=None, help="Run ID to show")
    show_p.add_argument("--from", dest="from_date", default=None, help="Filter from date")
    show_p.add_argument("--to", dest="to_date", default=None, help="Filter to date")
    show_p.add_argument("--strategy", default=None, help="Filter by strategy name")
    show_p.add_argument("--ticker", default=None, help="Filter by ticker")
    show_p.add_argument("--direction", default=None, choices=["BUY", "SELL", "HOLD"])

    # annotate
    ann_p = sub.add_parser("annotate", help="Annotate a signal with an action")
    ann_p.add_argument("signal_id", help="Signal UUID to annotate")
    ann_p.add_argument("--action", required=True, choices=["acted", "skipped", "pending"])
    ann_p.add_argument("--notes", default="", help="Optional notes")

    # pending
    sub.add_parser("pending", help="Show signals not yet acted on")

    args = parser.parse_args()
    store = SignalStore()

    if args.command == "list":
        runs = store.list_runs()
        print(_format_runs_table(runs))

    elif args.command == "show":
        if args.run_id:
            df = store.load_run(args.run_id)
        else:
            df = store.load_all_signals(
                from_date=args.from_date,
                to_date=args.to_date,
                strategy=args.strategy,
                ticker=args.ticker,
                direction=args.direction,
            )
        print(_format_signals_table(df))

    elif args.command == "annotate":
        store.annotate(args.signal_id, args.action, args.notes)
        print(f"Signal {args.signal_id[:8]}... marked as '{args.action}'")

    elif args.command == "pending":
        df = store.get_pending_signals()
        print(_format_signals_table(df))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
