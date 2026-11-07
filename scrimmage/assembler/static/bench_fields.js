// The Bench tab's fields: a key's value in the Units setting, the field rows (with vectors: number boxes with - and +)
// and the Change it panel (bench.js is the tab's store and its edits).

import { $, h, fmt, UNIT, markUnits, typedText, keptSelection } from "./app.js";
import { units } from "./units.js";
import { t } from "./i18n.js";
import { FOCUS_SHOWN, bench, SINGLE, shownIdea, parseKey, entryOf, valueOf, refuse, setKey, setKeys, leftStage,
  drawnVersion, render } from "./bench.js";

// -- units: a key's number in the Units setting -------------------------------------------------------------------
// By the unit fields.toml gives a key: a length is "length" (in or mm), a flight's target "field" (ft or m). Angles,
// times, rps and the rest are shown as the file writes them.
const KINDS = { m: "length", "m/s": "speed", kg: "mass", N: "force", "N/m": "spring" };
const FIELD_KEYS = ["flight.target_distance", "flight.target_height"];
export function kindOf(key) {
  const { section, k, def } = parseKey(key);
  if (!def || def.choices) return null;
  return def.unit === "m" && FIELD_KEYS.includes(`${section}.${k}`) ? "field" : KINDS[def.unit] || null;
}
const isNumber = (x) => typeof x === "number" && Number.isFinite(x);
const each = (v, f) => (Array.isArray(v) ? v.map((x) => (isNumber(x) ? f(x) : x)) : isNumber(v) ? f(v) : v);
// a value as the file writes it, in SI (and back: to 1 µm, or a millionth of an inch)
export const siOf = (vt, v) => each(v, (x) => (vt.inches ? units.fromKey(x, "in") : x));
export const fileOf = (vt, si) => each(si, (x) => round6(vt.inches ? units.toKey(x, "in") : x));
// a number's text in a box (no unit), and a whole value's ("1, 2, 3"), in the Units setting
const shownNumber = (vt, x) => (vt.kind ? units.shown(siOf(vt, x), vt.kind) : fmt(x));
const shownValue = (vt, v) => (vt.kind && v != null && typeof v !== "string" ? (Array.isArray(v) ? v.map((x) => shownNumber(vt, x)).join(", ") : shownNumber(vt, v)) : show(v));
export const unitOf = (vt, def) => (vt.kind ? units.name(vt.kind) : vt.inches ? "in" : def && def.unit ? UNIT[def.unit] || def.unit : "");

