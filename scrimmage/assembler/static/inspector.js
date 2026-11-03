// The inspector (right column, below the tree): the selected part's name, placement, parameters and device IDs.
// A motor slot also has its brake / coast and invert controls (the op `motor`): scrimmage can't read those from the
// robot code. A slot whose table holds only its device and those two settings is still shown in the short form.
// Each change posts one edit. Every field's container carries data-field="<instance>/<key>" (the server's
// field ids), so errors and warnings render beside it; placeErrors returns the ones that have no field here.

import { $, h, edit, store, stockMeta, folderMeta, attachGroups, fillAttachSelect, fmt, UNIT, mountsOf, notice } from "./app.js";

const NUDGE = { pos: 0.01, rot: 5 };          // 10 mm, 5 degrees
const SHORT = { TalonFX: ["motor", "ctre"], CANcoder: ["encoder", "ctre"], Pigeon2: ["gyro", "ctre"], PWM: ["motor", "pwm"], DIO: ["encoder", "channel_a"],
  SparkMax: ["motor", "rev"], SparkFlex: ["motor", "rev"], SparkMaxAbs: ["encoder", "rev"], SparkFlexAbs: ["encoder", "rev"] };
const slotModes = new Map();                  // "<inst>/<slot>" -> "short" | "table" | "list" chosen locally
let rerendering = false;                      // removing a focused input must not commit it
let lastRendered = null;                      // the part the inspector shows

const stripPrefix = (msg) => String(msg).replace(/^parts\[\d+\] '[^']*': /, "");

// -- errors -------------------------------------------------------------------------------------
export function placeErrors(st, errors, warnings) {
  const root = $("#inspector");
  for (const el of root.querySelectorAll(".field-msgs")) el.replaceChildren();
  for (const el of root.querySelectorAll(".has-error")) el.classList.remove("has-error");
  const left = { errors: [], warnings: [] };
  const put = (e, kind) => {
    const box = e.field && e.field !== "status"
      ? root.querySelector(`[data-field="${CSS.escape(e.field)}"] > .field-msgs`) : null;
    if (!box) { left[kind === "error" ? "errors" : "warnings"].push(e); return; }
    box.append(h("p", { class: kind === "error" ? "error" : "warning", role: kind === "error" ? "alert" : null, text: stripPrefix(e.message) }));
    if (kind === "error") box.parentElement.classList.add("has-error");
  };
  errors.forEach((e) => put(e, "error"));
  warnings.forEach((w) => put(w, "warning"));
  return left;
}

// -- small builders -----------------------------------------------------------------------------
function field(fieldId, labelFor, label, control, { hint, doc, extraClass } = {}) {
  return h("div", { class: `field${extraClass ? " " + extraClass : ""}`, dataset: { field: fieldId } },
    h("label", { for: labelFor, class: "field-label" }, label),
    doc ? h("p", { class: "doc", text: doc }) : null,
    control,
    hint ? h("p", { class: "hint", text: hint }) : null,
    h("div", { class: "field-msgs" }));
}

export function markDirty(el) { el.addEventListener("input", () => { el.dataset.dirty = "1"; }); return el; }
export function clean(...els) { for (const el of els) delete el.dataset.dirty; }

// A text box for a number, not type=number: a number box reads "1." as "1", so the re-render (which carries the
// half-typed text into the new box) would turn "1." then "5" into 15.
function numberInput(id, key, value, { label } = {}) {
  return markDirty(h("input", { id, type: "text", inputmode: "decimal", autocomplete: "off", value: fmt(value), dataset: { key }, "aria-label": label }));
}

// Text that isn't a number ("0.1-", "1e"): check it before using .value. A type=number box (code.js) reports it
// with validity.badInput and an empty value, which would otherwise be taken as an empty box.
const NUMBER_TEXT = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
export const badNumber = (...inputs) => inputs.some((i) => (i.validity && i.validity.badInput)
  || (i.type === "text" && i.value.trim() !== "" && !NUMBER_TEXT.test(i.value.trim())));

// Commit on change; also on blur while still dirty, because a value restored across a re-render
// (see renderInspector) doesn't fire "change". Every commit function cleans its inputs. A box the re-render removes
// fires "change" in Chromium; it is ignored like its blur (the half-typed text goes on in the new box).
export function onCommit(el, fn) {
  el.addEventListener("change", () => { if (!rerendering && el.isConnected) fn(); });
  el.addEventListener("blur", () => { if (el.dataset.dirty && !rerendering && el.isConnected) fn(); });
  el.addEventListener("keydown", (e) => { if (e.key === "Enter" && el.tagName === "INPUT" && el.type !== "checkbox") { e.preventDefault(); el.blur(); } });
  return el;
}

