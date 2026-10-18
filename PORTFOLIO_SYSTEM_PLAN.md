# Portfolio System Plan — personal $10–20K trading system

**Status:** Active — Phase A engine ✅, backtest console ✅, control experiment ✅;
**next: `cash_yield` engine fix, then Phase A tuning gate.**
**Date:** October 2026
**Owner:** Anil
**Scope:** A personal, admin-gated trading system for a $10K–$20K account: good
returns with the safety of principal made as structural as markets allow. The
paid-product/subscription angle is deliberately **out of scope** here — this plan
serves the operator only. (Related but separate: `PAID_PRODUCT_PLAN.md`,
`thermaltrend/ROADMAP.md` Phase 4.)

---

## Resume here (next session)

Branch **`feat/spy-only-control`** holds the control experiment (pushed, awaiting
merge to `main`). Everything green: 916 fast + landing tests pass. Pick up with
**step 1 below**, in order:

1. **Add `cash_yield` to the engine** (the one change that un-biases every
   number — see "Known gap" under Phase A). Design:
   - `PortfolioConfig.cash_yield: float = 0.0` (annual; off by default so today's
     numbers stay reproducible), validated `>= 0`; the live config sets it to
     `0.04` to match `RISK_FREE_RATE`.
   - In `manager.py`, accrue daily interest on the **idle cash** balance
     (`cash - invested_notional`, i.e. everything not in a position — which
     includes the principal sleeve) at `cash_yield / 252`, credited to cash each
     step *before* fills. The committed payload should carry both a yield-off
     and a yield-on run so the effect is visible side by side.
   - Tests: interest accrues on idle cash only; zero exposure compounds exactly
     at `cash_yield`; breaker/ratchet still behave; `cash_yield=0` reproduces
     today's numbers byte-for-byte.
   - Re-run the decomposition (`portfolio_backtest.py --no-satellite` vs full vs
     `core_enabled=False`) and confirm the counterfactual: Sharpes climb to
     ~+0.45–0.50, CAGR +2.5–3pp.
2. **Re-generate the console payload** (`landing/generate_portfolio_data.py`)
   and re-state the verdict in the control-experiment table + README note.
3. **Decide the satellite lane:** it adds ~+1.3–1.8pp but only on
   hindsight-picked tickers and cannot stand alone. Either validate it on the
   point-in-time universe (`--universe point_in_time`) or plan to drop it and
   keep core-only until it proves an edge.
4. **Only then** run the Phase A tuning gate (item 3): tune risk knobs on train
   windows, sanity-check walk-forward, freeze `live_config`.

Open questions to settle along the way: whether banked principal should earn a
*historical* T-bill rate (era-accurate) rather than a flat 4%; and whether the
breaker's 26–35% trip rate is still too twitchy once cash yield is in.

---

## 0. Problem & honest framing

Goal: run $10K–$20K through the system and earn meaningful returns while
bounding the loss on **total capital** to roughly **−5% in any 12-month window**.

No market system guarantees principal. What this system delivers instead:

1. **A bounded, measured loss envelope** — computed *before* risking money by
   simulating the whole system over history: "worst historical 12-month outcome
   for the whole account: −X%".
