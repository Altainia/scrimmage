// The step editor (center of the Scenario tab): name / mode / duration, joystick inputs per port (or, for a step
// with an input track, trackplot.js's track panel), the checks with one form per kind, screenshots and the
// contact sheet. Every change posts one scenario edit; each field
// carries data-fields with its field ids so errors render beside it (scenario.js renderErrors).

import { h, fmt } from "./app.js";
import { MODES, scEdit, hooks, sc } from "./scenario.js";
import { trackPanel } from "./trackplot.js";

// -- shared form helpers (also used by the header in scenario.js) --------------------------------
let rerendering = false;   // removing a focused input must not commit it

export function guard(fn) {
  rerendering = true;
  try { fn(); } finally { rerendering = false; }
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
  el.addEventListener("change", run);
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
  const t = document.createElement("template");
  t.innerHTML = `<svg viewBox="0 0 16 16" class="icon icon-${name}" aria-hidden="true"><path d="${paths[name] || ""}"/></svg>`;
  return t.content.firstChild;
}

const num = (v) => typeof v === "number" && isFinite(v);
const isNumText = (t) => t !== "" && isFinite(Number(t));

// A number box for one key of a table value. onValue(number | null) — null when emptied.
function numBox({ id, key, value, placeholder, min, max, step = "any", label, fieldEl, onValue, int = false }) {
  const el = markDirty(h("input", { id, type: "number", step: int ? "1" : step, inputmode: int ? "numeric" : "decimal",
    value: num(value) ? fmt(value) : "", placeholder, min, max, "aria-label": label, dataset: { key } }));
  onCommit(el, () => {
    if (badNumber(el)) { localError(fieldEl(), `${label || key} must be a number`); return; }
    clean(el);
    const t = el.value.trim();
    const v = t === "" ? null : Number(t);
    if (v === (num(value) ? value : null)) return;
    onValue(v);
  });
  return el;
}

// A field with a number box; returns the field element.
function numField(fieldIds, id, key, label, value, onValue, { unit, placeholder, hint, min, max, int } = {}) {
  let f;
  const box = numBox({ id, key, value, placeholder, min, max, int, label, fieldEl: () => f, onValue });
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
    ? { key: act.dataset.key, dirty: act.dataset.dirty ? act.value : null } : null;
  const i = st.selected;
  const step = i === null ? null : s.steps[i];
  if (!step) {
    lastStep = null;
    title.textContent = "Step";
    root.replaceChildren(h("p", { class: "empty" }, "Add a step on the left to start the scenario."));
    return;
  }
  const switched = lastStep !== i;
  lastStep = i;
  title.textContent = `Step ${i + 1}${step.name ? ` · ${step.name}` : ""}`;
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
  const name = textField(`step${i}/name`, "ed-name", "name", "Name", step.name,
    (v) => setStep({ name: v || null }), { placeholder: `step ${i + 1}`, mono: false });
  const modeField = h("div", { class: "field", role: "group", "aria-labelledby": "ed-mode-label", dataset: { fields: `step${i}/mode` } },
    h("span", { id: "ed-mode-label", class: "field-label" }, "Mode"),
    h("div", { class: "mode-seg" }, ...MODES.map((m) => h("button", {
      type: "button", class: `mode-btn mode-${m}`, "aria-pressed": String(step.mode === m), dataset: { key: `mode-${m}` },
      onclick: () => { if (step.mode !== m) setStep({ mode: m }); },
    }, m))),
    !MODES.includes(step.mode) && step.mode != null ? h("p", { class: "hint", text: `The file says mode = ${JSON.stringify(step.mode)}.` }) : null,
    h("div", { class: "field-msgs" }));
  const dur = numField(`step${i}/duration`, "ed-duration", "duration", "Duration", step.duration,
    (v) => setStep({ duration: v }), { unit: "s", min: 0 });
  const startT = h("p", { class: "hint ed-start", text: `Starts at ${+step.start_t.toFixed(3)} s` });
  return h("section", { class: "ed-section ed-head", dataset: { fields: `step${i} step${i}/step` } },
    h("div", { class: "field-msgs" }),
    h("div", { class: "ed-head-grid" }, name, modeField, h("div", {}, dur, startT)));
}

