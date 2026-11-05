// Display units: the Units setting (Imperial | Metric) and the shared unit table from scrimmage/units.py, which
// GET /api/settings sends, so the page and the server format the same way.
//
// Values are SI (meters, kilograms, newtons, pascals, kg/m³, N/m, m/s), except angles, in degrees as the files keep
// them. A site names its kind (scrimmage/units.py KINDS): "length" for anything on a robot or mechanism, "field"
// for poses, field picks and drive distances.
//   fmt(0.1016, "length")        "4 in" or "101.6 mm"
//   toDisplay(si, kind)          the number in the shown unit;  fromDisplay(v, kind) back to SI
//   shown(si, kind)              an input box's text for si ("4", two more places than fmt)
//   name(kind)                   "in" or "mm"
//   parse(text, kind)            a typed "4", "4 in" or "100mm" in SI, or {error} saying plainly what to type
//   toKey(si, "in") / fromKey    a file key with its own unit (`diameter_in`, a measure file's `force_lb`): it keeps
//                                that unit whatever the setting
//   onChange(fn)                 fn(system) after the setting changes (here, in another tab, browser or by hand):
//                                re-render what's shown; returns a function that stops it
// load() fetches the table (app.js awaits it before the tabs start); watch() polls /api/settings so a change made
// elsewhere arrives within a few seconds; set(system) saves the setting.

let table = null;
let system = "imperial";
let warning = null;
const listeners = new Set();
const warners = new Set();

function token() { return new URLSearchParams(location.hash.slice(1)).get("token") || ""; }

