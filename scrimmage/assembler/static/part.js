// The Part tab: one CAD import (or one plugin-made part folder) turned into part folders. See /api/part/* in the
// docstring of scrimmage/assembler/server.py for the API contract.
//
//   Left       the tree: the bodies (pieces that move together), each with its pieces, the hardware the import
//              guessed folded under "+N hardware"; a piece dragged onto a body moves into it. Above it "New body
//              from selection" and "Leave out" act on the selection. Below it the checklist ("Still needed"): a click
//              selects what the item is about.
//   Middle     the 3D view (part3d.js, loaded on its own so a WebGL failure leaves the rest working), with the
//              "Where's the robot?" step over it until the robot frame is confirmed.
//   Right      the inspector: the sections in `sections` whose `when(ctx)` is true, in order (part_inspector.js).
//   Status     saved or not, "Parts up to date" / "Updating parts…" from build.state, the part's mass, Test it (once the
//              parts are built: puts them on a stand-in robot with a starter test and opens the Test tab), Import
//              again.
//
// This file is the store, the edits, the tree, the checklist and the status line. Every edit is saved at once (no
// Save button); the part folders rebuild in the background. A CAD file dropped anywhere on the tab replaces the
// source. In plugin mode (state.mode "plugin") there are no pieces: only the body-level controls show.
//
// For the inspector (part_inspector.js): `addSection({id, when, render}, {before})` adds an inspector section
// (render(ctx) returns a node or a list; ctx is {state, sel, pieces, bodies, body, plugin, ro}); `editOp(op, args)`
// makes one edit (it resolves {ok, errors}); `select(kind, keys, {add})` changes the selection; `onPart(fn)` calls fn
// after every render and `onView(fn)` with every new /api/part/view; `field()` draws a labelled control with its
// error messages. `view3d` is filled by part3d.js: startPick({hint, onPick}), cancelPick(), picking(), pose() (redraw
// the "Try it" poses from part.tryit), solidBox() (the drawn pieces' robot-frame box). Test hook:
// window.__scrimmagePart {store, screenPos(pieceKey, {along, across}), screenPoint(pieceKey), scene, toRobot(frame)}.

import { $, h, api, notice, serial, registerTab, setTab, showPath, sessionToken, markUnits, typedText } from "./app.js";
import { uploadCad, cadFilesProblem, filesName } from "./start.js";
import { units } from "./units.js";
import { i18n, t, tn } from "./i18n.js";

export const part = {
  state: null,        // /api/part/state
  materials: null,    // /api/part/materials
  view: null,         // /api/part/view
  sel: { kind: null, keys: new Set() },   // kind: "piece" | "body" | "frame" | null
  errors: [],         // the last refused edit's [{field, message}]
  showLeftOut: false,
  openHardware: new Set(),   // bodies whose hardware rows are unfolded
  appPath: "",        // /api/app/state's display path of the import file (or part folder)
  tryit: new Map(),   // body -> its "Try it" slider (degrees or meters): the 3D view only, never sent
};

// The 3D view's controls, filled in by part3d.js when it loads (empty when WebGL is missing).
export const view3d = {};

const listeners = [], viewListeners = [];
export function onPart(fn) { listeners.push(fn); }
export function onView(fn) { viewListeners.push(fn); if (part.view) fn(part.view); }

let busy = 0, working = "";   // working: a re-import or upload in progress ("Reading the CAD file…")
let reveal = false;           // after the next render, scroll the selected tree row into view
let pendingBody = null;       // select this body once a state that has it arrives (a new or renamed body)
export function expectBody(name) { pendingBody = name; }
let lastMessages = "[]";
let testing = false;          // Test it was pressed and the Test tab is on its way
let viewKey = null;

const plugin = () => !!part.state && part.state.mode === "plugin";
const readOnly = () => !!part.state && !!part.state.read_only_reason;
const pieceOf = (key) => part.state.pieces.find((p) => p.key === key) || null;
const bodyOf = (name) => part.state.bodies.find((b) => b.name === name) || null;
export const tail = (key) => String(key).split("/").pop();

// A weight in the chosen units (units.js): two places from 10 up, else three significant digits (a screw's weight).
export function kg(x) {
  if (typeof x !== "number" || !isFinite(x)) return "—";
  const v = units.toDisplay(x, "mass");
  return `${v >= 10 ? v.toFixed(2) : v === 0 ? "0" : String(+v.toPrecision(3))} ${units.name("mass")}`;
}
// A point (the center of gravity, a pivot): to 0.001 in or 0.1 mm, never "-0"
const len = (v) => units.number(units.toDisplay(v, "length"), units.name("length") === "in" ? 3 : 1);
export const cog = (c) => (c ? t("x {x}, y {y}, z {z} {unit}", { x: len(c[0]), y: len(c[1]), z: len(c[2]), unit: units.name("length") })
  : t("none (no weight)"));

