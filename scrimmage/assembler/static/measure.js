// The Measure tab: one measurement file (`scrimmage edit <name>.measure.toml`). See /api/measure/* in the docstring
// of scrimmage/assembler/server.py, and scrimmage/assembler/measure_session.py for the file's JSON.
//
//   Top        the file's tests (a ball file: size, squeeze, drop); one is shown at a time.
//   Left       the test's picture, what you need and the steps (the printed form's).
//   Right      "Print a blank form" (the test's form page, /forms/<test>.html) and, for shots and strokes, Fit; who
//              measured, when, with what; the setup (once for all trials); the trials, each reading with a unit
//              picker over its column, the value each trial gives and a flag on a trial that looks off; then the
//              average, lowest, highest, spread and warnings, and the fit.
//   Status     saved or not.
//
// The page keeps its own copy of the file (`m.file`). It sends it to /api/measure/summary 300 ms after the last
// change (the summary is the server's: the same code as `scrimmage measure`), and saves it, like every tab, 600 ms
// after the last change, or at once when a box loses focus or the tab is left (/api/measure/save keeps the file's
// comments). A box that holds something other than a number where a number goes holds the save back and says so. A
// 409 (the file changed meanwhile) shows the saved version and says so. Test hook: window.__scrimmageMeasure {store,
// saved (saves now, and resolves when every save is done), summarized (a promise for the summary under way)}.

import { $, h, api, notice, registerTab, sessionToken, setTab, showPath } from "./app.js";
import { units } from "./units.js";
import { t, tn, onChange } from "./i18n.js";

const SUMMARY_DELAY = 300;
const SAVE_DELAY = 600;

export const m = {
  state: null,        // /api/measure/state (its version: the file as this page last saved or read it)
  file: null,         // the file as edited here
  saved: "",          // canon() of the file as saved
  test: null,         // the test shown
  units: {},          // test -> reading name -> the unit its column's picker shows ("mixed" when the trials differ)
  summary: null,      // test -> {error, fields}: of the file as typed (the state's until the first change)
  summaryError: null, // why the file as typed can't be summarized (a bad date), or null
  saving: false,
  saveError: null,
  fit: {},            // test -> the last fit, or {error}
  fitStale: {},       // test -> the trials changed since the fit
  fitting: false,
  appPath: "",
};

let summaryTimer = null, summarySeq = 0, summarizing = Promise.resolve();
let saveTimer = null, saving = Promise.resolve();
let polled = null;              // the newest version the long poll has seen
let later = null;               // a state the long poll brought while a save was under way: applied after it
const ownVersions = new Set();  // the versions this page's saves made

const testOf = (name) => m.state.tests.find((tst) => tst.name === name);
const shown = () => testOf(m.test);
const editable = () => !!m.file;
const waiting = () => !!saveTimer || m.saving;

// The file without blank cells, as text: two files that would write the same are the same.
function canon(file) {
  if (!file) return "";
  const part = (p) => ({ cells: Object.fromEntries(Object.entries(p.cells).filter(([, c]) => c.value !== null && c.value !== "")
    .map(([k, c]) => [k, { unit: c.unit, value: c.value }])), extra: p.extra });
  const tests = Object.fromEntries(Object.entries(file.tests).map(([tst, v]) => [tst, { setup: part(v.setup),
    trials: v.trials.map((r) => ({ origin: r.origin, ...part(r) })) }]));
  return JSON.stringify({ measure: file.measure, tests });
}
const copy = (x) => JSON.parse(JSON.stringify(x));
const why = (data, status) => data.error || (data.errors && data.errors[0] && data.errors[0].message) || t("the server answered {status}", { status });

