// The Drive tab's mini-field: a small top-down drawing of the field (fieldpick.js's drawing) with the robot and the
// movable objects where the latest frame has them.
//
// Press on the field and drag: the robot is set there, facing the way you dragged (a click keeps its heading).
// Press on a free body such as a game piece (drawn at its frame position) and drag: it moves to where you let go.
// Named static geoms (walls, obstacles) are movable by MCP but stay fixed here.
//   new MiniField(canvas, view)          view: /api/live/view (world_geoms, geoms)
//   setView(view)                        the robot or field changed
//   setRobot(x, y, headingDeg)           null x: no robot shown
//   setObjects([{name, x, y, yaw?}])     the free bodies to draw and drag
//   onSetPose(cb(x, y, headingDeg)), onMoveObject(cb(name, x, y))
//   setEnabled(on)                       off: presses are ignored (no drag starts) and the canvas says aria-disabled
// Test hook: window.__scrimmage.miniField = {toScreen(x, y) -> {x, y} page pixels, robot, objects, enabled}.

import { fieldGeometry, drawField, fieldPoly, outline, hull } from "./fieldpick.js";

const DRAG_PX = 6;          // a press that moves less than this is a click
const OBJECT_HIT_PX = 5;    // extra grab margin around an object's marker
const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const normDeg = (d) => { let a = ((d + 180) % 360 + 360) % 360 - 180; if (a === -180) a = 180; return a; };

