// The Test tab's blocks: one Blockly block type per `do` kind and per condition kind, the palette (Blockly's
// toolbox, filled from the robot's names), and the test JSON (scrimmage/blocks/program.py to_json) to and from a
// Blockly workspace. Blockly itself (window.Blockly, vendor/blockly.min.js) is loaded by test.js before any of this
// runs.
//
//   defineBlocks(names)            registers the block types once; `names()` answers the current palette (or null)
//   relabel(ws)                    a Language change: the block types defined again in its words, the workspace made
//                                  again from its saved state (an open editor committed first; nothing is saved)
//   toolbox(palette)               the flyout's contents: Move, Wait, Repeat, Check (a block only when every one of its
//                                  pickers has something to pick: aim needs a turret and a goal, scored a goal, the
//                                  cylinder blocks and the air pressure check a cylinder, the element check a hinged
//                                  or sliding field element, clears a field element, the partner checks a partner
//                                  robot, the sensor check one of the robot's [[sensor]]s, the sees_tag check a
//                                  camera on a field with AprilTags)
//   testToWorkspace(test, ws)      replaces the workspace's blocks with the test's (one stack, top to bottom)
//   appendBlocks(ws, blocks)       joins blocks (the test JSON's) below the test's last block
//   workspaceToTest(ws)            the test JSON of the workspace: the test is the stack that holds the test's first
//                                  block (a stack dragged off it is left out); without one, the stack with the most
//                                  blocks; the header (robot, hooks, ...) is the one testToWorkspace was given
//   setUsed(tests)                 the names the tests use (the saved one and the one on screen), offered by the
//                                  pickers even when the palette doesn't have them
//   placeOf(ws) / incomplete(ws)   block -> its place ([i, j, ...] as problems() gives them); the blocks that can't be
//                                  saved yet (an empty condition socket) or aren't joined to the test
//   headOf(ws) / setHead(ws, p)    the test's header (robot, hooks, ...) as workspaceToTest writes it / changed
//   blockSettings(b, defaults)     the keys a block takes with no box (a timeout, a drive's or turn's volts, a repeat
//   setBlockSettings(b, values)    until's max) for Block settings…, with the state's block_defaults; writing them back
//   sweepNumber(block)             the block's number a sweep can try ({key, field, unit, scale}: scrimmage/blocks/
//                                  sweep.py's block keys; scale: the file's unit per shown unit), or null
//   showUnits(ws, from)            shows every block's lengths and pressures again after a Units change (`from`: the
//                                  system they were shown in)
//
// A block keeps the settings it has no box for (a move's timeout, a repeat until's max, a drive's volts) in
// `block.data`, so the round trip test -> workspace -> test is exact.
//
// Lengths and pressures show in the Units setting (static/units.js), while the test file keeps its own units (spec
// U4): a block's `unit` ("deg", "in" or "m"; a joint's length is a robot length, shown in in or mm), the `_in` keys
// (inches), the pressure check's psi, and a drive's distance in meters (a field length: ft or m).
//   - Loading, each number is shown converted, and block.data's DISPLAY keeps the file's number and unit beside
//     what was shown. Saving, a number still showing that writes the file's own number and unit back, so a test
//     nobody edited is written as it was, byte for byte.
//   - A number typed is written in the file's unit: an `_in` key typed as 101.6 mm writes 4. A move or a joint check
//     keeps the length unit its file had ("m" stays meters, shown in in or mm); one that had none (new, or turned
//     from degrees) gets the system's, "in" in Imperial and "m" in Metric. Each is rounded to a millionth of its unit
//     (1 µm in meters).

import { units } from "./units.js";
import { t } from "./i18n.js";

export const STYLES = { move: "move_blocks", wait: "wait_blocks", repeat: "repeat_blocks", check: "check_blocks" };
const COND = "Condition";

// Names the tests use that the palette may not have (a joint renamed on Import again): a picker keeps offering them
// so the block keeps its value (and shows the problem) instead of quietly picking another name. Rebuilt by setUsed.
const KINDS_OF_NAME = { motor: "robot", joint: "robot", robot: "robot", body: "robot", turret: "robot", cylinder: "robot",
  sensor: "robot", color: "robot", camera: "robot",
  field: "field", tag: "field", piece: "field", bar: "field", level: "field", station: "field", element: "field", rod: "field",
  goal: "field", moving: "field", cleared: "field", zone: "field" };
let used = {};
const resetUsed = () => {
  used = Object.fromEntries(Object.keys(KINDS_OF_NAME).map((k) => [k, new Set()]));
  used.sideways = false;   // a test drives left or right (a tank can't, but the block keeps what the file says)
};
resetUsed();

let names = () => null;
let loading = 0;        // testToWorkspace is filling the workspace: pickers don't adjust each other

const SPECIAL = { hook: () => t("any hook"), "next bar": () => t("next bar"), "any bar": () => t("any bar"),
  any: () => t("any piece"), all: () => t("all motors"), "a piece": () => t("a piece") };
// a name only the test uses, marked as missing from its side (KINDS_OF_NAME)
const MISSING = { robot: (name) => t("{name} (not on the robot)", { name }), field: (name) => t("{name} (not on the field)", { name }) };

// [[label, value], ...] for a picker: `special` first (when given: a value SPECIAL names, or [label, value]), the
// palette's names, then names only the test uses (marked as missing from the robot).
function choices(list, kind, special = [], label = (v) => v) {
  const out = [], seen = new Set();
  for (const sp of special) {
    const [text, v] = Array.isArray(sp) ? sp : [SPECIAL[sp] ? SPECIAL[sp]() : sp, sp];
    out.push([text, v]);
    seen.add(v);
  }
  for (const v of list) if (!seen.has(v)) { out.push([label(v), v]); seen.add(v); }
  for (const v of used[kind]) if (!seen.has(v)) { out.push([MISSING[KINDS_OF_NAME[kind]](label(v)), v]); seen.add(v); }
  return out.length ? out : [[t("(none)"), "(none)"]];
}

const pal = () => names() || {};
const motorNames = () => (pal().motors || []).map((m) => m.name);
const jointNames = () => (pal().joints || []).map((j) => j.name);
const robotSide = () => [...(pal().hooks || []), ...(pal().parts || []), ...(pal().bodies || [])];
// bars, elements and pieces first, then the field's other surfaces (the carpet, the walls)
const fieldSide = () => [...(pal().bars || []), ...(pal().elements || []), ...(pal().pieces || []), ...(pal().targets || [])];
const has = (xs) => (xs || []).length > 0;
const cylinderNames = () => (pal().cylinders || []).map((c) => c.name);
const movingNames = () => (pal().moving_elements || []).map((e) => e.name);
const sensorNames = () => (pal().sensors || []).map((s) => s.name);
const cameraNames = () => (pal().sensors || []).filter((s) => s.kind === "camera").map((s) => s.name);
const ANY_GOAL = () => [[t("any goal"), "any"]];   // a scored check's goal: every goal together (palette.py ANY_GOAL)

// A floor piece as a student reads it ("ball 10", "coral 2"): the palette's `piece_labels`, the names every sentence uses
// (scrimmage/testrange/balls.py piece_name). The value stays the body's name.
function pieceLabel(name) {
  return (pal().piece_labels || {})[name] || name;
}

