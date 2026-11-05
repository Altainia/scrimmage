// The assembler page: talks to the local server (see the docstring of scrimmage/assembler/server.py for the
// API contract), keeps the shared store, and re-renders the library, tree, inspector and status bar.
//
// Events other modules (the 3D view, Task 6) use, all on `window`:
//   scrimmage:state        detail {state}     a new document state was applied
//   scrimmage:view         detail {view}      /api/robot/view was reloaded (store.view)
//   scrimmage:selected     detail {name}      the selected instance changed (null = none)
//   scrimmage:pick-mount   detail {active}    the Add dialog wants a mount clicked in 3D (active false = stop)
//   scrimmage:mount-picked detail {name}      the 3D view reports a clicked mount "<instance>.<mount>"
//   scrimmage:hover        detail {name, source}  an instance is hovered in the tree or the 3D view (null = none)
//   scrimmage:tab          detail {tab}       the shown tab changed ("robot" | "scenario" | "drive" | "bench" | "part" |
//                                             "test" | "measure")
//   scrimmage:code         detail {state}     the robot code panel loaded /api/robot/code (code.js)
// The 3D view (view3d.js, loaded on its own so a WebGL failure can't break the page) selects a part with
// select(name) and sets store.view3d once it is running.

import { renderLibrary, initLibrary } from "./library.js";
import { renderTree } from "./tree.js";
import { renderInspector, placeErrors } from "./inspector.js";
import { units } from "./units.js";

// The hash carries the token and the tab: "#token=…" or "#scenario&token=…" ("#robot&token=…", "#drive&token=…").
const hashParams = () => new URLSearchParams(location.hash.slice(1));
const token = hashParams().get("token") || "";
export const sessionToken = () => token;

export const store = {
  state: null,      // /api/robot/state
  library: null,    // /api/robot/library
  view: null,       // /api/robot/view
  selected: null,   // selected instance name
  pendingSelect: null,  // select this instance once a state that has it arrives (after add/rename)
  lastErrors: [],   // errors from the last rejected edit/undo/redo, [{field, message}]
  view3d: false,    // the 3D view is running (WebGL available)
};

export const $ = (sel, root = document) => root.querySelector(sel);

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// -- server ------------------------------------------------------------------------------------
export async function api(method, path, body) {
  const headers = { "X-Scrimmage-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    return { status: 0, data: { ok: false, errors: [{ field: "status", message: "can't reach the assembler (is scrimmage edit still running?)" }] } };
  }
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!data || typeof data !== "object") data = {};
  if (res.status !== 200 && !(data.errors && data.errors.length)) {
    data = { ok: false, errors: [{ field: "status", message: data.error || `the server answered ${res.status}` }] };
  }
  return { status: res.status, data };
}

// A file from the server as a Blob (the token goes in the header, so <img src> can't fetch it directly).
// Returns {status, blob} (blob null when the request failed).
export async function apiFile(path) {
  try {
    const res = await fetch(path, { headers: { "X-Scrimmage-Token": token } });
    return { status: res.status, blob: res.ok ? await res.blob() : null };
  } catch {
    return { status: 0, blob: null };
  }
}

// -- tabs ----------------------------------------------------------------------------------------
// The Robot tab is this file's; the Scenario tab (scenario.js) registers itself with registerTab when a
// scenario is open, the Test tab (test.js) when a test is open, the Drive tab (drive.js) always:
// {renderTop(), undo(), redo(), onShow(), onHide()?}.
const TABS = ["robot", "scenario", "drive", "bench", "part", "test", "measure"];
const tabHooks = {};
let tab = "robot";

export function currentTab() { return tab; }

export function registerTab(name, hooks) {
  tabHooks[name] = hooks;
  $(`#tab-btn-${name}`).hidden = false;
  const want = hashParams();
  // a tab the link asks for opens; a scenario or a test opens on its own tab unless the link asks for another one
  if (want.has(name) || ((name === "scenario" || name === "test") && !TABS.some((t) => want.has(t)))) setTab(name);
}

