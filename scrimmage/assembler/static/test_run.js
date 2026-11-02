// The Test tab's run panel (test.js calls update() with each state): the shown run, its replay and its chart, and
// the kept runs. Data: GET /api/test/run/<id> (result.json's fields and signals), /view (the run's own view.json,
// so a run from before Import again draws its old robot) and /motion (motion.bin, scrimmage/blocks/store.py).
//
//   Outcome   "Run 3 · timed out", the outcome sentence in full, a note when a setting the tab has no box for (a
//             timeout, a repeat's max) ended it, and each check's line
//   Replay    live3d.js's LiveView draws the view; motion.bin's frames move it. Play / Pause, speed (¼× to 2×), a
//             scrubber with the time
//   Chart     one plot per signal picked (default: the test's `chart`, else tilt and the driven joints but the
//             drive wheels), the run
//             "compared with" (default: the run before it) drawn faded and dashed, the replay's time marked; a
//             click on a plot moves the replay there
//   Runs      the kept runs, newest first, each with its outcome (a stopped one says so); a click shows it
//   Sweep     over the shown run when it is one of a sweep's (and when a sweep has just finished): what it tried,
//             a row per value (the value, the outcome, each check) and a chart of when the run ended, or a signal's
//             value at the end, against the value; a click on a row shows that row's run
//
// When a new run is kept (a run ends), it is shown. Test hook: window.__scrimmageTest.replay (test.js) is replay():
// {time, playing, runId, view (the LiveView or null), chart: {signals, runs: [shown, compared or null]}}, and
// window.__scrimmageTest.sweep is sweepShown(): {id, rows, y, points: [[value, y], ...]} or null.

import { $, h, api, apiFile } from "./app.js";

const MOTION_MAGIC = "SCRM", MOTION_VERSION = 1, HEADER = 14;   // struct "<4sHII"
const CHART_H = 92, PAD_L = 44, PAD_R = 8, PAD_T = 6, TICKS_H = 16;
const KEEP_LOADED = 12;   // results kept in memory for compares

const rp = {
  state: null,
  newest: undefined,  // the newest kept run's id last seen (undefined: no state yet)
  shownId: null,
  run: null,          // the shown run: {id, result, motion: {n, times, poses}} once loaded
  compareId: undefined,   // undefined: the default (the run before the shown one); null: none
  compare: null,      // the compared run's result, once loaded
  results: new Map(), // "id|started" -> result (a kept run never changes; an id comes back if the runs folder is emptied)
  picked: null,       // the signals the student picked (a Set), or null for the default
  time: 0,
  playing: false,
  speed: 1,
  wall: 0,
  view: null,
  viewFailed: null,
  seq: 0,
  sweepId: null,      // the sweep shown over the run (its id, "sweep-002"), or null
  newestSweep: undefined,
  sweepY: "t_end",    // what the sweep's chart shows: "t_end" or a signal's name
  sweepFinals: new Map(),  // run id|started -> {signal: its last value}
};

export function runName(id) {
  const m = /^(?:sweep-(\d+)\/)?(\d+)$/.exec(id || "");
  if (!m) return String(id);
  return m[1] ? `Sweep ${+m[1]}, run ${+m[2]}` : `Run ${+m[2]}`;
}

const statusWord = (status) => (status === "cancelled" ? "stopped" : status || "");
// the outcome as the student reads it ("finished" alone says nothing the status doesn't)
const outcomeText = (r) => sentence(r.status === "finished" && r.outcome === "finished" ? "every block played" : r.outcome);
const sentence = (s) => (s ? `${s[0].toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? "" : "."}` : "");
const end = () => (rp.run ? rp.run.result.t_end || 0 : 0);
const start = () => (rp.run && rp.run.motion.times.length ? rp.run.motion.times[0] : 0);

export function replay() {
  return {
    time: rp.time, playing: rp.playing, runId: rp.run ? rp.run.id : null, view: rp.view,
    chart: { signals: rp.run ? shownSignals() : [], runs: [rp.run ? rp.run.id : null, rp.compare ? rp.compare.id : null],
      colors: (({ run, compare, bg }) => ({ run, compare, bg }))(tokens()) },
  };
}

