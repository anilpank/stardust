/* Portfolio console — reads /api/admin/portfolio and renders it with hand-rolled SVG. */
(function () {
  "use strict";

  var state = { payload: null, runIdx: 0 };

  function $(s) { return document.querySelector(s); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
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

  function cls(v) { return v > 0 ? "pos" : v < 0 ? "neg" : "neutral"; }

  function fmtDate(iso) {
    if (!iso) return "—";
    return String(iso).slice(0, 10);
  }

  /* ---------- KPI cards ---------- */
  function kpi(label, value, sub, accent) {
    return '<div class="kpi' + (accent ? " accent" : "") + '">' +
      '<div class="k-label">' + esc(label) + "</div>" +
      '<div class="k-value">' + value + "</div>" +
      (sub ? '<div class="k-sub">' + sub + "</div>" : "") + "</div>";
  }

  function renderKpis(run) {
    var m = run.metrics;
    var bench = m.benchmark || {};
    var totalRet = m.total_return_pct;
    $("#kpis").innerHTML =
      kpi("Equity", fmtMoney(m.equity_start) + " → " + fmtMoney(m.equity_end),
        '<span class="' + cls(totalRet) + '">' + fmtPct(totalRet, true) + "</span> total", true) +
      kpi("CAGR", fmtPct(m.cagr), "worst 12M: " + (m.worst_12m == null ? "N/A" : '<span class="' + cls(m.worst_12m) + '">' + fmtPct(m.worst_12m) + "</span>")) +
      kpi("Sharpe", m.sharpe == null ? "—" : Number(m.sharpe).toFixed(2),
        "Calmar " + (m.calmar == null ? "—" : Number(m.calmar).toFixed(2))) +
      kpi("Max drawdown", fmtPct(m.max_drawdown), "breaker days " + fmtPct(m.breaker_days_pct)) +
      kpi("Win rate", fmtPct(m.win_rate),
        m.trades_total + " trades (core " + (m.core_trades || 0) + ")") +
      kpi("Avg exposure", fmtPct(m.avg_exposure), "max " + fmtPct(m.max_exposure) + ", violations " + (m.band_violations || 0)) +
      kpi("Banked", fmtMoney(m.banked_total), "principal " + fmtMoney(m.principal_end)) +
      kpi("vs SPY excess", bench.excess_total_return == null ? "—" : '<span class="' + cls(bench.excess_total_return) + '">' + fmtPct(bench.excess_total_return, true) + "</span>",
        "SPY " + fmtPct(bench.spy_total_return, true) + " total");
  }

  /* ---------- SVG helpers ---------- */
  function svgEl(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function makeSvg(container, height) {
    container.innerHTML = "";
    var width = Math.max(container.clientWidth || 800, 320);
    var svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, height: height });
    container.appendChild(svg);
    return { svg: svg, width: width, height: height };
  }

  function yearTicks(dates, width, padLeft) {
    var seen = {}, ticks = [];
    for (var i = 0; i < dates.length; i++) {
      var y = String(dates[i]).slice(0, 4);
      if (!seen[y]) { seen[y] = true; ticks.push({ i: i, year: y }); }
    }
    var step = Math.max(1, Math.ceil(ticks.length / 9));
    var out = [];
    for (var j = 0; j < ticks.length; j += step) {
      var t = ticks[j];
      var x = padLeft + (t.i / Math.max(1, dates.length - 1)) * (width - padLeft - 8);
      out.push({ x: x, year: t.year });
    }
    return out;
  }

  function yTicks(min, max, count) {
    var out = [];
    for (var i = 0; i <= count; i++) out.push(min + ((max - min) * i) / count);
    return out;
  }

  /* ---------- equity chart ---------- */
  function renderEquityChart(run) {
    var box = $("#equity-chart");
    var dates = run.equity.dates || [];
    var port = run.equity.portfolio || [];
    var spy = run.equity.spy || [];
    if (!port.length) { box.innerHTML = '<p class="muted">No equity data.</p>'; return; }

    var H = 300, padL = 56, padT = 12, padB = 22;
    var c = makeSvg(box, H);
    var w = c.width, h = c.height;

    var all = port.concat(spy.filter(function (v) { return v != null; }));
    var min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    if (min === max) { min = min * 0.99; max = max * 1.01; }
    var pad = (max - min) * 0.04;
    min -= pad; max += pad;

    function X(i) { return padL + (i / Math.max(1, port.length - 1)) * (w - padL - 8); }
    function Y(v) { return padT + (1 - (v - min) / (max - min)) * (h - padT - padB); }

    yTicks(min, max, 4).forEach(function (v) {
      var y = Y(v);
      c.svg.appendChild(svgEl("line", { class: "grid", x1: padL, x2: w - 8, y1: y, y2: y }));
      var label = svgEl("text", { class: "axis-label", x: 4, y: y + 3 });
      label.textContent = fmtMoney(v);
      c.svg.appendChild(label);
    });
    yearTicks(dates, w, padL).forEach(function (t) {
      var label = svgEl("text", { class: "axis-label", x: t.x, y: h - 6 });
      label.textContent = t.year;
      c.svg.appendChild(label);
    });

    function path(values, clsName) {
      var pts = [];
      for (var i = 0; i < values.length; i++) {
        if (values[i] == null) continue;
        pts.push(X(i).toFixed(1) + "," + Y(values[i]).toFixed(1));
      }
      if (!pts.length) return;
      c.svg.appendChild(svgEl("polyline", { class: clsName, points: pts.join(" ") }));
    }
    if (spy.length) path(spy, "line-spy");
    path(port, "line-portfolio");
  }

  /* ---------- exposure / breaker / regime strip ---------- */
  function renderExposureChart(run) {
    var box = $("#exposure-chart");
    var d = run.daily || {};
    var dates = d.dates || [];
    var exposure = d.exposure || [];
    if (!exposure.length) { box.innerHTML = '<p class="muted">No daily data.</p>'; return; }

    var H = 150, padL = 40, padT = 8, stripH = 6, gap = 3, padB = 22 + stripH * 2 + gap * 2;
    var c = makeSvg(box, H);
    var w = c.width, h = c.height;
    var plotB = h - padB;
    var bandMax = run.config.band_max || 0.65;
    var maxV = Math.max(bandMax * 1.15, Math.max.apply(null, exposure) * 1.05);

    function X(i) { return padL + (i / Math.max(1, exposure.length - 1)) * (w - padL - 8); }
    function Y(v) { return padT + (1 - v / maxV) * (plotB - padT); }

    var step = Math.max(1, Math.floor(w / Math.max(1, exposure.length)));
    (d.breaker || []).forEach(function (b, i) {
      if (!b) return;
      c.svg.appendChild(svgEl("rect", {
        class: "breaker-band", x: X(i) - step / 2, y: padT,
        width: step + 1, height: plotB - padT,
      }));
    });

    yTicks(0, maxV, 3).forEach(function (v) {
      var y = Y(v);
      c.svg.appendChild(svgEl("line", { class: "grid", x1: padL, x2: w - 8, y1: y, y2: y }));
      var label = svgEl("text", { class: "axis-label", x: 2, y: y + 3 });
      label.textContent = fmtPct(v);
      c.svg.appendChild(label);
    });

    var maxY = Y(bandMax);
    c.svg.appendChild(svgEl("line", { class: "band-max", x1: padL, x2: w - 8, y1: maxY, y2: maxY }));

    var pts = [];
    for (var i = 0; i < exposure.length; i++) {
      pts.push(X(i).toFixed(1) + "," + Y(exposure[i]).toFixed(1));
    }
    c.svg.appendChild(svgEl("polyline", { class: "line-exposure", points: pts.join(" ") }));

    var coreY = plotB + gap;
    var bearY = coreY + stripH + gap;
    (d.regime || []).forEach(function (code, i) {
      if (code === "r") {
        c.svg.appendChild(svgEl("rect", {
          class: "bear-band", x: X(i) - step / 2, y: bearY,
          width: step + 1, height: stripH,
        }));
      }
    });
    (d.core || []).forEach(function (on, i) {
      if (on) {
        c.svg.appendChild(svgEl("rect", {
          class: "core-band", x: X(i) - step / 2, y: coreY,
          width: step + 1, height: stripH,
        }));
      }
    });
    c.svg.appendChild(svgEl("rect", {
      x: padL, y: coreY, width: w - padL - 8, height: stripH,
      fill: "none", stroke: "var(--border)", "stroke-width": 0.5,
    }));
    c.svg.appendChild(svgEl("rect", {
      x: padL, y: bearY, width: w - padL - 8, height: stripH,
      fill: "none", stroke: "var(--border)", "stroke-width": 0.5,
    }));

    yearTicks(dates, w, padL).forEach(function (t) {
      var label = svgEl("text", { class: "axis-label", x: t.x, y: h - 6 });
      label.textContent = t.year;
      c.svg.appendChild(label);
    });
  }

  /* ---------- tables ---------- */
  function renderPerYear(run) {
    var perYear = (run.metrics && run.metrics.per_year) || {};
    var years = Object.keys(perYear).sort().reverse();
    $("#per-year").innerHTML = years.map(function (y) {
      var v = perYear[y];
      return "<tr><td>" + esc(y) + '</td><td class="num ' + cls(v) + '">' + fmtPct(v, true) + "</td></tr>";
    }).join("") || '<tr><td colspan="2" class="muted">No data.</td></tr>';
  }

  function row(label, value) {
    return "<tr><td class=\"muted\">" + esc(label) + "</td><td>" + value + "</td></tr>";
  }

  function renderConfig(run) {
    var c = run.config;
    var m = run.metrics;
    $("#config-table").innerHTML =
      row("Capital", fmtMoney(run.capital)) +
      row("Exposure band", fmtPct(c.band_min) + " – " + fmtPct(c.band_max)) +
      row("Risk / trade", fmtPct(c.risk_per_trade) + " (stop " + c.stop_atr_multiple + "× ATR" + c.atr_lookback + ")") +
      row("Slots &amp; cap", c.max_positions + " max, " + fmtPct(c.max_position_pct) + " / position, min " + fmtMoney(c.min_position_notional)) +
      row("Slippage", c.slippage_bps + " bps") +
      row("Cash yield", c.cash_yield ? fmtPct(c.cash_yield) + " on idle cash" : "off") +
      row("Breaker", "−" + fmtPct(c.breaker_pct) + " off HWM, reopen +" + fmtPct(c.breaker_reopen_offset) + " / " + c.breaker_cooldown_days + "d cooldown") +
      row("Ratchet", fmtPct(c.ratchet_rate) + " of gains above HWM, monthly") +
      row("Principal sleeve", fmtPct(c.principal_start_pct) + " at start → " + fmtMoney(m.principal_end) + " end") +
      row("Core lane", c.core_enabled
        ? esc(c.core_ticker) + " · " + fmtPct(c.core_target_weight) + " weight · " + c.core_momentum_lookback + "d momentum"
        : "disabled") +
      row("Regime gate", "no entries in BEAR (" + c.regime_lookback_days + "d, bull >" + fmtPct(c.regime_bull_threshold) +
        ", bear <" + fmtPct(c.regime_bear_threshold) + ")");

    var bench = m.benchmark || {};
    $("#benchmark").innerHTML =
      "<strong>vs SPY</strong> (" + (bench.window_start || "?") + " → " + (bench.window_end || "?") +
      ", " + (bench.window_years || "?") + "y): SPY " + fmtPct(bench.spy_total_return, true) + " total · CAGR " +
      fmtPct(bench.spy_cagr) + " · MaxDD " + fmtPct(bench.spy_max_drawdown) +
      " — system " + fmtPct(bench.system_total_return, true) + " total · CAGR " +
      fmtPct(bench.system_cagr) + " — excess <strong class=\"" + cls(bench.excess_total_return) + "\">" +
      fmtPct(bench.excess_total_return, true) + "</strong>";

    var rejected = run.rejected_counts || {};
    var keys = Object.keys(rejected);
    $("#rejected").innerHTML = keys.length
      ? keys.map(function (k) {
          return '<span class="pill">' + esc(k) + " × " + rejected[k] + "</span>";
        }).join("")
      : '<span class="muted small">No rejected orders.</span>';
  }

  function tradeRows(run) {
    var lane = $("#trade-lane").value;
    var q = ($("#trade-search").value || "").trim().toLowerCase();
    return (run.trades || []).filter(function (t) {
      if (lane && t.lane !== lane) return false;
      if (!q) return true;
      return [t.ticker, t.exit_reason, t.strategy_id].join(" ").toLowerCase().indexOf(q) >= 0;
    });
  }

  function renderTrades(run) {
    var rows = tradeRows(run);
    $("#trade-count").textContent = rows.length ? "(" + rows.length + ")" : "";
    var shown = rows.slice(-400).reverse();
    $("#trades").innerHTML = shown.map(function (t) {
      var pnlPct = t.pnl_pct;
      return "<tr>" +
        "<td>" + fmtDate(t.entry_date) + "</td>" +
        "<td>" + esc(t.ticker) + "</td>" +
        '<td><span class="pill">' + esc(t.lane) + "</span></td>" +
        '<td class="num">' + t.shares + "</td>" +
        '<td class="num">' + Number(t.entry_price).toFixed(2) + "</td>" +
        '<td class="num">' + (t.stop_price ? Number(t.stop_price).toFixed(2) : "—") + "</td>" +
        "<td>" + fmtDate(t.exit_date) + "</td>" +
        '<td class="num">' + Number(t.exit_price).toFixed(2) + "</td>" +
        '<td class="num ' + cls(t.pnl) + '">' + fmtMoney(t.pnl, true) + "</td>" +
        '<td class="num ' + cls(pnlPct) + '">' + fmtPct(pnlPct, true) + "</td>" +
        "<td>" + esc(t.exit_reason) + "</td>" +
        "<td>" + esc(t.strategy_id) + "</td></tr>";
    }).join("") || '<tr><td colspan="12" class="muted">No trades.</td></tr>';
  }

  function renderRatchet(run) {
    $("#ratchet").innerHTML = (run.ratchet_events || []).slice().reverse().map(function (e) {
      return "<tr><td>" + fmtDate(e.date) + '</td><td class="num pos">' + fmtMoney(e.bank, true) +
        '</td><td class="num">' + fmtMoney(e.equity) +
        '</td><td class="num">' + fmtMoney(e.principal_target) + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="muted">Nothing banked yet.</td></tr>';
  }

  function renderBreakers(run) {
    $("#breakers").innerHTML = (run.breaker_events || []).slice().reverse().map(function (e) {
      var state = e.tripped
        ? '<span class="pill neg">tripped</span>'
        : '<span class="pill pos">reopened</span>';
      return "<tr><td>" + fmtDate(e.date) + "</td><td>" + state +
        '</td><td class="num">' + fmtMoney(e.equity) +
        '</td><td class="num">' + fmtMoney(e.hwm) + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="muted">Breaker never moved.</td></tr>';
  }

  /* ---------- top-level render ---------- */
  function render() {
    var payload = state.payload;
    if (!payload || !payload.runs || !payload.runs.length) {
      $("#updated").textContent = "No portfolio data — run landing/generate_portfolio_data.py";
      return;
    }
    var run = payload.runs[state.runIdx];
    var meta = [];
    if (run.strategy) meta.push(run.strategy);
    if (run.tickers && run.tickers.length) meta.push(run.tickers.join(" "));
    meta.push(
      (run.equity.dates[0] || "") + " → " +
      (run.equity.dates[run.equity.dates.length - 1] || "")
    );
    $("#run-meta").textContent = meta.join(" · ");
    renderKpis(run);
    renderEquityChart(run);
    renderExposureChart(run);
    renderPerYear(run);
    renderConfig(run);
    renderTrades(run);
    renderRatchet(run);
    renderBreakers(run);
    $("#updated").textContent = "Generated " + (payload.generated_at || "");
  }

  function load() {
    fetch("/api/admin/portfolio", { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        if (r.status === 429) throw new Error("locked");
        if (!r.ok) throw new Error("missing");
        return r.json();
      })
      .then(function (data) {
        state.payload = data;
        var sel = $("#run-select");
        sel.innerHTML = (data.runs || []).map(function (run, i) {
          return '<option value="' + i + '">' + esc(run.label) + "</option>";
        }).join("");
        sel.value = String(state.runIdx);
        render();
      })
      .catch(function (err) {
        if (err.message === "locked") $("#updated").textContent = "Locked — too many attempts";
        else if (err.message === "auth") $("#updated").textContent = "Reload to sign in";
        else $("#updated").textContent = "No portfolio data — run landing/generate_portfolio_data.py";
      });
  }

  $("#refresh").addEventListener("click", load);
  $("#run-select").addEventListener("change", function () {
    state.runIdx = parseInt(this.value, 10) || 0;
    render();
  });
  $("#trade-lane").addEventListener("change", function () {
    if (state.payload) renderTrades(state.payload.runs[state.runIdx]);
  });
  $("#trade-search").addEventListener("input", function () {
    if (state.payload) renderTrades(state.payload.runs[state.runIdx]);
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    if (!state.payload || !state.payload.runs || !state.payload.runs.length) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      var run = state.payload.runs[state.runIdx];
      renderEquityChart(run);
      renderExposureChart(run);
    }, 150);
  });

  load();
})();
