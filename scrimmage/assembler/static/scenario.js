// The Scenario tab: its store, the long poll of /api/scenario/state, scenario edits (one edit per change, each
// with the version), the header (description, robot config, speed, lockstep, [match]), error placement, the status bar
// and the hooks for richer controls (fieldpick.js's field picker); report.js is the run panel and report.
// See the docstring of scrimmage/assembler/server.py for the API contract.
//
// Events on `window`:
//   scrimmage:scenario-state     detail {state}   a new scenario state was applied
//   scrimmage:scenario-selected  detail {step}    the selected step changed (index, or null when there are none)
//   scrimmage:scenario-robot     detail {}        the robot behind the scenario changed (signals reloaded); a robot
//                                              edit can change signals or the view without a new scenario version

import { $, h, api, notice, serial, registerTab, setTab, showPath, currentTab, store } from "./app.js";
import { renderTimeline } from "./steps.js";
import { renderStepEditor, field, onCommit, markDirty, clean, badNumber, localError, guard, currentCommit } from "./step_editor.js";
import { poseTools } from "./fieldpick.js";
import { initRunPanel, runs } from "./report.js";
import { units } from "./units.js";
import { t, tn, onChange } from "./i18n.js";

export const MODES = ["disabled", "auto", "teleop", "test"];
export const STATIONS = ["red1", "red2", "red3", "blue1", "blue2", "blue3"];
const MATCH_KEYS = ["station", "fms", "timer", "auto_seconds", "teleop_seconds"];

export const sc = {
  state: null,        // /api/scenario/state
  signals: null,      // /api/scenario/signals ({} while the robot doesn't load)
  configs: null,      // /api/scenario/configs .configs
  selected: null,     // selected step index
  pendingSelect: null,  // select this step index once a state with it arrives
  lastErrors: [],     // errors from the last rejected edit/undo/redo
  robotVersion: null, // the robot document's version last seen (from the Robot tab's state)
  extraPorts: new Map(),  // step index -> Set of joystick ports added locally that have no values yet
  stepStatus: null,   // per-step last-run result ["pass" | "fail" | null, ...]; set by the run panel (Task 7)
  opening: false,     // this page is opening a scenario (/api/scenario/open): its new content isn't an outside edit
};

// Hooks for richer controls; each is optional and returns an element (or null to use the editor's own picker).
//   signalInput({value, id, onValue, kind})       the signal of an at_end / always check
//   contactInput({value, id, onValue, kind})      the target of a must_touch / never_touch check
//   poseTools({step, check, spec, setSpec})       extra controls for a pose check ("Pick on field", fieldpick.js)
// The run panel (report.js) fills #sc-run; the timeline draws a last-run dot per step from sc.stepStatus.
export const hooks = { poseTools };

// -- edits --------------------------------------------------------------------------------------
const ownVersions = new Set();
let busy = 0;

// `source` is the box whose commit made this edit ({key, value}, from step_editor's onCommit), if any.
async function mutate(path, body, source = null) {
  const s = sc.state;
  if (!s) return { ok: false, errors: [] };
  if (s.read_only) {
    notice(t("This scenario is read-only: {why}", { why: s.read_only_reason }), "warn");
    return { ok: false, errors: [] };
  }
  busy++; renderFooter();
  try {
    const { status, data } = await api("POST", path, { version: sc.state.version, ...body });
    if (status === 200 && data.ok) {
      ownVersions.add(data.version);
      sc.lastErrors = [];
      await refresh();
      return { ok: true, version: data.version };
    }
    const errors = data.errors && data.errors.length ? data.errors
      : [{ field: "status", message: data.error || t("request failed ({status})", { status }) }];
    if (status === 409) {
      notice(errors.map((e) => e.message).join("; "), "warn");
      sc.lastErrors = [];
      sc.pendingSelect = null;
      await refresh(true);
      return { ok: false, errors, conflict: true };
    }
    sc.pendingSelect = null;
    sc.lastErrors = errors;
    // redraw from the saved document, so a value an earlier edit had rejected (its error now replaced by
    // this one) goes back to what the file says; only this edit's own rejected value stays in its box
    render();
    if (source) {
      const box = document.querySelector(`#tab-scenario [data-key="${CSS.escape(source.key)}"]`);
      if (box && !box.dataset.dirty) box.value = source.value;
    }
    const shown = document.querySelector("#tab-scenario .field-msgs .error");
    if (shown) shown.scrollIntoView({ block: "nearest" });
    return { ok: false, errors };
  } finally {
    busy--; renderFooter();
  }
}

