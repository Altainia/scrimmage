// The Robot tab's robot code panel (beside the 3D view; "Robot code" above it switches): three numbered parts,
//   1. Your robot code: the folder (Browse, through /api/app/browse; POST /api/robot/code detects the project and
//      writes [robot_program]) and what scrimmage found there, in plain words;
//   2. Ready to start?: the checklist (✅ ready, ⚠️ missing, ℹ️ worth knowing), each missing item with a button
//      that fixes it where the page can (POST /api/robot/code/fix), its output while it runs (long-polled GET
//      /api/robot/code/fix) and, when it fails, the failure in plain words with the last lines of output;
//   3. Where it starts: "Not picked yet", or the sentence and a field drawing with the robot at its real size and
//      a triangle the way it faces; the numbers under "Exact position".
// and the buttons Start and check devices (POST /api/robot/check) and Drive (the Drive tab, drive.js), which both
// open the start picker first while nobody has picked where the robot starts, and refuse (with the build button
// there) Java code that hasn't built on this computer (the server's start/not_built); Stop the robot while it runs.
//
// The device check and the battery are in code_devices.js; the start picture and the start picker in code_start.js.
//
// Test hooks: window.__scrimmage.code = {state (the last /api/robot/code), devices (the last /api/robot/devices), picking (the picker is open or undoing), picture: {pose, footprint, triangle} (field
// meters), labels: [the field's boxes labeled in it]} while a start is shown}; window.__scrimmage.startPicker = {toScreen(x, y) -> page pixels, value} while the
// picker is open.

import { $, h, api, post, notice, setTab, store, fmt } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { i18n, t } from "./i18n.js";
import { markDirty, onCommit } from "./inspector.js";
import { browseList } from "./start.js";
import { BATTERY, pollDevices, renderDevices, renderBattery, saveBattery, applyMatches } from "./code_devices.js";
import { pickStart, picking, drawPicture } from "./code_start.js";

export { pickStart };

const ICON = { true: "✅", false: "⚠️", null: "ℹ️" };
const ICON_WORD = { true: () => t("Ready"), false: () => t("Missing"), null: () => t("Good to know") };
const FIX_LABEL = { build_agent: () => t("Build it (about 10 s)"), gradle_build: () => t("Build now (a few minutes)"),
  install_lockstep: () => t("Install it") };
export const FIELD_NAME = { 2026: () => t("2026 field (REBUILT)"), plain: () => t("Plain field (red / blue)") };

export const code = {
  state: null,          // GET /api/robot/code
  seq: 0,               // the latest load asked (older answers are dropped)
  fix: null,            // GET /api/robot/code/fix, while one is running or after one ran
  results: {},          // check id -> the failed result of its last fix (its message and output stay shown)
  postError: null,      // why the last folder pick wasn't used: the server's finished sentence
  view: null, viewVersion: null,   // /api/live/view for the robot document, and the robot version it is for
  shown: false,
  run: null,            // {text, kind, discard?} the last Start and check devices answer
  devices: null,        // GET /api/robot/devices
  matches: {},          // slot -> the device chosen for it ("Use …"), until applied
  applied: null,        // {kind: ok|bad, text}: the last Apply's answer
  adding: null,         // the extra device whose "Add as…" part chooser is open
  addSelect: null,      // that chooser (kept across redraws, so a choice being made isn't lost)
};


// -- loading ---------------------------------------------------------------------------------------------------
export async function loadState() {
  const seq = ++code.seq;
  const { status, data } = await api("GET", "/api/robot/code");
  if (seq !== code.seq || status !== 200) return code.state;
  code.state = data;
  for (const c of data.checks) if (c.ok === true) delete code.results[c.id];   // it has been fixed since (by hand, say)
  window.dispatchEvent(new CustomEvent("scrimmage:code", { detail: { state: data } }));
  render();
  return data;
}

export async function loadView(force = false) {
  const v = store.state ? store.state.version : null;
  if (!force && code.view && code.viewVersion === v) return code.view;
  const { status, data } = await api("GET", "/api/live/view");
  code.view = status === 200 ? data : {};
  code.viewVersion = v;
  return code.view;
}

let reloadTimer = null;
function reloadSoon() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => { if (code.shown) loadState(); }, 250);
}