export function setTab(name, { focus = false } = {}) {
  if (name !== "robot" && !tabHooks[name]) name = "robot";
  const changed = name !== tab;
  if (changed && tabHooks[tab] && tabHooks[tab].onHide) tabHooks[tab].onHide();
  tab = name;
  for (const t of TABS) {
    const btn = $(`#tab-btn-${t}`);
    btn.setAttribute("aria-selected", String(t === tab));
    btn.tabIndex = t === tab ? 0 : -1;
    btn.classList.toggle("active", t === tab);
    $(`#tab-${t}`).hidden = t !== tab;
  }
  if (focus) $(`#tab-btn-${tab}`).focus();
  history.replaceState(null, "", `#${tab}${token ? `&token=${encodeURIComponent(token)}` : ""}`);
  renderTop();
  if (changed) {
    if (tabHooks[tab] && tabHooks[tab].onShow) tabHooks[tab].onShow();
    window.dispatchEvent(new CustomEvent("scrimmage:tab", { detail: { tab } }));
  }
}

function initTabs() {
  for (const t of TABS) $(`#tab-btn-${t}`).addEventListener("click", () => setTab(t));
  $(".tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const shown = TABS.filter((t) => !$(`#tab-btn-${t}`).hidden);
    let k = shown.indexOf(tab);
    if (e.key === "ArrowLeft") k = (k + shown.length - 1) % shown.length;
    else if (e.key === "ArrowRight") k = (k + 1) % shown.length;
    else k = e.key === "Home" ? 0 : shown.length - 1;
    e.preventDefault();
    setTab(shown[k], { focus: true });
  });
  window.addEventListener("hashchange", () => {
    const want = hashParams();
    const t = TABS.find((x) => want.has(x));
    if (t && t !== tab) setTab(t);
  });
}

// The file path and Undo/Redo in the top bar belong to the shown tab.
export function renderTop() {
  if (tab !== "robot" && tabHooks[tab]) { tabHooks[tab].renderTop(); return; }
  const s = store.state;
  if (!s) return;
  showPath(s.display_path || s.path || "", s.path || "");
  $("#undo").disabled = !s.can_undo || s.read_only;
  $("#redo").disabled = !s.can_redo || s.read_only;
}

export function showPath(shown, full) {
  const slash = shown.lastIndexOf("/");
  $("#file-path").replaceChildren(h("strong", { text: shown.slice(slash + 1) }),
    ...(slash >= 0 ? [h("span", { class: "dir", text: `  in ${shown.slice(0, slash + 1)}` })] : []));
  $("#file-path").title = full;
}

function tabUndo() { return tab !== "robot" && tabHooks[tab] ? tabHooks[tab].undo() : undo(); }
function tabRedo() { return tab !== "robot" && tabHooks[tab] ? tabHooks[tab].redo() : redo(); }

// Our own edits' versions, so a version we didn't make reads as an outside change.
const ownVersions = new Set();
let chain = Promise.resolve();
let busy = 0;

export function serial(fn) {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
}

async function mutate(path, body, opts = {}) {
  if (!store.state) return { ok: false, errors: [] };
  if (store.state.read_only) {
    notice(`This config is read-only: ${store.state.read_only_reason}`, "warn");
    return { ok: false, errors: [] };
  }
  busy++; renderSaved();
  try {
    const { status, data } = await api("POST", path, { version: store.state.version, ...body });
    if (status === 200 && data.ok) {
      ownVersions.add(data.version);
      store.lastErrors = [];
      await refreshState();
      return { ok: true, version: data.version, data };
    }
    const errors = data.errors && data.errors.length ? data.errors
      : [{ field: "status", message: data.error || `request failed (${status})` }];
    if (status === 409) {
      // stale version, file changed on disk, or read-only: reload and say why
      notice(errors.map((e) => e.message).join("; "), "warn");
      store.lastErrors = [];
      await refreshState(true);
      return { ok: false, errors, conflict: true };
    }
    store.pendingSelect = null;
    store.lastErrors = errors;
    if (!opts.ownErrors) {   // else the caller shows them itself (the Add dialog) and clears them when done
      renderErrors();
      renderStatus();
      const shown = document.querySelector("#inspector .field-msgs .error");
      if (shown) shown.scrollIntoView({ block: "nearest" });
    }
    return { ok: false, errors };
  } finally {
    busy--; renderSaved();
  }
}

