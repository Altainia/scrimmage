// The Test tab's blocks: one Blockly block type per `do` kind and per condition kind, the palette (Blockly's
// toolbox, filled from the robot's names), and the test JSON (scrimmage/blocks/program.py to_json) to and from a
// Blockly workspace. Blockly itself (window.Blockly, vendor/blockly.min.js) is loaded by test.js before any of this
// runs.
//
//   defineBlocks(names)            registers the block types once; `names()` answers the current palette (or null)
//   toolbox(palette)               the flyout's contents: Move, Wait, Repeat, Check (a block only when every one of its
//                                  pickers has something to pick: aim needs a turret and a goal, scored a goal, the
//                                  cylinder blocks and the air pressure check a cylinder)
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
//   sweepNumber(block)             the block's number a sweep can try ({key, field, unit}: scrimmage/blocks/sweep.py's
//                                  block keys), or null
//
// A block keeps the settings it has no box for (a move's timeout, a repeat until's max, a drive's volts) in
// `block.data`, so the round trip test -> workspace -> test is exact.

export const STYLES = { move: "move_blocks", wait: "wait_blocks", repeat: "repeat_blocks", check: "check_blocks" };
const COND = "Condition";

// Names the tests use that the palette may not have (a joint renamed on Import again): a picker keeps offering them
// so the block keeps its value (and shows the problem) instead of quietly picking another name. Rebuilt by setUsed.
const KINDS_OF_NAME = { motor: "robot", joint: "robot", robot: "robot", body: "robot", turret: "robot", cylinder: "robot",
  field: "field", piece: "field", bar: "field", level: "field", station: "field", element: "field", rod: "field",
  goal: "field" };
let used = {};
const resetUsed = () => {
  used = Object.fromEntries(Object.keys(KINDS_OF_NAME).map((k) => [k, new Set()]));
  used.sideways = false;   // a test drives left or right (a tank can't, but the block keeps what the file says)
};
resetUsed();

let names = () => null;
let loading = 0;        // testToWorkspace is filling the workspace: pickers don't adjust each other

const SPECIAL = { hook: "any hook", "next bar": "next bar", "any bar": "any bar", any: "any piece", all: "all motors",
  "a piece": "a piece" };

// [[label, value], ...] for a picker: `special` first (when given: a value SPECIAL names, or [label, value]), the
// palette's names, then names only the test uses (marked as missing from the robot).
function choices(list, kind, special = [], label = (v) => v) {
  const out = [], seen = new Set();
  for (const sp of special) {
    const [text, v] = Array.isArray(sp) ? sp : [SPECIAL[sp] || sp, sp];
    out.push([text, v]);
    seen.add(v);
  }
  for (const v of list) if (!seen.has(v)) { out.push([label(v), v]); seen.add(v); }
  for (const v of used[kind]) if (!seen.has(v)) { out.push([`${label(v)} (not on the ${KINDS_OF_NAME[kind]})`, v]); seen.add(v); }
  return out.length ? out : [["(none)", "(none)"]];
}

const pal = () => names() || {};
const motorNames = () => (pal().motors || []).map((m) => m.name);
const jointNames = () => (pal().joints || []).map((j) => j.name);
const robotSide = () => [...(pal().hooks || []), ...(pal().parts || []), ...(pal().bodies || [])];
// bars, elements and pieces first, then the field's other surfaces (the carpet, the walls)
const fieldSide = () => [...(pal().bars || []), ...(pal().elements || []), ...(pal().pieces || []), ...(pal().targets || [])];
const has = (xs) => (xs || []).length > 0;
const cylinderNames = () => (pal().cylinders || []).map((c) => c.name);
const ANY_GOAL = [["any goal", "any"]];   // a scored check's goal: every goal together (palette.py ANY_GOAL)

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
  scoredGoal: () => choices(pal().goals || [], "goal", ANY_GOAL),
  cylinder: () => choices(cylinderNames(), "cylinder"),
  // left and right only on a swerve robot (or when the test already has one)
  heading: () => [["forward", "forward"], ["back", "back"],
    ...(pal().drive_kind === "swerve" || used.sideways ? [["left", "left"], ["right", "right"]] : [])],
};
const TURNS = [["left", "left"], ["right", "right"]];
const HOPPER_SIDES = [["or more", "at_least"], ["or fewer", "at_most"]];
const UNITS = [["°", "deg"], ["in", "in"], ["m", "m"]];
const SIDES = [["above", "above"], ["below", "below"]];
const CYLINDER_TO = [["extend", "extend"], ["retract", "retract"], ["off", "off"]];
const CYLINDER_IS = [["extended", "extended"], ["retracted", "retracted"]];
const AIR_SIDES = [["stored", "stored"], ["working", "working"]];

