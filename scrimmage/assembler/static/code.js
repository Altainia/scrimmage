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
// The start picker (pickStart(), also used by the Drive tab's Start): the field (2026 or the plain red / blue
// field; it is also the field the robot drives on, so choosing one writes [range] at once), the range's named
// starts as quick choices, a "No balls on the floor" switch with its reason on the 2026 field, and the field
// drawing (fieldpick.js): click to place the robot, keep the button down and drag to turn it; "Exact position"
// holds the numbers. OK writes [robot] start_pose (the op `start`, one undo step). See server.py for the API.
//
// The device check (under the buttons, once a check or a drive has run): every device the model expects beside
// what the code made (long-polled GET /api/robot/devices; it fills in as the code makes devices, teleop's too).
// Status is an icon and a bold word: ✅ matches, ⏳ waiting, ❌ nothing with this ID (with "Use …" choices: devices of
// the same type the code made that nothing uses), ➕ not in the model ("Add as…" opens a part's device IDs in the
// inspector; nothing is written), ↔ another type (a link to the part), – not checked yet (PWM, DIO, analog...).
// "Apply the matches and restart" writes the chosen IDs (op match_devices, one undo step) and restarts the robot
// ("restarting", then what the device check's restart says: restarted, or failed and why).
// "More settings" holds the [battery] fields (op battery). The Drive tab shows a line while devices don't match.
//
// Test hooks: window.__scrimmage.code = {state (the last /api/robot/code), devices (the last /api/robot/devices), picking (the picker is open or undoing), picture: {pose, footprint, triangle} (field
// meters), labels: [the field's boxes labeled in it]} while a start is shown}; window.__scrimmage.startPicker = {toScreen(x, y) -> page pixels, value} while the
// picker is open.

import { $, h, api, edit, post, undo, notice, setTab, store, fmt, select, showMeasure, readMeasure, measureError } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { i18n, t, tn } from "./i18n.js";
import { markDirty, clean, onCommit, badNumber } from "./inspector.js";
import { browseList } from "./start.js";
import { fieldGeometry, drawField, drawSides, fieldPoly, robotFootprint } from "./fieldpick.js";

// The robot is drawn in a color nothing on a field uses (dark slate with a white triangle; light gray with a dark one
// in the dark theme: app.css --field-robot*), so it can't be mistaken for the plain field's amber practice box or a
// game piece, on either alliance's half.
const ROBOT = { fill: ["--field-robot", 0.82], stroke: ["--field-robot-edge"], tri: ["--field-robot-mark"],
  ghostFill: ["--field-robot", 0.35] };
const robotColor = (which) => theme.color(...ROBOT[which]);

const ICON = { true: "✅", false: "⚠️", null: "ℹ️" };
const ICON_WORD = { true: () => t("Ready"), false: () => t("Missing"), null: () => t("Good to know") };
const FIX_LABEL = { build_agent: () => t("Build it (about 10 s)"), gradle_build: () => t("Build now (a few minutes)"),
  install_lockstep: () => t("Install it") };
const FIELD_NAME = { 2026: () => t("2026 field (REBUILT)"), plain: () => t("Plain field (red / blue)") };
const floorWhy = () => t("The 2026 field starts with 360 FUEL balls on the floor, which makes the simulation run slower.");

const code = {
  state: null,          // GET /api/robot/code
  seq: 0,               // the latest load asked (older answers are dropped)
  fix: null,            // GET /api/robot/code/fix, while one is running or after one ran
  results: {},          // check id -> the failed result of its last fix (its message and output stay shown)
  postError: null,      // why the last folder pick wasn't used
  view: null, viewVersion: null,   // /api/live/view for the robot document, and the robot version it is for
  shown: false,
  run: null,            // {text, kind, discard?} the last Start and check devices answer
  devices: null,        // GET /api/robot/devices
  matches: {},          // slot -> the device chosen for it ("Use …"), until applied
  applied: null,        // {kind: ok|bad, text}: the last Apply's answer
  adding: null,         // the extra device whose "Add as…" part chooser is open
  addSelect: null,      // that chooser (kept across redraws, so a choice being made isn't lost)
};

