// The Test tab's dialogs: Block settings… and the sweep form (Try values…, Try materials…) (test.js is the tab).

import { $, h, api } from "./app.js";
import { placeOf, sweepNumber, blockSettings, setBlockSettings } from "./blocks_defs.js";
import { t } from "./i18n.js";
import { schedule, flush, refresh, check, fits, renderTop } from "./test.js";
import { answered, T, fmt } from "./test_state.js";
import { gripText } from "./test_floor.js";

const MAX_SWEEP = 12;       // scrimmage/blocks/sweep.py's

// -- block settings ---------------------------------------------------------------------------------------------------
// "Block settings…" (a block's context menu): the keys a block takes with no box on it (blocks_defs.js blockSettings:
// a timeout, a drive's or turn's volts, a repeat until's max). Empty is the run's default (the key left out). Saved
// like any change to the blocks; Undo doesn't take it back (Blockly has no event for a block's data).
export function registerSettings() {
  const B = window.Blockly;
  const reg = B.ContextMenuRegistry.registry;
  if (reg.getItem("scrimmage-block-settings")) return;
  reg.register({
    id: "scrimmage-block-settings",
    scopeType: B.ContextMenuRegistry.ScopeType.BLOCK,
    weight: -2,
    displayText: () => t("Block settings…"),
    preconditionFn: (scope) => {
      const b = scope.block;
      if (!b || b.isInFlyout || !blockSettings(b, T.state && T.state.block_defaults).length) return "hidden";
      return T.state && !T.state.running ? "enabled" : "disabled";
    },
    callback: (scope) => openSettings(scope.block.id),
  });
}

export function openSettings(id) {
  const b = T.ws && T.ws.getBlockById(id);
  const list = b ? blockSettings(b, T.state && T.state.block_defaults) : [];
  if (!list.length) return;
  const d = $("#test-settings-dialog");
  T.settingsFor = { id, list };
  $("#test-settings-for").textContent = t("For “{block}”. Empty a box for the default.", { block: b.toString() });
  $("#test-settings-fields").replaceChildren(...list.map((s, i) => h("div", { class: "field" },
    h("label", { for: `test-settings-${i}`, text: s.label }),
    h("span", { class: "with-unit" },
      h("input", { id: `test-settings-${i}`, "data-key": s.key, inputmode: s.whole ? "numeric" : "decimal", autocomplete: "off",
        value: s.value === null ? "" : fmt(s.value), placeholder: fmt(s.def) }),
      h("span", { class: "unit", text: s.unit })),
    h("p", { class: "hint", text: s.hint }))));
  showSettingsErrors([]);
  d.showModal();
  const first = $("#test-settings-0");
  if (first) first.focus();
}

function showSettingsErrors(list) {
  const ul = $("#test-settings-errors");
  ul.replaceChildren(...list.map((m) => h("li", { text: m })));
  ul.hidden = !list.length;
}

export function submitSettings(e) {
  e.preventDefault();
  const { id, list } = T.settingsFor || {};
  const b = id && T.ws && T.ws.getBlockById(id);
  if (!b) { $("#test-settings-dialog").close(); return; }
  const values = {}, errors = [];
  list.forEach((s, i) => {
    const text = $(`#test-settings-${i}`).value.trim();
    if (text === "") { values[s.key] = null; return; }
    const v = Number(text);
    const lo = s.min, hi = s.max;
    if (!isFinite(v) || (s.whole && !Number.isInteger(v)) || (lo !== undefined && v < lo) || (hi !== undefined && v > hi)) {
      errors.push(s.whole ? t("{setting}: type a whole number from {low} to {high}", { setting: s.label, low: lo, high: hi })
        : hi === undefined ? t("{setting}: type a number of {low} or more", { setting: s.label, low: lo })
          : t("{setting}: type a number from {low} to {high}", { setting: s.label, low: lo, high: hi }));
      return;
    }
    values[s.key] = v;
  });
  if (errors.length) { showSettingsErrors(errors); return; }
  setBlockSettings(b, values);
  $("#test-settings-dialog").close();
  check();
  schedule();       // no Blockly event carries a block's data: save it as a change to the blocks
  renderTop();
}

// -- sweeps -----------------------------------------------------------------------------------------------------------
export function registerTryValues() {
  const B = window.Blockly;
  const reg = B.ContextMenuRegistry.registry;
  if (reg.getItem("scrimmage-try-values")) return;
  reg.register({
    id: "scrimmage-try-values",
    scopeType: B.ContextMenuRegistry.ScopeType.BLOCK,
    weight: -1,
    displayText: () => t("Try values…"),
    preconditionFn: (scope) => {
      const b = scope.block;
      if (!sweepNumber(b) || !placeOf(T.ws).has(b.id)) return "hidden";
      return T.state && !T.state.running ? "enabled" : "disabled";
    },
    callback: (scope) => tryBlock(scope.block.id),
  });
}

