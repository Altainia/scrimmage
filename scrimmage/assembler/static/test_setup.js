// The Test tab's Setup (folded away over the blocks): the field and its start, where a piece should end up, the
// hooks, the piece, the stations, the shuffle, the partner robot, the robot's weight and center of mass and the
// mounts (test.js is the tab; test_floor.js holds the floor spread, the station boxes, leftovers and the grip).

import { $, h, api, showMeasure, readMeasure, measureError, lengthWords } from "./app.js";
import { headOf, setHead, appendBlocks } from "./blocks_defs.js";
import { units } from "./units.js";
import { i18n, t } from "./i18n.js";
import { schedule } from "./test.js";
import { answered, T, canon, fmt, IN, round6 } from "./test_state.js";
import { stationBoxes, tipWords, floorAxes, floorKey, floorDraft, floorSpread, floorChanged, renderFloorCount,
  runSpread, stationBox, leftovers, gripBodies, gripRow, grabberStarter, floorKindsLine, floorHint } from "./test_floor.js";
import { openSweep } from "./test_dialogs.js";

// -- setup ------------------------------------------------------------------------------------------------------------
export async function loadRobot() {
  const [{ status, data }, lib] = await Promise.all([api("GET", "/api/robot/state"),
    T.stock ? null : api("GET", "/api/robot/library")]);
  if (lib && lib.status === 200) T.stock = Object.fromEntries((lib.data.stock || []).map((x) => [x.name, x]));
  T.robot = status === 200 ? data : null;
  renderSetup();
}

const stockDefault = (stock, name, fallback) => {
  const p = T.stock && T.stock[stock] && T.stock[stock].params.find((x) => x.name === name);
  return p ? p.default : fallback;
};
// the part the robot is (attached to nothing): Setup's weight and center of mass are its, when it is the stock chassis
const rootPart = () => (T.robot ? (T.robot.parts || []).find((p) => !p.attach) || null : null);
const chassis = () => { const r = rootPart(); return r && r.stock === "chassis" ? r : null; };
const mass = (c) => Number(c.params.mass ?? stockDefault("chassis", "mass", 46));
const com = (c) => (Array.isArray(c.params.com_offset) ? c.params.com_offset : stockDefault("chassis", "com_offset", [0, 0, 0])).map(Number);
// each part but the root and the drivetrain, which only the Robot tab moves
const isDrivetrain = (p) => !!(p.stock && T.stock && T.stock[p.stock] && T.stock[p.stock].group === "Drivetrain");
const mounted = () => (T.robot ? (T.robot.parts || []).filter((p) => p.attach && !isDrivetrain(p)) : []);
const AXES = [["x", 0, "pos", "forward"], ["y", 1, "pos", "left"], ["z", 2, "pos", "up"], ["yaw", 2, "rot", "turned"]];
// an axis's word ("forward" in AXES, or the center of mass's), as shown
const AXIS_WORDS = { forward: () => t("forward"), left: () => t("left"), up: () => t("up"), turned: () => t("turned") };
const axisWord = (w) => AXIS_WORDS[w]();
const MAX_SHUFFLE = 999999;   // New shuffle's seeds: 1 to this
const DEFAULT_SEED = 1;       // scrimmage/testrange/station.py's: the shuffle of a test with no seed
const FEED_KINDS = ["start_feeding", "stop_feeding", "feed_one"];
// a start's id in words: "at_station" -> "At station"
const startWords = (id) => { const w = id.replace(/_/g, " "); return w.charAt(0).toUpperCase() + w.slice(1); };

// A number box: `blank` (when given) is what an empty box means, and a value equal to it shows empty.
export function numberBox(id, value, label, commit, blank = null) {
  const el = h("input", { id, class: "test-num", inputmode: "decimal", autocomplete: "off", "aria-label": label });
  el.value = el.dataset.orig = blank !== null && value === blank ? "" : fmt(value);
  const send = () => {
    const text = el.value.trim();
    if (text === el.dataset.orig) return;
    const v = text === "" && blank !== null ? blank : Number(text);
    if ((text === "" && blank === null) || !isFinite(v)) { T.setupError = t("{label}: type a number", { label }); renderSetupError(); el.value = el.dataset.orig; return; }
    el.dataset.orig = text;
    commit(v);
  };
  el.addEventListener("change", send);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
  return el;
}