// -- the state -----------------------------------------------------------------------------------------------------
export function update(state) {
  rp.state = state;
  const sweeps = state.sweeps || [];
  const newestSweep = sweeps.length ? sweeps[0].id : null;
  if (newestSweep !== rp.newestSweep) {
    if (rp.newestSweep !== undefined && newestSweep) rp.sweepId = newestSweep;   // one just finished: show it
    rp.newestSweep = newestSweep;
  }
  const runs = state.runs || [];
  const newest = runs.length ? runs[0].id : null;
  if (newest !== rp.newest) {
    rp.newest = newest;
    if (newest) show(newest);
  }
  const shown = runs.find((r) => r.id === rp.shownId);
  if (rp.shownId && !shown) {   // pruned (or the folder was emptied)
    if (newest) show(newest); else clear();
  } else if (shown && rp.run && rp.run.id === shown.id && rp.run.result.started !== shown.started) {
    show(shown.id);             // the folder was emptied and this number is a new run
  }
  const err = $("#test-run-error");
  err.hidden = !state.run_error;
  err.textContent = state.run_error ? `The run couldn't finish: ${state.run_error}` : "";
  // before the first run: what to do (nothing kept, nothing running, no sweep, no error)
  $("#test-run-empty").hidden = !!(runs.length || sweeps.length || state.running || state.run_error);
  renderList();
  renderCompare();
  renderSweep();
}

function clear() {
  rp.seq++;
  rp.shownId = null;
  rp.run = null;
  rp.compare = null;
  pause();
  $("#test-results").hidden = true;
  $("#test-load-error").hidden = true;
}

function resultKey(id) {
  const entry = ((rp.state && rp.state.runs) || []).find((r) => r.id === id);
  return `${id}|${entry ? entry.started : ""}`;
}

