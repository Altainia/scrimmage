// The Part tab's 3D view: the pieces from /api/part/view in robot coordinates (plugin mode: one entry per body, in
// the part frame), drawn with three.js. z is up, as in the robot view (see scene3d.js).
//
// Drag to orbit, right-drag to pan, scroll to zoom. A click selects a piece (plugin mode: a body); Shift-click adds
// it to the selection; a click on nothing clears it. A body's pieces share its color (the tree's swatch); the
// selection is blue. Left-out pieces are not drawn; "Show left out" draws them faint (and clickable, to put back).
// Orange dots are the centers of gravity: a small one per body, a large one per part.
//
// Large imports stay smooth: the pieces of a body are merged into one BufferGeometry (with vertex colors, so a
// selection change rewrites colors, not geometry), and a click finds its piece through the mesh's
// triangle-to-piece index. The merge is redone only when a piece changes body or is left out or put back.
//
// For the next steps: pickAt(clientX, clientY) -> {key, triangle (an index into that piece's view faces), point}
// or null; `overlay` is a group drawn over the part (axes, anchors); requestRender() redraws.
// Test hook (on window.__scrimmagePart): screenPos(pieceKey) gives the client {x, y} of a visible spot on that piece,
// or null when it isn't drawn or is hidden behind others; scene is {drawn, faint, meshes, triangles}.

import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { $, h } from "./app.js";
import { part, onPart, onView, select, bodyColor } from "./part.js";
import { decode } from "./scene3d.js";

const SELECTED = new THREE.Color(0x2f7ff0);
const COG = 0xe8590c;
const CLICK_SLOP = 5;

let renderer, scene, camera, controls, root, cogs, grid;
export const overlay = new THREE.Group();
let pieces = new Map();        // key -> {pos: Float32Array, idx: Uint32Array}
let meshes = [];               // [{mesh, keys, triPiece, triStart, vStart, faint}]
let mergedSig = null;          // which pieces each merged mesh holds
let framed = false;
let pending = false;
let down = null;
let disposables = [];

export function initPartView() {
  const host = $("#part-view");
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch (e) {
    host.replaceChildren(h("p", { class: "view-placeholder" },
      "The 3D view needs WebGL, which this browser didn't provide. The list and the inspector still work."));
    return false;
  }
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const cv = renderer.domElement;
  cv.className = "view-canvas";
  cv.setAttribute("role", "img");
  cv.setAttribute("aria-label", "3D view of the part. Drag to orbit, right-drag to pan, scroll to zoom; click a piece to select it, Shift-click to add one. The list on the left does the same.");

  scene = new THREE.Scene();
  setBackground();
  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.0);
  hemi.position.set(0, 0, 1);
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(3, -2, 6);
  scene.add(hemi, sun, sun.target);

  camera = new THREE.PerspectiveCamera(40, 1, 0.005, 100);
  camera.up.set(0, 0, 1);
  camera.position.set(1.2, -1, 0.9);
  controls = new OrbitControls(camera, cv);
  controls.addEventListener("change", requestRender);

  root = new THREE.Group();
  cogs = new THREE.Group();
  scene.add(root, cogs, overlay);

  const leftBtn = h("button", { type: "button", class: "btn btn-small", id: "part-show-left-out", "aria-pressed": "false",
    title: "Draw the pieces you left out, faintly",
    onclick: () => {
      part.showLeftOut = !part.showLeftOut;
      leftBtn.setAttribute("aria-pressed", String(part.showLeftOut));
      for (const m of meshes) if (m.faint) m.mesh.visible = part.showLeftOut;
      requestRender();
    } }, "Show left out");
  const tools = h("div", { class: "view-tools" },
    h("button", { type: "button", class: "btn btn-small", id: "part-frame-view", title: "Fit the part in the view", onclick: () => frameAll() }, "Fit to view"),
    leftBtn);
  const legend = h("p", { class: "part-legend" }, h("span", { class: "part-cog-dot", "aria-hidden": "true" }), " center of gravity");
  const empty = h("p", { class: "view-placeholder", id: "part-view-empty", hidden: true });
  host.replaceChildren(cv, tools, legend, empty);

  new ResizeObserver(resize).observe(host);
  resize();
  cv.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    host.replaceChildren(h("p", { class: "view-placeholder", role: "alert" }, "The 3D view lost its graphics context: reload the page."));
  });
  cv.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, button: e.button }; });
  cv.addEventListener("pointerup", onUp);
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  if (dark && dark.addEventListener) dark.addEventListener("change", () => { setBackground(); requestRender(); });

  const hook = window.__scrimmagePart;
  hook.screenPos = screenPos;
  Object.defineProperty(hook, "scene", { configurable: true, enumerable: true, get: sceneInfo });
  hook.pickAt = pickAt;

  onView(build);
  onPart(update);
  return true;
}

function setBackground() {
  const css = getComputedStyle(document.documentElement).getPropertyValue("--sunken").trim() || "#eef0f3";
  scene.background = new THREE.Color(css);
}