// A joint picker's validator: picking a joint that slides (or turns) switches a unit that doesn't fit it.
function fitUnit(unitField) {
  return function (value) {
    const block = this.getSourceBlock();
    if (loading || !block) return value;
    const j = (pal().joints || []).find((x) => x.name === value);
    const unit = block.getFieldValue(unitField);
    if (j && j.unit === "in" && unit === "deg") block.setFieldValue("in", unitField);
    if (j && j.unit === "deg" && unit !== "deg") block.setFieldValue("deg", unitField);
    return value;
  };
}

const dd = (name, options) => ({ type: "field_dropdown", name, options });
const num = (name, value, extra = {}) => ({ type: "field_number", name, value, ...extra });
const stmt = { previousStatement: null, nextStatement: null };

// Each block type: its Blockly JSON (without type), how its fields come from the block's JSON and go back.
const KINDS = {
  spin: {
    def: { message0: "spin %1 at %2 V", args0: [dd("MOTOR", OPTIONS.motor), num("VOLTS", 6, { min: -12, max: 12 })],
      style: STYLES.move, ...stmt, tooltip: "Run a motor at this many volts until a later block changes it." },
    keys: ["motor", "volts"],
    fields: (a) => ({ MOTOR: a.motor, VOLTS: a.volts }),
    args: (b) => ({ motor: b.getFieldValue("MOTOR"), volts: b.getFieldValue("VOLTS") }),
  },
  move: {
    def: { message0: "move %1 to %2 %3", args0: [dd("JOINT", OPTIONS.moveJoint), num("TO", 40), dd("UNIT", UNITS)],
      style: STYLES.move, ...stmt, tooltip: "Drive a joint to this position and wait until it gets there." },
    keys: ["joint", "to", "unit"],
    fields: (a) => ({ JOINT: a.joint, TO: a.to, UNIT: a.unit }),
    args: (b) => ({ joint: b.getFieldValue("JOINT"), to: b.getFieldValue("TO"), unit: b.getFieldValue("UNIT") }),
    init(b) { b.getField("JOINT").setValidator(fitUnit("UNIT")); },
  },
  drive: {
    def: { message0: "drive %1 %2 %3",
      args0: [dd("DIR", OPTIONS.heading), num("AMOUNT", 0.3), dd("WHAT", [["m", "distance"], ["s", "seconds"]])],
      style: STYLES.move, ...stmt, tooltip: "Drive the robot this far, or for this long. Forward with a minus distance drives back." },
    keys: ["distance", "seconds", "direction"],
    fields: (a) => ({ DIR: a.direction || "forward",
      ...("seconds" in a ? { AMOUNT: a.seconds, WHAT: "seconds" } : { AMOUNT: a.distance, WHAT: "distance" }) }),
    // forward is what no direction means: only the others are written (a file that says forward keeps it, from block.data)
    args: (b) => ({ [b.getFieldValue("WHAT")]: b.getFieldValue("AMOUNT"),
      ...(b.getFieldValue("DIR") === "forward" ? {} : { direction: b.getFieldValue("DIR") }) }),
    extra: (a) => (a.direction === "forward" ? { direction: "forward" } : {}),
    drop: (b) => (b.getFieldValue("WHAT") === "seconds" ? ["timeout"] : []),   // a timeout only goes with a distance
  },
  turn: {
    def: { message0: "turn %1 %2 °", args0: [dd("DIR", TURNS), num("DEGREES", 90, { min: 0, max: 720 })],
      style: STYLES.move, ...stmt, tooltip: "Turn the robot on the spot this many degrees, left or right." },
    keys: ["degrees"],
    fields: (a) => ({ DIR: a.degrees < 0 ? "right" : "left", DEGREES: Math.abs(a.degrees) }),
    args: (b) => ({ degrees: b.getFieldValue("DIR") === "right" ? -b.getFieldValue("DEGREES") : b.getFieldValue("DEGREES") }),
  },
  start_feeding: {
    def: { message0: "start feeding %1", args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: "The station starts putting pieces onto the field." },
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  stop_feeding: {
    def: { message0: "stop feeding %1", args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: "The station stops putting pieces onto the field." },
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  feed_one: {
    def: { message0: "feed one from %1", args0: [dd("STATION", OPTIONS.station)], style: STYLES.move, ...stmt,
      tooltip: "The station puts one piece onto the field, and the test waits for it to land." },
    keys: ["station"],
    fields: (a) => ({ STATION: a.station }),
    args: (b) => ({ station: b.getFieldValue("STATION") }),
  },
  stop: {
    def: { message0: "stop %1", args0: [dd("MOTOR", OPTIONS.stopMotor)], style: STYLES.move, ...stmt,
      tooltip: "Stop a motor (or all of them)." },
    keys: ["motor"],
    fields: (a) => ({ MOTOR: a.motor }),
    args: (b) => ({ motor: b.getFieldValue("MOTOR") }),
  },
  aim: {
    def: { message0: "aim %1 at %2", args0: [dd("TURRET", OPTIONS.turret), dd("GOAL", OPTIONS.goal)],
      style: STYLES.move, ...stmt,
      tooltip: "Keep the turret pointed at the goal's center, even while the robot drives and turns, until a later block spins, moves or stops it." },
    keys: ["turret", "at"],
    fields: (a) => ({ TURRET: a.turret, GOAL: a.at }),
    args: (b) => ({ turret: b.getFieldValue("TURRET"), at: b.getFieldValue("GOAL") }),
  },
  set_cylinder: {
    def: { message0: "set %1 to %2", args0: [dd("CYLINDER", OPTIONS.cylinder), dd("TO", CYLINDER_TO)],
      style: STYLES.move, ...stmt,
      tooltip: "Switch the cylinder's valve to push the rod out (extend) or pull it in (retract). Off lets a single "
        + "solenoid's spring retract it, and leaves a double solenoid's cylinder where it is." },
    keys: ["cylinder", "to"],
    fields: (a) => ({ CYLINDER: a.cylinder, TO: a.to }),
    args: (b) => ({ cylinder: b.getFieldValue("CYLINDER"), to: b.getFieldValue("TO") }),
  },
  wait: {
    def: { message0: "wait %1 s", args0: [num("SECONDS", 1, { min: 0 })], style: STYLES.wait, ...stmt,
      tooltip: "Wait this many seconds." },
    keys: ["seconds"],
    fields: (a) => ({ SECONDS: a.seconds }),
    args: (b) => ({ seconds: b.getFieldValue("SECONDS") }),
  },
  wait_until: {
    def: { message0: "wait until %1", args0: [{ type: "input_value", name: "COND", check: COND }], style: STYLES.wait,
      ...stmt, tooltip: "Wait until this is true." },
    keys: ["until"], cond: ["COND", "until"],
  },
  repeat: {
    def: { message0: "repeat %1 times", args0: [num("TIMES", 4, { min: 1, max: 100, precision: 1 })],
      message1: "%1", args1: [{ type: "input_statement", name: "DO" }], style: STYLES.repeat, ...stmt,
      tooltip: "Do the blocks inside this many times." },
    keys: ["times"],
    fields: (a) => ({ TIMES: a.times }),
    args: (b) => ({ times: b.getFieldValue("TIMES") }),
    body: true,
  },
  repeat_until: {
    def: { message0: "repeat until %1", args0: [{ type: "input_value", name: "COND", check: COND }],
      message1: "%1", args1: [{ type: "input_statement", name: "DO" }], style: STYLES.repeat, ...stmt,
      tooltip: "Do the blocks inside again and again until this is true." },
    keys: ["until"], cond: ["COND", "until"], body: true,
  },
  expect: {
    def: { message0: "expect %1 %2 the whole time",
      args0: [{ type: "input_value", name: "COND", check: COND }, { type: "field_checkbox", name: "THROUGHOUT", checked: false }],
      inputsInline: true, style: STYLES.check, ...stmt,
      tooltip: "Check this is true here (or, ticked, from here to the end of the run)." },
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
    def: { message0: "%1 touches %2", args0: [dd("ROBOT", OPTIONS.robot), dd("FIELD", OPTIONS.field)],
      tooltip: "A part of the robot touches something on the field." },
    fields: (a) => ({ ROBOT: a.robot, FIELD: a.field }),
    json: (b) => ({ touch: { robot: b.getFieldValue("ROBOT"), field: b.getFieldValue("FIELD") } }),
  },
  tilt: {
    def: { message0: "tilt %1 %2 °", args0: [dd("SIDE", SIDES), num("ANGLE", 20)],
      tooltip: "How far the robot leans from upright, in degrees." },
    fields: (a) => ("above" in a ? { SIDE: "above", ANGLE: a.above } : { SIDE: "below", ANGLE: a.below }),
    json: (b) => ({ tilt: { [b.getFieldValue("SIDE")]: b.getFieldValue("ANGLE") } }),
  },
  joint: {
    def: { message0: "%1 %2 %3 %4", args0: [dd("NAME", OPTIONS.joint), dd("SIDE", SIDES), num("VALUE", 40), dd("UNIT", UNITS)],
      tooltip: "Where a joint is." },
    fields: (a) => ("above" in a ? { NAME: a.name, SIDE: "above", VALUE: a.above, UNIT: a.unit }
      : { NAME: a.name, SIDE: "below", VALUE: a.below, UNIT: a.unit }),
    json: (b) => ({ joint: { name: b.getFieldValue("NAME"), [b.getFieldValue("SIDE")]: b.getFieldValue("VALUE"),
      unit: b.getFieldValue("UNIT") } }),
    init(b) { b.getField("NAME").setValidator(fitUnit("UNIT")); },
  },
  piece_in: {
    def: { message0: "%1 in %2", args0: [dd("PIECE", OPTIONS.piece), dd("BODY", OPTIONS.body)],
      tooltip: "A game piece is inside a body of the robot." },
    fields: (a) => ({ PIECE: a.piece, BODY: a.body }),
    json: (b) => ({ piece_in: { body: b.getFieldValue("BODY"), piece: b.getFieldValue("PIECE") } }),
  },
  held: {
    def: { message0: "%1 is held by %2", args0: [dd("PIECE", OPTIONS.piece), dd("BY", OPTIONS.heldBy)],
      tooltip: "A game piece is held by a part or body of the robot: off the ground, touching it and moving with it." },
    fields: (a) => ({ PIECE: a.piece, BY: a.by }),
    json: (b) => ({ held: { piece: b.getFieldValue("PIECE"), by: b.getFieldValue("BY") } }),
  },
  rests_on: {
    def: { message0: "%1 rests on %2", args0: [dd("PIECE", OPTIONS.piece), dd("ON", OPTIONS.restsOn)],
      tooltip: "A game piece rests on a part of the field (or on another piece), which carries its weight." },
    fields: (a) => ({ PIECE: a.piece, ON: a.on }),
    json: (b) => ({ rests_on: { piece: b.getFieldValue("PIECE"), on: b.getFieldValue("ON") } }),
  },
  seated: {
    def: { message0: "%1 is seated at least %2 in on %3",
      args0: [dd("PIECE", OPTIONS.piece), num("DEPTH", 3, { min: 0 }), dd("ON", OPTIONS.rod)],
      tooltip: "A game piece with a hole is on a rod, its hole passed over the rod's tip by at least this many inches." },
    fields: (a) => ({ PIECE: a.piece, DEPTH: a.at_least_in, ON: a.on }),
    json: (b) => ({ seated: { piece: b.getFieldValue("PIECE"), on: b.getFieldValue("ON"),
      at_least_in: b.getFieldValue("DEPTH") } }),
  },
  hanging_from: {
    def: { message0: "hanging from %1", args0: [dd("BAR", OPTIONS.bar)],
      tooltip: "The robot's hooks hold it up from this bar." },
    fields: (v) => ({ BAR: v }),
    json: (b) => ({ hanging_from: b.getFieldValue("BAR") }),
  },
  level: {
    def: { message0: "holding level %1", args0: [dd("LEVEL", OPTIONS.level)],
      tooltip: "The robot is holding this climb level." },
    fields: (v) => ({ LEVEL: v }),
    json: (b) => ({ level: b.getFieldValue("LEVEL") }),
  },
  in_hopper: {
    def: { message0: "%1 %2 pieces in the hopper",
      args0: [num("COUNT", 5, { min: 0, max: 1000, precision: 1 }), dd("SIDE", HOPPER_SIDES)],
      tooltip: "How many pieces the hopper holds (pick the hopper in the test's setup)." },
    fields: (a) => ("at_most" in a ? { COUNT: a.at_most, SIDE: "at_most" } : { COUNT: a.at_least, SIDE: "at_least" }),
    json: (b) => ({ in_hopper: { [b.getFieldValue("SIDE")]: b.getFieldValue("COUNT") } }),
  },
  scored: {
    def: { message0: "%1 scored in %2", args0: [num("COUNT", 1, { min: 1, max: 1000, precision: 1 }), dd("GOAL", OPTIONS.scoredGoal)],
      tooltip: "At least this many balls have gone into the goal (or into the goals together, for any goal)." },
    fields: (a) => ({ COUNT: a.at_least, GOAL: a.goal }),
    json: (b) => ({ scored: { goal: b.getFieldValue("GOAL"), at_least: b.getFieldValue("COUNT") } }),
  },
  aimed: {
    def: { message0: "%1 aimed at %2 within %3 °",
      args0: [dd("TURRET", OPTIONS.turret), dd("GOAL", OPTIONS.goal), num("WITHIN", 2, { min: 0.1 })],
      tooltip: "The turret points at the goal's center, off by no more than this many degrees." },
    fields: (a) => ({ TURRET: a.turret, GOAL: a.at, WITHIN: a.within_deg }),
    json: (b) => ({ aimed: { turret: b.getFieldValue("TURRET"), at: b.getFieldValue("GOAL"),
      within_deg: b.getFieldValue("WITHIN") } }),
  },
  cylinder: {
    def: { message0: "%1 is %2", args0: [dd("CYLINDER", OPTIONS.cylinder), dd("IS", CYLINDER_IS)],
      tooltip: "The cylinder's rod is all the way out (extended) or all the way in (retracted)." },
    fields: (a) => ({ CYLINDER: a.cylinder, IS: a.is }),
    json: (b) => ({ cylinder: { cylinder: b.getFieldValue("CYLINDER"), is: b.getFieldValue("IS") } }),
  },
  pressure: {
    def: { message0: "%1 air pressure %2 %3 psi", args0: [dd("AIR", AIR_SIDES), dd("SIDE", SIDES), num("PSI", 60, { min: 0 })],
      tooltip: "The air pressure in the tanks (stored), or after the regulator, which the cylinders get (working)." },
    fields: (a) => ({ AIR: a.side || "stored", ...("above" in a ? { SIDE: "above", PSI: a.above } : { SIDE: "below", PSI: a.below }) }),
    json: (b) => ({ pressure: { side: b.getFieldValue("AIR"), [b.getFieldValue("SIDE")]: b.getFieldValue("PSI") } }),
  },
  station_empty: {
    def: { message0: "%1 is empty", args0: [dd("STATION", OPTIONS.station)],
      tooltip: "The station has no pieces left to feed." },
    fields: (v) => ({ STATION: v }),
    json: (b) => ({ station_empty: b.getFieldValue("STATION") }),
  },
  made_it_in: {
    def: { message0: "at least %1% made it in", args0: [num("PERCENT", 80, { min: 0, max: 100 })],
      tooltip: "Of the pieces fed to the robot, at least this share made it into the hopper. "
        + "In an expect that is the test's last block, it waits (up to 3 s) for the pieces to settle first; anywhere else it counts them as they are." },
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
  const B = window.Blockly;
  const defs = [];
  for (const [type, k] of Object.entries(KINDS)) defs.push({ type, ...k.def });
  for (const [kind, c] of Object.entries(CONDS)) {
    defs.push({ type: condType(kind), output: COND, style: STYLES.check, ...c.def });
  }
  B.defineBlocksWithJsonArray(defs);
  // the joint pickers' validators (JSON can't carry a function)
  for (const [type, k] of [...Object.entries(KINDS), ...Object.entries(CONDS).map(([n, c]) => [condType(n), c])]) {
    if (!k.init) continue;
    const base = B.Blocks[type].init;
    B.Blocks[type].init = function () { base.call(this); k.init(this); };
  }
}

// -- the palette ------------------------------------------------------------------------------------------------------
export function toolbox(p) {
  p = p || {};
  const motors = has(p.motors), bars = has(p.bars);
  const touchOk = has([...(p.hooks || []), ...(p.parts || []), ...(p.bodies || [])])
    && has([...(p.bars || []), ...(p.elements || []), ...(p.pieces || []), ...(p.targets || [])]);
  const cond = (kind, fields = {}) => ({ block: { type: condType(kind), fields } });
  const waitCond = touchOk ? cond("touch") : cond("tilt", { SIDE: "below", ANGLE: 10 });
  const repeatCond = bars && has(p.hooks) ? cond("hanging_from") : cond("tilt", { SIDE: "above", ANGLE: 45 });
  const item = (type, extra = {}) => ({ kind: "block", type, ...extra });
  const out = [{ kind: "label", text: "Move" }];
  if (motors) out.push(item("spin"), item("move"));
  if (p.drive) out.push(item("drive"), item("turn"));
  const aimOk = has(p.turrets) && has(p.goals), cylinders = has(p.cylinders);
  if (aimOk) out.push(item("aim"));
  if (cylinders) out.push(item("set_cylinder"));
  out.push(item("stop"));
  if (has(p.stations)) out.push({ kind: "label", text: "Station" }, item("start_feeding"), item("stop_feeding"), item("feed_one"));
  out.push({ kind: "label", text: "Wait" }, item("wait"), item("wait_until", { inputs: { COND: waitCond } }));
  out.push({ kind: "label", text: "Repeat" }, item("repeat"), item("repeat_until", { inputs: { COND: repeatCond } }));
  out.push({ kind: "label", text: "Check" }, item("expect", { inputs: { COND: cond("tilt", { SIDE: "below", ANGLE: 20 }) } }));
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
  if (aimOk) out.push(item(condType("aimed")));
  if (cylinders) out.push(item(condType("cylinder")), item(condType("pressure")));
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
    }
    rememberNames(b.body);
  }
}

function condState(c) {
  if (!c || typeof c !== "object") return null;
  const [kind, a] = Object.entries(c)[0] || [];
  const spec = CONDS[kind];
  return spec ? { type: condType(kind), fields: spec.fields(a) } : null;
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
  return st;
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
    for (const t of tops) if (!best || stackSize(t) > stackSize(best)) best = t;
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
  for (const key of k.drop ? k.drop(b) : []) delete extra[key];
  const out = { do: b.type === "repeat_until" ? "repeat" : b.type, ...extra, ...(k.args ? k.args(b) : {}) };
  if (k.cond) out[k.cond[1]] = condJson(b.getInputTargetBlock(k.cond[0]));
  if (k.body) out.body = stackBlocks(b.getInputTargetBlock("DO")).map(blockJson);
  return out;
}

export const headOf = (ws) => header.get(ws) || {};
export function setHead(ws, patch) { header.set(ws, { ...headOf(ws), ...patch }); }

const UNIT_TEXT = { deg: "°", in: "in", m: "m" };
export function sweepNumber(b) {
  if (!b || b.isInFlyout) return null;
  if (b.type === "spin") return { key: "volts", field: "VOLTS", unit: "V" };
  if (b.type === "move") return { key: "to", field: "TO", unit: UNIT_TEXT[b.getFieldValue("UNIT")] || "" };
  if (b.type === "wait") return { key: "seconds", field: "SECONDS", unit: "s" };
  if (b.type === "drive") {
    return b.getFieldValue("WHAT") === "seconds" ? { key: "seconds", field: "AMOUNT", unit: "s" } : { key: "distance", field: "AMOUNT", unit: "m" };
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

// [{block, message, blocking}]: an empty condition socket (blocking: the test can't be saved like that) and blocks not
// joined to the test (not blocking: they are left out of the file).
export function incomplete(ws) {
  const out = [];
  const top = mainStack(ws);
  for (const t of looseStacks(ws)) {
    out.push({ block: t, blocking: false, message: t.outputConnection
      ? "This check isn't plugged into a block, so it does nothing: plug it into wait until, repeat until or expect."
      : "These blocks aren't joined to the test, so they don't run: drag them under the others, or delete them." });
  }
  if (top) {
    for (const b of top.getDescendants(false)) {
      const k = KINDS[b.type];
      if (k && k.cond && !b.getInputTargetBlock(k.cond[0])) {
        out.push({ block: b, blocking: true, message: `Plug a check into "${b.type === "expect" ? "expect" : b.type.replace("_", " ")}" (from Check).` });
      }
    }
  }
  return out;
}