// -- the panel ----------------------------------------------------------------------------------------------------
export let els = null;
// The panel's fixed words, built once: each a text node with the function that writes it, done again on a language
// change (rebuilding the panel would lose what is half typed in the battery boxes)
const relabels = [];
const L = (fn) => { const node = document.createTextNode(fn()); relabels.push([node, fn]); return node; };

function build() {
  const panel = $("#code-panel");
  const part = (n, title, ...body) => h("section", { class: "code-part", "aria-labelledby": `code-part-${n}` },
    h("h3", { id: `code-part-${n}`, class: "code-part-title" }, h("span", { class: "code-num", "aria-hidden": "true" }, String(n)), title), ...body);
  els = {
    folder: h("p", { id: "code-folder", class: "code-folder" }),
    browse: h("button", { type: "button", id: "code-browse", class: "btn", onclick: toggleBrowse }, t("Browse…")),
    browseBox: h("div", { id: "code-browse-box", class: "code-browse", hidden: true }),
    summary: h("p", { id: "code-summary", class: "code-summary", hidden: true }),
    problem: h("div", { id: "code-problem", class: "code-problem", role: "alert", hidden: true }),
    checks: h("ul", { id: "code-checks", class: "code-checks" }),
    checksNote: h("p", { class: "hint", id: "code-checks-note" }),
    sentence: h("p", { id: "start-sentence", class: "start-sentence" }),
    startHint: h("p", { class: "hint" }, L(() => t("There's no default: choose a field, then click where the robot starts. Drive and Start and check devices ask first."))),
    pick: h("button", { type: "button", id: "start-pick", class: "btn", onclick: () => pickStart() }, t("Pick on the field")),
    canvas: h("canvas", { id: "start-canvas", class: "start-canvas", role: "img", hidden: true }),
    exact: h("details", { id: "start-exact", class: "start-exact", hidden: true }),
    checkBtn: h("button", { type: "button", id: "code-check-devices", class: "btn btn-primary", onclick: () => checkDevices() }, L(() => t("Start and check devices"))),
    driveBtn: h("button", { type: "button", id: "code-drive", class: "btn", onclick: () => driveIt() }, L(() => t("Drive"))),
    stopBtn: h("button", { type: "button", id: "code-stop", class: "btn btn-danger", hidden: true, onclick: () => stopRobot() }, L(() => t("Stop the robot"))),
    runStatus: h("div", { id: "code-run-status", class: "code-run-status", role: "status", hidden: true }),
    devState: h("p", { id: "code-devices-state", class: "hint" }),
    devBody: h("tbody"),
    apply: h("button", { type: "button", id: "code-apply", class: "btn btn-primary", disabled: true, onclick: () => applyMatches() }, t("Apply the matches")),
    applyStatus: h("p", { id: "code-apply-status", class: "code-apply-status", role: "status", hidden: true }),
    battery: {},
  };
  els.devices = h("section", { id: "code-devices", class: "code-part code-devices", "aria-labelledby": "code-devices-title", hidden: true },
    h("h3", { id: "code-devices-title", class: "code-part-title" }, L(() => t("Devices: the model beside your code"))),
    els.devState,
    h("div", { class: "dev-table-wrap" }, h("table", { class: "dev-table" },
      h("thead", {}, h("tr", {}, ...[() => t("Model part"), () => t("Model expects"), () => t("Your code made"), () => t("Status")]
        .map((fn) => h("th", { scope: "col" }, L(fn))))),
      els.devBody)),
    h("div", { class: "row code-apply-row" }, els.apply), els.applyStatus);
  const more = h("details", { id: "code-more", class: "code-more" }, h("summary", {}, L(() => t("More settings"))),
    h("p", { class: "hint" }, L(() => t("How the simulated battery behaves ({table} in the robot file). Leave a box empty for the default.", { table: "[battery]" }))));
  for (const b of BATTERY) {
    const inp = markDirty(h("input", { id: b.id, type: "number", step: b.step, min: "0", inputmode: "decimal", dataset: { key: b.key } }));
    onCommit(inp, () => saveBattery(b, inp));
    els.battery[b.key] = inp;
    more.append(h("div", { class: "field code-field", dataset: { field: `battery/${b.key}` } },
      h("label", { class: "field-label sp-label", for: b.id }, L(b.label)),
      h("span", { class: "with-unit" }, inp, h("span", { class: "unit" }, b.unit)),
      h("p", { class: "hint" }, L(b.help)),
      h("div", { class: "field-msgs" })));
  }
  panel.replaceChildren(h("div", { class: "code-inner" },
    h("h2", { class: "code-title" }, L(() => t("Robot code"))),
    h("p", { class: "hint code-lead" }, L(() => t("Point scrimmage at your team's robot code, check it's ready, and pick where the robot starts."))),
    part(1, L(() => t("Your robot code")), h("div", { class: "row code-folder-row" }, els.folder, els.browse), els.browseBox, els.summary, els.problem),
    part(2, L(() => t("Ready to start?")), els.checks, els.checksNote),
    part(3, L(() => t("Where it starts")), h("div", { class: "row start-row" }, els.sentence, els.pick), els.startHint, els.canvas, els.exact),
    h("div", { class: "row code-actions" }, els.checkBtn, els.driveBtn, els.stopBtn),
    els.runStatus, els.devices, more));
  new ResizeObserver(() => drawPicture()).observe(panel);
}

