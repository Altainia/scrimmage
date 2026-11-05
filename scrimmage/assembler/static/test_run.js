// The Test tab's run panel (test.js calls update() with each state): the shown run, its replay and its chart, and
// the kept runs. Data: GET /api/test/run/<id> (result.json's fields and signals), /view (the run's own view.json,
// so a run from before Import again draws its old robot) and /motion (motion.bin, scrimmage/blocks/store.py).
//
//   Outcome   "Run 3 · timed out", the outcome sentence in full, a note when a setting the tab has no box for (a
//             timeout, a repeat's max) ended it, and each check's line; for a run that fed pieces (result.json's
//             feeds and counts), "Fed pieces: 17 in · 1 stuck · 2 missed" ("17 in · 2 placed · …" when pieces
//             were placed on the field; no "0 in" when the test names no hopper) and a "Show the stuck piece"
//             button for each stuck one; for a run with a piece on a rod at the end, its depth in words ("seated
//             3.5 in of 4.0 in on branch_l2"); the goal counts, how the air went and how close the robot came to
//             each field element the test measures, as `scrimmage test` prints them (the result's lines: "Scored 5
//             in hub_blue.", "Air: 120 psi at the start, …", "Closest to trench: 0.82 in at 3.1 s."); and the run's
//             notes (what its numbers rest on: an UNCALIBRATED grip) as a list
//   Replay    live3d.js's LiveView draws the view (a cylinder between its pins, a sensor's line lit while the run's
//             sensor.<name> signal says it was on: a beam broken, a distance closer than its threshold, a color
//             sensor seeing a piece); motion.bin's frames move it. Play /
//             Pause, speed (¼× to 2×), a scrubber with the time, and under it a mark per fed piece at its feed time, its shape and color its
//             outcome (in: filled; placed on the field: a square; stuck: a diamond, the warning color; missed:
//             hollow). A click on a mark or a stuck
//             button moves the replay to when that piece's outcome was settled and outlines the piece. Under
//             those, a cross (the warning color) at each moment result.json's marks keep (a piece slipping out of
//             a grabber), its sentence its tooltip; a click moves the replay there and outlines the piece
//   Chart     one plot per signal picked (default: the test's `chart`, else tilt and the driven joints but the
//             drive wheels), the run
//             "compared with" (default: the run before it) drawn faded and dashed, the replay's time marked; a
//             click on a plot moves the replay there. An empty sample (null: a seated depth while no piece is on
//             its rod, a clearance while nothing is within 20 in) breaks the line. A field element's clearance is
//             measured only when the test names it, so the picker also offers `clearance.<element>` for each field
//             element the run didn't measure: picking one puts it in the test's `chart` (with the signals shown
//             now, so they stay the default), and the next run measures it
//   Runs      the kept runs, newest first, each with its outcome (a stopped one says so); a click shows it
//   Sweep     over the shown run when it is one of a sweep's (and when a sweep has just finished): what it tried,
//             a row per value (the value, the outcome, each check) and a chart of when the run ended, or a signal's
//             value at the end, against the value; a click on a row shows that row's run
//   Spread    over the shown run when it is one of a floor spread's (and when a spread has just finished): its
//             sentence ("Picked up 7 of 9; missed at …") and a row per placement (where, picked up, the outcome); a
//             click on a row shows that placement's run
//
// When a new run is kept (a run ends), it is shown. Test hook: window.__scrimmageTest.replay (test.js) is replay():
// {time, playing, runId, view (the LiveView or null), cylinders (LiveView.cylinderShapes(): [{name, barrel, rod}]
// drawn lengths in m, or null), sensors (LiveView.sensorShapes(): [{name, lit, a, b}], or null), chart: {signals,
// runs: [shown, compared or null]}, feeds (the shown run's), marks (the shown run's), outlined (the ball index of
// the piece outlined, or null), feedColors: {in, placed, stuck, missed, bg}, markColors: {slip, bg}},
// window.__scrimmageTest.sweep is sweepShown(): {id, rows, y, points: [[value, y], ...]} or null, and
// window.__scrimmageTest.spread is spreadShown(): {id, rows, sentence} or null.