const OPTIONS = {
  motor: () => choices(motorNames(), "motor"),
  stopMotor: () => choices(motorNames(), "motor", ["all"]),
  moveJoint: () => choices(motorNames(), "joint"),
  joint: () => choices(jointNames(), "joint"),
  robot: () => choices(robotSide(), "robot", has(pal().hooks) ? ["hook"] : []),
  field: () => choices(fieldSide(), "field", has(pal().bars) ? ["next bar", "any bar"] : [], pieceLabel),
  body: () => choices(pal().bodies || [], "body"),
  piece: () => choices(pal().pieces || [], "piece", has(pal().pieces) ? ["any"] : [], pieceLabel),
  heldBy: () => choices([...(pal().parts || []), ...(pal().bodies || [])], "robot"),
  restsOn: () => choices(pal().elements || [], "element", (pal().pieces || []).length > 1 || used.element.has("a piece") ? ["a piece"] : []),
  rod: () => choices(pal().rods || [], "rod"),
  bar: () => choices(pal().bars || [], "bar", has(pal().bars) ? ["next bar"] : []),
  level: () => choices(pal().levels || [], "level"),
  station: () => choices(pal().stations || [], "station"),
  turret: () => choices(pal().turrets || [], "turret"),
  goal: () => choices(pal().goals || [], "goal"),
  scoredGoal: () => choices(pal().goals || [], "goal", ANY_GOAL()),
  cylinder: () => choices(cylinderNames(), "cylinder"),
  moving: () => choices(movingNames(), "moving"),
  sensor: () => choices(sensorNames(), "sensor"),
  // a color sensor's check: a piece of any color (no sees in the file), or one of the palette's named colors
  sees: () => choices(pal().sensor_colors || [], "color", [[t("a piece"), "any"]]),
  camera: () => choices(cameraNames(), "camera"),
  // a camera's check: any tag (no tag in the file), or one of the field's AprilTags by its ID (a dropdown's value is
  // text: the test JSON's tag is the number)
  tag: () => choices((pal().tags || []).map(String), "tag", [[t("any tag"), "any"]], (id) => t("tag {id}", { id })),
  cleared: () => choices(pal().elements || [], "cleared"),
  zone: () => choices(pal().zones || [], "zone"),
  // left and right only on a swerve robot (or when the test already has one)
  heading: () => [[t("forward"), "forward"], [t("back"), "back"],
    ...(pal().drive_kind === "swerve" || used.sideways ? [[t("left"), "left"], [t("right"), "right"]] : [])],
};
// The fixed pickers' words, made when the blocks are defined (defineBlocks: again after a Language change), so each
// stays a list Blockly can take the shared words out of ("or more", "or fewer": "or").
const TURNS = () => [[t("left"), "left"], [t("right"), "right"]];
const HOPPER_SIDES = () => [[t("or more"), "at_least"], [t("or fewer"), "at_most"]];
const LEN = "len";      // the UNIT picker's length (the file's own length unit is kept in block.data's DISPLAY)
const UNITS = () => [["°", "deg"], [units.name("length"), LEN]];
const SIDES = () => [[t("above"), "above"], [t("below"), "below"]];
const CYLINDER_TO = () => [[t("extend"), "extend"], [t("retract"), "retract"], [t("off"), "off"]];
const CYLINDER_IS = () => [[t("extended"), "extended"], [t("retracted"), "retracted"]];
const AIR_SIDES = () => [[t("stored"), "stored"], [t("working"), "working"]];
// A sensor check's on or off, and the same choice worded for a color sensor. Given as functions: Blockly takes the
// words a list's options share ("is", "seeing") out into labels of their own, which wouldn't hide with the picker.
const SENSOR_IS = () => [[t("is on"), "on"], [t("is off"), "off"]];
const ZONE_IS = () => [[t("in", { context: "zone" }), "in"], [t("out of", { context: "zone" }), "out"]];
const MATCH_PHASES = () => [[t("auto", { context: "match" }), "auto"], [t("pause", { context: "match" }), "pause"],
  [t("teleop", { context: "match" }), "teleop"]];
const POINTS_PHASES = () => [[t("auto", { context: "match" }), "auto"], [t("teleop", { context: "match" }), "teleop"],
  [t("endgame", { context: "match" }), "endgame"], [t("total", { context: "match" }), "total"]];
const SENSOR_SEEING = () => [[t("is seeing"), "on"], [t("isn't seeing"), "off"]];

// A joint picker's validator: picking a joint that slides (or turns) switches a unit that doesn't fit it.
function fitUnit(unitField) {
  return function (value) {
    const block = this.getSourceBlock();
    if (loading || !block) return value;
    const j = (pal().joints || []).find((x) => x.name === value);
    const unit = block.getFieldValue(unitField);
    if (j && j.unit === "in" && unit === "deg") block.setFieldValue(LEN, unitField);
    if (j && j.unit === "deg" && unit !== "deg") block.setFieldValue("deg", unitField);
    return value;
  };
}

// An element check's validator: the unit shown follows the element picked (° for a hinged one, in or mm for a
// sliding one; nothing for a name the field doesn't have).
const movingElement = (name) => (pal().moving_elements || []).find((x) => x.name === name) || null;
const sliding = (name) => { const e = movingElement(name); return !!e && e.unit === "in"; };
function elementUnit(value) {
  const block = this.getSourceBlock();
  const e = movingElement(value);
  if (block && block.getField("UNIT")) block.setFieldValue(e ? (e.unit === "deg" ? "°" : units.name("length")) : "", "UNIT");
  return value;
}

// -- display units (see the top) ----------------------------------------------------------------------------------------
const DISPLAY = "_display";     // block.data's {field: {file, unit, shown, system}}: the file's number as loaded
const FILE_SI = { in: 0.0254, m: 1, psi: 6894.757293168361 };     // the SI value of one of a test file's units
const label = (name) => ({ type: "field_label", name, text: "" });
// The numbers a block shows in the Units setting, by block type: field -> {kind, unit: the file's (null: the UNIT
// picker's), label: the field_label naming the shown unit, min/max in the file's unit, json(a) / on(b): whether
// the number is a length (or pressure) in the block's JSON / on the block now, else it is shown as it is}.
const MEASURES = {
  move: { TO: { kind: "length", unit: null, json: (a) => a.unit !== "deg", on: (b) => b.getFieldValue("UNIT") === LEN } },
  drive: { AMOUNT: { kind: "field", unit: "m", json: (a) => !("seconds" in a), on: (b) => b.getFieldValue("WHAT") === "distance" } },
  cond_joint: { VALUE: { kind: "length", unit: null, json: (a) => a.unit !== "deg", on: (b) => b.getFieldValue("UNIT") === LEN } },
  cond_seated: { DEPTH: { kind: "length", unit: "in", label: "DEPTH_UNIT" } },
  cond_pressure: { PSI: { kind: "pressure", unit: "psi", label: "PSI_UNIT" } },
  cond_element: { VALUE: { kind: "length", unit: "in", json: (a) => sliding(a.element), on: (b) => sliding(b.getFieldValue("ELEMENT")) } },
  cond_clears: { CLEAR: { kind: "length", unit: "in", label: "CLEAR_UNIT", min: 0.1, max: 19.6 } },
  cond_partner_lifted: { LIFT: { kind: "length", unit: "in", label: "LIFT_UNIT", min: 0.1 } },
  cond_partner_moved: { MOVED: { kind: "length", unit: "in", label: "MOVED_UNIT", min: 0.1 } },
};
// A new block's number in each system (the palette's), [imperial, metric]: round numbers in the units shown
const DEFAULTS = { drive: { AMOUNT: [1, 0.3] }, cond_seated: { DEPTH: [3, 75] }, cond_pressure: { PSI: [60, 400] },
  cond_clears: { CLEAR: [2, 50] }, cond_partner_lifted: { LIFT: [2, 50] }, cond_partner_moved: { MOVED: [6, 150] } };
const systemDefaults = (type) => Object.fromEntries(Object.entries(DEFAULTS[type] || {})
  .map(([f, [imperial, metric]]) => [f, units.system === "metric" ? metric : imperial]));

function dataOf(b) {
  try { return b.data ? JSON.parse(b.data) : {}; } catch { return {}; }
}
const factor = (kind, system) => (units.table ? units.table.units[system][kind].factor : 1);
const shownOf = (file, unit, kind) => Number(units.shown(file * FILE_SI[unit], kind));
const lengthUnit = (system) => (system === "metric" ? "m" : "in");

