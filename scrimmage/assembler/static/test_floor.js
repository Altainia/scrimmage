// The Test tab's Setup, continued: the floor spread, a station's boxes, settings for another field, the grip rows
// and the hints (test_setup.js draws Setup).

import { $, h, api, notice, defaultGrip, carryText } from "./app.js";
import { headOf } from "./blocks_defs.js";
import { units } from "./units.js";
import { t, tn } from "./i18n.js";
import { flush, refresh, check, fits, renderStatus } from "./test.js";
import { answered, T, canon, fmt, IN, round6 } from "./test_state.js";
import { numberBox, measureBox, setTest, setupOf, setStation, renderSetupError, renderSetup } from "./test_setup.js";
import { openSweep } from "./test_dialogs.js";

const MAX_SPREAD = 25;        // scrimmage/blocks/program.py's MAX_PLACEMENTS
// a station's numbers (scrimmage/testrange/station.py's OVERRIDE_KEYS): key, label, unit on screen, a list key's
// three axes, and what Try values… goes up to from a spread of 0 (in the unit shown). Every spread is ± its value;
// the speed and the release box (`kind`) are in the Units setting here and SI in the file.
const STATION_BOXES = () => [
  { key: "interval", label: t("Time between pieces (s)"), unit: "s" },
  { key: "stock", label: t("Stock (empty: never runs out)"), unit: "" },
  { key: "spread_yaw_deg", label: t("Left/right angle (± degrees)"), unit: "°", upTo: 10 },
  { key: "spread_pitch_deg", label: t("Up/down angle (± degrees)"), unit: "°", upTo: 10 },
  { key: "spread_speed", label: t("Speed"), kind: "speed", upTo: [1.3, 0.4] },
  { key: "spread_position", label: t("Release box"), kind: "length", axes: ["x", "y", "z"], upTo: [2, 50] },
  { key: "spread_rotation_deg", label: t("Turned (± degrees)"), unit: "°", axes: [t("about x"), t("about y"), t("about z")], upTo: 90 },
];
// a piece other than a ball (ruling P30): its tip and facing (a ball has neither; Try values… goes from and to these
// two), then the 3-axis spread last, as an extra tumble, so the three can't be mixed up
const PIECE_BOXES = () => [
  { key: "tip_deg", label: t("Tipped up (degrees: 0 lying, 90 standing)"), unit: "°", from: 0, to: 90 },
  { key: "spread_tip_deg", label: t("Tipped up varies by (± degrees)"), unit: "°", upTo: 30 },
  { key: "turn_deg", label: t("Facing (degrees from the feed direction)"), unit: "°", from: 0, to: 90 },
  { key: "spread_turn_deg", label: t("Facing varies by (± degrees)"), unit: "°", upTo: 90 },
  { ...STATION_BOXES().at(-1), label: t("Extra tumble (± degrees)") },
];
export const stationBoxes = (piece) => (piece ? [...STATION_BOXES().slice(0, -1), ...PIECE_BOXES()] : STATION_BOXES())
  .map((box) => (box.kind ? { ...box, label: t("{label} (± {unit})", { label: box.label, unit: units.name(box.kind) }), unit: units.name(box.kind),
    upTo: box.upTo[units.system === "metric" ? 1 : 0] } : box));
// a flat kind (a disc, a hatch panel) lies flat at a tip of 90: its axis is its face's normal
const tipLabel = (kind) => (kind && kind.flat ? t("Tipped up (degrees: 0 on its edge, 90 lying flat)")
  : t("Tipped up (degrees: 0 lying, 90 standing)"));
export const tipWords = (kind) => (kind && kind.flat ? t("Tipped up: 0 is standing on its edge, 90 lying flat.")
  : t("Tipped up: 0 is lying on its side, 90 standing on end."));
