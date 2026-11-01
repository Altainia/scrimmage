// The Part tab: one CAD import (or one plugin-made part folder) turned into part folders. See /api/part/* in the
// docstring of scrimmage/assembler/server.py for the API contract.
//
//   Left       the tree: the bodies (pieces that move together), each with its pieces, the hardware the import
//              guessed folded under "+N hardware"; a piece dragged onto a body moves into it. Above it "New body
//              from selection" and "Leave out" act on the selection. Below it the checklist ("Still needed"): a click
//              selects what the item is about.
//   Middle     the 3D view (part3d.js, loaded on its own so a WebGL failure leaves the rest working).
//   Right      the inspector: the sections in `sections` whose `when(ctx)` is true, in order.
//   Status     saved or not, "Parts up to date" / "Updating parts…" from build.state, the part's mass, Import again.
//
// Every edit is saved at once (no Save button); the part folders rebuild in the background. A CAD file dropped
// anywhere on the tab replaces the source. In plugin mode (state.mode "plugin") there are no pieces: only the
// body-level controls show.
//
// For the next steps (joints, slots, mounts, the robot frame): `addSection({id, when, render}, {before})` adds an
// inspector section (render(ctx) returns a node or a list; ctx is {state, sel, pieces, bodies, body, plugin, ro});
// `editOp(op, args)` makes one edit (it resolves {ok, errors}); `select(kind, keys, {add})` changes the selection;
// `onPart(fn)` calls fn after every render and `onView(fn)` with every new /api/part/view; `field()` draws a
// labelled control with its error messages. Test hook: window.__scrimmagePart {store, screenPos(pieceKey), scene}.

import { $, h, api, notice, serial, registerTab, setTab, showPath } from "./app.js";
import { uploadCad } from "./start.js";

export const part = {
  state: null,        // /api/part/state
  materials: null,    // /api/part/materials
  view: null,         // /api/part/view
  sel: { kind: null, keys: new Set() },   // kind: "piece" | "body" | "frame" | null
  errors: [],         // the last refused edit's [{field, message}]
  showLeftOut: false,
  openHardware: new Set(),   // bodies whose hardware rows are unfolded
  appPath: "",        // /api/app/state's display path of the import file (or part folder)
};

const listeners = [], viewListeners = [];
export function onPart(fn) { listeners.push(fn); }
export function onView(fn) { viewListeners.push(fn); if (part.view) fn(part.view); }

let busy = 0, working = "";   // working: a re-import or upload in progress ("Reading the CAD file…")
let reveal = false;           // after the next render, scroll the selected tree row into view
let pendingBody = null;       // select this body once a state that has it arrives (a new or renamed body)
let lastMessages = "[]";
let viewKey = null;

const plugin = () => !!part.state && part.state.mode === "plugin";
const readOnly = () => !!part.state && !!part.state.read_only_reason;
const pieceOf = (key) => part.state.pieces.find((p) => p.key === key) || null;
const bodyOf = (name) => part.state.bodies.find((b) => b.name === name) || null;
const tail = (key) => String(key).split("/").pop();

export function kg(x) {
  if (typeof x !== "number" || !isFinite(x)) return "—";
  return `${x >= 10 ? x.toFixed(2) : x === 0 ? "0" : String(+x.toPrecision(3))} kg`;
}
const cog = (c) => (c ? `x ${c[0].toFixed(3)}, y ${c[1].toFixed(3)}, z ${c[2].toFixed(3)} m` : "none (no weight)");

// -- edits ------------------------------------------------------------------------------------------
async function mutate(path, body) {
  if (!part.state) return { ok: false, errors: [] };
  if (readOnly()) {
    notice(`This part is read-only: ${part.state.read_only_reason}`, "warn");
    return { ok: false, errors: [] };
  }
  busy++; renderStatus();
  try {
    const { status, data } = await api("POST", path, { version: part.state.version, ...body });
    if (status === 200) {
      part.errors = [];
      if (data.ok === false) notice(`Nothing to ${path.endsWith("undo") ? "undo" : "redo"}.`, "info");
      applyState(data, true);
      return { ok: data.ok !== false };
    }
    const errors = data.errors && data.errors.length ? data.errors
      : [{ field: "status", message: data.error || `the server answered ${status}` }];
    if (status === 400) {
      part.errors = errors;
      pendingBody = null;
      render();
      return { ok: false, errors };
    }
    // stale (another tab, an undo, an outside edit) or read-only: say why and reload
    notice(errors.map((e) => e.message).join("; "), "warn");
    part.errors = [];
    await refresh();
    return { ok: false, errors, conflict: true };
  } finally {
    busy--; renderStatus();
  }
}

// One edit, after the ones queued before it. `args` may be a function, called when the edit runs.
export function editOp(op, args) {
  return serial(() => mutate("/api/part/edit", { op, args: typeof args === "function" ? args() : args }));
}
const undo = () => serial(() => mutate("/api/part/undo", {}));
const redo = () => serial(() => mutate("/api/part/redo", {}));

