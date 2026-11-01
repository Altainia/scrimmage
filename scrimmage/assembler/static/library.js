// The parts library (left column) and the "Add part" dialog.

import { $, h, api, edit, store, attachGroups, fillAttachSelect, importCad, loadLibrary, notice, renderErrors, renderStatus, stockMeta } from "./app.js";

const GROUP_ORDER = ["Chassis", "Drivetrain", "Mechanisms"];
let rendered = { library: null, readOnly: null };
let adding = null;          // {source: {stock} or {path}, title} while the dialog is open
let picking = false;
let dialogErrors = null;  // the lastErrors array the dialog is showing

export function renderLibrary(st) {
  const lib = st.library;
  const ro = !!(st.state && st.state.read_only);
  if (rendered.library === lib && rendered.readOnly === ro) return;
  rendered = { library: lib, readOnly: ro };
  const root = $("#library");
  if (!lib) { root.replaceChildren(h("p", { class: "empty" }, "Loading parts…")); return; }

  const groups = new Map(GROUP_ORDER.map((g) => [g, []]));
  for (const s of lib.stock) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  const out = [];
  for (const [g, items] of groups) {
    if (!items.length) continue;
    out.push(h("section", { class: "lib-group" },
      h("h3", { class: "lib-group-title" }, g),
      h("ul", { class: "lib-list" }, items.map((s) => libItem({
        title: s.name, kind: "stock", doc: s.doc, note: s.note,
        meta: [Object.keys(s.slots).length ? `Slots: ${Object.entries(s.slots).map(([k, v]) => `${k} (${v})`).join(", ")}` : "No device slots",
          s.mounts.length ? `Mounts: ${s.mounts.join(", ")}` : "No mounts"],
        source: { stock: s.name }, ro,
      })))));
  }
  const folders = lib.folders || [];
  out.push(h("section", { class: "lib-group" },
    h("h3", { class: "lib-group-title" }, "Part folders"),
    folders.length
      ? h("ul", { class: "lib-list" }, folders.map((f) => libItem({
        title: f.name, kind: "folder", doc: f.path, error: f.error,
        meta: f.error ? [] : [Object.keys(f.slots).length ? `Slots: ${Object.entries(f.slots).map(([k, v]) => `${k} (${v})`).join(", ")}` : "No device slots",
          f.mounts.length ? `Mounts: ${f.mounts.join(", ")}` : "No mounts"],
        source: { path: f.path }, cad: f.cad || null, ro,
      })))
      : h("p", { class: "empty empty-small" }, "No part folders found next to this config (folders holding a part.toml)."),
    h("div", { class: "lib-import" },
      h("button", { type: "button", class: "btn btn-small", id: "lib-import-cad", disabled: ro || !(st.state && st.state.path),
        title: ro ? "This config is read-only" : "Make part folders from a CAD file", onclick: () => importCad(st.state.path) }, "Import CAD…"),
      h("p", { class: "hint" }, "Make part folders from a STEP, STL or OBJ file. They go in the parts folder next to this config.")),
    folderForm(ro)));
  root.replaceChildren(...out);
}

function libItem({ title, kind, doc, note, meta, error, source, cad = null, ro }) {
  const label = kind === "folder" ? `${title} (folder)` : title;
  return h("li", { class: "lib-item" },
    h("div", { class: "lib-item-head" },
      h("span", { class: "lib-name", text: title }),
      h("button", {
        type: "button", class: "btn btn-small btn-primary-soft", disabled: ro || !!error,
        "aria-label": `Add ${label}`, title: error ? "This folder's part.toml has an error" : `Add a ${title}`,
        onclick: () => openAdd(source, title, cad),
      }, "Add")),
    doc ? h("p", { class: "lib-doc", text: doc }) : null,
    error ? h("p", { class: "error", text: error }) : null,
    meta.length || note ? h("details", { class: "lib-more" },
      h("summary", {}, "Details"),
      note ? h("p", { text: note }) : null,
      meta.map((m) => h("p", { class: "muted", text: m }))) : null);
}

