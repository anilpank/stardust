# Portfolio System Plan — personal $10–20K trading system

**Status:** Active — Phase A engine ✅, backtest console ✅, control experiment ✅,
`cash_yield` T-bill sleeve ✅, satellite lane **shelved** (PIT validation failed),
Phase A tuning gate ✅ — `live_config` **frozen** (core-only), gate **PASS**;
**Phase B (shadow-run loop) complete: journal ✅ → snapshot ✅ → live console ✅
→ automation ✅. Next: Phase C — run the multi-week shadow window (operator-driven).**
**Date:** October 2026
**Owner:** Anil
**Scope:** A personal, admin-gated trading system for a $10K–$20K account: good
returns with the safety of principal made as structural as markets allow. The
paid-product/subscription angle is deliberately **out of scope** here — this plan
serves the operator only. (Related but separate: `PAID_PRODUCT_PLAN.md`,
`thermaltrend/ROADMAP.md` Phase 4.)

---

## Resume here (next session)

**Phase B is complete.** Steps 6–9 live on short-lived branches off `main` and
await your merge (the running log below records each); steps 1–5 are already on
`main`. The frozen core-only config is committed at
`thermaltrend/portfolio/live_config.json` and surfaced as the `live-core-only-10k`
run in the console. Everything green: fast unit suite + landing tests pass.
**Nothing is left to build** — next is **Phase C**: run the multi-week shadow
window (operator-driven; the step-9 automation is documented, not enabled). The
running log:

1. ✅ **`cash_yield` added to the engine** — `PortfolioConfig.cash_yield` (annual,
   default `0.0`, validated `>= 0`) accrues on the **idle cash** balance
   (everything not in a position, including the principal sleeve) at
   `cash_yield / 252`, credited to cash each step *before* fills. The console
   carries yield-off and yield-on (`0.04` = `RISK_FREE_RATE`) runs side by side.
   Tests cover idle-cash-only accrual, exact compounding at zero exposure,
   breaker/ratchet behaviour, and byte-for-byte `cash_yield=0` reproduction.
   Confirmed the counterfactual: Sharpes climb to ~+0.43–0.67 and CAGR
   +2.5–4.4pp (see the table below).
2. ✅ **Console payload regenerated** (`landing/generate_portfolio_data.py`) with
   the control-experiment verdict restated below.
3. ✅ **Satellite lane decision: shelved.** Validated on the full point-in-time
   universe (1206 tickers), it is value-destroying — every PIT variant is worse
   than core-only on CAGR, Sharpe, drawdown, worst-12M and breaker days, and the
   full PIT run *underperforms core-only* because satellite losses trip the
   shared breaker and block the core sleeve. The earlier +1.4pp edge was
   hindsight ticker selection. **Core-only is the base**; the satellite lane
   stays off until a ranked/consensus selector proves an edge on PIT. (Caveat:
   the current selector is unranked "first signal wins", so a ranked selector
   remains untested — not disproven.)
4. ✅ **Phase A tuning gate** — `thermaltrend/portfolio_tune.py` walk-forward
   tunes only the knobs that actually bind for core-only (`core_target_weight`,
   `core_momentum_lookback`, `breaker_pct`; the band, principal sleeve and regime
   thresholds are inert without a satellite lane) on rolling 10y train / 3y test
   windows, selecting **max Calmar subject to a −5% worst-12M envelope**, under
   10 bps slippage + 4% cash yield. All 7 held-out windows met the envelope;
   held-out OOS = 5.4% CAGR / 0.41 Sharpe / −5.9% maxDD / −1.4% worst-12M. Froze
   the most-recent-refit winner to `live_config.json`: `core_target_weight 0.25`,
   `core_momentum_lookback 189`, `breaker_pct 0.04` (full history 6.0% CAGR,
   −3.7% worst-12M, Calmar 0.74) — gate **PASS**. See the tuning-gate table below.
5. ✅ **Live console** (step 8, branch `feat/phase-b-live-console`): the top of
   `/admin/portfolio` is now the daily brief, fed by the committed
   `private/portfolio_snapshot.json` through the guarded
   `GET /api/admin/portfolio/snapshot`: KPI cards (live equity, day P&L, total
   return/CAGR/maxDD, cash, positions, adherence %, model equity), the
   next-session **proposal** with **Acted + fill / Skipped / Deferred** buttons
   (Acted posts a `journal_decisions` row *and* a `journal_fills` row; the others
   a decision only), a positions table, and the live journal fills/decisions
   tables with CSV export links. Frontend logic is a new `live.js` (+ CSS);
   `portfolio.js` (the backtest viewer below the divider) is untouched.