// -- the units over each column ------------------------------------------------------------------
// A reading's unit before one is picked or typed (a new column, a setup box): the Units setting's, where the reading
// takes it (a key's unit is the file's: the picker stays, and what is typed is in the unit it shows). Measure files
// have no pressure reading, so no psi or kPa here; a force read on a scale is lb in Imperial and N in Metric.
const SYSTEM_UNITS = { imperial: { length: "in", mass: "lb", force: "lb", weight: "lb" },
  metric: { length: "mm", mass: "kg", force: "n", weight: "n" } };
export function defaultUnit(r, sys = units.system) {
  const u = (SYSTEM_UNITS[sys] || {})[r.quantity];
  return u && r.units.some((x) => x.unit === u) ? u : r.unit;
}
const usedUnits = (test, name) => new Set(m.file.tests[test].trials.map((row) => row.cells[name])
  .filter((c) => c && c.value !== null && c.value !== "").map((c) => c.unit));
function columnUnits(test) {
  const tst = testOf(test), out = {};
  for (const r of tst.trial) {
    if (!r.units.length) continue;
    const used = usedUnits(test, r.name);
    out[r.name] = used.size === 1 ? [...used][0] : used.size ? "mixed" : defaultUnit(r);
  }
  return out;
}
// The Units setting changed (from `was`): an empty column still on the old default takes the new one; the summary's
// numbers and the fit's speeds are shown again in the new units. A box being typed in keeps its focus and caret.
let unitsWas = null;
function unitsChanged(sys) {
  const was = unitsWas;
  unitsWas = sys;
  if (!m.state || !m.file) return;
  for (const tst of m.state.tests) {
    const cols = m.units[tst.name];
    if (!cols) continue;
    for (const r of tst.trial) {
      if (r.units.length && !usedUnits(tst.name, r.name).size && cols[r.name] === defaultUnit(r, was)) cols[r.name] = defaultUnit(r, sys);
    }
  }
  const act = document.activeElement;
  const keep = act && act.closest && act.closest("#tab-measure") && act.getAttribute("aria-label")
    ? { label: act.getAttribute("aria-label"), start: act.selectionStart, end: act.selectionEnd } : null;
  renderSetup();
  renderTrials();
  renderFit();
  if (keep) {
    const el = [...document.querySelectorAll("#tab-measure [aria-label]")].find((x) => x.getAttribute("aria-label") === keep.label);
    if (el) { el.focus(); try { el.setSelectionRange(keep.start, keep.end); } catch { /* not a text box */ } }
  }
  if (editable()) summarizing = summarize();
  refit();
}
// The Language setting changed: the page's words again, and the server's (the tests' words, the summary's and the
// fit's) asked for again; a box being typed in keeps its text, focus and caret (its value is in m.file already).
async function languageChanged() {
  if (!m.state) return;
  const act = document.activeElement;
  const keep = act && act.closest && act.closest("#tab-measure") && act.dataset && (act.dataset.reading || act.id)
    ? { id: act.id, reading: act.dataset.reading, trial: act.closest("tr[data-trial]")?.dataset.trial, value: act.value,
      start: act.selectionStart, end: act.selectionEnd } : null;
  const { status, data } = await api("GET", "/api/measure/state");
  if (status === 200 && data.version === m.state.version) m.state = { ...data, file: m.state.file };   // the tests' words; the file as typed here
  render();
  if (keep) {
    const sel = keep.id ? `#${CSS.escape(keep.id)}` : `#measure-trials tr[data-trial="${keep.trial}"] [data-reading="${CSS.escape(keep.reading)}"], #measure-setup [data-reading="${CSS.escape(keep.reading)}"]`;
    const el = document.querySelector(sel);
    if (el) {
      if (el.tagName === "INPUT") el.value = keep.value;   // as typed ("9." is 9 in the file)
      el.focus();
      try { el.setSelectionRange(keep.start, keep.end); } catch { /* not a text box */ }
    }
  }
  if (editable()) summarizing = summarize();
  refit();
}

