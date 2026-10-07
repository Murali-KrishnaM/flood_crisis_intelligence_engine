/* Crisis Intelligence Engine — dashboard front end.
 * Vanilla JS + SVG, no external dependencies. Reads only the JSON API; every value shown
 * comes from the real project files. Internal research prototype, not an official warning. */
(() => {
"use strict";

const DAY = 86400000;
const SEV = { LOW: "#3fa37a", MODERATE: "#d4a537", HIGH: "#e8803a", CRITICAL: "#d9453d", NO_DATA: "#6b7685", UNRATED: "#6b7685" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const RANGE_DAYS = { "7D": 7, "30D": 30, "90D": 90, "1Y": 365 };

const state = {
  summary: null, risk: null, rain: null, timeline: null, policy: null, system: null,
  idx: 0, playing: false, timer: null, stepTimer: null, stepToken: 0, stepCache: new Map(),
  riskRange: "90D", rainRange: "30D", riskAuto: false, lastStations: null,
};

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
};
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const dayMs = (iso) => Date.parse(iso + "T00:00:00Z");
const fmtDate = (iso, year = true) => {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${+d} ${MONTHS[+m - 1]}${year ? " " + y : ""}`;
};
const fmtScore = (v) => (v == null ? "—" : Number(v).toFixed(3));
const fmtVal = (v) => (v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 3 }));
const humanize = (s) => (s ? String(s).replace(/_/g, " ").replace(/;/g, " ·") : "—");
const sevColor = (s) => SEV[s] || SEV.NO_DATA;

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-JSON error body */ }
  if (!r.ok) throw new Error((body && body.error) || `HTTP ${r.status}`);
  return body;
}
const safe = (url) => getJSON(url).catch((e) => ({ available: false, error: String(e.message || e) }));

/* ------------------------------------------------------------------ pipeline + header */
function renderPipeline() {
  const host = $("pipeline");
  host.textContent = "";
  const stages = (state.system && state.system.stages) || [];
  if (!stages.length) host.append(el("span", { class: "sub", text: "System status unavailable" }));
  for (const s of stages) {
    host.append(el("div", { class: `pstage s-${s.status.toLowerCase()}`, title: s.detail || "" },
      el("span", { class: "dot" }), el("b", { text: s.name }), el("span", { class: "st", text: s.status })));
  }
}

function renderTop() {
  const s = state.summary;
  $("asof").textContent = s && s.available
    ? `Data as of ${fmtDate(s.as_of_date)} · historical dataset, not live` : "No risk data available";
}

/* ------------------------------------------------------------------ risk + trigger panels */
function renderRiskStatic() {
  const t = state.risk;
  const ticks = $("meter-ticks");
  ticks.textContent = "";
  const bands = (t && t.severity_bands) || [];
  for (const b of bands) {
    if (b.from <= 0) continue;
    const tk = el("div", { class: "tick" }, el("span", { text: b.from.toFixed(2) }));
    tk.style.left = `${b.from * 100}%`;
    ticks.append(tk);
  }
  const mode = $("risk-mode");
  if (t && t.model_mode === "fallback") { mode.textContent = t.label; mode.className = "chip amber"; }
  else if (t && t.available) { mode.textContent = "XGBoost p_xgboost"; mode.className = "chip"; }
  else { mode.textContent = "NO DATA"; mode.className = "chip"; }
}

function renderRisk(i) {
  const t = state.timeline;
  if (!t || !t.available) {
    $("risk-score").textContent = "—";
    $("risk-sev").textContent = "NO DATA";
    $("risk-date").textContent = (t && (t.message || t.error)) || "No risk data";
    $("meter-fill").style.width = "0";
    $("trig-state").textContent = "UNAVAILABLE"; $("trig-state").className = "trig-state";
    return;
  }
  const score = t.risk_score[i], sev = t.severity[i], c = sevColor(sev);
  $("risk-score").textContent = fmtScore(score);
  $("risk-score").style.color = score == null ? "var(--muted)" : c;
  const chip = $("risk-sev");
  chip.textContent = sev.replace("_", " ");
  chip.style.setProperty("--c", c);
  $("risk-date").textContent = `Feature date ${fmtDate(t.dates[i])}`;
  const fill = $("meter-fill");
  fill.style.width = `${score == null ? 0 : Math.max(0, Math.min(1, score)) * 100}%`;
  fill.style.background = c;
  $("risk-target").textContent = t.target_date[i] ? `Research proxy for ${fmtDate(t.target_date[i])} (t+1)` : "";
  const note = $("risk-note");
  if (t.model_mode === "fallback" && t.note) { note.textContent = t.note; note.classList.remove("hidden"); }
  else if (t.note) { note.textContent = t.note; note.classList.remove("hidden"); }
  else note.classList.add("hidden");
  const badges = $("risk-badges");
  badges.textContent = "";
  const split = t.split[i];
  if (split) badges.append(el("span", { class: "chip" + (split === "test" ? "" : " amber"), text: split === "test" ? "TEST PERIOD" : `${split.toUpperCase()} · IN-SAMPLE` }));
  renderTrigger(i);
}

function renderTrigger(i) {
  const t = state.timeline, cfg = (state.risk && state.risk.thresholds) || (state.summary && state.summary.thresholds);
  const st = t.trigger_state[i];
  const stEl = $("trig-state");
  stEl.textContent = st;
  stEl.className = "trig-state" + (st === "ACTIVE" ? " active" : st === "CLEAR" ? " clear" : "");
  const need = t.persistence_days || (cfg && cfg.persistence_days) || 0;
  const count = t.consecutive_high_count[i];
  const dots = $("trig-dots");
  dots.textContent = "";
  for (let k = 0; k < need; k++) dots.append(el("i", { class: count != null && k < count ? (count >= need ? "on full" : "on") : "" }));
  $("trig-count").textContent = st === "UNAVAILABLE"
    ? "Trigger history file unavailable" : `Consecutive days at or above threshold: ${count ?? 0} / ${need}`;
  $("trig-reason").textContent = st === "UNAVAILABLE" ? "" : humanize(t.trigger_reason[i]);
  $("trig-config").textContent = cfg
    ? `Config: score ≥ ${cfg.trigger_threshold} on ${cfg.persistence_days} consecutive observed days (trigger_config.json). Internal research trigger.`
    : "trigger_config.json unavailable";
  const h = state.summary && state.summary.history;
  $("trig-history").textContent = h && h.n_rows
    ? `Full history: ${h.days_at_or_above_trigger_threshold ?? "—"} days ≥ threshold · ${h.trigger_active_days ?? "—"} trigger-active days · peak ${fmtScore(h.peak_score)} on ${fmtDate(h.peak_date)}`
    : "";
}

/* ------------------------------------------------------------------ stations / reservoirs / map */
function renderStations(stations, date) {
  state.lastStations = stations;
  $("stations-date").textContent = date ? fmtDate(date) : "—";
  const host = $("stations");
  host.textContent = "";
  for (const s of stations || []) {
    const obs = s.availability === "OBSERVED";
    let sub = "";
    if (!obs && s.last_observed_date) sub = `last observed ${fmtDate(s.last_observed_date)} (${s.days_since_observation} d earlier)`;
    else if (s.availability === "NO DATA") sub = "no observations in source";
    host.append(el("div", { class: "srow" },
      el("div", { class: "sname" }, s.name, sub ? el("span", { class: "sub", text: sub }) : null),
      el("div", { class: "sval", text: fmtVal(s.value) }),
      el("span", { class: "av " + (obs ? "obs" : "none"), text: obs ? "OBSERVED" : s.availability === "NO DATA" ? "NO DATA" : "NO OBS" })));
  }
  if (!(stations || []).length) host.append(el("div", { class: "sub", text: "Station data unavailable" }));
  renderMap(stations);
}

function renderReservoirs(snap) {
  const host = $("reservoirs");
  host.textContent = "";
  $("reservoir-asof").textContent = snap && snap.as_of ? `as of ${fmtDate(snap.as_of)}` : "latest";
  if (!snap || !snap.available) {
    host.append(el("div", { class: "empty", text: (snap && snap.error) || "Reservoir data unavailable" }));
    return;
  }
  for (const r of snap.reservoirs) {
    const top = el("div", { class: "res-top" }, el("b", { text: r.name }));
    if (r.available) {
      const age = r.age_days > 0 ? ` · ${r.age_days} d before` : "";
      top.append(el("span", { class: "sub", text: `obs ${r.observation_time}${age}` }));
      host.append(el("div", { class: "res" }, top, el("div", { class: "res-vals" },
        ...[["Level", r.water_level], ["Storage", r.storage], ["Inflow", r.inflow], ["Outflow", r.outflow]]
          .map(([k, v]) => el("div", {}, el("i", { text: k }), el("span", { text: fmtVal(v) }))))));
    } else {
      host.append(el("div", { class: "res" }, top, el("div", { class: "none", text: "No observation on or before this date" })));
    }
  }
}

function renderMap(stations) {
  const host = $("map");
  host.textContent = "";
  const pts = (stations || []).filter((s) => s.lat != null && s.lon != null);
  if (pts.length < 2) {
    host.append(el("div", { class: "empty", text: pts.length ? "Only one station has coordinates" : "Station coordinates unavailable (stations.csv)" }));
    return;
  }
  const W = host.clientWidth, H = host.clientHeight;
  if (W < 60 || H < 60) return;
  const kx = Math.cos((pts.reduce((a, p) => a + p.lat, 0) / pts.length) * Math.PI / 180);
  const xs = pts.map((p) => p.lon * kx), ys = pts.map((p) => p.lat);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 40, scale = Math.min((W - 2 * pad) / Math.max(maxX - minX, 1e-6), (H - 2 * pad) / Math.max(maxY - minY, 1e-6));
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` });
  for (let gx = 0; gx < W; gx += 40) svg.append(svgEl("line", { x1: gx, y1: 0, x2: gx, y2: H, stroke: "#142030", "stroke-width": 1 }));
  for (let gy = 0; gy < H; gy += 40) svg.append(svgEl("line", { x1: 0, y1: gy, x2: W, y2: gy, stroke: "#142030", "stroke-width": 1 }));
  const north = svgEl("g", { transform: `translate(${W - 18},22)` });
  north.append(svgEl("path", { d: "M0,-9 L5,5 L0,2 L-5,5 Z", fill: "#5c6b7d" }));
  const nt = svgEl("text", { x: 0, y: 18, "text-anchor": "middle", fill: "#5c6b7d", "font-size": 9, "font-family": "monospace" });
  nt.textContent = "N"; north.append(nt); svg.append(north);
  pts.forEach((p, k) => {
    const x = W / 2 + (xs[k] - cx) * scale, y = H / 2 - (ys[k] - cy) * scale, obs = p.availability === "OBSERVED";
    svg.append(svgEl("circle", { cx: x, cy: y, r: 6, fill: obs ? "#6aa7d8" : "none", stroke: obs ? "#6aa7d8" : "#d4a537", "stroke-width": 1.6, "stroke-dasharray": obs ? "" : "2 2" }));
    const right = x < W * 0.6, tx = right ? x + 10 : x - 10, anchor = right ? "start" : "end";
    const a = svgEl("text", { x: tx, y: y - 1, "text-anchor": anchor, fill: "#dbe3ec", "font-size": 11, "font-family": "Segoe UI, sans-serif" });
    a.textContent = p.name; svg.append(a);
    const b = svgEl("text", { x: tx, y: y + 12, "text-anchor": anchor, fill: obs ? "#8a99ab" : "#d4a537", "font-size": 10, "font-family": "monospace" });
    b.textContent = obs ? fmtVal(p.value) : "no obs"; svg.append(b);
  });
  host.append(svg);
}