// A box for a length, weight or speed (`kind`) in the Units setting: `si` shows in the unit chosen, and commit gets
// SI (or `toFile(si)`, a key with its own unit), rounded once, to a millionth of that unit; the exact value it showed
// while its text is unchanged.
export function measureBox(id, si, kind, label, commit, toFile = (x) => x) {
  const el = h("input", { id, class: "test-num", inputmode: "decimal", autocomplete: "off", "aria-label": label });
  showMeasure(el, si, kind);
  el.dataset.orig = el.value;
  const send = () => {
    if (el.value.trim() === el.dataset.orig) return;
    const v = readMeasure(el, kind);
    const why = v === null ? t("type a number in {unit}", { unit: units.name(kind) }) : measureError(v);
    if (why) { T.setupError = t("{label}: {why}", { label, why }); renderSetupError(); el.value = el.dataset.orig; return; }
    el.dataset.orig = el.value.trim();
    commit(round6(toFile(v)));
  };
  el.addEventListener("change", send);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
  return el;
}

// Try values… for an SI number shown in the Units setting: from and to in the unit shown, sent in SI
const measured = (label, target, si, kind, extra = {}) => ({ label, target, value: +units.toDisplay(Number(si), kind).toPrecision(6),
  unit: units.name(kind), scale: units.fromDisplay(1, kind), ...extra });

// Robot edits go one at a time, each built (`make()`) from the robot as the last one left it, with its version: two
// boxes changed quickly don't undo each other or come back stale.
let editing = Promise.resolve();
function robotEdit(make) {
  editing = editing.then(async () => {
    const body = T.robot && make(T.robot);
    if (!body) return;
    const { status, data } = await api("POST", "/api/robot/edit", { version: T.robot.version, ...body });
    T.setupError = status === 200 && data.ok ? null
      : (data.errors && data.errors[0] && data.errors[0].message) || answered(status);
    await loadRobot();
  }).catch((e) => { T.setupError = String(e.message || e); renderSetupError(); });
  return editing;
}
const partNamed = (robot, name) => (robot.parts || []).find((p) => p.name === name) || null;

function setHooks(hooks) {
  setTest({ hooks: [...hooks].sort() });
}

// The hopper Setup ticks: the test's (an empty one the student cleared stays empty); for a test that names none,
// starter_hopper's until the student changes it.
const hopperNamed = () => Array.isArray(headOf(T.ws).hopper);
function hopperShown() {
  const mine = headOf(T.ws).hopper || [];
  return hopperNamed() || T.hopperSet ? mine : (T.state && T.state.starter_hopper) || [];
}

// A change to the test's own Setup settings (`patch`: hopper, setup) saves the test; a preselected hopper goes
// along with the first one. Setup is drawn again once the focus has moved on (a box's change comes as it loses the
// focus), so the box tabbed to keeps it.
export function setTest(patch) {
  const shown = hopperShown();
  if (!("hopper" in patch) && !hopperNamed() && shown.length) patch = { ...patch, hopper: [...shown] };
  setHead(T.ws, patch);
  schedule();
  setTimeout(renderSetup, 0);
}
export const setupOf = () => JSON.parse(JSON.stringify(headOf(T.ws).setup || {}));

function setHopper(bodies) {
  T.hopperSet = true;
  setTest({ hopper: [...bodies].sort() });
}

export function setStation(name, key, v) {
  const s = setupOf();
  s.station = s.station || {};
  s.station[name] = { ...(s.station[name] || {}), [key]: v };
  setTest({ setup: s });
}

function newShuffle() {
  const s = setupOf();
  const now = s.seed ?? DEFAULT_SEED;
  let seed = now;
  while (seed === now) seed = 1 + Math.floor(Math.random() * MAX_SHUFFLE);
  s.seed = seed;
  setTest({ setup: s });
}