// A measured field's number for the file, {value, unit}: the file's own while the block still shows what it loaded
// (in `system`, the units it is shown in), else what is shown in the file's unit; a number that isn't a length now
// (degrees, seconds, a hinged element) as it is.
function stored(b, field, system = units.system) {
  const m = MEASURES[b.type][field];
  const v = b.getFieldValue(field);
  if (m.on && !m.on(b)) return { value: v, unit: m.unit === null ? "deg" : m.unit };
  const memo = (dataOf(b)[DISPLAY] || {})[field];
  if (memo && memo.system === system && memo.shown === v) return { value: memo.file, unit: memo.unit };
  const unit = m.unit || (memo ? memo.unit : lengthUnit(system));
  return { value: Number((v / factor(m.kind, system) / FILE_SI[unit]).toFixed(6)), unit };
}

// Block state (testToWorkspace's) with its measured numbers shown in the Units setting, and DISPLAY kept.
function showState(st, a) {
  const ms = MEASURES[st.type];
  if (!ms) return st;
  const memo = {};
  for (const [field, m] of Object.entries(ms)) {
    const file = st.fields[field];
    if ((m.json && !m.json(a)) || typeof file !== "number" || !isFinite(file)) continue;
    const unit = m.unit || a.unit;
    st.fields[field] = shownOf(file, unit, m.kind);
    memo[field] = { file, unit, shown: st.fields[field], system: units.system };
  }
  if (Object.keys(memo).length) st.data = JSON.stringify({ ...(st.data ? JSON.parse(st.data) : {}), [DISPLAY]: memo });
  return st;
}

// A block's unit labels, limits and pickers as the Units setting names them.
function unitLabels(b) {
  for (const [field, m] of Object.entries(MEASURES[b.type] || {})) {
    if (m.label) b.getField(m.label).setValue(units.name(m.kind));
    const f = b.getField(field);
    const shown = (x) => +(x * FILE_SI[m.unit] * factor(m.kind, units.system)).toPrecision(6);
    if (m.min !== undefined) f.setMin(shown(m.min));
    if (m.max !== undefined) f.setMax(shown(m.max));
  }
  for (const name of ["UNIT", "WHAT"]) {      // a picker whose words come from the setting: its shown words again
    const f = b.getField(name);
    if (!f || !(f instanceof window.Blockly.FieldDropdown)) continue;
    const v = f.getValue();
    f.selectedOption = f.getOptions(false).find((o) => o[1] === v) || f.selectedOption;
    f.forceRerender();
  }
  if (b.type === "cond_element") elementUnit.call(b.getField("ELEMENT"), b.getFieldValue("ELEMENT"));
}

export function showUnits(ws, from) {
  const B = window.Blockly;
  // a number being typed in a block's editor: committed first, as typed (in the units it was typed in)
  ws.hideChaff();
  B.Events.disable();
  try {
    for (const b of ws.getAllBlocks(false)) {
      const ms = MEASURES[b.type];
      if (ms) {
        const data = dataOf(b), memo = {};
        const now = Object.entries(ms).filter(([, m]) => !m.on || m.on(b)).map(([field, m]) => [field, m, stored(b, field, from)]);
        unitLabels(b);
        for (const [field, m, { value, unit }] of now) {
          memo[field] = { file: value, unit, shown: shownOf(value, unit, m.kind), system: units.system };
          b.setFieldValue(memo[field].shown, field);
        }
        if (Object.keys(memo).length) data[DISPLAY] = memo; else delete data[DISPLAY];
        b.data = Object.keys(data).length ? JSON.stringify(data) : null;
      } else unitLabels(b);
    }
  } finally {
    B.Events.enable();
  }
  ws.clearUndo();       // its steps hold numbers in the old units
}

// A sensor check reads "sensor mouth is on" for a beam break or distance sensor, and "sensor eye is seeing red" (or
// "isn't seeing a piece") for a color sensor, or when the check already names a color. IS and SEEING are the same
// choice (on or off) worded both ways, kept equal; one of them shows. A validator runs before its field takes the
// value, so the field being set gives its new value.
function showSees(block, sensor, sees) {
  const s = (pal().sensors || []).find((x) => x.name === sensor);
  const color = (s ? s.kind === "color" : false) || sees !== "any";
  block.getField("IS").setVisible(!color);
  block.getField("SEEING").setVisible(color);
  block.getField("SEES").setVisible(color);
  if (block.rendered) block.queueRender();
}

// partner_lifted's level box: the degrees show only while it is ticked.
function showLevel(block, on) {
  for (const f of ["WITHIN", "DEG"]) block.getField(f).setVisible(on);
  if (block.rendered) block.queueRender();
}

const dd = (name, options) => ({ type: "field_dropdown", name, options });
const num = (name, value, extra = {}) => ({ type: "field_number", name, value, ...extra });
const stmt = { previousStatement: null, nextStatement: null };