import { $, h, api, apiFile } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";

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
  chartOf: () => null,    // the test's chart as on screen (initRunPanel's chart)
  setChart: () => {},     // writes the test's chart (null for none)
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
  spreadId: null,     // the floor spread shown over the run ("spread-001"), or null
  newestSpread: undefined,
  outlined: null,     // the fed piece outlined in the replay (its ball index), or null
};

export function runName(id) {
  const m = /^(?:(sweep|spread)-(\d+)\/)?(\d+)$/.exec(id || "");
  if (!m) return String(id);
  if (m[1] === "spread") return `Spread ${+m[2]}, placement ${+m[3]}`;
  return m[1] ? `Sweep ${+m[2]}, run ${+m[3]}` : `Run ${+m[3]}`;
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
    cylinders: rp.view ? rp.view.cylinderShapes() : null,
    sensors: rp.view ? rp.view.sensorShapes() : null,
    chart: { signals: rp.run ? shownSignals() : [], titles: [...$("#test-chart").querySelectorAll(".test-plot-title")].map((e) => e.textContent),
      runs: [rp.run ? rp.run.id : null, rp.compare ? rp.compare.id : null],
      colors: (({ run, compare, bg }) => ({ run, compare, bg }))(tokens()) },
    feeds: rp.run ? feedsOf(rp.run.result) : null, marks: rp.run ? marksOf(rp.run.result) : null, outlined: rp.outlined,
    feedColors: feedColors(), markColors: markColors(),
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
  const spreads = state.spreads || [];
  const newestSpread = spreads.length ? spreads[0].id : null;
  if (newestSpread !== rp.newestSpread) {
    if (rp.newestSpread !== undefined && newestSpread) rp.spreadId = newestSpread;
    rp.newestSpread = newestSpread;
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
  $("#test-run-empty").hidden = !!(runs.length || sweeps.length || spreads.length || state.running || state.run_error);
  renderList();
  renderCompare();
  renderSweep();
  renderSpread();
  if (rp.run) renderSignals();      // the test's chart or the field's elements may have changed
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
const spreadOf = (id) => { const m = /^(spread-\d+)\//.exec(id || ""); return m ? m[1] : null; };

export async function show(id) {
  const seq = ++rp.seq;
  pause();
  rp.shownId = id;
  rp.sweepId = sweepOf(id);
  rp.spreadId = spreadOf(id);
  renderSweep();
  renderSpread();
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
  rp.run = { id, result: res, motion, movers: (viewData.movers || []).map((m) => m.name) };
  rp.outlined = null;
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
  const seated = Object.entries(r.seated || {}).map(([rod, words]) => `${words} on ${rod}`);
  $("#test-seated").hidden = !seated.length;
  $("#test-seated").textContent = seated.join("; ");
  const totals = Array.isArray(r.lines) ? r.lines : [];
  $("#test-totals").hidden = !totals.length;
  $("#test-totals").replaceChildren(...totals.map((s) => h("li", { text: sentence(s) })));
  const notes = Array.isArray(r.notes) ? r.notes : [];
  $("#test-run-notes").hidden = !notes.length;
  $("#test-run-notes").replaceChildren(...notes.map((n) => h("li", { text: n })));
  $("#test-checks").replaceChildren(...(r.checks || []).map((c) => h("li", { class: c.passed ? "test-check-ok" : "test-check-bad" },
    h("span", { class: "test-check-icon", "aria-hidden": "true", text: c.passed ? "✓" : "✗" }), sentence(c.line))));
  renderFeeds();
  renderMarks();
}

// -- the fed pieces --------------------------------------------------------------------------------------------------
const feedsOf = (r) => (r && Array.isArray(r.feeds) ? r.feeds : []);
const OUTCOME_WORDS = { in: "made it in", placed: "placed", stuck: "stuck in the robot", missed: "missed" };
const feedWhen = (f) => (f.t_outcome == null ? f.t : f.t_outcome);

// "piece 4 from feeder at 3.0 s: missed (bounced out)"; "piece 1 from drop at 0.5 s: placed on peg"
function feedText(f, k) {
  const from = f.source === "floor" ? "the floor" : f.source;
  const head = `piece ${k + 1} from ${from} at ${Number(f.t).toFixed(1)} s: `;
  if (f.outcome === "placed") return `${head}placed${f.why ? ` on ${f.why}` : ""}`;
  const why = f.outcome === "stuck" && f.body ? `resting on ${f.body}` : f.why;
  return `${head}${OUTCOME_WORDS[f.outcome] || f.outcome}${why ? ` (${why})` : ""}`;
}

function renderFeeds() {
  const r = rp.run.result, feeds = feedsOf(r), c = r.counts;
  const summary = $("#test-feeds-summary");
  const shown = !!c && c.fed > 0;      // a station field's run that fed nothing has nothing to sum up
  summary.hidden = !shown;
  // with no hopper named (feeds.py's sentence) a test can't count a piece in, so "0 in" is left out
  const words = c ? [...(c.in || r.hopper !== false ? [`${c.in} in`] : []), ...(c.placed ? [`${c.placed} placed`] : []),
    `${c.stuck} stuck`, `${c.missed} missed`] : [];
  summary.replaceChildren(...(shown ? [h("span", { text: `Fed pieces: ${words.join(" · ")}` }),
    ...feeds.filter((f) => f.outcome === "stuck").map((f) => h("button", { type: "button", class: "btn btn-small test-stuck-show",
      "data-ball": String(f.ball), onclick: () => jumpTo(f), text: `Show the stuck piece (at ${feedWhen(f).toFixed(1)} s)` }))] : []));
  const marks = $("#test-feed-marks");
  marks.hidden = !feeds.length;
  const t0 = start(), span = Math.max(1e-9, end() - t0);
  // marks shrink (to 6 px) to the room between the closest two, so each stays a click of its own
  const times = feeds.map((f) => f.t).sort((a, b) => a - b);
  const gap = Math.min(...times.slice(1).map((x, k) => x - times[k]));
  const room = (gap / span) * marks.clientWidth;
  const size = marks.clientWidth > 0 && room < 11 ? Math.max(6, Math.floor(room)) : 11;
  marks.style.setProperty("--feed-size", `${size}px`);
  marks.replaceChildren(...feeds.map((f, k) => {
    const words = feedText(f, k);
    return h("button", { type: "button", class: `test-feed-mark test-feed-${f.outcome}`, "data-outcome": f.outcome,
      "data-ball": String(f.ball), title: words, "aria-label": words, onclick: () => jumpTo(f),
      style: `left: ${(100 * Math.max(0, Math.min(1, (f.t - t0) / span))).toFixed(3)}%` });
  }));
}

// Move the replay to when piece f's outcome was settled, and outline it there.
function jumpTo(f) {
  for (const m of $("#test-feed-marks").children) m.classList.toggle("test-feed-mark-on", m.dataset.ball === String(f.ball));
  for (const m of $("#test-slip-marks").children) m.classList.remove("test-slip-mark-on");
  showPiece(f.ball, feedWhen(f));
}

// Move the replay to t and outline piece `ball` there (none when it isn't one of the run's movers).
function showPiece(ball, t) {
  pause();
  const mover = ball == null ? -1 : rp.run.movers.indexOf(`ball.${ball}`);
  rp.outlined = mover >= 0 ? ball : null;
  if (rp.view) rp.view.outline(mover);
  setTime(t);
}

// -- the moments the replay marks (a slip) ---------------------------------------------------------------------------
const marksOf = (r) => (r && Array.isArray(r.marks) ? r.marks : []);
const slipsOf = (r) => marksOf(r).filter((m) => m.kind === "slip");     // the kinds drawn: a slip, as a cross

function renderMarks() {
  const marks = slipsOf(rp.run.result), row = $("#test-slip-marks");
  row.hidden = !marks.length;
  const t0 = start(), span = Math.max(1e-9, end() - t0);
  row.replaceChildren(...marks.map((m, k) => h("button", { type: "button", class: "test-slip-mark", "data-mark": String(k),
    title: m.text, "aria-label": m.text, style: `left: ${(100 * Math.max(0, Math.min(1, (m.t - t0) / span))).toFixed(3)}%`,
    onclick: (e) => {
      for (const b of row.children) b.classList.toggle("test-slip-mark-on", b === e.currentTarget);
      for (const b of $("#test-feed-marks").children) b.classList.remove("test-feed-mark-on");
      showPiece(m.ball, m.t);
    } })));
}

function markColors() {
  return { slip: theme.color("--slip-mark"), bg: behind($("#test-slip-marks")) };
}

// the colors the marks draw with, and the color behind them (#rrggbb), for the 3:1 check
function feedColors() {
  const v = (n) => theme.color(n);
  return { in: v("--feed-in"), placed: v("--feed-placed"), stuck: v("--feed-stuck"), missed: v("--feed-missed"),
    bg: behind($("#test-feed-marks")) };
}

function behind(el) {
  for (let e = el; e; e = e.parentElement) {
    const m = /rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/.exec(getComputedStyle(e).backgroundColor);
    if (m && (m[4] === undefined || Number(m[4]) > 0)) return `#${[m[1], m[2], m[3]].map((x) => (+x).toString(16).padStart(2, "0")).join("")}`;
  }
  return "#ffffff";
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
  if (rp.view) rp.view.lightSensors((spec) => sensorOn(spec, signalAt(`sensor.${spec.name}`, rp.time)));
  renderPlayer();
  drawChart();
}

// The shown run's sample of a signal at time t (the last at or before it); null when it has none.
function signalAt(name, t) {
  const sig = rp.run.result.signals || {}, times = sig.t || [], vs = sig[name];
  if (!vs || !times.length || times[0] > t + 1e-9) return null;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= t + 1e-9) lo = mid; else hi = mid - 1;
  }
  return vs[lo];
}

// A sensor is on from its signal (scrimmage/blocks/run.py sensor_value): a beam broken (1), a distance sensor closer
// than its threshold, a color sensor seeing a piece (a color's index; null while it sees none).
function sensorOn(spec, v) {
  if (v == null) return false;
  if (spec.kind === "beam") return v >= 0.5;
  if (spec.kind === "distance") return v < spec.threshold_in;
  return true;
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
const shownUnit = (name) => { const u = unitOf(name), w = WIRE[u]; return w ? units.name(w[0]) : u; };
// a function from the signal's wire value to the value shown (null stays null: no sample)
function toShown(name) {
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

function renderSignals() {
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
  })), ...(next.length ? [h("span", { class: "test-signals-next", text: "From the next run:" })] : []),
  ...next.map((s) => button(s, chart.has(s), () => toggleNextRun(s),
    { title: "Measured from the next run: this puts it in the test's chart" })));
  const again = focused && [...box.querySelectorAll("[data-signal]")].find((b) => b.dataset.signal === focused);
  if (again) again.focus();
}

