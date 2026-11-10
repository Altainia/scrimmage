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
//             now, so they stay the default), and the next run measures it. A name in the test's chart the run
//             has no signal for gets a note under the chart ("This run has no signal called arm.lift")
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
//
// The state and the helpers the panel's files share are in test_run_state.js, the chart in test_run_chart.js, the
// sweep and the spread in test_run_sweep.js.

import { $, h, api, apiFile } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { i18n, t } from "./i18n.js";
import { rp, runName, statusWord, shownResult, outcomeText, sentence, end, start, resultKey } from "./test_run_state.js";
import { shownSignals, renderSignals, tokens, drawChart } from "./test_run_chart.js";
import { renderSweep, drawSweep, renderSpread, sweepShown, spreadShown } from "./test_run_sweep.js";

export { runName, sweepShown, spreadShown };

const MOTION_MAGIC = "SCRM", MOTION_VERSION = 1, HEADER = 14;   // struct "<4sHII"
const KEEP_LOADED = 12;   // results kept in memory for compares

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
  err.textContent = state.run_error ? t("The run couldn't finish: {why}", { why: state.run_error }) : "";
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

export async function result(id) {
  const key = resultKey(id);
  if (rp.results.has(key)) return rp.results.get(key);
  const { status, data } = await api("GET", `/api/test/run/${id}`);
  if (status !== 200) throw new Error((data.errors && data.errors[0] && data.errors[0].message) || t("the server answered {status}", { status }));
  if (`${id}|${data.started}` !== key) return data;     // the run changed meanwhile: use it, keep nothing
  rp.results.set(key, data);
  while (rp.results.size > KEEP_LOADED) rp.results.delete(rp.results.keys().next().value);
  return data;
}

const damaged = () => t("this run's replay file is damaged; run the test again");

export function parseMotion(buf) {
  const dv = new DataView(buf);
  if (buf.byteLength < HEADER || String.fromCharCode(...new Uint8Array(buf, 0, 4)) !== MOTION_MAGIC) {
    throw new Error(damaged());
  }
  if (dv.getUint16(4, true) !== MOTION_VERSION) throw new Error(t("this run was saved by a newer scrimmage: update scrimmage to replay it"));
  const frames = dv.getUint32(6, true), n = dv.getUint32(10, true);
  const size = 8 + n * 28;
  if (buf.byteLength !== HEADER + frames * size) throw new Error(damaged());
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
    if (v.status !== 200) throw new Error((v.data.errors && v.data.errors[0] && v.data.errors[0].message) || t("the server answered {status}", { status: v.status }));
    if (!m.blob) throw new Error(t("the server answered {status}", { status: m.status }));
    res = r;
    viewData = v.data;
    motion = parseMotion(await m.blob.arrayBuffer());
  } catch (e) {
    if (seq !== rp.seq) return;
    rp.run = null;                     // the error takes the run's place: no earlier run stays up under this one
    rp.compare = null;
    $("#test-results").hidden = true;
    loadErr.hidden = false;
    loadErr.textContent = t("Couldn't load {run}: {why}", { run: runName(id), why: e.message || e });
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
    rp.view.renderer.domElement.setAttribute("aria-label", t("Replay of the run in 3D"));
  } catch (e) {
    rp.viewFailed = String(e.message || e);
    box.replaceChildren(h("p", { class: "view-placeholder", text: t("The replay needs WebGL: {why}", { why: rp.viewFailed }) }));
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
  const opts = [h("option", { value: "", text: t("nothing") }),
    ...runs.map((r) => h("option", { value: r.id, text: `${runName(r.id)} · ${statusWord(r.status)}` }))];
  const key = opts.map((o) => `${o.value}=${o.textContent}`).join("|");
  if (sel.dataset.key !== key) { sel.replaceChildren(...opts); sel.dataset.key = key; }
  sel.value = want || "";
  if (rp.run && (want || null) !== (rp.compare ? rp.compare.id : null)) loadCompare();
}

