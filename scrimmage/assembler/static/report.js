// The run panel (right of the Scenario tab): Run (with a speed override) and Stop, live progress of the current
// run (step i/N and its name, sim time / total, console lines as they arrive), the report of a finished run
// (per step, each check's pass/fail with the runner's own message; the console tail; screenshot thumbnails with
// click-to-enlarge and contact sheets; the report folder), and this session's recent runs. It also sets the
// timeline's last-run dots (sc.stepStatus) from the newest finished run.
//
// Watch (while a run is going): the live 3D view (live3d.js, model from /api/scenario/view) moved by the run's frames
// (source 1) from the page's one stream (livestream.js sharedStream); it goes away when the run ends.
//
// Run files need the token header, so images are fetched into blob: URLs (png / jpg only; revoked once their
// run is no longer shown) and text files (.md, .txt, .log, .json) are shown as text. Nothing else is opened.

import { h, $, api, apiFile, notice, serial } from "./app.js";
import { units } from "./units.js";
import { sc, selectStep, rerenderTimeline } from "./scenario.js";
import { sharedStream } from "./livestream.js";

export const runs = {
  version: null,   // /api/scenario/runs version
  list: null,      // newest first
  viewId: null,    // the run shown (null: the newest)
  errors: [],      // why the last Run was refused
};

const IMAGE = /\.(png|jpe?g)$/i;
const TEXT = /\.(md|txt|log|json)$/i;
const STATE_LABEL = { running: "Running", passed: "Passed", failed: "Failed", error: "Error", stopped: "Stopped" };
const SPEEDS = [["", "the scenario's"], ["1", "1× (real time)"], ["0.5", "0.5×"], ["0.25", "0.25×"], ["2", "2×"], ["max", "max"]];

const t2 = (t) => (typeof t === "number" && isFinite(t) ? t.toFixed(2) : "–");
const runFile = (id, name) => `/api/scenario/run/${encodeURIComponent(id)}/${name.split("/").map(encodeURIComponent).join("/")}`;

// -- the panel skeleton (built once; parts are updated in place) ------------------------------------------
const el = {};

function build() {
  const root = $("#sc-run");
  el.run = h("button", { type: "button", id: "run-btn", class: "btn btn-primary run-btn", onclick: startRun }, "▶ Run");
  el.stop = h("button", { type: "button", id: "run-stop", class: "btn btn-danger", disabled: true, onclick: stopRun }, "■ Stop");
  el.speed = h("select", { id: "run-speed", "aria-label": "Speed for this run" }, ...SPEEDS.map(([v, label]) => h("option", { value: v }, label)));
  el.why = h("p", { id: "run-why", class: "hint" });
  el.errors = h("div", { id: "run-errors", role: "alert" });
  el.watch = h("button", { type: "button", id: "run-watch", class: "btn run-watch", "aria-pressed": "false", hidden: true,
    title: "Watch the run in 3D", onclick: () => setWatching(!watch.on) }, "Watch");
  el.watchView = h("div", { id: "run-watch-view", class: "run-watch-view", hidden: true });
  el.progress = h("section", { id: "run-progress", class: "run-sec", hidden: true });
  el.report = h("section", { id: "run-report", class: "run-sec", hidden: true });
  el.list = h("ol", { id: "run-list", class: "run-list" });
  el.recent = h("section", { id: "run-recent", class: "run-sec", hidden: true },
    h("h3", { class: "run-h" }, "Recent runs"), el.list);
  el.empty = h("p", { class: "empty empty-run" }, "Run the scenario to see each check pass or fail, with screenshots.");
  root.replaceChildren(
    h("h2", { class: "col-title" }, "Run"),
    h("div", { class: "run-controls" },
      h("div", { class: "row run-buttons" }, el.run, el.stop, el.watch),
      h("label", { class: "run-speed", for: "run-speed" }, h("span", { class: "muted" }, "Speed"), el.speed),
      el.why, el.errors),
    h("div", { id: "run-body", class: "scroll" }, el.watchView, el.empty, el.progress, el.report, el.recent));
  root.hidden = false;
}