function render() {
  if (!els || !code.state) return;
  const s = code.state, ro = !!(store.state && store.state.read_only);
  // 1. the folder and what is in it
  els.folder.replaceChildren(s.cwd === null
    ? h("span", { class: "muted", text: t("No robot code picked yet. Browse to the folder that holds it.") })
    : h("span", {}, h("span", { class: "muted", text: t("Folder") + " " }), h("strong", { class: "mono", text: s.display_folder || s.cwd })));
  els.browse.disabled = ro;
  els.browse.textContent = s.cwd === null ? t("Browse…") : t("Change…");
  els.summary.hidden = !s.info;
  els.summary.replaceChildren(...(s.info ? [h("span", { class: "muted", text: t("Found:") + " " }), h("strong", { text: s.info.summary })] : []));
  // the panel's own problem and a refused folder pick both come as finished sentences (problem_text)
  const problem = code.postError || s.problem_text || s.problem;
  els.problem.hidden = !problem;
  els.problem.replaceChildren(...(problem ? [h("strong", { text: (code.postError ? t("Not used.") : t("Can't use the robot code.")) + " " }),
    h("span", { text: problem }),
    h("p", { class: "hint", text: t("Pick the folder that holds the robot project: build.gradle for Java, robot.py for Python (RobotPy).") })] : []));
  // 2. the checklist
  renderChecks();
  // 3. where it starts
  const st = s.start;
  els.sentence.replaceChildren(h("strong", { text: st.sentence }));
  els.startHint.hidden = st.picked;
  els.pick.textContent = st.picked ? t("Change") : t("Pick on the field");
  els.pick.disabled = ro;
  els.canvas.hidden = !st.picked || !st.pose;
  els.exact.hidden = !st.picked;
  const open = els.exact.open;
  els.exact.replaceChildren(h("summary", {}, t("Exact position")),
    h("p", { class: "start-numbers" }, st.pose
      ? t("x {x} · y {y} · facing {heading}°", { x: units.fmt(st.pose[0], "field"), y: units.fmt(st.pose[1], "field"), heading: fmt(st.pose[2]) })
      : t("As written in the robot file."),
    st.named ? " " + t("(the named start {name})", { name: st.named }) : "",
    " · " + t("field: {field}", { field: st.range === null ? FIELD_NAME.plain() : st.range === "2026" ? FIELD_NAME[2026]() : st.range }),
    st.range === "2026" && !st.floor_balls ? " · " + t("no balls on the floor") : ""));
  els.exact.open = open;
  els.checkBtn.disabled = els.driveBtn.disabled = ro;
  renderRun();
  renderBattery(ro);
  renderDevices();
  if (st.picked && st.pose) loadView().then(drawPicture);
  else window.__scrimmage.code.picture = null;
}

