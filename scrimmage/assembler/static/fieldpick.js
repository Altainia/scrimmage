// The field picker: a top-down 2D drawing of the robot's world (x right, y up, field meters) for a pose check.
// Click to place the target; keep the button down and drag to aim the heading (or drag from the target to
// re-aim it); the tolerances show as a circle (tol_xy) and a wedge (tol_heading); the scenario's other pose
// checks are drawn as ghosts. OK sends one set_check; Cancel sends nothing.
//
// Field geometry comes from /api/scenario/view: world_geoms (see view.py's _world_geoms for the size
// conventions) and the robot geoms at the start pose (their projected outline is the robot's footprint).
// fieldGeometry(view) and drawField(ctx, geometry, toCanvas) draw the field itself (floor, a grid in the chosen units,
// world geoms, axes); the Drive tab's mini-field (minifield.js) and the robot code panel's start picker (code.js)
// draw with them too, the start picker also with drawSides (the plain red / blue field) and robotFootprint. Their
// colors are app.css's --field-* tokens (theme.color), so each drawing redraws on theme.onChange.

import { h, api, fmt, showMeasure, readMeasure, measureError } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { sc } from "./scenario.js";

const DEFAULT_TOL_XY = 0.1, DEFAULT_TOL_H = 5;   // checks.py's defaults
const MARGIN = 0.45;                               // meters of border around the floor (walls sit there)
const KEYS = ["x", "y", "heading", "tol_xy", "tol_heading"];

let view = null;          // /api/scenario/view, or {} when the robot doesn't load
let viewSeq = 0;
window.addEventListener("scrimmage:scenario-robot", () => { view = null; });

async function loadView() {
  if (view) return view;
  const seq = ++viewSeq;
  const { status, data } = await api("GET", "/api/scenario/view");
  if (status !== 200) return {};          // not cached: the next open asks again
  if (seq === viewSeq) view = data;
  return data;
}

// -- geometry -------------------------------------------------------------------------------------------
function rotate(q, v) {   // q = [w, x, y, z]
  const [w, x, y, z] = q;
  const [vx, vy, vz] = v;
  const tx = 2 * (y * vz - z * vy), ty = 2 * (z * vx - x * vz), tz = 2 * (x * vy - y * vx);
  return [vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx)];
}