async function result(id) {
  const key = resultKey(id);
  if (rp.results.has(key)) return rp.results.get(key);
  const { status, data } = await api("GET", `/api/test/run/${id}`);
  if (status !== 200) throw new Error((data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`);
  if (`${id}|${data.started}` !== key) return data;     // the run changed meanwhile: use it, keep nothing
  rp.results.set(key, data);
  while (rp.results.size > KEEP_LOADED) rp.results.delete(rp.results.keys().next().value);
  return data;
}

const DAMAGED = "this run's replay file is damaged; run the test again";

export function parseMotion(buf) {
  const dv = new DataView(buf);
  if (buf.byteLength < HEADER || String.fromCharCode(...new Uint8Array(buf, 0, 4)) !== MOTION_MAGIC) {
    throw new Error(DAMAGED);
  }
  if (dv.getUint16(4, true) !== MOTION_VERSION) throw new Error("this run was saved by a newer scrimmage: update scrimmage to replay it");
  const frames = dv.getUint32(6, true), n = dv.getUint32(10, true);
  const size = 8 + n * 28;
  if (buf.byteLength !== HEADER + frames * size) throw new Error(DAMAGED);
  const times = new Float64Array(frames), poses = new Float32Array(frames * n * 7);
  for (let k = 0, o = HEADER; k < frames; k++, o += size) {
    times[k] = dv.getFloat64(o, true);
    for (let j = 0; j < n * 7; j++) poses[k * n * 7 + j] = dv.getFloat32(o + 8 + 4 * j, true);
  }
  return { n, times, poses };
}

const sweepOf = (id) => { const m = /^(sweep-\d+)\//.exec(id || ""); return m ? m[1] : null; };

export async function show(id) {
  const seq = ++rp.seq;
  pause();
  rp.shownId = id;
  rp.sweepId = sweepOf(id);
  renderSweep();
  rp.compareId = undefined;
  renderList();
  const loadErr = $("#test-load-error");
  let res, viewData, motion;
  try {
    const [r, v, m] = await Promise.all([result(id), api("GET", `/api/test/run/${id}/view`), apiFile(`/api/test/run/${id}/motion`)]);
    if (v.status !== 200) throw new Error((v.data.errors && v.data.errors[0] && v.data.errors[0].message) || `the server answered ${v.status}`);
    if (!m.blob) throw new Error(`the server answered ${m.status}`);
    res = r;
    viewData = v.data;
    motion = parseMotion(await m.blob.arrayBuffer());
  } catch (e) {
    if (seq !== rp.seq) return;
    rp.run = null;                     // the error takes the run's place: no earlier run stays up under this one
    rp.compare = null;
    $("#test-results").hidden = true;
    loadErr.hidden = false;
    loadErr.textContent = `Couldn't load ${runName(id)}: ${e.message || e}`;
    return;
  }
  await ensureView();
  if (seq !== rp.seq) return;
  loadErr.hidden = true;
  rp.run = { id, result: res, motion };
  if (rp.view) rp.view.setModel(viewData);
  if (rp.picked) rp.picked = new Set([...rp.picked].filter((s) => s in res.signals));
  if (rp.picked && !rp.picked.size) rp.picked = null;
  $("#test-results").hidden = false;
  renderOutcome();
  renderSignals();
  renderCompare();
  setTime(start());
  loadCompare();
}

async function ensureView() {
  if (rp.view || rp.viewFailed) return;
  const box = $("#test-replay");
  try {
    const { LiveView } = await import("./live3d.js");
    if (rp.view) return;
    rp.view = new LiveView(box);
    rp.view.renderer.domElement.setAttribute("aria-label", "Replay of the run in 3D");
  } catch (e) {
    rp.viewFailed = String(e.message || e);
    box.replaceChildren(h("p", { class: "view-placeholder", text: `The replay needs WebGL: ${rp.viewFailed}` }));
  }
}

// -- compare -------------------------------------------------------------------------------------------------------
function compareTarget() {
  if (rp.compareId !== undefined) return rp.compareId;
  const runs = (rp.state && rp.state.runs) || [];
  const k = runs.findIndex((r) => r.id === rp.shownId);
  return k >= 0 && k + 1 < runs.length ? runs[k + 1].id : null;
}

async function loadCompare() {
  const id = compareTarget();
  const seq = rp.seq;
  if (!id) { rp.compare = null; drawChart(); return; }
  if (rp.compare && rp.compare.id === id) { drawChart(); return; }
  try {
    const res = await result(id);
    if (seq !== rp.seq || compareTarget() !== id) return;
    rp.compare = res;
  } catch {
    rp.compare = null;
  }
  drawChart();
}

function renderCompare() {
  const sel = $("#test-compare");
  const runs = ((rp.state && rp.state.runs) || []).filter((r) => r.id !== rp.shownId);
  const want = compareTarget();
  const opts = [h("option", { value: "", text: "nothing" }),
    ...runs.map((r) => h("option", { value: r.id, text: `${runName(r.id)} · ${statusWord(r.status)}` }))];
  const key = opts.map((o) => `${o.value}=${o.textContent}`).join("|");
  if (sel.dataset.key !== key) { sel.replaceChildren(...opts); sel.dataset.key = key; }
  sel.value = want || "";
  if (rp.run && (want || null) !== (rp.compare ? rp.compare.id : null)) loadCompare();
}

// -- outcome and runs ----------------------------------------------------------------------------------------------
const NOTES = {
  "timed out": "A block gave up after its timeout. Timeouts aren't shown on the blocks yet: change one in the test file.",
  "repeat limit": "A “repeat until” block gave up after going round as many times as it may (its max). The max isn't shown on the block yet: change it in the test file.",
};

function renderOutcome() {
  const r = rp.run.result;
  $("#test-result-title").textContent = `${runName(rp.run.id)} · ${statusWord(r.status)}`;
  $("#test-outcome").textContent = outcomeText(r);
  const note = $("#test-outcome-note");
  note.hidden = !NOTES[r.status];
  note.textContent = NOTES[r.status] || "";
  $("#test-checks").replaceChildren(...(r.checks || []).map((c) => h("li", { class: c.passed ? "test-check-ok" : "test-check-bad" },
    h("span", { class: "test-check-icon", "aria-hidden": "true", text: c.passed ? "✓" : "✗" }), sentence(c.line))));
}

// Updated in place (not rebuilt), so a run button keeps the keyboard focus while states arrive during a run.
function renderList() {
  const runs = (rp.state && rp.state.runs) || [];
  $("#test-runs-wrap").hidden = !runs.length;
  const ol = $("#test-runs");
  const have = new Map([...ol.children].map((li) => [li.dataset.key, li]));
  const want = new Set(runs.map((r) => r.id));
  for (const [id, li] of have) if (!want.has(id)) li.remove();
  let prev = null;
  for (const r of runs) {
    let li = have.get(r.id);
    if (!li || !li.isConnected) {
      li = h("li", {},
        h("button", { type: "button", class: "test-run-item", "data-run": r.id, onclick: () => { if (r.id !== rp.shownId) show(r.id); } },
          h("span", { class: "test-run-name" }, runName(r.id), h("span", { class: "test-run-status" })),
          h("span", { class: "test-run-outcome" })));
      li.dataset.key = r.id;
      if (prev) prev.after(li); else ol.prepend(li);
    }
    const b = li.firstChild;
    b.classList.toggle("test-run-stopped", !!r.cancelled);
    if (r.id === rp.shownId) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current");
    const status = ` · ${statusWord(r.status)}`, outcome = outcomeText(r);
    const [st, oc] = [b.querySelector(".test-run-status"), b.querySelector(".test-run-outcome")];
    if (st.textContent !== status) st.textContent = status;
    if (oc.textContent !== outcome) oc.textContent = outcome;
    prev = li;
  }
}

// -- the replay ----------------------------------------------------------------------------------------------------
function frameAt(t) {
  const times = rp.run.motion.times;
  let lo = 0, hi = times.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {   // the last frame at or before t
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= t + 1e-9) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function setTime(t) {
  if (!rp.run) return;
  rp.time = Math.max(start(), Math.min(end(), t));
  const k = frameAt(rp.time);
  const { n, poses } = rp.run.motion;
  if (rp.view && k >= 0) rp.view.applyFrame({ poses: poses.subarray(k * n * 7, (k + 1) * n * 7), n, seq: k });
  renderPlayer();
  drawChart();
}

function renderPlayer() {
  const scrub = $("#test-scrub");
  scrub.min = String(start());
  scrub.max = String(Math.max(end(), start() + 0.01));
  scrub.value = String(rp.time);
  $("#test-time").textContent = `${rp.time.toFixed(1)} s of ${end().toFixed(1)} s`;
  const play = $("#test-play");
  play.textContent = rp.playing ? "❚❚ Pause" : "▶ Play";
  play.setAttribute("aria-pressed", String(rp.playing));
}

function play() {
  if (!rp.run) return;
  if (rp.time >= end() - 1e-6) setTime(start());
  rp.playing = true;
  rp.wall = performance.now();
  renderPlayer();
  requestAnimationFrame(tick);
}

function pause() {
  rp.playing = false;
  if (rp.run) renderPlayer();
}

function tick(now) {
  if (!rp.playing || !rp.run) return;
  const dt = Math.max(0, now - rp.wall) / 1000;
  rp.wall = now;
  setTime(rp.time + dt * rp.speed);
  if (rp.time >= end() - 1e-9) { pause(); return; }
  requestAnimationFrame(tick);
}

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

const shownSignals = () => (rp.picked ? available().filter((s) => rp.picked.has(s)) : defaultSignals());

function unitOf(name) {
  if (name === "tilt") return "°";
  if (name.endsWith(".current")) return "A";
  if (name === "hook_load") return "N";
  const pal = rp.state && rp.state.palette;
  const j = pal && (pal.joints || []).find((x) => x.name === name);
  return j ? (j.unit === "deg" ? "°" : j.unit) : "";
}

function renderSignals() {
  const shown = new Set(shownSignals());
  $("#test-signals").replaceChildren(...available().map((s) => h("button", {
    type: "button", class: "test-signal", "aria-pressed": String(shown.has(s)), "data-signal": s, text: s,
    onclick: () => {
      const now = new Set(shownSignals());
      if (now.has(s)) now.delete(s); else now.add(s);
      rp.picked = now;
      renderSignals();
      drawChart();
    } })));
}

function tokens() {
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  return { run: v("--accent-text"), compare: v("--chart-compare"), grid: v("--line"), text: v("--muted"), mark: v("--text"),
    bg: v("--subtle"), font: v("--font") };
}

function drawChart() {
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
        h("div", { class: "test-plot-title", text: `${name}${unitOf(name) ? ` (${unitOf(name)})` : ""}` }), c);
    }));
    canvases = [...box.querySelectorAll("canvas")];
  }
  const tk = tokens();
  canvases.forEach((c) => plot(c, c.dataset.signal, cmp, tEnd, tk));
  key.replaceChildren(...[
    h("span", { class: "test-key" }, h("span", { class: "test-swatch test-swatch-run" }), runName(rp.run.id)),
    cmp ? h("span", { class: "test-key" }, h("span", { class: "test-swatch test-swatch-compare" }), `${runName(cmp.id)} (faded)`) : null,
    names.length ? null : h("span", { class: "muted", text: "Pick something to chart above." })].filter(Boolean));
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
  const run = rp.run.result.signals;
  const other = cmp && name in cmp.signals ? cmp.signals : null;
  canvas.dataset.series = String(other ? 2 : 1);
  canvas.setAttribute("aria-label", `${name} over time, ${runName(rp.run.id)}${other ? ` and ${runName(cmp.id)}` : ""}`);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = tk.bg;
  ctx.fillRect(0, 0, W, H);
  let lo = Infinity, hi = -Infinity;
  for (const vs of [run[name], other ? other[name] : []]) for (const v of vs) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!(hi >= lo)) { lo = 0; hi = 1; }
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const t0 = start();
  const plotH = H - TICKS_H - PAD_T;
  const X = (t) => PAD_L + ((t - t0) / Math.max(1e-9, tEnd - t0)) * (W - PAD_L - PAD_R);
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
  const line = (sig, color, dash, alpha) => {
    const ts = sig.t, vs = sig[name];
    ctx.save();
    ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = 1.75; ctx.setLineDash(dash);
    ctx.beginPath();
    for (let k = 0; k < vs.length; k++) { if (k === 0) ctx.moveTo(X(ts[k]), Y(vs[k])); else ctx.lineTo(X(ts[k]), Y(vs[k])); }
    ctx.stroke();
    ctx.restore();
  };
  if (other) line(other, tk.compare, [5, 3], 1);   // "faded": a lighter gray, dashed (still 3:1 on the background)
  line(run, tk.run, [], 1);
  // the replay's time
  ctx.strokeStyle = tk.mark; ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(Math.round(X(rp.time)) + 0.5, PAD_T); ctx.lineTo(Math.round(X(rp.time)) + 0.5, PAD_T + plotH); ctx.stroke();
  ctx.globalAlpha = 1;
}