// The fit's words (its band, "stiffness 20.6 lbf ...") are the server's, in the Units setting it fit in: fit again.
async function refit() {
  for (const test of Object.keys(m.fit)) if (m.fit[test] && !m.fit[test].error) await fit(test);
}
function adopt(file) {
  m.file = file ? copy(file) : null;
  m.saved = canon(m.file);
  m.units = {};
  if (m.file) for (const tst of m.state.tests) m.units[tst.name] = columnUnits(tst.name);
}

// -- talking to the server -----------------------------------------------------------------------
function changed() {
  m.saveError = null;
  if (m.test && m.fit[m.test]) m.fitStale[m.test] = true;
  clearTimeout(summaryTimer);
  summaryTimer = setTimeout(() => { summaryTimer = null; summarizing = summarize(); }, SUMMARY_DELAY);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saveTimer = null; queueSave(); }, SAVE_DELAY);
  renderStatus();
  renderFit();
}

// Save now (a change waiting), and wait for every save under way.
function flush() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; queueSave(); }
  return saving;
}
function queueSave() { saving = saving.then(save, save); }

async function summarize() {
  if (!editable()) return;
  const seq = ++summarySeq;
  const { status, data } = await api("POST", "/api/measure/summary", { file: m.file });
  if (seq !== summarySeq) return;   // overtaken by a newer one
  if (status === 200) { m.summary = data.tests; m.summaryError = null; }
  else m.summaryError = why(data, status);
  renderSummary();
}

async function save() {
  if (!editable() || canon(m.file) === m.saved) { renderStatus(); return; }
  const bad = notNumbers();
  if (bad) { m.saveError = bad; renderStatus(); return; }
  const sent = copy(m.file);
  const rows = Object.fromEntries(Object.entries(m.file.tests).map(([tst, p]) => [tst, p.trials.slice()]));
  m.saving = true; m.saveError = null; renderStatus();
  try {
    const { status, data } = await api("POST", "/api/measure/save", { file: sent, version: m.state.version });
    if (status === 200 && data.ok) {
      ownVersions.add(data.version);
      m.state = { ...m.state, version: data.version };
      // the file now holds the trials sent, in order: number them as the file does (trials typed meanwhile too)
      for (const part of Object.values(sent.tests)) part.trials.forEach((row, i) => { row.origin = i; });
      m.saved = canon(sent);
      for (const [tst, part] of Object.entries(m.file.tests)) {
        part.trials.forEach((row) => { const i = rows[tst].indexOf(row); row.origin = i >= 0 ? i : null; });
      }
      return;
    }
    if (status === 409 && data.state) {   // the file changed outside this page: show it as saved
      clearTimeout(saveTimer); saveTimer = null;
      m.state = data.state;
      adopt(data.state.file);
      m.summary = data.state.summary;
      m.summaryError = null;
      notice(t("The file changed outside this page (another tab, or an outside edit), so it shows the saved version."), "warn", "measure");
      render();
      return;
    }
    m.saveError = why(data, status);
  } finally {
    m.saving = false;
    renderStatus();
    const s = later;
    later = null;
    if (s) applyState(s);
  }
}

// The page is going away (closed, reloaded, or another file opened from the start page) with a change still
// waiting: send it now, in a request that outlives the page.
function saveOnLeaving() {
  if (!saveTimer || !editable() || notNumbers()) return;
  clearTimeout(saveTimer); saveTimer = null;
  fetch("/api/measure/save", { method: "POST", keepalive: true,
    headers: { "X-Scrimmage-Token": sessionToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ file: m.file, version: m.state.version }) }).catch(() => {});
}