// Setup's partner robot, [setup] partner: a table puts one ahead of the robot; false hides the robot config's own
// [partner] (the palette's robot_partner); with neither key the config decides.
const FACING = () => [[0, t("the same way")], [180, t("toward me")], [90, t("sideways")]];
const PARTNER = { ahead_in: 60, heading_deg: 0, brake: true };      // a new partner's: 60 in ahead, facing the same way
// (ahead_in is inches in the file, whatever the Units setting: a typed 101.6 mm writes 4)
const ownPartner = () => !!(T.state && T.state.palette && T.state.palette.robot_partner);
function partnerOn() {
  const p = (headOf(T.ws).setup || {}).partner;
  return p === undefined ? ownPartner() : p !== false;
}
function setPartnerOn(on) {
  const s = setupOf();
  if (on) { if (ownPartner()) delete s.partner; else s.partner = { ...PARTNER }; }
  else if (ownPartner()) s.partner = false;
  else delete s.partner;
  setTest({ setup: s });
}
function setPartner(key, v) {
  const s = setupOf();
  s.partner = { ...PARTNER, ...(s.partner && typeof s.partner === "object" ? s.partner : {}), [key]: v };
  setTest({ setup: s });
}

function partnerBox() {
  const on = partnerOn();
  const p = { ...PARTNER, ...((headOf(T.ws).setup || {}).partner || {}) };
  const own = ownPartner() && !((headOf(T.ws).setup || {}).partner);     // the config's, placed where it says
  const parts = [h("legend", { text: t("Partner robot") }),
    h("p", { class: "hint", text: t("A second robot with no program of its own, to push, lift or drive around. It starts ahead of your robot, along the way your robot faces.") }),
    h("label", { class: "test-hook", for: "test-partner" },
      h("input", { type: "checkbox", id: "test-partner", checked: on, onchange: (e) => setPartnerOn(e.target.checked) }),
      h("span", { text: t("A partner robot") }))];
  if (on) {
    if (own) parts.push(h("p", { class: "hint", text: t("Where the robot file's {table} puts it, until you change it here.", { table: "[partner]" }) }));
    const facing = FACING().some(([deg]) => deg === p.heading_deg) ? FACING()
      : [...FACING(), [p.heading_deg, t("turned {degrees}°", { degrees: fmt(p.heading_deg) })]];
    const sel = h("select", { id: "test-partner-facing", onchange: (e) => setPartner("heading_deg", Number(e.target.value)) },
      ...facing.map(([deg, text]) => h("option", { value: String(deg), text })));
    sel.value = String(p.heading_deg);
    parts.push(h("div", { class: "test-setup-grid" },
      h("label", { for: "test-partner-ahead", text: t("Ahead of my robot by") }),
      h("div", { class: "row" }, measureBox("test-partner-ahead", p.ahead_in * IN, "length",
        t("Partner robot: ahead of my robot by ({unit})", { unit: units.name("length") }), (v) => setPartner("ahead_in", v), (si) => si / IN),
      h("span", { text: units.name("length") })),
      h("label", { for: "test-partner-facing", text: t("Facing") }), sel,
      h("span", { class: "test-setup-label", text: t("Drive", { context: "partner" }) }),
      h("label", { class: "test-hook", for: "test-partner-brake" },
        h("input", { type: "checkbox", id: "test-partner-brake", checked: p.brake !== false,
          onchange: (e) => setPartner("brake", e.target.checked) }),
        h("span", { text: t("brakes on") }))));
  }
  return h("fieldset", { class: "test-setup-partner" }, ...parts);
}

const hasFeed = () => !!T.ws && T.ws.getAllBlocks(false).some((b) => FEED_KINDS.includes(b.type));

function addStationBlocks() {
  const blocks = T.state && T.state.station_starter;
  if (!blocks || hasFeed()) return;
  appendBlocks(T.ws, blocks);
  renderSetup();
}

export function renderSetupError() {
  const el = document.getElementById("test-setup-error");
  if (!el) return;
  el.hidden = !T.setupError;
  el.textContent = T.setupError ? t("Not changed: {why}", { why: T.setupError }) : "";
}