// Several inputs that make one value (a table, a motor list): commit when focus leaves the group, or on Enter.
function onGroupCommit(group, commit) {
  group.addEventListener("focusout", (e) => {
    if (rerendering || !group.isConnected) return;
    if (e.relatedTarget && group.contains(e.relatedTarget)) return;
    if ([...group.querySelectorAll("input")].some((i) => i.dataset.dirty)) commit();
  });
  group.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); commit(); }
  });
}

function localError(fieldEl, message) {
  const box = fieldEl.querySelector(":scope > .field-msgs");
  box.replaceChildren(h("p", { class: "error", role: "alert", text: message }));
  fieldEl.classList.add("has-error");
}

const unit = (u) => UNIT[u] || u;

// -- render -------------------------------------------------------------------------------------
export function renderInspector(st) {
  const root = $("#inspector");
  // remember focus (and what the student is half-way through typing) across the re-render
  const act = document.activeElement;
  const keep = act && root.contains(act) && act.dataset.key && lastRendered === st.selected
    ? { key: act.dataset.key, dirty: act.dataset.dirty ? act.value : null, sel: safeSel(act) } : null;

  const s = st.state;
  const part = s.parts.find((p) => p.name === st.selected);
  if (!part) {
    lastRendered = null;
    rerendering = true;
    try { root.replaceChildren(h("p", { class: "empty" }, s.parts.length
      ? "Select a part in the robot tree to see its settings."
      : "Add a part from the library on the left.")); } finally { rerendering = false; }
    return;
  }
  const switched = lastRendered !== part.name;
  lastRendered = part.name;
  const meta = part.stock ? stockMeta(part.stock) : folderMeta(part.path);
  const fs = h("fieldset", { class: "insp", disabled: s.read_only });
  fs.append(headSection(part), placementSection(part), paramsSection(part, meta), idsSection(part, meta));
  rerendering = true;
  try { root.replaceChildren(fs); } finally { rerendering = false; }
  if (switched) root.scrollTop = 0;

  if (keep) {
    const el = root.querySelector(`[data-key="${CSS.escape(keep.key)}"]`);
    if (el) {
      if (keep.dirty !== null) { el.value = keep.dirty; el.dataset.dirty = "1"; }
      el.focus();
      if (keep.sel && typeof el.setSelectionRange === "function") { try { el.setSelectionRange(...keep.sel); } catch { /* no text selection */ } }
    }
  }
}

function safeSel(el) {
  try { return el.selectionStart === null ? null : [el.selectionStart, el.selectionEnd]; } catch { return null; }
}

function headSection(part) {
  const n = part.name;
  const name = markDirty(h("input", { id: "insp-name", value: n, autocomplete: "off", spellcheck: "false", dataset: { key: "name" } }));
  onCommit(name, async () => {
    const v = name.value.trim();
    if (v === n) { clean(name); return; }
    if (!v) { name.value = n; clean(name); return; }
    store.pendingSelect = v;
    clean(name);
    await edit("rename", { name: n, new_name: v });
  });
  const del = h("button", {
    type: "button", class: "btn btn-danger", onclick: async () => {
      const r = await edit("remove", { name: n });
      if (r.ok) {
        notice(`Deleted ${n}. Press Ctrl+Z (or Undo) to bring it back.`, "info");
      }
    },
  }, "Delete part");
  return h("section", { class: "insp-section insp-head" },
    field(`${n}/name`, "insp-name", "Name", name, { hint: "Letters, digits and underscores. Renaming also updates parts attached to it." }),
    h("div", { class: "source-row" },
      h("span", { class: "muted" }, part.stock ? "Stock part" : "Part folder"),
      h("code", { text: part.stock || part.path })),
    h("div", { class: "field field-part", dataset: { field: `${n}/part` } },
      h("div", { class: "field-msgs" }),
      del));
}