// -- the sweep -----------------------------------------------------------------------------------------------------
const ROBOT_WORDS = { mass: "the robot's weight (kg)", com_forward: "the robot's center of mass, forward (m)",
  com_left: "the robot's center of mass, left (m)", com_up: "the robot's center of mass, up (m)" };
const AXIS_WORDS = { x: "forward (x, m)", y: "left (y, m)", z: "up (z, m)", yaw: "turned (yaw, °)" };
const KEY_UNITS = { volts: "V", seconds: "s", distance: "m" };

// The block at a place in the test JSON (0-based, through repeat bodies), or null.
function blockAt(test, place) {
  let blocks = (test && test.blocks) || [], b = null;
  for (const i of place || []) { b = blocks[i]; if (!b) return null; blocks = b.body || []; }
  return b;
}

const blockWords = (b) => {
  if (!b) return "a block";
  const name = b.motor || b.joint;
  return name ? `${b.do} ${name}` : b.do;
};

function targetText(target) {
  if (!target) return "";
  if ("block" in target) {
    const b = blockAt(rp.state && rp.state.test, target.block);
    const unit = target.key === "to" ? (b && b.unit === "deg" ? "°" : b ? b.unit : "") : KEY_UNITS[target.key] || "";
    return `${blockWords(b)} (block ${target.block.map((i) => i + 1).join(" in ")}): ${target.key}${unit ? ` (${unit})` : ""}`;
  }
  if ("robot" in target) return ROBOT_WORDS[target.robot] || target.robot;
  if ("mount" in target) return `where ${target.mount} is mounted: ${AXIS_WORDS[target.axis] || target.axis}`;
  return "";
}

