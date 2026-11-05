// The live view: draws the robot and the field from /api/live/view and moves them with pose frames from the stream.
//
// setModel(view) builds a static group (the floor and world geoms with mover -1) and one THREE.Group per mover
// holding that mover's geoms at their local poses. applyFrame({poses, n}) sets each mover group's pose (ignored if
// n differs from the mover count). Scene frame is MuJoCo's: z up, quaternions w, x, y, z.
//
// A pneumatic cylinder (view.cylinders, assembler/view.py) is drawn between its two pins instead of by its part's
// geoms: a barrel (the bore's diameter, the retracted length long) from pin a toward pin b, and the rod (its
// diameter) the rest of the way, placed again with every frame. cylinderShapes() gives each one's drawn lengths.
//
// A sensor (view.sensors, assembler/view.py: a beam break, distance or color sensor) is a thin line between its two
// pins, placed again with every frame, drawn faint and lit (bright) while lightSensors() says it is on.
// sensorShapes() gives each one's ends in the world and whether it is lit.
//
// A mover below PARK_Z_HIDE (a parked ball sits at z = -20) is hidden. outline(i) draws mover i (a piece the Test
// tab's replay points at) with a colored rim, a larger back-faced copy of its geoms; outline(-1) takes it off.
// setScore(range) draws the test range's counts
// ({goals, fired, held}, or null for none) as an overlay in the top-left corner.
//
// Camera modes: follow (behind and above the robot's root mover, yaw-following, smoothed), top (straight down over
// the floor), driver (from the blue alliance wall at x = 0 looking +x, 1.7 m up), free (OrbitControls). Rendering is
// on demand. The follow camera tracks view.root_mover (-1: it looks at the floor center instead).
// Where WebGL is software (SwiftShader, llvmpipe: no GPU acceleration) the view draws at half resolution without
// antialiasing and at most every SOFTWARE_MS, so drawing doesn't starve the page.
// For tests: window.__scrimmage.live3d = {groups, lastSeq, camera, root, follow, software, background (#rrggbb:
// --watch-bg in the page's theme)} of the newest live view not yet
// disposed (the Drive tab's, or the Scenario run panel's Watch view while it is shown).

import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { meshGeometry, primitive, setPose, geomMaterial, buildFloor } from "./scene3d.js";
import { theme } from "./theme.js";

const FOLLOW_BACK = 2.2, FOLLOW_UP = 1.4, FOLLOW_LOOK_UP = 0.25, FOLLOW_SMOOTH = 0.18;
const DRIVER_HEIGHT = 1.7;
const MODES = ["follow", "top", "driver", "free"];
const PARK_Z_HIDE = -10;   // movers below this are parked, not drawn
const SOFTWARE_MS = 66;   // about 15 drawings a second
const OUTLINE = 0xd6336c, OUTLINE_SCALE = 1.5;   // outline(i)'s rim: a pink that stands out from a yellow ball and the field
const BARREL_RGBA = [0.55, 0.55, 0.6, 1], ROD_RGBA = [0.8, 0.7, 0.35, 1];   // the stock cylinder's steel and brass
const UP = new THREE.Vector3(0, 1, 0);   // three's cylinders run along y
const SENSOR_RADIUS = 0.003;   // m: a sensor's drawn line
const SENSOR_OFF = 0x9aa4ad, SENSOR_ON = 0xff2d2d, SENSOR_OFF_OPACITY = 0.6;   // a faint gray line; a bright red one

// Is WebGL drawn in software here? Asked once, from a throwaway context, before the real renderer picks antialiasing.
let softwareGL = null;
export function isSoftwareGL() {
  if (softwareGL === null) {
    softwareGL = false;
    try {
      const gl = document.createElement("canvas").getContext("webgl2") || document.createElement("canvas").getContext("webgl");
      if (gl) {
        const ext = gl.getExtension("WEBGL_debug_renderer_info");
        const name = String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || "");
        softwareGL = /swiftshader|llvmpipe|softpipe|software/i.test(name);
        const lose = gl.getExtension("WEBGL_lose_context");
        if (lose) lose.loseContext();
      }
    } catch { softwareGL = false; }
  }
  return softwareGL;
}

const views = [];   // the live views not yet disposed, oldest first

function hook() {
  if (!window.__scrimmage) return;
  const v = views[views.length - 1];
  if (!v) { delete window.__scrimmage.live3d; return; }
  Object.defineProperty(window.__scrimmage, "live3d", {
    configurable: true, enumerable: true,
    get: () => ({ groups: v.groups.length, lastSeq: v.lastSeq, camera: v.mode, root: v.rootMover, follow: v.followTarget.toArray(),
                  software: v.software, background: `#${v.scene.background.getHexString()}` }),
  });
}