6. ✅ **Journal tables + guarded API** (step 6, branch `feat/phase-b-journal`):
   append-only `journal_fills` / `journal_decisions` in `landing.db`, with
   admin-gated `GET/POST /api/admin/trade/fills`, `GET/POST
   /api/admin/trade/decisions`, and `GET /api/admin/trade/export/<fills|decisions>.csv`.
   Server-side validation (symbol, side, positive shares/price, `YYYY-MM-DD`
   date defaulting to today, lane/source choices; `created_at` stamped server
   side). The server stays the single SQLite writer; step 7's generator reads the
   journal back through the export endpoint. `landing/tests/test_server.py`
   covers auth, validation, persistence, ordering and CSV.
7. ✅ **Snapshot generator** (step 7, branch `feat/phase-b-snapshot`):
   `landing/generate_portfolio_snapshot.py` runs the frozen core-only model
   (`live_config.json`) as what the system *would* do, loads the journal (HTTP
   export endpoints with admin creds from env, or a local dir), replays fills
   into a live ledger marked to market from the committed Parquet, and writes
   committed `landing/private/portfolio_snapshot.json`: `as_of`, `config`,
   `model`/`live` ledgers (equity, metrics, positions, cash), the next-session
   `proposal` (or `null`), `adherence` (decision counts, `acted_pct`, exposure
   match, rejected fills) and a flattened `fills` log. **`paper` mode** treats
   the model's core trades as the paper fills (no journal required); **`live`
   mode** replays the operator's journal fills. Tests:
   `landing/tests/test_generate_portfolio_snapshot.py`.
8. ✅ **Automation** (step 9, branch `feat/phase-b-automation`):
   `landing/shadow_run.py` chains `update_data.py` → the snapshot generator →
   (opt-in) `git commit`, never pushes, and refuses to commit on `main`/`master`
   without `--allow-main`. launchd plist + cron recipes documented in
   `landing/README.md` ("Shadow-run automation"). Tests:
   `landing/tests/test_shadow_run.py` (command build, flag forwarding, commit
   gating, main-branch guard, no-op when unchanged).

Open questions to settle along the way: whether banked principal should earn a
*historical* T-bill rate (era-accurate) rather than a flat 4%; whether the
breaker's trip rate is still too twitchy; and what (if anything) could make a
satellite lane earn its keep.

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
| Return engine | **Core-only**: regime/absolute-momentum-timed SPY. The core-satellite satellite lane (individual-stock signals) is **shelved** — on the point-in-time universe it destroyed value (step 3); off until a ranked/consensus selector proves an edge |
| Profit ratchet | **Bank 50%** of gains above the last banked high-water mark, monthly, into principal |
| Journal system of record | **Landing SQLite** (append-only, server-timestamped) via guarded admin API; local snapshot generator pulls it each run |
| Start mode | **Shadow/paper first** (4–8 weeks through the full loop), then live with the ratchet |
| Execution | **Manual** — the system proposes, the operator executes at next open and confirms; no broker automation (per `PAID_PRODUCT_PLAN.md` locked decisions) |

### Risk rules (frozen in Phase A — see `thermaltrend/portfolio/live_config.json`)

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

### Phase A — Portfolio engine + measured safety envelope *(engine ✅ · tuning gate ✅)*

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
3. **Validation gate ✅** (`thermaltrend/portfolio_tune.py`): tunes only the risk
   knobs that bind for core-only on rolling train windows, sanity-checks
   walk-forward, and **froze** `thermaltrend/portfolio/live_config.json`. The
   measured worst-year envelope is inside ~−5% of total capital under realistic
   (10 bps) slippage, so the gate passes and Phase B can proceed. See the
   tuning-gate table below.
4. **Tests:** sizing math, breaker/ratchet transitions, cash/exposure
   invariants (cash ≥ 0, cash ≥ principal target, exposure ≤ band, integer
   shares), CLI smoke tests.

#### Control experiment — where the return comes from (measured 1994-2026) ✅

`portfolio_backtest.py --no-satellite` (and `core_enabled=False` for the
complement) splits the system so the satellite lane and the core sleeve can be
measured on one shared 1994–2026 window (rf = 4%, matching the metric). Runs
shipped on branch `feat/spy-only-control` and, with the `cash_yield` T-bill
sleeve added, in the console (`landing/generate_portfolio_data.py`, 8 runs).