const sweepNumber = (id) => (/^sweep-(\d+)$/.exec(id || "") || [])[1];
const rowRun = (sweep, k) => `${sweep.id}/${String(k + 1).padStart(3, "0")}`;
const hasRun = (id) => ((rp.state && rp.state.runs) || []).some((r) => r.id === id);
const shownSweep = () => ((rp.state && rp.state.sweeps) || []).find((s) => s.id === rp.sweepId) || null;
const checkColumns = (sweep) => (sweep.rows.length ? Object.keys(sweep.rows[0]).filter((c) => !["value", "status", "outcome", "t_end"].includes(c)) : []);

export function sweepShown() {
  const sw = shownSweep();
  return sw ? { id: sw.id, rows: sw.rows.length, y: rp.sweepY, points: sweepPoints(sw) } : null;
}

function renderSweep() {
  const wrap = $("#test-sweep-wrap");
  const sw = shownSweep();
  wrap.hidden = !sw;
  if (!sw) return;
  $("#test-sweep-title").textContent = `Sweep ${+sweepNumber(sw.id)}: ${targetText(sw.target)}`;
  const notes = [...(sw.cancelled ? ["You stopped this sweep, so it has the values done before that."] : []), ...(sw.problems || [])];
  const note = $("#test-sweep-note");
  note.hidden = !notes.length;
  note.textContent = notes.join(" ");
  const checks = checkColumns(sw);
  const table = $("#test-sweep-table");
  const key = JSON.stringify([sw.id, sw.rows, checks, rp.shownId, (rp.state.runs || []).map((r) => r.id)]);
  if (table.dataset.key !== key) {
    table.dataset.key = key;
    table.replaceChildren(
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Value" }), h("th", { scope: "col", text: "Outcome" }),
        ...checks.map((c, i) => h("th", { scope: "col", title: c, text: `Check ${i + 1}` })))),
      h("tbody", {}, ...sw.rows.map((r, k) => {
        const id = rowRun(sw, k), ran = hasRun(id);
        const current = id === rp.shownId;
        const outcome = r.status === "couldn't run" ? `Couldn't run: ${r.outcome}` : `${statusWord(r.status)}: ${outcomeText(r)}`;
        return h("tr", { class: ran ? "test-sweep-row" : "test-sweep-row test-sweep-norun", "data-row": String(k), "aria-current": current ? "true" : null,
          onclick: ran ? () => { if (id !== rp.shownId) show(id); } : null },
          h("td", {}, ran ? h("button", { type: "button", class: "linklike test-sweep-value", "data-run": id,
            "aria-label": `Show the run at ${r.value}`, text: r.value }) : h("span", { text: r.value })),
          h("td", { class: "test-sweep-outcome", text: outcome }),
          ...checks.map((c) => h("td", { class: r[c] === "passed" ? "test-check-ok" : r[c] === "failed" ? "test-check-bad" : "",
            title: c, text: r[c] === "passed" ? "✓" : r[c] === "failed" ? "✗" : "" })));
      })));
    if (checks.length) {
      table.prepend(h("caption", { class: "test-sweep-caption" }, ...checks.map((c, i) => h("span", { text: `Check ${i + 1}: ${sentence(c)}` }))));
    }
  }
  const sel = $("#test-sweep-y");
  const signals = ((rp.state.palette && rp.state.palette.signals) || []);
  const opts = [["t_end", "when the run ended (s)"], ...signals.map((s) => [s, `${s} at the end${unitOf(s) ? ` (${unitOf(s)})` : ""}`])];
  const okey = opts.map((o) => o.join("=")).join("|");
  if (sel.dataset.key !== okey) {
    sel.replaceChildren(...opts.map(([v, text]) => h("option", { value: v, text })));
    sel.dataset.key = okey;
  }
  if (!opts.some(([v]) => v === rp.sweepY)) rp.sweepY = "t_end";
  sel.value = rp.sweepY;
  drawSweep();
}

