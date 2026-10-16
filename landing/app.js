/* stardust landing page — funnel tracking + interactive prototype.
 *
 * Records the experiment's events (visit, ticker_lookup, signal_view,
 * cta_click, email_submit, return_visit, wtp_*) to the first-party backend.
 * Third-party analytics is optional — fill the two keys below to also load
 * PostHog / Plausible. Leave blank for first-party only.
 */
(function () {
  "use strict";

  var ANALYTICS = {
    posthogKey: "",        // e.g. "phc_xxx" -> enables PostHog autocapture
    plausibleDomain: "",   // e.g. "signals.example.com" -> enables Plausible
  };

  /* ---------- identity + traffic source ---------- */
  var params = new URLSearchParams(window.location.search);

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "v-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  var visitorId;
  var isReturn = false;
  try {
    visitorId = localStorage.getItem("sd_vid");
    if (!visitorId) {
      visitorId = uuid();
      localStorage.setItem("sd_vid", visitorId);
    } else {
      isReturn = true; // we've seen this browser before
    }
  } catch (e) {
    visitorId = uuid();
  }

  var source =
    params.get("utm_source") ||
    params.get("ref") ||
    (document.referrer ? "referral" : "direct");
  var context = {
    source: source,
    utm_medium: params.get("utm_medium") || "",
    utm_campaign: params.get("utm_campaign") || "",
    referrer: document.referrer || "",
    visitor_id: visitorId,
    is_return: isReturn,
    path: window.location.pathname,
  };

  function track(event, extra) {
    var payload = Object.assign({ event: event }, context, extra || {});
    try {
      var body = JSON.stringify(payload);
      var url = "/api/track";
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      } else {
        fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true });
      }
    } catch (e) { /* never let analytics break the page */ }
    if (window.posthog && ANALYTICS.posthogKey) window.posthog.capture(event, extra || {});
    if (window.plausible && ANALYTICS.plausibleDomain) window.plausible(event, { props: extra || {} });
  }

  // On arrival (or on return) fire the visit / return_visit event.
  track(isReturn ? "return_visit" : "visit");

  /* ---------- optional third-party analytics ---------- */
  if (ANALYTICS.posthogKey) {
    var ph = document.createElement("script");
    ph.src = "https://us-assets.i.posthog.com/static/array.js";
    ph.onload = function () {
      window.posthog.init(ANALYTICS.posthogKey, { api_host: "https://us.i.posthog.com" });
    };
    document.head.appendChild(ph);
  }
  if (ANALYTICS.plausibleDomain) {
    var pl = document.createElement("script");
    pl.defer = true;
    pl.setAttribute("data-domain", ANALYTICS.plausibleDomain);
    pl.src = "https://plausible.io/js/script.js";
    document.head.appendChild(pl);
  }

  /* ---------- helpers ---------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function normTicker(v) { return (v || "").trim().toUpperCase().replace(/[^A-Z.\-]/g, "").slice(0, 6); }
  function validEmail(v) { return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((v || "").trim()); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtPct(v) { return (v == null || isNaN(v)) ? "—" : (v * 100).toFixed(0) + "%"; }
  function fmtSignedPct(v) {
    if (v == null || isNaN(v)) return "—";
    var p = Number(v) * 100;
    return (p > 0 ? "+" : "") + p.toFixed(1) + "%";
  }
  function fmtNum(v, dp) {
    return (v == null || isNaN(v)) ? "—" : Number(v).toFixed(dp == null ? 2 : dp);
  }
  function fmtMoney(v) {
    if (v == null || isNaN(v)) return "—";
    var n = Number(v);
    return (n < 0 ? "-$" : "$") + Math.abs(n).toLocaleString([], { maximumFractionDigits: 0 });
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
  }
  function debounce(fn, wait) {
    var t;
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }

  var WINDOW_LABELS = { "1y": "1 year", "3y": "3 years", "5y": "5 years", "10y": "10 years" };

  /* ---------- plain-English explanations (strategy + honesty badge) ---------- */
  var STRATEGY_INFO = {
    "MA 50/200": {
      kind: "Trend-following",
      blurb: "Buys when the 50-day moving average crosses above the 200-day one (a " +
        "“golden cross”) and sells on the reverse (“death cross”) — the textbook " +
        "long-term trend rule.",
    },
    "Donchian 20/10": {
      kind: "Breakout",
      blurb: "Buys when price breaks out to a new 20-day high and exits when it " +
        "falls to a 10-day low. It’s the classic trading-channel breakout, made " +
        "famous by the original Turtles.",
    },
    "RSI 14": {
      kind: "Mean-reversion",
      blurb: "Buys when the 14-day RSI climbs back up out of oversold territory " +
        "(below 30) and sells when it drops back from overbought (above 70). It " +
        "bets that sharp moves snap back.",
    },
    "ATR Trail 20/14/3": {
      kind: "Volatility stop",
      blurb: "Enters on a 20-day breakout, then rides the move with a trailing stop " +
        "3× ATR(14) below the highest high since entry. The stop only ratchets up, " +
        "so it protects gains as the trend runs.",
    },
    "Dual Mom 126d": {
      kind: "Dual momentum",
      blurb: "Buys when the stock’s 6-month (126-day) return is positive AND better " +
        "than the S&P 500’s; sells when either condition fails. It combines absolute " +
        "and relative momentum.",
    },
    "Factor 126d": {
      kind: "Factor scoring",
      blurb: "Blends momentum, low volatility, trend and short-term reversal into a " +
        "single 0–1 score, and holds while the score is strong and improving — a " +
        "rules-based multi-factor model.",
    },
  };
  var OOS_INFO = {
    "true": {
      label: "Held up out-of-sample",
      tone: "good",
      blurb: "The winning strategy was re-tested on a slice of recent data it was " +
        "never tuned on — and it still made money with a ≥50% win rate. That’s the " +
        "good sign: the edge looks real, not just curve-fit to the past.",
    },
    "false": {
      label: "Likely overfit",
      tone: "bad",
      blurb: "On held-out data the strategy’s edge disappeared (a negative return or " +
        "a <50% win rate). That’s the classic warning sign: great in the backtest, " +
        "unreliable in live trading, because it was tuned to the past.",
    },
    "null": {
      label: "Out-of-sample check unavailable",
      tone: "",
      blurb: "There wasn’t enough recent data to re-test this window, so no " +
        "out-of-sample judgement is made either way.",
    },
  };

  /* ---------- company-name search (offline ticker index) ----------
   * /tickers.json is a tiny ticker -> company-name index generated from the
   * S&P 500 membership table. It lets a visitor type a company name ("Apple",
   * "Bank of America") instead of a ticker; we resolve it locally, so the
   * landing page stays self-contained (no backend lookup, no network).
   */
  var COMPANY_STOP = ["an", "and", "co", "company", "corp", "corporation", "group", "holding",
    "holdings", "inc", "incorporated", "limited", "llc", "ltd", "of", "plc", "sa", "the"];
  var COMPANY_INDEX = [];    // { ticker, name, norm, tokens }
  var COMPANY_TICKERS = {};  // ticker -> true

  function normName(s) {
    return String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  }
  function nameTokens(s) {
    return normName(s).split(" ").filter(function (t) { return t && COMPANY_STOP.indexOf(t) < 0; });
  }
  // Ticker canonical form for matching, e.g. "brk.b" -> "BRK-B".
  function canonTicker(v) { return normTicker(v).replace(/\./g, "-"); }

  function loadTickerIndex() {
    fetch("/tickers.json")
      .then(function (r) { return r.ok ? r.json() : { tickers: [] }; })
      .then(function (j) {
        COMPANY_INDEX = (j.tickers || []).map(function (e) {
          var ticker = String(e.ticker || "").toUpperCase();
          var name = e.name || ticker;
          return { ticker: ticker, name: name, norm: normName(name), tokens: nameTokens(name) };
        });
        COMPANY_INDEX.forEach(function (e) { COMPANY_TICKERS[e.ticker] = true; });
      })
      .catch(function () {});
  }

  function companyRank(qn, qt, rec) {
    if (!qt.length) return 0;
    if (qn === rec.norm) return 6;
    if (qt.every(function (t) { return rec.tokens.indexOf(t) >= 0; })) return 5;
    if (rec.norm.indexOf(qn) >= 0) return 4;
    if (qn.indexOf(rec.norm) >= 0) return 3;
    for (var i = 0; i < qt.length; i++) {
      if (qt[i].length >= 3 && rec.norm.indexOf(qt[i]) >= 0) return 1;
    }
    return 0;
  }

  function searchCompanies(query, limit) {
    var raw = (query || "").trim();
    if (!raw || !COMPANY_INDEX.length) return [];
    var qn = normName(raw), qt = nameTokens(raw), sym = canonTicker(raw);
    var out = [];
    for (var i = 0; i < COMPANY_INDEX.length; i++) {
      var rec = COMPANY_INDEX[i];
      var rank = 0;
      if (sym && sym === rec.ticker) rank = 7;
      var nr = companyRank(qn, qt, rec);
      if (nr > rank) rank = nr;
      if (rank > 0) out.push({ ticker: rec.ticker, name: rec.name, rank: rank, len: rec.norm.length });
    }
    out.sort(function (a, b) {
      if (b.rank !== a.rank) return b.rank - a.rank;
      if (a.len !== b.len) return a.len - b.len;
      return a.ticker < b.ticker ? -1 : a.ticker > b.ticker ? 1 : 0;
    });
    return out.slice(0, limit || 8);
  }

  // Turn whatever the visitor typed into a ticker: exact symbol wins, then a
  // confident company-name match, then a bare symbol, else "" (email still captured).
  function resolveTicker(raw) {
    var v = (raw || "").trim();
    if (!v) return "";
    var sym = canonTicker(v);
    if (COMPANY_TICKERS[sym]) return sym;
    var hits = searchCompanies(v, 1);
    if (hits.length && hits[0].rank >= 4) return hits[0].ticker;
    return /\s/.test(v) ? "" : sym;
  }

  /* ---------- ticker autocomplete dropdown ---------- */
  function attachCombo(input) {
    var box = document.createElement("div");
    box.className = "combo-suggest";
    box.setAttribute("role", "listbox");
    box.hidden = true;
    document.body.appendChild(box);

    var hits = [], active = -1;

    function position() {
      var r = input.getBoundingClientRect();
      box.style.left = r.left + "px";
      box.style.top = (r.bottom + 4) + "px";
      box.style.width = r.width + "px";
    }
    function close() { box.hidden = true; box.innerHTML = ""; hits = []; active = -1; }
    function choose(i) {
      if (i < 0 || i >= hits.length) return;
      input.value = hits[i].ticker;
      close();
      if (input.id === "demo-ticker") lookup(input.value);
    }
    function render() {
      if (!hits.length) { close(); return; }
      position();
      box.hidden = false;
      box.innerHTML = hits.map(function (h, i) {
        return '<div class="combo-item' + (i === active ? " active" : "") + '" data-i="' + i +
          '" role="option"><span class="combo-ticker">' + esc(h.ticker) +
          '</span><span class="combo-name">' + esc(h.name) + "</span></div>";
      }).join("");
    }
    function update() {
      active = -1;
      var q = input.value.trim();
      if (!q) { close(); return; }
      hits = searchCompanies(q, 8);
      render();
    }

    input.addEventListener("input", debounce(update, 80));
    input.addEventListener("focus", update);
    input.addEventListener("keydown", function (e) {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % hits.length; render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + hits.length) % hits.length; render(); }
      else if (e.key === "Enter") { if (active >= 0) { e.preventDefault(); choose(active); } }
      else if (e.key === "Escape" || e.key === "Tab") { close(); }
    });
    box.addEventListener("mousedown", function (e) {
      var item = e.target.closest ? e.target.closest(".combo-item") : null;
      if (item) { e.preventDefault(); choose(parseInt(item.getAttribute("data-i"), 10)); }
    });
    input.addEventListener("blur", function () { setTimeout(close, 140); });
    input.addEventListener("scroll", position);
    window.addEventListener("scroll", function () { if (!box.hidden) position(); }, true);
    window.addEventListener("resize", function () { if (!box.hidden) position(); });
  }

  /* ---------- interactive prototype ---------- */
  var demoResult = $("#demo-result");
  var demoAsof = document.getElementById("demo-asof");
  var demoKnown = [];
  var lastTicker = "";      // last ticker looked up in the demo
  var pendingTicker = "";   // ticker a visitor tried to unlock

  // Signal gate: the historical strategy fit is free for everyone; the live
  // buy/sell call is the one actionable thing, so it takes an email. Unlocking
  // is client-side only (persisted per browser) — far less to build than
  // accounts/auth, and enough to make the "email for the signal" test honest.
  var SIGNALS_UNLOCKED = false;
  try { SIGNALS_UNLOCKED = localStorage.getItem("sd_unlocked") === "1"; } catch (e) {}
  function signalsUnlocked() { return SIGNALS_UNLOCKED; }
  function unlockSignals() {
    SIGNALS_UNLOCKED = true;
    try { localStorage.setItem("sd_unlocked", "1"); } catch (e) {}
  }

  function setAnalysisDate(iso) {
    if (!demoAsof) return;
    var d = fmtDate(iso);
    if (!d) { demoAsof.hidden = true; return; }
    demoAsof.textContent = "Analysis updated " + d;
    demoAsof.hidden = false;
  }

  /* ---------- trade-level drill-down (pre-generated, fetched lazily) ---------- */
  var TRADE_COLUMNS = ["Entry Date", "Entry Price", "Exit Date", "Exit Price",
    "P&L ($)", "P&L (%)", "Holding (days)", "Exit Reason"];
  var TRADE_VIEW = [
    ["Entry Date", "Entry"], ["Exit Date", "Exit"], ["P&L ($)", "P&L ($)"],
    ["P&L (%)", "P&L (%)"], ["Holding (days)", "Days"], ["Exit Reason", "Exit reason"],
  ];
  var tradesCache = {};  // ticker -> { columns, windows } | null

  function loadTrades(ticker, cb) {
    if (tradesCache[ticker] !== undefined) { cb(tradesCache[ticker]); return; }
    fetch("/api/trades?ticker=" + encodeURIComponent(ticker))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        tradesCache[ticker] = j && j.available
          ? { windows: j.windows || {}, columns: (j.columns && j.columns.length) ? j.columns : TRADE_COLUMNS }
          : null;
        cb(tradesCache[ticker]);
      })
      .catch(function () { tradesCache[ticker] = null; cb(null); });
  }

  function renderTrades(box, rows, columns) {
    if (!rows || !rows.length) {
      box.innerHTML = '<p class="note muted">Trade-by-trade detail isn’t available for this window yet.</p>';
      return;
    }
    var idx = {};
    columns.forEach(function (c, i) { idx[c] = i; });
    function at(rec, name) { var i = idx[name]; return i == null ? "" : rec[i]; }

    var head = "<tr>" + TRADE_VIEW.map(function (v) {
      var num = v[0] === "P&L ($)" || v[0] === "P&L (%)" || v[0] === "Holding (days)";
      return '<th' + (num ? ' class="num"' : "") + ">" + esc(v[1]) + "</th>";
    }).join("") + "</tr>";

    var body = rows.map(function (rec) {
      var pnl = Number(at(rec, "P&L ($)"));
      var pnlPct = Number(at(rec, "P&L (%)"));
      var cls = pnl >= 0 ? "pos" : "neg";
      return "<tr>" +
        "<td>" + esc(at(rec, "Entry Date")) + "</td>" +
        "<td>" + esc(at(rec, "Exit Date")) + "</td>" +
        '<td class="num ' + cls + '">' + (pnl >= 0 ? "+" : "") + fmtMoney(pnl) + "</td>" +
        '<td class="num ' + cls + '">' + (pnlPct >= 0 ? "+" : "") + pnlPct.toFixed(1) + "%</td>" +
        '<td class="num">' + esc(at(rec, "Holding (days)")) + "</td>" +
        "<td>" + esc(at(rec, "Exit Reason")) + "</td>" +
        "</tr>";
    }).join("");

    box.innerHTML =
      '<div class="trades-head">Actual trades <span class="muted">(' + rows.length +
      " completed)</span></div>" +
      '<div class="trades-scroll"><table class="trades"><thead>' + head +
      "</thead><tbody>" + body + "</tbody></table></div>";
  }

  function callBadge(dir) {
    var d = (dir || "").toUpperCase();
    return d ? '<span class="win-call signal-' + esc(d) + '">now ' + esc(d) + "</span>" : "";
  }

  // The headline call is a consensus: a strict majority of the windows' own
  // calls. No majority -> "Mixed"; nothing computed -> no signal.
  function consensusPill(sig) {
    sig = sig || {};
    var total = sig.total || 0;
    if (!total) return '<span class="signal-pill signal-HOLD">No signal</span>';
    var agree = sig.agree || 0;
    if (sig.consensus && sig.direction) {
      var d = String(sig.direction).toUpperCase();
      return '<span class="signal-pill signal-' + esc(d) + '">' + esc(d) + " · " +
        agree + " of " + total + " windows agree</span>";
    }
    return '<span class="signal-pill signal-MIXED">Mixed · no clear signal</span>';
  }

  function winCard(wlabel, c) {
    var label = WINDOW_LABELS[wlabel] || wlabel;
    if (!c || !c.strategy) {
      return '<div class="win-card"><div class="win-win">' + esc(label) +
             '</div><div class="win-name muted">—</div></div>';
    }
    var info = STRATEGY_INFO[c.strategy];
    var oosInfo = OOS_INFO[String(c.oos)] || OOS_INFO["null"];
    var oos = "";
    if (c.oos === true) oos = '<span class="oos oos-true" title="' + esc(oosInfo.blurb) + '">✓ Held up out-of-sample</span>';
    else if (c.oos === false) oos = '<span class="oos oos-false" title="' + esc(oosInfo.blurb) + '">⚠ Likely overfit</span>';
    var call = (signalsUnlocked() && c.call && c.call.direction) ? callBadge(c.call.direction) : "";

    var stats = [
      ["Win rate", fmtPct(c.win_rate)],
      ["Total P&amp;L", fmtMoney(c.total_pnl)],
      ["CAGR", fmtSignedPct(c.cagr)],
      ["Sharpe", fmtNum(c.sharpe)],
      ["Trades", c.trades_completed == null ? "—" : String(c.trades_completed)],
    ].map(function (r) {
      return "<div><dt>" + r[0] + "</dt><dd>" + esc(r[1]) + "</dd></div>";
    }).join("");

    var about = info
      ? '<p class="about"><strong>' + esc(c.strategy) + '</strong> — ' + esc(info.kind) +
        ': ' + esc(info.blurb) + '</p>'
      : '';
    var oosNote = '<p class="about oos-note ' + esc(oosInfo.tone) + '"><strong>' +
      esc(oosInfo.label) + '.</strong> ' + esc(oosInfo.blurb) + '</p>';

    return '<div class="win-card">' +
      '<button type="button" class="win-toggle" aria-expanded="false" data-window="' + esc(wlabel) + '">' +
        '<span class="win-win">' + esc(label) + '</span>' +
        '<span class="win-name" title="' + esc(info ? info.kind + ": " + info.blurb : "") + '">' +
          esc(c.strategy) + '</span>' +
        '<span class="win-metric">Win rate ' + fmtPct(c.win_rate) + ' · ' +
          esc(c.trades_completed || 0) + ' trades</span>' +
        call +
        oos +
        '<span class="win-caret" aria-hidden="true">▾</span>' +
      '</button>' +
      '<div class="win-detail" hidden>' +
        '<dl class="win-stats">' + stats + '</dl>' +
        (c.low_confidence ? '<p class="note warn">Low confidence — few trades.</p>' : '') +
        about +
        oosNote +
        '<div class="win-trades" data-trades="' + esc(wlabel) + '"></div>' +
      '</div>' +
    '</div>';
  }

  function renderResult(resp) {
    var t = resp.data;
    setAnalysisDate(resp.generated_at);
    if (!t) {
      demoResult.innerHTML =
        '<p class="note warn">We don’t have a cached report for <strong>' + esc(resp.ticker) +
        '</strong> yet — coverage is expanding. Enter your email below and we’ll send this ticker’s ' +
        'report as soon as it’s ready.</p>';
      return;
    }

    var sig = t.signal || {};
    var html = "";
    html += '<div class="result-head">';
    html += '<span class="name">' + esc(t.name || resp.ticker) + '</span>';
    html += '<span class="sym">' + esc(resp.ticker) + '</span>';
    if (signalsUnlocked()) {
      html += consensusPill(sig);
    } else {
      html += '<button type="button" class="signal-pill signal-locked" data-unlock="' +
              esc(resp.ticker) + '">🔒 Live signal — unlock by email</button>';
    }
    var asOf = fmtDate(resp.generated_at);
    if (asOf) {
      html += '<span class="as-of" title="When this analysis was generated">Analysis as of ' +
              esc(asOf) + '</span>';
    }
    html += '</div>';

    html += '<p class="matrix-cap">' + (signalsUnlocked()
      ? 'Headline call is the majority vote of the four windows below.'
      : 'Each card is the strategy that fit that period best.') + '</p>';

    html += '<div class="matrix">';
    ["1y", "3y", "5y", "10y"].forEach(function (w) {
      html += winCard(w, (t.windows || {})[w]);
    });
    html += '</div>';

    if (resp.oos_definition) {
      html += '<p class="note">Out-of-sample check: ' + esc(resp.oos_definition) + '.</p>';
    }
    html += '<p class="note">Illustrative prototype output from historical backtests. ' +
            'Not investment advice. ' + (signalsUnlocked()
              ? 'Live signal unlocked for this browser ✓'
              : '<a href="#offer">Get this signal by email →</a>') + '</p>';
    demoResult.innerHTML = html;

    // Drill-down: expand a window card to reveal the raw metrics + trades.
    demoResult.querySelectorAll(".win-toggle").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var open = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!open));
        var card = btn.parentElement;
        card.classList.toggle("open", !open);
        var detail = card.querySelector(".win-detail");
        if (detail) detail.hidden = open;
        if (open) return;  // just collapsed
        var window_ = btn.getAttribute("data-window");
        track("window_detail", { ticker: resp.ticker, window: window_ });
        var box = detail && detail.querySelector("[data-trades]");
        if (!box || box.getAttribute("data-loaded") === "1") return;
        if (tradesCache[resp.ticker] === undefined) {
          box.innerHTML = '<p class="note muted">Loading trades…</p>';
        }
        loadTrades(resp.ticker, function (data) {
          var rows = data && data.windows ? data.windows[window_] : null;
          renderTrades(box, rows, (data && data.columns) || TRADE_COLUMNS);
          box.setAttribute("data-loaded", "1");
        });
      });
    });

    var lockBtn = demoResult.querySelector("[data-unlock]");
    if (lockBtn) {
      lockBtn.addEventListener("click", function () {
        pendingTicker = lockBtn.getAttribute("data-unlock") || "";
        track("cta_click", { cta: "unlock_signal", ticker: pendingTicker });
        ["#hero-ticker", "#offer-ticker"].forEach(function (sel) {
          var el = $(sel); if (el) el.value = pendingTicker;
        });
        var offer = document.getElementById("offer");
        if (offer) offer.scrollIntoView({ behavior: "smooth", block: "center" });
        var email = offer && offer.querySelector('input[type="email"]');
        if (email) setTimeout(function () { email.focus({ preventScroll: true }); }, 450);
      });
    }
  }

  function lookup(raw) {
    var ticker = resolveTicker(raw);
    if (!ticker) return;
    lastTicker = ticker;
    track("ticker_lookup", { ticker: ticker });
    demoResult.innerHTML = '<p class="muted">Running six strategies on ' + esc(ticker) + '…</p>';
    fetch("/api/demo?ticker=" + encodeURIComponent(ticker))
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        renderResult(resp);
        track("signal_view", { ticker: ticker, known: !!resp.data });
      })
      .catch(function () {
        demoResult.innerHTML = '<p class="note warn">Couldn’t load the prototype right now. ' +
          'Enter your email below and we’ll send the report.</p>';
      });
  }

  var demoForm = $("#demo-form");
  if (demoForm) {
    demoForm.addEventListener("submit", function (e) {
      e.preventDefault();
      lookup($("#demo-ticker").value);
    });
  }

  /* ---------- quick picks ---------- */
  document.querySelectorAll(".chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var t = normTicker(chip.getAttribute("data-ticker"));
      ["#hero-ticker", "#demo-ticker", "#offer-ticker"].forEach(function (sel) {
        var el = $(sel); if (el) el.value = t;
      });
      track("cta_click", { cta: "quickpick", ticker: t });
      lookup(t);
      var demo = $("#demo");
      if (demo) demo.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  /* ---------- pricing card CTA -> focus the offer form ---------- */
  document.querySelectorAll("[data-focus-form]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var targetId = btn.getAttribute("data-focus-form");
      track("cta_click", { cta: "pricing_" + targetId });
      var section = document.getElementById(targetId);
      if (!section) return;
      section.scrollIntoView({ behavior: "smooth", block: "center" });
      var email = section.querySelector('input[type="email"]');
      if (email) setTimeout(function () { email.focus({ preventScroll: true }); }, 450);
    });
  });

  /* ---------- signup (hero + offer forms) ---------- */
  function submitSignup(form, cta) {
    var tickerEl = form.querySelector('input[name="ticker"]') ||
                   form.querySelector('input[id$="ticker"]');
    var emailEl = form.querySelector('input[type="email"]');
    var msg = form.querySelector(".microcopy");
    var btn = form.querySelector('button[type="submit"]');
    var ticker = resolveTicker(tickerEl && tickerEl.value);
    var email = (emailEl && emailEl.value || "").trim();

    if (!validEmail(email)) {
      if (msg) { msg.textContent = "Please enter a valid email address."; msg.className = "microcopy error"; }
      if (emailEl) emailEl.focus();
      return;
    }
    track("cta_click", { cta: cta, ticker: ticker });

    var payload = {
      email: email, ticker: ticker,
      source: context.source, utm_medium: context.utm_medium,
      utm_campaign: context.utm_campaign, referrer: context.referrer,
      visitor_id: context.visitor_id, is_return: context.is_return,
    };
    if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }

    fetch("/api/signup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.ok) throw new Error((res.body && res.body.error) || "Signup failed");
        track("email_submit", { ticker: ticker, cta: cta });
        if (!signalsUnlocked()) {
          unlockSignals();
          track("signal_unlock", { ticker: ticker });
        }
        var reveal = ticker || pendingTicker || lastTicker;
        pendingTicker = "";
        if (reveal) {
          var demoTicker = $("#demo-ticker");
          if (demoTicker) demoTicker.value = reveal;
          lookup(reveal);  // open the prototype detail for this ticker
        }
        showSuccess(form, ticker);
        setTimeout(function () { jumpToPrototype(!reveal); }, 1000);
      })
      .catch(function (err) {
        if (msg) { msg.textContent = err.message || "Something went wrong. Try again."; msg.className = "microcopy error"; }
        if (btn) { btn.disabled = false; btn.textContent = "Try again →"; }
      });
  }

  function jumpToPrototype(focusTicker) {
    var demo = document.getElementById("demo");
    if (!demo) return;
    demo.scrollIntoView({ behavior: "smooth", block: "start" });
    if (!focusTicker) return;
    var input = $("#demo-ticker");
    if (input) setTimeout(function () { input.focus({ preventScroll: true }); }, 450);
  }

  function showSuccess(form, ticker) {
    var wrap = document.createElement("div");
    wrap.className = "success-block";
    wrap.innerHTML =
      '<p class="microcopy success">✓ You’re in. ' +
      (ticker ? 'We’ll email the current signal for <strong>' + esc(ticker) + '</strong> — and alert you when it flips.' :
                'We’ll email your ticker’s current signal — and alert you when it flips.') +
      '</p>' +
      '<p class="note">Help us shape the paid version — would you pay for daily signals?</p>' +
      '<div class="quickpicks">' +
      '<button type="button" class="chip" data-wtp="yes">Yes, ~$19/mo</button>' +
      '<button type="button" class="chip" data-wtp="maybe">Maybe</button>' +
      '<button type="button" class="chip" data-wtp="no">No, free only</button>' +
      '</div>';
    form.replaceWith(wrap);
    wrap.querySelectorAll("[data-wtp]").forEach(function (b) {
      b.addEventListener("click", function () {
        track("wtp_" + b.getAttribute("data-wtp"), { ticker: ticker });
        wrap.querySelectorAll("[data-wtp]").forEach(function (x) { x.disabled = true; });
        b.textContent = "Thanks — noted.";
      });
    });
  }

  var heroForm = $("#hero-form");
  if (heroForm) heroForm.addEventListener("submit", function (e) { e.preventDefault(); submitSignup(heroForm, "hero"); });
  var offerForm = $("#offer-form");
  if (offerForm) offerForm.addEventListener("submit", function (e) { e.preventDefault(); submitSignup(offerForm, "offer"); });

  // Pre-load the known-ticker list so quick picks feel responsive.
  fetch("/api/demo?ticker=").then(function (r) { return r.json(); })
    .then(function (j) { demoKnown = j.known || []; setAnalysisDate(j.generated_at); }).catch(function () {});

  /* ---------- glossary panel ("what do these mean?") ---------- */
  function renderGlossary() {
    var body = document.getElementById("glossary-body");
    if (!body) return;
    var strategies = Object.keys(STRATEGY_INFO).map(function (label) {
      var i = STRATEGY_INFO[label];
      return '<div class="gloss-item"><h4>' + esc(label) +
        ' <span class="gloss-kind">' + esc(i.kind) + "</span></h4><p>" + esc(i.blurb) + "</p></div>";
    }).join("");
    var badges = ["true", "false", "null"].map(function (key) {
      var i = OOS_INFO[key];
      return '<div class="gloss-item ' + esc(i.tone) + '"><h4>' + esc(i.label) +
        "</h4><p>" + esc(i.blurb) + "</p></div>";
    }).join("");
    body.innerHTML =
      '<div class="gloss-grid">' + strategies + "</div>" +
      '<h4 class="gloss-head">The honesty badge</h4>' +
      '<div class="gloss-grid">' + badges + "</div>" +
      '<p class="note">Every backtest runs against real point-in-time S&amp;P 500 ' +
      "membership — including the companies that were later removed — so the results " +
      "aren’t flattered by survivorship bias.</p>";
  }

  /* ---------- company-name index + per-field autocomplete ("Apple" -> AAPL). ---------- */
  loadTickerIndex();
  ["#hero-ticker", "#demo-ticker", "#offer-ticker"].forEach(function (sel) {
    var el = $(sel); if (el) attachCombo(el);
  });
  renderGlossary();
})();