// -- actions --------------------------------------------------------------------------------------------
async function startRun() {
  runs.errors = [];
  renderControls();
  const unpicked = await serial(async () => {   // after any queued edits, with the version they leave
    const s = sc.state;
    if (!s) return;
    const body = { version: s.version };
    const sp = el.speed.value;
    if (sp) body.speed = sp === "max" ? "max" : Number(sp);
    const { status, data } = await api("POST", "/api/scenario/run", body);
    if (status === 200 && data.ok) {
      runs.viewId = null;
      await loadRuns();
      return;
    }
    const errors = data.errors && data.errors.length ? data.errors : [{ field: "status", message: data.error || `the server answered ${status}` }];
    if (status === 200 && errors.some((e) => e.field === "start/unpicked")) return true;
    if (status === 409) notice(errors.map((e) => e.message).join("; "), "warn");
    else runs.errors = errors;
    renderControls();
    return false;
  });
  if (!unpicked) return;
  // nobody has picked where the robot starts: the robot code panel's start picker asks (outside serial: its
  // edits queue there), then the run goes on
  const { pickStart } = await import("./code.js");
  if (await pickStart()) await startRun();
}

async function stopRun() {
  el.stop.disabled = true;
  let why = null;
  try {
    const { status, data } = await api("POST", "/api/scenario/stop", {});
    if (status !== 200) why = (data && data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`;
  } catch (e) {
    why = (e && e.message) || String(e);
  }
  if (why !== null) {
    notice(`Couldn't stop the run: ${why}`, "error");
    el.stop.disabled = !running();
  }
}

// -- watch ------------------------------------------------------------------------------------------------
const watch = { on: false, view: null, seq: 0, stream: null };

function onRunFrame(e) {
  if (watch.view && e.detail.source === 1) watch.view.applyFrame(e.detail);
}

function onRunState(e) {
  if (watch.view && e.detail.source === "run") watch.view.setScore(e.detail.range);
}

async function setWatching(on) {
  watch.on = on && running();
  const seq = ++watch.seq;
  renderWatch();
  if (!watch.on) { dropWatchView(); return; }
  if (watch.view) return;
  let LiveView;
  try {
    ({ LiveView } = await import("./live3d.js"));
  } catch (e) {
    notice(`The live view failed to load: ${e.message || e}`, "error");
    return;
  }
  if (seq !== watch.seq || !watch.on) return;
  try {
    watch.view = new LiveView(el.watchView);
  } catch (e) {
    el.watchView.replaceChildren(h("p", { class: "view-placeholder", text: `The live view needs WebGL: ${e.message || e}` }));
    return;
  }
  const [{ status, data }, stream] = await Promise.all([api("GET", "/api/scenario/view"), sharedStream()]);
  if (seq !== watch.seq || !watch.view) return;
  watch.view.setModel(status === 200 ? data : {});
  if (!stream) { notice("Couldn't connect to the live stream to watch the run.", "error"); return; }
  if (watch.stream !== stream) {
    stream.addEventListener("frame", onRunFrame);
    stream.addEventListener("state", onRunState);
    watch.stream = stream;
  }
}

function dropWatchView() {
  if (watch.view) { watch.view.dispose(); watch.view = null; }
  el.watchView.replaceChildren();
}

function renderWatch() {
  const busy = running();
  if (!busy && watch.on) { watch.on = false; watch.seq++; dropWatchView(); }
  el.watch.hidden = !busy;
  el.watch.setAttribute("aria-pressed", String(watch.on));
  el.watchView.hidden = !watch.on;
}

// -- runs: long poll --------------------------------------------------------------------------------------
async function loadRuns(since = null) {
  const { status, data } = await api("GET", "/api/scenario/runs" + (since === null ? "" : `?since=${since}`));
  if (status === 200) apply(data);
  return status;
}

async function pollRuns() {
  for (;;) {
    const status = await loadRuns(runs.version);
    if (status !== 200) await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

function apply(data) {
  if (runs.list && data.version === runs.version) return;
  runs.version = data.version;
  runs.list = data.runs || [];
  if (runs.viewId && !runs.list.some((r) => r.id === runs.viewId)) runs.viewId = null;
  render();
  updateStepStatus();
}

const running = () => !!(runs.list && runs.list.some((r) => r.state === "running"));
const shown = () => (runs.list || []).find((r) => r.id === runs.viewId) || (runs.list || [])[0] || null;

// A dot for step i only when the newest finished run reported a step i of the same name (after a reorder, an
// insert or a rename the run's results no longer line up with the steps); none for a step cut short by Stop.
function updateStepStatus() {
  const last = (runs.list || []).find((r) => r.state !== "running" && r.report);
  const steps = sc.state ? sc.state.steps : [];
  const next = last ? steps.map((st, i) => {
    const s = last.report.steps[i];
    if (!s || s.name !== (st.name || `step ${i + 1}`) || (last.state === "stopped" && s.interrupted)) return null;
    return s.passed ? "pass" : "fail";
  }) : null;
  if (JSON.stringify(next) === JSON.stringify(sc.stepStatus)) return;
  sc.stepStatus = next;
  rerenderTimeline();
}

// -- rendering --------------------------------------------------------------------------------------------
export function render() {
  if (!el.run) return;
  renderControls();
  const r = shown();
  el.empty.hidden = !!r;
  renderProgress(r);
  renderReport(r);
  renderList();
  dropBlobs();
}

function renderControls() {
  if (!el.run) return;
  const s = sc.state;
  const busy = running();
  const nerr = s ? s.errors.length : 0;
  let why = "";
  if (!s) why = "Loading…";
  else if (s.read_only) why = `Read-only: ${s.read_only_reason}`;
  else if (nerr) why = `Fix the ${nerr === 1 ? "error" : `${nerr} errors`} in the scenario before running it.`;
  else if (busy) why = "A run is in progress.";
  el.run.disabled = busy || !s || !!s.read_only || nerr > 0;
  el.stop.disabled = !busy;
  renderWatch();
  el.why.textContent = why;
  el.why.hidden = !why;
  const sp = s && s.top ? s.top.speed : null;
  el.speed.options[0].textContent = `the scenario's (${sp === null || sp === undefined ? "max" : sp === "max" ? "max" : `${sp}×`})`;
  el.errors.replaceChildren(...runs.errors.map((e) => e.field === "start/not_built"
    // the Java code hasn't built: Build now is in the robot code panel's checklist, as from the Drive tab
    ? h("p", { class: "error" }, `Can't run: ${e.message} `,
        h("button", { type: "button", class: "btn", id: "run-to-code", text: "Go to Robot code",
                      onclick: async () => (await import("./code.js")).showChecks() }))
    : h("p", { class: "error", text: `Can't run: ${e.message}` })));
}

function renderProgress(r) {
  if (!r || r.state !== "running") { el.progress.hidden = true; el.progress.replaceChildren(); el.console = null; return; }
  el.progress.hidden = false;
  const n = sc.state ? sc.state.steps.length : null;
  const frac = r.total_t > 0 ? Math.min(1, r.t / r.total_t) : 0;
  if (!el.console) {
    el.pStep = h("div", { id: "run-step", class: "run-step" });
    el.pTime = h("div", { id: "run-time", class: "run-time muted" });
    el.pBar = h("span", { class: "run-bar-fill" });
    el.console = h("pre", { id: "run-console", class: "run-console", tabindex: "0", "aria-label": "Robot program console" });
    el.progress.replaceChildren(
      h("div", { class: "run-status" }, h("span", { class: "run-chip run-chip-running" }, "Running"), el.pStep),
      h("div", { class: "run-bar", role: "progressbar", "aria-label": "Simulated time" }, el.pBar), el.pTime,
      h("h3", { class: "run-h" }, "Console"), el.console);
  }
  el.pStep.textContent = r.step === null || r.step === undefined ? "starting the robot program…"
    : `Step ${r.step + 1}${n ? `/${n}` : ""}${r.step_name ? ` · ${r.step_name}` : ""}`;
  el.pTime.textContent = `${t2(r.t)} s of ${t2(r.total_t)} s simulated`;
  el.pBar.style.width = `${(frac * 100).toFixed(1)}%`;
  el.pBar.parentElement.setAttribute("aria-valuenow", String(Math.round(frac * 100)));
  const pre = el.console;
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  const text = r.console.join("\n");
  if (pre.textContent !== text) {
    pre.textContent = text || "(nothing printed yet)";
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }
}

let reportKey = null;

function renderReport(r) {
  const key = r && r.state !== "running" ? `${r.id}:${r.state}:${units.system}` : null;   // redrawn when Units changes
  if (key === reportKey) return;
  reportKey = key;
  if (!key) { el.report.hidden = true; el.report.replaceChildren(); return; }
  el.report.hidden = false;
  const rep = r.report;
  const parts = [verdict(r)];
  if (rep) {
    if (rep.error) {
      parts.push(r.state === "stopped" ? h("p", { class: "hint rp-why", text: "Stopped before the end: the steps after this point didn't run." })
        : h("p", { class: "error rp-why", text: `Couldn't run: ${rep.error}` }));
    }
    for (const n of rep.notes || []) parts.push(h("p", { class: "hint rp-note", text: `Note: ${n}` }));
    rep.steps.forEach((s, i) => parts.push(stepBlock(r, s, i)));
    if (rep.range) parts.push(scoreBlock(rep.range), ballsBlock(rep.balls || [], rep.ball_summary || {}));
    const bad = (rep.implicit || []).filter((c) => !c.passed);
    if (bad.length) {
      parts.push(h("div", { class: "rp-step rp-step-fail" },
        h("div", { class: "rp-step-head" }, h("span", { class: "dot run-dot run-fail" }), h("strong", {}, "Robot program")),
        h("ul", { class: "rp-checks" }, ...bad.map((c) => checkItem(c)))));
    }
  }
  const tail = rep ? rep.console_tail : r.console;
  if (tail && tail.length) {
    parts.push(h("details", { class: "rp-console", open: r.state !== "passed" ? true : null },
      h("summary", {}, `Console (last ${tail.length} line${tail.length === 1 ? "" : "s"})`),
      h("pre", { class: "run-console", text: tail.join("\n") })));
  }
  parts.push(filesBlock(r));
  el.report.replaceChildren(...parts);
}

function verdict(r) {
  const rep = r.report;
  const checks = rep ? rep.steps.flatMap((s) => s.checks) : [];
  const nfail = checks.filter((c) => !c.passed).length + (rep ? rep.steps.filter((s) => s.interrupted).length : 0);
  const sub = rep && rep.steps.length
    ? `${checks.length - checks.filter((c) => !c.passed).length}/${checks.length} checks passed${nfail && r.state === "failed" ? `, ${nfail} failed` : ""}`
    : "";
  return h("div", { class: `rp-verdict rp-${r.state}`, role: "status" },
    h("strong", { class: "rp-state" }, STATE_LABEL[r.state] || r.state),
    h("span", { class: "rp-sub", text: sub }),
    h("span", { class: "rp-id muted", text: r.id }),
    rep && rep.effective_speed ? h("span", { class: "rp-sub muted", text: `ran at ${rep.effective_speed}×` }) : null);
}

function stepBlock(r, s, i) {
  const ok = s.passed;
  const cut = r.state === "stopped" && s.interrupted;   // stopped mid-step: neither passed nor failed
  const res = cut ? "stopped" : ok ? "pass" : "fail";
  const known = ["disabled", "auto", "teleop", "test"].includes(s.mode);
  const shots = s.screenshots.filter((n) => !/contact-sheet/.test(n));
  const sheets = s.screenshots.filter((n) => /contact-sheet/.test(n));
  return h("div", { class: `rp-step rp-step-${res}` },
    h("button", { type: "button", class: "rp-step-head", title: "Show this step in the editor", onclick: () => selectStep(i, { focus: true }) },
      h("span", { class: `dot run-dot run-${res}`, "aria-label": { pass: "passed", fail: "failed", stopped: "stopped" }[res] }),
      h("span", { class: "step-num" }, `${i + 1}`),
      h("strong", { class: "rp-step-name", text: s.name }),
      h("span", { class: `chip mode-chip mode-${known ? s.mode : "unknown"}`, text: s.mode }),
      h("span", { class: "muted rp-t", text: `${t2(s.start_t)}–${t2(s.end_t)} s` }),
      s.game_data ? h("span", { class: "muted rp-t", text: `game data ${JSON.stringify(s.game_data)}` }) : null),
    s.checks.length || s.interrupted || s.skipped.length ? h("ul", { class: "rp-checks" },
      ...s.checks.map((c) => checkItem(c)),
      s.interrupted ? h("li", { class: `rp-check ${cut ? "rp-skip muted" : "rp-fail"}` }, h("span", { class: "rp-mark", "aria-hidden": "true" }, cut ? "■" : "✗"),
        h("span", { class: "rp-msg", text: cut ? `stopped: ${s.interrupted}` : s.interrupted })) : null,
      ...s.skipped.map((x) => h("li", { class: "rp-check rp-skip muted" }, h("span", { class: "rp-mark", "aria-hidden": "true" }, "–"), h("span", { text: `skipped: ${x}` }))))
      : h("p", { class: "muted small rp-none" }, "No checks in this step."),
    shots.length ? h("div", { class: "rp-shots" }, ...shots.map((n) => thumb(r, n))) : null,
    sheets.length ? h("div", { class: "rp-sheets" }, ...sheets.map((n) => thumb(r, n, true))) : null);
}

// -- the test range: the score at the end of the run and what became of each ball (report.json range / balls) --
function scoreBlock(c) {
  const goals = Object.entries(c.goals || {});
  return h("div", { id: "rp-score", class: "rp-step rp-range" },
    h("div", { class: "rp-step-head" }, h("strong", {}, "Score"),
      h("span", { class: "muted rp-t", text: `${c.score} scored` })),
    h("ul", { class: "rp-checks rp-score-list" },
      ...goals.map(([g, n]) => h("li", { class: "rp-check" }, h("span", { class: "rp-msg", text: `${g}: ${n}` }))),
      h("li", { class: "rp-check" }, h("span", { class: "rp-msg", text: `fired: ${c.fired}` })),
      h("li", { class: "rp-check" }, h("span", { class: "rp-msg", text: `held: ${c.held}` })),
      h("li", { class: "rp-check" }, h("span", { class: "rp-msg", text: `on the field: ${c.on_field}` }))));
}

const f = (v, d = 2) => (typeof v === "number" && isFinite(v) ? v.toFixed(d) : "–");
// a position or a speed in the chosen units (units.js), to `d` places
const fu = (v, kind, d = 2) => f(typeof v === "number" ? units.toDisplay(v, kind) : v, d);
const ballColumns = () => ["#", "fired t", `x ${units.name("field")}`, `y ${units.name("field")}`, "heading", `vx ${units.name("speed")}`,
  `vy ${units.name("speed")}`, "ω °/s", "hood °", "rps", `exit ${units.name("speed")}`, "spin rpm", "slip", "outcome", "goal and time / rest point / first hit"];

// Where a ball ended up (as the report.md table says it): the goal and time it scored, where it left the field or
// came to rest, or why the launcher kept it; and what it hit first after its release.
export function ballResult(b) {
  const p = b.last_pos;
  let text = "";
  if (b.outcome === "scored") text = `${b.goal} at t=${f(b.scored_t)}`;
  else if (b.outcome === "stuck") text = `launcher: ${b.launcher_status}`;
  else if (p) {
    const u = units.name("field");
    const xyz = `(${fu(p[0], "field")}, ${fu(p[1], "field")}, ${fu(p[2], "field")}) ${u}`;
    text = { out_of_field: `left at ${xyz}`, in_flight: `flying at ${xyz}`,
      on_field: b.at_rest ? `rest at (${fu(p[0], "field")}, ${fu(p[1], "field")}) ${u}` : `moving at ${xyz}` }[b.outcome] || xyz;
  }
  if (b.first_hit) text += `${text ? "; " : ""}first hit ${b.first_hit}`;
  // a ball fired that missed: how high it got against the nearest goal's rim (as report.md's)
  if (b.outcome !== "scored" && b.fired_t != null && b.apex_z != null && b.nearest_goal)
    text += `${text ? "; " : ""}apex ${fu(b.apex_z, "field")} ${units.name("field")} vs rim ${fu(b.rim_height, "field")} ${units.name("field")} (${b.nearest_goal})`;
  return text;
}

// The line under the table for the balls it leaves out (as report.md's).
function notListed(sum) {
  const parts = [];
  const n = sum.untouched, s = sum.spare;
  if (n) parts.push(`${n} floor ball${n === 1 ? "" : "s"} untouched (still where the range put ${n === 1 ? "it" : "them"})`);
  if (s) parts.push(`${s} spare ball${s === 1 ? "" : "s"} (unused slots, or floor balls turned off)`);
  return parts.length ? `Not listed: ${parts.join("; ")}.` : "";
}

// One row per ball that took part in the run (report.json balls); the rest counted under the table.
function ballsBlock(shown, sum) {
  const note = notListed(sum);
  const pose = (b) => b.robot_pose || [null, null, null];
  const vel = (b) => b.robot_velocity || [null, null, null];
  const rows = shown.map((b) => h("tr", { class: `rp-ball rp-ball-${b.outcome}`, dataset: { ball: String(b.id) } },
    ...[String(b.id), f(b.fired_t), fu(pose(b)[0], "field"), fu(pose(b)[1], "field"), f(pose(b)[2], 1), fu(vel(b)[0], "speed"), fu(vel(b)[1], "speed"), f(vel(b)[2], 1),
      f(b.hood_deg, 1), f(b.shooter_rps, 1), fu(b.exit_speed, "speed"), f(b.spin_rpm, 0), f(b.exit_slip)].map((t) => h("td", { class: "num", text: t })),
    h("td", { class: "rp-outcome", text: b.outcome }), h("td", { text: ballResult(b) })));
  return h("details", { id: "rp-balls", class: "rp-balls", open: true },
    h("summary", {}, `Balls (${shown.length})`),
    shown.length ? h("div", { class: "rp-table-wrap" }, h("table", { class: "rp-table" },
      h("thead", {}, h("tr", {}, ...ballColumns().map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", {}, ...rows))) : h("p", { class: "muted small" }, "No ball took part."),
    note ? h("p", { class: "muted small rp-not-listed", text: note }) : null);
}

function checkItem(c) {
  return h("li", { class: `rp-check ${c.passed ? "rp-pass" : "rp-fail"}` },
    h("span", { class: "rp-mark", "aria-label": c.passed ? "pass" : "fail" }, c.passed ? "✓" : "✗"),
    h("span", { class: "rp-msg", text: c.message }));
}

function filesBlock(r) {
  const rep = r.report;
  const files = rep && rep.files ? Object.values(rep.files) : [];
  const copy = h("button", { type: "button", class: "btn btn-small", onclick: async () => {
    try { await navigator.clipboard.writeText(r.out_dir); notice("Copied the report folder's path.", "info"); }
    catch { notice("Couldn't copy; select the path and copy it.", "warn"); }
  } }, "Copy");
  return h("div", { class: "rp-files" },
    h("h3", { class: "run-h" }, "Report folder"),
    h("div", { class: "row rp-folder" }, h("code", { class: "rp-path", text: r.out_dir, title: r.out_dir }), copy),
    files.length ? h("div", { class: "rp-file-list" }, ...files.map((name) => TEXT.test(name)
      ? h("button", { type: "button", class: "btn btn-small", onclick: () => showText(r, name) }, name)
      : h("span", { class: "rp-file muted", text: name }))) : null);
}

function renderList() {
  const list = runs.list || [];
  el.recent.hidden = !list.length;
  const cur = shown();
  el.list.replaceChildren(...list.map((r) => h("li", {},
    h("button", { type: "button", class: `run-item${cur && cur.id === r.id ? " current" : ""}`, "aria-current": cur && cur.id === r.id ? "true" : null,
      onclick: () => { runs.viewId = r === list[0] ? null : r.id; render(); } },
      h("span", { class: `run-chip run-chip-${r.state}`, text: STATE_LABEL[r.state] || r.state }),
      h("span", { class: "run-item-id", text: r.id }),
      h("span", { class: "muted run-item-t", text: r.state === "running" ? `${t2(r.t)} / ${t2(r.total_t)} s` : `${t2(r.total_t)} s` })))));
}

// -- images and text files ---------------------------------------------------------------------------------
const blobs = new Map();   // run id -> Map(file name -> blob: URL)

function dropBlobs() {
  const keep = shown();
  for (const [id, urls] of blobs) {
    if (keep && keep.id === id) continue;
    for (const u of urls.values()) URL.revokeObjectURL(u);
    blobs.delete(id);
  }
}

async function imageUrl(r, name) {
  if (!IMAGE.test(name)) return null;   // never a blob: URL for anything but png / jpg
  const have = blobs.get(r.id);
  if (have && have.has(name)) return have.get(name);
  const { blob } = await apiFile(runFile(r.id, name));
  if (!blob) return null;
  const s = shown();
  if (!s || s.id !== r.id) return null;   // the run was swapped out meanwhile
  if (!blobs.has(r.id)) blobs.set(r.id, new Map());
  const urls = blobs.get(r.id);
  if (urls.has(name)) return urls.get(name);
  const url = URL.createObjectURL(new Blob([blob], { type: /\.png$/i.test(name) ? "image/png" : "image/jpeg" }));
  urls.set(name, url);
  return url;
}

function thumb(r, name, sheet = false) {
  const img = h("img", { alt: name, class: sheet ? "rp-sheet-img" : "rp-thumb-img", loading: "lazy" });
  const btn = h("button", { type: "button", class: sheet ? "rp-sheet" : "rp-thumb", title: `${name} (click to enlarge)`, onclick: () => enlarge(r, name) },
    img, h("span", { class: "rp-cap", text: name }));
  imageUrl(r, name).then((u) => { if (u) img.src = u; else btn.classList.add("rp-missing"); });
  return btn;
}

let viewer = null;

function viewerDialog() {
  if (!viewer) {
    viewer = h("dialog", { id: "run-viewer", class: "run-viewer", "aria-labelledby": "run-viewer-title" });
    viewer.addEventListener("click", (e) => { if (e.target === viewer) viewer.close(); });
    document.body.append(viewer);
  }
  return viewer;
}

function showViewer(title, body) {
  const d = viewerDialog();
  d.replaceChildren(h("div", { class: "run-viewer-inner" },
    h("div", { class: "run-viewer-head" }, h("h2", { id: "run-viewer-title", text: title }),
      h("button", { type: "button", class: "btn btn-small", onclick: () => d.close(), "aria-label": "Close" }, "Close")),
    body));
  if (!d.open) d.showModal();
}

async function enlarge(r, name) {
  const url = await imageUrl(r, name);
  if (!url) { notice(`Couldn't load ${name}.`, "warn"); return; }
  showViewer(name, h("img", { src: url, alt: name, class: "run-viewer-img" }));
}

async function showText(r, name) {
  if (!TEXT.test(name)) return;
  const { blob } = await apiFile(runFile(r.id, name));
  if (!blob) { notice(`Couldn't load ${name}.`, "warn"); return; }
  showViewer(name, h("pre", { class: "run-viewer-text", text: await blob.text() }));
}

// -- boot ----------------------------------------------------------------------------------------------------
export async function initRunPanel() {
  build();
  window.addEventListener("scrimmage:scenario-state", () => {
    runs.errors = [];   // a refused Run's reasons are stale once the scenario changes
    renderControls();
    updateStepStatus();
  });
  units.onChange(() => render());
  await loadRuns();
  pollRuns();
}