// -- joystick inputs -----------------------------------------------------------------------------------
const POV_DIRS = [[315, "↖"], [0, "↑"], [45, "↗"], [270, "←"], [-1, "·"], [90, "→"], [225, "↙"], [180, "↓"], [135, "↘"]];

function inputsSection(i, step) {
  const ports = new Set([...Object.keys(step.axes), ...Object.keys(step.buttons), ...Object.keys(step.povs)].map(Number));
  for (const p of sc.extraPorts.get(i) || []) ports.add(p);
  const sorted = [...ports].filter((p) => p >= 0 && p <= 5).sort((a, b) => a - b);
  const free = [0, 1, 2, 3, 4, 5].filter((p) => !ports.has(p));
  const add = h("select", { id: "ed-add-port", class: "add-select", "aria-label": "Add a joystick port", dataset: { key: "add-port" } },
    h("option", { value: "" }, "Add port ▾"), ...free.map((p) => h("option", { value: String(p) }, `Port ${p}`)));
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
  return section(["Joystick inputs", h("span", { class: "ed-sub" }, "held for the whole step")],
    `step${i}/axes step${i}/buttons step${i}/povs`,
    sorted.length ? h("div", { class: "ports" }, ...sorted.map((p) => portCard(i, step, p)))
      : h("p", { class: "empty empty-small" }, "No joystick input in this step: every port is neutral."),
    free.length ? h("div", { class: "ed-actions" }, add) : null);
}

// A step with an input track: the track panel (loaded after the editor is drawn) in place of the constant inputs.
function trackSection(i, step) {
  const body = h("p", { class: "muted track-loading" }, "Loading the track…");
  trackPanel(i, step).then((el) => { if (body.isConnected) body.replaceWith(el); },
    (e) => { if (body.isConnected) body.replaceWith(h("p", { class: "error", text: `Couldn't show the track: ${e.message || e}` })); });
  return section(["Input track", h("span", { class: "ed-sub" }, "replayed step for step")], `step${i}/inputs`, body);
}

function portCard(i, step, port) {
  const key = String(port);
  const axes = step.axes[key] || [], buttons = step.buttons[key] || [], povs = step.povs[key] || [];
  const set = (kind, values) => scEdit("inputs", { step: i, kind, port, values: values.length ? values : null });

  const axisRows = axes.map((v, n) => {
    const id = `ax-${port}-${n}`;
    const val = num(v) ? v : 0;
    const slider = h("input", { type: "range", min: "-1", max: "1", step: "0.05", value: String(val), "aria-label": `Port ${port} axis ${n}`,
      dataset: { key: `${id}-r` } });
    const box = markDirty(h("input", { id, type: "number", min: "-1", max: "1", step: "0.05", value: fmt(val), class: "axis-num",
      "aria-label": `Port ${port} axis ${n} value`, dataset: { key: id } }));
    const commit = (x) => { const next = [...axes]; next[n] = x; set("axes", next); };
    slider.addEventListener("input", () => { box.value = slider.value; });
    slider.addEventListener("change", () => commit(Number(slider.value)));
    let row;
    onCommit(box, () => {
      if (badNumber(box) || box.value.trim() === "") { localError(row, "an axis is a number between -1 and 1"); return; }
      clean(box);
      const x = Number(box.value);
      if (x !== val) commit(x);
    });
    row = h("div", { class: "axis-row field" }, h("span", { class: "idx" }, `Axis ${n}`), slider, box, h("div", { class: "field-msgs" }));
    return row;
  });

  const btns = buttons.map((b, n) => h("button", {
    type: "button", class: "toggle", "aria-pressed": String(b === true), title: `Button ${n + 1}${b ? " (held)" : ""}`, dataset: { key: `bt-${port}-${n}` },
    onclick: () => { const next = [...buttons]; next[n] = !(b === true); set("buttons", next); },
  }, String(n + 1)));

  const povRows = povs.map((v, n) => {
    const id = `pov-${port}-${n}`;
    const compass = h("div", { class: "compass", role: "group", "aria-label": `Port ${port} POV ${n}` },
      ...POV_DIRS.map(([deg, glyph]) => h("button", {
        type: "button", class: "compass-btn", "aria-pressed": String(v === deg), title: deg === -1 ? "Released (-1)" : `${deg}°`,
        "aria-label": deg === -1 ? "released" : `${deg} degrees`, dataset: { key: `${id}-${deg}` },
        onclick: () => { if (v !== deg) { const next = [...povs]; next[n] = deg; set("povs", next); } },
      }, glyph)));
    let row;
    const box = numBox({ id, key: id, value: v, label: `POV ${n}`, int: true, min: -1, max: 359, fieldEl: () => row,
      onValue: (x) => { const next = [...povs]; next[n] = x === null ? -1 : x; set("povs", next); } });
    row = h("div", { class: "pov-row field" }, h("span", { class: "idx" }, `POV ${n}`), compass,
      h("span", { class: "with-unit" }, box, h("span", { class: "unit" }, "°")), h("div", { class: "field-msgs" }));
    return row;
  });

  const plusMinus = (kind, list, fresh, what) => h("span", { class: "pm" },
    h("button", { type: "button", class: "btn btn-small", "aria-label": `Add ${what} on port ${port}`, onclick: () => set(kind, [...list, fresh]) }, `+ ${what}`),
    list.length ? h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": `Remove the last ${what} on port ${port}`,
      onclick: () => set(kind, list.slice(0, -1)) }, `− ${what}`) : null);

  return h("div", { class: "port", dataset: { port: key } },
    h("div", { class: "port-head" }, icon("joystick"), h("strong", {}, `Port ${port}`)),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, "Axes"), plusMinus("axes", axes, 0, "axis")),
      axisRows.length ? axisRows : h("p", { class: "muted small" }, "none (−1 … 1 each)")),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, "Buttons"), plusMinus("buttons", buttons, false, "button")),
      btns.length ? h("div", { class: "toggles" }, btns) : h("p", { class: "muted small" }, "none (1 is the first button)")),
    h("div", { class: "port-kind" }, h("div", { class: "port-kind-head" }, h("span", { class: "kind-label" }, "POVs"), plusMinus("povs", povs, -1, "POV")),
      povRows.length ? povRows : h("p", { class: "muted small" }, "none (−1 is released)")));
}

