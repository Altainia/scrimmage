// The robot frame's math for the Part tab, the same as scrimmage/cad/frame.py's to_robot (robot = R · cad + t), and
// the "Try it" poses. Plain arrays, no three.js: R is three rows, t and points are [x, y, z].
//
// Robot coordinates are x forward, y left, z up (meters), the floor at z = 0 and the footprint center at x = y = 0.
// /api/part/view's meshes and the bodies' centers of gravity are robot coordinates; joint anchors and axes, mount
// points and loop points are stored in CAD coordinates, so they go through cadToRobot / dirToRobot to be drawn.

const axisOf = (name) => ["xyz".indexOf(name[1]), name[0] === "+" ? 1 : -1];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (i, s) => { const v = [0, 0, 0]; v[i] = s; return v; };

// {R, t} for a frame {up, front, floor, center, skip}, as frame.py's to_robot.
export function toRobot(frame) {
  if (!frame || frame.skip) return { R: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], t: [0, 0, 0] };
  const [ua, us] = axisOf(frame.up), [fa, fs] = axisOf(frame.front);
  const up = unit(ua, us), front = unit(fa, fs);
  const R = [front, cross(up, front), up];
  const origin = [0, 0, 0];
  origin[ua] = +frame.floor || 0;
  if (frame.center) {
    const others = [0, 1, 2].filter((i) => i !== ua);
    origin[others[0]] = frame.center[0];
    origin[others[1]] = frame.center[1];
  }
  const t = R.map((row) => -(row[0] * origin[0] + row[1] * origin[1] + row[2] * origin[2]));
  return { R: R.map((row) => row.map((x) => x + 0)), t: t.map((x) => x + 0) };   // + 0: no -0
}

const mul = (R, v) => R.map((row) => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);
const mulT = (R, v) => [0, 1, 2].map((j) => R[0][j] * v[0] + R[1][j] * v[1] + R[2][j] * v[2]);

export const cadToRobot = ({ R, t }, p) => mul(R, p).map((x, i) => x + t[i]);
export const dirToRobot = ({ R }, d) => mul(R, d);
export const robotToCad = ({ R, t }, p) => mulT(R, p.map((x, i) => x - t[i]));
export const dirToCad = ({ R }, d) => mulT(R, d);

// The axis name ("+x" ...) of a unit CAD vector along one axis, or null.
export function axisName(v) {
  const i = v.findIndex((x) => Math.abs(x) > 0.5);
  return i < 0 ? null : `${v[i] > 0 ? "+" : "-"}${"xyz"[i]}`;
}

// Turn `front` a quarter turn about `up` (counterclockwise seen from above): up × front.
export function turnFront(frame) {
  const [ua, us] = axisOf(frame.up), [fa, fs] = axisOf(frame.front);
  return axisName(cross(unit(ua, us), unit(fa, fs)));
}

// The floor and the footprint center that put a CAD box [lo, hi] on the floor, centered, with `up` up.
export function floorAndCenter(up, lo, hi) {
  const [ua, us] = axisOf(up);
  const others = [0, 1, 2].filter((i) => i !== ua);
  return { floor: us > 0 ? lo[ua] : hi[ua], center: others.map((i) => (lo[i] + hi[i]) / 2) };
}

export const otherAxes = (up) => [0, 1, 2].filter((i) => i !== axisOf(up)[0]);
export const upIndex = (up) => axisOf(up)[0];

// What a "Try it" slider covers: the joint's range, else ±180° (turns) or ±0.5 m (slides).
export function tryRange(joint) {
  if (joint.range) return joint.range;
  return joint.kind === "slide" ? [-0.5, 0.5] : [-180, 180];
}

// Each body's pose for the Try-it values (`values`: body -> degrees or meters), as {R, t} acting on robot
// coordinates at zero pose: a body moves with its parent, then about (or along) its own joint. Bodies without a
// joint, or with no Try-it value, follow their parent.
export function bodyPoses(state, values) {
  const rt = toRobot(state.frame);
  const byName = new Map(state.bodies.map((b) => [b.name, b]));
  const out = new Map();
  const I = { R: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], t: [0, 0, 0] };
  const pose = (name, seen = new Set()) => {
    if (out.has(name)) return out.get(name);
    const b = byName.get(name);
    if (!b || seen.has(name)) return I;
    seen.add(name);
    const parent = b.parent != null ? pose(b.parent, seen) : I;
    const q = values.get(name) || 0;
    let p = parent;
    const j = b.joint;
    if (j && j.anchor && j.axis && q) {
      const a = normalize(dirToRobot(rt, j.axis)), c = cadToRobot(rt, j.anchor);
      const J = j.kind === "slide" ? { R: I.R, t: a.map((x) => x * q) }
        : rotationAbout(a, c, (q * Math.PI) / 180);
      p = compose(parent, J);
    }
    out.set(name, p);
    return p;
  };
  for (const b of state.bodies) pose(b.name);
  return out;
}

export function normalize(v) {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return v.map((x) => x / n);
}

// x -> R x + t for a turn of `angle` radians about the line through `c` along unit `a`.
function rotationAbout(a, c, angle) {
  const [x, y, z] = a, cs = Math.cos(angle), sn = Math.sin(angle), k = 1 - cs;
  const R = [
    [cs + x * x * k, x * y * k - z * sn, x * z * k + y * sn],
    [y * x * k + z * sn, cs + y * y * k, y * z * k - x * sn],
    [z * x * k - y * sn, z * y * k + x * sn, cs + z * z * k]];
  const Rc = mul(R, c);
  return { R, t: c.map((v, i) => v - Rc[i]) };
}

// a ∘ b: first b, then a.
export function compose(a, b) {
  const R = a.R.map((row) => [0, 1, 2].map((j) => row[0] * b.R[0][j] + row[1] * b.R[1][j] + row[2] * b.R[2][j]));
  const t = mul(a.R, b.t).map((x, i) => x + a.t[i]);
  return { R, t };
}

export const apply = (p, v) => mul(p.R, v).map((x, i) => x + p.t[i]);
