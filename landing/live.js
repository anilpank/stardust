(function () {
  "use strict";

  var snapshot = null;
  var fills = [];
  var decisions = [];

  function $(s) { return document.querySelector(s); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function cls(v) { return v > 0 ? "pos" : v < 0 ? "neg" : "neutral"; }

  function num(v, dp) {
    if (v == null || isNaN(v)) return "—";
    return Number(v).toFixed(dp == null ? 2 : dp);
  }

  function fmtInt(v) {
    if (v == null || isNaN(v)) return "—";
    return Number(v).toLocaleString();
  }

  function fmtMoney(v, signed) {
    if (v == null || isNaN(v)) return "—";
    var sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
    var a = Math.abs(v);
    if (a >= 1e6) return sign + "$" + (a / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return sign + "$" + (a / 1e3).toFixed(1) + "K";
    if (a >= 1e3) return sign + "$" + a.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return sign + "$" + a.toFixed(a < 100 ? 2 : 0);
  }

  function fmtPct(v, signed) {
    if (v == null || isNaN(v)) return "—";
    var sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
    return sign + (Math.abs(v) * 100).toFixed(1) + "%";
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    return String(iso).slice(0, 10);
  }

  function intOr0(v) {
    var n = parseInt(v, 10);
    return isNaN(n) ? 0 : n;
  }

  function floatOr0(v) {
    var n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  }

  function kpi(label, value, sub, accent) {
    return '<div class="kpi' + (accent ? " accent" : "") + '">' +
      '<div class="k-label">' + esc(label) + "</div>" +
      '<div class="k-value">' + value + "</div>" +
      (sub ? '<div class="k-sub">' + sub + "</div>" : "") + "</div>";
  }

  function dayPnl() {
    var eq = (snapshot && snapshot.live && snapshot.live.equity && snapshot.live.equity.values) || [];
    if (eq.length < 2) return null;
    return eq[eq.length - 1] - eq[eq.length - 2];
  }

  function renderLiveKpis() {
    var box = $("#live-kpis");
    if (!snapshot) {
      box.innerHTML = '<div class="muted small">No snapshot — run landing/generate_portfolio_snapshot.py</div>';
      return;
    }
    var live = snapshot.live || {};
    var m = live.metrics || {};
    var a = snapshot.adherence || {};
    var day = dayPnl();
    var positions = (live.positions || []).length;
    var mode = snapshot.mode ? " · " + esc(snapshot.mode) : "";
    box.innerHTML =
      kpi("Equity", fmtMoney(m.equity_end), "as of " + fmtDate(snapshot.as_of) + mode, true) +
      kpi("Day P&L", day == null ? "—" : '<span class="' + cls(day) + '">' + fmtMoney(day, true) + "</span>",
        "total " + '<span class="' + cls(m.total_return_pct) + '">' + fmtPct(m.total_return_pct, true) + "</span>") +
      kpi("CAGR", fmtPct(m.cagr), "max DD " + fmtPct(m.max_drawdown)) +
      kpi("Cash", fmtMoney(live.cash), positions + " position" + (positions === 1 ? "" : "s")) +
      kpi("Adherence", a.acted_pct == null ? "—" : fmtPct(a.acted_pct),
        (a.acted || 0) + " acted / " + (a.decisions || 0) + " decisions") +
      kpi("Model equity", fmtMoney((snapshot.model && snapshot.model.metrics) ? snapshot.model.metrics.equity_end : null),
        "config weight " + fmtPct(snapshot.config ? snapshot.config.core_target_weight : null));
  }

  function setStatus(msg) {
    var el = $("#pf-status");
    if (el) el.textContent = msg;
  }

  function renderProposal() {
    var box = $("#proposal-body");
    if (!snapshot) { box.innerHTML = '<p class="muted">No snapshot.</p>'; return; }
    var p = snapshot.proposal;
    if (!p) {
      box.innerHTML = '<p class="muted">Hold — no core action queued for the next session.</p>';
      return;
    }
    box.innerHTML =
      '<div class="proposal">' +
        '<div class="prop-main">' +
          '<span class="side ' + esc(p.side) + '">' + esc(String(p.side).toUpperCase()) + "</span> " +
          "<strong>" + esc(p.ticker) + "</strong> " +
          '<span class="pill">' + esc(p.lane) + "</span>" +
          '<div class="muted small">' + esc(p.reason) + " · target " + fmtPct(p.target_weight) +
            " · ref " + fmtMoney(p.ref_price) + " · for " + fmtDate(p.for_session) + "</div>" +
          '<div class="muted small mono">' + esc(p.proposal_id) + "</div>" +
        "</div>" +
        '<div class="prop-form">' +
          '<input id="pf-shares" type="number" min="0" step="1" placeholder="shares" />' +
          '<input id="pf-price" type="number" min="0" step="0.01" placeholder="fill price" />' +
          '<input id="pf-note" type="text" placeholder="note (optional)" />' +
          '<div class="prop-buttons">' +
            '<button type="button" class="btn primary" id="pf-acted">Acted + fill</button>' +
            '<button type="button" class="btn" id="pf-skipped">Skipped</button>' +
            '<button type="button" class="btn" id="pf-deferred">Deferred</button>' +
          "</div>" +
        "</div>" +
      "</div>" +
      '<div id="pf-status" class="muted small"></div>';

    if (p.ref_price != null) $("#pf-price").value = Number(p.ref_price).toFixed(2);
    $("#pf-acted").addEventListener("click", function () { actOnProposal(p); });
    $("#pf-skipped").addEventListener("click", function () { decide(p, "skipped"); });
    $("#pf-deferred").addEventListener("click", function () { decide(p, "deferred"); });
  }

  function plannedShares() {
    var el = $("#pf-shares");
    return el ? intOr0(el.value) : 0;
  }

  function plannedPrice(p) {
    var el = $("#pf-price");
    var v = el ? floatOr0(el.value) : 0;
    return v > 0 ? v : p.ref_price;
  }

  function proposalDecision(p, action) {
    return {
      proposal_id: p.proposal_id, action: action, ticker: p.ticker, lane: p.lane, side: p.side,
      planned_shares: plannedShares(), planned_price: plannedPrice(p),
      note: $("#pf-note") ? $("#pf-note").value : "",
    };
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      });
    });
  }

  function decide(p, action) {
    postJSON("/api/admin/trade/decisions", proposalDecision(p, action))
      .then(function () { setStatus("Recorded (" + action + ")."); loadLive(); })
      .catch(function (e) { setStatus("Error: " + e.message); });
  }

  function actOnProposal(p) {
    var shares = plannedShares();
    var price = plannedPrice(p);
    if (shares <= 0 || !(price > 0)) {
      setStatus("Enter shares and a fill price to mark it acted.");
      return;
    }
    postJSON("/api/admin/trade/decisions", proposalDecision(p, "acted"))
      .then(function () {
        return postJSON("/api/admin/trade/fills", {
          ticker: p.ticker, lane: p.lane, side: p.side, shares: shares, price: price,
          source: "live", proposal_id: p.proposal_id,
          note: $("#pf-note") ? $("#pf-note").value : "",
        });
      })
      .then(function () { setStatus("Recorded (acted + fill)."); loadLive(); })
      .catch(function (e) { setStatus("Error: " + e.message); });
  }

  function renderPositions() {
    var rows = (snapshot && snapshot.live && snapshot.live.positions) || [];
    $("#live-positions").innerHTML = rows.map(function (p) {
      return "<tr>" +
        "<td>" + esc(p.ticker) + "</td>" +
        '<td><span class="pill">' + esc(p.lane) + "</span></td>" +
        '<td class="num">' + fmtInt(p.shares) + "</td>" +
        '<td class="num">' + num(p.avg_entry) + "</td>" +
        '<td class="num">' + num(p.last_close) + "</td>" +
        '<td class="num">' + fmtMoney(p.market_value) + "</td>" +
        '<td class="num ' + cls(p.unrealized_pnl) + '">' + fmtMoney(p.unrealized_pnl, true) + "</td>" +
        '<td class="num">—</td>' +
      "</tr>";
    }).join("") || '<tr><td colspan="8" class="muted">Flat — no open positions.</td></tr>';
  }

  function renderFills() {
    $("#live-fills").innerHTML = fills.slice(0, 100).map(function (f) {
      return "<tr>" +
        "<td>" + fmtDate(f.trade_date) + "</td>" +
        "<td>" + esc(f.ticker) + "</td>" +
        '<td><span class="pill">' + esc(f.lane) + "</span></td>" +
        "<td>" + esc(f.side) + "</td>" +
        '<td class="num">' + fmtInt(f.shares) + "</td>" +
        '<td class="num">' + num(f.price) + "</td>" +
        "<td>" + esc(f.source) + "</td>" +
        '<td class="mono">' + esc(f.proposal_id) + "</td>" +
      "</tr>";
    }).join("") || '<tr><td colspan="8" class="muted">No fills recorded.</td></tr>';
  }

  function renderDecisions() {
    var pill = { acted: "pos", skipped: "", deferred: "warn" };
    $("#live-decisions").innerHTML = decisions.slice(0, 100).map(function (d) {
      return "<tr>" +
        "<td>" + fmtDate(d.trade_date) + "</td>" +
        '<td><span class="pill ' + (pill[d.action] || "") + '">' + esc(d.action) + "</span></td>" +
        "<td>" + esc(d.ticker) + "</td>" +
        "<td>" + esc(d.side) + "</td>" +
        '<td class="num">' + fmtInt(d.planned_shares) + "</td>" +
        '<td class="mono">' + esc(d.proposal_id) + "</td>" +
      "</tr>";
    }).join("") || '<tr><td colspan="6" class="muted">No decisions recorded.</td></tr>';
  }

  function renderLive() {
    renderLiveKpis();
    renderProposal();
    renderPositions();
    renderFills();
    renderDecisions();
    var modeEl = $("#live-mode");
    if (modeEl) modeEl.textContent = snapshot && snapshot.mode ? "(" + snapshot.mode + ")" : "";
    var up = $("#live-updated");
    if (up) {
      up.textContent = snapshot
        ? "snapshot " + (snapshot.generated_at || "") + " · journal " + (snapshot.journal_source || "empty")
        : "no snapshot";
    }
  }

  function getJSON(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) throw new Error("auth");
      if (!r.ok) return { missing: true };
      return r.json();
    });
  }

  function loadLive() {
    return Promise.all([
      getJSON("/api/admin/portfolio/snapshot"),
      getJSON("/api/admin/trade/fills"),
      getJSON("/api/admin/trade/decisions"),
    ]).then(function (res) {
      snapshot = res[0].missing ? null : res[0];
      fills = res[1].fills || [];
      decisions = res[2].decisions || [];
      renderLive();
    }).catch(function (err) {
      var up = $("#live-updated");
      if (up) up.textContent = err.message === "auth" ? "Reload to sign in" : "Failed to load live data";
    });
  }

  var refresh = $("#refresh");
  if (refresh) refresh.addEventListener("click", loadLive);
  loadLive();
})();