const yawOf = (q) => Math.atan2(2 * (q[0] * q[3] + q[1] * q[2]), 1 - 2 * (q[2] * q[2] + q[3] * q[3]));   // q = w, x, y, z

export class LiveView {
  constructor(container) {
    this.container = container;
    this.software = isSoftwareGL();
    this.renderer = new THREE.WebGLRenderer({ antialias: !this.software });
    this.renderer.setPixelRatio(this.software ? 0.5 : window.devicePixelRatio || 1);
    this.lastDraw = 0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.className = "view-canvas";
    this.renderer.domElement.setAttribute("role", "img");
    this.renderer.domElement.setAttribute("aria-label", "3D view of the field and robot");

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(theme.color("--watch-bg"));   // the page's theme (app.css)
    this.offTheme = theme.onChange(() => {
      this.scene.background.set(theme.color("--watch-bg"));
      this.requestRender();
    });
    const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f96, 2.0);
    hemi.position.set(0, 0, 1);
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(3, -2, 6);
    this.scene.add(hemi, sun, sun.target);

    this.camera = new THREE.PerspectiveCamera(50, 1, 0.02, 200);
    this.camera.up.set(0, 0, 1);   // before OrbitControls, which reads camera.up once
    this.camera.position.set(-2, -3, 3);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.addEventListener("change", () => this.requestRender());

    this.model = new THREE.Group();
    this.scene.add(this.model);
    this.groups = [];
    this.disposables = [];
    this.outlines = [];       // the outline(i) rim's meshes (their geometries are the mover's own)
    this.outlined = -1;
    this.cylinders = [];      // {spec (the view's), barrel, rod (meshes), lengths: [barrel, rod] as drawn}
    this.sensors = [];        // {spec (the view's), mesh, lit, ends: [a, b] in the world as drawn}
    this.floorInfo = { pos: [8, 4, 0], size: [8, 4] };
    this.rootMover = 0;
    this.mode = "follow";
    this.lastSeq = -1;
    this.followYaw = null;
    this.followTarget = new THREE.Vector3();
    this.pending = false;
    this.disposed = false;

    this.score = document.createElement("div");
    this.score.className = "view-score";
    this.score.hidden = true;
    container.replaceChildren(this.renderer.domElement, this.score);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
    this.onLost = (e) => e.preventDefault();
    this.renderer.domElement.addEventListener("webglcontextlost", this.onLost);