const GRABBER = /claw|grabber|gripper|jaw/i;     // test_session.py's GRABBER_WORDS
// a grip material as its picker shows it (ruling P27): two with the same estimate read the same
export const gripText = ([, label, mu]) => t("{material} — {friction}, estimate", { material: label, friction: fmt(mu) });
const OWN = "own";      // the grip picker's "My own number…"
// the floor spread's axes (scrimmage/blocks/spread.py's SPREAD_AXES): label, and from, to, steps when ticked: around
// where the field's ball lies (palette.floor_ball; offset and distance), never nearer than its nearest_in
// tip_deg (a piece other than a ball's) is from 0 to 90 whatever the piece's own. The offset and distance are inches
// in the file and the Units setting's length on screen (LENGTH_AXES).
const LENGTH_AXES = ["offset_in", "distance_in"];
const FLOOR_AXES = [
  ["offset_in", () => t("Offset across ({unit})", { unit: units.name("length") }), -6, 6, 3],
  ["distance_in", () => t("Distance ahead ({unit})", { unit: units.name("length") }), -12, 12, 3],
  ["angle_deg", () => t("Angle (degrees)"), -30, 30, 3],
  ["rotation_deg", () => t("Turned (degrees)"), 0, 90, 3],
  ["tip_deg", () => t("Tipped up (degrees)"), 0, 90, 3],
];
const floorBall = () => (T.state && T.state.palette && T.state.palette.floor_ball) || null;
// the floor spread's piece kind ({form, flat, label}), null for a ball
function floorKind() {
  const ball = floorBall(), kinds = (T.state && T.state.palette && T.state.palette.kinds) || {};
  const k = ball && kinds[ball.kind];
  return k && k.form !== "ball" ? k : null;
}
// the floor spread's axes as Setup shows them ([axis, label, ...]): for a piece other than a ball, its tip, and its
// turn read as where it faces (ruling P30, as a station's)
export function floorAxes() {
  const k = floorKind();
  const label = { tip_deg: tipLabel(k), rotation_deg: t("Facing (degrees)") };
  return FLOOR_AXES.filter(([axis]) => axis !== "tip_deg" || k)
    .map(([axis, words, ...rest]) => [axis, k && label[axis] ? label[axis] : words(), ...rest]);
}

// -- the floor spread: a draft of the boxes ({axis: {on, from, to, steps}}), written to the test when it makes at
// most MAX_SPREAD runs
// (offset and distance: inches in the file, in the Units setting's length in the boxes, `T.floorSystem`'s: a Units
// change converts the draft, floorUnits)
export const floorKey = (fs) => canon(fs);
// The draft and the Units system its boxes' lengths are in (`T.floorSystem`): set together, only here (show() drops
// the draft with T.floor = null; floorDraft() then makes a new one through here).
function setFloor(draft) {
  T.floor = draft;
  T.floorSystem = units.system;
  return draft;
}
export function floorDraft() {
  const fs = (headOf(T.ws).setup || {}).floor_spread || {};
  const key = floorKey(fs);
  if (T.floor && T.floorFor === key) return T.floor;
  T.floorFor = key;
  const ball = floorBall() || {};
  return setFloor(Object.fromEntries(FLOOR_AXES.map(([axis, , from, to, steps]) => {
    const text = (x) => (LENGTH_AXES.includes(axis) ? units.shown(x * IN, "length") : fmt(x));
    const v = fs[axis];
    if (v && v.length) return [axis, { on: true, from: text(v[0]), to: text(v[v.length - 1]), steps: String(v.length) }];
    const base = axis === "tip_deg" ? 0 : ball[axis] ?? 0;
    let lo = base + from, hi = base + to;
    if (axis === "distance_in" && ball.nearest_in !== undefined && lo < ball.nearest_in) {
      lo = ball.nearest_in;
      hi = Math.max(hi, lo + (to - from));
    }
    // in metric, round millimeters (the inches' 1 in steps would show as 25.4 mm)
    const round = (x) => (LENGTH_AXES.includes(axis) && units.system === "metric"
      ? units.shown(Math.round(units.toDisplay(x * IN, "length") / 10) * 10 / 1000, "length") : text(Math.round(x)));
    return [axis, { on: false, from: round(lo), to: round(hi), steps: String(steps) }];
  })));
}