// The first trial or setup box that holds something other than a number where a number goes, in words.
function notNumbers() {
  for (const tst of m.state.tests) {
    const part = m.file.tests[tst.name];
    const check = (readings, p, where) => {
      for (const r of readings) {
        const c = p.cells[r.name];
        if (c && (r.type === "reading" || r.type === "number") && typeof c.value === "string" && c.value !== "")
          return t("{where}: {name} must be a number, not \"{value}\"", { where, name: r.name, value: c.value });
      }
      return null;
    };
    const s = check(tst.setup, part.setup, t("{test}, setup", { test: tst.title }));
    if (s) return s;
    for (let i = 0; i < part.trials.length; i++) {
      const e = check(tst.trial, part.trials[i], t("{test}, trial {n}", { test: tst.title, n: i + 1 }));
      if (e) return e;
    }
  }
  return null;
}

async function fit(test = m.test) {
  if (!editable() || m.fitting) return;
  const asked = canon(m.file);
  m.fitting = true; renderFit();
  try {
    const { status, data } = await api("POST", "/api/measure/fit", { file: m.file, test });
    m.fit[test] = status === 200 ? data : { error: why(data, status) };
    m.fitStale[test] = canon(m.file) !== asked;   // the trials changed while it fit
  } finally {
    m.fitting = false;
    renderFit();
  }
}

// A state from the long poll. Our own saves' only move the version on; a change this page didn't make (an outside
// edit, another tab) is shown when nothing here waits to be saved, else the waiting save is refused (409) and shows it.
function applyState(s) {
  polled = s.version;
  const prev = m.state;
  if (prev && s.version <= prev.version) return;
  if (ownVersions.has(s.version)) { m.state = s; return; }
  if (m.saving) { later = s; return; }   // after the save: it may be the save's own, or a refusal shows it
  if (prev && saveTimer) return;          // the waiting save is refused (409) and shows it
  m.state = s;
  if (prev && canon(s.file) !== m.saved) notice(t("The file changed outside this page, so it was read again."), "info", "measure");
  adopt(s.file);
  m.summary = s.summary;
  m.summaryError = null;
  if (!m.test || !testOf(m.test)) m.test = pickTest();
  render();
}

// The first test with trials, else the first.
function pickTest() {
  const tests = m.state.tests;
  const withTrials = m.state.file && tests.find((tst) => m.state.file.tests[tst.name].trials.length);
  return (withTrials || tests[0] || { name: null }).name;
}

async function pollLoop() {
  for (;;) {
    const since = polled !== null ? `?since=${polled}` : "";
    const { status, data } = await api("GET", "/api/measure/state" + since);
    if (status === 200) applyState(data);
    else await new Promise((r) => setTimeout(r, status === 403 ? 10000 : 2000));
  }
}

// -- rendering -----------------------------------------------------------------------------------
function render() {
  if (!m.state) return;
  renderTop();
  const banner = $("#measure-banner");
  banner.hidden = !m.state.file_error;
  banner.textContent = m.state.file_error
    ? (m.state.file ? t("This file has a problem: {why}", { why: m.state.file_error }) : t("This file can't be edited here until it's fixed: {why}. Fix it in a text editor; the page reads it again on its own.", { why: m.state.file_error }))
    : "";
  renderTests();
  renderHow();
  $("#measure-add").disabled = !editable();
  const tst = shown();
  $("#measure-print").href = tst ? `/forms/${tst.name}.html` : "#";
  $("#measure-fit").hidden = !(tst && tst.fit);
  renderHeader();
  renderSetup();
  renderTrials();
  renderSummary();
  renderFit();
  renderStatus();
}

function renderTop() {
  if (!m.state || $("#tab-measure").hidden || document.body.classList.contains("on-start")) return;
  showPath(m.appPath || m.state.path, m.state.path);
  $("#undo").disabled = true;
  $("#redo").disabled = true;
}

function renderTests() {
  const nav = $("#measure-tests");
  nav.replaceChildren(...m.state.tests.map((tst) => {
    const n = m.file ? m.file.tests[tst.name].trials.length : 0;
    return h("button", { type: "button", class: `measure-test${tst.name === m.test ? " active" : ""}`, dataset: { test: tst.name },
      "aria-pressed": String(tst.name === m.test), onclick: () => { m.test = tst.name; render(); } },
    h("strong", { text: tst.title }), h("span", { class: "muted", text: " · " + (n ? tn("{count} trial", "{count} trials", n) : t("no trials yet")) }));
  }));
}

