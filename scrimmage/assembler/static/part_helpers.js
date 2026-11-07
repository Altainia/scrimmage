// The Part tab's inspector: the helpers its sections share (part_inspector.js lists the sections).

import { h, notice } from "./app.js";
import { part, view3d, editOp, cog } from "./part.js";
import { units } from "./units.js";
import { t } from "./i18n.js";
import { toRobot, dirToRobot, normalize } from "./part_frame.js";

if (window.__scrimmagePart) window.__scrimmagePart.toRobot = toRobot;

// -- helpers -------------------------------------------------------------------------------------------------
export const bodyOf = (name) => part.state.bodies.find((b) => b.name === name) || null;
export const rt = () => toRobot(part.state.frame);
const sentence = (m) => { const text = String(m || "").trim(); return text ? text[0].toUpperCase() + text.slice(1) : text; };
export const why = (data) => sentence((data.errors && data.errors[0] && data.errors[0].message) || data.error || t("That didn't work"));
// A translated sentence with elements in it (a <code>, a <strong>): t() is given each of those placeholders as itself,
// and here the elements go in their places, wherever the translation puts them
export const rich = (text, nodes) => text.split(/(\{[A-Za-z_]\w*\})/).map((bit) => nodes[bit.slice(1, -1)] ?? bit);
export const fmtNum = (x, digits = 3) => (typeof x === "number" && isFinite(x) ? String(+x.toFixed(digits)) : "");
// A weight box's placeholder: the weight it has now, to three significant digits in the chosen units
export const placeholderMass = (m) => (typeof m === "number" && isFinite(m) ? String(+units.toDisplay(m, "mass").toPrecision(3)) : "");
// Plugin mode: say on a field whether its value was changed here (in scrimmage-edits.toml) or comes from the CAD
// plugin (the state's body.changed lists the overlay's keys), as data-from="here" | "plugin" and a line.
export function origin(c, b, keys, row) {
  if (!c.plugin) return row;
  const here = keys.some((k) => (b.changed || []).includes(k));
  row.dataset.from = here ? "here" : "plugin";
  row.append(h("p", { class: "hint part-origin", text: here ? t("Changed here. Empty it to go back to what the CAD plugin wrote.")
    : t("From the CAD plugin.") }));
  return row;
}
export const selSig = () => `${part.sel.kind}:${[...part.sel.keys].join("\n")}`;
export const AXIS_WORDS = [() => t("x (forward)"), () => t("y (left)"), () => t("z (up)")];

// The joint as set_joint takes it (`patch` replaces fields; null fields are left out).
export function jointSpec(j, patch = {}) {
  const out = {};
  for (const k of ["kind", "anchor", "axis", "range", "spring", "damping", "frictionloss", "limit_solref"]) {
    const v = k in patch ? patch[k] : j[k];
    if (v !== null && v !== undefined) out[k] = v;
  }
  return out;
}

// set_joint for body `name`, built when the edit runs (from the state then), with `patch` on its joint.
export function patchJoint(name, patch) {
  return editOp("set_joint", () => {
    const b = bodyOf(name);
    if (!b || !b.joint) return { body: name, parent: b ? b.parent : null, joint: null };
    if (part.state.mode === "plugin") return { body: name, joint: patch };
    return { body: name, parent: b.parent, joint: jointSpec(b.joint, patch) };
  });
}

// "along z (up)" for an axis along a robot axis, else its three numbers.
export function axisWords(axisCad) {
  const a = normalize(dirToRobot(rt(), axisCad));
  const i = a.findIndex((x) => Math.abs(x) > 0.999);
  if (i >= 0) return t("along {axis}", { axis: `${a[i] < 0 ? "−" : ""}${AXIS_WORDS[i]()}` });
  return t("along ({numbers})", { numbers: a.map((x) => x.toFixed(2)).join(", ") });
}
export const pointWords = (p) => cog(p);

export function needView() {
  if (view3d.startPick) return true;
  notice(t("Picking needs the 3D view, which isn't working here."), "warn");
  return false;
}

// A small row of buttons.
export const buttons = (label, ...btns) => h("div", { class: "row part-btns", role: "group", "aria-label": label }, ...btns);
export const btn = (id, text, onclick, title = null) => h("button", { type: "button", class: "btn btn-small", id, title, onclick,
  disabled: !!(part.state && part.state.read_only_reason) }, text);

export function descendants(name) {
  const out = new Set();
  let grew = true;
  while (grew) {
    grew = false;
    for (const b of part.state.bodies) if (b.parent && (b.parent === name || out.has(b.parent)) && !out.has(b.name)) { out.add(b.name); grew = true; }
  }
  return out;
}

// A <details> that stays open across redraws.
const opened = new Set();
export function details(key, title, rows, forceOpen = false) {
  const now = document.getElementById(`part-${key}`);   // the one on the page now (its toggle event may not have run)
  if (now) { if (now.open) opened.add(key); else opened.delete(key); }
  const d = h("details", { class: "part-more", id: `part-${key}`, open: opened.has(key) || forceOpen });
  d.addEventListener("toggle", () => { if (d.open) opened.add(key); else opened.delete(key); });
  d.append(h("summary", { text: title }), ...rows);
  return d;
}

