// The Drive tab: drive the open robot live. Start / Stop the session (POST /api/live/start|stop), the Driver
// Station bar (mode, Enable / Disable, the match: station, FMS, match timer and game data, joystick ports 0-5 fed by the keyboard or browser gamepads), sim controls
// (Pause / Resume, Step, speed), the live 3D view with a HUD, and the readouts (pose, speed, battery, motors,
// joints, up to 4 plotted signals, console tail). See the docstring of scrimmage/assembler/server.py for the API
// and the stream protocol.
//
// Everything after Start goes over the stream (livestream.js): {"type": "cmd", cmd, ...} for the controls and
// {"type": "input", ports} every 20 ms while the socket is open. Frames with source 0 move the live view; state
// messages with source "live" drive the rest of the page. When the socket drops, the server disables and pauses
// the session; after reconnecting the page shows it paused and the student resumes it (no auto-resume).
//
// Poke tools (minifield.js: set the pose by dragging, move an object; Push; Reset) and Record / "Save as
// scenario…" (POST /api/live/save_recording, then /api/scenario/open and the Scenario tab) are commands too.
// Start asks (#discard-dialog) before it drops a recording that was never saved, and opens the robot code panel's
// start picker (code.js) first while nobody has picked where the robot starts.
// The stream is the page's one connection (sharedStream), shared with the run panel's Watch view.
//
// The Driver Station choice (#drive-ds-source, before Start; it starts from the config's [driver_station] real and is
// fixed while a session runs): with "The FRC Driver Station app" the real DS drives the robot. The bar then says so
// and shows what the robot reports (mode, enabled, station, FMS, match time); Mode, Enable / Disable, the Match
// inputs, the joystick ports, Pause / Step and Record are off, with tooltips saying why, and the page sends no
// joystick inputs or DS commands.
//
// Keys on the Drive tab: Enter enables (unless a button has focus: Enter presses it), Space always disables (even on a
// focused button); neither while typing in a text box or while a dialog is open.
// While the device check (code.js) has devices that don't match the model, a line under the banner says how many,
// with "see Robot code" (the panel's device table).
// Test hook: window.__scrimmage.live = {state, frames, lastFrame: {t, poses}, connected, plots, assignments}.

import { $, h, api, notice, registerTab, setTab, showPath, currentTab, store, showMeasure, readMeasure, measureError } from "./app.js";
import { units } from "./units.js";
import { sharedStream } from "./livestream.js";
import { LiveView } from "./live3d.js";
import { InputRouter, KEYBOARD, isEditable, isDrivingKey } from "./gamepad.js";
import { renderReadouts, Plot } from "./readouts.js";
import { MiniField } from "./minifield.js";

const PORTS = 6;
const INPUT_PERIOD = 20;          // ms between input messages (50 Hz)
const SIGNALS = 4;
const SHOWN_AXES = 6, SHOWN_BUTTONS = 10;
const MAX_AXES = 12, MAX_BUTTONS = 32, MAX_POVS = 12;   // what the server takes per port
const MODES = ["teleop", "auto", "test"];
const RTF_WINDOW = 1000;          // ms of wall time the real-time factor is measured over
const MODE_SETTLE = 1000;         // ms a mode sent from this page outranks an older chosen_mode from the server
const MATCH_CONTROLS = { station: "#drive-station", fms: "#drive-fms", timer: "#drive-timer", game_data: "#drive-game-data" };
// What is off while the FRC Driver Station app drives, and why (the tooltips); the server refuses these commands too.
const REAL_DS_WHY = "The FRC Driver Station app is driving: use it instead";
const REAL_DS_NO_PAUSE = "The FRC Driver Station runs the robot in real time, so it can't be paused";
const REAL_DS_NO_RECORD = "Recording doesn't work while the FRC Driver Station app is driving";
const REAL_DS_OFF = new Set(["mode", "enable", "disable", "match", "pause", "resume", "step", "record_start"]);
const REAL_DS_MODE_TEXT = { teleop: "Teleop enabled", auto: "Auto enabled", test: "Test enabled", disabled: "Disabled" };
const KEYS_HINT = "Enter enables, Space disables.";
const REAL_DS_KEYS_HINT = "Enable in the Driver Station app. There, Enter disables and Space is the E-stop.";
const MATCH_HINT = "Game data reaches the robot when it is enabled in Teleop.";
const REAL_DS_MATCH_HINT = "The Driver Station app sets the match. Its game data isn't shown here.";
const STATE_TEXT = {
  idle: "Not started", starting: "Starting the robot program…", running: "Running", paused: "Paused",
  stopped: "Stopped", error: "Error",
};

