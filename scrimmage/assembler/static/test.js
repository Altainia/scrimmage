// The Test tab: a block test (`<name>.test.toml`) in Blockly. See /api/test/* in the docstring of
// scrimmage/assembler/server.py for the API contract, and blocks_defs.js for the blocks.
//
//   Left       the palette: Blockly's flyout (Move, Wait, Repeat, Check), filled from the robot's own names
//   Middle     the test: Blockly's workspace; the stack with the most blocks is the test
//   Right      the run: the problems that keep it from running, Run (and Stop while it runs), and the runs
//              (test_run.js: the shown run's outcome, replay and chart, and the kept runs)
//   Status     saved or not, how many blocks
//   Setup      (folded away over the blocks) the field and its start, the robot's weight and center of mass (when its
//              root part is the stock chassis) and where each part is mounted, which change the robot file through
//              /api/robot/edit, so the Robot tab's Undo takes them back; and the test's own (saved in the test): its
//              hopper (starter_hopper's preselection until changed) and hooks, and its [setup]: the ball, each
//              station's settings, the shuffle (seed) and the floor spread (Run the spread: /api/test/spread). On a
//              field with a station and a test with no feed block, "Add the station blocks" appends station_starter.
//
// "Try values…" (a block's context menu, for its number; or beside a number in Setup) opens the sweep form: from, to
// and steps, or typed values, at most 12; /api/test/sweep runs them and test_run.js shows the table and its chart.
//
// Every change is saved (500 ms after the last one) through /api/test/save with the state's version; a 409 (the file
// changed meanwhile) shows the saved version again and says so. Problems from the state show as a warning on their
// block and in the list above Run, with this page's own (a save the server refused, an empty condition socket, blocks
// not joined to the test); Run is off while there are any, and runs only the test as it is on screen. Blockly's theme follows the page's (light or dark),
// its colors read from app.css's tokens.
//
// Test hook: window.__scrimmageTest {workspace, state, toJSON(), saved: a promise for the save under way (or
// resolved), replay: test_run.js's replay(), sweep: test_run.js's sweepShown(), spread: its spreadShown(), robot: the
// robot state Setup shows, sweepFor: the sweep form's numbers, trySweep(blockId): opens the sweep form for that
// block's number}.

import { $, h, api, notice, registerTab, setTab, showPath, currentTab, store } from "./app.js";
import { initRunPanel, update as updateRuns, replay, sweepShown, spreadShown } from "./test_run.js";
import { defineBlocks, toolbox, testToWorkspace, workspaceToTest, placeOf, incomplete, looseStacks, setUsed, STYLES,
  headOf, setHead, sweepNumber, appendBlocks } from "./blocks_defs.js";

const SAVE_DELAY = 500;     // ms after the last change
const MAX_SWEEP = 12;       // scrimmage/blocks/sweep.py's
const STALE = "The test changed on disk; showing the saved version.";

const t = {
  state: null,        // /api/test/state
  ws: null,           // the Blockly workspace
  synced: null,       // canon() of the test as the server has it, when the workspace shows it
  base: null,         // the state version whose test the workspace started from (a save sends it)
  local: [],          // incomplete(ws): blocks not joined to the test, empty condition sockets
  saveError: null,    // the last save the server refused (400): {message, place: [i, j, ...] or null}
  timer: null,        // the pending save
  saving: null,       // the save under way (a promise)
  toolboxKey: null,
  stopping: false,    // Stop was pressed and the run hasn't ended yet
  serverWarn: new Map(),   // block id -> the state's problems on it, placed while the workspace showed the saved test
  robot: null,        // /api/robot/state (Setup's weight, center of mass and mounts)
  stock: null,        // /api/robot/library's stock parts by name (their defaults and groups)
  setupError: null,   // the last robot edit Setup made that the server refused
  setupKey: null,     // what Setup drew last (see renderSetup)
  sweepFor: null,     // the sweep form's numbers: [{label, target, value, unit, block, from, to, scale (file per shown)}]
  hopperSet: false,   // the student changed the hopper in Setup (no longer starter_hopper's preselection)
  floor: null,        // the floor spread's boxes as typed (floorDraft), for the test's floor_spread `floorFor`
  floorFor: null,
};

// JSON with sorted keys, so two equal tests compare equal whatever order their keys came in.
function canon(v) {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (v && typeof v === "object") return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`;
  return JSON.stringify(v === undefined ? null : v);
}

// -- Blockly ----------------------------------------------------------------------------------------------------------
function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (window.Blockly) { resolve(); return; }
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`couldn't load ${src}`));
    document.head.append(s);
  });
}

const dark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

// The Blockly theme for the page's theme, from app.css's tokens (read when that theme is first shown).
const themes = {};
function theme() {
  const name = dark && dark.matches ? "scrimmage-dark" : "scrimmage-light";
  if (!themes[name]) themes[name] = makeTheme(name);
  return themes[name];
}

function makeTheme(name) {
  const B = window.Blockly;
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const style = (cat) => ({ colourPrimary: v(`--block-${cat}`), colourTertiary: v(`--block-${cat}-3`) });
  return B.Theme.defineTheme(name, {
    base: B.Themes.Classic,
    blockStyles: Object.fromEntries(Object.entries(STYLES).map(([cat, id]) => [id, style(cat)])),
    componentStyles: {
      workspaceBackgroundColour: v("--panel"),
      flyoutBackgroundColour: v("--subtle"),
      flyoutForegroundColour: v("--text"),
      flyoutOpacity: 1,
      scrollbarColour: v("--line-strong"),
      scrollbarOpacity: 0.8,
      insertionMarkerColour: v("--text"),
      insertionMarkerOpacity: 0.3,
      selectedGlowColour: v("--accent-text"),
      cursorColour: v("--accent-text"),
      markerColour: v("--accent-text"),
    },
    fontStyle: { family: v("--font"), weight: "600", size: 11 },
  });
}

function inject() {
  const B = window.Blockly;
  defineBlocks(() => (t.state ? t.state.palette : null));
  const box = $("#test-blockly");
  box.replaceChildren();
  t.ws = B.inject(box, {
    toolbox: toolbox(t.state.palette),
    theme: theme(),
    media: "vendor/blockly-media/",
    sounds: false,
    trashcan: false,
    comments: false,
    collapse: false,
    disable: false,
    renderer: "geras",
    move: { scrollbars: true, drag: true, wheel: true },
    zoom: { controls: true, wheel: false, startScale: 1.0 },
  });
  t.toolboxKey = canon(t.state.palette);
  t.ws.addChangeListener(onChange);
  registerTryValues();
  if (dark && dark.addEventListener) dark.addEventListener("change", () => t.ws.setTheme(theme()));
  new ResizeObserver(() => { B.svgResize(t.ws); fitHeads(); }).observe(box);
}

