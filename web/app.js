/* REYGUN SuperChart front end. Talks only to the local Python server (127.0.0.1). */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const BUILD = "2026-10-01.20";          // same as BUILD in superchart.py
  const TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
  const LS = (() => { try { return window.localStorage; } catch (e) { return null; } })();
  const lsGet = (k, d) => { try { return (LS && LS.getItem(k)) || d; } catch (e) { return d; } };
  const lsSet = (k, v) => { try { LS && LS.setItem(k, v); } catch (e) { /* ignore */ } };

  let S = null;                 // latest /api/state
  const URLQ = new URLSearchParams(location.search);
  const TF_FROM_URL = TFS.includes(URLQ.get("tf")) ? URLQ.get("tf") : null;   // pop-out window: its own TF
  let tf = TF_FROM_URL || lsGet("rg_tf", "M15");
  if (!TFS.includes(tf)) tf = "M15";
  let digits = 2;
  let online = false;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fx = (v, d = digits) => (v === null || v === undefined || isNaN(v) ? "--" : Number(v).toFixed(d));
  const money = (v) => (v === null || v === undefined ? "--" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const trendWord = (t) => (t === 1 ? "BULL" : t === -1 ? "BEAR" : "----");
  const trendCls = (t) => (t === 1 ? "bull" : t === -1 ? "bear" : "none");

  // Scroll ONLY the panel that holds el (never the page itself: scrollIntoView also scrolled the whole window,
  // which pushed the sidebar up with no way back).
  function scrollInside(el, block) {
    if (!el) return;
    for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const oy = getComputedStyle(p).overflowY;
      if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight + 1) {
        const r = el.getBoundingClientRect(), pr = p.getBoundingClientRect();
        let dy = 0;
        if (block === "start") dy = r.top - pr.top - 8;
        else if (r.top < pr.top) dy = r.top - pr.top - 8;
        else if (r.bottom > pr.bottom) dy = Math.min(r.bottom - pr.bottom + 8, r.top - pr.top - 8);
        if (dy) p.scrollBy({ top: dy, behavior: "smooth" });
        return;
      }
    }
  }
  // safety net: if anything still moves the page, put it back
  const unscroll = () => {
    if (window.scrollY || document.documentElement.scrollTop || document.body.scrollTop) {
      window.scrollTo(0, 0); document.documentElement.scrollTop = 0; document.body.scrollTop = 0;
    }
  };
  window.addEventListener("scroll", unscroll, { passive: true });
  setInterval(unscroll, 1500);

  async function api(path, body) {
    const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  // ================================================================ chart
  const wrap = $("#chartWrap");
  const chart = LightweightCharts.createChart($("#chart"), {
    autoSize: true,
    localization: { locale: "en-US" },
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#8a9bb8", fontSize: 11,
      fontFamily: '"Segoe UI", system-ui, sans-serif' },
    grid: { vertLines: { color: "rgba(148,163,184,0.06)" }, horzLines: { color: "rgba(148,163,184,0.06)" } },
    rightPriceScale: { borderColor: "rgba(148,163,184,0.15)", scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: "rgba(148,163,184,0.15)", timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 7 },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "rgba(45,212,191,0.45)", labelBackgroundColor: "#0f766e" },
      horzLine: { color: "rgba(45,212,191,0.45)", labelBackgroundColor: "#0f766e" },
    },
  });
  const LC = LightweightCharts;
  const CANDLE_OPTS = {
    upColor: "#26d7a0", downColor: "#ff5a78", borderUpColor: "#26d7a0", borderDownColor: "#ff5a78",
    wickUpColor: "#26d7a0", wickDownColor: "#ff5a78", priceLineColor: "#fbbf24", priceLineWidth: 1,
  };
  let candles = chart.addSeries(LC.CandlestickSeries, CANDLE_OPTS, 0);
  let markerApi = LC.createSeriesMarkers(candles, []);
  window.__rg = { chart, get candles() { return candles; } };        // debugging handle

  // Self-heal: the candle series must always live in pane 0. If anything ever detaches it,
  // wipe every series and start the price chart again (the next load refills everything).
  function candlesHealthy() {
    try {
      const p0 = chart.panes()[0];
      return !!p0 && p0.getSeries().includes(candles);
    } catch (e) { return false; }
  }
  function rebuildChart(reason) {
    console.warn("rebuilding chart:", reason);
    for (const pane of chart.panes()) {
      for (const s of pane.getSeries()) { try { chart.removeSeries(s); } catch (e) { /* ignore */ } }
    }
    try { chart.removeSeries(candles); } catch (e) { /* already detached */ }
    indSeries.clear(); priceLines = []; paneKey = null; levelsKey = ""; markersKey = "";
    candles = chart.addSeries(LC.CandlestickSeries, Object.assign({}, CANDLE_OPTS, { priceFormat: pricePF() }), 0);
    markerApi = LC.createSeriesMarkers(candles, []);
    loadedTf = null; lastBarT = null;
  }

  let loadedTf = null, lastBarT = null, lastBars = [];
  const indSeries = new Map();         // id -> {series, label, color, pane}
  let paneNames = [], paneKey = null;  // pane index (1..n) -> name
  let priceLines = [], levelsKey = "", markersKey = "";
  let zones = [], vlines = [], profiles = [];
  let chartBusy = false;

  const pricePF = () => ({ type: "price", precision: digits, minMove: Math.pow(10, -digits) });
  const oscPF = { type: "price", precision: 2, minMove: 0.01 };

  function setPrecision(d) {
    digits = d;
    candles.applyOptions({ priceFormat: pricePF() });
    indSeries.forEach((o) => { if (!o.pane) o.series.applyOptions({ priceFormat: pricePF() }); });
  }

  // One chart request at a time. A forced reload while another is running is queued, not run in parallel.
  let chartQueued = false, chartSeq = 0;
  async function loadChart(force = false) {
    if (chartBusy) { if (force) chartQueued = true; return; }
    chartBusy = true;
    const seq = ++chartSeq;
    try {
      if (!candlesHealthy()) { rebuildChart("candle series detached"); force = true; }
      const want = tf;
      const d = await api(`/api/chart?tf=${want}&n=800`);
      if (d.tf !== tf || seq !== chartSeq) return;
      const bars = d.bars.map((b) => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] }));
      if (!bars.length) return;
      if (force || loadedTf !== d.tf || lastBarT === null || bars[bars.length - 1].time < lastBarT) {
        candles.setData(bars);
        if (loadedTf !== d.tf) chart.timeScale().scrollToRealTime();
        loadedTf = d.tf;
      } else {
        for (const b of bars) if (b.time >= lastBarT) candles.update(b);
      }
      lastBarT = bars[bars.length - 1].time;
      lastBars = bars;
      try { applyOverlays(d); } catch (e) { console.warn("overlay error", e); paneKey = null; }
      updateLegend(null);
      if (!candlesHealthy()) rebuildChart("after overlays");
    } catch (e) { console.warn(e); }
    finally {
      chartBusy = false;
      if (chartQueued) { chartQueued = false; loadChart(true); }
    }
  }

  function clearIndicators() {
    priceLines.forEach((x) => { try { x.series.removePriceLine(x.pl); } catch (e) { /* gone */ } });
    priceLines = [];
    // Remove EVERY series except the candles, pane by pane (also ones we may have lost track of).
    // Empty panes are then removed by the library itself. Never call chart.removePane(): in v5 it
    // drops a pane without detaching its series, which is what could strand the candle series.
    for (const pane of chart.panes().slice().reverse()) {
      for (const s of pane.getSeries()) {
        if (s !== candles) { try { chart.removeSeries(s); } catch (e) { /* gone */ } }
      }
    }
    indSeries.clear();
    levelsKey = "";
  }

  function applyOverlays(d) {
    const hists = d.hists || [];
    // ---- panes: one per indicator name that asked for its own panel
    const names = [];
    for (const x of [...d.lines, ...hists]) if (x.pane && !names.includes(x.pane)) names.push(x.pane);
    const pk = names.join("|");
    const rebuilt = pk !== paneKey;
    if (rebuilt) { clearIndicators(); paneNames = names; paneKey = pk; }
    const paneIdx = (name) => (name ? paneNames.indexOf(name) + 1 : 0);

    // ---- lines + histograms
    // Lightweight Charts joins a line across whitespace points (and a transparent-segment trick breaks when the
    // gap starts off-screen), so a line with gaps (None from the plugin) is drawn as one series per segment.
    function segments(raw) {
      const segs = [];
      let cur = null;
      for (const p of raw) {
        if (p[1] === null) { cur = null; continue; }
        if (!cur) { cur = []; segs.push(cur); }
        cur.push({ time: p[0], value: p[1] });
      }
      return segs.slice(-40);                 // enough history; keeps the number of series bounded
    }
    const seen = new Set();
    for (const ln of d.lines) {
      const segs = segments(ln.data);
      segs.forEach((seg, k) => {
        const id = segs.length > 1 ? `${ln.id}#${seg[0].time}` : ln.id;   // keyed by start time: stable
        const isLast = k === segs.length - 1;
        seen.add(id);
        let o = indSeries.get(id);
        if (!o) {
          const dots = ln.style === "dots";
          const sr = chart.addSeries(LC.LineSeries, {
            color: ln.color, lineWidth: ln.width || 2, priceLineVisible: false, lastValueVisible: isLast && ln.axis !== false,
            title: isLast && ln.axis !== false ? ln.label : "", crosshairMarkerVisible: false, lineVisible: !dots, pointMarkersVisible: dots,
            pointMarkersRadius: 1.6, lineType: ln.style === "step" ? LC.LineType.WithSteps : LC.LineType.Simple,
            lineStyle: ln.style === "dashed" ? LC.LineStyle.Dashed : LC.LineStyle.Solid,
            ...(ln.autoscale === false ? { autoscaleInfoProvider: () => null } : {}),
            priceFormat: ln.pane ? oscPF : pricePF(),
          }, paneIdx(ln.pane));
          o = { series: sr, label: ln.label, color: ln.color, pane: ln.pane || "", base: ln.id, last: isLast, axis: ln.axis !== false };
          indSeries.set(id, o);
        } else if (o.last !== isLast) {
          o.series.applyOptions({ lastValueVisible: isLast && o.axis, title: isLast && o.axis ? ln.label : "" });
          o.last = isLast;
        }
        o.series.setData(seg);
      });
    }
    for (const hs of hists) {
      seen.add(hs.id);
      let o = indSeries.get(hs.id);
      if (!o) {
        const s = chart.addSeries(LC.HistogramSeries, {
          priceLineVisible: false, lastValueVisible: true, title: hs.label, priceFormat: oscPF,
        }, paneIdx(hs.pane));
        o = { series: s, label: hs.label, color: "#60a5fa", pane: hs.pane };
        indSeries.set(hs.id, o);
      }
      o.series.setData(hs.data.map((p) => ({ time: p[0], value: p[1], color: p[2] })));
    }
    for (const [id, o] of indSeries) {
      if (!seen.has(id)) { try { chart.removeSeries(o.series); } catch (e) { /* gone */ } indSeries.delete(id); }
    }
    if (rebuilt) {
      const ps = chart.panes();
      ps.forEach((p, i) => p.setStretchFactor(i === 0 ? Math.max(3, (ps.length - 1) * 1.1) : 1));
    }

    // ---- horizontal levels (price chart or indicator panes)
    const lk = JSON.stringify(d.levels);
    if (lk !== levelsKey) {
      levelsKey = lk;
      priceLines.forEach((x) => { try { x.series.removePriceLine(x.pl); } catch (e) { /* gone */ } });
      priceLines = [];
      for (const lv of d.levels) {
        let target = candles;
        if (lv.pane) {
          const o = [...indSeries.values()].find((x) => x.pane === lv.pane);
          if (!o) continue;
          target = o.series;
        }
        const pl = target.createPriceLine({
          price: lv.price, color: lv.color, lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
          axisLabelVisible: !lv.pane, title: lv.label || "",
        });
        priceLines.push({ series: target, pl });
      }
    }
    // ---- markers
    chartSignals = d.signals || [];
    renderSignalCard();
    const mk = JSON.stringify(d.markers);
    if (mk !== markersKey) {
      markersKey = mk;
      markerApi.setMarkers(d.markers.map((m) => ({ time: m.time, position: m.position, color: m.color, shape: m.shape, text: m.text })));
    }
    zones = d.zones;
    vlines = d.vlines || [];
    profiles = d.profiles || [];
    drawZones();
    const inds = (S ? S.plugins : []).filter((p) => p.kind === "indicator");
    const nScore = inds.filter((p) => p.score && !p.error).length;
    $("#layersLabel").textContent = `LAYERS ${inds.filter((p) => p.enabled && !p.error).length}/${inds.length}` + (nScore ? ` · SCORE ${nScore}` : "");
  }

  const zc = $("#zones");
  function drawZones() {
    const w = wrap.clientWidth, h = wrap.clientHeight, dpr = window.devicePixelRatio || 1;
    if (zc.width !== Math.round(w * dpr) || zc.height !== Math.round(h * dpr)) {
      zc.width = Math.round(w * dpr); zc.height = Math.round(h * dpr);
      zc.style.width = w + "px"; zc.style.height = h + "px";
    }
    const ctx = zc.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!zones.length && !vlines.length && !profiles.length) return;
    const ts = chart.timeScale();
    const paneW = ts.width();
    const allH = h - ts.height();
    const p0H = chart.panes()[0] ? chart.panes()[0].getHeight() : allH;
    const half = Math.max(1, (ts.options().barSpacing || 6) / 2);
    ctx.font = '600 10.5px "Segoe UI", system-ui, sans-serif';
    ctx.textBaseline = "top";
    // period separators: across every pane
    for (const v of vlines) {
      let x = ts.timeToCoordinate(v.t);
      if (x === null) continue;
      x = Math.round(x - half) + 0.5;
      if (x < 0 || x > paneW) continue;
      ctx.strokeStyle = hexA(v.color, 0.45);
      ctx.lineWidth = 1;
      ctx.setLineDash(v.dashed ? [4, 4] : []);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, allH); ctx.stroke();
      ctx.setLineDash([]);
      if (v.label) {
        const tw = ctx.measureText(v.label).width;
        ctx.fillStyle = "rgba(8,16,34,0.85)";
        ctx.fillRect(x + 3, p0H - 18, tw + 8, 15);
        ctx.fillStyle = hexA(v.color, 0.95);
        ctx.fillText(v.label, x + 7, p0H - 16);
      }
    }
    // zones + profiles: price pane only
    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, paneW, p0H); ctx.clip();
    for (const z of zones) {
      const y1 = candles.priceToCoordinate(z.hi), y2 = candles.priceToCoordinate(z.lo);
      if (y1 === null || y2 === null) continue;
      let x1 = ts.timeToCoordinate(z.t_from);
      x1 = x1 === null ? 0 : x1 - half;
      let x2 = paneW;
      if (z.t_to !== null && z.t_to !== undefined) {
        const c = ts.timeToCoordinate(z.t_to);
        if (c === null) continue;
        x2 = Math.min(paneW, c + half);
      }
      x1 = Math.max(0, x1);
      if (x1 >= paneW || x2 <= 0 || x2 <= x1) continue;
      const top = Math.min(y1, y2), hh = Math.max(1, Math.abs(y2 - y1));
      ctx.fillStyle = z.color;
      ctx.fillRect(x1, top, x2 - x1, hh);
      if (z.border) {
        ctx.strokeStyle = hexA(z.border, 0.7);
        ctx.lineWidth = 1;
        ctx.strokeRect(Math.round(x1) + 0.5, Math.round(top) + 0.5, Math.round(x2 - x1) - 1, Math.round(hh) - 1);
      } else {
        ctx.fillStyle = z.color.replace(/[\d.]+\)$/, "0.55)");
        ctx.fillRect(x1, top, x2 - x1, 1);
        ctx.fillRect(x1, top + hh - 1, x2 - x1, 1);
      }
      if (z.label && x2 - x1 > 30) {
        ctx.fillStyle = hexA(z.border || "#e6edf7", 0.95);
        const ly = top - 14 < 26 ? top + 4 : top - 14;
        ctx.fillText(z.label, x1 + 4, ly);
      }
    }
    for (const pf of profiles) {
      const maxV = Math.max(1e-9, ...pf.rows.map((r) => r[2]));
      let left = null, maxW = paneW * (pf.width || 0.22);
      if (pf.t_from != null) {                           // a day's own profile, drawn over that day's candles
        const a = ts.timeToCoordinate(pf.t_from), b = pf.t_to != null ? ts.timeToCoordinate(pf.t_to) : paneW;
        if (a === null || b === null || b < 0 || a > paneW) continue;
        left = a - half;
        maxW = Math.max(4, (b + half - left) * (pf.width || 0.6));
      }
      const hl = new Set(pf.highlight || []);
      pf.rows.forEach((r, i) => {
        const y1 = candles.priceToCoordinate(r[1]), y2 = candles.priceToCoordinate(r[0]);
        if (y1 === null || y2 === null) return;
        const top = Math.min(y1, y2), hh = Math.max(1, Math.abs(y2 - y1) - 1);
        const bw = (r[2] / maxV) * maxW;
        ctx.fillStyle = hexA(hl.has(i) ? pf.hcolor : pf.color, hl.has(i) ? 0.55 : 0.28);
        ctx.fillRect(left === null ? paneW - bw : left, top, bw, hh);
      });
    }
    ctx.restore();
  }
  function hexA(hex, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex || `rgba(148,163,184,${a})`;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(drawZones));
  new ResizeObserver(() => requestAnimationFrame(drawZones)).observe(wrap);
  setInterval(drawZones, 400);   // follows price-scale changes too

  function updateLegend(param) {
    let bar = null;
    if (param && param.time !== undefined) bar = param.seriesData.get(candles);
    if (!bar && lastBars.length) bar = lastBars[lastBars.length - 1];
    let html = "";
    if (bar) {
      const cls = bar.close >= bar.open ? "c-bull" : "c-bear";
      html += `<span>O <b>${fx(bar.open)}</b></span><span>H <b>${fx(bar.high)}</b></span><span>L <b>${fx(bar.low)}</b></span><span>C <b class="${cls}">${fx(bar.close)}</b></span>`;
    }
    const lg = new Map();                     // base line id -> {label, color, value}
    for (const o of indSeries.values()) {
      if (o.pane || o.axis === false) continue;
      const key = o.base || o.label;
      const e = lg.get(key) || { label: o.label, color: o.color, v: "" };
      if (param && param.time !== undefined) {
        const p = param.seriesData.get(o.series);
        if (p && p.value !== undefined) e.v = " " + fx(p.value);
      }
      lg.set(key, e);
    }
    for (const e of lg.values()) {
      html += `<span class="lg-layer"><i class="sw" style="background:${esc(e.color)}"></i>${esc(e.label)}${e.v}</span>`;
    }
    $("#legend").innerHTML = html;
  }
  chart.subscribeCrosshairMove(updateLegend);

  // ---- signal details (Analyser Signals / any plugin that returns Overlay.signals)
  let chartSignals = [];
  let sigSel = null;                                  // selected signal key "source|t"
  const sigKey = (g) => `${g.source}|${g.t}`;
  function sigDetailHtml(g) {
    const li = (arr, ic) => arr.map((x) => `<li>${ic} ${esc(x)}</li>`).join("") || '<li class="muted">-</li>';
    const res = g.result === "open" ? "RUNNING" : g.result === "TRAIL" ? `⇢ TRAIL ${g.r >= 0 ? "+" : ""}${g.r}R`
      : g.result === "TP" ? `✓ TP +${g.r}R` : `✗ SL ${g.r}R`;
    const when = new Date(g.t * 1000).toLocaleString("en-US", { timeZone: "UTC", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
    const groups = (g.groups || []).map((x) => `<span class="sgd-g">${catChip(x.category)} ${x.agree}/${x.total} ${Math.round(x.pct)}%</span>`).join("");
    return `<div class="sgd">
      <div class="sgd-top"><span class="sigchip ${g.side === 1 ? "buy" : "sell"}">${g.side === 1 ? "BUY" : "SELL"}</span>
        <b class="mono">${fx(g.entry)}</b><span class="muted">${when} · ${esc(g.source)}</span>
        ${g.score == null ? "<b>Breakout</b>" : `<b>Score ${Math.round(g.score)}%</b><span class="muted">pass ${Math.round(g.passing)}%</span>`}
        <span>SL ${fx(g.sl)} <i class="muted">${esc(g.sl_name)}</i></span><span>TP ${fx(g.tp)} <i class="muted">${esc(g.tp_name)}</i></span>
        <b class="${g.result === "open" ? "" : (g.r || 0) > 0 ? "c-bull" : "c-bear"}">${res}</b></div>
      ${groups ? `<div class="sgd-groups">${groups}</div>` : ""}
      <div class="sgd-cols">
        <div><div class="sgd-h c-bull">✓ SANG-AYON (${g.why_for.length})</div><ul>${li(g.why_for, "✓")}</ul></div>
        <div><div class="sgd-h c-bear">✗ SALUNGAT (${g.why_against.length})</div><ul>${li(g.why_against, "✗")}</ul>
          ${g.neutral && g.neutral.length ? `<div class="sgd-h muted">• NEUTRAL (${g.neutral.length})</div><ul class="muted">${li(g.neutral, "•")}</ul>` : ""}</div>
        <div class="${g.cautions && g.cautions.length ? "sgd-warn" : ""}"><div class="sgd-h c-warn">⚠ CAUTION (${(g.cautions || []).length})</div>
          <ul>${g.cautions && g.cautions.length ? li(g.cautions, "⚠") : '<li class="muted">walang babala: malinis na setup</li>'}</ul></div>
      </div></div>`;
  }
  function renderSignalCard() {
    const has = chartSignals.length > 0;
    $("#sigCard").hidden = !has;
    $("#sigStatCard").hidden = !has;
    $("#bottomRow").classList.toggle("with-sig", has);
    if (!has) return;
    const rows = [...chartSignals].sort((a, b) => b.t - a.t);
    if (!sigSel || !rows.some((g) => sigKey(g) === sigSel)) sigSel = sigKey(rows[0]);
    // ---- list (bottom-left)
    const list = $("#sigDetails");
    const keepScroll = list.scrollTop;
    list.innerHTML = rows.slice(0, 30).map((g) => {
      const k = sigKey(g);
      const dt = new Date(g.t * 1000);
      const when = `${String(dt.getUTCDate()).padStart(2, "0")} ${String(dt.getUTCHours()).padStart(2, "0")}:${String(dt.getUTCMinutes()).padStart(2, "0")}`;
      const rr = g.r == null ? "" : `${g.r > 0 ? "+" : ""}${Math.round(g.r * 10) / 10}R`;
      const resTip = g.result === "open" ? "Bukas pa: hindi pa tumatama sa TP o SL"
        : (g.r || 0) > 0 ? `Kung pinasok ang ${g.side === 1 ? "BUY" : "SELL"} na ito: umabot muna sa TP (${rr})` : `Kung pinasok ang ${g.side === 1 ? "BUY" : "SELL"} na ito: tumama muna sa SL (${rr}). Ang BUY / SELL ay direksyon ng signal, ang WIN / LOSS ay resulta nito.`;
      const res = g.result === "open" ? '<span class="rchip open" title="' + resTip + '">RUN</span>'
        : `<span class="rchip ${(g.r || 0) > 0 ? "win" : "loss"}" title="${esc(resTip)}">${(g.r || 0) > 0 ? "WIN" : "LOSS"} ${rr}</span>`;
      const warn = g.cautions && g.cautions.length ? `<span class="c-warn" title="${esc(g.cautions.join(" · "))}">⚠${g.cautions.length}</span>` : "<span></span>";
      return `<div class="sg-h ${k === sigSel ? "sel" : ""}" data-k="${esc(k)}"><span class="sigchip ${g.side === 1 ? "buy" : "sell"}" title="${esc((g.side === 1 ? "BUY" : "SELL") + " signal mula sa " + (g.source || "?"))}">${g.side === 1 ? "BUY" : "SELL"}</span>
        <span class="mono">${fx(g.entry)}</span><span class="muted">${when}</span><b>${g.score == null ? '<i class="muted" title="breakout signal">BOX</i>' : Math.round(g.score) + "%"}</b>${warn}
        <span class="sg-cnt" title="✓ = dahilan na sang-ayon · ✗ = dahilan na salungat"><i class="c-bull">✓${g.why_for.length}</i> <i class="c-bear">✗${g.why_against.length}</i></span>${res}</div>`;
    }).join("");
    const doneS = rows.slice(0, 30).filter((g) => g.result !== "open" && g.r != null);
    const w = doneS.filter((g) => g.r > 0).length, sumR = doneS.reduce((a, g) => a + g.r, 0);
    const sumEl = $("#sigSum");
    if (sumEl) sumEl.innerHTML = doneS.length ? `huling ${doneS.length}: <b class="c-bull">${w} WIN</b> · <b class="c-bear">${doneS.length - w} LOSS</b> · <b class="${sumR >= 0 ? "c-bull" : "c-bear"}">${sumR >= 0 ? "+" : ""}${Math.round(sumR * 10) / 10}R</b>` : "";
    list.scrollTop = keepScroll;
    // ---- detail (bottom-right)
    const sel = rows.find((g) => sigKey(g) === sigSel);
    $("#sigDetail").innerHTML = sel ? sigDetailHtml(sel) : "";
    // ---- score rate (right column)
    const done = chartSignals.filter((g) => g.result !== "open");
    const blk = (arr) => {
      const w = arr.filter((g) => (g.r || 0) > 0).length, R = arr.reduce((t, g) => t + (g.r || 0), 0);
      return { n: arr.length, w, l: arr.length - w, wr: arr.length ? Math.round(100 * w / arr.length) : null, R };
    };
    const row = (label, b, cls = "") => `<div class="ssr ${cls}"><span>${label}</span>
      <span class="ssr-bar">${b.wr === null ? "" : `<i style="width:${b.wr}%"></i>`}</span>
      <b>${b.wr === null ? "-" : b.wr + "%"}</b><span class="muted">W${b.w}/L${b.l}</span>
      <b class="${b.R >= 0 ? "c-bull" : "c-bear"}">${b.n ? (b.R >= 0 ? "+" : "") + b.R.toFixed(1) + "R" : "-"}</b></div>`;
    const sources = [...new Set(chartSignals.map((g) => g.source))];
    const open = chartSignals.length - done.length;
    $("#sigStats").innerHTML = row("Lahat", blk(done), "all")
      + row("Malinis", blk(done.filter((g) => !(g.cautions && g.cautions.length))))
      + row("May ⚠", blk(done.filter((g) => g.cautions && g.cautions.length)), "warn")
      + (sources.length > 1 ? sources.map((src) => row(esc(src.replace(" Signals", "")), blk(done.filter((g) => g.source === src)))).join("") : "")
      + `<div class="ssr-foot muted">${done.length} sarado · ${open} tumatakbo · chart TF ${tf}</div>`;
  }
  document.addEventListener("click", (e) => {
    const h = e.target.closest("#sigDetails .sg-h");
    if (!h) return;
    sigSel = h.dataset.k;
    renderSignalCard();
  });
  chart.subscribeClick((param) => {                    // click an arrow's bar -> select it in the panel below
    if (!param || param.time === undefined) return;
    const g = chartSignals.find((x) => x.t === param.time);
    if (!g) return;
    sigSel = sigKey(g);
    renderSignalCard();
    const el = document.querySelector(`#sigDetails .sg-h.sel`);
    if (el) scrollInside(el, "nearest");
    scrollInside($("#sigCard"), "nearest");
  });

  // TF buttons
  const AUTO_TFS = ["M1", "M5", "M15", "M30", "H1"];
  let AUTO_TF_NOW = null;                  // set by renderAT (the AUTO TRADE timeframe)
  const autoTf = () => AUTO_TF_NOW;
  function tfBtns(cur, attr) {
    const at = autoTf();
    return TFS.map((t) => `<button ${attr}="${t}" class="${t === cur ? "on" : ""} ${t === at ? "auto-tf" : ""}" title="${t === at ? "AUTO TRADE timeframe" : ""}">${t}${t === at ? "<i>⚡</i>" : ""}</button>`).join("");
  }
  function renderTfs() {
    $("#tfs").innerHTML = tfBtns(tf, "data-tf");
    renderTradeBtns();
  }
  function renderTradeBtns() {
    const at = autoTf();
    const one = (btn, lbl, t) => {
      if (!$(btn)) return;
      const isAuto = t === at, ok = AUTO_TFS.includes(t) && !!at;
      $(btn).classList.toggle("on", isAuto);
      $(btn).disabled = !at || isAuto || !ok;
      $(lbl).textContent = !at ? "AUTO n/a" : isAuto ? `AUTO: ${t}` : ok ? `AUTO → ${t}` : `AUTO: ${at}`;
      $(btn).title = !at ? "AUTO TRADE not available" : isAuto ? `Dito papasok ang AUTO TRADE (${t})` :
        ok ? `Gawing ${t} ang AUTO TRADE timeframe (ngayon: ${at})` : `Hindi puwede ang ${t} sa AUTO (M1–H1 lang). Ngayon: ${at}`;
    };
    one("#btnTrade1", "#trade1Lbl", tf);
    if (split) one("#btnTrade2", "#trade2Lbl", tf2);
  }
  async function setAutoTf(t) {
    if (!AT || !AUTO_TFS.includes(t) || t === autoTf()) return;
    const on = AT.enabled;
    showModal(`AUTO TRADE timeframe → ${t}?`,
      `<p>Papasok ang AUTO sa signals ng <b>${t}</b> (sa pagsara ng bawat ${t} bar) sa halip na ${esc(autoTf())}.</p>
       ${on ? `<p class="c-warn"><b>Naka-ON ang AUTO${T && !T.paper ? " sa LIVE account" : ""}.</b> Mula sa susunod na sarado na ${t} bar ito magsisimula; hindi kukunin ang mga lumang arrow.</p>` : ""}
       <p class="muted small">Pareho pa rin ang lahat ng filter at gate. Ang score at ang chart ay hindi nagbabago.</p>`,
      `Gamitin ang ${t}`, async () => {
        const r = await api("/api/auto/settings", { tf: t }).catch(() => ({ ok: false }));
        hideModal();
        await pollAT();
        renderTfs(); if (split) renderC2Tfs();
        if (!r.ok) showModal("AUTO TRADE", '<p class="c-bear">Hindi na-save.</p>', "", null);
      }, on && T && !T.paper);
  }
  $("#btnTrade1").addEventListener("click", () => setAutoTf(tf));
  $("#tfs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tf]");
    if (b) setTf(b.dataset.tf);
  });
  function setTf(t) {
    if (t === tf) return;
    tf = t; if (!TF_FROM_URL) lsSet("rg_tf", t); renderTfs();
    levelsKey = ""; markersKey = "";
    loadChart(true);
  }
  // ---- 2 SCREEN: a second chart with its own timeframe (candles, price-chart indicators, levels, arrows)
  let split = lsGet("rg_split", "0") === "1" && !TF_FROM_URL;
  let tf2 = lsGet("rg_tf2", "H1");
  if (!TFS.includes(tf2)) tf2 = "H1";
  let c2 = null, c2Candles = null, c2Markers = null, c2Lines = new Map(), c2PLs = [], c2Key = "", c2Busy = false, c2Fit = true;
  function renderC2Tfs() { $("#c2Tfs").innerHTML = tfBtns(tf2, "data-tf2"); $("#c2Title").textContent = `${(S && S.symbol) || ""} · ${tf2}`; renderTradeBtns(); }
  function c2Init() {
    if (c2) return;
    c2 = LC.createChart($("#chart2"), {
      autoSize: true, localization: { locale: "en-US" },
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#8a9bb8", fontSize: 11 },
      grid: { vertLines: { color: "rgba(148,163,184,0.06)" }, horzLines: { color: "rgba(148,163,184,0.06)" } },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.15)", scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: "rgba(148,163,184,0.15)", timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 7 },
      crosshair: { mode: LC.CrosshairMode.Normal },
    });
    c2Candles = c2.addSeries(LC.CandlestickSeries, Object.assign({}, CANDLE_OPTS, { priceFormat: pricePF() }), 0);
    c2Markers = LC.createSeriesMarkers(c2Candles, []);
  }
  async function loadC2() {
    if (!split || c2Busy || document.hidden) return;
    c2Busy = true;
    try {
      c2Init();
      const want = tf2;
      const d = await api(`/api/chart?tf=${want}&n=500`);
      if (!d || d.tf !== tf2 || !d.bars || !d.bars.length) return;
      c2Candles.setData(d.bars.map((b) => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] })));
      const seen = new Set();
      for (const ln of d.lines || []) {
        if (ln.pane) continue;                                   // price-chart lines only (panes stay on screen 1)
        seen.add(ln.id);
        let sr = c2Lines.get(ln.id);
        if (!sr) {
          sr = c2.addSeries(LC.LineSeries, { color: ln.color, lineWidth: ln.width || 2, priceLineVisible: false,
            lastValueVisible: false, title: "", crosshairMarkerVisible: false,
            lineType: ln.style === "step" ? LC.LineType.WithSteps : LC.LineType.Simple,
            lineStyle: ln.style === "dashed" ? LC.LineStyle.Dashed : LC.LineStyle.Solid }, 0);
          c2Lines.set(ln.id, sr);
        }
        const pts = [];
        for (const p of ln.data) pts.push(p[1] == null ? { time: p[0] } : { time: p[0], value: p[1] });
        sr.setData(pts);
      }
      for (const [id, sr] of c2Lines) if (!seen.has(id)) { try { c2.removeSeries(sr); } catch (x) { /* gone */ } c2Lines.delete(id); }
      const key = JSON.stringify(d.levels) + JSON.stringify(d.markers);
      if (key !== c2Key) {
        c2Key = key;
        c2PLs.forEach((pl) => { try { c2Candles.removePriceLine(pl); } catch (x) { /* gone */ } });
        c2PLs = (d.levels || []).filter((lv) => !lv.pane).map((lv) => c2Candles.createPriceLine({
          price: lv.price, color: lv.color, lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: lv.label || "" }));
        c2Markers.setMarkers((d.markers || []).map((m) => ({ time: m.time, position: m.position, color: m.color, shape: m.shape, text: m.text })));
      }
      if (c2Fit) { c2.timeScale().scrollToRealTime(); c2Fit = false; }
    } catch (e) { console.warn("chart 2", e); }
    finally { c2Busy = false; }
  }
  function setSplit(on) {
    split = on;
    if (!TF_FROM_URL) lsSet("rg_split", on ? "1" : "0");
    $("#chart2Box").hidden = !on;
    $("#chartsRow").classList.toggle("split", on);
    $("#btnSplit").classList.toggle("on", on);
    if (on) { renderC2Tfs(); c2Fit = true; loadC2(); }
  }
  $("#btnSplit").addEventListener("click", () => setSplit(!split));
  $("#c2Tfs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tf2]");
    if (!b || b.dataset.tf2 === tf2) return;
    tf2 = b.dataset.tf2; lsSet("rg_tf2", tf2);
    c2Lines.forEach((sr) => { try { c2.removeSeries(sr); } catch (x) { /* gone */ } });
    c2Lines.clear(); c2Key = ""; c2Fit = true;
    renderC2Tfs(); loadC2();
  });
  $("#btnTrade2").addEventListener("click", () => setAutoTf(tf2));
  $("#btnPop").addEventListener("click", () => {
    const t = split ? tf2 : (tf === "H1" ? "M5" : "H1");
    window.open(`/?tf=${t}`, `reygun_${t}`, "width=1400,height=900");
  });
  setInterval(loadC2, 3000);
  renderTfs();
  if (split) setTimeout(() => setSplit(true), 0);

  // Layers menu
  $("#btnLayers").addEventListener("click", (e) => {
    e.stopPropagation();
    const m = $("#layersMenu");
    m.hidden = !m.hidden;
    if (!m.hidden) { renderLayersMenu(); placeLayersMenu(); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".layers-wrap")) $("#layersMenu").hidden = true;
  });
  // ---------------------------------------------------------------- plugin rows (LAYERS menu + Plugins page)
  const openEditors = new Set();
  const CAT_ORDER = ["Signals", "Structure", "TP/SL", "Reversal", "Trend", "Momentum", "Volatility", "Volume", "Order Flow", "Levels", "SMC", "Intermarket", "Time", "Strategy", "Other"];
  // What each group is FOR (shown as group headers, tags and in the score breakdown)
  const CAT_INFO = {
    Signals: ["#22c55e", "Posibleng BUY/SELL na may entry, SL at target profit — signal lang, walang trade"],
    "TP/SL": ["#2dd4bf", "Exits: posibleng take profit, stop loss at trailing (VWAP bands, order blocks)"],
    Structure: ["#60a5fa", "Price structure: trend lines at breakout nila (kasama sa STRUCTURE score)"],
    Reversal: ["#fb7185", "Posibleng pagbaliktad: sweep + reject, pin bar, engulfing, divergence, exhaustion, climax volume"],
    "Order Flow": ["#e879f9", "Buyers vs sellers mula sa ticks: delta, absorption, stacked imbalance, divergence"],
    Intermarket: ["#f59e0b", "Ibang market na kasabay o salungat ng gold: DXY, EURUSD, silver, USDJPY, yields"],
    Trend: ["#38bdf8", "Direksyon ng market at lakas ng trend: pataas ba o pababa?"],
    Momentum: ["#a78bfa", "Bilis ng galaw, overbought at oversold: lumalakas ba o humihina?"],
    Volatility: ["#fbbf24", "Laki ng galaw, bands at channels: extremes, breakouts, at laki ng SL"],
    Volume: ["#34d399", "Dami ng galaw (tick volume), flow at value areas: kinukumpirma ba ang galaw?"],
    Levels: ["#f472b6", "Mga presyong support at resistance: pivots, kahapon, fibonacci"],
    SMC: ["#818cf8", "Smart money: structure, liquidity, premium at discount"],
    Time: ["#94a3b8", "Oras: sessions at hati ng araw o linggo"],
    Strategy: ["#fb923c", "Strategy alerts: signal lang, walang trade"],
    Other: ["#94a3b8", ""],
  };
  const catColor = (c) => (CAT_INFO[c] || CAT_INFO.Other)[0];
  const catChip = (c) => `<span class="cat" style="--c:${catColor(c)}">${esc(String(c).toUpperCase())}</span>`;
  const catSort = (x, y) => (CAT_ORDER.indexOf(x) + 99) % 99 - (CAT_ORDER.indexOf(y) + 99) % 99;
  const TFS_VOTE = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];

  function scoreNote(p, ctfs) {
    if (!p.can_score) return "Walang boto sa score ang indicator na ito (drawing lang).";
    if (!ctfs.length) return p.module ? "SCORE: sariling check sa default na TF nito." : "SCORE: ayon sa vote timeframe sa ibaba.";
    if (p.module) {
      const ok = ctfs.filter((t) => ["H4", "H1", "M15", "M5"].includes(t));
      return ok.length ? `SCORE: sa ${ok.join(", ")} lang din${ok.length < ctfs.length ? " (H4/H1/M15/M5 lang ang may structure data)" : ""}.`
        : "SCORE: walang structure data sa napiling TF, kaya default TF ang gamit ng check.";
    }
    const hid = p.enabled ? "" : " CHART naka-OFF: hindi makikita sa chart pero bumibilang sa score" + (p.score ? "." : " kapag naka-ON ang SCORE.");
    return `SCORE: sa ${ctfs.join(", ")} lang din${ctfs.length > 1 ? " · isang boto lang (majority ng mga TF)" : ""}.${hid}`;
  }

  function pluginRow(p, where) {
    const k = esc(p.key);
    const isInd = p.kind === "indicator";
    const scoreTip = !isInd ? "" : p.can_score
      ? (p.module ? `Adds its own check to the analyser score${(p.chart_tfs || []).length ? " on " + p.chart_tfs.join(", ") : ""}`
        : `Votes on ${(p.chart_tfs || []).length ? p.chart_tfs.join(", ") + " (one vote, majority)" : p.vote_tf || "M15"} in the ${p.category} group`)
      : "This indicator has no direction, so it can't vote";
    const params = Object.keys(p.defaults || {});
    const hasEdit = params.length > 0 || p.votes || isInd;
    const ctfs = p.chart_tfs || [];
    const edited = params.some((n) => p.params[n] !== p.defaults[n]) || (p.votes && p.vote_tf && p.vote_tf !== "M15") || ctfs.length > 0;
    const tfTag = isInd && ctfs.length ? ` <span class="tfonly ${ctfs.includes(tf) ? "" : "off"}" title="${ctfs.includes(tf) ? "drawn on this chart" : "NOT drawn on " + tf + " (other timeframes only)"}">📺 ${ctfs.map(esc).join(" ")}</span>` : "";
    const open = openEditors.has(where + p.key);
    let html = `<div class="prow" data-key="${k}">
      <div class="pinfo">
        <div class="nm">${catChip(isInd ? p.category || "Other" : "Strategy")} ${esc(p.name)}${p.purpose ? ` <span class="purpose">${esc(p.purpose)}</span>` : ""}${tfTag}${edited ? ' <span class="edited">edited</span>' : ""}${p.error ? ' <span class="c-bear">(error)</span>' : ""}</div>
        <div class="ds">${esc(p.description)}</div>
        ${where === "page" ? `<div class="file">plugins\\${esc(p.key.split(".")[0])}.py</div>` : ""}
        ${p.error ? `<div class="er">${esc(p.error)}</div>` : ""}
      </div>
      <div class="pbtns">
        <label class="tg chart" title="${isInd ? "Draw on the chart" : "Run this strategy"}"><input type="checkbox" data-act="chart" ${p.enabled ? "checked" : ""}><span>${isInd ? "CHART" : "ON"}</span></label>
        ${isInd ? `<label class="tg score ${p.can_score ? "" : "na"}" title="${esc(scoreTip)}"><input type="checkbox" data-act="score" ${p.score ? "checked" : ""} ${p.can_score ? "" : "disabled"}><span>SCORE</span></label>` : ""}
        ${hasEdit ? `<button class="gear ${open ? "on" : ""}" data-act="gear" title="Settings">⚙</button>` : '<span class="gear-sp"></span>'}
      </div>
    </div>`;
    if (hasEdit && open) {
      html += `<div class="pedit" data-key="${k}">` + params.map((n) => {
        const d = p.defaults[n], v = p.params[n];
        const lab = n.replace(/_/g, " ");
        if (typeof d === "boolean") {
          return `<label class="pf"><span>${esc(lab)}</span><input type="checkbox" data-param="${esc(n)}" ${v ? "checked" : ""}></label>`;
        }
        const isInt = Number.isInteger(d) && !String(d).includes(".");
        return `<label class="pf"><span>${esc(lab)}</span><input type="number" data-param="${esc(n)}" value="${esc(v)}" step="${isInt ? 1 : "any"}"
          title="default ${esc(d)}"></label>`;
      }).join("") +
      (isInd ? `<div class="pf pf-tfs"><span>timeframe ng indicator: chart + score (walang check = lahat sa chart)</span><div class="tfchips">${TFS_VOTE.map((t) =>
        `<label class="tfchip"><input type="checkbox" data-ctf="${t}" ${ctfs.includes(t) ? "checked" : ""}><b>${t}</b></label>`).join("")}</div>
        <div class="score-note small" data-mod="${p.module ? 1 : 0}">${scoreNote(p, ctfs)}</div></div>` : "") +
      (p.votes ? `<label class="pf vtf" ${ctfs.length ? "hidden" : ""}><span>vote timeframe (para sa SCORE)</span><select data-param="__vote_tf">${TFS_VOTE.map((t) => `<option ${t === p.vote_tf ? "selected" : ""}>${t}</option>`).join("")}</select></label>` : "") +
      `<div class="pactions"><button class="btn-sm" data-act="apply">Apply</button><button class="btn-sm ghost" data-act="reset">Reset to default</button></div></div>`;
    }
    return html;
  }

  function pluginGroups(list, where) {
    const cats = [...new Set(list.map((p) => (p.kind === "indicator" ? p.category || "Other" : "Strategy")))].sort(catSort);
    return cats.map((c) => `<div class="lm-title grp-title" style="--c:${catColor(c)}"><b>${esc(c.toUpperCase())}</b><span>${esc((CAT_INFO[c] || CAT_INFO.Other)[1])}</span></div>` +
      list.filter((p) => (p.kind === "indicator" ? p.category || "Other" : "Strategy") === c).map((p) => pluginRow(p, where)).join("")).join("");
  }

  // keep the LAYERS dropdown fully on screen whether the button sits left or right in the header
  function placeLayersMenu() {
    const m = $("#layersMenu"), btn = $("#btnLayers");
    m.style.left = "0px"; m.style.right = "auto";
    const br = btn.getBoundingClientRect();
    const w = Math.min(520, window.innerWidth - 24);
    m.style.width = w + "px";
    let left = 0;
    if (br.left + w > window.innerWidth - 12) left = window.innerWidth - 12 - w - br.left;
    if (br.left + left < 12) left = 12 - br.left;
    m.style.left = left + "px";
  }

  function renderLayersMenu() {
    const inds = (S ? S.plugins : []).filter((p) => p.kind === "indicator");
    const m = $("#layersMenu");
    if (!inds.length) { m.innerHTML = `<div class="lm-empty">No indicator plugins in plugins\\ folder.</div>`; return; }
    m.innerHTML = `<div class="lm-help"><b>CHART</b> = draw it · <b>SCORE</b> = count it in the analyser (works even when not drawn) · ⚙ = settings</div>` +
      pluginGroups(inds, "menu");
  }

  async function pluginAction(e, where) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const row = el.closest("[data-key]");
    const key = row && row.dataset.key;
    if (!key) return;
    const act = el.dataset.act;
    if (act === "gear") {
      const id = where + key;
      openEditors.has(id) ? openEditors.delete(id) : openEditors.add(id);
      rerenderPlugins(where);
      return;
    }
    if ((act === "chart" || act === "score") && e.type !== "change") return;
    if ((act === "apply" || act === "reset") && e.type !== "click") return;
    const body = { key };
    if (act === "chart") body.chart = el.checked;
    if (act === "score") body.score = el.checked;
    if (act === "reset") body.reset = true;
    if (act === "apply") {
      const box = el.closest(".pedit");
      body.params = {};
      box.querySelectorAll("[data-param]").forEach((inp) => {
        const n = inp.dataset.param;
        if (n === "__vote_tf") body.vote_tf = inp.value;
        else body.params[n] = inp.type === "checkbox" ? inp.checked : inp.value;
      });
      if (box.querySelector("[data-ctf]")) body.chart_tfs = [...box.querySelectorAll("[data-ctf]")].filter((x) => x.checked).map((x) => x.dataset.ctf);
    }
    await api("/api/plugin", body).catch(() => {});
    await pollState();
    levelsKey = ""; markersKey = "";
    await loadChart(true);
    rerenderPlugins(where);
  }
  function rerenderPlugins(where) {
    if (where === "menu" && !$("#layersMenu").hidden) renderLayersMenu();
    renderViews._pk = "";
    renderViews();
  }
  for (const [sel, where] of [["#layersMenu", "menu"], ["#plugList", "page"]]) {
    $(sel).addEventListener("change", (e) => {
      const c = e.target.closest("[data-ctf]");
      if (c) {                                   // live preview of what the score will use
        const box = c.closest(".pedit");
        const key = box.dataset.key;
        const p = (S ? S.plugins : []).find((x) => x.key === key);
        const sel_ = [...box.querySelectorAll("[data-ctf]")].filter((x) => x.checked).map((x) => x.dataset.ctf);
        if (p) box.querySelector(".score-note").innerHTML = scoreNote(p, sel_);
        const v = box.querySelector(".vtf");
        if (v) v.hidden = sel_.length > 0;
        return;
      }
      pluginAction(e, where);
    });
    $(sel).addEventListener("click", (e) => {
      e.stopPropagation();                 // keep the LAYERS menu open while editing
      if (e.target.closest("button[data-act]")) pluginAction(e, where);
    });
  }

  // ================================================================ navigation
  $("#nav").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (!b) return;
    document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n === b));
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + b.dataset.view));
    lsSet("rg_view", b.dataset.view);
    if (b.dataset.view === "overview") requestAnimationFrame(drawZones);
    if (b.dataset.view === "signals") { seenSignals = S ? S.signals.length : 0; $("#sigBadge").hidden = true; }
    renderViews();
  });
  const startView = lsGet("rg_view", "overview");
  const nb = document.querySelector(`.nav[data-view="${startView}"]`);
  if (nb && startView !== "overview") nb.click();

  // refresh: rebuild the whole chart from scratch and reload data + indicators
  async function refreshChart() {
    const btn = $("#btnRefresh");
    btn.classList.add("spin");
    rebuildChart("manual refresh");
    await pollState();
    await loadChart(true);
    chart.timeScale().scrollToRealTime();
    setTimeout(() => btn.classList.remove("spin"), 500);
  }
  $("#btnRefresh").addEventListener("click", refreshChart);

  // header buttons
  $("#btnRec").addEventListener("click", () => api("/api/record", {}).then(pollState).catch(() => {}));
  $("#btnPause").addEventListener("click", () => api("/api/pause", {}).then(pollState).catch(() => {}));
  $("#btnFaster").addEventListener("click", () => api("/api/speed", { dir: 1 }).then(pollState).catch(() => {}));
  $("#btnSlower").addEventListener("click", () => api("/api/speed", { dir: -1 }).then(pollState).catch(() => {}));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea")) return;
    const i = "1234567".indexOf(e.key);
    if (i >= 0) setTf(TFS[i]);
    if (e.key === " " && S && !S.live) { e.preventDefault(); $("#btnPause").click(); }
    if (e.key === "r" || e.key === "R") refreshChart();
  });

  // ================================================================ state
  let seenSignals = 0, logKey = "";
  async function pollState() {
    try {
      S = await api("/api/state");
      if (!online) { online = true; }
      if (S.digits !== undefined && S.digits !== digits) setPrecision(S.digits);
      checkBuild(S);
      render();
    } catch (e) {
      online = false;
      $("#liveDot").className = "dot off";
      $("#liveTitle").textContent = "DISCONNECTED";
      $("#liveSub").textContent = "Naka-restart ang server (kusang babalik sa loob ng ~10 s). Kung hindi, buksan ulit ang run_superchart_live.bat.";
    }
  }

  function render() {
    const s = S;
    // header
    const dot = $("#liveDot");
    if (s.paused) { dot.className = "dot off"; $("#liveTitle").textContent = "PAUSED"; }
    else if (s.live) { dot.className = "dot live"; $("#liveTitle").textContent = "LIVE ANALYSIS ACTIVE"; }
    else { dot.className = "dot sim"; $("#liveTitle").textContent = s.source === "REPLAY" ? `REPLAY x${s.speed}` : `DEMO MODE x${s.speed}`; }
    const plugOn = s.plugins.filter((p) => p.enabled).length;
    $("#liveSub").textContent = `${s.symbol} · ${s.source} · ${s.status} · ${plugOn} plugin${plugOn === 1 ? "" : "s"} on`;
    const act = (s.sessions || []).filter((x) => x.active);
    $("#session").textContent = act.length ? act.map((x) => x.name).join(" + ") + (act.length > 1 ? " overlap" : "") : s.session;
    $("#sessBadges").innerHTML = (s.sessions || []).map((x) => `<span class="sess ${x.active ? "on" : ""}" style="--c:${esc(x.color)}"
      title="${esc(x.name)} ${String(x.start).padStart(2, "0")}:00-${String(x.end).padStart(2, "0")}:00 UTC"><i></i>${esc(x.name)}</span>`).join("");
    $("#replayCtl").hidden = s.live;
    $("#speed").textContent = "x" + s.speed;
    $("#btnPause").textContent = s.paused ? "▶" : "❚❚";
    $("#btnRec").classList.toggle("on", !!s.recording);
    $("#recLabel").textContent = s.recording ? `REC ${s.recording.count.toLocaleString("en-US")}` : "REC";
    $("#ver").textContent = "v" + s.version;
    $("#ver").title = `REYGUN SuperChart v${s.version} · build ${s.build || "?"}`;
    if ($("#verBuild")) $("#verBuild").textContent = s.build || "?";
    $("#chTitle").textContent = `${s.symbol.toUpperCase()} · ${s.live ? "MT5" : s.source} · ${(s.live ? s.status.split(" #")[0] : "SIMULATED").toUpperCase()}`;
    document.title = (s.tick ? `${fx(s.tick.bid)} ${s.symbol} · ` : "") + `REYGUN SuperChart v${s.version}`;

    // KPI cards
    const tk = s.tick;
    $("#pxLabel").textContent = `${s.symbol} price`;
    $("#pxVal").textContent = tk ? fx(tk.bid) : "--";
    const ch = s.change_pct;
    $("#pxSub").innerHTML = ch === null || ch === undefined ? "--" :
      `<span class="${ch >= 0 ? "c-bull" : "c-bear"}">${ch >= 0 ? "+" : ""}${ch.toFixed(3)}%</span> latest M1 candle`;
    $("#pxIcon").className = "kicon " + (ch >= 0 ? "up" : "down");

    const r = s.read;
    const score = r ? r.score : 0;
    const mods = new Set(s.modules || []);
    const ov = r && r.overall != null ? r.overall : 0;
    const pass = r ? r.passing : (s.scoring ? s.scoring.passing : 70);
    $("#scoreVal").textContent = r && r.bias ? `${ov.toFixed(0)}%` : "--";
    const fg = $("#ringFg");
    fg.style.strokeDashoffset = String(113.1 * (1 - Math.min(100, ov) / 100));
    fg.style.stroke = r && r.passed ? "var(--bull)" : ov >= pass * 0.75 ? "var(--accent)" : "var(--warn)";
    $("#scoreSub").innerHTML = !r || !r.bias ? "no bias" :
      `${r.bias === 1 ? "bullish" : "bearish"} · pass ${pass.toFixed(0)}% ${r.passed ? '<span class="c-bull">✓</span>' : '<span class="c-warn">✗</span>'}`;

    const h4 = s.tfs.H4, h1 = s.tfs.H1, m15 = s.tfs.M15;
    const bias = r ? r.bias : 0;
    $("#biasVal").innerHTML = bias ? `<span class="c-${bias === 1 ? "bull" : "bear"}">${bias === 1 ? "Bullish" : "Bearish"}</span>` : `<span class="muted">No bias</span>`;
    const aligned = [h4, h1, m15].filter((x) => x && x.trend === bias && bias !== 0).length;
    $("#biasSub").textContent = bias ? `H4·H1·M15 aligned ${aligned}/3` : "waiting for H4/H1 structure";
    $("#biasIcon").className = "kicon " + (bias === 1 ? "up" : bias === -1 ? "down" : "");

    $("#sprVal").textContent = tk ? fx(tk.ask - tk.bid) : "--";
    $("#sprSub").textContent = `${s.tps.toFixed(1)} ticks/s · poll ${s.poll_ms.toFixed(1)} ms`;

    if (s.account) {
      const a = s.account;
      const open = s.positions.reduce((t, p) => t + p.pnl, 0);
      $("#eqVal").textContent = money(a.equity);
      $("#eqSub").innerHTML = `bal ${money(a.balance)} · open <span class="${open >= 0 ? "c-bull" : "c-bear"}">${open >= 0 ? "+" : ""}${money(open)}</span>`;
    } else {
      $("#eqVal").textContent = "--";
      $("#eqSub").textContent = s.live ? "no account info" : "not available in " + s.source.toLowerCase();
    }

    // verdict
    const vc = $("#verdictCard");
    let cls = "", main = "--";
    if (r) {
      const v = r.verdict;
      if (v.includes("READY")) { cls = r.bias === 1 ? "long" : "short"; main = r.bias === 1 ? "LONG READY" : "SHORT READY"; }
      else if (v.startsWith("WATCH")) { cls = "watch"; main = "WATCH"; }
      else if (v.startsWith("WAIT")) { cls = "wait"; main = "WAIT"; }
      else { cls = ""; main = v.split(" - ")[0]; }
      $("#vText").textContent = (v.includes(" - ") ? v.split(" - ").slice(1).join(" - ") : v)
        + (r && r.cautions && r.cautions.length ? `  ·  ⚠ ${r.cautions.length} caution` : "");
    }
    vc.className = "card verdict " + cls;
    $("#vMain").textContent = main;
    $("#scoreBar").style.width = Math.min(100, ov) + "%";
    $("#passMark").style.left = pass + "%";
    $("#scoreBar").style.background = cls === "short" ? "var(--bear)" : cls === "long" ? "var(--bull)" : cls === "wait" ? "var(--warn)" : "var(--accent)";
    $("#navDot").classList.toggle("on", cls === "long" || cls === "short");
    $("#checks").innerHTML = r && r.lines.length ? r.lines.map((l) => `<li>${esc(l)}</li>`).join("") : `<li class="muted">no confluence yet</li>`;
    $("#votes").innerHTML = (r && r.cautions && r.cautions.length
      ? `<div class="caution-box"><div class="cb-h">⚠ CAUTION: posibleng reversal (${r.cautions.length})</div>${r.cautions.map((c) => `<div>⚠ ${esc(c)}</div>`).join("")}
         ${r.passed ? '<div class="cb-note">Pasado ang score, pero mag-ingat: huli na o may harang sa daan.</div>' : ""}</div>` : "")
      + (r && r.groups && r.groups.length ? votesHtml(r) : "");
    renderScoringEditor(s.scoring);
    renderActiveInds(s);

    // MTF
    $("#mtf").innerHTML = ["H4", "H1", "M15", "M5"].map((k) => {
      const t = s.tfs[k];
      if (!t) return "";
      const e = t.last;
      const brk = e ? `<span class="${e.dir === 1 ? "c-bull" : "c-bear"}">${e.kind} ${e.dir === 1 ? "▲" : "▼"}</span> ${fx(e.level)}` : '<span class="muted">-</span>';
      const pd = t.pd === null || t.pd === undefined ? null : Math.max(0, Math.min(1, t.pd));
      return `<div class="mtf-row"><span class="tfname">${k}</span><span class="chip ${trendCls(t.trend)}">${trendWord(t.trend)}</span>
        <span class="brk">${brk}</span>
        ${mods.has("premium_discount") ? `<span class="pdbar" title="premium/discount ${t.pd === null ? "-" : Math.round(t.pd * 100) + "%"}">${pd === null ? "" : `<i style="left:${pd * 100}%"></i>`}</span>` : "<span></span>"}</div>`;
    }).join("");

    // liquidity
    const liq = [];
    for (const k of (mods.has("liquidity") ? ["H1", "M15"] : [])) {
      const t = s.tfs[k];
      if (!t) continue;
      const tag = (x) => x.broken ? '<span class="tag broken">broken</span>' : x.swept ? '<span class="tag swept">swept</span>' : '<span class="tag">resting</span>';
      if (t.sh) liq.push(`<div class="liq-row"><span class="tfname">${k}</span><span class="c-bear">BSL</span><span class="px">${fx(t.sh.level)}</span>${tag(t.sh)}</div>`);
      if (t.sl) liq.push(`<div class="liq-row"><span class="tfname">${k}</span><span class="c-bull">SSL</span><span class="px">${fx(t.sl.level)}</span>${tag(t.sl)}</div>`);
      if (t.fvg_bull) liq.push(`<div class="liq-row"><span class="tfname">${k}</span><span class="c-bull">FVG+</span><span class="px">${fx(t.fvg_bull[0])} – ${fx(t.fvg_bull[1])}</span><span></span></div>`);
      if (t.fvg_bear) liq.push(`<div class="liq-row"><span class="tfname">${k}</span><span class="c-bear">FVG−</span><span class="px">${fx(t.fvg_bear[0])} – ${fx(t.fvg_bear[1])}</span><span></span></div>`);
    }
    if (r && r.pdh !== null && r.pdh !== undefined) {
      liq.push(`<div class="liq-row"><span class="tfname">D1</span><span class="c-mag">PDH</span><span class="px">${fx(r.pdh)}</span><span></span></div>`);
      liq.push(`<div class="liq-row"><span class="tfname">D1</span><span class="c-mag">PDL</span><span class="px">${fx(r.pdl)}</span><span></span></div>`);
    }
    $("#liq").innerHTML = liq.join("") || '<span class="muted">-</span>';
    $("#liqCard").hidden = !(mods.has("liquidity") || mods.has("prev_day"));
    $("#modNote").innerHTML = mods.size ? "" : 'Structure lang (price action + timeframe bias) ang binibilang ngayon. Bawat indicator na i-SCORE mo sa LAYERS ay papasok sa grupo nito (Trend, Momentum, Volatility, Volume, Levels), at ang SMC checks sa SMC group.';
    const hasVotes = !!(r && r.groups && r.groups.length);
    $("#modNote").hidden = mods.size > 0 || hasVotes;

    // strategies
    $("#stratCard").hidden = !s.strategies.length;
    $("#strats").innerHTML = s.strategies.map((st) => `
      <div class="liq-row" style="grid-template-columns:1fr auto">
        <span><b>${esc(st.name)}</b><br><span class="muted">${st.error ? '<span class="c-bear">error</span>' : esc(st.status.join(" · "))}</span></span>
        ${st.last ? `<span class="sigchip ${st.last.dir === 1 ? "buy" : "sell"}">${st.last.dir === 1 ? "BUY" : "SELL"}</span>` : ""}
      </div>`).join("");

    // positions
    $("#posCard").hidden = !s.positions.length;
    $("#pos").innerHTML = s.positions.map((p) => `<div class="pos-row">
      <span class="${p.side === "BUY" ? "c-bull" : "c-bear"}">${p.side} ${p.vol.toFixed(2)}</span>
      <span>@${fx(p.open)}</span><span class="muted">SL ${p.sl ? fx(p.sl) : "-"}</span>
      <span class="${p.pnl >= 0 ? "c-bull" : "c-bear"}">${p.pnl >= 0 ? "+" : ""}${money(p.pnl)}</span></div>`).join("");

    // log
    const lk = s.events.length + ":" + (s.events.length ? s.events[s.events.length - 1].wall : 0);
    if (lk !== logKey) {
      logKey = lk;
      const html = s.events.slice().reverse().slice(0, 120).map(evHtml).join("");
      $("#log").innerHTML = html;
      $("#logFull").innerHTML = html;
    }

    // badges
    const newSig = s.signals.length - seenSignals;
    const onSignals = !$("#view-signals").hidden;
    if (onSignals) seenSignals = s.signals.length;
    $("#sigBadge").hidden = onSignals || newSig <= 0;
    $("#sigBadge").textContent = String(newSig);
    $("#plugBadge").textContent = `${plugOn}/${s.plugins.length}`;

    renderViews();
  }

  function votesHtml(r) {
    const pass = r.passing;
    const head = r.bias ? `<div class="ov-line ${r.passed ? "pass" : "fail"}"><b>OVERALL ${r.overall.toFixed(0)}%</b>
        <span>pass ${pass.toFixed(0)}% ${r.passed ? "✓" : "✗"}</span>${r.trigger ? "" : '<span class="c-warn">no trigger yet</span>'}</div>` : "";
    return `<div class="votes-title">SCORE BREAKDOWN <span>bawat grupo = ilan ang sang-ayon sa bias ÷ ilan lahat · × timbang mo</span></div>` + head +
      r.groups.map((g) => {
        const pct = g.pct || 0;
        const off = g.weight === 0;
        const items = g.items.map((it) => {
          const agree = r.bias && it.dir === r.bias, against = r.bias && it.dir === -r.bias;
          const cls = agree ? "up" : against ? "dn" : "nt";
          const ic = it.check ? (agree ? "✓" : "✗") : it.dir === 1 ? "▲" : it.dir === -1 ? "▼" : "•";
          return `<span class="vote ${cls}" title="${esc(it.reason)}">${ic} ${esc(it.name)}</span>`;
        }).join("");
        return `<div class="vgroup ${off ? "off" : ""}"><div class="vhead">${catChip(g.category)}
            <span class="vcnt">${g.agree}/${g.total}</span>
            <span class="gbar"><i style="width:${pct}%;background:${catColor(g.category)}"></i><em style="left:${pass}%"></em></span>
            <span class="vpct">${pct.toFixed(0)}%</span><span class="vw" title="weight">×${g.weight}</span></div>
          <div class="vitems">${items}</div></div>`;
      }).join("") + lostHtml(r);
  }

  // ---- why the score is what it is: every item that does not agree and the exact points it costs
  const ALT_NAME = { "against/group": "neutral = kontra · bawat grupo", "ignore/group": "neutral = hindi bilang · bawat grupo",
    "against/indicator": "neutral = kontra · bawat indicator", "ignore/indicator": "neutral = hindi bilang · bawat indicator" };
  function lostHtml(r) {
    if (!r.bias || !r.lost) return "";
    const sc = (S && S.scoring) || {};
    const cur = `${sc.neutral || "against"}/${sc.mode || "group"}`;
    const top = r.lost.slice(0, 8).map((x) => `<tr><td>${catChip(x.category)} ${esc(x.name)}</td>
        <td class="${x.state === "neutral" ? "muted" : "c-bear"}">${x.state === "neutral" ? "• neutral" : "✗ kontra"}</td>
        <td class="num">−${x.points.toFixed(1)}</td></tr>`).join("");
    const nNeutral = r.lost.filter((x) => x.state === "neutral").reduce((a, x) => a + x.points, 0);
    const alts = Object.keys(ALT_NAME).map((k) => `<span class="sc-alt ${k === cur ? "cur" : ""}">${ALT_NAME[k]}: <b>${(r.alt[k] ?? 0).toFixed(0)}%</b></span>`).join("");
    const single = r.groups.filter((g) => g.weight > 0 && g.total === 1).length;
    const tips = [];
    if (nNeutral >= 5) tips.push(`Ang mga <b>neutral</b> na vote ay nagbabawas ng ${nNeutral.toFixed(0)} puntos (sa ngayon, bilang silang "hindi sang-ayon").`);
    if (single >= 2) tips.push(`${single} grupo ang may <b>iisang indicator</b> lang: 0% o 100% agad ang grupong iyon, kaya malaki ang talon ng score.`);
    const st = r.groups.find((g) => g.category === "Structure");
    if (st && st.pct < 100) tips.push(`Laging kasama ang <b>Structure</b> (HTF bias, H1, M15, trigger): ${st.agree}/${st.total} ngayon.`);
    return `<div class="sc-why"><div class="votes-title">BAKIT ${r.overall.toFixed(0)}% ANG SCORE? <span>ang bawat hindi sang-ayon at ilang puntos ang bawas nito</span></div>
      ${top ? `<table class="sc-lost"><tbody>${top}</tbody></table>` : '<div class="muted small">Lahat sang-ayon.</div>'}
      ${tips.length ? `<ul class="sc-tips">${tips.map((t) => `<li>${t}</li>`).join("")}</ul>` : ""}
      <div class="sc-alts"><span class="muted small">Kung ibang scoring option:</span>${alts}</div></div>`;
  }

  // ---- which indicators are ON (CHART / SCORE), their timeframes and their vote right now
  const MOD_ITEMS = { premium_discount: ["Premium/Discount"], liquidity: ["Liquidity sweep"], prev_day: ["Prev-day sweep"] };
  function renderActiveInds(s) {
    const box = $("#actInds");
    if (!box) return;
    const r = s.read || {};
    const bias = r.bias || 0;
    const byName = {};
    (r.groups || []).forEach((g) => g.items.forEach((it) => { byName[it.name] = it; }));
    const pv = s.plugin_votes || {};
    const on = (s.plugins || []).filter((p) => p.kind === "indicator" && (p.enabled || p.score));
    const strat = (s.plugins || []).filter((p) => p.kind !== "indicator" && p.enabled);
    if (!on.length && !strat.length) { box.innerHTML = '<span class="muted small">Walang naka-ON na indicator.</span>'; return; }
    on.sort((a, b) => (b.score - a.score) || a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
    const chipsHtml = on.map((p) => {
      let votes = [];
      if (p.score && !p.error) votes = p.module ? (MOD_ITEMS[p.module] || []).filter((n) => byName[n]).map((n) => byName[n]) : (pv[p.key] || []);
      const st = votes.map((v) => (!bias ? "nt" : v.dir === bias ? "up" : v.dir === -bias ? "dn" : "nt"));
      const cls = p.error ? "err" : !p.score ? "off" : !st.length ? "nt" : st.every((x) => x === "up") ? "up" : st.every((x) => x === "dn") ? "dn" : st.includes("up") && st.includes("dn") ? "mx" : st.includes("up") ? "up" : st.includes("dn") ? "dn" : "nt";
      const tfs = (p.chart_tfs && p.chart_tfs.length ? p.chart_tfs : p.vote_tf ? [p.vote_tf] : []).join("·");
      const arrow = votes.length ? votes.map((v) => v.dir === 1 ? "▲" : v.dir === -1 ? "▼" : "•").join("") : "";
      const tip = `${p.name} · ${p.category}${tfs ? " · " + tfs : ""} · ${p.enabled ? "CHART" : ""}${p.enabled && p.score ? " + " : ""}${p.score ? "SCORE" : ""}` +
        (votes.length ? "\n" + votes.map((v) => `${v.dir === 1 ? "▲" : v.dir === -1 ? "▼" : "•"} ${v.name}: ${v.reason || ""}`).join("\n") : "") + (p.error ? "\nERROR: " + p.error : "");
      return `<span class="ai-chip ${cls}" title="${esc(tip)}"><i></i>${esc(p.name)}${tfs ? `<em>${esc(tfs)}</em>` : ""}${arrow ? `<b>${arrow}</b>` : ""}<small>${p.enabled ? "C" : ""}${p.score ? "S" : ""}</small></span>`;
    }).join("");
    const nS = on.filter((p) => p.score).length, nC = on.filter((p) => p.enabled).length;
    box.innerHTML = `<span class="ai-lbl">NAKA-ON <span class="muted">${nS} score · ${nC} chart</span></span>${chipsHtml}` +
      (strat.length ? `<span class="ai-lbl">STRATEGY</span>${strat.map((p) => `<span class="ai-chip st ${p.error ? "err" : ""}" title="${esc(p.name)} (alerts / signals)"><i></i>${esc(p.name)}</span>`).join("")}` : "");
  }

  // ---- scoring settings (passing score + weight per group)
  const SCORE_GROUPS = ["Structure", "SMC", "Trend", "Momentum", "Volatility", "Volume", "Order Flow", "Levels", "Reversal", "Intermarket"];
  let scoringOpen = false;
  function renderScoringEditor(sc) {
    const box = $("#scoringEdit");
    if (!sc || !scoringOpen) { box.hidden = true; return; }
    if (!box.hidden && box.contains(document.activeElement)) return;     // don't overwrite while typing
    box.hidden = false;
    box.innerHTML = `<div class="se-row"><label class="pf"><span>Passing score %</span>
        <input type="number" min="0" max="100" step="1" data-s="passing" value="${sc.passing}"></label></div>
      <div class="se-row"><label class="pf"><span>Neutral na vote (0)</span><select data-s="neutral">
          <option value="against" ${sc.neutral !== "ignore" ? "selected" : ""}>bilang na hindi sang-ayon (dati)</option>
          <option value="ignore" ${sc.neutral === "ignore" ? "selected" : ""}>hindi bilang (abstain)</option></select></label>
        <label class="pf"><span>Paano pagsamahin</span><select data-s="mode">
          <option value="group" ${sc.mode !== "indicator" ? "selected" : ""}>bawat grupo (dati)</option>
          <option value="indicator" ${sc.mode === "indicator" ? "selected" : ""}>bawat indicator</option></select></label></div>
      <div class="se-sub">Timbang (weight) bawat grupo</div>
      <div class="se-grid">${SCORE_GROUPS.map((g) => `<label class="pf"><span>${catChip(g)}</span>
        <input type="number" min="0" max="10" step="0.5" data-w="${g}" value="${sc.weights[g] ?? 1}"></label>`).join("")}</div>
      <div class="se-hint">Weight 0 = hindi kasama ang grupo. Weight 2 = doble ang bigat. OVERALL = Σ(grupo% × weight) ÷ Σ weight
        (mga grupong may naka-SCORE lang).</div>
      <div class="pactions"><button class="btn-sm" data-act="save-scoring">Save</button>
        <button class="btn-sm ghost" data-act="reset-scoring">Reset (70%, all ×1, dati)</button></div>`;
  }
  $("#btnScoring").addEventListener("click", () => { scoringOpen = !scoringOpen; $("#btnScoring").classList.toggle("on", scoringOpen); renderScoringEditor(S && S.scoring); });
  $("#scoringEdit").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    let body;
    if (b.dataset.act === "reset-scoring") body = { reset: true };
    else {
      body = { passing: parseFloat($('#scoringEdit [data-s="passing"]').value), weights: {},
        neutral: $('#scoringEdit [data-s="neutral"]').value, mode: $('#scoringEdit [data-s="mode"]').value };
      document.querySelectorAll("#scoringEdit [data-w]").forEach((i) => { body.weights[i.dataset.w] = parseFloat(i.value); });
    }
    await api("/api/scoring", body).catch(() => {});
    document.activeElement && document.activeElement.blur();
    await pollState();
    renderScoringEditor(S.scoring);
  });

  function evHtml(e) {
    const tm = e.bar || new Date(e.wall * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (e.kind === "structure") {
      const up = e.dir === 1;
      const col = e.ev === "SWEEP" ? "c-warn" : up ? "c-bull" : "c-bear";
      const big = (e.tf === "H4" || e.tf === "H1") && (e.ev === "BOS" || e.ev === "CHoCH");
      return `<div class="ev ${big ? "big" : ""} ${e.hist ? "hist" : ""}"><span class="tm">${esc(tm)}</span><span class="tf">${esc(e.tf)}</span>
        <span class="kd ${col}">${up ? "▲" : "▼"} ${esc(e.ev)}</span><span class="tx">${esc(e.text)}${e.hist ? " (hist)" : ""}</span></div>`;
    }
    if (e.kind === "signal") {
      const up = e.dir === 1;
      const lv = [e.entry != null ? "@" + fx(e.entry) : "", e.sl != null ? "SL " + fx(e.sl) : "", e.tp != null ? "TP " + fx(e.tp) : ""].filter(Boolean).join(" ");
      return `<div class="ev big"><span class="tm">${esc(tm)}</span><span class="tf">${esc(e.tf || "")}</span>
        <span><span class="sigchip ${up ? "buy" : "sell"}">${up ? "BUY" : "SELL"}</span></span>
        <span class="tx"><b>${esc(e.plugin)}</b> ${esc(e.text)} <span class="c-info">${esc(lv)}</span></span></div>`;
    }
    const lab = e.kind === "error" ? "ERROR" : e.kind === "warn" ? "WARN" : "INFO";
    const cls = e.kind === "error" ? "c-bear" : e.kind === "warn" ? "c-warn" : "muted";
    return `<div class="ev ${e.kind}"><span class="tm">${esc(tm)}</span><span class="tf"></span><span class="kd ${cls}">${lab}</span><span class="tx">${esc(e.text)}</span></div>`;
  }

  // ================================================================ other views
  function renderViews() {
    if (!S) return;
    const s = S;
    const mods = new Set(s.modules || []);
    if (!$("#view-analyser").hidden) {
      const rows = ["H4", "H1", "M15", "M5"].map((k) => {
        const t = s.tfs[k];
        if (!t) return "";
        const e = t.last;
        const lq = (x, lab) => x ? `${lab} ${fx(x.level)} <span class="muted">${x.broken ? "broken" : x.swept ? "swept" : "resting"}</span>` : "-";
        return `<tr><td><b>${k}</b></td><td><span class="chip ${trendCls(t.trend)}" style="padding:3px 8px">${trendWord(t.trend)}</span></td>
          <td>${e ? `<span class="${e.dir === 1 ? "c-bull" : "c-bear"}">${e.kind} ${e.dir === 1 ? "▲" : "▼"}</span> ${fx(e.level)}` : "-"}</td>
          <td class="muted">${e ? esc(e.bar) : "-"}</td>
          <td>${!mods.has("premium_discount") ? '<span class="muted">off</span>' : t.pd === null || t.pd === undefined ? "-" : `<span class="${t.pd > 0.5 ? "c-warn" : "c-info"}">${Math.round(t.pd * 100)}% ${t.pd > 0.5 ? "premium" : "discount"}</span>`}</td>
          <td>${fx(t.atr)}</td>${!mods.has("liquidity") ? '<td class="muted">off</td><td class="muted">off</td><td class="muted">off</td><td class="muted">off</td>' : `<td class="c-bear">${lq(t.sh, "BSL")}</td><td class="c-bull">${lq(t.sl, "SSL")}</td>
          <td>${t.fvg_bull ? `<span class="c-bull">${fx(t.fvg_bull[0])}–${fx(t.fvg_bull[1])}</span>` : "-"}</td>
          <td>${t.fvg_bear ? `<span class="c-bear">${fx(t.fvg_bear[0])}–${fx(t.fvg_bear[1])}</span>` : "-"}</td>`}</tr>`;
      }).join("");
      $("#anTable").innerHTML = `<tr><th>TF</th><th>Trend</th><th>Last break</th><th>Bar</th><th>P/D</th><th>ATR</th><th>Buy-side liq.</th><th>Sell-side liq.</th><th>FVG +</th><th>FVG −</th></tr>${rows}`;
      const r = s.read;
      $("#anVerdict").innerHTML = r ? `<div class="v-main" style="font-size:18px">${esc(r.verdict)}</div>
        <p class="muted">Overall ${r.overall != null ? r.overall.toFixed(0) : "-"}% · pass ${r.passing}%</p><ul class="checks">${r.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "-";
    }
    if (!$("#view-analyser").hidden) {
      const m = new Set(s.modules || []);
      const on = (k) => m.has(k) ? '<span class="c-bull">ON</span>' : '<span class="muted">off</span>';
      const sc = s.scoring || { passing: 70, weights: {} };
      $("#rules").innerHTML = `
        <li><b>Bawat grupo</b> = ilan ang sang-ayon sa HTF bias ÷ ilan lahat sa grupo (hal. Trend 3 sa 5 = 60%). Neutral na vote: ${sc.neutral === "ignore" ? "<b>hindi bilang</b> (abstain)" : "<b>hindi sang-ayon</b>"} · ${sc.mode === "indicator" ? "bawat <b>indicator</b> ay isang boto" : "bawat <b>grupo</b> ay isang average"} (palitan sa ⚙ Scoring).</li>
        <li><b>Structure</b> (palagi): HTF bias · H1 aligned · M15 aligned · M5/M15 CHoCH/BOS trigger</li>
        <li><b>SMC</b>: Premium/Discount location ${on("premium_discount")} · liquidity sweep ${on("liquidity")}</li>
        <li><b>Trend, Momentum, Volatility, Volume, Levels</b>: mga indicator na naka-SCORE (Prev-day sweep ${on("prev_day")} ay nasa Levels)</li>
        <li><b>OVERALL</b> = Σ(grupo% × weight) ÷ Σ weight. Weights ngayon: ${SCORE_GROUPS.map((g) => `${g} ×${sc.weights[g] ?? 1}`).join(" · ")}</li>`;
      $("#rulesNote").textContent = `READY = may trigger + OVERALL ≥ ${sc.passing}%` + (m.has("premium_discount") ? " + tamang side ng range." : ".") +
        " Palitan ang passing score at weights sa ⚙ SCORING ng Confluence card.";
    }
    if (!$("#view-signals").hidden) {
      $("#sigList").innerHTML = s.signals.length ? s.signals.slice().reverse().map((g) => {
        const lv = [g.entry != null ? "entry " + fx(g.entry) : "", g.sl != null ? "SL " + fx(g.sl) : "", g.tp != null ? "TP " + fx(g.tp) : ""].filter(Boolean).join(" · ");
        return `<div class="sig"><div class="row1"><span class="sigchip ${g.dir === 1 ? "buy" : "sell"}">${g.dir === 1 ? "BUY" : "SELL"}</span>
          <span class="nm">${esc(g.plugin)}</span><span class="tm">${esc(g.tf)} · ${esc(g.bar)}</span></div>
          <div class="rs">${esc(g.reason)}</div>${lv ? `<div class="lv">${esc(lv)}</div>` : ""}</div>`;
      }).join("") : `<div class="empty">No signals yet. Switch on a strategy plugin in Plugins.</div>`;
    }
    if (!$("#view-plugins").hidden) {
      const key = JSON.stringify(s.plugins);
      const ae = document.activeElement;
      const typing = ae && $("#plugList").contains(ae) && ["INPUT", "SELECT", "TEXTAREA"].includes(ae.tagName) && ae.type !== "checkbox";
      if (key !== renderViews._pk && !typing) {
        renderViews._pk = key;
        $("#plugList").innerHTML = s.plugins.length ? pluginGroups(s.plugins, "page")
          : `<div class="empty">No plugins found in the plugins\\ folder.</div>`;
      }
    }
    if (!$("#view-tape").hidden) {
      $("#tape").innerHTML = s.tape.slice().reverse().map((t) => {
        const d = new Date(t.t);
        const tm = d.toISOString().substr(11, 12);
        const col = t.d > 0 ? "c-bull" : t.d < 0 ? "c-bear" : "";
        return `<div class="tk"><span class="tm">${tm}</span><span class="${col}">${t.d > 0 ? "▲" : t.d < 0 ? "▼" : ""}</span>
          <span class="${col}">${fx(t.bid)}</span><span class="muted">${fx(t.ask)}</span><span class="${t.ask - t.bid > 0.4 ? "c-warn" : "muted"}">${fx(t.ask - t.bid)}</span></div>`;
      }).join("");
      const up = s.uptime;
      $("#conn").innerHTML = `<div class="kv">
        <div>Source</div><div>${esc(s.source)}</div><div>Symbol</div><div>${esc(s.symbol)}</div>
        <div>Account / status</div><div>${esc(s.status)}</div><div>Ticks received</div><div>${s.total_ticks.toLocaleString("en-US")}</div>
        <div>Ticks / sec</div><div>${s.tps.toFixed(1)}</div><div>MT5 poll</div><div>${s.poll_ms.toFixed(1)} ms</div>
        <div>Uptime</div><div>${Math.floor(up / 3600)}h ${Math.floor(up / 60) % 60}m ${up % 60}s</div>
        <div>Recording</div><div>${s.recording ? `${esc(s.recording.path)}<br>${s.recording.count.toLocaleString("en-US")} ticks` : "off"}</div>
        ${s.progress !== null && s.progress !== undefined ? `<div>Replay</div><div>${(s.progress * 100).toFixed(1)}%</div>` : ""}</div>
        <button class="btn" id="btnRec2">${s.recording ? "Stop recording" : "Start recording"}</button>`;
      $("#btnRec2").onclick = () => $("#btnRec").click();
    }
  }

  // clock
  function tickClock() {
    let t;
    try { t = new Date().toLocaleTimeString("en-US", { timeZone: "Asia/Manila", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (e) { t = new Date().toLocaleTimeString(); }
    $("#clock").textContent = "Manila " + t;
  }
  setInterval(tickClock, 1000); tickClock();

  // ================================================================ loops
  // ================================================================ trading
  let T = null;                 // latest /api/trading
  let acctDraft = null;         // accounts form being edited

  // ---- modal
  let modalOk = null;
  function showModal(title, html, okLabel = "Confirm", onOk = null, danger = false) {
    $("#cTitle").textContent = title;
    $("#cBody").innerHTML = html;
    const ok = $("#cOk");
    ok.textContent = okLabel;
    ok.className = "btn-sm" + (danger ? " danger" : "");
    ok.hidden = !onOk && okLabel === "";
    $("#cCancel").textContent = onOk ? "Cancel" : "Close";
    $("#cOk").hidden = !onOk;
    modalOk = onOk;
    $("#confirmModal").hidden = false;
  }
  function hideModal() { $("#confirmModal").hidden = true; modalOk = null; $("#confirmModal .modal-box").classList.remove("wide"); }
  // No separate ARM step any more (Eugene, 2026-10-01): the confirm of AUTO / NEWS TRADE / a BUY-SELL ticket arms
  // trading too. A HALT (daily loss / target / emergency stop) is NEVER cleared silently: that still asks.
  async function ensureArmed() {
    if (!T || T.disabled) return false;
    if (T.armed) return true;
    if (T.halted) {
      showModal("Naka-HALT ang trading", `<p class="c-bear">${esc(T.halted)}</p><p>Itinigil ito ng risk limit / emergency stop. Ituloy pa rin?</p>`,
        "Ituloy (resume)", async () => { await api("/api/trade/arm", { armed: true }); hideModal(); await pollTrading(); }, true);
      return false;
    }
    await api("/api/trade/arm", { armed: true }).catch(() => null);
    await pollTrading();
    return !!(T && T.armed);
  }
  window.rgModal = { show: showModal, hide: hideModal };          // for the separate page modules below
  $("#cCancel").addEventListener("click", hideModal);
  $("#cOk").addEventListener("click", async () => {
    const fn = modalOk;
    if (!fn) return hideModal();
    $("#cOk").disabled = true;
    try { await fn(); } finally { $("#cOk").disabled = false; }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#confirmModal").hidden) hideModal(); });

  function resultsHtml(res) {
    if (!res) return "";
    const rows = (res.results || []).map((r) => `<div class="res ${r.ok ? "ok" : "bad"}"><b>${esc(r.account)}</b>
      <span>${r.ok ? (r.ticket ? `#${r.ticket} · ${r.volume ?? ""} @ ${fx(r.price)}` : "done") : esc(r.error || "failed")}</span></div>`).join("");
    return (res.ok ? "" : `<div class="res bad"><b>Error</b><span>${esc(res.error || "some accounts failed")}</span></div>`) + rows;
  }

  async function pollTrading() {
    try { T = await api("/api/trading"); } catch (e) { T = null; }
    renderTrading();
  }

  function num(id) { const v = parseFloat($(id).value); return isNaN(v) ? 0 : v; }

  function renderTrading() {
    const on = T && !T.disabled;
    $("#tradeCard").hidden = !on;
    $("#navAccounts").hidden = !on;
    const safe = $("#safeBox");
    if (!on) {
      $("#groupsCard").hidden = true;
      safe.className = "safe";
      $("#safeTitle").textContent = "READ-ONLY";
      $("#safeText").innerHTML = "Analysis &amp; alerts only.<br>Never sends orders.";
      return;
    }
    // mode + arm
    $("#modePill").textContent = T.paper ? "PAPER" : "LIVE";
    $("#modePill").className = "mode-pill " + (T.paper ? "paper" : "live");
    $("#armSwitch").checked = T.armed;
    $("#armLabel").textContent = T.armed ? "ARMED" : "DISARMED";
    $("#tradeCard").classList.toggle("armed", T.armed);
    $("#tradeCard").classList.toggle("live", !T.paper);
    safe.className = "safe " + (T.armed ? (T.paper ? "paper" : "live") : "");
    $("#safeTitle").textContent = T.halted ? "TRADING HALTED" : T.armed ? (T.paper ? "PAPER TRADING ON" : "LIVE TRADING ON") : "TRADING OFF";
    $("#safeText").innerHTML = T.armed
      ? (T.paper ? "Simulated orders only.<br>No real money." : "BUY/SELL send real orders<br>to all enabled accounts.")
      : "Walang order hangga't hindi mo<br>i-ON ang AUTO o mag-BUY/SELL.";
    // prices + preview
    const tk = S && S.tick;
    const sl = num("#tSL"), tp = num("#tTP");
    $("#buyPx").textContent = tk ? fx(tk.ask) : "--";
    $("#sellPx").textContent = tk ? fx(tk.bid) : "--";
    if (tk) {
      const b = `BUY → SL ${sl ? fx(tk.ask - sl) : "none"} · TP ${tp ? fx(tk.ask + tp) : "none"}`;
      const s2 = `SELL → SL ${sl ? fx(tk.bid + sl) : "none"} · TP ${tp ? fx(tk.bid - tp) : "none"}`;
      $("#tPreview").innerHTML = `<span class="c-bull">${b}</span><span class="c-bear">${s2}</span>`;
    }
    const onlyArm = !T.armed && !T.halted && T.reason === "trading is not armed";      // the order confirm arms it
    $("#btnBuy").disabled = $("#btnSell").disabled = (!T.can_trade && !onlyArm) || !!T.halted;
    $("#tReason").textContent = T.halted ? `⛔ ${T.halted}` : T.can_trade || onlyArm ? "" : T.reason;
    $("#estopBox").hidden = false;
    $("#haltMsg").textContent = T.halted ? `⛔ ${T.halted}` : "";
    // accounts line
    const accts = [T.master, ...T.followers];
    $("#tAccts").innerHTML = accts.map((a) => {
      const lot = a.role === "master" ? num("#tLot") : a.lot;
      const bad = !a.connected || a.locked || (a.role !== "master" && !a.enabled);
      const why = !a.connected ? a.status : a.locked ? "locked" : (a.role !== "master" && !a.enabled) ? "off" : `${lot}`;
      return `<span class="achip ${bad ? "off" : ""}" title="${esc(a.locked || a.error || "")}">${esc(a.name)} <b>${esc(why)}</b></span>`;
    }).join("");
    $("#acctBadge").textContent = String(accts.length);
    renderGroups();
    if (!$("#view-accounts").hidden && !acctDraft) renderAccountsForm();
    renderAcctStatus();
  }

  function renderGroups() {
    const box = $("#groups");
    const gs = T.groups || [];
    $("#groupsCard").hidden = !gs.length;
    if (!gs.length) { box.innerHTML = ""; return; }
    const total = gs.reduce((t, g) => t + (g.pnl || 0), 0);
    $("#groupsSum").innerHTML = `${gs.length} open · <span class="${total >= 0 ? "c-bull" : "c-bear"}">${total >= 0 ? "+" : ""}${money(total)}</span>`;
    const editing = document.activeElement && box.contains(document.activeElement);
    if (editing) {                                   // don't wipe what you're typing; just refresh P/L
      gs.forEach((g) => { const el = box.querySelector(`[data-pnl="${g.gid}"]`); if (el) el.textContent = (g.pnl >= 0 ? "+" : "") + money(g.pnl); });
      return;
    }
    box.innerHTML = gs.map((g) => {
      const up = g.side === 1;
      const legs = g.legs.map((l) => `<span class="leg ${l.volume == null ? "gone" : ""}">${esc(l.account)} ${l.volume ?? "closed"}
        ${l.pnl != null ? `<i class="${l.pnl >= 0 ? "c-bull" : "c-bear"}">${l.pnl >= 0 ? "+" : ""}${money(l.pnl)}</i>` : ""}</span>`).join("");
      return `<div class="grp" data-gid="${esc(g.gid)}">
        <div class="grp-h"><span class="sigchip ${up ? "buy" : "sell"}">${up ? "BUY" : "SELL"}</span>
          <b class="mono">${g.volume}</b><span class="mono muted">@ ${fx(g.open)}</span>
          <span class="gstate ${g.unprotected && g.unprotected.length ? "bad" : ""}" title="lifecycle state">${g.unprotected && g.unprotected.length ? "⚠ NO SL" : esc(g.state || "")}</span>
          <span class="grp-pnl mono ${g.pnl >= 0 ? "c-bull" : "c-bear"}" data-pnl="${esc(g.gid)}">${g.pnl >= 0 ? "+" : ""}${money(g.pnl)}</span></div>
        <div class="grp-legs">${legs}</div>
        <div class="grp-edit">
          <label>SL<input type="number" step="0.01" data-f="sl" value="${g.sl ? g.sl.toFixed(digits) : ""}" placeholder="none"></label>
          <label>TP<input type="number" step="0.01" data-f="tp" value="${g.tp ? g.tp.toFixed(digits) : ""}" placeholder="none"></label>
          <button class="btn-sm ghost" data-act="modify" title="Apply SL/TP to all accounts">Set</button>
        </div>
        <div class="grp-btns">
          <button class="btn-sm ghost" data-act="be">Break-even</button>
          <button class="btn-sm ghost" data-act="half">Close 50%</button>
          <button class="btn-sm danger" data-act="close">Close</button>
        </div></div>`;
    }).join("");
  }

  $("#groups").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const card = b.closest("[data-gid]");
    const gid = card.dataset.gid;
    const act = b.dataset.act;
    b.disabled = true;
    let res;
    if (act === "close") res = await api("/api/trade/close", { gid, fraction: 1 }).catch((x) => ({ ok: false, error: String(x) }));
    if (act === "half") res = await api("/api/trade/close", { gid, fraction: 0.5 }).catch((x) => ({ ok: false, error: String(x) }));
    if (act === "be") res = await api("/api/trade/be", { gid }).catch((x) => ({ ok: false, error: String(x) }));
    if (act === "modify") {
      const sl = parseFloat(card.querySelector('[data-f="sl"]').value) || 0;
      const tp = parseFloat(card.querySelector('[data-f="tp"]').value) || 0;
      res = await api("/api/trade/modify", { gid, sl, tp }).catch((x) => ({ ok: false, error: String(x) }));
      document.activeElement && document.activeElement.blur();
    }
    b.disabled = false;
    if (res && !res.ok) showModal("Action failed", resultsHtml(res), "", null);
    await pollTrading();
  });

  $("#btnCloseAll").addEventListener("click", () => {
    showModal("Close ALL SuperChart trades?", `<p>Every trade opened from SuperChart will be closed on <b>all accounts</b>. Trades from your EAs are not touched.</p>`,
      "Close all", async () => {
        const r = await api("/api/trade/closeall", {}).catch((x) => ({ ok: false, error: String(x) }));
        showModal(r.ok ? "Closed" : "Close all: some failed", resultsHtml(r), "", null);
        await pollTrading();
      }, true);
  });

  $("#armSwitch").addEventListener("change", (e) => {
    const want = e.target.checked;
    e.target.checked = !want;                         // stays as it was until confirmed
    if (!want) { api("/api/trade/arm", { armed: false }).then(pollTrading); return; }
    const accts = [T.master, ...T.followers].filter((a) => a.connected && !a.locked && (a.role === "master" || a.enabled));
    const list = accts.map((a) => `<li><b>${esc(a.name)}</b> ${a.login ? "#" + esc(a.login) : ""} ${esc(a.server || "")} · lot ${a.role === "master" ? "from ticket" : esc(a.lot)}</li>`).join("");
    showModal(T.paper ? "Arm PAPER trading?" : "⚠ Arm LIVE trading?",
      `<p>${T.paper ? "Simulated orders only." : "BUY / SELL will send <b>real orders</b> to these accounts:"}</p><ul class="alist">${list}</ul>
       <p class="muted">Every order still needs a click and a confirm. Strategies never trade on their own. Trading is disarmed again when you restart the app.</p>`,
      T.paper ? "Arm paper" : "Arm LIVE trading",
      async () => { await api("/api/trade/arm", { armed: true }); hideModal(); await pollTrading(); }, !T.paper);
  });

  const newReqId = () => (crypto.randomUUID ? crypto.randomUUID() : "r" + Date.now() + Math.random().toString(16).slice(2));
  async function entry(side) {
    if (!T) return;
    if (!T.armed && !(await ensureArmed())) return;
    if (!T.can_trade) return showModal("Hindi makapag-trade", `<p class="c-bear">${esc(T.reason || "trading not available")}</p>`, "", null);
    const lot = num("#tLot"), sl = num("#tSL"), tp = num("#tTP");
    if (lot <= 0) return showModal("Lot size", "<p>Set a lot size above 0.</p>", "", null);
    const word = side === 1 ? "BUY" : "SELL";
    const request_id = newReqId();
    // 1) the SERVER checks everything and issues a one-time token (expires in ~15 s)
    const pr = await api("/api/trade/prepare", { side, lot, sl_dist: sl, tp_dist: tp, request_id }).catch((x) => ({ ok: false, error: String(x) }));
    if (!pr.ok) return showModal(`${word} blocked`, `<div class="res bad"><b>BLOCKED</b><span>${esc(pr.error || "failed")}</span></div>`, "", null);
    const pv = pr.preview || {};
    const rows = (pr.plan || []).map((a) => `<tr class="${a.ok ? "" : "skip"}"><td>${esc(a.name)}</td><td class="mono">${a.ok ? esc(a.lot) : "-"}</td>
      <td class="muted">${a.ok ? "will trade" : "skipped: " + esc(a.why)}</td></tr>`).join("");
    const noSL = !sl ? `<p class="c-warn">⚠ No stop loss.</p>` : "";
    const nw = newsWarn();
    const newsLine = nw && nw.active ? `<p class="c-warn">⚠ NEWS: ${esc(nw.currency)} ${esc(nw.title)} ${nw.mins >= 0 ? "in " + dur(nw.mins * 60) : "released " + dur(-nw.mins * 60) + " ago"}. Spread can widen and SL can slip.</p>` : "";
    const expires = Date.now() + (pr.expires_in || 15) * 1000;
    showModal(`${word} ${S.symbol}${T.paper ? " (paper)" : ""}`,
      `<p class="big ${side === 1 ? "c-bull" : "c-bear"}">${word} @ ~${pv.price ? fx(pv.price) : "market"}</p>
       <p>SL ${sl ? "$" + sl + " → ~" + fx(pv.sl) : "none"} · TP ${tp ? "$" + tp + " → ~" + fx(pv.tp) : "none"}${pv.rr ? " · RR 1:" + pv.rr : ""}
       <span class="muted">(same $ distance on every account, from its own fill)</span></p>${noSL}${newsLine}
       <table class="ptable">${rows}</table>
       <p class="muted small">Server-checked ✓ · confirmation valid for <b id="tokLeft">${pr.expires_in || 15}</b>s · request ${esc(request_id.slice(0, 8))}</p>`,
      `Confirm ${word}`,
      async () => {
        if (Date.now() > expires) return showModal(`${word}`, `<div class="res bad"><b>Expired</b><span>Confirmation expired: press ${word} again.</span></div>`, "", null);
        // 2) submit the token: the server executes once and invalidates it
        const r = await api("/api/trade/confirm", { token: pr.token, request_id, side, lot, sl_dist: sl, tp_dist: tp }).catch((x) => ({ ok: false, error: String(x) }));
        showModal(r.ok ? `${word} sent` : `${word} failed`, resultsHtml(r), "", null);
        await pollTrading();
      }, side === -1);
    const tick = setInterval(() => {
      const el = $("#tokLeft");
      if (!el) return clearInterval(tick);
      const left = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
      el.textContent = left;
      if (!left) { clearInterval(tick); el.parentElement.innerHTML = '<span class="c-bear">Expired: press BUY/SELL again.</span>'; }
    }, 500);
  }
  $("#btnBuy").addEventListener("click", () => entry(1));
  $("#btnSell").addEventListener("click", () => entry(-1));
  for (const id of ["#tLot", "#tSL", "#tTP"]) {
    $(id).value = lsGet("rg" + id, $(id).value);
    $(id).addEventListener("input", () => { lsSet("rg" + id, $(id).value); renderTrading(); });
  }

  // ---- accounts page
  const RISK_FIELDS = (a, isMaster, paper) => `
      <label class="pf"><span>Daily DD limit</span><span class="pair"><input type="number" step="0.1" min="0" data-k="daily_dd" value="${esc(a.daily_dd)}">
        <select data-k="dd_mode"><option ${a.dd_mode === "%" ? "selected" : ""}>%</option><option ${a.dd_mode === "$" ? "selected" : ""}>$</option></select></span></label>
      <label class="pf"><span>Daily TP target</span><span class="pair"><input type="number" step="0.1" min="0" data-k="daily_tp" value="${esc(a.daily_tp)}">
        <select data-k="tp_mode"><option ${a.tp_mode === "%" ? "selected" : ""}>%</option><option ${a.tp_mode === "$" ? "selected" : ""}>$</option></select></span></label>
      <label class="pf"><span>When hit, close</span><select data-k="close_scope">
        <option value="superchart" ${a.close_scope !== "all" ? "selected" : ""}>only SuperChart trades (default)</option>
        <option value="all" ${a.close_scope === "all" ? "selected" : ""}>ALL trades on the account (incl. other EAs + manual)</option></select></label>
      ${paper ? `<label class="pf"><span>Paper balance</span><input type="number" step="100" min="0" data-k="paper_balance" value="${esc(a.paper_balance)}"></label>` : ""}`;

  function renderAccountsForm() {
    if (!T || T.disabled) return;
    if (!acctDraft) acctDraft = JSON.parse(JSON.stringify(T.config));
    const m = acctDraft.master;
    $("#acctMode").textContent = T.paper ? "paper mode: orders are simulated" : "LIVE mode: real orders";
    let html = `<div class="acard master" data-id="master">
      <div class="ahead"><span class="role">MASTER</span><input class="aname" data-k="name" value="${esc(m.name)}">
        <span class="astat" id="st-master"></span></div>
      <div class="agrid">
        <label class="pf wide2"><span>Master MT5 terminal path (terminal64.exe)</span><input data-k="path" value="${esc(m.path || "")}" placeholder="blank = the MT5 that is already open"></label>
        <label class="pf"><span>Login # (check)</span><input type="number" data-k="login" value="${m.login || ""}" placeholder="optional"></label>
        <div class="pf symf"><span>Symbol</span><div class="symrow"><input data-k="symbol" value="${esc(m.symbol || "")}" placeholder="${esc(S ? S.symbol : "")}">
          <button class="btn-sm ghost symfind" data-act="findsym" data-acc="master" title="Find this broker's gold symbol (prefix/suffix)">🔍</button></div>
          <div class="symlist" hidden></div></div>
        ${RISK_FIELDS(m, true, T.paper)}</div>
      <div class="ahint">Ang master ang pinagkukunan ng chart at ng lahat ng entry. Kapag pinalitan mo ang path, login o symbol at nag-Save,
        lilipat agad ang chart sa MT5 na iyon. Hindi ito papayagan habang may bukas na SuperChart trade.</div></div>`;
    html += acctDraft.followers.map((f, i) => `<div class="acard" data-idx="${i}" data-id="${esc(f.id || "")}">
      <div class="ahead"><span class="role f">FOLLOWER</span><input class="aname" data-k="name" value="${esc(f.name)}">
        <label class="en"><input type="checkbox" data-k="enabled" ${f.enabled ? "checked" : ""}> copy trades</label>
        <span class="astat" id="st-${esc(f.id || "new" + i)}"></span></div>
      <div class="agrid">
        <label class="pf wide2"><span>MT5 terminal path (terminal64.exe)</span><input data-k="path" value="${esc(f.path)}" placeholder="C:\\MT5_Switch\\terminal64.exe"></label>
        <label class="pf"><span>Login # (check)</span><input type="number" data-k="login" value="${f.login || ""}" placeholder="optional"></label>
        <div class="pf symf"><span>Symbol</span><div class="symrow"><input data-k="symbol" value="${esc(f.symbol)}" placeholder="same as master">
          <button class="btn-sm ghost symfind" data-act="findsym" data-acc="${esc(f.id || "")}" title="Find this broker's gold symbol (prefix/suffix)">🔍</button></div>
          <div class="symlist" hidden></div></div>
        <label class="pf"><span>Fixed lot</span><input type="number" step="0.01" min="0" data-k="lot" value="${esc(f.lot)}"></label>
        ${RISK_FIELDS(f, false, T.paper)}
      </div>
      <div class="abtns"><button class="btn-sm ghost" data-act="reconnect">Reconnect</button>
        <button class="btn-sm ghost" data-act="unlock">Unlock</button>
        <button class="btn-sm danger" data-act="remove">Remove</button></div></div>`).join("");
    html += `<div class="acard master-btns"><button class="btn-sm ghost" data-act="unlock-master">Unlock master</button></div>`;
    $("#acctList").innerHTML = html;
    renderAcctStatus();
  }

  function readAccountsForm() {
    const val = (el) => (el.type === "checkbox" ? el.checked : el.type === "number" ? parseFloat(el.value || "0") : el.value);
    const out = JSON.parse(JSON.stringify(acctDraft));
    const mcard = $('#acctList .acard[data-id="master"]');
    mcard.querySelectorAll("[data-k]").forEach((el) => { out.master[el.dataset.k] = val(el); });
    document.querySelectorAll("#acctList .acard[data-idx]").forEach((card) => {
      const f = out.followers[+card.dataset.idx];
      card.querySelectorAll("[data-k]").forEach((el) => { f[el.dataset.k] = val(el); });
    });
    return out;
  }

  function renderAcctStatus() {
    if (!T || T.disabled || $("#view-accounts").hidden) return;
    for (const a of [T.master, ...T.followers]) {
      const el = document.getElementById("st-" + a.id);
      if (!el) continue;
      const pl = a.day_pl;
      el.className = "astat " + (a.locked || a.login_error ? "locked" : a.connected ? "ok" : "bad");
      el.innerHTML = !a.connected ? `● ${esc(a.status)} ${a.error ? "· " + esc(a.error) : ""}` :
        `● ${a.login ? "#" + esc(a.login) + " " : ""}${esc(a.server || "")} · bal ${money(a.balance)} · eq ${money(a.equity)} · day
         <b class="${pl >= 0 ? "c-bull" : "c-bear"}">${pl == null ? "-" : (pl >= 0 ? "+" : "") + money(pl)}</b>
         ${a.dd_amount ? ` · DD at -${money(a.dd_amount)}` : ""}${a.tp_amount ? ` · TP at +${money(a.tp_amount)}` : ""}
         ${!T.paper && !a.trade_allowed ? ' · <span class="c-warn">Algo Trading OFF</span>' : ""}
         ${a.locked ? ` · <span class="c-bear">LOCKED: ${esc(a.locked)}</span>` : ""}
         ${a.login_error ? ` · <span class="c-bear">${esc(a.login_error)}</span>` : ""}
         ${a.role === "master" && !T.paper ? `<br><span class="muted">terminal: ${esc(a.terminal_path || "default (already open)")}</span>` : ""}`;
    }
  }

  // ---- symbol finder (each broker names gold differently: XAUUSD_ph, XAUUSD.m, #XAUUSD, GOLD ...)
  async function findSymbols(accId) {
    if (!accId) return { ok: false, error: "Save the accounts first, then wait until this follower is connected" };
    return api("/api/trade/symbols", { id: accId }).catch((e) => ({ ok: false, error: String(e) }));
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest('#acctList [data-act="findsym"]');
    if (b) {
      const wrap = b.closest(".symf");
      const list = wrap.querySelector(".symlist");
      if (!list.hidden) { list.hidden = true; return; }
      list.hidden = false;
      list.innerHTML = '<div class="muted">searching this MT5…</div>';
      const r = await findSymbols(b.dataset.acc);
      if (!r.ok) { list.innerHTML = `<div class="c-bear">${esc(r.error || "failed")}</div>`; return; }
      if (!r.symbols.length) { list.innerHTML = `<div class="c-warn">no symbol with "${esc(r.base)}" on ${esc(r.account)}</div>`; return; }
      list.innerHTML = `<div class="muted">${esc(r.account)} · matches for ${esc(r.base)}:</div>` + r.symbols.map((x, i) =>
        `<button class="symopt ${x.tradable ? "" : "off"}" data-sym="${esc(x.name)}"><b>${esc(x.name)}</b>${x.current ? ' <i class="c-bull">in use</i>' : i === 0 ? ' <i class="c-bull">best</i>' : ""}
          <span class="muted">${esc(x.description || "")}${x.tradable ? "" : " · NOT tradable"}</span></button>`).join("");
      return;
    }
    const o = e.target.closest("#acctList .symopt");
    if (o) {
      const wrap = o.closest(".symf");
      wrap.querySelector('input[data-k="symbol"]').value = o.dataset.sym;
      wrap.querySelector(".symlist").hidden = true;
      $("#acctSaved").textContent = "symbol set · click Save accounts";
    }
  });
  $("#btnAutoSym").addEventListener("click", async () => {
    const cards = [...document.querySelectorAll("#acctList .acard")].filter((c) => c.querySelector('[data-act="findsym"]'));
    const notes = [];
    for (const c of cards) {
      const btn = c.querySelector('[data-act="findsym"]');
      if (btn.dataset.acc === "master") continue;           // the master symbol is the reference
      const r = await findSymbols(btn.dataset.acc);
      const name = c.querySelector(".aname").value;
      if (r.ok && r.best) {
        c.querySelector('input[data-k="symbol"]').value = r.best;
        notes.push(`${name}: ${r.best}`);
      } else notes.push(`${name}: ${r.error || "not found"}`);
    }
    $("#acctSaved").textContent = notes.length ? notes.join(" · ") + " → click Save accounts" : "no connected followers yet";
  });

  $("#btnTestConn").addEventListener("click", async () => {
    const box = $("#connTest");
    box.hidden = false;
    box.innerHTML = '<p class="muted">Checking every account… (no order is sent)</p>';
    const r = await api("/api/trade/test", { lot: num("#tLot") || 0.01 }).catch((e) => ({ ok: false, error: String(e), results: [] }));
    if (!r.results) { box.innerHTML = `<p class="c-bear">${esc(r.error || "test failed")}</p>`; return; }
    const ready = r.results.filter((x) => x.ok).length;
    box.innerHTML = `<div class="ct-head ${r.ok ? "c-bull" : "c-warn"}">${r.ok ? "✓ LAHAT HANDA" : "⚠ MAY KAILANGANG AYUSIN"} · ${ready}/${r.results.length} account(s) ready${r.paper ? " · PAPER mode (simulated)" : ""}
        <span class="muted">· walang order na ipinadala</span></div>` +
      r.results.map((a) => `<div class="ct-acct ${a.ok ? "ok" : a.skipped ? "skip" : "bad"}">
        <div class="ct-name"><b>${a.ok ? "✓" : a.skipped ? "–" : "✗"} ${esc(a.name)}</b> <span class="muted">${esc(a.role)}${a.symbol ? " · " + esc(a.symbol) : ""}${a.lot ? " · lot " + a.lot : ""}</span></div>
        <div class="ct-checks">${(a.checks || []).map((c) => `<div class="${c.ok ? "" : "c-bear"}"><span>${c.ok ? "✓" : "✗"} ${esc(c.name)}</span><i>${esc(c.detail)}</i></div>`).join("")}</div>
      </div>`).join("");
  });
  // ---- open MT5 terminals on this PC: one click = MASTER or FOLLOWER (then Save accounts)
  let MT5F = [];
  $("#btnFindMt5").addEventListener("click", async () => {
    const box = $("#mt5List");
    box.innerHTML = '<p class="muted">Hinahanap ang mga bukas na MT5… (ilang segundo; walang order, walang binabago sa MT5)</p>';
    const r = await api("/api/mt5/detect").catch((e) => ({ ok: false, error: String(e) }));
    if (!r.ok) { box.innerHTML = `<p class="c-bear">${esc(r.error || "failed")}</p>`; return; }
    MT5F = r.terminals || [];
    if (!MT5F.length) {
      box.innerHTML = `<p class="c-warn">${r.windows ? "Walang bukas na MT5. Buksan muna at mag-login ang bawat MT5 (master at followers), tapos pindutin ulit." : "Gumagana lang ito sa Windows (kung saan naka-install ang MT5)."}</p>`;
      return;
    }
    box.innerHTML = `<table class="mt5tab"><tr><th>MT5 folder</th><th>Account</th><th>Balance</th><th>${esc(r.symbol || "")}</th><th></th></tr>` +
      MT5F.map((f, i) => `<tr class="${f.ok ? "" : "bad"}"><td><b>${esc(f.folder)}</b><br><span class="muted small">${esc(f.path)}</span></td>
        <td>${f.ok ? `#${esc(f.login)}<br><span class="muted small">${esc(f.server || "")}${f.trade_allowed === false ? ' · <span class="c-warn">Algo Trading OFF</span>' : ""}</span>` : `<span class="c-bear">${esc(f.error || "")}</span>`}</td>
        <td class="mono">${f.ok ? money(f.balance) + " " + esc(f.currency || "") : "-"}</td>
        <td>${f.has_symbol ? '<span class="c-bull">✓</span>' : f.ok ? `<span class="c-warn">wala</span>${(f.symbols || []).length ? `<br><span class="muted small">${esc(f.symbols.join(", "))}</span>` : ""}` : "-"}</td>
        <td>${f.role ? `<span class="mchip info">${esc(f.role.toUpperCase())}</span>` : f.ok ? `<button class="btn-sm" data-mt5m="${i}">Gamitin bilang MASTER</button> <button class="btn-sm ghost" data-mt5f="${i}">+ FOLLOWER</button>` : ""}</td></tr>`).join("") +
      "</table><p class=\"muted small\">Pagkatapos pumili: <b>Save accounts</b>. Kapag pinalitan ang MASTER, lilipat agad ang chart sa MT5 na iyon pagka-Save (hindi na kailangang i-restart).</p>";
  });
  $("#mt5List").addEventListener("click", (e) => {
    const m = e.target.closest("[data-mt5m]"), f = e.target.closest("[data-mt5f]");
    if (!m && !f || !acctDraft) return;
    const t = MT5F[+(m ? m.dataset.mt5m : f.dataset.mt5f)];
    acctDraft = readAccountsForm();
    if (m) {
      Object.assign(acctDraft.master, { path: t.path, login: t.login });
      if (t.has_symbol === false && (t.symbols || []).length) acctDraft.master.symbol = t.symbols[0];
      $("#acctSaved").textContent = `MASTER = #${t.login} (${t.folder}) · click Save accounts`;
    } else {
      const sym = t.has_symbol ? (acctDraft.master.symbol || "") : ((t.symbols || [])[0] || "");
      acctDraft.followers.push({ name: `${t.company || t.folder} #${t.login}`.slice(0, 40), path: t.path, login: t.login, symbol: sym, lot: 0.01, enabled: true,
        daily_dd: 0, dd_mode: "%", daily_tp: 0, tp_mode: "%", close_scope: "superchart", paper_balance: 10000 });
      $("#acctSaved").textContent = `FOLLOWER #${t.login} idinagdag (lot 0.01) · i-check ang lot at symbol, tapos Save accounts`;
    }
    renderAccountsForm();
    e.target.closest("tr").querySelector("td:last-child").innerHTML = `<span class="mchip warn">${m ? "MASTER" : "FOLLOWER"} · i-Save</span>`;
  });
  $("#btnAddFollower").addEventListener("click", () => {
    if (!acctDraft) return;
    acctDraft = readAccountsForm();
    acctDraft.followers.push({ name: `Follower ${acctDraft.followers.length + 1}`, path: "", login: 0, symbol: "", lot: 0.01, enabled: true,
      daily_dd: 0, dd_mode: "%", daily_tp: 0, tp_mode: "%", close_scope: "superchart", paper_balance: 10000 });
    renderAccountsForm();
  });
  $("#btnSaveAccts").addEventListener("click", async () => {
    const cfg = readAccountsForm();
    const r = await api("/api/accounts", cfg).catch((x) => ({ ok: false, error: String(x) }));
    $("#acctSaved").innerHTML = !r.ok ? `<span class="c-bear">error: ${esc(r.error || "")}</span>`
      : r.warning ? `<span class="c-warn">saved, but: ${esc(r.warning)}</span>` : "saved ✓ (followers reconnect in the background)";
    await pollTrading();
    acctDraft = JSON.parse(JSON.stringify(T.config));
    renderAccountsForm();
  });
  $("#acctList").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const card = b.closest(".acard");
    const act = b.dataset.act;
    if (act === "findsym") return;                     // handled by the symbol finder
    if (act === "remove") {
      acctDraft = readAccountsForm();
      acctDraft.followers.splice(+card.dataset.idx, 1);
      renderAccountsForm();
      $("#acctSaved").textContent = "removed — click Save accounts to apply";
      return;
    }
    const id = act === "unlock-master" ? "master" : card.dataset.id;
    if (!id) { $("#acctSaved").textContent = "save first"; return; }
    const r = await api(`/api/trade/${act === "unlock-master" ? "unlock" : act}`, { id }).catch((x) => ({ ok: false, error: String(x) }));
    $("#acctSaved").textContent = r.ok ? `${act} ✓` : "error: " + (r.error || "");
    pollTrading();
  });
  document.querySelector('.nav[data-view="accounts"]').addEventListener("click", () => { acctDraft = null; setTimeout(renderAccountsForm, 0); });

  // ================================================================ journal
  let J = null, jOpen = new Set();
  async function pollJournal(force = false) {
    if (!force && $("#view-journal").hidden) return;
    try { J = await api("/api/journal"); } catch (e) { return; }
    renderJournal();
  }
  const pct = (v) => (v == null ? "-" : v.toFixed(0) + "%");
  const sgn = (v) => (v == null ? "-" : `<span class="${v >= 0 ? "c-bull" : "c-bear"}">${v >= 0 ? "+" : ""}${money(v)}</span>`);
  function statBlock(b) {
    return `<span>${b.trades} trades</span><span>win ${pct(b.win_rate)}</span><span>${sgn(b.pnl)}</span>
      <span>avg R ${b.avg_r == null ? "-" : b.avg_r}</span><span>PF ${b.profit_factor == null ? "-" : b.profit_factor}</span>`;
  }
  // ---- equity curve + P/L calendar (Journal page)
  const mnlDay = (t) => { try { return new Date(t * 1000).toLocaleDateString("en-CA", { timeZone: "Asia/Manila" }); } catch (e) { return new Date(t * 1000).toISOString().slice(0, 10); } };
  let eqChart = null, eqSeries = new Map(), eqHidden = new Set(JSON.parse(lsGet("rg_eq_hidden", "[]"))), eqKey = "";
  let jDay = null, calYM = null;
  const EQ_COLORS = ["#38bdf8", "#f472b6", "#a78bfa", "#fbbf24", "#34d399", "#fb923c", "#e879f9"];
  function renderEquity() {
    const ser = (J && J.series) || [];
    const box = $("#eqChart");
    if (!ser.length) { box.innerHTML = '<p class="muted" style="padding:20px">Wala pang saradong trade.</p>'; eqChart = null; eqSeries.clear(); $("#eqTabs").innerHTML = ""; return; }
    const key = ser.length + "|" + ser[ser.length - 1].t + "|" + [...eqHidden].join(",");
    if (key === eqKey && eqChart) return;
    eqKey = key;
    if (!eqChart) {
      box.innerHTML = "";
      eqChart = LC.createChart(box, {
        autoSize: true, localization: { locale: "en-US" },
        layout: { background: { color: "transparent" }, textColor: "#8b98b8", fontSize: 11 },
        grid: { vertLines: { color: "rgba(148,163,184,.06)" }, horzLines: { color: "rgba(148,163,184,.06)" } },
        rightPriceScale: { borderColor: "rgba(148,163,184,.15)" }, timeScale: { borderColor: "rgba(148,163,184,.15)", timeVisible: true },
      });
    }
    const accounts = [...new Set(ser.flatMap((x) => Object.keys(x.acc)))];
    const lines = [["TOTAL", "#e2e8f0", 3], ...accounts.map((a, i) => [a, EQ_COLORS[i % EQ_COLORS.length], 2])];
    const seen = new Set();
    for (const [name, color, w] of lines) {
      seen.add(name);
      let s = eqSeries.get(name);
      if (!s) {
        s = eqChart.addSeries(LC.LineSeries, { color, lineWidth: w, priceLineVisible: false, lastValueVisible: true, title: name,
          priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
        eqSeries.set(name, s);
      }
      s.applyOptions({ visible: !eqHidden.has(name) });
      let cum = 0, lastT = 0;
      const pts = [{ time: ser[0].t - 60 + 8 * 3600, value: 0 }];        // shown in Manila time
      lastT = pts[0].time;
      for (const x of ser) {
        const v = name === "TOTAL" ? x.pnl : x.acc[name];
        if (v === undefined) continue;
        cum += v;
        let t = x.t + 8 * 3600;
        if (t <= lastT) t = lastT + 1;                                    // unique, increasing times
        lastT = t;
        pts.push({ time: t, value: Math.round(cum * 100) / 100 });
      }
      s.setData(pts);
    }
    for (const [n, s] of eqSeries) if (!seen.has(n)) { eqChart.removeSeries(s); eqSeries.delete(n); }
    requestAnimationFrame(() => { try { eqChart.timeScale().fitContent(); } catch (e) { /* hidden */ } });
    const tot = ser.reduce((t, x) => t + x.pnl, 0);
    let peak = 0, cum = 0, mdd = 0;
    for (const x of ser) { cum += x.pnl; peak = Math.max(peak, cum); mdd = Math.min(mdd, cum - peak); }
    $("#eqInfo").innerHTML = `total <b class="${tot >= 0 ? "c-bull" : "c-bear"}">${tot >= 0 ? "+" : ""}${money(tot)}</b> · max drawdown <b class="c-bear">${money(mdd)}</b>`;
    $("#eqTabs").innerHTML = lines.map(([n, c]) => `<button class="eqtab ${eqHidden.has(n) ? "off" : ""}" data-n="${esc(n)}"><i style="background:${c}"></i>${esc(n)}</button>`).join("");
  }
  $("#eqTabs").addEventListener("click", (e) => {
    const b = e.target.closest(".eqtab");
    if (!b) return;
    eqHidden.has(b.dataset.n) ? eqHidden.delete(b.dataset.n) : eqHidden.add(b.dataset.n);
    lsSet("rg_eq_hidden", JSON.stringify([...eqHidden]));
    renderEquity();
  });
  function renderPLCalendar() {
    const ser = (J && J.series) || [];
    const today = mnlDay(Date.now() / 1000);
    if (!calYM) calYM = today.slice(0, 7);
    const [y, m] = calYM.split("-").map(Number);
    const days = {};
    for (const x of ser) {
      const d = mnlDay(x.t);
      const o = days[d] || (days[d] = { pnl: 0, n: 0, w: 0 });
      o.pnl += x.pnl; o.n += 1; if (x.pnl > 0) o.w += 1;
    }
    const monthName = new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
    $("#calMonth").textContent = monthName;
    const first = new Date(Date.UTC(y, m - 1, 1));
    const nDays = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7;                             // Monday first
    const inMonth = Object.entries(days).filter(([d]) => d.startsWith(calYM));
    const maxAbs = Math.max(1, ...inMonth.map(([, o]) => Math.abs(o.pnl)));
    let html = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week"].map((d) => `<div class="pc-h">${d}</div>`).join("");
    let cells = [], wk = { pnl: 0, n: 0 };
    const pushWeek = () => { cells.push(`<div class="pc-week ${wk.n ? (wk.pnl >= 0 ? "up" : "dn") : ""}">${wk.n ? `${wk.pnl >= 0 ? "+" : ""}${money(wk.pnl)}<i>${wk.n} tr</i>` : ""}</div>`); wk = { pnl: 0, n: 0 }; };
    for (let i = 0; i < lead; i++) cells.push('<div class="pc-day empty"></div>');
    for (let d = 1; d <= nDays; d++) {
      const key = `${calYM}-${String(d).padStart(2, "0")}`;
      const o = days[key];
      const a = o ? Math.min(1, Math.abs(o.pnl) / maxAbs) : 0;
      const bg = o ? (o.pnl >= 0 ? `rgba(38,215,160,${0.12 + a * 0.45})` : `rgba(255,90,120,${0.12 + a * 0.45})`) : "";
      cells.push(`<div class="pc-day ${o ? "has" : ""} ${key === today ? "today" : ""} ${key === jDay ? "sel" : ""}" data-d="${key}" style="${bg ? "background:" + bg : ""}">
        <span class="pc-n">${d}</span>${o ? `<b>${o.pnl >= 0 ? "+" : ""}${money(o.pnl)}</b><i>${o.n} tr · ${o.w}W</i>` : ""}</div>`);
      if (o) { wk.pnl += o.pnl; wk.n += o.n; }
      if ((lead + d) % 7 === 0) pushWeek();
    }
    if ((lead + nDays) % 7 !== 0) { for (let i = (lead + nDays) % 7; i < 7; i++) cells.push('<div class="pc-day empty"></div>'); pushWeek(); }
    $("#pcal").innerHTML = html + cells.join("");
    const mt = inMonth.reduce((t, [, o]) => t + o.pnl, 0), mn = inMonth.reduce((t, [, o]) => t + o.n, 0);
    const green = inMonth.filter(([, o]) => o.pnl > 0).length;
    $("#pcalSum").innerHTML = `${monthName}: <b class="${mt >= 0 ? "c-bull" : "c-bear"}">${mt >= 0 ? "+" : ""}${money(mt)}</b> · ${mn} trades · ${green}/${inMonth.length} green days`
      + (jDay ? ` · <button class="tool mini" id="calClear">showing ${jDay} ✕</button>` : "");
  }
  $("#calPrev").addEventListener("click", () => { const [y, m] = calYM.split("-").map(Number); const d = new Date(Date.UTC(y, m - 2, 1)); calYM = d.toISOString().slice(0, 7); renderPLCalendar(); });
  $("#calNext").addEventListener("click", () => { const [y, m] = calYM.split("-").map(Number); const d = new Date(Date.UTC(y, m, 1)); calYM = d.toISOString().slice(0, 7); renderPLCalendar(); });
  document.addEventListener("click", (e) => {
    if (e.target.id === "calClear") { jDay = null; renderJournal(); return; }
    const c = e.target.closest("#pcal .pc-day.has");
    if (!c) return;
    jDay = jDay === c.dataset.d ? null : c.dataset.d;
    renderJournal();
  });

  function renderJournal() {
    if (!J || J.disabled) { $("#jStats").innerHTML = '<span class="muted">Trading is off (read-only mode).</span>'; return; }
    const st = J.stats, a = st.all;
    $("#jBadge").textContent = String(a.trades + st.open);
    $("#jMode").textContent = J.paper ? "paper trades (journal_paper.jsonl)" : "live trades (journal.jsonl)";
    $("#jStats").innerHTML = [
      ["Closed trades", a.trades, `${st.open} open`],
      ["Win rate", pct(a.win_rate), `${a.wins} W · ${a.losses} L`],
      ["Total P/L", sgn(a.pnl), "all accounts"],
      ["Average R", a.avg_r == null ? "-" : a.avg_r, "per trade"],
      ["Profit factor", a.profit_factor == null ? "-" : a.profit_factor, "gross win ÷ gross loss"],
      ["Closed by", Object.entries(st.reasons).map(([k, v]) => `${esc(k)} ${v}`).join(" · ") || "-", ""],
    ].map(([l, v, sub]) => `<div class="jstat"><div class="klabel">${l}</div><div class="kval mono">${v}</div><div class="ksub">${sub}</div></div>`).join("");
    const pass = S && S.scoring ? S.scoring.passing : 70;
    $("#jPass").innerHTML = `<div class="jrow good"><b>≥ passing (${pass}%)</b>${statBlock(st.by_pass.passed)}</div>
      <div class="jrow"><b>&lt; passing</b>${statBlock(st.by_pass.not_passed)}</div>
      ${st.by_news ? `<div class="jrow warnrow"><b>Entry sa news window</b>${statBlock(st.by_news.in_window)}</div>` : ""}
      <p class="muted small">Kung mas mataas ang win rate at avg R ng mga pumasa, gumagana ang scoring mo. Kung hindi, baguhin ang timbang o passing score.</p>`;
    $("#jBands").innerHTML = st.by_kind ? `<div class="jrow"><b>Manual</b>${statBlock(st.by_kind.manual)}</div>
      ${st.by_kind.auto ? `<div class="jrow"><b>Auto</b>${statBlock(st.by_kind.auto)}</div>` : ""}
      <div class="jrow"><b>News trade</b>${statBlock(st.by_kind.news)}</div>` : "";
    renderEquity();
    renderPLCalendar();
    renderHeatmap();
    const rg = st.by_regime || {};
    $("#jRegime").innerHTML = Object.keys(rg).length ? Object.entries(rg).map(([k, b]) =>
      `<div class="jrow"><b><span class="regchip" style="--c:${REG_COL[k] || "#94a3b8"}">${esc(k)}</span></b>${statBlock(b)}</div>`).join("")
      + '<p class="muted small">Makikita kung saang regime ka kumikita. Gamitin ito sa AUTO TRADE ⚙ → regimes.</p>'
      : '<p class="muted">Wala pang saradong trade na may regime.</p>';
    const rows = J.entries.filter((e) => !jDay || mnlDay(e.close_time || e.open_time) === jDay).map((e) => {
      const sc = e.score || {};
      const open = e.status === "open";
      const res = open ? '<span class="rchip open">OPEN</span>' : `<span class="rchip ${e.result.toLowerCase()}">${e.result}</span>`;
      const t = new Date(e.open_time * 1000);
      const when = t.toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      let html = `<tr class="jr" data-gid="${esc(e.gid)}"><td class="muted">${when}</td>
        <td><span class="sigchip ${e.side === 1 ? "buy" : "sell"}">${e.side === 1 ? "BUY" : "SELL"}</span>${e.kind === "news" ? ' <span class="rchip newsk">NEWS</span>' : e.kind === "auto" ? ' <span class="rchip autok">AUTO</span>' : ""}</td>
        <td>${e.lot}</td><td>${fx(e.entry)} → ${e.exit != null ? fx(e.exit) : "…"}</td>
        <td>${res}</td><td class="muted">${esc(e.reason || "")}</td><td>${open ? "-" : sgn(e.pnl)}</td>
        <td>${e.r == null ? "-" : (e.r >= 0 ? "+" : "") + e.r + "R"}</td>
        <td>${sc.overall == null ? "-" : `<b class="${sc.passed ? "c-bull" : "c-warn"}">${sc.overall.toFixed(0)}%</b> ${sc.passed ? "✓" : "✗"}`}</td>
        <td>${sc.trigger ? "✓" : "✗"}${e.news && e.news.active ? ' <span title="entered inside a news warning window">📰</span>' : ""}</td><td class="muted">${e.duration_min != null ? e.duration_min + "m" : ""}</td>
        <td>${e.snaps && Object.keys(e.snaps).length ? `<button class="tool mini" data-snap="${esc(e.gid)}" title="chart picture">📷</button>` : ""}</td></tr>`;
      if (jOpen.has(e.gid)) {
        const groups = (sc.groups || []).map((g) => `<span class="jg">${catChip(g.category)} ${g.agree}/${g.total} ${g.pct.toFixed(0)}% ×${g.weight}</span>`).join("");
        const accts = Object.entries(e.pnl_by_account || {}).map(([k, v]) => `<span class="jg">${esc(k)} ${v == null ? "-" : sgn(v)}</span>`).join("");
        const parts = (e.partials || []).map((p) => `${Math.round(p.fraction * 100)}% (${esc(p.source)})`).join(", ");
        html += `<tr class="jdetail"><td colspan="12">
          ${(e.lifecycle || []).length ? `<div class="lifec"><b>Lifecycle:</b> ${e.lifecycle.map((x) => `<span class="lc ${/FAIL|UNPROT/.test(x.state) ? "bad" : ""}" title="${esc(x.note || "")}">${new Date(x.t * 1000).toLocaleTimeString("en-US", { hour12: false })} ${esc(x.state)}</span>`).join(" → ")}</div>` : ""}
          <div><b>Score at entry:</b> ${esc(sc.verdict || "")}${e.regime ? ` · <b>Regime:</b> ${esc(e.regime.label)} (${esc(e.regime.tf)}), ${esc(e.regime.htf || "")} (HTF)` : ""}</div>
          ${e.news ? `<div class="${e.news.active ? "c-warn" : "muted"}"><b>News at entry:</b> ${esc(e.news.currency)} ${esc(e.news.title)} (${esc(e.news.impact)}) ${e.news.minutes >= 0 ? "in " + Math.round(e.news.minutes) + "m" : Math.round(-e.news.minutes) + "m after release"}${e.news.active ? " — inside warning window" : ""}</div>` : ""}<div class="jgs">${groups}</div>
          <div><b>SL/TP:</b> ${e.sl ? fx(e.sl) : "none"} / ${e.tp ? fx(e.tp) : "none"} · distance $${e.sl_dist} / $${e.tp_dist}
            ${e.sl_changes && e.sl_changes.length ? ` · moved ${e.sl_changes.length}×` : ""}${parts ? " · partials: " + parts : ""}</div>
          ${accts ? `<div class="jgs"><b>P/L per account:</b> ${accts}</div>` : `<div class="muted">accounts: ${(e.accounts || []).map(esc).join(", ")}</div>`}
        </td></tr>`;
      }
      return html;
    }).join("");
    $("#jTable").innerHTML = `<tr><th>Opened</th><th>Side</th><th>Lot</th><th>Entry → exit</th><th>Result</th><th>Closed by</th>
      <th>P/L</th><th>R</th><th>Score</th><th>Trig</th><th>Time</th><th></th></tr>` + (rows || `<tr><td colspan="12" class="muted">Wala pang trade. Lahat ng BUY/SELL mula sa SuperChart ay lalabas dito.</td></tr>`);
  }
  $("#jTable").addEventListener("click", (e) => {
    const sb = e.target.closest("[data-snap]");
    if (sb) {
      const en = J.entries.find((x) => x.gid === sb.dataset.snap);
      const sn = (en && en.snaps) || {};
      showModal(`${en.side === 1 ? "BUY" : "SELL"} ${fx(en.entry)} · ${en.result || "OPEN"}`,
        ["entry", "exit"].filter((k) => sn[k]).map((k) => `<div class="klabel">${k.toUpperCase()}</div><img class="snapimg" src="/snap/${encodeURIComponent(sn[k])}?v=${Date.now()}">`).join(""), "", null);
      $("#confirmModal .modal-box").classList.add("wide");
      return;
    }
    const tr = e.target.closest("tr.jr");
    if (!tr) return;
    const g = tr.dataset.gid;
    jOpen.has(g) ? jOpen.delete(g) : jOpen.add(g);
    renderJournal();
  });
  document.querySelector('.nav[data-view="journal"]').addEventListener("click", () => setTimeout(() => {
    eqKey = "";                                    // redraw + fit now that the page has a size
    pollJournal(true);
  }, 0));


  // ---- heatmap (hour x weekday, Manila time, by entry time)
  const REG_COL = { "TREND UP": "#26d7a0", "TREND DOWN": "#ff5a78", RANGE: "#fbbf24", VOLATILE: "#e879f9", QUIET: "#60a5fa", TRANSITION: "#94a3b8" };
  let hmMetric = lsGet("rg_hm", "pnl");
  function renderHeatmap() {
    $("#hmTabs").innerHTML = [["pnl", "P/L"], ["win", "Win %"], ["n", "Trades"]].map(([k, l]) =>
      `<button class="tool mini ${k === hmMetric ? "on" : ""}" data-hm="${k}">${l}</button>`).join("");
    const cells = {};
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    for (const x of (J && J.series) || []) {
      let wd, hr;
      try {
        const p = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Manila", weekday: "short", hour: "2-digit", hourCycle: "h23" }).formatToParts(new Date(x.ot * 1000));
        wd = p.find((q) => q.type === "weekday").value; hr = parseInt(p.find((q) => q.type === "hour").value, 10);
      } catch (e) { const d = new Date(x.ot * 1000); wd = days[(d.getDay() + 6) % 7]; hr = d.getHours(); }
      const k = wd + "|" + hr;
      const c = cells[k] || (cells[k] = { n: 0, w: 0, pnl: 0 });
      c.n++; c.pnl += x.pnl; if (x.result === "WIN") c.w++;
    }
    const vals = Object.values(cells);
    if (!vals.length) { $("#heatmap").innerHTML = '<p class="muted">Wala pang saradong trade.</p>'; return; }
    const maxAbs = Math.max(...vals.map((c) => Math.abs(c.pnl)), 0.01), maxN = Math.max(...vals.map((c) => c.n));
    const usedDays = days.filter((d) => Object.keys(cells).some((k) => k.startsWith(d + "|")) || !["Sat", "Sun"].includes(d));
    let h = '<div class="hm" style="--cols:24"><div></div>' + Array.from({ length: 24 }, (_, i) => `<div class="hm-h">${i % 3 === 0 ? i : ""}</div>`).join("");
    const byDay = {}, byHour = {};
    for (const d of usedDays) {
      h += `<div class="hm-d">${d}</div>`;
      for (let hr = 0; hr < 24; hr++) {
        const c = cells[d + "|" + hr];
        if (!c) { h += '<div class="hm-c"></div>'; continue; }
        let bg, txt;
        if (hmMetric === "pnl") { const a = Math.min(1, Math.abs(c.pnl) / maxAbs) * 0.85 + 0.12; bg = c.pnl >= 0 ? `rgba(38,215,160,${a})` : `rgba(255,90,120,${a})`; txt = Math.abs(c.pnl) >= 100 ? Math.round(c.pnl) : c.pnl.toFixed(0); }
        else if (hmMetric === "win") { const wr = c.w / c.n; bg = wr >= 0.5 ? `rgba(38,215,160,${0.15 + (wr - 0.5) * 1.5})` : `rgba(255,90,120,${0.15 + (0.5 - wr) * 1.5})`; txt = Math.round(wr * 100); }
        else { bg = `rgba(99,102,241,${0.15 + 0.8 * c.n / maxN})`; txt = c.n; }
        h += `<div class="hm-c" style="background:${bg}" title="${d} ${hr}:00-${hr}:59 · ${c.n} trade(s) · win ${Math.round(100 * c.w / c.n)}% · P/L ${c.pnl.toFixed(2)}">${txt}</div>`;
      }
    }
    h += "</div>";
    for (const [k, c] of Object.entries(cells)) {
      const [d, hr] = k.split("|");
      const bd = byDay[d] || (byDay[d] = { n: 0, pnl: 0 }); bd.n += c.n; bd.pnl += c.pnl;
      const ses = +hr >= 20 || +hr < 1 ? "NY open (20-01)" : +hr >= 15 ? "London (15-20)" : +hr >= 7 ? "Asia (07-15)" : "Late NY / quiet (01-07)";
      const bh = byHour[ses] || (byHour[ses] = { n: 0, pnl: 0 }); bh.n += c.n; bh.pnl += c.pnl;
    }
    const best = (o) => Object.entries(o).sort((a, b) => b[1].pnl - a[1].pnl);
    const bs = best(byHour);
    h += `<div class="hm-sum">${bs.map(([k, v]) => `<span class="jg">${esc(k)}: ${v.n} tr · <b class="${v.pnl >= 0 ? "c-bull" : "c-bear"}">${sgn(v.pnl)}</b></span>`).join("")}</div>`;
    $("#heatmap").innerHTML = h;
  }
  $("#hmTabs").addEventListener("click", (e) => { const b = e.target.closest("[data-hm]"); if (!b) return; hmMetric = b.dataset.hm; lsSet("rg_hm", hmMetric); renderHeatmap(); });

  // ================================================================ news
  let N = null;
  const NEWS_T0 = Date.now() / 1000;                  // headlines seen before this page loaded are not read out
  const spoken = new Set(JSON.parse(lsGet("rg_spoken", "[]")));
  let calMine = lsGet("rg_cal_mine", "1") === "1";
  let calImp = new Set(JSON.parse(lsGet("rg_cal_imp", '["High","Medium"]')));
  let headGold = lsGet("rg_head_gold", "0") === "1";
  let newsFormDirty = false;
  const IMP_CLS = { High: "hi", Medium: "med", Low: "low", Holiday: "hol" };

  function manila(t, opt) {
    try { return new Date(t * 1000).toLocaleString("en-US", Object.assign({ timeZone: "Asia/Manila" }, opt)); }
    catch (e) { return new Date(t * 1000).toLocaleString("en-US", opt); }
  }
  function dur(sec) {
    const a = Math.abs(sec), d = Math.floor(a / 86400), h = Math.floor((a % 86400) / 3600), m = Math.floor((a % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m`;
    return `${Math.floor(a)}s`;
  }
  function ago(t) { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : dur(s) + " ago"; }

  async function pollNews(force = false) {
    try { N = await api("/api/news"); } catch (e) { return; }
    if (force) newsFormDirty = false;
    renderNews();
    voiceHeadlinesCheck();
  }

  // live countdown (every second) - uses the event time, not the last poll
  function newsWarn() {
    if (!N || N.disabled || !N.calendar) return null;
    const st = N.settings, now = Date.now() / 1000;
    const cur = new Set(st.currencies), imp = new Set(st.warn_impacts);
    for (const e of N.calendar) {
      if (!cur.has(e.currency) || !imp.has(e.impact)) continue;
      const mins = (e.t - now) / 60;
      if (mins < -st.after_minutes) continue;
      return Object.assign({}, e, { mins, active: mins <= st.warn_minutes });
    }
    return null;
  }
  function tickNews() {
    const w = newsWarn();
    const pill = $("#newsPill");
    if (!w) { pill.hidden = true; $("#tNews").hidden = true; $("#newsBadge").hidden = true; }
    else {
      pill.hidden = false;
      const when = w.mins >= 0 ? `in ${dur(w.mins * 60)}` : `${dur(-w.mins * 60)} ago`;
      $("#newsPillText").textContent = `${w.currency} ${w.title} ${when}`;
      pill.classList.toggle("hot", w.active);
      $("#newsBadge").hidden = !w.active;
      const tn = $("#tNews");
      tn.hidden = !w.active;
      if (w.active) tn.innerHTML = `⚠ <b>${esc(w.currency)} ${esc(w.title)}</b> ${w.mins >= 0 ? "in " + dur(w.mins * 60) : "released " + dur(-w.mins * 60) + " ago"} · high-impact news: spread can widen and SL can slip`;
      voiceEventCheck(w);
    }
    if (!$("#view-news").hidden && N) renderCalendar();
  }
  setInterval(tickNews, 1000);
  $("#newsPill").addEventListener("click", () => document.querySelector('.nav[data-view="news"]').click());

  function renderNews() {
    if (!N) return;
    if (N.disabled) {
      $("#calList").innerHTML = '<p class="muted">News reader is off (started with --no-news).</p>';
      return;
    }
    const up = N.updated || {};
    $("#calInfo").textContent = up.calendar ? `ForexFactory · updated ${ago(up.calendar)} · times in Manila` : "ForexFactory · not loaded yet";
    $("#rssInfo").textContent = up.rss ? `updated ${ago(up.rss)}` : "not loaded yet";
    renderCalFilters();
    renderCalendar();
    renderHeadlines();
    renderNewsSettings();
    const errs = Object.values(N.errors || {});
    $("#newsErr").innerHTML = errs.length
      ? `<b>Fetch problems</b> (the last saved data is still shown):<br>${errs.map(esc).join("<br>")}` : "";
    tickNews();
  }

  function renderCalFilters() {
    const imps = ["High", "Medium", "Low", "Holiday"];
    $("#calFilters").innerHTML =
      `<button class="fchip ${calMine ? "on" : ""}" data-f="mine">${calMine ? esc(N.settings.currencies.join("/")) + " only" : "All currencies"}</button>` +
      imps.map((i) => `<button class="fchip imp-${IMP_CLS[i]} ${calImp.has(i) ? "on" : ""}" data-i="${i}">${i}</button>`).join("");
  }
  $("#calFilters").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.f) { calMine = !calMine; lsSet("rg_cal_mine", calMine ? "1" : "0"); }
    if (b.dataset.i) { calImp.has(b.dataset.i) ? calImp.delete(b.dataset.i) : calImp.add(b.dataset.i); lsSet("rg_cal_imp", JSON.stringify([...calImp])); }
    renderCalFilters(); renderCalendar();
  });

  function renderCalendar() {
    if (!N || N.disabled) return;
    const cur = new Set(N.settings.currencies), now = Date.now() / 1000;
    const rows = N.calendar.filter((e) => (!calMine || cur.has(e.currency)) && calImp.has(e.impact));
    if (!rows.length) { $("#calList").innerHTML = `<p class="muted">${N.updated && N.updated.calendar ? "No events for this filter." : "Loading calendar… (needs internet)"}</p>`; return; }
    let day = "", html = "", nextMarked = false;
    for (const e of rows) {
      const d = manila(e.t, { weekday: "short", month: "short", day: "2-digit" });
      if (d !== day) { day = d; html += `<div class="cal-day">${esc(d)}</div>`; }
      const secs = e.t - now;
      const past = secs < 0;
      let cd = past ? (secs > -3600 ? dur(-secs) + " ago" : "done") : "in " + dur(secs);
      const isNext = !past && !nextMarked && cur.has(e.currency) && N.settings.warn_impacts.includes(e.impact);
      if (isNext) nextMarked = true;
      html += `<div class="cal-row ${past ? "past" : ""} ${isNext ? "next" : ""}">
        <span class="mono cal-t">${esc(manila(e.t, { hour: "2-digit", minute: "2-digit", hour12: false }))}</span>
        <span class="cal-cur">${esc(e.currency)}</span>
        <span class="imp-dot ${IMP_CLS[e.impact] || ""}" title="${esc(e.impact)}"></span>
        <span class="cal-title">${esc(e.title)}</span>
        <span class="cal-num" title="actual / forecast / previous">${e.actual ? `<b>${esc(e.actual)}</b> · ` : ""}${esc(e.forecast || "-")} · <i>${esc(e.previous || "-")}</i></span>
        <span class="cal-cd mono">${cd}</span></div>`;
    }
    $("#calList").innerHTML = html;
  }

  function hl(title, hits) {
    let t = esc(title);
    for (const k of hits || []) {
      const re = new RegExp("\\b(" + k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
      t = t.replace(re, "<mark>$1</mark>");
    }
    return t;
  }
  function renderHeadlines() {
    const hs = (N.headlines || []).filter((h) => !headGold || (h.hits && h.hits.length));
    const btn = `<button class="fchip ${headGold ? "on" : ""}" id="headGold">${headGold ? "Gold-moving only" : "All headlines"}</button>`;
    $("#headList").innerHTML = btn + (hs.length ? hs.slice(0, 60).map((h) => `<a class="head ${h.hits && h.hits.length ? "hit" : ""}" href="${esc(h.link)}" target="_blank" rel="noopener">
        <span class="head-meta">${esc(h.source)} · ${ago(h.t)}</span><span class="head-t">${hl(h.title, h.hits)}</span></a>`).join("")
      : `<p class="muted">${N.updated && N.updated.rss ? "No headlines match." : "Loading headlines… (needs internet)"}</p>`);
  }
  $("#headList").addEventListener("click", (e) => {
    if (e.target.id !== "headGold") return;
    headGold = !headGold; lsSet("rg_head_gold", headGold ? "1" : "0"); renderHeadlines();
  });

  function renderNewsSettings() {
    if (newsFormDirty) return;
    const st = N.settings;
    const imps = ["High", "Medium", "Low"];
    $("#newsSet").innerHTML = `
      <label class="pf"><span>Currencies (comma)</span><input id="nsCur" value="${esc(st.currencies.join(", "))}"></label>
      <label class="pf"><span>Warn before (min)</span><input type="number" id="nsWarn" min="0" max="240" value="${st.warn_minutes}"></label>
      <label class="pf"><span>Warn after (min)</span><input type="number" id="nsAfter" min="0" max="240" value="${st.after_minutes}"></label>
      <div class="pf"><span>Warn / voice for</span><div>${imps.map((i) => `<label class="ck"><input type="checkbox" data-wi="${i}" ${st.warn_impacts.includes(i) ? "checked" : ""}> ${i}</label>`).join("")}</div></div>
      <label class="pf wide2"><span>Gold keywords (comma) — highlighted, and read out when READ HEADLINES is on</span><textarea id="nsKw" rows="3">${esc(st.keywords.join(", "))}</textarea></label>
      <label class="pf wide2"><span>RSS feeds (one per line: name | url)</span><textarea id="nsRss" rows="2">${esc(st.rss.map((f) => f.name + " | " + f.url).join("\n"))}</textarea></label>
      <div class="acct-actions"><button class="btn-sm" id="nsSave">Save news settings</button><span class="muted" id="nsSaved"></span></div>`;
  }
  $("#newsSet").addEventListener("input", () => { newsFormDirty = true; });
  $("#newsSet").addEventListener("click", async (e) => {
    if (e.target.id !== "nsSave") return;
    const list = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    const body = {
      currencies: list($("#nsCur").value).map((x) => x.toUpperCase()),
      warn_minutes: parseInt($("#nsWarn").value, 10) || 0,
      after_minutes: parseInt($("#nsAfter").value, 10) || 0,
      warn_impacts: [...document.querySelectorAll("#newsSet [data-wi]")].filter((c) => c.checked).map((c) => c.dataset.wi),
      keywords: list($("#nsKw").value.toLowerCase()),
      rss: $("#nsRss").value.split("\n").map((l) => l.split("|").map((x) => x.trim())).filter((p) => p.length === 2 && p[1])
        .map(([name, url]) => ({ name, url })),
    };
    const r = await api("/api/news/settings", body).catch(() => ({ ok: false }));
    $("#nsSaved").textContent = r.ok ? "saved ✓ fetching…" : "save failed";
    newsFormDirty = false;
    setTimeout(() => pollNews(true), 4000);
  });
  $("#newsRefresh").addEventListener("click", async () => {
    await api("/api/news/refresh", {}).catch(() => null);
    setTimeout(() => pollNews(true), 3000);
  });

  // ---- voice (the browser's own text-to-speech; nothing is sent anywhere)
  const hasTTS = "speechSynthesis" in window;
  $("#voiceOn").checked = lsGet("rg_voice", "0") === "1";
  $("#voiceHeadlines").checked = lsGet("rg_voice_heads", "0") === "1";
  $("#voiceOn").addEventListener("change", (e) => { lsSet("rg_voice", e.target.checked ? "1" : "0"); if (e.target.checked) speak("News voice on."); voiceNote(); });
  $("#voiceHeadlines").addEventListener("change", (e) => lsSet("rg_voice_heads", e.target.checked ? "1" : "0"));
  $("#voiceTest").addEventListener("click", () => {
    const w = newsWarn();
    speak(w ? `Next high impact news: ${w.currency} ${w.title}, ${w.mins >= 0 ? "in " + spokenDur(w.mins) : "released " + spokenDur(-w.mins) + " ago"}.`
            : "REYGUN news voice test. No high impact news coming up.", true);
  });
  function voiceNote() {
    const n = $("#voiceNote");
    if (!hasTTS) n.textContent = "voice not supported in this browser";
    else if (navigator.userActivation && !navigator.userActivation.hasBeenActive && $("#voiceOn").checked) n.textContent = "click anywhere once so the browser allows voice";
    else n.textContent = $("#voiceOn").checked ? `warns ${N && N.settings ? N.settings.warn_minutes : 30}m, 5m and 1m before, and at release` : "";
  }
  document.addEventListener("click", voiceNote, { once: true });
  setInterval(voiceNote, 5000);
  function spokenDur(mins) { const m = Math.round(mins); return m >= 60 ? `${Math.floor(m / 60)} hour ${m % 60} minutes` : `${m} minute${m === 1 ? "" : "s"}`; }
  function speak(text, force = false) {
    if (!hasTTS || (!force && !$("#voiceOn").checked)) return;
    try {
      const u = new SpeechSynthesisUtterance(text);
      const vs = speechSynthesis.getVoices();
      const v = vs.find((x) => /en-US/i.test(x.lang) && /natural|aria|jenny|guy/i.test(x.name)) || vs.find((x) => /^en/i.test(x.lang));
      if (v) u.voice = v;
      u.rate = 1.0;
      speechSynthesis.speak(u);
    } catch (e) { /* ignore */ }
  }
  function markSpoken(k) {
    spoken.add(k);
    lsSet("rg_spoken", JSON.stringify([...spoken].slice(-200)));
  }
  function voiceEventCheck(w) {
    if (!$("#voiceOn").checked || !N) return;
    const warnM = N.settings.warn_minutes;
    const stages = [[warnM, `Heads up. ${w.currency} ${w.title} in ${spokenDur(warnM)}.`],
                    [5, `${w.currency} ${w.title} in 5 minutes. Watch your spread and stop loss.`],
                    [1, `${w.currency} ${w.title} in 1 minute.`],
                    [0, `${w.currency} ${w.title} is out now.${w.actual ? " Actual " + w.actual + ", forecast " + (w.forecast || "none") + "." : ""}`]];
    for (const [m, text] of stages) {
      const key = `${w.t}|${w.title}|${m}`;
      // speak once when we cross the stage (not if the page opened long after it)
      if (w.mins <= m && w.mins > m - 2 && !spoken.has(key)) { markSpoken(key); speak(text); break; }
    }
  }
  function voiceHeadlinesCheck() {
    if (!N || N.disabled || !$("#voiceOn").checked || !$("#voiceHeadlines").checked) return;
    const fresh = (N.headlines || []).filter((h) => h.hits && h.hits.length && h.first_seen >= NEWS_T0 && !spoken.has("h|" + (h.link || h.title)));
    fresh.slice(0, 3).forEach((h) => { markSpoken("h|" + (h.link || h.title)); speak("Headline. " + h.title); });
    fresh.slice(3).forEach((h) => markSpoken("h|" + (h.link || h.title)));
  }
  document.querySelector('.nav[data-view="news"]').addEventListener("click", () => setTimeout(() => pollNews(true), 0));


  // ================================================================ news trade (conso breakout pending orders)
  let NT = null, ntSetOpen = false, ntDirty = false;
  async function pollNT() {
    if (!T || T.disabled) { $("#ntCard").hidden = true; return; }
    try { NT = await api("/api/newstrade"); } catch (e) { return; }
    renderNT();
  }
  const NT_FIELDS = [
    ["master_lot", "Master lot", "followers: own fixed lot"], ["place_before_sec", "Place before (sec)", ""],
    ["close_before_news", "Isara ang open trades bago ang news", "SuperChart trades lang (hindi ang ibang EA)", ["true", "false"]],
    ["close_before_sec", "Isara ilang sec bago", ">= place before"],
    ["nobox_mode", "Kapag walang box", "distance = presyo ± offset · skip = walang trade", ["distance", "skip"]],
    ["nobox_offset", "Walang box: layo ng stop $", "mula sa presyo"], ["nobox_sl", "Walang box: SL $", ""],
    ["cancel_after_min", "Cancel unfilled after (min)", ""], ["buffer_pips", "Entry buffer (pips)", "beyond the box edge"],
    ["levels", "Grid levels per side", "1 = one buy stop + one sell stop"], ["step", "Grid step $", ""],
    ["sl_mode", "SL", "box = other side of the box", ["box", "fixed"]], ["sl_fixed", "SL fixed $", ""],
    ["max_sl", "Max SL $", "cap for the box SL"], ["tp_mode", "TP", "rr = SL x RR", ["rr", "fixed"]],
    ["tp_rr", "TP RR", ""], ["tp_fixed", "TP fixed $", ""],
    ["conso_tf", "Box timeframe", "", ["M1", "M5", "M15"]], ["min_touches", "Min tops / bottoms", "same level touches each side"],
    ["tol_pips", "Same-level tolerance (pips)", ""], ["pip", "1 pip = $", "gold: 0.10"],
    ["swing", "Swing strength", "bars on each side of a top/bottom"], ["lookback", "Search last N bars", ""],
    ["min_bars", "Min box length (bars)", ""], ["max_range", "Max box size $", ""], ["min_range", "Min box size $", ""],
  ];
  function renderNT() {
    const card = $("#ntCard");
    if (!NT || NT.disabled) { card.hidden = true; return; }
    card.hidden = false;
    const auto = NT.allow_auto !== false;
    $("#ntSwitchWrap").hidden = !auto;
    $("#ntCancel").hidden = !auto;
    $("#ntTitle").textContent = auto ? "News trade" : "News box";
    $("#ntSwitch").checked = NT.enabled;
    $("#ntLabel").textContent = NT.enabled ? "ON" : "OFF";
    card.classList.toggle("on", NT.enabled);
    $("#foot").textContent = NT.enabled
      ? "NEWS TRADE is ON: before high-impact news, open SuperChart trades are closed and BUY STOP / SELL STOP are placed automatically (when ARMED)."
      : "Nothing is traded automatically. Orders are sent only when you ARM trading and click BUY/SELL and confirm.";
    $("#foot").classList.toggle("warnfoot", NT.enabled);
    const now = Date.now() / 1000;
    const st = NT.settings;
    let lines = [];
    if (!auto) lines.push('<span class="muted">Pang-tingin lang: ipinapakita ang box at kung saan papasok ang buy stop / sell stop. Walang order.</span>');
    else if (!NT.enabled) lines.push('<span class="muted">OFF · walang automatic na order.</span>');
    else if (!T.armed) lines.push('<span class="c-warn">ON pero DISARMED: hindi maglalagay ng order hangga\'t hindi naka-ARM.</span>');
    else lines.push(`<span class="c-bull">ON · ${T.paper ? "paper" : "LIVE"} · automatic bago ang high-impact news</span>`);
    if (NT.next) {
      const secs = NT.next.t - now;
      const place = secs - st.place_before_sec;
      lines.push(`Next: <b>${esc(NT.next.currency)} ${esc(NT.next.title)}</b> ${secs >= 0 ? "in " + dur(secs) : "released " + dur(-secs) + " ago"}` +
        (secs > 0 && NT.enabled ? ` · orders ${place > 0 ? "in " + dur(place) : "now"}` : ""));
    } else lines.push('<span class="muted">No upcoming high-impact news (see News settings).</span>');
    const b = NT.box;
    if (b && b.ok) lines.push(`<span class="nt-box">BOX ${fx(b.low)} – ${fx(b.high)} · $${b.range} · ${st.conso_tf}</span>
      <span class="c-bull">✓ ${b.tops.length} tops / ${b.bottoms.length} bottoms</span>`);
    else lines.push(`<span class="c-bear">✗ no consolidation</span> <span class="muted">${esc((b && b.why) || "no data")}</span>` +
      (st.nobox_mode === "distance" ? ` <span class="c-info">→ stops sa presyo ± $${st.nobox_offset}</span>` : ""));
    if (auto && st.close_before_news) lines.push(`<span class="muted">Isasara ang open SuperChart trades ${st.close_before_sec}s bago ang news</span>`);
    $("#ntStatus").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
    $("#ntPlan").innerHTML = (NT.plan || []).filter((L) => L.level === 0 || st.levels > 1).map((L) =>
      `<div class="${L.side === 1 ? "c-bull" : "c-bear"}"><b>${L.side === 1 ? "BUY STOP" : "SELL STOP"}${st.levels > 1 ? " #" + (L.level + 1) : ""}</b>
        ${fx(L.entry)} <span class="muted">SL</span> ${L.sl ? fx(L.sl) : "-"} <span class="muted">TP</span> ${L.tp ? fx(L.tp) : "-"}</div>`).join("");
    $("#ntEvents").innerHTML = (NT.events || []).map((e) => {
      const orders = Object.values(e.orders || {}).flat();
      const filled = orders.filter((o) => o.state === "filled").length, open = orders.filter((o) => !o.state).length;
      return `<div class="nt-ev ${e.stage}"><span class="nt-stage">${esc(e.stage.toUpperCase())}</span>
        <span>${esc(e.currency)} ${esc(e.title)}</span>
        <span class="muted">${esc(e.note)}${orders.length ? ` · ${open} pending · ${filled} filled` : ""}</span></div>`;
    }).join("");
    if (ntSetOpen && !ntDirty) renderNTSet();
  }
  function renderNTSet() {
    const st = NT.settings;
    const skip = NT.allow_auto === false ? ["master_lot", "place_before_sec", "cancel_after_min", "levels", "step"] : [];
    $("#ntSet").innerHTML = NT_FIELDS.filter((f) => !skip.includes(f[0])).map(([k, label, hint, opts]) => opts
      ? `<label class="pf" title="${esc(hint)}"><span>${label}</span><select data-k="${k}">${opts.map((o) => `<option ${String(st[k]) === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>`
      : `<label class="pf" title="${esc(hint)}"><span>${label}</span><input type="number" step="any" data-k="${k}" value="${st[k]}"></label>`).join("") +
      `<div class="acct-actions"><button class="btn-sm" id="ntSave">Save</button><span class="muted" id="ntSaved"></span></div>
       <p class="muted small">Currencies at impact: kinukuha sa News settings (News page).</p>`;
  }
  $("#ntGear").addEventListener("click", () => goTradeSet("#ntSetCard"));
  $("#ntSet").addEventListener("input", () => { ntDirty = true; });
  $("#ntSet").addEventListener("click", async (e) => {
    if (e.target.id !== "ntSave") return;
    const body = {};
    document.querySelectorAll("#ntSet [data-k]").forEach((el) => { body[el.dataset.k] = el.value; });
    const r = await api("/api/newstrade/settings", body).catch(() => ({ ok: false }));
    ntDirty = false;
    $("#ntSaved").textContent = r.ok ? "saved ✓" : "save failed";
    await pollNT();
  });
  $("#ntSwitch").addEventListener("change", async (e) => {
    const on = e.target.checked;
    e.target.checked = !on;                         // wait for the confirmation / server
    if (!on) { await api("/api/newstrade/enable", { enabled: false }).catch(() => null); return pollNT(); }
    const st = NT.settings;
    showModal("Turn NEWS TRADE on?",
      `<p><b>Automatic</b> na order ito sa paligid ng news${T.paper ? " (paper)" : ` — <b class="c-bear">LIVE, totoong order</b>`}, hiwalay sa AUTO TRADE.</p>
       <p>Bago ang bawat high-impact news (currencies at impact mula sa News settings), kapag naka-ARM:</p>
       <ol class="small">
         ${st.close_before_news ? `<li><b>${st.close_before_sec}s bago</b>: isasara ang <b>lahat ng open SuperChart trade</b> (master + followers). Hindi gagalawin ang trades ng ibang EA o manual sa MT5.</li>` : `<li class="c-warn">Hindi isasara ang open trades (naka-off).</li>`}
         <li><b>${st.place_before_sec}s bago</b>: <b>BUY STOP</b> sa itaas at <b>SELL STOP</b> sa ibaba ng
           ${st.nobox_mode === "distance" ? `consolidation box (${st.buffer_pips} pips buffer), o kung walang box, <b>presyo ± $${st.nobox_offset}</b> (SL $${st.nobox_sl})` : `consolidation box lang (walang box = walang trade)`}
           · TP ${st.tp_mode === "rr" ? "RR 1:" + st.tp_rr : "$" + st.tp_fixed} · master lot ${st.master_lot} + followers.</li>
         <li>Kapag na-trigger ang isa, kakanselahin ang kabila (OCO). Ang hindi na-trigger ay kakanselahin ${st.cancel_after_min} min pagkatapos ng news.</li></ol>
       <p class="muted">Sinusunod ang MASTER TRADING at ang risk limits (DD, target, risk per trade). Hindi papasok ang AUTO TRADE sa loob ng news window. Kapag nag-OFF o nag-DISARM, mag-o-OFF ito at kakanselahin ang lahat ng news pending orders. Laging OFF pagbukas ng app.</p>`,
      "Turn ON", async () => {
        hideModal();
        if (!(await ensureArmed())) return;
        const r = await api("/api/newstrade/enable", { enabled: true }).catch(() => ({ ok: false }));
        hideModal(); await pollNT();
        if (!r.ok) showModal("News trade", `<p class="c-bear">${esc(r.error || "failed")}</p>`, "", null);
      }, !T.paper);
  });
  $("#ntCancel").addEventListener("click", async () => {
    const r = await api("/api/newstrade/cancel", {}).catch(() => ({ ok: false }));
    showModal("News orders", `<p>${r.ok ? `Cancelled ${r.cancelled} pending order(s).` : "Failed."}</p>`, "", null);
    pollNT();
  });

  // ================================================================ trading mode (MANUAL / AUTO) + connected accounts
  function renderMode() {
    const card = $("#modeCard");
    if (!card) return;
    const dot = $("#tradeDot");
    if (dot) { dot.classList.toggle("on", !!((AT && AT.enabled) || (NT && NT.enabled))); dot.classList.toggle("warn", !!(AT && AT.enabled)); }
    if (!T || T.disabled) { card.hidden = true; return; }
    card.hidden = false;
    const auto = !!(AT && AT.enabled), news = !!(NT && NT.enabled);
    const st = AT && AT.settings ? AT.settings : null;
    $("#modeBig").textContent = auto ? "AUTO TRADE" : "MANUAL";
    card.classList.toggle("auto", auto);
    const chip = (t, c) => `<span class="mchip ${c}">${t}</span>`;
    $("#modeChips").innerHTML = [
      T.armed ? chip("ARMED", "warn") : chip("DISARMED", ""),
      T.paper ? chip("PAPER", "info") : chip("LIVE", "bear"),
      news ? chip("NEWS TRADE ON", "warn") : "",
      T.halted ? chip("HALTED", "bear") : "",
    ].join("");
    $("#modeSub").innerHTML = auto && st
      ? `⚡ ${esc(st.tf)} · ${st.router ? `router: trend 1:${st.router_trend_rr} / range arrow 1:${st.router_range_rr}` : esc(st.sources.join(" + "))} · lot ${st.lot} · today ${AT.today ?? 0}/${st.max_per_day}` +
        (AT.armed ? "" : ' · <span class="c-warn">DISARMED: walang papasok</span>')
      : `Ikaw ang papasok (BUY / SELL + confirm).${st ? ` AUTO: OFF` : ""}`;
    $("#modeSub").innerHTML += ` <button class="tool mini mode-go" id="modeGo">⚡ Trading →</button>`;
    const accts = [T.master ? Object.assign({ role: "master" }, T.master) : null, ...(T.followers || []).map((f) => Object.assign({ role: "follower" }, f))].filter(Boolean);
    $("#modeAccts").innerHTML = accts.map((a) => {
      const on = a.connected && !a.locked && (a.role === "master" || a.enabled !== false);
      const cls = !a.connected ? "off" : a.locked ? "lock" : a.enabled === false ? "dis" : "on";
      const why = !a.connected ? (a.error || a.status || "hindi konektado") : a.locked ? `locked: ${a.locked}` : a.enabled === false ? "disabled" : "konektado";
      return `<div class="macct ${cls}" title="${esc(why)}"><i></i><b>${esc(a.role === "master" ? "MASTER" : a.name)}</b>
        <span class="muted">${esc(a.login ? "#" + a.login : "")}${a.server ? " · " + esc(a.server) : ""}</span>
        <span class="mono">${a.equity != null ? fx(a.equity) : "--"}</span>
        <span class="muted">${a.role === "master" ? "" : "lot " + (a.lot ?? "-")}${a.day_pl != null ? ` · ${a.day_pl >= 0 ? "+" : ""}${fx(a.day_pl)}` : ""}</span>
        ${on ? "" : `<span class="c-warn small">${esc(why)}</span>`}</div>`;
    }).join("") || '<div class="muted small">Walang account.</div>';
  }
  setInterval(renderMode, 1000);
  $("#modeCard").addEventListener("click", (e) => { if (e.target.closest("#modeGo")) goTradeSet(); });

  // ================================================================ strip above the chart: MANUAL / AUTO · ARM · profile · BUY/SELL
  // Every button goes through the SAME confirm flows as the Trading page (ARM confirm, AUTO confirm, profile confirm).
  const manPop = $("#manPop");
  ["#tradeCard", "#groupsCard"].forEach((q) => { const el = $(q); if (el) manPop.appendChild(el); });   // manual ticket lives here now
  function renderStrip() {
    const strip = $("#tStrip");
    if (!strip) return;
    const trading = !!(T && !T.disabled);
    const auto = !!(AT && AT.enabled);
    $("#tsMode").hidden = !trading || !AT || AT.disabled;
    $("#tsArm").hidden = !trading;
    $("#tsManual").hidden = !trading;
    $("#tsMode").querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.mode === "auto") === auto));
    const arm = $("#tsArm");
    arm.textContent = trading ? (T.halted ? "⛔ HALTED" : T.paper ? "PAPER" : "LIVE") : "";
    arm.classList.toggle("armed", !!(trading && !T.paper));
    // master + followers: connected dot, account # and balance / equity (always visible above the chart)
    const accts = trading ? [T.master ? Object.assign({}, T.master, { role: "M" }) : null, ...(T.followers || []).map((f) => Object.assign({}, f, { role: "F" }))].filter(Boolean) : [];
    $("#tsAccts").innerHTML = accts.map((a) => {
      const ok = a.connected && !a.locked && !a.login_error && (a.role === "M" || a.enabled !== false);
      const why = !a.connected ? (a.error || a.status || "hindi konektado") : a.locked ? "locked: " + a.locked : a.login_error || (a.enabled === false ? "disabled" : "konektado");
      return `<span class="ts-acct ${ok ? "ok" : "bad"}" title="${esc((a.role === "M" ? "MASTER" : "FOLLOWER") + " " + (a.name || "") + " · " + why)}"><i></i>${a.role} ${a.login ? "#" + esc(a.login) : esc(a.name || "")} <b>${a.balance != null ? money(a.balance) : "--"}</b>${a.equity != null && a.balance != null && Math.abs(a.equity - a.balance) >= 0.01 ? ` <span class="${a.equity >= a.balance ? "c-bull" : "c-bear"}">eq ${money(a.equity)}</span>` : ""}</span>`;
    }).join("");
    const D = window.rgStratD;
    const prof = D && D.settings ? D.settings.profile : null;
    $("#tsProf").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.p === prof));
    const on = D && D.settings ? ["trend", "pullback", "conso"].filter((n) => D.settings[n].auto).map((n) => ({ trend: "TREND", pullback: "PULLBACK", conso: "CONSO" }[n])) : [];
    $("#tsStrat").hidden = !trading || !D || !D.settings;
    $("#tsStrat").querySelectorAll("[data-stauto]").forEach((b) => {
      const isOn = !!(D && D.settings && D.settings[b.dataset.stauto].auto);
      b.classList.toggle("on", isOn);
      b.innerHTML = `${isOn ? "●" : "○"} ${b.dataset.lbl}`;
      b.title = isOn ? "Naka-AUTO: papasukan ng AUTO kapag lahat green · i-click para gawing MANUAL" : "MANUAL: i-click para i-AUTO";
    });
    const st = AT && AT.settings;
    const extra = st && st.strategies_only === false && (st.sources || []).length ? ` <span class="muted" title="iba pang AUTO source (Trading → AUTO TRADE settings)">+ ${esc(st.sources.join(" + "))}</span>` : "";
    $("#tsInfo").innerHTML = !trading ? '<span class="muted">READ-ONLY: walang trading</span>'
      : auto ? `⚡ ${esc(st.tf)} · ${on.length ? "<b>" + esc(on.join(" + ")) + "</b>" : '<span class="c-warn">↑ i-click ang ○ ng strategy para i-AUTO</span>'}${extra} · today ${AT.today ?? 0}/${st.max_per_day}`
        : `✋ ikaw ang papasok (BUY / SELL + confirm)${prof === "custom" ? " · CUSTOM" : ""}`;
  }
  setInterval(renderStrip, 1000);
  // 🛡 profit protection (Position manager) on the strip
  async function pollPmChip() {
    const b = $("#tsPm");
    if (!b) return;
    const pm = await api("/api/positions/manager").catch(() => null);
    if (!pm || pm.disabled || !pm.settings || !T || T.disabled) { b.hidden = true; return; }
    const st = pm.settings, parts = [];
    if (st.be_r) parts.push(`BE ${st.be_r}R`);
    if (st.be_usd) parts.push(`BE $${st.be_usd}`);
    (st.profit_lock || []).forEach(([a, l]) => parts.push(`${a}R→lock ${l}R`));
    if (st.trail_mode && st.trail_mode !== "none") parts.push(`trail ${st.trail_mode}`);
    (st.partial_close || []).forEach(([a, f]) => parts.push(`close ${Math.round(f * 100)}% @${a}R`));
    b.hidden = false;
    b.classList.toggle("on", !!st.enabled);
    b.textContent = st.enabled ? `🛡 ${parts.join(" · ") || "ON"}` : "🛡 PROFIT PROTECT OFF";
  }
  setTimeout(pollPmChip, 2500);
  setInterval(pollPmChip, 15000);
  $("#tsPm").addEventListener("click", () => {
    document.querySelector('.nav[data-view="command"]').click();
    setTimeout(() => { const c = $("#pmForm"); if (c) scrollInside(c.closest(".card"), "start"); }, 300);
  });
  $("#tStrip").addEventListener("click", async (e) => {
    const m = e.target.closest("[data-mode]");
    if (m) {
      const want = m.dataset.mode === "auto";
      if (!!(AT && AT.enabled) === want) return;
      const sw = $("#autoSwitch");
      sw.checked = want;                           // the AUTO switch handler shows the confirm / turns it off
      sw.dispatchEvent(new Event("change"));
      return;
    }
    if (e.target.closest("#tsArm")) {
      if (T && T.halted) ensureArmed();             // HALTED: asks before resuming
      return;
    }
    if (e.target.closest("#tsManual")) {
      manPop.hidden = !manPop.hidden;
      $("#tsManual").classList.toggle("open", !manPop.hidden);
      return;
    }
    const pb = e.target.closest("[data-p]");
    if (pb) {
      const D = window.rgStratD, k = pb.dataset.p;
      const p = D && D.profiles && D.profiles[k];
      if (!p || (D.settings && D.settings.profile === k)) return;
      const anyAuto = ["trend", "pullback", "conso"].some((n) => D.settings[n].auto);
      const pr = p.protect || {};
      showModal(`Gamitin ang ${p.title}?`, `<p>${esc(p.desc)}</p><p>Papalitan ang settings ng TREND, PULLBACK at CONSOLIDATION, at magiging <b>${esc(p.auto_tf)}</b> ang AUTO TF.</p>
        <p>🛡 Profit protect: break-even sa <b>${pr.be_r}R</b>${(pr.profit_lock || []).map(([a, l]) => ` · sa ${a}R lock <b>${l}R</b>`).join("")}</p>
        ${anyAuto ? '<p class="c-warn">May strategy na naka-AUTO: ang bagong settings na ang gagamitin sa susunod na signal.</p>' : '<p class="muted">Walang ino-ON.</p>'}`,
        "Gamitin", async () => {
          const r = await api("/api/strategies/profile", { name: k }).catch(() => ({ ok: false }));
          hideModal();
          if (!r.ok) showModal("Profile", `<p class="c-bear">${esc(r.error || "failed")}</p>`, "", null);
          if (window.rgStratReload) window.rgStratReload();
          pollAT();
        }, anyAuto);
    }
  });
  document.addEventListener("click", (e) => {
    if (!manPop.hidden && !e.composedPath().includes(manPop) && !e.target.closest("#tsManual") && !e.target.closest("#confirmModal")) {
      manPop.hidden = true; $("#tsManual").classList.remove("open");
    }
  });
  function checkBuild(s) {
    const w = $("#buildWarn");
    if (!w || !s) return;
    const old = s.build !== BUILD;
    w.hidden = !old;
    if (old) w.innerHTML = `⚠ <b>Luma pa ang tumatakbong terminal</b> (server ${esc(s.build || "bago ang 2026-09-29")}, page ${BUILD}). I-close ang terminal at buksan ulit para gumana ang mga bagong feature (router, reversal, news trade). Laging OFF ang AUTO pagkatapos mag-restart.`;
  }

  // ================================================================ scroll buttons
  function scroller() {
    const view = [...document.querySelectorAll(".view")].find((v) => !v.hidden);
    for (const el of [view, document.querySelector("main")]) {
      if (el && el.scrollHeight > el.clientHeight + 2 && getComputedStyle(el).overflowY !== "visible") return el;
    }
    return document.querySelector("main");
  }
  function scrollStep(dir, all) {
    const el = scroller();
    el.scrollBy({ top: all ? dir * el.scrollHeight : dir * el.clientHeight * 0.7, behavior: "smooth" });
  }
  function updScrollBtns() {
    const el = scroller();
    $("#scrUp").disabled = el.scrollTop <= 2;
    $("#scrDown").disabled = el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
    $("#scrollBtns").hidden = el.scrollHeight <= el.clientHeight + 2;
  }
  $("#scrUp").addEventListener("click", () => scrollStep(-1, false));
  $("#scrDown").addEventListener("click", () => scrollStep(1, false));
  $("#scrUp").addEventListener("dblclick", () => scrollStep(-1, true));
  $("#scrDown").addEventListener("dblclick", () => scrollStep(1, true));
  document.addEventListener("scroll", updScrollBtns, true);
  window.addEventListener("resize", updScrollBtns);
  setInterval(updScrollBtns, 700);

  // ================================================================ backtest
  const WAB = { Structure: "Str", SMC: "SMC", Trend: "Trd", Momentum: "Mom", Volatility: "Vlt", Volume: "Vol", "Order Flow": "OF", Levels: "Lvl", Intermarket: "IM" };
  let btKeepOpt = null, BT = null, btChart = null, btSeries = null, btPoll = null, btShownKey = "";
  const fmtSetup = (c) => {
    const ses = c.sessions && c.sessions.length ? c.sessions.map((x) => x === "New York" ? "NY" : x).join("+") : "all";
    const w = c.weights ? " · w " + Object.entries(c.weights).filter(([k]) => k !== "Other").map(([k, v]) => `${WAB[k] || k.slice(0, 3)}${+v}`).join(" ") : "";
    return `pass ${Math.round(c.passing)}% · SL ${c.sl_mode}${c.sl_mode === "atr" ? " " + c.atr_sl + "×" : ""} · TP ${c.tp_source === "rr" ? c.rr + "R" : c.tp_source}
      · trail ${c.trail} · ⚠≤${c.max_cautions < 0 ? "any" : c.max_cautions} · ${ses}${w}`;
  };
  const stTxt = (st) => st.trades ? `${st.trades} tr · ${st.win_rate}% · <b class="${st.total_r >= 0 ? "c-bull" : "c-bear"}">${st.total_r >= 0 ? "+" : ""}${st.total_r}R</b> · PF ${st.pf ?? "-"} · DD ${st.max_dd_r}R` : '<span class="muted">0 trades</span>';
  async function pollBT(force = false) {
    if (!force && $("#view-backtest").hidden) return;
    let r;
    try { r = await api("/api/backtest"); } catch (e) { return; }
    if (r.disabled) return;
    BT = r;
    $("#btBar").style.width = Math.round((r.progress || 0) * 100) + "%";
    $("#btMsg").textContent = r.state === "running" ? r.msg : r.state === "error" ? "⚠ " + r.msg : r.state === "done" ? "done ✓" : "";
    $("#btRun").disabled = $("#btOpt").disabled = r.state === "running";
    $("#btCur").innerHTML = `<b>Current setup:</b> ${fmtSetup(r.current_setup)} <span class="muted">(Analyser Signals ⚙ + SCORING + Auto trade filters)</span>`;
    if (r.state === "done" && r.result) {
      const key = JSON.stringify([r.result.mode, r.result.current.stats, r.result.current.setup]);
      if (key !== btShownKey) {
        btShownKey = key;
        const x = r.result;            // form shows what the shown result was run with
        if (x.date_from) { $("#btDays").value = "dates"; $("#btFrom").value = x.date_from; $("#btTo").value = x.date_to; }
        else $("#btDays").value = String(x.days);
        btDateUi();
        $("#btSkip").value = x.skip_days || 0; $("#btTf").value = x.tf;
        $("#btSpread").value = x.spread; $("#btLot").value = x.lot;
        renderBT(x);
      }
    }
    if (r.state !== "running" && btPoll) { clearInterval(btPoll); btPoll = null; }
  }
  async function runBT(mode, setup = null, same = null) {
    const useDates = $("#btDays").value === "dates";
    if (useDates && (!$("#btFrom").value || !$("#btTo").value || $("#btTo").value < $("#btFrom").value)) {
      $("#btMsg").textContent = "⚠ pumili ng tamang From at To date"; return;
    }
    const body = { mode, days: useDates ? 30 : +$("#btDays").value, skip_days: useDates ? 0 : (+$("#btSkip").value || 0), tf: $("#btTf").value,
      date_from: useDates ? $("#btFrom").value : null, date_to: useDates ? $("#btTo").value : null,
      spread: +$("#btSpread").value, lot: +$("#btLot").value, objective: $("#btObj").value, min_trades: +$("#btMin").value,
      opt_weights: $("#btW").checked,
      spread_mode: $("#btSpMode").value, commission_per_lot: +$("#btComm").value || 0, slippage: +$("#btSlip").value || 0,
      swap_per_lot: +$("#btSwap").value || 0, execution_delay_ms: +$("#btDelay").value || 0, intrabar: $("#btIntra").checked,
      train_days: +$("#btTrain").value || 20, test_days: +$("#btTest").value || 5 };
    if (setup) body.setup = setup;
    const be = +$("#btBE").value || 0;
    if (be > 0) body.setup = Object.assign({}, body.setup || {}, { be_r: be });
    const entryM = $("#btEntry").value;
    if (entryM && entryM !== "analyser") body.setup = Object.assign({}, body.setup || {}, (entryM === "router" || entryM.startsWith("st_")) ? { entry: entryM } : { entry: entryM, rev_min_score: +$("#btRevMin").value || 60, rev_bias: $("#btRevBias").checked });
    const smcF = $("#btSmc").value;
    if (smcF && smcF !== "off") body.setup = Object.assign({}, body.setup || {}, { smc_filter: smcF });
    body.daily_target_r = +$("#btDT").value || 0;
    if (same) Object.assign(body, { days: same.days, skip_days: same.skip_days || 0, tf: same.tf, spread: same.spread, lot: same.lot,
      date_from: same.date_from || null, date_to: same.date_to || null }, same.costs || {});
    const r = await api("/api/backtest/run", body).catch((e) => ({ ok: false, error: String(e) }));
    if (!r.ok) { $("#btMsg").textContent = "⚠ " + (r.error || "failed"); return; }
    btShownKey = "";
    if (!btPoll) btPoll = setInterval(() => pollBT(true), 700);
    pollBT(true);
  }
  function btDateUi() {
    const on = $("#btDays").value === "dates";
    document.querySelectorAll(".bt-date").forEach((el) => { el.hidden = !on; });
    $("#btSkipWrap").hidden = on;
    if (on && !$("#btTo").value) {
      const d = new Date(), iso = (x) => x.toISOString().slice(0, 10);
      $("#btTo").value = iso(d); d.setDate(d.getDate() - 30); $("#btFrom").value = iso(d);
    }
  }
  $("#btDays").addEventListener("change", btDateUi);
  $("#btRun").addEventListener("click", () => { btKeepOpt = null; runBT("backtest"); });
  $("#btWF").addEventListener("click", () => { btKeepOpt = null; runBT("walkforward"); });
  $("#btOpt").addEventListener("click", () => { btKeepOpt = null; runBT("optimize"); });
  function renderBT(res) {
    const c = res.current, st = c.stats;
    $("#btResCard").hidden = false;
    const d = (t) => new Date(t * 1000).toISOString().slice(0, 10);
    const co = res.costs || {};
    const costTxt = [co.spread_mode === "variable" ? (c.variable_spread_available ? "variable spread" : "variable spread (no history → fixed)") : "",
      co.commission_per_lot ? `comm $${co.commission_per_lot}/lot` : "", co.slippage ? `slip $${co.slippage}` : "",
      co.swap_per_lot ? `swap $${co.swap_per_lot}/night` : "", co.execution_delay_ms ? `delay ${co.execution_delay_ms}ms → next open` : "",
      co.intrabar ? (c.m1_available ? `intrabar M1 (${c.intrabar_trades} trades)` : "intrabar: no M1 history → bar mode") : "",
      c.setup.be_r ? `BE at ${c.setup.be_r}R` : "", c.setup.smc_filter && c.setup.smc_filter !== "off" ? `SMC filter: ${c.setup.smc_filter}` : "",
      (c.setup.entry || "").startsWith("st_") ? `ENTRY: STRATEGY ${c.setup.entry.slice(3).toUpperCase()} (Trading-page settings)` :
      c.setup.entry === "router" ? `ENTRY: ROUTER trend 1:${c.setup.router_trend_rr} / range arrow 1:${c.setup.router_range_rr} (AUTO TRADE settings)` :
      c.setup.entry && c.setup.entry !== "analyser" ? `ENTRY: ${c.setup.entry === "reversal_ok" ? "arrow R✓" : "arrow R?+R✓"} (min ${c.setup.rev_min_score}${c.setup.rev_bias ? ", with HTF bias" : ""})` : ""].filter(Boolean).join(" · ");
    $("#btResSub").innerHTML = `${esc(res.source)} · ${res.tf} · ${d(res.period[0])} → ${d(res.period[1])} · ${res.bars.toLocaleString("en-US")} bars · spread $${res.spread}${costTxt ? " · " + esc(costTxt) : ""} · <br>${fmtSetup(c.setup)}`;
    $("#btMCCard").hidden = false;
    const sgn = c.signals || null;
    const r2 = (v, suf = "") => v == null ? "-" : `${v}${suf}`;
    $("#btInteg").innerHTML = `<div class="bt-integ-row">
      ${sgn ? `<span><b>${sgn.generated}</b> signals generated</span><span class="c-bear"><b>${sgn.blocked}</b> blocked</span><span class="c-bull"><b>${sgn.executed}</b> executed</span>
        <span class="muted small">${Object.entries(sgn.by_reason || {}).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</span>` : ""}</div>
      <div class="bt-integ-row small">
        <span>Loss rate <b>${r2(st.loss_rate, "%")}</b></span><span>Expectancy <b>${r2(st.expectancy_r, "R")}</b></span>
        <span>Avg win <b>${r2(st.avg_win_r, "R")}</b></span><span>Avg loss <b>${r2(st.avg_loss_r, "R")}</b></span>
        <span>Avg RR (planned) <b>${r2(st.avg_rr)}</b></span><span>Max losing streak <b>${r2(st.max_consec_losses)}</b></span>
        <span>Worst day <b>${r2(st.worst_day_r, "R")}</b></span>
        <span>Days ≥ ${r2(st.daily_target_r, "R")} target <b>${r2(st.daily_target_hit_rate, "%")}</b> of ${r2(st.trading_days)}</span></div>`;
    $("#btStats").innerHTML = [
      ["Trades", st.trades, `${st.wins} W · ${st.losses} L`], ["Win rate", st.win_rate == null ? "-" : st.win_rate + "%", ""],
      ["Total R", `<span class="${st.total_r >= 0 ? "c-bull" : "c-bear"}">${st.total_r >= 0 ? "+" : ""}${st.total_r}</span>`, `avg ${st.avg_r ?? "-"} R/trade`],
      ["Profit factor", st.pf ?? "-", ""], ["Max drawdown", `${st.max_dd_r} R`, st.calmar != null ? `R ÷ DD ${st.calmar}` : ""],
      ["P/L at lot " + res.lot, `<span class="${st.usd >= 0 ? "c-bull" : "c-bear"}">${st.usd >= 0 ? "+" : ""}$${money(st.usd)}</span>`, "≈ 100 oz/lot"],
    ].map(([l, v, sub]) => `<div class="jstat"><div class="klabel">${l}</div><div class="kval mono">${v}</div><div class="ksub">${sub}</div></div>`).join("");
    // equity curve
    const box = $("#btEq");
    if (!btChart) {
      btChart = LC.createChart(box, { autoSize: true, localization: { locale: "en-US" },
        layout: { background: { color: "transparent" }, textColor: "#8b98b8", fontSize: 11 },
        grid: { vertLines: { color: "rgba(148,163,184,.06)" }, horzLines: { color: "rgba(148,163,184,.06)" } },
        rightPriceScale: { borderColor: "rgba(148,163,184,.15)" }, timeScale: { borderColor: "rgba(148,163,184,.15)", timeVisible: true } });
      btSeries = btChart.addSeries(LC.BaselineSeries, { baseValue: { type: "price", price: 0 }, lineWidth: 2,
        topLineColor: "#26d7a0", topFillColor1: "rgba(38,215,160,.25)", topFillColor2: "rgba(38,215,160,.02)",
        bottomLineColor: "#ff5a78", bottomFillColor1: "rgba(255,90,120,.02)", bottomFillColor2: "rgba(255,90,120,.25)",
        priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
    }
    let lastT = 0;
    const pts = [];
    for (const [t, v] of c.curve) { let tt = t; if (tt <= lastT) tt = lastT + 1; lastT = tt; pts.push({ time: tt, value: v }); }
    btSeries.setData(pts);
    requestAnimationFrame(() => btChart.timeScale().fitContent());
    $("#btMonth").innerHTML = "<tr><th>Month</th><th>Trades</th><th>Win</th><th>R</th></tr>" + c.monthly.map((m) =>
      `<tr><td>${m.month}</td><td>${m.trades}</td><td>${Math.round(100 * m.wins / m.trades)}%</td><td class="${m.r >= 0 ? "c-bull" : "c-bear"}">${m.r >= 0 ? "+" : ""}${m.r}</td></tr>`).join("");
    // trades
    $("#btTrCard").hidden = !c.trades.length;
    const evTxt = (t) => (t.events || []).map((e) => `${new Date(e.t * 1000).toISOString().slice(11, 16)} ${e.kind} ${fx(e.price)}${e.r != null ? " (" + (e.r >= 0 ? "+" : "") + e.r + "R)" : ""}`).join("  →  ");
    $("#btTrades").innerHTML = "<tr><th>Time</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Exit</th><th>Result</th><th>R</th><th>Mode</th><th>Events</th></tr>" +
      [...c.trades].reverse().slice(0, 100).map((t) => `<tr><td class="muted">${new Date(t.t * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
        <td><span class="sigchip ${t.side === 1 ? "buy" : "sell"}">${t.side === 1 ? "BUY" : "SELL"}</span></td><td>${fx(t.entry)}</td><td>${fx(t.sl)}</td><td>${fx(t.tp)}</td>
        <td>${fx(t.exit)}</td><td>${t.result}</td><td class="${t.r >= 0 ? "c-bull" : "c-bear"}">${t.r >= 0 ? "+" : ""}${t.r}</td>
        <td class="muted">${esc(t.mode || "bar")}${t.costs ? " · -" + t.costs : ""}</td><td class="bt-ev" title="${esc(evTxt(t))}">${(t.events || []).length} ⓘ</td></tr>`).join("");
    // optimizer
    const o = res.opt;
    $("#btOptCard").hidden = !o;
    if (o) {
      $("#btOptSub").textContent = `${o.tested.toLocaleString("en-US")} setups tested · ${o.valid} with ≥${o.min_trades} trades · ${o.unique} unique · split ${d(o.split_t)} · goal: ${o.objective}`;
      const sens = (x) => !x ? '<span class="muted">-</span>' : x.stable === null ? '<span class="muted">n/a</span>' :
        `<span class="rchip ${x.stable ? "win" : "loss"}" title="${esc(x.rows.map((r) => `${r.change}: ${r.total_r >= 0 ? "+" : ""}${r.total_r}R (${r.trades} tr)`).join("\n"))}">${x.stable ? "✓ stable" : "POSSIBLE OVERFIT"}</span>`;
      $("#btOptTbl").innerHTML = `<tr><th>#</th><th>Setup</th><th>In-sample (70%)</th><th>Out-of-sample (30%)</th><th title="small parameter changes">Sensitivity</th><th></th><th></th></tr>` +
        o.rows.map((row, i) => `<tr class="${row.robust ? "robust" : ""}"><td>${i + 1}</td><td class="bt-setup">${fmtSetup(row.setup)}</td>
          <td>${stTxt(row.is_)}</td><td>${stTxt(row.oos)} ${row.robust ? '<span class="rchip win">✓ robust</span>' : '<span class="rchip loss">overfit?</span>'}</td>
          <td>${sens(row.sensitivity)}</td>
          <td><button class="btn-sm ghost" data-bt-test="${i}">Test</button></td><td><button class="btn-sm" data-bt-apply="${i}">Apply</button></td></tr>`).join("");
      if (!o.rows.length) $("#btOptTbl").innerHTML += '<tr><td colspan="7" class="muted">Walang setup na umabot sa min trades. Palakihin ang period o babaan ang min trades.</td></tr>';
    }
    const wf = res.wf;
    $("#btWFCard").hidden = !wf;
    if (wf) {
      $("#btWFSub").innerHTML = `${wf.windows} windows · train ${wf.train_days}d / test ${wf.test_days}d · OOS total <b class="${wf.total_oos_r >= 0 ? "c-bull" : "c-bear"}">${wf.total_oos_r >= 0 ? "+" : ""}${wf.total_oos_r}R</b> (${wf.oos_trades} trades) · profitable windows ${wf.profitable_windows}/${wf.tested} (${wf.pct_profitable ?? "-"}%) · parameter changes ${wf.parameter_changes}`;
      $("#btWFTbl").innerHTML = "<tr><th>#</th><th>Train</th><th>Test (unseen)</th><th>Chosen setup</th><th>In-sample</th><th>Out-of-sample</th></tr>" +
        wf.rows.map((r) => `<tr><td>${r.window}</td><td class="muted">${d(r.train[0])} → ${d(r.train[1])}</td><td>${d(r.test[0])} → ${d(r.test[1])}</td>
          <td class="bt-setup">${r.setup ? fmtSetup(r.setup) : esc(r.note || "-")}</td><td>${r.is_ ? stTxt(r.is_) : "-"}</td><td>${r.oos ? stTxt(r.oos) : "-"}</td></tr>`).join("");
    }
  }
  $("#mcRun").addEventListener("click", async () => {
    const q = `n=${+$("#mcN").value || 1000}&slip_r=${+$("#mcSlip").value || 0}&skip_pct=${+$("#mcSkip").value || 0}&source=${$("#mcSrc").value}`;
    $("#mcOut").innerHTML = '<span class="muted">running…</span>';
    const r = await api("/api/montecarlo?" + q).catch((x) => ({ ok: false, error: String(x) }));
    if (!r.ok) { $("#mcOut").innerHTML = `<span class="c-bear">${esc(r.error || "failed")}</span>`; return; }
    const sg = (v) => v == null ? "-" : `<span class="${v >= 0 ? "c-bull" : "c-bear"}">${v >= 0 ? "+" : ""}${v}</span>`;
    $("#mcOut").innerHTML = `<table class="tbl"><tr><th></th><th>Actual</th><th>5%</th><th>Median</th><th>95%</th></tr>
      <tr><td>Total R</td><td>${sg(r.actual.total_r)}</td><td>${sg(r.total_r.p5)}</td><td>${sg(r.total_r.median)}</td><td>${sg(r.total_r.p95)}</td></tr>
      <tr><td>Max drawdown R</td><td>${r.actual.max_dd_r}</td><td class="c-bear">${r.max_dd_r.p95} (95% worst)</td><td>${r.max_dd_r.median}</td><td class="muted">worst ${r.max_dd_r.worst}</td></tr>
      <tr><td>Losing streak</td><td>${r.actual.max_losing_streak}</td><td></td><td>${r.losing_streak.median}</td><td>${r.losing_streak.p95} (max ${r.losing_streak.max})</td></tr></table>
      <p class="muted small">${r.runs} runs · ${r.trades} trades (${esc(r.source)}) · chance na talo sa kabuuan: <b>${r.prob_loss}%</b>. ${!r.slip_r && !r.skip_pct ? 'Pareho ang Total R sa lahat ng run kapag walang slip/skip (order lang ang binago); tingnan ang drawdown at losing streak. ' : ''}${esc(r.note)}: ipinapakita kung gaano kasama ang puwedeng mangyari sa parehong mga trade kung iba ang pagkakasunod.</p>`;
  });
  $("#btOptTbl").addEventListener("click", (e) => {
    const res = BT && BT.result;
    if (!res || !res.opt) return;
    const ti = e.target.dataset.btTest, ai = e.target.dataset.btApply;
    if (ti !== undefined) { btKeepOpt = res.opt; runBT("backtest", res.opt.rows[+ti].setup, res); }
    if (ai !== undefined) {
      const row = res.opt.rows[+ai];
      showModal("Apply this setup?", `<p>${fmtSetup(row.setup)}</p>
        <p>In-sample: ${stTxt(row.is_)}<br>Out-of-sample: ${stTxt(row.oos)} ${row.robust ? '<span class="c-bull">✓ robust</span>' : '<span class="c-warn">⚠ mahina sa out-of-sample, pwedeng overfit</span>'}</p>
        <p class="muted">Isusulat ito sa: Analyser Signals ⚙ (SL/TP/trail), SCORING (passing score${row.setup.weights ? " at timbang" : ""}) at Auto trade filters (max ⚠, sessions).
        Makikita agad ang bagong arrows sa chart. Hindi nito binubuksan ang auto trade.</p>
        <p class="c-warn small">Ligtas na daan: OPTIMIZE → review → WALK-FORWARD → PAPER test ng ilang araw → saka manual na Apply para sa live. Hindi kailanman kusang ina-apply ng optimizer.</p>
        ${row.sensitivity && row.sensitivity.stable === false ? '<p class="c-bear">⚠ POSSIBLE OVERFIT: malaki ang nagbabago sa maliit na palit ng parameter.</p>' : ""}`, "Apply", async () => {
          const r = await api("/api/backtest/apply", { setup: row.setup }).catch(() => ({ ok: false }));
          pollBT(true);
          showModal("Backtest", r.ok ? "<p>✓ Applied. Tingnan ang chart at ang Signal score rate.</p>" : "<p class='c-bear'>Failed.</p>", "", null);
        });
    }
  });
    const _renderBT = renderBT;
  renderBT = function (res) { if (!res.opt && btKeepOpt && res.mode === "backtest") res.opt = btKeepOpt; _renderBT(res); };
  document.querySelector('.nav[data-view="backtest"]').addEventListener("click", () => setTimeout(() => pollBT(true), 30));

  // ================================================================ auto trade
  let AT = null, atSetOpen = false, atDirty = false;
  async function pollAT() {
    if (!T || T.disabled) { $("#autoCard").hidden = true; return; }
    try { AT = await api("/api/auto"); } catch (e) { return; }
    renderAT();
  }
  let lastAutoTf = null;
  function renderDiag() {
    const box = $("#autoDiag");
    if (!box) return;
    const d = AT && AT.diag;
    if (!d || d.error) { box.innerHTML = d && d.error ? `<div class="c-bear small">diagnosis: ${esc(d.error)}</div>` : ""; return; }
    const SRC = { analyser: "Analyser", breakout: "Conso box", trendline: "Trend line", reversal: "Reversal" };
    const fmtT = (t) => { if (!t) return ""; const x = new Date(t * 1000); return `${String(x.getUTCDate()).padStart(2, "0")} ${String(x.getUTCHours()).padStart(2, "0")}:${String(x.getUTCMinutes()).padStart(2, "0")}`; };
    const rows = d.checks.map((c) => `<div class="dg ${c.ok === true ? "ok" : c.ok === false ? "no" : "na"}"><i>${c.ok === true ? "✓" : c.ok === false ? "✗" : "•"}</i><b>${esc(c.name)}</b><span>${esc(c.text)}</span></div>`).join("");
    const last = (d.last_signals || []).map((g) => g.t
      ? `<div class="dg na"><i>↳</i><b>${esc(SRC[g.source] || g.source)}</b><span>huling arrow: <b class="${g.side === 1 ? "c-bull" : "c-bear"}">${g.side === 1 ? "BUY" : "SELL"}</b> ${fmtT(g.t)}${g.score != null ? " · " + Math.round(g.score) + "%" : ""} · ${g.bars_ago} ${esc(d.tf)} bar na ang nakalipas</span></div>`
      : `<div class="dg na"><i>↳</i><b>${esc(SRC[g.source] || g.source)}</b><span>${g.error ? esc(g.error) : "walang arrow sa history ng " + esc(d.tf)}</span></div>`).join("");
    const sk = d.last_skip ? `<div class="dg no"><i>⤫</i><b>Huling skip</b><span>${esc(d.last_skip.text)}</span></div>` : "";
    box.innerHTML = `<div class="dg-head ${d.ready ? "c-bull" : "c-warn"}">${d.ready ? "✓" : "⏳"} ${esc(d.summary)}</div>${rows}${last}${sk}`;
  }
  function renderAT() {
    try { renderDiag(); } catch (e) { /* never break the card */ }
    AUTO_TF_NOW = AT && !AT.disabled && AT.settings ? AT.settings.tf : null;
    if (AUTO_TF_NOW !== lastAutoTf) { lastAutoTf = AUTO_TF_NOW; renderTfs(); if (split) renderC2Tfs(); }
    const card = $("#autoCard");
    if (!AT || AT.disabled) { card.hidden = true; return; }
    card.hidden = false;
    const st = AT.settings;
    $("#autoSwitch").checked = AT.enabled;
    $("#autoLabel").textContent = AT.enabled ? "ON" : "OFF";
    card.classList.toggle("on", AT.enabled);
    $("#autoSub").textContent = `${st.tf} · ${st.router ? `ROUTER trend 1:${st.router_trend_rr} / range arrow 1:${st.router_range_rr}` : st.sources.join(" + ")} · lot ${st.lot}`;
    const lines = [];
    if (!AT.enabled) lines.push('<span class="muted">OFF · walang automatic na order. Signals lang ang nasa chart.</span>');
    else if (!AT.armed) lines.push('<span class="c-warn">DISARMED: hindi makakapag-trade</span>');
    else lines.push(`<span class="c-bull">ON · ${T && T.paper ? "paper" : "<b>LIVE</b>"} · kukunin ang bagong signal sa pagsara ng bawat ${st.tf} bar</span>`);
    lines.push(`Today: <b>${AT.today}/${st.max_per_day}</b> · session: ${AT.session_ok ? `<span class="c-bull">${esc(AT.session || "open")}</span>` : `<span class="c-warn">${esc(AT.session)}</span>`}`);
    lines.push(`<span class="muted">filters: ${st.direction} · max ⚠ ${st.max_cautions < 0 ? "any" : st.max_cautions} · ${st.min_score ? "score ≥ " + st.min_score + "%" : "passing score"} · ${st.sessions.join("/") || "all sessions"} · ${st.news_block ? "no news window" : "news allowed"} · max open ${st.max_open}</span>`);
    $("#autoStatus").innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
    $("#autoLog").innerHTML = (AT.log || []).slice(0, 6).map((l) => {
      const tt = new Date(l.t * 1000).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" });
      const cls = l.decision === "trade" ? "c-bull" : l.decision === "error" || l.kind === "error" ? "c-bear" : l.decision === "skip" ? "muted" : "";
      return `<div class="al ${cls}"><span class="mono">${tt}</span> ${esc(l.text.replace(/^AUTO (?=skip|BUY|SELL)/, ""))}</div>`;
    }).join("");
    if (atSetOpen && !atDirty) renderATSet();
  }
  function renderATSet() {
    const st = AT.settings;
    const sel = (k, opts) => `<select data-a="${k}">${opts.map((o) => `<option ${st[k] === o ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    const num = (k, step = "any") => `<input type="number" step="${step}" data-a="${k}" value="${st[k]}">`;
    const ck = (grp, v, on) => `<label class="ck"><input type="checkbox" data-g="${grp}" value="${v}" ${on ? "checked" : ""}> ${v}</label>`;
    $("#autoSet").innerHTML = `
      <label class="ck wide2"><input type="checkbox" data-a="strategies_only" ${st.strategies_only !== false ? "checked" : ""}> <b>STRATEGIES LANG</b>: ang mga strategy na naka-AUTO lang ang papasukan (TREND / PULLBACK / CONSOLIDATION). OFF = pati ang mga source sa ibaba (analyser, breakout…)</label>
      <label class="pf"><span>Master lot</span>${num("lot", "0.01")}</label>
      <label class="pf"><span>Timeframe</span>${sel("tf", ["M1", "M5", "M15", "M30", "H1"])}</label>
      <label class="pf"><span>Direction</span>${sel("direction", ["both", "buy", "sell"])}</label>
      <label class="pf"><span>Max ⚠ cautions (-1 = any)</span>${num("max_cautions", "1")}</label>
      <label class="pf"><span>Min score % (0 = passing)</span>${num("min_score", "1")}</label>
      <label class="pf"><span>Max auto trades / day</span>${num("max_per_day", "1")}</label>
      <label class="pf"><span>Max open trades</span>${num("max_open", "1")}</label>
      <label class="pf"><span>Re-entry: maghintay (min)</span>${num("reentry_minutes", "5")}</label>
      <label class="pf"><span>Max spread $ (0 = any)</span>${num("max_spread", "0.05")}</label>
      <div class="pf wide2"><span>Signals</span><div>${ck("sources", "analyser", st.sources.includes("analyser"))}${ck("sources", "breakout", st.sources.includes("breakout"))}${ck("sources", "trendline", st.sources.includes("trendline"))}${ck("sources", "reversal", st.sources.includes("reversal"))}</div></div>
      <div class="pf wide2 at-router"><span>Regime router (trend → 1:${st.router_trend_rr ?? 2} · range → arrow 1:${st.router_range_rr ?? 1})</span><div>
        <label class="ck"><input type="checkbox" data-a="router" ${st.router ? "checked" : ""}> <b>ON</b>: ang regime ang pipili kung aling signal ang papasok at ang RR</label>
        <label class="pf bt-inl"><span>Trend RR (1:x)</span>${num("router_trend_rr", "0.25")}</label>
        <label class="pf bt-inl"><span>Range RR (1:x)</span>${num("router_range_rr", "0.25")}</label>
        <div class="rt-row"><span class="muted small">TREND kapag:</span>${Object.keys(REG_COL).map((x) => ck("router_trend_labels", x, (st.router_trend_labels || []).includes(x))).join("")}</div>
        <div class="rt-row"><span class="muted small">RANGE (arrow) kapag:</span>${Object.keys(REG_COL).map((x) => ck("router_range_labels", x, (st.router_range_labels || []).includes(x))).join("")}</div>
        <label class="ck"><input type="checkbox" data-a="router_range_against_htf" ${st.router_range_against_htf ? "checked" : ""}> Range: pati arrow na kontra sa malinaw na HTF bias</label>
        <div class="muted small"><b>TREND</b>: analyser (at breakout / trend line kung naka-check) sa direksyon lang ng trend, TP = ${st.router_trend_rr ?? 2}× SL.
          <b>RANGE</b>: reversal arrow lang (BUY sa bottom, SELL sa top), TP = ${st.router_range_rr ?? 1}× SL. Ibang regime (VOLATILE, QUIET, TRANSITION): <b>walang trade</b>.
          Ang ibang setting sa ibaba (R✓ lang, min score) ay para rin sa arrows ng router.</div></div></div>
      <div class="pf wide2 at-rev"><span>Reversal arrows (R? / R✓)</span><div>
        <label class="ck"><input type="checkbox" data-a="reversal_confirmed_only" ${st.reversal_confirmed_only !== false ? "checked" : ""}> R✓ lang (kumpirmado: may close lampas sa reversal bar)</label>
        <label class="pf bt-inl"><span>Min reversal score</span>${num("reversal_min_score", "5")}</label>
        <label class="pf bt-inl"><span>Kumpirmasyon</span><select data-a="reversal_confirm"><option value="bias" ${st.reversal_confirm !== "score" ? "selected" : ""}>HTF bias lang (dulo ng pullback)</option><option value="score" ${st.reversal_confirm === "score" ? "selected" : ""}>HTF bias + pasadong score</option></select></label>
        <div class="muted small">Papasok lang ang arrow kapag <b>kasabay ng HTF bias</b>: hindi tini-trade ang kontra-trend na arrow. Dadaan pa rin sa lahat ng gate (session, news, risk, max trades).</div></div></div>
      <div class="pf wide2"><span>Sessions (UTC)</span><div>${["Asia", "London", "New York"].map((x) => ck("sessions", x, st.sessions.includes(x))).join("")}</div></div>
      <label class="ck wide2"><input type="checkbox" data-a="news_block" ${st.news_block ? "checked" : ""}> Walang entry sa news warning window</label>
      <div class="pf wide2"><span>Market regime (walang check = kahit ano)</span><div>${Object.keys(REG_COL).map((x) => ck("regimes", x, (st.regimes || []).includes(x))).join("")}</div></div>
      <label class="ck wide2"><input type="checkbox" data-a="no_opposite" ${st.no_opposite !== false ? "checked" : ""}> <b>Bawal ang kontrang trade</b>: kapag may bukas na BUY, walang SELL (at kabaligtaran). Kasama ang manual na entry.</label>
      <label class="ck wide2"><input type="checkbox" data-a="score_confirm" ${st.score_confirm !== false ? "checked" : ""}> Breakout / trend line: papasok lang kapag <b>kinumpirma ng analyser score</b> (kasabay ng bias at pasado sa passing score)</label>
      <label class="ck wide2"><input type="checkbox" data-a="no_countertrend" ${st.no_countertrend ? "checked" : ""}> Bawal ang kontra sa trend (TREND UP: walang SELL, TREND DOWN: walang BUY)</label>
      <div class="acct-actions wide2"><button class="btn-sm" id="atSave">Save</button><span class="muted" id="atSaved"></span></div>
      <p class="muted small wide2">Re-entry (max open &gt; 1): walang sabay na pasok. Ang dagdag na trade sa parehong direksyon ay papasok lang pagkatapos ng oras na ito, kung kasabay pa ng HTF bias, pasado pa ang score, at hindi kontra ang regime. Para sa Analyser Signals, i-OFF ang <b>one_at_a_time</b> sa ⚙ nito para may bagong arrow habang bukas pa ang nauna.</p>
      <p class="muted small wide2">SL/TP = ang SL at TP ng signal (tingnan ang ⚙ ng Analyser Signals). Kailangang naka-ON sa LAYERS ang signal plugin.</p>`;
  }
  const goTradeSet = (target) => {
    const nav = document.querySelector('.nav[data-view="tradeset"]');
    if ($("#view-tradeset").hidden) nav.click();
    if (target) setTimeout(() => { const el = $(target); if (el) scrollInside(el, "start"); }, 120);
  };
  $("#autoGear").addEventListener("click", () => goTradeSet("#autoSetCard"));
  document.querySelector('.nav[data-view="tradeset"]').addEventListener("click", () => setTimeout(() => {
    atSetOpen = true; ntSetOpen = true; atDirty = false; ntDirty = false;
    if (AT) renderATSet(); else pollAT().then(() => AT && renderATSet());
    if (NT) renderNTSet(); else pollNT().then(() => NT && renderNTSet());
  }, 0));
  $("#autoSet").addEventListener("input", () => { atDirty = true; });
  $("#autoSet").addEventListener("click", async (e) => {
    if (e.target.id !== "atSave") return;
    const body = {};
    document.querySelectorAll("#autoSet [data-a]").forEach((el) => { body[el.dataset.a] = el.type === "checkbox" ? el.checked : el.value; });
    for (const g of ["sources", "sessions", "regimes", "router_trend_labels", "router_range_labels"]) body[g] = [...document.querySelectorAll(`#autoSet [data-g="${g}"]`)].filter((x) => x.checked).map((x) => x.value);
    const r = await api("/api/auto/settings", body).catch(() => ({ ok: false }));
    atDirty = false;
    $("#atSaved").textContent = r.ok ? "saved ✓" : "save failed";
    pollAT();
  });
  $("#autoSwitch").addEventListener("change", async (e) => {
    const on = e.target.checked;
    e.target.checked = !on;
    if (!on) { await api("/api/auto/enable", { enabled: false }).catch(() => null); return pollAT(); }
    const st = AT.settings;
    const plugOn = (key) => S && S.plugins && S.plugins.some((p) => p.key === key && p.enabled);
    const missing = st.strategies_only !== false ? [] : st.sources.filter((x) => !plugOn({ analyser: "analyser_signals.AnalyserSignals", breakout: "conso_breakout.ConsoBreakoutSignals", trendline: "trendline_breakout.TrendlineBreakout", reversal: "reversal_signals.ReversalSignals" }[x]));
    showModal("Turn AUTO TRADE on?",
      `<p>Kapag naka-ON at naka-ARM, <b>kusang magpapadala ng order</b>${T.paper ? " (paper)" : ` — <b class="c-bear">LIVE, totoong pera</b>`} sa master at sa lahat ng naka-enable na follower,
        ${st.strategies_only !== false ? `kapag <b>lahat green</b> ang strategy na naka-AUTO (${esc(((window.rgStratD && window.rgStratD.settings) ? ["trend", "pullback", "conso"].filter((n) => window.rgStratD.settings[n].auto).map((n) => n.toUpperCase()).join(", ") : "") || "wala pa: i-click ang ○ ng strategy")}).` : `tuwing may bagong <b>${esc(st.sources.join(" / "))}</b> signal sa pagsara ng ${st.tf} bar.`}</p>
       <ul class="small"><li>Lot ng master: <b>${st.lot}</b> (followers: sariling fixed lot)</li>
        <li>Max <b>${st.max_per_day}</b> auto trade bawat araw · max <b>${st.max_open}</b> bukas${st.max_open > 1 ? ` · re-entry pagkatapos ng <b>${st.reentry_minutes ?? 30} min</b> kung nasa trend pa at pasado pa` : ""}${st.no_opposite !== false ? " · <b>walang kontrang trade</b>" : ""}</li>
        <li>Sessions: ${esc(st.sessions.join(", ") || "lahat")} · ${st.news_block ? "walang entry sa news window" : "<b class='c-warn'>pwede kahit may news</b>"}</li>
        <li>Max ⚠ caution: ${st.max_cautions < 0 ? "<b class='c-warn'>kahit ilan</b>" : st.max_cautions} · direksyon: ${st.direction}</li>
        ${st.router ? `<li><b>Regime router ON</b>: ${esc((st.router_trend_labels || []).join("/"))} → trend signal 1:${st.router_trend_rr} · ${esc((st.router_range_labels || []).join("/"))} → reversal arrow 1:${st.router_range_rr} · ibang regime: walang trade</li>` : ""}
        ${st.sources.includes("reversal") ? `<li>Reversal arrows: ${st.reversal_confirmed_only !== false ? "R✓ lang" : "<b class='c-warn'>pati R? (hindi pa kumpirmado)</b>"} · min score ${st.reversal_min_score ?? 60} · ${st.reversal_confirm === "score" ? "bias + pasadong score" : "HTF bias lang"}</li>` : ""}</ul>
       ${missing.length ? `<p class="c-warn">⚠ Naka-OFF sa LAYERS: ${esc(missing.join(", "))}. I-ON muna ang plugin para may signal.</p>` : ""}
       <p class="muted">Hindi nito kukunin ang mga lumang arrow sa chart, pati ang mga bagong nabuo pa lang. Laging OFF pagbukas ng app. Ang pag-ON nito ay nag-e-enable din ng trading (wala nang hiwalay na ARM).</p>`,
      "Turn AUTO ON", async () => {
        hideModal();
        if (!(await ensureArmed())) return;
        const r = await api("/api/auto/enable", { enabled: true }).catch((x) => ({ ok: false, error: String(x) }));
        if (!r.ok) showModal("Auto trade", `<p class="c-bear">${esc(r.error || "failed")}</p>`, "", null);
        else hideModal();
        pollAT();
      }, !T.paper);
  });

  // ================================================================ order flow (footprint)
  let OF = null, ofTf = lsGet("rg_of_tf", "M5"), ofN = parseInt(lsGet("rg_of_n", "12"), 10) || 12, ofRowDirty = false;
  $("#ofTfs").innerHTML = ["M1", "M5", "M15", "M30", "H1"].map((t) => `<button data-tf="${t}" class="${t === ofTf ? "on" : ""}">${t}</button>`).join("");
  $("#ofN").value = String(ofN);
  $("#ofTfs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tf]");
    if (!b) return;
    ofTf = b.dataset.tf; lsSet("rg_of_tf", ofTf); ofRowDirty = false;
    $("#ofTfs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.tf === ofTf));
    pollOF(true);
  });
  $("#ofN").addEventListener("change", (e) => { ofN = parseInt(e.target.value, 10); lsSet("rg_of_n", String(ofN)); pollOF(true); });
  $("#ofRow").addEventListener("input", () => { ofRowDirty = true; });
  $("#ofRow").addEventListener("change", async (e) => {
    const row = parseFloat(e.target.value);
    if (!(row > 0)) return;
    await api("/api/orderflow/row", { tf: ofTf, row }).catch(() => null);
    ofRowDirty = false;
    pollOF(true);
  });
  async function pollOF(force = false) {
    if (!force && $("#view-orderflow").hidden) return;
    try { OF = await api(`/api/orderflow?tf=${ofTf}&n=${ofN}`); } catch (e) { return; }
    if (OF.disabled) return;
    if (!ofRowDirty) $("#ofRow").value = OF.row;
    const cov = OF.coverage_from ? new Date(OF.coverage_from * 1000).toISOString().slice(5, 16).replace("T", " ") : "-";
    $("#ofStatus").textContent = `${OF.status} · data from ${cov} (server time)`;
    drawFootprint();
    $("#ofEvents").innerHTML = OF.events.length ? OF.events.slice(0, 40).map((e) => {
      const tt = new Date(e.t * 1000).toISOString().slice(11, 16);
      const cls = e.side > 0 ? "c-bull" : e.side < 0 ? "c-bear" : "c-warn";
      const tag = { absorption: "ABSORPTION", stacked: "STACKED", divergence: "Δ DIVERGENCE" }[e.kind];
      return `<div class="of-ev"><span class="muted mono">${tt}</span><b class="${cls}">${tag}</b><span>${esc(e.text)}</span></div>`;
    }).join("") : '<p class="muted">Walang event sa mga bar na ito.</p>';
  }
  function drawFootprint() {
    const cv = $("#ofCanvas"), wrap = $("#ofWrap");
    if (!OF || !OF.bars || !cv) return;
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    if (!W || !H) return;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const bars = OF.bars, row = OF.row;
    if (!bars.length) { g.fillStyle = "#8b98b8"; g.font = "13px sans-serif"; g.fillText("Waiting for ticks…", 20, 30); return; }
    const axisW = 62, bottomH = 104, topPad = 6;
    const mainH = H - bottomH - topPad;
    let lo = Infinity, hi = -Infinity, maxCell = 1, maxVol = 1, maxAbsD = 1;
    for (const b of bars) {
      for (const r of b.rows) { lo = Math.min(lo, r[0]); hi = Math.max(hi, r[0]); maxCell = Math.max(maxCell, r[1], r[2]); }
      maxVol = Math.max(maxVol, b.vol); maxAbsD = Math.max(maxAbsD, Math.abs(b.delta));
    }
    const nRows = Math.round((hi - lo) / row) + 1;
    const rowPx = mainH / nRows;
    const colW = (W - axisW) / bars.length;
    const yOf = (p) => topPad + (hi - p) / row * rowPx;
    const fmt = (v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)));
    const showText = colW >= 50 && rowPx >= 10;
    const fs = Math.max(8, Math.min(12, rowPx - 2));
    g.font = `${fs}px "JetBrains Mono", monospace`;
    g.textBaseline = "middle";
    // price axis grid
    g.fillStyle = "#8b98b8";
    const every = Math.max(1, Math.ceil(16 / rowPx));
    for (let k = 0; k < nRows; k += every) {
      const p = hi - k * row, y = yOf(p);
      g.fillStyle = "rgba(148,163,184,.06)"; g.fillRect(0, y, W - axisW, 1);
      g.fillStyle = "#8b98b8"; g.textAlign = "left"; g.font = "10.5px monospace"; g.fillText(p.toFixed(2), W - axisW + 6, y + rowPx / 2);
    }
    g.font = `${fs}px monospace`;
    bars.forEach((b, i) => {
      const x0 = i * colW, cw = colW - 3, half = (cw - 6) / 2;
      // candle strip
      const up = b.c >= b.o;
      g.fillStyle = up ? "#26d7a0" : "#ff5a78";
      g.fillRect(x0 + 2, yOf(b.h + 0) , 1, Math.max(1, yOf(b.l) - yOf(b.h) + rowPx));
      const yo = yOf(Math.max(b.o, b.c)), yc = yOf(Math.min(b.o, b.c));
      g.fillRect(x0, yo, 5, Math.max(2, yc - yo + rowPx));
      for (const r of b.rows) {
        const y = yOf(r[0]);
        const sA = r[2] / maxCell, bA = r[1] / maxCell;
        g.fillStyle = `rgba(255,90,120,${0.08 + sA * 0.55})`; g.fillRect(x0 + 6, y + 0.5, half, rowPx - 1);
        g.fillStyle = `rgba(38,215,160,${0.08 + bA * 0.55})`; g.fillRect(x0 + 6 + half, y + 0.5, half, rowPx - 1);
        if (r[4]) { g.strokeStyle = "#ff5a78"; g.lineWidth = 1.5; g.strokeRect(x0 + 6.5, y + 1, half - 1, rowPx - 2); }
        if (r[3]) { g.strokeStyle = "#26d7a0"; g.lineWidth = 1.5; g.strokeRect(x0 + 6 + half + 0.5, y + 1, half - 1, rowPx - 2); }
        if (showText) {
          g.textAlign = "right"; g.fillStyle = r[4] ? "#ffd1da" : "#f1a3b3"; g.fillText(fmt(r[2]), x0 + 6 + half - 3, y + rowPx / 2);
          g.textAlign = "left"; g.fillStyle = r[3] ? "#c9ffe9" : "#8fe3c6"; g.fillText(fmt(r[1]), x0 + 6 + half + 3, y + rowPx / 2);
        }
        if (b.poc !== null && Math.abs(r[0] - b.poc) < 1e-6) { g.strokeStyle = "#fbbf24"; g.lineWidth = 1.5; g.strokeRect(x0 + 6, y + 0.5, cw - 6, rowPx - 1); }
      }
      // event badges on top of the column
      (b.events || []).forEach((e, k) => {
        const ch = { absorption: "A", stacked: "S", divergence: "D" }[e.kind];
        g.fillStyle = e.side > 0 ? "#26d7a0" : e.side < 0 ? "#ff5a78" : "#fbbf24";
        g.beginPath(); g.arc(x0 + cw - 7 - k * 15, topPad + 8, 6.5, 0, 7); g.fill();
        g.fillStyle = "#0b1220"; g.textAlign = "center"; g.font = "bold 9px sans-serif"; g.fillText(ch, x0 + cw - 7 - k * 15, topPad + 8.5);
        g.font = `${fs}px monospace`;
      });
      // bottom: delta, volume, time
      const yb = H - bottomH;
      g.textAlign = "center"; g.font = "10.5px monospace";
      g.fillStyle = b.delta >= 0 ? "rgba(38,215,160,.18)" : "rgba(255,90,120,.18)";
      g.fillRect(x0 + 1, yb + 4, cw, 18 * Math.min(1, Math.abs(b.delta) / maxAbsD) + 2);
      g.fillStyle = b.delta >= 0 ? "#26d7a0" : "#ff5a78"; g.fillText((b.delta >= 0 ? "+" : "") + fmt(b.delta), x0 + cw / 2, yb + 13);
      g.fillStyle = "#8b98b8"; g.fillText(fmt(b.vol), x0 + cw / 2, yb + 31);
      g.fillStyle = "#64748b"; g.fillText(new Date(b.t * 1000).toISOString().slice(11, 16), x0 + cw / 2, H - 8);
    });
    // labels + cumulative delta line
    const yb = H - bottomH;
    g.textAlign = "left"; g.fillStyle = "#64748b"; g.font = "10px sans-serif";
    g.fillText("Δ", W - axisW + 6, yb + 13); g.fillText("VOL", W - axisW + 6, yb + 31); g.fillText("CumΔ", W - axisW + 6, yb + 58);
    const cums = bars.map((b) => b.cum), cmin = Math.min(...cums), cmax = Math.max(...cums), cy0 = yb + 42, ch = 34;
    g.strokeStyle = "#fbbf24"; g.lineWidth = 2; g.beginPath();
    bars.forEach((b, i) => {
      const x = i * colW + colW / 2, y = cy0 + ch - (cmax === cmin ? ch / 2 : (b.cum - cmin) / (cmax - cmin) * ch);
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.stroke();
    g.fillStyle = "#fbbf24"; g.fillText(fmt(bars[bars.length - 1].cum), W - axisW + 6, yb + 72);
  }
  window.addEventListener("resize", () => { if (!$("#view-orderflow").hidden) drawFootprint(); });
  document.querySelector('.nav[data-view="orderflow"]').addEventListener("click", () => setTimeout(() => pollOF(true), 30));

  // ================================================================ emergency controls
  $("#btnStopNew").addEventListener("click", () => showModal("STOP NEW TRADES?",
    `<p>• AUTO TRADE OFF<br>• DISARM (walang bagong order)<br>• Kakanselahin ang lahat ng REYGUN pending orders</p>
     <p class="muted">Mananatiling bukas ang mga position. Para magpatuloy, i-ARM ulit.</p>`, "STOP NEW TRADES", async () => {
      const r = await api("/api/trade/stopnew", {}).catch(() => ({ ok: false }));
      showModal("STOP NEW TRADES", r.ok ? `<p>✓ Tapos. ${r.cancelled} pending order(s) cancelled.</p>` : "<p class='c-bear'>failed</p>", "", null);
      pollTrading();
    }, true));
  $("#btnEmergency").addEventListener("click", () => showModal("🛑 EMERGENCY STOP?",
    `<p>• AUTO TRADE OFF · DISARM · cancel REYGUN pending orders</p>
     <label class="ck"><input type="checkbox" id="emClose"> <b>Isara rin ang lahat ng SuperChart positions</b> (master + followers)</label>
     <p class="muted">Hindi kailanman gagalawin ang trades ng ibang EA o ang manual trades mo sa MT5.</p>`, "EMERGENCY STOP", async () => {
      const close_positions = !!($("#emClose") && $("#emClose").checked);
      const r = await api("/api/trade/emergency", { close_positions }).catch(() => ({ ok: false }));
      showModal("EMERGENCY STOP", r.ok ? `<p>✓ Tapos. ${r.cancelled} pending cancelled${close_positions ? " · SuperChart positions closed" : ""}.</p>` : "<p class='c-bear'>failed</p>", "", null);
      pollTrading();
    }, true));

  // ================================================================ exit guard
  let EG = null, egDirty = false;
  async function pollEG() {
    try { EG = await api("/api/exitguard"); } catch (e) { return; }
    if (!EG || EG.disabled) return;
    $("#egCard").hidden = false;
    const st = EG.settings;
    $("#egSwitch").checked = st.enabled;
    $("#egLabel").textContent = st.enabled ? (EG.armed ? "ON" : "ON · DISARMED") : "OFF";
    $("#egLabel").className = "arm-label" + (st.enabled && EG.armed ? " on" : "");
    if (!egDirty) {
      const sel = (k, opts) => `<select data-e="${k}">${opts.map(([v, l]) => `<option value="${v}" ${String(st[k]) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
      $("#egForm").innerHTML = `
        <label class="pf"><span>Kapag</span>${sel("trigger", [["trend_change", "TREND CHANGE kontra"], ["tl_break", "TL✓ break kontra"]])}</label>
        <label class="pf"><span>Gawin</span>${sel("action", [["close", "Close lahat"], ["partial", "Partial close"], ["breakeven", "SL → breakeven"], ["alert", "Alert lang"]])}</label>
        <label class="pf"><span>Timeframe</span>${sel("tf", [["M5", "M5"], ["M15", "M15"], ["M30", "M30"], ["H1", "H1"]])}</label>
        <label class="pf"><span>Trades</span>${sel("scope", [["all", "Lahat"], ["auto", "Auto lang"], ["manual", "Manual lang"]])}</label>
        ${st.action === "partial" ? `<label class="pf"><span>Partial %</span><input type="number" data-e="partial" value="${Math.round(st.partial * 100)}" min="10" max="90" step="10"></label>` : ""}
        <div class="acct-actions wide2"><button class="btn-sm" id="egSave">Save</button><span class="muted small">Gumagana lang kapag naka-ARM. ${EG.open} bukas na trade.</span></div>`;
    }
    $("#egLog").innerHTML = EG.log.length ? EG.log.map((l) => `<div class="al-row ${l.kind === "warn" ? "c-warn" : ""}"><span class="muted">${new Date(l.t * 1000).toLocaleTimeString("en-US", { hour12: false })}</span> ${esc(l.text)}</div>`).join("")
      : '<div class="muted small">Wala pang aksyon.</div>';
  }
  $("#egForm").addEventListener("input", () => { egDirty = true; });
  $("#egForm").addEventListener("change", (e) => { if (e.target.dataset.e === "action") { egSave(); } });
  async function egSave() {
    const b = {};
    document.querySelectorAll("#egForm [data-e]").forEach((el) => { b[el.dataset.e] = el.dataset.e === "partial" ? (+el.value) / 100 : el.value; });
    await api("/api/exitguard/settings", b).catch(() => null);
    egDirty = false; pollEG();
  }
  $("#egForm").addEventListener("click", (e) => { if (e.target.id === "egSave") egSave(); });
  $("#egSwitch").addEventListener("change", async (e) => {
    await api("/api/exitguard/settings", { enabled: e.target.checked }).catch(() => null);
    pollEG();
  });

  // ================================================================ market regime (overview card)
  let RG = null, regSetOpen = false;
  async function pollRegime() {
    try { RG = await api("/api/regime"); } catch (e) { return; }
    if (!RG || RG.disabled) return;
    const m = RG.tf_read, h = RG.htf_read, st = RG.settings;
    $("#regSub").textContent = `${st.tf} + ${st.htf}`;
    const chip = (r) => `<span class="regchip" style="--c:${r.color}">${esc(r.label)}</span>`;
    $("#regBody").innerHTML = `<div class="reg-top">${chip(m)} <span class="muted">${st.tf}</span>
        <span class="reg-htf">${chip(h)} <span class="muted">${st.htf}</span></span></div>
      <div class="reg-why mono">${m.why.map(esc).join(" · ")}</div>
      ${RG.agree === false ? '<div class="c-warn small">⚠ Salungat ang mas mataas na TF</div>' : RG.agree ? '<div class="c-bull small">✓ Sang-ayon ang mas mataas na TF</div>' : ""}
      <div class="reg-adv">${esc(RG.advice)}</div>`;
    if (regSetOpen && !$("#regSet").dataset.dirty) renderRegSet();
  }
  function renderRegSet() {
    const st = RG.settings;
    const tfSel = (k) => `<select data-r="${k}">${["M5", "M15", "M30", "H1", "H4", "D1"].map((t) => `<option ${t === st[k] ? "selected" : ""}>${t}</option>`).join("")}</select>`;
    const num = (k) => `<input type="number" data-r="${k}" value="${st[k]}" step="1">`;
    $("#regSet").innerHTML = `<div class="sys-form">
      <label class="pf"><span>Main TF</span>${tfSel("tf")}</label><label class="pf"><span>Higher TF</span>${tfSel("htf")}</label>
      <label class="pf"><span>Trend: ADX ≥</span>${num("trend_adx")}</label><label class="pf"><span>Range: ADX &lt;</span>${num("range_adx")}</label>
      <label class="pf"><span>Choppy: Chop ≥</span>${num("chop_hi")}</label><label class="pf"><span>Volatile: ATR pct ≥</span>${num("vol_hi")}</label>
      <label class="pf"><span>Quiet: ATR pct ≤</span>${num("vol_lo")}</label>
      <div class="acct-actions"><button class="btn-sm" id="regSave">Save</button></div></div>`;
  }
  $("#regGear").addEventListener("click", () => { regSetOpen = !regSetOpen; $("#regSet").hidden = !regSetOpen; delete $("#regSet").dataset.dirty; if (regSetOpen && RG) renderRegSet(); });
  $("#regSet").addEventListener("input", () => { $("#regSet").dataset.dirty = "1"; });
  $("#regSet").addEventListener("click", async (e) => {
    if (e.target.id !== "regSave") return;
    const b = {};
    document.querySelectorAll("#regSet [data-r]").forEach((el) => { b[el.dataset.r] = el.value; });
    await api("/api/regime/settings", b).catch(() => null);
    delete $("#regSet").dataset.dirty;
    pollRegime();
  });

  // ================================================================ system: health, telegram, drift, report
  let SYS = null, tgDirty = false, drDirty = false;
  const LVL = { ok: ["🟢", "c-bull"], na: ["⚪", "muted"], warn: ["🟡", "c-warn"], bad: ["🔴", "c-bear"], info: ["ℹ️", "muted"] };
  const agoS = (t) => { const s = Math.max(0, Date.now() / 1000 - t); return s < 60 ? `${s.toFixed(0)}s` : s < 3600 ? `${(s / 60).toFixed(0)}m` : `${(s / 3600).toFixed(1)}h`; };
  async function pollSys(force = false) {
    const onPage = !$("#view-system").hidden;
    if (!force && !onPage && SYS && Date.now() - SYS._t < 10000) return;
    try { SYS = await api("/api/system"); SYS._t = Date.now(); } catch (e) { return; }
    const hw = SYS.health;
    if (hw) {
      const dot = $("#healthDot");
      dot.className = "navdot " + hw.status;
      dot.title = "health: " + hw.status;
    }
    if (onPage || force) renderSys();
  }
  function renderSys() {
    const hw = SYS.health;
    if (hw) {
      $("#hwSub").textContent = `${hw.status.toUpperCase()} · gold market ${hw.market_open ? "open" : "closed"}`;
      $("#hwBody").innerHTML = `<table class="tbl hw-tbl">${hw.checks.map((c) => `<tr><td>${LVL[c.level][0]}</td><td><b>${esc(c.name)}</b></td>
        <td class="${LVL[c.level][1]}">${esc(c.text)}</td><td class="muted">${agoS(c.since)}</td></tr>`).join("")}</table>`;
    }
    renderTg();
    renderDrift();
    const rp = SYS.report;
    $("#eodSub").textContent = SYS.telegram ? `awtomatiko ${SYS.telegram.settings.eod_hour < 0 ? "OFF" : `${String(SYS.telegram.settings.eod_hour).padStart(2, "0")}:00 (UTC${SYS.telegram.settings.tz_offset >= 0 ? "+" : ""}${SYS.telegram.settings.tz_offset})`}` : "";
    if (rp && !$("#eodText").dataset.preview) $("#eodText").innerHTML = `<div class="muted small">huling report ${esc(rp.day)}</div><pre>${rp.text.replace(/<(?!\/?b>)[^>]+>/g, "")}</pre>`
      + (rp.png ? `<img class="snapimg" src="/report/${esc(rp.day)}.png?v=${Math.round(rp.t)}">` : "");
  }
  function renderTg() {
    const tg = SYS.telegram;
    if (!tg) return;
    const st = tg.settings;
    $("#tgSub").innerHTML = tg.searching ? `<span class="c-warn">⏳ hinahanap ang chat id: ${esc(tg.search_msg || "i-message ang bot mo")}</span>` : tg.ready ? `<span class="c-bull">● ON</span> · ${tg.sent} sent${tg.last_error ? ` · <span class="c-bear">${esc(tg.last_error)}</span>` : ""}`
      : `<span class="muted">OFF</span>${tg.last_error ? ` · <span class="c-bear">${esc(tg.last_error)}</span>` : ""}`;
    $("#tgLog").innerHTML = tg.log.length ? tg.log.map((l) => `<div class="tg-row"><span class="muted">${new Date(l.t * 1000).toLocaleTimeString("en-US", { hour12: false })}</span>
      <span class="rchip ${l.ok ? "win" : "loss"}">${esc(l.event)}</span>${l.pic ? "📷" : ""} ${esc(l.text)}${l.error ? ` <span class="c-bear">${esc(l.error)}</span>` : ""}</div>`).join("")
      : '<div class="muted small">Wala pang naipadala.</div>';
    if (tgDirty) return;
    const EV = { signals: "Signals (bagong arrow)", ready: "READY (decision engine)", blocked: "BLOCKED (may blocker)", trades: "Trades open/close + picture", risk: "Daily DD/TP lock", news: "High-impact news", health: "Health / disconnect", drift: "Drift monitor", eod: "Daily report" };
    const tfs = ["M1", "M5", "M15", "M30", "H1", "H4"];
    $("#tgForm").innerHTML = `
      <label class="ck wide2"><input type="checkbox" data-t="enabled" ${st.enabled ? "checked" : ""}> <b>Telegram alerts ON</b></label>
      <label class="pf wide2"><span>Bot token (mula sa @BotFather)</span><input type="password" data-t="token" placeholder="${st.token ? "naka-save: " + esc(st.token) : "123456789:AA…"}" autocomplete="off"></label>
      <label class="pf"><span>Chat id</span><input data-t="chat_id" value="${esc(st.chat_id)}" placeholder="kusang mapupuno"></label>
      <div class="pf"><span>&nbsp;</span><div class="acct-actions"><button class="btn-sm ghost" id="tgFind">Find my chat id</button><button class="btn-sm ghost" id="tgTest">Send test</button></div></div>
      <div class="pf wide2"><span>Ipapadala</span><div class="ckgrid">${Object.entries(EV).map(([k, l]) => `<label class="ck"><input type="checkbox" data-ev="${k}" ${st.events[k] ? "checked" : ""}> ${l}</label>`).join("")}
        <label class="ck"><input type="checkbox" data-t="pictures" ${st.pictures ? "checked" : ""}> May chart picture</label></div></div>
      <label class="pf"><span>Signal TF</span><select data-t="signal_tf">${tfs.map((t) => `<option ${t === st.signal_tf ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="pf"><span>Signal min score % (0 = lahat)</span><input type="number" data-t="signal_min_score" value="${st.signal_min_score}" step="1"></label>
      <div class="pf wide2"><span>Signal sources</span><div>${["analyser", "breakout", "trendline", "reversal"].map((x) => `<label class="ck"><input type="checkbox" data-src="${x}" ${st.signal_sources.includes(x) ? "checked" : ""}> ${{ analyser: "Analyser Signals", breakout: "Conso Breakout", trendline: "Trend Line Breakout", reversal: "Reversal (top/bottom)" }[x]}</label>`).join("")}</div></div>
      <label class="pf"><span>Daily report oras (-1 = off)</span><input type="number" data-t="eod_hour" value="${st.eod_hour}" min="-1" max="23"></label>
      <label class="pf"><span>Time zone (UTC+)</span><input type="number" data-t="tz_offset" value="${st.tz_offset}" min="-12" max="14"></label>
      <label class="pf wide2"><span>Walang tunog sa mga oras na ito (hal. 0-6)</span><input data-t="silent" value="${esc(rangeTxt(st.silent_hours))}" placeholder="0-6"></label>
      <div class="acct-actions wide2"><button class="btn-sm" id="tgSave">Save</button><span class="muted" id="tgMsg"></span></div>
      <details class="wide2 small"><summary>Paano i-setup (2 minuto)</summary><ol>
        <li>Sa Telegram, hanapin ang <b>@BotFather</b> → <code>/newbot</code> → pangalan → kopyahin ang <b>token</b>.</li>
        <li>I-paste ang token dito → <b>Save</b>.</li>
        <li>Buksan ang bot mo sa Telegram at pindutin ang <b>START</b> (o mag-type ng "hi"). Sa loob ng ~10 segundo, kusang makikita ng terminal ang chat id mo at magpapadala ng "konektado na". Tapos na: awtomatiko nang darating ang lahat ng alert.</li></ol>
        <p class="muted">Naka-save lang ang token sa PC/VPS mo (telegram_settings.json). Huwag itong ibigay kaninuman.</p></details>`;
  }
  const rangeTxt = (hs) => { if (!hs || !hs.length) return ""; const out = []; let a = hs[0], b = hs[0];
    for (const h of hs.slice(1).concat([null])) { if (h === b + 1) { b = h; continue; } out.push(a === b ? `${a}` : `${a}-${b}`); a = b = h; } return out.join(","); };
  const parseRange = (t) => { const out = []; for (const part of String(t).split(",")) { const m = part.trim().match(/^(\d+)(?:-(\d+))?$/); if (!m) continue;
    const a = +m[1], b = m[2] != null ? +m[2] : a; for (let h = a; h <= b && h < 24; h++) out.push(h); } return out; };
  function tgBody() {
    const b = { events: {}, signal_sources: [] };
    document.querySelectorAll("#tgForm [data-t]").forEach((el) => { b[el.dataset.t] = el.type === "checkbox" ? el.checked : el.value; });
    document.querySelectorAll("#tgForm [data-ev]").forEach((el) => { b.events[el.dataset.ev] = el.checked; });
    document.querySelectorAll("#tgForm [data-src]").forEach((el) => { if (el.checked) b.signal_sources.push(el.dataset.src); });
    if (!b.token) delete b.token;
    b.silent_hours = parseRange(b.silent || ""); delete b.silent;
    return b;
  }
  $("#tgForm").addEventListener("input", () => { tgDirty = true; });
  $("#tgForm").addEventListener("click", async (e) => {
    const id = e.target.id;
    if (!["tgSave", "tgFind", "tgTest"].includes(id)) return;
    const msg = $("#tgMsg");
    if (id === "tgSave" || tgDirty) { await api("/api/telegram/settings", tgBody()).catch(() => null); tgDirty = false; }
    if (id === "tgSave") msg.textContent = "saved ✓";
    if (id === "tgFind") {
      const r = await api("/api/telegram/findchat", { use_first: true }).catch((x) => ({ ok: false, error: String(x) }));
      msg.innerHTML = r.ok ? `<span class="c-bull">nakita: ${esc(r.chats[0].name)} (${esc(r.chats[0].id)}) ✓</span>` : `<span class="c-bear">${esc(r.error)}</span>`;
    }
    if (id === "tgTest") {
      const r = await api("/api/telegram/test", {}).catch((x) => ({ ok: false, error: String(x) }));
      msg.innerHTML = r.ok ? '<span class="c-bull">naipadala ✓ tingnan ang Telegram</span>' : `<span class="c-bear">${esc(r.error || "failed")}</span>`;
    }
    await pollSys(true);
  });
  function renderDrift() {
    const d = SYS.drift;
    if (!d) return;
    const b = d.baseline;
    if (!b) {
      $("#drBody").innerHTML = `<p class="muted">Walang baseline. Pumunta sa <b>Backtest</b>, patakbuhin ang setup mo (o <b>Test</b> ang isang row), tapos pindutin ang <b>Set as drift baseline</b>.</p>`;
    } else {
      const B = b.stats, L = d.live || {};
      const row = (l, bv, lv) => `<tr><td>${l}</td><td>${bv}</td><td>${lv ?? "-"}</td></tr>`;
      $("#drBody").innerHTML = `<div class="dr-status ${d.status}">${{ ok: "✓ NORMAL", warn: "⚠ BANTAYAN", bad: "✗ DRIFT: mas mahina kaysa backtest", waiting: "⏳ naghihintay ng resulta", none: "-" }[d.status] || d.status}</div>
        <div class="muted small">baseline: ${esc(b.tf)} · ${b.days} araw · ${esc(b.source)} · set ${new Date(b.set_time * 1000).toLocaleString("en-US")}</div>
        <table class="tbl dr-tbl"><tr><th></th><th>Backtest</th><th>Live</th></tr>
          ${row("Trades", B.trades, L.trades)}${row("Win rate", B.win_rate + "%", L.trades ? L.win_rate + "%" : "-")}
          ${row("Avg R", B.avg_r, L.trades ? L.avg_r : "-")}${row("Longest losing streak", B.max_streak, L.trades ? `${L.max_streak} (now ${L.cur_streak})` : "-")}
          ${row("Max drawdown R", B.max_dd_r, L.trades ? `${L.max_dd_r} (now ${L.cur_dd_r})` : "-")}${row("Trades / day", B.per_day, L.per_day)}</table>
        <div class="dr-checks">${(d.checks || []).map((c) => `<div>${LVL[c.level][0]} <b>${esc(c.name)}</b> <span class="${LVL[c.level][1]}">${esc(c.text)}</span></div>`).join("")}</div>`;
    }
    if (drDirty) return;
    const s = d.settings;
    $("#drSet").innerHTML = `
      <label class="pf"><span>Ikukumpara</span><select data-d="source"><option value="signals" ${s.source === "signals" ? "selected" : ""}>Signals sa chart (kahit walang trade)</option><option value="trades" ${s.source === "trades" ? "selected" : ""}>Journal trades (auto)</option></select></label>
      <label class="pf"><span>Min trades bago ang test</span><input type="number" data-d="min_trades" value="${s.min_trades}" min="5"></label>
      <label class="ck"><input type="checkbox" data-d="include_manual" ${s.include_manual ? "checked" : ""}> Isama ang manual trades</label>
      <label class="ck"><input type="checkbox" data-d="auto_pause" ${s.auto_pause ? "checked" : ""}> I-OFF ang AUTO TRADE kapag DRIFT</label>
      <div class="acct-actions wide2"><button class="btn-sm" id="drSave">Save</button>${b ? '<button class="btn-sm ghost" id="drClear">Clear baseline</button>' : ""}</div>`;
  }
  $("#drSet").addEventListener("input", () => { drDirty = true; });
  $("#drSet").addEventListener("click", async (e) => {
    if (e.target.id === "drSave") {
      const b = {};
      document.querySelectorAll("#drSet [data-d]").forEach((el) => { b[el.dataset.d] = el.type === "checkbox" ? el.checked : el.value; });
      await api("/api/drift/settings", b).catch(() => null);
      drDirty = false; pollSys(true);
    }
    if (e.target.id === "drClear") { await api("/api/drift/clear", {}).catch(() => null); pollSys(true); }
  });
  $("#eodPrev").addEventListener("click", async () => {
    const r = await api("/api/report/send", { preview: true }).catch(() => null);
    if (r && r.ok) { $("#eodText").dataset.preview = "1"; $("#eodText").innerHTML = `<div class="muted small">preview (hindi pa naipadala)</div><pre>${r.text.replace(/<(?!\/?b>)[^>]+>/g, "")}</pre>`; }
  });
  $("#eodSend").addEventListener("click", async () => {
    const r = await api("/api/report/send", {}).catch(() => null);
    delete $("#eodText").dataset.preview;
    await pollSys(true);
    if (r) $("#eodText").insertAdjacentHTML("afterbegin", `<div class="${r.sent ? "c-bull" : "c-warn"} small">${r.sent ? "naipadala sa Telegram ✓" : "na-save sa reports/ (hindi naipadala: i-setup ang Telegram)"}</div>`);
  });
  $("#btBase").addEventListener("click", async () => {
    const r = await api("/api/drift/baseline", {}).catch((x) => ({ ok: false, error: String(x) }));
    showModal("Drift baseline", r.ok ? `<p>✓ Naka-set. Mula ngayon, ikukumpara ng <b>Drift monitor</b> (System &amp; Alerts) ang live na resulta sa backtest na ito.</p>`
      : `<p class="c-bear">${esc(r.error || "failed")}</p>`, "", null);
  });
  document.querySelector('.nav[data-view="system"]').addEventListener("click", () => setTimeout(() => { tgDirty = drDirty = false; delete $("#eodText").dataset.preview; pollSys(true); }, 0));

  // ================================================================ intermarket
  let IM = null, imSetOpen = false, imDirty = false;
  async function pollIM() {
    try { IM = await api("/api/intermarket"); } catch (e) { return; }
    renderIM();
  }
  const REL = { "-1": "inverse", "1": "kasabay", "0": "auto" };
  function renderIM() {
    const card = $("#imCard");
    if (!IM || IM.disabled) { card.hidden = true; return; }
    card.hidden = false;
    const b = IM.bias;
    $("#imSub").textContent = `${IM.tf} · ${IM.live ? "from your MT5" : "SIMULATED (demo)"}`;
    $("#imSum").innerHTML = `<span class="im-bias ${b === 1 ? "bull" : b === -1 ? "bear" : ""}">${b === 1 ? "▲ BULLISH para sa gold" : b === -1 ? "▼ BEARISH para sa gold" : "– walang malinaw na lead"}</span>
      <span class="muted">${IM.bull} ▲ · ${IM.bear} ▼ · ${IM.counted} symbols</span>`;
    $("#imRows").innerHTML = IM.rows.map((r) => {
      if (r.implication === undefined) {
        return `<div class="im-row na"><b>${esc(r.base)}</b><span class="muted">${esc(r.symbol || "")}</span><span class="muted im-err">${esc(r.error || "")}</span></div>`;
      }
      const tr = r.trend === 1 ? '<span class="c-bull">▲</span>' : r.trend === -1 ? '<span class="c-bear">▼</span>' : '<span class="muted">–</span>';
      const imp = r.implication === 1 ? '<span class="im-imp bull">gold ▲</span>' : r.implication === -1 ? '<span class="im-imp bear">gold ▼</span>' : '<span class="im-imp">–</span>';
      const d = String(r.price).includes(".") ? Math.min(5, Math.max(2, (r.price < 10 ? 5 : r.price < 200 ? 3 : 2))) : 2;
      return `<div class="im-row" title="${esc(r.why || "")}"><b>${esc(r.base)}</b><span class="muted im-sym">${esc(r.symbol || "")}</span>
        <span class="mono">${Number(r.price).toFixed(d)}</span><span class="mono ${r.chg >= 0 ? "c-bull" : "c-bear"}">${r.chg >= 0 ? "+" : ""}${r.chg.toFixed(2)}%</span>
        ${tr}<span class="muted im-rel">${REL[String(r.relation)]}${r.corr == null ? "" : ` · ρ ${r.corr >= 0 ? "+" : ""}${r.corr.toFixed(2)}`}</span>${imp}</div>`;
    }).join("");
    if (imSetOpen && !imDirty) renderIMSet();
  }
  function renderIMSet() {
    const st = IM.settings;
    $("#imSet").innerHTML = `<div class="im-set-row head"><span>Base</span><span>Broker symbol (blank = auto)</span><span>Relation</span><span></span></div>` +
      st.items.map((it, i) => `<div class="im-set-row" data-i="${i}"><input data-f="base" value="${esc(it.base)}">
        <input data-f="symbol" value="${esc(it.symbol)}" placeholder="auto">
        <select data-f="relation">${["-1", "1", "0"].map((v) => `<option value="${v}" ${String(it.relation) === v ? "selected" : ""}>${REL[v]}</option>`).join("")}</select>
        <button class="btn-sm ghost" data-imx="${i}">✕</button></div>`).join("") +
      `<div class="acct-actions"><button class="btn-sm ghost" id="imAdd">+ symbol</button>
        <label class="pf" style="flex-direction:row;align-items:center;gap:6px"><span>TF</span><select id="imTf">${["M5", "M15", "M30", "H1", "H4"].map((t) => `<option ${t === st.tf ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <button class="btn-sm" id="imSave">Save</button><span class="muted" id="imSaved"></span></div>
      <p class="muted small">inverse = kapag tumaas ito, bearish sa gold (DXY, USDJPY, yields). kasabay = pareho ng galaw (EURUSD, silver).
        auto = sinusunod ang nasukat na correlation (ρ). I-ON ang "Intermarket" sa SCORE (LAYERS) para pumasok sa score.</p>`;
  }
  function readIMSet() {
    return [...document.querySelectorAll("#imSet .im-set-row[data-i]")].map((row) => ({
      base: row.querySelector('[data-f="base"]').value, symbol: row.querySelector('[data-f="symbol"]').value,
      relation: parseInt(row.querySelector('[data-f="relation"]').value, 10) }));
  }
  $("#imGear").addEventListener("click", () => { imSetOpen = !imSetOpen; imDirty = false; $("#imSet").hidden = !imSetOpen; if (imSetOpen && IM) renderIMSet(); });
  $("#imSet").addEventListener("input", () => { imDirty = true; });
  $("#imSet").addEventListener("click", async (e) => {
    if (e.target.dataset.imx !== undefined) { const items = readIMSet(); items.splice(+e.target.dataset.imx, 1); IM.settings.items = items; imDirty = true; renderIMSet(); return; }
    if (e.target.id === "imAdd") { const items = readIMSet(); items.push({ base: "", symbol: "", relation: 0 }); IM.settings.items = items; imDirty = true; renderIMSet(); return; }
    if (e.target.id === "imSave") {
      const r = await api("/api/intermarket", { items: readIMSet().filter((x) => x.base), tf: $("#imTf").value }).catch(() => ({ ok: false }));
      imDirty = false;
      $("#imSaved").textContent = r.ok ? "saved ✓ (reading symbols…)" : "save failed";
      setTimeout(pollIM, 1500);
    }
  });


  // ================= COMMAND CENTER (read-only view of the decision / risk / execution layers) =================
  const DEC_CLS = { WAIT: "muted", WATCH: "c-warn", READY: "c-bull", BLOCKED: "c-bear", OPEN: "c-info", MANAGING: "c-info" };
  const ccVisible = () => !$("#view-command").hidden;
  let CC = null, ccPlanKey = "", ccPlanData = null;
  const mark = (ok) => ok === true ? '<span class="c-bull">✓</span>' : ok === false ? '<span class="c-bear">✗</span>' : '<span class="muted">–</span>';
  const tfc = (v) => v === "BULL" ? "c-bull" : v === "BEAR" ? "c-bear" : "muted";
  async function pollDecision(force) {
    const vis = ccVisible();
    if (!vis && !force && CC && Date.now() - (CC._at || 0) < 5000) return;     // background: every 5 s (nav dot)
    let d;
    try { d = await api("/api/decision"); } catch (e) { return; }
    if (!d || d.disabled) { $("#decDot").className = "navdot"; return; }
    d._at = Date.now(); CC = d;
    const onlyArm = d.state === "BLOCKED" && (d.hard_blockers || []).every((b) => ["ARM_OFF", "HALTED", "READ_ONLY", "MASTER_DISABLED"].includes(b.code));
    $("#decDot").className = "navdot " + (onlyArm ? "" : ({ READY: "ok", WATCH: "warn", BLOCKED: "bad", OPEN: "ok", MANAGING: "ok" }[d.state] || ""));
    $("#decDot").title = `${d.state} ${d.side || ""}`;
    if (vis) renderCC();
  }
  function renderCC() {
    const d = CC; if (!d) return;
    const risk = d.risk_ok ? '<span class="c-bull">OK</span>' : '<span class="c-bear">BLOCKED</span>';
    const sp = (d.matrix.rows.find((r) => r.key === "spread") || {}).value || "-";
    $("#ccTop").innerHTML = `<div class="cc-dec ${DEC_CLS[d.state] || ""}"><b>${esc(d.state)}</b><span>${esc(d.side || "-")}</span></div>
      <div class="cc-cells">
        <div><span>H4</span><b class="${tfc(d.bias.H4)}">${esc(d.bias.H4 || "-")}</b></div>
        <div><span>H1</span><b class="${tfc(d.bias.H1)}">${esc(d.bias.H1 || "-")}</b></div>
        <div><span>M15</span><b class="${tfc(d.structure.M15)}">${esc(d.structure.M15 || "-")}</b></div>
        <div><span>M5</span><b class="${tfc(d.structure.M5)}">${esc(d.structure.M5 || "-")}</b></div>
        <div><span>Score</span><b class="${d.score >= d.passing_score ? "c-bull" : "c-warn"}">${d.score ?? "-"}%</b><small>pass ${d.passing_score}%</small></div>
        <div><span>Trigger</span><b>${esc(d.trigger || "none")}</b></div>
        <div><span>Regime</span><b>${esc(d.regime || "-")}</b></div>
        <div><span>Session</span><b>${esc(d.session || "-")}</b></div>
        <div><span>News</span><b class="${d.news === "CLEAR" ? "" : "c-warn"}">${esc(d.news || "-")}</b></div>
        <div><span>Spread</span><b>${esc(sp)}</b></div>
        <div><span>Risk</span><b>${risk}</b></div>
      </div>
      <div class="cc-states">${ccStates(d)}</div>
      <div class="cc-verdict muted">Existing engine verdict: ${esc(d.verdict || "-")} · final decision sa itaas (kasama ang blockers)</div>`;
    const kindLbl = { required: "REQUIRED", confirmation: "confirmation", info: "info" };
    $("#ccMatrix").innerHTML = `<table class="tbl"><tr><th></th><th>Item</th><th>Value</th><th>Type</th></tr>` +
      d.matrix.rows.map((r) => `<tr><td>${mark(r.ok)}</td><td>${esc(r.label)}</td><td>${esc(r.value)}</td><td class="muted small">${kindLbl[r.kind] || esc(r.kind)}</td></tr>`).join("") + "</table>";
    const hard = d.hard_blockers || [], soft = (d.blockers || []).filter((b) => b.level !== "hard");
    const why = d.state === "READY" ? `<p class="c-bull"><b>READY ${esc(d.side)}</b> · pasado ang lahat ng required, walang hard blocker.</p>`
      : d.state === "BLOCKED" ? `<p class="c-bear"><b>BLOCKED</b> · ${hard.map((b) => esc(b.code)).join(", ")}</p>`
      : `<p class="c-warn"><b>${esc(d.state)}</b> · ${esc(d.verdict || "")}</p>`;
    const reqFail = d.matrix.rows.filter((r) => r.kind === "required" && r.ok === false);
    $("#ccWhy").innerHTML = why +
      (reqFail.length ? `<p class="small">Kulang na required: ${reqFail.map((r) => `<b>${esc(r.label)}</b> (${esc(r.value)})`).join(", ")}</p>` : "") +
      (hard.length ? `<ul class="cc-bl">${hard.map((b) => `<li><span class="rchip loss">${esc(b.code)}</span> ${esc(b.text)}</li>`).join("")}</ul>` : "") +
      (soft.length ? `<ul class="cc-bl">${soft.map((b) => `<li><span class="rchip">${esc(b.code)}</span> ${esc(b.text)} <span class="muted">(babala lang)</span></li>`).join("")}</ul>` : "") +
      `<p class="muted small">Score, confirmations at blockers ay magkahiwalay: ang confirmations ay impormasyon lang, hindi dinadagdag sa score.</p>`;
    $("#ccTimeline").innerHTML = (d.timeline || []).slice(0, 40).map((e) =>
      `<div class="tl-row ${esc(e.kind)}"><span class="muted">${new Date(e.t * 1000).toLocaleTimeString("en-GB")}</span> ${esc(e.text)}</div>`).join("") || '<span class="muted">wala pa</span>';
    ccPlanRefresh();
    renderQuality(d);
  }
  let RS = null, SMC = null, GATE = null, PMS = null, DSET = null;
  // GREEN = allowed · YELLOW = waiting / warning · RED = blocked · GRAY = unavailable
  const chip = (label, val, cls, title = "") => `<span class="stchip ${cls}" title="${esc(title)}"><i>${esc(label)}</i>${val}</span>`;
  function ccStates(d) {
    const q = d.quality || {}, qq = q.quality || {}, sm = d.smc || {};
    const conf = d.confidence;
    const minc = q.min_confidence || 0;
    const su = sm.ok && sm.setup ? sm.setup : null;
    const pd = (d.matrix.rows.find((r) => r.key === "pd") || {});
    const news = d.news;
    const at = AT && AT.enabled;
    const armed = T && T.armed;
    const master = RS ? RS.master_enabled : null;
    const data = d.data;
    return [
      chip("Confidence", conf == null ? "-" : conf.toFixed(2), conf == null ? "gray" : conf >= Math.max(0.6, minc) ? "green" : conf >= minc ? "yellow" : "red",
        minc ? `min ${minc}` : "contribution map"),
      chip("SMC", su ? `${su.direction} ${su.quality} ${su.confidence.toFixed(2)}` : (sm.ok ? "none" : "n/a"),
        su ? (su.direction === d.side ? "green" : "yellow") : sm.ok ? "yellow" : "gray", su ? `${su.status} · stage ${su.stage}/4` : (sm.reason || "")),
      chip("P/D", pd.raw ? esc(pd.value) : "n/a", pd.ok === true ? "green" : pd.ok === false ? "yellow" : "gray", pd.range ? `range ${pd.range.low}–${pd.range.high}` : ""),
      chip("News", esc(news || "-"), news === "CLEAR" ? "green" : news === "UNKNOWN" ? "gray" : "red"),
      chip("Data", data ? (data.ok ? "OK" : "INVALID") : "-", !data ? "gray" : data.ok ? "green" : "red",
        data && data.issues ? data.issues.map((i) => i.text).join("; ") : ""),
      chip("Conflicts", (d.conflicts || []).length ? (d.conflicts || []).map((c) => c.category).join(", ") : "none",
        (d.conflicts || []).length ? (q.conflict_block ? "red" : "yellow") : "green"),
      chip("AutoTrade", at ? "ON" : "OFF", at ? "green" : "gray"),
      chip("ARM", armed ? "ARMED" : "OFF", armed ? "green" : "gray"),
      chip("Master", master == null ? "-" : master ? "ENABLED" : "DISABLED", master == null ? "gray" : master ? "green" : "red",
        RS && RS.master_error ? RS.master_error : "MASTER TRADING ENABLE"),
    ].join("");
  }
  const WD = { BUY: "c-bull", SELL: "c-bear", NEUTRAL: "muted" };
  function renderQuality(d) {
    const q = d.quality;
    if (!q) { $("#sqBody").innerHTML = '<span class="muted">n/a</span>'; return; }
    const cats = Object.entries(q.categories).sort((a, b) => b[1].weight - a[1].weight);
    $("#sqSub").innerHTML = `confidence <b>${q.confidence.toFixed(2)}</b>${q.min_confidence ? ` / min ${q.min_confidence.toFixed(2)}` : ""} · structure ${esc(q.structure_alignment)}`;
    $("#sqBody").innerHTML = `<table class="tbl sq"><tr><th>Category</th><th>Says</th><th>Support (this side)</th><th>Weight</th><th>Adds</th><th>Items</th></tr>` +
      cats.map(([k, v]) => `<tr class="${v.available ? "" : "gray"}"><td><b>${k}</b></td>
        <td class="${WD[v.direction] || ""}">${v.available ? v.direction : "UNAVAILABLE"}</td>
        <td><div class="sbar"><div style="width:${Math.round(v.support * 100)}%"></div></div><span class="small">${v.available ? v.support.toFixed(2) : "-"}</span></td>
        <td>${v.weight}</td><td>${q.contributions[k] != null ? q.contributions[k].toFixed(2) : "-"}</td><td class="muted small" title="${esc(v.source)}">${v.items}</td></tr>`).join("") + "</table>" +
      ((q.conflicts || []).length ? `<div class="small">${q.conflicts.map((c) => `<span class="rchip ${q.conflict_block && q.conflict_block.code === c.code ? "loss" : "warnc"}">${esc(c.code)}</span> ${esc(c.text)}`).join("<br>")}</div>` : "") +
      (q.regime && q.regime.cls ? `<p class="small">Regime <b>${esc(q.regime.cls)}</b>${q.regime.notes.length ? ": " + q.regime.notes.map(esc).join("; ") : " (walang dagdag na requirement)"}</p>` : "") +
      `<p class="small">Required: ${q.required.map((k) => `<span class="rchip ${q.missing_required.includes(k) ? "loss" : "win"}">${esc(k)}</span>`).join(" ")}</p>` +
      (d.explanation ? `<div class="expl"><b>${esc(d.explanation.head)}</b><div class="small">${d.explanation.reasons.map(esc).join(" · ")}</div>
        ${d.explanation.but.length ? `<div class="small c-bear">BUT: ${d.explanation.but.map(esc).join(" · ")}</div>` : ""}</div>` : "");
  }
  function ccInputs() {
    if (!$("#ccLot").value) $("#ccLot").value = $("#tLot").value || 0.01;
    if (!$("#ccSL").value) $("#ccSL").value = $("#tSL").value || 5;
    if (!$("#ccTP").value) $("#ccTP").value = $("#tTP").value || 10;
    let side = +$("#ccSide").value;
    if (!side && CC) side = CC.side_i || 0;
    return { side, lot: +$("#ccLot").value || 0, sl: +$("#ccSL").value || 0, tp: +$("#ccTP").value || 0 };
  }
  async function ccPlanRefresh(force) {
    const v = ccInputs();
    if (!v.side) { $("#ccPlan").innerHTML = '<p class="muted">Walang direksyon ang decision ngayon. Pumili ng BUY o SELL.</p>'; $("#ccConfirm").disabled = true; return; }
    const key = JSON.stringify(v);
    if (!force && key === ccPlanKey && ccPlanData && Date.now() - ccPlanData._at < 2000) return;
    ccPlanKey = key;
    let p;
    try { p = await api(`/api/tradeplan?side=${v.side}&lot=${v.lot}&sl=${v.sl}&tp=${v.tp}`); } catch (e) { return; }
    if (!p || p.disabled) return;
    p._at = Date.now(); ccPlanData = p;
    const word = v.side === 1 ? "BUY" : "SELL";
    $("#ccPlanSub").innerHTML = `<span class="${v.side === 1 ? "c-bull" : "c-bear"}">${word}</span> · ${esc(p.state)}`;
    const hb = p.hard_blockers || [];
    $("#ccPlan").innerHTML = `<table class="tbl cc-pt">
        <tr><td>Entry (~)</td><td class="mono">${p.entry ? fx(p.entry) : "-"}</td><td>RR</td><td>${p.rr ? "1:" + p.rr : "-"}</td></tr>
        <tr><td>SL</td><td class="mono c-bear">${p.sl ? fx(p.sl) : "wala"}</td><td>Risk (master)</td><td>${p.risk_usd != null ? "$" + p.risk_usd : "-"}</td></tr>
        <tr><td>TP</td><td class="mono c-bull">${p.tp ? fx(p.tp) : "wala"}</td><td>Target</td><td>${p.target_usd != null ? "$" + p.target_usd : "-"}</td></tr>
        <tr><td>Score</td><td>${p.score}% / ${p.passing}%</td><td>Regime · session</td><td>${esc(p.regime)} · ${esc(p.session)}</td></tr></table>
      <div class="small">Accounts: ${(p.execution || []).map((a) => `<span class="rchip ${a.ok ? "win" : "loss"}" title="${esc(a.why || "")}">${esc(a.name)} ${a.ok ? a.lot : "skip"}</span>`).join(" ")}</div>
      ${!hb.length && ["WAIT", "WATCH"].includes(p.state) ? `<p class="c-warn small">⚠ Decision: ${esc(p.state)} (hindi READY). Puwede pa ring mag-manual entry, pero hindi pasado ang lahat ng required.</p>` : ""}
      ${hb.length ? `<p class="c-bear small">Hard blockers: ${hb.map((b) => esc(b.code)).join(", ")}</p>` : ""}
      <p class="muted small">Ang CONFIRM ay dadaan pa rin sa server check + 15 s na one-time confirmation (parehong daan ng BUY/SELL sa sidebar).</p>`;
    $("#ccConfirm").textContent = `CONFIRM ${word}`;
    $("#ccConfirm").className = "tbtn " + (v.side === 1 ? "buy" : "sell");
    $("#ccConfirm").disabled = !(T && T.can_trade && !T.halted) || hb.length > 0;
  }
  for (const id of ["#ccSide", "#ccLot", "#ccSL", "#ccTP"]) $(id).addEventListener("input", () => ccPlanRefresh(true));
  $("#ccConfirm").addEventListener("click", () => {
    const v = ccInputs();
    if (!v.side) return;
    $("#tLot").value = v.lot; $("#tSL").value = v.sl; $("#tTP").value = v.tp;
    for (const id of ["#tLot", "#tSL", "#tTP"]) lsSet("rg" + id, $(id).value);
    entry(v.side);                                   // the same prepare -> token -> confirm flow
  });

  async function pollCCPanels() {
    if (!ccVisible()) return;
    const [ex, rk, rc, rs, sm, gt, pm] = await Promise.all([api("/api/execution").catch(() => null), api("/api/risk/accounts").catch(() => null),
      api("/api/reconciliation").catch(() => null), api("/api/risk").catch(() => null), api("/api/smc").catch(() => null),
      api("/api/gate").catch(() => null), api("/api/positions/manager").catch(() => null)]);
    if (rs && !rs.disabled) { RS = rs; renderRisk(); }
    if (sm) { SMC = sm; renderSMC(); }
    if (gt && !gt.disabled) { GATE = gt; renderGate(); }
    if (pm && !pm.disabled) { PMS = pm; renderPM(); }
    if (!DSET) loadRules();
    if (ex && ex.accounts) {
      $("#ccExec").innerHTML = "<tr><th>Account</th><th>Status</th><th>Broker</th><th>Symbol</th><th>Lot min/step/max</th><th>Stops / freeze</th><th>Filling</th><th>Spread</th><th>Latency</th><th>Data age</th><th>Reasons</th></tr>" +
        ex.accounts.map((a) => `<tr><td><b>${esc(a.name)}</b> <span class="muted small">${esc(a.role)}</span></td>
          <td><span class="rchip ${a.status === "READY" ? "win" : a.status === "DISABLED" ? "" : "loss"}">${esc(a.status)}</span></td>
          <td class="small">${esc(a.broker || "-")}<br><span class="muted">${esc(a.server || "")}</span></td><td>${esc(a.symbol || "-")}</td>
          <td class="mono">${a.min_lot ?? "-"} / ${a.lot_step ?? "-"} / ${a.max_lot ?? "-"}</td><td class="mono">${a.stops_level ?? "-"} / ${a.freeze_level ?? "-"}</td>
          <td class="small">${esc(a.filling_mode || "-")}</td><td class="mono">${a.spread != null ? "$" + a.spread : "-"}</td>
          <td class="mono">${a.latency_ms != null ? Math.round(a.latency_ms) + " ms" : "-"}</td><td class="mono">${a.data_age_s != null ? a.data_age_s + "s" : "-"}</td>
          <td class="small">${(a.reasons || []).map((r) => `<span class="rchip loss">${esc(r.code || r)}</span>`).join(" ") || '<span class="muted">-</span>'}</td></tr>`).join("");
    }
    if (rk && rk.accounts) {
      $("#ccRisk").innerHTML = "<tr><th>Account</th><th>Status</th><th>Daily P/L</th><th>DD limit</th><th>TP target</th><th>Open</th><th>Open risk</th><th>Trades today</th></tr>" +
        rk.accounts.map((a) => `<tr><td><b>${esc(a.name)}</b></td>
          <td><span class="rchip ${a.locked ? "loss" : a.status === "ACTIVE" ? "win" : ""}" title="${esc(a.lock_reason || "")}">${esc(a.locked ? "LOCKED" : a.status)}</span></td>
          <td class="${(a.daily_pl || 0) >= 0 ? "c-bull" : "c-bear"}">${money(a.daily_pl || 0)}</td>
          <td>${a.dd_limit != null ? money(-Math.abs(a.dd_limit)) : '<span class="muted">-</span>'}</td><td>${a.tp_limit != null ? money(a.tp_limit) : '<span class="muted">-</span>'}</td>
          <td>${a.open_trades}</td><td>${a.open_risk ? money(a.open_risk) : "-"}</td><td>${a.trades_today}</td></tr>`).join("");
    }
    if (rc && !rc.disabled) {
      $("#ccRecSub").innerHTML = `<span class="rchip ${rc.status === "SYNC" ? "win" : "loss"}">${esc(rc.status)}</span>`;
      if (document.activeElement !== $("#ccAutoRepair")) $("#ccAutoRepair").checked = !!rc.auto_repair;
      $("#ccRec").innerHTML =
        (rc.paused || []).map((p) => `<div class="cc-paused"><span class="rchip loss">PAUSED</span> <b>${esc(p.name)}</b> · ${esc(p.why)}
          <button class="btn-sm" data-resume="${esc(p.id)}">Resume</button></div>`).join("") +
        (rc.rows.length ? `<table class="tbl"><tr><th>Trade</th><th>Account</th><th>Status</th><th>Details</th></tr>` +
          rc.rows.map((r) => `<tr><td class="mono">${esc(r.gid)}</td><td>${esc(r.account)}</td><td><span class="rchip ${r.status === "SYNC" || r.status === "CLOSED" ? "win" : r.status === "STALE" ? "" : "loss"}">${esc(r.status)}</span></td>
            <td class="small">${r.details.map(esc).join(" · ")}</td></tr>`).join("") + "</table>"
          : '<p class="muted">Walang bukas na copied trade. Master at followers: SYNC.</p>') +
        ((rc.log || []).length ? `<div class="cc-tl small">${rc.log.slice(0, 10).map((l) => `<div class="tl-row ${esc(l.kind)}"><span class="muted">${new Date(l.t * 1000).toLocaleTimeString("en-GB")}</span> ${esc(l.text)}</div>`).join("")}</div>` : "");
    }
  }
  $("#ccRec").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-resume]");
    if (!b) return;
    await api("/api/reconciliation/resume", { id: b.dataset.resume });
    pollCCPanels();
  });
  $("#ccAutoRepair").addEventListener("change", async () => {
    await api("/api/reconciliation/settings", { auto_repair: $("#ccAutoRepair").checked });
    pollCCPanels();
  });
  document.querySelector('.nav[data-view="command"]').addEventListener("click", () => setTimeout(() => { pollDecision(true); pollCCPanels(); ccPlanRefresh(true); }, 0));

  // ---- journal analytics (descriptive only)
  async function pollAnalytics() {
    if ($("#view-journal").hidden) return;
    let a;
    try { a = await api("/api/analytics"); } catch (e) { return; }
    if (!a || a.disabled) return;
    const sr = (v) => v == null ? "-" : `<span class="${v >= 0 ? "c-bull" : "c-bear"}">${v >= 0 ? "+" : ""}${v}</span>`;
    const tbl = (rows, head) => rows.length ? `<table class="tbl"><tr><th>${head}</th><th>Trades</th><th>Win %</th><th>Avg R</th><th>Total R</th><th>Max DD R</th><th>Avg hold</th><th>MFE / MAE</th></tr>` +
      rows.map((r) => `<tr><td><b>${esc(r.key)}</b></td><td>${r.trades}</td><td>${r.win_rate ?? "-"}</td><td>${sr(r.avg_r)}</td><td>${sr(r.total_r)}</td><td>${r.max_dd_r}</td>
        <td>${r.avg_hold_min != null ? r.avg_hold_min + "m" : "-"}</td><td>${r.avg_mfe_r ?? "-"} / ${r.avg_mae_r ?? "-"}</td></tr>`).join("") + "</table>"
      : '<p class="muted">Wala pang closed trades.</p>';
    $("#aExit").innerHTML = tbl(a.exits, "Exit");
    $("#aSess").innerHTML = tbl(a.sessions, "Session") + (a.regimes.length ? "<h4 class='small muted'>By regime at entry</h4>" + tbl(a.regimes, "Regime") : "");
    $("#aSetup").innerHTML = tbl(a.setups.slice(0, 15), "Setup") + '<p class="muted small">Descriptive lang: pinakamaraming trade sa itaas, hindi "best setup" ranking.</p>';
    const m = a.mfe_mae.summary;
    $("#aMfe").innerHTML = m.trades ? `<div class="anl-sum">Avg MFE <b>${m.avg_mfe_r ?? "-"}R</b> · Avg MAE <b>${m.avg_mae_r ?? "-"}R</b> ·
        Naiwan sa mesa (avg) <b>${m.avg_left_on_table_r ?? "-"}R</b> · Pinakamalalim na MAE ng winners <b>${m.worst_mae_of_winners_r ?? "-"}R</b></div>
      <table class="tbl"><tr><th>Trade</th><th>Side</th><th>R</th><th>MFE R</th><th>MAE R</th><th>Left</th><th>Exit</th></tr>` +
      [...a.mfe_mae.trades].reverse().slice(0, 30).map((t) => `<tr><td class="mono">${esc(t.gid)}</td><td>${esc(t.side)}</td><td>${sr(t.r)}</td>
        <td>${t.mfe_r ?? "-"}</td><td>${t.mae_r ?? "-"}</td><td>${t.left_on_table_r ?? "-"}</td><td>${esc(t.exit_reason)}</td></tr>`).join("") + "</table>"
      : '<p class="muted">Wala pang trade na may MFE/MAE (bago ito: lalabas sa mga susunod na trade).</p>';
  }
  document.querySelector('.nav[data-view="journal"]').addEventListener("click", () => setTimeout(pollAnalytics, 0));


  // ---- SMC card
  const DIRW = { 1: "BUY", "-1": "SELL", 0: "-" };
  function renderSMC() {
    const sm = SMC && SMC.smc;
    if (!sm || !sm.ok) { $("#smcBody").innerHTML = `<span class="muted">${esc((sm && sm.reason) || "n/a")}</span>`; return; }
    const st = sm.structure, rng = sm.dealing_range, le = st.last_event;
    const stage = (su) => {
      const c = su.components || {};
      const steps = [["Sweep", c.sweep], ["BOS/CHoCH", c.break_], ["FVG/OB", c.fvg || c.ob], ["Retest", c.retest]];
      return steps.map(([n, ok], i) => `<span class="stp ${ok ? "on" : ""}">${i + 1}. ${n}</span>`).join('<span class="muted">→</span>');
    };
    const setupRow = (k) => {
      const su = sm.setups[k] || {};
      const cls = su.status === "RETESTED" || su.status === "WAITING_RETEST" ? (k === "BUY" ? "c-bull" : "c-bear") : su.status === "INVALIDATED" ? "muted" : "";
      return `<div class="smc-setup"><b class="${cls}">${k}</b> <span class="small">${esc((su.status || "NONE").replace(/_/g, " "))}${su.quality && su.quality !== "NONE" ? ` · ${su.quality} ${su.confidence.toFixed(2)}` : ""}</span>
        <div class="smc-steps">${stage(su)}</div>
        ${su.entry_zone ? `<div class="small">Entry zone <b>${fx(su.entry_zone[0])} – ${fx(su.entry_zone[1])}</b> · invalidation <b>${fx(su.invalidation)}</b></div>` : ""}</div>`;
    };
    $("#smcSub").innerHTML = `${esc(sm.tf)} · structure <b class="${st.direction === 1 ? "c-bull" : st.direction === -1 ? "c-bear" : ""}">${DIRW[st.direction]}</b> (${st.strength.toFixed(2)})`;
    $("#smcBody").innerHTML = setupRow("BUY") + setupRow("SELL") +
      `<div class="small">Last: ${le ? `<b>${esc(le.event_type)}</b> ${le.direction === 1 ? "UP" : "DN"} @ ${fx(le.price)} (strength ${le.strength})` : "-"} ·
        swings ${st.swings.slice(-6).map((x) => esc(x.label)).join(" ")}</div>
      <div class="small">Dealing range <select id="smcRange">${["htf_swing", "prev_day", "session", "custom"].map((x) => `<option ${x === rng.source ? "selected" : ""}>${x}</option>`).join("")}</select>
        ${rng.high != null ? `${fx(rng.low)} – ${fx(rng.high)} · EQ ${fx(rng.equilibrium)} · <b>${esc(rng.zone)}</b> (${rng.position != null ? Math.round(rng.position * 100) + "%" : "-"})` : '<span class="muted">not available</span>'}</div>
      <table class="tbl small"><tr><th>FVG</th><th>Zone</th><th>Status</th><th>Fill</th></tr>${sm.fvgs.slice(-5).reverse().map((z) =>
        `<tr><td class="${z.direction === 1 ? "c-bull" : "c-bear"}">${DIRW[z.direction]}</td><td>${fx(z.lower_price)}–${fx(z.upper_price)}</td><td>${esc(z.status)}</td><td>${z.fill_percentage}%</td></tr>`).join("")}
        <tr><th>OB</th><th>Zone</th><th>Status</th><th>From</th></tr>${sm.order_blocks.slice(-4).reverse().map((o) =>
        `<tr><td class="${o.direction === 1 ? "c-bull" : "c-bear"}">${DIRW[o.direction]}</td><td>${fx(o.zone_low)}–${fx(o.zone_high)}</td><td>${esc(o.mitigation_status)}</td><td>${esc(o.source_event_type)}</td></tr>`).join("")}</table>
      <div class="small">Sweeps: ${sm.liquidity.sweeps.slice(-4).reverse().map((x) => `<span class="rchip ${x.confirmed ? "win" : ""}">${esc(x.type)} ${x.direction === 1 ? "↑" : "↓"} ${fx(x.level)}</span>`).join(" ") || '<span class="muted">none</span>'}</div>`;
    $("#smcRange").addEventListener("change", async (e) => { await api("/api/smc/settings", { range_source: e.target.value }); pollCCPanels(); });
  }

  // ---- master gate card
  function renderGate() {
    const g = GATE, st = g.stats || {};
    $("#gateSub").innerHTML = `${st.generated || 0} signals · <span class="c-bear">${st.blocked || 0} blocked</span> · <span class="c-bull">${st.executed || 0} traded</span>`;
    const last = g.last;
    const strip = last ? `<div class="gstrip">${last.gates.map((x) => `<span class="gcell ${x.ok ? "ok" : "bad"}" title="${esc(x.reasons.join("; ") || "OK")}">${esc(x.gate)}</span>`).join("")}</div>
      <p class="small">${last.allowed ? '<b class="c-bull">ALLOWED</b>' : `<b class="c-bear">${esc(last.block_code)}</b> ${esc(last.block_reason || "")}`}</p>` : '<p class="muted small">Wala pang auto signal na dumaan (kapag naka-ON ang AUTO TRADE).</p>';
    $("#gateBody").innerHTML = strip + `<div class="table-wrap"><table class="tbl small"><tr><th>Time</th><th>Signal</th><th>Source</th><th>Decision</th><th>Reason</th></tr>` +
      (g.recent || []).slice(0, 15).map((e) => `<tr><td class="muted">${new Date(e.t * 1000).toLocaleTimeString("en-GB")}</td>
        <td class="${e.direction === "BUY" ? "c-bull" : "c-bear"}">${esc(e.direction)} <span class="muted">${esc((e.signal_id || "").slice(0, 7))}</span></td><td>${esc(e.source)}</td>
        <td>${e.trade_id ? `<span class="rchip win">TRADE ${esc(e.trade_id)}</span>` : e.decision === "ALLOWED" ? '<span class="rchip win">ALLOWED</span>' : `<span class="rchip loss">${esc(e.block_code || "BLOCKED")}</span>`}</td>
        <td class="small">${esc(e.block_reason || "")}</td></tr>`).join("") + "</table></div>";
  }

  // ---- risk engine card
  const RISK_F = [["max_risk_per_trade_pct", "Max risk / trade %"], ["max_risk_per_trade_usd", "Max risk / trade $"],
    ["max_total_dd_pct", "Max total DD % (from peak)"], ["max_exposure_lots", "Max exposure (lots)"], ["max_trades_per_day", "Max trades / day (all)"],
    ["max_open_positions", "Max open positions (all)"], ["min_rr", "Min RR"]];
  let rsDirty = false;
  function renderRisk() {
    const r = RS;
    const b = $("#btnMaster");
    if (b) { b.textContent = `MASTER: ${r.master_enabled ? "ENABLED" : "DISABLED"}`; b.classList.toggle("off", !r.master_enabled); }
    $("#rsSub").innerHTML = `${r.master_enabled ? '<span class="c-bull">MASTER ENABLED</span>' : `<span class="c-bear">MASTER DISABLED</span>${r.master_error ? " · " + esc(r.master_error) : ""}`}
      ${r.config_error ? ` · <span class="c-bear">CONFIG INVALID: ${esc(r.config_error)}</span>` : ""} · news ${esc(r.news ? r.news.state : "-")}
      ${r.total_dd_pct != null ? ` · DD from peak ${r.total_dd_pct}%` : ""}${r.contract_size_from_broker ? "" : ' · <span class="c-warn">contract size: fallback 100</span>'}`;
    if (rsDirty) return;
    const s = r.settings;
    $("#rsForm").innerHTML = RISK_F.map(([k, l]) => `<label class="pf"><span>${l} <span class="muted">(0 = off)</span></span><input type="number" step="0.1" min="0" data-rk="${k}" value="${s[k]}"></label>`).join("") +
      `<label class="pf"><span>News unknown (AUTO)</span><select data-rk="news_unknown">${["block", "allow", "reduce_risk"].map((x) => `<option ${x === s.news_unknown ? "selected" : ""}>${x}</option>`).join("")}</select></label>
       <label class="ck"><input type="checkbox" data-rk="require_tp" ${s.require_tp ? "checked" : ""}> TP required</label>
       <label class="ck"><input type="checkbox" data-rk="news_block_manual" ${s.news_block_manual ? "checked" : ""}> news window blocks MANUAL too</label>`;
  }
  $("#rsForm").addEventListener("input", () => { rsDirty = true; });
  $("#rsSave").addEventListener("click", async () => {
    const body = {};
    document.querySelectorAll("#rsForm [data-rk]").forEach((el) => {
      const k = el.dataset.rk;
      body[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : (["max_trades_per_day", "max_open_positions"].includes(k) ? parseInt(el.value || "0", 10) : parseFloat(el.value || "0"));
    });
    const r = await api("/api/risk/settings", body);
    $("#rsMsg").textContent = r.ok ? "saved ✓" : "NOT saved: " + (r.error || "");
    rsDirty = false; pollCCPanels();
  });
  $("#rsPeak").addEventListener("click", async () => { await api("/api/risk/reset_peak", {}); $("#rsMsg").textContent = "peak reset ✓"; pollCCPanels(); });
  $("#btnMaster").addEventListener("click", () => {
    const on = !(RS && RS.master_enabled);
    showModal(on ? "Enable MASTER TRADING?" : "Disable MASTER TRADING?",
      on ? "<p>Papayagan ulit ang trading (kailangan pa rin ng ARM, at ng AUTO ON para sa auto trade).</p>"
         : "<p><b>Walang kahit anong bagong trade</b> (manual, auto, news box) hanggang i-enable mo ulit. Naka-save ito: OFF pa rin pag-restart. Hindi nito isinasara ang bukas na trades.</p>",
      on ? "Enable" : "Disable", async () => { await api("/api/master", { enabled: on, reason: "UI" }); hideModal(); pollCCPanels(); }, !on);
  });

  // ---- position manager card
  let pmDirty = false;
  function renderPM() {
    const p = PMS, s = p.settings;
    $("#pmSub").innerHTML = `${s.enabled ? '<span class="c-bull">ON</span>' : '<span class="muted">OFF</span>'} · priority ${p.priority.join(" → ")}`;
    if (!pmDirty) {
      const num = (k, l, st = "0.1") => `<label class="pf"><span>${l}</span><input type="number" step="${st}" min="0" data-pk="${k}" value="${s[k]}"></label>`;
      $("#pmForm").innerHTML = `<label class="ck"><input type="checkbox" data-pk="enabled" ${s.enabled ? "checked" : ""}> Enabled</label>
        <label class="ck"><input type="checkbox" data-pk="require_armed" ${s.require_armed ? "checked" : ""}> only while ARMED</label>
        ${num("be_r", "Break-even at R (0 = off)", "0.25")}${num("be_usd", "Break-even at $ (0 = off)", "1")}${num("be_offset", "BE offset $")}
        <label class="pf"><span>Profit lock (at R:lock R, …)</span><input data-pk="profit_lock" value="${s.profit_lock.map((x) => x.join(":")).join(", ")}" placeholder="2:1, 3:2"></label>
        <label class="pf"><span>Trailing</span><select data-pk="trail_mode">${p.trail_modes.map((x) => `<option ${x === s.trail_mode ? "selected" : ""}>${x}</option>`).join("")}</select></label>
        ${num("trail_start_r", "Trail starts at R", "0.25")}${num("trail_atr_mult", "ATR x")}${num("step_r", "Step R", "0.25")}${num("structure_buffer", "Swing buffer $")}
        <label class="pf"><span>Trail TF</span><select data-pk="trail_tf">${["M1", "M5", "M15", "H1"].map((x) => `<option ${x === s.trail_tf ? "selected" : ""}>${x}</option>`).join("")}</select></label>
        <label class="pf"><span>Partial close (at R:fraction, …)</span><input data-pk="partial_close" value="${s.partial_close.map((x) => x.join(":")).join(", ")}" placeholder="1:0.5"></label>`;
    }
    const rows = Object.entries(p.status || {});
    $("#pmStatus").innerHTML = rows.length ? `<table class="tbl small"><tr><th>Trade</th><th>R</th><th>Mode</th><th>SL</th><th>Next</th></tr>` +
      rows.map(([gid, x]) => `<tr><td class="mono">${esc(gid)}</td><td>${x.r != null ? x.r.toFixed(2) : "-"}</td><td>${esc(x.mode)}</td><td>${fx(x.sl)}</td>
        <td class="small">${x.new_sl != null ? `→ ${fx(x.new_sl)} (${esc(x.rule)})` : esc(x.blocked || "hold")}</td></tr>`).join("") + "</table>" : "";
  }
  $("#pmForm").addEventListener("input", () => { pmDirty = true; });
  $("#pmSave").addEventListener("click", async () => {
    const body = {};
    const pairs = (v) => v.split(",").map((x) => x.trim()).filter(Boolean).map((x) => x.split(":").map(Number));
    document.querySelectorAll("#pmForm [data-pk]").forEach((el) => {
      const k = el.dataset.pk;
      body[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : ["profit_lock", "partial_close"].includes(k) ? pairs(el.value) : parseFloat(el.value || "0");
    });
    const r = await api("/api/positions/settings", body);
    $("#pmMsg").textContent = r.ok ? "saved ✓" : "not saved";
    pmDirty = false; pollCCPanels();
  });

  // ---- decision rules card (which conditions are REQUIRED, conflict policy, weights, regime rules)
  async function loadRules() {
    try { DSET = await api("/api/decision/settings"); } catch (e) { return; }
    if (!DSET || !DSET.settings) return;
    const s = DSET.settings;
    const conds = Object.entries(DSET.conditions);
    $("#drlBody").innerHTML = `<div class="drl-conds">${conds.map(([k, c]) => `<label class="ck small" title="${esc(c.label)}"><input type="checkbox" data-req="${k}" ${s.required.includes(k) ? "checked" : ""}>
        ${esc(k)} <span class="muted">${esc(c.cls.toLowerCase())}</span></label>`).join("")}</div>
      <div class="sys-form">
        <label class="pf"><span>Min confidence (0 – 1)</span><input type="number" step="0.05" min="0" max="1" id="drlMin" value="${s.min_confidence}"></label>
        <label class="pf"><span>Conflict policy</span><select id="drlPol">${DSET.policies.map((x) => `<option ${x === s.conflict_policy ? "selected" : ""}>${x}</option>`).join("")}</select></label>
        <label class="pf"><span>High conflict = N categories</span><input type="number" min="1" max="10" id="drlHigh" value="${s.high_conflict_count}"></label>
        <label class="ck"><input type="checkbox" id="drlReg" ${s.regime_rules ? "checked" : ""}> regime modifies requirements</label>
      </div>
      <div class="klabel">Contribution weights</div><div class="sys-form drl-w">${Object.entries(s.weights).map(([k, v]) =>
        `<label class="pf"><span>${k}</span><input type="number" step="1" min="0" data-w="${k}" value="${v}"></label>`).join("")}</div>`;
  }
  $("#drlSave").addEventListener("click", async () => {
    const required = [...document.querySelectorAll("#drlBody [data-req]")].filter((x) => x.checked).map((x) => x.dataset.req);
    const weights = {};
    document.querySelectorAll("#drlBody [data-w]").forEach((el) => { weights[el.dataset.w] = parseFloat(el.value || "0"); });
    const r = await api("/api/decision/settings", { required, weights, min_confidence: parseFloat($("#drlMin").value || "0"),
      conflict_policy: $("#drlPol").value, high_conflict_count: parseInt($("#drlHigh").value || "2", 10), regime_rules: $("#drlReg").checked });
    $("#drlMsg").textContent = r.ok ? "saved ✓ (applies to the decision + AUTO gate)" : "not saved";
    DSET = null; loadRules(); pollDecision(true);
  });

  // ---- error log (System page)
  async function pollErrors() {
    if ($("#view-system").hidden) return;
    let e;
    try { e = await api("/api/errors?n=40"); } catch (x) { return; }
    const cnt = Object.entries(e.counts || {});
    $("#errSub").innerHTML = cnt.length ? cnt.map(([k, v]) => `<span class="rchip loss">${esc(k.replace("_ERROR", ""))} ${v}</span>`).join(" ") : "walang error ✓";
    $("#errBody").innerHTML = e.items.length ? `<div class="err-list">${e.items.map((x) => `<div class="err-row"><span class="muted">${new Date(x.t * 1000).toLocaleTimeString("en-GB")}</span>
      <span class="rchip">${esc(x.category)}</span> <b>${esc(x.component)}</b> <span class="muted">${esc(x.code)}</span><div class="small">${esc(x.message)}</div></div>`).join("")}</div>`
      : '<p class="muted">Walang naitalang error.</p>';
  }
  document.querySelector('.nav[data-view="system"]').addEventListener("click", () => setTimeout(pollErrors, 0));


  // ================= FORECAST (read-only prediction layer; never an order) =================
  let FC = null, fcSetDirty = false;
  const fcVisible = () => !$("#view-forecast").hidden;
  const dirCls = (d) => (d === "LONG" || d === 1 ? "c-bull" : d === "SHORT" || d === -1 ? "c-bear" : "muted");
  const regCls = (r) => /BULL|LONG/.test(r || "") ? "c-bull" : /BEAR|SHORT/.test(r || "") ? "c-bear" : "c-warn";
  const STATUS_TXT = { WAITING: "WAITING FOR ENTRY", TRIGGERED: "TRIGGERED", RUNNING: "RUNNING", TP_HIT: "TP HIT", SL_HIT: "SL HIT",
    EXPIRED: "EXPIRED", INVALIDATED: "INVALIDATED", AMBIGUOUS: "AMBIGUOUS BAR" };
  function fcLevels(f) {
    return `<table class="tbl fc-lv">
      <tr><td>Entry (${esc(f.entry_type)})</td><td class="mono">${fx(f.entry)}${f.entry_zone_low != null ? ` <span class="muted">zone ${fx(f.entry_zone_low)}–${fx(f.entry_zone_high)}</span>` : ""}</td></tr>
      ${f.trigger != null ? `<tr><td>Trigger</td><td class="mono">${fx(f.trigger)}</td></tr>` : ""}
      <tr><td>SL</td><td class="mono c-bear">${fx(f.sl)} <span class="muted small">${esc(f.sl_reason || "")}</span></td></tr>
      <tr><td>TP1 / TP2 / TP3</td><td class="mono c-bull">${fx(f.tp1)} / ${fx(f.tp2)} / ${fx(f.tp3)}</td></tr>
      <tr><td>RR</td><td>1:${f.rr}</td></tr></table>`;
  }
  function fcCard(f, active) {
    const n = (f.id || "").replace("FC-", "#").replace(/#0+/, "#");
    const head = `<div class="fc-head"><b>FORECAST ${esc(n)}</b> <span class="rchip fc-st">${esc(SETUP_NAME[f.setup_type || "structure"] || f.setup_type)}</span> <span class="${dirCls(f.direction)}"><b>${esc(f.direction)}</b></span>
      <span class="${regCls(f.regime)}">${esc((f.regime || "").replace("_", " "))}</span>
      <span class="rchip ${f.status === "TP_HIT" ? "win" : f.status === "SL_HIT" ? "loss" : ""}">${esc(STATUS_TXT[f.status] || f.status)}</span></div>`;
    const tfs = Object.entries(f.timeframes || {}).map(([k, v]) => `<span class="fc-tf"><i>${esc(k)}</i>${esc(v)}</span>`).join("");
    const nums = `<div class="fc-nums"><span>Analyser score <b>${f.analyser_score}%</b></span><span>Forecast confidence <b>${f.confidence}%</b></span>
      <span>Setup <b>${esc(f.setup_quality)}</b></span><span class="muted">${esc(f.session || "")}${f.news_risk && f.news_risk !== "n/a" ? " · news " + esc(f.news_risk) : ""}</span></div>`;
    let live = "";
    if (active) {
      live = `<div class="fc-nums small"><span>Age <b>${f.age_min != null ? dur(f.age_min * 60) : "-"}</b></span>
        <span>to entry <b>${f.dist_entry != null ? f.dist_entry : "-"}</b></span><span>to SL <b>${f.dist_sl != null ? f.dist_sl : "-"}</b></span>
        <span>to TP2 <b>${f.dist_tp != null ? f.dist_tp : "-"}</b></span>${f.r_now != null ? `<span>now <b class="${f.r_now >= 0 ? "c-bull" : "c-bear"}">${f.r_now >= 0 ? "+" : ""}${f.r_now}R</b></span>` : ""}</div>
        <div class="acct-actions"><button class="btn-sm ghost" data-fccancel="${esc(f.id)}">Cancel forecast</button></div>`;
    } else {
      live = `<div class="fc-nums"><span>Result <b>${esc(f.result || "-")}</b></span>
        <span>R <b class="${(f.r || 0) >= 0 ? "c-bull" : "c-bear"}">${f.r == null ? "-" : (f.r >= 0 ? "+" : "") + f.r + "R"}</b></span>
        <span>Duration <b>${f.duration_min != null ? f.duration_min + " min" : "-"}</b></span>
        <span>MFE <b>${f.mfe_r != null ? "+" + f.mfe_r + "R" : "-"}</b></span><span>MAE <b>${f.mae_r != null ? f.mae_r + "R" : "-"}</b></span></div>
        <div class="small muted">${esc(f.result_reason || "")}${f.invalidation_reason ? " · " + esc(f.invalidation_reason) : ""}</div>`;
    }
    return `<div class="fc-card ${active ? "act" : ""}">${head}<div class="fc-tfs">${tfs}</div>${nums}${fcLevels(f)}${live}
      <details class="small"><summary>Why / risks</summary><ul>${(f.why || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${(f.risks || []).length ? `<div class="c-warn">Risks:</div><ul>${f.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="muted">Confidence: ${(f.confidence_factors || []).map((x) => `${esc(x.factor)} ${x.points}/${x.max}`).join(" · ")}</div></details></div>`;
  }
  const SETUP_NAME = { structure: "Structure", struct_poc: "Structure + POC pullback", rev_ok: "Arrow R✓ (kumpirmado)", rev_any: "Arrow R?/R✓ (lahat)", router: "ROUTER trend 1:2 / range arrow 1:1", st_trend: "Strategy TREND", st_pullback: "Strategy PULLBACK", st_conso: "Strategy CONSOLIDATION", vp_poc: "VP · POC rejection", vp_edge: "VP · VAH/VAL rejection" };
  function fcCompareHtml(st) {
    const rows = (st && st.comparison) || [];
    if (!rows.length) return '<p class="muted small">Wala pang natapos na forecast para ikumpara.</p>';
    const best = (k, better) => { const v = rows.map((r) => r[k]).filter((x) => x != null); return v.length ? (better === "max" ? Math.max(...v) : Math.min(...v)) : null; };
    const bw = best("win_rate", "max"), br = best("avg_r", "max"), bp = best("profit_factor", "max");
    const cell = (v, b, suf = "") => v == null ? "-" : `<span class="${v === b && rows.length > 1 ? "fc-best" : ""}">${v}${suf}</span>`;
    return `<div class="table-wrap"><table class="tbl fc-cmp"><tr><th>Setup</th><th>Forecasts</th><th>Triggered</th><th>TP</th><th>SL</th>
      <th>Win %</th><th>Avg R</th><th>Total R</th><th>Profit factor</th><th>Avg conf.</th><th>Sample</th></tr>` +
      rows.map((r) => `<tr><td><b>${esc(SETUP_NAME[r.key] || r.key)}</b><div class="muted small">${esc(r.name || "")}</div></td>
        <td>${r.forecasts}</td><td>${r.triggered}</td><td class="c-bull">${r.tp}</td><td class="c-bear">${r.sl}</td>
        <td>${cell(r.win_rate, bw, "%")}</td><td>${cell(r.avg_r, br)}</td><td>${r.total_r}</td><td>${cell(r.profit_factor, bp)}</td>
        <td>${r.avg_confidence ?? "-"}</td><td class="small ${r.triggered >= (st.min_samples || 30) ? "c-bull" : "muted"}">${r.triggered >= (st.min_samples || 30) ? "sapat" : "kulang pa (" + r.triggered + "/" + (st.min_samples || 30) + ")"}</td></tr>`).join("") +
      `</table></div><p class="muted small">Pinakamataas sa bawat column ang naka-highlight. Huwag pang husgahan hangga't "kulang pa" ang sample.</p>`;
  }
  function fcStatsHtml(st) {
    if (!st) return "--";
    const o = st.overall;
    const tile = (l, v) => `<div class="fc-tile"><span>${l}</span><b>${v == null ? "-" : v}</b></div>`;
    const sr = (v) => v == null ? "-" : `<span class="${v >= 0 ? "c-bull" : "c-bear"}">${v >= 0 ? "+" : ""}${v}</span>`;
    const tbl = (rows, head, extra) => rows && rows.length ? `<div class="table-wrap"><table class="tbl small"><tr><th>${head}</th><th>Forecasts</th><th>Triggered</th><th>TP</th><th>SL</th><th>Expired</th><th>Invalid.</th><th>Win %</th><th>Avg R</th>${extra ? "<th>Data</th>" : ""}</tr>` +
      rows.map((r) => `<tr><td><b>${esc(r.key)}</b></td><td>${r.forecasts}</td><td>${r.triggered}</td><td>${r.tp}</td><td>${r.sl}</td><td>${r.expired}</td><td>${r.invalidated}</td>
        <td>${r.win_rate ?? "-"}</td><td>${sr(r.avg_r)}</td>${extra ? `<td class="small ${r.enough_data ? "c-bull" : "muted"}">${r.enough_data ? "enough" : "kulang pa"}</td>` : ""}</tr>`).join("") + "</table></div>" : '<p class="muted small">wala pa</p>';
    return `<div class="klabel">Comparison ng setups</div>${fcCompareHtml(st)}<div class="fc-tiles">${tile("Forecasts", o.forecasts)}${tile("Triggered", o.triggered)}${tile("Not triggered", o.not_triggered)}
      ${tile("Expired", o.expired)}${tile("Invalidated", o.invalidated)}${tile("TP", o.tp)}${tile("SL", o.sl)}${tile("Ambiguous", o.ambiguous)}
      ${tile("Win rate", o.win_rate == null ? null : o.win_rate + "%")}${tile("Trigger rate", o.trigger_rate == null ? null : o.trigger_rate + "%")}
      ${tile("Avg R", o.avg_r)}${tile("Profit factor", o.profit_factor)}${tile("Avg confidence", o.avg_confidence)}${tile("Avg score", o.avg_score)}
      ${tile("MFE / MAE", o.avg_mfe_r == null ? null : o.avg_mfe_r + " / " + o.avg_mae_r)}${tile("Avg duration", o.avg_duration_min == null ? null : o.avg_duration_min + "m")}</div>
      <div class="an-grid"><div><div class="klabel">Regime performance</div>${tbl(st.by_regime, "Regime")}</div>
        <div><div class="klabel">Confidence calibration <span class="muted">(min ${st.min_samples} triggered bawat bucket bago paniwalaan)</span></div>${tbl(st.calibration, "Confidence", true)}</div>
        <div><div class="klabel">Analyser score vs result</div>${tbl(st.by_score, "Score")}</div>
        <div><div class="klabel">Setup quality</div>${tbl(st.by_quality, "Quality")}</div>
        <div><div class="klabel">Direction</div>${tbl(st.by_direction, "Direction")}</div>
        <div><div class="klabel">Session</div>${tbl(st.by_session, "Session")}</div></div>`;
  }
  async function pollForecast(force) {
    if (!fcVisible() && !force) {
      if (FC && Date.now() - FC._t < 10000) return;
    }
    let s;
    try { s = await api("/api/forecast"); } catch (e) { return; }
    if (!s || s.disabled) return;
    s._t = Date.now(); FC = s;
    const act = s.active || [];
    $("#fcDot").className = "navdot " + (act.some((f) => f.status !== "WAITING") ? "ok" : act.length ? "warn" : "");
    if (!fcVisible()) return;
    const r = s.regime || {};
    const main = act[0];
    const hist = (s.regime_history || []).slice(0, 4).map((x) => esc((x.regime || "").replace("_", " "))).join(" ← ");
    $("#fcDash").innerHTML = `<div class="fc-dash-grid">
      <div><span>FORECAST REGIME</span><b class="${regCls(r.regime)}">${esc((r.regime || "--").replace("_", " "))}</b><small>${hist ? "dati: " + hist : ""}</small></div>
      <div><span>BIAS</span><b class="${dirCls(r.direction)}">${r.direction === 1 ? "BULLISH" : r.direction === -1 ? "BEARISH" : "NEUTRAL"}</b></div>
      <div><span>SETUP</span><b>${main ? esc(main.setup_quality) : "-"}</b></div>
      <div><span>ANALYSER SCORE</span><b>${main ? main.analyser_score + "%" : S && S.read ? S.read.overall + "%" : "-"}</b></div>
      <div><span>FORECAST CONFIDENCE</span><b>${main ? main.confidence + "%" : "-"}</b></div>
      <div><span>ENTRY</span><b class="mono">${main ? fx(main.entry) : "-"}</b></div>
      <div><span>SL</span><b class="mono c-bear">${main ? fx(main.sl) : "-"}</b></div>
      <div><span>TP</span><b class="mono c-bull">${main ? fx(main.tp2) : "-"}</b></div>
      <div><span>RR</span><b>${main ? "1:" + main.rr : "-"}</b></div>
      <div><span>STATUS</span><b>${main ? esc(STATUS_TXT[main.status] || main.status) : "NO ACTIVE FORECAST"}</b></div></div>
      <div class="fc-tfs">${Object.entries(r.tfs || {}).map(([k, v]) => `<span class="fc-tf"><i>${esc(k)}</i>${esc(v)}</span>`).join("")}</div>
      <div class="small muted">${(r.reasons || []).map(esc).join(" · ")}${s.why_not ? ` · <span class="c-warn">walang bagong forecast: ${esc(s.why_not)}</span>` : ""}${s.settings.enabled ? "" : ' · <b class="c-bear">FORECAST OFF</b>'}</div>`;
    $("#fcActSub").textContent = `${act.length} active · ${s.total} total`;
    $("#fcActive").innerHTML = act.length ? act.map((f) => fcCard(f, true)).join("") : '<p class="muted">Walang active forecast. Gagawa lang kapag malinaw ang regime at may valid na setup (hindi pinipilit).</p>';
    $("#fcResults").innerHTML = (s.recent || []).length ? s.recent.map((f) => fcCard(f, false)).join("") : '<p class="muted">wala pa</p>';
    if (!fcSetDirty) renderFcSettings(s.settings);
    try { $("#fcStats").innerHTML = fcStatsHtml(await api("/api/forecast/stats")); } catch (e) { /* next poll */ }
  }
  function renderFcSettings(st) {
    const num = (k, l, step = "1") => `<label class="pf"><span>${l}</span><input type="number" step="${step}" min="0" data-fk="${k}" value="${st[k]}"></label>`;
    const sel = (k, l, opts) => `<label class="pf"><span>${l}</span><select data-fk="${k}">${opts.map((o) => `<option ${o === st[k] ? "selected" : ""}>${o}</option>`).join("")}</select></label>`;
    $("#fcSet").innerHTML = `<label class="ck"><input type="checkbox" data-fk="enabled" ${st.enabled ? "checked" : ""}> Forecast ON</label>
      <label class="ck"><input type="checkbox" data-fk="draw" ${st.draw ? "checked" : ""}> Draw on chart (violet FC lines)</label>
      <label class="ck"><input type="checkbox" data-fk="one_per_direction" ${st.one_per_direction ? "checked" : ""}> One active per symbol/direction</label>
      ${num("min_score", "Min analyser score (0 = passing)")}${num("min_confidence", "Min confidence")}
      ${sel("min_quality", "Min setup quality", ["C", "B", "A", "A+"])}${sel("entry_mode", "Entry mode", ["auto", "market", "limit", "breakout"])}
      ${num("rr", "Default RR", "0.25")}${sel("final_target", "Finished at", ["tp1", "tp2", "tp3"])}
      ${num("expiry_bars", "Expiry (bars, not triggered)")}${num("running_timeout_bars", "Running timeout (bars)")}
      ${sel("ambiguous_rule", "Same-bar SL + TP", ["sl_first", "tp_first", "exclude"])}${sel("breakout_confirm", "Breakout confirm", ["close", "touch"])}
      ${sel("tf", "Forecast bar TF", ["M1", "M5", "M15"])}${num("max_spread", "Max spread $ (0 = off)", "0.05")}
      <div class="pf pf-tfs"><span>Allowed regimes</span><div class="tfchips">${["TREND", "PULLBACK", "BREAKOUT", "REVERSAL", "RANGE"].map((x) =>
        `<label class="tfchip"><input type="checkbox" data-freg="${x}" ${st.allowed_regimes.includes(x) ? "checked" : ""}><b>${x}</b></label>`).join("")}</div></div>
      <label class="ck"><input type="checkbox" data-fk="news_filter" ${st.news_filter ? "checked" : ""}> no new forecast on HIGH news risk</label>
      <div class="pf pf-tfs"><span>Setups na ikukumpara</span><div class="tfchips">${Object.keys(SETUP_NAME).map((x) =>
        `<label class="tfchip"><input type="checkbox" data-fset="${x}" ${(st.setups || []).includes(x) ? "checked" : ""}><b>${SETUP_NAME[x]}</b></label>`).join("")}</div></div>
      ${num("vp_min_rr", "VP: min RR to target", "0.25")}
      ${num("sp_max_dist_atr", "Structure + POC: max layo ng pPOC (x ATR)", "0.5")}
      <label class="ck"><input type="checkbox" data-fk="vp_bias_filter" ${st.vp_bias_filter ? "checked" : ""}> VP setups only with the HTF bias</label>
      ${num("rev_min_score", "Reversal arrows: min score", "5")}
      <label class="ck"><input type="checkbox" data-fk="rev_bias_filter" ${st.rev_bias_filter ? "checked" : ""}> Reversal arrows only with the HTF bias</label>`;
  }
  $("#fcSet").addEventListener("input", () => { fcSetDirty = true; });
  $("#fcSave").addEventListener("click", async () => {
    const body = { allowed_regimes: [...document.querySelectorAll("#fcSet [data-freg]")].filter((x) => x.checked).map((x) => x.dataset.freg),
      setups: [...document.querySelectorAll("#fcSet [data-fset]")].filter((x) => x.checked).map((x) => x.dataset.fset) };
    document.querySelectorAll("#fcSet [data-fk]").forEach((el) => {
      body[el.dataset.fk] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : parseFloat(el.value || "0");
    });
    const r = await api("/api/forecast/settings", body);
    $("#fcMsg").textContent = r.ok ? "saved ✓" : "not saved";
    fcSetDirty = false; pollForecast(true);
  });
  $("#fcCreate").addEventListener("click", async () => {
    const r = await api("/api/forecast/create", {});
    showModal(r.ok ? "New forecast" : "No forecast", r.ok ? `<p>${esc(r.forecast.id)} ${esc(r.forecast.direction)} · ${esc(r.forecast.regime)}</p>`
      : `<p>Walang malinaw na setup ngayon: <b>${esc(r.why_not || r.error || "")}</b></p><p class="muted small">Hindi pinipilit ang forecast kapag hindi malinaw ang market.</p>`, "", null);
    pollForecast(true);
  });
  $("#fcActive").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-fccancel]");
    if (!b) return;
    await api("/api/forecast/cancel", { id: b.dataset.fccancel });
    pollForecast(true);
  });
  let fcRepPoll = null;
  $("#fcReplay").addEventListener("click", async () => {
    const r = await api("/api/forecast/replay", { days: +$("#fcDays").value || 10 });
    if (!r.ok) { $("#fcRepMsg").textContent = r.error || "failed"; return; }
    if (!fcRepPoll) fcRepPoll = setInterval(async () => {
      const j = await api("/api/forecast/replay").catch(() => null);
      if (!j) return;
      $("#fcRepMsg").textContent = j.state === "running" ? `${Math.round(j.progress * 100)}% · ${j.msg}` : j.state === "error" ? j.msg : "done ✓";
      if (j.state !== "running") {
        clearInterval(fcRepPoll); fcRepPoll = null;
        if (j.result) $("#fcRepOut").innerHTML = `<p class="small muted">${j.result.bars} ${esc(j.result.tf)} bars · replay records are NOT saved to the live forecast journal</p>` + fcStatsHtml(j.result.stats)
          + `<div class="klabel">Huling replay forecasts</div><div class="fc-cards">${j.result.records.filter((f) => !["WAITING", "TRIGGERED", "RUNNING"].includes(f.status)).slice(-6).reverse().map((f) => fcCard(f, false)).join("")}</div>`;
      }
    }, 800);
  });
  document.querySelector('.nav[data-view="forecast"]').addEventListener("click", () => setTimeout(() => pollForecast(true), 0));


  // ---- forecast chart (candles + forecast-profile indicators + violet forecast levels / markers)
  let fcChart = null, fcCandles = null, fcMarkers = null, fcLines = new Map(), fcPLs = [], fcTf = lsGet("rg_fctf", "M15"), fcChKey = "";
  function fcChartInit() {
    if (fcChart) return;
    fcChart = LC.createChart($("#fcChart"), {
      autoSize: true, localization: { locale: "en-US" },
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#8a9bb8", fontSize: 11 },
      grid: { vertLines: { color: "rgba(148,163,184,0.05)" }, horzLines: { color: "rgba(148,163,184,0.05)" } },
      rightPriceScale: { borderColor: "rgba(148,163,184,0.15)" },
      timeScale: { borderColor: "rgba(148,163,184,0.15)", timeVisible: true, secondsVisible: false, rightOffset: 6 },
      crosshair: { mode: LC.CrosshairMode.Normal },
    });
    fcCandles = fcChart.addSeries(LC.CandlestickSeries, CANDLE_OPTS, 0);
    fcMarkers = LC.createSeriesMarkers(fcCandles, []);
    $("#fcTfs").innerHTML = ["M1", "M5", "M15", "H1", "H4"].map((t) => `<button class="tool mini ${t === fcTf ? "on" : ""}" data-fctf="${t}">${t}</button>`).join("");
    $("#fcTfs").addEventListener("click", (e) => {
      const b = e.target.closest("[data-fctf]");
      if (!b) return;
      fcTf = b.dataset.fctf; lsSet("rg_fctf", fcTf); fcChKey = "";
      document.querySelectorAll("#fcTfs [data-fctf]").forEach((x) => x.classList.toggle("on", x.dataset.fctf === fcTf));
      fcLines.forEach((sr) => { try { fcChart.removeSeries(sr); } catch (x) { /* gone */ } });
      fcLines.clear();
      loadFcChart(true);
    });
  }
  async function loadFcChart(fit) {
    if (!fcVisible()) return;
    fcChartInit();
    let d;
    try { d = await api(`/api/forecast/chart?tf=${fcTf}&n=400`); } catch (e) { return; }
    if (!d || !d.bars) return;
    fcCandles.setData(d.bars.map((b) => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] })));
    const seen = new Set();
    for (const ln of d.lines || []) {
      if (ln.pane) continue;                                // price-chart lines only on the forecast chart
      seen.add(ln.id);
      let sr = fcLines.get(ln.id);
      if (!sr) {
        sr = fcChart.addSeries(LC.LineSeries, { color: ln.color, lineWidth: ln.width || 2, priceLineVisible: false,
          lastValueVisible: false, title: "", crosshairMarkerVisible: false }, 0);
        fcLines.set(ln.id, sr);
      }
      sr.setData(ln.data.filter((p) => p[1] != null).map((p) => ({ time: p[0], value: p[1] })));
    }
    for (const [id, sr] of fcLines) if (!seen.has(id)) { try { fcChart.removeSeries(sr); } catch (x) { /* gone */ } fcLines.delete(id); }
    const key = JSON.stringify(d.levels) + JSON.stringify(d.markers);
    if (key !== fcChKey) {
      fcChKey = key;
      fcPLs.forEach((pl) => { try { fcCandles.removePriceLine(pl); } catch (x) { /* gone */ } });
      fcPLs = (d.levels || []).filter((lv) => !lv.pane).map((lv) => fcCandles.createPriceLine({
        price: lv.price, color: lv.color, lineWidth: lv.owner === "forecast" ? 2 : 1,
        lineStyle: lv.owner === "forecast" ? LC.LineStyle.LargeDashed : LC.LineStyle.Dashed, axisLabelVisible: true, title: lv.label || "" }));
      fcMarkers.setMarkers((d.markers || []).map((m) => ({ time: m.time, position: m.position, color: m.color, shape: m.shape, text: m.text })));
    }
    $("#fcChSub").innerHTML = `${esc(fcTf)} · indicators mula sa <b>${d.profile === "LIVE" ? "LIVE setup" : "forecast setup (demo)"}</b> · violet = forecast (virtual) · hindi totoong order`;
    if (fit) fcChart.timeScale().fitContent();
  }

  // ---- forecast indicator profile (demo setup)
  let FCP = null, fcProDirty = false;
  async function loadFcProfile() {
    try { FCP = await api("/api/forecast/profile"); } catch (e) { return; }
    if (!FCP || FCP.disabled) return;
    renderFcProfile();
  }
  function renderFcProfile() {
    const p = FCP;
    $("#fcProLive").checked = p.use_live;
    $("#fcProOwn").checked = !p.use_live;
    $("#fcProPass").value = p.scoring.passing;
    $("#fcProPass").disabled = p.use_live;
    $("#fcProSub").innerHTML = p.use_live ? "ginagamit ang LIVE setup (read-only dito; baguhin sa Plugins page)"
      : `sariling setup: ${p.plugins.filter((x) => x.score).length} sa SCORE · ${p.plugins.filter((x) => x.chart).length} sa CHART · hindi apektado ang live score at AUTO TRADE`;
    const cats = [...new Set(p.plugins.map((x) => x.category))].sort(catSort);
    const dis = p.use_live ? "disabled" : "";
    $("#fcProList").innerHTML = cats.map((c) => `<div class="lm-title grp-title" style="--c:${catColor(c)}"><b>${esc(String(c).toUpperCase())}</b></div>` +
      p.plugins.filter((x) => x.category === c).map((x) => `<div class="fcp-row" data-fk="${esc(x.key)}">
        <div class="fcp-nm">${esc(x.name)}${x.error ? ' <span class="c-bear">(error)</span>' : ""}</div>
        <label class="ck small"><input type="checkbox" data-f="chart" ${x.chart ? "checked" : ""} ${dis}> CHART</label>
        <label class="ck small ${x.can_score ? "" : "muted"}"><input type="checkbox" data-f="score" ${x.score ? "checked" : ""} ${x.can_score ? "" : "disabled"} ${dis}> SCORE</label>
        <span class="tfchips">${TFS_VOTE.map((t) => `<label class="tfchip"><input type="checkbox" data-ctf="${t}" ${x.chart_tfs.includes(t) ? "checked" : ""} ${dis}><b>${t}</b></label>`).join("")}</span>
        ${x.votes ? `<select data-f="vote_tf" ${dis} title="vote timeframe kapag walang TF na naka-check">${TFS_VOTE.map((t) => `<option ${t === x.vote_tf ? "selected" : ""}>${t}</option>`).join("")}</select>` : ""}
        ${Object.keys(x.defaults || {}).length ? `<details class="fcp-par"><summary>⚙</summary>${Object.keys(x.defaults).map((n) => {
          const dv = x.defaults[n], v = x.params[n];
          return typeof dv === "boolean" ? `<label class="ck small"><input type="checkbox" data-p="${esc(n)}" ${v ? "checked" : ""} ${dis}> ${esc(n)}</label>`
            : `<label class="pf bt-inl"><span>${esc(n.replace(/_/g, " "))}</span><input type="number" step="any" data-p="${esc(n)}" value="${esc(v)}" ${dis}></label>`;
        }).join("")}</details>` : ""}
      </div>`).join("")).join("");
  }
  $("#fcProList").addEventListener("input", () => { fcProDirty = true; });
  for (const id of ["#fcProLive", "#fcProOwn"]) $(id).addEventListener("change", async () => {
    if (!FCP) return;
    FCP.use_live = $("#fcProLive").checked;                 // "own" starts from what is listed (the live setup
    renderFcProfile();                                         // the first time); Save makes it the forecast setup
    $("#fcProMsg").textContent = "i-Save para magamit";
  });
  $("#fcProCopy").addEventListener("click", async () => {
    const r = await api("/api/forecast/profile_copy", {});
    $("#fcProMsg").textContent = r.ok ? "kinopya ang live setup ✓ (sariling setup na ngayon)" : "failed";
    fcProDirty = false; FCP = r.profile; renderFcProfile(); fcChKey = ""; loadFcChart(false);
  });
  $("#fcProSave").addEventListener("click", async () => {
    const plugins = {};
    document.querySelectorAll("#fcProList .fcp-row").forEach((row) => {
      const g = (sel) => row.querySelector(sel);
      const params = {};
      row.querySelectorAll("[data-p]").forEach((el) => { params[el.dataset.p] = el.type === "checkbox" ? el.checked : el.value; });
      plugins[row.dataset.fk] = { chart: g('[data-f="chart"]').checked, score: g('[data-f="score"]').checked,
        chart_tfs: [...row.querySelectorAll("[data-ctf]")].filter((x) => x.checked).map((x) => x.dataset.ctf),
        vote_tf: g('[data-f="vote_tf"]') ? g('[data-f="vote_tf"]').value : null, params };
    });
    const body = { use_live: $("#fcProLive").checked };
    if (!body.use_live) { body.plugins = plugins; body.scoring = Object.assign({}, FCP.scoring, { passing: parseFloat($("#fcProPass").value || "70") }); }
    const r = await api("/api/forecast/profile", body);
    $("#fcProMsg").textContent = r.ok ? "saved ✓ (sa susunod na sarado na bar gagamitin)" : "not saved";
    fcProDirty = false; FCP = r.profile; renderFcProfile(); fcChKey = ""; loadFcChart(false);
  });
  document.querySelector('.nav[data-view="forecast"]').addEventListener("click", () => setTimeout(() => {
    loadFcChart(true); if (!fcProDirty) loadFcProfile();
  }, 50));

  (async function boot() {
    await pollState();
    await loadChart(true);
    setInterval(pollState, 500);
    pollTrading();
    setInterval(pollTrading, 1000);
    pollJournal(true);
    setInterval(pollJournal, 3000);
    setInterval(() => loadChart(false), 1000);
    pollNews(true);
    setInterval(pollNews, 20000);
    setInterval(pollNT, 1000);
    pollIM();
    setInterval(pollIM, 10000);
    setInterval(pollOF, 1500);
    setInterval(pollAT, 1500);
    pollRegime();
    setInterval(pollRegime, 5000);
    pollEG();
    setInterval(pollEG, 3000);
    pollSys(true);
    setInterval(pollSys, 3000);
    pollDecision(true);
    setInterval(pollDecision, 1000);
    setInterval(pollCCPanels, 3000);
    setInterval(pollAnalytics, 10000);
    setInterval(pollErrors, 5000);
    setInterval(pollForecast, 2000);
    setInterval(() => loadFcChart(false), 2000);
    api("/api/risk").then((r) => { if (r && !r.disabled) { RS = r; renderRisk(); } }).catch(() => {});
    setInterval(() => { if (!ccVisible()) api("/api/risk").then((r) => { if (r && !r.disabled) { RS = r; renderRisk(); } }).catch(() => {}); }, 10000);
  })();
})();

// ============================================================================ AI ANALYST (Claude Fable, read-only)
(function () {
  const $ = (q) => document.querySelector(q);
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let AI = null, timer = null;
  const MODE = { analysis: "Market read", score: "Score check", question: "Tanong" };
  async function get() { const r = await fetch("/api/ai", { cache: "no-store" }); return r.json(); }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    return r.json();
  }
  // light markdown: headings, bold, lists, line breaks (text is escaped first)
  function md(t) {
    const lines = esc(t).split(/\n/);
    let out = "", list = null;
    const close = () => { if (list) { out += `</${list}>`; list = null; } };
    for (const raw of lines) {
      const l = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
      let m;
      if ((m = l.match(/^#{1,4}\s+(.*)/))) { close(); out += `<h4>${m[1]}</h4>`; }
      else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${m[1]}</li>`; }
      else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${m[1]}</li>`; }
      else if (!l.trim()) { close(); }
      else { close(); out += `<p>${l}</p>`; }
    }
    close();
    return out;
  }
  function recHtml(r, big) {
    if (!r) return "";
    const when = new Date(r.t * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const head = `<div class="ai-meta"><b>${esc(MODE[r.mode] || r.mode)}</b> · ${esc(when)} · ${esc(r.model)} · score ${r.score != null ? Math.round(r.score) + "%" : "-"} · ${esc(r.bias || "")}${r.seconds != null ? " · " + r.seconds + "s" : ""}${r.usage && r.usage.output_tokens ? " · " + r.usage.input_tokens + "/" + r.usage.output_tokens + " tokens" : ""}</div>`;
    const q = r.question ? `<div class="ai-q">“${esc(r.question)}”</div>` : "";
    const body = r.ok ? `<div class="ai-text">${md(r.answer)}</div>` : `<div class="c-bear">✗ ${esc(r.error)}</div>`;
    return big ? head + q + body : `<details class="ai-h"><summary>${head}</summary>${q}${body}</details>`;
  }
  function renderSettings(st) {
    const box = $("#aiSet");
    if (box.contains(document.activeElement)) return;
    const s = st.settings;
    const opt = (list, v) => list.map((x) => `<option ${x === v ? "selected" : ""}>${esc(x)}</option>`).join("");
    box.innerHTML = `<label class="pf"><span>Anthropic API key ${s.key_set ? `<span class="c-bull">✓ naka-set (${esc(s.key_hint)})</span>` : '<span class="c-warn">wala pa</span>'}</span>
        <input type="password" id="aiKey" autocomplete="off" placeholder="${s.key_set ? "iwanang blangko para hindi palitan" : "sk-ant-..."}"></label>
      <label class="pf"><span>Model</span><select id="aiModel">${opt(st.models, s.model)}</select></label>
      <label class="pf"><span>Wika ng sagot</span><select id="aiLang">${opt(["Taglish", "English", "Filipino"], s.language)}</select></label>
      <label class="pf"><span>Max tokens ng sagot</span><input type="number" id="aiMax" min="300" max="8000" step="100" value="${s.max_tokens}"></label>
      <label class="pf"><span>Pagitan ng request (s)</span><input type="number" id="aiGap" min="5" step="5" value="${s.min_interval_s}"></label>
      <div class="muted small">Kukuha ng key sa console.anthropic.com. Bawat pindot ay isang API request (may bayad ayon sa tokens). Sa PC na ito lang naka-save ang key, at hindi ito ibinabalik ng server sa browser.</div>`;
  }
  function render(st) {
    AI = st;
    if (st.disabled) { $("#aiOut").innerHTML = '<div class="muted">AI analyst not available.</div>'; return; }
    $("#aiSub").textContent = `${st.settings.model} · ${st.settings.key_set ? "handa" : "maglagay muna ng API key sa Settings"}`;
    document.querySelectorAll("[data-ai]").forEach((b) => { b.disabled = st.running || !st.settings.key_set; });
    $("#aiDot").classList.toggle("on", !!st.running);
    if (st.running) $("#aiMsg").innerHTML = '<span class="c-info">⏳ Nag-iisip si Claude… (karaniwang 10–60s)</span>';
    else if ($("#aiMsg").dataset.keep !== "1") $("#aiMsg").textContent = "";
    $("#aiOut").innerHTML = st.last ? recHtml(st.last, true) : '<div class="muted">Pindutin ang isang button para sa unang analysis.</div>';
    $("#aiHist").innerHTML = st.history.length > 1 ? st.history.slice(1).map((r) => recHtml(r, false)).join("") : '<div class="muted small">Wala pa.</div>';
    renderSettings(st);
  }
  async function poll() {
    try { render(await get()); } catch (e) { /* server restarting */ }
    clearTimeout(timer);
    if (!$("#view-ai").hidden) timer = setTimeout(poll, AI && AI.running ? 1500 : 6000);
  }
  document.querySelectorAll("[data-ai]").forEach((b) => b.addEventListener("click", async () => {
    const mode = b.dataset.ai;
    const res = await post("/api/ai/ask", { mode, question: mode === "question" ? $("#aiQ").value : "" });
    $("#aiMsg").dataset.keep = res.ok ? "0" : "1";
    $("#aiMsg").innerHTML = res.ok ? "" : `<span class="c-bear">${esc(res.error)}</span>`;
    setTimeout(() => { $("#aiMsg").dataset.keep = "0"; }, 5000);
    poll();
  }));
  $("#aiSave").addEventListener("click", async () => {
    const body = { model: $("#aiModel").value, language: $("#aiLang").value, max_tokens: $("#aiMax").value, min_interval_s: $("#aiGap").value };
    const k = $("#aiKey").value.trim();
    if (k) body.api_key = k;
    const r = await post("/api/ai/settings", body);
    $("#aiKey").value = "";
    document.activeElement && document.activeElement.blur();
    $("#aiSetMsg").textContent = r.ok ? "Saved ✓" : "Error: " + (r.error || "");
    poll();
  });
  $("#aiClear").addEventListener("click", async () => {
    if (!confirm("Alisin ang API key sa PC na ito?")) return;
    await post("/api/ai/settings", { clear_key: true });
    $("#aiSetMsg").textContent = "Key removed";
    poll();
  });
  document.querySelector('.nav[data-view="ai"]').addEventListener("click", () => setTimeout(poll, 0));
})();

// ============================================================================ STRATEGIES (Trading page)
(function () {
  const $ = (q) => document.querySelector(q);
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fx = (v) => (v == null || isNaN(v) ? "--" : Number(v).toFixed(2));
  let ST = null, dirty = false, timer = null;
  const PARAMS = {
    trend: [["bos_ltf", "Entry TF (BOS)", ["M5", "M15", "H1"]], ["bos_htf", "Trend TF (BOS)", ["M15", "H1", "H4"]], ["bos_m15_min", "Ilang BOS sa entry TF", 1], ["rsi_hi", "KONTRA: overbought kapag RSI ≥ (BUY)", 1], ["rsi_lo", "KONTRA: oversold kapag RSI ≤ (SELL)", 1], ["ob_tf", "KONTRA: order block TF", ["M5", "M15", "H1"]], ["ob_block_atr", "KONTRA: OB nakaharang kapag ≤ ×ATR ang layo", 0.1], ["bos_window", "Entry BOS: sa huling N kandila", 1], ["h4_window", "Trend BOS: sa huling N kandila", 1], ["rr", "RR (1:x)", 0.25], ["sl_atr", "SL × ATR", 0.1], ["adx_min", "ADX min", 1], ["cooldown", "Pahinga (bars)", 1]],
    pullback: [["rr", "RR (1:x)", 0.25], ["sl_atr", "SL × ATR (fallback)", 0.1], ["os", "RSI oversold (uptrend)", 1], ["ob", "RSI overbought (downtrend)", 1], ["mfi_os", "MFI oversold ≤", 1], ["mfi_ob", "MFI overbought ≥", 1], ["doji_body", "Doji: body ≤ ×range", 0.05], ["ob_tf", "Order block TF (hindi dapat ma-break)", ["M5", "M15", "H1"]], ["ob_near", "Order block: lapit (× ATR)", 0.05], ["vol_pct", "VOLATILE kapag ATR pct ≥", 1], ["cooldown", "Pahinga (bars)", 1]],
    conso: [["adx_max", "ADX max (mababa)", 1], ["vol_pct", "Volume ≤ percentile", 5], ["quiet_bars", "Tahimik nang min. N kandila", 1], ["levels", "Grid levels bawat side", 1], ["step", "Grid step $", 0.25], ["rr", "RR (1:x)", 0.25], ["buffer", "Buffer sa labas ng range $", 0.1], ["max_sl", "Max SL $", 0.5], ["range_bars", "Range: huling N bars (kapag walang box)", 1], ["max_range", "Max range $", 0.5], ["expiry_bars", "Kanselahin pagkatapos ng (bars)", 1]],
  };
  window.rgStratParams = PARAMS;
  const DESC = { trend: "BOS sa entry TF (hal. 2 sa M15) + 1 BOS sa trend TF (hal. H4) sa parehong direksyon → market entry SA DIREKSYON ng trend", pullback: "Uptrend + oversold + MFI + doji + order block → BUY (salamin para sa SELL)", conso: "Volume mahina (1) + ADX < 20 (2) + box (3) → BUY STOP sa itaas / SELL STOP sa ibaba ng box; na-hit ang isa = cancel ang kabila (OCO)" };
  async function load() {
    try { const r = await fetch("/api/strategies", { cache: "no-store" }); ST = await r.json(); } catch (e) { return; }
    if (!dirty) render();
    else renderLive();
  }
  function planHtml(n, e) {
    if (!e) return "";
    if (n === "conso") return e.box_high != null
      ? `<div class="sp-plan"><b>${e.range_src === "box" ? "BOX" : "RANGE"}</b> ${fx(e.box_low)}–${fx(e.box_high)} · ADX ${e.adx ?? "-"} · volume pct ${e.vol_pct ?? "-"} · <span class="${(e.quiet_n || 0) >= (e.quiet_bars || 4) ? "c-bull" : "c-warn"}">tahimik ${e.quiet_n ?? 0}/${e.quiet_bars ?? 4} kandila</span><br>
         <span class="c-bull">BUY STOP ${(e.grid || []).filter((g) => g.side === 1).map((g) => fx(g.entry)).join(" / ")}</span> ·
         <span class="c-bear">SELL STOP ${(e.grid || []).filter((g) => g.side === -1).map((g) => fx(g.entry)).join(" / ")}</span> · SL $${fx(e.sl_dist)} · TP $${fx(e.tp_dist)}</div>` : "";
    if (!e.side) return "";
    return `<div class="sp-plan"><b class="${e.side === 1 ? "c-bull" : "c-bear"}">${e.side === 1 ? "BUY" : "SELL"}</b> ${fx(e.entry)} · SL ${fx(e.sl)} · TP ${fx(e.tp)} (1:${e.rr})</div>`;
  }
  function liveHtml(n, e, cfg) {
    if (!e) return "";
    const ok = e.ok;
    const SHORT = { bos_m15: `${cfg.bos_ltf || "M15"} BOS ×${cfg.bos_m15_min || 2}`, bos_h4: `${cfg.bos_htf || "H4"} BOS`, rsi: "RSI oversold/overbought", mfi: "MFI", doji: "Doji", ob: "Order block", vol_low: "Mahinang volume", adx_low: "ADX < " + (cfg.adx_max || 20), box: "Box", squeeze: "BB squeeze", atr_low: "ATR tahimik", stoch: "Stoch", cci: "CCI", wpr: "W%R", near_ema: "Malapit sa EMA", turn: "Bumabalik", volatile: "hindi VOLATILE", range: "siksik na range", trend: "trend" };
    const lab = (k) => SHORT[k] || (e.labels && e.labels[k]) || k;
    const miss = (e.missing || []).map(lab);
    const on = (e.on || []).length, have = (e.on || []).filter((k) => e.met && e.met[k]).length;
    const notMet = (e.on || []).filter((k) => !(e.met && e.met[k])).map(lab);
    const head = ok ? `<span class="c-bull">✓ HANDA (100%)</span>` : `<span class="c-warn">⏳ ${esc(miss.length ? "kulang: " + miss.join(", ") + (e.why && !/BOS/.test(e.why) ? " · " + e.why : "") : (e.why || (notMet.length ? "kulang: " + notMet.join(", ") : "hinihintay")))}</span>`;
    return `<div class="sp-live">${head} · <b>${have}/${on || "-"}</b> kondisyon (kailangan lahat)${e.side ? ` · direksyon <b class="${e.side === 1 ? "c-bull" : "c-bear"}">${e.side === 1 ? "BUY" : "SELL"}</b>` : ""}${e.atr_pct != null ? ` · ATR pct ${Math.round(e.atr_pct)}` : ""}</div>` + planHtml(n, e);
  }
  function render() {
    const g = $("#stratGrid");
    if (!g || !ST) return;
    if (ST.error && !ST.settings) { g.innerHTML = `<div class="muted">${esc(ST.error)}</div>`; return; }
    $("#stratTf").textContent = `AUTO TF: ${ST.tf}`;
    renderProfiles();
    g.innerHTML = ["trend", "pullback", "conso"].map((n) => {
      const cfg = ST.settings[n], e = (ST.strategies || {})[n] || {};
      const conds = ST.catalog[n].map((c) => {
        const req = cfg.required.includes(c.key), on = cfg.conditions[c.key];
        const met = e.met ? e.met[c.key] : undefined;
        return `<label class="sp-cond ${on ? "" : "off"}"><input type="checkbox" data-sn="${n}" data-cond="${c.key}" ${on ? "checked" : ""} ${req ? "disabled" : ""}>
          <i class="${met === true ? "c-bull" : met === false ? "c-bear" : "muted"}">${met === true ? "✓" : met === false ? "✗" : "•"}</i><span>${esc(c.label)}${req ? " 🔒" : ""}</span></label>`;
      }).join("");
      const params = PARAMS[n].map(([k, lab, step]) => Array.isArray(step)
        ? `<label class="pf"><span>${lab}</span><select data-sn="${n}" data-p="${k}">${step.map((o) => `<option ${cfg[k] === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>`
        : `<label class="pf"><span>${lab}</span><input type="number" step="${step}" data-sn="${n}" data-p="${k}" value="${cfg[k]}"></label>`).join("") +
        (n === "pullback" ? `<label class="ck"><input type="checkbox" data-sn="${n}" data-p="block_volatile" ${cfg.block_volatile ? "checked" : ""}> Huwag pumasok kapag VOLATILE</label>` : "");
      return `<div class="sp ${cfg.auto ? "auto" : ""}"><div class="sp-h"><b>${esc(ST.titles[n])}</b>
          <label class="ck sp-auto"><input type="checkbox" data-sn="${n}" data-p="auto" ${cfg.auto ? "checked" : ""}> AUTO</label></div>
        <div class="muted small">${DESC[n]}</div>
        <div class="sp-livebox" id="spLive_${n}">${liveHtml(n, e, cfg)}</div>
        <div class="sp-conds">${conds}</div>
        <details class="sp-more"><summary>Settings</summary><div class="sys-form">${params}</div></details></div>`;
    }).join("");
  }
  // ---- NORMAL / AGGRESSIVE / SCALP
  function renderProfiles() {
    const bar = $("#profBar");
    if (!bar || !ST || !ST.profiles) return;
    const cur = ST.settings.profile || "normal";
    bar.innerHTML = `<div class="prof-h"><b>PROFILE</b><span class="muted small">pinapalitan ang settings ng 3 strategy + ang AUTO TF · hindi nito ino-ON ang AUTO</span></div>
      <div class="prof-row">${Object.entries(ST.profiles).map(([k, p]) => `<button class="prof ${k === cur ? "on" : ""}" data-prof="${k}" title="${esc(p.desc)}">
        <b>${esc(p.title)}</b><span>${esc(p.desc)}</span><i>AUTO TF ${esc(p.auto_tf)}</i></button>`).join("")}
        ${cur === "custom" ? '<div class="prof on custom"><b>CUSTOM</b><span>Binago mo ang settings. Pumili ng profile para ibalik.</span></div>' : ""}</div>
      <label class="ck"><input type="checkbox" id="profHtf" ${ST.settings.htf_filter !== false ? "checked" : ""}> Kailangan ding kasabay ang HTF bias ng analyser (TREND / PULLBACK). OFF = ang sariling trend ng strategy lang (hal. H4 BOS) ang susundin.</label>`;
  }
  document.addEventListener("change", (e) => { if (e.target && e.target.id === "profHtf") dirty = true; });
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-prof]");
    if (!b || !ST) return;
    const k = b.dataset.prof, p = ST.profiles[k];
    const go = async () => {
      if (window.rgModal) window.rgModal.hide();
      const r = await fetch("/api/strategies/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: k }) }).then((x) => x.json()).catch(() => ({ ok: false }));
      $("#stratMsg").textContent = r.ok ? `${p.title} ✓${r.auto_tf ? " · AUTO TF " + r.auto_tf : ""}` : (r.error || "failed");
      dirty = false;
      await load(); render();
    };
    const anyAuto = ["trend", "pullback", "conso"].some((n) => ST.settings[n].auto);
    if (window.rgModal) window.rgModal.show(`Gamitin ang ${p.title}?`, `<p>${esc(p.desc)}</p><p>Papalitan ang settings at conditions ng TREND, PULLBACK at CONSOLIDATION, at magiging <b>${p.auto_tf}</b> ang AUTO TF.</p>
      ${anyAuto ? '<p class="c-warn">May strategy na naka-AUTO: ang bagong settings na ang gagamitin sa susunod na signal.</p>' : '<p class="muted">Walang ino-ON: naka-OFF pa rin ang AUTO ng bawat strategy.</p>'}`, "Gamitin", go, anyAuto);
    else go();
  });
  function renderLive() {
    if (!ST || !ST.strategies) return;
    for (const n of ["trend", "pullback", "conso"]) {
      const el = $(`#spLive_${n}`);
      if (el) el.innerHTML = liveHtml(n, ST.strategies[n], ST.settings[n]);
    }
  }
  document.addEventListener("input", (e) => { if (e.target.closest && e.target.closest("#stratGrid")) dirty = true; });
  document.addEventListener("click", async (e) => {
    if (!e.target || e.target.id !== "stratSave") return;
    const body = {};
    document.querySelectorAll("#stratGrid [data-sn]").forEach((el) => {
      const n = el.dataset.sn;
      body[n] = body[n] || { conditions: {} };
      if (el.dataset.cond) body[n].conditions[el.dataset.cond] = el.checked;
      else if (el.type === "checkbox") body[n][el.dataset.p] = el.checked;
      else if (el.tagName === "SELECT") body[n][el.dataset.p] = el.value;
      else body[n][el.dataset.p] = parseFloat(el.value);
    });
    const hf = $("#profHtf");
    if (hf) body.htf_filter = hf.checked;
    const newAuto = ["trend", "pullback", "conso"].filter((n) => body[n] && body[n].auto && !(ST.settings[n] || {}).auto);
    const doSave = async () => {
      const r = await fetch("/api/strategies/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((x) => x.json()).catch(() => ({ ok: false }));
      $("#stratMsg").textContent = r.ok ? "saved ✓" : "save failed";
      dirty = false;
      await load(); render();
    };
    if (newAuto.length && window.rgModal) {
      window.rgModal.show("I-AUTO ang strategy?", `<p>Papasukan ng AUTO TRADE ang: <b>${newAuto.map((n) => ST.titles[n]).join(", ")}</b> (kapag naka-ON at naka-ARM ang AUTO).</p>
        <p class="muted">Dadaan pa rin sa lahat ng gate. Mas mabuting subukan muna sa Forecast at Backtest.</p>`, "I-AUTO", async () => { window.rgModal.hide(); await doSave(); }, true);
    } else await doSave();
  });
  function tick() {
    clearTimeout(timer);
    const v = $("#view-tradeset");
    if (v && !v.hidden) load();
    timer = setTimeout(tick, 5000);
  }
  document.querySelector('.nav[data-view="tradeset"]').addEventListener("click", () => { dirty = false; setTimeout(() => load().then(render), 50); });
  tick();
})();

// ============================================================================ STRATEGY side panel (Market Overview)
// The 4 strategies (CONSOLIDATION · TREND · PULLBACK · NEWS): how close each one is to firing (progress bar,
// the price that completes it and how far it is), and the AUTO TRADE status. Read-only: it only shows /api/strategies.
(function () {
  const $ = (q) => document.querySelector(q);
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fx = (v) => (v == null || isNaN(v) ? "--" : Number(v).toFixed(2));
  let D = null, timer = null;
  const dots = (arr) => arr.map(([ok, t]) => `<span class="ss-dot ${ok ? "on" : ""}">${esc(t)}</span>`).join("");
  const side = (d) => (d === 1 ? '<b class="c-bull">BUY ▲</b>' : d === -1 ? '<b class="c-bear">SELL ▼</b>' : "");
  const chip = (t, c) => `<span class="mchip sm ${c}">${t}</span>`;
  function badge(n) {
    if (!D || !D.auto || !n) return "";
    const a = D.auto, on = a.strategies && a.strategies[n];
    const click = a.trading ? ` click" data-stauto="${n}` : "";
    if (!on) return `<span class="ss-auto tog${click}" title="MANUAL: hindi ito papasukan ng AUTO. I-click para i-AUTO (may confirm).">○ MANUAL</span>`;
    const live = a.enabled && a.armed;
    return `<span class="ss-auto tog on ${live ? "live" : ""}${click}" title="${live ? "papasok ang AUTO kapag lahat green" : "naka-AUTO ang strategy pero OFF ang AUTO TRADE (⚡ AUTO sa itaas)"} · i-click para gawing MANUAL">● AUTO${live ? "" : " (off)"}</span>`;
  }
  // why a READY strategy did / will not trade
  function readyNote(n, e) {
    if (!e || !e.ok || !D || !D.auto || !D.auto.trading) return "";
    const a = D.auto, ls = e.last_signal;
    const old = ls && !ls.fresh ? ` (nag-READY ${ls.bars_ago} bar na ang nakalipas: AUTO ay pumapasok lang sa bar na unang nag-READY)` : "";
    if (!a.strategies[n]) return `<div class="ss-warn">⚠ READY pero MANUAL: hindi ito ite-trade ng AUTO. I-click ang ○ MANUAL para i-AUTO.</div>`;
    if (!a.enabled) return `<div class="ss-warn">⚠ READY pero OFF ang AUTO TRADE.</div>`;
    if (!a.armed) return `<div class="ss-warn">⚠ READY pero naka-OFF / HALTED ang trading.</div>`;
    const la = e.last_auto;
    if (la && la.decision === "skip") {
      const why = (la.text.match(/\[(BLOCKED_[A-Z_]+)\]\s*(.*)$/) || [])[2] || la.text;
      return `<div class="ss-warn c-bear">⛔ lahat green pero HINDI pumasok: ${esc(why)} <span class="muted">(susubukan ulit kada 1 min habang green)</span></div>`;
    }
    if (la && la.decision === "error") return `<div class="ss-warn c-bear">⛔ ${esc(la.text)}</div>`;
    return `<div class="ss-warn c-bull">▶ lahat green: papasok ang AUTO (sa loob ng ~15 s, kung pasado sa risk / session / news)</div>`;
  }
  function cd(sec) {
    if (sec <= 0) return "NGAYON";
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
    return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(s).padStart(2, "0")}`;
  }
  function nearHtml(nr, ok) {
    if (!nr) return "";
    let t = "";
    if (nr.text) t += esc(nr.text);
    if (nr.dist != null) t += ` · <b>${nr.dist <= 0 ? "LAGPAS NA" : "$" + fx(nr.dist) + " ang layo"}</b>`;
    return t ? `<div class="ss-near">${ok ? "▶ " : ""}${t}</div>` : "";
  }
  function row(n, title, state, body, pct, ok, bdg) {
    const cls = ok ? "ready" : pct >= 60 ? "near" : "";
    const tag = ok ? '<span class="c-bull">READY</span>' : pct >= 60 ? '<span class="c-warn">MALAPIT</span>' : "";
    return `<div class="ss-row ${cls}"><div class="ss-h"><b>${title}</b>${bdg ?? badge(n)}${n ? `<button class="ss-gear" data-sedit="${n}" title="Strategy panel: i-edit ang values / magdagdag ng indicator">⚙</button>` : ""}<span class="ss-state">${tag} ${state}</span></div>
      <div class="ss-bar" title="${pct}% ng kailangan"><i style="width:${Math.max(3, pct)}%"></i></div>${body}</div>`;
  }
  function render() {
    const box = $("#ssRows");
    if (!box) return;
    if (!D) { box.innerHTML = '<div class="muted small">naglo-load…</div>'; return; }
    $("#ssTf").textContent = D.tf ? `· ${D.tf}` : "";
    const a = D.auto || {};
    $("#ssAuto").innerHTML = !a.trading ? chip("READ-ONLY: walang trading", "")
      : [a.enabled ? chip("AUTO TRADE ON", "warn") : chip("AUTO TRADE OFF", ""), 
         a.paper ? chip("PAPER", "info") : chip("LIVE", "bear"), D.news && D.news.enabled ? chip("NEWS TRADE ON", "warn") : ""].join("");
    const pf = D.settings && D.settings.profile;
    if (pf) $("#ssAuto").innerHTML += chip(pf === "custom" ? "CUSTOM" : ((D.profiles || {})[pf] || {}).title || pf, "info");
    if (D.error) { box.innerHTML = `<div class="muted small">${esc(D.error)}</div>`; return; }
    const S = D.strategies || {}, out = [];
    const c = S.conso;
    if (c) {
      const m = c.met || {}, nr = c.near || {};
      const ccfg = (D.settings && D.settings.conso) || {}, con = ccfg.conditions || {};
      const ckeys = Object.keys(con).filter((k) => con[k]);
      const cnt = ckeys.filter((k) => m[k]).length;
      const cname = { adx_low: `ADX<${ccfg.adx_max || 20}${c.adx != null ? " (" + c.adx + ")" : ""}`, vol_low: "Volume", box: "Box", squeeze: "Squeeze", atr_low: "ATR", rsi_mid: "RSI 40–60", inside_bb: "Loob ng BB" };
      let b = `<div class="ss-line">${dots(ckeys.map((k) => [m[k], cname[k] || k]))}${c.range_src === "box" ? '<span class="muted small">· may box</span>' : ""}</div>`;
      b += nearHtml(nr, c.ok);
      b += readyNote("conso", c);
      if (c.ok) b += `<div class="ss-line small">BUY STOP <b class="c-bull">${fx(c.buy_stop)}</b> · SELL STOP <b class="c-bear">${fx(c.sell_stop)}</b> · OCO</div>`;
      out.push(row("conso", "CONSOLIDATION", `<b>${cnt}/${ckeys.length}</b>`, b, nr.progress ?? Math.round(cnt / Math.max(1, ckeys.length) * 100), c.ok));
    }
    const t = S.trend;
    if (t) {
      const up = t.m15_up || 0, dn = t.m15_dn || 0, h4 = t.h4_bos || 0, nr = t.near || {};
      const need = (D.settings && D.settings.trend && D.settings.trend.bos_m15_min) || 2;
      const got = h4 === 1 ? up : h4 === -1 ? dn : 0;
      const ltf = t.ltf || (D.settings && D.settings.trend && D.settings.trend.bos_ltf) || "M15", htf = t.htf || (D.settings && D.settings.trend && D.settings.trend.bos_htf) || "H4";
      const tcon = (D.settings.trend || {}).conditions || {};
      let b = `<div class="ss-line">${dots([...(tcon.bos_h4 ? [[h4 !== 0, `${htf} BOS ${h4 === 1 ? "↑" : h4 === -1 ? "↓" : "–"}`]] : []), ...(tcon.bos_m15 ? [[got >= need && t.m15_last === h4 && h4 !== 0, `${ltf} BOS ${Math.min(got, need)}/${need}${h4 && t.m15_last && t.m15_last !== h4 ? (t.m15_last === 1 ? " (huli ↑)" : " (huli ↓)") : ""}`]] : []),
        ...Object.entries((D.settings.trend || {}).conditions || {}).filter(([k, v]) => v && k !== "bos_m15" && k !== "bos_h4")
          .map(([k]) => [(t.met || {})[k], { ema_align: "EMA20/50", price_ema50: "EMA50", adx: "ADX", di: "DI", macd: "MACD", supertrend: "Supertrend", htf: "HTF bias", bos: "BOS", rsi_side: "RSI 50", ema200: "EMA200", not_extreme: t.side === -1 || (t.h4_bos === -1) ? "hindi oversold" : "hindi overbought", no_ob_block: "walang OB sa daan" }[k] || k])])}</div>`;
      b += nearHtml(nr, t.ok);
      b += readyNote("trend", t);
      if (t.ok) b += `<div class="ss-line small">entry ${fx(t.entry)} · SL ${fx(t.sl)} · TP ${fx(t.tp)} · 1:${t.rr}</div>`;
      const tside = h4 || t.side || 0;
      out.push(row("trend", "TREND", tside ? side(tside) : `<span class="muted">${tcon.bos_h4 ? "walang " + htf + " BOS" : "walang trend"}</span>`, b, nr.progress ?? 0, t.ok));
    }
    const p = S.pullback;
    if (p) {
      const m = p.met || {}, names = { rsi: p.side === -1 ? "Overbought" : "Oversold", mfi: "MFI", doji: "Doji", ob: `OB ${(D.settings && D.settings.pullback && D.settings.pullback.ob_tf) || "M15"} hindi break`, stoch: "Stoch", cci: "CCI", wpr: "W%R", near_ema: "EMA", turn: "Bumabalik", bb_touch: "BB band", engulf: "Engulfing" }, nr = p.near || {};
      const pcfg = (D.settings && D.settings.pullback) || {};
      const keys = (p.on && p.on.length) ? p.on : Object.keys(pcfg.conditions || {}).filter((k) => pcfg.conditions[k]);
      const cnt = keys.filter((k) => m[k]).length;
      let b = `<div class="ss-line">${dots(keys.map((k) => [m[k], names[k]]))}</div>` + nearHtml(nr, p.ok);
      b += readyNote("pullback", p);
      if (p.ok) b += `<div class="ss-line small">entry ${fx(p.entry)} · SL ${fx(p.sl)} · TP ${fx(p.tp)} · 1:${p.rr}</div>`;
      else if ((p.missing || []).includes("volatile")) b += `<div class="ss-line small c-warn">VOLATILE: naka-hold</div>`;
      out.push(row("pullback", "PULLBACK", p.side ? `${side(p.side)} <b>${cnt}/${keys.length}</b>` : '<span class="muted">walang trend</span>', b, nr.progress ?? Math.round(cnt / Math.max(1, keys.length) * 100), p.ok));
    }
    const n = D.news || {};
    const now = Date.now() / 1000;
    let nb, nst, npct = 0, nok = false;
    if (n.next) {
      const secs = n.next.t - now;
      nst = `<span class="${secs < 900 ? "c-bear" : "c-warn"} mono">${secs > 0 ? cd(secs) : "kalalabas"}</span>`;
      npct = secs <= 0 ? 100 : Math.max(0, Math.min(100, Math.round(100 - secs / 36)));      // last hour fills the bar
      nok = secs <= (n.place_before_sec || 120) && secs > -300;
      nb = `<div class="ss-line small"><b>${esc(n.next.currency)}</b> ${esc(n.next.title)} <span class="muted">${new Date(n.next.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></div>`;
    } else {
      nst = '<span class="muted">walang news</span>';
      nb = `<div class="ss-line small muted">${esc(n.error || "walang high-impact sa filter mo")}</div>`;
    }
    nb += `<div class="ss-line small">${n.available ? `NEWS TRADE <b class="${n.enabled ? "c-warn" : "muted"}">${n.enabled ? "ON" : "OFF"}</b>${n.enabled && n.place_before_sec ? ` · pending ${Math.round(n.place_before_sec / 6) / 10}m bago` : ""}` : '<span class="muted">NEWS TRADE: wala sa mode na ito</span>'}</div>`;
    const nBadge = n.available ? `<span class="ss-auto ${n.enabled && a.armed ? "live" : ""}">${n.enabled ? "AUTO" + (a.armed ? "" : " (off)") : "MANUAL"}</span>` : "";
    out.push(row("", "NEWS", nst, nb, npct, nok, nBadge));
    box.innerHTML = out.join("");
  }
  async function load() {
    try { const r = await fetch("/api/strategies", { cache: "no-store" }); D = await r.json(); } catch (e) { return; }
    window.rgStratD = D;
    render();
  }
  window.rgStratReload = load;
  // ---- STRATEGY PANEL: edit the values and add / remove the indicators (conditions) of one strategy
  let edit = null;
  function openEdit(n) {
    if (!D || !D.settings || !D.catalog) return;
    const cfg = JSON.parse(JSON.stringify(D.settings[n]));
    edit = { n, cfg };
    let ov = document.getElementById("spEdit");
    if (!ov) { ov = document.createElement("div"); ov.id = "spEdit"; ov.className = "sp-edit-bg"; document.body.appendChild(ov); }
    ov.hidden = false;
    drawEdit();
  }
  function drawEdit() {
    const ov = document.getElementById("spEdit");
    if (!ov || !edit) return;
    const { n, cfg } = edit;
    const title = { trend: "TREND", pullback: "PULLBACK", conso: "CONSOLIDATION" }[n];
    const e = (D.strategies || {})[n] || {}, met = e.met || {};
    const cat = D.catalog[n] || [];
    const on = cat.filter((c) => cfg.conditions[c.key]), off = cat.filter((c) => !cfg.conditions[c.key]);
    const mark = (k) => met[k] === true ? '<i class="c-bull">✓</i>' : met[k] === false ? '<i class="c-bear">✗</i>' : '<i class="muted">•</i>';
    const P = (window.rgStratParams || {})[n] || [];
    const params = P.map(([k, lab, step]) => Array.isArray(step)
      ? `<label class="pf"><span>${esc(lab)}</span><select data-ep="${k}">${step.map((o) => `<option ${cfg[k] === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>`
      : `<label class="pf"><span>${esc(lab)}</span><input type="number" step="${step}" data-ep="${k}" value="${cfg[k]}"></label>`).join("");
    ov.innerHTML = `<div class="sp-edit">
      <div class="spe-h"><b>⚙ STRATEGY PANEL · ${title}</b><span class="muted small">live sa ${esc(D.tf)} · papasok kapag LAHAT ng naka-ON ay ✓</span><button class="tool mini" data-eclose>✕</button></div>
      <div class="spe-sec">NAKA-ON (${on.length}) <span class="muted">— ito ang setup mo</span></div>
      ${on.map((c) => `<div class="spe-it">${mark(c.key)}<span>${esc(c.label)}</span><button class="btn-sm" data-eoff="${c.key}" ${on.length <= 1 ? "disabled title='kailangan ng kahit isa'" : ""}>✕ alisin</button></div>`).join("")}
      <div class="spe-sec">＋ MAGDAGDAG NG INDICATOR</div>
      ${off.map((c) => `<div class="spe-it off">${mark(c.key)}<span>${esc(c.label)}</span><button class="btn-sm" data-eon="${c.key}">＋ idagdag</button></div>`).join("") || '<div class="muted small">nasa setup na lahat</div>'}
      <div class="spe-sec">VALUES</div>
      <div class="sys-form">${params}${n === "pullback" ? `<label class="ck"><input type="checkbox" data-ep="block_volatile" ${cfg.block_volatile ? "checked" : ""}> Huwag pumasok kapag VOLATILE</label>` : ""}</div>
      <div class="spe-foot"><span class="muted small">Magiging <b>CUSTOM</b> ang profile kapag na-save. Pumili ulit ng SCALP / NORMAL / AGGRESSIVE para ibalik.</span>
        <button class="btn-sm" data-eclose>Cancel</button><button class="btn-sm primary" data-esave>Save</button></div></div>`;
  }
  function readEditInputs() {
    if (!edit) return;
    document.querySelectorAll("#spEdit [data-ep]").forEach((el) => {
      const k = el.dataset.ep;
      edit.cfg[k] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : parseFloat(el.value);
    });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    const g = t.closest("[data-sedit]");
    if (g) { openEdit(g.dataset.sedit); return; }
    if (!edit || !t.closest("#spEdit")) return;
    if (t.closest("[data-eclose]") || t.id === "spEdit") { document.getElementById("spEdit").hidden = true; edit = null; return; }
    const offK = t.closest("[data-eoff]"), onK = t.closest("[data-eon]");
    if (offK || onK) { readEditInputs(); edit.cfg.conditions[(offK || onK).dataset[offK ? "eoff" : "eon"]] = !!onK; drawEdit(); return; }
    if (t.closest("[data-esave]")) {
      readEditInputs();
      const { n, cfg } = edit;
      const body = { [n]: Object.assign({}, cfg, { required: Object.keys(cfg.conditions).filter((k) => cfg.conditions[k]) }) };
      delete body[n].auto;                                 // AUTO is switched with ○ / ● only
      const r = await fetch("/api/strategies/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((x) => x.json()).catch(() => ({ ok: false }));
      document.getElementById("spEdit").hidden = true; edit = null;
      if (!r.ok && window.rgModal) window.rgModal.show("Strategy panel", "<p class='c-bear'>Hindi na-save.</p>", "", null);
      load();
    }
  });
  // click MANUAL / AUTO on a strategy: switch its AUTO flag (turning it ON asks first)
  document.addEventListener("click", async (e) => {
    const b = e.target.closest && e.target.closest("[data-stauto]");
    if (!b || !D) return;
    const n = b.dataset.stauto, on = !(D.auto.strategies || {})[n];
    const title = { trend: "TREND", pullback: "PULLBACK", conso: "CONSOLIDATION" }[n];
    const save = async () => {
      if (window.rgModal) window.rgModal.hide();
      await fetch("/api/strategies/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [n]: { auto: on } }) }).catch(() => null);
      load();
    };
    if (on && window.rgModal) {
      window.rgModal.show(`I-AUTO ang ${title}?`, `<p>Papasukan ng AUTO TRADE ang <b>${title}</b> tuwing mag-<b>READY</b> ito sa pagsara ng ${esc(D.tf)} bar${D.auto.enabled ? "" : " (kapag naka-ON ang AUTO TRADE)"}${D.auto.armed ? "" : " at naka-ARM"}.</p>
        <p class="muted">Dadaan pa rin sa lahat ng gate (risk, session, news, max trades). Hindi nito papasukan ang READY na kanina pa.</p>`, "I-AUTO", save, !D.auto.paper);
    } else save();
  });
  function tick() {
    clearTimeout(timer);
    const v = $("#view-overview"), c = $("#stratSide");
    if (v && !v.hidden && c && c.offsetParent !== null) load();
    timer = setTimeout(tick, 5000);
  }
  setInterval(() => { const v = $("#view-overview"); if (D && v && !v.hidden) render(); }, 1000);   // news countdown
  tick();
})();

// ============================================================================ LAYOUT: choose / order the Market Overview cards
// Saved in this browser only (localStorage). Nothing to do with trading.
(function () {
  const $ = (q) => document.querySelector(q);
  const KEY = "reygun_layout_v2";
  const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NAMES = { stratSide: "STRATEGIES (4 strategy)", modeCard: "MANUAL / AUTO + accounts", tradeCard: "Trade (BUY / SELL)",
    groupsCard: "SuperChart trades", verdictCard: "Analyser verdict", regCard: "Market regime", sigStatCard: "Signal score rate",
    imCard: "Intermarket", liqCard: "Liquidity & zones", stratCard: "Strategies (plugins)", posCard: "All MT5 positions",
    sigCard: "Signals (ilalim ng chart)", logCard: "Event log", actInds: "Naka-ON na indicators (ilalim ng chart)", kpiCards: "Mga numero sa itaas (price, score…)" };
  let cfg = { hidden: null, order: [] };
  try { cfg = Object.assign(cfg, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) { /* private window etc. */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch (e) { /* ignore */ } };
  const right = $(".rightcol");
  // give every card a stable key
  [...right.children].forEach((el, k) => {
    if (!el.dataset.lay) el.dataset.lay = el.id || ((el.querySelector("h3") || {}).textContent || "card" + k).trim().split(/\s+/).slice(0, 2).join(" ");
  });
  const under = [["sigCard", $("#sigCard")], ["logCard", document.querySelector("#view-overview .log-card")], ["actInds", $("#actInds")], ["kpiCards", document.querySelector("section.cards")]];
  under.forEach(([k, el]) => { if (el) el.dataset.lay = k; });
  const label = (k) => NAMES[k] || k;
  // default: the right side shows ONLY the 4 strategies (no scrolling); add others back with ⚙ LAYOUT
  if (!Array.isArray(cfg.hidden)) cfg.hidden = [...right.children].map((e) => e.dataset.lay).filter((k) => k !== "stratSide");
  function apply() {
    const items = [...right.children];
    const ord = cfg.order.filter((k) => items.some((e) => e.dataset.lay === k));
    items.forEach((e) => { if (!ord.includes(e.dataset.lay)) ord.push(e.dataset.lay); });
    // STRATEGIES first unless you moved it
    if (!cfg.order.length) { ord.splice(ord.indexOf("stratSide"), 1); ord.unshift("stratSide"); }
    ord.forEach((k) => { const e = items.find((x) => x.dataset.lay === k); if (e) right.appendChild(e); });
    cfg.order = ord;
    // a <style> rule, not a class: other code rewrites some cards' className
    let st = document.getElementById("layStyle");
    if (!st) { st = document.createElement("style"); st.id = "layStyle"; document.head.appendChild(st); }
    st.textContent = cfg.hidden.map((k) => `[data-lay="${CSS.escape(k)}"]{display:none !important}`).join("\n");
  }
  let pop = null;
  function draw() {
    if (!pop) return;
    const item = (k, i, n, mov) => `<div class="lay-it"><input type="checkbox" data-k="${esc(k)}" ${cfg.hidden.includes(k) ? "" : "checked"}><span>${esc(label(k))}</span>
      ${mov ? `<button data-up="${i}" ${i === 0 ? "disabled" : ""}>↑</button><button data-dn="${i}" ${i === n - 1 ? "disabled" : ""}>↓</button>` : ""}</div>`;
    pop.innerHTML = `<h4>MARKET OVERVIEW LAYOUT</h4><div class="muted small">✓ = makikita · ↑↓ = pagkakasunod sa kanan. Naka-save sa browser na ito.</div>
      <div class="lay-sec">KANAN</div>${cfg.order.map((k, i) => item(k, i, cfg.order.length, true)).join("")}
      <div class="lay-sec">IBA PA</div>${under.filter(([, e]) => e).map(([k]) => item(k, 0, 0, false)).join("")}
      <div style="display:flex;gap:8px;margin-top:10px"><button class="btn-sm" id="layReset">Reset</button><button class="btn-sm" id="layAll">Ipakita lahat</button><span style="flex:1"></span><button class="btn-sm" id="layClose">Close</button></div>`;
  }
  function open() {
    if (pop) { close(); return; }
    pop = document.createElement("div");
    pop.className = "lay-pop";
    document.body.appendChild(pop);
    draw();
    pop.addEventListener("click", (e) => {
      const t = e.target;
      if (t.dataset.k !== undefined) {
        cfg.hidden = cfg.hidden.filter((x) => x !== t.dataset.k);
        if (!t.checked) cfg.hidden.push(t.dataset.k);
      } else if (t.dataset.up !== undefined || t.dataset.dn !== undefined) {
        const i = +(t.dataset.up ?? t.dataset.dn), j = t.dataset.up !== undefined ? i - 1 : i + 1;
        [cfg.order[i], cfg.order[j]] = [cfg.order[j], cfg.order[i]];
      } else if (t.id === "layReset") { cfg = { hidden: [...right.children].map((x) => x.dataset.lay).filter((k) => k !== "stratSide"), order: [] }; }
      else if (t.id === "layAll") { cfg.hidden = []; } else if (t.id === "layClose") { close(); return; }
      else return;
      save(); apply(); draw();
      window.dispatchEvent(new Event("resize"));            // the chart re-fits the space
    });
  }
  function close() { if (pop) { pop.remove(); pop = null; } }
  $("#btnLayout").addEventListener("click", (e) => { e.stopPropagation(); open(); });
  document.addEventListener("click", (e) => { if (pop && !e.composedPath().includes(pop) && e.target.id !== "btnLayout") close(); });
  apply();
})();
