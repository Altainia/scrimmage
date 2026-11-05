// The Bench tab's shape editor ("Edit shape" on the stage): the idea in 3D, an inspector for one entry, and Add
// buttons. Loaded on its own by bench.js (a failure here leaves the stage working). See /api/bench/* in the docstring
// of scrimmage/assembler/server.py for the API contract.
//
//   View        /api/bench/view of the idea in three.js (as the Robot tab's view3d.js draws a robot): orbit, pan and
//               zoom; inlets and outlets as translucent boxes; Frame fits the bench's bounds. Hover highlights a
//               shape, a click selects its entry.
//   Entries     every entry of the idea (the shapes, and the shafts, piece and run settings that have no shape), so
//               each can be selected without the view.
//   Inspector   the selected entry: every key with its help, unit and choices (bench.js fieldRow, with number boxes
//               and - + for points, sizes and angles: 0.5 in or 10 mm, and 5 degree steps), Rename and Delete. An entry from an
//               included file is read-only, with "from <file>" and Edit here instead (override).
//   Add         wheel, plate, bar, hood (curve), inlet, outlet, CAD part (a part folder or a mesh file) and shaft.
//
// While the editor is open bench.editing pins the idea (the stage's own following of the server's stage waits), so
// the top bar's Undo and Redo (and Ctrl+Z) act on it. Test hooks: window.__scrimmageBench.select(instance) and
// window.__scrimmageBench.editor {open, selected, hovered, instances, boxes, screenPos(instance), posOf(instance)}.

import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { $, h, api, notice } from "./app.js";
import { meshGeometry, primitive, setPose, geomMaterial } from "./scene3d.js";
import {
  bench, stageHooks, onBench, render, fieldRow, entries, keysOf, dotted, included, entryOf, shownIdea,
  editOp, redraw, keepTyping, restoreTyping,
} from "./bench.js";
import { browseList } from "./start.js";

const SINGLE = ["bench", "piece", "run", "flight"];
const ADD = [   // [section, button label]
  ["wheel", "Wheel"], ["plate", "Plate"], ["bar", "Bar"], ["curve", "Hood (curve)"], ["inlet", "Inlet"],
  ["outlet", "Outlet"], ["cad", "CAD part"], ["shaft", "Shaft"],
];
const HOVER = new THREE.Color(0x5b8fd6);
const SELECTED = new THREE.Color(0x1d6fd6);
const OUTLINE = new THREE.LineBasicMaterial({ color: 0x1d6fd6, depthTest: false, transparent: true, opacity: 0.95 });
const BOX_COLOR = { inlet: 0x2f9e44, outlet: 0xe8590c };
const CLICK_SLOP = 5;

const ed = {
  open: false,
  selected: null,      // the selected entry's key ("bar.kicker", "shaft.top", "piece")
  fallback: null,      // the selection to go back to when an add or rename that selected its result is refused
  busy: 0,             // adds and renames in flight: a redraw before their result is in doesn't drop the selection
  hovered: null,
  view: null,          // /api/bench/view of the idea ({} while it has errors)
  viewKey: null,       // what the view was fetched for
  viewSeq: 0,
  framedFor: null,     // the file the camera was last fitted to
};
let renderer = null, scene, camera, controls, group, tip, empty;
let built = { geoms: [], boxes: [] };
let disposables = [];
let pending = false, down = null;
let selBox = null;   // a box around the selected shapes, in the accent color (smooth shapes show few edges)

// -- opening and closing --------------------------------------------------------------------------------------------
function openEditor(file) {
  bench.editing = file;
  bench.pick = file;
  ed.open = true;
  ed.selected = null;
  ed.hovered = null;
  ed.framedFor = null;
  $(".bench-layout").hidden = true;
  $(".bench-foot").hidden = true;
  $("#bench-editor").hidden = false;
  if (renderer === null) initView();
  else resize();
  render();
  $("#bench-editor-name").focus();
}

function closeEditor() {
  ed.open = false;
  bench.editing = null;
  setHover(null);
  $("#bench-editor").hidden = true;
  $(".bench-layout").hidden = false;
  $(".bench-foot").hidden = false;
  render();
}

function select(key) {
  ed.selected = key;
  ed.fallback = null;
  if (bench.errors.list.length) bench.errors = { file: null, list: [] };
  renderEditor();
  updateHighlight();
}

