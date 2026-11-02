// The Test tab's blocks: one Blockly block type per `do` kind and per condition kind, the palette (Blockly's
// toolbox, filled from the robot's names), and the test JSON (scrimmage/blocks/program.py to_json) to and from a
// Blockly workspace. Blockly itself (window.Blockly, vendor/blockly.min.js) is loaded by test.js before any of this
// runs.
//
//   defineBlocks(names)            registers the block types once; `names()` answers the current palette (or null)
//   toolbox(palette)               the flyout's contents: Move, Wait, Repeat, Check (a block only when every one of its
//                                  pickers has something to pick)
//   testToWorkspace(test, ws)      replaces the workspace's blocks with the test's (one stack, top to bottom)
//   workspaceToTest(ws)            the test JSON of the workspace: the stack with the most blocks is the test; the
//                                  header (robot, hooks, ...) is the one testToWorkspace was given
//   placeOf(ws) / incomplete(ws)   block -> its place ([i, j, ...] as problems() gives them); the blocks that can't be
//                                  saved yet (an empty condition socket) or aren't joined to the test
//
// A block keeps the settings it has no box for (a move's timeout, a repeat until's max, a drive's volts) in
// `block.data`, so the round trip test -> workspace -> test is exact.

export const STYLES = { move: "move_blocks", wait: "wait_blocks", repeat: "repeat_blocks", check: "check_blocks" };
const COND = "Condition";

// Names the loaded tests use that the palette may not have (a joint renamed on Import again): a picker keeps
// offering them so the block keeps its value (and shows the problem) instead of quietly picking another name.
const used = { motor: new Set(), joint: new Set(), robot: new Set(), field: new Set(), body: new Set(),
  piece: new Set(), bar: new Set(), level: new Set() };

let names = () => null;
let loading = 0;        // testToWorkspace is filling the workspace: pickers don't adjust each other

const SPECIAL = { hook: "any hook", "next bar": "next bar", any: "any piece", all: "all motors" };

// [[label, value], ...] for a picker: `special` first (when given), the palette's names, then names only the test
// uses (marked as missing from the robot).
function choices(list, kind, special = []) {
  const out = [], seen = new Set();
  for (const v of special) { out.push([SPECIAL[v] || v, v]); seen.add(v); }
  for (const v of list) if (!seen.has(v)) { out.push([v, v]); seen.add(v); }
  for (const v of used[kind]) if (!seen.has(v)) { out.push([`${v} (not on the robot)`, v]); seen.add(v); }
  return out.length ? out : [["(none)", "(none)"]];
}

const pal = () => names() || {};
const motorNames = () => (pal().motors || []).map((m) => m.name);
const jointNames = () => (pal().joints || []).map((j) => j.name);
const robotSide = () => [...(pal().hooks || []), ...(pal().parts || []), ...(pal().bodies || [])];
const fieldSide = () => [...(pal().bars || []), ...(pal().elements || []), ...(pal().pieces || [])];
const has = (xs) => (xs || []).length > 0;

