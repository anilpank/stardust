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

  var WINDOW_LABELS = { "1y": "1 year", "3y": "3 years", "5y": "5 years", "10y": "10 years" };

  /* ---------- interactive prototype ---------- */
  var demoResult = $("#demo-result");
  var demoKnown = [];

  function renderResult(resp) {
    var t = resp.data;
    if (!t) {
      demoResult.innerHTML =
        '<p class="note warn">We don’t have a cached report for <strong>' + esc(resp.ticker) +
        '</strong> yet — coverage is expanding. Enter your email below and we’ll send this ticker’s ' +
        'report as soon as it’s ready.</p>';
      return;
    }

    var sig = t.signal || {};
    var dir = (sig.direction || "HOLD").toUpperCase();
    var html = "";
    html += '<div class="result-head">';
    html += '<span class="name">' + esc(t.name || resp.ticker) + '</span>';
    html += '<span class="sym">' + esc(resp.ticker) + '</span>';
    html += '<span class="signal-pill signal-' + esc(dir) + '">' + esc(dir) +
            (sig.strategy ? ' · ' + esc(sig.strategy) : '') + '</span>';
    html += '</div>';

    html += '<div class="matrix">';
    ["1y", "3y", "5y", "10y"].forEach(function (w) {
      var c = (t.windows || {})[w];
      html += '<div class="win-card">';
      html += '<div class="win-win">' + esc(WINDOW_LABELS[w] || w) + '</div>';
      if (c && c.strategy) {
        html += '<div class="win-name">' + esc(c.strategy) + '</div>';
        html += '<div class="win-metric">Win rate ' + fmtPct(c.win_rate) + ' · ' +
                esc(c.trades_completed || 0) + ' trades</div>';
        if (c.oos === true) {
          html += '<span class="oos oos-true">✓ Held up out-of-sample</span>';
        } else if (c.oos === false) {
          html += '<span class="oos oos-false">⚠ Likely overfit</span>';
        }
      } else {
        html += '<div class="win-name muted">—</div>';
      }
      html += '</div>';
    });
    html += '</div>';

    if (resp.oos_definition) {
      html += '<p class="note">Out-of-sample check: ' + esc(resp.oos_definition) + '.</p>';
    }
    html += '<p class="note">Illustrative prototype output from historical backtests. ' +
            'Not investment advice. <a href="#offer">Get this signal by email →</a></p>';
    demoResult.innerHTML = html;
  }

  function lookup(ticker) {
    ticker = normTicker(ticker);
    if (!ticker) return;
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
    var ticker = normTicker(tickerEl && tickerEl.value);
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
        showSuccess(form, ticker);
      })
      .catch(function (err) {
        if (msg) { msg.textContent = err.message || "Something went wrong. Try again."; msg.className = "microcopy error"; }
        if (btn) { btn.disabled = false; btn.textContent = "Try again →"; }
      });
  }

  function showSuccess(form, ticker) {
    var wrap = document.createElement("div");
    wrap.className = "success-block";
    wrap.innerHTML =
      '<p class="microcopy success">✓ You’re in. ' +
      (ticker ? 'We’ll email the current signal for <strong>' + esc(ticker) + '</strong>.' :
                'We’ll email your ticker’s current signal.') +
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
    .then(function (j) { demoKnown = j.known || []; }).catch(function () {});
})();