function tokens() {
  const v = (n) => theme.color(n);
  return { run: v("--accent-text"), compare: v("--chart-compare"), grid: v("--line"), text: v("--muted"), mark: v("--text"),
    bg: v("--subtle"), font: getComputedStyle(document.documentElement).getPropertyValue("--font").trim() };
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
        h("div", { class: "test-plot-title", text: `${name}${shownUnit(name) ? ` (${shownUnit(name)})` : ""}` }), c);
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
  const shown = toShown(name);
  const series = (sig) => ({ t: sig.t, [name]: sig[name].map(shown) });     // in the units shown
  const run = series(rp.run.result.signals);
  const other = cmp && name in cmp.signals ? series(cmp.signals) : null;
  canvas.dataset.series = String(other ? 2 : 1);
  canvas.setAttribute("aria-label", `${name} over time, ${runName(rp.run.id)}${other ? ` and ${runName(cmp.id)}` : ""}`);
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


// -- the sweep -----------------------------------------------------------------------------------------------------
// What a sweep tried, in words, with the unit shown (L: the Units setting's, filled in by unitWords)
const ROBOT_WORDS = { mass: "the robot's weight (M)", com_forward: "the robot's center of mass, forward (L)",
  com_left: "the robot's center of mass, left (L)", com_up: "the robot's center of mass, up (L)" };