export class MiniField {
  constructor(canvas, view) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.robot = null;          // {x, y, heading}
    this.objects = [];          // [{name, x, y, yaw}]
    this.drag = null;           // {kind: "pose" | "object", ...}
    this.setPoseCb = null;
    this.moveObjectCb = null;
    this.width = 0;
    this.enabled = true;
    this.setView(view);
    canvas.addEventListener("pointerdown", (e) => this.down(e));
    canvas.addEventListener("pointermove", (e) => this.move(e));
    canvas.addEventListener("pointerup", (e) => this.up(e));
    canvas.addEventListener("pointercancel", () => { this.drag = null; this.draw(); });
    this.observer = new ResizeObserver(() => this.layout());
    this.observer.observe(canvas.parentElement || canvas);
    const self = this;
    if (window.__scrimmage) {
      window.__scrimmage.miniField = {
        toScreen(x, y) { const rc = self.canvas.getBoundingClientRect(); const [px, py] = self.toPx(x, y); return { x: rc.left + px, y: rc.top + py }; },
        get robot() { return self.robot ? { ...self.robot } : null; },
        get objects() { return self.objects.map((o) => ({ ...o })); },
        get enabled() { return self.enabled; },
      };
    }
  }

  setView(view) {
    const v = view || {};
    this.geo = fieldGeometry(v);
    // free bodies (movers of kind "body") move on the mini-field; static geoms, named or not, are drawn fixed
    const movers = v.movers || [];
    const isBody = (i) => i >= 0 && !!movers[i] && movers[i].kind === "body";
    this.staticGeo = { ...this.geo, outlines: this.geo.outlines.filter((o) => !isBody(o.mover)) };
    // each movable object's outline, relative to its center (drawn where the frame puts it; its turning isn't shown)
    this.shapes = new Map();
    const byMover = new Map();
    for (const o of this.geo.outlines) {
      if (!isBody(o.mover)) continue;
      if (!byMover.has(o.mover)) byMover.set(o.mover, []);
      byMover.get(o.mover).push(...o.poly);
    }
    for (const [i, pts] of byMover) {
      const name = movers[i].name;
      const poly = hull(pts);
      if (!name || poly.length < 3) continue;
      const cx = poly.reduce((s, q) => s + q[0], 0) / poly.length, cy = poly.reduce((s, q) => s + q[1], 0) / poly.length;
      this.shapes.set(name, poly.map(([x, y]) => [x - cx, y - cy]));
    }
    // the robot's size: its footprint's reach from its center
    const robot = hull((v.geoms || []).map((g) => outline(g, v.meshes)).filter((p) => p.length >= 3).flat());
    if (robot.length >= 3) {
      const cx = robot.reduce((s, q) => s + q[0], 0) / robot.length, cy = robot.reduce((s, q) => s + q[1], 0) / robot.length;
      this.robotSize = Math.max(...robot.map((q) => Math.hypot(q[0] - cx, q[1] - cy)));
    } else {
      this.robotSize = 0.4;
    }
    this.layout(true);
  }

  layout(force = false) {
    const parent = this.canvas.parentElement;
    const W = Math.max(120, Math.floor((parent && parent.clientWidth) || 300));
    if (W === this.width && !force) return;
    const b = this.geo.floor.bounds;
    this.width = W;
    this.scale = W / (b.maxx - b.minx);
    this.height = Math.round((b.maxy - b.miny) * this.scale);
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(W * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.canvas.style.width = `${W}px`;
    this.canvas.style.height = `${this.height}px`;
    this.dpr = dpr;
    this.draw();
  }

  toPx(x, y) {
    const b = this.geo.floor.bounds;
    return [(x - b.minx) * this.scale, (b.maxy - y) * this.scale];
  }

  toField(px, py) {
    const b = this.geo.floor.bounds;
    return [b.minx + px / this.scale, b.maxy - py / this.scale];
  }

  setRobot(x, y, headingDeg) {
    this.robot = typeof x === "number" ? { x, y, heading: headingDeg } : null;
    if (!this.drag) this.scheduleDraw();
  }

  setObjects(list) {
    this.objects = (list || []).map((o) => ({ name: o.name, x: o.x, y: o.y, yaw: o.yaw ?? 0 }));
    if (!this.drag) this.scheduleDraw();
  }

  // Live frames update the robot and objects ~30 times a second: draw at most once per animation frame, and not
  // while the canvas is hidden (another tab is shown); show() draws whatever changed meanwhile.
  scheduleDraw() {
    if (this.drawPending) return;
    if (!this.canvas.isConnected || this.canvas.offsetParent === null) { this.stale = true; return; }
    this.drawPending = true;
    requestAnimationFrame(() => { this.drawPending = false; this.stale = false; this.draw(); });
  }

  show() { if (this.stale) this.scheduleDraw(); }

  setEnabled(on) {
    on = !!on;
    this.canvas.setAttribute("aria-disabled", String(!on));
    if (on === this.enabled) return;
    this.enabled = on;
    if (!on && this.drag) { this.drag = null; this.draw(); }   // a drag in progress is dropped, not sent
  }

  onSetPose(cb) { this.setPoseCb = cb; }
  onMoveObject(cb) { this.moveObjectCb = cb; }

  // The object's outline at (x, y) in field coordinates (a small square when the view has no outline for it).
  objectPoly(o, x = o.x, y = o.y) {
    const rel = this.shapes.get(o.name);
    const r = 5 / this.scale;
    return (rel || [[-r, -r], [r, -r], [r, r], [-r, r]]).map(([u, v]) => [x + u, y + v]);
  }

  // Pixels from (px, py) to the object's outline (0 inside it).
  objectDistance(o, px, py) {
    const pts = this.objectPoly(o).map(([x, y]) => this.toPx(x, y));
    let inside = false, best = Infinity;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
      const dx = xj - xi, dy = yj - yi, len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, ((px - xi) * dx + (py - yi) * dy) / len)) : 0;
      best = Math.min(best, Math.hypot(px - (xi + t * dx), py - (yi + t * dy)));
    }
    return inside ? 0 : best;
  }

  at(e) {
    const rc = this.canvas.getBoundingClientRect();
    return [e.clientX - rc.left, e.clientY - rc.top];
  }

  down(e) {
    if (e.button !== 0 || !this.enabled) return;
    e.preventDefault();
    const [px, py] = this.at(e);
    // the nearest object marker under the pointer wins over setting the pose
    let hit = null, best = Infinity;
    for (const o of this.objects) {
      const d = this.objectDistance(o, px, py);
      if (d <= OBJECT_HIT_PX && d < best) { hit = o; best = d; }
    }
    const [fx, fy] = this.toField(px, py);
    this.drag = hit ? { kind: "object", name: hit.name, px, py, at: [px, py], moved: false, off: [hit.x - fx, hit.y - fy] }
      : { kind: "pose", px, py, at: [px, py], moved: false };
    this.canvas.setPointerCapture(e.pointerId);
    this.draw();
  }

  move(e) {
    if (!this.drag) return;
    const [px, py] = this.at(e);
    this.drag.at = [px, py];
    if (Math.hypot(px - this.drag.px, py - this.drag.py) >= DRAG_PX) this.drag.moved = true;
    this.draw();
  }

  up(e) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    const [px, py] = this.at(e);
    if (Math.hypot(px - d.px, py - d.py) >= DRAG_PX) d.moved = true;
    if (d.kind === "object") {
      if (d.moved && this.moveObjectCb) {
        const [x, y] = this.toField(px, py);
        this.moveObjectCb(d.name, round(x + d.off[0], 3), round(y + d.off[1], 3));
      }
    } else if (this.setPoseCb) {
      const [x, y] = this.toField(d.px, d.py);
      const heading = d.moved ? normDeg(Math.atan2(-(py - d.py), px - d.px) * 180 / Math.PI)
        : this.robot ? this.robot.heading : 0;
      this.setPoseCb(round(x, 3), round(y, 3), round(heading, 1));
    }
    this.draw();
  }

  draw() {
    if (!this.width) return;
    const ctx = this.ctx, W = this.width, H = this.height;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = "#dfe3e8";
    ctx.fillRect(0, 0, W, H);
    const toPx = (x, y) => this.toPx(x, y);
    drawField(ctx, this.staticGeo, toPx);
    const d = this.drag;
    for (const o of this.objects) {
      const moving = d && d.kind === "object" && d.name === o.name && d.moved;
      const at = moving ? this.toField(...d.at) : null;
      const [x, y] = moving ? [at[0] + d.off[0], at[1] + d.off[1]] : [o.x, o.y];
      fieldPoly(ctx, toPx, this.objectPoly(o, x, y), moving ? "rgba(232,89,12,.45)" : "rgba(212,160,23,.6)", "#8a6500", 1);
    }
    if (this.robot) this.robotMarker(this.robot.x, this.robot.y, this.robot.heading, false);
    if (d && d.kind === "pose") {
      const [x, y] = this.toField(d.px, d.py);
      const heading = d.moved ? Math.atan2(-(d.at[1] - d.py), d.at[0] - d.px) * 180 / Math.PI : this.robot ? this.robot.heading : 0;
      this.robotMarker(x, y, heading, true);
    }
  }

  robotMarker(x, y, headingDeg, ghost) {
    const ctx = this.ctx;
    const [px, py] = this.toPx(x, y);
    const r = Math.max(5, this.robotSize * this.scale);
    const a = -(headingDeg || 0) * Math.PI / 180;
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(a);
    // a square footprint with an arrow out of its front
    const s = r / Math.SQRT2;
    fieldPoly(ctx, (u, v) => [u, -v], [[-s, -s], [s, -s], [s, s], [-s, s]],
      ghost ? "rgba(232,89,12,.25)" : "rgba(43,108,176,.35)", ghost ? "#e8590c" : "#2b6cb0", 1.5, ghost ? [4, 3] : null);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(r + 6, 0);
    ctx.strokeStyle = ghost ? "#e8590c" : "#1f5592"; ctx.lineWidth = 2; ctx.stroke();
    ctx.beginPath(); ctx.moveTo(r + 9, 0); ctx.lineTo(r + 3, -3.5); ctx.lineTo(r + 3, 3.5); ctx.closePath();
    ctx.fillStyle = ghost ? "#e8590c" : "#1f5592"; ctx.fill();
    ctx.restore();
  }

  dispose() {
    this.observer.disconnect();
    if (window.__scrimmage && window.__scrimmage.miniField) delete window.__scrimmage.miniField;
  }
}
