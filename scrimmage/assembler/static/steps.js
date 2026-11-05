// The step timeline (left of the Scenario tab): one row per step with its name, mode chip, start time and
// duration, a to-scale bar, check / screenshot counts, an error dot and the last run's result dot. Add,
// duplicate, delete, move up / down (buttons and Alt+↑/↓) and drag to reorder; ↑/↓ move the selection.

import { h, notice } from "./app.js";
import { scEdit, selectStep, stepOfField, sc } from "./scenario.js";
import { icon, guard } from "./step_editor.js";
import { t, tn } from "./i18n.js";

const t3 = (secs) => `${+secs.toFixed(3)}`;
const dur = (d) => (typeof d === "number" && d > 0 ? d : 0);

let dragFrom = null;

export function renderTimeline(st) {
  const root = document.getElementById("timeline");
  const s = st.state;
  const steps = s.steps;
  const hadFocus = root.contains(document.activeElement) ? document.activeElement : null;
  const focusRow = hadFocus && hadFocus.classList.contains("step-row");
  const focusKey = hadFocus && hadFocus.dataset.key;
  const last = steps[steps.length - 1];
  const total = last ? last.start_t + dur(last.duration) : 0;
  const bad = new Set();
  for (const e of [...s.errors, ...st.lastErrors]) {
    const i = stepOfField(e.field);
    if (i !== null) bad.add(i);
  }
  const ro = !!s.read_only;
  const sel = st.selected;

  const list = h("ol", { class: "steps", role: "listbox", "aria-label": t("Steps") });
  steps.forEach((step, i) => {
    const mode = typeof step.mode === "string" ? step.mode : "?";
    const known = ["disabled", "auto", "teleop", "test"].includes(mode);
    const d = dur(step.duration);
    const left = total ? (step.start_t / total) * 100 : 0;
    const width = total ? (d / total) * 100 : 0;
    const result = st.stepStatus ? st.stepStatus[i] : null;
    const shots = step.shots.length + (step.sheet ? 1 : 0);
    const row = h("li", {
      id: `step-row-${i}`, class: `step-row${i === sel ? " selected" : ""}${bad.has(i) ? " has-errors" : ""}`,
      role: "option", "aria-selected": String(i === sel), tabindex: i === sel ? "0" : "-1", draggable: ro ? null : "true",
      dataset: { step: String(i) },
      "aria-label": rowLabel(i, step, mode, d, bad.has(i)),
      onclick: () => selectStep(i, { focus: true }),
      onkeydown: (e) => onRowKey(e, i, steps.length, ro),
    },
      h("span", { class: "grip", "aria-hidden": "true", title: t("Drag to reorder") }, icon("grip")),
      h("div", { class: "step-main" },
        h("div", { class: "step-line" },
          h("span", { class: "step-num" }, `${i + 1}`),
          h("span", { class: `step-name${step.name ? "" : " unnamed"}`, text: step.name || t("step {n}", { n: i + 1 }) }),
          h("span", { class: `chip mode-chip mode-${known ? mode : "unknown"}`, text: mode })),
        h("div", { class: "step-line step-meta" },
          h("span", { class: "time", title: t("Runs {seconds} s, from {from} s to {to} s", { seconds: t3(d), from: t3(step.start_t), to: t3(step.start_t + d) }) },
            h("strong", {}, t("{seconds} s", { seconds: t3(d) })), h("span", { class: "muted" }, "  ", t("from {seconds} s", { seconds: t3(step.start_t) }))),
          h("span", { class: "badges" },
            step.checks.length ? h("span", { class: "badge", title: tn("{count} check", "{count} checks", step.checks.length) }, icon("check"), String(step.checks.length)) : null,
            shots ? h("span", { class: "badge", title: step.sheet ? tn("{count} screenshot and a contact sheet", "{count} screenshots and a contact sheet", step.shots.length) : tn("{count} screenshot", "{count} screenshots", step.shots.length) }, icon("camera"), String(shots)) : null,
            bad.has(i) ? h("span", { class: "dot dot-error", title: t("This step has errors") }) : null,
            result ? h("span", { class: `dot run-dot run-${result}`, title: result === "pass" ? t("Last run: passed") : t("Last run: failed") }) : null)),
        h("div", { class: "bar", "aria-hidden": "true" },
          h("span", { class: `bar-fill mode-bg-${known ? mode : "unknown"}`, style: `left:${left}%;width:${Math.max(width, 0.8)}%` }))));
    if (!ro) {
      row.addEventListener("dragstart", (e) => { dragFrom = i; row.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); });
      row.addEventListener("dragend", () => { dragFrom = null; row.classList.remove("dragging"); clearDrop(root); });
      row.addEventListener("dragover", (e) => {
        if (dragFrom === null) return;
        e.preventDefault();
        clearDrop(root);
        if (dragFrom !== i) row.classList.add(dragFrom < i ? "drop-after" : "drop-before");
      });
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = dragFrom;
        dragFrom = null;
        clearDrop(root);
        if (from !== null && from !== i) move(from, i);
      });
    }
    list.append(row);
  });

  const n = steps.length;
  const btn = (label, ic, onclick, disabled, extra = "") => h("button", {
    type: "button", class: `btn btn-small ${extra}`, "aria-label": label, title: label, disabled: disabled || ro, onclick, dataset: { key: `tl-${ic}` },
  }, icon(ic));
  const tools = h("div", { class: "timeline-tools", role: "toolbar", "aria-label": t("Step actions") },
    h("button", {
      type: "button", class: "btn btn-small btn-primary-soft", disabled: ro, dataset: { key: "tl-add" },
      onclick: () => addStep(),
    }, icon("plus"), " ", t("Add step")),
    h("span", { class: "spacer" }),
    btn(t("Move the step up (Alt+↑)"), "up", () => move(sel, sel - 1), sel === null || sel === 0),
    btn(t("Move the step down (Alt+↓)"), "down", () => move(sel, sel + 1), sel === null || sel >= n - 1),
    btn(t("Duplicate the step"), "copy", () => duplicate(sel), sel === null),
    btn(t("Delete the step"), "trash", () => remove(sel), sel === null || n <= 1, "btn-danger"));

  guard(() => root.replaceChildren(...[
    total ? h("div", { class: "timeline-total" }, h("span", {}, tn("{count} step", "{count} steps", n)), h("span", {}, t("{seconds} s in all", { seconds: t3(total) }))) : null,
    list,
    n ? null : h("p", { class: "empty" }, t("No steps yet.")),
    tools].filter(Boolean)));
  if (focusRow && sel !== null) root.querySelector(`[data-step="${sel}"]`)?.focus();
  else if (focusKey) {
    const el = root.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
    if (el && !el.disabled) el.focus();
    else if (sel !== null) root.querySelector(`[data-step="${sel}"]`)?.focus();
  }
}