function placementSection(part) {
  const n = part.name;
  const groups = attachGroups(n);
  const isRoot = !part.attach;
  const sel = h("select", { id: "insp-attach", dataset: { key: "attach" } });
  fillAttachSelect(sel, groups, part.attach, { allowRoot: isRoot });
  const vecs = { pos: vecInputs(n, "pos", part.pos), rot: vecInputs(n, "rot", part.rot) };

  const commit = async () => {
    const attach = sel.value === "" ? null : sel.value;
    const vals = {};
    for (const k of ["pos", "rot"]) {
      if (badNumber(...vecs[k].inputs)) { localError(vecs[k].fieldEl, "Enter a number."); return; }
      vals[k] = vecs[k].inputs.map((i) => (i.value.trim() === "" ? 0 : Number(i.value)));
      if (vals[k].some((v) => !isFinite(v))) { localError(vecs[k].fieldEl, "Enter numbers (leave a box empty for 0)."); return; }
    }
    clean(...vecs.pos.inputs, ...vecs.rot.inputs);
    await edit("attach", { name: n, attach, pos: vals.pos, rot: vals.rot });
  };
  sel.addEventListener("change", commit);
  for (const k of ["pos", "rot"]) vecs[k].wire(commit);

  const mountsHere = mountsOf(part);
  return h("section", { class: "insp-section" },
    h("h3", {}, "Placement"),
    field(`${n}/attach`, "insp-attach", "Attach to", sel,
      { hint: isRoot ? "This is the robot's root part: start_pose places it." : "A mount of another part, or one of its bodies (at the body's origin)." }),
    vecs.pos.fieldEl, vecs.rot.fieldEl,
    cadLine(part),
    mountsHere.length ? h("p", { class: "hint" }, `Mounts on ${n}: ${mountsHere.join(", ")}`) : null);
}

// A part made from a CAD file: whether it sits where the CAD has it (the state's cad_pos, for what it attaches to),
// and "Reset to CAD" to put it back there.
function onChassis(part) {
  const target = store.state && store.state.parts.find((p) => p.name === String(part.attach || "").split(".")[0]);
  return !!target && target.stock === "chassis";
}

function cadLine(part) {
  const hangs = part.cad && part.cad.hangs_on;
  if (!part.cad_pos) {
    if (!part.cad) return null;
    return h("p", { class: "hint", id: "insp-cad" }, hangs
      ? `Made from a CAD file, where it hangs on ${hangs}: attach it there to place it where the CAD has it.`
      : "Made from a CAD file: attach it to the chassis, or to a body of another part from the same CAD file, to place it where the CAD has it.");
  }
  const near = (a, b) => (a || [0, 0, 0]).every((v, i) => Math.abs(v - b[i]) < 1e-6);
  const there = near(part.pos, part.cad_pos) && near(part.rot, [0, 0, 0]);
  // fixed elsewhere (the chassis) while the CAD hangs it on another part's body: in place, but it won't move with it
  const elsewhere = hangs && hangs !== part.attach;
  return h("div", {},
    h("div", { class: "insp-cad" },
      h("p", { class: "hint", id: "insp-cad" }, there ? "Placed where the CAD has it" : "Moved from where the CAD has it"),
      h("button", { type: "button", class: "btn btn-small", id: "insp-cad-reset", disabled: there || (store.state && store.state.read_only),
        title: "Put this part back where the CAD has it", onclick: () => edit("attach", { name: part.name, attach: part.attach, pos: part.cad_pos, rot: [0, 0, 0] }) },
      "Reset to CAD")),
    elsewhere ? h("p", { class: "hint", id: "insp-cad-hangs" }, `In the CAD this hangs on ${hangs}; it's fixed to ${onChassis(part) ? "the chassis" : part.attach} here.`) : null);
}

