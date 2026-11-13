// The step editor (center of the Scenario tab): name / mode / duration / game data, joystick inputs per port (or,
// for a step with an input track, trackplot.js's track panel), the checks with one form per kind, screenshots and
// the contact sheet. Every change posts one scenario edit; each field
// carries data-fields with its field ids so errors render beside it (scenario.js renderErrors).

import { h, fmt, showMeasure, readMeasure, measureError, typedText } from "./app.js";
import { units } from "./units.js";
import { MODES, scEdit, hooks, sc } from "./scenario.js";
import { trackPanel } from "./trackplot.js";
import { t } from "./i18n.js";

// -- shared form helpers (also used by the header in scenario.js) --------------------------------
let rerendering = false;   // removing a focused input must not commit it

export function guard(fn) {
  const was = rerendering;     // a guard inside a guard (render -> renderTimeline) leaves the outer one on
  rerendering = true;
  try { fn(); } finally { rerendering = was; }
}

// A labeled field: `fieldIds` (one id or a list) say which errors render in its .field-msgs.
export function field(fieldIds, labelFor, label, control, { hint, extraClass } = {}) {
  const ids = Array.isArray(fieldIds) ? fieldIds.join(" ") : fieldIds;
  return h("div", { class: `field${extraClass ? " " + extraClass : ""}`, dataset: { fields: ids } },
    h("label", { for: labelFor, class: "field-label" }, label),
    control,
    hint ? h("p", { class: "hint", text: hint }) : null,
    h("div", { class: "field-msgs" }));
}

export function markDirty(el) { el.addEventListener("input", () => { el.dataset.dirty = "1"; }); return el; }
export function clean(...els) { for (const el of els) delete el.dataset.dirty; }

// Text a number box can't parse ("1e", "0.1-") reads as "" with validity.badInput set.
export const badNumber = (...inputs) => inputs.some((i) => i.validity && i.validity.badInput);

// The box whose commit is running ({key, value}): scEdit remembers it, so a rejected edit can leave the
// rejected text in that box while every other box shows the saved value again.
let committing = null;
export const currentCommit = () => committing;

// Commit on change; also on blur while still dirty (a value restored across a re-render fires no change).
export function onCommit(el, fn) {
  const run = () => {
    committing = el.dataset.key ? { key: el.dataset.key, value: el.value } : null;
    try { fn(); } finally { committing = null; }
  };
  // (not while a re-render removes it: Chromium fires change on a focused, dirty box as it goes)
  el.addEventListener("change", () => { if (!rerendering) run(); });
  el.addEventListener("blur", () => { if (el.dataset.dirty && !rerendering && el.isConnected) run(); });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && el.tagName === "INPUT" && el.type !== "checkbox") { e.preventDefault(); el.blur(); }
  });
  return el;
}

export function localError(fieldEl, message) {
  const box = fieldEl.querySelector(":scope > .field-msgs");
  box.replaceChildren(h("p", { class: "error", role: "alert", text: message }));
  fieldEl.classList.add("has-error");
}