function renderChecks() {
  const s = code.state;
  const fix = code.fix, running = !!(fix && fix.running);
  els.checks.replaceChildren();
  for (const c of s.checks) {
    const failed = code.results[c.id];
    const shown = failed || c;
    const mine = fix && fix.fix === c.fix && c.fix;
    const li = h("li", { class: `check check-${shown.ok === true ? "ok" : shown.ok === false ? "missing" : "info"}`, dataset: { check: c.id } },
      h("span", { class: "check-icon", "aria-hidden": "true", text: ICON[shown.ok] }),
      h("div", { class: "check-body" },
        h("p", { class: "check-text" }, h("strong", { class: "check-word", text: t("{word}:", { word: ICON_WORD[shown.ok]() }) + " " }), h("span", { text: shown.message })),
        mine && running ? h("p", { class: "check-running", role: "status", text: t("Working on it… (this keeps going if you switch tabs)") }) : null,
        mine && running && fix.lines.length ? h("pre", { class: "check-lines", text: fix.lines.slice(-12).join("\n") }) : null,
        failed && failed.detail ? h("pre", { class: "check-detail", text: failed.detail }) : null),
      shown.ok === false && c.fix ? h("button", { type: "button", class: "btn btn-small", dataset: { fix: c.fix },
        disabled: running || (store.state && store.state.read_only), onclick: () => runFix(c.fix) },
      failed ? t("Try again") : FIX_LABEL[c.fix] ? FIX_LABEL[c.fix]() : t("Fix it")) : null);
    els.checks.append(li);
  }
  els.checksNote.textContent = !s.checks.length ? (s.cwd === null ? t("Pick the robot code first; then this says what's ready and what's missing.") : "") : "";
  els.checksNote.hidden = !els.checksNote.textContent;
}

export function renderRun() {
  const r = code.run;
  els.runStatus.hidden = !r;
  if (!r) return;
  els.runStatus.className = `code-run-status code-run-${r.kind}`;
  els.runStatus.replaceChildren(h("span", { class: "check-icon", "aria-hidden": "true", text: r.kind === "ok" ? "✅" : "⚠️" }),
    h("span", {}, h("strong", { text: (r.kind === "ok" ? t("Started.") : t("Didn't start.")) + " " }), r.text),
    ...(r.discard ? [h("button", { type: "button", class: "btn btn-small btn-danger", id: "code-discard", onclick: () => checkDevices(true) }, t("Discard it and start"))] : []),
    ...(r.build ? [h("button", { type: "button", class: "btn btn-small", id: "code-run-build", dataset: { fix: "gradle_build" },
      disabled: !!(code.fix && code.fix.running) || (store.state && store.state.read_only) || undefined,
      onclick: () => { code.run = null; renderRun(); runFix("gradle_build"); } }, FIX_LABEL.gradle_build())] : []));
}

// -- 1. the folder -------------------------------------------------------------------------------------------------
async function toggleBrowse() {
  if (!els.browseBox.hidden) { els.browseBox.hidden = true; return; }
  els.browseBox.hidden = false;
  let dir = null;
  if (code.state && code.state.folder) dir = code.state.folder.replace(/[\\/]+[^\\/]*[\\/]*$/, "") || null;
  if (!dir) {
    const { status, data } = await api("GET", "/api/app/folders");
    if (status === 200 && data.code) dir = data.code;
  }
  browse(dir);
}

async function browse(dir) {
  const data = await browseList(els.browseBox, dir, { noticeId: "code",
    folderButton: { key: "use", label: t("Use this folder"), title: t("This folder holds the robot code"), onclick: (e) => useFolder(e.path) } });
  if (!data) return;
  els.browseBox.append(h("div", { class: "row code-browse-actions" },
    h("button", { type: "button", class: "btn btn-small", id: "code-use-here", onclick: () => useFolder(data.dir) }, t("Use the folder shown")),
    h("button", { type: "button", class: "btn btn-small btn-ghost", onclick: () => { els.browseBox.hidden = true; } }, t("Close"))));
}

async function useFolder(path) {
  const r = await post("/api/robot/code", { folder: path }, { ownErrors: true });
  if (r.ok) {
    code.postError = null;
    code.results = {};
    els.browseBox.hidden = true;
  } else if (!r.conflict) {
    code.postError = (r.errors && r.errors[0] && r.errors[0].problem_text) || t("That folder couldn't be used.");
  }
  await loadState();
}

// -- 2. fixes -------------------------------------------------------------------------------------------------------
async function runFix(fix) {
  const { status, data } = await api("POST", "/api/robot/code/fix", { fix });
  if (!(status === 200 && data.ok)) {
    notice((data.errors || [{}])[0].message || t("the server answered {status}", { status }), "error", "code");
    return;
  }
  for (const c of code.state.checks) if (c.fix === fix) delete code.results[c.id];
  followFix(data.id);
}

