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
// elsewhere arrives within a few seconds; set(system) saves the setting. The request and the poll are settings.js's,
// which i18n.js shares.

import { settings } from "./settings.js";
import { t } from "./i18n.js";

let table = null;
let system = "imperial";
const listeners = new Set();

settings.onSettings((data) => {
  const hadTable = table !== null, was = system;
  table = data.table || table;
  system = data.units || system;
  // the first load() may have failed: the table arriving later changes every shown value too
  if (system !== was || (!hadTable && table)) {
    for (const fn of [...listeners]) {
      try { fn(system); } catch (e) { console.error(e); }
    }
  }
});

// load(), watch() and onWarning() are settings.js's: one request serves Units and Language
export const { load, watch, onWarning } = settings;

// Save the setting; resolves to null, or the server's error text.
export function set(next) { return settings.save({ units: next }); }

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
const SYSTEM_NAME = { imperial: () => t("Imperial"), metric: () => t("Metric") };

const LENGTH_KINDS = ["length", "field"];

// What a typed value means, in SI: a bare number is in the shown unit; a number may also end with that unit
// ("4 in", "100mm"), or, in a length or field box, any length unit of the system ("6 in" where ft is shown, "2 cm"
// where mm is). The other system's unit is refused, not converted, so a typo never slips in silently. `sys`: the
// system the text was typed in, when it isn't the setting now (text kept across a Units change).
export function parse(text, kind, sys = system) {
  const u = unit(kind, sys);
  const m = NUMBER.exec(String(text ?? ""));
  const typeOne = () => (u.name ? t("Type a number in {unit}", { unit: u.name }) : t("Type a number"));
  if (!m) return { error: typeOne() };
  const v = Number(m[1]);
  if (!Number.isFinite(v)) return { error: typeOne() };
  const said = squash(m[2]);
  if (!said || !table) return fromDisplay(v, kind, sys);
  const names = (n) => (table.aliases[n] || [n]).map(squash);
  if (names(u.name).includes(said)) return fromDisplay(v, kind, sys);
  const lengths = LENGTH_KINDS.includes(kind) && table.lengths ? table.lengths : null;
  if (lengths) {
    const mine = Object.keys(lengths[sys] || {}).find((n) => names(n).includes(said));
    if (mine) return clean(v * lengths[sys][mine]);
  }
  const refuse = (o) => ({ error: t("Units is set to {system}, so type {unit} here, not {other} (or change Units in Settings)",
    { system: SYSTEM_NAME[sys](), unit: u.name, other: o }) });
  for (const other of table.systems) {
    if (other === sys) continue;
    const o = table.units[other][kind];
    if (o.name !== u.name && names(o.name).includes(said)) return refuse(o.name);
    const theirs = lengths && Object.keys(lengths[other] || {}).find((n) => names(n).includes(said));
    if (theirs) return refuse(theirs);
  }
  return { error: u.name ? t("\"{typed}\" isn't a unit for this: type a number in {unit}", { typed: m[2], unit: u.name })
    : t("\"{typed}\" isn't a unit for this: type a number", { typed: m[2] }) };
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
  get warning() { return settings.warning; },
  load, set, watch, onChange, onWarning, fmt, shown, toDisplay, fromDisplay, name, parse, number, toKey, fromKey,
};
export default units;