// [[value, y], ...] of the rows that ran (a signal's final value once its run is loaded)
function sweepPoints(sw) {
  const out = [];
  sw.rows.forEach((r, k) => {
    const x = Number(r.value);
    if (rp.sweepY === "t_end") { if (r.t_end !== "" && r.t_end != null) out.push([x, Number(r.t_end)]); return; }
    const fin = rp.sweepFinals.get(resultKey(rowRun(sw, k)));
    if (fin && rp.sweepY in fin) out.push([x, fin[rp.sweepY]]);
  });
  return out;
}

const loadingFinals = new Set();
async function loadFinals(sw) {
  await Promise.all(sw.rows.map(async (r, k) => {
    const id = rowRun(sw, k), key = resultKey(id);
    if (!hasRun(id) || rp.sweepFinals.has(key) || loadingFinals.has(key)) return;
    loadingFinals.add(key);
    try {
      const res = await result(id);
      const fin = {};
      for (const [name, vs] of Object.entries(res.signals || {})) if (name !== "t" && vs.length) fin[name] = vs[vs.length - 1];
      rp.sweepFinals.set(key, fin);
    } catch {
      rp.sweepFinals.set(key, {});     // that row's point is left out
    } finally {
      loadingFinals.delete(key);
    }
  }));
  while (rp.sweepFinals.size > 64) rp.sweepFinals.delete(rp.sweepFinals.keys().next().value);
  const now = shownSweep();
  if (now && now.id === sw.id) drawSweep();
}

