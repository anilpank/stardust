/* stardust experiment console — reads /api/admin/data and renders it. */
(function () {
  "use strict";

  var REFRESH_MS = 15000;
  var state = { signups: [], events: [], summary: null };

  function $(s) { return document.querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtTime(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      return d.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return iso; }
  }

  function load() {
    fetch("/api/admin/data", { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        if (r.status === 429) throw new Error("locked");
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) throw new Error("bad payload");
        state.signups = data.signups || [];
        state.events = data.events || [];
        state.summary = data.summary;
        render(data);
      })
      .catch(function (err) {
        if (err.message === "locked") $("#updated").textContent = "Locked — too many attempts";
        else if (err.message === "auth") $("#updated").textContent = "Reload to sign in";
        else $("#updated").textContent = "Load failed";
      });
  }

  /* ---------- rendering ---------- */
  function kpi(label, value, sub, accent) {
    return '<div class="kpi' + (accent ? " accent" : "") + '">' +
      '<div class="k-label">' + esc(label) + '</div>' +
      '<div class="k-value">' + esc(value) + '</div>' +
      (sub ? '<div class="k-sub">' + esc(sub) + '</div>' : "") + '</div>';
  }

  function render(data) {
    var k = data.summary.kpis;
    $("#kpis").innerHTML =
      kpi("Leads", k.leads, k.conversion_pct + "% of unique visitors", true) +
      kpi("Visits", k.visits, k.unique_visitors + " unique") +
      kpi("Email submits", k.email_submits, "") +
      kpi("Ticker lookups", k.ticker_lookups, "") +
      kpi("Signal views", k.signal_views, "") +
      kpi("CTA clicks", k.cta_clicks, "") +
      kpi("Return rate", k.return_rate_pct + "%", k.return_visits + " returning") +
      kpi("Would pay $19/mo", k.wtp_yes, k.wtp_maybe + " maybe · " + k.wtp_no + " no");

    var funnel = data.summary.funnel || [];
    var max = Math.max.apply(null, funnel.map(function (f) { return f.value; }).concat([1]));
    $("#funnel").innerHTML = funnel.map(function (f) {
      var w = Math.round((f.value / max) * 100);
      return '<div class="funnel-row">' +
        '<span class="f-step">' + esc(f.step) + '</span>' +
        '<span class="funnel-bar"><span style="width:' + w + '%"></span></span>' +
        '<span class="f-val">' + f.value + '</span></div>';
    }).join("");

    $("#sources").innerHTML = (data.summary.sources || []).map(function (s) {
      return "<tr><td>" + esc(s.source) + '</td><td class="num">' + s.events +
        '</td><td class="num">' + s.leads + "</td></tr>";
    }).join("") || '<tr><td colspan="3" class="muted">No traffic yet.</td></tr>';

    $("#tickers").innerHTML = (data.summary.top_tickers || []).map(function (t) {
      return "<tr><td>" + esc(t.ticker) + '</td><td class="num">' + t.leads + "</td></tr>";
    }).join("") || '<tr><td colspan="2" class="muted">No tickers yet.</td></tr>';

    renderLeads();
    renderEvents();
    $("#updated").textContent = "Updated " + fmtTime(data.generated_at);
  }

  function renderLeads() {
    $("#lead-count").textContent = state.signups.length ? "(" + state.signups.length + ")" : "";
    $("#leads-empty").classList.toggle("hidden", state.signups.length > 0);
    $("#leads").innerHTML = state.signups.map(function (s) {
      return "<tr>" +
        "<td>" + esc(fmtTime(s.timestamp)) + "</td>" +
        '<td class="mono">' + esc(s.email) + "</td>" +
        "<td>" + esc(s.ticker || "—") + "</td>" +
        "<td>" + esc(s.source || "direct") + "</td>" +
        "<td>" + (s.is_return === "true" ? "yes" : "no") + "</td>" +
        "<td>" + esc(s.ip || "") + "</td></tr>";
    }).join("");
  }

  function renderEvents() {
    var q = ($("#event-search").value || "").trim().toLowerCase();
    var type = $("#event-type").value;
    var rows = state.events.filter(function (e) {
      if (type && e.event !== type) return false;
      if (!q) return true;
      return [e.ticker, e.source, e.visitor_id, e.event].join(" ").toLowerCase().indexOf(q) >= 0;
    });
    $("#event-count").textContent = "(" + rows.length + ")";
    $("#events").innerHTML = rows.slice(0, 1000).map(function (e) {
      return "<tr>" +
        "<td>" + esc(fmtTime(e.timestamp)) + "</td>" +
        '<td><span class="pill ' + esc(e.event) + '">' + esc(e.event) + "</span></td>" +
        "<td>" + esc(e.ticker || "—") + "</td>" +
        "<td>" + esc(e.source || "direct") + "</td>" +
        "<td>" + (e.visitor_id ? esc(e.visitor_id.slice(0, 8)) : "") + "</td>" +
        "<td>" + (e.is_return === "true" ? "yes" : "no") + "</td></tr>";
    }).join("");
  }

  /* ---------- wiring ---------- */
  $("#refresh").addEventListener("click", load);
  $("#event-search").addEventListener("input", renderEvents);
  $("#event-type").addEventListener("change", renderEvents);

  $("#btn-signups").href = "/api/admin/export/signups.csv";
  $("#btn-events").href = "/api/admin/export/events.csv";
  $("#btn-db").href = "/api/admin/db";

  load();
  setInterval(load, REFRESH_MS);
})();
