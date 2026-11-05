// The Drive tab's readouts: pose, speed, battery, motor command and current bars, joint positions (renderReadouts),
// and a small canvas plot of one signal over the last few seconds of sim time (Plot).
//
// renderReadouts(root, state) draws from a live state message (its `readouts`: Session.state()); with no readouts
// (no session yet) it says so. Rows are reused between calls, so it is cheap at 10 Hz.

import { h } from "./app.js";
import { units } from "./units.js";

const fmt = (v, digits) => (typeof v === "number" && isFinite(v) ? v.toFixed(digits) : "—");
// A measure in the chosen units (units.js), to `digits` places kept (the rows don't jump about at 10 Hz)
const measure = (v, kind, digits) => (typeof v === "number" && isFinite(v) ? `${units.toDisplay(v, kind).toFixed(digits)} ${units.name(kind)}` : "—");

// A bar from -1 to 1 filled from the center (command), or from 0 to max (current).
function bar(kind) {
  const fill = h("span", { class: "bar-fill" });
  return { el: h("span", { class: `bar bar-${kind}` }, fill), fill };
}

function setCentered(b, v) {
  const x = Math.max(-1, Math.min(1, Number(v) || 0));
  b.fill.style.left = `${50 + Math.min(0, x) * 50}%`;
  b.fill.style.width = `${Math.abs(x) * 50}%`;
  b.fill.classList.toggle("neg", x < 0);
}

function setFrom0(b, v, max) {
  const x = Math.max(0, Math.min(1, Math.abs(Number(v) || 0) / max));
  b.fill.style.left = "0";
  b.fill.style.width = `${x * 100}%`;
}

const CURRENT_SCALE = 60;   // A for a full current bar (grows if a motor draws more)

class Readouts {
  constructor(root) {
    this.root = root;
    this.pose = h("dd", { class: "ro-val" });
    this.speed = h("dd", { class: "ro-val" });
    this.battery = h("dd", { class: "ro-val" });
    this.scoreTitle = h("dt", { text: "Score", hidden: true });
    this.score = h("dd", { class: "ro-val", hidden: true });
    this.motors = h("div", { class: "ro-motors" });
    this.joints = h("div", { class: "ro-joints" });
    this.empty = h("p", { class: "muted ro-empty", text: "Start the robot to see its readouts." });
    this.motorRows = new Map();
    this.jointRows = new Map();
    this.currentMax = CURRENT_SCALE;
    root.replaceChildren(this.empty,
      h("dl", { class: "ro-grid" },
        h("dt", { text: "Pose" }), this.pose,
        h("dt", { text: "Speed" }), this.speed,
        h("dt", { text: "Battery" }), this.battery,
        this.scoreTitle, this.score),
      h("h3", { class: "ro-title", text: "Motors" }), h("p", { class: "ro-legend muted", text: "command (−1…1) · current (A)" }), this.motors,
      h("h3", { class: "ro-title", text: "Joints" }), this.joints);
  }

  render(state) {
    const r = state && state.readouts;
    this.empty.hidden = !!r;
    if (!r) return;
    const p = r.pose || {};
    this.pose.textContent = `x ${measure(p.x, "field", 2)} · y ${measure(p.y, "field", 2)} · ${fmt(p.heading_deg, 1)}°`;
    this.speed.textContent = measure(r.speed, "speed", 2);
    this.battery.textContent = `${fmt(r.battery, 2)} V`;
    const sc = r.range;   // the test range's counts, when the robot is on one
    this.scoreTitle.hidden = this.score.hidden = !sc;
    if (sc) this.score.textContent = [...Object.entries(sc.goals || {}).map(([k, n]) => `${k} ${n}`), `fired ${sc.fired}`, `held ${sc.held}`].join(" · ");

    const motors = r.motors || {};
    for (const c of Object.values(motors)) this.currentMax = Math.max(this.currentMax, Math.abs(c.current || 0));
    this.sync(this.motors, this.motorRows, Object.keys(motors), (name) => {
      const cmd = bar("cmd"), cur = bar("cur");
      const cmdText = h("span", { class: "ro-num" }), curText = h("span", { class: "ro-num" });
      const el = h("div", { class: "motor-row" }, h("span", { class: "ro-name", text: name, title: name }),
        cmd.el, cmdText, cur.el, curText);
      return { el, cmd, cur, cmdText, curText };
    });
    for (const [name, row] of this.motorRows) {
      const m = motors[name];
      setCentered(row.cmd, m.command);
      setFrom0(row.cur, m.current, this.currentMax);
      row.cmdText.textContent = fmt(m.command, 2);
      row.curText.textContent = `${fmt(m.current, 1)} A`;
    }

    const joints = r.joints || {};
    this.sync(this.joints, this.jointRows, Object.keys(joints), (name) => {
      const val = h("span", { class: "ro-num" });
      return { el: h("div", { class: "joint-row" }, h("span", { class: "ro-name", text: name, title: name }), val), val };
    });
    for (const [name, row] of this.jointRows) {
      const j = joints[name];
      row.val.textContent = `${fmt(j.position, 3)} · ${fmt(j.velocity, 2)}/s`;
    }
  }