// `keepTyping` (a Units change): drawn again even while a box is half typed, so every label names the new unit; the
// box's text (already converted: floorUnits, unitsChanged), focus and caret carry over into the new box.
export function renderSetup({ keepTyping = false } = {}) {
  const s = T.state;
  if (!s || !T.ws) return;
  const body = $("#test-setup-body");
  const open = $("#test-setup").open;
  const pal = s.palette || {};
  const head = headOf(T.ws);
  const setup = head.setup || {};
  const mine = new Set(head.hooks || []);
  const counted = new Set((pal.hooks || []).filter((x) => !mine.has(x)));     // the stock climber's: always hooks
  const hopper = new Set(hopperShown());
  const c = chassis();
  const sum = [[...mine, ...counted].length ? t("hooks: {hooks}", { hooks: [...counted, ...mine].join(", ") }) : t("hooks: none")];
  if (hopper.size) sum.push(t("hopper: {bodies}", { bodies: [...hopper].join(", ") }));
  if (c) sum.push(t("robot {weight}", { weight: units.fmt(mass(c), "mass") }));
  if (T.robot) sum.push(T.robot.range ? t("field: {field}", { field: T.robot.range }) : t("plain field"));
  $("#test-setup-sum").textContent = `· ${sum.join(" · ")}`;
  if (!open) { T.setupKey = null; return; }      // filled in when it is opened
  const ro = !T.robot || !!T.robot.read_only;
  const feeding = hasFeed();
  // drawn again only when something it shows changed (states come four times a second while a run goes)
  // the floor spread's boxes show its draft: the spread the draft wrote is nothing new to draw
  const { floor_spread: fs, ...drawn } = setup;
  const key = canon([pal.bodies || [], [...mine], [...counted], [...hopper], drawn,
    floorKey(fs || {}) === T.floorFor ? null : fs, i18n.lang, pal.station_settings || {},
    pal.ball || null, pal.balls || [], !!pal.ball_choice, !!pal.floor_piece, s.station_starter, feeding, !!s.running,
    !!pal.game, !!head.match, pal.piece || null, pal.piece_presets || null, pal.kinds || {}, pal.floor_kinds || {},
    pal.station_kinds || {},
    pal.floor_ball || null, !!pal.robot_partner, pal.grips || {}, pal.grip_materials || [], [...T.gripOwn], [...T.gripSent],
    (s.fields || []).length, T.robot ? [T.robot.version, T.robot.parts, ro] : null, !!T.stock, T.setupError, units.system]);
  if (key === T.setupKey) return;
  const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
  const act = document.activeElement;
  const typing = focused && act.matches("input.test-num") && act.value !== act.dataset.orig;
  if (typing && !keepTyping) return;
  const kept = typing ? { value: act.value, start: act.selectionStart, end: act.selectionEnd } : null;

  // the mechanism's bodies: not the drivetrain's or the stock root part's (as the mounts); a ticked one always shows
  const skip = new Set();
  if (T.robot && T.robot.part_bodies) {
    const root = rootPart();
    for (const p of T.robot.parts || []) {
      if (!(p.stock && ((root && p.name === root.name) || isDrivetrain(p)))) continue;
      for (const b of T.robot.part_bodies[p.name] || []) skip.add(`${p.name}.${b}`);
    }
  }
  const base = (pal.bodies || []).filter((b) => !skip.has(b));
  const bodyList = (kind, ticked, fixed, onchange) => h("div", { class: "test-hook-list" },
    ...[...new Set([...base, ...ticked, ...fixed])].sort().map((b, i) => {
      const id = `test-${kind}-${i}`;
      const missing = !(pal.bodies || []).includes(b);
      const box = h("input", { type: "checkbox", id, "data-body": b, checked: ticked.has(b) || fixed.has(b), disabled: fixed.has(b),
        onchange: (e) => { const next = new Set(ticked); if (e.target.checked) next.add(b); else next.delete(b); onchange(next); } });
      return h("label", { class: "test-hook", for: id }, box, h("span", { text: missing ? t("{name} (not on the robot)", { name: b }) : b }));
    }));

  const parts = [];
  if (T.robot && !T.robot.read_only) {
    const fields = s.fields || [];
    const now = T.robot.range || "";
    const entry = fields.find((f) => (f.file || "") === now);
    const opts = fields.map((f) => [f.file || "", f.name]);
    if (!entry) opts.push([now, t("{field} (this robot's field)", { field: now })]);
    const sel = h("select", { id: "test-field", onchange: (e) => {
      const f = fields.find((x) => (x.file || "") === e.target.value);
      if (!f || e.target.value === now) return;
      // the new field's first start; a [robot] start_pose goes, and Undo brings it back
      robotEdit(() => (!f.file ? { op: "start", range: null }
        : f.starts.length ? { op: "start", range: f.file, named: f.starts[0] } : { op: "start", range: f.file }));
    } }, ...opts.map(([v, text]) => h("option", { value: v, text })));
    sel.value = now;
    const field = [h("legend", { text: t("The field") }),
      h("p", { class: "hint", text: (T.robot.display_path || T.robot.path)
        ? t("Where the robot starts: an empty floor, a field made to try one mechanism on (a ladder to climb, a ball to pick up, a human player station that feeds balls), or a whole season's field. Picking a field moves the robot to that field's start (this changes {file}). The Robot tab's Undo puts the old field and start back.",
          { file: T.robot.display_path || T.robot.path })
        : t("Where the robot starts: an empty floor, a field made to try one mechanism on (a ladder to climb, a ball to pick up, a human player station that feeds balls), or a whole season's field. Picking a field moves the robot to that field's start (this changes the robot file). The Robot tab's Undo puts the old field and start back.") }),
      h("label", { class: "visually-hidden", for: "test-field", text: t("The field") }), sel];
    const floorWords = floorKindsLine(pal);
    if (floorWords) field.push(h("p", { id: "test-floor-kinds", class: "hint test-floor-kinds", text: floorWords }));
    if (entry && entry.starts.length > 1) {
      const start = T.robot.start || "";
      const starts = [...(start && entry.starts.includes(start) ? []
        : [[start, start ? t("{start} (not on this field)", { start }) : t("Where the robot file puts it")]]),
        ...entry.starts.map((x) => [x, startWords(x)])];
      const pick = h("select", { id: "test-start", onchange: (e) => {
        const v = e.target.value;
        if (v && v !== start && entry.starts.includes(v)) robotEdit(() => ({ op: "start", named: v }));
      } }, ...starts.map(([v, text]) => h("option", { value: v, text })));
      pick.value = start;
      field.push(h("div", { class: "row test-start" }, h("label", { for: "test-start", text: t("Start", { context: "pose" }) }), pick));
    }
    parts.push(h("fieldset", { class: "test-setup-field" }, ...field));
  }
  parts.push(h("fieldset", { class: "test-setup-hopper" },
    h("legend", { text: t("Where a piece should end up") }),
    h("p", { class: "hint", text: t("Tick the bodies a piece should end up in or be held by: a hopper, an intake, a claw. A piece whose center is inside one, or that one holds when the run ends, made it in.") }),
    bodyList("hopper", hopper, new Set(), setHopper)));
  const grips = gripBodies(pal, hopper);
  if (grips.length) {
    parts.push(h("fieldset", { class: "test-setup-grip" },
      h("legend", { text: t("Grip") }),
      h("p", { class: "hint", text: t("What the pads are made of. Friction values are estimates (UNCALIBRATED); docs/test-tab.md says how to measure yours.") }),
      h("div", { class: "test-setup-grid" }, ...grips.flatMap((b, i) => gripRow(pal, b, i, !!s.running,
        !grips.slice(0, i).some((x) => x.split(".")[0] === b.split(".")[0]))))));
  }
  if (pal.game || head.match) {
    // a match takes the range's [game] phases for the run's length, so a time limit goes when it is ticked
    parts.push(h("fieldset", { class: "test-setup-match" },
      h("legend", { text: t("Match") }),
      h("p", { class: "hint", text: t("Play the field's game as a match: auto, a pause with the robot disabled, then teleop. The match sets how long the run lasts. Ticking this box removes the test's time limit, and unticking it won't bring the time limit back.") }),
      h("label", { class: "row" },
        h("input", { type: "checkbox", id: "test-match", checked: !!head.match, onchange: (e) =>
          setTest(e.target.checked ? { match: true, time_limit: undefined } : { match: undefined }) }),
        t("This test is a match"))));
  }
  parts.push(h("fieldset", { class: "test-setup-hooks" },
    h("legend", { text: t("Hooks") }),
    h("p", { class: "hint", text: t("Tick the bodies that grab a bar. A stock climber's hooks always count.") }),
    bodyList("hook", mine, counted, setHooks)));
  const own = pal.piece || pal.ball;
  if (pal.ball_choice && own) {
    // the field's own first, then every ball and every other piece; a pick is [setup] piece (an old test's ball goes)
    const presets = pal.piece_presets || { balls: pal.balls || [], pieces: [] };
    const now = setup.piece || setup.ball || own;
    const odd = now !== own && ![...presets.balls, ...presets.pieces].includes(now) ? [now] : [];
    const group = (label, names) => h("optgroup", { label },
      ...names.filter((b) => b !== own).map((b) => h("option", { value: b, text: b })));
    const sel = h("select", { id: "test-piece", onchange: (e) => {
      const next = setupOf();
      delete next.ball;
      delete next.piece;
      if (e.target.value !== own) next.piece = e.target.value;
      setTest({ setup: next });
    } }, h("option", { value: own, text: t("{piece} (the field's)", { piece: own }) }), group(t("Balls"), presets.balls),
      group(t("Other pieces"), presets.pieces), ...odd.map((b) => h("option", { value: b, text: t("{piece} (not in the list)", { piece: b }) })));
    sel.value = now;
    parts.push(h("fieldset", { class: "test-setup-piece" },
      h("legend", { text: t("Piece") }),
      h("p", { class: "hint", text: t("Pick the piece this test uses.") }),
      h("label", { class: "visually-hidden", for: "test-piece", text: t("Piece") }), sel));
  }
  const settings = pal.station_settings || {};
  const kinds = pal.kinds || {};
  const several = Object.keys(kinds).length > 1;
  for (const name of pal.stations || []) {
    const field = settings[name] || {};
    const chosen = (setup.station || {})[name] || {};
    const kind = kinds[(pal.station_kinds || {})[name]] || null;
    const piece = !!kind && kind.form !== "ball";          // a ball has no tip or turn
    parts.push(h("fieldset", { class: "test-setup-station", "data-station": name },
      h("legend", { text: several && kind ? t("Station: {name} ({piece})", { name, piece: kind.label }) : t("Station: {name}", { name }) }),
      h("p", { class: "hint", text: [t("How the human player feeds pieces. Each box shows the field's value until you change it; a change is saved in the test. Each piece varies by up to ± each spread."),
        ...(piece ? [tipWords(kind), t("Facing: about the vertical, from the way the station feeds. Extra tumble turns each piece a little more about each axis.")] : [])].join(" ") }),
      h("div", { class: "test-setup-grid" }, ...stationBoxes(piece)
        .flatMap((box) => stationBox(name, box, chosen[box.key] ?? field[box.key], field[box.key], kind)))));
  }
  if ((pal.stations || []).length) {
    const seed = setup.seed ?? DEFAULT_SEED;
    parts.push(h("fieldset", { class: "test-setup-shuffle" },
      h("legend", { text: t("Shuffle") }),
      h("p", { class: "hint", text: t("Which draw of the spreads the stations feed: the same shuffle gives the same feeds every run, so a comparison is fair. New shuffle checks it wasn't a lucky draw.") }),
      h("div", { class: "row" }, h("span", { id: "test-seed", class: "test-seed", text: t("Shuffle {seed}", { seed }) }),
        h("button", { type: "button", id: "test-new-shuffle", class: "btn btn-small", onclick: newShuffle }, t("New shuffle")),
        h("button", { type: "button", class: "btn btn-small", "data-try": "seed",
          onclick: () => openSweep([{ label: t("Shuffle"), target: { setup: "seed" }, value: seed, unit: "", from: seed, to: seed + 4 }], t("the shuffle")) }, t("Try values…")))));
  }
  if (pal.floor_piece) {
    const draft = floorDraft();
    const box = (axis, k, label) => {
      const el = h("input", { id: `test-spread-${axis}-${k}`, class: "test-num", inputmode: "decimal", autocomplete: "off", "aria-label": label });
      el.value = el.dataset.orig = draft[axis][k];
      el.addEventListener("change", floorChanged);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); floorChanged(); } });
      return el;
    };
    parts.push(h("fieldset", { class: "test-setup-floor" },
      h("legend", { text: t("Floor spread") }),
      h("p", { class: "hint", text: floorHint() }),
      h("table", { class: "test-mounts test-floor" },
        h("thead", {}, h("tr", {}, h("th", { text: t("Axis") }), h("th", { text: t("from") }), h("th", { text: t("to") }), h("th", { text: t("steps") }))),
        h("tbody", {}, ...floorAxes().map(([axis, label]) => h("tr", { "data-axis": axis },
          h("th", { scope: "row" }, h("label", { class: "test-hook", for: `test-spread-${axis}-on` },
            h("input", { type: "checkbox", id: `test-spread-${axis}-on`, checked: draft[axis].on, onchange: floorChanged }),
            h("span", { text: label }))),
          h("td", {}, box(axis, "from", t("{axis} from", { axis: label }))), h("td", {}, box(axis, "to", t("{axis} to", { axis: label }))),
          h("td", {}, box(axis, "steps", t("{axis} steps", { axis: label }))))))),
      h("div", { class: "row" }, h("span", { id: "test-spread-count", class: "test-spread-count", role: "status" }),
        h("button", { type: "button", id: "test-spread-run", class: "btn btn-small", onclick: runSpread }, t("Run the spread")))));
  }
  parts.push(partnerBox());
  const left = leftovers(pal, setup);
  if (left.length) {
    parts.push(h("fieldset", { class: "test-setup-leftover" },
      h("legend", { text: t("Settings for another field") }),
      h("p", { class: "hint", text: t("The test has settings this field can't use (they were made on another field). Clear them to run the test here.") }),
      ...left.map(([key, words, clear]) => h("div", { class: "row", "data-leftover": key },
        h("span", { text: words }),
        h("button", { type: "button", class: "btn btn-small", "aria-label": t("Clear: {setting}", { setting: words }),
          onclick: () => { const next = setupOf(); clear(next); setTest({ setup: next }); } }, t("Clear"))))));
  }
  if (s.station_starter && !feeding) {
    parts.push(h("fieldset", { class: "test-setup-starter" },
      h("legend", { text: t("Station blocks") }),
      h("p", { class: "hint", text: grabberStarter(s.station_starter)
        ? t("Start feeding, wait until the grabber holds a piece, stop feeding: added at the end of the test.")
        : t("Start feeding, wait until 5 pieces are where a piece should end up, stop feeding: added at the end of the test.") }),
      h("button", { type: "button", id: "test-add-station-blocks", class: "btn btn-small", onclick: addStationBlocks }, t("Add the station blocks"))));
  }
  if (c) {
    const [f, l, u] = com(c);
    const send = (key, v) => robotEdit(() => ({ op: "params", name: c.name, params: { [key]: v } }));
    const L = units.name("length");
    const comBox = (i, word, v) => measureBox(`test-com-${word}`, v, "length", t("Center of mass {axis} ({unit})", { axis: axisWord(word), unit: L }), (x) => robotEdit((r) => {
      const now = partNamed(r, c.name);
      if (!now) return null;
      const off = com(now); off[i] = x;
      return { op: "params", name: c.name, params: { com_offset: off } };
    }));
    parts.push(h("fieldset", { class: "test-setup-robot" },
      h("legend", { text: t("The robot") }),
      h("p", { class: "hint", text: t("Weight: everything that isn't one of the robot's parts (frame, battery, bumpers). The Robot tab's Undo takes changes back.") }),
      h("div", { class: "test-setup-grid" },
        h("label", { for: "test-mass", text: t("Weight ({unit})", { unit: units.name("mass") }) }),
        h("div", { class: "row" }, measureBox("test-mass", mass(c), "mass", t("Weight ({unit})", { unit: units.name("mass") }), (v) => send("mass", v)),
          h("button", { type: "button", class: "btn btn-small", "data-try": "mass", disabled: ro,
            onclick: () => openSweep([measured(t("Weight"), { robot: "mass" }, mass(c), "mass")], t("the robot's weight")) }, t("Try values…"))),
        h("span", { class: "test-setup-label", text: t("Center of mass ({unit})", { unit: L }) }),
        h("div", { class: "row test-com" },
          h("label", { for: "test-com-forward", text: axisWord("forward") }), comBox(0, "forward", f),
          h("label", { for: "test-com-left", text: axisWord("left") }), comBox(1, "left", l),
          h("label", { for: "test-com-up", text: axisWord("up") }), comBox(2, "up", u),
          h("button", { type: "button", class: "btn btn-small", "data-try": "com", disabled: ro,
            onclick: () => openSweep([["forward", 0], ["left", 1], ["up", 2]].map(([w, i]) => measured(t("Center of mass {axis}", { axis: axisWord(w) }),
              { robot: `com_${w}` }, com(c)[i], "length")), t("the robot's center of mass")) }, t("Try values…"))))));
  }
  const ms = mounted();
  if (ms.length) {
    const L = units.name("length");
    parts.push(h("fieldset", { class: "test-setup-mounts" },
      h("legend", { text: t("Where the parts are mounted") }),
      h("p", { class: "hint", text: t("How far each part is moved from where it attaches: forward (x), left (y), up (z) in {unit}, turned (yaw) in degrees.", { unit: lengthWords() }) }),
      h("table", { class: "test-mounts" },
        h("thead", {}, h("tr", {}, h("th", { text: t("Part") }), ...AXES.map(([a]) => h("th", { text: a === "yaw" ? t("yaw (°)") : `${a} (${L})` })), h("th", {}))),
        h("tbody", {}, ...ms.map((p) => h("tr", { "data-part": p.name },
          h("th", { scope: "row", title: t("on {attach}", { attach: p.attach }), text: p.name }),
          ...AXES.map(([a, i, key, word]) => {
            const label = t("{part} {axis} ({unit})", { part: p.name, axis: axisWord(word), unit: a === "yaw" ? "°" : L });
            const commit = (v) => robotEdit((r) => {
              const now = partNamed(r, p.name);
              if (!now) return null;
              const pos = [...now.pos].map(Number), rot = [...now.rot].map(Number);
              (key === "pos" ? pos : rot)[i] = v;
              return { op: "attach", name: p.name, attach: now.attach, pos, rot };
            });
            return h("td", {}, a === "yaw" ? numberBox(`test-mount-${p.name}-${a}`, p[key][i], label, commit)
              : measureBox(`test-mount-${p.name}-${a}`, Number(p[key][i]), "length", label, commit));
          }),
          h("td", {}, h("button", { type: "button", class: "btn btn-small", "data-try": `mount-${p.name}`, disabled: ro,
            onclick: () => openSweep(AXES.map(([a, i, key, word]) => (a === "yaw"
              ? { label: `${axisWord(word)} (${a})`, target: { mount: p.name, axis: a }, value: Number(p[key][i]), unit: "°" }
              : measured(`${axisWord(word)} (${a})`, { mount: p.name, axis: a }, p[key][i], "length"))),
            t("where {part} is mounted", { part: p.name })) }, t("Try values…")))))))));
  }
  const note = !T.robot ? h("p", { class: "hint", text: t("Loading the robot…") })
    : T.robot.read_only ? h("p", { class: "hint", text: t("The robot file can't be changed here: {why}", { why: T.robot.read_only_reason }) }) : null;
  body.replaceChildren(...parts, note || "", h("p", { id: "test-setup-error", class: "error", role: "alert", hidden: true }));
  if (ro) for (const x of body.querySelectorAll(".test-setup-robot input.test-num, .test-setup-mounts input.test-num")) x.disabled = true;
  if (pal.floor_piece) renderFloorCount(floorSpread(T.floor));
  T.setupKey = key;
  renderSetupError();
  const el = focused && document.getElementById(focused);
  if (el) {
    if (kept) { el.value = kept.value; try { el.setSelectionRange(kept.start, kept.end); } catch { /* not a text box */ } }
    el.focus();
  }
}