// -- edits ------------------------------------------------------------------------------------------
async function mutate(path, body) {
  if (!part.state) return { ok: false, errors: [] };
  if (readOnly()) {
    notice(t("This part is read-only: {why}", { why: part.state.read_only_reason }), "warn");
    return { ok: false, errors: [] };
  }
  busy++; renderStatus();
  try {
    const { status, data } = await api("POST", path, { version: part.state.version, ...body });
    if (status === 200) {
      part.errors = [];
      if (data.ok === false) notice(path.endsWith("undo") ? t("Nothing to undo.") : t("Nothing to redo."), "info");
      applyState(data, true);
      return { ok: data.ok !== false };
    }
    const errors = data.errors && data.errors.length ? data.errors
      : [{ field: "status", message: data.error || t("the server answered {status}", { status }) }];
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

// Read the source again. With `files` (dropped or chosen) they replace it; `again` says they are the same source
// exported again (Import again on a copied source), so the settings are kept as for an ordinary re-import.
async function reimport(files = null, again = false) {
  if (!part.state) return;
  const what = files ? filesName(files) : null;
  working = files ? t("Reading {files}…", { files: what }) : t("Reading the CAD file again…");
  renderStatus();
  try {
    const { status, data } = files ? await uploadCad(files, { again })
      : await api("POST", "/api/part/reimport", { version: part.state.version });
    if (status === 200 && data.version !== undefined) {
      part.errors = [];
      viewKey = null;   // the same path may hold new shapes
      applyState(data, true);
      notice(again ? t("Read the new export ({files}).", { files: what }) : files ? t("Using {files} now.", { files: what }) : t("Read the CAD file again."), "info");
    } else {
      const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || t("the server answered {status}", { status });
      notice(files ? t("Couldn't use {files}: {why}", { files: what, why }) : t("Couldn't read the CAD file again: {why}", { why }), "error");
      await refresh();
    }
  } finally {
    working = "";
    renderStatus();
  }
}

// Import again: a source that is a dropped or chosen file's copy (state.source_copied) would read the same copy
// again, so it asks for the new export instead; a source opened from a path is read again from there.
function importAgain() {
  if (!part.state) return;
  if (!part.state.source_copied) { reimport(); return; }
  const chooser = $("#part-reimport-file");
  chooser.value = "";
  chooser.click();
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
    ro.textContent = readOnly() ? t("Read-only: {why}", { why: part.state.read_only_reason }) : "";
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
  $("#part-leave-out").textContent = back ? t("Put back") : t("Leave out");
  $("#part-leave-out").title = back ? t("Put the selected pieces back into the part") : t("Leave the selected pieces out of the part (they weigh nothing and aren't drawn)");
  $("#part-leave-out").disabled = off;
  $("#part-reimport").disabled = !!working || (readOnly() && !part.state.source);
}

// The tree: [role=tree] with bodies (li[role=treeitem] > .part-row[data-body] + ul[role=group]) and pieces
// (li[role=treeitem][data-piece]). Arrow keys move, Space or Enter selects (with Shift or Ctrl: adds).
function renderTree() {
  const s = part.state, box = $("#part-tree");
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  const tree = h("ul", { class: "part-tree", role: "tree", "aria-label": t("Bodies and their pieces"), "aria-multiselectable": "true" });
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
          title: t("Bolts, nuts and other hardware the import folded into this body"),
          onclick: () => { if (open) part.openHardware.delete(b.name); else part.openHardware.add(b.name); render(); } },
        open ? t("−{count} hardware", { count: hw.length }) : t("+{count} hardware", { count: hw.length }))));
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
    row, mine.length || plugin() ? group : h("p", { class: "hint part-empty" }, t("No pieces: drag some here."))));
  }
  if (!s.bodies.length) tree.append(h("li", { role: "none", class: "empty" }, s.read_only_reason ? t("Nothing to show.") : t("No bodies yet.")));
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
  const pieces = plugin() ? "" : `${tn("{count} piece", "{count} pieces", n)} · `;
  if (b.parent == null) return pieces + (b.part && b.part !== b.name ? t("its own part ({part})", { part: b.part }) : t("its own part"));
  const kind = b.joint && b.joint.kind;   // JointSettings.kind (and a plugin part's Joint.kind): "hinge" | "slide"
  const parent = { parent: b.parent };
  return pieces + (kind === "hinge" ? t("turns on {parent}", parent) : kind === "slide" ? t("slides on {parent}", parent) : t("fixed to {parent}", parent));
}

