# AGENTS.md

Guidance for AI coding agents (and humans) working in this repo. Read this at the
start of every task.

## What this is

`stardust` is an event-driven backtesting system for trading strategies on S&P 500
equities. Two top-level packages:

- **`thermaltrend/`** — the core library, CLI scripts, and a Streamlit dashboard:
  download/update Parquet price data, run strategies over an event queue, simulate
  trades, and score/compare them (with a survivorship-bias-safe point-in-time
  universe).
- **`landing/`** — a standalone signals sales/validation landing page: static
  HTML/CSS/JS frontend plus one small Flask backend that records signups and funnel
  events to SQLite. A market experiment, not a website.

## Golden rules

- **Python 3.12+.** Use the existing stack (`pandas`, `numpy`, `pyarrow`,
  `yfinance`, `streamlit`, `plotly`; `flask` + `gunicorn` for `landing/`). Do not
  add a dependency for something the standard library or an existing package
  already does.
- **Do not add code comments** unless the user explicitly asks.
- **Never commit secrets or captured data:** `landing/data/`, `*.db`, `.env`, and
  local logs. The `landing/` `.gitignore` enforces this. Do not commit `.DS_Store`.
- **Run the tests before proposing or committing changes**, and add tests for new
  behavior (see the workflow below).
- **Never commit or push to `main` directly.** Create a short-lived branch, push
  it, and let the user do the actual merge to `main` (keep `main` safe).
- **Only commit or push when the user explicitly asks.** Match the repo's concise
  commit style (`feat:`, `fix:`, `docs:`, `chore:`, `tests:`).
- **Do not run a production deploy yourself.** The user deploys (see below).

## Workflow for every change

1. Branch off `main` with a descriptive name (`feat/…`, `fix/…`, `docs/…`,
   `chore/…`, `tests/…`).
2. Implement the change and **add/update tests** for the new behavior — including
   the cases that would catch a regression. Tests mirror module names (e.g.
   `thermaltrend/core/strategy.py` → `thermaltrend/tests/test_strategy.py`; a new
   `landing/` endpoint → `landing/tests/test_server.py`).
3. Run the relevant suites (from the **repo root**, so `thermaltrend` and
   `landing` import correctly) and make sure they are green:
   ```sh
   python -m pytest thermaltrend/tests/ -m "not slow" -q   # fast, no network
   python -m pytest landing/tests/ -q                       # if you touched landing/
   ```
   Run the full suite (including network) only when your change warrants it:
   `python -m pytest thermaltrend/tests/ -v`.
4. **Refresh the locally running dev instance** so the user can test manually:
   ```sh
   # landing page -> http://127.0.0.1:8000  (admin: admin / secretpass123)
   pkill -f "landing/server.py" 2>/dev/null
   ADMIN_PASSWORD=secretpass123 nohup python landing/server.py >/tmp/stardust-landing.log 2>&1 &

   # dashboard -> http://localhost:8501
   streamlit run thermaltrend/dashboard.py
   ```
   A restart is needed for `server.py` changes; static HTML/CSS/JS in `landing/` is
   read from disk per request.
5. Commit and **push the branch** (never `main`), then hand back the branch name and
   a PR/compare link. **Stop there** — the user reviews and merges into `main`.

## Commands

```sh
python -m pytest thermaltrend/tests/ -m "not slow" -q   # fast unit suite (also runs on commit)
python -m pytest thermaltrend/tests/ -v                 # all tests (hits Yahoo Finance; slow)
python -m pytest landing/tests/ -q                      # landing page tests

streamlit run thermaltrend/dashboard.py                 # dashboard at http://localhost:8501
ADMIN_PASSWORD=secretpass123 python landing/server.py   # landing at http://127.0.0.1:8000
```

Tests marked `@pytest.mark.slow` hit external APIs (Yahoo Finance) and are
deselected by `-m "not slow"`. The `slow` marker is declared in `pyproject.toml`.
`pre-commit install` wires up two local hooks (fast unit suite + landing suite);
run it once per clone. Note the landing hook has no `-m "not slow"`, so
`slow`-marked landing tests do run on commit.

> **zsh gotcha:** an unquoted `$VAR` is **not** word-split, so a ticker list held
> in a variable collapses into a single argument (e.g. `--tickers $T` silently
> runs one bogus ticker). Use command substitution — `--tickers $(python -c "…")`
> — an array, or `echo $T | xargs` when invoking `generate_demo_data.py`.

## Environment variables (`landing/server.py`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | Port |
| `LANDING_DATA_DIR` | `landing/data` | Directory containing `landing.db` |
| `ADMIN_USER` | `admin` | Admin username |
| `ADMIN_PASS_HASH` | — | `salt:hash` scrypt value (production) |
| `ADMIN_PASSWORD` | — | Plaintext fallback (local dev only) |
| `FLASK_DEBUG` | — | `1` enables the Flask debugger |

The admin console is at <http://127.0.0.1:8000/admin> (HTTP Basic auth, user
`admin`). **There is no default password** — set `ADMIN_PASSWORD` (plaintext, local
dev) or `ADMIN_PASS_HASH` (scrypt `salt:hash`, production, from
`landing/gen_admin_password.py`) or login always fails.

## Repository layout

