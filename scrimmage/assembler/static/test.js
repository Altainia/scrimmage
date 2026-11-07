// The Test tab: a block test (`<name>.test.toml`) in Blockly. See /api/test/* in the docstring of
// scrimmage/assembler/server.py for the API contract, and blocks_defs.js for the blocks.
//
//   Left       the palette: Blockly's flyout (Move, Wait, Repeat, Check), filled from the robot's own names
//   Middle     the test: Blockly's workspace; the stack with the most blocks is the test
//   Right      the run: the problems that keep it from running, the hints that don't, Run (and Stop while it runs),
//              and the runs (test_run.js: the shown run's outcome, replay and chart, and the kept runs)
//   Status     saved or not, how many blocks
//   Setup      (folded away over the blocks) the field and its start, the robot's weight and center of mass (when its
//              root part is the stock chassis) and where each part is mounted, which change the robot file through
//              /api/robot/edit, so the Robot tab's Undo takes them back; the grip material of the pads (a grabber's,
//              or a body ticked as where a piece should end up), which /api/test/grip writes in that part folder's
//              own edits at once, as the Part tab's Grip row does; and the test's own (saved in the test): where a
//              piece should end up (the hopper: starter_hopper's preselection until changed) and its hooks, and its
//              [setup]: the piece (on the intake and station fields), each station's settings, the shuffle (seed),
//              the floor spread (Run the spread: /api/test/spread) and the partner robot (where it starts, which way
//              it faces, its brakes). On a field with a station and a test with no feed block, "Add the station
//              blocks" appends station_starter.
//
// "Try values…" (a block's context menu, for its number; or beside a number in Setup) opens the sweep form: from, to
// and steps, or typed values, at most 12; /api/test/sweep runs them and test_run_sweep.js shows the table and its chart.
// "Try materials…" (beside a grip) opens it over ticked materials instead, for the whole part (ruling P28: one pad of
// two shows no difference while the other holds alone) or the one body.
// "Block settings…" (a block's context menu) sets the keys a block has no box for: a move's, wait until's, feed
// one's, drive's or turn's timeout, a drive's or turn's volts, a repeat until's max (empty: the run's default).
//
// Every change is saved (500 ms after the last one) through /api/test/save with the state's version; a 409 (the file
// changed meanwhile) shows the saved version again and says so. Problems from the state show as a warning on their
// block and in the list above Run, with this page's own (a save the server refused, an empty condition socket, blocks
// not joined to the test); Run is off while there are any, and runs only the test as it is on screen. Blockly's
// theme follows the page's (theme.js: light or dark), its colors read from app.css's tokens.
//
// Test hook: window.__scrimmageTest {workspace, state, toJSON(), saved: a promise for the save under way (or
// resolved), replay: test_run.js's replay(), sweep: test_run.js's sweepShown(), spread: its spreadShown(), robot: the
// robot state Setup shows, sweepFor: the sweep form's numbers, trySweep(blockId): opens the sweep form for that
// block's number}.
//
// The state and its small helpers are in test_state.js, Setup in test_setup.js (its floor spread, station boxes,
// leftovers, grip and hints in test_floor.js), the Block settings and sweep dialogs in test_dialogs.js.

import { $, h, api, notice, registerTab, setTab, showPath, currentTab, store, convertTyped } from "./app.js";
import { initRunPanel, update as updateRuns, replay, sweepShown, spreadShown } from "./test_run.js";
import { defineBlocks, toolbox, testToWorkspace, workspaceToTest, placeOf, incomplete, looseStacks, setUsed, STYLES,
  headOf, showUnits, relabel } from "./blocks_defs.js";
import { units } from "./units.js";
import { i18n, t, tn } from "./i18n.js";
import { theme as pageTheme } from "./theme.js";
import { answered, T, canon } from "./test_state.js";
import { loadRobot, setTest, renderSetup } from "./test_setup.js";
import { floorUnits } from "./test_floor.js";
import { registerSettings, openSettings, submitSettings, registerTryValues, tryBlock, submitSweep } from "./test_dialogs.js";