// Each block type: its Blockly JSON (without type), how its fields come from the block's JSON and go back.
const KINDS = {
  spin: {
    def: () => ({ message0: t("spin %1 at %2 V"), args0: [dd("MOTOR", OPTIONS.motor), num("VOLTS", 6, { min: -12, max: 12 })],
      style: STYLES.move, ...stmt, tooltip: t("Run a motor at this many volts until a later block changes it.") }),
    keys: ["motor", "volts"],
    fields: (a) => ({ MOTOR: a.motor, VOLTS: a.volts }),
    args: (b) => ({ motor: b.getFieldValue("MOTOR"), volts: b.getFieldValue("VOLTS") }),
  },
  move: {
    def: () => ({ message0: t("move %1 to %2 %3"), args0: [dd("JOINT", OPTIONS.moveJoint), num("TO", 40), dd("UNIT", UNITS)],
      style: STYLES.move, ...stmt, tooltip: t("Drive a joint to this position and wait until it gets there.") }),
    keys: ["joint", "to", "unit"],
    fields: (a) => ({ JOINT: a.joint, TO: a.to, UNIT: a.unit === "deg" ? "deg" : LEN }),
    args: (b) => { const to = stored(b, "TO"); return { joint: b.getFieldValue("JOINT"), to: to.value, unit: to.unit }; },
    init(b) { b.getField("JOINT").setValidator(fitUnit("UNIT")); },
  },
  drive: {
    def: () => ({ message0: t("drive %1 %2 %3"),
      args0: [dd("DIR", OPTIONS.heading), num("AMOUNT", 1), dd("WHAT", () => [[units.name("field"), "distance"], ["s", "seconds"]])],
      style: STYLES.move, ...stmt, tooltip: t("Drive the robot this far, or for this long. Forward with a minus distance drives back.") }),
    keys: ["distance", "seconds", "direction"],
    fields: (a) => ({ DIR: a.direction || "forward",
      ...("seconds" in a ? { AMOUNT: a.seconds, WHAT: "seconds" } : { AMOUNT: a.distance, WHAT: "distance" }) }),
    // forward is what no direction means: only the others are written (a file that says forward keeps it, from block.data)
    args: (b) => ({ [b.getFieldValue("WHAT")]: stored(b, "AMOUNT").value,
      ...(b.getFieldValue("DIR") === "forward" ? {} : { direction: b.getFieldValue("DIR") }) }),
    extra: (a) => (a.direction === "forward" ? { direction: "forward" } : {}),
    drop: (b) => (b.getFieldValue("WHAT") === "seconds" ? ["timeout"] : []),   // a timeout only goes with a distance
  },
  turn: {
    def: () => ({ message0: t("turn %1 %2 °"), args0: [dd("DIR", TURNS()), num("DEGREES", 90, { min: 0, max: 720 })],
      style: STYLES.move, ...stmt, tooltip: t("Turn the robot on the spot this many degrees, left or right.") }),
    keys: ["degrees"],
    fields: (a) => ({ DIR: a.degrees < 0 ? "right" : "left", DEGREES: Math.abs(a.degrees) }),
    args: (b) => ({ degrees: b.getFieldValue("DIR") === "right" ? -b.getFieldValue("DEGREES") : b.getFieldValue("DEGREES") }),
  },
  start_feeding: {
    def: () => ({ message0: t("start feeding %1"), args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: t("The station starts putting pieces onto the field.") }),
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  stop_feeding: {
    def: () => ({ message0: t("stop feeding %1"), args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: t("The station stops putting pieces onto the field.") }),
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  feed_one: {
    def: () => ({ message0: t("feed one from %1"), args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: t("The station puts one piece onto the field, and the test waits for it to land.") }),
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  stop: {
    def: () => ({ message0: t("stop %1"), args0: [dd("MOTOR", OPTIONS.stopMotor)], style: STYLES.move, ...stmt,
      tooltip: t("Stop a motor (or all of them).") }),
    keys: ["motor"],
    fields: (a) => ({ MOTOR: a.motor }),
    args: (b) => ({ motor: b.getFieldValue("MOTOR") }),
  },
  aim: {
    def: () => ({ message0: t("aim %1 at %2"), args0: [dd("TURRET", OPTIONS.turret), dd("GOAL", OPTIONS.goal)],
      style: STYLES.move, ...stmt,
      tooltip: t("Keep the turret pointed at the goal's center, even while the robot drives and turns, until a later block spins, moves or stops it.") }),
    keys: ["turret", "at"],
    fields: (a) => ({ TURRET: a.turret, GOAL: a.at }),
    args: (b) => ({ turret: b.getFieldValue("TURRET"), at: b.getFieldValue("GOAL") }),
  },
  set_cylinder: {
    def: () => ({ message0: t("set %1 to %2"), args0: [dd("CYLINDER", OPTIONS.cylinder), dd("TO", CYLINDER_TO())],
      style: STYLES.move, ...stmt,
      tooltip: t("Switch the cylinder's valve to push the rod out (extend) or pull it in (retract). Off lets a single solenoid's spring retract it, and leaves a double solenoid's cylinder where it is.") }),
    keys: ["cylinder", "to"],
    fields: (a) => ({ CYLINDER: a.cylinder, TO: a.to }),
    args: (b) => ({ cylinder: b.getFieldValue("CYLINDER"), to: b.getFieldValue("TO") }),
  },
  wait: {
    def: () => ({ message0: t("wait %1 s"), args0: [num("SECONDS", 1, { min: 0 })], style: STYLES.wait, ...stmt,
      tooltip: t("Wait this many seconds.") }),
    keys: ["seconds"],
    fields: (a) => ({ SECONDS: a.seconds }),
    args: (b) => ({ seconds: b.getFieldValue("SECONDS") }),
  },
  wait_until: {
    def: () => ({ message0: t("wait until %1"), args0: [{ type: "input_value", name: "COND", check: COND }], style: STYLES.wait,
      ...stmt, tooltip: t("Wait until this is true.") }),
    keys: ["until"], cond: ["COND", "until"],
  },
  repeat: {
    def: () => ({ message0: t("repeat %1 times"), args0: [num("TIMES", 4, { min: 1, max: 100, precision: 1 })],
      message1: "%1", args1: [{ type: "input_statement", name: "DO" }], style: STYLES.repeat, ...stmt,
      tooltip: t("Do the blocks inside this many times.") }),
    keys: ["times"],
    fields: (a) => ({ TIMES: a.times }),
    args: (b) => ({ times: b.getFieldValue("TIMES") }),
    body: true,
  },
  repeat_until: {
    def: () => ({ message0: t("repeat until %1"), args0: [{ type: "input_value", name: "COND", check: COND }],
      message1: "%1", args1: [{ type: "input_statement", name: "DO" }], style: STYLES.repeat, ...stmt,
      tooltip: t("Do the blocks inside again and again until this is true.") }),
    keys: ["until"], cond: ["COND", "until"], body: true,
  },
  expect: {
    def: () => ({ message0: t("expect %1 %2 the whole time"),
      args0: [{ type: "input_value", name: "COND", check: COND }, { type: "field_checkbox", name: "THROUGHOUT", checked: false }],
      inputsInline: true, style: STYLES.check, ...stmt,
      tooltip: t("Check this is true here (or, ticked, from here to the end of the run).") }),
    keys: ["that"], cond: ["COND", "that"],
    fields: (a) => ({ THROUGHOUT: a.throughout === true }),
    // ticked: throughout = true; not ticked: a file's throughout = false stays (from block.data), else nothing
    args: (b) => (b.getFieldValue("THROUGHOUT") === "TRUE" ? { throughout: true } : {}),
    extra: (a) => (a.throughout === false ? { throughout: false } : {}),
  },
};

// Condition blocks: value-shaped, they plug into wait until, repeat until and expect.
const CONDS = {
  touch: {
    def: () => ({ message0: t("%1 touches %2"), args0: [dd("ROBOT", OPTIONS.robot), dd("FIELD", OPTIONS.field)],
      tooltip: t("A part of the robot touches something on the field.") }),
    fields: (a) => ({ ROBOT: a.robot, FIELD: a.field }),
    json: (b) => ({ touch: { robot: b.getFieldValue("ROBOT"), field: b.getFieldValue("FIELD") } }),
  },
  tilt: {
    def: () => ({ message0: t("tilt %1 %2 °"), args0: [dd("SIDE", SIDES()), num("ANGLE", 20)],
      tooltip: t("How far the robot leans from upright, in degrees.") }),
    fields: (a) => ("above" in a ? { SIDE: "above", ANGLE: a.above } : { SIDE: "below", ANGLE: a.below }),
    json: (b) => ({ tilt: { [b.getFieldValue("SIDE")]: b.getFieldValue("ANGLE") } }),
  },
  joint: {
    def: () => ({ message0: "%1 %2 %3 %4", args0: [dd("NAME", OPTIONS.joint), dd("SIDE", SIDES()), num("VALUE", 40), dd("UNIT", UNITS)],
      tooltip: t("Where a joint is.") }),
    fields: (a) => ({ NAME: a.name, ...("above" in a ? { SIDE: "above", VALUE: a.above } : { SIDE: "below", VALUE: a.below }),
      UNIT: a.unit === "deg" ? "deg" : LEN }),
    json: (b) => { const v = stored(b, "VALUE"); return { joint: { name: b.getFieldValue("NAME"), [b.getFieldValue("SIDE")]: v.value, unit: v.unit } }; },
    init(b) { b.getField("NAME").setValidator(fitUnit("UNIT")); },
  },
  piece_in: {
    def: () => ({ message0: t("%1 in %2"), args0: [dd("PIECE", OPTIONS.piece), dd("BODY", OPTIONS.body)],
      tooltip: t("A game piece is inside a body of the robot.") }),
    fields: (a) => ({ PIECE: a.piece, BODY: a.body }),
    json: (b) => ({ piece_in: { body: b.getFieldValue("BODY"), piece: b.getFieldValue("PIECE") } }),
  },
  held: {
    def: () => ({ message0: t("%1 is held by %2"), args0: [dd("PIECE", OPTIONS.piece), dd("BY", OPTIONS.heldBy)],
      tooltip: t("A game piece is held by a part or body of the robot: off the ground, touching it and moving with it.") }),
    fields: (a) => ({ PIECE: a.piece, BY: a.by }),
    json: (b) => ({ held: { piece: b.getFieldValue("PIECE"), by: b.getFieldValue("BY") } }),
  },
  rests_on: {
    def: () => ({ message0: t("%1 rests on %2"), args0: [dd("PIECE", OPTIONS.piece), dd("ON", OPTIONS.restsOn)],
      tooltip: t("A game piece rests on a part of the field (or on another piece), which carries its weight.") }),
    fields: (a) => ({ PIECE: a.piece, ON: a.on }),
    json: (b) => ({ rests_on: { piece: b.getFieldValue("PIECE"), on: b.getFieldValue("ON") } }),
  },
  seated: {
    def: () => ({ message0: t("%1 is seated at least %2 %3 on %4"),
      args0: [dd("PIECE", OPTIONS.piece), num("DEPTH", 3, { min: 0 }), label("DEPTH_UNIT"), dd("ON", OPTIONS.rod)],
      tooltip: t("A game piece with a hole is on a rod, its hole passed over the rod's tip by at least this much.") }),
    fields: (a) => ({ PIECE: a.piece, DEPTH: a.at_least_in, ON: a.on }),
    json: (b) => ({ seated: { piece: b.getFieldValue("PIECE"), on: b.getFieldValue("ON"),
      at_least_in: stored(b, "DEPTH").value } }),
  },
  hanging_from: {
    def: () => ({ message0: t("hanging from %1"), args0: [dd("BAR", OPTIONS.bar)],
      tooltip: t("The robot's hooks hold it up from this bar.") }),
    fields: (v) => ({ BAR: v }),
    json: (b) => ({ hanging_from: b.getFieldValue("BAR") }),
  },
  level: {
    def: () => ({ message0: t("holding level %1"), args0: [dd("LEVEL", OPTIONS.level)],
      tooltip: t("The robot is holding this climb level.") }),
    fields: (v) => ({ LEVEL: v }),
    json: (b) => ({ level: b.getFieldValue("LEVEL") }),
  },
  in_hopper: {
    def: () => ({ message0: t("%1 %2 pieces in the hopper"),
      args0: [num("COUNT", 5, { min: 0, max: 1000, precision: 1 }), dd("SIDE", HOPPER_SIDES())],
      tooltip: t("How many pieces the hopper holds (pick the hopper in the test's setup).") }),
    fields: (a) => ("at_most" in a ? { COUNT: a.at_most, SIDE: "at_most" } : { COUNT: a.at_least, SIDE: "at_least" }),
    json: (b) => ({ in_hopper: { [b.getFieldValue("SIDE")]: b.getFieldValue("COUNT") } }),
  },
  in_zone: {
    def: () => ({ message0: t("robot is %1 zone %2"), args0: [dd("IS", ZONE_IS), dd("ZONE", OPTIONS.zone)],
      tooltip: t("The robot's center, seen from above, is in (or out of) one of the field's zones: an area marked on the carpet, such as a starting zone or a wing.") }),
    fields: (a) => ({ IS: a.is || "in", ZONE: a.zone }),
    json: (b) => ({ in_zone: { zone: b.getFieldValue("ZONE"), is: b.getFieldValue("IS") } }),
  },
  match: {
    def: () => ({ message0: t("match is in %1"), args0: [dd("PHASE", MATCH_PHASES)],
      tooltip: t("The match is in this phase now: auto, the pause (the robot is disabled), or teleop. Only in a test with the match box ticked in Setup.") }),
    fields: (a) => ({ PHASE: a.phase }),
    json: (b) => ({ match: { phase: b.getFieldValue("PHASE") } }),
  },
  points: {
    def: () => ({ message0: t("points: %1 at least %2"),
      args0: [dd("PHASE", POINTS_PHASES), num("POINTS", 1, { min: 0 })],
      tooltip: t("The points scored so far, in one phase or in total, are at least this many. Only in a test with the match box ticked in Setup.") }),
    fields: (a) => ({ PHASE: a.phase || "total", POINTS: a.at_least }),
    json: (b) => ({ points: { phase: b.getFieldValue("PHASE"), at_least: b.getFieldValue("POINTS") } }),
  },
  scored: {
    def: () => ({ message0: t("%1 scored in %2"), args0: [num("COUNT", 1, { min: 1, max: 1000, precision: 1 }), dd("GOAL", OPTIONS.scoredGoal)],
      tooltip: t("At least this many balls have gone into the goal (or into the goals together, for any goal).") }),
    fields: (a) => ({ COUNT: a.at_least, GOAL: a.goal }),
    json: (b) => ({ scored: { goal: b.getFieldValue("GOAL"), at_least: b.getFieldValue("COUNT") } }),
  },
  aimed: {
    def: () => ({ message0: t("%1 aimed at %2 within %3 °"),
      args0: [dd("TURRET", OPTIONS.turret), dd("GOAL", OPTIONS.goal), num("WITHIN", 2, { min: 0.1 })],
      tooltip: t("The turret points at the goal's center, off by no more than this many degrees.") }),
    fields: (a) => ({ TURRET: a.turret, GOAL: a.at, WITHIN: a.within_deg }),
    json: (b) => ({ aimed: { turret: b.getFieldValue("TURRET"), at: b.getFieldValue("GOAL"),
      within_deg: b.getFieldValue("WITHIN") } }),
  },
  cylinder: {
    def: () => ({ message0: t("%1 is %2"), args0: [dd("CYLINDER", OPTIONS.cylinder), dd("IS", CYLINDER_IS())],
      tooltip: t("The cylinder's rod is all the way out (extended) or all the way in (retracted).") }),
    fields: (a) => ({ CYLINDER: a.cylinder, IS: a.is }),
    json: (b) => ({ cylinder: { cylinder: b.getFieldValue("CYLINDER"), is: b.getFieldValue("IS") } }),
  },
  pressure: {
    def: () => ({ message0: t("%1 air pressure %2 %3 %4"),
      args0: [dd("AIR", AIR_SIDES()), dd("SIDE", SIDES()), num("PSI", 60, { min: 0 }), label("PSI_UNIT")],
      tooltip: t("The air pressure in the tanks (stored), or after the regulator, which the cylinders get (working).") }),
    fields: (a) => ({ AIR: a.side || "stored", ...("above" in a ? { SIDE: "above", PSI: a.above } : { SIDE: "below", PSI: a.below }) }),
    json: (b) => ({ pressure: { side: b.getFieldValue("AIR"), [b.getFieldValue("SIDE")]: stored(b, "PSI").value } }),
  },
  element: {
    def: () => ({ message0: t("%1 is %2 %3 %4"),
      args0: [dd("ELEMENT", OPTIONS.moving), dd("SIDE", SIDES()), num("VALUE", 10), { type: "field_label", name: "UNIT", text: "" }],
      tooltip: t("Where a moving part of the field is: how far a hinged one has swung (degrees) or a sliding one has moved, from where it rests.") }),
    fields: (a) => ({ ELEMENT: a.element, ...("above" in a ? { SIDE: "above", VALUE: a.above } : { SIDE: "below", VALUE: a.below }) }),
    json: (b) => ({ element: { element: b.getFieldValue("ELEMENT"), [b.getFieldValue("SIDE")]: stored(b, "VALUE").value } }),
    init(b) {
      const f = b.getField("ELEMENT");
      f.setValidator(elementUnit);
      elementUnit.call(f, f.getValue());
    },
  },
  clears: {
    def: () => ({ message0: t("robot clears %1 by at least %2 %3"),
      args0: [dd("ELEMENT", OPTIONS.cleared), num("CLEAR", 2), label("CLEAR_UNIT")] }),
    fields: (a) => ({ ELEMENT: a.element, CLEAR: a.at_least_in }),
    json: (b) => ({ clears: { element: b.getFieldValue("ELEMENT"), at_least_in: stored(b, "CLEAR").value } }),
    init(b) {
      b.setTooltip(() => t("Nothing on the robot comes closer to this part of the field than this (measured out to about {far}).",
        { far: units.fmt(20 * FILE_SI.in, "length") }));
    },
  },
  partner_lifted: {
    def: () => ({ message0: t("partner is lifted at least %1 %2 %3 level within %4 %5"),
      args0: [num("LIFT", 2), label("LIFT_UNIT"), { type: "field_checkbox", name: "LEVEL", checked: false },
        num("WITHIN", 5, { min: 0.1 }), { type: "field_label", name: "DEG", text: "°" }],
      tooltip: t("The partner robot's lowest corner is at least this high above where it rested. Tick level within to also want it tilted no more than that many degrees.") }),
    fields: (a) => ({ LIFT: a.at_least_in, LEVEL: a.level_within_deg != null,
      ...(a.level_within_deg != null ? { WITHIN: a.level_within_deg } : {}) }),
    json: (b) => ({ partner_lifted: { at_least_in: stored(b, "LIFT").value,
      ...(b.getFieldValue("LEVEL") === "TRUE" ? { level_within_deg: b.getFieldValue("WITHIN") } : {}) } }),
    init(b) {
      const f = b.getField("LEVEL");
      f.setValidator(function (v) { showLevel(this.getSourceBlock(), v === true || v === "TRUE"); return v; });
      showLevel(b, f.getValueBoolean());
    },
  },
  partner_moved: {
    def: () => ({ message0: t("partner moved at least %1 %2"), args0: [num("MOVED", 6), label("MOVED_UNIT")],
      tooltip: t("The partner robot's center has gone at least this far across the floor from where it rested.") }),
    fields: (a) => ({ MOVED: a.at_least_in }),
    json: (b) => ({ partner_moved: { at_least_in: stored(b, "MOVED").value } }),
  },
  sensor: {
    def: () => ({ message0: t("sensor %1 %2 %3 %4"),
      args0: [dd("SENSOR", OPTIONS.sensor), dd("IS", SENSOR_IS), dd("SEEING", SENSOR_SEEING), dd("SEES", OPTIONS.sees)],
      tooltip: t("One of the robot's sensors: a beam break is on while something breaks its beam, a distance sensor while something is closer than its threshold. A color sensor is seeing a piece within its range (of this color, when you pick one).") }),
    fields: (a) => ({ SENSOR: a.sensor, IS: a.is, SEEING: a.is, SEES: a.sees || "any" }),
    json: (b) => ({ sensor: { sensor: b.getFieldValue("SENSOR"), is: b.getFieldValue("IS"),
      ...(b.getFieldValue("SEES") === "any" ? {} : { sees: b.getFieldValue("SEES") }) } }),
    init(b) {
      const watch = (field, show) => b.getField(field).setValidator(function (v) {
        const block = this.getSourceBlock();
        if (block) show(block, v);
        return v;
      });
      watch("SENSOR", (block, v) => showSees(block, v, block.getFieldValue("SEES")));
      watch("SEES", (block, v) => showSees(block, block.getFieldValue("SENSOR"), v));
      // on or off, worded either way: the two pickers stay equal (`syncing`: the other one is being set to match,
      // as a validator sees the value before its field has it)
      let syncing = false;
      const match = (other) => (block, v) => {
        if (syncing || block.getFieldValue(other) === v) return;
        syncing = true;
        try { block.setFieldValue(v, other); } finally { syncing = false; }
      };
      watch("IS", match("SEEING"));
      watch("SEEING", match("IS"));
      showSees(b, b.getFieldValue("SENSOR"), b.getFieldValue("SEES"));
    },
  },
  sees_tag: {
    def: () => ({ message0: t("camera %1 sees %2"), args0: [dd("CAMERA", OPTIONS.camera), dd("TAG", OPTIONS.tag)],
      tooltip: t("One of the robot's cameras sees this AprilTag (or any tag): it is in the camera's view, close enough, not too edge-on, and nothing is in the way.") }),
    fields: (a) => ({ CAMERA: a.camera, TAG: a.tag == null ? "any" : String(a.tag) }),
    json: (b) => ({ sees_tag: { camera: b.getFieldValue("CAMERA"),
      ...(b.getFieldValue("TAG") === "any" ? {} : { tag: Number(b.getFieldValue("TAG")) }) } }),
  },
  station_empty: {
    def: () => ({ message0: t("%1 is empty"), args0: [dd("STATION", OPTIONS.station)],
      tooltip: t("The station has no pieces left to feed.") }),
    fields: (v) => ({ STATION: v }),
    json: (b) => ({ station_empty: b.getFieldValue("STATION") }),
  },
  made_it_in: {
    def: () => ({ message0: t("at least %1% made it in"), args0: [num("PERCENT", 80, { min: 0, max: 100 })],
      tooltip: t("Of the pieces fed to the robot, at least this share made it into the hopper. In an expect that is the test's last block, it waits (up to 3 s) for the pieces to settle first; anywhere else it counts them as they are.") }),
    fields: (a) => ({ PERCENT: a.at_least }),
    json: (b) => ({ made_it_in: { at_least: b.getFieldValue("PERCENT") } }),
  },
};
const condType = (kind) => `cond_${kind}`;

let defined = false;
export function defineBlocks(namesFn) {
  names = namesFn;
  if (defined) return;
  defined = true;
  register();
}

// The block types in the current language: their messages, tooltips and fixed pickers' words (each def() is made
// again), replacing the ones defined before.
function register() {
  const B = window.Blockly;
  const defs = [];
  for (const [type, k] of Object.entries(KINDS)) defs.push({ type, ...k.def() });
  for (const [kind, c] of Object.entries(CONDS)) {
    defs.push({ type: condType(kind), output: COND, style: STYLES.check, ...c.def() });
  }
  for (const d of defs) delete B.Blocks[d.type];      // replaced, not warned about
  B.defineBlocksWithJsonArray(defs);
  // the joint pickers' validators (JSON can't carry a function)
  // and the unit labels and limits of the blocks with a length or pressure (before a block's numbers are set)
  for (const [type, k] of [...Object.entries(KINDS), ...Object.entries(CONDS).map(([n, c]) => [condType(n), c])]) {
    if (!k.init && !MEASURES[type]) continue;
    const base = B.Blocks[type].init;
    B.Blocks[type].init = function () {
      base.call(this);
      if (MEASURES[type]) unitLabels(this);
      if (k.init) k.init(this);
    };
  }
}

// A Language change (spec L7): an open field editor is committed (as typed), the block types are defined again in the
// new words, and the workspace is made again from its own saved state. The blocks keep their ids, values and
// block.data (DISPLAY too, so the file is written as it was); the view keeps its scroll, but not the selection.
export function relabel(ws) {
  const B = window.Blockly;
  ws.hideChaff();
  register();
  const { scrollX, scrollY } = ws;
  B.Events.disable();
  loading++;
  try {
    B.serialization.workspaces.load(B.serialization.workspaces.save(ws), ws);
  } finally {
    loading--;
    B.Events.enable();
  }
  ws.scroll(scrollX, scrollY);
}

// -- the palette ------------------------------------------------------------------------------------------------------
export function toolbox(p) {
  p = p || {};
  const motors = has(p.motors), bars = has(p.bars);
  const touchOk = has([...(p.hooks || []), ...(p.parts || []), ...(p.bodies || [])])
    && has([...(p.bars || []), ...(p.elements || []), ...(p.pieces || []), ...(p.targets || [])]);
  const cond = (kind, fields = {}) => ({ block: { type: condType(kind), fields } });
  const item = (type, extra = {}) => {
    const fields = systemDefaults(type);
    return { kind: "block", type, ...(Object.keys(fields).length ? { fields } : {}), ...extra };
  };
  const waitCond = touchOk ? cond("touch") : cond("tilt", { SIDE: "below", ANGLE: 10 });
  const repeatCond = bars && has(p.hooks) ? cond("hanging_from") : cond("tilt", { SIDE: "above", ANGLE: 45 });
  const out = [{ kind: "label", text: t("Move", { context: "blocks" }) }];
  if (motors) out.push(item("spin"), item("move"));
  if (p.drive) out.push(item("drive"), item("turn"));
  const aimOk = has(p.turrets) && has(p.goals), cylinders = has(p.cylinders);
  if (aimOk) out.push(item("aim"));
  if (cylinders) out.push(item("set_cylinder"));
  out.push(item("stop"));
  if (has(p.stations)) out.push({ kind: "label", text: t("Station", { context: "blocks" }) }, item("start_feeding"), item("stop_feeding"), item("feed_one"));
  out.push({ kind: "label", text: t("Wait", { context: "blocks" }) }, item("wait"), item("wait_until", { inputs: { COND: waitCond } }));
  out.push({ kind: "label", text: t("Repeat", { context: "blocks" }) }, item("repeat"), item("repeat_until", { inputs: { COND: repeatCond } }));
  out.push({ kind: "label", text: t("Check", { context: "blocks" }) }, item("expect", { inputs: { COND: cond("tilt", { SIDE: "below", ANGLE: 20 }) } }));
  if (touchOk) out.push(item(condType("touch")));
  out.push(item(condType("tilt")));
  if (has(p.joints)) out.push(item(condType("joint")));
  if (has(p.pieces) && has(p.bodies)) out.push(item(condType("piece_in")));
  if (has(p.bodies) || has(p.parts)) out.push(item(condType("held")));
  if (has(p.elements) || (p.pieces || []).length > 1) out.push(item(condType("rests_on")));
  if (has(p.rods)) out.push(item(condType("seated")));
  if (bars) out.push(item(condType("hanging_from")));
  if (has(p.levels)) out.push(item(condType("level")));
  if (has(p.stations)) out.push(item(condType("station_empty")));
  if (has(p.goals)) out.push(item(condType("scored")));
  if (has(p.zones)) out.push(item(condType("in_zone")));
  if (p.game) out.push(item(condType("match")), item(condType("points")));
  if (aimOk) out.push(item(condType("aimed")));
  if (cylinders) out.push(item(condType("cylinder")), item(condType("pressure")));
  if (has(p.moving_elements)) out.push(item(condType("element")));
  if (has(p.elements)) out.push(item(condType("clears")));
  if (p.partner) out.push(item(condType("partner_lifted")), item(condType("partner_moved")));
  if (has(p.sensors)) out.push(item(condType("sensor")));
  if ((p.sensors || []).some((s) => s.kind === "camera") && has(p.tags)) out.push(item(condType("sees_tag")));
  out.push(item(condType("in_hopper")), item(condType("made_it_in")));
  return { kind: "flyoutToolbox", contents: out };
}

// -- test JSON -> workspace -------------------------------------------------------------------------------------------
const header = new WeakMap();   // workspace -> the test's robot, hooks, time_limit, chart and setup

function remember(kind, v) { if (typeof v === "string" && v) used[kind].add(v); }

function rememberNames(blocks) {
  for (const b of blocks || []) {
    if (b.do === "spin") remember("motor", b.motor);
    if (b.do === "stop" && b.motor !== "all") remember("motor", b.motor);
    if (b.do === "move") remember("joint", b.joint);
    if (["start_feeding", "stop_feeding", "feed_one"].includes(b.do)) remember("station", b.station);
    if (b.do === "drive" && (b.direction === "left" || b.direction === "right")) used.sideways = true;
    if (b.do === "aim") { remember("turret", b.turret); remember("goal", b.at); }
    if (b.do === "set_cylinder") remember("cylinder", b.cylinder);
    const c = b.until || b.that;
    if (c && typeof c === "object") {
      const [kind, a] = Object.entries(c)[0] || [];
      if (kind === "joint") remember("joint", a.name);
      if (kind === "touch") { remember("robot", a.robot); remember("field", a.field); }
      if (kind === "piece_in") { remember("body", a.body); remember("piece", a.piece); }
      if (kind === "held") { remember("piece", a.piece); remember("robot", a.by); }
      if (kind === "rests_on") { remember("piece", a.piece); remember("element", a.on); }
      if (kind === "seated") { remember("piece", a.piece); remember("rod", a.on); }
      if (kind === "hanging_from") remember("bar", a);
      if (kind === "level") remember("level", a);
      if (kind === "station_empty") remember("station", a);
      if (kind === "scored" && a.goal !== "any") remember("goal", a.goal);
      if (kind === "aimed") { remember("turret", a.turret); remember("goal", a.at); }
      if (kind === "cylinder") remember("cylinder", a.cylinder);
      if (kind === "element") remember("moving", a.element);
      if (kind === "clears") remember("cleared", a.element);
      if (kind === "in_zone") remember("zone", a.zone);
      if (kind === "sensor") { remember("sensor", a.sensor); remember("color", a.sees); }
      if (kind === "sees_tag") { remember("camera", a.camera); if (a.tag != null) remember("tag", String(a.tag)); }
    }
    rememberNames(b.body);
  }
}

function condState(c) {
  if (!c || typeof c !== "object") return null;
  const [kind, a] = Object.entries(c)[0] || [];
  const spec = CONDS[kind];
  return spec ? showState({ type: condType(kind), fields: spec.fields(a) }, a) : null;
}

function blockState(b) {
  const type = b.do === "repeat" && "until" in b ? "repeat_until" : b.do;
  const k = KINDS[type];
  const known = new Set(["do", "body", ...k.keys, ...(type === "expect" ? ["throughout"] : [])]);
  const extra = Object.fromEntries(Object.entries(b).filter(([key]) => !known.has(key)));
  Object.assign(extra, k.extra ? k.extra(b) : {});
  const st = { type, fields: k.fields ? k.fields(b) : {} };
  if (Object.keys(extra).length) st.data = JSON.stringify(extra);
  const inputs = {};
  if (k.cond) { const c = condState(b[k.cond[1]]); if (c) inputs[k.cond[0]] = { block: c }; }
  if (k.body && (b.body || []).length) inputs.DO = { block: chain(b.body) };
  if (Object.keys(inputs).length) st.inputs = inputs;
  return showState(st, b);
}

function chain(blocks) {
  let first = null, last = null;
  for (const b of blocks) {
    const st = blockState(b);
    if (last) last.next = { block: st }; else first = st;
    last = st;
  }
  return first;
}

export function setUsed(tests) {
  resetUsed();
  for (const test of tests) rememberNames((test && test.blocks) || []);
}

export function testToWorkspace(test, ws) {
  const B = window.Blockly;
  const { blocks = [], ...head } = test || {};
  header.set(ws, head);
  rememberNames(blocks);   // added to the current set until the next setUsed
  loading++;
  try {
    const top = chain(blocks);
    if (top) { top.x = 24; top.y = 24; }
    B.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: top ? [top] : [] } }, ws);
    const loaded = ws.getTopBlocks(false)[0];
    if (loaded) firstBlock.set(ws, loaded.id); else firstBlock.delete(ws);
  } finally {
    loading--;
  }
  ws.clearUndo();
}

