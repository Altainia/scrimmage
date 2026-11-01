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
// Over the part (`overlay`, drawn on top): each joint's axis as an arrow through its anchor (orange: turns, with a ring;
// blue: slides; the selected body's brighter), the mounts (green), and while the robot frame is being checked a red
// "front" arrow along the robot's +x on the floor. Joint anchors and axes, and mount points, are CAD coordinates in
// the state; they are drawn through the robot frame (part_frame.js, the same math as scrimmage/cad/frame.py).
//
// "Try it" (part.tryit, set by the inspector's sliders) moves each body's merged mesh, and the bodies hanging on it,
// with a matrix: nothing is sent to the server. Picking still finds the right piece and triangle of a moved body.
//
// Pick mode (view3d.startPick({hint, onPick, owner})): a hint shows over the view, the next click on a piece calls
// onPick({key, triangle, point, rest}) instead of selecting (rest: the point with the body at zero pose); onPick may
// resolve {again: message} to stay in pick mode with that message. Esc or Cancel stops it.
//
// pickAt(clientX, clientY) -> {key, triangle (an index into that piece's view faces), point, rest} or null;
// requestRender() redraws. Test hook (on window.__scrimmagePart): screenPos(pieceKey, {across}) gives the client
// {x, y} of a visible spot on that piece (with `across`, a robot-frame direction, only on triangles whose normal is
// across it: a round side), or null when it isn't drawn or is hidden behind others; screenPoint(pieceKey) is where
// the piece's first vertex is drawn (it follows Try it); scene is {drawn, faint, meshes, triangles, arrows (bodies
// whose joint axis is drawn), mounts, lowest (the drawn pieces' lowest robot z at zero pose)}.

import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { $, h } from "./app.js";
import { part, onPart, onView, select, bodyColor, view3d } from "./part.js";
import { decode } from "./scene3d.js";
import { toRobot, cadToRobot, dirToRobot, normalize, bodyPoses, apply } from "./part_frame.js";

const SELECTED = new THREE.Color(0x2f7ff0);
const COG = 0xe8590c;
const TURNS = 0xd58a3a, SLIDES = 0x3a7bd5, MOUNT = 0x2a9d55, FRONT = 0xc62828;
const CLICK_SLOP = 5;

let renderer, scene, camera, controls, root, cogs, grid;
export const overlay = new THREE.Group();
let pieces = new Map();        // key -> {pos: Float32Array, idx: Uint32Array}
let meshes = [];               // [{mesh, keys, triPiece, triStart, vStart, faint, body}]
let mergedSig = null;          // which pieces each merged mesh holds
let framed = false;
let pending = false;
let down = null;
let disposables = [];
let overlayParts = [];         // geometries and materials of the overlay, disposed on each redraw
let arrows = [];               // bodies whose joint axis is drawn
let mountsDrawn = 0;
let poses = new Map();         // body -> {R, t}: its Try-it pose
let pick = null;               // {hint, onPick, owner, busy}
let frameKey = null;

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
  const legend = h("p", { class: "part-legend" }, h("span", { class: "part-cog-dot", "aria-hidden": "true" }), " center of gravity",
    h("span", { id: "part-legend-front", hidden: true }, h("span", { class: "part-front-dot", "aria-hidden": "true" }), " front"));
  const empty = h("p", { class: "view-placeholder", id: "part-view-empty", hidden: true });
  const hint = h("div", { class: "part-pick-hint", id: "part-pick-hint", hidden: true, role: "status" },
    h("span", { id: "part-pick-text" }),
    h("button", { type: "button", class: "btn btn-small", id: "part-pick-cancel", onclick: () => cancelPick() }, "Cancel (Esc)"));
  host.replaceChildren(cv, tools, legend, empty, hint);

  new ResizeObserver(resize).observe(host);
  resize();
  cv.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    host.replaceChildren(h("p", { class: "view-placeholder", role: "alert" }, "The 3D view lost its graphics context: reload the page."));
  });
  cv.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, button: e.button }; });
  cv.addEventListener("pointerup", onUp);
  document.addEventListener("keydown", (e) => {
    if (pick && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelPick(); }
  }, true);
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  if (dark && dark.addEventListener) dark.addEventListener("change", () => { setBackground(); requestRender(); });

  const hook = window.__scrimmagePart;
  hook.screenPos = screenPos;
  Object.defineProperty(hook, "scene", { configurable: true, enumerable: true, get: sceneInfo });
  hook.pickAt = pickAt;
  hook.screenPoint = screenPoint;
  Object.assign(view3d, { startPick, cancelPick, picking: () => !!pick, pickOwner: () => (pick ? pick.owner || null : null),
    pose: () => { applyPoses(); updateCogs(); drawOverlay(); requestRender(); }, solidBox });

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
  const f = part.state && part.state.frame;
  const fk = JSON.stringify(f ? [f.up, f.front, f.floor, f.center, f.skip] : null);
  if ((!framed || fk !== frameKey) && meshes.some((m) => !m.faint)) { frameAll(); framed = true; }
  frameKey = fk;
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

