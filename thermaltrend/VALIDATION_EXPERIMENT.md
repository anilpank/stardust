# Validation Experiment — Sales Landing Page

**Status:** Design (pre-build). No landing page code yet.
**Date:** October 2026
**Owner:** Anil

---

## 1. What we are actually testing

This is a **demand test for a strategy-per-company + signals product**, not a test of
whether the landing page "looks good." The page's only job is to measure whether
self-directed investors want (a) to know which strategy has historically won on a
given stock, and (b) to receive live buy/sell signals for it.

> **Hypothesis:** Active retail investors will trade an email address for the
> winning strategy + current signal on a ticker they care about, and will come
> back for more — because no accessible tool tells them *which* approach works on
> *which* stock and *why*.

If that is false, no amount of page polish saves it.

---

## 2. Positioning

> **For every major US company, show which strategy has actually worked best over
> 1 / 3 / 5 / 10 years — then give live buy/sell signals for that stock.**

Each result is paired with an **out-of-sample (OOS) badge** so we never sell the
same overfitting lie the project spent Phase 3c/3d correcting.

### Standing decisions (locked)

| Decision | Choice |
|---|---|
| Primary CTA | **Email-gated signal** for any ticker the visitor names |
| Ticker scope | Any **S&P 500** ticker |
| OOS badge | **Shown** — `Held up out-of-sample ✅` / `Likely overfit ⚠️` |
| Landing page | **Not built yet** — awaiting further feedback |

---

## 3. Readiness checklist (finalized)

| # | Item | Answer |
|---|------|--------|
| 1 | **Target customer (1 sentence)** | An active retail investor/swing trader who wants to know which approach works on which stock and receive timely buy/sell calls, without building anything. |
| 2 | **Specific problem** | One strategy does not fit all stocks. They don't know whether trend, momentum, or mean-reversion actually works on NVDA vs. KO — and generic signal services never explain *why* or whether the edge is real. |
| 3 | **Headline promise (concrete outcome)** | "Which strategy wins on each stock — and the buy/sell signal it's giving now." Outcome = a per-stock winner + a current signal. |
| 4 | **Understandable without explanation** | The result itself: ticker × {1y, 3y, 5y, 10y} columns showing the winning strategy, an OOS badge, and a green/red current signal. |
| 5 | **CTA = real customer action** | Email-gated **"See the winning strategy + current signal for my ticker."** The captured action is a request to receive signals for a ticker they chose. |
| 6 | **Reach plan** | The matrix is shareable. Post "NVDA's best strategy changed from Mean-Reversion (1y) to Trend (5y)" on r/stocks, r/swingtrading, r/algotrading, StockTwits, X fintwit. Cold-DM people asking "what strategy for X." |
| 7 | **Recording** | Visits + events `ticker_lookup`, `signal_view`, `email_submit`, `return_visit`, each tagged `utm_source`. Return-visit rate is first-class (signals are recurring). |
| 8 | **Continue/change/stop** | Defined in §6, keyed to signal demand + retention. |

---

## 4. Measurement setup

**Events (priority order):**
1. `visit` — unique, with `utm_source`
2. `ticker_lookup` — entered a ticker, saw the matrix
3. `signal_view` — saw the current signal / OOS badge
4. `email_submit` — **the money event** (email + ticker)
5. `return_visit` — same visitor returns within 7 days

**Primary metric:** `email_submit` — a named ticker + working contact.
**Secondary:** `return_visit` rate; lookups per visitor; source spread.

**Tooling:** static page (Vercel/Netlify/GitHub Pages) + PostHog (free) or
Plausible. Intake via the page's email capture → your inbox/Notion. UTM-tag every
link posted. Budget ≈ $0–50; outreach effort ≈ 3–4 hrs.

---

## 5. The OOS badge (trust wedge)

Every recommended strategy carries one of:

- `Held up out-of-sample ✅` — the historical winner also survived walk-forward evaluation.
- `Likely overfit ⚠️` — it won in-sample but decayed out-of-sample; treat with caution.

This is the differentiator versus generic signal services and is mandatory on every
result, including the pre-filled sample tickers on the landing page.

---

## 6. Threshold — decided in advance (2-week run)

Traffic assumption for one Show HN + 3–4 subreddit posts + ~25 DMs: **~500–800 unique visits**.

| Signal | CONTINUE | ITERATE | STOP |
|--------|----------|---------|------|
| `email_submit` | ≥ 40 (≈5%+) | 12–39 | < 12 |
| `return_visit` within 7 days | ≥ 20% of visitors | 8–19% | < 8% |
| Lookups per visitor | ≥ 2 tickers | 1.2–2 | ~1 |
| Source spread | ≥2 channels each ≥15 submits | 1 channel only | — |

**Leading indicators:** matrix screenshot shared/retweeted; `ticker_lookup` → `email_submit` conversion > 8%; qualitative "does this update daily?" questions (strong intent).

**Interpretation rules (fixed now):**
- Traffic but few lookups → the **matrix** isn't compelling. Make it larger / pre-fill famous tickers.
- Lookups but no email → **value/trust** gap. Strengthen the OOS badge, add a sample signal + update cadence.
- Emails but no return visits → a curiosity, not a habit → business model is shaky.
- Requests concentrated in one channel → don't scale yet; find a second audience first.

**STOP** means the audience/problem framing is wrong — try advisors or a dev-tool
angle before abandoning the product.

---

## 7. Open gaps / risks

1. **Backward-looking selection.** "Best strategy over the last N years" is
   data-mined. The OOS badge mitigates but does not eliminate this. Never present a
   historical winner without it.
2. **Signal freshness.** Signals are recurring; the page must state a concrete
   update cadence or retention will collapse.
3. **Compliance.** "Buy/sell signals" edges toward investment advice. Keep language
   as research/education; avoid personalized recommendations. Review before launch.

---

## 8. Still to gather

- Further feedback on the **sales landing page** itself (design, sections, copy) —
  pending from owner.
- Update cadence to advertise (§7.2).

---

## 9. Next steps

1. Receive landing-page design feedback.
2. Build the landing page around the ticker × timeframe × strategy matrix + OOS badge
   + email-gated CTA.
3. Launch 2-week experiment; record events with source tags; judge against §6.