export function icon(name) {
  const paths = {
    check: "M3 8.5l3 3 7-7",
    camera: "M2 5h3l1.5-2h3L11 5h3v8H2z M8 11a2.2 2.2 0 1 0 0-4.4 2.2 2.2 0 0 0 0 4.4z",
    grid: "M2 2h5v5H2z M9 2h5v5H9z M2 9h5v5H2z M9 9h5v5H9z",
    up: "M4 10l4-4 4 4", down: "M4 6l4 4 4-4", copy: "M5 5h8v8H5z M3 11V3h8",
    trash: "M3 4h10 M6 4V2.5h4V4 M4.5 4l.7 9.5h5.6l.7-9.5", plus: "M8 3v10 M3 8h10", grip: "M6 4h.01 M10 4h.01 M6 8h.01 M10 8h.01 M6 12h.01 M10 12h.01",
    joystick: "M8 9V5 M8 4.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z M3 10h10v3.5H3z",
  };
  // the HTML parser puts <svg> in the SVG namespace; the markup is only these constant paths
  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg viewBox="0 0 16 16" class="icon icon-${name}" aria-hidden="true"><path d="${paths[name] || ""}"/></svg>`;
  return tpl.content.firstChild;
}

const num = (v) => typeof v === "number" && isFinite(v);
const isNumText = (text) => text !== "" && isFinite(Number(text));

// A number box for one key of a table value. onValue(number | null) — null when emptied. With a `kind` (units.js)
// it is a text box in the chosen units ("12.5" or "12.5 ft"), and the value stays SI.
function numBox({ id, key, value, placeholder, min, max, step = "any", label, fieldEl, onValue, int = false, kind = null }) {
  const el = markDirty(kind
    ? h("input", { id, type: "text", inputmode: "decimal", autocomplete: "off", placeholder, "aria-label": label, dataset: { key } })
    : h("input", { id, type: "number", step: int ? "1" : step, inputmode: int ? "numeric" : "decimal",
      value: num(value) ? fmt(value) : "", placeholder, min, max, "aria-label": label, dataset: { key } }));
  if (kind) showMeasure(el, num(value) ? value : null, kind);
  onCommit(el, () => {
    let v;
    if (kind) {
      v = readMeasure(el, kind);
      if (measureError(v)) { localError(fieldEl(), measureError(v)); return; }
    } else {
      if (badNumber(el)) { localError(fieldEl(), t("{label} must be a number", { label: label || key })); return; }
      const text = el.value.trim();
      v = text === "" ? null : Number(text);
    }
    clean(el);
    if (v === (num(value) ? value : null)) return;
    onValue(v);
  });
  return el;
}

// A field with a number box; returns the field element. A `kind` shows it in the chosen units (and names them).
function numField(fieldIds, id, key, label, value, onValue, { unit, placeholder, hint, min, max, int, kind } = {}) {
  let f;
  if (kind) unit = units.name(kind);
  const box = numBox({ id, key, value, placeholder, min, max, int, label, fieldEl: () => f, onValue, kind });
  f = field(fieldIds, id, label, unit ? h("span", { class: "with-unit" }, box, h("span", { class: "unit" }, unit)) : box, { hint, extraClass: "field-num" });
  return f;
}

function textField(fieldIds, id, key, label, value, onValue, { placeholder, list, hint, mono = true } = {}) {
  const el = markDirty(h("input", { id, type: "text", value: value ?? "", placeholder, list, autocomplete: "off",
    spellcheck: "false", class: mono ? "mono" : null, dataset: { key } }));
  onCommit(el, () => {
    clean(el);
    const v = el.value.trim();
    if (v === (value ?? "")) return;
    onValue(v);
  });
  return field(fieldIds, id, label, el, { hint });
}

function section(title, fieldIds, ...children) {
  return h("section", { class: "ed-section", dataset: fieldIds ? { fields: fieldIds } : {} },
    h("h3", { class: "ed-title" }, ...[title].flat()),
    h("div", { class: "field-msgs" }),
    ...children);
}

// -- render ------------------------------------------------------------------------------------------
let lastStep = null;

export function renderStepEditor(st) {
  const root = document.getElementById("step-editor");
  const title = document.getElementById("editor-title");
  const s = st.state;
  const act = document.activeElement;
  const keep = act && root.contains(act) && act.dataset.key && lastStep === st.selected
    ? { key: act.dataset.key, dirty: act.dataset.dirty ? typedText(act) : null } : null;   // converted after a Units change
  const i = st.selected;
  const step = i === null ? null : s.steps[i];
  if (!step) {
    lastStep = null;
    title.textContent = t("Step");
    root.replaceChildren(h("p", { class: "empty" }, t("Add a step on the left to start the scenario.")));
    return;
  }
  const switched = lastStep !== i;
  lastStep = i;
  title.textContent = step.name ? t("Step {n} · {name}", { n: i + 1, name: step.name }) : t("Step {n}", { n: i + 1 });
  const fs = h("fieldset", { class: "ed", disabled: s.read_only },
    headSection(i, step), step.inputs ? trackSection(i, step) : inputsSection(i, step), checksSection(i, step), shotsSection(i, step));
  root.replaceChildren(fs);
  if (switched) root.scrollTop = 0;
  if (keep) {
    const el = root.querySelector(`[data-key="${CSS.escape(keep.key)}"]`);
    if (el) {
      if (keep.dirty !== null) { el.value = keep.dirty; el.dataset.dirty = "1"; }
      el.focus();
    }
  }
}

// -- name, mode, duration ----------------------------------------------------------------------------
function headSection(i, step) {
  const setStep = (fields) => scEdit("set_step", { step: i, fields });
  const name = textField(`step${i}/name`, "ed-name", "name", t("Name"), step.name,
    (v) => setStep({ name: v || null }), { placeholder: t("step {n}", { n: i + 1 }), mono: false });
  const modeField = h("div", { class: "field", role: "group", "aria-labelledby": "ed-mode-label", dataset: { fields: `step${i}/mode` } },
    h("span", { id: "ed-mode-label", class: "field-label" }, t("Mode")),
    h("div", { class: "mode-seg" }, ...MODES.map((m) => h("button", {
      type: "button", class: `mode-btn mode-${m}`, "aria-pressed": String(step.mode === m), dataset: { key: `mode-${m}` },
      onclick: () => { if (step.mode !== m) setStep({ mode: m }); },
    }, m))),
    !MODES.includes(step.mode) && step.mode != null ? h("p", { class: "hint", text: t("The file says {setting}.", { setting: `mode = ${JSON.stringify(step.mode)}` }) }) : null,
    h("div", { class: "field-msgs" }));
  const dur = numField(`step${i}/duration`, "ed-duration", "duration", t("Duration"), step.duration,
    (v) => setStep({ duration: v }), { unit: "s", min: 0 });
  const startT = h("p", { class: "hint ed-start", text: t("Starts at {seconds} s", { seconds: +step.start_t.toFixed(3) }) });
  return h("section", { class: "ed-section ed-head", dataset: { fields: `step${i} step${i}/step` } },
    h("div", { class: "field-msgs" }),
    h("div", { class: "ed-head-grid" }, name, modeField, h("div", {}, dur, startT), gameDataField(i, step)));
}

// The game data sent when the step starts. Without a game_data the robot keeps what the step before left it (the
// box shows that as its placeholder); "" clears it.
function gameDataField(i, step) {
  const set = (value) => scEdit("game_data", { step: i, value });
  let before = "";
  for (const s of sc.state.steps.slice(0, i)) if (typeof s.game_data === "string") before = s.game_data;
  const own = step.game_data;
  const f = textField(`step${i}/game_data`, "ed-game-data", "game_data", t("Game data"), typeof own === "string" ? own : "",
    (v) => set(v || null),
    { placeholder: own === "" ? t("cleared") : before ? t("keeps {value}", { value: JSON.stringify(before) }) : t("none") });
  if (own === "") {
    f.insertBefore(h("button", { type: "button", class: "linklike", dataset: { key: "game-data-keep" }, onclick: () => set(null) },
      before ? t("Keep {value} instead", { value: JSON.stringify(before) }) : t("Don't clear")), f.querySelector(":scope > .field-msgs"));
  } else if (own == null && before) {
    f.insertBefore(h("button", { type: "button", class: "linklike", dataset: { key: "game-data-clear" }, onclick: () => set("") },
      t("Clear it here")), f.querySelector(":scope > .field-msgs"));
  }
  return f;
}

// -- joystick inputs -----------------------------------------------------------------------------------
const POV_DIRS = [[315, "↖"], [0, "↑"], [45, "↗"], [270, "←"], [-1, "·"], [90, "→"], [225, "↙"], [180, "↓"], [135, "↘"]];

function inputsSection(i, step) {
  const ports = new Set([...Object.keys(step.axes), ...Object.keys(step.buttons), ...Object.keys(step.povs)].map(Number));
  for (const p of sc.extraPorts.get(i) || []) ports.add(p);
  const sorted = [...ports].filter((p) => p >= 0 && p <= 5).sort((a, b) => a - b);
  const free = [0, 1, 2, 3, 4, 5].filter((p) => !ports.has(p));
  const add = h("select", { id: "ed-add-port", class: "add-select", "aria-label": t("Add a joystick port"), dataset: { key: "add-port" } },
    h("option", { value: "" }, t("Add port ▾")), ...free.map((p) => h("option", { value: String(p) }, t("Port {n}", { n: p }))));
  add.addEventListener("change", () => {
    if (add.value === "") return;
    const p = Number(add.value);
    if (!sc.extraPorts.has(i)) sc.extraPorts.set(i, new Set());
    sc.extraPorts.get(i).add(p);
    const root = document.getElementById("step-editor");
    guard(() => renderStepEditor(sc));
    const first = root.querySelector(`[data-port="${p}"] button`);
    if (first) first.focus();
  });
  return section([t("Joystick inputs"), h("span", { class: "ed-sub" }, t("held for the whole step"))],
    `step${i}/axes step${i}/buttons step${i}/povs`,
    sorted.length ? h("div", { class: "ports" }, ...sorted.map((p) => portCard(i, step, p)))
      : h("p", { class: "empty empty-small" }, t("No joystick input in this step: every port is neutral.")),
    free.length ? h("div", { class: "ed-actions" }, add) : null);
}

// A step with an input track: the track panel (loaded after the editor is drawn) in place of the constant inputs.
function trackSection(i, step) {
  const body = h("p", { class: "muted track-loading" }, t("Loading the track…"));
  trackPanel(i, step).then((el) => { if (body.isConnected) { body.replaceWith(el); el.afterInsert(); } },
    (e) => { if (body.isConnected) body.replaceWith(h("p", { class: "error", text: t("Couldn't show the track: {why}", { why: e.message || e }) })); });
  return section([t("Input track"), h("span", { class: "ed-sub" }, t("replayed step for step"))], `step${i}/inputs`, body);
}

function portCard(i, step, port) {
  const key = String(port);
  const axes = step.axes[key] || [], buttons = step.buttons[key] || [], povs = step.povs[key] || [];
  const set = (kind, values) => scEdit("inputs", { step: i, kind, port, values: values.length ? values : null });

  const axisRows = axes.map((v, n) => {
    const id = `ax-${port}-${n}`;
    const val = num(v) ? v : 0;
    const slider = h("input", { type: "range", min: "-1", max: "1", step: "0.05", value: String(val), "aria-label": t("Port {port} axis {n}", { port, n }),
      dataset: { key: `${id}-r` } });
    const box = markDirty(h("input", { id, type: "number", min: "-1", max: "1", step: "0.05", value: fmt(val), class: "axis-num",
      "aria-label": t("Port {port} axis {n} value", { port, n }), dataset: { key: id } }));
    const commit = (x) => { const next = [...axes]; next[n] = x; set("axes", next); };
    slider.addEventListener("input", () => { box.value = slider.value; });
    slider.addEventListener("change", () => commit(Number(slider.value)));
    let row;
    onCommit(box, () => {
      if (badNumber(box) || box.value.trim() === "") { localError(row, t("an axis is a number between -1 and 1")); return; }
      clean(box);
      const x = Number(box.value);
      if (x !== val) commit(x);
    });
    row = h("div", { class: "axis-row field" }, h("span", { class: "idx" }, t("Axis {n}", { n })), slider, box, h("div", { class: "field-msgs" }));
    return row;
  });

  const btns = buttons.map((b, n) => h("button", {
    type: "button", class: "toggle", "aria-pressed": String(b === true), title: b ? t("Button {n} (held)", { n: n + 1 }) : t("Button {n}", { n: n + 1 }), dataset: { key: `bt-${port}-${n}` },
    onclick: () => { const next = [...buttons]; next[n] = !(b === true); set("buttons", next); },
  }, String(n + 1)));

  const povRows = povs.map((v, n) => {
    const id = `pov-${port}-${n}`;
    const compass = h("div", { class: "compass", role: "group", "aria-label": t("Port {port} POV {n}", { port, n }) },
      ...POV_DIRS.map(([deg, glyph]) => h("button", {
        type: "button", class: "compass-btn", "aria-pressed": String(v === deg), title: deg === -1 ? t("Released (-1)") : `${deg}°`,
        "aria-label": deg === -1 ? t("released") : t("{deg} degrees", { deg }), dataset: { key: `${id}-${deg}` },
        onclick: () => { if (v !== deg) { const next = [...povs]; next[n] = deg; set("povs", next); } },
      }, glyph)));
    let row;
    const box = numBox({ id, key: id, value: v, label: t("POV {n}", { n }), int: true, min: -1, max: 359, fieldEl: () => row,
      onValue: (x) => { const next = [...povs]; next[n] = x === null ? -1 : x; set("povs", next); } });
    row = h("div", { class: "pov-row field" }, h("span", { class: "idx" }, t("POV {n}", { n })), compass,
      h("span", { class: "with-unit" }, box, h("span", { class: "unit" }, "°")), h("div", { class: "field-msgs" }));
    return row;
  });

  // words per kind: [add's label, remove's label, the add button, the remove button]
  const WORDS = {
    axes: () => [t("Add axis on port {port}", { port }), t("Remove the last axis on port {port}", { port }), t("+ axis"), t("− axis")],
    buttons: () => [t("Add button on port {port}", { port }), t("Remove the last button on port {port}", { port }), t("+ button"), t("− button")],
    povs: () => [t("Add POV on port {port}", { port }), t("Remove the last POV on port {port}", { port }), t("+ POV"), t("− POV")],
  };
  const plusMinus = (kind, list, fresh) => {
    const [addLabel, removeLabel, addText, removeText] = WORDS[kind]();
    return h("span", { class: "pm" },
      h("button", { type: "button", class: "btn btn-small", "aria-label": addLabel, onclick: () => set(kind, [...list, fresh]) }, addText),
      list.length ? h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": removeLabel,
        onclick: () => set(kind, list.slice(0, -1)) }, removeText) : null);
  };

  return h("div", { class: "port", dataset: { port: key } },
    h("div", { class: "port-head" }, icon("joystick"), h("strong", {}, t("Port {n}", { n: port }))),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, t("Axes")), plusMinus("axes", axes, 0)),
      axisRows.length ? axisRows : h("p", { class: "muted small" }, t("none (−1 … 1 each)"))),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, t("Buttons")), plusMinus("buttons", buttons, false)),
      btns.length ? h("div", { class: "toggles" }, btns) : h("p", { class: "muted small" }, t("none (1 is the first button)"))),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, t("POVs")), plusMinus("povs", povs, -1)),
      povRows.length ? povRows : h("p", { class: "muted small" }, t("none (−1 is released)"))));
}

// -- checks ----------------------------------------------------------------------------------------------
export const CHECK_KINDS = ["pose", "odometry_error", "at_end", "always", "by_time", "must_touch", "never_touch", "console", "sensor"];
// (functions: the words are in the language of the moment)
const CHECK_LABEL = () => ({
  pose: t("Pose at the end"), odometry_error: t("Odometry error"), at_end: t("Signal at the end"), always: t("Signal throughout"),
  by_time: t("Signal by a time"), must_touch: t("Must touch"), never_touch: t("Never touch"), console: t("Console output"),
  sensor: t("Sensor"),
});
const CHECK_DOC = () => ({
  pose: t("Where the robot is when the step ends (on the field, heading in degrees)."),
  odometry_error: t("How far the robot's own odometry (on NetworkTables) is from where it really is."),
  at_end: t("A signal's value when the step ends."),
  always: t("A signal's value on every tick of the step."),
  by_time: t("A signal reaches a value at some point up to t seconds into the step (e.g. 8 scored by 15 s)."),
  must_touch: t("The robot touches this geom or body at some point in the step."),
  never_touch: t("The robot never touches this geom or body in the step."),
  console: t("A line the robot program prints during the step (a regular expression)."),
  sensor: t("One of the robot's sensors is on or off: a beam broken, something closer than a distance sensor's threshold, a color sensor seeing a piece (of a named color, with sees), a camera seeing a tag."),
});

// The robot's [[sensor]]s ({name, kind}) and the named colors a color sensor's sees can name, as
// /api/scenario/signals lists them (null when it doesn't: the robot doesn't load).
const robotSensors = () => (sc.signals && Array.isArray(sc.signals.sensors) ? sc.signals.sensors : null);
const sensorColors = () => (sc.signals && Array.isArray(sc.signals.sensor_colors) ? sc.signals.sensor_colors : null);

function defaultSpec(kind, step) {
  const contacts = (sc.signals && sc.signals.contacts) || [];
  const scored = ((sc.signals && sc.signals.range) || []).includes("score");
  return {
    pose: { x: 0, y: 0 }, odometry_error: { max: 0.1 }, at_end: { signal: "battery", min: 10 },
    always: { signal: "battery", min: 10 },
    by_time: scored ? { signal: "score", min: 1, t: num(step.duration) ? step.duration : 1 }
      : { signal: "battery", min: 10, t: num(step.duration) ? step.duration : 1 }, must_touch: contacts[0] || "", never_touch: contacts[0] || "",
    console: { contains: "" },
    sensor: { name: ((robotSensors() || [])[0] || {}).name || "" },
  }[kind];
}

function checksSection(i, step) {
  const noContacts = !((sc.signals && sc.signals.contacts) || []).length;
  const noSensors = !(robotSensors() || []).length;
  const labels = CHECK_LABEL();
  const add = h("select", { id: "ed-add-check", class: "add-select", "aria-label": t("Add a check"), dataset: { key: "add-check" } },
    h("option", { value: "" }, t("Add check ▾")), ...CHECK_KINDS.map((k) => {
      const offTouch = noContacts && (k === "must_touch" || k === "never_touch");
      const offSensor = noSensors && k === "sensor";
      const title = offTouch ? t("This robot's world has nothing to touch (or it doesn't load)")
        : offSensor ? t("This robot has no sensors (or it doesn't load)") : null;
      const text = offTouch ? t("{label} ({kind}) (nothing to touch)", { label: labels[k], kind: k })
        : offSensor ? t("{label} ({kind}) (no sensors)", { label: labels[k], kind: k }) : t("{label} ({kind})", { label: labels[k], kind: k });
      return h("option", { value: k, disabled: offTouch || offSensor, title }, text);
    }));
  add.addEventListener("change", () => {
    const k = add.value;
    add.value = "";
    if (k) scEdit("add_check", { step: i, kind: k, spec: defaultSpec(k, step) });
  });
  return section([t("Checks"), h("span", { class: "ed-sub" }, t("judged at the end of the step"))], `step${i}/check`,
    step.checks.length ? h("div", { class: "cards" }, ...step.checks.map((c, j) => checkCard(i, j, c)))
      : h("p", { class: "empty empty-small" }, t("No checks: this step only drives the robot.")),
    h("div", { class: "ed-actions" }, add));
}

// `what`: "check" or "screenshot" (a key, not words)
function cardHead(title, kindText, fieldIds, onDelete, what) {
  const del = what === "check" ? t("Delete this check") : t("Delete this screenshot");
  return [h("div", { class: "card-head" },
    h("strong", {}, title), kindText ? h("code", { class: "kind" }, kindText) : null,
    h("button", { type: "button", class: "btn btn-small btn-ghost card-del", "aria-label": del, title: del, onclick: onDelete }, icon("trash"))),
  h("div", { class: "field-msgs" })];
}

function checkCard(i, j, c) {
  const base = `step${i}/check${j}`;
  const del = () => scEdit("delete_check", { step: i, check: j });
  if (!c.kind || !CHECK_KINDS.includes(c.kind)) {
    return h("div", { class: "card", dataset: { fields: `${base} ${base}/check` } },
      ...cardHead(t("Check {n}", { n: j + 1 }), c.kind || "?", base, del, "check"),
      h("p", { class: "hint", text: t("This check isn't one of the kinds the editor knows; change it in a text editor or delete it.") }),
      h("pre", { class: "raw", text: JSON.stringify(c.spec) }));
  }
  const kind = c.kind;
  const spec = c.spec;
  const setSpec = (next) => scEdit("set_check", { step: i, check: j, spec: { [kind]: next } });
  const tableSpec = spec && typeof spec === "object" && !Array.isArray(spec) ? spec : {};
  const setKey = (k, v) => {
    const next = { ...tableSpec };
    if (v === null || v === undefined || v === "") delete next[k]; else next[k] = v;
    return setSpec(next);
  };
  const fid = (k) => `${base}/${kind}.${k}`;
  const id = (k) => `ck-${i}-${j}-${k}`;
  let body;
  if (kind === "pose") {
    body = h("div", { class: "form-grid" },
      numField(fid("x"), id("x"), id("x"), "x", tableSpec.x, (v) => setKey("x", v), { kind: "field" }),
      numField(fid("y"), id("y"), id("y"), "y", tableSpec.y, (v) => setKey("y", v), { kind: "field" }),
      numField(fid("heading"), id("heading"), id("heading"), "heading", tableSpec.heading, (v) => setKey("heading", v), { unit: "°" }),
      numField(fid("tol_xy"), id("tol_xy"), id("tol_xy"), "tol_xy", tableSpec.tol_xy, (v) => setKey("tol_xy", v), { kind: "field", placeholder: units.shown(0.1, "field", 0) }),
      numField(fid("tol_heading"), id("tol_heading"), id("tol_heading"), "tol_heading", tableSpec.tol_heading, (v) => setKey("tol_heading", v), { unit: "°", placeholder: "5", min: 0 }));
    const extra = hooks.poseTools ? hooks.poseTools({ step: i, check: j, spec: tableSpec, setSpec }) : null;
    if (extra) body = h("div", {}, body, extra);
  } else if (kind === "odometry_error") {
    const when = h("select", { id: id("when"), dataset: { key: id("when") } },
      h("option", { value: "end" }, t("at the end of the step")), h("option", { value: "always" }, t("on every tick")));
    when.value = tableSpec.when === "always" ? "always" : "end";
    when.addEventListener("change", () => setKey("when", when.value === "end" ? null : when.value));
    body = h("div", { class: "form-grid" },
      numField(fid("max"), id("max"), id("max"), "max", tableSpec.max, (v) => setKey("max", v), { kind: "field" }),
      field(fid("when"), id("when"), "when", when));
  } else if (kind === "at_end" || kind === "always" || kind === "by_time") {
    body = signalForm(kind, tableSpec, setKey, fid, id);
  } else if (kind === "sensor") {
    body = sensorForm(i, tableSpec, setSpec, fid, id);
  } else if (kind === "must_touch" || kind === "never_touch") {
    const value = typeof spec === "string" ? spec : "";
    const custom = hooks.contactInput ? hooks.contactInput({ value, id: id("target"), onValue: (v) => setSpec(v), kind }) : contactPicker(id("target"), value, (v) => setSpec(v));
    body = custom ? field(`${base}/${kind}`, id("target"), t("Geom or body"), custom)
      : textField(`${base}/${kind}`, id("target"), id("target"), t("Geom or body"), value, (v) => setSpec(v),
        { list: "sc-contact-list", placeholder: t("a geom or body name") });
  } else {   // console
    body = h("div", { class: "form-grid form-grid-wide" },
      textField(fid("contains"), id("contains"), id("contains"), t("Contains"), tableSpec.contains, (v) => setKey("contains", v),
        { placeholder: t("text or regex a line must match") }),
      textField(fid("not_contains"), id("not_contains"), id("not_contains"), t("Doesn't contain"), tableSpec.not_contains,
        (v) => setKey("not_contains", v), { placeholder: t("text or regex no line may match") }));
  }
  // must/never_touch put `<base>/<kind>` on their one field; the others take it on the card
  const cardIds = kind === "must_touch" || kind === "never_touch" ? `${base} ${base}/check` : `${base} ${base}/check ${base}/${kind}`;
  return h("div", { class: `card card-check card-${kind}`, dataset: { fields: cardIds } },
    ...cardHead(CHECK_LABEL()[kind], kind, base, del, "check"),
    h("p", { class: "doc", text: CHECK_DOC()[kind] }),
    body);
}

// A sensor check: name, is (on, the default, or off), sees (a color sensor's named color), when (end, the
// default; always; by, with t). The spec is written in that key order, with the defaults left out.
const SENSOR_KEYS = ["name", "is", "sees", "when", "t"];

function sensorForm(i, spec, setSpec, fid, id) {
  const sensors = robotSensors();
  const name = typeof spec.name === "string" ? spec.name : "";
  const known = sensors ? sensors.find((s) => s.name === name) : null;
  const sensorKind = known ? known.kind : null;    // null: not one of this robot's (or the robot doesn't load)
  const put = (changes) => {
    const next = { ...spec, ...changes };
    const out = {};
    for (const k of [...SENSOR_KEYS, ...Object.keys(next)]) {
      if (k in out || next[k] === null || next[k] === undefined || next[k] === "") continue;
      out[k] = next[k];
    }
    return setSpec(out);
  };
  // the sensor's own signal errors (an unknown sensor, a color it can't see) show on name, or on sees with one
  const signalOnSees = spec.sees !== undefined && known;
  const nameIds = signalOnSees ? fid("name") : `${fid("name")} ${fid("signal")}`;
  let nameField;
  if (sensors && sensors.length) {
    const sel = h("select", { id: id("name"), class: "mono", dataset: { key: id("name") } },
      !name ? h("option", { value: "", disabled: true }, t("(choose)")) : null,
      name && !known ? h("option", { value: name }, t("{name} (not on this robot)", { name })) : null,
      ...sensors.map((s) => h("option", { value: s.name }, `${s.name} (${s.kind})`)));
    sel.value = name;
    sel.addEventListener("change", () => {
      if (!sel.value || sel.value === name) return;
      const to = sensors.find((s) => s.name === sel.value);
      put({ name: sel.value, ...(to && to.kind !== "color" ? { sees: null } : {}) });   // only a color sensor sees
    });
    nameField = field(nameIds, id("name"), t("Sensor"), sel);
  } else {
    nameField = textField(nameIds, id("name"), id("name"), t("Sensor"), name, (v) => put({ name: v }),
      { placeholder: t("a sensor's name") });
  }

  const is = h("select", { id: id("is"), dataset: { key: id("is") } },
    h("option", { value: "on" }, t("on")), h("option", { value: "off" }, t("off")));
  is.value = spec.is === "off" ? "off" : "on";
  is.addEventListener("change", () => put({ is: is.value === "on" ? null : is.value }));
  const fields = [nameField, field(fid("is"), id("is"), "is", is)];

  // sees: a color sensor's (or any, while the kind isn't known; and always when the file has one, to clear it)
  if (sensorKind === "color" || sensorKind === null || spec.sees !== undefined) {
    const sees = typeof spec.sees === "string" ? spec.sees : "";
    const colors = sensorColors();
    const seesIds = signalOnSees ? `${fid("sees")} ${fid("signal")}` : fid("sees");
    if (colors) {
      const sel = h("select", { id: id("sees"), class: "mono", dataset: { key: id("sees") } },
        h("option", { value: "" }, t("any piece")),
        sees && !colors.includes(sees) ? h("option", { value: sees }, t("{name} (not a named color)", { name: sees })) : null,
        ...colors.map((c) => h("option", { value: c }, c)));
      sel.value = sees;
      sel.addEventListener("change", () => { if (sel.value !== sees) put({ sees: sel.value || null }); });
      fields.push(field(seesIds, id("sees"), "sees", sel, { hint: t("a color sensor only") }));
    } else {
      fields.push(textField(seesIds, id("sees"), id("sees"), "sees", sees, (v) => put({ sees: v || null }),
        { placeholder: t("a named color"), hint: t("a color sensor only") }));
    }
  }

  const when = h("select", { id: id("when"), dataset: { key: id("when") } },
    h("option", { value: "end" }, t("at the end of the step")), h("option", { value: "always" }, t("on every tick")),
    h("option", { value: "by" }, t("by a time into the step")));
  const w = spec.when === "always" || spec.when === "by" ? spec.when : "end";
  when.value = w;
  when.addEventListener("change", () => {
    const v = when.value;
    if (v === w) return;
    const dur = sc.state.steps[i].duration;
    // "by" goes with t (the step's duration to start with); the others with none
    put({ when: v === "end" ? null : v, t: v === "by" ? (num(spec.t) ? spec.t : num(dur) ? dur : 1) : null });
  });
  fields.push(field(fid("when"), id("when"), "when", when));
  if (w === "by" || spec.t !== undefined) {
    fields.push(numField(fid("t"), id("t"), id("t"), t("by t"), spec.t, (v) => put({ t: v }),
      { unit: "s", min: 0, hint: t("seconds into the step") }));
  }
  return h("div", { class: "form-grid" }, ...fields);
}

// The signal box with a picker beside it: the robot's signals grouped as /api/scenario/signals lists them, and
// hal: / nt: starters for free text.
function signalPicker(id, value, onValue) {
  const s = sc.signals || {};
  const groups = [
    [t("Robot"), s.fixed || []],
    [t("Motors"), (s.motors || []).flatMap((m) => [`${m}.current`, `${m}.command`])],
    [t("Joints"), (s.joints || []).flatMap((j) => [`${j}.position`, `${j}.velocity`])],
    [t("Test range"), s.range || []],
  ].filter(([, names]) => names.length);
  const known = groups.some(([, names]) => names.includes(value));
  const text = markDirty(h("input", { id, type: "text", value, list: "sc-signal-list", autocomplete: "off", spellcheck: "false",
    class: "mono", placeholder: "battery, motor.<name>.current, nt:/…", dataset: { key: id } }));
  onCommit(text, () => {
    clean(text);
    const v = text.value.trim();
    if (v !== value) onValue(v);
  });
  const pick = h("select", { id: `${id}-pick`, class: "picker", "aria-label": t("Choose a signal"), dataset: { key: `${id}-pick` } },
    h("option", { value: "" }, known ? t("Change ▾") : t("Choose ▾")),
    ...groups.map(([label, names]) => h("optgroup", { label }, ...names.map((n) => h("option", { value: n }, n)))),
    h("optgroup", { label: t("Type it in") },
      h("option", { value: "hal:" }, "hal:<type>/<device>/<key>…"), h("option", { value: "nt:/" }, "nt:/<topic>…")));
  pick.addEventListener("change", () => {
    const v = pick.value;
    pick.value = "";
    if (!v) return;
    if (v === "hal:" || v === "nt:/") {
      text.value = v;
      text.dataset.dirty = "1";
      text.focus();
      text.setSelectionRange(v.length, v.length);
    } else if (v !== value) {
      text.value = v;
      onValue(v);
    }
  });
  return h("div", { class: "row picker-row" }, text, pick);
}

// The target of a must_touch / never_touch check: a list of the world's contacts (null when there are none:
// the text box then takes any name).
function contactPicker(id, value, onValue) {
  const names = (sc.signals && sc.signals.contacts) || [];
  if (!names.length) return null;
  const sel = h("select", { id, class: "mono", dataset: { key: id } },
    !value ? h("option", { value: "", disabled: true }, t("(choose)")) : null,
    value && !names.includes(value) ? h("option", { value }, t("{name} (not in this world)", { name: value })) : null,
    ...names.map((n) => h("option", { value: n }, n)));
  sel.value = value;
  sel.addEventListener("change", () => { if (sel.value && sel.value !== value) onValue(sel.value); });
  return sel;
}

// equals takes a bool, a number or text: "true"/"false" are booleans, numeric text a number.
function parseEquals(text) {
  if (text === "true") return true;
  if (text === "false") return false;
  return isNumText(text) ? Number(text) : text;
}

function signalForm(kind, spec, setKey, fid, id) {
  const sigValue = typeof spec.signal === "string" ? spec.signal : "";
  const custom = hooks.signalInput ? hooks.signalInput({ value: sigValue, id: id("signal"), onValue: (v) => setKey("signal", v), kind }) : null;
  const signal = field(fid("signal"), id("signal"), t("Signal"), custom || signalPicker(id("signal"), sigValue, (v) => setKey("signal", v)),
    { hint: t("Choose one of this robot's signals, or type hal:<type>/<device>/<key> or nt:/<topic>.") });
  const eqText = spec.equals === undefined ? "" : String(spec.equals);
  const eq = textField(fid("equals"), id("equals"), id("equals"), "equals", eqText, (v) => setKey("equals", v === "" ? null : parseEquals(v)),
    { placeholder: t("true, 3, or text") });
  const fields = [
    numField(fid("min"), id("min"), id("min"), "min", spec.min, (v) => setKey("min", v)),
    numField(fid("max"), id("max"), id("max"), "max", spec.max, (v) => setKey("max", v)),
    eq,
  ];
  if (kind === "at_end" || kind === "by_time") {
    fields.push(numField(fid("approx"), id("approx"), id("approx"), "approx", spec.approx, (v) => setKey("approx", v)),
      numField(fid("tol"), id("tol"), id("tol"), "tol", spec.tol, (v) => setKey("tol", v), { min: 0, hint: t("with approx") }));
  }
  if (kind === "by_time") {
    fields.unshift(numField(fid("t"), id("t"), id("t"), t("by t"), spec.t, (v) => setKey("t", v), { unit: "s", min: 0, hint: t("seconds into the step") }));
  }
  const note = {
    at_end: () => t("Pass when the value is within min … max, equals a value, or is approx ± tol."),
    always: () => t("Pass when every sample is within min … max, or equals a value."),
    by_time: () => t("Pass when, at some point up to t seconds into the step, the value is within min … max, equals a value, or is approx ± tol."),
  }[kind]();
  return h("div", {}, signal,
    h("p", { class: "hint form-note" }, note),
    h("div", { class: "form-grid" }, ...fields));
}

// -- screenshots and the contact sheet ----------------------------------------------------------------
// The view picker: the built-in views, then the model's own cameras (camera:<name>).
function viewOptions(current, { free = true } = {}) {
  const cams = ((sc.signals && sc.signals.cameras) || []).map((c) => `camera:${c}`);
  const views = [["follow", t("follow the robot")], ["top", t("top (whole field)")]];
  if (free) views.push(["free", t("free camera")]);
  const out = [h("optgroup", { label: t("Views") }, ...views.map(([v, label]) => h("option", { value: v }, label))),
    h("optgroup", { label: t("Model cameras") }, ...(cams.length ? cams.map((c) => h("option", { value: c }, c))
      : [h("option", { value: "", disabled: true }, t("(this model has no cameras)"))]))];
  if (current && !views.some(([v]) => v === current) && !cams.includes(current)) {
    out.push(h("option", { value: current }, t("{name} (not in this model)", { name: current })));
  }
  return out;
}

function shotsSection(i, step) {
  const addShot = h("button", { type: "button", class: "btn btn-small", onclick: () => scEdit("add_shot", { step: i, spec: { view: "follow" } }) },
    icon("plus"), " ", t("Add screenshot"));
  const sheetOn = h("input", { type: "checkbox", id: "ed-sheet-on", checked: !!step.sheet, dataset: { key: "sheet-on" } });
  sheetOn.addEventListener("change", () => scEdit("sheet", { step: i, spec: sheetOn.checked ? { frames: 8 } : null }));
  return section(t("Screenshots"), `step${i}/shot`,
    step.shots.length ? h("div", { class: "cards" }, ...step.shots.map((sh, j) => shotCard(i, j, sh, step))) : null,
    h("div", { class: "ed-actions" }, addShot),
    h("div", { class: "sheet", dataset: { fields: `step${i}/sheet` } },
      h("label", { class: "check-label", for: "ed-sheet-on" }, sheetOn, h("strong", {}, t("Contact sheet")),
        h("span", { class: "muted" }, " — ", t("one image with frames from across the step"))),
      h("div", { class: "field-msgs" }),
      step.sheet ? sheetForm(i, step.sheet) : null));
}

function shotCard(i, j, sh, step) {
  const base = `step${i}/shot${j}`;
  const id = (k) => `sh-${i}-${j}-${k}`;
  const set = (spec) => scEdit("set_shot", { step: i, shot: j, spec });
  const view = typeof sh.view === "string" ? sh.view : "follow";
  const viewSel = h("select", { id: id("view"), dataset: { key: id("view") } }, ...viewOptions(view));
  viewSel.value = view;
  viewSel.addEventListener("change", () => {
    const v = viewSel.value;
    if (v === "free") set({ view: v, camera: { lookat: [0, 0, 0], ...(sh.camera || {}) } });   // free needs a lookat
    else set({ view: v, ...(view === "free" ? { camera: null } : {}) });
  });
  const at = sh.at === undefined ? "end" : sh.at;
  const atSel = h("select", { id: id("at-kind"), "aria-label": t("When"), dataset: { key: id("at-kind") } },
    h("option", { value: "end" }, t("end of the step")), h("option", { value: "time" }, t("a time into the step")));
  atSel.value = num(at) ? "time" : "end";
  atSel.addEventListener("change", () => set({ at: atSel.value === "end" ? null : 0 }));
  let atField;
  const atBox = num(at) ? numBox({ id: id("at"), key: id("at"), value: at, label: "at", min: 0, fieldEl: () => atField,
    onValue: (v) => set({ at: v }) }) : null;
  atField = h("div", { class: `field${atBox ? " field-span" : ""}`, dataset: { fields: `${base}/at` } },
    h("label", { for: id("at-kind"), class: "field-label" }, "at"),
    h("div", { class: "row" }, atSel, atBox ? h("span", { class: "with-unit" }, atBox, h("span", { class: "unit" }, "s")) : null),
    h("div", { class: "field-msgs" }));
  const grid = h("div", { class: "form-grid" },
    field(`${base}/view`, id("view"), "view", viewSel), atField,
    numField(`${base}/width`, id("width"), id("width"), "width", sh.width, (v) => set({ width: v }), { unit: "px", placeholder: "960", int: true }),
    numField(`${base}/height`, id("height"), id("height"), "height", sh.height, (v) => set({ height: v }), { unit: "px", placeholder: "600", int: true }));
  return h("div", { class: "card card-shot", dataset: { fields: base } },
    ...cardHead(t("Screenshot {n}", { n: j + 1 }), null, base, () => scEdit("delete_shot", { step: i, shot: j }), "screenshot"),
    grid,
    view === "free" ? cameraForm(base, id, sh.camera, (cam) => set({ camera: cam })) : null);
}

function cameraForm(base, id, camera, setCam) {
  const cam = camera && typeof camera === "object" ? camera : {};
  const la = Array.isArray(cam.lookat) ? cam.lookat : [0, 0, 0];
  const put = (k, v) => {
    const next = { ...cam };
    if (v === null) delete next[k]; else next[k] = v;
    return setCam(next);
  };
  const lookat = h("div", { class: "vec" }, ...["x", "y", "z"].map((ax, n) => {
    let cell;
    const box = numBox({ id: id(`la-${ax}`), key: id(`la-${ax}`), value: la[n], label: `lookat ${ax}`, kind: "field", fieldEl: () => cell.closest(".field"),
      onValue: (v) => { const next = [...la]; next[n] = v === null ? 0 : v; put("lookat", next); } });
    cell = h("label", { class: "vec-cell" }, h("span", { class: "vec-axis" }, ax), box);
    return cell;
  }));
  return h("div", { class: "camera", dataset: { fields: `${base}/camera` } },
    h("div", { class: "field-msgs" }),
    h("div", { class: "form-grid" },
      h("div", { class: "field field-span", role: "group", "aria-label": t("Camera looks at"), dataset: { fields: `${base}/camera.lookat` } },
        h("span", { class: "field-label" }, t("camera lookat ({unit})", { unit: units.name("field") })), lookat, h("div", { class: "field-msgs" })),
      numField(`${base}/camera.distance`, id("distance"), id("distance"), "distance", cam.distance, (v) => put("distance", v), { kind: "field" }),
      numField(`${base}/camera.azimuth`, id("azimuth"), id("azimuth"), "azimuth", cam.azimuth, (v) => put("azimuth", v), { unit: "°" }),
      numField(`${base}/camera.elevation`, id("elevation"), id("elevation"), "elevation", cam.elevation, (v) => put("elevation", v), { unit: "°" })));
}

function sheetForm(i, sheet) {
  const base = `step${i}/sheet`;
  const id = (k) => `cs-${i}-${k}`;
  const set = (spec) => scEdit("sheet", { step: i, spec });
  const view = typeof sheet.view === "string" ? sheet.view : "follow";
  const viewSel = h("select", { id: id("view"), dataset: { key: id("view") } }, ...viewOptions(view, { free: false }));
  viewSel.value = view;
  viewSel.addEventListener("change", () => set({ view: viewSel.value }));
  return h("div", { class: "form-grid" },
    numField(`${base}/frames`, id("frames"), id("frames"), "frames", sheet.frames, (v) => set({ frames: v }), { placeholder: "8", min: 1, max: 16, int: true }),
    field(`${base}/view`, id("view"), "view", viewSel),
    numField(`${base}/width`, id("width"), id("width"), t("frame width"), sheet.width, (v) => set({ width: v }), { unit: "px", placeholder: "480", int: true }),
    numField(`${base}/height`, id("height"), id("height"), t("frame height"), sheet.height, (v) => set({ height: v }), { unit: "px", placeholder: "300", int: true }));
}