function renderHow() {
  const tst = shown();
  $("#measure-title").textContent = tst ? tst.title : t("No tests");
  $("#measure-gives").textContent = tst ? t("Gives {what}.", { what: tst.gives }) : "";
  $("#measure-picture").innerHTML = tst ? tst.picture : "";   // our own SVG, from scrimmage/measure/img
  $("#measure-need").replaceChildren(...(tst ? tst.need : []).map((x) => h("li", { text: x })));
  $("#measure-steps").replaceChildren(...(tst ? tst.steps : []).map((x) => h("li", { text: x })));
}

const HEADER = [   // (label: a function, the words of the moment)
  { key: "by", label: () => t("Measured by"), type: "text" },
  { key: "date", label: () => t("Date"), type: "date" },
  { key: "tools", label: () => t("Tools"), type: "text" },
  { key: "about", label: () => t("What you measured"), type: "text" },
];

function renderHeader() {
  const box = $("#measure-header");
  if (!editable()) { box.replaceChildren(); return; }
  const head = m.file.measure;
  const fields = HEADER.map((f) => {
    const input = h("input", { id: `measure-${f.key}`, type: f.type, value: head[f.key] || "", autocomplete: "off",
      oninput: (e) => { head[f.key] = e.target.value; changed(); } });
    return h("div", { class: "measure-field" }, h("label", { for: input.id, text: f.label() }), input);
  });
  if (m.state.kind === "ball") {
    const sel = h("select", { id: "measure-base", onchange: (e) => { head.base = e.target.value; changed(); } },
      h("option", { value: "" }, t("(none)")), ...m.state.bases.map((b) => h("option", { value: b }, b)));
    sel.value = head.base || "";
    fields.push(h("div", { class: "measure-field" }, h("label", { for: "measure-base", text: t("Ball preset it measured") }), sel,
      h("p", { class: "hint", text: t("Its numbers fill in what you didn't measure.") })));
  }
  box.replaceChildren(...fields);
}

// A reading's value box: a number, text, or yes/no. `cell` is {unit, value} (made when first typed in).
function valueBox(r, part, label, unitOf) {
  const cell = part.cells[r.name];
  const set = (value) => {
    if (value === null || value === "") { if (part.cells[r.name]) part.cells[r.name].value = null; }
    else part.cells[r.name] = { unit: part.cells[r.name] ? part.cells[r.name].unit : unitOf(), value };
    changed();
  };
  if (r.type === "bool") {
    const sel = h("select", { "aria-label": label, dataset: { reading: r.name }, onchange: (e) => set(e.target.value === "" ? null : e.target.value === "true") },
      h("option", { value: "" }, t("(not given)")), h("option", { value: "true" }, t("yes")), h("option", { value: "false" }, t("no")));
    sel.value = cell && cell.value !== null ? String(cell.value) : "";
    return sel;
  }
  const number = r.type !== "text";
  const value = cell && cell.value !== null && cell.value !== undefined ? String(cell.value) : "";
  return h("input", { value, "aria-label": label, autocomplete: "off", spellcheck: "false", dataset: { reading: r.name },
    inputmode: number ? "decimal" : null, class: number ? "measure-num" : "measure-text",
    oninput: (e) => {
      const text = e.target.value.trim();
      const v = !number || text === "" ? text : Number.isFinite(Number(text)) ? Number(text) : text;
      e.target.classList.toggle("invalid", number && typeof v === "string" && v !== "");
      set(v);
    } });
}