async function request(method, body) {
  const headers = { "X-Scrimmage-Token": token() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  try {
    const res = await fetch("/api/settings", { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    return { status: res.status, data: data || {} };
  } catch {
    return { status: 0, data: { error: "can't reach the editor (is scrimmage edit still running?)" } };
  }
}

function apply(data) {
  const hadTable = table !== null, was = system, hadWarning = warning;
  table = data.table || table;
  warning = data.warning || null;
  system = data.units || system;
  // the first load() may have failed: the table arriving later changes every shown value too
  if (system !== was || (!hadTable && table)) {
    for (const fn of [...listeners]) {
      try { fn(system); } catch (e) { console.error(e); }
    }
  }
  if (warning && warning !== hadWarning) {
    for (const fn of [...warners]) {
      try { fn(warning); } catch (e) { console.error(e); }
    }
  }
}

// Requests are numbered: a GET sent before a set() was answered can carry the old setting (a poll that left just
// before the POST and came back just after it), so its answer is dropped, as is any GET answered while a set() is
// still on its way.
let sent = 0, settled = 0, setting = 0, latestSet = 0;   // latestSet: two set()s answered out of order, the later wins

export async function load() {
  const n = ++sent;
  const { status, data } = await request("GET");
  if (status === 200 && !setting && n > settled) apply(data);
  return status === 200;
}

// Save the setting; resolves to null, or the server's error text.
export async function set(next) {
  const n = ++sent;
  setting++;
  let res;
  try { res = await request("POST", { units: next }); } finally { setting--; settled = ++sent; }
  const { status, data } = res;
  if (status !== 200) return data.error || `the server answered ${status}`;
  if (n > latestSet) { latestSet = n; apply(data); }
  return null;
}

let watching = null;
export function watch(ms = 2000) {
  if (watching) return;
  const tick = async () => {
    if (!document.hidden) await load();
    watching = setTimeout(tick, ms);
  };
  watching = setTimeout(tick, ms);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
}

// fn(text) when a settings warning appears or changes (not for the one load() found before anyone listened)
export function onWarning(fn) {
  warners.add(fn);
  return () => warners.delete(fn);
}

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function unit(kind, sys = system) {
  const u = table && table.units[sys] && table.units[sys][kind];
  if (!u) {
    if (table) throw new Error(`unknown kind "${kind}"; one of ${table.kinds.join(", ")}`);
    return { name: "", factor: 1, decimals: 10 };   // the table hasn't loaded: SI, unnamed
  }
  return u;
}

// x to 15 significant digits: no float noise from a conversion (3.8100000000000005 -> 3.81)
const clean = (x) => (Number.isFinite(x) ? +x.toPrecision(15) : x);

export function name(kind) { return unit(kind).name; }
export function toDisplay(si, kind) { return clean(si * unit(kind).factor); }
export function fromDisplay(v, kind, sys = system) { return clean(v / unit(kind, sys).factor); }

// v to `decimals` places, trailing zeros trimmed, never "-0": the number as written rounded (6.35, not the binary
// 6.3499...), a tie away from zero, as scrimmage/units.py number() does
export function number(v, decimals) {
  const a = Math.abs(v);
  let text;
  if (String(a).includes("e")) text = a.toFixed(decimals);   // tiny (rounds to 0) or past 1e21
  else text = (Math.round(Number(`${a}e${decimals}`)) / 10 ** decimals).toFixed(decimals);
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text === "0" || v >= 0 ? text : `-${text}`;
}

// The number an input box shows for `si` (no unit name; "" for none): two more places than fmt, so a typed value
// reads back as typed. A box whose text is still this is unchanged: save its SI value, not this rounding of it.
export function shown(si, kind, extra = 2) {
  if (typeof si !== "number" || !Number.isFinite(si)) return si === null || si === undefined ? "" : String(si);
  return number(toDisplay(si, kind), unit(kind).decimals + extra);
}

export function fmt(si, kind) {
  const u = unit(kind);
  if (si === null || si === undefined) return "";
  if (typeof si !== "number" || !Number.isFinite(si)) return String(si);
  const text = number(toDisplay(si, kind), u.decimals);
  if (!u.name) return text;
  return u.name === "°" ? `${text}°` : `${text} ${u.name}`;
}

const squash = (s) => s.replace(/\s+/g, "").toLowerCase();
const NUMBER = /^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*(.*?)\s*$/i;
const SYSTEM_NAME = { imperial: "Imperial", metric: "Metric" };

const LENGTH_KINDS = ["length", "field"];

// What a typed value means, in SI: a bare number is in the shown unit; a number may also end with that unit
// ("4 in", "100mm"), or, in a length or field box, any length unit of the system ("6 in" where ft is shown, "2 cm"
// where mm is). The other system's unit is refused, not converted, so a typo never slips in silently. `sys`: the
// system the text was typed in, when it isn't the setting now (text kept across a Units change).
export function parse(text, kind, sys = system) {
  const u = unit(kind, sys);
  const m = NUMBER.exec(String(text ?? ""));
  const want = u.name ? ` in ${u.name}` : "";
  if (!m) return { error: `Type a number${want}` };
  const v = Number(m[1]);
  if (!Number.isFinite(v)) return { error: `Type a number${want}` };
  const said = squash(m[2]);
  if (!said || !table) return fromDisplay(v, kind, sys);
  const names = (n) => (table.aliases[n] || [n]).map(squash);
  if (names(u.name).includes(said)) return fromDisplay(v, kind, sys);
  const lengths = LENGTH_KINDS.includes(kind) && table.lengths ? table.lengths : null;
  if (lengths) {
    const mine = Object.keys(lengths[sys] || {}).find((n) => names(n).includes(said));
    if (mine) return clean(v * lengths[sys][mine]);
  }
  const refuse = (o) => ({ error: `Units is set to ${SYSTEM_NAME[sys]}, so type ${u.name} here, not ${o} (or change Units in Settings)` });
  for (const other of table.systems) {
    if (other === sys) continue;
    const o = table.units[other][kind];
    if (o.name !== u.name && names(o.name).includes(said)) return refuse(o.name);
    const theirs = lengths && Object.keys(lengths[other] || {}).find((n) => names(n).includes(said));
    if (theirs) return refuse(theirs);
  }
  return { error: `"${m[2]}" isn't a unit for this: type a number${want}` };
}

// A file key's own unit: `keyUnit` is the unit ("in") or the whole key ("diameter_in"); `quantity` ("length",
// "mass", "force", ...) is needed only when the unit could be more than one (a measure file's "lb": mass or force).
function keyFactor(keyUnit, quantity) {
  const u = String(keyUnit).split("_").pop().toLowerCase();
  const keys = (table && table.keys) || {};
  const found = Object.keys(keys).filter((q) => (quantity ? q === quantity : true) && u in keys[q]);
  if (found.length !== 1) {
    throw new Error(found.length ? `"${u}" could be ${found.join(" or ")}: say which` : `"${u}" isn't a key unit${quantity ? ` of ${quantity}` : ""}`);
  }
  return keys[found[0]][u];
}
export function toKey(si, keyUnit, quantity) { return clean(si / keyFactor(keyUnit, quantity)); }
export function fromKey(v, keyUnit, quantity) { return clean(v * keyFactor(keyUnit, quantity)); }

export const units = {
  get system() { return system; },
  get table() { return table; },
  get warning() { return warning; },
  load, set, watch, onChange, onWarning, fmt, shown, toDisplay, fromDisplay, name, parse, number, toKey, fromKey,
};
export default units;