const drive = {
  started: false,       // the stream, view and input loop are up (on first show)
  stream: null,
  connected: false,
  view: null,           // LiveView, or null when WebGL isn't available
  router: null,
  state: null,          // the latest live state message (or /api/live/info's status before any)
  frames: 0,
  lastFrame: null,
  mode: "teleop",       // the chosen mode (the effective one is "disabled" while not enabled)
  modeSent: null,       // {mode, at}: a mode this page just sent; the server's chosen_mode catches up shortly
  matchSent: {},        // key -> {value, at}: a match setting this page just sent, likewise
  port0Default: true,   // port 0 still holds the keyboard it got at load (a gamepad connected later takes it)
  startProblem: null,   // why the robot can't be driven (/api/live/info's start_problem): Start is disabled
  startError: null,     // why the last Start press failed (busy, bad speed, no answer); Start stays usable
  notBuilt: false,      // it failed because the Java code hasn't built (start/not_built): the banner links Build now
  posting: false,       // a Start request is on its way
  startTaken: false,    // the server took a Start, and no state message since has said it is starting (or ended)
  starts: 0,            // Starts the server has taken (so a late answer about an earlier one is ignored)
  messages: 0,          // state messages received from the stream
  picks: new Array(SIGNALS).fill(""),
  sentPicks: "[]",
  plots: [],
  consoleSeq: null,
  rtf: [],              // [wall ms, sim t] samples for the real-time factor
  robotVersion: null,
  field: null,          // the poke panel's MiniField
  movers: null,         // {root, objects: [[name, mover index]]} from the live view
  saving: false,        // a save request is on its way
  realDsDefault: false, // the config's [driver_station] real (/api/live/info): where the Driver Station choice starts
  dsTouched: false,     // the student picked the Driver Station choice themselves
};

const isLive = (s) => !!s && (s.state === "running" || s.state === "paused");
const isActive = (s) => isLive(s) || (!!s && s.state === "starting");

function send(obj) {
  return drive.stream ? drive.stream.send(obj) : false;
}

function cmd(name, fields = {}) {
  if (!isLive(drive.state)) return false;
  if (realDs(drive.state) && REAL_DS_OFF.has(name)) return false;   // the FRC Driver Station app has these
  return send({ type: "cmd", cmd: name, ...fields });
}

// The FRC Driver Station app drives the session that is starting, running or paused: the server says so once the
// session runs (state.real_ds); while Start is on its way and while it starts, the choice it was started with (the
// server refuses inputs from the moment it takes the Start).
function realDs(s) {
  if (isLive(s)) return !!s.real_ds;
  return (isActive(s) || drive.posting || drive.startTaken) && $("#drive-ds-source").value === "real";
}

// A control's tooltip: `why` while it is off for a reason, else its own title from the page.
function setWhy(el, why) {
  if (el.dataset.title === undefined) el.dataset.title = el.getAttribute("title") || "";
  const t = why || el.dataset.title;
  if (t) el.title = t;
  else el.removeAttribute("title");
}

// The mini-field's pokes also go while the robot program starts: the session applies them once it runs.
function poke(name, fields) {
  if (!isActive(drive.state)) return false;
  return send({ type: "cmd", cmd: name, ...fields });
}

// -- inputs -------------------------------------------------------------------------------------------------
const clampAxis = (v) => Math.max(-1, Math.min(1, Number(v) || 0));
const cleanPov = (v) => (Number.isInteger(v) && v >= 0 && v <= 359 ? v : -1);

function cleanInputs(pi) {
  return {
    axes: pi.axes.slice(0, MAX_AXES).map(clampAxis),
    buttons: pi.buttons.slice(0, MAX_BUTTONS).map(Boolean),
    povs: pi.povs.slice(0, MAX_POVS).map(cleanPov),
  };
}

const zeroInputs = () => ({ axes: new Array(SHOWN_AXES).fill(0), buttons: new Array(SHOWN_BUTTONS).fill(false), povs: [-1] });

function pollInputs() {
  const ports = {};
  for (const [port, pi] of Object.entries(drive.router.poll())) ports[port] = cleanInputs(pi);
  return ports;
}

function inputTick() {
  const ports = pollInputs();
  if (drive.connected && !realDs(drive.state)) send({ type: "input", ports });
  if (currentTab() === "drive") renderPortBars(ports);
}

function connectedPads() {
  const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()) : [];
  return pads.map((gp, i) => (gp && gp.connected !== false ? { index: Number.isInteger(gp.index) ? gp.index : i, id: gp.id || "gamepad" } : null))
    .filter(Boolean);
}

let padsSig = null;
function refreshPadOptions() {
  const pads = connectedPads();
  const sig = JSON.stringify(pads);
  if (sig === padsSig) return;
  padsSig = sig;
  for (let p = 0; p < PORTS; p++) {
    const sel = $(`#drive-port-${p}`);
    const cur = sourceValue(drive.router.ports.get(p));
    sel.replaceChildren(h("option", { value: "" }, "None"), h("option", { value: KEYBOARD }, "Keyboard"),
      ...pads.map((g) => h("option", { value: String(g.index) }, `Gamepad ${g.index}: ${g.id}`)));
    sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "";
  }
  // Browsers expose a gamepad only once one of its buttons is pressed, usually after the page has loaded: the first
  // one then takes port 0 from the keyboard, unless the student has set port 0 themselves.
  if (drive.port0Default && pads.length && drive.router.ports.get(0) === KEYBOARD) {
    drive.port0Default = false;
    drive.router.assign(0, pads[0].index);
    $("#drive-port-0").value = String(pads[0].index);
    notice(`Gamepad ${pads[0].index} (${pads[0].id}) is now on port 0; the keyboard no longer drives. Pick the Keyboard in the Joysticks list to switch back.`, "info");
  }
}

const sourceValue = (src) => (src === undefined || src === null ? "" : String(src));

function assignPort(port, value) {
  const src = value === "" ? null : value === KEYBOARD ? KEYBOARD : Number(value);
  drive.router.assign(port, src);
  if (src === null && drive.connected && !realDs(drive.state)) {
    send({ type: "input", ports: { [port]: zeroInputs() } });   // the robot sees the stick let go at once
  }
  renderPortBars({});
}