function merge(keys, faint, body) {
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
  mesh.matrixAutoUpdate = false;
  root.add(mesh);
  return { mesh, keys, triPiece, triStart, vStart, faint, body: faint ? null : body };
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
    for (const [g, keys] of groups) meshes.push(merge(keys, g === "\u0000faint", g));
  }
  recolor(where);
  applyPoses();
  updateCogs();
  drawOverlay();
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
  for (const b of part.state.bodies) if (b.com) dot(poses.has(b.name) ? apply(poses.get(b.name), b.com) : b.com, r, COG);
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

// -- Try it poses and the overlay ---------------------------------------------------------------------------------
const toMatrix = (p) => new THREE.Matrix4().set(
  p.R[0][0], p.R[0][1], p.R[0][2], p.t[0], p.R[1][0], p.R[1][1], p.R[1][2], p.t[1],
  p.R[2][0], p.R[2][1], p.R[2][2], p.t[2], 0, 0, 0, 1);

function applyPoses() {
  const s = part.state;
  poses = s && s.mode === "import" && s.frame && part.tryit.size ? bodyPoses(s, part.tryit) : new Map();
  for (const m of meshes) {
    const p = m.body != null && poses.get(m.body);
    if (p) m.mesh.matrix.copy(toMatrix(p)); else m.mesh.matrix.identity();
    m.mesh.matrixWorldNeedsUpdate = true;
  }
}

function keep(x) { overlayParts.push(x); return x; }

function clearOverlay() {
  for (const d of overlayParts) d.dispose();
  overlayParts = [];
  overlay.clear();
}

const Y = new THREE.Vector3(0, 1, 0);

// An arrow from `from` along unit `dir`, `len` long, drawn on top of the part.
function arrow(from, dir, len, color, opacity, radius) {
  const g = new THREE.Group();
  const mat = keep(new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false }));
  const shaft = new THREE.Mesh(keep(new THREE.CylinderGeometry(radius, radius, len * 0.8, 10)), mat);
  shaft.position.y = len * 0.4;
  const head = new THREE.Mesh(keep(new THREE.ConeGeometry(radius * 2.6, len * 0.2, 16)), mat);
  head.position.y = len * 0.9;
  for (const m of [shaft, head]) { m.renderOrder = 12; g.add(m); }
  g.quaternion.setFromUnitVectors(Y, dir);
  g.position.copy(from);
  return g;
}

function ring(center, dir, radius, tube, color, opacity) {
  const mat = keep(new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false }));
  const m = new THREE.Mesh(keep(new THREE.TorusGeometry(radius, tube, 8, 32)), mat);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), dir);
  m.position.copy(center);
  m.renderOrder = 12;
  return m;
}

