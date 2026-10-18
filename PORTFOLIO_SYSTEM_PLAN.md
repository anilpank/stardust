# Portfolio System Plan — personal $10–20K trading system

**Status:** Active — building (Phase A).
**Date:** October 2026
**Owner:** Anil
**Scope:** A personal, admin-gated trading system for a $10K–$20K account: good
returns with the safety of principal made as structural as markets allow. The
paid-product/subscription angle is deliberately **out of scope** here — this plan
serves the operator only. (Related but separate: `PAID_PRODUCT_PLAN.md`,
`thermaltrend/ROADMAP.md` Phase 4.)

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

### Phase A — Portfolio engine + measured safety envelope *(current)*

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
3. **Validation gate:** tune only risk knobs on train windows, sanity-check
   walk-forward, **freeze** `live_config`. Proceed to Phase B only if the
   measured worst-year envelope is within ~−5% of total capital under realistic
   slippage; otherwise tighten band/breaker and re-run.
4. **Tests:** sizing math, breaker/ratchet transitions, cash/exposure
   invariants (cash ≥ 0, cash ≥ principal target, exposure ≤ band, integer
   shares), CLI smoke tests.

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