// The draft's lists and run count ({spread, runs}), or {error} saying what is wrong.
export function floorSpread(draft) {
  const fs = (headOf(T.ws).setup || {}).floor_spread || {};
  const spread = {};
  let runs = 1;
  for (const [axis, label] of floorAxes()) {
    const d = draft[axis];
    if (!d.on) continue;
    const length = LENGTH_AXES.includes(axis);
    // a length's from and to in inches (the file's), as typed in the unit shown; the file's own while unchanged
    const inches = (k) => {
      const shown = fs[axis] && fs[axis].length ? units.shown((k === "from" ? fs[axis][0] : fs[axis][fs[axis].length - 1]) * IN, "length") : null;
      if (shown !== null && d[k].trim() === shown) return k === "from" ? fs[axis][0] : fs[axis][fs[axis].length - 1];
      const si = units.parse(d[k], "length");
      return typeof si === "number" ? round6(si / IN) : NaN;
    };
    const a = length ? inches("from") : Number(d.from), b = length ? inches("to") : Number(d.to), n = Number(d.steps);
    if (d.from.trim() === "" || d.to.trim() === "" || !isFinite(a) || !isFinite(b)) {
      return { error: length ? t("{axis}: type a number in from and to (in {unit})", { axis: label, unit: units.name("length") })
        : t("{axis}: type a number in from and to", { axis: label }) };
    }
    if (!Number.isInteger(n) || n < 1) return { error: t("{axis}: steps is a whole number, 1 or more", { axis: label }) };
    // nearest_in alone, ignoring the offset: stricter than the server's exact check, which is fine as a quick warning
    const nearest = floorBall() && floorBall().nearest_in;
    if (axis === "distance_in" && nearest !== undefined && nearest !== null && Math.min(a, b) < nearest) {
      return { error: t("{axis}: {distance} or more (nearer, the ball starts inside the robot)", { axis: label, distance: units.fmt(nearest * IN, "length") }) };
    }
    spread[axis] = n === 1 ? [a] : Array.from({ length: n }, (_, k) => +(a + ((b - a) * k) / (n - 1)).toPrecision(6));
    runs *= n;
  }
  if (!Object.keys(spread).length) {
    return { error: floorKind() ? t("Tick an axis to spread the piece over") : t("Tick an axis to spread the ball over"), spread, runs: 0 };
  }
  if (runs > MAX_SPREAD) return { error: t("{runs} runs: at most {max} runs; use fewer steps", { runs, max: MAX_SPREAD }), runs };
  return { spread, runs };
}

// A Units change: the draft's lengths in the new units (the file's own value shown again where a box still shows
// it), in the boxes too, even when Setup isn't drawn again (another box is half typed); what a box being typed in
// holds is converted too (units.convert), not read again in the new units.
export function floorUnits() {
  const from = T.floorSystem;
  if (!T.floor || !from || from === units.system) return;
  setFloor(T.floor);   // its lengths are converted below
  const fs = (headOf(T.ws).setup || {}).floor_spread || {};
  for (const axis of LENGTH_AXES) {
    const d = T.floor[axis], file = fs[axis] && fs[axis].length ? { from: fs[axis][0], to: fs[axis][fs[axis].length - 1] } : {};
    for (const k of ["from", "to"]) {
      let next = d[k];
      if (file[k] !== undefined && d[k].trim() === units.shown(file[k] * IN, "length", 2, from)) next = units.shown(file[k] * IN, "length");
      else {
        const si = units.parse(d[k], "length", from);
        if (typeof si === "number") next = units.shown(si, "length");
      }
      d[k] = next;
      const el = document.getElementById(`test-spread-${axis}-${k}`);
      if (!el) continue;
      if (el === document.activeElement && el.value !== el.dataset.orig) el.value = carryText(el.value, "length", from);
      else el.value = next;
      el.dataset.orig = next;
    }
  }
}

