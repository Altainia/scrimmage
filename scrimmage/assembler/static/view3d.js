// The 3D view (center column): the robot's start pose from /api/robot/view, drawn with three.js.
//
// MuJoCo's world is z-up and three.js is y-up by default. The scene keeps MuJoCo's frame (positions and
// quaternions are copied as they are) and the camera is told that +z is up instead.
//
// Hover a part to highlight it; click it to select it (tree and inspector follow). Mount triads show where
// parts attach; in "pick in 3D" mode (the Add dialog's button) they become clickable targets. The "CG" button shows the
// robot's center of gravity (the view's com) as an orange ball with a line down to the floor.
//
// For tests: window.__scrimmage.scene is {instances, meshes, mounts, cg (the CG marker is shown), background}; window.__scrimmage.screenPos(name) gives the
// client {x, y} of a mount ("<instance>.<mount>") or of an instance's center, or null when off screen;
// window.__scrimmage.gpuMemory() is three's count of live GPU geometries and textures.

import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { $, h, store, select, attachGroups } from "./app.js";
import { meshGeometry, primitive, setPose, geomMaterial, buildFloor } from "./scene3d.js";
import { theme } from "./theme.js";
import { i18n, t, live as L, liveAttr } from "./i18n.js";

const HOVER = new THREE.Color(0x5b8fd6);     // emissive tints
const SELECTED = new THREE.Color(0x1d6fd6);
const PICK = new THREE.Color(0xe8590c);       // pickable mount markers
const CG = new THREE.Color(0xff8c1a);         // the center of gravity marker
const CLICK_SLOP = 5;                          // px a press may move and still count as a click

let renderer, scene, camera, controls, robot, floor, tip;
let built = { geoms: [], mounts: [], instances: [], triad: 0.1, cg: null };   // what the current view drew
let disposables = [];                                    // geometries/materials of the current view
let hovered = null;            // {kind: "instance"|"mount", name}
let picking = false;
let showMounts = true;
let showCg = false;
let framed = false;            // the camera has been fitted to a non-empty robot once
let pending = false;           // a render is scheduled
let down = null;               // pointerdown position, to tell clicks from orbit drags