export function tryBlock(id) {
  const b = T.ws && T.ws.getBlockById(id);
  const n = sweepNumber(b);
  if (!n || !placeOf(T.ws).has(id)) return;
  openSweep([{ label: n.key, target: { key: n.key }, block: id, value: Number(b.getFieldValue(n.field)), unit: n.unit,
    ...(n.scale ? { scale: n.scale } : {}) }],
    t("“{block}”", { block: b.toString() }));
}

// A choice with `materials` (a grip) is tried over the ticked materials, not from and to.
export function openSweep(choices, what) {
  const d = $("#test-sweep-dialog");
  T.sweepFor = choices;
  const mats = choices.some((c) => c.materials);
  $("#test-sweep-dialog-title").textContent = mats ? t("Try materials") : t("Try values");
  $("#test-sweep-for").textContent = mats ? t("Run the test once for each material you tick, for {what}, to see which grips best.", { what })
    : t("Run the test once for each value of {what}, to see which works best.", { what });
  $("#test-sweep-which-label").textContent = mats ? t("Which pads?") : t("Which number?");
  $("#test-sweep-range").hidden = mats;
  $("#test-sweep-list-field").hidden = mats;
  $("#test-sweep-limit").textContent = mats ? t("The test runs once for each material you tick; your files don't change.")
    : t("Up to {max} values. The test runs once for each; your files don't change.", { max: MAX_SWEEP });
  const list = $("#test-sweep-materials");
  list.hidden = !mats;
  const pal = (T.state && T.state.palette) || {};
  list.replaceChildren(h("legend", { text: t("Materials") }), ...(mats ? pal.grip_materials || [] : []).map((m, i) =>
    h("label", { class: "test-hook", for: `test-sweep-mat-${i}` },
      h("input", { type: "checkbox", id: `test-sweep-mat-${i}`, value: m[0], checked: true }), h("span", { text: gripText(m) }))));
  const now = (c) => (c.materials ? c.now || null : `${fmt(c.value)}${c.unit ? ` ${c.unit}` : ""}`);
  const which = $("#test-sweep-which");
  which.replaceChildren(...choices.map((c, i) => h("option", { value: String(i),
    text: now(c) === null ? c.label : t("{setting} (now {value})", { setting: c.label, value: now(c) }) })));
  $("#test-sweep-which-field").hidden = choices.length < 2;
  const fill = () => {
    const c = choices[Number(which.value) || 0];
    if (c.materials) return;
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
  (mats ? list.querySelector("input") || which : $("#test-sweep-from")).focus();
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
    if (bad !== undefined) return t("“{text}” isn't a number: type numbers with commas between them", { text: bad });
    if (vs.length > MAX_SWEEP) return t("Try at most {max} values at a time (that's {count})", { max: MAX_SWEEP, count: vs.length });
    return vs;
  }
  const a = Number(from), b = Number(to), n = Number(steps);
  if (from.trim() === "" || to.trim() === "" || !isFinite(a) || !isFinite(b)) return t("Type a number in From and To (or type the values)");
  if (!Number.isInteger(n) || n < 1 || n > MAX_SWEEP) return t("Steps is a whole number from 1 to {max}", { max: MAX_SWEEP });
  if (n === 1) return [a];
  return Array.from({ length: n }, (_, k) => +(a + ((b - a) * k) / (n - 1)).toPrecision(6));
}

export async function submitSweep(e) {
  e.preventDefault();
  const choices = T.sweepFor || [];
  const c = choices[Number($("#test-sweep-which").value) || 0];
  if (!c) return;
  const values = c.materials
    ? [...$("#test-sweep-materials").querySelectorAll("input:checked")].map((b) => b.value)
    : sweepValues($("#test-sweep-from").value, $("#test-sweep-to").value, $("#test-sweep-steps").value, $("#test-sweep-list").value);
  if (Array.isArray(values) && !values.length) { showSweepErrors([t("Tick at least one material to try")]); return; }
  if (typeof values === "string") { showSweepErrors([values]); return; }
  let target = c.target;
  await flush();      // a sweep runs the saved test, as Run does: only the test on screen, with nothing to fix
  if (T.saveError || T.local.length || !fits()) {
    check();
    showSweepErrors([t("Fix the problems first (they are listed over Run): the test can't run like this yet.")]);
    return;
  }
  if (c.block) {
    const place = placeOf(T.ws).get(c.block);
    if (!place) { showSweepErrors([t("That block isn't in the test any more.")]); return; }
    target = { block: place, key: c.target.key };
  }
  $("#test-sweep-go").disabled = true;
  const sent = c.scale ? values.map((v) => +(v * c.scale).toPrecision(9)) : values;    // the file's unit
  const { status, data } = await api("POST", "/api/test/sweep", { target, values: sent });
  $("#test-sweep-go").disabled = false;
  if (status !== 200) {
    const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || answered(status);
    showSweepErrors([t("Can't try them: {why}", { why })]);
    return;
  }
  $("#test-sweep-dialog").close();
  await refresh();
}