// -- the panel --------------------------------------------------------------------------------------------------------
function entryKey(section, name) { return name === null ? section : `${section}.${name}`; }
function splitEntry(key) {
  const i = key.indexOf(".");
  return i < 0 ? { section: key, name: null } : { section: key.slice(0, i), name: key.slice(i + 1) };
}
const exists = (key) => {
  const { section, name } = splitEntry(key);
  return entries().some((e) => e.section === section && e.name === name);
};
const own = (section, name) => !!entryOf(bench.idea && bench.idea.own, section, name);

function renderEditor() {
  if (!ed.open) return;
  if (!bench.editing || bench.shown !== bench.editing) { closeEditor(); return; }
  const i = shownIdea();
  $("#bench-editor-name").textContent = i ? `Editing ${i.name}` : "Editing";
  if (!bench.idea || !bench.fields) {
    $("#bench-inspector-body").replaceChildren(h("p", { class: "hint", text: "Loading…" }));
    return;
  }
  if (ed.selected && !exists(ed.selected) && !ed.busy) ed.selected = ed.fallback && exists(ed.fallback) ? ed.fallback : null;
  const readOnly = !!(i && i.read_only_reason);
  for (const b of document.querySelectorAll("#bench-add button")) b.disabled = readOnly;
  renderEntries(i);
  redraw(() => {
    const keep = keepTyping("#bench-inspector");
    drawInspector(i, readOnly);
    restoreTyping(keep, "#bench-inspector");
  });
  loadView();
}

function renderEntries(i) {
  const probs = i ? i.problems : [];
  const list = entries().map(({ section, name }) => {
    const key = entryKey(section, name);
    const bad = probs.some((p) => p.level === "error" && p.key && (p.key === key || p.key.startsWith(`${key}.`)));
    const inc = included(section, name);
    return h("li", {}, h("button", { type: "button", class: `bench-entry${key === ed.selected ? " selected" : ""}`,
      dataset: { entry: key }, "aria-pressed": String(key === ed.selected), onclick: () => select(key) },
      h("span", { class: "bench-entry-kind", text: section === "curve" ? "hood" : section }),
      h("strong", { text: name === null ? "" : name }),
      inc ? h("span", { class: "bench-entry-from", text: `from ${inc}` }) : null,
      bad ? h("span", { class: "dot dot-error", title: "has problems" }) : null));
  });
  $("#bench-entries").replaceChildren(...list);
}

function drawInspector(i, readOnly) {
  const body = $("#bench-inspector-body");
  const title = $("#bench-inspector-title");
  if (!ed.selected || !exists(ed.selected)) {
    title.textContent = ed.selected ? "Saving…" : "Nothing selected";
    body.replaceChildren(h("p", { class: "hint bench-empty", text: "Click a shape in the view, or a part in the list, to change it." }));
    return;
  }
  const { section, name } = splitEntry(ed.selected);
  const key = ed.selected;
  const inc = included(section, name);
  // "kicker — bar", "hood — curve" (a curve not named for the hood: "guide — hood (curve)")
  const kind = section !== "curve" ? section : /hood/i.test(name || "") ? "curve" : "hood (curve)";
  title.textContent = name === null ? section : `${name} — ${kind}`;
  const head = [];
  const msgs = [];
  for (const p of i ? i.problems : []) if (p.key === key) msgs.push(h("p", { class: `field-msg ${p.level === "error" ? "error" : "warning"}`, text: p.message }));
  if (bench.errors.file === bench.shown) {
    for (const e of bench.errors.list) if (e.field === key) msgs.push(h("p", { class: "field-msg error", role: "alert", text: e.message }));
  }
  if (inc) {
    head.push(h("div", { class: "bench-from-row" },
      h("span", { class: "bench-from", text: `from ${inc}` }),
      h("button", { type: "button", class: "btn btn-primary-soft", id: "bench-override", disabled: readOnly,
        title: `Copy it into ${bench.shown}, so this idea's own copy can be changed (${inc} stays as it is)`,
        onclick: () => override(section, name) }, "Edit here instead")),
    h("p", { class: "hint", text: `It comes from ${inc}, so it can't be changed here. Edit here instead copies it into this idea's file.` }));
  }
  if (!SINGLE.includes(section)) {
    // keyed like a field box, so half-typed text survives a redraw for this file and entry only
    const nameBox = h("input", { id: "bench-rename-name", type: "text", value: name, autocomplete: "off", spellcheck: "false",
      disabled: readOnly || !!inc, "aria-label": `new name for ${section} ${name}`,
      dataset: { key: `${key}#rename`, file: bench.shown, orig: name, version: String(bench.state.version) } });
    nameBox.value = name;
    const go = () => rename(section, name, nameBox.value.trim());
    nameBox.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
    head.push(h("div", { class: "row bench-insp-actions" },
      nameBox,
      h("button", { type: "button", class: "btn", id: "bench-rename", disabled: readOnly || !!inc, onclick: go }, "Rename"),
      h("button", { type: "button", class: "btn btn-danger", id: "bench-delete", disabled: readOnly || !!inc || !own(section, name),
        onclick: () => remove(section, name) }, "Delete")));
  }
  const keys = keysOf(section).filter((k) => k !== "name" || section === "bench");
  body.replaceChildren(...head, ...msgs,
    h("div", { class: "bench-fields" }, ...keys.map((k) => fieldRow(dotted(section, name, k), { prefix: "e", vectors: true }))));
}