export function initView() {
  const host = $("#view");
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch (e) {
    host.replaceChildren(h("p", { class: "view-placeholder" },
      t("The 3D view needs WebGL, which this browser didn't provide. Everything else still works.")));
    return false;
  }
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.className = "view-canvas";
  liveAttr(renderer.domElement, "aria-label", () => t("3D view of the robot. Drag to orbit, right-drag to pan, scroll to zoom; click a part to select it."));
  renderer.domElement.setAttribute("role", "img");

  scene = new THREE.Scene();
  scene.background = new THREE.Color(theme.color("--watch-bg"));   // the page's theme (app.css)
  theme.onChange(() => { scene.background.set(theme.color("--watch-bg")); requestRender(); });
  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.0);
  hemi.position.set(0, 0, 1);                  // sky is +z
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(3, -2, 6);
  scene.add(sun, sun.target);

  camera = new THREE.PerspectiveCamera(40, 1, 0.01, 200);
  camera.up.set(0, 0, 1);                      // before OrbitControls, which reads camera.up once
  camera.position.set(3.5, 2.5, 2);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(2, 4, 0);
  controls.addEventListener("change", requestRender);

  robot = new THREE.Group();
  scene.add(robot);

  tip = h("div", { class: "view-tip", hidden: true });
  const mountsBtn = liveAttr(h("button", {
    type: "button", class: "btn btn-small", id: "view-mounts", "aria-pressed": "true",
    onclick: () => { showMounts = !showMounts; mountsBtn.setAttribute("aria-pressed", String(showMounts)); updateMountVisibility(); },
  }, L(() => t("Mounts"))), "title", () => t("Show or hide the mount triads (where parts attach)"));
  const cgBtn = liveAttr(h("button", {
    type: "button", class: "btn btn-small", id: "view-cg", "aria-pressed": "false",
    onclick: () => { showCg = !showCg; cgBtn.setAttribute("aria-pressed", String(showCg)); updateCg(); },
  }, L(() => t("CG"))), "title", () => t("Show or hide the robot's center of gravity"));
  const tools = h("div", { class: "view-tools" },
    liveAttr(h("button", { type: "button", class: "btn btn-small", id: "view-frame", onclick: () => frameRobot() }, L(() => t("Frame robot"))),
      "title", () => t("Fit the robot in the view")),
    mountsBtn, cgBtn);
  const empty = h("p", { class: "view-placeholder", id: "view-empty", hidden: true });
  host.replaceChildren(renderer.domElement, tools, tip, empty);

  new ResizeObserver(resize).observe(host);
  resize();

  const cv = renderer.domElement;
  cv.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    host.replaceChildren(h("p", { class: "view-placeholder", role: "alert" }, t("3D view lost its graphics context — reload the page")));
  });
  cv.addEventListener("pointermove", onMove);
  cv.addEventListener("pointerleave", () => setHover(null));
  cv.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, button: e.button }; });
  cv.addEventListener("pointerup", onUp);

  window.addEventListener("scrimmage:view", () => build(store.view));
  window.addEventListener("scrimmage:state", () => { updateEmpty(); updateMountVisibility(); });
  i18n.onChange(() => updateEmpty());
  window.addEventListener("scrimmage:selected", () => updateHighlight());
  window.addEventListener("scrimmage:hover", (e) => {
    if (e.detail && e.detail.source !== "view") setHover(e.detail.name ? { kind: "instance", name: e.detail.name } : null, false);
  });
  window.addEventListener("scrimmage:pick-mount", (e) => {
    picking = !!(e.detail && e.detail.active);
    document.body.classList.toggle("picking", picking);
    setHover(null);
    updateMountVisibility();
  });

  Object.defineProperty(window.__scrimmage, "scene", {
    configurable: true, enumerable: true,
    get: () => ({ instances: [...built.instances], meshes: built.geoms.length, mounts: built.mounts.length,
      cg: !!(built.cg && built.cg.visible), background: `#${scene.background.getHexString()}` }),
  });
  window.__scrimmage.screenPos = screenPos;
  window.__scrimmage.frameRobot = frameRobot;
  window.__scrimmage.gpuMemory = () => ({ ...renderer.info.memory });   // {geometries, textures}: leak checks

  store.view3d = true;
  if (store.view) build(store.view);
  return true;
}

// -- building ---------------------------------------------------------------------------------
function clear() {
  for (const d of disposables) d.dispose();
  disposables = [];
  robot.clear();
  if (floor) { scene.remove(floor); floor = null; }
  built = { geoms: [], mounts: [], instances: [], triad: 0.1, cg: null };
}

function build(view) {
  if (!renderer) return;
  const hadRobot = built.geoms.length > 0;
  const keep = hovered;
  clear();
  view = view || {};
  if (view.floor) {
    const f = buildFloor(view.floor);
    floor = f.group;
    disposables.push(...f.disposables);
    scene.add(floor);
  }

  const meshGeoms = (view.meshes || []).map(() => null);   // built on first use, shared between geoms
  const names = new Set();
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
    robot.add(mesh);
    built.geoms.push(mesh);
    names.add(gd.instance);
  }
  built.instances = (view.instances || []).filter((n) => names.has(n));

  const size = built.triad = triadSize(view);
  const marker = new THREE.SphereGeometry(1, 16, 12);
  disposables.push(marker);
  for (const md of view.mounts || []) {
    const g = new THREE.Group();
    setPose(g, md.pos, md.quat);
    const axes = new THREE.AxesHelper(size);
    axes.renderOrder = 2;
    axes.material.depthTest = false;      // triads sit inside parts (a module mount is in the frame rail)
    axes.material.transparent = true;
    axes.material.opacity = 0.9;
    disposables.push(axes.geometry, axes.material);
    const matDot = new THREE.MeshBasicMaterial({ color: PICK, depthTest: false, transparent: true, opacity: 0.85 });
    disposables.push(matDot);
    const dot = new THREE.Mesh(marker, matDot);
    dot.scale.setScalar(size * 0.28);
    dot.renderOrder = 3;
    g.add(axes, dot);
    g.userData = { mount: md.name, axes, dot };
    robot.add(g);
    built.mounts.push(g);
  }

  if (view.com) built.cg = cgMarker(view.com, size);

  hovered = keep && (keep.kind === "instance" ? names.has(keep.name) : built.mounts.some((m) => m.userData.mount === keep.name)) ? keep : null;
  updateMountVisibility();
  updateHighlight();
  updateEmpty();
  if (!framed || !hadRobot) { if (built.geoms.length) { frameRobot(); framed = true; } }
  requestRender();
}

