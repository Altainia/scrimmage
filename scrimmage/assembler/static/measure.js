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

const testOf = (name) => m.state.tests.find((t) => t.name === name);
const shown = () => testOf(m.test);
const editable = () => !!m.file;
const waiting = () => !!saveTimer || m.saving;

// The file without blank cells, as text: two files that would write the same are the same.
function canon(file) {
  if (!file) return "";
  const part = (p) => ({ cells: Object.fromEntries(Object.entries(p.cells).filter(([, c]) => c.value !== null && c.value !== "")
    .map(([k, c]) => [k, { unit: c.unit, value: c.value }])), extra: p.extra });
  const tests = Object.fromEntries(Object.entries(file.tests).map(([t, v]) => [t, { setup: part(v.setup),
    trials: v.trials.map((r) => ({ origin: r.origin, ...part(r) })) }]));
  return JSON.stringify({ measure: file.measure, tests });
}
const copy = (x) => JSON.parse(JSON.stringify(x));
const why = (data, status) => data.error || (data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`;

// -- the units over each column ------------------------------------------------------------------
function columnUnits(test) {
  const t = testOf(test), out = {};
  for (const r of t.trial) {
    if (!r.units.length) continue;
    const used = new Set(m.file.tests[test].trials.map((row) => row.cells[r.name]).filter((c) => c && c.value !== null && c.value !== "").map((c) => c.unit));
    out[r.name] = used.size === 1 ? [...used][0] : used.size ? "mixed" : r.unit;
  }
  return out;
}
function adopt(file) {
  m.file = file ? copy(file) : null;
  m.saved = canon(m.file);
  m.units = {};
  if (m.file) for (const t of m.state.tests) m.units[t.name] = columnUnits(t.name);
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
  const rows = Object.fromEntries(Object.entries(m.file.tests).map(([t, p]) => [t, p.trials.slice()]));
  m.saving = true; m.saveError = null; renderStatus();
  try {
    const { status, data } = await api("POST", "/api/measure/save", { file: sent, version: m.state.version });
    if (status === 200 && data.ok) {
      ownVersions.add(data.version);
      m.state = { ...m.state, version: data.version };
      // the file now holds the trials sent, in order: number them as the file does (trials typed meanwhile too)
      for (const part of Object.values(sent.tests)) part.trials.forEach((row, i) => { row.origin = i; });
      m.saved = canon(sent);
      for (const [t, part] of Object.entries(m.file.tests)) {
        part.trials.forEach((row) => { const i = rows[t].indexOf(row); row.origin = i >= 0 ? i : null; });
      }
      return;
    }
    if (status === 409 && data.state) {   // the file changed outside this page: show it as saved
      clearTimeout(saveTimer); saveTimer = null;
      m.state = data.state;
      adopt(data.state.file);
      m.summary = data.state.summary;
      m.summaryError = null;
      notice("The file changed outside this page (another tab, or an outside edit), so it shows the saved version.", "warn", "measure");
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
  for (const t of m.state.tests) {
    const part = m.file.tests[t.name];
    const check = (readings, p, where) => {
      for (const r of readings) {
        const c = p.cells[r.name];
        if (c && (r.type === "reading" || r.type === "number") && typeof c.value === "string" && c.value !== "")
          return `${where}: ${r.name} must be a number, not "${c.value}"`;
      }
      return null;
    };
    const s = check(t.setup, part.setup, `${t.title}, setup`);
    if (s) return s;
    for (let i = 0; i < part.trials.length; i++) {
      const e = check(t.trial, part.trials[i], `${t.title}, trial ${i + 1}`);
      if (e) return e;
    }
  }
  return null;
}

async function fit() {
  if (!editable() || m.fitting) return;
  const test = m.test;
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
  if (prev && canon(s.file) !== m.saved) notice("The file changed outside this page, so it was read again.", "info", "measure");
  adopt(s.file);
  m.summary = s.summary;
  m.summaryError = null;
  if (!m.test || !testOf(m.test)) m.test = pickTest();
  render();
}

// The first test with trials, else the first.
function pickTest() {
  const tests = m.state.tests;
  const withTrials = m.state.file && tests.find((t) => m.state.file.tests[t.name].trials.length);
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
    ? (m.state.file ? `This file has a problem: ${m.state.file_error}` : `This file can't be edited here until it's fixed: ${m.state.file_error}. Fix it in a text editor; the page reads it again on its own.`)
    : "";
  renderTests();
  renderHow();
  $("#measure-add").disabled = !editable();
  const t = shown();
  $("#measure-print").href = t ? `/forms/${t.name}.html` : "#";
  $("#measure-fit").hidden = !(t && t.fit);
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
  nav.replaceChildren(...m.state.tests.map((t) => {
    const n = m.file ? m.file.tests[t.name].trials.length : 0;
    return h("button", { type: "button", class: `measure-test${t.name === m.test ? " active" : ""}`, dataset: { test: t.name },
      "aria-pressed": String(t.name === m.test), onclick: () => { m.test = t.name; render(); } },
    h("strong", { text: t.title }), h("span", { class: "muted", text: n ? ` · ${n} trial${n === 1 ? "" : "s"}` : " · no trials yet" }));
  }));
}

function renderHow() {
  const t = shown();
  $("#measure-title").textContent = t ? t.title : "No tests";
  $("#measure-gives").textContent = t ? `Gives ${t.gives}.` : "";
  $("#measure-picture").innerHTML = t ? t.picture : "";   // our own SVG, from scrimmage/measure/img
  $("#measure-need").replaceChildren(...(t ? t.need : []).map((x) => h("li", { text: x })));
  $("#measure-steps").replaceChildren(...(t ? t.steps : []).map((x) => h("li", { text: x })));
}

const HEADER = [
  { key: "by", label: "Measured by", type: "text" },
  { key: "date", label: "Date", type: "date" },
  { key: "tools", label: "Tools", type: "text" },
  { key: "about", label: "What you measured", type: "text" },
];

function renderHeader() {
  const box = $("#measure-header");
  if (!editable()) { box.replaceChildren(); return; }
  const head = m.file.measure;
  const fields = HEADER.map((f) => {
    const input = h("input", { id: `measure-${f.key}`, type: f.type, value: head[f.key] || "", autocomplete: "off",
      oninput: (e) => { head[f.key] = e.target.value; changed(); } });
    return h("div", { class: "measure-field" }, h("label", { for: input.id, text: f.label }), input);
  });
  if (m.state.kind === "ball") {
    const sel = h("select", { id: "measure-base", onchange: (e) => { head.base = e.target.value; changed(); } },
      h("option", { value: "" }, "(none)"), ...m.state.bases.map((b) => h("option", { value: b }, b)));
    sel.value = head.base || "";
    fields.push(h("div", { class: "measure-field" }, h("label", { for: "measure-base", text: "Ball preset it measured" }), sel,
      h("p", { class: "hint", text: "Its numbers fill in what you didn't measure." })));
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
      h("option", { value: "" }, "(not given)"), h("option", { value: "true" }, "yes"), h("option", { value: "false" }, "no"));
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
      current === "mixed" ? "mixed: as typed in the file" : `${current} (not a unit Scrimmage reads)`));
  }
  sel.value = current;
  return sel;
}

function renderSetup() {
  const box = $("#measure-setup");
  const t = shown();
  if (!editable() || !t || !t.setup.length) { box.replaceChildren(); return; }
  const part = m.file.tests[t.name].setup;
  const rows = t.setup.map((r) => {
    const unitOf = () => (part.cells[r.name] ? part.cells[r.name].unit : r.unit);
    const picker = unitPicker(r, unitOf(), `${r.name} unit`, (u) => {
      part.cells[r.name] = { unit: u, value: part.cells[r.name] ? part.cells[r.name].value : null };
      changed();
    });
    return h("div", { class: "measure-setup-row" },
      h("div", {}, h("strong", { text: r.name }), r.optional ? h("span", { class: "muted", text: " (if needed)" }) : null,
        h("p", { class: "hint", text: r.what })),
      h("div", { class: "row" }, valueBox(r, part, `${r.name} (setup)`, unitOf), picker));
  });
  box.replaceChildren(h("h2", { class: "measure-sub", text: "Setup: once for all trials" }), h("div", { class: "measure-setup" }, ...rows));
}

function renderTrials() {
  const table = $("#measure-trials");
  const t = shown();
  if (!editable() || !t) { table.replaceChildren(); $("#measure-extra").hidden = true; return; }
  const part = m.file.tests[t.name];
  const units = m.units[t.name];
  const head = h("tr", {}, h("th", { text: "trial" }),
    ...t.trial.map((r) => h("th", {},
      h("div", { class: "row measure-col" }, h("strong", { text: r.name }),
        unitPicker(r, units[r.name], `${r.name} unit`, (u) => {
          units[r.name] = u;
          for (const row of part.trials) if (row.cells[r.name]) row.cells[r.name].unit = u;
          changed();
        })),
      h("span", { class: "hint", text: r.optional ? `${r.what} (if needed)` : r.what }))),
    ...t.fields.map((f) => h("th", { text: f.label })),
    h("th", { class: "measure-flag-head" }, h("span", { class: "visually-hidden", text: "check" })), h("th", {}, h("span", { class: "visually-hidden", text: "remove" })));
  const body = part.trials.map((row, i) => h("tr", { dataset: { trial: String(i) } },
    h("td", { class: "measure-n", text: String(i + 1) }),
    ...t.trial.map((r) => h("td", {}, valueBox(r, row, `${r.name}, trial ${i + 1}`, () => (units[r.name] === "mixed" ? r.unit : units[r.name])))),
    ...t.fields.map((f) => h("td", { class: "measure-value", dataset: { field: f.field } })),
    h("td", { class: "measure-flag" }),
    h("td", {}, h("button", { type: "button", class: "btn btn-small btn-ghost", "aria-label": `Remove trial ${i + 1}`, title: "Remove this trial",
      onclick: () => { part.trials.splice(i, 1); renderTrials(); renderTests(); changed(); } }, "×"))));
  if (!part.trials.length) body.push(h("tr", {}, h("td", { colspan: String(t.trial.length + t.fields.length + 3), class: "muted measure-none",
    text: "No trials yet. Add one for each time you measured, or print a blank form to write them on first." })));
  table.replaceChildren(h("thead", {}, head), h("tbody", {}, ...body));
  const extra = part.trials.map((row, i) => [i, Object.entries(row.extra)]).filter(([, e]) => e.length);
  const box = $("#measure-extra");
  box.hidden = !extra.length;
  box.textContent = extra.length ? "Also in the file, kept as written: " + extra.map(([i, e]) => `trial ${i + 1}: ${e.map(([k, v]) => `${k} = ${v}`).join(", ")}`).join("; ") : "";
  renderValues();
}

// Each trial's values and flags, from the summary.
function renderValues() {
  const t = shown();
  if (!t || !editable()) return;
  const s = (m.summary || {})[t.name];
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
    tr.querySelector(".measure-flag").replaceChildren(off ? h("span", { class: "measure-off-flag", text: "⚠ looks off: check it" }) : "");
  }
}

function verdict(good, why) {
  return h("p", { class: `measure-verdict ${good ? "stat-ok" : "stat-warn"}`, text: good ? "✓ Good enough to use" : `Not good enough yet: ${why}` });
}
const warnings = (list) => (list.length ? h("ul", { class: "measure-warnings" }, ...list.map((w) => h("li", { text: w }))) : null);
const pct = (x) => `${Math.round(x * 100)}%`;

function renderSummary() {
  const box = $("#measure-summary");
  const t = shown();
  renderValues();
  if (!t || !editable()) { box.replaceChildren(); return; }
  const out = [h("h2", { class: "measure-sub", text: t.fields.length ? "Average and spread" : "Your shots" })];
  const s = (m.summary || {})[t.name];
  if (m.summaryError) out.push(h("p", { class: "error", text: m.summaryError }));
  else if (s && s.error) out.push(h("p", { class: "error measure-error", text: s.error }));
  else if (!t.fields.length) out.push(h("p", { class: "hint", text: `${t.note} Press Fit to fit them.` }));
  else if (!s || !s.fields.length) out.push(h("p", { class: "muted", text: "No trials yet: the average shows here as you type them in." }));
  else for (const f of s.fields) {
    const label = t.fields.find((x) => x.field === f.field)?.label || f.field;
    out.push(h("div", { class: "measure-field-summary", dataset: { field: f.field } },
      h("p", { class: "measure-line" }, h("strong", { text: `${label}: ` }),
        `average ${f.shown.mean}, lowest ${f.shown.low}, highest ${f.shown.high}, spread ${pct(f.spread)} (${f.n} trial${f.n === 1 ? "" : "s"})`),
      verdict(f.good, f.why), warnings(f.warnings)));
  }
  box.replaceChildren(...out);
}

function renderFit() {
  const box = $("#measure-fit-result");
  const t = shown();
  const btn = $("#measure-fit");
  btn.disabled = !editable() || m.fitting;
  btn.textContent = m.fitting ? "Fitting…" : "Fit";
  if (!t || !t.fit || !editable()) { box.hidden = true; return; }
  const r = m.fit[t.name];
  box.hidden = !r && !m.fitting;
  if (m.fitting) { box.replaceChildren(h("p", { class: "muted", text: "Fitting the model to your trials… (a few seconds)" })); return; }
  if (!r) return;
  const out = [h("h2", { class: "measure-sub", text: "The fit" })];
  if (m.fitStale[t.name]) out.push(h("p", { class: "warning", text: "Your trials changed since this fit: press Fit again." }));
  if (r.error) { out.push(h("p", { class: "error", text: r.error })); box.replaceChildren(...out); return; }
  const signed = (x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;
  if (r.test === "shot") {
    out.push(h("p", { text: `Fits ${r.what} to your shots (${r.held}).` }),
      h("p", { class: "measure-line" }, h("strong", { text: "Fit: " }), r.band_text),
      h("p", { text: `The model misses your shots by ${(r.rms_error * 100).toFixed(1)}% (root mean square).` }),
      h("table", { class: "measure-trials measure-fit-rows" },
        h("thead", {}, h("tr", {}, ...["shot", "speed across", "model", "model off by"].map((x) => h("th", { text: x })))),
        h("tbody", {}, ...r.rows.map((row, i) => h("tr", {}, h("td", { text: String(i + 1) }), h("td", { text: `${row.measured.toFixed(2)} m/s` }),
          h("td", { text: row.model === null ? "not launched" : `${row.model.toFixed(2)} m/s` }),
          h("td", { text: row.off === null ? "" : signed(row.off) }))))));
  } else {
    out.push(h("p", { text: "Fits the valve's conductance to your strokes." }),
      h("p", { class: "measure-line" }, h("strong", { text: "Fit: " }), r.band_text),
      h("p", { text: `Your strokes average ${r.measured_time.toFixed(3)} s; the model's takes ${r.model_time.toFixed(3)} s, off by ${signed(r.off)}.` }));
  }
  out.push(verdict(r.good, r.why), warnings(r.warnings));
  box.replaceChildren(...out);
}

function renderStatus() {
  const el = $("#measure-saved");
  if (!m.state) return;
  if (!editable()) { el.textContent = "Can't be edited here"; el.className = "saved saved-warn"; return; }
  if (m.saveError) { el.textContent = `Not saved: ${m.saveError}`; el.className = "saved saved-bad"; return; }
  if (waiting()) { el.textContent = "Saving…"; el.className = "saved saved-busy"; return; }
  el.textContent = "✓ All changes saved";
  el.className = "saved saved-ok";
}

function addTrial() {
  const t = shown();
  if (!editable() || !t) return;
  m.file.tests[t.name].trials.push({ origin: null, cells: {}, extra: {} });
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
  const app = await api("GET", "/api/app/state");
  if (app.status === 200) m.appPath = app.data.display_path || app.data.path || "";
  await pollLoop();
}