// -- edits -----------------------------------------------------------------------------------------------------------
function unique(section) {
  const merged = (bench.idea && bench.idea.merged) || {};
  const names = new Set(section === "shaft" ? Object.keys(merged.shaft || {}) : (merged[section] || []).map((e) => e && e.name));
  let n = 1;
  while (names.has(`${section}${n}`)) n++;
  return `${section}${n}`;
}

async function add(section, fields = null) {
  const name = unique(section);
  const key = `${section}.${name}`;
  ed.fallback = ed.selected;
  ed.selected = key;
  ed.busy++;
  let ok;
  try { ok = await editOp({ op: "add", section, name, ...(fields ? { fields } : {}) }, key); } finally { ed.busy--; }
  if (!ok) {
    const why = bench.errors.list.map((e) => e.message).join("; ");
    if (why) notice(`Can't add the ${section}: ${why}`, "error", "bench-add");
    if (ed.selected === key) ed.selected = ed.fallback && exists(ed.fallback) ? ed.fallback : null;
    renderEditor();
  }
  ed.fallback = null;
  updateHighlight();
  return ok;
}

async function remove(section, name) {
  const ok = await editOp({ op: "remove", section, name }, `${section}.${name}`);
  if (ok) { ed.selected = null; renderEditor(); updateHighlight(); }
}

async function rename(section, name, newName) {
  if (!newName || newName === name) return;
  const key = `${section}.${name}`;
  ed.fallback = key;
  ed.selected = `${section}.${newName}`;
  ed.busy++;
  let ok;
  try { ok = await editOp({ op: "rename", section, name, new_name: newName }, key); } finally { ed.busy--; }
  if (!ok && ed.selected === `${section}.${newName}`) { ed.selected = key; renderEditor(); }
  ed.fallback = null;
  updateHighlight();
}

function override(section, name) {
  return editOp(name === null ? { op: "override", section } : { op: "override", section, name }, entryKey(section, name));
}

// -- the CAD part dialog: a part folder (browsed) or a mesh file (typed: the browse list shows folders) ---------------
let cadFolder = null;
function relPath(abs) {
  const base = String(bench.state.folder).replace(/\/+$/, "").split("/");
  const to = String(abs).replace(/\/+$/, "").split("/");
  let k = 0;
  while (k < base.length && k < to.length && base[k] === to[k]) k++;
  const rel = [...base.slice(k).map(() => ".."), ...to.slice(k)].join("/");
  return rel || ".";
}

function openCad() {
  cadFolder = null;
  $("#bench-cad-mesh").value = "";
  $("#bench-cad-chosen").textContent = "No part folder chosen.";
  $("#bench-cad-units").replaceChildren(...((bench.fields && bench.fields.choices.units) || ["m", "mm", "in"]).map((u) => h("option", { value: u }, u)));
  $("#bench-cad-units").value = "mm";
  $("#bench-cad-errors").hidden = true;
  $("#bench-cad-dialog").showModal();
  browseList($("#bench-cad-browse"), bench.state.folder, { noticeId: "bench-cad",
    folderButton: { key: "part", label: "Use this part", title: "Use this folder (from the FreeCAD exporter) as the part",
      onclick: (e) => { cadFolder = e.path; $("#bench-cad-mesh").value = ""; $("#bench-cad-chosen").textContent = `Part folder: ${relPath(e.path)}`; } } });
}

