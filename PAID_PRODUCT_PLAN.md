# Paid Product Plan

**Status:** Planning (pre-build). Gated on the landing-page experiment.
**Date:** October 2026
**Owner:** Anil
**Audience (locked):** B2C retail / swing traders — individuals who make their own
trading decisions.

---

## 0. How to read this document

This is a **conditional** plan, not a committed backlog. The landing page
(`landing/`) is a demand test with pre-declared go/no-go thresholds
(`thermaltrend/VALIDATION_EXPERIMENT.md` §6). **No paid feature gets built until
the experiment clears those thresholds.** Everything below describes what we
would build *if* demand is confirmed, in the order that maximizes learning per
week of effort.

---

## 1. Positioning

> **For a list of tickers you care about, tell me which strategy actually works on
> each one — and give me today's buy/sell call, plus a nudge when it flips.**

The paid product is the **automated, fresh, personalized** version of the free
page's promise. The free page proves the *value of the insight*; the paid product
proves the *value of recurring delivery*.

### Who pays

Self-directed retail / swing traders who already act on signals or indicators and
who would otherwise assemble this themselves. They are paying to avoid building,
maintaining, and babysitting the pipeline — and to be *told when something
changed* rather than having to check.

---

## 2. Where the product stands today

Two working halves that do not yet touch:

- **Research engine + dashboard** — single-ticker, single-strategy backtesting
  engine (survivorship-aware, walk-forward OOS, 6 strategies), a Streamlit
  dashboard, and a set of CLIs.
- **Demand-test landing page** — a static page + tiny Flask backend that records
  an email for a ticker's winning strategy + current signal. It advertises a paid
  product it cannot yet deliver: *"Pro $19/mo — daily signals for up to 10
  tickers, full 1/3/5/10-year breakdown, watchlist alerts"*
  (`landing/index.html:180-189`) — currently **marketing copy only**.

The free "current signal" is derived from a precomputed JSON snapshot
(`landing/demo_signals.json`) served by `landing/server.py`. There is no live
computation, no per-user state, and no delivery.

---

## 3. The paid job-to-be-done and the gap

> "Watch **my** tickers, tell me what each one's strategy is saying **today**, and
> alert me **when it changes**."

| Capability | Why a paid product needs it | Status today |
|---|---|---|
| Accounts / login | Per-user watchlists, entitlements | **Absent** — only a shared admin PIN |
| Billing / subscriptions | Collect monthly revenue, gate features | **Absent** — static HTML card |
| Watchlists (per-user) | The unit of value ("up to 10 tickers") | **Absent** — marketing copy only |
| Live signal engine | Compute today's signal from latest bars | Exists as **offline batch** (`landing/generate_demo_data.py`); no daily path |
| Scheduler / orchestration | Recompute + notify on a schedule | **Absent** — full run is manual (~30 min) |
| Email / notification delivery | Daily digest + "it flipped" alerts | **Absent** — no email code anywhere |
| Signal history / track record | Trust + retention ("was it right?") | **Absent** — `signal_store.py` is local-only Parquet |
| Entitlement gating | Enforce free vs. Pro | **Absent** — client-side `localStorage`, trivially bypassed |
| Cost / realism model | Credibility of reported returns | **Absent** — frictionless fills, no costs |
| Portfolio & risk | Position sizing, exposure limits | **Absent** — ROADMAP "Phase 4" |
| Execution | Auto-trade / broker handoff | **Absent** — explicitly out of scope for now |

The two absences that matter most are **freshness** and **change alerts**. They
are what drive the experiment's `return_visit ≥ 20%` threshold; a signal service
that is neither fresh nor notifying is not a business.

---

## 4. Locked decisions

| Decision | Choice | Reason |
|---|---|---|
| Audience | **B2C retail / swing traders** | Self-directed; values signals + recency; no institutional compliance overhead |
| Trigger to build | **Experiment thresholds met** | Avoid building a product 12 people want (`VALIDATION_EXPERIMENT.md` §6) |
| First paid deliverable | **Scheduled delivery of the free promise** | Cheapest path to first revenue; validates willingness-to-pay |
| Execution / brokerage | **Out of scope (for now)** | Large scope + regulatory surface; revisit only on strong user pull |
| Advice framing | **Research / educational only** | No personalization; keep disclaimers (`VALIDATION_EXPERIMENT.md` §7) |

### Open prerequisite: data licensing

Current market data is `yfinance`/Yahoo — acceptable for personal research and an
anonymous landing page, **not licensed for commercial redistribution**. Before
charging for signals we must move to a licensed vendor (e.g. Polygon, Tiingo,
EODHD, Alpaca, IEX). This is a **cost-of-goods prerequisite**, not a later
optimization, and should be scoped during Phase 1.

---

## 5. The plan