// -- checks ----------------------------------------------------------------------------------------------
export const CHECK_KINDS = ["pose", "odometry_error", "at_end", "always", "by_time", "must_touch", "never_touch", "console"];
const CHECK_LABEL = {
  pose: "Pose at the end", odometry_error: "Odometry error", at_end: "Signal at the end", always: "Signal throughout",
  by_time: "Signal by a time", must_touch: "Must touch", never_touch: "Never touch", console: "Console output",
};
const CHECK_DOC = {
  pose: "Where the robot is when the step ends (field meters, heading in degrees).",
  odometry_error: "How far the robot's own odometry (on NetworkTables) is from where it really is.",
  at_end: "A signal's value when the step ends.",
  always: "A signal's value on every tick of the step.",
  by_time: "A signal reaches a value at some point up to t seconds into the step (e.g. 8 scored by 15 s).",
  must_touch: "The robot touches this geom or body at some point in the step.",
  never_touch: "The robot never touches this geom or body in the step.",
  console: "A line the robot program prints during the step (a regular expression).",
};

function defaultSpec(kind, step) {
  const contacts = (sc.signals && sc.signals.contacts) || [];
  const scored = ((sc.signals && sc.signals.range) || []).includes("score");
  return {
    pose: { x: 0, y: 0 }, odometry_error: { max: 0.1 }, at_end: { signal: "battery", min: 10 },
    always: { signal: "battery", min: 10 },
    by_time: scored ? { signal: "score", min: 1, t: num(step.duration) ? step.duration : 1 }
      : { signal: "battery", min: 10, t: num(step.duration) ? step.duration : 1 }, must_touch: contacts[0] || "", never_touch: contacts[0] || "",
    console: { contains: "" },
  }[kind];
}