// Blocks (the test's JSON shape) joined below the test's last block, or the test when there is none (Undo takes them
// off again).
export function appendBlocks(ws, blocks) {
  const B = window.Blockly;
  const top = chain(blocks);
  if (!top) return;
  rememberNames(blocks);
  const main = mainStack(ws);
  let last = main;
  while (last && last.getNextBlock()) last = last.getNextBlock();
  B.Events.setGroup(true);
  try {
    const made = B.serialization.blocks.append(top, ws);
    if (last && last.nextConnection && made.previousConnection) last.nextConnection.connect(made.previousConnection);
    else if (!main) { made.moveBy(24, 24); firstBlock.set(ws, made.id); }
  } finally {
    B.Events.setGroup(false);
  }
}

// -- workspace -> test JSON -------------------------------------------------------------------------------------------
const stackSize = (b) => b.getDescendants(false).length;

const firstBlock = new WeakMap();   // workspace -> id of the test's first block

// The stack that is the test: the one holding the test's first block; when that block is gone, the one with the most
// blocks (the topmost of equals); null when there are none. Its top block becomes the test's first block.
export function mainStack(ws) {
  const tops = ws.getTopBlocks(true).filter((b) => !b.outputConnection);
  const first = ws.getBlockById(firstBlock.get(ws) || "");
  let best = first ? first.getRootBlock() : null;
  if (!best || !tops.includes(best)) {
    best = null;
    for (const s of tops) if (!best || stackSize(s) > stackSize(best)) best = s;
  }
  if (best) firstBlock.set(ws, best.id);
  return best;
}