function vecInputs(n, key, value) {
  const axes = key === "pos" ? ["x", "y", "z"] : ["roll", "pitch", "yaw"];
  const u = key === "pos" ? "m" : "°";
  const inputs = [];
  const cells = axes.map((ax, i) => {
    const id = `insp-${key}-${i}`;
    const inp = numberInput(id, `${key}:${i}`, (value || [0, 0, 0])[i], { label: `${key === "pos" ? "Offset" : "Rotation"} ${ax} (${key === "pos" ? "meters" : "degrees"})` });
    inputs.push(inp);
    const nudge = (sign) => h("button", {
      type: "button", class: "nudge", "aria-label": `${sign > 0 ? "Increase" : "Decrease"} ${ax} by ${key === "pos" ? "10 mm" : "5°"}`,
      onclick: () => {
        if (badNumber(inp)) { localError(inp.closest(".field"), "Enter a number."); return; }
        const cur = inp.value.trim() === "" ? 0 : Number(inp.value);
        if (!isFinite(cur)) return;
        inp.value = fmt(Math.round((cur + sign * NUDGE[key]) * 1e6) / 1e6);
        inp.dispatchEvent(new Event("change"));
      },
    }, sign > 0 ? "+" : "−");
    return h("div", { class: "vec-cell" },
      h("span", { class: "vec-axis", "aria-hidden": "true" }, ax),
      h("div", { class: "nudge-group" }, nudge(-1), inp, nudge(+1)));
  });
  const title = key === "pos" ? "Offset (pos)" : "Rotation (rot)";
  const fieldEl = h("div", { class: "field", dataset: { field: `${n}/${key}` }, role: "group", "aria-label": title },
    h("span", { class: "field-label" }, title, h("span", { class: "unit" }, ` ${u}`)),
    h("div", { class: "vec" }, cells),
    h("p", { class: "hint" }, key === "pos" ? "From the mount, in the mount's frame. ± moves 10 mm." : "Roll, pitch, yaw about fixed x, y, z. ± turns 5°."),
    h("div", { class: "field-msgs" }));
  return { inputs, fieldEl, wire: (fn) => inputs.forEach((i) => onCommit(i, fn)) };
}

// -- parameters ---------------------------------------------------------------------------------
function paramsSection(part, meta) {
  const n = part.name;
  const sec = h("section", { class: "insp-section" }, h("h3", {}, "Parameters"));
  if (!part.stock) {
    sec.append(h("p", { class: "muted" }, "A part folder has no parameters here; edit its part.toml."));
    return sec;
  }
  if (!meta) { sec.append(h("p", { class: "muted" }, store.library ? `Unknown stock part ${part.stock}.` : "Loading…")); return sec; }
  const known = new Set(meta.params.map((p) => p.name));
  for (const p of meta.params) sec.append(paramField(n, p, part.params[p.name]));
  for (const [k, v] of Object.entries(part.params)) {
    if (!known.has(k)) sec.append(field(`${n}/param:${k}`, `insp-p-${k}`, k, h("code", { id: `insp-p-${k}`, text: JSON.stringify(v) }), { hint: "Not a parameter of this part." }));
  }
  return sec;
}

function hintFor(p) {
  const bits = [];
  const d = p.kind === "pair" ? p.default.map(fmt).join(" to ") : p.kind === "vec3" ? `[${p.default.map(fmt).join(", ")}]` : p.kind === "bool" ? (p.default ? "on" : "off") : fmt(p.default);
  bits.push(`default ${d}${p.unit && p.kind !== "bool" ? " " + unit(p.unit) : ""}`);
  if (p.positive) bits.push("more than 0");
  if (p.min !== null && p.min !== undefined) bits.push(`at least ${fmt(p.min)}`);
  if (p.max !== null && p.max !== undefined) bits.push(`at most ${fmt(p.max)}`);
  if (p.max_depends) bits.push("maximum depends on other parameters");
  return bits.join(" · ");
}