const AXIS_WORDS = { x: "forward (x, L)", y: "left (y, L)", z: "up (z, L)", yaw: "turned (yaw, °)" };
const KEY_UNITS = { volts: () => "V", seconds: () => "s", distance: () => units.name("field") };
const STATION_WORDS = { interval: "time between pieces (s)", stock: "stock", spread_yaw_deg: "left/right angle (± °)",
  spread_pitch_deg: "up/down angle (± °)", spread_speed: "speed (± S)", spread_position: "release box (± L)",
  spread_rotation_deg: "turned (± °)" };
const unitWords = (text) => text.replace(/\b([LMS])\)/g, (_, k) => `${units.name({ L: "length", M: "mass", S: "speed" }[k])})`);
const isGrip = (sw) => !!(sw.target && "grip" in sw.target);
const FILE_SI = { in: 0.0254, m: 1 };     // a block's length units in the test file
// A sweep's number as the file has it: [kind, SI per one of the file's unit], or null for one shown as it is
// (degrees, seconds, volts, the shuffle). The robot file and a station keep SI; a block, its own unit.
function targetMeasure(target) {
  if (!target) return null;
  if ("robot" in target) return target.robot === "mass" ? ["mass", 1] : ["length", 1];
  if ("mount" in target) return target.axis === "yaw" ? null : ["length", 1];
  if ("station" in target) return target.key === "spread_position" ? ["length", 1] : target.key === "spread_speed" ? ["speed", 1] : null;
  if ("block" in target) {
    if (target.key === "distance") return ["field", 1];
    const b = blockAt(rp.state && rp.state.test, target.block);
    return target.key === "to" && b && FILE_SI[b.unit] ? ["length", FILE_SI[b.unit]] : null;
  }
  return null;
}
// a sweep row's value as the table and chart show it: a length, weight or speed in the units shown (to 5 figures:
// the sweep keeps 6, so 100 lb tried reads 100, not 99.9999); a grip's material names (not numbers) are placed in
// order, one step apart, and labeled with the name
function rowValue(sw, r) {
  if (isGrip(sw)) return sw.rows.indexOf(r);
  const m = targetMeasure(sw.target);
  return m ? +units.toDisplay(Number(r.value) * m[1], m[0]).toPrecision(5) : Number(r.value);
}
const rowText = (sw, r) => (targetMeasure(sw.target) ? String(rowValue(sw, r)) : r.value);

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
    const unit = target.key === "to" ? (b && b.unit === "deg" ? "°" : b && FILE_SI[b.unit] ? units.name("length") : b ? b.unit : "")
      : KEY_UNITS[target.key] ? KEY_UNITS[target.key]() : "";
    return `${blockWords(b)} (block ${target.block.map((i) => i + 1).join(" in ")}): ${target.key}${unit ? ` (${unit})` : ""}`;
  }
  if ("robot" in target) return ROBOT_WORDS[target.robot] ? unitWords(ROBOT_WORDS[target.robot]) : target.robot;
  if ("grip" in target) return `grip material of ${target.grip}`;
  if ("mount" in target) return `where ${target.mount} is mounted: ${AXIS_WORDS[target.axis] ? unitWords(AXIS_WORDS[target.axis]) : target.axis}`;
  if ("station" in target) return `${target.station}: ${STATION_WORDS[target.key] ? unitWords(STATION_WORDS[target.key]) : target.key}`;
  if (target.setup === "seed") return "the shuffle";
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
  const key = JSON.stringify([sw.id, sw.rows, checks, rp.shownId, (rp.state.runs || []).map((r) => r.id), units.system]);
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
            "aria-label": `Show the run at ${rowText(sw, r)}`, text: rowText(sw, r) }) : h("span", { text: rowText(sw, r) })),
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
  const opts = [["t_end", "when the run ended (s)"], ...signals.map((s) => [s, `${s} at the end${shownUnit(s) ? ` (${shownUnit(s)})` : ""}`])];
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
    const x = rowValue(sw, r);
    if (rp.sweepY === "t_end") { if (r.t_end !== "" && r.t_end != null) out.push([x, Number(r.t_end)]); return; }
    const fin = rp.sweepFinals.get(resultKey(rowRun(sw, k)));
    if (fin && rp.sweepY in fin) out.push([x, toShown(rp.sweepY)(fin[rp.sweepY])]);
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
      // an empty last sample (null: no piece on a rod) leaves that row's point out
      for (const [name, vs] of Object.entries(res.signals || {})) if (name !== "t" && vs.length && vs[vs.length - 1] != null) fin[name] = vs[vs.length - 1];
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
  canvas.setAttribute("aria-label", `${yName} against ${targetText(sw.target)}: ${pts.map(([x, y]) => `${isGrip(sw) ? sw.rows[x].value : +x.toPrecision(4)} → ${+y.toPrecision(4)}`).join(", ") || "no runs"}`);
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
  const xs = sw.rows.map((r) => rowValue(sw, r)).filter(isFinite);
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
  for (const v of labels) ctx.fillText(isGrip(sw) ? String(sw.rows[v].value) : +v.toPrecision(4) + "", X(v), H - TICKS_H + 3);
  ctx.strokeStyle = tk.run; ctx.lineWidth = 1.75;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
  ctx.stroke();
  const shown = sw.rows.findIndex((_, k) => rowRun(sw, k) === rp.shownId);
  ctx.fillStyle = tk.run;
  for (const [x, y] of pts) { ctx.beginPath(); ctx.arc(X(x), Y(y), 3.5, 0, 2 * Math.PI); ctx.fill(); }
  if (shown >= 0) {
    const p = pts.find(([x]) => x === rowValue(sw, sw.rows[shown]));
    if (p) { ctx.strokeStyle = tk.mark; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(X(p[0]), Y(p[1]), 6.5, 0, 2 * Math.PI); ctx.stroke(); }
  }
}