async function reimport(file = null) {
  if (!part.state) return;
  working = file ? `Reading ${file.name}…` : "Reading the CAD file again…";
  renderStatus();
  try {
    const { status, data } = file ? await uploadCad(file)
      : await api("POST", "/api/part/reimport", { version: part.state.version });
    if (status === 200 && data.version !== undefined) {
      part.errors = [];
      viewKey = null;   // the same path may hold new shapes
      applyState(data, true);
      notice(file ? `Using ${file.name} now.` : "Read the CAD file again.", "info");
    } else {
      const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
      notice(file ? `Couldn't use ${file.name}: ${why}` : `Couldn't read the CAD file again: ${why}`, "error");
      await refresh();
    }
  } finally {
    working = "";
    renderStatus();
  }
}

// -- state --------------------------------------------------------------------------------------------
async function refresh() {
  const { status, data } = await api("GET", "/api/part/state");
  if (status === 200) applyState(data, true);
}

function applyState(s, force = false) {
  const prev = part.state;
  if (prev && !force && s.version <= prev.version) return;
  if (prev && s.version < prev.version) return;   // an older answer overtaken by a newer one
  part.state = s;
  // the selection keeps what still exists
  const have = part.sel.kind === "piece" ? new Set(s.pieces.map((p) => p.key))
    : part.sel.kind === "body" ? new Set(s.bodies.map((b) => b.name)) : null;
  if (have) for (const k of [...part.sel.keys]) if (!have.has(k)) part.sel.keys.delete(k);
  if (pendingBody && s.bodies.some((b) => b.name === pendingBody)) {
    part.sel = { kind: "body", keys: new Set([pendingBody]) };
    pendingBody = null;
  }
  if (part.sel.kind && part.sel.kind !== "frame" && !part.sel.keys.size) part.sel.kind = null;
  const msgs = JSON.stringify(s.messages || []);
  if (msgs !== lastMessages) {
    lastMessages = msgs;
    for (const m of s.messages || []) notice(m, "info");
  }
  render();
  const key = JSON.stringify(s.mode === "plugin" ? [s.path, s.bodies.map((b) => b.name)]
    : [s.path, s.source, s.units, s.frame, s.pieces.map((p) => [p.key, p.volume])]);
  if (key !== viewKey) { viewKey = key; loadView(); }
}

let viewSeq = 0;
async function loadView() {
  const seq = ++viewSeq;
  const { status, data } = await api("GET", "/api/part/view");
  if (seq !== viewSeq) return;
  part.view = status === 200 ? data : { pieces: [] };
  for (const fn of viewListeners) fn(part.view);
}