function paramField(n, p, value) {
  const id = `insp-p-${p.name}`;
  const fid = `${n}/param:${p.name}`;
  const cur = value === undefined ? p.default : value;
  const send = (v) => edit("params", { name: n, params: { [p.name]: v } });
  const isDefault = value === undefined;
  const reset = isDefault ? null : h("button", {
    type: "button", class: "btn btn-small btn-ghost reset", title: "Back to the default", "aria-label": `Reset ${p.name} to its default`,
    onclick: () => send(null),
  }, "Reset");
  let control;
  const withUnit = (el) => h("div", { class: "with-unit" }, el, p.unit ? h("span", { class: "unit" }, unit(p.unit)) : null, reset);

  if (p.kind === "bool") {
    const cb = h("input", { id, type: "checkbox", checked: !!cur, dataset: { key: `param:${p.name}` } });
    cb.addEventListener("change", () => send(cb.checked));
    control = h("div", { class: "with-unit" }, cb, h("span", { class: "muted" }, cur ? "on" : "off"), reset);
  } else if (p.kind === "str" && p.choices) {
    const sel = h("select", { id, dataset: { key: `param:${p.name}` } }, p.choices.map((c) => h("option", { value: c }, c)));
    if (!p.choices.includes(cur)) sel.append(h("option", { value: cur }, String(cur)));
    sel.value = cur;
    sel.addEventListener("change", () => send(sel.value));
    control = withUnit(sel);
  } else if (p.kind === "str") {
    const inp = markDirty(h("input", { id, value: cur ?? "", dataset: { key: `param:${p.name}` } }));
    onCommit(inp, () => { clean(inp); send(inp.value === "" ? null : inp.value); });
    control = withUnit(inp);
  } else if (p.kind === "text") {   // free text, empty allowed (a cylinder's push): the default when it matches it
    const inp = markDirty(h("input", { id, type: "text", value: cur ?? "", autocomplete: "off", spellcheck: "false",
      dataset: { key: `param:${p.name}` } }));
    onCommit(inp, () => { clean(inp); send(inp.value === p.default ? null : inp.value); });
    control = withUnit(inp);
  } else if (p.kind === "ratio") {
    const custom = typeof cur === "number";
    const sel = h("select", { id, dataset: { key: `param:${p.name}` } },
      (p.choices || []).map((c) => h("option", { value: c }, c)), h("option", { value: "__custom__" }, "Custom number…"));
    const num = numberInput(`${id}-n`, `param:${p.name}:n`, custom ? cur : "", { label: `${p.name} (custom ratio)` });
    sel.value = custom ? "__custom__" : String(cur);
    num.hidden = !custom;
    sel.addEventListener("change", () => {
      if (sel.value === "__custom__") { num.hidden = false; num.focus(); return; }
      num.hidden = true; send(sel.value);
    });
    onCommit(num, () => {
      if (badNumber(num)) return localError(num.closest(".field"), "Enter a number.");
      clean(num);
      if (num.value.trim() === "") return;
      const v = Number(num.value);
      if (!isFinite(v)) return localError(num.closest(".field"), "Enter a number.");
      send(v);
    });
    control = h("div", { class: "with-unit" }, sel, num, reset);
  } else if (p.kind === "pair") {
    const a = numberInput(id, `param:${p.name}:0`, cur[0], { label: `${p.name} low (${p.unit || "value"})` });
    const b = numberInput(`${id}-1`, `param:${p.name}:1`, cur[1], { label: `${p.name} high (${p.unit || "value"})` });
    const commit = () => {
      if (badNumber(a, b)) return localError(a.closest(".field"), "Enter a number.");
      const va = Number(a.value), vb = Number(b.value);
      if (a.value.trim() === "" || b.value.trim() === "" || !isFinite(va) || !isFinite(vb)) {
        return localError(a.closest(".field"), "Enter both numbers.");
      }
      clean(a, b);
      send([va, vb]);
    };
    onCommit(a, commit); onCommit(b, commit);
    control = h("div", { class: "with-unit" }, a, h("span", { class: "muted" }, "to"), b, p.unit ? h("span", { class: "unit" }, unit(p.unit)) : null, reset);
  } else if (p.kind === "vec3") {   // [x, y, z]: three numbers, sent together
    const boxes = ["x", "y", "z"].map((axis, k) => numberInput(k ? `${id}-${k}` : id, `param:${p.name}:${k}`, cur[k],
      { label: `${p.name} ${axis} (${p.unit || "value"})` }));
    const commit = () => {
      if (badNumber(...boxes)) return localError(boxes[0].closest(".field"), "Enter a number.");
      const v = boxes.map((b) => Number(b.value));
      if (boxes.some((b) => b.value.trim() === "") || !v.every(isFinite)) {
        return localError(boxes[0].closest(".field"), "Enter all three numbers.");
      }
      clean(...boxes);
      send(v);
    };
    for (const b of boxes) onCommit(b, commit);
    control = h("div", { class: "with-unit" }, ...boxes, p.unit ? h("span", { class: "unit" }, unit(p.unit)) : null, reset);
  } else {   // float, int
    const isInt = p.kind === "int";
    const inp = numberInput(id, `param:${p.name}`, cur);
    onCommit(inp, () => {
      if (badNumber(inp)) return localError(inp.closest(".field"), isInt ? "Enter a whole number." : "Enter a number.");
      clean(inp);
      if (inp.value.trim() === "") return send(null);   // empty box = the default
      const v = Number(inp.value);
      if (!isFinite(v) || (isInt && !Number.isInteger(v))) return localError(inp.closest(".field"), isInt ? "Enter a whole number." : "Enter a number.");
      send(v);
    });
    control = withUnit(inp);
  }
  return field(fid, id, p.name, control, { doc: p.doc, hint: hintFor(p) });
}