function folderForm(ro) {
  const input = h("input", { id: "folder-path", placeholder: "../parts or /path/to/parts", autocomplete: "off", spellcheck: "false", disabled: ro });
  const err = h("p", { class: "error", hidden: true });
  const form = h("form", {
    class: "folder-form", onsubmit: async (e) => {
      e.preventDefault();
      err.hidden = true;
      const path = input.value.trim();
      if (!path) { err.textContent = "Type a folder path first."; err.hidden = false; return; }
      const { status, data } = await api("POST", "/api/robot/folder", { path });
      if (status === 200 && data.ok) { notice(`Added part folder ${path}`, "info"); await loadLibrary(); }
      else { err.textContent = data.errors.map((x) => x.message).join("; "); err.hidden = false; }
    },
  },
  h("label", { for: "folder-path" }, "Add folder…"),
  h("div", { class: "row" }, input, h("button", { type: "submit", class: "btn btn-small", disabled: ro }, "Add folder")),
  h("p", { class: "hint" }, "A folder of part folders, or one part folder. Relative to the config file; remembered for this session."),
  err);
  return form;
}

// -- Add dialog ---------------------------------------------------------------------------------
export function uniqueName(base) {
  base = String(base).split("/").pop().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "part";
  if (/^[0-9]/.test(base)) base = "p" + base;
  const taken = new Set((store.state ? store.state.parts : []).map((p) => p.name));
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`;
}

// The stock chassis's root body ("drive.chassis", the name from the library): a part made from a CAD file sits where
// the CAD has it there.
function chassisRoot(groups) {
  const meta = stockMeta("chassis");
  const parts = store.state ? store.state.parts : [];
  for (const p of [...parts.filter((x) => !x.attach), ...parts]) {
    const g = meta && p.stock === "chassis" && groups.find((x) => x.instance === p.name);
    const body = g && g.bodies.find((b) => b === `${p.name}.${meta.root_body}`);
    if (body) return body;
  }
  return null;
}

// What the CAD hangs a part from an import on ({import_id, attach: "<part>.<body>"}), as an attach target in this
// robot: "<instance>.<body>" when a part from the same import with that name is in it, else null.
function cadTarget(groups, cad) {
  if (!cad || !cad.attach) return null;
  const [part, body] = cad.attach.split(".");
  for (const p of store.state ? store.state.parts : []) {
    if (!p.cad || p.cad.import_id !== cad.import_id || p.cad.part !== part) continue;
    const g = groups.find((x) => x.instance === p.name);
    if (g && g.bodies.includes(`${p.name}.${body}`)) return `${p.name}.${body}`;
  }
  return null;
}

function defaultAttach(groups, cad) {
  if (cad && (cadTarget(groups, cad) || chassisRoot(groups))) return cadTarget(groups, cad) || chassisRoot(groups);
  const root = store.state && store.state.parts.find((p) => !p.attach);
  const g = groups.find((x) => root && x.instance === root.name) || groups[0];
  if (!g) return "";
  return g.mounts.find((o) => o.endsWith(".top")) || g.mounts[0] || g.bodies[0] || "";
}

function openAdd(source, title, cad = null) {
  if (!store.state || store.state.read_only) return;
  adding = { source, title };
  const dlg = $("#add-dialog");
  $("#add-source").textContent = source.stock ? `Stock part: ${source.stock}` : `Part folder: ${source.path}`;
  $("#add-title").textContent = `Add ${title}`;
  $("#add-name").value = uniqueName(title);
  const groups = attachGroups();
  const sel = $("#add-attach");
  if (groups.length) {
    fillAttachSelect(sel, groups, defaultAttach(groups, cad));
    sel.disabled = false;
  } else {
    sel.replaceChildren(h("option", { value: "" }, "Nothing: this will be the robot's root part"));
    sel.disabled = true;
  }
  $("#add-cad-hint").hidden = !cad;
  const pick = $("#add-pick");
  pick.disabled = !groups.length || !store.view3d;
  pick.title = !store.view3d ? "The 3D view isn't available" : !groups.length ? "Nothing to attach to yet" : "Click a mount in the 3D view";
  clearDialogErrors();
  if (!dlg.open) dlg.showModal();
  $("#add-name").focus();
  $("#add-name").select();
}

function clearDialogErrors() {
  for (const el of document.querySelectorAll("#add-dialog [data-err]")) { el.hidden = true; el.textContent = ""; }
  for (const el of document.querySelectorAll("#add-dialog .invalid")) el.classList.remove("invalid");
  const list = $("#add-errors");
  list.replaceChildren(); list.hidden = true;
}

function showDialogErrors(errors, name) {
  clearDialogErrors();
  const list = $("#add-errors");
  for (const e of errors) {
    const f = e.field || "";
    const cut = f.lastIndexOf("/");
    const m = cut > 0 ? [f, f.slice(0, cut), f.slice(cut + 1)] : null;   // the name may be invalid, even contain "/"
    const key = m && m[1] === name ? m[2] : null;
    const slot = key && $(`#add-dialog [data-err="${key}"]`);
    if (slot) {
      slot.textContent = slot.hidden ? e.message : `${slot.textContent}; ${e.message}`;
      slot.hidden = false;
      (key === "name" ? $("#add-name") : $("#add-attach")).classList.add("invalid");
    } else {
      list.append(h("li", { text: m ? `${m[1]}: ${e.message}` : e.message }));
    }
  }
  list.hidden = !list.children.length;
}