// One scenario edit {op, ...fields}; `fields` may be a function called when the edit runs (after queued ones).
export function scEdit(op, fields = {}) {
  const source = currentCommit();
  return serial(() => mutate("/api/scenario/edit", { op, ...(typeof fields === "function" ? fields() : fields) }, source));
}
export function scUndo() { return serial(() => mutate("/api/scenario/undo", {})); }
export function scRedo() { return serial(() => mutate("/api/scenario/redo", {})); }

// -- state --------------------------------------------------------------------------------------
async function refresh(force = false) {
  const { status, data } = await api("GET", "/api/scenario/state");
  if (status === 200) apply(data, force);
}

const content = (s) => JSON.stringify([s.path, s.top, s.steps]);

function apply(s, force = false) {
  const prev = sc.state;
  if (prev && s.version === prev.version && !force) return;
  // a new version we didn't make whose text changed: an outside edit (a robot edit can bump the version
  // without changing the scenario's text: only its errors or read-only state then change)
  const replaced = !!prev && (prev.path !== s.path || (sc.opening && content(s) !== content(prev)));
  if (replaced) {   // another scenario file (or the same one rewritten) opened from this page
    sc.opening = false;
    sc.lastErrors = [];
    sc.extraPorts.clear();
    sc.selected = null;
  } else if (prev && s.version !== prev.version && !ownVersions.has(s.version) && !busy && content(s) !== content(prev)) {
    notice(t("The scenario changed outside this page (the file was edited, or another tab), so it was reloaded. Undo history starts again from here."), "info");
    sc.lastErrors = [];
  }
  const robotMoved = !prev || prev.top.config !== s.top.config || prev.robot_problem !== s.robot_problem;
  sc.state = s;
  const n = s.steps.length;
  const old = sc.selected;
  if (sc.pendingSelect !== null && sc.pendingSelect < n) sc.selected = sc.pendingSelect;
  sc.pendingSelect = null;
  if (sc.selected === null && n) sc.selected = 0;
  if (sc.selected !== null && sc.selected >= n) sc.selected = n ? n - 1 : null;
  if (prev && prev.steps.length !== n) sc.extraPorts.clear();
  render();
  window.dispatchEvent(new CustomEvent("scrimmage:scenario-state", { detail: { state: s } }));
  if (sc.selected !== old) window.dispatchEvent(new CustomEvent("scrimmage:scenario-selected", { detail: { step: sc.selected } }));
  if (robotMoved) loadRobotInfo();
}

export function selectStep(i, { focus = false } = {}) {
  const n = sc.state ? sc.state.steps.length : 0;
  if (i === null || i < 0 || i >= n) return false;
  if (sc.selected !== i) {
    sc.selected = i;
    render();
    window.dispatchEvent(new CustomEvent("scrimmage:scenario-selected", { detail: { step: i } }));
  }
  if (focus) {
    const row = document.querySelector(`#timeline [data-step="${i}"]`);
    if (row) row.focus();
  }
  return true;
}