function buildPorts() {
  const box = $("#drive-ports");
  box.replaceChildren();
  for (let p = 0; p < PORTS; p++) {
    const sel = h("select", { id: `drive-port-${p}`, "aria-label": `Port ${p} source` });
    sel.addEventListener("change", () => {
      if (p === 0) drive.port0Default = false;
      assignPort(p, sel.value);
    });
    const axes = h("div", { class: "port-axes", "aria-hidden": "true" },
      ...Array.from({ length: SHOWN_AXES }, (_, i) => h("span", { class: "port-axis", title: `axis ${i}` }, h("span", { class: "port-axis-fill" }))));
    const buttons = h("div", { class: "port-buttons", "aria-hidden": "true" },
      ...Array.from({ length: SHOWN_BUTTONS }, (_, i) => h("span", { class: "port-button", title: `button ${i + 1}` })));
    box.append(h("div", { class: "port-row idle", dataset: { port: String(p) } },
      h("label", { class: "port-label", for: `drive-port-${p}` }, `Port ${p}`), sel, axes, buttons));
  }
}

function renderPortBars(ports) {
  for (let p = 0; p < PORTS; p++) {
    const row = document.querySelector(`#drive-ports [data-port="${p}"]`);
    const pi = ports[String(p)];
    row.classList.toggle("idle", !drive.router.ports.has(p));
    if (!pi) continue;
    row.querySelectorAll(".port-axis-fill").forEach((el, i) => {
      const v = pi.axes[i] || 0;
      el.style.left = `${50 + Math.min(0, v) * 50}%`;
      el.style.width = `${Math.abs(v) * 50}%`;
    });
    row.querySelectorAll(".port-button").forEach((el, i) => el.classList.toggle("on", !!pi.buttons[i]));
  }
}

// -- signals ------------------------------------------------------------------------------------------------
function buildSignals() {
  const box = $("#drive-signals");
  box.replaceChildren();
  drive.plots = [];
  for (let k = 0; k < SIGNALS; k++) {
    const input = h("input", { id: `drive-signal-${k}`, type: "text", list: "drive-signal-list", autocomplete: "off",
      spellcheck: "false", placeholder: "signal, e.g. battery or nt:/…", "aria-label": `Signal ${k + 1}` });
    const value = h("span", { class: "ro-num sig-value" });
    const canvas = h("canvas", { class: "sig-plot", height: "54", "aria-hidden": "true" });
    input.addEventListener("change", () => {
      drive.picks[k] = input.value.trim();
      drive.plots[k].plot.clear();
      sendPicks();
    });
    drive.plots.push({ plot: new Plot(canvas, 10), value, canvas });
    box.append(h("div", { class: "sig" }, h("div", { class: "row" }, input, value), canvas));
  }
}

function sendPicks(force = false) {
  const names = drive.picks.filter(Boolean);
  const json = JSON.stringify(names);
  if (json === drive.sentPicks && !force) return;
  if (!names.length && force) return;
  if (cmd("signals", { names })) drive.sentPicks = json;
}

async function loadSignals() {
  const { status, data } = await api("GET", "/api/live/signals");
  const s = status === 200 ? data : {};
  const names = [...(s.fixed || [])];
  for (const m of s.motors || []) names.push(`${m}.current`, `${m}.command`);
  for (const j of s.joints || []) names.push(`${j}.position`, `${j}.velocity`);
  $("#drive-signal-list").replaceChildren(...names.map((n) => h("option", { value: n })));
}

function renderSignals(s) {
  const vals = (s && s.signals) || {};
  drive.plots.forEach(({ plot, value, canvas }, k) => {
    const name = drive.picks[k];
    canvas.hidden = !name;
    const v = name ? vals[name] : undefined;
    value.textContent = typeof v === "number" ? String(+v.toPrecision(5)) : "";
    if (name && v !== undefined && isLive(s)) plot.push(s.t, v);
    if (currentTab() === "drive") plot.draw();
  });
}

// -- view ---------------------------------------------------------------------------------------------------
let viewSeq = 0;
async function loadView() {
  const seq = ++viewSeq;
  const { status, data } = await api("GET", "/api/live/view");
  if (seq !== viewSeq) return;
  const v = status === 200 ? data : {};
  if (drive.view) drive.view.setModel(v);
  // only free bodies (game pieces) are dragged on the mini-field; named static geoms stay put there
  const bodies = (v.movers || []).map((m) => (m.kind === "body" ? m.name : null));
  drive.movers = {
    root: Number.isInteger(v.root_mover) ? v.root_mover : -1,
    objects: (v.movable || []).map((n) => [n, bodies.indexOf(n)]).filter(([, i]) => i >= 0),
  };
  drive.field.setView(v);
  drive.field.setRobot(null);
  drive.field.setObjects([]);
}

function createView() {
  try {
    drive.view = new LiveView($("#drive-canvas"));
  } catch (e) {
    console.warn(e);
    drive.view = null;
    $("#drive-canvas").replaceChildren(h("p", { class: "view-placeholder" }, `The live view needs WebGL: ${e.message || e}`));
  }
}

const yawDeg = (p, o) => {   // the heading of the pose at p[o..o+6] (x, y, z, qw, qx, qy, qz)
  const [w, x, y, z] = [p[o + 3], p[o + 4], p[o + 5], p[o + 6]];
  return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) * 180 / Math.PI;
};