// -- outcome and runs ----------------------------------------------------------------------------------------------
const NOTES = {
  "timed out": () => t("A block gave up after its timeout. Timeouts aren't shown on the blocks yet: change one in the test file."),
  "repeat limit": () => t("A “repeat until” block gave up after going round as many times as it may (its max). The max isn't shown on the block yet: change it in the test file."),
};

function renderOutcome() {
  const r = shownResult(rp.run.result);
  $("#test-result-title").textContent = `${runName(rp.run.id)} · ${statusWord(r.status)}`;
  $("#test-outcome").textContent = outcomeText(r);
  const note = $("#test-outcome-note");
  note.hidden = !NOTES[r.status];
  note.textContent = NOTES[r.status] ? NOTES[r.status]() : "";
  const seated = Object.entries(r.seated || {}).map(([rod, words]) => t("{depth} on {rod}", { depth: words, rod }));
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
const OUTCOME_WORDS = { in: () => t("made it in"), stuck: () => t("stuck in the robot"), missed: () => t("missed") };
// a missed piece's why (feeds.py's, a key in the run) in words
const FEED_WHY = { "bounced out": () => t("bounced out"), "off the field": () => t("off the field"),
  "still moving when the run ended": () => t("still moving when the run ended"), "on the floor": () => t("on the floor") };
const feedWhen = (f) => (f.t_outcome == null ? f.t : f.t_outcome);

// "piece 4 from feeder at 3.0 s: missed (bounced out)"; "piece 1 from drop at 0.5 s: placed on peg"
function feedText(f, k) {
  const p = { piece: k + 1, source: f.source === "floor" ? t("the floor") : f.source, t: Number(f.t).toFixed(1) };
  if (f.outcome === "placed") {
    return f.why ? t("piece {piece} from {source} at {t} s: placed on {place}", { ...p, place: f.why })
      : t("piece {piece} from {source} at {t} s: placed", p);
  }
  const why = f.outcome === "stuck" && f.body ? t("resting on {body}", { body: f.body }) : FEED_WHY[f.why] ? FEED_WHY[f.why]() : f.why;
  const outcome = OUTCOME_WORDS[f.outcome] ? OUTCOME_WORDS[f.outcome]() : f.outcome;
  return why ? t("piece {piece} from {source} at {t} s: {outcome} ({why})", { ...p, outcome, why })
    : t("piece {piece} from {source} at {t} s: {outcome}", { ...p, outcome });
}

function renderFeeds() {
  const r = rp.run.result, feeds = feedsOf(r), c = r.counts;
  const summary = $("#test-feeds-summary");
  const shown = !!c && c.fed > 0;      // a station field's run that fed nothing has nothing to sum up
  summary.hidden = !shown;
  // with no hopper named (feeds.py's sentence) a test can't count a piece in, so "0 in" is left out
  const words = c ? [...(c.in || r.hopper !== false ? [t("{count} in", { count: c.in })] : []),
    ...(c.placed ? [t("{count} placed", { count: c.placed })] : []),
    t("{count} stuck", { count: c.stuck }), t("{count} missed", { count: c.missed })] : [];
  summary.replaceChildren(...(shown ? [h("span", { text: t("Fed pieces: {counts}", { counts: words.join(" · ") }) }),
    ...feeds.filter((f) => f.outcome === "stuck").map((f) => h("button", { type: "button", class: "btn btn-small test-stuck-show",
      "data-ball": String(f.ball), onclick: () => jumpTo(f), text: t("Show the stuck piece (at {t} s)", { t: feedWhen(f).toFixed(1) }) }))] : []));
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

// Move the replay to `time` and outline piece `ball` there (none when it isn't one of the run's movers).
function showPiece(ball, time) {
  pause();
  const mover = ball == null ? -1 : rp.run.movers.indexOf(`ball.${ball}`);
  rp.outlined = mover >= 0 ? ball : null;
  if (rp.view) rp.view.outline(mover);
  setTime(time);
}

// -- the moments the replay marks (a slip) ---------------------------------------------------------------------------
const marksOf = (r) => (r && Array.isArray(r.marks) ? shownResult(r).marks : []);
const slipsOf = (r) => marksOf(r).filter((m) => m.kind === "slip");     // a slip, as a cross
const phasesOf = (r) => marksOf(r).filter((m) => m.kind === "phase");   // a match's phase change, as a tick

function renderMarks() {
  const slips = slipsOf(rp.run.result), phases = phasesOf(rp.run.result), row = $("#test-slip-marks");
  row.hidden = !slips.length && !phases.length;
  const t0 = start(), span = Math.max(1e-9, end() - t0);
  const left = (m) => `left: ${(100 * Math.max(0, Math.min(1, (m.t - t0) / span))).toFixed(3)}%`;
  const on = (e) => {
    for (const b of row.children) b.classList.toggle("test-slip-mark-on", b === e.currentTarget);
    for (const b of $("#test-feed-marks").children) b.classList.remove("test-feed-mark-on");
  };
  row.replaceChildren(
    ...phases.map((m, k) => h("button", { type: "button", class: "test-phase-mark", "data-phase": m.phase, "data-mark": `phase${k}`,
      title: m.text, "aria-label": m.text, style: left(m), onclick: (e) => { on(e); showPiece(null, m.t); } })),
    ...slips.map((m, k) => h("button", { type: "button", class: "test-slip-mark", "data-mark": String(k),
      title: m.text, "aria-label": m.text, style: left(m), onclick: (e) => { on(e); showPiece(m.ball, m.t); } })));
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
function frameAt(time) {
  const times = rp.run.motion.times;
  let lo = 0, hi = times.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {   // the last frame at or before time
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= time + 1e-9) lo = mid; else hi = mid - 1;
  }
  return lo;
}

export function setTime(time) {
  if (!rp.run) return;
  rp.time = Math.max(start(), Math.min(end(), time));
  const k = frameAt(rp.time);
  const { n, poses } = rp.run.motion;
  if (rp.view && k >= 0) rp.view.applyFrame({ poses: poses.subarray(k * n * 7, (k + 1) * n * 7), n, seq: k });
  if (rp.view) rp.view.lightSensors((spec) => sensorOn(spec, signalAt(`sensor.${spec.name}`, rp.time)));
  renderPlayer();
  drawChart();
}

// The shown run's sample of a signal at `time` (the last at or before it); null when it has none.
function signalAt(name, time) {
  const sig = rp.run.result.signals || {}, times = sig.t || [], vs = sig[name];
  if (!vs || !times.length || times[0] > time + 1e-9) return null;
  let lo = 0, hi = times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= time + 1e-9) lo = mid; else hi = mid - 1;
  }
  return vs[lo];
}

// A sensor is on from its signal (scrimmage/blocks/run_setup.py sensor_value): a beam broken (1), a distance sensor closer
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
  $("#test-time").textContent = t("{now} s of {end} s", { now: rp.time.toFixed(1), end: end().toFixed(1) });
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
  units.onChange(() => {          // the chart, the sweep and the spread in the new units: its titles drawn again
    $("#test-chart").replaceChildren();
    if (rp.state) { renderList(); renderSweep(); renderSpread(); }
    if (rp.run) { renderOutcome(); drawChart(); }
  });
  theme.onChange(() => { drawChart(); drawSweep(); });   // the colors are the theme's
  i18n.onChange(() => {         // everything drawn in words, again in the new ones
    $("#test-chart").replaceChildren();
    $("#test-runs").replaceChildren();     // its names are made once per run
    if (rp.state) update(rp.state);
    if (rp.run) { renderOutcome(); renderSignals(); drawChart(); setTime(rp.time); }
  });
}
