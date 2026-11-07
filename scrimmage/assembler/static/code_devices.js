// The robot code panel's device check and its battery settings (code.js holds the panel and its state).
//
// The device check (under the buttons, once a check or a drive has run): every device the model expects beside
// what the code made (long-polled GET /api/robot/devices; it fills in as the code makes devices, teleop's too).
// Status is an icon and a bold word: ✅ matches, ⏳ waiting, ❌ nothing with this ID (with "Use …" choices: devices of
// the same type the code made that nothing uses), ➕ not in the model ("Add as…" opens a part's device IDs in the
// inspector; nothing is written), ↔ another type (a link to the part), – not checked yet (PWM, DIO, analog...).
// "Apply the matches and restart" writes the chosen IDs (op match_devices, one undo step) and restarts the robot
// ("restarting", then what the device check's restart says: restarted, or failed and why).
// "More settings" holds the [battery] fields (op battery). The Drive tab shows a line while devices don't match.

import { h, api, edit, notice, store, fmt, select } from "./app.js";
import { t, tn } from "./i18n.js";
import { clean, badNumber } from "./inspector.js";
import { code, els, loadState, renderRun } from "./code.js";

const DEV = {
  ok: ["✅", () => t("Matches")], waiting: ["⏳", () => t("Waiting")], missing: ["❌", () => t("Nothing with this ID")],
  extra: ["➕", () => t("Not in the model")], other_type: ["↔", () => t("Another type")], not_checked: ["–", () => t("Not checked yet")],
};
const SHORT_KIND = { TalonFX: "TalonFX", CANcoder: "CANcoder", Pigeon2: "Pigeon2", "SPARK MAX": "SparkMax", "SPARK Flex": "SparkFlex" };
export const BATTERY = [
  { key: "nominal_voltage", id: "code-battery-voltage", label: () => t("Battery voltage"), unit: "V", step: "0.1",
    help: () => t("The battery's voltage when it is charged and resting. A fresh competition battery reads about 12.5 to 13 V.") },
  { key: "resistance", id: "code-battery-resistance", label: () => t("Battery resistance"), unit: "Ω", step: "0.001",
    help: () => t("How much the voltage drops when the motors pull hard. About 0.02 Ω is a good battery, an old one 0.03 Ω or more; 0 means it never drops.") },
];

// -- the device check -------------------------------------------------------------------------------------------
export async function pollDevices() {
  let since = null;
  for (;;) {
    const { status, data } = await api("GET", "/api/robot/devices" + (since === null ? "" : `?since=${since}`));
    if (status !== 200) { since = null; await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 3000)); continue; }
    since = data.version;
    code.devices = data;
    window.dispatchEvent(new CustomEvent("scrimmage:devices", { detail: { devices: data } }));
    renderDevices();
  }
}

const partOf = (slot) => slot.split(".")[0];
const slotKeyOf = (slot) => slot.slice(slot.indexOf(".") + 1).replace(/\[\d+\]$/, "");
const categoryOf = (kind) => (/encoder|CANcoder/i.test(kind) ? "encoder" : /gyro|Pigeon/i.test(kind) ? "gyro" : "motor");
function shortText(made) {   // "TalonFX 1" -> "TalonFX:1", as the part inspector's short form writes it
  const m = /^(.*) (\d+)$/.exec(made || "");
  return m && SHORT_KIND[m[1]] ? `${SHORT_KIND[m[1]]}:${m[2]}` : made;
}