function pieceItem(p, drag) {
  const chosen = part.sel.kind === "piece" && part.sel.keys.has(p.key);
  const li = h("li", { role: "treeitem", class: `part-item part-piece${chosen ? " selected" : ""}${p.left_out ? " left-out" : ""}`,
    tabindex: "-1", "aria-selected": String(chosen), draggable: drag ? "true" : null, title: p.key,
    dataset: { piece: p.key, key: `p:${p.key}` },
    onclick: (e) => select("piece", [p.key], { add: e.shiftKey || e.ctrlKey || e.metaKey, show: false }) },
  h("span", { class: "part-row" },
    h("span", { class: "part-piece-name", text: tail(p.key) }),
    p.left_out ? h("span", { class: "part-tag", text: t("left out") }) : null,
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
  const MARK = { todo: ["☐", () => t("To do")], check: ["!", () => t("Check")], error: ["✕", () => t("Problem")] };
  box.replaceChildren(...s.checklist.map((it) => {
    const [mark, word] = MARK[it.level] || MARK.todo;
    const target = it.target || { kind: null, keys: [] };
    return h("li", { class: `part-check part-check-${it.level}` },
      h("button", { type: "button", class: "part-check-btn", title: t("Show what this is about"),
        onclick: () => select(target.kind, target.keys || []) },
      h("span", { class: "part-check-mark", "aria-hidden": "true", text: mark }),
      h("span", { class: "visually-hidden", text: t("{word}:", { word: word() }) + " " }),
      h("span", { text: it.text })));
  }));
  if (!s.checklist.length) box.append(h("li", { class: "part-check part-check-done" }, t("Nothing left to do.")));
}

function renderStatus() {
  const s = part.state;
  if (!s) return;
  const saved = $("#part-saved");
  if (readOnly()) { saved.textContent = t("Read-only"); saved.className = "saved saved-warn"; }
  else if (busy) { saved.textContent = t("Saving…"); saved.className = "saved saved-busy"; }
  else if (part.errors.length) { saved.textContent = t("Last change not saved (see the error)"); saved.className = "saved saved-bad"; }
  else { saved.textContent = t("✓ All changes saved"); saved.className = "saved saved-ok"; }
  const b = $("#part-build"), st = (s.build || {}).state;
  if (working) { b.textContent = working; b.className = "stat saved-busy"; }
  else if (st === "building") { b.textContent = t("Updating parts…"); b.className = "stat saved-busy"; }
  else if (st === "failed") { b.textContent = t("Parts not updated: {why}", { why: s.build.message || t("the build failed") }); b.className = "stat stat-bad"; }
  else { b.textContent = t("Parts up to date"); b.className = "stat stat-ok"; }
  const total = s.parts.reduce((a, p) => a + (p.mass || 0), 0);
  $("#part-total").textContent = s.parts.length > 1 ? t("{count} parts · {weight}", { count: s.parts.length, weight: kg(total) })
    : t("Weight {weight}", { weight: kg(total) });
  const tryIt = $("#part-test-it");
  const built = st === "idle" && !working && s.parts.length > 0 && s.parts.every((p) => p.mass != null);
  tryIt.hidden = plugin();
  tryIt.disabled = !built || readOnly() || testing;
  tryIt.title = readOnly() ? t("This part is read-only") : built ? t("Try the part on a stand-in robot: opens the Test tab with a test to start from")
    : t("Wait for the parts to be up to date");
  $("#part-reimport").hidden = plugin();
  $("#part-reimport").title = s.source_copied ? t("Choose the new export: this part was made from a copy of a dropped or chosen file")
    : t("Read the CAD file again (after you changed it in your CAD program)");
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

// A text box (numbers too: see number) that commits on Enter or leaving it, when its text differs from the server's (dataset.orig),
// once. Not only on "change": text carried across a redraw (renderInspector) is set by script, and then Enter or
// leaving the box fires no change. A box a redraw removes never commits (live): its text lives on in the new box.
// `kind`: a units.js kind its number is typed in (so text half typed across a Units change is converted).
export function box({ kind = null, ...attrs }, commit) {
  const el = markUnits(h("input", { autocomplete: "off", ...attrs }), kind);
  el.dataset.orig = el.value = attrs.value == null ? "" : String(attrs.value);
  const send = () => {
    if (!live(el) || el.value === el.dataset.orig) return;
    el.dataset.orig = el.value;     // once: "change" and "blur" both come when leaving an edited box
    commit(el.value.trim(), el);
  };
  el.addEventListener("change", send);
  el.addEventListener("blur", send);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); el.blur(); } });
  return el;
}

