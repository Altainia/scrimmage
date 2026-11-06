// The Scenario tab's input-track panel, shown by the step editor for a step with `inputs` (a recorded CSV) in place
// of the constant-input editor: a read-only plot of the track (axis traces, one color per column; button and POV
// lanes), Trim (start / end: the `trim_track` edit, which writes a trimmed copy of the CSV and sets the duration),
// "Replace with constant inputs" (`inputs_file` with null: the CSV stays on disk) and "Choose file…" (`inputs_file`
// with a path relative to the scenario file). Data: GET /api/scenario/track?step=i -> {path, duration, t, series}.

import { h, api, fmt } from "./app.js";
import { scEdit, sc } from "./scenario.js";
import { theme } from "./theme.js";
import { t, tn, i18n } from "./i18n.js";

// one color per column, app.css's --series-1 to --series-8 (the legend's swatches are the same tokens)
const COLORS = [1, 2, 3, 4, 5, 6, 7, 8].map((k) => `--series-${k}`);
const AXES_H = 110, LANE_H = 14, TIME_H = 18, PAD_L = 34, PAD_R = 8;

let cache = { key: null, data: null };   // the last track fetched (re-renders of the same version don't refetch)

async function loadTrack(i, step) {
  const key = `${i}|${step.inputs}|${step.duration}|${sc.state ? sc.state.version : ""}|${i18n.lang}`;   // the error is in the Language
  if (cache.key === key) return cache.data;
  const { status, data } = await api("GET", `/api/scenario/track?step=${i}`);
  const out = status === 200 ? { ok: true, track: data } : { ok: false, message: (data.errors && data.errors[0] && data.errors[0].message) || t("the server answered {status}", { status }) };
  cache = { key, data: out };
  return out;
}

// Columns worth drawing: axes that ever leave 0, buttons ever pressed, POVs ever pushed; the rest are counted.
function columns(series) {
  const axes = [], lanes = [];
  let quiet = 0;
  for (const [name, values] of Object.entries(series)) {
    if (/\.axis\d+$/.test(name)) { if (values.some((v) => v !== 0)) axes.push(name); else quiet++; }
    else if (/\.button\d+$/.test(name)) { if (values.some((v) => v)) lanes.push(name); else quiet++; }
    else if (/\.pov\d+$/.test(name)) { if (values.some((v) => v !== -1)) lanes.push(name); else quiet++; }
  }
  return { axes, lanes, quiet };
}