// -- building ---------------------------------------------------------------------------------------------
function build(view) {
  if (!renderer) return;
  pieces = new Map();
  for (const p of (view && view.pieces) || []) {
    pieces.set(p.key, { pos: decode(p.vert, Float32Array), idx: new Uint32Array(decode(p.face, Int32Array)) });
  }
  mergedSig = null;
  update();
  if (!framed && meshes.some((m) => !m.faint)) { frameAll(); framed = true; }
}

// The body each drawn key belongs to, and whether it is left out (plugin mode: the key is the body).
function placement() {
  const s = part.state;
  const out = new Map();
  if (!s) return out;
  if (s.mode === "plugin") { for (const k of pieces.keys()) out.set(k, { body: k, faint: false }); return out; }
  for (const p of s.pieces) if (pieces.has(p.key)) out.set(p.key, { body: p.body, faint: !!p.left_out });
  return out;
}

function clearMeshes() {
  for (const d of disposables) d.dispose();
  disposables = [];
  root.clear();
  meshes = [];
}

function merge(keys, faint) {
  let nv = 0, nt = 0;
  for (const k of keys) { const p = pieces.get(k); nv += p.pos.length / 3; nt += p.idx.length / 3; }
  const pos = new Float32Array(nv * 3), col = new Float32Array(nv * 3), idx = new Uint32Array(nt * 3);
  const triPiece = new Int32Array(nt), triStart = [], vStart = [];
  let v = 0, t = 0;
  keys.forEach((k, j) => {
    const p = pieces.get(k);
    pos.set(p.pos, v * 3);
    const n = p.idx.length;
    for (let i = 0; i < n; i++) idx[t * 3 + i] = p.idx[i] + v;
    triStart.push(t);
    vStart.push(v);
    triPiece.fill(j, t, t + n / 3);
    v += p.pos.length / 3;
    t += n / 3;
  });
  vStart.push(v);
  triStart.push(t);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeBoundingSphere();
  g.computeBoundingBox();
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.7, metalness: 0.05,
    side: THREE.DoubleSide, ...(faint ? { transparent: true, opacity: 0.22, depthWrite: false } : {}) });
  disposables.push(g, mat);
  const mesh = new THREE.Mesh(g, mat);
  if (faint) { mesh.visible = part.showLeftOut; mesh.renderOrder = 1; }
  root.add(mesh);
  return { mesh, keys, triPiece, triStart, vStart, faint };
}

// After every render of the tab: re-merge when pieces moved, else recolor; the centers of gravity.
function update() {
  if (!renderer || !part.state) return;
  const where = placement();
  const sig = JSON.stringify([...where].map(([k, w]) => [k, w.body, w.faint]));
  if (sig !== mergedSig) {
    mergedSig = sig;
    clearMeshes();
    const groups = new Map();
    for (const [k, w] of where) {
      const g = w.faint ? "\u0000faint" : w.body;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(k);
    }
    for (const [g, keys] of groups) meshes.push(merge(keys, g === "\u0000faint"));
  }
  recolor(where);
  updateCogs();
  const empty = $("#part-view-empty");
  const s = part.state;
  const why = meshes.length || !part.view ? ""
    : s.read_only_reason && s.mode !== "plugin" ? "Nothing to draw: the CAD file can't be read (see above)."
    : s.mode === "plugin" ? "Nothing to draw: this part folder has no mesh files (its bodies are simple shapes)."
    : "Nothing to draw: the CAD file has no pieces.";
  empty.textContent = why;
  empty.hidden = !why;
  requestRender();
}

function recolor(where) {
  const sel = part.sel;
  const color = new THREE.Color();
  for (const m of meshes) {
    const col = m.mesh.geometry.getAttribute("color");
    m.keys.forEach((k, j) => {
      const w = where.get(k);
      const chosen = (sel.kind === "piece" && sel.keys.has(k)) || (sel.kind === "body" && w && sel.keys.has(w.body));
      if (chosen) color.copy(SELECTED);
      else color.set(bodyColor(w ? w.body : null));   // three converts the sRGB hex to linear
      for (let v = m.vStart[j]; v < m.vStart[j + 1]; v++) col.setXYZ(v, color.r, color.g, color.b);
    });
    col.needsUpdate = true;
  }
}

function drawnBox() {
  const box = new THREE.Box3();
  for (const m of meshes) if (!m.faint || m.mesh.visible) box.union(m.mesh.geometry.boundingBox);
  return box;
}