function drawOverlay() {
  clearOverlay();
  arrows = [];
  const s = part.state;
  if (!s || s.mode !== "import" || !s.frame) { showFront(false); return; }
  const rt = toRobot(s.frame);
  const box = drawnBox();
  const size = box.isEmpty() ? 0.5 : box.getSize(new THREE.Vector3()).length();
  const len = Math.max(0.05, size * 0.22), radius = Math.max(0.0015, size * 0.004);
  const chosen = (name) => part.sel.kind === "body" && part.sel.keys.has(name);
  const byName = new Map(s.bodies.map((b) => [b.name, b]));
  const posed = (name, v) => { const p = name != null && poses.get(name); return p ? apply(p, v) : v; };
  const turned = (name, d) => { const p = name != null && poses.get(name); return p ? apply({ R: p.R, t: [0, 0, 0] }, d) : d; };
  for (const b of s.bodies) {
    const j = b.joint;
    if (!j || !j.anchor || !j.axis) continue;
    // the axis is fixed to the parent: it moves with the parent's pose
    const a = normalize(turned(b.parent, normalize(dirToRobot(rt, j.axis))));
    const c = posed(b.parent, cadToRobot(rt, j.anchor));
    const color = j.kind === "slide" ? SLIDES : TURNS, op = chosen(b.name) ? 1 : 0.5;
    const r = chosen(b.name) ? radius * 1.6 : radius;
    const va = new THREE.Vector3(...a), vc = new THREE.Vector3(...c);
    overlay.add(arrow(vc.clone().addScaledVector(va, -len / 2), va, len, color, op, r));
    if (j.kind === "hinge") overlay.add(ring(vc, va, len * 0.18, r * 0.8, color, op));
    arrows.push(b.name);
  }
  // mounts (and one picked but not named yet)
  const mounts = [];
  for (const b of s.bodies) for (const m of b.mounts || []) mounts.push({ body: b.name, ...m });
  if (part.pendingMount) mounts.push(part.pendingMount);
  mountsDrawn = 0;
  for (const m of mounts) {
    if (!byName.has(m.body)) continue;
    mountsDrawn++;
    const n = normalize(turned(m.body, normalize(dirToRobot(rt, m.normal))));
    const pt = new THREE.Vector3(...posed(m.body, cadToRobot(rt, m.point)));
    const op = chosen(m.body) ? 1 : 0.5;
    overlay.add(arrow(pt, new THREE.Vector3(...n), len * 0.4, MOUNT, op, radius));
    const dotMat = keep(new THREE.MeshBasicMaterial({ color: MOUNT, transparent: true, opacity: op, depthTest: false }));
    const dot = new THREE.Mesh(keep(new THREE.SphereGeometry(radius * 3, 12, 8)), dotMat);
    dot.position.copy(pt);
    dot.renderOrder = 12;
    overlay.add(dot);
  }
  // the robot's front, along +x on the floor, while the frame is being checked
  const front = !!part.frameStep || part.sel.kind === "frame";
  if (front) overlay.add(arrow(new THREE.Vector3(0, 0, 0.001), new THREE.Vector3(1, 0, 0), Math.max(0.15, size * 0.3), FRONT, 0.95, radius * 1.6));
  showFront(front);
}

function showFront(on) {
  const el = document.getElementById("part-legend-front");
  if (el) el.hidden = !on;
}

// The drawn (not left-out) pieces' box in robot coordinates at zero pose, as {min, max} arrays, or null.
function solidBox() {
  const box = new THREE.Box3();
  for (const m of meshes) if (!m.faint) box.union(m.mesh.geometry.boundingBox);
  return box.isEmpty() ? null : { min: box.min.toArray(), max: box.max.toArray() };
}

// -- pick mode -------------------------------------------------------------------------------------------------
function startPick(opts) {
  pick = { ...opts, busy: false };
  $("#part-pick-text").textContent = opts.hint;
  $("#part-pick-hint").hidden = false;
  $("#part-view").classList.add("picking");
  requestRender();
}

function cancelPick() {
  if (!pick) return;
  pick = null;
  $("#part-pick-hint").hidden = true;
  $("#part-view").classList.remove("picking");
  const b = document.getElementById("part-pick-axis");
  if (b) b.setAttribute("aria-pressed", "false");
}

