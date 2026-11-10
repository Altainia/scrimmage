// The Test tab's run panel: the chart of the shown run's signals, the run compared with it faded, and the signal
// picker (test_run.js is the panel).

import { $, h } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { t } from "./i18n.js";
import { setTime } from "./test_run.js";
import { shownResult, CHART_H, PAD_L, PAD_R, PAD_T, TICKS_H, rp, runName, statusWord, outcomeText, end, start }
  from "./test_run_state.js";

// -- the chart -----------------------------------------------------------------------------------------------------
function available() {
  return rp.run ? Object.keys(rp.run.result.signals).filter((k) => k !== "t") : [];
}

// The test's own `chart`, else tilt and the driven joints: not the currents or the hook load, and not the drive
// wheels, whose turning says little about a mechanism (they are in the picker).
function defaultSignals() {
  const have = available();
  const chart = rp.state && rp.state.test && rp.state.test.chart;
  const listed = chart ? chart.filter((s) => have.includes(s)) : [];
  if (listed.length) return listed;
  const pal = rp.state && rp.state.palette;
  const wheels = new Set(((pal && pal.joints) || []).filter((j) => j.kind === "wheel").map((j) => j.name));
  return have.filter((s) => !s.endsWith(".current") && s !== "hook_load" && !wheels.has(s));
}

// The names in the test's chart this run has no signal for, but a clearance the next run measures (the picker offers
// it): the chart says so rather than leaving them out without a word.
function missingSignals() {
  const have = new Set([...available(), ...nextRunSignals()]);
  return (rp.chartOf() || []).filter((s) => !have.has(s));
}

export const shownSignals = () => (rp.picked ? available().filter((s) => rp.picked.has(s)) : defaultSignals());

// A signal's unit on the wire: the run's signals keep the units signals.csv and the MCP tools have (inches, psi,
// newtons, degrees), whatever the Units setting; the chart shows each in the setting's (shownUnit, toShown).
function unitOf(name) {
  if (name === "tilt") return "°";
  if (name.endsWith(".current")) return "A";
  if (name === "hook_load") return "N";
  if (name === "air.psi" || name === "air.working_psi") return "psi";
  if (name.endsWith(".extension")) return "in";
  if (name.startsWith("seated.") || name.startsWith("clearance.")) return "in";
  const sn = name.startsWith("sensor.") && ((rp.state && rp.state.palette && rp.state.palette.sensors) || [])
    .find((x) => `sensor.${x.name}` === name);
  if (sn) return sensorUnit(sn.kind);
  if (name === "partner.height" || name === "partner.moved") return "in";
  if (name === "partner.tilt") return "°";
  const pal = rp.state && rp.state.palette;
  const e = name.startsWith("element.") && pal && (pal.moving_elements || []).find((x) => `element.${x.name}` === name);
  if (e) return e.unit === "deg" ? "°" : e.unit;
  const j = pal && (pal.joints || []).find((x) => x.name === name);
  return j ? (j.unit === "deg" ? "°" : j.unit) : "";
}

const WIRE = { in: ["length", 0.0254], psi: ["pressure", 6894.757293168361], N: ["force", 1] };   // [kind, SI per one]
export const shownUnit = (name) => { const u = unitOf(name), w = WIRE[u]; return w ? units.name(w[0]) : u; };
// a function from the signal's wire value to the value shown (null stays null: no sample)
export function toShown(name) {
  const w = WIRE[unitOf(name)];
  return w ? (v) => (v == null ? v : units.toDisplay(v * w[1], w[0])) : (v) => v;
}

// What a sensor's chart reads in: a beam break is 1 while broken, a distance sensor inches, a color sensor the
// number of the color it sees (the palette's sensor_colors: 0 red, 1 blue, ...).
function sensorUnit(kind) {
  if (kind === "beam") return "1 = broken";
  if (kind === "distance") return "in";
  const colors = (rp.state && rp.state.palette && rp.state.palette.sensor_colors) || [];
  return colors.map((c, i) => `${i} ${c}`).join(", ");
}

// The clearances the picker offers for the next run: each field element's the shown run didn't measure.
function nextRunSignals() {
  const have = new Set(available());
  const pal = (rp.state && rp.state.palette) || {};
  return rp.run ? (pal.elements || []).map((e) => `clearance.${e}`).filter((s) => !have.has(s)) : [];
}