// The column titles over the flyout and the workspace.
function fitHeads() {
  const fl = t.ws && t.ws.getFlyout();
  if (fl) $(".test-heads").style.setProperty("--flyout-w", `${Math.round(fl.getWidth())}px`);
}

function show(test) {
  t.saveError = null;
  t.hopperSet = false;
  t.floor = null;
  testToWorkspace(test, t.ws);
  t.synced = canon(test);
  t.base = t.state.version;
  check();
}

// -- saving -----------------------------------------------------------------------------------------------------------
function onChange(e) {
  const B = window.Blockly;
  if (e.isUiEvent) return;
  if (![B.Events.BLOCK_CHANGE, B.Events.BLOCK_CREATE, B.Events.BLOCK_DELETE, B.Events.BLOCK_MOVE].includes(e.type)) return;
  check();
  // Blockly reports the blocks testToWorkspace made a moment later: those are no change to save
  if (t.timer || t.saving || canon(workspaceToTest(t.ws)) !== t.synced) schedule();
  else renderStatus();
  renderTop();
}

function schedule() {
  clearTimeout(t.timer);
  t.timer = setTimeout(() => { t.timer = null; t.saving = save().finally(() => { t.saving = null; }); }, SAVE_DELAY);
  renderStatus();
}

// Save now (a pending change), and wait for any save under way.
async function flush() {
  if (t.timer) {
    clearTimeout(t.timer);
    t.timer = null;
    t.saving = save().finally(() => { t.saving = null; });
  }
  while (t.saving) await t.saving;
}