// A typed measure (units.js kind) in SI, or null for an empty box; NaN (and a local message, nothing sent) for
// text that isn't one. As number(): the comma rule, the typed minus sign; a number may end with its unit ("4 in").
export function measure(text, name, kind) {
  text = text.trim().replace(/\u2212/g, "-");
  if (text === "") return null;
  let message = null;
  if (THOUSANDS.test(text)) {
    const whole = text.replace(",", ""), dec = text.replace(",", ".");
    message = t("\"{text}\" could mean {whole} or {dec}. Type it without the comma.", { text, whole, dec });
  }
  const v = message ? null : units.parse(text.replace(/^([+-]?\d*),/, "$1."), kind);
  if (v !== null && typeof v === "object") message = v.error;
  if (message) {
    part.errors = [{ field: name, message }];
    render();
    return NaN;
  }
  return v;
}

// A box's text for an SI measure (units.js shown), and the box's value back: its saved SI value while its text is
// still the shown one, so a row of boxes saved together writes back the ones not changed exactly as they were.
export const shownMeasure = (si, kind) => (typeof si === "number" && isFinite(si) ? units.shown(si, kind) : "");
export function boxMeasure(el, si, name, kind) {
  return el.value.trim() === shownMeasure(si, kind) && typeof si === "number" ? si : measure(el.value, name, kind);
}

// A typed number, or null for an empty box; NaN (and a local message, nothing sent) for text that isn't one.
// The boxes are text boxes, not type=number: a number box reads "1." as "1", so a redraw mid-typing would turn
// "1." then "5" into 15. A comma counts as the decimal point, except before exactly three digits: "7,850" could
// mean 7850 or 7.85, so it is refused rather than guessed. A typed minus sign (−, as the hints show) counts as "-".
const NUMBER_TEXT = /^[+-]?(\d+[.,]?\d*|[.,]\d+)([eE][+-]?\d+)?$/;
const THOUSANDS = /\d,\d{3}(?!\d)/;
export function number(text, name) {
  text = text.trim().replace(/\u2212/g, "-");
  if (text === "") return null;
  let message = null;
  if (THOUSANDS.test(text)) {
    const whole = text.replace(",", ""), dec = text.replace(",", ".");
    message = t("\"{text}\" could mean {whole} or {dec}. Type it without the comma.", { text, whole, dec });
  } else if (!NUMBER_TEXT.test(text) || !isFinite(Number(text.replace(",", ".")))) {
    message = t("\"{text}\" isn't a number. Type one such as 0.25.", { text });
  }
  if (message) {
    part.errors = [{ field: name, message }];
    render();
    return NaN;
  }
  return Number(text.replace(",", "."));
}

export function matLabel(key, short = false) {
  if (key === "custom") return short ? t("custom") : t("Something else (type its density)");
  const m = part.materials && part.materials.materials[key];
  if (!m) return key;
  if (short) return m.label;
  const g = units.fmt(m.density, "density");
  return m.printed ? t("{material}, {density} solid", { material: m.label, density: g }) : t("{material} ({density})", { material: m.label, density: g });
}

const noticed = new WeakSet();   // refused edits whose unplaced messages were already shown as notices

