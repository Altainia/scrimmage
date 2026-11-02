// The Test tab: a block test (`<name>.test.toml`) in Blockly. See /api/test/* in the docstring of
// scrimmage/assembler/server.py for the API contract, and blocks_defs.js for the blocks.
//
//   Left       the palette: Blockly's flyout (Move, Wait, Repeat, Check), filled from the robot's own names
//   Middle     the test: Blockly's workspace; the stack with the most blocks is the test
//   Right      the run: the problems that keep it from running, Run (and Stop while it runs), and the runs
//              (test_run.js: the shown run's outcome, replay and chart, and the kept runs)
//   Status     saved or not, how many blocks
//   Setup      (folded away over the blocks) the test's hooks (saved in the test), and the robot's weight and center of
//              mass (when its root part is the stock chassis) and where each part is mounted, which change the robot
//              file through /api/robot/edit, so the Robot tab's Undo takes them back
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
// resolved), replay: test_run.js's replay(), sweep: test_run.js's sweepShown(), robot: the robot state Setup shows,
// trySweep(blockId): opens the sweep form for that block's number}.

import { $, h, api, notice, registerTab, setTab, showPath, currentTab, store } from "./app.js";
import { initRunPanel, update as updateRuns, replay, sweepShown } from "./test_run.js";
import { defineBlocks, toolbox, testToWorkspace, workspaceToTest, placeOf, incomplete, looseStacks, setUsed, STYLES,
  headOf, setHead, sweepNumber } from "./blocks_defs.js";

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
  sweepFor: null,     // the sweep form's numbers: [{label, target, value, unit, block}]
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
const mounted = () => (t.robot ? (t.robot.parts || []).filter((p) => p.attach
  && !(p.stock && t.stock && t.stock[p.stock] && t.stock[p.stock].group === "Drivetrain")) : []);
const AXES = [["x", 0, "pos", "forward"], ["y", 1, "pos", "left"], ["z", 2, "pos", "up"], ["yaw", 2, "rot", "turned"]];
const fmt = (v) => String(+Number(v).toPrecision(6));

function numberBox(id, value, label, commit) {
  const el = h("input", { id, class: "test-num", inputmode: "decimal", autocomplete: "off", "aria-label": label });
  el.value = el.dataset.orig = fmt(value);
  const send = () => {
    if (el.value.trim() === el.dataset.orig) return;
    const v = Number(el.value.trim());
    if (el.value.trim() === "" || !isFinite(v)) { t.setupError = `${label}: type a number`; renderSetupError(); el.value = el.dataset.orig; return; }
    el.dataset.orig = el.value.trim();
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
  setHead(t.ws, { hooks: [...hooks].sort() });
  schedule();
  renderSetup();
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
  const mine = new Set(headOf(t.ws).hooks || []);
  const counted = new Set((pal.hooks || []).filter((x) => !mine.has(x)));     // the stock climber's: always hooks
  const c = chassis();
  const sum = [`hooks: ${[...mine, ...counted].length ? [...counted, ...mine].join(", ") : "none"}`];
  if (c) sum.push(`robot ${fmt(mass(c))} kg`);
  $("#test-setup-sum").textContent = `· ${sum.join(" · ")}`;
  if (!open) { t.setupKey = null; return; }      // filled in when it is opened
  const ro = !t.robot || !!t.robot.read_only;
  // drawn again only when something it shows changed (states come four times a second while a run goes)
  const key = canon([pal.bodies || [], [...mine], [...counted], t.robot ? [t.robot.version, t.robot.parts, ro] : null,
    !!t.stock, t.setupError]);
  if (key === t.setupKey) return;
  const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
  if (focused && document.activeElement.matches("input.test-num") && document.activeElement.value !== document.activeElement.dataset.orig) return;

  const bodies = [...new Set([...(pal.bodies || []), ...mine])].sort();
  const hooks = h("fieldset", { class: "test-setup-hooks" },
    h("legend", { text: "Hooks" }),
    h("p", { class: "hint", text: "Tick the bodies that grab a bar. A stock climber's hooks always count." }),
    h("div", { class: "test-hook-list" }, ...bodies.map((b, i) => {
      const id = `test-hook-${i}`;
      const missing = !(pal.bodies || []).includes(b);
      const box = h("input", { type: "checkbox", id, "data-body": b, checked: mine.has(b) || counted.has(b), disabled: counted.has(b),
        onchange: (e) => { const next = new Set(mine); if (e.target.checked) next.add(b); else next.delete(b); setHooks(next); } });
      return h("label", { class: "test-hook", for: id }, box, h("span", { text: b + (missing ? " (not on the robot)" : "") }));
    })));

  const parts = [];
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
      h("p", { class: "hint", text: "The weight of everything on the robot that isn't one of its parts: the frame, the battery, "
        + "the bumpers and the rest. These change the robot file; the Robot tab's Undo takes them back." }),
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
  body.replaceChildren(hooks, ...parts, note || "", h("p", { id: "test-setup-error", class: "error", role: "alert", hidden: true }));
  if (ro) for (const x of body.querySelectorAll("input.test-num")) x.disabled = true;
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
  which.replaceChildren(...choices.map((c, i) => h("option", { value: String(i), text: `${c.label} (now ${fmt(c.value)} ${c.unit})` })));
  $("#test-sweep-which-field").hidden = choices.length < 2;
  const fill = () => {
    const c = choices[Number(which.value) || 0];
    const span = c.unit === "°" ? 45 : Math.abs(c.value) > 1e-9 ? Math.abs(c.value) / 2 : 1;
    $("#test-sweep-from").value = fmt(c.value - span);
    $("#test-sweep-to").value = fmt(c.value + span);
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
  if (c.block) {
    await flush();      // the sweep runs the saved test: it must be the one on screen
    const place = placeOf(t.ws).get(c.block);
    if (!place || t.saveError || t.local.some((p) => p.blocking) || !fits()) {
      showSweepErrors(["Fix the test first: it isn't saved the way it shows."]);
      return;
    }
    target = { block: place, key: c.target.key };
  }
  $("#test-sweep-go").disabled = true;
  const { status, data } = await api("POST", "/api/test/sweep", { target, values });
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
    get robot() { return t.robot; },
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
  if (currentTab() !== "test" && new URLSearchParams(location.hash.slice(1)).has("test")) setTab("test");
  pollLoop();
}