// An orange ball at the CG and a line from it straight down to the floor (how high it is), drawn on top of the parts.
function cgMarker(com, size) {
  const g = new THREE.Group();
  const ballGeo = new THREE.SphereGeometry(size * 0.3, 20, 14);
  const ballMat = new THREE.MeshBasicMaterial({ color: CG, depthTest: false, transparent: true, opacity: 0.95 });
  const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -com[2])]);
  const lineMat = new THREE.LineBasicMaterial({ color: CG, depthTest: false, transparent: true });
  disposables.push(ballGeo, ballMat, lineGeo, lineMat);
  const ball = new THREE.Mesh(ballGeo, ballMat);
  const line = new THREE.Line(lineGeo, lineMat);
  ball.renderOrder = line.renderOrder = 4;
  g.add(ball, line);
  g.position.set(com[0], com[1], com[2]);
  g.visible = showCg;
  robot.add(g);
  return g;
}

function updateCg() {
  if (built.cg) built.cg.visible = showCg;
  requestRender();
}

function robotBox() {
  const box = new THREE.Box3();
  for (const m of built.geoms) box.expandByObject(m);
  return box;
}

function triadSize(view) {
  const box = new THREE.Box3();
  for (const gd of view.geoms || []) box.expandByPoint(new THREE.Vector3(...gd.pos));
  const d = box.isEmpty() ? 0.5 : box.getSize(new THREE.Vector3()).length();
  return Math.min(0.15, Math.max(0.04, d * 0.08));
}

// -- camera -----------------------------------------------------------------------------------
export function frameRobot() {
  if (!renderer) return;
  const box = robotBox();
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const r = Math.max(sphere.radius, 0.1);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const fit = Math.min(fov, 2 * Math.atan(Math.tan(fov / 2) * camera.aspect));
  const dist = (r / Math.sin(fit / 2)) * 1.1;
  // keep the current viewing direction, but never look from below the floor
  const dir = camera.position.clone().sub(controls.target);
  if (!framed || dir.lengthSq() < 1e-9 || dir.z <= 0) dir.set(1.3, -1.0, 0.9);   // front-right, above (+x is forward)
  dir.normalize();
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(dir, dist);
  camera.near = Math.max(0.01, dist / 100);
  camera.far = dist * 100 + 50;
  camera.updateProjectionMatrix();
  controls.update();
  requestRender();
}