| Run | cash_yield | CAGR | vol | Sharpe | maxDD | worst 12M | avg exp | breaker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Full — MA crossover | 0% | 4.9% | 6.5% | 0.15 | −12.4% | −8.1% | 34% | 35% |
| Full — MA crossover | **4%** | **8.0%** | 6.6% | **0.59** | −12.1% | **−4.7%** | 35% | 10% |
| Full — Donchian | 0% | 5.4% | 5.9% | 0.25 | −9.2% | −6.8% | 32% | 26% |
| Full — Donchian | **4%** | **8.2%** | 6.1% | **0.67** | −9.8% | **−4.9%** | 33% | 9% |
| Control — core only | 0% | 3.6% | 5.3% | −0.06 | −11.4% | −6.5% | 31% | 26% |
| Control — core only | **4%** | **6.6%** | 5.2% | **0.49** | −8.6% | −5.5% | 31% | 6% |
| Control — satellite only | 0% | 1.8% | 4.0% | −0.52 | −8.6% | −6.4% | 7% | 42% |
| Control — satellite only | **4%** | **6.0%** | 4.6% | **0.43** | −11.3% | −8.5% | 11% | 7% |
| SPY buy & hold | — | 10.8% | 18.5% | 0.37 | −55.2% | — | — | — |

Read: the core SPY-timing sleeve is the base engine; satellite selection adds
~+1.4pp of CAGR on top (but on hindsight-chosen tickers) and cannot stand alone.
All variants are drawdown-reduction vehicles, not return maximisers.

**The `cash_yield` fix (the principal/idle-cash sleeve now earns the T-bill
rate) closes the old bias:** every variant's Sharpe jumps above SPY's 0.37
(0.43–0.67), CAGR rises ~2.5–4.4pp, and the full system's worst 12 months lands
at **−4.7% to −4.9%** — inside the −5% envelope. `cash_yield=0` reproduces the
pre-fix numbers byte-for-byte, so the two columns are directly comparable.

#### Point-in-time satellite validation (step 3) — lane shelved ✅

Re-run over the **full point-in-time universe** (1206 membership tickers,
1994–2026, T-bill on) instead of the hindsight tickers above. The
hindsight-favoured satellite edge disappears and reverses — every PIT variant is
worse than core-only on every axis:

| Run | CAGR | vol | Sharpe | maxDD | worst 12M | avg exp | breaker |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Core only (control)** | **6.6%** | 5.2% | **0.49** | **−8.6%** | **−5.5%** | 31% | **6%** |
| PIT full — MA crossover | 5.5% | 7.1% | 0.23 | −13.7% | −12.2% | 36% | 40% |
| PIT satellite-only — MA crossover | 5.1% | 7.6% | 0.17 | −11.6% | −8.5% | 38% | 32% |
| PIT full — Donchian | 6.2% | 6.6% | 0.33 | −10.3% | −6.1% | 41% | 17% |
| PIT satellite-only — Donchian | 4.8% | 6.7% | 0.14 | −12.3% | −10.8% | 36% | 33% |

The satellite lane is not merely flat but *destructive*: the full PIT runs
**underperform core-only** (5.5% and 6.2% vs 6.6%) because satellite drawdowns
trip the shared breaker (~40% and ~17% of days) and, with entries blocked
account-wide, suppress the core sleeve too. **Decision: drop the satellite lane;
core-only is the base.** It stays off until a *ranked/consensus* selector proves
an edge on the point-in-time universe. Caveat: the current selector is unranked
"first signal wins" among ~1200 tickers, so this refutes the lane *as
implemented*, not the idea categorically.

#### Phase A tuning gate (step 4) — `live_config` frozen ✅

`thermaltrend/portfolio_tune.py`: rolling **10y train / 3y test** windows over
SPY (1993–2026), re-fitting only the knobs that bind for core-only
(`core_target_weight`, `core_momentum_lookback`, `breaker_pct`; the band,
principal sleeve and regime thresholds are inert without a satellite lane). On
each train window it picks the config with **max Calmar subject to worst-12M
≥ −5%**, then scores it held-out. Assumptions: **10 bps** slippage, cash yield
= `RISK_FREE_RATE` (4%). All 7 held-out windows cleared the envelope (no
fallback):