async function save() {
  if (t.local.some((p) => p.blocking)) { renderStatus(); return; }   // can't be written like this yet
  const test = workspaceToTest(t.ws);
  const key = canon(test);
  if (key === t.synced) { t.saveError = null; renderStatus(); return; }
  renderStatus(true);
  const { status, data } = await api("POST", "/api/test/save", { test, version: t.base });
  if (status === 200 && data.ok) {
    t.synced = key;
    t.base = data.version;
    t.saveError = null;
    await refresh();
  } else if (status === 409) {
    t.saveError = null;
    notice(STALE, "warn", "test-stale");
    await refresh(true);
  } else {
    t.saveError = refused((data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`);
    check();
  }
  renderStatus();
}

// The server names a bad block by its place ("block 2 inside block 3 (repeat): seconds can't be negative"): the
// message without that, and the place (0-based, outermost first) when it has one.
function refused(text) {
  const m = /^block (\d+)((?: inside block \d+)*)(?: \(repeat\))?: (.*)$/s.exec(text);
  if (!m) return { message: text, place: null };
  const nums = [m[1], ...(m[2].match(/\d+/g) || [])].map(Number).reverse();
  return { message: m[3], place: nums.map((n) => n - 1) };
}

// -- state ------------------------------------------------------------------------------------------------------------
async function refresh(reload = false) {
  const { status, data } = await api("GET", "/api/test/state");
  if (status === 200) apply(data, reload);
}

function apply(s, reload = false) {
  const prev = t.state;
  if (prev && !reload && s.version < prev.version) return;   // an older answer overtaken by a newer one
  t.state = s;
  if (t.ws) {
    setUsed([s.test, workspaceToTest(t.ws)]);
    const key = canon(s.palette);
    if (key !== t.toolboxKey) {
      t.toolboxKey = key;
      t.ws.updateToolbox(toolbox(s.palette));
      fitHeads();
      if ($("#test-setup").open) loadRobot();     // the robot changed: its parts may have too
    }
    const changed = canon(s.test) !== t.synced;
    // a change this page didn't make (an outside edit, another tab), while nothing of ours is waiting to be saved
    if (reload || (changed && !t.timer && !t.saving)) {
      if (changed && !reload && prev) notice("The test changed outside this page (the file was edited, or another tab), so it was reloaded.", "info");
      const loose = looseStacks(t.ws).map((b) => b.toString());
      show(s.test);
      if (loose.length) notice(`Blocks not joined to your test were left out: ${loose.join("; ")}`, "warn");
    } else {
      if (!changed) t.base = s.version;
      check();
    }
  }
  render();
}

async function pollLoop() {
  for (;;) {
    const since = t.state ? `?since=${t.state.version}` : "";
    const { status, data } = await api("GET", "/api/test/state" + since);
    if (status === 200) apply(data);
    else if (status === 404) return;     // the test was closed (another file opened); the page reloads
    else await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

// -- problems ---------------------------------------------------------------------------------------------------------
// Server problems (at their place) and this page's own (incomplete blocks), as warnings on the blocks.
function check() {
  if (!t.ws) return;
  t.local = incomplete(t.ws);
  // the state's places are the saved test's: they fit the workspace while it shows that test (else the last ones stay)
  if (t.state && fits()) {
    const ids = placeIds();
    t.serverWarn = new Map();
    for (const p of t.state.problems || []) {
      const id = p.place.length ? ids.get(p.place.join("/")) : null;
      if (id) t.serverWarn.set(id, [...(t.serverWarn.get(id) || []), p.message]);
    }
  }
  const byBlock = new Map(t.serverWarn);
  const refusedAt = saveErrorBlock();
  if (refusedAt) byBlock.set(refusedAt, [...(byBlock.get(refusedAt) || []), t.saveError.message]);
  for (const p of t.local) byBlock.set(p.block.id, [...(byBlock.get(p.block.id) || []), p.message]);
  for (const b of t.ws.getAllBlocks(false)) {
    const msgs = byBlock.get(b.id);
    const text = msgs ? msgs.join("\n") : null;
    if ((b.warningShown || null) !== text) { b.setWarningText(text); b.warningShown = text; }
  }
  renderProblems();
}

const fits = () => !!t.ws && canon(workspaceToTest(t.ws)) === t.synced;
const placeIds = () => new Map([...placeOf(t.ws)].map(([id, place]) => [place.join("/"), id]));
// the block a refused save names (its place is the workspace's: that is the test it tried to save)
const saveErrorBlock = () => (t.saveError && t.saveError.place && t.ws ? placeIds().get(t.saveError.place.join("/")) || null : null);

function problemList() {
  const out = [];
  const s = t.state;
  if (!s) return out;
  if (s.file_error) out.push({ message: `The test file can't be read: ${s.file_error}` });
  const ids = fits() ? placeIds() : new Map();
  for (const p of s.problems || []) out.push({ message: p.message, id: p.place.length ? ids.get(p.place.join("/")) : null });
  if (t.saveError) out.push({ message: `Not saved: ${t.saveError.message}`, id: saveErrorBlock() });
  for (const p of t.local) out.push({ message: p.message, id: p.block.id });
  return out;
}

function goTo(id) {
  const b = t.ws && t.ws.getBlockById(id);
  if (!b) return;
  t.ws.centerOnBlock(id);
  b.select();
}

function renderProblems() {
  const list = problemList();
  const ul = $("#test-problems");
  ul.replaceChildren(...list.map((p) => {
    const b = p.id && t.ws ? t.ws.getBlockById(p.id) : null;
    return h("li", { class: "test-problem" },
      b ? h("button", { type: "button", class: "linklike", onclick: () => goTo(p.id), text: b.toString() }) : null,
      h("span", { text: p.message }));
  }));
  $("#test-problems-wrap").hidden = !list.length;
  const s = t.state;
  $("#test-run").disabled = !s || !!list.length || !!s.running;
  $("#test-run").title = list.length ? "Fix the problems first" : "";
}

// -- rendering --------------------------------------------------------------------------------------------------------
function renderTop() {
  if (currentTab() !== "test" || !t.state) return;
  showPath(store.appPath || t.state.path, t.state.path);
  $("#undo").disabled = !t.ws || !t.ws.getUndoStack().length;
  $("#redo").disabled = !t.ws || !t.ws.getRedoStack().length;
}

function renderStatus(busy = false) {
  const el = $("#test-saved");
  if (!t.state) return;
  const blocking = t.local.find((p) => p.blocking);
  if (busy || t.timer || t.saving) { el.textContent = "Saving…"; el.className = "saved saved-busy"; }
  else if (t.saveError) { el.textContent = `Not saved: ${t.saveError.message}`; el.className = "saved saved-bad"; }
  else if (blocking) { el.textContent = "Not saved yet: a block needs a check plugged in"; el.className = "saved saved-warn"; }
  else { el.textContent = "✓ All changes saved"; el.className = "saved saved-ok"; }
  const n = t.ws ? placeOf(t.ws).size : 0;   // every block of the test, the ones inside repeats too
  $("#test-count").textContent = t.ws ? `${n} block${n === 1 ? "" : "s"} in the test` : "";
}

function render() {
  const s = t.state;
  if (!s) return;
  const banner = $("#test-banner");
  banner.hidden = !s.file_error;
  banner.textContent = s.file_error ? `The test file can't be read, so this is its last good version: ${s.file_error}` : "";
  const running = $("#test-running");
  running.hidden = !s.running;
  running.textContent = s.running ? `Running… ${Math.round(100 * (s.running.progress || 0))}%` : "";
  if (!s.running) t.stopping = false;
  $("#test-stop").hidden = !s.running;
  $("#test-stop").disabled = t.stopping;
  updateRuns(s);
  renderProblems();
  renderStatus();
  renderTop();
  renderSetup();
}

async function run() {
  await flush();
  // only the test on screen runs: not a file this page couldn't save
  if (t.saveError || t.local.length || !fits()) { check(); renderStatus(); return; }
  const { status, data } = await api("POST", "/api/test/run", {});
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
    notice(`Can't run the test: ${why}`, "warn");
  }
  await refresh();
}

async function stop() {
  t.stopping = true;
  $("#test-stop").disabled = true;
  const { status, data } = await api("POST", "/api/test/cancel", {});
  if (status !== 200) {
    notice(`Couldn't stop the run: ${(data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`}`, "error");
    t.stopping = false;
    $("#test-stop").disabled = false;
  }
}


// -- setup ------------------------------------------------------------------------------------------------------------
async function loadRobot() {
  const [{ status, data }, lib] = await Promise.all([api("GET", "/api/robot/state"),
    t.stock ? null : api("GET", "/api/robot/library")]);
  if (lib && lib.status === 200) t.stock = Object.fromEntries((lib.data.stock || []).map((x) => [x.name, x]));
  t.robot = status === 200 ? data : null;
  renderSetup();
}

const stockDefault = (stock, name, fallback) => {
  const p = t.stock && t.stock[stock] && t.stock[stock].params.find((x) => x.name === name);
  return p ? p.default : fallback;
};
// the part the robot is (attached to nothing): Setup's weight and center of mass are its, when it is the stock chassis
const rootPart = () => (t.robot ? (t.robot.parts || []).find((p) => !p.attach) || null : null);
const chassis = () => { const r = rootPart(); return r && r.stock === "chassis" ? r : null; };
const mass = (c) => Number(c.params.mass ?? stockDefault("chassis", "mass", 46));
const com = (c) => (Array.isArray(c.params.com_offset) ? c.params.com_offset : stockDefault("chassis", "com_offset", [0, 0, 0])).map(Number);
// each part but the root and the drivetrain, which only the Robot tab moves
const isDrivetrain = (p) => !!(p.stock && t.stock && t.stock[p.stock] && t.stock[p.stock].group === "Drivetrain");
const mounted = () => (t.robot ? (t.robot.parts || []).filter((p) => p.attach && !isDrivetrain(p)) : []);
const AXES = [["x", 0, "pos", "forward"], ["y", 1, "pos", "left"], ["z", 2, "pos", "up"], ["yaw", 2, "rot", "turned"]];
const fmt = (v) => String(+Number(v).toPrecision(6));
const IN = 0.0254;            // m per inch
const MAX_SPREAD = 25;        // scrimmage/blocks/program.py's MAX_PLACEMENTS
const MAX_SHUFFLE = 999999;   // New shuffle's seeds: 1 to this
const DEFAULT_SEED = 1;       // scrimmage/testrange/station.py's: the shuffle of a test with no seed
const FEED_KINDS = ["start_feeding", "stop_feeding", "feed_one"];
// a station's numbers (scrimmage/testrange/station.py's OVERRIDE_KEYS): key, label, unit on screen, a list key's
// three axes, and what Try values… goes up to from a spread of 0. Every spread is ± its value; the release box is in
// inches here and meters in the file.
const STATION_BOXES = [
  { key: "interval", label: "Time between pieces (s)", unit: "s" },
  { key: "stock", label: "Stock (empty: never runs out)", unit: "" },
  { key: "spread_yaw_deg", label: "Left/right angle (± degrees)", unit: "°", upTo: 10 },
  { key: "spread_pitch_deg", label: "Up/down angle (± degrees)", unit: "°", upTo: 10 },
  { key: "spread_speed", label: "Speed (± m/s)", unit: "m/s", upTo: 0.4 },
  { key: "spread_position", label: "Release box (± in)", unit: "in", axes: ["x", "y", "z"], scale: IN, upTo: 2 },
  { key: "spread_rotation_deg", label: "Turned (± degrees)", unit: "°", axes: ["about x", "about y", "about z"], upTo: 90 },
];
// the floor spread's axes (scrimmage/blocks/spread.py's SPREAD_AXES): label, and from, to, steps when ticked: around
// where the field's ball lies (palette.floor_ball; offset and distance), never nearer than its nearest_in
const FLOOR_AXES = [
  ["offset_in", "Offset across (in)", -6, 6, 3],
  ["distance_in", "Distance ahead (in)", -12, 12, 3],
  ["angle_deg", "Angle (degrees)", -30, 30, 3],
  ["rotation_deg", "Turned (degrees)", 0, 90, 3],
];
// a start's id in words: "at_station" -> "At station"
const startWords = (id) => { const w = id.replace(/_/g, " "); return w.charAt(0).toUpperCase() + w.slice(1); };
const floorBall = () => (t.state && t.state.palette && t.state.palette.floor_ball) || null;

// A number box: `blank` (when given) is what an empty box means, and a value equal to it shows empty.
function numberBox(id, value, label, commit, blank = null) {
  const el = h("input", { id, class: "test-num", inputmode: "decimal", autocomplete: "off", "aria-label": label });
  el.value = el.dataset.orig = blank !== null && value === blank ? "" : fmt(value);
  const send = () => {
    const text = el.value.trim();
    if (text === el.dataset.orig) return;
    const v = text === "" && blank !== null ? blank : Number(text);
    if ((text === "" && blank === null) || !isFinite(v)) { t.setupError = `${label}: type a number`; renderSetupError(); el.value = el.dataset.orig; return; }
    el.dataset.orig = text;
    commit(v);
  };
  el.addEventListener("change", send);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
  return el;
}

// Robot edits go one at a time, each built (`make()`) from the robot as the last one left it, with its version: two
// boxes changed quickly don't undo each other or come back stale.
let editing = Promise.resolve();
function robotEdit(make) {
  editing = editing.then(async () => {
    const body = t.robot && make(t.robot);
    if (!body) return;
    const { status, data } = await api("POST", "/api/robot/edit", { version: t.robot.version, ...body });
    t.setupError = status === 200 && data.ok ? null
      : (data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`;
    await loadRobot();
  }).catch((e) => { t.setupError = String(e.message || e); renderSetupError(); });
  return editing;
}
const partNamed = (robot, name) => (robot.parts || []).find((p) => p.name === name) || null;

function setHooks(hooks) {
  setTest({ hooks: [...hooks].sort() });
}

// The hopper Setup ticks: the test's (an empty one the student cleared stays empty); for a test that names none,
// starter_hopper's until the student changes it.
const hopperNamed = () => Array.isArray(headOf(t.ws).hopper);
function hopperShown() {
  const mine = headOf(t.ws).hopper || [];
  return hopperNamed() || t.hopperSet ? mine : (t.state && t.state.starter_hopper) || [];
}

// A change to the test's own Setup settings (`patch`: hopper, setup) saves the test; a preselected hopper goes
// along with the first one. Setup is drawn again once the focus has moved on (a box's change comes as it loses the
// focus), so the box tabbed to keeps it.
function setTest(patch) {
  const shown = hopperShown();
  if (!("hopper" in patch) && !hopperNamed() && shown.length) patch = { ...patch, hopper: [...shown] };
  setHead(t.ws, patch);
  schedule();
  setTimeout(renderSetup, 0);
}
const setupOf = () => JSON.parse(JSON.stringify(headOf(t.ws).setup || {}));

function setHopper(bodies) {
  t.hopperSet = true;
  setTest({ hopper: [...bodies].sort() });
}

function setStation(name, key, v) {
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

const hasFeed = () => !!t.ws && t.ws.getAllBlocks(false).some((b) => FEED_KINDS.includes(b.type));

function addStationBlocks() {
  const blocks = t.state && t.state.station_starter;
  if (!blocks || hasFeed()) return;
  appendBlocks(t.ws, blocks);
  renderSetup();
}

// -- the floor spread: a draft of the boxes ({axis: {on, from, to, steps}}), written to the test when it makes at
// most MAX_SPREAD runs
function floorDraft() {
  const fs = (headOf(t.ws).setup || {}).floor_spread || {};
  const key = canon(fs);
  if (t.floor && t.floorFor === key) return t.floor;
  t.floorFor = key;
  const ball = floorBall() || {};
  t.floor = Object.fromEntries(FLOOR_AXES.map(([axis, , from, to, steps]) => {
    const v = fs[axis];
    if (v && v.length) return [axis, { on: true, from: fmt(v[0]), to: fmt(v[v.length - 1]), steps: String(v.length) }];
    const base = ball[axis] ?? 0;
    let lo = base + from, hi = base + to;
    if (axis === "distance_in" && ball.nearest_in !== undefined && lo < ball.nearest_in) {
      lo = ball.nearest_in;
      hi = Math.max(hi, lo + (to - from));
    }
    return [axis, { on: false, from: fmt(Math.round(lo)), to: fmt(Math.round(hi)), steps: String(steps) }];
  }));
  return t.floor;
}

// The draft's lists and run count ({spread, runs}), or {error} saying what is wrong.
function floorSpread(draft) {
  const spread = {};
  let runs = 1;
  for (const [axis, label] of FLOOR_AXES) {
    const d = draft[axis];
    if (!d.on) continue;
    const a = Number(d.from), b = Number(d.to), n = Number(d.steps);
    if (d.from.trim() === "" || d.to.trim() === "" || !isFinite(a) || !isFinite(b)) return { error: `${label}: type a number in from and to` };
    if (!Number.isInteger(n) || n < 1) return { error: `${label}: steps is a whole number, 1 or more` };
    // nearest_in alone, ignoring the offset: stricter than the server's exact check, which is fine as a quick warning
    const nearest = floorBall() && floorBall().nearest_in;
    if (axis === "distance_in" && nearest !== undefined && nearest !== null && Math.min(a, b) < nearest) {
      return { error: `${label}: ${nearest} in or more (nearer, the ball starts inside the robot)` };
    }
    spread[axis] = n === 1 ? [a] : Array.from({ length: n }, (_, k) => +(a + ((b - a) * k) / (n - 1)).toPrecision(6));
    runs *= n;
  }
  if (!Object.keys(spread).length) return { error: "Tick an axis to spread the ball over", spread, runs: 0 };
  if (runs > MAX_SPREAD) return { error: `${runs} runs: at most ${MAX_SPREAD} runs; use fewer steps`, runs };
  return { spread, runs };
}

function floorChanged() {
  const draft = t.floor;
  for (const [axis] of FLOOR_AXES) {
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
  if (canon(s.floor_spread || {}) === canon((headOf(t.ws).setup || {}).floor_spread || {})) return;
  t.floorFor = canon(s.floor_spread || {});
  setTest({ setup: s });
}

function renderFloorCount(r) {
  const el = document.getElementById("test-spread-count");
  if (!el) return;
  el.textContent = r.error || `${r.runs} run${r.runs === 1 ? "" : "s"}`;
  el.classList.toggle("error", !!r.error && r.runs > MAX_SPREAD);
  $("#test-spread-run").disabled = !!r.error || !t.state || !!t.state.running;
}

async function runSpread() {
  await flush();
  if (t.saveError || t.local.length || !fits()) { check(); renderStatus(); return; }
  const { status, data } = await api("POST", "/api/test/spread", {});
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
    notice(`Can't run the spread: ${why}`, "warn");
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
  // a list: the sweep sets all three axes to the value; the release box is tried in inches, as its boxes show it
  const scale = box.scale || 1;
  const v = +((box.axes ? Math.max(...value) : value) / scale).toPrecision(6);
  const label = box.axes ? `${box.label.replace(/ \(.*/, "")}, every axis (± ${box.unit})` : box.label;
  return { label, target, value: v, unit: box.unit, from: 0, to: v > 0 ? 2 * v : box.upTo, ...(box.scale ? { scale } : {}) };
}

function stationBox(name, box, setting, field) {
  const id = `test-station-${name}-${box.key}`;
  const tryIt = h("button", { type: "button", class: "btn btn-small", "data-try": `station-${name}-${box.key}`,
    onclick: () => openSweep([stationChoice(name, box, setting)], `${name}'s ${box.label.replace(/ \(.*/, "").toLowerCase()}`) }, "Try values…");
  if (!box.axes) {
    return [h("label", { for: id, text: box.label }),
      h("div", { class: "row" }, numberBox(id, setting, `${name}: ${box.label}`, (v) => setStation(name, box.key, v),
        box.key === "stock" ? 0 : null), tryIt)];
  }
  const scale = box.scale || 1;
  return [h("span", { class: "test-setup-label", text: box.label }),
    h("div", { class: "row test-com" }, ...box.axes.flatMap((word, i) => [
      h("label", { for: `${id}-${i}`, text: word }),
      numberBox(`${id}-${i}`, setting[i] / scale, `${name}: ${box.label} ${word}`, (v) => {
        const now = (((headOf(t.ws).setup || {}).station || {})[name] || {})[box.key] || field;
        const next = [...now].map(Number);
        next[i] = +(v * scale).toPrecision(6);
        setStation(name, box.key, next);
      })]), tryIt)];
}

// The test's [setup] settings this field can't use, as [key, words, clear(setup)]: a station it doesn't have, a ball
// on a field that keeps its own, a floor spread with no ball on the floor (palette.problems refuses the run for each)
function leftovers(pal, setup) {
  const out = [];
  for (const name of Object.keys(setup.station || {})) {
    if ((pal.stations || []).includes(name)) continue;
    out.push([`station-${name}`, `Station "${name}": this field has no station by that name`, (x) => {
      delete x.station[name];
      if (!Object.keys(x.station).length) delete x.station;
    }]);
  }
  if (setup.ball && !pal.ball_choice) {
    out.push(["ball", `Ball "${setup.ball}": ${pal.ball ? "this field keeps its own ball" : "this field has no ball to pick"}`, (x) => { delete x.ball; }]);
  }
  if (setup.floor_spread && Object.keys(setup.floor_spread).length && !pal.floor_piece) {
    out.push(["floor_spread", "Floor spread: this field has no ball on the floor", (x) => { delete x.floor_spread; }]);
  }
  return out;
}

function renderSetupError() {
  const el = document.getElementById("test-setup-error");
  if (!el) return;
  el.hidden = !t.setupError;
  el.textContent = t.setupError ? `Not changed: ${t.setupError}` : "";
}

function renderSetup() {
  const s = t.state;
  if (!s || !t.ws) return;
  const body = $("#test-setup-body");
  const open = $("#test-setup").open;
  const pal = s.palette || {};
  const head = headOf(t.ws);
  const setup = head.setup || {};
  const mine = new Set(head.hooks || []);
  const counted = new Set((pal.hooks || []).filter((x) => !mine.has(x)));     // the stock climber's: always hooks
  const hopper = new Set(hopperShown());
  const c = chassis();
  const sum = [`hooks: ${[...mine, ...counted].length ? [...counted, ...mine].join(", ") : "none"}`];
  if (hopper.size) sum.push(`hopper: ${[...hopper].join(", ")}`);
  if (c) sum.push(`robot ${fmt(mass(c))} kg`);
  if (t.robot) sum.push(t.robot.range ? `field: ${t.robot.range}` : "plain field");
  $("#test-setup-sum").textContent = `· ${sum.join(" · ")}`;
  if (!open) { t.setupKey = null; return; }      // filled in when it is opened
  const ro = !t.robot || !!t.robot.read_only;
  const feeding = hasFeed();
  // drawn again only when something it shows changed (states come four times a second while a run goes)
  // the floor spread's boxes show its draft: the spread the draft wrote is nothing new to draw
  const { floor_spread: fs, ...drawn } = setup;
  const key = canon([pal.bodies || [], [...mine], [...counted], [...hopper], drawn,
    canon(fs || {}) === t.floorFor ? null : fs, pal.station_settings || {},
    pal.ball || null, pal.balls || [], !!pal.ball_choice, !!pal.floor_piece, s.station_starter, feeding, !!s.running,
    (s.fields || []).length, t.robot ? [t.robot.version, t.robot.parts, ro] : null, !!t.stock, t.setupError]);
  if (key === t.setupKey) return;
  const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
  if (focused && document.activeElement.matches("input.test-num") && document.activeElement.value !== document.activeElement.dataset.orig) return;

  // the mechanism's bodies: not the drivetrain's or the stock root part's (as the mounts); a ticked one always shows
  const skip = new Set();
  if (t.robot && t.robot.part_bodies) {
    const root = rootPart();
    for (const p of t.robot.parts || []) {
      if (!(p.stock && ((root && p.name === root.name) || isDrivetrain(p)))) continue;
      for (const b of t.robot.part_bodies[p.name] || []) skip.add(`${p.name}.${b}`);
    }
  }
  const base = (pal.bodies || []).filter((b) => !skip.has(b));
  const bodyList = (kind, ticked, fixed, onchange) => h("div", { class: "test-hook-list" },
    ...[...new Set([...base, ...ticked, ...fixed])].sort().map((b, i) => {
      const id = `test-${kind}-${i}`;
      const missing = !(pal.bodies || []).includes(b);
      const box = h("input", { type: "checkbox", id, "data-body": b, checked: ticked.has(b) || fixed.has(b), disabled: fixed.has(b),
        onchange: (e) => { const next = new Set(ticked); if (e.target.checked) next.add(b); else next.delete(b); onchange(next); } });
      return h("label", { class: "test-hook", for: id }, box, h("span", { text: b + (missing ? " (not on the robot)" : "") }));
    }));

  const parts = [];
  if (t.robot && !t.robot.read_only) {
    const fields = s.fields || [];
    const now = t.robot.range || "";
    const entry = fields.find((f) => (f.file || "") === now);
    const opts = fields.map((f) => [f.file || "", f.name]);
    if (!entry) opts.push([now, `${now} (this robot's field)`]);
    const sel = h("select", { id: "test-field", onchange: (e) => {
      const f = fields.find((x) => (x.file || "") === e.target.value);
      if (!f || e.target.value === now) return;
      // the new field's first start; a [robot] start_pose goes, and Undo brings it back
      robotEdit(() => (!f.file ? { op: "start", range: null }
        : f.starts.length ? { op: "start", range: f.file, named: f.starts[0] } : { op: "start", range: f.file }));
    } }, ...opts.map(([v, text]) => h("option", { value: v, text })));
    sel.value = now;
    const field = [h("legend", { text: "The field" }),
      h("p", { class: "hint", text: "Where the robot starts: an empty floor, a field made to try one mechanism on (a ladder to climb, a ball to pick up, "
        + "a human player station that feeds balls), or a whole season's field. "
        + `Picking a field moves the robot to that field's start (this changes ${t.robot.display_path || t.robot.path || "the robot file"}). The Robot tab's Undo puts the old field and start back.` }),
      h("label", { class: "visually-hidden", for: "test-field", text: "The field" }), sel];
    if (entry && entry.starts.length > 1) {
      const start = t.robot.start || "";
      const starts = [...(start && entry.starts.includes(start) ? [] : [[start, start ? `${start} (not on this field)` : "Where the robot file puts it"]]),
        ...entry.starts.map((x) => [x, startWords(x)])];
      const pick = h("select", { id: "test-start", onchange: (e) => {
        const v = e.target.value;
        if (v && v !== start && entry.starts.includes(v)) robotEdit(() => ({ op: "start", named: v }));
      } }, ...starts.map(([v, text]) => h("option", { value: v, text })));
      pick.value = start;
      field.push(h("div", { class: "row test-start" }, h("label", { for: "test-start", text: "Start" }), pick));
    }
    parts.push(h("fieldset", { class: "test-setup-field" }, ...field));
  }
  parts.push(h("fieldset", { class: "test-setup-hopper" },
    h("legend", { text: "Hopper" }),
    h("p", { class: "hint", text: "Tick the bodies a piece lands in. A piece whose center is inside one made it in." }),
    bodyList("hopper", hopper, new Set(), setHopper)));
  parts.push(h("fieldset", { class: "test-setup-hooks" },
    h("legend", { text: "Hooks" }),
    h("p", { class: "hint", text: "Tick the bodies that grab a bar. A stock climber's hooks always count." }),
    bodyList("hook", mine, counted, setHooks)));
  if (pal.ball_choice && pal.ball) {
    const balls = [pal.ball, ...(pal.balls || []).filter((b) => b !== pal.ball)];
    const odd = setup.ball && !balls.includes(setup.ball) ? [setup.ball] : [];
    const sel = h("select", { id: "test-ball", onchange: (e) => {
      const next = setupOf();
      if (e.target.value === pal.ball) delete next.ball; else next.ball = e.target.value;
      setTest({ setup: next });
    } }, ...balls.map((b) => h("option", { value: b, text: b === pal.ball ? `${b} (the field's)` : b })),
      ...odd.map((b) => h("option", { value: b, text: `${b} (not in the list)` })));
    sel.value = setup.ball || pal.ball;
    parts.push(h("fieldset", { class: "test-setup-ball" },
      h("legend", { text: "Ball" }),
      h("p", { class: "hint", text: "Pick the ball this test uses." }),
      h("label", { class: "visually-hidden", for: "test-ball", text: "Ball" }), sel));
  }
  const settings = pal.station_settings || {};
  for (const name of pal.stations || []) {
    const field = settings[name] || {};
    const own = (setup.station || {})[name] || {};
    parts.push(h("fieldset", { class: "test-setup-station", "data-station": name },
      h("legend", { text: `Station: ${name}` }),
      h("p", { class: "hint", text: "How the human player feeds pieces. Each box shows the field's value until you change it; a change is saved in the test. "
        + "Each piece varies by up to ± each spread." }),
      h("div", { class: "test-setup-grid" }, ...STATION_BOXES.flatMap((box) => stationBox(name, box, own[box.key] ?? field[box.key], field[box.key])))));
  }
  if ((pal.stations || []).length) {
    const seed = setup.seed ?? DEFAULT_SEED;
    parts.push(h("fieldset", { class: "test-setup-shuffle" },
      h("legend", { text: "Shuffle" }),
      h("p", { class: "hint", text: "Which draw of the spreads the stations feed: the same shuffle gives the same feeds every run, so a comparison is fair. New shuffle checks it wasn't a lucky draw." }),
      h("div", { class: "row" }, h("span", { id: "test-seed", class: "test-seed", text: `Shuffle ${seed}` }),
        h("button", { type: "button", id: "test-new-shuffle", class: "btn btn-small", onclick: newShuffle }, "New shuffle"),
        h("button", { type: "button", class: "btn btn-small", "data-try": "seed",
          onclick: () => openSweep([{ label: "Shuffle", target: { setup: "seed" }, value: seed, unit: "", from: seed, to: seed + 4 }], "the shuffle") }, "Try values…"))));
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
      h("legend", { text: "Floor spread" }),
      h("p", { class: "hint", text: "Run the test with the ball in front of the robot at each place of a grid, to see which ones it picks up. Tick the axes to spread it over." }),
      h("table", { class: "test-mounts test-floor" },
        h("thead", {}, h("tr", {}, h("th", { text: "Axis" }), h("th", { text: "from" }), h("th", { text: "to" }), h("th", { text: "steps" }))),
        h("tbody", {}, ...FLOOR_AXES.map(([axis, label]) => h("tr", { "data-axis": axis },
          h("th", { scope: "row" }, h("label", { class: "test-hook", for: `test-spread-${axis}-on` },
            h("input", { type: "checkbox", id: `test-spread-${axis}-on`, checked: draft[axis].on, onchange: floorChanged }),
            h("span", { text: label }))),
          h("td", {}, box(axis, "from", `${label} from`)), h("td", {}, box(axis, "to", `${label} to`)),
          h("td", {}, box(axis, "steps", `${label} steps`)))))),
      h("div", { class: "row" }, h("span", { id: "test-spread-count", class: "test-spread-count", role: "status" }),
        h("button", { type: "button", id: "test-spread-run", class: "btn btn-small", onclick: runSpread }, "Run the spread"))));
  }
  const left = leftovers(pal, setup);
  if (left.length) {
    parts.push(h("fieldset", { class: "test-setup-leftover" },
      h("legend", { text: "Settings for another field" }),
      h("p", { class: "hint", text: "The test has settings this field can't use (they were made on another field). Clear them to run the test here." }),
      ...left.map(([key, words, clear]) => h("div", { class: "row", "data-leftover": key },
        h("span", { text: words }),
        h("button", { type: "button", class: "btn btn-small", "aria-label": `Clear: ${words}`,
          onclick: () => { const next = setupOf(); clear(next); setTest({ setup: next }); } }, "Clear")))));
  }
  if (s.station_starter && !feeding) {
    parts.push(h("fieldset", { class: "test-setup-starter" },
      h("legend", { text: "Station blocks" }),
      h("p", { class: "hint", text: "Start feeding, wait until 5 pieces are in the hopper, stop feeding: added at the end of the test." }),
      h("button", { type: "button", id: "test-add-station-blocks", class: "btn btn-small", onclick: addStationBlocks }, "Add the station blocks")));
  }
  if (c) {
    const [f, l, u] = com(c);
    const send = (key, v) => robotEdit(() => ({ op: "params", name: c.name, params: { [key]: v } }));
    const comBox = (i, word, v) => numberBox(`test-com-${word}`, v, `Center of mass ${word} (m)`, (x) => robotEdit((r) => {
      const now = partNamed(r, c.name);
      if (!now) return null;
      const off = com(now); off[i] = x;
      return { op: "params", name: c.name, params: { com_offset: off } };
    }));
    parts.push(h("fieldset", { class: "test-setup-robot" },
      h("legend", { text: "The robot" }),
      h("p", { class: "hint", text: "Weight: everything that isn't one of the robot's parts (frame, battery, bumpers). "
        + "The Robot tab's Undo takes changes back." }),
      h("div", { class: "test-setup-grid" },
        h("label", { for: "test-mass", text: "Weight (kg)" }),
        h("div", { class: "row" }, numberBox("test-mass", mass(c), "Weight (kg)", (v) => send("mass", v)),
          h("button", { type: "button", class: "btn btn-small", "data-try": "mass", disabled: ro,
            onclick: () => openSweep([{ label: "Weight", target: { robot: "mass" }, value: mass(c), unit: "kg" }], "the robot's weight") }, "Try values…")),
        h("span", { class: "test-setup-label", text: "Center of mass (m)" }),
        h("div", { class: "row test-com" },
          h("label", { for: "test-com-forward", text: "forward" }), comBox(0, "forward", f),
          h("label", { for: "test-com-left", text: "left" }), comBox(1, "left", l),
          h("label", { for: "test-com-up", text: "up" }), comBox(2, "up", u),
          h("button", { type: "button", class: "btn btn-small", "data-try": "com", disabled: ro,
            onclick: () => openSweep([["forward", 0], ["left", 1], ["up", 2]].map(([w, i]) => ({ label: `Center of mass ${w}`,
              target: { robot: `com_${w}` }, value: com(c)[i], unit: "m" })), "the robot's center of mass") }, "Try values…")))));
  }
  const ms = mounted();
  if (ms.length) {
    parts.push(h("fieldset", { class: "test-setup-mounts" },
      h("legend", { text: "Where the parts are mounted" }),
      h("p", { class: "hint", text: "How far each part is moved from where it attaches: forward (x), left (y), up (z) in meters, turned (yaw) in degrees." }),
      h("table", { class: "test-mounts" },
        h("thead", {}, h("tr", {}, h("th", { text: "Part" }), ...AXES.map(([a]) => h("th", { text: a === "yaw" ? "yaw (°)" : `${a} (m)` })), h("th", {}))),
        h("tbody", {}, ...ms.map((p) => h("tr", { "data-part": p.name },
          h("th", { scope: "row", title: `on ${p.attach}`, text: p.name }),
          ...AXES.map(([a, i, key, word]) => h("td", {}, numberBox(`test-mount-${p.name}-${a}`, p[key][i],
            `${p.name} ${word} (${a === "yaw" ? "°" : "m"})`, (v) => robotEdit((r) => {
            const now = partNamed(r, p.name);
            if (!now) return null;
            const pos = [...now.pos].map(Number), rot = [...now.rot].map(Number);
            (key === "pos" ? pos : rot)[i] = v;
            return { op: "attach", name: p.name, attach: now.attach, pos, rot };
          })))),
          h("td", {}, h("button", { type: "button", class: "btn btn-small", "data-try": `mount-${p.name}`, disabled: ro,
            onclick: () => openSweep(AXES.map(([a, i, key, word]) => ({ label: `${word} (${a})`, target: { mount: p.name, axis: a },
              value: Number(p[key][i]), unit: a === "yaw" ? "°" : "m" })), `where ${p.name} is mounted`) }, "Try values…"))))))));
  }
  const note = !t.robot ? h("p", { class: "hint", text: "Loading the robot…" })
    : t.robot.read_only ? h("p", { class: "hint", text: `The robot file can't be changed here: ${t.robot.read_only_reason}` }) : null;
  body.replaceChildren(...parts, note || "", h("p", { id: "test-setup-error", class: "error", role: "alert", hidden: true }));
  if (ro) for (const x of body.querySelectorAll(".test-setup-robot input.test-num, .test-setup-mounts input.test-num")) x.disabled = true;
  if (pal.floor_piece) renderFloorCount(floorSpread(t.floor));
  t.setupKey = key;
  renderSetupError();
  if (focused && document.getElementById(focused)) document.getElementById(focused).focus();
}

// -- sweeps -----------------------------------------------------------------------------------------------------------
function registerTryValues() {
  const B = window.Blockly;
  const reg = B.ContextMenuRegistry.registry;
  if (reg.getItem("scrimmage-try-values")) return;
  reg.register({
    id: "scrimmage-try-values",
    scopeType: B.ContextMenuRegistry.ScopeType.BLOCK,
    weight: -1,
    displayText: () => "Try values…",
    preconditionFn: (scope) => {
      const b = scope.block;
      if (!sweepNumber(b) || !placeOf(t.ws).has(b.id)) return "hidden";
      return t.state && !t.state.running ? "enabled" : "disabled";
    },
    callback: (scope) => tryBlock(scope.block.id),
  });
}

function tryBlock(id) {
  const b = t.ws && t.ws.getBlockById(id);
  const n = sweepNumber(b);
  if (!n || !placeOf(t.ws).has(id)) return;
  openSweep([{ label: n.key, target: { key: n.key }, block: id, value: Number(b.getFieldValue(n.field)), unit: n.unit }],
    `“${b.toString()}”`);
}

function openSweep(choices, what) {
  const d = $("#test-sweep-dialog");
  t.sweepFor = choices;
  $("#test-sweep-for").textContent = `Run the test once for each value of ${what}, to see which works best.`;
  const which = $("#test-sweep-which");
  which.replaceChildren(...choices.map((c, i) => h("option", { value: String(i), text: `${c.label} (now ${fmt(c.value)}${c.unit ? ` ${c.unit}` : ""})` })));
  $("#test-sweep-which-field").hidden = choices.length < 2;
  const fill = () => {
    const c = choices[Number(which.value) || 0];
    const span = c.unit === "°" ? 45 : Math.abs(c.value) > 1e-9 ? Math.abs(c.value) / 2 : 1;
    $("#test-sweep-from").value = fmt(c.from ?? c.value - span);
    $("#test-sweep-to").value = fmt(c.to ?? c.value + span);
  };
  which.onchange = fill;
  fill();
  $("#test-sweep-steps").value = "5";
  $("#test-sweep-list").value = "";
  showSweepErrors([]);
  d.showModal();
  $("#test-sweep-from").focus();
}

function showSweepErrors(list) {
  const ul = $("#test-sweep-errors");
  ul.replaceChildren(...list.map((m) => h("li", { text: m })));
  ul.hidden = !list.length;
}

// The values the form asks for: [values] or a sentence saying what is wrong.
function sweepValues(from, to, steps, list) {
  if (list.trim()) {
    const parts = list.split(/[\s,;]+/).filter(Boolean);
    const vs = parts.map(Number);
    const bad = parts.find((x, i) => !isFinite(vs[i]));
    if (bad !== undefined) return `“${bad}” isn't a number: type numbers with commas between them`;
    if (vs.length > MAX_SWEEP) return `Try at most ${MAX_SWEEP} values at a time (that's ${vs.length})`;
    return vs;
  }
  const a = Number(from), b = Number(to), n = Number(steps);
  if (from.trim() === "" || to.trim() === "" || !isFinite(a) || !isFinite(b)) return "Type a number in From and To (or type the values)";
  if (!Number.isInteger(n) || n < 1 || n > MAX_SWEEP) return `Steps is a whole number from 1 to ${MAX_SWEEP}`;
  if (n === 1) return [a];
  return Array.from({ length: n }, (_, k) => +(a + ((b - a) * k) / (n - 1)).toPrecision(6));
}

async function submitSweep(e) {
  e.preventDefault();
  const choices = t.sweepFor || [];
  const c = choices[Number($("#test-sweep-which").value) || 0];
  if (!c) return;
  const values = sweepValues($("#test-sweep-from").value, $("#test-sweep-to").value, $("#test-sweep-steps").value, $("#test-sweep-list").value);
  if (typeof values === "string") { showSweepErrors([values]); return; }
  let target = c.target;
  await flush();      // a sweep runs the saved test, as Run does: only the test on screen, with nothing to fix
  if (t.saveError || t.local.length || !fits()) {
    check();
    showSweepErrors(["Fix the problems first (they are listed over Run): the test can't run like this yet."]);
    return;
  }
  if (c.block) {
    const place = placeOf(t.ws).get(c.block);
    if (!place) { showSweepErrors(["That block isn't in the test any more."]); return; }
    target = { block: place, key: c.target.key };
  }
  $("#test-sweep-go").disabled = true;
  const sent = c.scale ? values.map((v) => +(v * c.scale).toPrecision(6)) : values;    // the file's unit
  const { status, data } = await api("POST", "/api/test/sweep", { target, values: sent });
  $("#test-sweep-go").disabled = false;
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
    showSweepErrors([`Can't try them: ${why}`]);
    return;
  }
  $("#test-sweep-dialog").close();
  await refresh();
}