// One edit: {op, ...fields} plus the current version. Edits run one at a time, in order.
// opts.ownErrors: the caller displays the errors itself, so they don't go to lastErrors.
// `fields` may be a function, called when the edit runs (after earlier queued edits), for payloads built
// from the current state.
export function edit(op, fields, opts = {}) {
  return serial(() => mutate("/api/robot/edit", { op, ...(typeof fields === "function" ? fields() : fields) }, opts));
}
// Another robot-document write (the robot code panel's POST /api/robot/code), run in order with the edits and
// counted as this page's own change; same answer shape and opts as edit().
export function post(path, fields, opts = {}) {
  return serial(() => mutate(path, typeof fields === "function" ? fields() : fields, opts));
}
// undo(version): undo only while the robot is still at `version` (the server refuses it otherwise), for a caller
// that checked the version itself (the start picker's Cancel); without one, the version this page last saw.
export function undo(version) { return serial(() => mutate("/api/robot/undo", version === undefined ? {} : { version })); }
export function redo() { return serial(() => mutate("/api/robot/redo", {})); }

async function refreshState(force = false) {
  const { status, data } = await api("GET", "/api/robot/state");
  if (status === 200) applyState(data, force);
}

async function loadLibrary() {
  const { status, data } = await api("GET", "/api/robot/library");
  if (status === 200) { store.library = data; renderAll(); }
  else notice(`Couldn't load the parts library: ${data.errors[0].message}`, "error");
}
export { loadLibrary };

let viewSeq = 0;
async function loadView() {
  const seq = ++viewSeq;
  const { status, data } = await api("GET", "/api/robot/view");
  if (seq !== viewSeq) return;   // a newer state already asked for a newer view
  store.view = status === 200 ? data : {};
  window.dispatchEvent(new CustomEvent("scrimmage:view", { detail: { view: store.view } }));
  renderAll();
}

function applyState(s, force = false) {
  const prev = store.state;
  if (prev && s.version === prev.version && s.path === prev.path && !force) return;
  // busy: our own edit's state can arrive by the long poll before the edit's response does; a new version with the
  // same robot (a scenario opened from this page bumps it) isn't an outside edit
  const robotText = (x) => JSON.stringify([x.path, x.parts, x.other_sections]);
  if (prev && s.version !== prev.version && !ownVersions.has(s.version) && !busy && robotText(s) !== robotText(prev)) {
    notice("The robot changed outside this page (the file was edited, or another tab), so it was reloaded. "
      + "Undo history starts again from here.", "info");
    store.lastErrors = [];
  }
  store.state = s;
  const oldSelected = store.selected;
  const names = new Set(s.parts.map((p) => p.name));
  if (store.pendingSelect && names.has(store.pendingSelect)) store.selected = store.pendingSelect;
  store.pendingSelect = null;
  if (store.selected && !names.has(store.selected)) store.selected = null;
  if (!store.selected && s.parts.length) store.selected = s.parts[0].name;
  renderAll();
  window.dispatchEvent(new CustomEvent("scrimmage:state", { detail: { state: s } }));
  if (store.selected !== oldSelected) {
    window.dispatchEvent(new CustomEvent("scrimmage:selected", { detail: { name: store.selected } }));
  }
  if (!prev || s.version !== prev.version || force) loadView();
}

async function pollLoop() {
  for (;;) {
    const since = store.state ? `?since=${store.state.version}` : "";
    const { status, data } = await api("GET", "/api/robot/state" + since);
    if (status === 200) {
      setConnected(true);
      applyState(data);
    } else {
      setConnected(false, data.errors && data.errors[0] && data.errors[0].message);
      await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
    }
  }
}

let connected = null;
function setConnected(ok, why) {
  if (connected === ok) return;
  connected = ok;
  if (!ok) notice(`Lost contact with the assembler: ${why || "no answer"}. Retrying…`, "error", "conn");
  else dismiss("conn");
  renderSaved();
}

// -- selection ---------------------------------------------------------------------------------
export function select(name, render = true) {
  if (name !== null && store.state && !store.state.parts.some((p) => p.name === name)) return false;
  if (store.selected === name) return true;
  store.selected = name;
  if (render) renderAll();
  window.dispatchEvent(new CustomEvent("scrimmage:selected", { detail: { name } }));
  return true;
}