function drawTrack(canvas, track, cols) {
  const W = Math.max(200, Math.floor(canvas.clientWidth || canvas.parentElement.clientWidth || 400));
  const H = AXES_H + cols.lanes.length * LANE_H + TIME_H;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  canvas.style.height = `${H}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const c = theme.color;
  ctx.fillStyle = c("--subtle-2");
  ctx.fillRect(0, 0, W, H);
  const t = track.t;
  const dur = typeof track.duration === "number" && track.duration > 0 ? track.duration : (t.length ? t[t.length - 1] : 1) || 1;
  const X = (tt) => PAD_L + (tt / dur) * (W - PAD_L - PAD_R);
  const Y = (v) => 6 + (1 - (v + 1) / 2) * (AXES_H - 12);
  // the axis grid: -1, 0, 1
  ctx.font = "10px system-ui, sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.fillStyle = c("--plot-label");
  for (const v of [-1, 0, 1]) {
    ctx.strokeStyle = c(v === 0 ? "--plot-axis" : "--plot-grid"); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, Math.round(Y(v)) + 0.5); ctx.lineTo(W - PAD_R, Math.round(Y(v)) + 0.5); ctx.stroke();
    ctx.fillText(String(v), PAD_L - 5, Y(v));
  }
  // axis traces, held between rows (zero-order hold) and to the end of the step
  const step = (values, at) => {
    ctx.beginPath();
    values.forEach((v, k) => {
      const x0 = X(t[k]), x1 = X(k + 1 < t.length ? t[k + 1] : dur);
      if (k === 0) ctx.moveTo(x0, at(v)); else ctx.lineTo(x0, at(v));
      ctx.lineTo(x1, at(v));
    });
    ctx.stroke();
  };
  cols.axes.forEach((name, k) => {
    ctx.strokeStyle = c(COLORS[k % COLORS.length]); ctx.lineWidth = 1.5;
    step(track.series[name], Y);
  });
  // button / POV lanes: filled while pressed
  cols.lanes.forEach((name, k) => {
    const y = AXES_H + k * LANE_H;
    const values = track.series[name];
    const pov = /\.pov\d+$/.test(name);
    ctx.fillStyle = c("--plot-label"); ctx.textAlign = "right"; ctx.fillText(name.replace("button", "b"), PAD_L - 5, y + LANE_H / 2);
    ctx.fillStyle = c("--sunken"); ctx.fillRect(PAD_L, y + 2, W - PAD_L - PAD_R, LANE_H - 4);
    ctx.fillStyle = c(COLORS[(cols.axes.length + k) % COLORS.length]);
    values.forEach((v, j) => {
      const on = pov ? v !== -1 : !!v;
      if (!on) return;
      const x0 = X(t[j]), x1 = X(j + 1 < t.length ? t[j + 1] : dur);
      ctx.fillRect(x0, y + 2, Math.max(1, x1 - x0), LANE_H - 4);
    });
  });
  // time ticks
  const yT = AXES_H + cols.lanes.length * LANE_H;
  ctx.fillStyle = c("--plot-label"); ctx.textAlign = "center"; ctx.textBaseline = "top";
  const every = dur > 20 ? 5 : dur > 8 ? 2 : dur > 3 ? 1 : 0.5;
  for (let s = 0; s <= dur + 1e-9; s += every) {
    ctx.strokeStyle = c("--plot-axis"); ctx.beginPath(); ctx.moveTo(X(s) + 0.5, yT); ctx.lineTo(X(s) + 0.5, yT + 3); ctx.stroke();
    ctx.fillText(`${+s.toFixed(1)} s`, X(s), yT + 4);
  }
}

// The panel body for step i (the step editor puts it in its "Input track" section, which shows the errors).
export async function trackPanel(stepIndex, step) {
  const i = stepIndex;
  const res = await loadTrack(i, step);
  const parts = [];
  parts.push(h("p", { class: "track-meta" }, h("code", { class: "track-path", text: step.inputs, title: step.inputs }),
    res.ok ? h("span", { class: "muted", text: ` · ${tn("{count} row", "{count} rows", res.track.t.length)} · ${t("{seconds} s", { seconds: fmt(step.duration) })}` }) : null));
  if (res.ok) {
    const cols = columns(res.track.series);
    const canvas = h("canvas", { id: "track-canvas", class: "track-canvas", role: "img",
      "aria-label": t("Input track: {axes} moving axes, {lanes} button or POV lanes", { axes: cols.axes.length, lanes: cols.lanes.length }) });
    const ro = new ResizeObserver(() => { if (canvas.isConnected) drawTrack(canvas, res.track, cols); else ro.disconnect(); });
    ro.observe(canvas);
    // redrawn in a new theme; dropped once the canvas has been on the page and left it (not before it is put there)
    let shown = false;
    const off = theme.onChange(() => {
      if (canvas.isConnected) { shown = true; drawTrack(canvas, res.track, cols); } else if (shown) off();
    });
    parts.push(canvas);
    const legend = [...cols.axes].map((name, k) => h("span", { class: "track-key" },
      h("span", { class: "track-swatch", style: `background:var(${COLORS[k % COLORS.length]})` }), name));
    if (cols.quiet) legend.push(h("span", { class: "muted", text: tn("{count} other column stays at rest", "{count} other columns stay at rest", cols.quiet) }));
    if (!cols.axes.length && !cols.lanes.length) legend.unshift(h("span", { class: "muted", text: t("Every input stays at rest in this track.") }));
    parts.push(h("div", { class: "track-legend" }, ...legend));
  } else {
    parts.push(h("p", { class: "error", role: "alert", text: t("Couldn't load the track: {problem}", { problem: res.message }) }));
  }

  // Trim
  const dur = typeof step.duration === "number" ? step.duration : 0;
  const startIn = h("input", { id: "trim-start", type: "number", step: "any", min: "0", value: "0", inputmode: "decimal", "aria-label": t("Trim start") });
  const endIn = h("input", { id: "trim-end", type: "number", step: "any", min: "0", value: fmt(dur), inputmode: "decimal", "aria-label": t("Trim end") });
  const trimErr = h("p", { class: "error", role: "alert", hidden: true });
  const trim = h("button", { type: "button", id: "trim-go", class: "btn btn-small", onclick: () => {
    const a = Number(startIn.value), b = Number(endIn.value);
    const bad = startIn.validity.badInput || endIn.validity.badInput || startIn.value.trim() === "" || endIn.value.trim() === ""
      || !(a >= 0 && b > a && b <= dur + 1e-9);
    trimErr.hidden = !bad;
    trimErr.textContent = bad ? t("Trim needs 0 ≤ start < end ≤ {duration} s.", { duration: fmt(dur) }) : "";
    if (!bad) scEdit("trim_track", { step: i, start: a, end: b });
  } }, t("Trim"));
  parts.push(h("div", { class: "track-trim" },
    h("label", { for: "trim-start", class: "track-field" }, h("span", { class: "muted" }, t("Keep from")), h("span", { class: "with-unit" }, startIn, h("span", { class: "unit" }, "s"))),
    h("label", { for: "trim-end", class: "track-field" }, h("span", { class: "muted" }, t("to")), h("span", { class: "with-unit" }, endIn, h("span", { class: "unit" }, "s"))),
    trim), trimErr);

  // Replace / choose another file
  const fileIn = h("input", { id: "track-file", type: "text", class: "mono", value: step.inputs, autocomplete: "off", spellcheck: "false",
    "aria-label": t("Track file, relative to the scenario file") });
  const use = h("button", { type: "button", id: "track-file-go", class: "btn btn-small", onclick: () => {
    const v = fileIn.value.trim();
    if (v && v !== step.inputs) scEdit("inputs_file", { step: i, path: v });
  } }, t("Use this file"));
  fileIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); use.click(); } });
  const chooser = h("div", { class: "row track-chooser", hidden: true }, fileIn, use);
  parts.push(h("div", { class: "row track-actions" },
    h("button", { type: "button", id: "track-choose", class: "btn btn-small", "aria-expanded": "false", onclick: (e) => {
      chooser.hidden = !chooser.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!chooser.hidden));
      if (!chooser.hidden) fileIn.focus();
    } }, t("Choose file…")),
    h("button", { type: "button", id: "track-constant", class: "btn btn-small", title: t("Remove the track from this step (the CSV file stays on disk)"),
      onclick: () => scEdit("inputs_file", { step: i, path: null }) }, t("Replace with constant inputs"))),
  chooser);
  return h("div", { class: "track-panel" }, ...parts);
}