function checksSection(i, step) {
  const noContacts = !((sc.signals && sc.signals.contacts) || []).length;
  const add = h("select", { id: "ed-add-check", class: "add-select", "aria-label": "Add a check", dataset: { key: "add-check" } },
    h("option", { value: "" }, "Add check ▾"), ...CHECK_KINDS.map((k) => {
      const off = noContacts && (k === "must_touch" || k === "never_touch");
      return h("option", { value: k, disabled: off, title: off ? "This robot's world has nothing to touch (or it doesn't load)" : null },
        `${CHECK_LABEL[k]} (${k})${off ? " (nothing to touch)" : ""}`);
    }));
  add.addEventListener("change", () => {
    const k = add.value;
    add.value = "";
    if (k) scEdit("add_check", { step: i, kind: k, spec: defaultSpec(k, step) });
  });
  return section(["Checks", h("span", { class: "ed-sub" }, "judged at the end of the step")], `step${i}/check`,
    step.checks.length ? h("div", { class: "cards" }, ...step.checks.map((c, j) => checkCard(i, j, c)))
      : h("p", { class: "empty empty-small" }, "No checks: this step only drives the robot."),
    h("div", { class: "ed-actions" }, add));
}

function cardHead(title, kindText, fieldIds, onDelete, what) {
  return [h("div", { class: "card-head" },
    h("strong", {}, title), kindText ? h("code", { class: "kind" }, kindText) : null,
    h("button", { type: "button", class: "btn btn-small btn-ghost card-del", "aria-label": `Delete this ${what}`, title: `Delete this ${what}`, onclick: onDelete }, icon("trash"))),
  h("div", { class: "field-msgs" })];
}

function checkCard(i, j, c) {
  const base = `step${i}/check${j}`;
  const del = () => scEdit("delete_check", { step: i, check: j });
  if (!c.kind || !CHECK_KINDS.includes(c.kind)) {
    return h("div", { class: "card", dataset: { fields: `${base} ${base}/check` } },
      ...cardHead(`Check ${j + 1}`, c.kind || "?", base, del, "check"),
      h("p", { class: "hint", text: "This check isn't one of the kinds the editor knows; change it in a text editor or delete it." }),
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
      numField(fid("x"), id("x"), id("x"), "x", tableSpec.x, (v) => setKey("x", v), { unit: "m" }),
      numField(fid("y"), id("y"), id("y"), "y", tableSpec.y, (v) => setKey("y", v), { unit: "m" }),
      numField(fid("heading"), id("heading"), id("heading"), "heading", tableSpec.heading, (v) => setKey("heading", v), { unit: "°" }),
      numField(fid("tol_xy"), id("tol_xy"), id("tol_xy"), "tol_xy", tableSpec.tol_xy, (v) => setKey("tol_xy", v), { unit: "m", placeholder: "0.1", min: 0 }),
      numField(fid("tol_heading"), id("tol_heading"), id("tol_heading"), "tol_heading", tableSpec.tol_heading, (v) => setKey("tol_heading", v), { unit: "°", placeholder: "5", min: 0 }));
    const extra = hooks.poseTools ? hooks.poseTools({ step: i, check: j, spec: tableSpec, setSpec }) : null;
    if (extra) body = h("div", {}, body, extra);
  } else if (kind === "odometry_error") {
    const when = h("select", { id: id("when"), dataset: { key: id("when") } },
      h("option", { value: "end" }, "at the end of the step"), h("option", { value: "always" }, "on every tick"));
    when.value = tableSpec.when === "always" ? "always" : "end";
    when.addEventListener("change", () => setKey("when", when.value === "end" ? null : when.value));
    body = h("div", { class: "form-grid" },
      numField(fid("max"), id("max"), id("max"), "max", tableSpec.max, (v) => setKey("max", v), { unit: "m", min: 0 }),
      field(fid("when"), id("when"), "when", when));
  } else if (kind === "at_end" || kind === "always" || kind === "by_time") {
    body = signalForm(kind, tableSpec, setKey, fid, id);
  } else if (kind === "must_touch" || kind === "never_touch") {
    const value = typeof spec === "string" ? spec : "";
    const custom = hooks.contactInput ? hooks.contactInput({ value, id: id("target"), onValue: (v) => setSpec(v), kind }) : contactPicker(id("target"), value, (v) => setSpec(v));
    body = custom ? field(`${base}/${kind}`, id("target"), "Geom or body", custom)
      : textField(`${base}/${kind}`, id("target"), id("target"), "Geom or body", value, (v) => setSpec(v),
        { list: "sc-contact-list", placeholder: "a geom or body name" });
  } else {   // console
    body = h("div", { class: "form-grid form-grid-wide" },
      textField(fid("contains"), id("contains"), id("contains"), "Contains", tableSpec.contains, (v) => setKey("contains", v),
        { placeholder: "text or regex a line must match" }),
      textField(fid("not_contains"), id("not_contains"), id("not_contains"), "Doesn't contain", tableSpec.not_contains,
        (v) => setKey("not_contains", v), { placeholder: "text or regex no line may match" }));
  }
  // must/never_touch put `<base>/<kind>` on their one field; the others take it on the card
  const cardIds = kind === "must_touch" || kind === "never_touch" ? `${base} ${base}/check` : `${base} ${base}/check ${base}/${kind}`;
  return h("div", { class: `card card-check card-${kind}`, dataset: { fields: cardIds } },
    ...cardHead(CHECK_LABEL[kind], kind, base, del, "check"),
    h("p", { class: "doc", text: CHECK_DOC[kind] }),
    body);
}