function onFrame(f) {
  if (f.source !== 0) return;
  drive.frames++;
  drive.lastFrame = f;
  if (drive.view) drive.view.applyFrame(f);
  const m = drive.movers;
  if (!m) return;
  const p = f.poses, ok = (i) => i >= 0 && (i + 1) * 7 <= p.length;
  if (ok(m.root)) drive.field.setRobot(p[m.root * 7], p[m.root * 7 + 1], yawDeg(p, m.root * 7));
  drive.field.setObjects(m.objects.filter(([, i]) => ok(i)).map(([name, i]) => ({ name, x: p[i * 7], y: p[i * 7 + 1], yaw: yawDeg(p, i * 7) })));
}

// -- state --------------------------------------------------------------------------------------------------
function onState(s) {
  if (s.source !== undefined && s.source !== "live") return;
  const prev = drive.state;
  drive.state = s;
  if (s.state !== "idle" && s.state !== "stopped") drive.startTaken = false;
  else if (drive.startTaken) checkStartEnded();
  if (isLive(s) && !isLive(prev)) sessionBegan(prev);
  if (!isLive(s)) drive.rtf = [];
  else if (!s.paused) {
    const now = performance.now();
    drive.rtf.push([now, s.t]);
    while (drive.rtf.length > 2 && now - drive.rtf[0][0] > RTF_WINDOW) drive.rtf.shift();
  }
  if (isLive(s)) adoptChosenMode(s.chosen_mode);
  render();
}

// While a session is live the mode buttons show the server's chosen mode (what Enable runs), so a reloaded page
// shows Auto when Auto was chosen; a mode this page has just sent wins until the server reports it.
function adoptChosenMode(m) {
  if (!MODES.includes(m)) return;
  const sent = drive.modeSent;
  if (sent && m !== sent.mode && performance.now() - sent.at < MODE_SETTLE) return;
  drive.modeSent = null;
  drive.mode = m;
}

function chooseMode(m) {
  drive.mode = m;
  if (cmd("mode", { mode: m })) drive.modeSent = { mode: m, at: performance.now() };
}

// The session has just come up: it starts in teleop with no signals, so the mode picked before Start is sent.
// A page that reconnects or reloads into a live session takes the session's mode instead.
function sessionBegan(prev) {
  const fresh = !!prev && prev.state === "starting";
  if (fresh && drive.mode !== "teleop") chooseMode(drive.mode);
  sendPicks(true);
}

// The match controls show the session's settings and game data box (state.match, from the config's [match] at
// Start); they work only while a session is live. The server sends the box to the robot when it is enabled in Teleop.
function sendMatch(key, value) {
  if (cmd("match", { [key]: value })) drive.matchSent[key] = { value, at: performance.now() };
}

function renderMatch(s) {
  const m = isLive(s) && s.match;
  const station = $("#drive-station");
  if (s.stations && station.options.length !== s.stations.length) {
    station.replaceChildren(...s.stations.map((st) => h("option", { value: st }, st)));
  }
  const real = realDs(s);
  $("#drive-match-hint").textContent = real ? REAL_DS_MATCH_HINT : MATCH_HINT;
  for (const id of Object.values(MATCH_CONTROLS)) setWhy($(id), real ? REAL_DS_WHY : "");
  if (real) {   // read-only: what the robot reports from the real DS (it doesn't report the game data)
    for (const id of Object.values(MATCH_CONTROLS)) $(id).disabled = true;
    if (!m) return;
    station.value = m.station || "";
    $("#drive-fms").checked = !!m.fms;
    $("#drive-timer").checked = typeof m.match_time === "number" && m.match_time >= 0;
    $("#drive-game-data").value = "";
    return;
  }
  for (const [key, id] of Object.entries(MATCH_CONTROLS)) {
    const el = $(id);
    el.disabled = !m;
    if (!m) continue;
    const sent = drive.matchSent[key];
    if (sent && sent.value !== m[key] && performance.now() - sent.at < MODE_SETTLE) continue;
    delete drive.matchSent[key];
    if (el.type === "checkbox") el.checked = !!m[key];
    else if (document.activeElement !== el) el.value = m[key];
  }
}

const fmtT = (t) => (typeof t === "number" ? `${t.toFixed(3)} s` : "—");

function rtfText(s) {
  if (!isLive(s)) return "";
  if (s.paused) return "paused";
  const r = drive.rtf;
  if (r.length < 2) return "…";
  const dw = (r[r.length - 1][0] - r[0][0]) / 1000, dt = r[r.length - 1][1] - r[0][1];
  return dw > 0 ? `${(dt / dw).toFixed(2)}× real time` : "…";
}

function renderSource(s) {
  const sel = $("#drive-ds-source");
  if (isLive(s)) sel.value = s.real_ds ? "real" : "page";   // a reloaded page shows the session's choice
  else if (!isActive(s) && !drive.dsTouched) sel.value = drive.realDsDefault ? "real" : "page";
  sel.disabled = isActive(s) || drive.posting;
  setWhy(sel, isActive(s) ? "Fixed while the robot runs: to change it, stop and start again" : "");
}

function realDsText(m) {
  if (!m || !m.connected) return "Waiting for the Driver Station";
  const text = REAL_DS_MODE_TEXT[m.mode] || m.mode;
  return m.mode === "disabled" && m.estop ? `${text} (E-stopped)` : text;
}