export function floorChanged() {
  const draft = T.floor;
  for (const [axis] of floorAxes()) {
    const on = document.getElementById(`test-spread-${axis}-on`);
    if (!on) continue;
    draft[axis].on = on.checked;
    for (const k of ["from", "to", "steps"]) {
      const el = document.getElementById(`test-spread-${axis}-${k}`);
      draft[axis][k] = el.value;
      el.dataset.orig = el.value;
    }
  }
  const r = floorSpread(draft);
  renderFloorCount(r);
  if (!r.spread) return;      // nothing written until it can run
  const s = setupOf();
  if (Object.keys(r.spread).length) s.floor_spread = r.spread; else delete s.floor_spread;
  if (canon(s.floor_spread || {}) === canon((headOf(T.ws).setup || {}).floor_spread || {})) return;
  T.floorFor = floorKey(s.floor_spread || {});
  setTest({ setup: s });
}

export function renderFloorCount(r) {
  const el = document.getElementById("test-spread-count");
  if (!el) return;
  el.textContent = r.error || tn("{count} run", "{count} runs", r.runs);
  el.classList.toggle("error", !!r.error && r.runs > MAX_SPREAD);
  $("#test-spread-run").disabled = !!r.error || !T.state || !!T.state.running;
}

export async function runSpread() {
  await flush();
  if (T.saveError || T.local.length || !fits()) { check(); renderStatus(); return; }
  const { status, data } = await api("POST", "/api/test/spread", {});
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || answered(status);
    notice(t("Can't run the spread: {why}", { why }), "warn");
  }
  await refresh();
}