  // Keep one row per name, in order; rows for names gone are dropped.
  sync(box, rows, names, make) {
    const same = names.length === rows.size && names.every((n) => rows.has(n));
    if (same) return;
    rows.clear();
    for (const n of names) rows.set(n, make(n));
    box.replaceChildren(...[...rows.values()].map((r) => r.el));
    if (!names.length) box.append(h("p", { class: "muted ro-none", text: "(none)" }));
  }
}

const views = new WeakMap();

export function renderReadouts(root, state) {
  let v = views.get(root);
  if (!v) { v = new Readouts(root); views.set(root, v); }
  v.render(state);
}

// One signal's values over the last `seconds` of sim time. push(t, value) adds a sample (a t that goes back, as
// in a new session, starts over; null values leave a gap); draw() paints the canvas.
export class Plot {
  constructor(canvas, seconds = 10) {
    this.canvas = canvas;
    this.seconds = seconds;
    this.t = [];
    this.v = [];
  }

  clear() {
    this.t = [];
    this.v = [];
  }

  push(t, value) {
    if (typeof t !== "number") return;
    const n = this.t.length;
    if (n && t < this.t[n - 1]) this.clear();
    if (n && t === this.t[n - 1]) { this.v[n - 1] = value; return; }
    this.t.push(t);
    this.v.push(typeof value === "number" && isFinite(value) ? value : null);
    const cut = t - this.seconds;
    let k = 0;
    while (k < this.t.length - 1 && this.t[k] < cut) k++;
    if (k) { this.t.splice(0, k); this.v.splice(0, k); }
  }

  get length() { return this.t.length; }

  draw() {
    const c = this.canvas;
    const w = c.clientWidth || c.width, hgt = c.clientHeight || c.height;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(hgt * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(hgt * dpr);
    }
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, hgt);
    const vals = this.v.filter((x) => x !== null);
    if (!vals.length) return;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const pad = 4;
    const t1 = this.t[this.t.length - 1], t0 = t1 - this.seconds;
    const X = (t) => ((t - t0) / this.seconds) * w;
    const Y = (v) => pad + (1 - (v - lo) / (hi - lo)) * (hgt - 2 * pad);
    if (lo < 0 && hi > 0) {   // the zero line
      g.strokeStyle = "#d0d5dc";
      g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, Y(0)); g.lineTo(w, Y(0)); g.stroke();
    }
    g.strokeStyle = "#2b6cb0";
    g.lineWidth = 1.5;
    g.beginPath();
    let pen = false;
    for (let i = 0; i < this.t.length; i++) {
      if (this.v[i] === null) { pen = false; continue; }
      const x = X(this.t[i]), y = Y(this.v[i]);
      if (pen) g.lineTo(x, y); else g.moveTo(x, y);
      pen = true;
    }
    g.stroke();
    g.fillStyle = "#656d76";
    g.font = "10.5px system-ui, sans-serif";
    g.fillText(String(+hi.toPrecision(4)), 3, 11);
    g.fillText(String(+lo.toPrecision(4)), 3, hgt - 3);
  }
}