// The bar's read-only line while the FRC Driver Station app drives: what the robot reports it is doing.
function renderRealDs(s, real) {
  const box = $("#drive-real-ds");
  box.hidden = !real;
  $("#drive-keys-hint").textContent = real ? REAL_DS_KEYS_HINT : KEYS_HINT;
  if (!real) return;
  const m = isLive(s) ? s.match : null;
  const st = $("#drive-real-ds-state");
  st.textContent = realDsText(m);
  st.className = `mode-chip ds-real-state mode-${m && m.connected && m.mode !== "disabled" ? m.mode : "disabled"}`;
  const parts = [];
  if (m && m.connected) {
    if (m.station && m.station !== "unknown") parts.push(`Station ${m.station}`);
    if (m.fms) parts.push("FMS attached");
    if (typeof m.match_time === "number" && m.match_time >= 0) parts.push(`match time ${m.match_time.toFixed(1)} s`);
  }
  $("#drive-real-ds-match").textContent = parts.join(" · ");
  $("#drive-real-ds-match").hidden = !parts.length;
}

function render() {
  const s = drive.state || { state: "idle" };
  const live = isLive(s), active = isActive(s);
  renderSource(s);
  const real = realDs(s);
  $("#drive-start").disabled = active || drive.posting || !!drive.startProblem;
  $("#drive-stop").disabled = !active;
  for (const m of MODES) {
    const b = document.querySelector(`#drive-ds [data-key="mode-${m}"]`);
    b.setAttribute("aria-pressed", String(real ? live && s.mode === m : drive.mode === m));
    b.disabled = real;
    setWhy(b, real ? REAL_DS_WHY : "");
  }
  $("#drive-enable").disabled = !live || s.enabled || real;
  $("#drive-disable").disabled = !live || real;
  for (const id of ["#drive-enable", "#drive-disable"]) setWhy($(id), real ? REAL_DS_WHY : "");
  $("#drive-enable").classList.toggle("on", live && !!s.enabled);
  for (let p = 0; p < PORTS; p++) {
    const sel = $(`#drive-port-${p}`);
    sel.disabled = real;
    setWhy(sel, real ? REAL_DS_WHY : "");
  }
  renderRealDs(s, real);
  const pause = $("#drive-pause");
  pause.disabled = !live || real;
  pause.textContent = live && s.paused ? "Resume" : "Pause";
  pause.setAttribute("aria-pressed", String(live && !!s.paused));
  $("#drive-step").disabled = !live || real;
  for (const id of ["#drive-pause", "#drive-step"]) setWhy($(id), real ? REAL_DS_NO_PAUSE : "");
  const speed = $("#drive-speed");
  if (live && document.activeElement !== speed) {
    const want = s.speed === null ? "max" : String(s.speed);
    if (![...speed.options].some((o) => o.value === want)) speed.append(h("option", { value: want }, `${want}×`));
    speed.value = want;
  }
  speed.disabled = !!(live && s.speed_locked);
  const lock = $("#drive-speed-lock");
  lock.hidden = !(live && s.speed_locked);
  lock.textContent = live && s.speed_locked ? `Speed locked at 1×: ${s.speed_locked}` : "";
  for (const id of ["#push-go", "#drive-reset", "#drive-record"]) $(id).disabled = !live;
  const rec = live && !!s.recording;
  const recBtn = $("#drive-record");
  if (real) recBtn.disabled = true;
  setWhy(recBtn, real ? REAL_DS_NO_RECORD : "");
  recBtn.setAttribute("aria-pressed", String(rec));
  recBtn.classList.toggle("recording", rec);
  recBtn.textContent = rec ? `● REC · ${pluralPokes(s.pokes || 0)}` : "● Record";
  $("#drive-save").disabled = !s.can_save || drive.saving;
  $("#drive-pokes").classList.toggle("off", !active);
  if (drive.field) drive.field.setEnabled(active);   // drags only while a session is starting, running or paused
  renderMatch(s);
  renderHud(s);
  renderReadouts($("#drive-values"), s);
  if (drive.view) drive.view.setScore(s.readouts && s.readouts.range);
  renderSignals(s);
  renderConsole(s);
  renderStatus(s);
  renderBanner();
}

function renderHud(s) {
  const live = isLive(s);
  const mode = live ? s.mode : "—";
  const badge = live && s.enabled ? h("span", { class: "hud-badge hud-enabled", text: "Enabled" })
    : h("span", { class: "hud-badge hud-disabled", text: "Disabled" });
  const items = [
    h("span", { class: "hud-item", text: `t ${fmtT(live ? s.t : null)}` }),
    h("span", { class: `hud-item mode-chip hud-mode mode-${live && MODES.includes(s.mode) ? s.mode : "disabled"}`, text: mode }),
    badge,
    h("span", { class: "hud-item", text: rtfText(s) }),
  ];
  const match = live && s.readouts && s.readouts.match;
  if (match && (match.timer || s.real_ds) && match.match_time >= 0) items.splice(3, 0, h("span", { class: "hud-item", text: `match ${match.match_time.toFixed(1)} s` }));
  if (drive.started && !drive.connected) items.push(h("span", { class: "hud-item hud-bad", text: "reconnecting…" }));
  $("#drive-hud").replaceChildren(...items);
}