/* ------------------------------------------------------------------ charts */
function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function segments(xs, ys) {
  const segs = []; let cur = [];
  for (let i = 0; i < ys.length; i++) {
    if (ys[i] == null || (cur.length && xs[i] - xs[cur[cur.length - 1]] > 1.5 * DAY)) { if (cur.length) segs.push(cur); cur = []; }
    if (ys[i] != null) cur.push(i);
  }
  if (cur.length) segs.push(cur);
  return segs;
}

function nearest(xs, t) {
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] < t) lo = mid; else hi = mid; }
  return Math.abs(xs[lo] - t) <= Math.abs(xs[hi] - t) ? lo : hi;
}

function lineChart(host, cfg) {
  host.textContent = "";
  const W = host.clientWidth, H = host.clientHeight;
  if (W < 60 || H < 60) return;
  if (!cfg.x.length) { host.append(el("div", { class: "empty", text: cfg.empty || "No data in this range" })); return; }
  const m = { l: 46, r: 14, t: 8, b: 22 };
  const xs = cfg.x.map(dayMs);
  let x0 = cfg.xMin ?? xs[0], x1 = cfg.xMax ?? xs[xs.length - 1];
  if (x1 <= x0) { x0 -= DAY; x1 += DAY; }
  const vals = cfg.series.flatMap((s) => s.y).filter((v) => v != null);
  const y0 = cfg.yMin ?? 0;
  let y1 = cfg.yMax ?? niceMax((vals.length ? Math.max(...vals) : 1) * 1.12);
  if (y1 <= y0) y1 = y0 + 1;
  const px = (t) => m.l + ((t - x0) / (x1 - x0)) * (W - m.l - m.r);
  const py = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` });
  const axis = svgEl("g", { class: "axis" });
  for (let k = 0; k <= 4; k++) {
    const v = y0 + ((y1 - y0) * k) / 4, y = py(v);
    axis.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: "#1a2634", "stroke-width": 1 }));
    const tx = svgEl("text", { x: m.l - 6, y: y + 3, "text-anchor": "end" });
    tx.textContent = (cfg.yFmt || ((q) => String(Number(q.toPrecision(3)))))(v); axis.append(tx);
  }
  const span = (x1 - x0) / DAY;
  for (let k = 0; k <= 5; k++) {
    const t = x0 + ((x1 - x0) * k) / 5, iso = new Date(t).toISOString().slice(0, 10);
    const tx = svgEl("text", { x: px(t), y: H - 6, "text-anchor": k === 0 ? "start" : k === 5 ? "end" : "middle" });
    tx.textContent = span > 400 ? `${MONTHS[+iso.slice(5, 7) - 1]} ${iso.slice(2, 4)}` : fmtDate(iso, false); axis.append(tx);
  }
  svg.append(axis);
  for (const h of cfg.hlines || []) {
    if (h.y < y0 || h.y > y1) continue;
    svg.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: py(h.y), y2: py(h.y), stroke: h.color, "stroke-width": 1, "stroke-dasharray": "4 3", opacity: 0.8 }));
    const lt = svgEl("text", { x: W - m.r - 2, y: py(h.y) - 3, "text-anchor": "end", fill: h.color, "font-size": 9.5, "font-family": "monospace" });
    lt.textContent = h.label; svg.append(lt);
  }
  if (cfg.marker) {
    const mt = dayMs(cfg.marker);
    if (mt >= x0 && mt <= x1) svg.append(svgEl("line", { x1: px(mt), x2: px(mt), y1: m.t, y2: H - m.b, stroke: "#6aa7d8", "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0.7 }));
  }
  for (const s of cfg.series) {
    for (const seg of segments(xs, s.y)) {
      if (seg.length === 1) { svg.append(svgEl("circle", { cx: px(xs[seg[0]]), cy: py(s.y[seg[0]]), r: 2, fill: s.color })); continue; }
      const pts = seg.map((i) => `${px(xs[i]).toFixed(1)},${py(s.y[i]).toFixed(1)}`);
      if (s.fill) svg.append(svgEl("polygon", { points: `${px(xs[seg[0]]).toFixed(1)},${py(y0)} ${pts.join(" ")} ${px(xs[seg[seg.length - 1]]).toFixed(1)},${py(y0)}`, fill: s.color, opacity: 0.12 }));
      svg.append(svgEl("polyline", { points: pts.join(" "), fill: "none", stroke: s.color, "stroke-width": s.width || 1.6, "stroke-linejoin": "round", opacity: s.opacity ?? 1 }));
    }
  }
  const cross = svgEl("line", { y1: m.t, y2: H - m.b, stroke: "#8a99ab", "stroke-width": 1, opacity: 0, "pointer-events": "none" });
  const dots = cfg.series.map((s) => { const d = svgEl("circle", { r: 3.5, fill: s.color, opacity: 0, "pointer-events": "none" }); svg.append(d); return d; });
  svg.append(cross);
  const tip = el("div", { class: "tip" });
  tip.style.display = "none";
  const overlay = svgEl("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
  overlay.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const t = x0 + ((ev.clientX - rect.left - m.l) / (W - m.l - m.r)) * (x1 - x0);
    const i = nearest(xs, t), x = px(xs[i]);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("opacity", 0.6);
    cfg.series.forEach((s, k) => {
      if (s.y[i] == null) { dots[k].setAttribute("opacity", 0); return; }
      dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", py(s.y[i])); dots[k].setAttribute("opacity", 1);
    });
    tip.replaceChildren();
    for (const line of cfg.tip(i)) tip.append(typeof line === "string" ? el("div", { text: line }) : el("div", {}, `${line.k} `, el("b", { text: line.v })));
    tip.style.display = "block";
    const tw = tip.offsetWidth;
    tip.style.left = `${x + 14 + tw > W ? x - tw - 14 : x + 14}px`;
    tip.style.top = `${m.t + 4}px`;
  });
  overlay.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.setAttribute("opacity", 0); dots.forEach((d) => d.setAttribute("opacity", 0)); });
  svg.append(overlay);
  host.append(svg, tip);
}

function selectedDate() {
  const t = state.timeline;
  return t && t.available ? t.dates[state.idx] : null;
}

function windowIdx(dates, range, endIso) {
  if (range === "FULL") return { idxs: dates.map((_, i) => i), xMin: undefined, xMax: undefined };
  const days = RANGE_DAYS[range], endMs = dayMs(endIso || dates[dates.length - 1]);
  const idxs = [];
  dates.forEach((d, i) => { const t = dayMs(d); if (t <= endMs && t > endMs - days * DAY) idxs.push(i); });
  return { idxs, xMin: endMs - (days - 1) * DAY, xMax: endMs };
}

function drawRiskChart() {
  const h = state.risk, host = $("risk-chart");
  if (!h || !h.available) {
    host.textContent = ""; host.append(el("div", { class: "empty", text: (h && (h.message || h.error)) || "Risk history unavailable" })); return;
  }
  $("risk-chart-title").textContent = h.model_mode === "fallback" ? `${h.label} over time` : "XGBoost risk score over time";
  const sel = selectedDate(), w = windowIdx(h.dates, state.riskRange, sel);
  const hl = [];
  const th = h.thresholds;
  if (th) {
    hl.push({ y: th.trigger_threshold, label: `trigger ≥ ${th.trigger_threshold}`, color: "#e8803a" });
    if (th.critical_threshold !== th.trigger_threshold) hl.push({ y: th.critical_threshold, label: `critical ≥ ${th.critical_threshold}`, color: "#d9453d" });
  }
  lineChart(host, {
    x: w.idxs.map((i) => h.dates[i]), xMin: w.xMin, xMax: w.xMax, yMin: 0, yMax: state.riskAuto ? undefined : 1,
    yFmt: (v) => v.toFixed(2), marker: sel, hlines: hl, empty: "No risk scores in this range",
    series: [{ name: "risk", y: w.idxs.map((i) => h.risk_score[i]), color: "#6aa7d8", fill: true, width: 1.6 }],
    tip: (k) => {
      const i = w.idxs[k], lines = [fmtDate(h.dates[i]), { k: "Risk score", v: fmtScore(h.risk_score[i]) }, { k: "Severity", v: h.severity[i] }];
      if (h.trigger_state[i] !== "UNAVAILABLE") lines.push({ k: "Trigger", v: h.trigger_state[i] });
      if (h.split[i]) lines.push(h.split[i] === "test" ? "test period" : `${h.split[i]} period (in-sample)`);
      return lines;
    },
  });
}

function drawRainChart() {
  const r = state.rain, host = $("rain-chart");
  if (!r || !r.available) {
    host.textContent = ""; host.append(el("div", { class: "empty", text: (r && r.error) || "Rainfall data unavailable" })); return;
  }
  const w = windowIdx(r.dates, state.rainRange, selectedDate());
  const at = (arr) => w.idxs.map((i) => arr[i]);
  lineChart(host, {
    x: w.idxs.map((i) => r.dates[i]), xMin: w.xMin, xMax: w.xMax, yMin: 0, marker: selectedDate(), empty: "No rainfall observations in this range",
    series: [{ name: "max", y: at(r.max), color: "#5c6b7d", width: 1, opacity: 0.9 }, { name: "mean", y: at(r.mean), color: "#6aa7d8", fill: true, width: 1.8 }],
    tip: (k) => {
      const i = w.idxs[k];
      if (r.mean[i] == null) return [fmtDate(r.dates[i]), "No station observation"];
      return [fmtDate(r.dates[i]), { k: "Station mean", v: fmtVal(r.mean[i]) }, { k: "Station max", v: fmtVal(r.max[i]) },
        { k: "Station min", v: fmtVal(r.min[i]) }, `${r.station_count[i]} stations reporting`, "source rainfall value"];
    },
  });
}

function redrawCharts() { drawRiskChart(); drawRainChart(); }

/* ------------------------------------------------------------------ lower panels */
function renderPolicy() {
  const p = state.policy, host = $("policy-body");
  host.textContent = "";
  if (!p || p.error) { host.append(el("div", { class: "sub", text: (p && p.error) || "Policy status unavailable" })); return; }
  const chip = $("policy-chip");
  chip.textContent = `CORPUS ${p.corpus_status}`;
  host.append(el("div", { class: "sub", text: `${p.files_found} document files found in knowledge_base` }));
  host.append(el("ul", { class: "docs" }, ...p.documents.map((d) =>
    el("li", {}, el("span", { text: d.title }), el("span", { class: "mk" + (d.detected ? " yes" : ""), text: d.detected ? "file matched" : "not matched by name" })))));
  host.append(el("div", { class: "flow" }, ...p.stages.map((s) =>
    el("div", { class: `s-${s.status.toLowerCase()}` }, el("span", { text: s.name }), el("b", { text: s.status })))));
  host.append(el("div", { class: "note-box", text: p.note }));
  host.append(el("div", { class: "micro", text: p.detection_note }));
}

function renderSystem() {
  const s = state.system, host = $("system-body");
  host.textContent = "";
  if (!s || s.error) { host.append(el("div", { class: "sub", text: (s && s.error) || "System status unavailable" })); return; }
  const bad = s.stages.some((x) => ["OFFLINE", "MISSING", "DEGRADED"].includes(x.status));
  $("system-chip").textContent = bad ? "ATTENTION" : "NOMINAL";
  $("system-chip").className = "chip" + (bad ? " amber" : "");
  for (const g of s.stages) {
    host.append(el("div", { class: "stage" }, el("span", { class: "nm", text: g.name }),
      el("span", { class: `st s-${g.status.toLowerCase()}` }, el("span", { class: "dot" }), g.status), el("span", { class: "dt", text: g.detail })));
  }
  const rows = s.files.map((f) => el("tr", {}, el("td", { text: f.name }),
    el("td", { text: f.available ? "OK" : "MISSING" }), el("td", { text: f.rows ?? "—" }),
    el("td", { text: f.start ? `${f.start} → ${f.end}` : "—" })));
  host.append(el("table", { class: "files" }, el("tr", {}, el("th", { text: "File" }), el("th", { text: "State" }), el("th", { text: "Rows" }), el("th", { text: "Range" })), ...rows));
}

function renderEvidence() {
  const s = state.summary, host = $("evidence-body");
  host.textContent = "";
  if (!s || !s.recorded_metrics) { host.append(el("div", { class: "sub", text: "Unavailable" })); return; }
  const m = s.recorded_metrics;
  const items = [["ROC-AUC", m.roc_auc], ["PR-AUC", m.pr_auc], ["Precision", m.precision], ["Recall", m.recall], ["F1", m.f1], ["Brier", m.brier]];
  host.append(el("div", { class: "metrics" }, ...items.map(([k, v]) => el("div", {}, el("i", { text: k }), el("b", { text: Number(v).toFixed(3) })))));
  host.append(el("div", { class: "sub", text: `Test split: ${m.test_positive_days} positive proxy-target days.` }));
  host.append(el("div", { class: "micro", text: m.caveat }));
  host.append(el("div", { class: "micro", text: s.definition }));
  host.append(el("div", { class: "micro", text: s.disclaimer }));
}

/* ------------------------------------------------------------------ replay */
const hasTimeline = () => state.timeline && state.timeline.available && state.timeline.dates.length > 0;

function renderReplayLabel() {
  const t = state.timeline, n = t.dates.length, last = state.idx === n - 1;
  $("replay-date").textContent = `${fmtDate(t.dates[state.idx])} · ${state.idx + 1}/${n}`;
  const mode = $("replay-mode");
  mode.textContent = last ? "LATEST" : "REPLAY";
  mode.className = "mode" + (last ? " latest" : "");
}

function setIdx(i, delay = 0) {
  const n = state.timeline.dates.length;
  state.idx = Math.max(0, Math.min(n - 1, i));
  $("slider").value = state.idx;
  renderReplayLabel();
  renderRisk(state.idx);
  redrawCharts();
  clearTimeout(state.stepTimer);
  state.stepTimer = setTimeout(() => loadStep(state.idx), delay);
}

async function loadStep(i) {
  const date = state.timeline.dates[i], token = ++state.stepToken;
  let step = state.stepCache.get(date);
  if (!step) {
    step = await safe(`/api/replay?date=${date}`);
    if (step.available) state.stepCache.set(date, step);
  }
  if (token !== state.stepToken) return;           // a newer step superseded this one
  if (!step.available) {
    renderStations([], date); renderReservoirs({ available: false, error: step.error });
    return;
  }
  renderStations(step.stations, step.date);
  renderReservoirs({ available: step.reservoirs.available, reservoirs: step.reservoirs.reservoirs, as_of: step.reservoirs.as_of, error: step.reservoirs.error });
}

function syncButtons() {
  $("btn-play").disabled = state.playing;
  $("btn-pause").disabled = !state.playing;
}

function stopPlay() {
  clearInterval(state.timer); state.timer = null; state.playing = false; syncButtons();
}

function startPlay() {
  if (!hasTimeline() || state.playing) return;
  const n = state.timeline.dates.length;
  if (state.idx >= n - 1) setIdx(0);
  state.playing = true; syncButtons();
  state.timer = setInterval(() => {
    if (state.idx >= state.timeline.dates.length - 1) { stopPlay(); return; }
    setIdx(state.idx + 1, 120);
  }, Number($("speed").value));
}

function bindControls() {
  $("btn-play").addEventListener("click", startPlay);
  $("btn-pause").addEventListener("click", stopPlay);
  $("btn-prev").addEventListener("click", () => { if (hasTimeline()) { stopPlay(); setIdx(state.idx - 1); } });
  $("btn-next").addEventListener("click", () => { if (hasTimeline()) { stopPlay(); setIdx(state.idx + 1); } });
  $("btn-latest").addEventListener("click", () => { if (hasTimeline()) { stopPlay(); setIdx(state.timeline.dates.length - 1); } });
  $("slider").addEventListener("input", (e) => { if (hasTimeline()) { stopPlay(); setIdx(Number(e.target.value), 60); } });
  $("speed").addEventListener("change", () => { if (state.playing) { stopPlay(); startPlay(); } });
  for (const [id, key] of [["risk-range", "riskRange"], ["rain-range", "rainRange"]]) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      state[key] = b.dataset.r;
      $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      redrawCharts();
    });
  }
  $("risk-yscale").addEventListener("click", () => {
    state.riskAuto = !state.riskAuto;
    $("risk-yscale").textContent = state.riskAuto ? "Y: auto" : "Y: 0–1";
    drawRiskChart();
  });
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(e.target.tagName) || !hasTimeline()) return;
    if (e.key === "ArrowLeft") { stopPlay(); setIdx(state.idx - 1); }
    else if (e.key === "ArrowRight") { stopPlay(); setIdx(state.idx + 1); }
    else if (e.key === " ") { e.preventDefault(); state.playing ? stopPlay() : startPlay(); }
  });
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { redrawCharts(); renderMap(state.lastStations); }, 150); });
}

/* ------------------------------------------------------------------ init */
async function init() {
  bindControls();
  syncButtons();
  const [summary, risk, rain, timeline, policy, system] = await Promise.all(
    ["/api/summary", "/api/risk-history", "/api/rainfall", "/api/replay", "/api/policy-status", "/api/system-status"].map(safe));
  Object.assign(state, { summary, risk, rain, timeline, policy, system });
  renderPipeline(); renderTop(); renderRiskStatic(); renderPolicy(); renderSystem(); renderEvidence();
  if (hasTimeline()) {
    const slider = $("slider");
    slider.max = state.timeline.dates.length - 1;
    setIdx(state.timeline.dates.length - 1);
  } else {
    renderRisk(0); redrawCharts();
    renderStations(rain && rain.available ? rain.latest_by_station : [], null);
    renderReservoirs(await safe("/api/reservoirs"));
    ["btn-play", "btn-prev", "btn-next", "btn-latest", "btn-pause", "slider"].forEach((id) => { $(id).disabled = true; });
    $("replay-date").textContent = "Replay unavailable";
  }
}

document.addEventListener("DOMContentLoaded", init);
})();