**Sequencing principle:** the first paid product is *not a new engine*. It is
wiring the existing engine to a **clock, an inbox, and a login**. Per-user daily
work is tiny (≤10 tickers) compared with the 30-minute full-universe marketing
build.

### Phase 0 — Experiment & go/no-go *(current)*

Run the 2-week landing-page test and judge against the fixed thresholds. If
cheap, add one event: whether `signal_unlock` predicts a return visit.

- **Gate:** `email_submit ≥ 40`, `return_visit ≥ 20%`, `lookups/visitor ≥ 2`.
- **Output:** go / no-go. **No feature work before this.**

### Phase 1 — Paid MVP: "signals as a service"

Deliver the landing page's promise automatically. This is the single vertical
slice that must work end to end.

- **Live signal engine** — daily job that, per ticker, runs the recommended
  strategy on fresh bars and persists *today's* signal. Reuses `core/engine.py` +
  `analytics/*`; new module (e.g. `thermaltrend/live_signals.py`) writing to a
  small database (start SQLite, move to Postgres when multi-user).
- **Scheduler** — cron or APScheduler: step `update_data.py` → compute watchlist
  signals → enqueue notifications.
- **Accounts** — magic-link email auth (no passwords); per-user watchlist table
  (≤10 tickers).
- **Delivery** — daily digest email + immediate **flip alerts**
  ("NVDA's MA-crossover flipped to SELL"). This is the retention engine.
- **Billing** — Stripe Checkout + subscription webhooks, with **server-side
  entitlement gating** (replace the client-side `localStorage` gate).
- **Signal history page** — persist each day's signal and show the live track
  record next to the backtest.

**Milestone:** a paying stranger picks 10 tickers and gets a useful email every
morning plus an alert when something flips.

### Phase 2 — Trust & depth

Reduce churn and justify the price.

- **Transaction-cost model** in `analytics/trade_simulator.py` (the model needs a
  `CostModel`; today `position_size=10_000` is the only sizing and fills are
  costless).
- **Per-signal "why"** — surface market regime (`analytics/regime.py`), the OOS
  badge, confidence, and the indicator that triggered the call. Directly answers
  the "is the edge real?" objection (`VALIDATION_EXPERIMENT.md` §3 #2).
- **Public track record** — live OOS performance vs. backtest, turning the OOS
  honesty wedge into recurring proof.

### Phase 3 — Portfolio & risk

Highest willingness-to-pay, but only once the signal habit is proven.

- `portfolio/` package: position sizing, sector/exposure caps, drawdown breaker
  (the ROADMAP "Phase 4").
- **Multi-ticker portfolio view** — "if you followed the signals across my 10
  names, here's the equity curve."
- Execution handoff (broker bridge) only if users push for it.

### Phase 4 — Scale & ops

Multi-tenancy hardening, additional channels (Telegram/push), usage analytics,
admin tooling, and tiering (portfolio features at a higher price).

---

## 6. Why this order

| Layer | Build cost | User-visible value | Risk if built first |
|---|---|---|---|
| Live signals + alerts + accounts | Medium | **High** — the core promise | — |
| Costs + explainability | Low | Medium — trust | Premature polish |
| Portfolio / risk | High | High *later* | Large build for unvalidated demand |
| Execution | High | Low now | Regulatory + operational weight |

---

## 7. What we are NOT building (yet)

- Auto-trading / broker execution.
- Mobile apps.
- Multi-asset (crypto, futures, options, FX) — S&P 500 only for now.
- Social / community features.
- Advisor / B2B tooling (a *fallback* audience only if the B2C test fails —
  `VALIDATION_EXPERIMENT.md` §6).
- A general backtesting UI for paying users; the engine already exists for us.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Demand not validated | Phase 0 gate — nothing is built until thresholds clear |
| Compliance ("advice") | Research/educational framing, no personalization, disclaimers |
| Freshness dishonesty | Advertise a concrete cadence and honor it |
| Data licensing | Resolve a licensed vendor before charging (prerequisite) |
| Overfitting / trust | Keep the OOS badge; add a live track record in Phase 2 |
| Per-user compute cost | Daily work is ≤10 tickers/user; full-universe precompute is marketing only |

---

## 9. Success metrics

- **Free → paid conversion** rate and revenue.
- **Activation:** watchlist created and first digest opened.
- **Retention:** weekly signal-open rate; flip-alert engagement; churn.
- **Trust:** track-record page views; qualitative "is this still working?".
- **Cost:** data-vendor + compute cost per paying user.

---

## 10. Next steps

1. **Run the experiment; record events** (current).
2. **Judge against Phase 0 thresholds** → go / no-go.
3. **If go:** scope a licensed data vendor and turn Phase 1 into a detailed
   design doc (data model, routes, scheduler, Stripe flow), broken into issues.
4. **If no-go:** treat the framing as wrong before adding features
   (`VALIDATION_EXPERIMENT.md` §6) — do not start Phase 1.