async function addCad(e) {
  e.preventDefault();
  const mesh = $("#bench-cad-mesh").value.trim();
  const errs = $("#bench-cad-errors");
  let fields;
  if (mesh) fields = { mesh, units: $("#bench-cad-units").value };
  else if (cadFolder) fields = { part: relPath(cadFolder) };
  else {
    errs.replaceChildren(h("li", { text: "Choose a part folder, or type the path of a mesh file." }));
    errs.hidden = false;
    return;
  }
  $("#bench-cad-dialog").close();
  await add("cad", fields);
}

// -- the 3D view (as view3d.js) ---------------------------------------------------------------------------------------
function initView() {
  const host = $("#bench-editor-view");
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch {
    renderer = false;
    host.replaceChildren(h("p", { class: "view-placeholder" },
      "The 3D view needs WebGL, which this browser didn't provide. The list of parts and the fields still work."));
    return;
  }
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = "view-canvas";
  renderer.domElement.setAttribute("role", "img");
  renderer.domElement.setAttribute("aria-label", "3D view of the idea. Drag to orbit, right-drag to pan, scroll to zoom; click a shape to change it.");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0xe9ecf0);
  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.0);
  hemi.position.set(0, 0, 1);
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(2, -3, 5);
  scene.add(hemi, sun, sun.target);
  camera = new THREE.PerspectiveCamera(40, 1, 0.01, 200);
  camera.up.set(0, 0, 1);   // MuJoCo's +z is up; before OrbitControls, which reads camera.up once
  camera.position.set(0.5, -2, 0.6);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.addEventListener("change", requestRender);
  group = new THREE.Group();
  scene.add(group);
  scene.add(new THREE.AxesHelper(0.1));   // the bench origin; +x is the way pieces go

  tip = h("div", { class: "view-tip", hidden: true });
  empty = h("p", { class: "view-placeholder", id: "bench-editor-empty", hidden: true });
  const tools = h("div", { class: "view-tools" },
    h("button", { type: "button", class: "btn", id: "bench-edit-zoom", title: "Zoom in on the selected shape", onclick: () => frame(ed.selected) }, "Zoom to selected"),
    h("button", { type: "button", class: "btn", id: "bench-edit-frame", title: "Fit the whole bench in the view", onclick: () => frame() }, "Frame"));
  host.replaceChildren(renderer.domElement, tools, tip, empty);
  new ResizeObserver(resize).observe(host);
  resize();
  const cv = renderer.domElement;
  cv.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    host.replaceChildren(h("p", { class: "view-placeholder", role: "alert" }, "The 3D view lost its graphics context — reload the page. The fields still work."));
  });
  cv.addEventListener("pointermove", onMove);
  cv.addEventListener("pointerleave", () => setHover(null));
  cv.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, button: e.button }; });
  cv.addEventListener("pointerup", onUp);
}

async function loadView() {
  const file = bench.editing;
  const i = shownIdea();
  const key = JSON.stringify([file, bench.idea && bench.idea.merged, i && i.problems]);
  if (!file || key === ed.viewKey) return;
  ed.viewKey = key;
  const seq = ++ed.viewSeq;
  const { status, data } = await api("GET", `/api/bench/view?file=${encodeURIComponent(file)}`);
  if (seq !== ed.viewSeq || !ed.open || bench.editing !== file) return;
  ed.view = status === 200 ? data : {};
  build(ed.view);
  if (ed.framedFor !== file && built.geoms.length + built.boxes.length) { frame(SHAPES); ed.framedFor = file; }
}

function clear() {
  for (const d of disposables) d.dispose();
  disposables = [];
  if (group) group.clear();
  built = { geoms: [], boxes: [] };
}