// -- device IDs ---------------------------------------------------------------------------------
function slotKind(slot, meta) {
  return meta && meta.slots ? meta.slots[slot] || null : null;   // null: not a slot, or no metadata yet
}

// "a motor", "an encoder" (as scrimmage/parts/ids.py's _a() writes it)
const withArticle = (word) => `${/^[aeiou]/i.test(word) ? "an" : "a"} ${word}`;

function motorCount(part, meta) {
  if (!part.stock || !meta) return null;
  const p = meta.params.find((x) => x.name === "count" || x.name === "motors_per_side");
  if (!p) return null;
  const v = part.params[p.name];
  return Number(v === undefined ? p.default : v);
}

function slotsOf(part, meta) {
  const names = meta && meta.slots ? Object.keys(meta.slots) : [];
  for (const k of Object.keys(part.ids || {})) if (!names.includes(k)) names.push(k);
  return names;
}

function idsSection(part, meta) {
  const n = part.name;
  const slots = slotsOf(part, meta);
  const sec = h("section", { class: "insp-section", dataset: { field: `${n}/ids` } },
    h("h3", {}, "Device IDs"),
    h("p", { class: "hint" }, "Short form: PWM:0, DIO:4, TalonFX:3, CANcoder:9, Pigeon2:13. Use a table for other devices and extra keys. Leave empty to leave the slot unbound."),
    h("div", { class: "field-msgs" }));
  if (!slots.length) sec.append(h("p", { class: "muted" }, meta || !store.library ? "This part has no device slots." : "Loading…"));
  const count = motorCount(part, meta);
  for (const slot of slots) sec.append(slotField(part, slot, slotKind(slot, meta), count, !!meta));
  return sec;
}

function shortProblem(v, kind) {
  if (v === "") return null;
  const m = /^([A-Za-z0-9]+):(\d+)$/.exec(v);
  if (!m) return "Expected Kind:number, e.g. TalonFX:3";
  if (!SHORT[m[1]]) return `Kind is one of ${Object.keys(SHORT).join(", ")} (other devices: use a table)`;
  if (kind && SHORT[m[1]][0] !== kind) return `${m[1]} is ${withArticle(SHORT[m[1]][0])}; this is ${withArticle(kind)} slot`;
  return null;
}

function toTable(v) {
  if (v && typeof v === "object" && !Array.isArray(v)) return { ...v };
  if (typeof v === "string") {
    const m = /^([A-Za-z0-9]+):(\d+)$/.exec(v);
    if (m && SHORT[m[1]]) return { [SHORT[m[1]][1]]: ["ctre", "rev"].includes(SHORT[m[1]][1]) ? v : Number(m[2]) };
  }
  return {};
}

function parseScalar(s) {
  const t = s.trim();
  if (t === "true") return true;
  if (t === "false") return false;
  if (t !== "" && isFinite(Number(t))) return Number(t);
  return t;
}

// The short ID of a motor table that holds only its device and the motor settings ({ ctre = "TalonFX:1",
// neutral = "brake" } -> "TalonFX:1"), else null.
const SETTINGS = ["neutral", "invert"];
function shortOf(v) {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const dev = Object.keys(v).filter((k) => !SETTINGS.includes(k));
  if (dev.length !== 1) return null;
  const [k] = dev, d = v[k];
  if ((k === "ctre" || k === "rev") && typeof d === "string" && /^[A-Za-z0-9]+:\d+$/.test(d) && SHORT[d.split(":")[0]]) return d;
  if (k === "pwm" && Number.isInteger(d)) return `PWM:${d}`;
  return null;
}

// The motor settings a short ID keeps when it is retyped: the new ID as a table with them, or the plain ID.
function withSettings(text, old) {
  const keep = old && typeof old === "object" && !Array.isArray(old) ? SETTINGS.filter((k) => old[k] !== undefined) : [];
  if (!keep.length) return text;
  const t = toTable(text);
  if (!Object.keys(t).length) return text;
  for (const k of keep) t[k] = old[k];
  return t;
}

// The device's own neutral mode when the config says nothing: a Talon FX coasts, the others brake.
function defaultNeutral(v) {
  const d = typeof v === "string" ? v : v && typeof v === "object" ? String(v.ctre || "") : "";
  return /^TalonFX:/.test(d) ? "coast" : "brake";
}