// -- notices ------------------------------------------------------------------------------------
export function notice(text, kind = "info", id = null) {
  const box = $("#notices");
  if (id) dismiss(id);
  const el = h("div", { class: `notice notice-${kind}`, role: kind === "error" ? "alert" : "status", dataset: id ? { id } : {} },
    h("span", { text }),
    h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": "Dismiss", onclick: () => el.remove() }, "×"));
  box.append(el);
  while (box.children.length > 4) box.firstChild.remove();
  if (kind === "info") setTimeout(() => el.remove(), 10000);
}
function dismiss(id) {
  for (const el of document.querySelectorAll(`#notices [data-id="${id}"]`)) el.remove();
}

// -- rendering ----------------------------------------------------------------------------------
function renderAll() {
  const s = store.state;
  if (!s) return;
  const shown = s.display_path || s.path || "";
  renderTop();
  $("#inspector-title").textContent = store.selected ? `Inspector · ${store.selected}` : "Inspector";
  const banner = $("#readonly-banner");
  banner.hidden = !s.read_only;
  banner.textContent = s.read_only ? `Read-only: ${s.read_only_reason}` : "";
  document.body.classList.toggle("read-only", !!s.read_only);
  $("#other-path").textContent = shown;
  $("#other-text").textContent = s.other_sections.trim() || "(none)";
  renderLibrary(store);
  renderTree(store);
  renderInspector(store);
  renderErrors();
  renderStatus();
}

// Every error and warning lands somewhere: beside its field when the inspector shows that field,
// otherwise in the messages list above the status bar. None is dropped.
function renderErrors() {
  const s = store.state;
  if (!s) return;
  const errors = [...s.errors, ...store.lastErrors];
  const leftover = placeErrors(store, errors, s.warnings);
  const box = $("#messages");
  box.replaceChildren();
  const add = (e, kind) => {
    const m = /^([A-Za-z0-9_]+)\/(.+)$/.exec(e.field || "");
    const inst = m && s.parts.some((p) => p.name === m[1]) ? m[1] : null;
    box.append(h("div", { class: `message message-${kind}`, role: kind === "error" ? "alert" : null },
      h("span", { class: "message-kind", text: kind === "error" ? "Error" : "Warning" }),
      inst ? h("button", { type: "button", class: "linklike", onclick: () => select(inst), text: inst }) : null,
      h("span", { text: inst ? String(e.message).replace(/^parts\[\d+\] '[^']*': /, "") : e.message })));
  };
  for (const e of leftover.errors) add(e, "error");
  for (const w of leftover.warnings) add(w, "warning");
  box.hidden = !box.children.length;
}

function renderSaved() {
  const el = $("#saved");
  if (!el) return;
  if (connected === false) { el.textContent = "Not connected"; el.className = "saved saved-bad"; return; }
  if (!store.state) return;
  if (store.state.read_only) { el.textContent = "Read-only"; el.className = "saved saved-warn"; return; }
  if (busy) { el.textContent = "Saving…"; el.className = "saved saved-busy"; return; }
  if (store.lastErrors.length) {
    el.textContent = "Last change not saved (see the error)";
    el.className = "saved saved-bad";
    return;
  }
  el.textContent = "✓ All changes saved";
  el.className = "saved saved-ok";
}

function renderStatus() {
  const s = store.state;
  renderSaved();
  $("#mass").textContent = s.mass_kg == null ? "Mass: —" : `Mass: ${s.mass_kg.toFixed(1)} kg`;
  $("#cg").textContent = cgText(s.com);
  $("#cg").title = s.com ? "The robot's center of gravity at its start pose, from the floor at the frame's center" : "";
  $("#counts").textContent = s.bodies == null ? "" : `${s.bodies} bodies · ${s.joints} joints`;
  const ne = s.errors.length + store.lastErrors.length, nw = s.warnings.length;
  const parts = [];
  if (ne) parts.push(`${ne} error${ne === 1 ? "" : "s"}`);
  if (nw) parts.push(`${nw} warning${nw === 1 ? "" : "s"}`);
  $("#problems").textContent = parts.length ? parts.join(" · ") : "No problems";
  $("#problems").className = `stat ${ne ? "stat-bad" : nw ? "stat-warn" : "stat-ok"}`;
}
export { renderErrors, renderStatus };

// "CG: 0.31 m up, 0.02 m forward" (meters, two decimals) from the state's com, with ", 0.03 m left" (or right) when
// that rounds to 0.01 m or more; "" without one. Each side comes from the rounded value: never "0.00 m back".
export function cgText(com) {
  if (!com) return "";
  const r = (v) => Number(v.toFixed(2)) || 0;     // || 0: -0 reads as 0
  const m = (v) => Math.abs(r(v)).toFixed(2);
  const side = r(com.left);
  return `CG: ${m(com.height)} m up, ${m(com.forward)} m ${r(com.forward) < 0 ? "back" : "forward"}`
    + (side ? `, ${m(com.left)} m ${side < 0 ? "right" : "left"}` : "");
}

// -- helpers shared by the library and inspector ------------------------------------------------
export function stockMeta(name) {
  return store.library ? store.library.stock.find((s) => s.name === name) || null : null;
}

function normPath(p) {
  return String(p || "").replace(/\\/g, "/").replace(/\/part\.toml$/, "").replace(/\/+$/, "").replace(/^\.\//, "");
}
export function folderMeta(path) {
  if (!store.library) return null;
  const want = normPath(path);
  return store.library.folders.find((f) => normPath(f.path) === want) || null;
}

// Mounts of an instance: from the 3D view when it has them, else from the library metadata.
export function mountsOf(part) {
  const v = store.view;
  if (v && v.mounts && v.instances && v.instances.includes(part.name)) {
    return v.mounts.filter((m) => m.name.startsWith(part.name + ".")).map((m) => m.name.slice(part.name.length + 1));
  }
  const meta = part.stock ? stockMeta(part.stock) : folderMeta(part.path);
  return meta ? [...meta.mounts] : [];
}

// [{instance, mounts: ["inst.mount", ...], bodies: ["inst.body", ...]}] for every part except `exclude` and
// what attaches to it. A body named like a mount is left out: the mount wins.
export function attachGroups(exclude = null) {
  const s = store.state;
  if (!s) return [];
  const below = exclude ? descendants(exclude) : new Set();
  return s.parts.filter((p) => p.name !== exclude && !below.has(p.name))
    .map((p) => {
      const mounts = mountsOf(p);
      const bodies = ((s.part_bodies || {})[p.name] || []).filter((b) => !mounts.includes(b));
      return { instance: p.name, mounts: mounts.map((m) => `${p.name}.${m}`), bodies: bodies.map((b) => `${p.name}.${b}`) };
    });
}

export function descendants(name) {
  const out = new Set();
  const parts = store.state ? store.state.parts : [];
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of parts) {
      const parent = p.attach ? String(p.attach).split(".")[0] : null;
      if (parent && (parent === name || out.has(parent)) && !out.has(p.name) && p.name !== name) {
        out.add(p.name); grew = true;
      }
    }
  }
  return out;
}

// Fill an attach <select>: an optional "root" choice, then one optgroup per instance with its mounts and bodies.
export function fillAttachSelect(select, groups, current, { allowRoot = false } = {}) {
  select.replaceChildren();
  if (allowRoot) select.append(h("option", { value: "" }, "Nothing (this is the robot's root part)"));
  let found = current === null || current === undefined || current === "" ? allowRoot : false;
  for (const g of groups) {
    const og = h("optgroup", { label: g.instance });
    if (!g.mounts.length && !g.bodies.length) og.append(h("option", { disabled: true, value: "" }, "(nothing to attach to)"));
    for (const [list, what] of [[g.mounts, "mount"], [g.bodies, "body"]]) {
      for (const o of list) {
        og.append(h("option", { value: o }, `${o}  (${what})`));
        if (o === current) found = true;
      }
    }
    select.append(og);
  }
  if (current && !found) select.append(h("option", { value: current }, current));
  select.value = current || (allowRoot ? "" : select.querySelector("option:not([disabled])")?.value || "");
}

export function fmt(v) {
  if (typeof v !== "number" || !isFinite(v)) return v === null || v === undefined ? "" : String(v);
  return String(+v.toPrecision(10));
}

// A Grip picker's entry for a pad with no grip set (the Part and Test tabs; scrimmage/blocks/run.py DEFAULT_GRIP_NOTE)
export const DEFAULT_GRIP = "Default — 1.0 (MuJoCo's default; the larger of pad and piece applies)";
export const UNIT = { "kg*m^2": "kg·m²", deg: "°", "N*m/rad": "N·m/rad" };

// -- settings menu ------------------------------------------------------------------------------
// The header's Settings menu: one row per setting, each a group of [data-setting][data-value] buttons. A change
// applies at once here and, through units.js's poll, in every other open editor.
const SETTINGS = { units: { now: () => units.system, set: (v) => units.set(v), onChange: units.onChange } };

function initSettings() {
  const btn = $("#settings-btn"), menu = $("#settings-menu");
  const show = (open) => { menu.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
  btn.addEventListener("click", () => show(menu.hidden));
  document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest(".settings-wrap")) show(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { show(false); btn.focus(); }
  });
  for (const b of menu.querySelectorAll("[data-setting]")) {
    b.addEventListener("click", async () => {
      const why = await SETTINGS[b.dataset.setting].set(b.dataset.value);
      if (why) notice(`Couldn't change the setting: ${why}`, "error");
      renderSettings();
    });
  }
  for (const s of Object.values(SETTINGS)) s.onChange(renderSettings);
  renderSettings();
}

function renderSettings() {
  for (const b of document.querySelectorAll("#settings-menu [data-setting]")) {
    b.setAttribute("aria-pressed", String(SETTINGS[b.dataset.setting].now() === b.dataset.value));
  }
}

// -- boot ---------------------------------------------------------------------------------------
function onKey(e) {
  const mod = e.ctrlKey || e.metaKey;
  if (!mod || e.altKey || e.defaultPrevented) return;   // defaultPrevented: the block editor did it itself
  const k = e.key.toLowerCase();
  const t = e.target;
  const typing = t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !["checkbox", "button", "radio"].includes(t.type)));
  if (typing || document.querySelector("dialog[open]")) return;   // let text boxes keep their own undo
  if (startShown) return;   // the start page edits nothing: undo would act on a document out of sight
  if (k === "z" && !e.shiftKey) { e.preventDefault(); tabUndo(); }
  else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); tabRedo(); }
}