// The stacks and checks lying loose beside the test (they aren't in the file).
export function looseStacks(ws) {
  const top = mainStack(ws);
  return ws.getTopBlocks(true).filter((b) => b !== top);
}

function stackBlocks(first) {
  const out = [];
  for (let b = first; b; b = b.getNextBlock()) out.push(b);
  return out;
}

function condJson(block) {
  if (!block) return null;
  const kind = block.type.replace(/^cond_/, "");
  return CONDS[kind] ? CONDS[kind].json(block) : null;
}

function blockJson(b) {
  const k = KINDS[b.type];
  let extra = {};
  try { extra = b.data ? JSON.parse(b.data) : {}; } catch { extra = {}; }
  delete extra[DISPLAY];
  for (const key of k.drop ? k.drop(b) : []) delete extra[key];
  const out = { do: b.type === "repeat_until" ? "repeat" : b.type, ...extra, ...(k.args ? k.args(b) : {}) };
  if (k.cond) out[k.cond[1]] = condJson(b.getInputTargetBlock(k.cond[0]));
  if (k.body) out.body = stackBlocks(b.getInputTargetBlock("DO")).map(blockJson);
  return out;
}

// -- a block's settings (Block settings…) ---------------------------------------------------------------------------
// The keys a block takes beside its boxes (scrimmage/blocks/program.py): a timeout, a drive's or turn's volts, a
// repeat until's max. Each: {key, label, unit, def (the run's default when the key is left out), hint, whole, min,
// max}. The defaults and limits come from the server (the Test tab state's block_defaults: program.py's constants).
// They live in block.data like any key with no box, so the file keeps them; nothing to convert (s, V, rounds). A
// drive's or turn's volts are a size (the run takes their absolute value; a drive's direction says which way): 0 up.
const TIMEOUT = (def) => ({ key: "timeout", label: () => t("Give up after"), unit: "s", def, min: 0,
  hint: () => t("If it isn't done by then, the run ends there, naming this block. Empty: {seconds} s.", { seconds: def }) });