async function pickClick(e) {
  const t = pickAt(e.clientX, e.clientY);
  const text = $("#part-pick-text");
  if (!t) { text.textContent = `${pick.hint} (That click missed: click on a piece.)`; return; }
  if (pick.busy) return;
  const cur = pick;
  cur.busy = true;
  try {
    const r = await cur.onPick(t);
    if (pick === cur && r && r.again) text.textContent = `${r.again} ${cur.hint}`;
  } finally {
    cur.busy = false;
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
  root.updateMatrixWorld(true);
  raycaster.setFromCamera(ndc, camera);
  const shown = meshes.filter((m) => m.mesh.visible);
  const hits = raycaster.intersectObjects(shown.map((m) => m.mesh), false);
  // a solid piece wins over a faint (left-out) one in front of it
  const best = hits.find((x) => !x.object.material.transparent) || hits[0];
  if (!best || best.faceIndex == null) return null;
  const m = shown.find((x) => x.mesh === best.object);
  const j = m.triPiece[best.faceIndex];
  const rest = best.point.clone().applyMatrix4(m.mesh.matrixWorld.clone().invert());
  return { key: m.keys[j], triangle: best.faceIndex - m.triStart[j], point: best.point.toArray(), rest: rest.toArray() };
}

function onUp(e) {
  const d = down;
  down = null;
  if (!d || d.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_SLOP) return;
  if (pick) { pickClick(e); return; }
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

// A spot on the piece that a click there would hit: some of its triangles' centers, tried in turn. With `across`
// (a robot-frame direction), only triangles whose normal is across it (within about 10 degrees).
function screenPos(key, { across = null } = {}) {
  if (!renderer) return null;
  camera.updateMatrixWorld();
  root.updateMatrixWorld(true);
  const ax = across ? new THREE.Vector3(...across).normalize() : null;
  for (const m of meshes) {
    const j = m.keys.indexOf(key);
    if (j < 0 || !m.mesh.visible) continue;
    const pos = m.mesh.geometry.getAttribute("position"), idx = m.mesh.geometry.index, w = m.mesh.matrixWorld;
    const t0 = m.triStart[j], n = m.triStart[j + 1] - t0;
    const v = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()], nrm = new THREE.Vector3(), e = new THREE.Vector3();
    const cands = [];
    for (let t = t0; t < t0 + n; t++) {
      for (let i = 0; i < 3; i++) v[i].fromBufferAttribute(pos, idx.getX(t * 3 + i)).applyMatrix4(w);
      if (ax) {
        nrm.subVectors(v[1], v[0]).cross(e.subVectors(v[2], v[0]));
        if (nrm.lengthSq() < 1e-20 || Math.abs(nrm.normalize().dot(ax)) > 0.17) continue;
      }
      cands.push(v[0].clone().add(v[1]).add(v[2]).divideScalar(3));
    }
    const step = Math.max(1, Math.floor(cands.length / 64));
    for (let i = 0; i < cands.length; i += step) {
      const p = toScreen(cands[i]);
      if (p && pickAt(p.x, p.y)?.key === key) return p;
    }
  }
  return null;
}

// Where the piece's first vertex is drawn (it moves with Try it; maybe off screen), or null.
function screenPoint(key) {
  if (!renderer) return null;
  camera.updateMatrixWorld();
  root.updateMatrixWorld(true);
  for (const m of meshes) {
    const j = m.keys.indexOf(key);
    if (j < 0) continue;
    const v = new THREE.Vector3().fromBufferAttribute(m.mesh.geometry.getAttribute("position"), m.vStart[j]).applyMatrix4(m.mesh.matrixWorld);
    const q = v.project(camera), r = renderer.domElement.getBoundingClientRect();   // off screen too
    return { x: r.left + ((q.x + 1) / 2) * r.width, y: r.top + ((1 - q.y) / 2) * r.height };
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
  const box = solidBox();
  return { drawn, faint, meshes: meshes.length, triangles, arrows: [...arrows], mounts: mountsDrawn, lowest: box ? box.min[2] : null };
}