// The signal box with a picker beside it: the robot's signals grouped as /api/scenario/signals lists them, and
// hal: / nt: starters for free text.
function signalPicker(id, value, onValue) {
  const s = sc.signals || {};
  const groups = [
    ["Robot", s.fixed || []],
    ["Motors", (s.motors || []).flatMap((m) => [`${m}.current`, `${m}.command`])],
    ["Joints", (s.joints || []).flatMap((j) => [`${j}.position`, `${j}.velocity`])],
    ["Test range", s.range || []],
  ].filter(([, names]) => names.length);
  const known = groups.some(([, names]) => names.includes(value));
  const text = markDirty(h("input", { id, type: "text", value, list: "sc-signal-list", autocomplete: "off", spellcheck: "false",
    class: "mono", placeholder: "battery, motor.<name>.current, nt:/…", dataset: { key: id } }));
  onCommit(text, () => {
    clean(text);
    const v = text.value.trim();
    if (v !== value) onValue(v);
  });
  const pick = h("select", { id: `${id}-pick`, class: "picker", "aria-label": "Choose a signal", dataset: { key: `${id}-pick` } },
    h("option", { value: "" }, known ? "Change ▾" : "Choose ▾"),
    ...groups.map(([label, names]) => h("optgroup", { label }, ...names.map((n) => h("option", { value: n }, n)))),
    h("optgroup", { label: "Type it in" },
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
    !value ? h("option", { value: "", disabled: true }, "(choose)") : null,
    value && !names.includes(value) ? h("option", { value }, `${value} (not in this world)`) : null,
    ...names.map((n) => h("option", { value: n }, n)));
  sel.value = value;
  sel.addEventListener("change", () => { if (sel.value && sel.value !== value) onValue(sel.value); });
  return sel;
}

// equals takes a bool, a number or text: "true"/"false" are booleans, numeric text a number.
function parseEquals(t) {
  if (t === "true") return true;
  if (t === "false") return false;
  return isNumText(t) ? Number(t) : t;
}

function signalForm(kind, spec, setKey, fid, id) {
  const sigValue = typeof spec.signal === "string" ? spec.signal : "";
  const custom = hooks.signalInput ? hooks.signalInput({ value: sigValue, id: id("signal"), onValue: (v) => setKey("signal", v), kind }) : null;
  const signal = field(fid("signal"), id("signal"), "Signal", custom || signalPicker(id("signal"), sigValue, (v) => setKey("signal", v)),
    { hint: "Choose one of this robot's signals, or type hal:<type>/<device>/<key> or nt:/<topic>." });
  const eqText = spec.equals === undefined ? "" : String(spec.equals);
  const eq = textField(fid("equals"), id("equals"), id("equals"), "equals", eqText, (v) => setKey("equals", v === "" ? null : parseEquals(v)),
    { placeholder: "true, 3, or text" });
  const fields = [
    numField(fid("min"), id("min"), id("min"), "min", spec.min, (v) => setKey("min", v)),
    numField(fid("max"), id("max"), id("max"), "max", spec.max, (v) => setKey("max", v)),
    eq,
  ];
  if (kind === "at_end" || kind === "by_time") {
    fields.push(numField(fid("approx"), id("approx"), id("approx"), "approx", spec.approx, (v) => setKey("approx", v)),
      numField(fid("tol"), id("tol"), id("tol"), "tol", spec.tol, (v) => setKey("tol", v), { min: 0, hint: "with approx" }));
  }
  if (kind === "by_time") {
    fields.unshift(numField(fid("t"), id("t"), id("t"), "by t", spec.t, (v) => setKey("t", v), { unit: "s", min: 0, hint: "seconds into the step" }));
  }
  const note = {
    at_end: "Pass when the value is within min … max, equals a value, or is approx ± tol.",
    always: "Pass when every sample is within min … max, or equals a value.",
    by_time: "Pass when, at some point up to t seconds into the step, the value is within min … max, equals a value, or is approx ± tol.",
  }[kind];
  return h("div", {}, signal,
    h("p", { class: "hint form-note" }, note),
    h("div", { class: "form-grid" }, ...fields));
}

// -- screenshots and the contact sheet ----------------------------------------------------------------
// The view picker: the built-in views, then the model's own cameras (camera:<name>).
function viewOptions(current, { free = true } = {}) {
  const cams = ((sc.signals && sc.signals.cameras) || []).map((c) => `camera:${c}`);
  const views = [["follow", "follow the robot"], ["top", "top (whole field)"]];
  if (free) views.push(["free", "free camera"]);
  const out = [h("optgroup", { label: "Views" }, ...views.map(([v, label]) => h("option", { value: v }, label))),
    h("optgroup", { label: "Model cameras" }, ...(cams.length ? cams.map((c) => h("option", { value: c }, c))
      : [h("option", { value: "", disabled: true }, "(this model has no cameras)")]))];
  if (current && !views.some(([v]) => v === current) && !cams.includes(current)) {
    out.push(h("option", { value: current }, `${current} (not in this model)`));
  }
  return out;
}

function shotsSection(i, step) {
  const addShot = h("button", { type: "button", class: "btn btn-small", onclick: () => scEdit("add_shot", { step: i, spec: { view: "follow" } }) },
    icon("plus"), " Add screenshot");
  const sheetOn = h("input", { type: "checkbox", id: "ed-sheet-on", checked: !!step.sheet, dataset: { key: "sheet-on" } });
  sheetOn.addEventListener("change", () => scEdit("sheet", { step: i, spec: sheetOn.checked ? { frames: 8 } : null }));
  return section("Screenshots", `step${i}/shot`,
    step.shots.length ? h("div", { class: "cards" }, ...step.shots.map((sh, j) => shotCard(i, j, sh, step))) : null,
    h("div", { class: "ed-actions" }, addShot),
    h("div", { class: "sheet", dataset: { fields: `step${i}/sheet` } },
      h("label", { class: "check-label", for: "ed-sheet-on" }, sheetOn, h("strong", {}, "Contact sheet"),
        h("span", { class: "muted" }, " — one image with frames from across the step")),
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
  const atSel = h("select", { id: id("at-kind"), "aria-label": "When", dataset: { key: id("at-kind") } },
    h("option", { value: "end" }, "end of the step"), h("option", { value: "time" }, "a time into the step"));
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
    ...cardHead(`Screenshot ${j + 1}`, null, base, () => scEdit("delete_shot", { step: i, shot: j }), "screenshot"),
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
    const box = numBox({ id: id(`la-${ax}`), key: id(`la-${ax}`), value: la[n], label: `lookat ${ax}`, fieldEl: () => cell.closest(".field"),
      onValue: (v) => { const next = [...la]; next[n] = v === null ? 0 : v; put("lookat", next); } });
    cell = h("label", { class: "vec-cell" }, h("span", { class: "vec-axis" }, ax), box);
    return cell;
  }));
  return h("div", { class: "camera", dataset: { fields: `${base}/camera` } },
    h("div", { class: "field-msgs" }),
    h("div", { class: "form-grid" },
      h("div", { class: "field field-span", role: "group", "aria-label": "Camera looks at", dataset: { fields: `${base}/camera.lookat` } },
        h("span", { class: "field-label" }, "camera lookat (m)"), lookat, h("div", { class: "field-msgs" })),
      numField(`${base}/camera.distance`, id("distance"), id("distance"), "distance", cam.distance, (v) => put("distance", v), { unit: "m", min: 0 }),
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
    numField(`${base}/width`, id("width"), id("width"), "frame width", sheet.width, (v) => set({ width: v }), { unit: "px", placeholder: "480", int: true }),
    numField(`${base}/height`, id("height"), id("height"), "frame height", sheet.height, (v) => set({ height: v }), { unit: "px", placeholder: "300", int: true }));
}