const VOLTS = (d, label, hint) => ({ key: "volts", label, unit: "V", def: d.volts, min: 0, max: d.max_volts, hint });
const SETTINGS = {
  move: (b, d) => [TIMEOUT(d.timeout.move)],
  wait_until: (b, d) => [TIMEOUT(d.timeout.wait_until)],
  feed_one: (b, d) => [TIMEOUT(d.timeout.feed_one)],
  drive: (b, d) => [VOLTS(d, () => t("Drive at"), () => t("The wheels' volts (0 to {most}). Empty: {volts} V.", { most: d.max_volts, volts: d.volts })),
    ...(b.getFieldValue("WHAT") === "seconds" ? [] : [TIMEOUT(d.timeout.drive)])],      // a timeout only goes with a distance
  turn: (b, d) => [VOLTS(d, () => t("Turn at up to"), () => t("The most volts the turn uses (0 to {most}). Empty: {volts} V.", { most: d.max_volts, volts: d.volts })),
    TIMEOUT(d.timeout.turn)],
  repeat_until: (b, d) => [{ key: "max", label: () => t("Give up after"), unit: t("rounds"), def: d.repeat_max, whole: true, min: 1,
    max: d.max_times, hint: () => t("A repeat until still going after this many rounds ends the run (1 to {most}). Empty: {count}.", { most: d.max_times, count: d.repeat_max }) }],
};