function build(view) {
  clear();
  view = view || {};
  if (renderer) {
    const meshGeoms = (view.meshes || []).map(() => null);
    for (const gd of view.geoms || []) {
      if (gd.rgba && gd.rgba[3] <= 0) continue;
      let geo;
      if (gd.type === "mesh") {
        const m = view.meshes && view.meshes[gd.mesh];
        if (!m) continue;
        if (!meshGeoms[gd.mesh]) { meshGeoms[gd.mesh] = meshGeometry(m); disposables.push(meshGeoms[gd.mesh]); }
        geo = meshGeoms[gd.mesh];
      } else {
        geo = primitive(gd.type, gd.size);
        if (!geo) continue;
        disposables.push(geo);
      }
      const mat = geomMaterial(gd.rgba, gd.type === "mesh");
      disposables.push(mat);
      const mesh = new THREE.Mesh(geo, mat);
      setPose(mesh, gd.pos, gd.quat);
      mesh.userData.instance = gd.instance;
      // the selection's outline: its edges in the accent color, drawn over everything (shown when selected)
      const edges = new THREE.EdgesGeometry(geo, 25);
      disposables.push(edges);
      const outline = new THREE.LineSegments(edges, OUTLINE);
      outline.renderOrder = 5;
      outline.visible = false;
      mesh.add(outline);
      mesh.userData.outline = outline;
      group.add(mesh);
      built.geoms.push(mesh);
    }
    for (const b of view.boxes || []) {
      const size = b.hi.map((x, k) => Math.max(x - b.lo[k], 1e-4));
      const geo = new THREE.BoxGeometry(...size);
      const color = BOX_COLOR[b.instance.split(".")[0]] || 0x888888;
      const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.12, depthWrite: false });
      const edges = new THREE.EdgesGeometry(geo);
      const lmat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8 });
      disposables.push(geo, mat, edges, lmat);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(...b.lo.map((x, k) => x + size[k] / 2));
      mesh.add(new THREE.LineSegments(edges, lmat));
      mesh.userData = { instance: b.instance, box: true, lmat, color };
      mesh.renderOrder = 1;
      group.add(mesh);
      built.boxes.push(mesh);
    }
  } else {
    // no WebGL: the hooks still list what the view would draw
    for (const gd of view.geoms || []) built.geoms.push({ userData: { instance: gd.instance }, position: { toArray: () => gd.pos } });
    for (const b of view.boxes || []) built.boxes.push({ userData: { instance: b.instance, box: true } });
  }
  if (ed.hovered && !instances().includes(ed.hovered)) ed.hovered = null;
  updateHighlight();
  if (empty) {
    const i = shownIdea();
    const msg = !view.geoms && i && i.problems.some((p) => p.level === "error")
      ? "No 3D view while the idea has errors: fix them in the fields (the list shows which part)."
      : !(view.geoms || []).length && !(view.boxes || []).length ? "Nothing to show yet: add a shape." : "";
    empty.textContent = msg;
    empty.hidden = !msg;
  }
  requestRender();
}

const instances = () => [...new Set(built.geoms.map((g) => g.userData.instance))];

// Fit the bench's bounds in the view (Frame); SHAPES: the shapes and the inlets (the first view of an idea: an outlet
// box can be a meter wide and dwarf the mechanism); or an instance: the selected entry's shapes.
const SHAPES = Symbol("shapes");
function frame(target = null) {
  if (!renderer) return;
  const only = typeof target === "string" ? target : null;
  const bd = ed.view && ed.view.bounds;
  const box = new THREE.Box3();
  if (only) for (const m of [...built.geoms, ...built.boxes]) { if (m.userData.instance === only) box.expandByObject(m); }
  else if (target === SHAPES) {
    for (const m of [...built.geoms, ...built.boxes]) if (!m.userData.instance.startsWith("outlet.")) box.expandByObject(m);
    if (box.isEmpty()) for (const m of built.boxes) box.expandByObject(m);
  } else if (bd) box.set(new THREE.Vector3(...bd.lo), new THREE.Vector3(...bd.hi));
  else for (const m of [...built.geoms, ...built.boxes]) box.expandByObject(m);
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const r = Math.max(sphere.radius, only ? 0.05 : 0.1) * (only ? 1.6 : target === SHAPES ? 1.15 : 1);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const fit = Math.min(fov, 2 * Math.atan(Math.tan(fov / 2) * camera.aspect));
  const dist = (r / Math.sin(fit / 2)) * 1.05;
  // from the side (-y), a little ahead and above; a zoom to the selection keeps the direction it is seen from
  const dir = only ? camera.position.clone().sub(controls.target) : new THREE.Vector3(0.35, -1, 0.35);
  if (dir.lengthSq() < 1e-12) dir.set(0.35, -1, 0.35);
  dir.normalize();
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(dir, dist);
  camera.near = Math.max(0.005, dist / 200);
  camera.far = dist * 100 + 50;
  camera.updateProjectionMatrix();
  controls.update();
  requestRender();
}