// -- start page and modes ------------------------------------------------------------------------
// The server holds one thing at a time: "robot" (today's tabs), "bench" (an idea session), "part" (a CAD import or a
// plugin part folder: the Part tab), "measure" (a measurement file: the Measure tab) or "start" (nothing; only the
// start page). The "Start page" link shows the start page client-side; the server's mode only changes when
// something is opened or made there (the page then reloads).
let startShown = false;
// The start page from the Robot tab's "Import CAD…": straight to "New part from CAD", the part going in parts/ next to
// the robot config `cadFor`.
export function importCad(cadFor) { return showStartPage(true, { cadFor }); }
async function showStartPage(canGoBack, { cadFor = null } = {}) {
  startShown = true;
  document.body.classList.add("on-start");
  $("#start-page").replaceChildren();   // not the last visit's answers (the team folders may have changed since)
  $("#start-page").hidden = false;
  $("#start-link").hidden = true;
  $("#file-path").replaceChildren(h("strong", { text: "Start" }));
  try {
    const m = await import("./start.js");
    await m.showStart($("#start-page"), { canGoBack, onBack: hideStartPage, cadFor });
  } catch (e) {
    console.error(e);
    notice(`The start page failed to load: ${e.message || e}`, "error");
  }
}
function hideStartPage() {
  startShown = false;
  document.body.classList.remove("on-start");
  $("#start-page").hidden = true;
  $("#start-link").hidden = false;
  renderTop();
}