// [{key, label, unit, def, hint, whole, min, max, value}] for block b (value: the file's, or null for the default),
// with `defaults` the state's block_defaults; [] for a block with no such settings (or no defaults yet).
export function blockSettings(b, defaults) {
  const make = b && defaults && SETTINGS[b.type];
  if (!make) return [];
  const data = dataOf(b);
  return make(b, defaults).map((s) => ({ ...s, label: s.label(), hint: s.hint(),
    value: typeof data[s.key] === "number" ? data[s.key] : null }));
}

// Writes `values` ({key: number, or null for the default}) into block b's data, keeping its other keys.
export function setBlockSettings(b, values) {
  const data = dataOf(b);
  for (const [k, v] of Object.entries(values)) {
    if (v === null || v === undefined) delete data[k]; else data[k] = v;
  }
  b.data = Object.keys(data).length ? JSON.stringify(data) : null;
}

export const headOf = (ws) => header.get(ws) || {};
export function setHead(ws, patch) { header.set(ws, { ...headOf(ws), ...patch }); }

export function sweepNumber(b) {
  if (!b || b.isInFlyout) return null;
  if (b.type === "spin") return { key: "volts", field: "VOLTS", unit: "V" };
  // a length: in the file's unit (the saved test's: the sweep runs it), per unit shown
  const scale = (field, kind) => +(1 / factor(kind, units.system) / FILE_SI[stored(b, field).unit]).toPrecision(15);
  if (b.type === "move") {
    return b.getFieldValue("UNIT") === "deg" ? { key: "to", field: "TO", unit: "°" }
      : { key: "to", field: "TO", unit: units.name("length"), scale: scale("TO", "length") };
  }
  if (b.type === "wait") return { key: "seconds", field: "SECONDS", unit: "s" };
  if (b.type === "drive") {
    return b.getFieldValue("WHAT") === "seconds" ? { key: "seconds", field: "AMOUNT", unit: "s" }
      : { key: "distance", field: "AMOUNT", unit: units.name("field"), scale: scale("AMOUNT", "field") };
  }
  return null;
}

export function workspaceToTest(ws) {
  const top = mainStack(ws);
  return { ...(header.get(ws) || {}), blocks: top ? stackBlocks(top).map(blockJson) : [] };
}

// block id -> place ([i, j, ...]) for every statement block of the test (0-based, through repeat bodies)
export function placeOf(ws) {
  const out = new Map();
  const walk = (first, trail) => stackBlocks(first).forEach((b, i) => {
    out.set(b.id, [...trail, i]);
    if (KINDS[b.type] && KINDS[b.type].body) walk(b.getInputTargetBlock("DO"), [...trail, i]);
  });
  const top = mainStack(ws);
  if (top) walk(top, []);
  return out;
}

// what an empty condition socket says, by block type (the block's own first words)
const PLUG = { wait_until: () => t("Plug a check into \"wait until\" (from Check)."),
  repeat_until: () => t("Plug a check into \"repeat until\" (from Check)."),
  expect: () => t("Plug a check into \"expect\" (from Check).") };

// [{block, message, blocking}]: an empty condition socket (blocking: the test can't be saved like that) and blocks not
// joined to the test (not blocking: they are left out of the file).
export function incomplete(ws) {
  const out = [];
  const top = mainStack(ws);
  for (const loose of looseStacks(ws)) {
    out.push({ block: loose, blocking: false, message: loose.outputConnection
      ? t("This check isn't plugged into a block, so it does nothing: plug it into wait until, repeat until or expect.")
      : t("These blocks aren't joined to the test, so they don't run: drag them under the others, or delete them.") });
  }
  if (top) {
    for (const b of top.getDescendants(false)) {
      const k = KINDS[b.type];
      if (k && k.cond && !b.getInputTargetBlock(k.cond[0])) {
        out.push({ block: b, blocking: true, message: PLUG[b.type]() });
      }
    }
  }
  return out;
}