const MOTOR_WHY = "scrimmage can't read brake / coast or invert from your robot code, so set them here the way your code sets them.";

// Brake / coast and invert for each motor of a motor slot (one, or each of a per-motor list).
function motorControls(part, slot, value) {
  const n = part.name;
  const one = (path, v, label, suffix) => {
    const t = v && typeof v === "object" && !Array.isArray(v) ? v : {};
    const def = defaultNeutral(v);
    const sel = h("select", { id: `insp-neutral-${suffix}`, dataset: { key: `motor:${path}:neutral` }, "aria-label": label ? `${slot} ${label}: when stopped` : null },
      h("option", { value: "" }, `Default (${def})`),
      h("option", { value: "brake" }, "Brake (holds when stopped)"),
      h("option", { value: "coast" }, "Coast (rolls freely)"));
    sel.value = t.neutral === "brake" || t.neutral === "coast" ? t.neutral : "";
    const inv = h("input", { type: "checkbox", id: `insp-invert-${suffix}`, checked: t.invert === true || undefined, dataset: { key: `motor:${path}:invert` },
      "aria-label": label ? `${slot} ${label}: invert` : null });
    // a control replaced by a re-render (or not on the page any more) says nothing
    const send = (fields) => edit("motor", { slot: `${n}.${path}`, ...fields });
    sel.addEventListener("change", () => { if (!rerendering && sel.isConnected) send({ neutral: sel.value || null }); });
    inv.addEventListener("change", () => { if (!rerendering && inv.isConnected) send({ invert: inv.checked }); });
    return h("div", { class: "motor-set", dataset: { field: `motor/${n}.${path}` } },
      label ? h("span", { class: "muted motor-set-label" }, label) : null,
      h("label", { class: "motor-set-item", for: sel.id }, h("span", { class: "motor-set-name" }, "When stopped"), sel),
      h("label", { class: "motor-set-item motor-set-check", for: inv.id }, inv, h("span", {}, "Invert (turn the other way)")),
      h("div", { class: "field-msgs" }));
  };
  const rows = Array.isArray(value)
    ? value.map((v, i) => one(`${slot}[${i}]`, v, `motor ${i + 1}`, `${slot}-${i}`))
    : value === undefined || value === "" ? [] : [one(slot, value, null, slot)];
  if (!rows.length) return null;
  return h("div", { class: "motor-settings" }, ...rows, h("p", { class: "hint motor-why", text: MOTOR_WHY }));
}