function unitPicker(r, current, label, onpick) {
  if (!r.units.length) return null;
  if (r.units.length === 1) return r.units[0].unit === r.name ? null : h("span", { class: "measure-unit", text: r.units[0].label });
  const sel = h("select", { "aria-label": label, class: "measure-unit-pick", dataset: { unit: r.name }, onchange: (e) => onpick(e.target.value) },
    ...r.units.map((u) => h("option", { value: u.unit }, u.label)));
  if (!r.units.some((u) => u.unit === current)) {
    sel.append(h("option", { value: current, disabled: current === "mixed" },
      current === "mixed" ? t("mixed: as typed in the file") : t("{unit} (not a unit Scrimmage reads)", { unit: current })));
  }
  sel.value = current;
  return sel;
}

function renderSetup() {
  const box = $("#measure-setup");
  const tst = shown();
  if (!editable() || !tst || !tst.setup.length) { box.replaceChildren(); return; }
  const part = m.file.tests[tst.name].setup;
  const rows = tst.setup.map((r) => {
    const unitOf = () => (part.cells[r.name] ? part.cells[r.name].unit : defaultUnit(r));
    const picker = unitPicker(r, unitOf(), t("{name} unit", { name: r.name }), (u) => {
      part.cells[r.name] = { unit: u, value: part.cells[r.name] ? part.cells[r.name].value : null };
      changed();
    });
    return h("div", { class: "measure-setup-row" },
      h("div", {}, h("strong", { text: r.name }), r.optional ? h("span", { class: "muted", text: " " + t("(if needed)") }) : null,
        h("p", { class: "hint", text: r.what })),
      h("div", { class: "row" }, valueBox(r, part, t("{name} (setup)", { name: r.name }), unitOf), picker));
  });
  box.replaceChildren(h("h2", { class: "measure-sub", text: t("Setup: once for all trials") }), h("div", { class: "measure-setup" }, ...rows));
}

function renderTrials() {
  const table = $("#measure-trials");
  const tst = shown();
  if (!editable() || !tst) { table.replaceChildren(); $("#measure-extra").hidden = true; return; }
  const part = m.file.tests[tst.name];
  const cols = m.units[tst.name];
  const head = h("tr", {}, h("th", { text: t("trial") }),
    ...tst.trial.map((r) => h("th", {},
      h("div", { class: "row measure-col" }, h("strong", { text: r.name }),
        unitPicker(r, cols[r.name], t("{name} unit", { name: r.name }), (u) => {
          cols[r.name] = u;
          for (const row of part.trials) if (row.cells[r.name]) row.cells[r.name].unit = u;
          changed();
        })),
      h("span", { class: "hint", text: r.optional ? t("{what} (if needed)", { what: r.what }) : r.what }))),
    ...tst.fields.map((f) => h("th", { text: f.label })),
    h("th", { class: "measure-flag-head" }, h("span", { class: "visually-hidden", text: t("check") })), h("th", {}, h("span", { class: "visually-hidden", text: t("remove") })));
  const body = part.trials.map((row, i) => h("tr", { dataset: { trial: String(i) } },
    h("td", { class: "measure-n", text: String(i + 1) }),
    ...tst.trial.map((r) => h("td", {}, valueBox(r, row, t("{name}, trial {n}", { name: r.name, n: i + 1 }), () => (cols[r.name] === "mixed" ? defaultUnit(r) : cols[r.name])))),
    ...tst.fields.map((f) => h("td", { class: "measure-value", dataset: { field: f.field } })),
    h("td", { class: "measure-flag" }),
    h("td", {}, h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": t("Remove trial {n}", { n: i + 1 }), title: t("Remove this trial"),
      onclick: () => { part.trials.splice(i, 1); renderTrials(); renderTests(); changed(); } }, "×"))));
  if (!part.trials.length) body.push(h("tr", {}, h("td", { colspan: String(tst.trial.length + tst.fields.length + 3), class: "muted measure-none",
    text: t("No trials yet. Add one for each time you measured, or print a blank form to write them on first.") })));
  table.replaceChildren(h("thead", {}, head), h("tbody", {}, ...body));
  const extra = part.trials.map((row, i) => [i, Object.entries(row.extra)]).filter(([, e]) => e.length);
  const box = $("#measure-extra");
  box.hidden = !extra.length;
  box.textContent = extra.length ? t("Also in the file, kept as written: {extra}", { extra: extra.map(([i, e]) => t("trial {n}: {keys}", { n: i + 1, keys: e.map(([k, v]) => `${k} = ${v}`).join(", ") })).join("; ") }) : "";
  renderValues();
}