function resize() {
  if (!renderer) return;
  const host = $("#bench-editor-view");
  const w = Math.max(1, host.clientWidth), hgt = Math.max(1, host.clientHeight);
  renderer.setSize(w, hgt, false);
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  camera.aspect = w / hgt;
  camera.updateProjectionMatrix();
  requestRender();
}

function requestRender() {
  if (pending || !renderer) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; renderer.render(scene, camera); });
}

function updateHighlight() {
  const zoom = document.getElementById("bench-edit-zoom");
  if (zoom) zoom.disabled = !ed.selected || ![...built.geoms, ...built.boxes].some((m) => m.userData.instance === ed.selected);
  if (!renderer) return;
  for (const m of built.geoms) {
    const inst = m.userData.instance, mat = m.material;
    if (inst === ed.selected) { mat.emissive.copy(SELECTED); mat.emissiveIntensity = inst === ed.hovered ? 0.8 : 0.65; }
    else if (inst === ed.hovered) { mat.emissive.copy(HOVER); mat.emissiveIntensity = 0.3; }
    else { mat.emissive.setRGB(0, 0, 0); mat.emissiveIntensity = 1; }
    m.userData.outline.visible = inst === ed.selected;
  }
  if (selBox) { scene.remove(selBox); selBox.geometry.dispose(); selBox.material.dispose(); selBox = null; }
  const around = new THREE.Box3();
  for (const m of built.geoms) if (m.userData.instance === ed.selected) around.expandByObject(m);
  if (!around.isEmpty()) {
    around.expandByScalar(0.006);
    selBox = new THREE.Box3Helper(around, 0x1d6fd6);
    selBox.material.depthTest = false;
    selBox.material.transparent = true;
    selBox.renderOrder = 6;
    scene.add(selBox);
  }
  for (const m of built.boxes) {
    const inst = m.userData.instance;
    m.material.opacity = inst === ed.selected ? 0.3 : inst === ed.hovered ? 0.22 : 0.12;
    m.userData.lmat.color.setHex(inst === ed.selected ? 0x1d6fd6 : m.userData.color);
    m.userData.lmat.opacity = inst === ed.selected ? 1 : 0.8;
  }
  requestRender();
}

function setHover(name) {
  if (ed.hovered === name) return;
  ed.hovered = name;
  if (renderer) renderer.domElement.style.cursor = name ? "pointer" : "";
  if (!name && tip) tip.hidden = true;
  for (const b of document.querySelectorAll("#bench-entries .bench-entry.hovered")) b.classList.remove("hovered");
  if (name) document.querySelector(`#bench-entries [data-entry="${CSS.escape(name)}"]`)?.classList.add("hovered");
  updateHighlight();
}

const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
// The shape under the pointer; an inlet or outlet only where no shape is (an outlet box is big and in front).
function hit(e, shapesOnly = false) {
  const r = renderer.domElement.getBoundingClientRect();
  ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hits = raycaster.intersectObjects(built.geoms, false);
  if (hits.length) return hits[0].object.userData.instance;
  if (shapesOnly) return null;
  const boxes = raycaster.intersectObjects(built.boxes, false);
  return boxes.length ? boxes[0].object.userData.instance : null;
}

function onMove(e) {
  if (e.buttons) return;   // orbiting
  const t = hit(e);
  setHover(t);
  if (t) {
    const r = $("#bench-editor-view").getBoundingClientRect();
    tip.textContent = t;
    tip.style.left = `${e.clientX - r.left + 12}px`;
    tip.style.top = `${e.clientY - r.top + 12}px`;
    tip.hidden = false;
  }
}

function onUp(e) {
  const d = down;
  down = null;
  if (!d || d.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_SLOP) return;
  const t = hit(e);
  if (t) select(t);
}

function toScreen(v) {
  const p = v.clone().project(camera);
  if (p.z < -1 || p.z > 1) return null;
  const r = renderer.domElement.getBoundingClientRect();
  return { x: r.left + ((p.x + 1) / 2) * r.width, y: r.top + ((1 - p.y) / 2) * r.height };
}

// Where an instance shows on screen: the first of its shapes whose middle isn't hidden behind another shape.
function screenPos(name) {
  if (!renderer) return null;
  for (const g of built.geoms.filter((x) => x.userData.instance === name)) {
    const p = toScreen(g.getWorldPosition(new THREE.Vector3()));
    if (p && hit({ clientX: p.x, clientY: p.y }, true) === name) return p;
  }
  return null;
}