function bootBench() {
  // the Bench tab shows at once; bench.js, loaded on its own so a failure there reports itself instead of a blank
  // page, registers its real hooks in place of these
  registerTab("bench", { renderTop() {}, undo() {}, redo() {}, onShow() {} });
  setTab("bench");
  import("./bench.js").then((m) => m.initBench()).catch((e) => {
    console.error(e);
    notice(`The Bench tab failed to load: ${e.message || e}`, "error");
  });
}

function bootPart() {
  // like the Bench tab: shown at once, part.js registers its real hooks when it has loaded
  registerTab("part", { renderTop() {}, undo() {}, redo() {}, onShow() {} });
  setTab("part");
  import("./part.js").then((m) => m.initPart()).catch((e) => {
    console.error(e);
    notice(`The Part tab failed to load: ${e.message || e}`, "error");
  });
}

function bootTest() {
  // like the Bench tab: shown at once, test.js registers its real hooks when it has loaded
  registerTab("test", { renderTop() {}, undo() {}, redo() {}, onShow() {} });
  import("./test.js").then((m) => m.initTest()).catch((e) => {
    console.error(e);
    notice(`The Test tab failed to load: ${e.message || e}`, "error");
  });
}

function bootMeasure() {
  // like the Bench tab: shown at once, measure.js registers its real hooks when it has loaded
  registerTab("measure", { renderTop() {}, undo() {}, redo() {}, onShow() {} });
  setTab("measure");
  import("./measure.js").then((m) => m.initMeasure()).catch((e) => {
    console.error(e);
    notice(`The Measure tab failed to load: ${e.message || e}`, "error");
  });
}