function slotField(part, slot, kind, count, known) {
  const n = part.name;
  const fid = `${n}/ids:${slot}`;
  const value = (part.ids || {})[slot];
  const short = kind === "motor" ? shortOf(value) : null;
  const natural = Array.isArray(value) ? "list" : value && typeof value === "object" && short === null ? "table" : "short";
  const mkey = `${n}/${slot}`;
  if (slotModes.get(mkey) && slotModes.get(mkey) === natural) slotModes.delete(mkey);
  const mode = slotModes.get(mkey) || natural;
  const canList = kind === "motor";

  // the part's ids as they are when the edit runs (after any queued edits), not when this row was drawn
  const sendIds = (v) => edit("ids", () => {
    const now = store.state.parts.find((p) => p.name === n);
    const ids = { ...((now || part).ids || {}) };
    if (v === undefined) delete ids[slot]; else ids[slot] = v;
    return { name: n, ids };
  });
  const setMode = (m) => { slotModes.set(mkey, m); renderInspector(store); };
  const modes = h("div", { class: "seg", role: "group", "aria-label": `${slot} entry form` },
    [["short", "ID"], ["table", "Table"], ...(canList ? [["list", "Per motor"]] : [])].map(([m, label]) =>
      h("button", { type: "button", class: "seg-btn", "aria-pressed": String(mode === m), onclick: () => setMode(m) }, label)));

  let body;
  const id = `insp-id-${slot}`;
  if (mode === "short") {
    const text = typeof value === "string" ? value : short || "";
    const inp = markDirty(h("input", { id, value: text, placeholder: kind === "motor" ? "e.g. TalonFX:3, SparkMax:5 or PWM:0" : kind === "encoder" ? "e.g. CANcoder:9, SparkMaxAbs:5 or DIO:4" : "e.g. Pigeon2:13", autocomplete: "off", spellcheck: "false", dataset: { key: `ids:${slot}` } }));
    const live = h("p", { class: "hint live", "aria-live": "polite" });
    inp.addEventListener("input", () => {
      const prob = shortProblem(inp.value.trim(), kind);
      live.textContent = prob || "";
      live.classList.toggle("live-bad", !!prob);
    });
    onCommit(inp, () => {   // the server's own message replaces the live hint
      clean(inp); live.textContent = ""; live.classList.remove("live-bad");
      const v = inp.value.trim();
      sendIds(v === "" ? undefined : withSettings(v, (store.state.parts.find((p) => p.name === n) || part).ids?.[slot]));
    });
    body = h("div", {}, inp, live);
  } else if (mode === "table") {
    const rows = Object.entries(toTable(value));
    const list = h("div", { class: "kv" });
    const commit = () => {
      const t = {};
      const rowsNow = [...list.querySelectorAll(".kv-row")].map((r) => r.querySelectorAll("input"));
      const half = rowsNow.find(([k, v]) => k.value.trim() && !v.value.trim());
      if (half) return localError(list.closest(".field"), `Give a value for ${half[0].value.trim()}.`);
      for (const [k, v] of rowsNow) {
        if (k.value.trim()) t[k.value.trim()] = parseScalar(v.value);
        clean(k, v);
      }
      sendIds(Object.keys(t).length ? t : undefined);
    };
    const addRow = (k = "", v = "", i = list.children.length) => {
      const ki = markDirty(h("input", { value: k, placeholder: "key", "aria-label": `${slot} key ${i + 1}`, dataset: { key: `ids:${slot}:k${i}` } }));
      const vi = markDirty(h("input", { value: typeof v === "string" ? v : JSON.stringify(v), placeholder: "value", "aria-label": `${slot} value ${i + 1}`, dataset: { key: `ids:${slot}:v${i}` } }));
      const row = h("div", { class: "kv-row" }, ki, vi,
        h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": `Remove ${slot} key ${i + 1}`, onclick: () => { row.remove(); commit(); } }, "×"));
      list.append(row);
      return ki;
    };
    rows.forEach(([k, v], i) => addRow(k, v, i));
    if (!rows.length) addRow();
    body = h("div", {}, list,
      h("button", { type: "button", class: "btn btn-small", onclick: () => addRow().focus() }, "Add key"),
      h("p", { class: "hint" }, "Binding keys, e.g. can = VictorSPX[3], neutral = coast, counts_per_rev = 2048."));
    body.setAttribute("id", id);
    onGroupCommit(body, commit);
  } else {   // list: one ID per motor
    const cur = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
    const len = Math.max(count || 1, cur.length);
    const inputs = [];
    const commit = () => {
      const vals = inputs.map((i) => i.value.trim());
      inputs.forEach((i) => clean(i));
      if (vals.every((v) => v === "")) return sendIds(undefined);
      if (vals.some((v) => v === "")) {
        return localError(inputs[0].closest(".field"), "Give an ID for every motor, or leave them all empty.");
      }
      sendIds(vals.map((v) => (v.startsWith("{") ? safeJson(v) : v)));
    };
    const rows = [];
    for (let i = 0; i < len; i++) {
      const v = cur[i];
      const inp = markDirty(h("input", { id: i === 0 ? id : `${id}-${i}`, value: v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v), placeholder: `motor ${i + 1}, e.g. TalonFX:${3 + i}`, "aria-label": `${slot} motor ${i + 1}`, dataset: { key: `ids:${slot}:${i}` } }));
      inputs.push(inp);
      rows.push(h("div", { class: "list-row" }, h("span", { class: "muted" }, `${i + 1}`), inp));
    }
    body = h("div", {}, rows);
    onGroupCommit(body, commit);
    body.append(h("p", { class: "hint" }, count ? `One ID per motor: this slot has ${count} motor${count === 1 ? "" : "s"}.` : "One ID per motor."));
  }
  return h("div", { class: "field slot", dataset: { field: fid } },
    h("div", { class: "slot-head" },
      h("label", { for: id, class: "field-label" }, slot, h("span", { class: "kind" }, kind || (known ? "not a slot of this part" : store.library ? "unknown slot (part not in the library)" : "unknown slot (library not loaded)"))),
      modes),
    body,
    kind === "motor" ? motorControls(part, slot, value) : null,
    h("div", { class: "field-msgs" }));
}

function safeJson(s) {
  try { return JSON.parse(s); } catch { return s; }
}