const DEV = {
  ok: ["✅", () => t("Matches")], waiting: ["⏳", () => t("Waiting")], missing: ["❌", () => t("Nothing with this ID")],
  extra: ["➕", () => t("Not in the model")], other_type: ["↔", () => t("Another type")], not_checked: ["–", () => t("Not checked yet")],
};
const SHORT_KIND = { TalonFX: "TalonFX", CANcoder: "CANcoder", Pigeon2: "Pigeon2", "SPARK MAX": "SparkMax", "SPARK Flex": "SparkFlex" };
const BATTERY = [
  { key: "nominal_voltage", id: "code-battery-voltage", label: () => t("Battery voltage"), unit: "V", step: "0.1",
    help: () => t("The battery's voltage when it is charged and resting. A fresh competition battery reads about 12.5 to 13 V.") },
  { key: "resistance", id: "code-battery-resistance", label: () => t("Battery resistance"), unit: "Ω", step: "0.001",
    help: () => t("How much the voltage drops when the motors pull hard. About 0.02 Ω is a good battery, an old one 0.03 Ω or more; 0 means it never drops.") },
];

// -- loading ---------------------------------------------------------------------------------------------------
async function loadState() {
  const seq = ++code.seq;
  const { status, data } = await api("GET", "/api/robot/code");
  if (seq !== code.seq || status !== 200) return code.state;
  code.state = data;
  for (const c of data.checks) if (c.ok === true) delete code.results[c.id];   // it has been fixed since (by hand, say)
  window.dispatchEvent(new CustomEvent("scrimmage:code", { detail: { state: data } }));
  render();
  return data;
}