// -- boot -------------------------------------------------------------------------------------------------------------
function buildDom() {
  const add = h("div", { id: "bench-add", class: "bench-add", role: "group", "aria-labelledby": "bench-add-title" },
    h("h2", { id: "bench-add-title", class: "bench-sub", text: "Add" }),
    h("div", { class: "bench-add-buttons" }, ...ADD.map(([section, label]) => h("button", { type: "button", class: "btn",
      dataset: { add: section }, onclick: () => (section === "cad" ? openCad() : add_(section)) }, label))));
  const editor = h("section", { id: "bench-editor", class: "bench-editor", hidden: true, "aria-labelledby": "bench-editor-name" },
    h("header", { class: "bench-editor-head" },
      h("button", { type: "button", class: "btn btn-big", id: "bench-edit-back", onclick: () => closeEditor() }, "← Back to the stage"),
      h("h1", { id: "bench-editor-name", class: "bench-editor-name", tabindex: "-1", text: "Editing" }),
      h("p", { class: "muted bench-editor-help", text: "Drag to turn, right-drag to move, scroll to zoom. Click a shape to change it. Every change is saved at once; Undo takes it back." })),
    h("div", { class: "bench-editor-body" },
      h("nav", { class: "bench-editor-side", "aria-label": "Parts of the idea" }, add,
        h("h2", { class: "bench-sub", text: "Parts" }),
        h("ul", { id: "bench-entries", class: "bench-entries" })),
      h("div", { id: "bench-editor-view", class: "bench-editor-view" }),
      h("aside", { id: "bench-inspector", class: "bench-inspector", dataset: { benchFields: "" }, "aria-labelledby": "bench-inspector-title" },
        h("h2", { id: "bench-inspector-title", class: "bench-inspector-title", tabindex: "-1", text: "Nothing selected" }),
        h("div", { id: "bench-inspector-body", class: "scroll bench-inspector-body" }))));
  $("#tab-bench").append(editor);

  const units = h("select", { id: "bench-cad-units" });
  const dialog = h("dialog", { id: "bench-cad-dialog", "aria-labelledby": "bench-cad-title" },
    h("form", { method: "dialog", novalidate: true, onsubmit: addCad },
      h("h2", { id: "bench-cad-title", text: "Add a CAD part" }),
      h("p", { class: "hint", text: "A part folder from the FreeCAD exporter (its wheels can be driven by the bench's shafts), or a mesh file, which stays fixed." }),
      h("div", { class: "field" }, h("label", { text: "A part folder" }),
        h("div", { id: "bench-cad-browse", class: "bench-cad-browse" }),
        h("p", { id: "bench-cad-chosen", class: "hint", text: "No part folder chosen." })),
      h("div", { class: "field" }, h("label", { for: "bench-cad-mesh", text: "or a mesh file (STL or OBJ), its path from the idea's folder" }),
        h("div", { class: "row" }, h("input", { id: "bench-cad-mesh", type: "text", autocomplete: "off", spellcheck: "false", placeholder: "parts/bracket.stl" }),
          h("label", { for: "bench-cad-units", class: "unit", text: "drawn in" }), units)),
      h("ul", { id: "bench-cad-errors", class: "error-list", role: "alert", hidden: true }),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "btn", onclick: () => $("#bench-cad-dialog").close() }, "Cancel"),
        h("button", { type: "submit", class: "btn btn-primary", id: "bench-cad-go" }, "Add it"))));
  document.body.append(dialog);
}
const add_ = (section) => add(section);

export function initEdit() {
  buildDom();
  stageHooks.edit = openEditor;
  onBench(() => { if (ed.open) renderEditor(); });
  const hook = window.__scrimmageBench;
  hook.select = (instance) => { if (ed.open) select(instance); };
  hook.editor = {
    get open() { return ed.open; },
    get selected() { return ed.selected; },
    get hovered() { return ed.hovered; },
    get instances() { return instances(); },
    get boxes() { return built.boxes.map((b) => b.userData.instance); },
    get outlined() { return [...new Set(built.geoms.filter((g) => g.userData.outline && g.userData.outline.visible).map((g) => g.userData.instance))]; },
    screenPos,
    posOf: (instance) => { const g = built.geoms.find((x) => x.userData.instance === instance); return g ? g.position.toArray() : null; },
  };
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && ed.open && !document.querySelector("dialog[open]") && !(e.target && e.target.tagName === "INPUT")) closeEditor();
  });
  render();
}