const OPTIONS = {
  motor: () => choices(motorNames(), "motor"),
  stopMotor: () => choices(motorNames(), "motor", ["all"]),
  moveJoint: () => choices(motorNames(), "joint"),
  joint: () => choices(jointNames(), "joint"),
  robot: () => choices(robotSide(), "robot", has(pal().hooks) ? ["hook"] : []),
  field: () => choices(fieldSide(), "field", has(pal().bars) ? ["next bar"] : []),
  body: () => choices(pal().bodies || [], "body"),
  piece: () => choices(pal().pieces || [], "piece", has(pal().pieces) ? ["any"] : []),
  bar: () => choices(pal().bars || [], "bar", has(pal().bars) ? ["next bar"] : []),
  level: () => choices(pal().levels || [], "level"),
};
const UNITS = [["°", "deg"], ["in", "in"], ["m", "m"]];
const SIDES = [["above", "above"], ["below", "below"]];

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
    def: { message0: "drive forward %1 %2", args0: [num("AMOUNT", 0.3), dd("WHAT", [["m", "distance"], ["s", "seconds"]])],
      style: STYLES.move, ...stmt, tooltip: "Drive the robot forward this far (a minus number drives back), or for this long." },
    keys: ["distance", "seconds"],
    fields: (a) => ("seconds" in a ? { AMOUNT: a.seconds, WHAT: "seconds" } : { AMOUNT: a.distance, WHAT: "distance" }),
    args: (b) => ({ [b.getFieldValue("WHAT")]: b.getFieldValue("AMOUNT") }),
    drop: (b) => (b.getFieldValue("WHAT") === "seconds" ? ["timeout"] : []),   // a timeout only goes with a distance
  },
  stop: {
    def: { message0: "stop %1", args0: [dd("MOTOR", OPTIONS.stopMotor)], style: STYLES.move, ...stmt,
      tooltip: "Stop a motor (or all of them)." },
    keys: ["motor"],
    fields: (a) => ({ MOTOR: a.motor }),
    args: (b) => ({ motor: b.getFieldValue("MOTOR") }),
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
    && has([...(p.bars || []), ...(p.elements || []), ...(p.pieces || [])]);
  const cond = (kind, fields = {}) => ({ block: { type: condType(kind), fields } });
  const waitCond = touchOk ? cond("touch") : cond("tilt", { SIDE: "below", ANGLE: 10 });
  const repeatCond = bars && has(p.hooks) ? cond("hanging_from") : cond("tilt", { SIDE: "above", ANGLE: 45 });
  const item = (type, extra = {}) => ({ kind: "block", type, ...extra });
  const out = [{ kind: "label", text: "Move" }];
  if (motors) out.push(item("spin"), item("move"));
  if (p.drive) out.push(item("drive"));
  out.push(item("stop"));
  out.push({ kind: "label", text: "Wait" }, item("wait"), item("wait_until", { inputs: { COND: waitCond } }));
  out.push({ kind: "label", text: "Repeat" }, item("repeat"), item("repeat_until", { inputs: { COND: repeatCond } }));
  out.push({ kind: "label", text: "Check" }, item("expect", { inputs: { COND: cond("tilt", { SIDE: "below", ANGLE: 20 }) } }));
  if (touchOk) out.push(item(condType("touch")));
  out.push(item(condType("tilt")));
  if (has(p.joints)) out.push(item(condType("joint")));
  if (has(p.pieces) && has(p.bodies)) out.push(item(condType("piece_in")));
  if (bars) out.push(item(condType("hanging_from")));
  if (has(p.levels)) out.push(item(condType("level")));
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
    const c = b.until || b.that;
    if (c && typeof c === "object") {
      const [kind, a] = Object.entries(c)[0] || [];
      if (kind === "joint") remember("joint", a.name);
      if (kind === "touch") { remember("robot", a.robot); remember("field", a.field); }
      if (kind === "piece_in") { remember("body", a.body); remember("piece", a.piece); }
      if (kind === "hanging_from") remember("bar", a);
      if (kind === "level") remember("level", a);
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

export function testToWorkspace(test, ws) {
  const B = window.Blockly;
  const { blocks = [], ...head } = test || {};
  header.set(ws, head);
  rememberNames(blocks);
  loading++;
  try {
    const top = chain(blocks);
    if (top) { top.x = 24; top.y = 24; }
    B.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: top ? [top] : [] } }, ws);
  } finally {
    loading--;
  }
  ws.clearUndo();
}

// -- workspace -> test JSON -------------------------------------------------------------------------------------------
const stackSize = (b) => b.getDescendants(false).length;

// The stack that is the test: the one with the most blocks (the topmost of equals); null when there are none.
export function mainStack(ws) {
  const tops = ws.getTopBlocks(true).filter((b) => !b.outputConnection);
  let best = null;
  for (const t of tops) if (!best || stackSize(t) > stackSize(best)) best = t;
  return best;
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
  for (const t of ws.getTopBlocks(true)) {
    if (t === top) continue;
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