// Each trial's values and flags, from the summary.
function renderValues() {
  const tst = shown();
  if (!tst || !editable()) return;
  const s = (m.summary || {})[tst.name];
  for (const tr of document.querySelectorAll("#measure-trials tbody tr[data-trial]")) {
    const i = Number(tr.dataset.trial);
    let off = false;
    for (const td of tr.querySelectorAll(".measure-value")) {
      const f = s && s.fields.find((x) => x.field === td.dataset.field);
      const row = f && f.rows.find((x) => x.trial === i);
      td.textContent = row ? row.shown : "";
      if (row && row.outlier) off = true;
    }
    tr.classList.toggle("measure-off", off);
    tr.querySelector(".measure-flag").replaceChildren(off ? h("span", { class: "measure-off-flag", text: t("⚠ looks off: check it") }) : "");
  }
}

function verdict(good, why) {
  return h("p", { class: `measure-verdict ${good ? "stat-ok" : "stat-warn"}`, text: good ? t("✓ Good enough to use") : t("Not good enough yet: {why}", { why }) });
}
const warnings = (list) => (list.length ? h("ul", { class: "measure-warnings" }, ...list.map((w) => h("li", { text: w }))) : null);
const pct = (x) => `${Math.round(x * 100)}%`;

function renderSummary() {
  const box = $("#measure-summary");
  const tst = shown();
  renderValues();
  if (!tst || !editable()) { box.replaceChildren(); return; }
  const out = [h("h2", { class: "measure-sub", text: tst.fields.length ? t("Average and spread") : t("Your shots") })];
  const s = (m.summary || {})[tst.name];
  if (m.summaryError) out.push(h("p", { class: "error", text: m.summaryError }));
  else if (s && s.error) out.push(h("p", { class: "error measure-error", text: s.error }));
  else if (!tst.fields.length) out.push(h("p", { class: "hint", text: t("{note} Press Fit to fit them.", { note: tst.note }) }));
  else if (!s || !s.fields.length) out.push(h("p", { class: "muted", text: t("No trials yet: the average shows here as you type them in.") }));
  else for (const f of s.fields) {
    const label = tst.fields.find((x) => x.field === f.field)?.label || f.field;
    out.push(h("div", { class: "measure-field-summary", dataset: { field: f.field } },
      h("p", { class: "measure-line" }, h("strong", { text: t("{label}: ", { label }) }),
        tn("average {mean}, lowest {low}, highest {high}, spread {spread} ({count} trial)",
          "average {mean}, lowest {low}, highest {high}, spread {spread} ({count} trials)", f.n,
          { mean: f.shown.mean, low: f.shown.low, high: f.shown.high, spread: pct(f.spread) })),
      verdict(f.good, f.why), warnings(f.warnings)));
  }
  box.replaceChildren(...out);
}