function drawSweep() {
  const sw = shownSweep();
  const canvas = $("#test-sweep-chart");
  if (!sw) return;
  if (rp.sweepY !== "t_end" && sw.rows.some((r, k) => hasRun(rowRun(sw, k)) && !rp.sweepFinals.has(resultKey(rowRun(sw, k))))) loadFinals(sw);
  const pts = sweepPoints(sw);
  const tk = tokens();
  const W = Math.max(200, Math.floor(canvas.clientWidth || canvas.parentElement.clientWidth || 320)), H = 140;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.height = `${H}px`;
  }
  const yName = rp.sweepY === "t_end" ? "when the run ended" : `${rp.sweepY} at the end`;
  canvas.setAttribute("aria-label", `${yName} against ${targetText(sw.target)}: ${pts.map(([x, y]) => `${+x.toPrecision(4)} → ${+y.toPrecision(4)}`).join(", ") || "no runs"}`);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = tk.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.font = `10px ${tk.font}`;
  ctx.fillStyle = tk.text;
  if (!pts.length) {
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(rp.sweepY !== "t_end" && loadingFinals.size ? "Loading…" : "No run to chart", W / 2, H / 2);
    return;
  }
  const xs = sw.rows.map((r) => Number(r.value)).filter(isFinite);
  let [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  if (x1 - x0 < 1e-9) { x0 -= 1; x1 += 1; }
  let [y0, y1] = [Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1]))];
  if (y1 - y0 < 1e-6) { y0 -= 1; y1 += 1; }
  const padR = 14, padT = 8, padB = 10, plotH = H - TICKS_H - padT - padB;   // padB: the lowest dot clears the labels
  const X = (v) => PAD_L + 6 + ((v - x0) / (x1 - x0)) * (W - PAD_L - 6 - padR);
  const Y = (v) => padT + (1 - (v - y0) / (y1 - y0)) * plotH;
  ctx.lineWidth = 1; ctx.strokeStyle = tk.grid;
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const v of [y0, y1]) {
    ctx.beginPath(); ctx.moveTo(PAD_L, Math.round(Y(v)) + 0.5); ctx.lineTo(W - padR, Math.round(Y(v)) + 0.5); ctx.stroke();
    ctx.fillText(+v.toFixed(Math.abs(v) >= 100 ? 0 : 2) + "", PAD_L - 5, Y(v));
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const labels = xs.length <= 6 ? xs : [x0, x1];
  for (const v of labels) ctx.fillText(+v.toPrecision(4) + "", X(v), H - TICKS_H + 3);
  ctx.strokeStyle = tk.run; ctx.lineWidth = 1.75;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
  ctx.stroke();
  const shown = sw.rows.findIndex((_, k) => rowRun(sw, k) === rp.shownId);
  ctx.fillStyle = tk.run;
  for (const [x, y] of pts) { ctx.beginPath(); ctx.arc(X(x), Y(y), 3.5, 0, 2 * Math.PI); ctx.fill(); }
  if (shown >= 0) {
    const p = pts.find(([x]) => x === Number(sw.rows[shown].value));
    if (p) { ctx.strokeStyle = tk.mark; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(X(p[0]), Y(p[1]), 6.5, 0, 2 * Math.PI); ctx.stroke(); }
  }
}

// -- boot ----------------------------------------------------------------------------------------------------------
export function initRunPanel() {
  $("#test-play").addEventListener("click", () => (rp.playing ? pause() : play()));
  $("#test-scrub").addEventListener("input", (e) => setTime(Number(e.target.value)));
  $("#test-speed").addEventListener("change", (e) => { rp.speed = Number(e.target.value) || 1; });
  $("#test-compare").addEventListener("change", (e) => {
    rp.compareId = e.target.value || null;
    loadCompare();
  });
  new ResizeObserver(() => drawChart()).observe($("#test-chart"));
  new ResizeObserver(() => drawSweep()).observe($("#test-sweep-wrap"));
  $("#test-sweep-y").addEventListener("change", (e) => { rp.sweepY = e.target.value || "t_end"; drawSweep(); });
  const dark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  if (dark && dark.addEventListener) dark.addEventListener("change", () => { drawChart(); drawSweep(); });
}