function renderConsole(s) {
  if (!s.console || s.console_seq === drive.consoleSeq) return;
  drive.consoleSeq = s.console_seq;
  const pre = $("#drive-console");
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent = s.console.join("\n");
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

function renderStatus(s) {
  const el = $("#drive-state");
  el.textContent = STATE_TEXT[s.state] || s.state;
  el.className = `saved ${s.state === "error" ? "saved-bad" : isLive(s) ? "saved-ok" : s.state === "starting" ? "saved-busy" : ""}`;
  const err = $("#drive-error");
  err.hidden = !s.error;
  err.textContent = s.error || "";
  $("#drive-restart").hidden = s.state !== "error";
}

function renderDeviceLine(d) {
  const el = $("#drive-devices");
  const bad = d ? d.rows.filter((r) => r.status === "missing" || r.status === "other_type").length : 0;
  el.hidden = !bad;
  if (!bad) { el.replaceChildren(); return; }
  el.replaceChildren(h("strong", { text: `${bad} device${bad === 1 ? " doesn't" : "s don't"} match the model` }), ": ",
    h("button", { type: "button", class: "linklike", onclick: async () => (await import("./code.js")).showDevices() }, "see Robot code"));
}

function renderBanner() {
  const problem = drive.startProblem, why = problem || drive.startError;
  $("#drive-banner").hidden = !why;
  $("#drive-banner-text").textContent = !why ? "" : problem ? `Start is unavailable: ${problem}` : `Couldn't start: ${why}`;
  // Java code that hasn't built: the button goes to the Robot code panel, where Build now is
  const toCode = !problem && !!drive.startError && drive.notBuilt;
  $("#drive-banner-robot").hidden = !problem && !toCode;
  $("#drive-banner-robot").textContent = toCode ? "Go to Robot code" : "Go to the Robot tab";
}

// -- session ------------------------------------------------------------------------------------------------
// The server publishes the session's states itself ("starting" first), so the page doesn't make one up: a
// program that fails at once may have sent "error" before this request returns. When the last session left a
// recording that was never saved, the server refuses (field "live/unsaved") and the page asks before discarding it.
async function startSession(discard = false) {
  let unpicked = false;
  drive.startError = null;
  drive.notBuilt = false;
  drive.posting = true;
  render();
  try {
    await loadView();
    const sp = $("#drive-speed").value;
    const { status, data } = await api("POST", "/api/live/start",
      { speed: sp === "max" ? "max" : Number(sp), discard_recording: discard, real_ds: $("#drive-ds-source").value === "real" });
    if (status === 200 && data.ok) {
      drive.startTaken = true;
      drive.starts++;
      checkStartEnded();
      drive.consoleSeq = null;
      for (const p of drive.plots) p.plot.clear();
      return;
    }
    if (status === 200 && (data.errors || []).some((e) => e.field === "live/unsaved")) {
      $("#discard-dialog").showModal();
      return;
    }
    unpicked = status === 200 && (data.errors || []).some((e) => e.field === "start/unpicked");
    drive.notBuilt = status === 200 && (data.errors || []).some((e) => e.field === "start/not_built");
    if (!unpicked) {
      drive.startError = (data.errors || []).map((e) => e.message).join("; ") || `the server answered ${status}`;
      refreshInfo();   // the robot may have become undrivable since the last look
    }
  } finally {
    drive.posting = false;
    render();
  }
  if (!unpicked) return;
  // nobody has picked where the robot starts: the robot code panel's start picker asks, then Start goes on
  const { pickStart } = await import("./code.js");
  if (await pickStart()) startSession(discard);
}

// After a taken Start, the session may start and end before the page hears of it (the stream sends only the latest
// state, so "starting" can be skipped): ask the server. It marks a Start "starting" before it answers, so an idle or
// stopped session now means it has ended, and the idle page shouldn't look like a real-DS session any more.
async function checkStartEnded() {
  const start = drive.starts;
  const { status, data } = await api("GET", "/api/live/info");
  if (status === 200 && drive.startTaken && drive.starts === start && ["idle", "stopped"].includes(data.status.state)) {
    drive.startTaken = false;
    render();
  }
}

// The robot code panel's Drive button: the Drive tab, and a start when nothing is running yet.
export async function driveFromPanel() {
  setTab("drive");
  await ensureStarted();
  const info = await refreshInfo();
  if (info && info.can_start && !isActive(drive.state) && !drive.posting) startSession();
}

async function stopSession() {
  const { status, data } = await api("POST", "/api/live/stop");
  if (status !== 200) notice(`Couldn't stop: ${(data.errors || [{}])[0].message || status}`, "error");
}

async function refreshInfo() {
  const seen = drive.messages;
  const { status, data } = await api("GET", "/api/live/info");
  if (status !== 200) return null;
  drive.startProblem = data.can_start ? null : data.start_problem || "this robot can't be driven";
  drive.realDsDefault = !!data.real_ds_default;
  // a state message that came meanwhile is newer than this status; else keep the last readouts until the next one
  if (drive.messages === seen) onState({ ...(drive.state || {}), ...data.status });
  else render();
  return data;
}

// -- pokes and recording ----------------------------------------------------------------------------------------
const pluralPokes = (n) => `${n} poke${n === 1 ? "" : "s"}`;

// The push force box shows the force in the chosen units (it is sent in newtons). A change of units redraws it,
// unless it is being typed in.
let pushForce = 100;   // N
function showPushForce() {
  const el = $("#push-force");
  $("#push-force-unit").textContent = units.name("force");
  if (document.activeElement === el && el.value.trim() !== el.dataset.shown) return;
  showMeasure(el, pushForce, "force");
}

function push() {
  const read = (id) => { const el = $(id); return el.validity.badInput || el.value.trim() === "" ? NaN : Number(el.value); };
  const typed = readMeasure($("#push-force"), "force");
  const force = typeof typed === "number" ? typed : NaN, dir = read("#push-dir"), dur = read("#push-duration");
  if (![force, dir, dur].every(Number.isFinite) || dur <= 0) {
    const why = measureError(typed) ? `${measureError(typed)}. ` : "";
    notice(`${why}Push needs a force (${units.name("force")}), a direction (°) and a duration above 0 s.`, "warn");
    return;
  }
  pushForce = force;
  cmd("push", { force_n: force, direction_deg: dir, duration_s: dur });
}

function defaultSavePath() {
  const d = new Date(), p2 = (n) => String(n).padStart(2, "0");
  const stamp = `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}`;
  // next to the robot config (its path as the server shows it: relative to where scrimmage edit was started)
  const cfg = store.state ? store.state.display_path || store.state.path || "" : "";
  const slash = cfg.lastIndexOf("/");
  return `${slash >= 0 ? cfg.slice(0, slash + 1) : ""}scenarios/drive-${stamp}.toml`;
}

function openSaveDialog() {
  const d = $("#save-dialog");
  $("#save-path").value = defaultSavePath();
  const n = (drive.state && drive.state.pokes) || 0;
  const warn = $("#save-pokes");
  warn.hidden = !n;
  warn.textContent = n ? `${n === 1 ? "1 poke happened while recording and is" : `${n} pokes happened while recording and are`} not in the scenario.` : "";
  showSaveErrors([]);
  d.showModal();
  $("#save-path").select();
}

function showSaveErrors(errors) {
  const exists = errors.find((e) => e.field === "save/exists");
  $("#save-exists").hidden = !exists;
  $("#save-exists-text").textContent = exists ? `${exists.message}. Overwrite?` : "";
  const rest = errors.filter((e) => e !== exists);
  const list = $("#save-errors");
  list.replaceChildren(...rest.map((e) => h("li", { text: e.message })));
  list.hidden = !rest.length;
}

async function saveRecording(overwrite = false) {
  const path = $("#save-path").value.trim();
  if (!path) { showSaveErrors([{ field: "status", message: "Enter a file name for the scenario." }]); return; }
  if (drive.saving) return;
  drive.saving = true;
  $("#save-go").disabled = $("#save-overwrite").disabled = true;
  try {
    const { status, data } = await api("POST", "/api/live/save_recording", { path, overwrite });
    if (!(status === 200 && data.ok)) {
      showSaveErrors(data.errors && data.errors.length ? data.errors : [{ field: "status", message: `the server answered ${status}` }]);
      return;
    }
    $("#save-dialog").close();
    notice(`Saved the recording as ${data.display_path} (${data.steps} step${data.steps === 1 ? "" : "s"}).`, "info");
    await openSaved(data.path);
  } finally {
    drive.saving = false;
    $("#save-go").disabled = $("#save-overwrite").disabled = false;
    render();
  }
}

// Opens the saved scenario in the Scenario tab (registering the tab when the page was opened on a robot config).
async function openSaved(path) {
  const sc = await import("./scenario.js");
  sc.expectOpen();
  const { status, data } = await api("POST", "/api/scenario/open", { path });
  if (!(status === 200 && data.ok)) {
    sc.expectOpen(false);
    notice(`Saved, but couldn't open it in the Scenario tab: ${(data.errors || [{}])[0].message || status}`, "error");
    return;
  }
  await sc.showScenario();
}

// -- keys -----------------------------------------------------------------------------------------------------
function onKey(e) {
  if (currentTab() !== "drive" || e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  if (t && t.tagName === "SELECT" && isDrivingKey(e.code)) {
    e.preventDefault();   // W or 1 drives the robot; it doesn't type-ahead in a Drive-tab list (camera, speed, ports)
    return;
  }
  if (isEditable(t) && t.tagName !== "SELECT") return;   // typing in a text box
  // an open dialog (Save as scenario…) has the keys to itself
  if (document.querySelector("dialog[open]") || (t && t.closest && t.closest("dialog"))) return;
  if (e.key === " " || e.code === "Space") {
    e.preventDefault();   // the e-stop: Space disables, even on a focused button, which it then doesn't press
    cmd("disable");
  } else if (e.key === "Enter") {
    if (t && t.closest && t.closest("button")) return;   // Enter presses the focused button
    e.preventDefault();
    if (!e.repeat) cmd("enable");
  }
}

// -- tab ------------------------------------------------------------------------------------------------------
function renderTop() {
  const s = store.state;
  if (s) showPath(s.display_path || s.path || "", s.path || "");
  $("#undo").disabled = true;
  $("#redo").disabled = true;
}

async function ensureStarted() {
  if (drive.started) return;
  drive.started = true;
  createView();
  loadSignals();
  await loadView();
  const info = await refreshInfo();
  if (!info) { notice("Couldn't reach the live session (is scrimmage edit still running?)", "error"); return; }
  const stream = await sharedStream(info.port);
  if (!stream) { notice("Couldn't reach the live session (is scrimmage edit still running?)", "error"); return; }
  drive.stream = stream;
  stream.addEventListener("open", () => { drive.connected = true; refreshInfo(); render(); });
  stream.addEventListener("close", () => { drive.connected = false; render(); });
  stream.addEventListener("frame", (e) => onFrame(e.detail));
  stream.addEventListener("state", (e) => { drive.messages++; onState(e.detail); });
  stream.addEventListener("notice", (e) => {
    const k = e.detail.kind;
    notice(e.detail.text, k === "error" ? "error" : k === "warn" || k === "warning" ? "warn" : "info");
  });
  if (stream.isOpen) { drive.connected = true; refreshInfo(); render(); }   // the run panel's Watch opened it first
  setInterval(inputTick, INPUT_PERIOD);
}

function onRobotState(e) {
  const v = e.detail.state.version;
  if (v === drive.robotVersion) return;
  drive.robotVersion = v;
  if (!drive.started) return;
  refreshInfo();
  if (!isActive(drive.state)) { loadView(); loadSignals(); }
}

export function initDrive() {
  drive.router = new InputRouter({ accept: () => currentTab() === "drive" });
  drive.router.addEventListener("gamepad-lost", (e) => {
    notice(`The gamepad on port ${e.detail} went away; that port now reads zeros.`, "warn");
    padsSig = null;
    refreshPadOptions();
  });
  buildPorts();
  buildSignals();
  const pads = connectedPads();
  drive.router.assign(0, pads.length ? pads[0].index : KEYBOARD);
  drive.port0Default = !pads.length;
  refreshPadOptions();
  window.addEventListener("gamepadconnected", refreshPadOptions);
  window.addEventListener("gamepaddisconnected", refreshPadOptions);
  setInterval(refreshPadOptions, 1000);   // some browsers report gamepads only by polling

  $("#drive-ds-source").addEventListener("change", (e) => {
    drive.dsTouched = true;
    e.target.dataset.touched = "1";   // the robot code panel's Start and check devices sends the pick too
    render();
  });
  $("#drive-start").addEventListener("click", () => startSession());
  $("#drive-restart").addEventListener("click", () => startSession());
  $("#discard-form").addEventListener("submit", (e) => {
    e.preventDefault();
    $("#discard-dialog").close();
    startSession(true);
  });
  $("#discard-cancel").addEventListener("click", () => $("#discard-dialog").close());
  $("#drive-stop").addEventListener("click", stopSession);
  $("#drive-banner-robot").addEventListener("click", async () => {
    if (drive.notBuilt && !drive.startProblem) (await import("./code.js")).showChecks();
    else setTab("robot", { focus: true });
  });
  for (const m of MODES) {
    document.querySelector(`#drive-ds [data-key="mode-${m}"]`).addEventListener("click", () => {
      chooseMode(m);
      render();
    });
  }
  $("#drive-station").addEventListener("change", (e) => sendMatch("station", e.target.value));
  for (const key of ["fms", "timer"]) $(MATCH_CONTROLS[key]).addEventListener("change", (e) => sendMatch(key, e.target.checked));
  $("#drive-game-data").addEventListener("change", (e) => sendMatch("game_data", e.target.value));
  $("#drive-enable").addEventListener("click", () => cmd("enable"));
  $("#drive-disable").addEventListener("click", () => cmd("disable"));
  $("#drive-pause").addEventListener("click", () => cmd(drive.state && drive.state.paused ? "resume" : "pause"));
  $("#drive-step").addEventListener("click", () => cmd("step", { n: 1 }));
  $("#drive-speed").addEventListener("change", (e) => {
    const v = e.target.value;
    cmd("speed", { speed: v === "max" ? "max" : Number(v) });
  });
  $("#drive-camera").addEventListener("change", (e) => { if (drive.view) drive.view.setCamera(e.target.value); });
  drive.field = new MiniField($("#drive-field"), {});
  drive.field.onSetPose((x, y, heading) => poke("set_pose", { x, y, heading_deg: heading }));
  drive.field.onMoveObject((name, x, y) => {
    const o = drive.field.objects.find((k) => k.name === name);
    poke("move_body", { name, x, y, yaw_deg: o ? Math.round(o.yaw * 10) / 10 : 0 });
  });
  $("#push-go").addEventListener("click", push);
  $("#push-force").addEventListener("change", (e) => {
    const v = readMeasure(e.target, "force");
    if (typeof v === "number") pushForce = v;
  });
  showPushForce();
  units.onChange(() => { showPushForce(); render(); });
  $("#drive-reset").addEventListener("click", () => cmd("reset"));
  $("#drive-record").addEventListener("click", () => cmd(drive.state && drive.state.recording ? "record_stop" : "record_start"));
  $("#drive-save").addEventListener("click", openSaveDialog);
  $("#save-form").addEventListener("submit", (e) => { e.preventDefault(); saveRecording(false); });
  $("#save-overwrite").addEventListener("click", () => saveRecording(true));
  $("#save-cancel").addEventListener("click", () => $("#save-dialog").close());
  $("#save-path").addEventListener("input", () => showSaveErrors([]));
  document.addEventListener("keydown", onKey);
  // a pick from a Drive-tab list hands the keys back to driving (WASD, the digits, Enter and Space)
  $("#tab-drive").addEventListener("change", (e) => { if (e.target.tagName === "SELECT") e.target.blur(); });
  window.addEventListener("scrimmage:state", onRobotState);
  window.addEventListener("scrimmage:devices", (e) => renderDeviceLine(e.detail.devices));

  window.__scrimmage.live = {
    get state() { return drive.state; },
    get frames() { return drive.frames; },
    get lastFrame() { return drive.lastFrame ? { t: drive.lastFrame.t, poses: Array.from(drive.lastFrame.poses) } : null; },
    get connected() { return drive.connected; },
    get plots() { return drive.plots.map((p) => p.plot.length); },
    get assignments() { return drive.router.assignments(); },
  };
  render();
  registerTab("drive", {
    renderTop,
    undo() {},
    redo() {},
    onShow() { renderTop(); ensureStarted(); if (drive.view) drive.view.resize(); if (drive.field) drive.field.show(); },
    onHide() { drive.router.pressed.clear(); },
  });
}