// -- the floor spread ------------------------------------------------------------------------------------------------
const shownSpread = () => ((rp.state && rp.state.spreads) || []).find((s) => s.id === rp.spreadId) || null;

export function spreadShown() {
  const sp = shownSpread();
  return sp ? { id: sp.id, rows: sp.rows.length, sentence: sp.sentence } : null;
}

function renderSpread() {
  const wrap = $("#test-spread-wrap");
  const sp = shownSpread();
  wrap.hidden = !sp;
  if (!sp) return;
  const num = /^spread-(\d+)$/.exec(sp.id);
  $("#test-spread-title").textContent = num ? `Spread ${+num[1]}` : String(sp.id);
  $("#test-spread-sentence").textContent = sentence(sp.sentence || "");
  const note = $("#test-spread-note");
  note.hidden = !sp.cancelled;
  note.textContent = sp.cancelled ? "You stopped this spread, so it has the placements done before that." : "";
  const table = $("#test-spread-table");
  const key = JSON.stringify([sp.id, sp.rows, rp.shownId, (rp.state.runs || []).map((r) => r.id)]);
  if (table.dataset.key === key) return;
  table.dataset.key = key;
  table.replaceChildren(
    h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Placement" }), h("th", { scope: "col", text: "Picked up" }),
      h("th", { scope: "col", text: "Outcome" }))),
    h("tbody", {}, ...sp.rows.map((r, k) => {
      const id = rowRun(sp, k), ran = hasRun(id);
      const outcome = r.status === "couldn't run" ? `Couldn't run: ${r.outcome}` : `${statusWord(r.status)}: ${outcomeText(r)}`;
      const picked = r["picked up"] === "yes";
      return h("tr", { class: ran ? "test-sweep-row" : "test-sweep-row test-sweep-norun", "data-row": String(k),
        "aria-current": id === rp.shownId ? "true" : null, onclick: ran ? () => { if (id !== rp.shownId) show(id); } : null },
        h("td", {}, ran ? h("button", { type: "button", class: "linklike", "data-run": id,
          "aria-label": `Show the run at ${r.placement}`, text: r.placement }) : h("span", { text: r.placement })),
        h("td", { class: picked ? "test-check-ok" : "test-check-bad", text: r["picked up"] }),
        h("td", { class: "test-sweep-outcome", text: outcome }));
    })));
}

// -- boot ----------------------------------------------------------------------------------------------------------
// `chart()`: the test's chart as on screen; `setChart(list)`: writes it (the picker's clearances for the next run).
export function initRunPanel({ chart, setChart } = {}) {
  if (chart) rp.chartOf = chart;
  if (setChart) rp.setChart = setChart;
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
  units.onChange(() => {          // the chart and the sweep in the new units: its titles drawn again
    $("#test-chart").replaceChildren();
    if (rp.run) { drawChart(); renderSweep(); }
  });
  theme.onChange(() => { drawChart(); drawSweep(); });   // the colors are the theme's
}