// What Try values… offers for a station's number: from and to that make sense for it (whole numbers for the stock,
// never below 0 for a spread).
function stationChoice(name, box, value) {
  const target = { station: name, key: box.key };
  if (box.key === "stock") {
    const from = Math.max(1, value - 2);
    return { label: box.label, target, value, unit: "", from, to: from + 4 };
  }
  if (box.key === "interval") return { label: box.label, target, value, unit: "s" };
  if (box.from !== undefined) return { label: box.label, target, value, unit: box.unit, from: box.from, to: box.to };
  // a list: the sweep sets all three axes to the value; the speed and the release box are tried in the unit shown
  const si = box.axes ? Math.max(...value) : value;
  const v = box.kind ? +units.toDisplay(si, box.kind).toPrecision(6) : si;
  const label = box.axes ? t("{setting}, every axis (± {unit})", { setting: box.label.replace(/ \(.*/, ""), unit: box.unit }) : box.label;
  return { label, target, value: v, unit: box.unit, from: 0, to: v > 0 ? 2 * v : box.upTo,
    ...(box.kind ? { scale: units.fromDisplay(1, box.kind) } : {}) };
}

export function stationBox(name, box, setting, field, kind) {
  if (box.key === "tip_deg") box = { ...box, label: tipLabel(kind) };
  const id = `test-station-${name}-${box.key}`;
  const tryIt = h("button", { type: "button", class: "btn btn-small", "data-try": `station-${name}-${box.key}`,
    onclick: () => openSweep([stationChoice(name, box, setting)],
      t("{station}'s {setting}", { station: name, setting: box.label.replace(/ \(.*/, "").toLowerCase() })) }, t("Try values…"));
  if (!box.axes) {
    const label = t("{station}: {setting}", { station: name, setting: box.label }), commit = (v) => setStation(name, box.key, v);
    return [h("label", { for: id, text: box.label }),
      h("div", { class: "row" }, box.kind ? measureBox(id, setting, box.kind, label, commit)
        : numberBox(id, setting, label, commit, box.key === "stock" ? 0 : null), tryIt)];
  }
  return [h("span", { class: "test-setup-label", text: box.label }),
    h("div", { class: "row test-com" }, ...box.axes.flatMap((word, i) => [
      h("label", { for: `${id}-${i}`, text: word }),
      (box.kind ? (...a) => measureBox(a[0], a[1], box.kind, a[2], a[3]) : numberBox)(`${id}-${i}`, Number(setting[i]),
        t("{station}: {setting} {axis}", { station: name, setting: box.label, axis: word }), (v) => {
          const now = (((headOf(T.ws).setup || {}).station || {})[name] || {})[box.key] || field;
          const next = [...now].map(Number);
          next[i] = v;
          setStation(name, box.key, next);
        })]), tryIt)];
}

// The test's [setup] settings this field can't use, as [key, words, clear(setup)]: a station it doesn't have, a ball
// on a field that keeps its own (one with pieces of its own: palette's own_pieces) or has none to pick (a climb
// field), a floor spread with no ball on the floor (palette.problems refuses the run for each, in the same sense)
export function leftovers(pal, setup) {
  const out = [];
  for (const name of Object.keys(setup.station || {})) {
    if ((pal.stations || []).includes(name)) continue;
    out.push([`station-${name}`, t("Station \"{name}\": this field has no station by that name", { name }), (x) => {
      delete x.station[name];
      if (!Object.keys(x.station).length) delete x.station;
    }]);
  }
  if (setup.ball && !pal.ball_choice) {
    out.push(["ball", pal.ball && pal.own_pieces ? t("Ball \"{name}\": this field keeps its own ball", { name: setup.ball })
      : t("Ball \"{name}\": this field has no ball to pick", { name: setup.ball }), (x) => { delete x.ball; }]);
  }
  if (setup.piece && !pal.ball_choice) {
    out.push(["piece", pal.piece && pal.own_pieces ? t("Piece \"{name}\": this field keeps its own pieces", { name: setup.piece })
      : t("Piece \"{name}\": this field has no piece to pick", { name: setup.piece }), (x) => { delete x.piece; }]);
  }
  if (setup.floor_spread && Object.keys(setup.floor_spread).length && !pal.floor_piece) {
    out.push(["floor_spread", t("Floor spread: this field has no ball on the floor"), (x) => { delete x.floor_spread; }]);
  }
  return out;
}

// -- grip: a body's pad material, written in its part folder at once (/api/test/grip); the robot is built again with it
const errorOf = (data, status) => (data.errors && data.errors[0] && data.errors[0].message) || data.error || answered(status);

// The bodies Setup offers a grip for: those palette.grips has (a path part's, touching things) that are ticked as
// where a piece should end up, named like a grabber, or already given a grip.
export function gripBodies(pal, hopper) {
  const grips = pal.grips || {};
  return Object.keys(grips).filter((b) => hopper.has(b) || GRABBER.test(b) || grips[b] !== null).sort();
}

// What a body's picker shows: the grip just sent until the palette has it, else the palette's.
function gripShown(pal, body) {
  const now = (pal.grips || {})[body] ?? null;
  if (T.gripSent.has(body) && canon(T.gripSent.get(body)) !== canon(now)) return T.gripSent.get(body);
  T.gripSent.delete(body);
  return now;
}

async function setGrip(body, grip) {
  T.gripSent.set(body, grip);
  const { status, data } = await api("POST", "/api/test/grip", { body, grip });
  if (status === 200 && data.ok) {
    T.setupError = null;
    T.gripOwn.delete(body);
  } else {
    T.setupError = t("{body}'s grip: {why}", { body, why: errorOf(data, status) });
    T.gripSent.delete(body);
  }
  T.setupKey = null;
  await refresh();
  renderSetup();
  renderSetupError();
}

// A grip picker's Default: what part.toml gives the body's pads (palette's grip_defaults), else MuJoCo's 1.0.
function defaultGripOf(pal, body) {
  const mu = (pal.grip_defaults || {})[body];
  return mu == null ? defaultGrip() : t("Default — {friction} (part.toml's; the larger of pad and piece applies)", { friction: fmt(mu) });
}

// `first`: the first row of its part, which carries the part's Try materials… (ruling P32: one per part)
export function gripRow(pal, body, i, running, first) {
  const id = `test-grip-${i}`;
  const now = gripShown(pal, body);
  const own = typeof now === "number" || T.gripOwn.has(body);
  const mats = pal.grip_materials || [];
  const sel = h("select", { id, "data-grip": body, disabled: running, onchange: (e) => {
    const v = e.target.value;
    if (v === OWN) {          // asks for the number first; nothing is sent until it is typed
      T.gripOwn.add(body);
      T.setupKey = null;
      renderSetup();
      const el = document.querySelector(`[data-grip-own="${body}"]`);
      if (el) el.focus();
      return;
    }
    T.gripOwn.delete(body);
    setGrip(body, v === "" ? null : v);
  } }, h("option", { value: "", text: defaultGripOf(pal, body) }),
  ...mats.map((m) => h("option", { value: m[0], text: gripText(m) })),
  h("option", { value: OWN, text: t("My own number…") }));
  sel.value = own ? OWN : now === null ? "" : now;
  const row = [sel];
  if (own) {
    const box = h("input", { id: `${id}-own`, class: "test-num", "data-grip-own": body, inputmode: "decimal", autocomplete: "off",
      disabled: running, placeholder: "0.9", "aria-label": t("{body}: my own friction (measured)", { body }) });
    box.value = box.dataset.orig = typeof now === "number" ? fmt(now) : "";
    const send = () => {
      const text = box.value.trim();
      if (text === box.dataset.orig) return;
      const v = Number(text);
      if (text === "" || !isFinite(v) || v <= 0) {
        T.setupError = t("{body}'s grip: type the friction you measured, a number above 0 such as 0.9", { body });
        renderSetupError();
        box.value = box.dataset.orig;
        return;
      }
      box.dataset.orig = text;
      setGrip(body, v);
    };
    box.addEventListener("change", send);
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
    row.push(box);
  }
  const part = body.split(".")[0];
  if (first) {
    row.push(h("button", { type: "button", class: "btn btn-small", "data-try": `grip-${part}`, disabled: running,
      onclick: () => openSweep(gripChoices(pal, part), t("{part}'s grip", { part })) }, t("Try materials…")));
  }
  return [h("label", { for: id, text: body }), h("div", { class: "row" }, ...row)];
}

// Try materials… for a part: every pad of it first (ruling P28), then each pad on its own.
function gripChoices(pal, part) {
  const named = (g, b) => {
    if (g === null || g === undefined) {
      const mu = (pal.grip_defaults || {})[b];       // part.toml's, else MuJoCo's 1.0 (its words as they were)
      return mu == null ? t("the default friction 1.0") : t("the default friction {friction}", { friction: fmt(mu) });
    }
    if (typeof g === "number") return t("my own {friction}", { friction: fmt(g) });
    const m = (pal.grip_materials || []).find((x) => x[0] === g);
    return m ? m[1] : g;
  };
  const pads = Object.keys(pal.grips || {}).filter((b) => b.split(".")[0] === part);
  return [{ label: t("Every pad of {part}: {pads}", { part, pads: pads.map((b) => b.split(".")[1]).join(", ") }), target: { grip: part },
    materials: true },
  ...pads.map((b) => ({ label: t("{body} only", { body: b }), target: { grip: b }, materials: true, now: named(gripShown(pal, b), b) }))];
}

// the station starter waits until the grabber holds a piece (ruling P31: the server's choice, on a robot with one)
export const grabberStarter = (blocks) => (blocks || []).some((b) => b.do === "wait_until" && b.until && "held" in b.until);

// "On the floor: 1 algae, 1 coral" on a field with several kinds (in the kinds' order), else null
export function floorKindsLine(pal) {
  const kinds = pal.kinds || {};
  if (Object.keys(kinds).length < 2) return null;
  const count = {};
  for (const k of Object.values(pal.floor_kinds || {})) count[k] = (count[k] || 0) + 1;
  const words = Object.keys(kinds).filter((k) => count[k]).map((k) => `${count[k]} ${kinds[k].label}`);
  return words.length ? t("On the floor: {pieces}", { pieces: words.join(", ") }) : null;
}

export function floorHint() {
  const k = floorKind();
  if (!k) return t("Run the test with the ball in front of the robot at each place of a grid, to see which ones it picks up. Tick the axes to spread it over.");
  return [t("Run the test with the {piece} in front of the robot at each place of a grid, to see which ones it picks up. Tick the axes to spread it over.",
    { piece: k.label }), tipWords(k), t("Facing: about the vertical, 0 in line with the robot's path, 90 across it.")].join(" ");
}