export function renderDevices() {
  if (!els) return;
  const d = code.devices, rows = d ? d.rows : [];
  els.stopBtn.hidden = !(d && d.live);   // a running robot blocks opening something else and scenario runs
  if (code.run && code.run.live && !(d && d.live)) { code.run = null; renderRun(); }
  restartOutcome(d);
  els.devices.hidden = !(d && (rows.length || d.live));
  if (els.devices.hidden) return;
  const ro = !!(store.state && store.state.read_only);
  // drop choices that no longer apply (the row matches now, or the device went)
  for (const [slot, dev] of Object.entries(code.matches)) {
    const r = rows.find((x) => x.slot === slot);
    if (!r || r.status !== "missing" || !r.suggest.includes(dev)) delete code.matches[slot];
  }
  const bad = rows.filter((r) => r.status === "missing" || r.status === "other_type").length;
  els.devState.replaceChildren(
    ...(bad ? [h("strong", { class: "dev-bad-count", text: tn("{count} device doesn't match the model.", "{count} devices don't match the model.", bad) + " " })] : []),
    d.live && !d.settled ? t("Checking… the robot has run {time} s.", { time: fmt(d.robot_time) }) + " " : "",
    d.live ? t("The robot is running: devices your code makes later (in teleop, say) show up here as they appear.")
      : t("From the last time the robot ran."));
  els.devBody.replaceChildren(...rows.map((r) => deviceRow(r, ro)));
  const n = Object.keys(code.matches).length;
  els.apply.disabled = ro || !n;
  els.apply.textContent = d.live ? t("Apply the matches and restart") : t("Apply the matches");
  const a = code.applied;
  els.applyStatus.hidden = !a;
  if (a) {
    els.applyStatus.className = `code-apply-status code-apply-${a.kind}`;
    els.applyStatus.replaceChildren(h("span", { class: "dev-icon", "aria-hidden": "true", text: a.kind === "ok" ? "✅" : "⚠️" }), h("span", { text: a.text }));
  }
}

function deviceRow(r, ro) {
  const [icon, word0] = DEV[r.status] || ["", () => r.status];
  const word = r.status === "missing" && r.made ? t("Not connected") : word0();
  const actions = h("div", { class: "dev-actions" });
  if (r.status === "missing") {
    for (const s of r.suggest) {
      const chosen = code.matches[r.slot] === s;
      actions.append(h("button", { type: "button", class: `btn btn-small${chosen ? " btn-primary-soft" : ""}`, dataset: { use: s },
        "aria-pressed": String(chosen), disabled: ro || undefined, onclick: () => choose(r.slot, s) }, t("Use {device}", { device: s })));
    }
  } else if (r.status === "other_type") {
    actions.append(h("button", { type: "button", class: "linklike", dataset: { part: partOf(r.slot) },
      onclick: () => openPart(partOf(r.slot), { slot: slotKeyOf(r.slot) }) }, t("Open {part} in the part inspector", { part: partOf(r.slot) })));
  } else if (r.status === "extra") {
    actions.append(h("button", { type: "button", class: "btn btn-small", dataset: { add: r.made }, disabled: ro || undefined,
      onclick: () => { code.adding = code.adding === r.made ? null : r.made; code.addSelect = null; renderDevices(); } }, t("Add as…")));
    if (code.adding === r.made) actions.append(addChooser(r));
  }
  return h("tr", { class: `dev-row dev-${r.status}`, dataset: { slot: r.slot || "", status: r.status, made: r.made || "" } },
    h("td", { class: "mono" }, r.slot ? r.slot : h("span", { class: "muted", text: t("(none)") })),
    h("td", {}, r.expected || h("span", { class: "muted", text: "–" })),
    h("td", {}, r.made || h("span", { class: "muted", text: t("nothing") })),
    h("td", { class: "dev-status" },
      h("p", { class: "dev-head" }, h("span", { class: "dev-icon", "aria-hidden": "true", text: icon }), h("strong", { class: "dev-word", text: word })),
      r.note ? h("p", { class: "dev-note", text: r.note + (/[.!?]$/.test(r.note) ? "" : ".") }) : null,   // it starts with the slot's name: kept as written
      actions.children.length ? actions : null));
}

function choose(slot, dev) {
  if (code.matches[slot] === dev) delete code.matches[slot];
  else {
    for (const [k, v] of Object.entries(code.matches)) if (v === dev) delete code.matches[k];   // one slot per device
    code.matches[slot] = dev;
  }
  code.applied = null;
  renderDevices();
}