const SAVE_DELAY = 500;     // ms after the last change
const stale = () => t("The test changed on disk; showing the saved version.");

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

// The Blockly theme for the page's theme (theme.js), from app.css's tokens (read when that theme is first shown).
const themes = {};
let followsTheme = null;      // theme.js's onChange, once for every workspace inject() makes
function theme() {
  const name = pageTheme.isDark() ? "scrimmage-dark" : "scrimmage-light";
  if (!themes[name]) themes[name] = makeTheme(name);
  return themes[name];
}

function makeTheme(name) {
  const B = window.Blockly;
  const v = (token) => pageTheme.color(token);
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
    fontStyle: { family: getComputedStyle(document.documentElement).getPropertyValue("--font").trim(), weight: "600", size: 11 },
  });
}

function inject() {
  const B = window.Blockly;
  defineBlocks(() => (T.state ? T.state.palette : null));
  const box = $("#test-blockly");
  box.replaceChildren();
  T.ws = B.inject(box, {
    toolbox: toolbox(T.state.palette),
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
  T.toolboxKey = toolboxKey(T.state.palette);
  T.shownUnits = units.system;
  T.ws.addChangeListener(onChange);
  registerTryValues();
  registerSettings();
  if (!followsTheme) followsTheme = pageTheme.onChange(() => { if (T.ws) T.ws.setTheme(theme()); });
  new ResizeObserver(() => { B.svgResize(T.ws); fitHeads(); }).observe(box);
}

// The column titles over the flyout and the workspace.
function fitHeads() {
  const fl = T.ws && T.ws.getFlyout();
  if (fl) $(".test-heads").style.setProperty("--flyout-w", `${Math.round(fl.getWidth())}px`);
}

function show(test) {
  T.saveError = null;
  T.hopperSet = false;
  T.floor = null;
  testToWorkspace(test, T.ws);
  T.synced = canon(test);
  T.base = T.state.version;
  check();
}

// The flyout's blocks show the palette's names and the Units setting's numbers.
const toolboxKey = (palette) => canon([palette, units.system, i18n.lang]);

// A Units change: the blocks, the flyout and Setup show their numbers in the new units; the test file is unchanged.
function unitsChanged() {
  if (T.ws) {
    showUnits(T.ws, T.shownUnits || units.system);
    T.toolboxKey = toolboxKey(T.state.palette);
    T.ws.updateToolbox(toolbox(T.state.palette));
    fitHeads();
  }
  T.shownUnits = units.system;
  floorUnits();
  // a Setup box half typed: what it holds is converted to the new units, and kept as Setup is drawn again
  const act = document.activeElement;
  if (act && act.matches && act.matches("input.test-num[data-units-kind]") && act.value !== act.dataset.orig) convertTyped(act);
  T.setupKey = null;
  renderSetup({ keepTyping: true });
}

// A Language change (spec L7): the blocks are made again in the new words (blocks_defs.js relabel: an open editor is
// committed first, and nothing is saved, as the test is unchanged), and so are the flyout and Setup; the state is
// asked for again, for the server's words (problems, hints) in the new language.
function languageChanged() {
  if (T.ws) {
    relabel(T.ws);
    T.toolboxKey = toolboxKey(T.state.palette);
    T.ws.updateToolbox(toolbox(T.state.palette));
    fitHeads();
    check();
  }
  T.setupKey = null;
  render();
  refresh();
}

// -- saving -----------------------------------------------------------------------------------------------------------
function onChange(e) {
  const B = window.Blockly;
  if (e.isUiEvent) return;
  if (![B.Events.BLOCK_CHANGE, B.Events.BLOCK_CREATE, B.Events.BLOCK_DELETE, B.Events.BLOCK_MOVE].includes(e.type)) return;
  check();
  // Blockly reports the blocks testToWorkspace made a moment later: those are no change to save
  if (T.timer || T.saving || canon(workspaceToTest(T.ws)) !== T.synced) schedule();
  else renderStatus();
  renderTop();
}

export function schedule() {
  clearTimeout(T.timer);
  T.timer = setTimeout(() => { T.timer = null; T.saving = save().finally(() => { T.saving = null; }); }, SAVE_DELAY);
  renderStatus();
}

// Save now (a pending change), and wait for any save under way.
export async function flush() {
  if (T.timer) {
    clearTimeout(T.timer);
    T.timer = null;
    T.saving = save().finally(() => { T.saving = null; });
  }
  while (T.saving) await T.saving;
}

async function save() {
  if (T.local.some((p) => p.blocking)) { renderStatus(); return; }   // can't be written like this yet
  const test = workspaceToTest(T.ws);
  const key = canon(test);
  if (key === T.synced) { T.saveError = null; renderStatus(); return; }
  renderStatus(true);
  const { status, data } = await api("POST", "/api/test/save", { test, version: T.base });
  if (status === 200 && data.ok) {
    T.synced = key;
    T.base = data.version;
    T.saveError = null;
    await refresh();
  } else if (status === 409) {
    T.saveError = null;
    notice(stale(), "warn", "test-stale");
    await refresh(true);
  } else {
    T.saveError = refused((data.errors && data.errors[0]) || { message: answered(status) });
    check();
  }
  renderStatus();
}

// A refused save that names a bad block ("block 2 inside block 3 (repeat): seconds can't be negative") sends its place
// (0-based, outermost first) and the problem without the place's words: those, else the whole message.
function refused(err) {
  return Array.isArray(err.place) ? { message: err.problem || err.message, place: err.place } : { message: err.message, place: null };
}

// -- state ------------------------------------------------------------------------------------------------------------
export async function refresh(reload = false) {
  const { status, data } = await api("GET", "/api/test/state");
  if (status === 200) apply(data, reload);
}

function apply(s, reload = false) {
  const prev = T.state;
  if (prev && !reload && s.version < prev.version) return;   // an older answer overtaken by a newer one
  T.state = s;
  if (T.ws) {
    setUsed([s.test, workspaceToTest(T.ws)]);
    const key = toolboxKey(s.palette);
    if (key !== T.toolboxKey) {
      T.toolboxKey = key;
      T.ws.updateToolbox(toolbox(s.palette));
      fitHeads();
      if ($("#test-setup").open) loadRobot();     // the robot changed: its parts may have too
    }
    const changed = canon(s.test) !== T.synced;
    // a change this page didn't make (an outside edit, another tab), while nothing of ours is waiting to be saved
    if (reload || (changed && !T.timer && !T.saving)) {
      if (changed && !reload && prev) notice(t("The test changed outside this page (the file was edited, or another tab), so it was reloaded."), "info");
      const loose = looseStacks(T.ws).map((b) => b.toString());
      show(s.test);
      if (loose.length) notice(t("Blocks not joined to your test were left out: {blocks}", { blocks: loose.join("; ") }), "warn");
    } else {
      if (!changed) T.base = s.version;
      check();
    }
  }
  render();
}

async function pollLoop() {
  for (;;) {
    const since = T.state ? `?since=${T.state.version}` : "";
    const { status, data } = await api("GET", "/api/test/state" + since);
    if (status === 200) apply(data);
    else if (status === 404) return;     // the test was closed (another file opened); the page reloads
    else await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

// -- problems ---------------------------------------------------------------------------------------------------------
// Server problems (at their place) and this page's own (incomplete blocks), as warnings on the blocks.
export function check() {
  if (!T.ws) return;
  T.local = incomplete(T.ws);
  // the state's places are the saved test's: they fit the workspace while it shows that test (else the last ones stay)
  if (T.state && fits()) {
    const ids = placeIds();
    T.serverWarn = new Map();
    for (const p of T.state.problems || []) {
      const id = p.place.length ? ids.get(p.place.join("/")) : null;
      if (id) T.serverWarn.set(id, [...(T.serverWarn.get(id) || []), p.message]);
    }
  }
  const byBlock = new Map(T.serverWarn);
  const refusedAt = saveErrorBlock();
  if (refusedAt) byBlock.set(refusedAt, [...(byBlock.get(refusedAt) || []), T.saveError.message]);
  for (const p of T.local) byBlock.set(p.block.id, [...(byBlock.get(p.block.id) || []), p.message]);
  for (const b of T.ws.getAllBlocks(false)) {
    const msgs = byBlock.get(b.id);
    const text = msgs ? msgs.join("\n") : null;
    if ((b.warningShown || null) !== text) { b.setWarningText(text); b.warningShown = text; }
  }
  renderProblems();
}

export const fits = () => !!T.ws && canon(workspaceToTest(T.ws)) === T.synced;
const placeIds = () => new Map([...placeOf(T.ws)].map(([id, place]) => [place.join("/"), id]));
// the block a refused save names (its place is the workspace's: that is the test it tried to save)
const saveErrorBlock = () => (T.saveError && T.saveError.place && T.ws ? placeIds().get(T.saveError.place.join("/")) || null : null);

function problemList() {
  const out = [];
  const s = T.state;
  if (!s) return out;
  if (s.file_error) out.push({ message: t("The test file can't be read: {why}", { why: s.file_error }) });
  const ids = fits() ? placeIds() : new Map();
  for (const p of s.problems || []) out.push({ message: p.message, id: p.place.length ? ids.get(p.place.join("/")) : null });
  if (T.saveError) out.push({ message: t("Not saved: {why}", { why: T.saveError.message }), id: saveErrorBlock() });
  for (const p of T.local) out.push({ message: p.message, id: p.block.id });
  return out;
}

function goTo(id) {
  const b = T.ws && T.ws.getBlockById(id);
  if (!b) return;
  T.ws.centerOnBlock(id);
  b.select();
}

function renderProblems() {
  const list = problemList();
  const ul = $("#test-problems");
  ul.replaceChildren(...list.map((p) => {
    const b = p.id && T.ws ? T.ws.getBlockById(p.id) : null;
    return h("li", { class: "test-problem" },
      b ? h("button", { type: "button", class: "linklike", onclick: () => goTo(p.id), text: b.toString() }) : null,
      h("span", { text: p.message }));
  }));
  $("#test-problems-wrap").hidden = !list.length;
  // hints (the state's): what the test can run with but should know; they never keep Run from running
  const hints = (T.state && T.state.hints) || [];
  $("#test-hints").replaceChildren(...hints.map((p) => h("li", { text: p.message })));
  $("#test-hints-wrap").hidden = !hints.length;
  const s = T.state;
  $("#test-run").disabled = !s || !!list.length || !!s.running;
  $("#test-run").title = list.length ? t("Fix the problems first") : "";
}

// -- rendering --------------------------------------------------------------------------------------------------------
export function renderTop() {
  if (currentTab() !== "test" || !T.state) return;
  showPath(store.appPath || T.state.path, T.state.path);
  $("#undo").disabled = !T.ws || !T.ws.getUndoStack().length;
  $("#redo").disabled = !T.ws || !T.ws.getRedoStack().length;
}

export function renderStatus(busy = false) {
  const el = $("#test-saved");
  if (!T.state) return;
  const blocking = T.local.find((p) => p.blocking);
  if (busy || T.timer || T.saving) { el.textContent = t("Saving…"); el.className = "saved saved-busy"; }
  else if (T.saveError) { el.textContent = t("Not saved: {why}", { why: T.saveError.message }); el.className = "saved saved-bad"; }
  else if (blocking) { el.textContent = t("Not saved yet: a block needs a check plugged in"); el.className = "saved saved-warn"; }
  else { el.textContent = t("✓ All changes saved"); el.className = "saved saved-ok"; }
  const n = T.ws ? placeOf(T.ws).size : 0;   // every block of the test, the ones inside repeats too
  $("#test-count").textContent = T.ws ? tn("{count} block in the test", "{count} blocks in the test", n) : "";
}

function render() {
  const s = T.state;
  if (!s) return;
  const banner = $("#test-banner");
  banner.hidden = !s.file_error;
  banner.textContent = s.file_error ? t("The test file can't be read, so this is its last good version: {why}", { why: s.file_error }) : "";
  const running = $("#test-running");
  running.hidden = !s.running;
  running.textContent = s.running ? t("Running… {percent}%", { percent: Math.round(100 * (s.running.progress || 0)) }) : "";
  if (!s.running) T.stopping = false;
  $("#test-stop").hidden = !s.running;
  $("#test-stop").disabled = T.stopping;
  updateRuns(s);
  renderProblems();
  renderStatus();
  renderTop();
  renderSetup();
}

async function run() {
  await flush();
  // only the test on screen runs: not a file this page couldn't save
  if (T.saveError || T.local.length || !fits()) { check(); renderStatus(); return; }
  const { status, data } = await api("POST", "/api/test/run", {});
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || answered(status);
    notice(t("Can't run the test: {why}", { why }), "warn");
  }
  await refresh();
}

async function stop() {
  T.stopping = true;
  $("#test-stop").disabled = true;
  const { status, data } = await api("POST", "/api/test/cancel", {});
  if (status !== 200) {
    notice(t("Couldn't stop the run: {why}", { why: (data.errors && data.errors[0] && data.errors[0].message) || answered(status) }), "error");
    T.stopping = false;
    $("#test-stop").disabled = false;
  }
}

// -- boot -------------------------------------------------------------------------------------------------------------
export async function initTest() {
  window.__scrimmageTest = {
    get workspace() { return T.ws; },
    get state() { return T.state; },
    toJSON: () => workspaceToTest(T.ws),
    get saved() { return flush(); },
    get replay() { return replay(); },
    get sweep() { return sweepShown(); },
    get spread() { return spreadShown(); },
    get robot() { return T.robot; },
    get sweepFor() { return T.sweepFor; },
    trySweep: (id) => tryBlock(id),
    openSettings: (id) => openSettings(id),
  };
  registerTab("test", {
    renderTop,
    undo() { if (T.ws) T.ws.undo(false); },
    redo() { if (T.ws) T.ws.undo(true); },
    onShow() { if (T.ws) { window.Blockly.svgResize(T.ws); fitHeads(); } render(); loadRobot(); },
  });
  $("#test-run").addEventListener("click", run);
  $("#test-setup").addEventListener("toggle", () => { if ($("#test-setup").open) loadRobot(); renderSetup(); });
  const dlg = $("#test-sweep-dialog");
  dlg.querySelector("form").addEventListener("submit", submitSweep);
  for (const b of dlg.querySelectorAll("[data-close]")) b.addEventListener("click", () => dlg.close());
  const sdlg = $("#test-settings-dialog");
  sdlg.querySelector("form").addEventListener("submit", submitSettings);
  for (const b of sdlg.querySelectorAll("[data-close]")) b.addEventListener("click", () => sdlg.close());
  $("#test-stop").addEventListener("click", stop);
  initRunPanel({ chart: () => (T.ws ? headOf(T.ws).chart || null : null), setChart: (c) => setTest({ chart: c }) });
  units.onChange(unitsChanged);
  i18n.onChange(languageChanged);
  while (!T.state) {
    await refresh();
    if (!T.state) await new Promise((r) => setTimeout(r, 2000));
  }
  await loadScript("vendor/blockly.min.js");
  inject();
  show(T.state.test);
  fitHeads();
  render();
  loadRobot();
  let said = null;      // what the Part tab's Test it did before the page reloaded here
  try { said = sessionStorage.getItem("scrimmage-test-notice"); sessionStorage.removeItem("scrimmage-test-notice"); } catch { said = null; }
  if (said) notice(said, "info");
  if (currentTab() !== "test" && new URLSearchParams(location.hash.slice(1)).has("test")) setTab("test");
  pollLoop();
}