function resize() {
  const host = $("#view");
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

// -- mounts -----------------------------------------------------------------------------------
function pickable() {
  return new Set(attachGroups().flatMap((g) => g.mounts));
}

function updateMountVisibility() {
  const ok = picking ? pickable() : null;
  for (const g of built.mounts) {
    const { axes, dot, mount } = g.userData;
    const target = picking && ok.has(mount);
    g.visible = picking ? target : showMounts;
    axes.visible = true;
    dot.visible = target;
    const hot = target && hovered && hovered.kind === "mount" && hovered.name === mount;
    dot.material.color.copy(hot ? SELECTED : PICK);
    dot.scale.setScalar(built.triad * (hot ? 0.4 : 0.28));
  }
  requestRender();
}

// -- highlight --------------------------------------------------------------------------------
function updateHighlight() {
  const hov = hovered && hovered.kind === "instance" && !picking ? hovered.name : null;
  for (const m of built.geoms) {
    const inst = m.userData.instance;
    const mat = m.material;
    if (inst === store.selected) { mat.emissive.copy(SELECTED); mat.emissiveIntensity = inst === hov ? 0.26 : 0.15; }
    else if (inst === hov) { mat.emissive.copy(HOVER); mat.emissiveIntensity = 0.2; }
    else { mat.emissive.setRGB(0, 0, 0); mat.emissiveIntensity = 1; }
  }
  for (const li of document.querySelectorAll("#tree .tree-item.hovered")) li.classList.remove("hovered");
  if (hov) document.querySelector(`#tree [data-name="${CSS.escape(hov)}"]`)?.classList.add("hovered");
  requestRender();
}

function setHover(next, announce = true) {
  const same = (a, b) => (a === b) || (a && b && a.kind === b.kind && a.name === b.name);
  if (same(hovered, next)) return;
  hovered = next;
  const cv = renderer && renderer.domElement;
  if (cv) cv.style.cursor = next ? "pointer" : "";
  if (!next) tip.hidden = true;
  if (announce) {
    window.dispatchEvent(new CustomEvent("scrimmage:hover", {
      detail: { name: next && next.kind === "instance" ? next.name : null, source: "view" },
    }));
  }
  updateHighlight();
  if (picking) updateMountVisibility();
}

// -- picking ----------------------------------------------------------------------------------
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();

function hit(e, partsOnly = false) {
  const r = renderer.domElement.getBoundingClientRect();
  ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  if (picking && !partsOnly) {
    // mount markers draw on top of everything, so they are hit regardless of what is in front
    const dots = built.mounts.filter((g) => g.visible && g.userData.dot.visible).map((g) => g.userData.dot);
    const hits = raycaster.intersectObjects(dots, false);
    if (hits.length) return { kind: "mount", name: hits[0].object.parent.userData.mount };
    // forgiving: the nearest marker within a few pixels of the pointer
    let best = null;
    for (const g of built.mounts) {
      if (!g.visible || !g.userData.dot.visible) continue;
      const p = toScreen(g.getWorldPosition(new THREE.Vector3()));
      if (!p) continue;
      const d = Math.hypot(p.x - e.clientX, p.y - e.clientY);
      if (d < 14 && (!best || d < best.d)) best = { d, name: g.userData.mount };
    }
    return best ? { kind: "mount", name: best.name } : null;
  }
  const hits = raycaster.intersectObjects(built.geoms, false);
  return hits.length ? { kind: "instance", name: hits[0].object.userData.instance } : null;
}

function onMove(e) {
  if (e.buttons) return;   // orbiting
  const target = hit(e);
  setHover(target);
  if (target) {
    const r = $("#view").getBoundingClientRect();
    tip.textContent = target.kind === "mount" ? t("Attach to {mount}", { mount: target.name }) : target.name;
    tip.style.left = `${e.clientX - r.left + 12}px`;
    tip.style.top = `${e.clientY - r.top + 12}px`;
    tip.hidden = false;
  }
}

function onUp(e) {
  const d = down;
  down = null;
  if (!d || d.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_SLOP) return;
  const target = hit(e);
  if (picking) {
    if (target && target.kind === "mount") {
      setHover(null);
      window.dispatchEvent(new CustomEvent("scrimmage:mount-picked", { detail: { name: target.name } }));
    }
    return;
  }
  if (target && target.kind === "instance") select(target.name);
}

// -- empty / read-only ------------------------------------------------------------------------
function updateEmpty() {
  const el = $("#view-empty");
  if (!el) return;
  const s = store.state;
  let msg = "";
  if (s && s.read_only) msg = t("No 3D view: this config is read-only.");
  else if (s && !s.parts.length) msg = t("No parts yet. Add one from the library.");
  else if (s && store.view && !built.geoms.length && s.errors.length) msg = t("No 3D view while the robot has errors.");
  el.textContent = msg;
  el.hidden = !msg;
}

// -- test helpers -----------------------------------------------------------------------------
function toScreen(v) {
  const p = v.clone().project(camera);
  if (p.z < -1 || p.z > 1) return null;
  const r = renderer.domElement.getBoundingClientRect();
  return { x: r.left + ((p.x + 1) / 2) * r.width, y: r.top + ((1 - p.y) / 2) * r.height };
}

function screenPos(name) {
  if (!renderer) return null;
  const m = built.mounts.find((g) => g.userData.mount === name);
  if (m) return toScreen(m.getWorldPosition(new THREE.Vector3()));
  // the first of the instance's geoms whose center is not hidden behind another part
  const mine = built.geoms.filter((g) => g.userData.instance === name);
  for (const g of mine) {
    const p = toScreen(g.getWorldPosition(new THREE.Vector3()));
    if (p && hit({ clientX: p.x, clientY: p.y }, true)?.name === name) return p;
  }
  const box = new THREE.Box3();
  for (const g of mine) box.expandByObject(g);
  return box.isEmpty() ? null : toScreen(box.getCenter(new THREE.Vector3()));
}