export function hull(pts) {      // Andrew's monotone chain; pts [[x, y], ...] -> counter-clockwise polygon
  const p = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

function decodeVerts(b64) {
  const bin = atob(b64);
  const buf = new ArrayBuffer(bin.length);
  const u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Float32Array(buf);
}

// A geom's local points (enough to outline its top-down shadow), before its pos / quat.
function localPoints(g, meshes) {
  const s = g.size || [];
  const ring = (r, z, n = 16) => Array.from({ length: n }, (_, k) => [r * Math.cos(2 * Math.PI * k / n), r * Math.sin(2 * Math.PI * k / n), z]);
  switch (g.type) {
    case "plane": return [[-s[0], -s[1], 0], [s[0], -s[1], 0], [s[0], s[1], 0], [-s[0], s[1], 0]];
    case "box": {
      const out = [];
      for (const a of [-1, 1]) for (const b of [-1, 1]) for (const c of [-1, 1]) out.push([a * s[0], b * s[1], c * s[2]]);
      return out;
    }
    case "cylinder": return [...ring(s[0], -s[1]), ...ring(s[0], s[1])];
    case "capsule": return [...ring(s[0], -s[1] - s[0]), ...ring(s[0], s[1] + s[0]), ...ring(s[0], -s[1]), ...ring(s[0], s[1])];
    case "sphere": return [...ring(s[0], 0), [0, 0, s[0]], [0, 0, -s[0]]];
    case "ellipsoid": {
      const out = [];
      for (let k = 0; k < 16; k++) {
        const t = 2 * Math.PI * k / 16;
        out.push([s[0] * Math.cos(t), s[1] * Math.sin(t), 0], [s[0] * Math.cos(t), 0, s[2] * Math.sin(t)], [0, s[1] * Math.cos(t), s[2] * Math.sin(t)]);
      }
      return out;
    }
    case "mesh": {
      const m = meshes && g.mesh != null ? meshes[g.mesh] : null;
      if (!m) return [];
      if (!m._verts) m._verts = decodeVerts(m.vert);
      const v = m._verts, n = v.length / 3, stride = Math.max(1, Math.floor(n / 400));
      const out = [];
      for (let i = 0; i < n; i += stride) out.push([v[3 * i], v[3 * i + 1], v[3 * i + 2]]);
      return out;
    }
    default: return [];
  }
}

// The geom's top-down outline in field coordinates: [[x, y], ...].
export function outline(g, meshes) {
  const q = g.quat || [1, 0, 0, 0], p = g.pos || [0, 0, 0];
  return hull(localPoints(g, meshes).map((v) => { const r = rotate(q, v); return [r[0] + p[0], r[1] + p[1]]; }));
}

function floorRect(v) {
  const plane = (v.world_geoms || []).find((g) => g.type === "plane" && g.size && g.size[0] > 0 && g.size[1] > 0);
  const f = plane ? { pos: plane.pos, size: plane.size } : v.floor || { pos: [8.27, 4.035, 0], size: [8.27, 4.035] };
  const hx = f.size[0] > 0 ? f.size[0] : 8.27, hy = f.size[1] > 0 ? f.size[1] : 4.035;
  return { minx: f.pos[0] - hx, maxx: f.pos[0] + hx, miny: f.pos[1] - hy, maxy: f.pos[1] + hy, plane };
}

// The field to draw: floor = {minx, maxx, miny, maxy, poly, bounds} (bounds: the floor plus a border for the walls)
// and outlines = [{poly, rgba, mover, name}] for the world geoms other than the floor (mover: the view's mover index,
// -1 for geoms that never move; they are outlined at their start pose).
export function fieldGeometry(view) {
  const v = view || {};
  const fr = floorRect(v);
  const poly = fr.plane ? outline(fr.plane) : [[fr.minx, fr.miny], [fr.maxx, fr.miny], [fr.maxx, fr.maxy], [fr.minx, fr.maxy]];
  const bounds = { minx: fr.minx - MARGIN, maxx: fr.maxx + MARGIN, miny: fr.miny - MARGIN, maxy: fr.maxy + MARGIN };
  const outlines = (v.world_geoms || []).filter((g) => g.type !== "plane")
    .map((g) => ({ poly: outline(g, v.meshes), rgba: g.rgba, mover: Number.isInteger(g.mover) ? g.mover : -1, name: g.name || "" }))
    .filter((o) => o.poly.length >= 3);
  return { floor: { minx: fr.minx, maxx: fr.maxx, miny: fr.miny, maxy: fr.maxy, poly, bounds }, outlines };
}

// A polygon in field coordinates, filled and / or stroked.
export function fieldPoly(ctx, toCanvas, pts, fill, stroke, width = 1, dash = null) {
  ctx.beginPath();
  pts.forEach(([x, y], k) => { const [px, py] = toCanvas(x, y); if (k) ctx.lineTo(px, py); else ctx.moveTo(px, py); });
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.setLineDash(dash || []); ctx.strokeStyle = stroke; ctx.lineWidth = width; ctx.stroke(); ctx.setLineDash([]); }
}

// The field's grid in the chosen units: a line every 5 ft in Imperial (1 m lines labeled in feet would read badly),
// every 1 m in Metric. {step: meters between lines, size: the step in the shown unit, unit}.
export function fieldGrid(system = units.system) {
  return system === "imperial" ? { step: 5 * 0.3048, size: 5, unit: "ft" } : { step: 1, size: 1, unit: "m" };
}