| Test window | Chosen on train | IS Calmar | OOS CAGR | OOS Sharpe | OOS maxDD | OOS worst 12M | OOS Calmar |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-10 → 2008-10 | 0.25 / 126 / 4% | 1.23 | 5.1% | 0.38 | −2.5% | +1.9% | 2.05 |
| 2008-10 → 2011-10 | 0.25 / 126 / 4% | 1.50 | 3.4% | −0.23 | −4.7% | +0.2% | 0.72 |
| 2011-10 → 2014-10 | 0.30 / 126 / 4% | 0.99 | 8.0% | 1.06 | −2.5% | +4.8% | 3.18 |
| 2014-10 → 2017-10 | 0.30 / 126 / 4% | 1.25 | 5.0% | 0.28 | −3.1% | −1.4% | 1.60 |
| 2017-10 → 2020-10 | 0.30 / 126 / 4% | 1.21 | 3.7% | −0.07 | −5.9% | +0.1% | 0.62 |
| 2020-10 → 2023-10 | 0.25 / 126 / 4% | 1.10 | 5.2% | 0.38 | −2.8% | −0.3% | 1.84 |
| 2023-10 → 2026-10 | 0.25 / 189 / 5% | 1.07 | 7.9% | 1.07 | −4.0% | +3.5% | 2.00 |

*Chosen config shown as `core_target_weight / core_momentum_lookback /
breaker_pct`.*

- **Held-out OOS (concatenated):** 5.4% CAGR, 0.41 Sharpe, −5.9% maxDD,
  **−1.4% worst-12M**, Calmar 0.93 — no IS→OOS collapse.
- **Frozen `live_config`** (most-recent-refit winner, full history): 5.9% CAGR,
  −8.1% maxDD, **−3.7% worst-12M**, Calmar 0.73 → inside the −5% envelope.
- **Gate: PASS** — under realistic slippage the worst-year loss is ~−3.7% of
  total capital, well within the −5% target, so Phase B can proceed. The tuned
  config is more conservative than the hand-set default (0.25 vs 0.40 core
  weight), trading ~0.6pp of CAGR for a much shallower envelope.

### Phase B — Shadow-run loop: journal + snapshot + admin console

6. ✅ **Journal tables** in landing SQLite (`journal_fills`, `journal_decisions`,
   append-only) + guarded `POST/GET /api/admin/trade/*` endpoints and CSV export
   (all first-line `_admin_guard()`). Schema and validation as described in
   "Resume here" item 6; the server is the single writer, and the tables are
   created idempotently on startup (`CREATE TABLE IF NOT EXISTS`), so existing
   `landing.db` files upgrade in place.
7. ✅ **Offline snapshot generator** (mirrors `landing/generate_demo_data.py` —
   the server never runs the engine): pulls the journal via the export endpoint,
   replays fills into positions, marks to market from local Parquet, computes
   model/live equity/metrics/adherence, derives paper fills from the model (live
   fills entered manually), writes committed
   `landing/private/portfolio_snapshot.json` + the next-session proposal. The
   live ledger starts at the first fill (no flat pre-history) and reports
   rejected fills (sells with no position, non-positive price/shares).
8. ✅ **Admin console** `/admin/portfolio` on the landing stack (Flask + static
   HTML/JS/CSS, same Basic auth; hand-rolled SVG — no chart dependency):
   **backtest viewer** — run selector, KPI cards, equity-vs-SPY chart,
   exposure/breaker/regime strip, per-year returns, config/gate panel, trades,
   ratchet and breaker logs, fed by committed
   `landing/private/portfolio_data.json` (offline
   `landing/generate_portfolio_data.py`; `private/` is never served statically).
   **Live shadow-run half** — KPI cards for *today* (equity, day P&L, adherence
   %), positions table, next-session proposal with Acted/Skipped + fill form,
   live journal trade log + CSV export — fed by committed
   `landing/private/portfolio_snapshot.json` (offline
   `landing/generate_portfolio_snapshot.py`) and the live `/api/admin/trade/*`
   endpoints. See "Resume here" item 5 for the layout.
9. ✅ **Automation:** `landing/shadow_run.py` chains `update_data.py` → snapshot
   generator → (opt-in) commit, never pushes, and refuses to commit on
   `main`/`master` without `--allow-main`. launchd plist + cron recipes and the
   CLI are documented in `landing/README.md` → "Shadow-run automation".
   Documented, not forced — the operator enables it when the shadow window
   starts.

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
