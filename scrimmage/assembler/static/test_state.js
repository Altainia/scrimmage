// The Test tab's state (T) and the small helpers its files share (test.js is the tab).

import { t } from "./i18n.js";

export const answered = (status) => t("the server answered {status}", { status });

export const T = {
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
  shownUnits: null,   // the Units setting the blocks show their numbers in
  hopperSet: false,   // the student changed the hopper in Setup (no longer starter_hopper's preselection)
  floor: null,        // the floor spread's boxes as typed (floorDraft), for the test's floor_spread `floorFor`
  floorFor: null,
  gripOwn: new Set(), // the bodies whose grip picker shows "My own number…" before a number is typed
  gripSent: new Map(),   // body -> the grip sent, shown until the palette has it (the robot is built again first)
};

// JSON with sorted keys, so two equal tests compare equal whatever order their keys came in.
export function canon(v) {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (v && typeof v === "object") return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`;
  return JSON.stringify(v === undefined ? null : v);
}

export const fmt = (v) => String(+Number(v).toPrecision(6));
export const IN = 0.0254;            // m per inch: an `_in` key's unit
export const round6 = (x) => Number(x.toFixed(6));       // a typed value as saved: to a millionth of its unit (1 µm in m)