function updateCogs() {
  for (const c of [...cogs.children]) { c.geometry.dispose(); c.material.dispose(); }
  cogs.clear();
  if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); grid = null; }
  const box = drawnBox();
  const size = box.isEmpty() ? 0.5 : box.getSize(new THREE.Vector3()).length();
  const r = Math.min(0.03, Math.max(0.003, size * 0.012));
  const dot = (c, radius, color) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(radius, 16, 12),
      new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true, opacity: 0.95 }));
    m.position.set(c[0], c[1], c[2]);
    m.renderOrder = 10;
    cogs.add(m);
  };
  for (const b of part.state.bodies) if (b.com) dot(b.com, r, COG);
  for (const p of part.state.parts) if (p.com) dot(p.com, r * 2.2, 0xc2410c);
  // the floor (robot frame z = 0) under an import, as a grid
  if (part.state.mode !== "plugin" && !box.isEmpty()) {
    const span = Math.max(0.5, Math.ceil(Math.max(box.max.x - box.min.x, box.max.y - box.min.y) * 1.5 * 10) / 10);
    grid = new THREE.GridHelper(span, Math.round(span / 0.05), 0x9aa3ad, 0xb8c0c8);
    grid.rotation.x = Math.PI / 2;
    grid.position.set((box.min.x + box.max.x) / 2, (box.min.y + box.max.y) / 2, 0);
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    scene.add(grid);
  }
}

// -- camera -----------------------------------------------------------------------------------------------
function frameAll() {
  const box = drawnBox();
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const r = Math.max(sphere.radius, 0.05);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const fit = Math.min(fov, 2 * Math.atan(Math.tan(fov / 2) * camera.aspect));
  const dist = (r / Math.sin(fit / 2)) * 1.1;
  const dir = camera.position.clone().sub(controls.target);
  if (!framed || dir.lengthSq() < 1e-12 || dir.z <= 0) dir.set(1.3, -1.0, 0.9);
  dir.normalize();
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(dir, dist);
  camera.near = Math.max(0.001, dist / 100);
  camera.far = dist * 100 + 10;
  camera.updateProjectionMatrix();
  controls.update();
  requestRender();
}

function resize() {
  const host = $("#part-view");
  const w = Math.max(1, host.clientWidth), hgt = Math.max(1, host.clientHeight);
  renderer.setSize(w, hgt, false);
  renderer.domElement.style.width = "100%";
  renderer.domElement.style.height = "100%";
  camera.aspect = w / hgt;
  camera.updateProjectionMatrix();
  requestRender();
}

export function requestRender() {
  if (pending || !renderer) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; renderer.render(scene, camera); });
}

// -- picking ----------------------------------------------------------------------------------------------
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();

export function pickAt(clientX, clientY) {
  if (!renderer) return null;
  const r = renderer.domElement.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
  camera.updateMatrixWorld();
  raycaster.setFromCamera(ndc, camera);
  const shown = meshes.filter((m) => m.mesh.visible);
  const hits = raycaster.intersectObjects(shown.map((m) => m.mesh), false);
  // a solid piece wins over a faint (left-out) one in front of it
  const best = hits.find((x) => !x.object.material.transparent) || hits[0];
  if (!best || best.faceIndex == null) return null;
  const m = shown.find((x) => x.mesh === best.object);
  const j = m.triPiece[best.faceIndex];
  return { key: m.keys[j], triangle: best.faceIndex - m.triStart[j], point: best.point.toArray() };
}

function onUp(e) {
  const d = down;
  down = null;
  if (!d || d.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_SLOP) return;
  const t = pickAt(e.clientX, e.clientY);
  const kind = part.state && part.state.mode === "plugin" ? "body" : "piece";
  if (t) select(kind, [t.key], { add: e.shiftKey || e.ctrlKey || e.metaKey });
  else if (!e.shiftKey) select(null);
}

// -- test hook --------------------------------------------------------------------------------------------
function toScreen(v) {
  const p = v.clone().project(camera);
  if (p.z < -1 || p.z > 1 || Math.abs(p.x) > 1 || Math.abs(p.y) > 1) return null;
  const r = renderer.domElement.getBoundingClientRect();
  return { x: r.left + ((p.x + 1) / 2) * r.width, y: r.top + ((1 - p.y) / 2) * r.height };
}

// A spot on the piece that a click there would hit: some of its triangles' centers, tried in turn.
function screenPos(key) {
  if (!renderer) return null;
  camera.updateMatrixWorld();
  for (const m of meshes) {
    const j = m.keys.indexOf(key);
    if (j < 0 || !m.mesh.visible) continue;
    const pos = m.mesh.geometry.getAttribute("position"), idx = m.mesh.geometry.index;
    const t0 = m.triStart[j], n = m.triStart[j + 1] - t0, step = Math.max(1, Math.floor(n / 64));
    const c = new THREE.Vector3(), a = new THREE.Vector3();
    for (let t = t0; t < t0 + n; t += step) {
      c.set(0, 0, 0);
      for (let i = 0; i < 3; i++) c.add(a.fromBufferAttribute(pos, idx.getX(t * 3 + i)));
      c.divideScalar(3);
      const p = toScreen(c);
      if (p && pickAt(p.x, p.y)?.key === key) return p;
    }
  }
  return null;
}

function sceneInfo() {
  const drawn = [], faint = [];
  let triangles = 0;
  for (const m of meshes) {
    if (!m.mesh.visible) continue;
    (m.faint ? faint : drawn).push(...m.keys);
    triangles += m.triPiece.length;
  }
  return { drawn, faint, meshes: meshes.length, triangles };
}
