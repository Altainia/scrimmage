// The Test tab: a block test (`<name>.test.toml`) in Blockly. See /api/test/* in the docstring of
// scrimmage/assembler/server.py for the API contract, and blocks_defs.js for the blocks.
//
//   Left       the palette: Blockly's flyout (Move, Wait, Repeat, Check), filled from the robot's own names
//   Middle     the test: Blockly's workspace; the stack with the most blocks is the test
//   Right      the run: the problems that keep it from running, Run (and Stop while it runs), and the runs
//              (test_run.js: the shown run's outcome, replay and chart, and the kept runs)
//   Status     saved or not, how many blocks
//
// Every change is saved (500 ms after the last one) through /api/test/save with the state's version; a 409 (the file
// changed meanwhile) shows the saved version again and says so. Problems from the state show as a warning on their
// block and in the list above Run, with this page's own (a save the server refused, an empty condition socket, blocks
// not joined to the test); Run is off while there are any, and runs only the test as it is on screen. Blockly's theme follows the page's (light or dark),
// its colors read from app.css's tokens.
//
// Test hook: window.__scrimmageTest {workspace, state, toJSON(), saved: a promise for the save under way (or
// resolved), replay: test_run.js's replay()}.

import { $, h, api, notice, registerTab, setTab, showPath, currentTab, store } from "./app.js";
import { initRunPanel, update as updateRuns, replay } from "./test_run.js";
import { defineBlocks, toolbox, testToWorkspace, workspaceToTest, placeOf, incomplete, looseStacks, setUsed, STYLES }
  from "./blocks_defs.js";

const SAVE_DELAY = 500;     // ms after the last change
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
    if (key !== t.toolboxKey) { t.toolboxKey = key; t.ws.updateToolbox(toolbox(s.palette)); fitHeads(); }
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

// -- boot -------------------------------------------------------------------------------------------------------------
export async function initTest() {
  window.__scrimmageTest = {
    get workspace() { return t.ws; },
    get state() { return t.state; },
    toJSON: () => workspaceToTest(t.ws),
    get saved() { return flush(); },
    get replay() { return replay(); },
  };
  registerTab("test", {
    renderTop,
    undo() { if (t.ws) t.ws.undo(false); },
    redo() { if (t.ws) t.ws.undo(true); },
    onShow() { if (t.ws) { window.Blockly.svgResize(t.ws); fitHeads(); } render(); },
  });
  $("#test-run").addEventListener("click", run);
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
  if (currentTab() !== "test" && new URLSearchParams(location.hash.slice(1)).has("test")) setTab("test");
  pollLoop();
}