async function pollLoop() {
  for (;;) {
    const since = part.state ? `?since=${part.state.version}` : "";
    const { status, data } = await api("GET", "/api/part/state" + since);
    if (status === 200) applyState(data);
    else await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

// -- selection ----------------------------------------------------------------------------------------
// select("piece", keys) / select("body", names) / select("frame") / select(null); add: toggle into the selection.
export function select(kind, keys = [], { add = false, show = true } = {}) {
  if (!part.state) return;
  const sel = part.sel;
  if (add && kind && kind === sel.kind) {
    for (const k of keys) { if (sel.keys.has(k)) sel.keys.delete(k); else sel.keys.add(k); }
    if (!sel.keys.size && kind !== "frame") sel.kind = null;
  } else {
    part.sel = { kind: kind || null, keys: new Set(kind ? keys : []) };
  }
  if (part.sel.kind === "piece") {   // a hardware piece picked in 3D or from the checklist shows in the tree
    for (const k of part.sel.keys) { const p = pieceOf(k); if (p && p.hardware) part.openHardware.add(p.body); }
  }
  part.errors = [];
  reveal = show;
  render();
}

// -- rendering ----------------------------------------------------------------------------------------
let redrawing = 0;
export const live = (el) => el.isConnected && !redrawing;

export function render() {
  if (!part.state) return;
  redrawing++;
  try {
    renderTop();
    const ro = $("#part-readonly");
    ro.hidden = !readOnly();
    ro.textContent = readOnly() ? `Read-only: ${part.state.read_only_reason}` : "";
    $("#tab-part").classList.toggle("read-only", readOnly());
    $("#tab-part").classList.toggle("part-plugin", plugin());
    renderTools();
    renderTree();
    renderChecklist();
    renderInspector();
    renderStatus();
  } finally {
    redrawing--;
  }
  for (const fn of listeners) fn(part);
}

function renderTop() {
  const s = part.state;
  if (!s || $("#tab-part").hidden || document.body.classList.contains("on-start")) return;
  showPath(part.appPath || s.path, s.path);
  $("#undo").disabled = !s.can_undo || readOnly();
  $("#redo").disabled = !s.can_redo || readOnly();
}

function selectedPieces() {
  return part.sel.kind === "piece" ? [...part.sel.keys].map(pieceOf).filter(Boolean) : [];
}

function renderTools() {
  const ps = selectedPieces();
  const off = plugin() || readOnly() || !ps.length;
  $("#part-new-body").disabled = off;
  const back = ps.length && ps.every((p) => p.left_out);
  $("#part-leave-out").textContent = back ? "Put back" : "Leave out";
  $("#part-leave-out").title = back ? "Put the selected pieces back into the part" : "Leave the selected pieces out of the part (they weigh nothing and aren't drawn)";
  $("#part-leave-out").disabled = off;
  $("#part-reimport").disabled = !!working || (readOnly() && !part.state.source);
}

// The tree: [role=tree] with bodies (li[role=treeitem] > .part-row[data-body] + ul[role=group]) and pieces
// (li[role=treeitem][data-piece]). Arrow keys move, Space or Enter selects (with Shift or Ctrl: adds).
function renderTree() {
  const s = part.state, box = $("#part-tree");
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  const tree = h("ul", { class: "part-tree", role: "tree", "aria-label": "Bodies and their pieces", "aria-multiselectable": "true" });
  const sel = part.sel;
  const drag = !plugin() && !readOnly();
  for (const b of s.bodies) {
    const mine = s.pieces.filter((p) => p.body === b.name);
    const group = h("ul", { role: "group", class: "part-group" });
    const plain = mine.filter((p) => !p.hardware), hw = mine.filter((p) => p.hardware);
    for (const p of plain) group.append(pieceItem(p, drag));
    if (hw.length) {
      const open = part.openHardware.has(b.name);
      group.append(h("li", { role: "none", class: "part-hw" },
        h("button", { type: "button", class: "linklike part-hw-btn", "aria-expanded": String(open), dataset: { hardware: b.name },
          title: "Bolts, nuts and other hardware the import folded into this body",
          onclick: () => { if (open) part.openHardware.delete(b.name); else part.openHardware.add(b.name); render(); } },
        `${open ? "−" : "+"}${hw.length} hardware`)));
      if (open) for (const p of hw) group.append(pieceItem(p, drag));
    }
    const chosen = sel.kind === "body" && sel.keys.has(b.name);
    const row = h("div", { class: "part-row part-body-row", dataset: { body: b.name } },
      h("span", { class: "part-swatch", dataset: { swatch: b.name }, "aria-hidden": "true" }),
      h("span", { class: "tree-name", text: b.name }),
      h("span", { class: "tree-src", text: ` · ${bodyWords(b, mine.length)}` }));
    if (drag) dropTarget(row, b.name);
    tree.append(h("li", { role: "treeitem", class: `part-item part-body${chosen ? " selected" : ""}`, tabindex: "-1",
      "aria-selected": String(chosen), "aria-expanded": "true", dataset: { key: `b:${b.name}`, name: b.name },
      onclick: (e) => { if (e.target.closest(".part-row") === row) select("body", [b.name], { add: e.shiftKey || e.ctrlKey || e.metaKey, show: false }); } },
    row, mine.length || plugin() ? group : h("p", { class: "hint part-empty" }, "No pieces: drag some here.")));
  }
  if (!s.bodies.length) tree.append(h("li", { role: "none", class: "empty" }, s.read_only_reason ? "Nothing to show." : "No bodies yet."));
  tree.addEventListener("keydown", treeKeys);
  box.replaceChildren(tree);
  // roving focus: the focused item (or the first selected one, or the first) is the one Tab reaches
  const items = [...tree.querySelectorAll('[role="treeitem"]')];
  const current = (focused && items.find((i) => i.dataset.key === focused))
    || items.find((i) => i.getAttribute("aria-selected") === "true") || items[0];
  if (current) current.tabIndex = 0;
  if (focused && current && current.dataset.key === focused) current.focus({ preventScroll: true });
  if (reveal) {
    reveal = false;
    const first = items.find((i) => i.getAttribute("aria-selected") === "true");
    if (first) first.scrollIntoView({ block: "nearest" });
  }
  for (const sw of tree.querySelectorAll("[data-swatch]")) sw.style.background = bodyColor(sw.dataset.swatch);
}

function bodyWords(b, n) {
  const pieces = plugin() ? "" : `${n} piece${n === 1 ? "" : "s"} · `;
  if (b.parent == null) return `${pieces}its own part${b.part && b.part !== b.name ? ` (${b.part})` : ""}`;
  const kind = b.joint && b.joint.kind;
  const how = kind === "hinge" || kind === "revolute" ? "turns on" : kind === "slide" || kind === "prismatic" ? "slides on" : "fixed to";
  return `${pieces}${how} ${b.parent}`;
}

function pieceItem(p, drag) {
  const chosen = part.sel.kind === "piece" && part.sel.keys.has(p.key);
  const li = h("li", { role: "treeitem", class: `part-item part-piece${chosen ? " selected" : ""}${p.left_out ? " left-out" : ""}`,
    tabindex: "-1", "aria-selected": String(chosen), draggable: drag ? "true" : null, title: p.key,
    dataset: { piece: p.key, key: `p:${p.key}` },
    onclick: (e) => select("piece", [p.key], { add: e.shiftKey || e.ctrlKey || e.metaKey, show: false }) },
  h("span", { class: "part-row" },
    h("span", { class: "part-piece-name", text: tail(p.key) }),
    p.left_out ? h("span", { class: "part-tag", text: "left out" }) : null,
    p.material ? h("span", { class: "part-mat", text: matLabel(p.material, true) }) : null));
  if (drag) {
    li.addEventListener("dragstart", (e) => {
      const keys = part.sel.kind === "piece" && part.sel.keys.has(p.key) ? [...part.sel.keys] : [p.key];
      e.dataTransfer.setData("application/x-scrimmage-pieces", JSON.stringify(keys));
      e.dataTransfer.setData("text/plain", keys.join("\n"));
      e.dataTransfer.effectAllowed = "move";
    });
    dropTarget(li, p.body);
  }
  return li;
}

const isPieceDrag = (e) => [...(e.dataTransfer && e.dataTransfer.types || [])].includes("application/x-scrimmage-pieces");

function dropTarget(el, body) {
  el.addEventListener("dragover", (e) => {
    if (!isPieceDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add("drop-here");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop-here"));
  el.addEventListener("drop", (e) => {
    if (!isPieceDrag(e)) return;
    e.preventDefault();
    e.stopPropagation();
    el.classList.remove("drop-here");
    let keys;
    try { keys = JSON.parse(e.dataTransfer.getData("application/x-scrimmage-pieces")); } catch { return; }
    const moving = keys.filter((k) => { const p = pieceOf(k); return p && p.body !== body; });
    if (moving.length) editOp("move_pieces", { pieces: moving, body });
  });
}

function treeKeys(e) {
  const items = [...e.currentTarget.querySelectorAll('[role="treeitem"]')];
  const at = items.indexOf(document.activeElement);
  let next = null;
  if (e.key === "ArrowDown") next = items[Math.min(items.length - 1, at + 1)];
  else if (e.key === "ArrowUp") next = items[Math.max(0, at - 1)];
  else if (e.key === "Home") next = items[0];
  else if (e.key === "End") next = items[items.length - 1];
  else if ((e.key === " " || e.key === "Enter") && at >= 0) {
    e.preventDefault();
    const it = items[at], add = e.shiftKey || e.ctrlKey || e.metaKey;
    if (it.dataset.piece) select("piece", [it.dataset.piece], { add, show: false });
    else select("body", [it.dataset.name], { add, show: false });
    return;
  } else return;
  e.preventDefault();
  if (!next) return;
  for (const i of items) i.tabIndex = -1;
  next.tabIndex = 0;
  next.focus();
}

// Body colors: the same in the tree's swatches and the 3D view. Never blue (the selection is blue).
const PALETTE = ["#9aa6b2", "#d9a441", "#5fa37a", "#c9736b", "#8f7fc4", "#4fa6a6", "#c48fb0", "#a3a35a", "#b08a6a", "#7fb0d0"];
export function bodyColor(name) {
  const i = part.state ? part.state.bodies.findIndex((b) => b.name === name) : -1;
  return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
}

function renderChecklist() {
  const s = part.state, box = $("#part-checklist");
  const MARK = { todo: ["☐", "To do"], check: ["!", "Check"], error: ["✕", "Problem"] };
  box.replaceChildren(...s.checklist.map((it) => {
    const [mark, word] = MARK[it.level] || MARK.todo;
    const t = it.target || { kind: null, keys: [] };
    return h("li", { class: `part-check part-check-${it.level}` },
      h("button", { type: "button", class: "part-check-btn", title: "Show what this is about",
        onclick: () => select(t.kind, t.keys || []) },
      h("span", { class: "part-check-mark", "aria-hidden": "true", text: mark }),
      h("span", { class: "visually-hidden", text: `${word}: ` }),
      h("span", { text: it.text })));
  }));
  if (!s.checklist.length) box.append(h("li", { class: "part-check part-check-done" }, "Nothing left to do."));
}

function renderStatus() {
  const s = part.state;
  if (!s) return;
  const saved = $("#part-saved");
  if (readOnly()) { saved.textContent = "Read-only"; saved.className = "saved saved-warn"; }
  else if (busy) { saved.textContent = "Saving…"; saved.className = "saved saved-busy"; }
  else if (part.errors.length) { saved.textContent = "Last change not saved (see the error)"; saved.className = "saved saved-bad"; }
  else { saved.textContent = "✓ All changes saved"; saved.className = "saved saved-ok"; }
  const b = $("#part-build"), st = (s.build || {}).state;
  if (working) { b.textContent = working; b.className = "stat saved-busy"; }
  else if (st === "building") { b.textContent = "Updating parts…"; b.className = "stat saved-busy"; }
  else if (st === "failed") { b.textContent = `Parts not updated: ${s.build.message || "the build failed"}`; b.className = "stat stat-bad"; }
  else { b.textContent = "Parts up to date"; b.className = "stat stat-ok"; }
  const total = s.parts.reduce((a, p) => a + (p.mass || 0), 0);
  $("#part-total").textContent = s.parts.length > 1 ? `${s.parts.length} parts · ${kg(total)}` : `Weight ${kg(total)}`;
  $("#part-reimport").hidden = plugin();
  renderTools();
}

// -- inspector ------------------------------------------------------------------------------------------
export const sections = [];
// Add an inspector section {id, when(ctx), render(ctx)}; with {before: id} it goes before that one; an existing id
// is replaced in place.
export function addSection(sec, { before = null } = {}) {
  const at = sections.findIndex((x) => x.id === sec.id);
  if (at >= 0) { sections[at] = sec; return; }
  const b = before ? sections.findIndex((x) => x.id === before) : -1;
  if (b >= 0) sections.splice(b, 0, sec); else sections.push(sec);
}

function context() {
  const s = part.state, sel = part.sel;
  const bodies = sel.kind === "body" ? [...sel.keys].map(bodyOf).filter(Boolean) : [];
  return { state: s, sel, pieces: selectedPieces(), bodies, body: bodies.length === 1 ? bodies[0] : null,
    plugin: plugin(), ro: readOnly() };
}

// A labelled control with its messages: the refused edit's errors whose field is `name` go under it.
export function field(label, control, name, hint = null) {
  if (control.id === "") control.id = `part-f-${name}`;
  if (readOnly()) for (const x of [control, ...control.querySelectorAll("input, select, button")]) if (x.matches("input, select, button")) x.disabled = true;
  const msgs = part.errors.filter((e) => e.field === name);
  if (msgs.length) control.classList.add("invalid");
  return h("div", { class: "field", dataset: { field: name } },
    h("label", { class: "field-label", for: control.id, text: label }),
    control,
    hint ? h("p", { class: "hint", text: hint }) : null,
    ...msgs.map((e) => h("p", { class: "error field-msg", role: "alert", text: e.message })));
}

// A text or number box that commits on Enter or leaving it, when its text changed.
export function box(attrs, commit) {
  const el = h("input", { autocomplete: "off", ...attrs });
  el.dataset.orig = el.value = attrs.value == null ? "" : String(attrs.value);
  el.addEventListener("change", () => { if (live(el) && el.value !== el.dataset.orig) commit(el.value.trim(), el); });
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  return el;
}

// A typed number, or null for an empty box; NaN (and a local message) for text that isn't one.
function number(text, name) {
  if (text === "") return null;
  const v = Number(text.replace(",", "."));
  if (!isFinite(v)) {
    part.errors = [{ field: name, message: "Type a number, such as 0.25." }];
    render();
    return NaN;
  }
  return v;
}

function matLabel(key, short = false) {
  if (key === "custom") return short ? "custom" : "Something else (type its density)";
  const m = part.materials && part.materials.materials[key];
  if (!m) return key;
  if (short) return m.label;
  const g = (m.density / 1000).toFixed(2);
  return m.printed ? `${m.label}, ${g} g/cm³ solid` : `${m.label} (${g} g/cm³)`;
}

const noticed = new WeakSet();   // refused edits whose unplaced messages were already shown as notices

function renderInspector() {
  const root = $("#part-inspector");
  // keep what is half typed in a box across the redraw
  const act = document.activeElement;
  const typing = act && root.contains(act) && act.tagName === "INPUT" && act.id && act.value !== act.dataset.orig
    ? { id: act.id, value: act.value, start: act.selectionStart, end: act.selectionEnd } : null;
  const keepFocus = act && root.contains(act) && act.id ? act.id : null;
  const ctx = context();
  const out = [];
  for (const sec of sections) {
    if (!sec.when(ctx)) continue;
    const node = sec.render(ctx);
    if (node) out.push(...(Array.isArray(node) ? node : [node]).filter(Boolean));
  }
  root.replaceChildren(...out);
  const t = ctx.sel.kind === "piece" ? (ctx.pieces.length === 1 ? tail(ctx.pieces[0].key) : `${ctx.pieces.length} pieces`)
    : ctx.sel.kind === "body" ? (ctx.body ? ctx.body.name : `${ctx.bodies.length} bodies`) : ctx.sel.kind === "frame" ? "robot frame" : null;
  $("#part-insp-title").textContent = t ? `Selected: ${t}` : "Inspector";
  if (keepFocus) {
    const el = document.getElementById(keepFocus);
    if (el && root.contains(el)) {
      if (typing && el.tagName === "INPUT") { el.value = typing.value; try { el.setSelectionRange(typing.start, typing.end); } catch { /* number boxes */ } }
      el.focus({ preventScroll: true });
    }
  }
  // a refused edit's message that has no field shown goes in a notice
  if (!noticed.has(part.errors)) {
    noticed.add(part.errors);
    for (const e of part.errors) if (!root.querySelector(`[data-field="${CSS.escape(e.field)}"]`)) notice(e.message, "error", "part-edit");
  }
}

// Nothing selected: the part as a whole.
addSection({
  id: "summary",
  when: (c) => !c.sel.kind,
  render: (c) => h("section", { class: "insp-section" },
    h("h3", { text: c.state.name || "This part" }),
    ...c.state.parts.map((p) => h("p", { class: "part-line" },
      c.state.parts.length > 1 ? h("strong", { text: `${p.name}: ` }) : null,
      `Weight ${kg(p.mass)} · center of gravity ${cog(p.com)}`)),
    h("p", { class: "hint", text: c.plugin
      ? "Click a body in the 3D view or the list to change its weight."
      : "Click a piece in the 3D view or the list to say what it is made of. Shift-click adds more pieces. Drag pieces onto a body to move them." }),
    c.state.source ? h("p", { class: "hint" }, "From ", h("code", { text: c.state.source }), ". Drop a new CAD file anywhere here to use it instead.") : null),
});

// The selected pieces: their weight, their body.
addSection({
  id: "pieces",
  when: (c) => c.sel.kind === "piece" && c.pieces.length > 0,
  render: (c) => {
    const ps = c.pieces;
    const mass = ps.reduce((a, p) => a + (p.left_out ? 0 : p.mass || 0), 0);
    const bodies = [...new Set(ps.map((p) => p.body))];
    const move = h("select", { id: "part-move" },
      bodies.length > 1 ? h("option", { value: "", disabled: true }, "Different bodies") : null,
      ...c.state.bodies.map((b) => h("option", { value: b.name }, b.name)));
    move.value = bodies.length > 1 ? "" : bodies[0];
    move.addEventListener("change", () => { if (live(move) && move.value) editOp("move_pieces", { pieces: ps.map((p) => p.key), body: move.value }); });
    const out = ps.filter((p) => p.left_out).length;
    return h("section", { class: "insp-section insp-head" },
      h("h3", { text: ps.length === 1 ? ps[0].name : `${ps.length} pieces` }),
      ps.length === 1 ? h("p", { class: "hint mono", text: ps[0].key }) : null,
      h("p", { class: "part-line" }, "Weight ",
        h("strong", { id: "part-sel-mass", dataset: { kg: String(mass) }, text: kg(mass) }),
        out ? h("span", { class: "muted", text: ` (${out === ps.length ? "left out" : `${out} left out`})` }) : null),
      field("In body", move, "body", "Pieces in one body move together. You can also drag them in the list."));
  },
});

// What the selected pieces (or the selected body's pieces) are made of, and "or type a weight".
addSection({
  id: "material",
  when: (c) => !c.plugin && ((c.sel.kind === "piece" && c.pieces.length > 0)
    || (c.body && c.state.pieces.some((p) => p.body === c.body.name))),
  render: (c) => {
    const ps = c.sel.kind === "piece" ? c.pieces : c.state.pieces.filter((p) => p.body === c.body.name);
    const keys = ps.map((p) => p.key);
    const mats = new Set(ps.map((p) => p.material));
    const one = mats.size === 1 ? [...mats][0] : undefined;
    const choices = part.materials ? Object.keys(part.materials.materials) : [];
    const sel = h("select", { id: "part-material" },
      one === undefined ? h("option", { value: "-", disabled: true }, "Different materials") : null,
      one === null ? h("option", { value: "", disabled: true }, "Not chosen yet (counted as aluminum)") : null,
      ...choices.map((k) => h("option", { value: k }, matLabel(k))),
      h("option", { value: "custom" }, matLabel("custom")));
    sel.value = one === undefined ? "-" : one === null ? "" : one;
    const printed = one && part.materials && part.materials.materials[one] && part.materials.materials[one].printed;
    const infillOf = ps[0] && ps[0].infill;
    const send = (material, extra = {}) => editOp("set_material", { pieces: keys, material, ...extra });
    sel.addEventListener("change", () => {
      if (!live(sel)) return;
      const m = sel.value;
      if (m === "custom") {   // ask for the density first; the edit goes when it is typed
        customAsked = true;
        render();
        const d = document.getElementById("part-density");
        if (d) d.focus();
        return;
      }
      customAsked = false;
      const pm = part.materials.materials[m];
      send(m, pm && pm.printed ? { infill: one === m && infillOf != null ? infillOf : null } : {});
    });
    const rows = [field("Material", sel, "material", ps.length > 1 ? `Sets all ${ps.length} pieces.` : null)];
    if (printed) {
      const fill = box({ id: "part-infill", type: "number", min: "0", max: "100", step: "5", inputmode: "decimal",
        value: infillOf == null ? "" : String(+(infillOf * 100).toFixed(1)), placeholder: "40" }, (text) => {
        const v = number(text, "infill");
        if (!Number.isNaN(v)) send(one, { infill: v === null ? null : v / 100 });
      });
      rows.push(field("Infill (percent)", h("span", { class: "with-unit" }, fill, h("span", { class: "unit", text: "%" })), "infill",
        "How full the slicer fills the inside. Empty means 40%."));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-infill";
    }
    if (one === "custom" || (customAsked && sel.value !== "-")) {
      if (one !== "custom") sel.value = "custom";
      const dens = box({ id: "part-density", type: "number", min: "0", step: "any", inputmode: "decimal",
        value: one === "custom" && ps[0].custom_density != null ? String(ps[0].custom_density) : "", placeholder: "2700" }, (text) => {
        const v = number(text, "custom_density");
        if (!Number.isNaN(v)) send("custom", { custom_density: v });
      });
      rows.push(field("Density", h("span", { class: "with-unit" }, dens, h("span", { class: "unit", text: "kg per cubic meter" })), "custom_density",
        "Aluminum is 2700, steel 7850, polycarbonate 1200."));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-density";
    }
    if (c.sel.kind === "piece") {
      const typed = new Set(ps.map((p) => p.typed_mass));
      const t = typed.size === 1 ? [...typed][0] : null;
      const w = box({ id: "part-weight", type: "number", min: "0", step: "any", inputmode: "decimal",
        value: t == null ? "" : String(t), placeholder: ps.length === 1 && ps[0].mass != null ? String(+ps[0].mass.toPrecision(3)) : "" }, async (text) => {
        const v = number(text, "kg");
        if (Number.isNaN(v)) return;
        for (const k of keys) {   // one edit per piece: set_mass names one piece
          const r = await editOp("set_mass", { piece: k, kg: v });
          if (!r.ok) break;
        }
      });
      rows.push(field(ps.length > 1 ? "or type a weight (each piece)" : "or type a weight",
        h("span", { class: "with-unit" }, w, h("span", { class: "unit", text: "kg" })), "kg",
        "A typed weight wins over the material. Empty it to use the material again."));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-weight";
    }
    return h("section", { class: "insp-section" }, h("h3", { text: c.sel.kind === "body" ? "What its pieces are made of" : "What it is made of" }), ...rows);
  },
});
let customAsked = false;
onPart(() => { if (part.sel.kind !== "piece" && part.sel.kind !== "body") customAsked = false; });

// One body: its name, weight, center of gravity and whether it is its own part.
addSection({
  id: "body",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body, s = c.state;
    const rows = [];
    if (c.plugin) rows.push(h("h3", { text: b.name }));
    else {
      rows.push(h("h3", { text: "Body" }));
      const name = box({ id: "part-body-name", type: "text", value: b.name, spellcheck: "false" }, (text) => {
        if (!text) { part.errors = [{ field: "new", message: "A body needs a name: type one." }]; render(); return; }
        pendingBody = text;
        editOp("rename_body", { old: b.name, new: text });
      });
      rows.push(field("Name", name, "new"));
    }
    rows.push(h("p", { class: "part-line" }, "Weight ", h("strong", { id: "part-body-mass", dataset: { kg: String(b.mass ?? "") }, text: kg(b.mass) })));
    rows.push(h("p", { class: "part-line" }, "Center of gravity ", h("span", { class: "mono", text: cog(b.com) })));
    if (b.typed_mass !== undefined || c.plugin) {
      const w = box({ id: "part-body-weight", type: "number", min: "0", step: "any", inputmode: "decimal",
        value: b.typed_mass == null ? "" : String(b.typed_mass), placeholder: b.mass != null ? String(+b.mass.toPrecision(3)) : "" }, (text) => {
        const v = number(text, "kg");
        if (!Number.isNaN(v)) editOp("set_mass", { body: b.name, kg: v });
      });
      rows.push(field("or type its weight", h("span", { class: "with-unit" }, w, h("span", { class: "unit", text: "kg" })), "kg",
        c.plugin ? "Empty it to use the weight the CAD plugin wrote." : "A typed weight wins over its pieces' materials. Empty it to use them again."));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-body-weight";
    }
    if (!c.plugin) {
      const own = b.parent == null;
      const roots = s.bodies.filter((x) => x.parent == null);
      const check = h("input", { type: "checkbox", id: "part-own" });
      check.checked = own && askParent !== b.name;
      const only = own && roots.length < 2;
      if (only) check.disabled = true;
      check.addEventListener("change", () => {
        if (!live(check)) return;
        if (check.checked) { askParent = null; editOp("set_part", { body: b.name, part: b.name }); }
        else { askParent = b.name; render(); const p = document.getElementById("part-parent"); if (p) p.focus(); }
      });
      const ownRow = h("div", { class: "field part-own", dataset: { field: "part" } },
        h("label", { class: "part-check-label", for: "part-own" }, check, " Its own part"),
        h("p", { class: "hint", text: only ? "Every import makes at least one part: this is it."
          : own ? "It is placed on the robot by itself, and the bodies on it go with it." : "Tick it to place this body on the robot by itself." }),
        ...part.errors.filter((e) => e.field === "part").map((e) => h("p", { class: "error field-msg", role: "alert", text: e.message })));
      rows.push(ownRow);
      if (own && askParent !== b.name) {
        const pn = box({ id: "part-part-name", type: "text", value: b.part || b.name, spellcheck: "false" }, (text) => {
          if (text) editOp("set_part", { body: b.name, part: text });
        });
        rows.push(field("Part name", pn, "part-name", "The name of its folder in parts/."));
      }
      if (askParent === b.name) {
        const below = descendants(b.name);
        const choices = s.bodies.filter((x) => x.name !== b.name && !below.has(x.name));
        const ps = h("select", { id: "part-parent" }, h("option", { value: "", disabled: true }, "Pick a body"),
          ...choices.map((x) => h("option", { value: x.name }, x.name)));
        ps.value = "";
        ps.addEventListener("change", async () => {
          if (!live(ps) || !ps.value) return;
          const r = await editOp("set_part", { body: b.name, part: null, parent: ps.value });
          if (r.ok) { askParent = null; render(); }
        });
        rows.push(field("It hangs on", ps, "parent", "It stops being a part and moves with that body."));
      }
    }
    return h("section", { class: "insp-section insp-head" }, ...rows);
  },
});
let askParent = null;   // the body whose "Its own part" was just unticked: which body does it hang on?

function descendants(name) {
  const out = new Set();
  let grew = true;
  while (grew) {
    grew = false;
    for (const b of part.state.bodies) if (b.parent && (b.parent === name || out.has(b.parent)) && !out.has(b.name)) { out.add(b.name); grew = true; }
  }
  return out;
}

// Several bodies (a checklist item names them): one button each.
addSection({
  id: "bodies",
  when: (c) => c.sel.kind === "body" && c.bodies.length > 1,
  render: (c) => h("section", { class: "insp-section" },
    h("h3", { text: `${c.bodies.length} bodies` }),
    h("ul", { class: "part-body-list" }, ...c.bodies.map((b) => h("li", {},
      h("button", { type: "button", class: "linklike", onclick: () => select("body", [b.name]) }, b.name),
      h("span", { class: "muted", text: ` · ${kg(b.mass)}` })))),
    h("p", { class: "hint", text: "Pick one to change it." })),
});

// The robot frame (which way is up and forward), as the import guessed it.
const AXIS = { "+x": "+x", "-x": "−x", "+y": "+y", "-y": "−y", "+z": "+z", "-z": "−z" };
addSection({
  id: "frame",
  when: (c) => c.sel.kind === "frame",
  render: (c) => {
    const f = c.state.frame;
    if (!f) return h("section", { class: "insp-section" }, h("p", { class: "hint", text: "A plugin-made part keeps the frame its CAD plugin wrote." }));
    return h("section", { class: "insp-section" },
      h("h3", { text: "Robot frame" }),
      h("p", { class: "part-line" }, "Up is ", h("strong", { text: AXIS[f.up] || f.up }), " and forward is ", h("strong", { text: AXIS[f.front] || f.front }), " in the CAD file."),
      h("p", { class: "hint", text: f.confirmed ? "Checked." : "This is a guess: most CAD programs use +z up, but Inventor uses +y up." }));
  },
});

// -- boot -------------------------------------------------------------------------------------------------
function initDrop() {
  // a CAD file dropped anywhere on the tab replaces the source (a piece dragged in the tree is not a file)
  const tab = $("#tab-part"), cover = $("#part-drop");
  const files = (e) => [...(e.dataTransfer && e.dataTransfer.types || [])].includes("Files");
  let depth = 0;
  tab.addEventListener("dragenter", (e) => { if (!files(e) || plugin()) return; depth++; cover.hidden = false; });
  tab.addEventListener("dragleave", (e) => { if (!files(e)) return; depth = Math.max(0, depth - 1); if (!depth) cover.hidden = true; });
  tab.addEventListener("dragover", (e) => { if (!files(e) || plugin()) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  tab.addEventListener("drop", (e) => {
    if (!files(e)) return;
    e.preventDefault();
    depth = 0;
    cover.hidden = true;
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (plugin()) { notice("A plugin-made part comes from its CAD plugin: export it again there.", "warn"); return; }
    if (!/\.(step|stp|stl|obj)$/i.test(f.name)) { notice(`${f.name} isn't a CAD file: drop a .step, .stp, .stl or .obj file.`, "error"); return; }
    reimport(f);
  });
}

export async function initPart() {
  window.__scrimmagePart = { store: part, screenPos: () => null, scene: { drawn: [], faint: [], meshes: 0 } };
  registerTab("part", { renderTop, undo, redo, onShow: render });
  setTab("part");
  $("#part-new-body").addEventListener("click", () => {
    const ps = selectedPieces();
    if (!ps.length) return;
    const taken = new Set(part.state.bodies.map((b) => b.name));
    const stem = (ps[0].name || "body").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "body";
    let name = stem, n = 2;
    while (taken.has(name)) name = `${stem}_${n++}`;
    pendingBody = name;
    editOp("new_body", { pieces: ps.map((p) => p.key), name }).then((r) => {
      if (!r.ok) return;
      const box = document.getElementById("part-body-name");
      if (box) { box.focus(); box.select(); }
    });
  });
  $("#part-leave-out").addEventListener("click", () => {
    const ps = selectedPieces();
    if (ps.length) editOp("leave_out", { pieces: ps.map((p) => p.key), out: !ps.every((p) => p.left_out) });
  });
  $("#part-reimport").addEventListener("click", () => reimport());
  initDrop();
  const [m, app] = await Promise.all([api("GET", "/api/part/materials"), api("GET", "/api/app/state")]);
  if (m.status === 200) part.materials = m.data;
  else notice("Couldn't load the list of materials; reload the page to try again.", "error");
  if (app.status === 200) part.appPath = app.data.display_path || app.data.path || "";
  import("./part3d.js").then((v) => v.initPartView()).catch((e) => {
    console.error(e);
    $("#part-view").replaceChildren(h("p", { class: "view-placeholder" }, `The 3D view failed to load: ${e.message || e}`));
  });
  await pollLoop();
}