// A Language change: the failed fix's result (the server words it again, /api/robot/code/fix) in the new words. A
// result kept from an older fix the server no longer holds goes: its check shows the server's words for it.
async function rewordResults() {
  if (!Object.keys(code.results).length) return;
  const { status, data } = await api("GET", "/api/robot/code/fix");
  if (status !== 200) return;
  const r = data.result && data.result.ok === false ? data.result : null;
  const had = !!r && r.id in code.results;
  for (const id of Object.keys(code.results)) delete code.results[id];
  if (had) code.results[r.id] = r;
  if (els && code.state) render();
}

let following = null;
async function followFix(id) {
  if (following === id) return;
  following = id;
  let since = null;
  for (;;) {
    const { status, data } = await api("GET", "/api/robot/code/fix" + (since === null ? "" : `?since=${since}`));
    if (following !== id) return;
    if (status !== 200) { await new Promise((r) => setTimeout(r, 2000)); continue; }
    since = data.version;
    code.fix = data;
    if (id !== null && data.id !== id) break;
    if (!data.running) {
      if (data.result && data.result.ok === false) code.results[data.result.id] = data.result;
      break;
    }
    if (els && code.state) renderChecks();
  }
  following = null;
  await loadState();
}

// -- the buttons --------------------------------------------------------------------------------------------------
// Java code that hasn't built on this computer (or whose last build failed, or that is building now) would only wait
// minutes for "didn't connect": say so, with the build button right there. The server refuses it too (start/not_built).
function notBuilt() {
  const s = code.state;
  if (!s || !s.info || s.info.kind !== "java") return null;
  if (code.fix && code.fix.running && code.fix.fix === "gradle_build") return t("The robot code is still building. Wait for the build to finish (under Ready to start?), then start again.");
  const c = s.checks.find((x) => x.id === "gradle_built");
  const shown = code.results.gradle_built || c;
  if (!shown || shown.ok !== false) return null;
  return code.results.gradle_built || s.build_failed
    ? t("The robot code didn't build the last time. Fix what the build said (under Ready to start?), then build it again.")
    : t("The robot code hasn't been built on this computer yet. Build it first: the first build downloads the libraries, so it takes a few minutes.");
}

// notBuilt() from a fresh look when the last one said so (it may have built in VS Code since).
async function stillNotBuilt() {
  if (!code.state || notBuilt()) await loadState();
  return notBuilt();
}

function refuseNotBuilt(text) {
  code.run = { kind: "bad", text, build: !(code.fix && code.fix.running) };
  renderRun();
  els.runStatus.scrollIntoView({ block: "nearest" });
}

const liveRun = () => ({ kind: "bad", live: true,
  text: t("The robot is already running. Drive it in the Drive tab, or press Stop the robot and start it again.") });
const busyText = () => t("A scenario is running (the Scenario tab). Wait for it to finish or stop it, then start again.");

async function stopRobot() {
  els.stopBtn.disabled = true;
  const { status, data } = await api("POST", "/api/live/stop", {});
  if (status !== 200) notice(t("Couldn't stop the robot: {why}", { why: (data.errors || [{}])[0].message || status }), "error", "code");
  else code.run = null;
  els.stopBtn.disabled = false;
  renderRun();
}

async function ensurePicked() {
  const s = code.state || await loadState();
  if (s && s.start.picked) return true;
  return pickStart();
}