// -- boot -------------------------------------------------------------------------------------------------------------
export async function initTest() {
  window.__scrimmageTest = {
    get workspace() { return t.ws; },
    get state() { return t.state; },
    toJSON: () => workspaceToTest(t.ws),
    get saved() { return flush(); },
    get replay() { return replay(); },
    get sweep() { return sweepShown(); },
    get spread() { return spreadShown(); },
    get robot() { return t.robot; },
    get sweepFor() { return t.sweepFor; },
    trySweep: (id) => tryBlock(id),
  };
  registerTab("test", {
    renderTop,
    undo() { if (t.ws) t.ws.undo(false); },
    redo() { if (t.ws) t.ws.undo(true); },
    onShow() { if (t.ws) { window.Blockly.svgResize(t.ws); fitHeads(); } render(); loadRobot(); },
  });
  $("#test-run").addEventListener("click", run);
  $("#test-setup").addEventListener("toggle", () => { if ($("#test-setup").open) loadRobot(); renderSetup(); });
  const dlg = $("#test-sweep-dialog");
  dlg.querySelector("form").addEventListener("submit", submitSweep);
  for (const b of dlg.querySelectorAll("[data-close]")) b.addEventListener("click", () => dlg.close());
  $("#test-stop").addEventListener("click", stop);
  initRunPanel();
  while (!t.state) {
    await refresh();
    if (!t.state) await new Promise((r) => setTimeout(r, 2000));
  }
  await loadScript("vendor/blockly.min.js");
  inject();
  show(t.state.test);
  fitHeads();
  render();
  loadRobot();
  let said = null;      // what the Part tab's Test it did before the page reloaded here
  try { said = sessionStorage.getItem("scrimmage-test-notice"); sessionStorage.removeItem("scrimmage-test-notice"); } catch { said = null; }
  if (said) notice(said, "info");
  if (currentTab() !== "test" && new URLSearchParams(location.hash.slice(1)).has("test")) setTab("test");
  pollLoop();
}