// A clearance for the next run in (or out of) the test's chart. The first one keeps the signals shown now as the
// default: a chart line lists every signal shown by default.
function toggleNextRun(s) {
  const chart = rp.chartOf();
  const now = chart ? [...chart] : shownSignals();
  const out = now.includes(s) ? now.filter((x) => x !== s) : [...now, s];
  rp.setChart(out.length ? out : null);
  renderSignals();
}

export function renderSignals() {
  const box = $("#test-signals");
  const shown = new Set(shownSignals());
  const next = nextRunSignals();
  const chart = new Set(rp.chartOf() || []);
  const key = JSON.stringify([available(), [...shown], next, next.filter((s) => chart.has(s))]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  const focused = document.activeElement && box.contains(document.activeElement) ? document.activeElement.dataset.signal : null;
  const button = (s, pressed, onclick, extra = {}) => h("button", {
    type: "button", class: "test-signal", "aria-pressed": String(pressed), "data-signal": s, text: s, onclick, ...extra });
  box.replaceChildren(...available().map((s) => button(s, shown.has(s), () => {
    const now = new Set(shownSignals());
    if (now.has(s)) now.delete(s); else now.add(s);
    rp.picked = now;
    renderSignals();
    drawChart();
  })), ...(next.length ? [h("span", { class: "test-signals-next", text: t("From the next run:") })] : []),
  ...next.map((s) => button(s, chart.has(s), () => toggleNextRun(s),
    { title: t("Measured from the next run: this puts it in the test's chart") })));
  const again = focused && [...box.querySelectorAll("[data-signal]")].find((b) => b.dataset.signal === focused);
  if (again) again.focus();
}

export function tokens() {
  const v = (n) => theme.color(n);
  return { run: v("--accent-text"), compare: v("--chart-compare"), grid: v("--line"), text: v("--muted"), mark: v("--text"),
    bg: v("--subtle"), font: getComputedStyle(document.documentElement).getPropertyValue("--font").trim() };
}

export function drawChart() {
  const box = $("#test-chart");
  const key = $("#test-chart-key");
  const outcomes = $("#test-chart-outcomes");
  if (!rp.run) { box.replaceChildren(); key.replaceChildren(); outcomes.replaceChildren(); outcomes.hidden = true; return; }
  const names = shownSignals();
  const cmp = rp.compare;
  const tEnd = Math.max(end(), cmp ? cmp.t_end || 0 : 0) || 1;
  let canvases = [...box.querySelectorAll("canvas")];
  if (canvases.map((c) => c.dataset.signal).join("|") !== names.join("|")) {
    box.replaceChildren(...names.map((name) => {
      const c = h("canvas", { class: "test-plot", "data-signal": name, role: "img" });
      c.addEventListener("pointerdown", (e) => {
        const r = c.getBoundingClientRect();
        const x = (e.clientX - r.left - PAD_L) / Math.max(1, r.width - PAD_L - PAD_R);
        setTime(start() + Math.max(0, Math.min(1, x)) * (Number(c.dataset.tend) - start()));
      });
      return h("div", { class: "test-plot-wrap" },
        h("div", { class: "test-plot-title", text: `${name}${shownUnit(name) ? ` (${shownUnit(name)})` : ""}` }), c);
    }));
    canvases = [...box.querySelectorAll("canvas")];
  }
  const tk = tokens();
  canvases.forEach((c) => plot(c, c.dataset.signal, cmp, tEnd, tk));
  key.replaceChildren(...[
    h("span", { class: "test-key" }, h("span", { class: "test-swatch test-swatch-run" }), runName(rp.run.id)),
    cmp ? h("span", { class: "test-key" }, h("span", { class: "test-swatch test-swatch-compare" }), t("{run} (faded)", { run: runName(cmp.id) })) : null,
    names.length ? null : h("span", { class: "muted", text: t("Pick something to chart above.") }),
    ...missingSignals().map((name) => h("span", { class: "muted test-chart-missing", "data-signal": name,
      text: t("This run has no signal called {name}", { name }) }))].filter(Boolean));
  // the two runs' outcomes side by side, so a compare reads without scrolling down to the Runs list
  const line = (id, r) => h("li", {}, h("span", { class: "test-chart-outcome-name", text: `${runName(id)} · ${statusWord(r.status)}: ` }),
    outcomeText(r));
  outcomes.replaceChildren(...(cmp ? [line(rp.run.id, rp.run.result), line(cmp.id, cmp)] : []));
  outcomes.hidden = !cmp;
}

function plot(canvas, name, cmp, tEnd, tk) {
  const W = Math.max(200, Math.floor(canvas.clientWidth || canvas.parentElement.clientWidth || 320));
  const H = CHART_H;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.height = `${H}px`;
  }
  canvas.dataset.tend = String(tEnd);
  const shown = toShown(name);
  const series = (sig) => ({ t: sig.t, [name]: sig[name].map(shown) });     // in the units shown
  const run = series(rp.run.result.signals);
  const other = cmp && name in cmp.signals ? series(cmp.signals) : null;
  canvas.dataset.series = String(other ? 2 : 1);
  canvas.setAttribute("aria-label", other ? t("{signal} over time, {run} and {other}", { signal: name, run: runName(rp.run.id), other: runName(cmp.id) })
    : t("{signal} over time, {run}", { signal: name, run: runName(rp.run.id) }));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = tk.bg;
  ctx.fillRect(0, 0, W, H);
  let lo = Infinity, hi = -Infinity;
  for (const vs of [run[name], other ? other[name] : []]) for (const v of vs) { if (v == null) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!(hi >= lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const t0 = start();
  const plotH = H - TICKS_H - PAD_T;
  const X = (time) => PAD_L + ((time - t0) / Math.max(1e-9, tEnd - t0)) * (W - PAD_L - PAD_R);
  const Y = (v) => PAD_T + (1 - (v - lo) / (hi - lo)) * plotH;
  ctx.font = `10px ${tk.font}`;
  ctx.lineWidth = 1;
  ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = tk.text;
  for (const v of [lo, hi]) {
    ctx.strokeStyle = tk.grid;
    ctx.beginPath(); ctx.moveTo(PAD_L, Math.round(Y(v)) + 0.5); ctx.lineTo(W - PAD_R, Math.round(Y(v)) + 0.5); ctx.stroke();
    ctx.fillText(+v.toFixed(Math.abs(v) >= 100 ? 0 : 1) + "", PAD_L - 5, Y(v));
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const span = tEnd - t0;
  const every = span > 30 ? 10 : span > 12 ? 5 : span > 5 ? 2 : span > 2 ? 1 : 0.5;
  for (let s = Math.ceil(t0 / every) * every; s <= tEnd + 1e-9; s += every) {
    ctx.fillText(`${+s.toFixed(1)} s`, X(s), H - TICKS_H + 3);
  }
  // a match's phases: the pause shaded, and each phase named where it starts
  const phases = (shownResult(rp.run.result).marks || []).filter((m) => m.kind === "phase");
  phases.forEach((m, k) => {
    const x0 = X(m.t), x1 = k + 1 < phases.length ? X(phases[k + 1].t) : W - PAD_R;
    if (m.phase === "pause") {
      ctx.save(); ctx.globalAlpha = 0.5; ctx.fillStyle = tk.grid;
      ctx.fillRect(x0, PAD_T, x1 - x0, plotH); ctx.restore();
    }
    const xl = Math.round(x0) + 0.5;
    ctx.strokeStyle = tk.grid; ctx.beginPath(); ctx.moveTo(xl, PAD_T); ctx.lineTo(xl, PAD_T + plotH); ctx.stroke();
    ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.fillStyle = tk.text; ctx.fillText(m.text, x0 + 3, PAD_T + 1);
  });
  // the line through a signal's samples, broken at each empty one (null); its pieces' [first t, last t]
  const line = (sig, color, dash, alpha) => {
    const ts = sig.t, vs = sig[name], pieces = [];
    ctx.save();
    ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = 1.75; ctx.setLineDash(dash);
    ctx.beginPath();
    for (let k = 0; k < vs.length; k++) {
      if (vs[k] == null) continue;
      if (k > 0 && vs[k - 1] != null) { ctx.lineTo(X(ts[k]), Y(vs[k])); pieces[pieces.length - 1][1] = ts[k]; continue; }
      ctx.moveTo(X(ts[k]), Y(vs[k]));
      pieces.push([ts[k], ts[k]]);
      if (k + 1 >= vs.length || vs[k + 1] == null) ctx.lineTo(X(ts[k]) + 1.5, Y(vs[k]));   // a lone sample: a dash
    }
    ctx.stroke();
    ctx.restore();
    return pieces;
  };
  if (other) line(other, tk.compare, [5, 3], 1);   // "faded": a lighter gray, dashed (still 3:1 on the background)
  canvas.dataset.segments = JSON.stringify(line(run, tk.run, [], 1));
  // the replay's time
  ctx.strokeStyle = tk.mark; ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(Math.round(X(rp.time)) + 0.5, PAD_T); ctx.lineTo(Math.round(X(rp.time)) + 0.5, PAD_T + plotH); ctx.stroke();
  ctx.globalAlpha = 1;
}