// A row's label for a screen reader: "Step 2 intake, auto, 1.5 seconds from 2, 3 checks, has errors"
function rowLabel(i, step, mode, d, bad) {
  const params = { n: i + 1, name: step.name, mode, seconds: t3(d), from: t3(step.start_t) };
  let label = step.name ? t("Step {n} {name}, {mode}, {seconds} seconds from {from}", params)
    : t("Step {n}, {mode}, {seconds} seconds from {from}", params);
  // (a list of clauses, each a whole phrase)
  if (step.checks.length) label += tn(", {count} check", ", {count} checks", step.checks.length);
  if (bad) label += t(", has errors");
  return label;
}

function clearDrop(root) {
  for (const el of root.querySelectorAll(".drop-before, .drop-after")) el.classList.remove("drop-before", "drop-after");
}

function onRowKey(e, i, n, ro) {
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    const j = e.key === "ArrowUp" ? i - 1 : i + 1;
    e.preventDefault();
    if (j < 0 || j >= n) return;
    if (e.altKey && !ro) move(i, j);
    else selectStep(j, { focus: true });
  } else if (e.key === "Home" || e.key === "End") {
    e.preventDefault();
    selectStep(e.key === "Home" ? 0 : n - 1, { focus: true });
  } else if (e.key === "Delete" && !ro && n > 1) {
    e.preventDefault();
    remove(i);
  }
}

async function addStep() {
  const s = sc.state;
  const sel = sc.selected;
  const cur = sel !== null ? s.steps[sel] : null;
  const mode = cur && ["disabled", "auto", "teleop", "test"].includes(cur.mode) ? cur.mode : "teleop";
  const after = sel === null ? null : sel;
  sc.pendingSelect = after === null ? s.steps.length : after + 1;
  const r = await scEdit("add_step", { after, mode, duration: 1.0 });
  if (r.ok) document.getElementById("ed-name")?.focus();
}

async function move(i, j) {
  sc.pendingSelect = j;
  const r = await scEdit("move_step", { step: i, to: j });
  if (r.ok) document.querySelector(`#timeline [data-step="${j}"]`)?.focus();
}

function duplicate(i) {
  sc.pendingSelect = i + 1;
  return scEdit("duplicate_step", { step: i });
}

async function remove(i) {
  const s = sc.state;
  const name = s.steps[i] && s.steps[i].name;
  const r = await scEdit("delete_step", { step: i });
  if (r.ok) {
    notice(name ? t("Deleted step {n} ({name}). Press Ctrl+Z (or Undo) to bring it back.", { n: i + 1, name })
      : t("Deleted step {n}. Press Ctrl+Z (or Undo) to bring it back.", { n: i + 1 }), "info");
  }
}