function renderInspector() {
  const root = $("#part-inspector");
  // keep what is half typed in a box across the redraw
  const act = document.activeElement;
  // (converted to the new units after a Units change: typedText)
  const typing = act && root.contains(act) && act.tagName === "INPUT" && act.id && act.value !== act.dataset.orig
    ? { id: act.id, value: typedText(act) } : null;
  const keepFocus = act && root.contains(act) && act.id ? act.id : null;
  // and what is selected in it, typed in or not: a box's text selected to type over (a triple click) and redrawn
  // before the first key would otherwise take the typing in front of its old number ("830" + "818" = 830818 mm)
  const sel = keepFocus && act.tagName === "INPUT" ? { start: act.selectionStart, end: act.selectionEnd, dir: act.selectionDirection } : null;
  const ctx = context();
  const out = [];
  for (const sec of sections) {
    if (!sec.when(ctx)) continue;
    const node = sec.render(ctx);
    if (node) out.push(...(Array.isArray(node) ? node : [node]).filter(Boolean));
  }
  root.replaceChildren(...out);
  const what = ctx.sel.kind === "piece" ? (ctx.pieces.length === 1 ? tail(ctx.pieces[0].key) : tn("{count} piece", "{count} pieces", ctx.pieces.length))
    : ctx.sel.kind === "body" ? (ctx.body ? ctx.body.name : tn("{count} body", "{count} bodies", ctx.bodies.length))
    : ctx.sel.kind === "frame" ? t("robot frame") : null;
  $("#part-insp-title").textContent = what ? t("Selected: {what}", { what }) : t("Inspector");
  if (keepFocus) {
    const el = document.getElementById(keepFocus);
    if (el && root.contains(el)) {
      if (typing && el.tagName === "INPUT") el.value = typing.value;
      el.focus({ preventScroll: true });
      if (sel && el.tagName === "INPUT" && sel.start !== null) {
        try { el.setSelectionRange(sel.start, sel.end, sel.dir || "none"); } catch { /* boxes with no text selection */ }
      }
    }
  }
  // a refused edit's message that has no field shown goes in a notice
  if (!noticed.has(part.errors)) {
    noticed.add(part.errors);
    for (const e of part.errors) if (!root.querySelector(`[data-field="${CSS.escape(e.field)}"]`)) notice(e.message, "error", "part-edit");
  }
}

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
    const dropped = [...e.dataTransfer.files];
    if (plugin()) { notice(t("A plugin-made part comes from its CAD plugin: export it again there."), "warn"); return; }
    const problem = cadFilesProblem(dropped);
    if (problem) { notice(problem, "error"); return; }
    reimport(dropped);
  });
}

// Test it: the server makes the stand-in robot and the test (once) and opens the test; the page then reloads on it.
async function testIt() {
  testing = true;
  renderStatus();
  const { status, data } = await api("POST", "/api/test/new", { from_part: true });
  if (status === 200 && data.ok) {
    const added = data.added || [];
    // said by the Test tab once the page has reloaded on it
    const text = added.length ? tn("Added to the stand-in robot where the CAD has it: {parts}.", "Added to the stand-in robot where the CAD has them: {parts}.",
      added.length, { parts: added.join(", ") })
      : data.plain_field ? t("No body is named like a hook or a hopper, so the stand-in robot starts on the plain field. Pick another field in Setup.")
        : null;
    if (text) {
      try { sessionStorage.setItem("scrimmage-test-notice", text); } catch { /* the notice is lost, nothing else */ }
    }
    location.hash = `token=${encodeURIComponent(sessionToken())}`;
    location.reload();
    return;
  }
  testing = false;
  renderStatus();
  const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || t("the server answered {status}", { status });
  notice(t("Can't test it yet: {why}", { why }), "warn");
}

export async function initPart() {
  window.__scrimmagePart = { store: part, screenPos: () => null, scene: { drawn: [], faint: [], meshes: 0 }, render: () => render() };
  await import("./part_inspector.js");   // its sections (it imports this file, so not at the top)
  registerTab("part", { renderTop, undo, redo, onShow: render });
  units.onChange(() => render());   // renderInspector keeps what is half typed
  i18n.onChange(async () => {      // likewise; then the checklist, the server's words, and the material names again
    render(); refresh();
    const m = await api("GET", "/api/part/materials");
    if (m.status === 200) { part.materials = m.data; render(); }
  });
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
  $("#part-reimport").addEventListener("click", importAgain);
  $("#part-test-it").addEventListener("click", testIt);
  $("#part-reimport-file").addEventListener("change", (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    const problem = cadFilesProblem(files);
    if (problem) { notice(problem, "error"); return; }
    reimport(files, true);
  });
  initDrop();
  const [m, app] = await Promise.all([api("GET", "/api/part/materials"), api("GET", "/api/app/state")]);
  if (m.status === 200) part.materials = m.data;
  else notice(t("Couldn't load the list of materials; reload the page to try again."), "error");
  if (app.status === 200) part.appPath = app.data.display_path || app.data.path || "";
  import("./part3d.js").then((v) => v.initPartView()).catch((e) => {
    console.error(e);
    $("#part-view").replaceChildren(h("p", { class: "view-placeholder" }, t("The 3D view failed to load: {why}", { why: e.message || e })));
  });
  await pollLoop();
}