// What a box of a key with a kind holds, as the file writes it: "" -> null (remove); a number, or a list "1, 2, 3",
// each typed in the Units setting ("4", "4 in", "100 mm"). A part still showing `orig`'s text keeps the file's own
// number (`file`), so a list with one number changed writes the others back exactly.
function parseMeasured(text, vt, orig, file) {
  const s = text.trim();
  if (s === "") return { value: null };
  const list = /^\[/.test(s) || s.includes(",");
  let parts = s.replace(/^\[|\]$/g, "").split(",").map((x) => x.trim()).filter(Boolean);
  const spaced = parts.length === 1 ? parts[0].split(/\s+/) : [];
  if (spaced.length > 1 && spaced.every((x) => NUM_RE.test(x))) parts = spaced;   // "1 2 3", as parseTyped takes it
  const was = String(orig ?? "").split(",").map((x) => x.trim());
  const exact = Array.isArray(file) ? file : [file];
  const out = [];
  for (const [j, x] of parts.entries()) {
    if (parts.length === was.length && x === was[j] && isNumber(exact[j])) { out.push(exact[j]); continue; }
    const v = units.parse(x, vt.kind);
    if (typeof v === "object") return { error: v.error };
    out.push(fileOf(vt, v));
  }
  return { value: out.length === 1 && !list ? out[0] : out };
}

export const meters = (field) => {
  if (!field || !field.endsWith("_in")) return field;
  const m = field.slice(0, -3);
  const { def } = parseKey(m);
  return def && def.length ? m : field;
};

function choiceList(kind) {
  const merged = bench.idea ? bench.idea.merged : {};
  if (kind === "shafts") return Object.keys(merged.shaft || {});
  if (kind === "outlets") return (merged.outlet || []).map((o) => o && o.name).filter(Boolean);
  return (bench.fields && bench.fields.choices[kind]) || [];
}

export function included(section, name) {
  const inc = (bench.idea && bench.idea.included) || {};
  return inc[name === null ? section : `${section}.${name}`] || null;
}

// What a text box holds: "" -> null (remove), a number, a list "a, b, c", true/false, else the text.
function parseTyped(text, numeric) {
  const s = text.trim();
  if (s === "") return { value: null };
  const num = (x) => (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(x) ? Number(x) : NaN);
  if (numeric) {
    const parts = s.replace(/^\[|\]$/g, "").split(/[\s,]+/).filter(Boolean);
    const nums = parts.map(num);
    if (nums.some((n) => Number.isNaN(n))) return { error: t("type a number (or numbers separated by commas)") };
    return { value: parts.length === 1 && !/^\[/.test(s) ? nums[0] : nums };
  }
  if (s === "true" || s === "false") return { value: s === "true" };
  return { value: s };
}

// Chromium fires `change` on a focused, edited box when a redraw removes it: that is not the user committing (the
// text is restored into the new box, waiting for Enter or a real change), so it is ignored.
let redrawing = 0;
export const live = (box) => box.isConnected && !redrawing;
export function redraw(fn) {
  redrawing++;
  try { return fn(); } finally { redrawing--; }
}

const show = (v) => (Array.isArray(v) ? v.map(fmt).join(", ") : typeof v === "boolean" ? String(v) : fmt(v));

// -- vectors (the shape editor): three number boxes, or one for an angle, each with - and + -----------------------
// a - or + step: an angle's 5 degrees; a length's 0.5 in or 10 mm, a field length's 0.5 ft or 0.1 m (in SI, by the
// Units setting, whatever unit the file writes the key in)
const STEP = { degrees: 5 };
const STEP_SI = { imperial: { length: 0.0127, field: 0.1524 }, metric: { length: 0.01, field: 0.1 } };
const stepOf = (vt, def) => (vt.kind ? (STEP_SI[units.system] || STEP_SI.metric)[vt.kind] || null : (def && STEP[def.unit]) || null);
const AXES = ["x", "y", "z"];
const round6 = (x) => Math.round(x * 1e6) / 1e6;
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const vec3 = (v) => Array.isArray(v) && v.length === 3 && v.every(isNum);
const NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

// Whether `key` is drawn as number boxes with - and +: a point or size ([x, y, z]), any three numbers, an angle, or a
// length (a radius, diameter, width, travel...: 0.5 in or 10 mm a step).
function isVector(key) {
  const { section, k, def } = parseKey(key);
  if (!def || def.choices) return false;
  const { value } = valueOf(key);
  const v = value !== undefined ? value : def.default;
  if (Array.isArray(v)) return vec3(v);
  if (v === undefined || v === null) return ["center", "a", "b"].includes(k) || (k === "size" && section !== "piece");
  return isNum(v) && (def.unit === "degrees" || def.unit === "m");
}

// What a vector field holds and how a new value is written: {value, base (what - and + start from), n, inches, kind,
// write(v) -> [[key, value], ...]}, each as the file writes it. A bar given by its two ends has no center: its
// center is their middle, and moving it moves both ends.
function vectorOf(key) {
  const { section, name, k, def } = parseKey(key);
  const { value, inches, kind } = valueOf(key);
  if (section === "bar" && k === "center" && value === undefined) {
    const ak = dotted(section, name, "a"), bk = dotted(section, name, "b");
    const a = valueOf(ak), b = valueOf(bk);
    if (vec3(a.value) && vec3(b.value) && a.inches === b.inches) {
      const mid = a.value.map((x, j) => round6((x + b.value[j]) / 2));
      return { value: mid, base: mid, n: 3, inches: a.inches, kind, ends: true, write: (v) => {
        const pairs = [[ak, a.value.map((x, j) => round6(x + v[j] - mid[j]))], [bk, b.value.map((x, j) => round6(x + v[j] - mid[j]))]];
        pairs.value = v;
        return pairs;
      } };
    }
  }
  const dv = def ? def.default : undefined;
  const n = isNum(value ?? dv) || (value == null && dv == null && def && def.unit === "degrees" && !["center", "a", "b", "size"].includes(k)) ? 1 : 3;
  return { value, base: value ?? dv ?? null, n, inches, kind, write: (v) => [[key, v]] };
}

function vectorBoxes(key, id, disabled) {
  const { k, def } = parseKey(key);
  const vt = vectorOf(key);
  const step = stepOf(vt, def);   // SI for a key with a kind
  const typed = bench.typed.has(key) ? bench.typed.get(key) : undefined;
  const cur = typed !== undefined ? typed : vt.value;
  const comp = (j) => (vt.n === 1 ? (Array.isArray(cur) ? cur[0] : cur) : Array.isArray(cur) ? cur[j] : undefined);
  const baseOf = (j) => (vt.base == null ? null : vt.n === 1 ? vt.base : vt.base[j]);
  const boxes = [];
  const row = h("div", { class: vt.n === 1 ? "vec vec-1" : "vec" });
  const commit = () => {
    if (boxes.some((b) => !live(b))) return;
    const texts = boxes.map((b) => b.value.trim());
    let v;
    if (texts.every((x) => x === "")) {
      if (vt.ends) return;
      v = null;
    } else {
      // a box still showing the file's number keeps it exactly; another is typed in the Units setting (with a kind)
      const read = (x, j) => {
        if (x === "") return baseOf(j);
        if (!vt.kind) return NUM_RE.test(x) ? Number(x) : NaN;
        if (x === boxes[j].dataset.orig && isNum(comp(j))) return comp(j);
        const v = units.parse(x, vt.kind);
        return typeof v === "object" ? v : fileOf(vt, v);
      };
      const vals = texts.map(read);
      const bad = vals.find((x) => x && typeof x === "object");
      if (bad || vals.some((x) => !isNum(x))) {
        const message = bad ? bad.error : vt.n === 1 ? t("type a number") : t("type a number in each box");
        refuse(bench.shown, [{ field: key, message }]);
        bench.typed.set(key, vt.n === 1 ? texts[0] : texts);
        bench.typedAt.set(key, Math.min(...boxes.map((b) => Number(b.dataset.typedAt || b.dataset.version))));
        render();
        return;
      }
      v = vt.n === 1 ? vals[0] : vals;
    }
    if (row.dataset.sent === JSON.stringify(v)) return;
    row.dataset.sent = JSON.stringify(v);
    const version = Math.min(...boxes.map((b) => Number(b.dataset.typedAt || b.dataset.version)));
    const pairs = v === null ? [[key, null]] : vt.write(v);
    for (const b of boxes) b.dataset.orig = b.value;   // sent: the redraw shows the saved value, as fieldRow's
    setKeys(pairs, version, boxes[0].dataset.file, key);
  };
  // - and +: the step is added to what the file holds when the edit's turn comes, so quick clicks all count
  const nudge = (j, sign) => setKeys(() => {
    const now2 = vectorOf(key);
    if (now2.base == null) return [];
    const add = (x) => (now2.kind ? fileOf(now2, siOf(now2, x) + sign * step) : round6(x + sign * step));
    const v = now2.n === 1 ? add(Array.isArray(now2.base) ? now2.base[0] : now2.base)
      : now2.base.map((x, m) => (m === j ? add(x) : x));
    return now2.write(v);
  }, null, bench.shown, key);
  const unit = unitOf(vt, def);
  const stepText = !step ? "" : vt.kind ? `${units.number(units.toDisplay(step, vt.kind), 4)} ${unit}` : `${fmt(step)} ${def.unit === "degrees" ? "°" : unit}`;
  for (let j = 0; j < vt.n; j++) {
    const c = comp(j);
    const text = c == null ? "" : typeof c === "string" ? c : shownNumber(vt, c);
    const box = h("input", { id: `${id}-${j}`, type: "text", class: "bench-vnum", inputmode: vt.kind ? null : "decimal", autocomplete: "off",
      spellcheck: "false", disabled, dataset: { key: `${key}#${j}` }, "aria-label": vt.n === 1 ? k : `${k} ${AXES[j]}`,
      placeholder: baseOf(j) != null && vt.value === undefined ? shownNumber(vt, baseOf(j)) : "" });
    box.value = text;
    box.dataset.orig = text;
    markUnits(box, vt.kind);
    box.dataset.file = bench.shown;
    box.dataset.version = String(drawnVersion());
    if (bench.typedAt.has(key)) box.dataset.typedAt = String(bench.typedAt.get(key));
    box.addEventListener("input", () => { if (!box.dataset.typedAt) box.dataset.typedAt = box.dataset.version; });
    box.addEventListener("change", () => { if (live(box)) commit(); });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    boxes.push(box);
    const can = !!step && !disabled && vt.base != null;
    const btn = (sign) => h("button", { type: "button", class: "nudge", dataset: { step: sign > 0 ? "+" : "-", axis: String(j) },
      disabled: !can, title: `${sign > 0 ? "+" : "−"}${stepText}`,
      "aria-label": sign > 0 ? t("{what} plus {step}", { what: vt.n === 1 ? k : `${k} ${AXES[j]}`, step: stepText })
        : t("{what} minus {step}", { what: vt.n === 1 ? k : `${k} ${AXES[j]}`, step: stepText }),
      onclick: () => nudge(j, sign) }, sign > 0 ? "+" : "−");
    row.append(h("div", { class: "vec-cell" },
      vt.n === 1 ? null : h("span", { class: "vec-axis", text: AXES[j] }),
      h("div", { class: "nudge-group" }, step ? btn(-1) : null, box, step ? btn(1) : null)));
  }
  return { control: row, unitText: unit, note: vt.ends ? t("the middle of the bar: moving it moves both ends") : null, firstId: `${id}-0` };
}

// One field: label, help, the box (a list for choices, a number box with its unit, a text box; with `vectors`,
// number boxes with - and + for points, sizes and angles), its messages. `prefix` keeps ids unique per panel.
export function fieldRow(key, { focus = false, prefix = null, vectors = false } = {}) {
  const { section, name, k, def } = parseKey(key);
  const i = shownIdea();
  const vt = valueOf(key);
  const { value } = vt;
  const inc = included(section, name);
  const disabled = !!(i && i.read_only_reason) || !!inc;
  const id = `bench-${prefix || (focus ? "f" : "a")}-${key.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  let unitText = unitOf(vt, def);
  const typed = bench.typed.has(key) ? bench.typed.get(key) : undefined;
  let box, vec = null;
  const commit = (v) => {
    if (box.dataset.sent === JSON.stringify(v)) return;
    box.dataset.sent = JSON.stringify(v);
    box.dataset.orig = box.value;   // sent: the redraw shows the saved value ("12 cm" reads back "120"), not this text
    setKey(key, v, Number(box.dataset.typedAt || box.dataset.version), box.dataset.file);
  };
  const stamp = () => {   // which idea and version this box shows, and the version typing began at
    box.dataset.file = bench.shown;
    box.dataset.version = String(drawnVersion());
    if (bench.typedAt.has(key)) box.dataset.typedAt = String(bench.typedAt.get(key));
    box.addEventListener("input", () => { if (!box.dataset.typedAt) box.dataset.typedAt = box.dataset.version; });
  };
  if (vectors && isVector(key)) {
    vec = vectorBoxes(key, id, disabled);
    unitText = vec.unitText;
  } else if (def && def.choices) {
    const opts = choiceList(def.choices);
    const cur = typed !== undefined ? typed : value;
    box = h("select", { id, dataset: { key }, disabled },
      h("option", { value: "" }, def.default != null ? t("(not set: {value})", { value: def.default }) : t("(not set)")),
      ...opts.map((o) => h("option", { value: o }, o)),
      cur != null && !opts.includes(cur) ? h("option", { value: cur }, cur) : null);
    box.value = cur == null ? "" : String(cur);
    box.addEventListener("change", () => { if (live(box)) commit(box.value === "" ? null : box.value); });
  } else if (typeof (value ?? def?.default) === "boolean") {
    box = h("select", { id, dataset: { key }, disabled },
      h("option", { value: "" }, t("(not set)")), h("option", { value: "true" }, t("yes")), h("option", { value: "false" }, t("no")));
    const cur = typed !== undefined ? typed : value;
    box.value = cur == null ? "" : String(cur);
    box.addEventListener("change", () => { if (live(box)) commit(box.value === "" ? null : box.value === "true"); });
  } else {
    const numeric = !!(def && def.unit) || typeof value === "number" || (Array.isArray(value) && value.every((x) => typeof x === "number"));
    const text = typed !== undefined ? (typeof typed === "string" ? typed : shownValue(vt, typed)) : value == null ? "" : shownValue(vt, value);
    const dflt = def && def.default != null ? shownValue({ kind: vt.kind, inches: false }, def.default) : "";   // in meters
    box = h("input", { id, type: "text", dataset: { key }, disabled, autocomplete: "off", spellcheck: "false",
      inputmode: numeric && !Array.isArray(value) && !vt.kind ? "decimal" : null,
      placeholder: dflt ? t("{value} if left out", { value: dflt }) : "", class: numeric ? "bench-num" : "bench-text" });
    box.value = text;
    box.dataset.orig = text;
    markUnits(box, vt.kind);
    const go = () => {
      // a key with a kind is typed in the Units setting and written as the file writes it (U3)
      const r = vt.kind ? parseMeasured(box.value, vt, typed === undefined ? text : null, value) : parseTyped(box.value, numeric);
      if (r.error) {
        refuse(bench.shown, [{ field: key, message: r.error }]);
        bench.typed.set(key, box.value);
        bench.typedAt.set(key, Number(box.dataset.typedAt || box.dataset.version));
        renderChange();
        return;
      }
      commit(r.value);
    };
    box.addEventListener("change", () => { if (live(box)) go(); });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  }
  if (!vec) stamp();
  const label = name === null ? `${section}: ${k}` : `${section} ${name}: ${k}`;
  const msgs = [];
  for (const pr of (i ? i.problems : [])) if (pr.key === key) msgs.push(h("p", { class: `field-msg ${pr.level === "error" ? "error" : "warning"}`, text: pr.message }));
  if (bench.errors.file === bench.shown) {
    for (const e of bench.errors.list) if (e.field === key) msgs.push(h("p", { class: "field-msg error", role: "alert", text: e.message }));
  }
  return h("div", { class: `bench-field${msgs.length ? " has-msg" : ""}${vec ? " bench-vec" : ""}`, dataset: { field: key } },
    h("label", { for: vec ? vec.firstId : id, class: "bench-label" }, label, vec && unitText ? h("span", { class: "unit", text: ` (${unitText})` }) : null),
    vec ? vec.control : h("div", { class: "with-unit" }, box, unitText ? h("span", { class: "unit", text: unitText }) : null),
    vec && vec.note ? h("p", { class: "hint", text: vec.note }) : def ? h("p", { class: "hint", text: def.help }) : null,
    inc ? h("p", { class: "hint", text: t("comes from {file}; change it there", { file: inc }) }) : null,
    ...msgs);
}

// Every entry of the idea, each with every key its section has (bench.name editable; other names via rename).
export function entries() {
  const merged = (bench.idea && bench.idea.merged) || {};
  const out = [];
  const sections = bench.fields ? [...new Set(Object.keys(bench.fields.fields).map((d) => d.split(".")[0]))] : [];
  for (const section of sections) {
    if (SINGLE.includes(section)) {
      if (section === "bench" || entryOf(merged, section, null)) out.push({ section, name: null });
    } else if (section === "shaft") {
      for (const n of Object.keys(merged.shaft || {})) out.push({ section, name: n });
    } else {
      for (const e of Array.isArray(merged[section]) ? merged[section] : []) if (e && typeof e.name === "string") out.push({ section, name: e.name });
    }
  }
  return out;
}
export const keysOf = (section) => Object.keys(bench.fields.fields).filter((d) => d.startsWith(`${section}.`)).map((d) => d.slice(section.length + 1));
export const dotted = (section, name, k) => (name === null ? `${section}.${k}` : `${section}.${name}.${k}`);

export function renderChange() {
  redraw(drawChange);
}

// What is being typed in a field panel (`root`, a selector), and the caret, so a redraw can put it back. After a
// Units change, a length, weight or speed half typed is converted to the new units (typedText).
export function keepTyping(root) {
  const act = document.activeElement;
  if (!(act && act.closest && act.closest(root) && act.dataset.key)) return null;
  return { id: act.id, key: act.dataset.key, file: act.dataset.file, typedAt: act.dataset.typedAt, value: typedText(act),
    dirty: act.tagName === "INPUT" && act.value !== act.dataset.orig,
    sel: { text: act.value, start: act.selectionStart, end: act.selectionEnd, dir: act.selectionDirection } };
}

export function restoreTyping(keep, root) {
  if (!keep) return;
  if (keep.file !== bench.shown) {
    // the stage moved to another idea while something was half typed: never write it into the other idea
    if (keep.dirty) leftStage(keep.file, keep.key.split("#")[0]);
    return;
  }
  const el = document.getElementById(keep.id);
  if (!el || !el.closest(root) || el.dataset.key !== keep.key) return;
  if (keep.dirty && el.tagName === "INPUT") {
    // the text typed so far stays, still tied to the version typing began at: if the idea changed since,
    // committing it is refused as stale and says so
    el.value = keep.value;
    el.dataset.typedAt = keep.typedAt || el.dataset.version;
  }
  el.focus();
  // where it was while the text is the same; else all of the new text if all was selected, or the caret at its end
  try { if (el.tagName === "INPUT" && keep.sel.start !== null) el.setSelectionRange(...keptSelection(keep.sel, el.value)); } catch { /* not a text box */ }
}

function drawChange() {
  const i = shownIdea();
  if (!bench.fields || !bench.state) return;
  const keep = keepTyping("#bench-change");   // what is being typed (and the caret) stays across a redraw

  const focus = $("#bench-focus");
  if (!i || !bench.idea) {
    focus.replaceChildren(h("p", { class: "hint", text: i ? t("Loading…") : "" }));
    $("#bench-all").replaceChildren();
    $("#bench-problems").replaceChildren();
    return;
  }
  const keys = [...new Set(i.focus)].slice(0, FOCUS_SHOWN);
  focus.replaceChildren(...(keys.length ? keys.map((k) => fieldRow(k, { focus: true }))
    : [h("p", { class: "hint bench-empty", text: t("Nothing talked about yet. Change a number in Show all, or ask the agent to pin some here.") })]));

  const all = $("#bench-all");
  all.hidden = !bench.showAll;
  $("#bench-showall").textContent = bench.showAll ? t("Hide all") : t("Show all");
  $("#bench-showall").setAttribute("aria-expanded", String(bench.showAll));
  if (bench.showAll) {
    all.replaceChildren(...entries().map(({ section, name }) => h("fieldset", { class: "bench-group", dataset: { entry: name === null ? section : `${section}.${name}` } },
      h("legend", { text: name === null ? section : `${section} ${name}` }),
      ...keysOf(section).filter((k) => k !== "name" || section === "bench").map((k) => fieldRow(dotted(section, name, k))))));
  } else all.replaceChildren();

  const probs = $("#bench-problems");
  const list = [...i.problems];
  if (bench.errors.file === bench.shown) for (const e of bench.errors.list) list.push({ level: "error", key: e.field, message: t("not saved: {why}", { why: e.message }) });
  probs.replaceChildren(...(list.length ? list.map((p) => h("li", { class: `message message-${p.level === "error" ? "error" : "warning"}` },
    h("span", { class: "message-kind", text: p.level === "error" ? t("Error") : t("Warning") }),
    p.key ? h("span", { class: "mono bench-problem-key", text: p.key }) : null,
    h("span", { text: p.message }))) : [h("li", { class: "hint", text: t("No problems.") })]));

  restoreTyping(keep, "#bench-change");
}