| Path | Purpose |
| --- | --- |
| `thermaltrend/core/` | Event engine (`events.py`) and strategies (`strategy.py`) |
| `thermaltrend/analytics/` | Trade simulation, metrics, regimes, comparison, reporting |
| `thermaltrend/data/` | Committed Parquet price data + membership tables (see below) |
| `thermaltrend/*.py` | CLI/library scripts (download, feed, backtest, compare, signals, walk-forward, …) |
| `thermaltrend/dashboard.py`, `charts.py` | Streamlit dashboard and its Plotly charts |
| `thermaltrend/tests/` | Unit + integration tests (integration marked `slow`) |
| `landing/` | Static sales page + Flask experiment server (see `landing/README.md`) |
| `landing/server.py` | Flask app: page + `/api/signup`, `/api/track`, `/api/demo`, `/api/trades`, `/admin` |
| `landing/gen_admin_password.py` | Generates an `ADMIN_PASS_HASH` for admin Basic auth |
| `landing/tests/` | Landing unit + Flask test-client integration tests |
| `README.md`, `USER_GUIDE.md` | Project docs and dashboard walkthrough |
| `pyproject.toml`, `.pre-commit-config.yaml` | Project metadata + test markers; commit hooks |

## Data & generated artifacts

- **Price data is committed** under `thermaltrend/data/equities/` (one Parquet per
  ticker) and `thermaltrend/data/equities_removed/`; membership is in
  `constituents.csv` / `membership.csv`. Refresh it with `update_data.py`, not by
  hand.
- **Re-downloadable caches are git-ignored** (and safe to delete):
  `thermaltrend/data/benchmarks/`, `thermaltrend/data/membership/`,
  `thermaltrend/data/equities/market_cap.csv`.
- **`landing/data/` is git-ignored** — it holds the runtime SQLite database
  (`landing.db`) and CSV exports. Never commit it.
- `landing/demo_signals.json`, `landing/demo_trades.json` and `landing/tickers.json`
  are **committed** and produced offline by `generate_demo_data.py` /
  `generate_ticker_index.py` (run from the repo root). The server never runs the
  engine at request time. Regenerate → commit → redeploy. See `landing/README.md`.
  - **They go stale the moment price data is refreshed.** After `update_data.py`,
    re-run the generator or the landing page keeps serving old signals — the demo
    files are *not* recomputed automatically.
  - A full refresh **omits `--resume`** (a fresh run overwrites both files);
    `--resume` keeps tickers already present, so it will silently do nothing once
    every ticker is there. Use it only to continue an interrupted run.
  - Use `--workers N` (e.g. `--workers 8`): the whole S&P 500 drops from ~25 min to
    ~2 min. Per-ticker work is independent and the writer preserves key order, so
    output is byte-identical to a sequential run.
  - To keep the diff small, pass tickers in the committed file's key order — the
    committed files start with `DEFAULT_TICKERS`, whereas `tickers.json` is
    alphabetical (506 entries vs 502 with price data).
  - The generator checkpoints after every ticker, so an interrupted run leaves a
    partial file; restore with `git checkout -- landing/demo_signals.json` (and
    `landing/demo_trades.json`).
- **A GitHub Actions job refreshes them daily**
  (`.github/workflows/refresh-demo-data.yml`): 03:00 UTC (or manual dispatch) →
  `update_data.py --strict` → full-universe `generate_demo_data.py` →
  `landing/tests/` gate → opens a `data/refresh-YYYYMMDD` PR only when the content
  changed (ignoring `generated_at`). It commits the JSON but **not** the Parquet.
  Scheduled workflows auto-disable after 60 days without repo activity. See
  `landing/README.md`.

## Conventions

- Strategies live in `thermaltrend/core/strategy.py` and are exposed by name through
  the factory used by `signals.py` / `backtest.py` / `compare_cli.py`; register a new
  one there and test it in `tests/test_strategy.py`.
- CLI modules expose a `run_*` (or equivalent) library function plus a `main()` —
  keep the logic importable, not just runnable.
- The dashboard reads the same library functions; keep `dashboard.py`/`charts.py`
  thin.
- `landing/` is deliberately dependency-light and framework-free on the frontend;
  server logic lives in the single `server.py`, with new routes added explicitly.
- **Prove behaviour-preserving refactors** (e.g. performance work) instead of
  eyeballing them: compare engine output field-by-field ignoring `SignalEvent.id`
  (a random UUID) and treating `NaN == NaN`, and diff generated JSON with
  `generated_at` stripped. Whole-object equality gives false mismatches.
- Parallel/worker code should keep its pool factory injectable (a module-level
  `_pool_factory`) and **default to one worker** so unit tests stay deterministic
  and can exercise the parallel path in-process — see `landing/generate_demo_data.py`
  (`_process_all`). On macOS the default start method is `spawn`, so a
  monkeypatched worker function will *not* reach child processes.
- Prefer small, focused diffs and match the existing style. When in doubt about
  scope, ask before making broad changes.

## Deploying (the user runs these; agents do not)

Production is a systemd `gunicorn` service behind nginx + certbot; the page is
host-agnostic (`Dockerfile` + `requirements.txt`). AWS options, verify/rollback, the
`/etc/gitconfig` https→ssh gotcha, and DB-persistence caveats live in
`landing/README.md`. To ship to the live EC2 instance (stardust.ojblabs.com) the user
pulls the root checkout and re-runs the idempotent provisioner:
```sh
sudo git -C /root/stardust.git pull
sudo bash /root/stardust.git/landing/deploy/provision.sh
```
It sparse-checks-out `landing/` only, refreshes the venv, and restarts
`stardust-landing` — touching nothing of the co-hosted `ojblabs.com` site. Runtime
data (`landing/data/`) is git-ignored, so updates never lose it.

The dashboard (`streamlit run thermaltrend/dashboard.py`) is a local/dev tool; it is
not part of the production landing deployment.

## Privacy / compliance constraints

- The landing page collects personal data (email addresses) plus visitor metadata
  (IP, user agent, referrer). Keep collection minimal; IP is stored only for
  deduplication and abuse prevention.
- No third-party trackers are enabled by default — analytics are first-party
  (the SQLite DB). Any PostHog/Plausible hookup in `landing/app.js` is opt-in and
  must be reflected in the page's compliance/footer language.