// "Add as…": which part the device is on; that part's device IDs then open in the inspector, for the student to type it in.
function addChooser(r) {
  if (!code.addSelect) {
    const sel = h("select", { id: "code-add-part", "aria-label": t("Which part is {device} on?", { device: r.made }) },
      h("option", { value: "" }, t("Which part is it on?")),
      ...((store.state && store.state.parts) || []).map((p) => h("option", { value: p.name }, p.name)));
    sel.addEventListener("change", () => {
      if (!sel.isConnected || !sel.value) return;
      const made = code.adding, part = sel.value;
      code.adding = null; code.addSelect = null;
      renderDevices();
      openPart(part, { kind: categoryOf(r.kind) });
      notice(t("Type {device} into the slot of {part} it belongs to (Device IDs, in the inspector).", { device: shortText(made), part }), "info", "code-add");
    });
    code.addSelect = sel;
  }
  return h("span", { class: "dev-add" }, code.addSelect);
}

// Select a part and put the inspector's cursor on one of its device slots (by name, or the first of a kind).
function openPart(name, { slot = null, kind = null } = {}) {
  if (!select(name)) { notice(t("There is no part named {name}.", { name }), "warn"); return; }
  const fields = [...document.querySelectorAll(`#inspector [data-field^="${CSS.escape(name)}/ids:"]`)];
  const f = fields.find((x) => (slot ? x.dataset.field === `${name}/ids:${slot}`
    : ((x.querySelector(".kind") || {}).dataset || {}).kind === kind)) || fields[0];
  const target = f ? f.querySelector("input, select") : document.querySelector(`#inspector [data-field="${CSS.escape(name)}/ids"]`);
  if (!target) return;
  target.scrollIntoView({ block: "center" });
  target.focus();
}

export async function applyMatches() {
  const matches = { ...code.matches };
  if (!Object.keys(matches).length) return;
  els.apply.disabled = true;
  const r = await edit("match_devices", { matches }, { ownErrors: true });
  if (r.ok) {
    code.matches = {};
    const d = r.data || {};
    code.applied = d.restarting ? { kind: "ok", text: t("Saved. The robot is restarting with the new IDs…"), restarting: true }
      : d.restart_problem ? { kind: "bad", text: t("Saved, but the robot wasn't restarted: {why}", { why: d.restart_problem }) }
      : { kind: "ok", text: t("Saved. Start and check devices again to see them match.") };
  } else if (!r.conflict) {
    code.applied = { kind: "bad", text: (r.errors || []).map((e) => e.message).join("; ") || t("The matches weren't saved.") };
  } else code.applied = null;
  renderDevices();
}

// What the restart after an Apply came to, from the device check (its restart: restarting, restarted or failed).
function restartOutcome(d) {
  const a = code.applied;
  if (!a || !a.restarting || !d) return;
  if (d.restart === "restarted") {
    code.applied = { kind: "ok", text: t("Saved. The robot restarted with the new IDs; the table fills in again as it starts.") };
  } else if (d.restart === "failed") {
    code.applied = { kind: "bad", text: t("Saved, but the robot didn't start again: {why}. Press Start and check devices.", { why: d.restart_problem || t("it stopped") }) };
  }
}

// -- more settings: the battery ----------------------------------------------------------------------------------
export function renderBattery(ro) {
  const b = code.state && code.state.battery;
  if (!b) return;
  for (const f of BATTERY) {
    const inp = els.battery[f.key];
    inp.disabled = ro;
    inp.placeholder = t("{value} (the default)", { value: fmt(b.defaults[f.key]) });
    // never overwrite what the student is typing (a redraw comes with every state change)
    if (document.activeElement !== inp && !inp.dataset.dirty) inp.value = b[f.key] === null || b[f.key] === undefined ? "" : fmt(b[f.key]);
  }
}

export async function saveBattery(f, inp) {
  const box = inp.closest(".field").querySelector(":scope > .field-msgs");
  const say = (msg) => box.replaceChildren(...(msg ? [h("p", { class: "error", role: "alert", text: msg })] : []));
  clean(inp);
  if (badNumber(inp)) { say(t("Enter a number.")); return; }
  const v = inp.value.trim() === "" ? null : Number(inp.value);
  say(null);
  const r = await edit("battery", { [f.key]: v }, { ownErrors: true });
  if (!r.ok && !r.conflict) say((r.errors || []).map((e) => e.message).join("; ") || t("That wasn't saved."));
  if (r.ok) await loadState();
}