// The labels along one edge from lo to hi (meters): [{at: meters, text}], every `every` grid steps. With `named`,
// the last one names the unit ("50 ft"), so the drawing says what its numbers are (the bottom edge's: the left
// edge's labels are right-aligned into the narrow border, where a unit wouldn't fit).
export function gridLabels(lo, hi, every, grid = fieldGrid(), named = false) {
  const out = [];
  for (let k = Math.ceil(lo / grid.step / every - 1e-9) * every; k * grid.step <= hi + 1e-9; k += every) {
    out.push({ at: k * grid.step, text: `${k * grid.size}` });
  }
  if (named && out.length) out[out.length - 1].text += ` ${grid.unit}`;
  return out;
}

// Draws the floor with a grid and its labels in the chosen units (fieldGrid), the world geoms and an x / y legend.
// toCanvas(x, y) -> [px, py] in the context's (CSS pixel) coordinates; the caller paints the background.
export function drawField(ctx, geometry, toCanvas) {
  const { floor: fr, outlines } = geometry;
  const [ox] = toCanvas(0, 0), [ux] = toCanvas(1, 0);
  const scale = ux - ox;   // pixels per meter
  const grid = fieldGrid();
  const c = theme.color;
  fieldPoly(ctx, toCanvas, fr.poly, c("--field-floor"), c("--field-edge"));
  ctx.save();
  ctx.beginPath();
  fr.poly.forEach(([x, y], k) => { const [px, py] = toCanvas(x, y); if (k) ctx.lineTo(px, py); else ctx.moveTo(px, py); });
  ctx.closePath();
  ctx.clip();
  ctx.strokeStyle = c("--field-grid");
  ctx.lineWidth = 1;
  const [, top] = toCanvas(0, fr.maxy), [, bottom] = toCanvas(0, fr.miny), [left] = toCanvas(fr.minx, 0), [right] = toCanvas(fr.maxx, 0);
  for (const { at } of gridLabels(fr.minx, fr.maxx, 1, grid)) { const [px] = toCanvas(at, 0); ctx.beginPath(); ctx.moveTo(px + 0.5, top); ctx.lineTo(px + 0.5, bottom); ctx.stroke(); }
  for (const { at } of gridLabels(fr.miny, fr.maxy, 1, grid)) { const [, py] = toCanvas(0, at); ctx.beginPath(); ctx.moveTo(left, py + 0.5); ctx.lineTo(right, py + 0.5); ctx.stroke(); }
  ctx.restore();
  // the grid's labels along the bottom and left edges (every other line when they would crowd)
  ctx.fillStyle = c("--plot-label");
  ctx.font = "10px system-ui, sans-serif";
  const every = grid.step * scale < 30 ? 2 : 1;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const { at, text } of gridLabels(fr.minx, fr.maxx, every, grid, true)) { const [px, py] = toCanvas(at, fr.miny); ctx.fillText(text, px, py + 3 + MARGIN * scale * 0.25); }
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const { at, text } of gridLabels(fr.miny, fr.maxy, every, grid)) { const [px, py] = toCanvas(fr.minx, at); ctx.fillText(text, px - 4 - MARGIN * scale * 0.2, py); }
  // the world's boxes, cylinders, ...
  for (const o of outlines) {
    const [r, gg, bb, a] = (o.rgba || [0.6, 0.6, 0.6, 1]).map((c, k) => (k < 3 ? Math.round(c * 255) : c));
    fieldPoly(ctx, toCanvas, o.poly, `rgba(${r},${gg},${bb},${Math.max(0.55, a)})`, `rgba(${Math.round(r * 0.6)},${Math.round(gg * 0.6)},${Math.round(bb * 0.6)},0.9)`);
  }
  // axes legend, in the floor's bottom left corner
  ctx.save();
  const [lx, ly] = toCanvas(fr.minx, fr.miny);
  ctx.translate(lx + 12, ly - 12);
  ctx.lineWidth = 2;
  ctx.strokeStyle = c("--field-axis-x"); ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(26, 0); ctx.stroke();
  ctx.strokeStyle = c("--field-axis-y"); ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, -26); ctx.stroke();
  ctx.fillStyle = c("--text"); ctx.font = "600 10px system-ui, sans-serif"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
  ctx.fillText("x", 29, 0); ctx.textAlign = "center"; ctx.fillText("y", 0, -33);
  ctx.restore();
}