2. **A floor yield while waiting** — the principal sleeve sits in cash/T-bills
   (~4%, the repo's `RISK_FREE_RATE`), so idle capital still pays.
3. **A ratchet** — profits are banked into principal after good periods, so the
   guarantee *strengthens with success*. Worst case stays "some fraction of
   recent profits", never "starting capital".

## 1. Locked design decisions

| Decision | Choice |
|---|---|
| Capital structure | **Two sleeves**: principal (cash/T-bill, untouched by trading rules) + risk sleeve |
| Risk anchor | Largest acceptable 12-month loss on total capital: **~−5%** → exposure band **25–65%**, breaker **−5%** off equity high-water mark |
| Return engine | **Core-satellite**: ≈60% of equity exposure in regime/absolute-momentum-timed SPY; ≈40% in 3–6 individual-stock signals (6-strategy ensemble consensus) |
| Profit ratchet | **Bank 50%** of gains above the last banked high-water mark, monthly, into principal |
| Journal system of record | **Landing SQLite** (append-only, server-timestamped) via guarded admin API; local snapshot generator pulls it each run |
| Start mode | **Shadow/paper first** (4–8 weeks through the full loop), then live with the ratchet |
| Execution | **Manual** — the system proposes, the operator executes at next open and confirms; no broker automation (per `PAID_PRODUCT_PLAN.md` locked decisions) |

### Risk rules (defaults — tuned in Phase A validation, then frozen)

- Per-trade risk ≤ **0.75–1% of total capital** (entry → 2×ATR(14) stop distance).
- Equity exposure band **25–65%** of total capital; rest always in principal.
- Max concurrent slots (3–6, sized by capital), whole shares, min-notional and
  liquidity floors; concentration bounded by slot count (repo has no sector data).
- **Regime gate** (`analytics/regime.py`): no new longs in BEAR.
- **Breaker ladder**: equity −5% off high-water mark → no new entries until the
  re-open rule (recovery buffer) is met, or a **cooldown** (default 21 trading
  days) expires — with entries blocked a cash account cannot recover on its own,
  so a pure level-based reopen would deadlock after one drawdown. Honest caveat:
  overnight gaps can overshoot stops/breakers — the envelope is measured, not
  promised.
- **Monthly ratchet**: `principal += 0.5 × max(0, equity − last_banked_hwm)`.
- **Adherence score**: fills deviating from the proposal (price/size tolerance)
  are flagged; fidelity is a first-class metric.

### Core timing (candidate — compared in Phase A)

Long SPY when absolute momentum (e.g. 126-day return) > 0 **and** regime ≠
BEAR; flat to principal otherwise. Competing rules (dual momentum, MA crossover)
backtested against it before freezing.

## 2. Phases

### Phase A — Portfolio engine + measured safety envelope *(engine ✅ · tuning gate pending)*

1. **`thermaltrend/portfolio/`** (implements ROADMAP Phase 4):
   - `sizing.py` — fixed-fractional risk sizing, notional caps, whole shares,
     slot limits.
   - `risk.py` — exposure band, regime gate, breaker ladder (HWM), liquidity /
     min-notional limits.
   - `manager.py` — **shared-cash system simulator**: sleeves, positions,
     next-day-open fills, 2×ATR stops, slippage cost model (bps), mark-to-market,
     ratchet — the generalization `TradeSimulator` cannot do (it runs each
     ticker independently with a fixed $10K stake).
   - `config.py` — committed config (capital, band, risk %, slots, core/satellite
     weights, breaker, ratchet, slippage); `--config` override.
2. **Validation CLI** `thermaltrend/portfolio_backtest.py` (`run_*` + `main()`):
   runs the whole system over history for $10K and $20K → per-year returns,
   worst 12 months, maxDD, exposure, vs SPY — across regimes (2020, 2022; 2008
   with the point-in-time universe where data allows).
3. **Validation gate (next after `cash_yield`):** tune only risk knobs on train
   windows, sanity-check walk-forward, **freeze** `live_config`. Proceed to Phase
   B only if the measured worst-year envelope is within ~−5% of total capital
   under realistic slippage; otherwise tighten band/breaker and re-run.
4. **Tests:** sizing math, breaker/ratchet transitions, cash/exposure
   invariants (cash ≥ 0, cash ≥ principal target, exposure ≤ band, integer
   shares), CLI smoke tests.

#### Control experiment — where the return comes from (measured 1994-2026) ✅

`portfolio_backtest.py --no-satellite` (and `core_enabled=False` for the
complement) splits the system so the satellite lane and the core sleeve can be
measured on one shared window (rf = 4%, matching the metric). Runs shipped on
branch `feat/spy-only-control` and in the console (5 runs total).

| Run | CAGR | vol | Sharpe | maxDD | worst 12M | avg exp | breaker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Full — MA crossover | 4.9% | 6.5% | 0.15 | −12.4% | −8.1% | 34% | 35% |
| Full — Donchian | 5.4% | 5.9% | 0.25 | −9.2% | −6.8% | 32% | 26% |
| Control — core only | 3.6% | 5.3% | −0.06 | −11.4% | −6.5% | 31% | 26% |
| Control — satellite only | 1.8% | 4.0% | −0.52 | −8.6% | −6.4% | 7% | 42% |
| SPY buy & hold | 10.8% | 18.5% | 0.37 | −55.2% | — | — | — |

Read: the core SPY-timing sleeve is the base engine; satellite selection adds
~+1.3–1.8pp of CAGR on top (but on hindsight-chosen tickers) and cannot stand
alone. All variants are drawdown-reduction vehicles, not return maximisers.

**Known gap that biases this table against the system:** the principal/idle
cash sleeve earns **0%** in the backtest, while the metric judges it against a
4% risk-free rate. Crediting the T-bill sleeve (as the design specifies) lifts
the Sharpe of every variant to ~+0.45–0.50 — above SPY's 0.37 — and raises
CAGR by ~2.5–3pp. The `cash_yield` knob is the next engine fix (see *Resume
here* step 1); until it lands, treat the raw-vs-SPY comparison as incomplete.


### Phase B — Shadow-run loop: journal + snapshot + admin console

6. **Journal tables** in landing SQLite (`journal_fills`, `journal_decisions`,
   append-only) + guarded `POST/GET /api/admin/trade/*` endpoints and CSV export
   (all first-line `_admin_guard()`).
7. **Offline snapshot generator** (mirrors `landing/generate_demo_data.py` —
   the server never runs the engine): pulls the journal via the export endpoint,
   replays fills into positions, marks to market from local Parquet, computes
   sleeves/equity/metrics/adherence, auto-generates paper fills at next-day open
   (live fills entered manually), writes committed
   `landing/portfolio_snapshot.json` + today's proposals.
8. **Admin console** `/admin/portfolio` on the landing stack (Flask + static
   HTML/JS/CSS, same Basic auth; hand-rolled SVG — no chart dependency):
   **backtest viewer done** — run selector, KPI cards, equity-vs-SPY chart,
   exposure/breaker/regime strip, per-year returns, config/gate panel, trades,
   ratchet and breaker logs, fed by committed
   `landing/private/portfolio_data.json` (offline
   `landing/generate_portfolio_data.py`; `private/` is never served statically).
   Still to build here: the live shadow-run half — KPI cards for *today*
   (equity, day P&L, adherence %), positions table with stops, proposals with
   Acted/Skipped + fill form, trade log + CSV export — which needs the journal
   tables from step 6–7 first.
9. **Automation:** local launchd/cron — `update_data.py` → snapshot generator →
   commit. Documented, not forced.

### Phase C — Run it (operator-driven)

- 4–8 weeks **shadow** (`mode: paper|live`): compare live vs backtest, calibrate
  slippage, track adherence.
- Go-live checklist: freeze config, fund sleeves, start monthly ratchet.
- Out of scope: broker automation, email alerts, subscriber pages, sector data.

## 3. Operating experience

- **~5 min/evening:** daily brief states exactly what to do — "BUY 5 XYZ @
  ~open, stop $W, risk $75. SELL ABC. Exposure 46% — GREEN."
- **Next morning:** execute at open; confirm Acted/Skipped + fill price from the
  phone (guarded admin API → landing SQLite).
- **~10 min Sunday:** equity curve, adherence %, sleeve balance; **monthly:**
  bank the ratchet transfer.
- Expectation, stated honestly: principal sleeve ≈ 4% floor + risk-sleeve
  expectancy to be *measured in Phase A* — target is meaningfully above T-bills
  with far less drawdown than buy-and-hold SPY, not multiples.

## 4. Workflow

Branch off `main` (`feat/…`), tests alongside every module (AGENTS.md naming),
suites green (`python -m pytest thermaltrend/tests/ -m "not slow" -q` and
`python -m pytest landing/tests/ -q` when landing is touched), push the branch —
never commit to `main` directly.