function renderFit() {
  const box = $("#measure-fit-result");
  const tst = shown();
  const btn = $("#measure-fit");
  btn.disabled = !editable() || m.fitting;
  btn.textContent = m.fitting ? t("Fitting…") : t("Fit");
  if (!tst || !tst.fit || !editable()) { box.hidden = true; return; }
  const r = m.fit[tst.name];
  box.hidden = !r && !m.fitting;
  if (m.fitting) { box.replaceChildren(h("p", { class: "muted", text: t("Fitting the model to your trials… (a few seconds)") })); return; }
  if (!r) return;
  const out = [h("h2", { class: "measure-sub", text: t("The fit") })];
  if (m.fitStale[tst.name]) out.push(h("p", { class: "warning", text: t("Your trials changed since this fit: press Fit again.") }));
  if (r.error) { out.push(h("p", { class: "error", text: r.error })); box.replaceChildren(...out); return; }
  const signed = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  if (r.test === "shot") {
    out.push(h("p", { text: t("Fits {what} to your shots ({held}).", { what: r.what, held: r.held }) }),
      h("p", { class: "measure-line" }, h("strong", { text: t("Fit: ") }), r.band_text),
      h("p", { text: t("The model misses your shots by {error}% (root mean square).", { error: (r.rms_error * 100).toFixed(1) }) }),
      h("table", { class: "measure-trials measure-fit-rows" },
        h("thead", {}, h("tr", {}, ...[t("shot"), t("speed across"), t("model"), t("model off by")].map((x) => h("th", { text: x })))),
        h("tbody", {}, ...r.rows.map((row, i) => h("tr", {}, h("td", { text: String(i + 1) }), h("td", { text: units.fmt(row.measured, "speed") }),
          h("td", { text: row.model === null ? t("not launched") : units.fmt(row.model, "speed") }),
          h("td", { text: row.off === null ? "" : signed(row.off) }))))));
  } else {
    out.push(h("p", { text: t("Fits the valve's conductance to your strokes.") }),
      h("p", { class: "measure-line" }, h("strong", { text: t("Fit: ") }), r.band_text),
      h("p", { text: t("Your strokes average {measured} s; the model's takes {model} s, off by {off}.", { measured: r.measured_time.toFixed(3), model: r.model_time.toFixed(3), off: signed(r.off) }) }));
  }
  out.push(verdict(r.good, r.why), warnings(r.warnings));
  box.replaceChildren(...out);
}

function renderStatus() {
  const el = $("#measure-saved");
  if (!m.state) return;
  if (!editable()) { el.textContent = t("Can't be edited here"); el.className = "saved saved-warn"; return; }
  if (m.saveError) { el.textContent = t("Not saved: {why}", { why: m.saveError }); el.className = "saved saved-bad"; return; }
  if (waiting()) { el.textContent = t("Saving…"); el.className = "saved saved-busy"; return; }
  el.textContent = t("✓ All changes saved");
  el.className = "saved saved-ok";
}

function addTrial() {
  const tst = shown();
  if (!editable() || !tst) return;
  m.file.tests[tst.name].trials.push({ origin: null, cells: {}, extra: {} });
  renderTrials();
  renderTests();
  changed();
  const rows = document.querySelectorAll("#measure-trials tbody tr[data-trial]");
  const first = rows[rows.length - 1]?.querySelector("input, select");
  if (first) first.focus();
}

export async function initMeasure() {
  window.__scrimmageMeasure = { store: m, get saved() { return flush(); }, get summarized() { return summarizing; } };
  registerTab("measure", { renderTop, undo() {}, redo() {}, onShow: render, onHide: flush });
  setTab("measure");
  $("#measure-add").addEventListener("click", addTrial);
  $("#measure-fit").addEventListener("click", () => fit());
  // focus leaving the tab (Start page, another window) saves at once; moving between boxes waits for typing to stop
  $("#tab-measure").addEventListener("focusout", (e) => { if (!e.relatedTarget || !$("#tab-measure").contains(e.relatedTarget)) flush(); });
  window.addEventListener("pagehide", saveOnLeaving);
  unitsWas = units.system;
  units.onChange(unitsChanged);
  onChange(languageChanged);
  const app = await api("GET", "/api/app/state");
  if (app.status === 200) m.appPath = app.data.display_path || app.data.path || "";
  await pollLoop();
}