// The plain field's halves: blue (x below the middle, the blue alliance wall at x = 0) and red, with the center
// line and BLUE / RED labels, over the floor drawField drew. The start picker uses it when no [range] is chosen.
export function drawSides(ctx, geometry, toCanvas) {
  const fr = geometry.floor, mid = (fr.minx + fr.maxx) / 2, c = theme.color;
  fieldPoly(ctx, toCanvas, [[fr.minx, fr.miny], [mid, fr.miny], [mid, fr.maxy], [fr.minx, fr.maxy]], c("--field-blue", 0.16), null);
  fieldPoly(ctx, toCanvas, [[mid, fr.miny], [fr.maxx, fr.miny], [fr.maxx, fr.maxy], [mid, fr.maxy]], c("--field-red", 0.14), null);
  const [mx, top] = toCanvas(mid, fr.maxy), [, bottom] = toCanvas(mid, fr.miny);
  ctx.save();
  ctx.setLineDash([6, 4]); ctx.strokeStyle = c("--field-line"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(mx + 0.5, top); ctx.lineTo(mx + 0.5, bottom); ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "700 12px system-ui, sans-serif"; ctx.textBaseline = "top";
  const [lx, ly] = toCanvas(fr.minx, fr.maxy), [rx] = toCanvas(fr.maxx, fr.maxy);
  ctx.fillStyle = c("--accent-strong"); ctx.textAlign = "left"; ctx.fillText("BLUE", lx + 6, ly + 5);
  ctx.fillStyle = c("--field-red-text"); ctx.textAlign = "right"; ctx.fillText("RED", rx - 6, ly + 5);
  ctx.restore();
}

// The robot's top-down footprint in its own frame (x forward, y left, meters from the root body's origin), from a
// model view whose robot geoms are drawn at the start pose: the root body's pose comes from one of its own geoms
// (world pos / quat against lpos / lquat), and every outline is moved back by it. [] when the view has no robot.
export function robotFootprint(v) {
  const geoms = (v && v.geoms) || [];
  const own = geoms.find((g) => g.mover === v.root_mover && g.mover >= 0 && g.lquat && g.lpos);
  let px = 0, py = 0, yaw = 0;
  if (own) {
    const [w, x, y, z] = own.quat, [lw, lx, ly, lz] = own.lquat;
    // root = quat * conj(lquat)
    const rw = w * lw + x * lx + y * ly + z * lz, rx = -w * lx + x * lw - y * lz + z * ly;
    const ry = -w * ly + x * lz + y * lw - z * lx, rz = -w * lz - x * ly + y * lx + z * lw;
    yaw = Math.atan2(2 * (rw * rz + rx * ry), 1 - 2 * (ry * ry + rz * rz));
    const off = rotate([rw, rx, ry, rz], own.lpos);
    px = own.pos[0] - off[0]; py = own.pos[1] - off[1];
  } else if (geoms.length) {
    const all = hull(geoms.map((g) => outline(g, v.meshes)).flat());
    if (all.length) { px = all.reduce((s, q) => s + q[0], 0) / all.length; py = all.reduce((s, q) => s + q[1], 0) / all.length; }
  }
  const c = Math.cos(-yaw), s = Math.sin(-yaw);
  const pts = geoms.map((g) => outline(g, v.meshes)).flat()
    .map(([x, y]) => [(x - px) * c - (y - py) * s, (x - px) * s + (y - py) * c]);
  return hull(pts);
}

// The other pose checks in the scenario (not the one being edited).
function ghosts(step, check) {
  const out = [];
  const steps = sc.state ? sc.state.steps : [];
  steps.forEach((st, i) => st.checks.forEach((c, j) => {
    if (c.kind !== "pose" || (i === step && j === check) || !c.spec || typeof c.spec !== "object") return;
    const s = c.spec;
    const n = (k) => (typeof s[k] === "number" && isFinite(s[k]) ? s[k] : null);
    if (n("x") === null || n("y") === null) return;
    out.push({ label: `${i + 1}${st.checks.filter((k) => k.kind === "pose").length > 1 ? `.${j + 1}` : ""}`, x: n("x"), y: n("y"),
      heading: n("heading"), tol_xy: n("tol_xy") ?? DEFAULT_TOL_XY, tol_heading: n("tol_heading") ?? DEFAULT_TOL_H });
  }));
  return out;
}

// -- the dialog ---------------------------------------------------------------------------------------------
let dlg = null;

function dialog() {
  if (!dlg) {
    dlg = h("dialog", { id: "field-dialog", class: "field-dialog", "aria-labelledby": "fp-title" });
    document.body.append(dlg);
  }
  return dlg;
}

const num = (v) => typeof v === "number" && isFinite(v);
const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const normDeg = (d) => { let a = ((d + 180) % 360 + 360) % 360 - 180; if (a === -180) a = 180; return a; };

export async function openFieldPicker({ step, check, spec, setSpec }) {
  const v = await loadView();
  const d = dialog();
  const cur = {};
  for (const k of KEYS) cur[k] = num(spec[k]) ? spec[k] : null;
  const st = sc.state.steps[step];
  const others = ghosts(step, check);
  const robot = (v.geoms || []).map((g) => outline(g, v.meshes)).filter((p) => p.length >= 3);
  const robotHull = hull(robot.flat());
  const geo = fieldGeometry(v);
  const b = geo.floor.bounds;

  // canvas size: fit the field into the dialog, keeping its aspect
  const maxW = Math.min(1100, window.innerWidth * 0.94 - 48), maxH = Math.max(220, window.innerHeight * 0.94 - 250);
  const bw = b.maxx - b.minx, bh = b.maxy - b.miny;
  const scale = Math.min(maxW / bw, maxH / bh);
  const W = Math.round(bw * scale), H = Math.round(bh * scale);
  const toPx = (x, y) => [(x - b.minx) * scale, (b.maxy - y) * scale];
  const toField = (px, py) => [b.minx + px / scale, b.maxy - py / scale];

  const canvas = h("canvas", { id: "field-canvas", class: "field-canvas", tabindex: "0", role: "application", width: String(W), height: String(H),
    style: `width:${W}px;height:${H}px`,
    "aria-label": "Field, seen from above. Click to place the target; drag to set the heading. Arrow keys move the target, [ and ] turn it." });
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  const ctx = canvas.getContext("2d");

  // the number boxes (blank = leave the key out); x, y and tol_xy are field lengths in the chosen units (units.js),
  // in text boxes so "12.5 ft" can be typed too, and a box back at the text it showed keeps its exact value
  const FIELD_KEYS = new Set(["x", "y", "tol_xy"]);
  const fieldUnits = [];   // the field-length unit labels, renamed when the setting changes
  const show = (el, k) => {
    if (FIELD_KEYS.has(k)) showMeasure(el, cur[k], "field"); else el.value = cur[k] === null ? "" : fmt(cur[k]);
  };
  const box = (k, label, unit, placeholder) => {
    const kind = FIELD_KEYS.has(k) ? "field" : null;
    const el = h("input", { id: `fp-${k.replace("_", "-")}`, type: kind ? "text" : "number", step: kind ? null : "any", inputmode: "decimal",
      autocomplete: kind ? "off" : null, class: "fp-num", placeholder: placeholder ?? "", "aria-label": label });
    show(el, k);
    el.addEventListener("input", () => {
      if (el.validity.badInput) return;
      const t = el.value.trim();
      const v = !kind ? (t === "" ? null : Number(t)) : readMeasure(el, kind);
      if (measureError(v)) return;   // not a field length yet: OK says what to type
      cur[k] = v;
      draw();
    });
    const u = h("span", { class: "unit" }, kind ? units.name(kind) : unit);
    if (kind) fieldUnits.push(u);
    return [el, h("label", { class: "fp-field", for: el.id }, h("span", { class: "fp-label" }, label), h("span", { class: "with-unit" }, el, u))];
  };
  const [xIn, xF] = box("x", "x"), [yIn, yF] = box("y", "y"), [hIn, hF] = box("heading", "heading", "°", "none");
  const [txIn, txF] = box("tol_xy", "tol_xy", null, units.shown(DEFAULT_TOL_XY, "field", 0)), [thIn, thF] = box("tol_heading", "tol_heading", "°", String(DEFAULT_TOL_H));
  const syncBoxes = () => { show(xIn, "x"); show(yIn, "y"); show(hIn, "heading"); };
  // Units changed in another tab or editor while this is open: the boxes and labels follow. Text being typed in
  // stays and now means the new units, as in the inspector and the step editor (it is read again here and at OK).
  const offUnits = units.onChange(() => {
    for (const u of fieldUnits) u.textContent = units.name("field");
    txIn.placeholder = units.shown(DEFAULT_TOL_XY, "field", 0);
    for (const [el, k] of [[xIn, "x"], [yIn, "y"], [txIn, "tol_xy"]]) {
      if (document.activeElement === el && el.value.trim() !== el.dataset.shown) el.dispatchEvent(new Event("input"));
      else show(el, k);
    }
    draw();
  });
  const readout = h("span", { class: "fp-readout muted", "aria-live": "off" }, " ");
  const err = h("p", { class: "error", role: "alert", hidden: true });

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const c = theme.color;
    ctx.fillStyle = c("--field-surround");
    ctx.fillRect(0, 0, W, H);
    drawField(ctx, geo, toPx);
    const poly = (pts, fill, stroke, width = 1, dash = null) => fieldPoly(ctx, toPx, pts, fill, stroke, width, dash);
    // the robot at its start pose
    for (const p of robot) poly(p, c("--accent-text", 0.12), null);
    if (robotHull.length >= 3) {
      poly(robotHull, c("--accent-text", 0.16), c("--accent-text"), 1.5);
      const cx = robotHull.reduce((s, q) => s + q[0], 0) / robotHull.length, cy = robotHull.reduce((s, q) => s + q[1], 0) / robotHull.length;
      const below = Math.min(...robotHull.map((q) => q[1]));
      const [px, py] = toPx(cx, below);
      ctx.fillStyle = c("--accent-strong"); ctx.font = "600 10px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.fillText("start", px, py + 3);
    }
    // the other pose checks, as ghosts
    for (const g of others) marker(g, { ghost: true });
    // the target
    if (cur.x !== null || cur.y !== null) marker({ ...cur, tol_xy: cur.tol_xy ?? DEFAULT_TOL_XY, tol_heading: cur.tol_heading ?? DEFAULT_TOL_H });
  }

  function marker(m, { ghost = false } = {}) {
    const c = theme.color;
    const color = ghost ? c("--field-line", 0.75) : c("--accent-text");
    // only x or only y: a line across the field
    if (m.x === null || m.y === null) {
      if (ghost) return;
      ctx.save();
      ctx.strokeStyle = color; ctx.fillStyle = c("--accent-text", 0.12); ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5;
      const t = m.tol_xy * scale;
      if (m.x !== null) { const [px] = toPx(m.x, 0); ctx.fillRect(px - t, 0, 2 * t, H); ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, H); ctx.stroke(); }
      else { const [, py] = toPx(0, m.y); ctx.fillRect(0, py - t, W, 2 * t); ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(W, py); ctx.stroke(); }
      ctx.restore();
      return;
    }
    let [px, py] = toPx(m.x, m.y);
    const off = px < 0 || py < 0 || px > W || py > H;
    if (off) {
      if (ghost) return;
      const cx = Math.min(W - 10, Math.max(10, px)), cy = Math.min(H - 10, Math.max(10, py));
      const ang = Math.atan2(py - cy, px - cx);
      ctx.save();
      ctx.fillStyle = c("--error"); ctx.translate(cx, cy); ctx.rotate(ang);
      ctx.beginPath(); ctx.moveTo(8, 0); ctx.lineTo(-6, -6); ctx.lineTo(-6, 6); ctx.closePath(); ctx.fill();
      ctx.restore();
      ctx.fillStyle = c("--error"); ctx.font = "600 11px system-ui, sans-serif";
      ctx.textAlign = cx > W / 2 ? "right" : "left"; ctx.textBaseline = cy > H / 2 ? "bottom" : "top";
      ctx.fillText(`target (${units.fmt(m.x, "field")}, ${units.fmt(m.y, "field")}) is off the field`, cx + (cx > W / 2 ? -14 : 14), cy + (cy > H / 2 ? -8 : 8));
      return;
    }
    const r = Math.max(m.tol_xy * scale, 2);
    const R = Math.max(r + 14, 42);
    if (m.heading !== null && m.heading !== undefined) {
      const a = -m.heading * Math.PI / 180, t = Math.min(m.tol_heading, 180) * Math.PI / 180;
      ctx.beginPath(); ctx.moveTo(px, py); ctx.arc(px, py, R, a - t, a + t); ctx.closePath();
      ctx.fillStyle = ghost ? c("--field-line", 0.12) : c("--field-aim", 0.22); ctx.fill();
      ctx.strokeStyle = ghost ? c("--field-line", 0.5) : c("--field-aim", 0.8); ctx.lineWidth = 1; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + R * Math.cos(a), py + R * Math.sin(a));
      ctx.strokeStyle = ghost ? color : c("--field-aim"); ctx.lineWidth = 2; ctx.stroke();
      ctx.save(); ctx.translate(px + R * Math.cos(a), py + R * Math.sin(a)); ctx.rotate(a);
      ctx.beginPath(); ctx.moveTo(4, 0); ctx.lineTo(-5, -4); ctx.lineTo(-5, 4); ctx.closePath();
      ctx.fillStyle = ghost ? color : c("--field-aim"); ctx.fill(); ctx.restore();
    }
    ctx.beginPath(); ctx.arc(px, py, r, 0, 2 * Math.PI);
    ctx.fillStyle = ghost ? c("--field-line", 0.1) : c("--accent-text", 0.18); ctx.fill();
    ctx.setLineDash(ghost ? [4, 3] : []); ctx.strokeStyle = color; ctx.lineWidth = ghost ? 1 : 1.5; ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(px, py, ghost ? 2.5 : 3.5, 0, 2 * Math.PI); ctx.fillStyle = color; ctx.fill();
    if (ghost) {
      ctx.fillStyle = c("--field-ghost-text"); ctx.font = "600 10px system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
      ctx.fillText(`step ${m.label}`, px + Math.max(r, 4) * 0.75 + 3, py - Math.max(r, 4) * 0.75 - 1);
    }
  }

  // pointer: press to place (or, on the target, to keep it); drag to aim the heading
  let drag = null;
  const at = (e) => { const rc = canvas.getBoundingClientRect(); return [e.clientX - rc.left, e.clientY - rc.top]; };
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    canvas.focus();
    const [px, py] = at(e);
    let onTarget = false;
    if (cur.x !== null && cur.y !== null) {
      const [tx, ty] = toPx(cur.x, cur.y);
      onTarget = Math.hypot(px - tx, py - ty) <= 10;
    }
    if (!onTarget) {
      const [x, y] = toField(px, py);
      cur.x = round(x, 2); cur.y = round(y, 2);
    }
    drag = { px, py, aiming: false };
    canvas.setPointerCapture(e.pointerId);
    syncBoxes(); draw();
  });
  canvas.addEventListener("pointermove", (e) => {
    const [px, py] = at(e);
    const [x, y] = toField(px, py);
    readout.textContent = `x ${units.fmt(x, "field")}, y ${units.fmt(y, "field")}`;
    if (!drag) return;
    if (!drag.aiming && Math.hypot(px - drag.px, py - drag.py) < 6) return;
    drag.aiming = true;
    const [tx, ty] = toPx(cur.x, cur.y);
    cur.heading = round(normDeg(Math.atan2(-(py - ty), px - tx) * 180 / Math.PI), 0);
    syncBoxes(); draw();
  });
  const endDrag = () => { drag = null; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => { readout.textContent = " "; });
  canvas.addEventListener("keydown", (e) => {
    const stepM = e.shiftKey ? 0.5 : 0.05, stepD = e.shiftKey ? 15 : 5;
    const move = { ArrowLeft: [-stepM, 0], ArrowRight: [stepM, 0], ArrowUp: [0, stepM], ArrowDown: [0, -stepM] }[e.key];
    if (move) {
      cur.x = round((cur.x ?? 0) + move[0], 3); cur.y = round((cur.y ?? 0) + move[1], 3);
    } else if (e.key === "[" || e.key === "]") {
      cur.heading = normDeg((cur.heading ?? 0) + (e.key === "[" ? stepD : -stepD));
    } else return;
    e.preventDefault();
    syncBoxes(); draw();
  });

  const close = () => { d.close(); };
  const ok = h("button", { type: "submit", id: "fp-ok", class: "btn btn-primary" }, "OK");
  const form = h("form", { method: "dialog", novalidate: true },
    h("h2", { id: "fp-title" }, `Pick the pose · step ${step + 1}${st && st.name ? ` (${st.name})` : ""}, check ${check + 1}`),
    h("p", { class: "muted fp-help" }, "Click the field to place the target; keep the button down and drag to aim the heading (drag from the target to re-aim it). "
      + "Arrow keys move it (Shift: faster), [ and ] turn it. A blank heading isn't checked."),
    h("div", { class: "fp-canvas-wrap" }, canvas),
    h("div", { class: "fp-row" }, xF, yF, hF, txF, thF, readout),
    h("div", { class: "fp-legend muted" },
      h("span", { class: "lg lg-robot" }), "robot at its start", h("span", { class: "lg lg-target" }), "target and tol_xy",
      h("span", { class: "lg lg-wedge" }), "heading ± tol_heading", others.length ? [h("span", { class: "lg lg-ghost" }), "other pose checks"] : null),
    err,
    h("div", { class: "dialog-actions" }, h("button", { type: "button", id: "fp-cancel", class: "btn", onclick: close }, "Cancel"), ok));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const bad = [hIn, thIn].find((el) => el.validity.badInput);
    if (bad) { err.textContent = `${bad.getAttribute("aria-label")} must be a number`; err.hidden = false; bad.focus(); return; }
    // x, y and tol_xy as their boxes say now
    for (const [el, k] of [[xIn, "x"], [yIn, "y"], [txIn, "tol_xy"]]) {
      const v = readMeasure(el, "field");
      if (measureError(v)) { err.textContent = measureError(v); err.hidden = false; el.focus(); return; }
      cur[k] = v;
    }
    const next = { ...spec };
    for (const k of KEYS) {
      if (cur[k] === null) delete next[k];
      // lengths to 1 µm: a typed 6.55 ft (1.99644 m) reads back as typed, with no float noise in the file
      else next[k] = k === "heading" || k === "tol_heading" ? round(cur[k], 2) : round(cur[k], 6);
    }
    close();
    if (JSON.stringify(next) !== JSON.stringify(spec)) setSpec(next);
  });
  d.replaceChildren(form);
  window.__scrimmage.fieldPick = {
    toScreen: (x, y) => { const rc = canvas.getBoundingClientRect(); const [px, py] = toPx(x, y); return { x: rc.left + px, y: rc.top + py }; },
    get value() { return { ...cur }; },
  };
  const offTheme = theme.onChange(() => draw());
  d.addEventListener("close", () => { delete window.__scrimmage.fieldPick; offUnits(); offTheme(); }, { once: true });
  draw();
  d.showModal();
  canvas.focus();
}

// The pose card's "Pick on field" button (scenario.js installs this as hooks.poseTools).
export function poseTools({ step, check, spec, setSpec }) {
  return h("div", { class: "pose-tools" },
    h("button", { type: "button", class: "btn btn-small", dataset: { key: `pick-${step}-${check}` },
      onclick: () => openFieldPicker({ step, check, spec, setSpec }) }, "Pick on field…"),
    h("span", { class: "hint" }, "Place the target and aim the heading on a drawing of the field."));
}