async function checkDevices(discard = false) {
  const nb = await stillNotBuilt();
  if (nb) { refuseNotBuilt(nb); return; }
  if (code.devices && code.devices.live) { code.run = liveRun(); renderRun(); return; }
  if (!(await ensurePicked())) return;
  els.checkBtn.disabled = true;
  try {
    // the Drive tab's Driver Station choice, once the student has picked it there; else null, the config's default
    const ds = $("#drive-ds-source");
    const realDs = ds && ds.dataset.touched ? ds.value === "real" : null;
    const { status, data } = await api("POST", "/api/robot/check", { discard_recording: discard, real_ds: realDs });
    const errors = data.errors || [];
    if (status === 200 && data.ok) {
      // the pick, else the config's default (a Drive tab pick not made yet)
      let driven = realDs;
      if (driven === null) {
        const info = await api("GET", "/api/live/info");
        driven = info.status === 200 && !!info.data.real_ds_default;
      }
      code.run = { kind: "ok", text: driven
        ? t("The robot is running, disabled, so scrimmage can see the devices your code makes. Enable it in the FRC Driver Station app.")
        : t("The robot is running, disabled, so scrimmage can see the devices your code makes. Enable it in the Drive tab to drive it.") };
    } else if (errors.some((e) => e.field === "start/unpicked")) {
      await loadState();
      if (await pickStart()) return checkDevices(discard);
      return;
    } else if (errors.some((e) => e.field === "live/unsaved")) {
      code.run = { kind: "bad", text: t("The last drive's recording hasn't been saved. Save it in the Drive tab (Save as scenario…), or discard it."), discard: true };
    } else if (errors.some((e) => e.field === "start/not_built")) {
      await loadState();
      code.run = { kind: "bad", text: errors.find((e) => e.field === "start/not_built").message, build: !(code.fix && code.fix.running) };
    } else if (status === 409 && errors.some((e) => e.field === "live/already")) {
      code.run = liveRun();
    } else if (status === 409 && errors.some((e) => e.field === "busy/run")) {
      code.run = { kind: "bad", text: busyText() };
    } else {
      code.run = { kind: "bad", text: errors.map((e) => e.message).join("; ") || t("the server answered {status}", { status }) };
    }
  } finally {
    els.checkBtn.disabled = !!(store.state && store.state.read_only);
    renderRun();
  }
}

async function driveIt() {
  const nb = await stillNotBuilt();
  if (nb) { refuseNotBuilt(nb); return; }
  if (!(await ensurePicked())) return;
  try {
    const m = await import("./drive.js");
    await m.driveFromPanel();
  } catch (e) {
    console.error(e);
    setTab("drive");
  }
}

// -- showing the panel ----------------------------------------------------------------------------------------------
// The Drive tab's "Go to Robot code" (its Start was refused: the code hasn't built): the panel's checklist, Build now.
export function showChecks() {
  setTab("robot");
  showCode(true);
  if (els) els.checks.scrollIntoView({ block: "start" });
}

// The Drive tab's "see Robot code": the Robot tab, the panel, its device table.
export function showDevices() {
  setTab("robot");
  showCode(true);
  if (els) els.devices.scrollIntoView({ block: "start" });
}

export function showCode(on) {
  code.shown = on;
  $("#code-panel").hidden = !on;
  $("#view").hidden = on;
  for (const [id, sel] of [["#center-3d", !on], ["#center-code", on]]) {
    const b = $(id);
    b.setAttribute("aria-selected", String(sel));
    b.tabIndex = sel ? 0 : -1;
    b.classList.toggle("active", sel);
  }
  if (on) {
    if (!els) build();
    loadState();
  }
}

export function initCode() {
  window.__scrimmage.code = { get state() { return code.state; }, get devices() { return code.devices; }, get picking() { return !!picking; },
    picture: null, show: () => showCode(true), showDevices };
  units.onChange(() => render());   // the Exact position line (the battery boxes keep what is being typed)
  i18n.onChange(() => {             // likewise: the panel's fixed words, then the rest
    for (const [node, fn] of relabels) node.data = fn();
    render();
    if (code.shown) loadState();    // the checklist and the start sentence are the server's words
    rewordResults();                // and so is a failed fix's result, kept beside its check
  });
  theme.onChange(() => drawPicture());   // the start's picture, in the theme's colors
  $("#center-3d").addEventListener("click", () => showCode(false));
  $("#center-code").addEventListener("click", () => showCode(true));
  $(".center-switch").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    showCode(!code.shown);
    $(code.shown ? "#center-code" : "#center-3d").focus();
  });
  // picking a mount for a new part needs the 3D view
  window.addEventListener("scrimmage:pick-mount", (e) => { if (e.detail.active && code.shown) showCode(false); });
  window.addEventListener("scrimmage:state", () => { if (code.shown) reloadSoon(); });
  pollDevices();
  // a fix may be running from before a reload
  api("GET", "/api/robot/code/fix").then(({ status, data }) => { if (status === 200 && data.running) { code.fix = data; followFix(data.id); } });
}