async function loadView(force = false) {
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
let els = null;
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
    h("p", { class: "hint" }, L(() => t("How the simulated battery behaves ([battery] in the robot file). Leave a box empty for the default."))));
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
  const problem = code.postError || s.problem;
  els.problem.hidden = !problem;
  els.problem.replaceChildren(...(problem ? [h("strong", { text: (code.postError ? t("Not used.") : t("Can't use the robot code.")) + " " }),
    h("span", { text: upper(problem) + (/[.!?]$/.test(problem) ? "" : ".") }),
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

function renderRun() {
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

const upper = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// -- the device check -------------------------------------------------------------------------------------------
async function pollDevices() {
  let since = null;
  for (;;) {
    const { status, data } = await api("GET", "/api/robot/devices" + (since === null ? "" : `?since=${since}`));
    if (status !== 200) { since = null; await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 3000)); continue; }
    since = data.version;
    code.devices = data;
    window.dispatchEvent(new CustomEvent("scrimmage:devices", { detail: { devices: data } }));
    renderDevices();
  }
}

const partOf = (slot) => slot.split(".")[0];
const slotKeyOf = (slot) => slot.slice(slot.indexOf(".") + 1).replace(/\[\d+\]$/, "");
const categoryOf = (kind) => (/encoder|CANcoder/i.test(kind) ? "encoder" : /gyro|Pigeon/i.test(kind) ? "gyro" : "motor");
function shortText(made) {   // "TalonFX 1" -> "TalonFX:1", as the part inspector's short form writes it
  const m = /^(.*) (\d+)$/.exec(made || "");
  return m && SHORT_KIND[m[1]] ? `${SHORT_KIND[m[1]]}:${m[2]}` : made;
}

function renderDevices() {
  if (!els) return;
  const d = code.devices, rows = d ? d.rows : [];
  els.stopBtn.hidden = !(d && d.live);   // a running robot blocks opening something else and scenario runs
  if (code.run && code.run.live && !(d && d.live)) { code.run = null; renderRun(); }
  restartOutcome(d);
  els.devices.hidden = !(d && (rows.length || d.live));
  if (els.devices.hidden) return;
  const ro = !!(store.state && store.state.read_only);
  // drop choices that no longer apply (the row matches now, or the device went)
  for (const [slot, dev] of Object.entries(code.matches)) {
    const r = rows.find((x) => x.slot === slot);
    if (!r || r.status !== "missing" || !r.suggest.includes(dev)) delete code.matches[slot];
  }
  const bad = rows.filter((r) => r.status === "missing" || r.status === "other_type").length;
  els.devState.replaceChildren(
    ...(bad ? [h("strong", { class: "dev-bad-count", text: tn("{count} device doesn't match the model.", "{count} devices don't match the model.", bad) + " " })] : []),
    d.live && !d.settled ? t("Checking… the robot has run {time} s.", { time: fmt(d.robot_time) }) + " " : "",
    d.live ? t("The robot is running: devices your code makes later (in teleop, say) show up here as they appear.")
      : t("From the last time the robot ran."));
  els.devBody.replaceChildren(...rows.map((r) => deviceRow(r, ro)));
  const n = Object.keys(code.matches).length;
  els.apply.disabled = ro || !n;
  els.apply.textContent = d.live ? t("Apply the matches and restart") : t("Apply the matches");
  const a = code.applied;
  els.applyStatus.hidden = !a;
  if (a) {
    els.applyStatus.className = `code-apply-status code-apply-${a.kind}`;
    els.applyStatus.replaceChildren(h("span", { class: "dev-icon", "aria-hidden": "true", text: a.kind === "ok" ? "✅" : "⚠️" }), h("span", { text: a.text }));
  }
}

function deviceRow(r, ro) {
  const [icon, word0] = DEV[r.status] || ["", () => r.status];
  const word = r.status === "missing" && r.made ? t("Not connected") : word0();
  const actions = h("div", { class: "dev-actions" });
  if (r.status === "missing") {
    for (const s of r.suggest) {
      const chosen = code.matches[r.slot] === s;
      actions.append(h("button", { type: "button", class: `btn btn-small${chosen ? " btn-primary-soft" : ""}`, dataset: { use: s },
        "aria-pressed": String(chosen), disabled: ro || undefined, onclick: () => choose(r.slot, s) }, t("Use {device}", { device: s })));
    }
  } else if (r.status === "other_type") {
    actions.append(h("button", { type: "button", class: "linklike", dataset: { part: partOf(r.slot) },
      onclick: () => openPart(partOf(r.slot), { slot: slotKeyOf(r.slot) }) }, t("Open {part} in the part inspector", { part: partOf(r.slot) })));
  } else if (r.status === "extra") {
    actions.append(h("button", { type: "button", class: "btn btn-small", dataset: { add: r.made }, disabled: ro || undefined,
      onclick: () => { code.adding = code.adding === r.made ? null : r.made; code.addSelect = null; renderDevices(); } }, t("Add as…")));
    if (code.adding === r.made) actions.append(addChooser(r));
  }
  return h("tr", { class: `dev-row dev-${r.status}`, dataset: { slot: r.slot || "", status: r.status, made: r.made || "" } },
    h("td", { class: "mono" }, r.slot ? r.slot : h("span", { class: "muted", text: t("(none)") })),
    h("td", {}, r.expected || h("span", { class: "muted", text: "–" })),
    h("td", {}, r.made || h("span", { class: "muted", text: t("nothing") })),
    h("td", { class: "dev-status" },
      h("p", { class: "dev-head" }, h("span", { class: "dev-icon", "aria-hidden": "true", text: icon }), h("strong", { class: "dev-word", text: word })),
      r.note ? h("p", { class: "dev-note", text: r.note + (/[.!?]$/.test(r.note) ? "" : ".") }) : null,   // it starts with the slot's name: kept as written
      actions.children.length ? actions : null));
}

function choose(slot, dev) {
  if (code.matches[slot] === dev) delete code.matches[slot];
  else {
    for (const [k, v] of Object.entries(code.matches)) if (v === dev) delete code.matches[k];   // one slot per device
    code.matches[slot] = dev;
  }
  code.applied = null;
  renderDevices();
}

// "Add as…": which part the device is on; that part's device IDs then open in the inspector, for the student to type it in.
function addChooser(r) {
  if (!code.addSelect) {
    const sel = h("select", { id: "code-add-part", "aria-label": t("Which part is {device} on?", { device: r.made }) },
      h("option", { value: "" }, t("Which part is it on?")),
      ...((store.state && store.state.parts) || []).map((p) => h("option", { value: p.name }, p.name)));
    sel.addEventListener("change", () => {
      if (!sel.isConnected || !sel.value) return;
      const made = code.adding, part = sel.value;
      code.adding = null; code.addSelect = null;
      renderDevices();
      openPart(part, { kind: categoryOf(r.kind) });
      notice(t("Type {device} into the slot of {part} it belongs to (Device IDs, in the inspector).", { device: shortText(made), part }), "info", "code-add");
    });
    code.addSelect = sel;
  }
  return h("span", { class: "dev-add" }, code.addSelect);
}

// Select a part and put the inspector's cursor on one of its device slots (by name, or the first of a kind).
function openPart(name, { slot = null, kind = null } = {}) {
  if (!select(name)) { notice(t("There is no part named {name}.", { name }), "warn"); return; }
  const fields = [...document.querySelectorAll(`#inspector [data-field^="${CSS.escape(name)}/ids:"]`)];
  const f = fields.find((x) => (slot ? x.dataset.field === `${name}/ids:${slot}`
    : ((x.querySelector(".kind") || {}).dataset || {}).kind === kind)) || fields[0];
  const target = f ? f.querySelector("input, select") : document.querySelector(`#inspector [data-field="${CSS.escape(name)}/ids"]`);
  if (!target) return;
  target.scrollIntoView({ block: "center" });
  target.focus();
}

async function applyMatches() {
  const matches = { ...code.matches };
  if (!Object.keys(matches).length) return;
  els.apply.disabled = true;
  const r = await edit("match_devices", { matches }, { ownErrors: true });
  if (r.ok) {
    code.matches = {};
    const d = r.data || {};
    code.applied = d.restarting ? { kind: "ok", text: t("Saved. The robot is restarting with the new IDs…"), restarting: true }
      : d.restart_problem ? { kind: "bad", text: t("Saved, but the robot wasn't restarted: {why}", { why: d.restart_problem }) }
      : { kind: "ok", text: t("Saved. Start and check devices again to see them match.") };
  } else if (!r.conflict) {
    code.applied = { kind: "bad", text: (r.errors || []).map((e) => e.message).join("; ") || t("The matches weren't saved.") };
  } else code.applied = null;
  renderDevices();
}

// What the restart after an Apply came to, from the device check (its restart: restarting, restarted or failed).
function restartOutcome(d) {
  const a = code.applied;
  if (!a || !a.restarting || !d) return;
  if (d.restart === "restarted") {
    code.applied = { kind: "ok", text: t("Saved. The robot restarted with the new IDs; the table fills in again as it starts.") };
  } else if (d.restart === "failed") {
    code.applied = { kind: "bad", text: t("Saved, but the robot didn't start again: {why}. Press Start and check devices.", { why: d.restart_problem || t("it stopped") }) };
  }
}

// -- more settings: the battery ----------------------------------------------------------------------------------
function renderBattery(ro) {
  const b = code.state && code.state.battery;
  if (!b) return;
  for (const f of BATTERY) {
    const inp = els.battery[f.key];
    inp.disabled = ro;
    inp.placeholder = t("{value} (the default)", { value: fmt(b.defaults[f.key]) });
    // never overwrite what the student is typing (a redraw comes with every state change)
    if (document.activeElement !== inp && !inp.dataset.dirty) inp.value = b[f.key] === null || b[f.key] === undefined ? "" : fmt(b[f.key]);
  }
}

async function saveBattery(f, inp) {
  const box = inp.closest(".field").querySelector(":scope > .field-msgs");
  const say = (msg) => box.replaceChildren(...(msg ? [h("p", { class: "error", role: "alert", text: msg })] : []));
  clean(inp);
  if (badNumber(inp)) { say(t("Enter a number.")); return; }
  const v = inp.value.trim() === "" ? null : Number(inp.value);
  say(null);
  const r = await edit("battery", { [f.key]: v }, { ownErrors: true });
  if (!r.ok && !r.conflict) say((r.errors || []).map((e) => e.message).join("; ") || t("That wasn't saved."));
  if (r.ok) await loadState();
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
    code.postError = (r.errors || []).map((e) => e.message).join("; ") || t("that folder couldn't be used");
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

// -- 3. the start picture ------------------------------------------------------------------------------------------
function triangleOf(foot) {
  const r = foot.length ? Math.max(0.12, Math.min(Math.max(...foot.map((p) => p[0])), Math.max(...foot.map((p) => p[1])))) : 0.3;
  return [[0.8 * r, 0], [-0.5 * r, 0.5 * r], [-0.5 * r, -0.5 * r]];
}

const place = (pts, [x, y, deg]) => {
  const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  return pts.map(([u, v]) => [x + u * c - v * s, y + u * s + v * c]);
};

// The robot (its footprint, or a 0.8 m square when the view has none) at pose, with its heading triangle.
function drawRobot(ctx, toPx, foot, pose, { ghost = false } = {}) {
  const body = foot.length >= 3 ? foot : [[-0.4, -0.4], [0.4, -0.4], [0.4, 0.4], [-0.4, 0.4]];
  const at = place(body, pose), tri = place(triangleOf(body), pose);
  fieldPoly(ctx, toPx, at, robotColor(ghost ? "ghostFill" : "fill"), robotColor("stroke"), 2, ghost ? [5, 3] : null);
  fieldPoly(ctx, toPx, tri, robotColor("tri"), robotColor("stroke"), 1);
  return { footprint: at, triangle: tri };
}

// Names on the field's own boxes that could be taken for something else: the plain field's practice obstacle.
const BOX_LABEL = { obstacle: () => t("practice box") };
function labelBoxes(ctx, geo, toPx) {
  const out = [];
  for (const o of geo.outlines) {
    if (!BOX_LABEL[o.name]) continue;
    const label = BOX_LABEL[o.name]();
    const cx = o.poly.reduce((a, q) => a + q[0], 0) / o.poly.length, cy = o.poly.reduce((a, q) => a + q[1], 0) / o.poly.length;
    const [px, py] = toPx(cx, cy);
    ctx.save();
    ctx.font = "600 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = theme.color("--text");
    ctx.fillText(label, px, py);
    ctx.restore();
    out.push(label);
  }
  return out;
}

function fieldFor(view) {
  return { geo: fieldGeometry(view), foot: robotFootprint(view) };
}

function drawPicture() {
  const s = code.state;
  if (!els || !s || !s.start.picked || !s.start.pose || !code.view || els.canvas.hidden) return;
  const { geo, foot } = fieldFor(code.view);
  const b = geo.floor.bounds;
  const W = Math.max(200, Math.min(640, Math.floor(els.canvas.parentElement.clientWidth || 480)));
  const scale = W / (b.maxx - b.minx), H = Math.round((b.maxy - b.miny) * scale);
  const dpr = window.devicePixelRatio || 1;
  els.canvas.width = Math.round(W * dpr); els.canvas.height = Math.round(H * dpr);
  els.canvas.style.width = `${W}px`; els.canvas.style.height = `${H}px`;
  const ctx = els.canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const toPx = (x, y) => [(x - b.minx) * scale, (b.maxy - y) * scale];
  ctx.fillStyle = theme.color("--field-surround"); ctx.fillRect(0, 0, W, H);
  drawField(ctx, geo, toPx);
  const labels = labelBoxes(ctx, geo, toPx);
  if (s.start.range === null) drawSides(ctx, geo, toPx);
  const pic = drawRobot(ctx, toPx, foot, s.start.pose);
  els.canvas.setAttribute("aria-label", t("{start}: the field seen from above, with the robot at its real size and a triangle pointing the way it faces.", { start: s.start.sentence }));
  window.__scrimmage.code.picture = { pose: [...s.start.pose], ...pic, labels };
}

// -- the start picker ----------------------------------------------------------------------------------------------
let dlg = null;
let picking = null;     // the open picker's promise, so a second ask joins it

export function pickStart() {
  if (picking) return picking;
  picking = openPicker().finally(() => { picking = null; });
  return picking;
}

const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const normDeg = (d) => { let a = ((d + 180) % 360 + 360) % 360 - 180; if (a === -180) a = 180; return a; };

async function openPicker() {
  if (store.state && store.state.read_only) {
    notice(t("This robot is read-only: {why}", { why: store.state.read_only_reason }), "warn");
    return false;
  }
  await loadState();
  if (!code.state) return false;
  if (!dlg) {
    dlg = h("dialog", { id: "start-dialog", class: "start-dialog", "aria-labelledby": "sp-title" });
    document.body.append(dlg);
  }
  const d = dlg;
  const st0 = code.state.start;
  const cur = { x: st0.pose ? st0.pose[0] : null, y: st0.pose ? st0.pose[1] : null, heading: st0.pose ? st0.pose[2] : 0 };
  let view = await loadView(), geo = null, foot = [], W = 0, H = 0, scale = 1, b = null;
  let changingField = false;

  return new Promise((resolve) => {
    let done = false;
    // Cancel means cancel: the field and floor-ball choices were written as they were made (the field drawing
    // comes from the robot's world), so closing without OK undoes each of them, newest first. Only the picker's
    // own: before each undo the robot must still be at the version the picker's last write (or undo) left; when
    // someone else (another tab, the agent, a text editor) changed it meanwhile, nothing more is undone.
    const written = [];    // the versions the picker's writes returned, oldest first
    let inFlight = Promise.resolve();   // the picker's writes still on their way: Cancel waits for them, since the
                                        // page can show a write (the robot's refresh) before its version is recorded
    const finish = async (ok) => {
      if (done) return;
      done = true;
      if (d.open) d.close();
      await inFlight;
      if (!ok && written.length) {
        let expect = written[written.length - 1];
        for (let n = written.length; n > 0; n--) {
          const { status, data } = await api("GET", "/api/robot/state");
          if (status !== 200 || data.version !== expect) {
            notice(t("Someone else changed the robot while the start picker was open, so the field wasn't put back. Use Undo if you want to."), "warn", "start-cancel");
            break;
          }
          const r = await undo(expect);   // refused if someone else's edit lands between the check and the undo
          if (!r.ok) { notice(t("Couldn't put the field back after Cancel; use Undo if you want to."), "warn", "start-cancel"); break; }
          expect = r.version;
        }
        await loadState();
      }
      resolve(ok);
    };
    d.onclose = () => { delete window.__scrimmage.startPicker; offUnits(); offTheme(); offLanguage(); finish(false); };

    const err = h("ul", { id: "sp-errors", class: "error-list", role: "alert", hidden: true });
    const showErrors = (errors) => {
      err.replaceChildren(...errors.map((e) => h("li", { text: e.message })));
      err.hidden = !errors.length;
    };
    const canvas = h("canvas", { id: "start-field-canvas", class: "field-canvas", tabindex: "0", role: "application",
      "aria-label": t("Field, seen from above. Click where the robot starts; keep the button down and drag to turn it. Arrow keys move it, [ and ] turn it.") });
    const ctx = canvas.getContext("2d");
    // x and y are field lengths in the chosen units (units.js): text boxes, so "12.5 ft" can be typed too
    // the picker's fixed words, done again on a language change (what is typed in its boxes stays)
    const words = [];
    const W_ = (fn) => { const node = document.createTextNode(fn()); words.push([node, fn]); return node; };
    const num = (id, label, unit, step, kind) => {
      const el = kind ? h("input", { id, type: "text", inputmode: "decimal", autocomplete: "off", class: "fp-num", "aria-label": label() })
        : h("input", { id, type: "number", step, inputmode: "decimal", class: "fp-num", "aria-label": label() });
      words.push([{ set data(v) { el.setAttribute("aria-label", v); } }, label]);
      const u = h("span", { class: "unit" }, unit);
      return [el, h("label", { class: "fp-field", for: id }, h("span", { class: "fp-label sp-label" }, W_(label)), h("span", { class: "with-unit" }, el, u)), u];
    };
    const [xIn, xF, xU] = num("sp-x", () => t("x (from the blue wall)"), units.name("field"), null, "field");
    const [yIn, yF, yU] = num("sp-y", () => t("y (from the right side, seen from blue)"), units.name("field"), null, "field");
    const [hIn, hF] = num("sp-heading", () => t("facing"), "°", "1");
    // a box being typed in (its text isn't what it showed): kept across a change of units
    const typing = (el) => document.activeElement === el && el.value.trim() !== el.dataset.shown;
    const sync = ({ keepTyping = false } = {}) => {
      for (const [el, k] of [[xIn, "x"], [yIn, "y"]]) if (!(keepTyping && typing(el))) showMeasure(el, cur[k], "field");
      hIn.value = fmt(round(cur.heading, 1));
    };
    // Units changed in another tab or editor while this is open: the boxes show the new units. Text being typed in
    // stays and now means the new units, as in the inspector and the step editor (it is read again here and at OK).
    const offUnits = units.onChange(() => {
      xU.textContent = yU.textContent = units.name("field");
      const kept = [xIn, yIn].filter(typing);
      sync({ keepTyping: true });
      for (const el of kept) el.dispatchEvent(new Event("input"));
      draw();   // the grid's labels are in the chosen units
    });
    const offTheme = theme.onChange(() => draw());
    const offLanguage = i18n.onChange(() => {
      for (const [node, fn] of words) node.data = fn();
      canvas.setAttribute("aria-label", t("Field, seen from above. Click where the robot starts; keep the button down and drag to turn it. Arrow keys move it, [ and ] turn it."));
      renderTop(); draw();
    });
    for (const [el, k] of [[xIn, "x"], [yIn, "y"], [hIn, "heading"]]) {
      el.addEventListener("input", () => {
        if (el.validity.badInput || el.value.trim() === "") return;
        if (k !== "heading") {
          const v = readMeasure(el, "field");   // not a field length yet: OK says what to type
          if (typeof v !== "number") return;
          cur[k] = v;
        } else cur[k] = Number(el.value);
        if (k !== "heading" && cur.x !== null && cur.y === null) cur.y = (geo.floor.miny + geo.floor.maxy) / 2;
        if (k !== "heading" && cur.y !== null && cur.x === null) cur.x = (geo.floor.minx + geo.floor.maxx) / 2;
        draw();
      });
    }
    const exact = h("details", { id: "sp-exact", class: "sp-exact" }, h("summary", {}, W_(() => t("Exact position"))),
      h("div", { class: "fp-row" }, xF, yF, hF),
      h("p", { class: "hint" }, W_(() => t("Measured from the blue alliance's corner of the field; facing 0° is toward the red wall, 90° to the left."))));
    const top = h("div", { class: "sp-top" });

    function layout() {
      ({ geo, foot } = fieldFor(view));
      b = geo.floor.bounds;
      const maxW = Math.min(980, window.innerWidth * 0.92 - 48), maxH = Math.max(220, window.innerHeight * 0.92 - 330);
      scale = Math.min(maxW / (b.maxx - b.minx), maxH / (b.maxy - b.miny));
      W = Math.round((b.maxx - b.minx) * scale); H = Math.round((b.maxy - b.miny) * scale);
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const toPx = (x, y) => [(x - b.minx) * scale, (b.maxy - y) * scale];
    const toField = (px, py) => [b.minx + px / scale, b.maxy - py / scale];

    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = theme.color("--field-surround"); ctx.fillRect(0, 0, W, H);
      drawField(ctx, geo, toPx);
      labelBoxes(ctx, geo, toPx);
      if (code.state.start.range === null) drawSides(ctx, geo, toPx);
      if (cur.x !== null && cur.y !== null) drawRobot(ctx, toPx, foot, [cur.x, cur.y, cur.heading], { ghost: !placed });
    }
    let placed = !!st0.pose;

    // the field choice, the floor balls and the named starts: written as they are chosen
    function renderTop() {
      const st = code.state.start;
      const radio = (value, label) => h("label", { class: "sp-choice" },
        h("input", { type: "radio", name: "sp-field", id: `sp-field-${value}`, value,
          checked: (value === "plain" ? st.range === null : st.range === value) || undefined,
          onchange: () => chooseField(value) }), h("span", { text: label }));
      const rows = [];
      if (st.range_read_only && !changingField) {
        rows.push(h("p", { class: "sp-field-row" }, h("strong", { text: t("Field:") + " " }), h("span", { class: "mono", text: st.range }),
          h("span", { class: "muted", text: " " + t("(named in the robot file)") + " " }),
          h("button", { type: "button", class: "btn btn-small", id: "sp-field-change", onclick: () => { changingField = true; renderTop(); } }, t("Change"))));
      } else {
        rows.push(h("fieldset", { class: "sp-field-row" }, h("legend", { class: "sp-legend" }, t("Field")),
          radio("2026", FIELD_NAME[2026]()), radio("plain", FIELD_NAME.plain()),
          h("p", { class: "hint", text: t("It's also the field the robot drives on.") })));
      }
      if (st.range === "2026") {
        const sw = h("input", { type: "checkbox", id: "sp-floor-balls", checked: !st.floor_balls || undefined,
          onchange: () => floorBalls(!sw.checked) });
        rows.push(h("div", { class: "sp-floor" }, h("label", { class: "sp-choice", for: "sp-floor-balls" }, sw, h("strong", { text: t("No balls on the floor") })),
          h("p", { class: "hint", id: "sp-floor-why", text: floorWhy() })));
      }
      if (st.named_starts.length) {
        rows.push(h("div", { class: "sp-named" }, h("strong", { class: "sp-legend", text: t("Quick choices:") + " " }),
          ...st.named_starts.map((n) => h("button", { type: "button", class: `btn btn-small${st.named === n ? " btn-primary-soft" : ""}`,
            dataset: { named: n }, onclick: () => named(n) }, n.replace(/_/g, " ")))));
      }
      top.replaceChildren(...rows);
    }

    function apply(fields) {
      const go = (async () => {
        showErrors([]);
        const r = await edit("start", fields, { ownErrors: true });
        if (!r.ok) { if (!r.conflict) showErrors(r.errors || []); return false; }
        written.push(r.version);
        await loadState();
        return true;
      })();
      inFlight = inFlight.then(() => go, () => go).catch(() => {});
      return go;
    }
    async function chooseField(value) {
      if (!(await apply({ range: value === "2026" ? "2026" : null }))) { renderTop(); return; }
      changingField = false;
      renderTop();
      view = await loadView(true);
      layout(); draw();
    }
    async function floorBalls(on) {
      if (!(await apply({ floor_balls: on }))) renderTop();
    }
    async function named(n) {
      if (await apply({ named: n })) finish(true);
    }

    // pointer: press to place, keep the button down and drag to turn
    let drag = null;
    const at = (e) => { const rc = canvas.getBoundingClientRect(); return [e.clientX - rc.left, e.clientY - rc.top]; };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      canvas.focus();
      const [px, py] = at(e);
      const [x, y] = toField(px, py);
      cur.x = round(x, 2); cur.y = round(y, 2);
      placed = true;
      drag = { px, py, aiming: false };
      canvas.setPointerCapture(e.pointerId);
      sync(); draw();
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const [px, py] = at(e);
      if (!drag.aiming && Math.hypot(px - drag.px, py - drag.py) < 6) return;
      drag.aiming = true;
      const [tx, ty] = toPx(cur.x, cur.y);
      cur.heading = round(normDeg(Math.atan2(-(py - ty), px - tx) * 180 / Math.PI), 0);
      sync(); draw();
    });
    const endDrag = () => { drag = null; };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("keydown", (e) => {
      const stepM = e.shiftKey ? 0.5 : 0.05, stepD = e.shiftKey ? 15 : 5;
      const move = { ArrowLeft: [-stepM, 0], ArrowRight: [stepM, 0], ArrowUp: [0, stepM], ArrowDown: [0, -stepM] }[e.key];
      if (move) {
        if (cur.x === null) { cur.x = (geo.floor.minx + geo.floor.maxx) / 4; cur.y = (geo.floor.miny + geo.floor.maxy) / 2; }
        cur.x = round(cur.x + move[0], 3); cur.y = round(cur.y + move[1], 3);
      } else if (e.key === "[" || e.key === "]") {
        if (cur.x === null) return;
        cur.heading = normDeg(cur.heading + (e.key === "[" ? stepD : -stepD));
      } else return;
      placed = true;
      e.preventDefault();
      sync(); draw();
    });

    const form = h("form", { method: "dialog", novalidate: true },
      h("h2", { id: "sp-title" }, W_(() => t("Where does the robot start?"))),
      top,
      h("p", { class: "muted fp-help" }, W_(() => t("Click the field where the robot starts; keep the button down and drag to turn it the way it faces. The triangle shows its front."))),
      h("div", { class: "fp-canvas-wrap" }, canvas),
      exact,
      err,
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", id: "sp-cancel", class: "btn", onclick: () => finish(false) }, W_(() => t("Cancel"))),
        h("button", { type: "submit", id: "sp-ok", class: "btn btn-primary" }, W_(() => t("OK")))));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (hIn.validity.badInput) { showErrors([{ message: t("{box} must be a number", { box: hIn.getAttribute("aria-label") }) }]); hIn.focus(); return; }
      // x and y as their boxes say now (an empty box keeps the picked value)
      for (const [el, k] of [[xIn, "x"], [yIn, "y"]]) {
        const v = readMeasure(el, "field");
        if (measureError(v)) { showErrors([{ message: measureError(v) }]); el.focus(); return; }
        if (v !== null) cur[k] = v;
      }
      if (cur.x === null || cur.y === null || !placed) { showErrors([{ message: t("Click the field where the robot starts first.") }]); return; }
      // to 1 µm: fine enough that a typed 11.5 ft (3.5052 m) reads back as typed, coarse enough for no float noise
      if (await apply({ pose: [round(cur.x, 6), round(cur.y, 6), round(normDeg(cur.heading), 1)] })) finish(true);
    });
    d.replaceChildren(form);
    layout(); renderTop(); sync(); draw();
    window.__scrimmage.startPicker = {
      toScreen: (x, y) => { const rc = canvas.getBoundingClientRect(); const [px, py] = toPx(x, y); return { x: rc.left + px, y: rc.top + py }; },
      get value() { return { ...cur }; },
    };
    d.showModal();
    canvas.focus();
  });
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