    views.push(this);
    hook();
    this.setCamera("follow");
    this.resize();
  }

  clear() {
    this.outline(-1);
    for (const d of this.disposables) d.dispose();
    this.disposables = [];
    this.model.clear();
    this.groups = [];
    this.cylinders = [];
    this.sensors = [];
  }

  setModel(view) {
    this.clear();
    view = view || {};
    const stat = new THREE.Group();
    this.model.add(stat);
    if (view.floor) {
      const f = buildFloor(view.floor);
      stat.add(f.group);
      this.disposables.push(...f.disposables);
      this.floorInfo = view.floor;
    }
    this.groups = (view.movers || []).map(() => {
      const g = new THREE.Group();
      this.model.add(g);
      return g;
    });
    const meshGeoms = (view.meshes || []).map(() => null);   // built on first use, shared between geoms
    const cylinderParts = new Set((view.cylinders || []).map((c) => c.name));
    const add = (gd) => {
      if (gd.rgba && gd.rgba[3] <= 0) return;
      if (gd.instance && cylinderParts.has(gd.instance)) return;   // drawn between its pins instead (below)
      let geo;
      if (gd.type === "mesh") {
        const m = view.meshes && view.meshes[gd.mesh];
        if (!m) return;
        if (!meshGeoms[gd.mesh]) { meshGeoms[gd.mesh] = meshGeometry(m); this.disposables.push(meshGeoms[gd.mesh]); }
        geo = meshGeoms[gd.mesh];
      } else {
        const mover = gd.mover >= 0 && view.movers ? view.movers[gd.mover] : null;
        geo = primitive(gd.type, gd.size, !!(mover && String(mover.name).startsWith("ball.")));   // a range ball
        if (!geo) return;
        this.disposables.push(geo);
      }
      const mat = geomMaterial(gd.rgba, gd.type === "mesh");
      this.disposables.push(mat);
      const mesh = new THREE.Mesh(geo, mat);
      const parent = gd.mover >= 0 ? this.groups[gd.mover] : stat;
      if (!parent) return;
      if (gd.mover >= 0) setPose(mesh, gd.lpos, gd.lquat);
      else setPose(mesh, gd.pos, gd.quat);   // static geoms sit at their world pose
      parent.add(mesh);
    };
    for (const gd of view.geoms || []) add(gd);
    for (const gd of view.world_geoms || []) add(gd);
    const tube = (radius, rgba) => {
      const geo = new THREE.CylinderGeometry(radius, radius, 1, 24), mat = geomMaterial(rgba);
      this.disposables.push(geo, mat);
      const mesh = new THREE.Mesh(geo, mat);
      stat.add(mesh);
      return mesh;
    };
    this.cylinders = (view.cylinders || []).map((c) => ({ spec: c, barrel: tube(c.bore / 2, BARREL_RGBA),
      rod: tube(c.rod / 2, ROD_RGBA), lengths: [0, 0] }));
    this.sensors = (view.sensors || []).map((sp) => {
      const geo = new THREE.CylinderGeometry(SENSOR_RADIUS, SENSOR_RADIUS, 1, 8);
      const mat = new THREE.MeshBasicMaterial({ color: SENSOR_OFF, transparent: true, opacity: SENSOR_OFF_OPACITY });
      this.disposables.push(geo, mat);
      const mesh = new THREE.Mesh(geo, mat);
      stat.add(mesh);
      return { spec: sp, mesh, lit: false, ends: [[0, 0, 0], [0, 0, 0]] };
    });
    this.placeCylinders();
    this.rootMover = Number.isInteger(view.root_mover) ? view.root_mover : -1;   // -1: follow falls back to the floor
    this.lastSeq = -1;
    this.followYaw = null;
    this.setCamera(this.mode);
  }

  applyFrame(frame) {
    if (frame.n !== this.groups.length) return;
    const p = frame.poses;
    for (let i = 0; i < frame.n; i++) {
      const o = i * 7;
      setPose(this.groups[i], [p[o], p[o + 1], p[o + 2]], [p[o + 3], p[o + 4], p[o + 5], p[o + 6]]);
      this.groups[i].visible = p[o + 2] >= PARK_Z_HIDE;
    }
    this.placeCylinders();
    this.lastSeq = frame.seq;
    this.requestRender();
  }

  // A pin ([mover, pos], assembler/view.py) where its mover group is now.
  pin([mover, pos]) {
    const v = new THREE.Vector3(pos[0], pos[1], pos[2]);
    const g = mover >= 0 ? this.groups[mover] : null;   // -1: a body that never moves, pos in the world
    return g ? v.applyQuaternion(g.quaternion).add(g.position) : v;
  }

  // Each cylinder between its pins where the mover groups are now: the barrel from pin a, the rod on to pin b; and
  // each sensor's line between its two pins.
  placeCylinders() {
    const pin = (p) => this.pin(p);
    const place = (mesh, from, along, start, length) => {
      mesh.visible = length > 1e-6;
      mesh.position.copy(from).addScaledVector(along, start + length / 2);
      mesh.quaternion.setFromUnitVectors(UP, along);
      mesh.scale.set(1, Math.max(length, 1e-6), 1);
    };
    for (const c of this.cylinders) {
      const a = pin(c.spec.a), along = pin(c.spec.b).sub(a);
      const apart = along.length();
      if (apart < 1e-9) along.set(1, 0, 0); else along.divideScalar(apart);
      const barrel = Math.min(c.spec.retracted, apart), rod = apart - barrel;
      place(c.barrel, a, along, 0, barrel);
      place(c.rod, a, along, barrel, rod);
      c.lengths = [barrel, rod];
    }
    for (const sn of this.sensors) {
      const a = pin(sn.spec.a), b = pin(sn.spec.b), along = b.clone().sub(a);
      const length = along.length();
      if (length < 1e-9) along.set(1, 0, 0); else along.divideScalar(length);
      place(sn.mesh, a, along, 0, length);
      sn.ends = [a.toArray(), b.toArray()];
    }
  }

  // Light each sensor whose `on(spec)` is true (the replay's sensor.<name> signal at the frame shown); dim the rest.
  lightSensors(on) {
    for (const sn of this.sensors) {
      const lit = !!on(sn.spec);
      if (lit === sn.lit) continue;
      sn.lit = lit;
      sn.mesh.material.color.setHex(lit ? SENSOR_ON : SENSOR_OFF);
      sn.mesh.material.opacity = lit ? 1 : SENSOR_OFF_OPACITY;
      this.requestRender();
    }
  }

  sensorShapes() {
    return this.sensors.map((sn) => ({ name: sn.spec.name, lit: sn.lit, a: sn.ends[0], b: sn.ends[1] }));
  }

  cylinderShapes() {
    return this.cylinders.map((c) => ({ name: c.spec.name, barrel: c.lengths[0], rod: c.lengths[1] }));
  }

  outline(i) {
    for (const m of this.outlines) { m.removeFromParent(); m.material.dispose(); }
    this.outlines = [];
    const g = i >= 0 ? this.groups[i] : null;
    this.outlined = g ? i : -1;
    for (const mesh of g ? g.children.filter((c) => c.isMesh) : []) {
      const rim = new THREE.Mesh(mesh.geometry, new THREE.MeshBasicMaterial({ color: OUTLINE, side: THREE.BackSide }));
      rim.position.copy(mesh.position);
      rim.quaternion.copy(mesh.quaternion);
      rim.scale.copy(mesh.scale).multiplyScalar(OUTLINE_SCALE);
      g.add(rim);
      this.outlines.push(rim);
    }
    this.requestRender();
  }

  setScore(range) {
    if (!range || !range.goals) { this.score.hidden = true; this.score.textContent = ""; return; }
    const lines = Object.entries(range.goals).map(([name, n]) => `${name} ${n}`);
    lines.push(`fired ${range.fired} · held ${range.held}`);
    this.score.textContent = lines.join("\n");
    this.score.hidden = false;
  }

  setCamera(mode) {
    if (!MODES.includes(mode)) return;
    this.mode = mode;
    this.controls.enabled = mode === "free";
    if (mode === "top") this.camera.up.set(0, 1, 0); else this.camera.up.set(0, 0, 1);   // top: +y up the screen
    if (mode === "free") {
      // start the orbit from a view over the field
      const [cx, cy] = this.floorInfo.pos;
      this.controls.target.set(cx, cy, 0);
      this.camera.position.set(cx - 3, cy - 8, 6);
      this.controls.update();
    } else if (mode === "follow") {
      this.followYaw = null;
    }
    this.placeCamera(true);
    this.requestRender();
  }

  rootPose() {
    const g = this.groups[this.rootMover];
    if (!g) return null;
    const q = g.quaternion;   // x, y, z, w
    return { pos: g.position, yaw: yawOf([q.w, q.x, q.y, q.z]) };
  }

  // Positions the camera for the current mode; returns true while a smoothed follow camera is still moving.
  placeCamera(snap) {
    const cam = this.camera;
    const [cx, cy] = this.floorInfo.pos, [hx, hy] = this.floorInfo.size;
    if (this.mode === "top") {
      const fov = THREE.MathUtils.degToRad(cam.fov);
      const need = Math.max(hy / Math.tan(fov / 2), hx / (Math.tan(fov / 2) * cam.aspect));
      cam.position.set(cx, cy, need * 1.05);
      cam.lookAt(cx, cy, 0);
    } else if (this.mode === "driver") {
      cam.position.set(0, cy, DRIVER_HEIGHT);
      cam.lookAt(cx, cy, 0.4);
    } else if (this.mode === "follow") {
      const r = this.rootPose();
      if (!r) { cam.position.set(cx - 3, cy - 3, 3); cam.lookAt(cx, cy, 0); return false; }
      let moving = false;
      if (this.followYaw === null || snap) {
        this.followYaw = r.yaw;
        this.followTarget.copy(r.pos);
      } else {
        let d = r.yaw - this.followYaw;
        d = Math.atan2(Math.sin(d), Math.cos(d));   // shortest way round
        this.followYaw += d * FOLLOW_SMOOTH;
        this.followTarget.lerp(r.pos, FOLLOW_SMOOTH);
        moving = Math.abs(d) > 1e-3 || this.followTarget.distanceTo(r.pos) > 1e-3;
      }
      const t = this.followTarget, y = this.followYaw;
      cam.position.set(t.x - Math.cos(y) * FOLLOW_BACK, t.y - Math.sin(y) * FOLLOW_BACK, t.z + FOLLOW_UP);
      cam.lookAt(t.x, t.y, t.z + FOLLOW_LOOK_UP);
      return moving;
    }
    return false;
  }

  resize() {
    if (this.disposed) return;
    const w = Math.max(1, this.container.clientWidth), hgt = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(w, hgt, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / hgt;
    this.camera.updateProjectionMatrix();
    this.placeCamera(true);
    this.requestRender();
  }

  requestRender() {
    if (this.pending || this.disposed) return;
    this.pending = true;
    const wait = this.software ? Math.max(0, this.lastDraw + SOFTWARE_MS - performance.now()) : 0;
    const go = () => requestAnimationFrame(() => {
      this.pending = false;
      if (this.disposed) return;
      const moving = this.placeCamera(false);
      this.renderer.render(this.scene, this.camera);
      this.lastDraw = performance.now();
      if (moving) this.requestRender();
    });
    if (wait > 0) setTimeout(go, wait); else go();
  }

  dispose() {
    this.disposed = true;
    this.offTheme();
    this.observer.disconnect();
    this.renderer.domElement.removeEventListener("webglcontextlost", this.onLost);
    this.controls.dispose();
    this.clear();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    const k = views.indexOf(this);
    if (k >= 0) views.splice(k, 1);
    hook();
  }
}