function attachValue() {
  const sel = $("#add-attach");
  if (sel.disabled) return null;
  return sel.value || null;
}

async function submitAdd(e) {
  e.preventDefault();
  if (!adding) return;
  const name = $("#add-name").value.trim();
  const fields = { name, ...adding.source };
  const attach = attachValue();
  if (attach) fields.attach = attach;
  if (!$("#add-attach").disabled && !attach) {
    showDialogErrors([{ field: `${name}/attach`, message: "Choose what this part attaches to." }], name);
    return;
  }
  $("#add-ok").disabled = true;
  store.pendingSelect = name;
  const r = await edit("add", fields, { ownErrors: true });
  $("#add-ok").disabled = false;
  if (r.ok) { adding = null; $("#add-dialog").close(); }
  else if (r.conflict) { adding = null; $("#add-dialog").close(); }
  else {
    dialogErrors = store.lastErrors;
    showDialogErrors(r.errors, name);
  }
}

function setPicking(on) {
  picking = on;
  $("#pick-banner").hidden = !on;
  window.dispatchEvent(new CustomEvent("scrimmage:pick-mount", { detail: { active: on } }));
}

export function initLibrary() {
  const dlg = $("#add-dialog");
  $("#add-form").addEventListener("submit", submitAdd);
  $("#add-cancel").addEventListener("click", () => { adding = null; dlg.close(); });
  dlg.addEventListener("close", () => {
    if (!picking) adding = null;
    if (dialogErrors && store.lastErrors === dialogErrors) {   // shown in the dialog; nothing was added
      store.lastErrors = [];
      renderErrors(); renderStatus();
    }
  });
  $("#add-pick").addEventListener("click", () => {
    if (!adding) return;
    setPicking(true);
    dlg.close();
  });
  $("#pick-cancel").addEventListener("click", () => {
    setPicking(false);
    if (adding) dlg.showModal();
  });
  document.addEventListener("keydown", (e) => {
    if (picking && e.key === "Escape") { setPicking(false); if (adding) dlg.showModal(); }
  });
  // the config went read-only or invalid while picking: the mounts shown may no longer be valid targets
  window.addEventListener("scrimmage:state", (e) => {
    const st = e.detail && e.detail.state;
    if (picking && st && (st.read_only || (st.errors && st.errors.length))) { adding = null; setPicking(false); }
  });
  window.addEventListener("scrimmage:mount-picked", (e) => {
    if (!picking || !adding) return;
    setPicking(false);
    const name = e.detail && e.detail.name;
    const sel = $("#add-attach");
    if (name && ![...sel.options].some((o) => o.value === name)) {
      sel.append(h("option", { value: name }, name));
    }
    if (name) sel.value = name;
    dlg.showModal();
  });
}