function bootRobot() {
  initLibrary();
  import("./view3d.js").then((m) => m.initView()).catch((e) => {
    console.error(e);
    $("#view").replaceChildren(h("p", { class: "view-placeholder" }, `The 3D view failed to load: ${e.message || e}`));
  });
  loadLibrary();
  pollLoop();
  // the Scenario tab: loaded on its own (like the 3D view) so a failure there can't break the Robot tab
  import("./scenario.js").then((m) => m.initScenario()).catch((e) => {
    console.error(e);
    notice(`The Scenario tab failed to load: ${e.message || e}`, "error");
  });
  // the robot code panel (in the Robot tab, beside the 3D view), likewise
  import("./code.js").then((m) => m.initCode()).catch((e) => {
    console.error(e);
    notice(`The robot code panel failed to load: ${e.message || e}`, "error");
  });
  // the Drive tab, likewise
  import("./drive.js").then((m) => m.initDrive()).catch((e) => {
    console.error(e);
    notice(`The Drive tab failed to load: ${e.message || e}`, "error");
  });
}

// The part was opened from a robot (the library's Import CAD…): "Back to the robot" opens that robot config again.
function backButton(back) {
  const btn = $("#back-robot");
  btn.hidden = false;
  btn.title = `Open ${back.display_path} again`;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const { status, data } = await api("POST", "/api/app/open", { path: back.path });
    if (status === 200 && data.ok) {
      location.hash = `token=${encodeURIComponent(token)}`;   // the robot opens on its own first tab
      location.reload();
      return;
    }
    btn.disabled = false;
    const why = (data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`;
    notice(status === 409 ? `Can't switch right now: ${why}` : why, "error");
  });
}

async function boot() {
  window.__scrimmage = {
    mode: null,
    get state() { return store.state; },
    get lastErrors() { return store.lastErrors; },
    get selected() { return store.selected; },
    get view() { return store.view; },
    select: (name) => select(name),
    store,
    get tab() { return tab; },
    setTab: (name) => setTab(name),
    render: () => renderAll(),      // tests: a redraw (as a state arriving does) at a moment they choose
  };
  if (!token) {
    notice("This page needs the link scrimmage edit printed (it carries a #token=…). Open that link.", "error");
  }
  initTabs();
  $("#start-link").addEventListener("click", () => showStartPage(true));
  $("#undo").addEventListener("click", () => tabUndo());
  $("#redo").addEventListener("click", () => tabRedo());
  document.addEventListener("keydown", onKey);
  // the unit table before any tab renders a value (units.js); without it, values show in SI
  const [{ status, data }] = await Promise.all([api("GET", "/api/app/state"), units.load()]);
  initSettings();
  units.watch();
  if (units.warning) notice(units.warning, "warn");
  const mode = status === 200 ? data.mode : "robot";   // without an answer, behave as before (it reports its own errors)
  window.__scrimmage.mode = mode;
  store.appPath = data.display_path || data.path || "";
  if (mode === "start") {
    showStartPage(false);
  } else if (mode === "bench") {
    $("#tab-btn-robot").hidden = true;
    $("#tab-btn-drive").hidden = true;
    $("#start-link").hidden = false;
    bootBench();
  } else if (mode === "part") {
    $("#tab-btn-robot").hidden = true;
    $("#tab-btn-drive").hidden = true;
    $("#start-link").hidden = false;
    if (data.back) backButton(data.back);
    bootPart();
  } else if (mode === "measure") {
    $("#tab-btn-robot").hidden = true;
    $("#tab-btn-drive").hidden = true;
    $("#start-link").hidden = false;
    bootMeasure();
  } else {
    $("#start-link").hidden = false;
    bootRobot();
    if (data.test) bootTest();   // a test file is open: its robot is the robot document
    // the robot (or scenario) names a team folder this computer hasn't set: the start page asks where it is
    if (data.needs_folder) showStartPage(true);
  }
}

boot();