async function pollLoop() {
  for (;;) {
    const since = sc.state ? `?since=${sc.state.version}` : "";
    const { status, data } = await api("GET", "/api/scenario/state" + since);
    if (status === 200) apply(data);
    else await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

// Signals and the configs list depend on the robot: reload them when the robot changes (a new robot
// version, a different config, or a robot problem coming or going).
let infoSeq = 0;
async function loadRobotInfo() {
  const seq = ++infoSeq;
  const [sig, cfg] = await Promise.all([api("GET", "/api/scenario/signals"), api("GET", "/api/scenario/configs")]);
  if (seq !== infoSeq) return;
  sc.signals = sig.status === 200 ? sig.data : {};
  sc.configs = cfg.status === 200 ? cfg.data.configs : [];
  fillDatalists();
  render();
  window.dispatchEvent(new CustomEvent("scrimmage:scenario-robot", { detail: {} }));
}

function fillDatalists() {
  const s = sc.signals || {};
  const names = [...(s.fixed || [])];
  for (const m of s.motors || []) names.push(`${m}.current`, `${m}.command`);
  for (const j of s.joints || []) names.push(`${j}.position`, `${j}.velocity`);
  names.push(...(s.range || []));
  $("#sc-signal-list").replaceChildren(...names.map((n) => h("option", { value: n })));
  $("#sc-contact-list").replaceChildren(...(s.contacts || []).map((n) => h("option", { value: n })));
}

// -- rendering ----------------------------------------------------------------------------------
// The timeline alone (the run panel's last-run dots changed).
export function rerenderTimeline() {
  if (sc.state) guard(() => renderTimeline(sc));
}

function render() {
  const s = sc.state;
  if (!s) return;
  if (currentTab() === "scenario") renderTop();
  const banner = $("#sc-banner");
  banner.hidden = !s.read_only;
  $("#sc-banner-text").textContent = s.read_only ? t("Read-only: {why}", { why: s.read_only_reason }) : "";
  $("#sc-banner-robot").hidden = !s.robot_problem;
  $("#tab-scenario").classList.toggle("read-only", !!s.read_only);
  guard(() => {
    renderHeader();
    renderTimeline(sc);
    renderStepEditor(sc);
  });
  renderErrors();
}

function renderTop() {
  const s = sc.state;
  if (!s) return;
  showPath(s.display_path || s.path, s.path);
  $("#undo").disabled = !s.can_undo || s.read_only;
  $("#redo").disabled = !s.can_redo || s.read_only;
}

const fmtT = (secs) => t("{seconds} s", { seconds: +secs.toFixed(3) });

function renderFooter() {
  const s = sc.state;
  const el = $("#sc-saved");
  if (!s) return;
  if (s.read_only) { el.textContent = t("Read-only"); el.className = "saved saved-warn"; }
  else if (busy) { el.textContent = t("Saving…"); el.className = "saved saved-busy"; }
  else if (sc.lastErrors.length) { el.textContent = t("Last change not saved (see the error)"); el.className = "saved saved-bad"; }
  else { el.textContent = t("✓ All changes saved"); el.className = "saved saved-ok"; }
  const n = s.steps.length;
  const last = s.steps[n - 1];
  const total = last ? last.start_t + (typeof last.duration === "number" && last.duration > 0 ? last.duration : 0) : 0;
  $("#sc-total").textContent = tn("{count} step · {time} in all", "{count} steps · {time} in all", n, { time: fmtT(total) });
  const ne = s.errors.length + sc.lastErrors.length, nw = s.warnings.length;
  const parts = [];
  if (ne) parts.push(tn("{count} error", "{count} errors", ne));
  if (nw) parts.push(tn("{count} warning", "{count} warnings", nw));
  $("#sc-problems").textContent = parts.length ? parts.join(" · ") : t("No problems");
  $("#sc-problems").className = `stat ${ne ? "stat-bad" : nw ? "stat-warn" : "stat-ok"}`;
}

// The runner's messages start with where they are ("x.toml, step 2 (auto), check 1, console: "); beside the
// field that's already clear.
const WHERE = /^[^:]*?\.toml(?:, step \d+(?: \([^)]*\))?)?(?:, (?:screenshot \d+|contact_sheet|check \d+|pose|odometry_error|at_end|always|by_time|console|never_touch|must_touch))*: /;
export const stripWhere = (m) => String(m).replace(WHERE, "");

// Where a field id can show: itself, then without a ".<key>" suffix, then its check / screenshot / sheet,
// then its step.
function candidates(f) {
  const out = [f];
  const dot = /^(.*\/[^/.]+)\.[^/]+$/.exec(f);
  if (dot) out.push(dot[1]);
  const item = /^(step\d+)\/((?:check|shot)\d+|sheet)(?:\/|$)/.exec(f);
  if (item) out.push(`${item[1]}/${item[2]}`);
  const step = /^(step\d+)\//.exec(f);
  if (step) out.push(step[1]);
  return out;
}

export function stepOfField(f) {
  const m = /^step(\d+)\//.exec(f || "");
  return m ? +m[1] : null;
}

// Every error and warning lands beside its field when the page shows it, else in the messages list.
export function renderErrors() {
  const s = sc.state;
  if (!s) return;
  const root = $("#tab-scenario");
  for (const el of root.querySelectorAll(".field-msgs")) el.replaceChildren();
  for (const el of root.querySelectorAll(".has-error")) el.classList.remove("has-error");
  const left = [];
  const put = (e, kind) => {
    let box = null;
    if (e.field && e.field !== "status") {
      for (const c of candidates(e.field)) {
        const host = root.querySelector(`[data-fields~="${CSS.escape(c)}"]`);
        if (host) { box = host.querySelector(":scope > .field-msgs"); if (box) break; }
      }
    }
    if (!box) { left.push([e, kind]); return; }
    box.append(h("p", { class: kind === "error" ? "error" : "warning", role: kind === "error" ? "alert" : null, text: stripWhere(e.message) }));
    if (kind === "error") box.parentElement.classList.add("has-error");
  };
  for (const e of [...s.errors, ...sc.lastErrors]) put(e, "error");
  for (const w of s.warnings) put(w, "warning");
  const list = $("#sc-messages");
  list.replaceChildren();
  for (const [e, kind] of left) {
    const i = stepOfField(e.field);
    const st = i !== null ? s.steps[i] : null;
    list.append(h("div", { class: `message message-${kind}`, role: kind === "error" ? "alert" : null },
      h("span", { class: "message-kind", text: kind === "error" ? t("Error") : t("Warning") }),
      st ? h("button", { type: "button", class: "linklike", onclick: () => selectStep(i, { focus: true }),
        text: st.name ? t("Step {n} {name}", { n: i + 1, name: st.name }) : t("Step {n}", { n: i + 1 }) }) : null,
      h("span", { text: st ? stripWhere(e.message) : e.message })));
  }
  list.hidden = !list.children.length;
  renderFooter();
}

// -- header ---------------------------------------------------------------------------------------
let headerKeep = null;

function renderHeader() {
  const s = sc.state;
  const top = s.top;
  const root = $("#sc-header");
  const act = document.activeElement;
  headerKeep = act && root.contains(act) && act.dataset.key ? { key: act.dataset.key, dirty: act.dataset.dirty ? act.value : null } : null;

  const desc = markDirty(h("input", { id: "sc-desc", type: "text", value: top.description ?? "", placeholder: t("What this scenario checks"),
    autocomplete: "off", dataset: { key: "desc" } }));
  onCommit(desc, () => {
    const v = desc.value.trim();
    clean(desc);
    if (v === (top.description ?? "")) return;
    scEdit("top", { key: "description", value: v || null });
  });

  const cfgSel = h("select", { id: "sc-config", dataset: { key: "config" } });
  const options = [...(sc.configs || [])];
  if (typeof top.config === "string" && !options.includes(top.config)) options.unshift(top.config);
  if (top.config == null) cfgSel.append(h("option", { value: "", disabled: true }, t("(none set)")));
  for (const c of options) cfgSel.append(h("option", { value: c }, c));
  cfgSel.value = top.config ?? "";
  cfgSel.addEventListener("change", () => {
    if (cfgSel.value && cfgSel.value !== top.config) scEdit("top", { key: "config", value: cfgSel.value });
  });
  const openRobot = h("button", { type: "button", class: "btn", onclick: () => setTab("robot", { focus: true }) }, t("Open in Robot tab"));
  const driveRobot = h("button", { type: "button", id: "sc-drive", class: "btn", title: t("Drive this robot live in the Drive tab"),
    onclick: () => setTab("drive", { focus: true }) }, t("Drive this robot"));

  // speed: absent means "max" (the runner's default)
  const sp = top.speed ?? "max";
  const isMax = sp === "max", isOne = sp === 1;
  const speedNum = markDirty(h("input", { id: "sc-speed", type: "number", min: "0", step: "any", inputmode: "decimal",
    value: typeof sp === "number" && sp !== 1 ? String(sp) : "", placeholder: t("other"), "aria-label": t("Speed, times real time"),
    dataset: { key: "speed" } }));
  const speedField = h("div", { class: "field sc-field", role: "group", "aria-labelledby": "sc-speed-label", dataset: { fields: "scenario/speed" } },
    h("span", { id: "sc-speed-label", class: "field-label" }, t("Speed")),
    h("div", { class: "row speed-row" },
      h("div", { class: "seg" },
        h("button", { type: "button", class: "seg-btn", "aria-pressed": String(isOne), title: t("Real time"),
          onclick: () => { if (!isOne) scEdit("top", { key: "speed", value: 1 }); } }, "1×"),
        h("button", { type: "button", class: "seg-btn", "aria-pressed": String(isMax), title: t("As fast as the computer can go"),
          onclick: () => { if (!isMax) scEdit("top", { key: "speed", value: "max" }); } }, "max")),
      h("span", { class: "with-unit" }, speedNum, h("span", { class: "unit" }, "×"))),
    h("div", { class: "field-msgs" }));
  onCommit(speedNum, () => {
    const raw = speedNum.value.trim();
    if (badNumber(speedNum)) { localError(speedField, t("speed must be a number")); return; }
    clean(speedNum);
    if (raw === "") return;
    const v = Number(raw);
    if (v === sp) return;
    scEdit("top", { key: "speed", value: v });
  });

  const lock = h("input", { id: "sc-lockstep", type: "checkbox", checked: top.require_lockstep !== false, dataset: { key: "lockstep" } });
  lock.addEventListener("change", () => scEdit("top", { key: "require_lockstep", value: lock.checked }));

  const matchField = renderMatch(top.match || {});

  const fs = h("div", { class: "sc-head-grid" },
    field("scenario/description", "sc-desc", t("Description"), desc, { extraClass: "sc-field sc-desc" }),
    h("div", { class: "field sc-field sc-robot", dataset: { fields: "scenario/config" } },
      h("label", { for: "sc-config", class: "field-label" }, t("Robot config")),
      h("div", { class: "row" }, cfgSel, openRobot, driveRobot),
      h("div", { class: "field-msgs" })),
    speedField,
    h("div", { class: "field sc-field sc-lock", dataset: { fields: "scenario/require_lockstep" } },
      h("span", { class: "field-label" }, t("Lockstep")),
      h("label", { class: "check-label", for: "sc-lockstep" }, lock, t("Require lockstep")),
      h("div", { class: "field-msgs" })),
    matchField);
  // "Open in Robot tab" and "Drive this robot" stay usable when the scenario is read-only (that's where the fix is)
  for (const el of fs.querySelectorAll("input, select, .seg-btn")) el.disabled = !!s.read_only;
  root.replaceChildren(fs);
  if (headerKeep) {
    const el = root.querySelector(`[data-key="${CSS.escape(headerKeep.key)}"]`);
    if (el) {
      if (headerKeep.dirty !== null) { el.value = headerKeep.dirty; el.dataset.dirty = "1"; }
      el.focus();
    }
  }
}

// The [match] row: each control empty ("robot config's") leaves that key to the robot config, which the scenario's
// keys go over when it runs.
function renderMatch(m) {
  const set = (key, value) => scEdit("match", { key, value });
  const CONFIG = t("robot config's");
  const station = h("select", { id: "sc-station", dataset: { key: "match-station" } },
    h("option", { value: "" }, CONFIG), ...STATIONS.map((st) => h("option", { value: st }, st)));
  if (m.station != null && !STATIONS.includes(m.station)) station.append(h("option", { value: String(m.station) }, String(m.station)));
  station.value = m.station != null ? String(m.station) : "";
  station.addEventListener("change", () => set("station", station.value || null));
  const onOff = (key) => {
    const el = h("select", { id: `sc-${key}`, dataset: { key: `match-${key}` } },
      h("option", { value: "" }, CONFIG), h("option", { value: "true" }, t("on")), h("option", { value: "false" }, t("off")));
    el.value = typeof m[key] === "boolean" ? String(m[key]) : "";
    el.addEventListener("change", () => set(key, el.value === "" ? null : el.value === "true"));
    return el;
  };
  let f;
  const seconds = (key, label) => {
    const have = typeof m[key] === "number" ? m[key] : null;
    const el = markDirty(h("input", { id: `sc-${key}`, type: "number", min: "0.1", step: "any", inputmode: "decimal",
      value: have !== null ? String(have) : "", placeholder: t("config's"), dataset: { key: `match-${key}` } }));
    onCommit(el, () => {
      if (badNumber(el)) { localError(f, t("{label} must be a number", { label })); return; }
      clean(el);
      const raw = el.value.trim();
      const v = raw === "" ? null : Number(raw);
      if (v !== have) set(key, v);
    });
    return h("span", { class: "with-unit" }, el, h("span", { class: "unit" }, "s"));
  };
  const item = (id, label, control) => h("div", { class: "match-item" }, h("label", { for: id }, label), control);
  const ids = ["scenario/match", ...MATCH_KEYS.map((k) => `scenario/match.${k}`)];
  f = h("div", { class: "field sc-field sc-match", role: "group", "aria-labelledby": "sc-match-label", dataset: { fields: ids.join(" ") } },
    h("span", { id: "sc-match-label", class: "field-label" }, t("Match")),
    h("div", { class: "row match-row" },
      item("sc-station", t("Station"), station), item("sc-fms", t("FMS attached"), onOff("fms")),
      item("sc-timer", t("Match timer"), onOff("timer")), item("sc-auto_seconds", t("Auto length"), seconds("auto_seconds", t("Auto length"))),
      item("sc-teleop_seconds", t("Teleop length"), seconds("teleop_seconds", t("Teleop length")))),
    h("div", { class: "field-msgs" }));
  return f;
}

// -- boot -----------------------------------------------------------------------------------------
// The robot state says whether a scenario is open (asking /api/scenario/state would be a 404 otherwise).
function robotState() {
  if (store.state) return Promise.resolve(store.state);
  return new Promise((resolve) => window.addEventListener("scrimmage:state", (e) => resolve(e.detail.state), { once: true }));
}

// The Scenario tab starts when the robot state says a scenario is open: at page load, or later when one is opened
// from this page (the Drive tab's "Save as scenario…").
let starting = null;

export async function initScenario() {
  const rs = await robotState();
  if (rs.scenario_open) return startScenario();
  const wait = (e) => {
    if (!e.detail.state.scenario_open) return;
    window.removeEventListener("scrimmage:state", wait);
    startScenario();
  };
  window.addEventListener("scrimmage:state", wait);
  return false;
}

function startScenario() {
  if (!starting) starting = boot().then((ok) => { if (!ok) starting = null; return ok; });
  return starting;
}

// Before POST /api/scenario/open: the state that follows is this page's doing, not an outside edit (false: the
// open failed, so nothing follows).
export function expectOpen(on = true) {
  sc.opening = on;
}

// After POST /api/scenario/open succeeded: show the opened scenario in the Scenario tab.
export async function showScenario() {
  const ok = await startScenario();
  if (!ok) return;
  await refresh();
  sc.opening = false;
  setTab("scenario", { focus: true });
}

async function boot() {
  const { status, data } = await api("GET", "/api/scenario/state");
  if (status !== 200) { notice(t("Couldn't load the scenario: {why}", { why: data.errors[0].message }), "error"); return false; }
  $("#sc-banner-robot").addEventListener("click", () => setTab("robot", { focus: true }));
  // the robot version the signals below are loaded for, so the very first robot edit reloads them too
  sc.robotVersion = store.state ? store.state.version : null;
  window.addEventListener("scrimmage:state", (e) => {
    const v = e.detail.state.version;
    if (sc.robotVersion !== null && v !== sc.robotVersion) loadRobotInfo();
    sc.robotVersion = v;
  });
  window.__scrimmage.scenario = {
    get state() { return sc.state; },
    get selected() { return sc.selected; },
    get lastErrors() { return sc.lastErrors; },
    get signals() { return sc.signals; },
    get configs() { return sc.configs; },
    get runs() { return runs.list; },
    select: (i) => selectStep(i),
    edit: (op, fields) => scEdit(op, fields),
    store: sc,
    hooks,
  };
  apply(data);
  registerTab("scenario", { renderTop, undo: scUndo, redo: scRedo, onShow: () => { renderTop(); } });
  units.onChange(() => render());   // the step editor keeps what is half typed (renderStepEditor)
  onChange(() => { render(); refresh(true); });   // likewise; the server's words (errors, read-only reason) again
  pollLoop();
  initRunPanel();
  return true;
}
