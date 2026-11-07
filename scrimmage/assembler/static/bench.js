// The Bench tab: one idea session (a folder of bench files), for the projector at kickoff. See /api/bench/* in the
// docstring of scrimmage/assembler/server.py for the API contract.
//
//   Stage        the idea on stage (the one edited or run last by anyone, or one clicked in the strip): author,
//                name, idea, its own newest run's side path picture (or the pass GIF) and headlines in large type
//                and, whenever a result is shown, its UNCALIBRATED line; who changed it last; who holds the run lock.
//                While the session's newest sweep or compare (the state's `result`) is on stage, the stage shows it
//                in place of an idea's run: its chart and a table of its numbers, with its UNCALIBRATED line.
//   Change it    the first FOCUS_SHOWN talked-about keys as fields, Show all (every key of every entry), Run, and
//                the idea's problems beside their fields and in a list.
//   Strip        every other idea, newest first (every idea while the result is on stage, and a card for the
//                result while it isn't); a click puts it on stage until the next edit, run, sweep or compare.
//   Tools        New idea, Sweep, Compare, Gallery (dialogs in index.html).
//
// The shape editor (bench_edit.js, loaded on its own): `stageHooks.edit` set to a function enables the Edit button,
// which calls it with the shown file; while `bench.editing` names a file, that idea stays shown whatever the server's
// stage does. `bench` is the tab's store; `onBench(fn)` calls fn after every render; `fieldRow()` draws one field
// (with `vectors`, three number boxes and - + buttons); `editOp()` and `setKeys()` make edits. A field panel is
// marked `data-bench-fields`, and `keepTyping()` / `restoreTyping()` keep what is half typed in it across a
// redraw (inside `redraw()`, so the change events of the boxes it removes are ignored). A test hook,
// window.__scrimmageBench, exposes the store.
//
// Units: lengths, speeds, masses, forces and springs are shown and typed in the Units setting (units.js; valueOf
// says how). What is typed is written in the form the file already has, `diameter_in` in inches, else meters (U3).
//
// The field helpers, the field rows and the Change it panel are in bench_fields.js, the stage, its pictures and table
// and the strip in bench_stage.js, the session tools' dialogs in bench_dialogs.js.

import { $, api, apiFile, notice, serial, registerTab, setTab, showPath } from "./app.js";
import { units } from "./units.js";
import { t, onChange } from "./i18n.js";
import { kindOf, meters, renderChange } from "./bench_fields.js";
import { resultWords, changedLine, renderStage, renderStrip } from "./bench_stage.js";
import { initDialogs } from "./bench_dialogs.js";

export { included, live, redraw, fieldRow, entries, keysOf, dotted, keepTyping, restoreTyping } from "./bench_fields.js";

export const FOCUS_SHOWN = 6;

export const bench = {
  state: null,       // /api/bench/state
  fields: null,      // /api/bench/fields
  examples: [],      // /api/app/state examples (stems)
  pick: null,        // a file clicked in the strip (local only), or null to follow the server's stage
  pickResult: false, // the session's result card clicked in the strip (local only)
  resultShown: false,  // the stage shows the session's result (state.result) in place of an idea's run
  shown: null,       // the file on stage
  idea: null,        // /api/bench/idea for `shown` ({file, own, included, merged})
  jobs: [],          // /api/bench/jobs .jobs
  showAll: false,
  gif: false,        // the stage shows the pass GIF in place of the side path
  errors: { file: null, list: [] },   // the last refused edit or run of `file`, [{field, message}]
  typed: new Map(),  // field key -> the value a refused edit typed (kept in its box)
  typedAt: new Map(),  // field key -> the session version that refused value was typed against
  editing: null,     // the file open in the shape editor: it stays shown while the editor is open
};
export const stageHooks = { edit: null };
const listeners = [];
export function onBench(fn) { listeners.push(fn); }

// who did something (a source key) in words; an unknown source as it is
export const who = (source) => ({ agent: () => t("the agent"), cli: () => t("someone at the command line"), page: () => t("this page") }[source]
  || (() => source || t("someone")))();
// "{who} ran this {ago}", by the kind of job (a key)
export const DID = {
  run: (p) => t("{who} ran this {ago}", p), sweep: (p) => t("{who} swept this {ago}", p),
  compare: (p) => t("{who} compared this {ago}", p), gallery: (p) => t("{who} put in the gallery this {ago}", p),
};
// a job (a kind key) as the thing being run, and the notices when it ends
export const JOB = { gallery: () => t("the gallery"), sweep: () => t("the sweep"), compare: () => t("the compare") };
const JOB_FAILED = {
  run: (why) => t("The run failed: {why}", { why }), sweep: (why) => t("The sweep failed: {why}", { why }),
  compare: (why) => t("The compare failed: {why}", { why }), gallery: (why) => t("The gallery failed: {why}", { why }),
};
const JOB_DONE = {
  sweep: () => t("The sweep finished; its picture is on the stage."), compare: () => t("The compare finished; its picture is on the stage."),
  gallery: () => t("The gallery finished; its picture is on the stage."),
};
export const SINGLE = ["bench", "piece", "run", "flight"];
export const now = () => Date.now() / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const changes = new Map();   // file -> {own, t}: the last edit seen (on this page, or outside it)
const ownVersions = new Set();
const ownJobs = new Map();   // job id -> {kind, file, key?, done?}
export const jobFailures = new Map();   // file -> {error, t}
let busy = 0;
let asking = 0;   // edits sent and not answered yet: the version each makes isn't known (ownVersions) until then
// This page's last edits that went in one after another, of one idea: from the version the first was sent against
// to the version the last made. A value typed while they were on their way (at a version in between) was typed
// against nothing but them, so it is sent against `to`, not refused as stale by this page's own edit.
let ownRun = null;   // {file, from, to}

// -- helpers ---------------------------------------------------------------------------------------
export const ideaOf = (file) => (bench.state ? bench.state.ideas.find((i) => i.file === file) || null : null);
export const shownIdea = () => ideaOf(bench.shown);
export const base = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();

export function ideaName(file) {
  const i = ideaOf(base(file));
  return i ? i.name : base(file).replace(/\.toml$/, "");
}

export function ago(when) {
  const s = Math.max(0, Math.round(now() - when));
  if (s < 60) return t("{n} s ago", { n: s });
  if (s < 3600) return t("{n} min ago", { n: Math.round(s / 60) });
  return t("{n} h ago", { n: Math.round(s / 3600) });
}

// "wheel.top.diameter" -> {section: "wheel", name: "top", k: "diameter", def: fields["wheel.diameter"]}
export function parseKey(key) {
  const p = String(key).split(".");
  const section = p[0], k = p[p.length - 1];
  const name = p.length > 2 ? p.slice(1, -1).join(".") : null;
  const def = bench.fields ? bench.fields.fields[`${section}.${k}`] || null : null;
  return { section, name, k, def };
}

export function entryOf(raw, section, name) {
  if (!raw) return null;
  if (name === null) return raw[section] && typeof raw[section] === "object" && !Array.isArray(raw[section]) ? raw[section] : null;
  if (section === "shaft") return (raw.shaft || {})[name] || null;
  return (Array.isArray(raw[section]) ? raw[section] : []).find((e) => e && e.name === name) || null;
}

// The value of a key as the file writes it: {value, inches, sendKey, kind}. A length the file writes in inches
// (`diameter_in`) is sent in inches, one it writes in meters, or not at all yet, in meters, whatever the Units
// setting (U3); `kind` (units.js) is what it is shown and typed in, or null for a key shown as the file has it.
export function valueOf(key) {
  const { section, name, k, def } = parseKey(key);
  const vt = entryOf(bench.idea && bench.idea.merged, section, name) || {};
  const kind = kindOf(key);
  if (def && def.length && vt[k] === undefined && vt[`${k}_in`] !== undefined) {
    return { value: vt[`${k}_in`], inches: true, sendKey: `${key}_in`, kind };
  }
  return { value: vt[k], inches: false, sendKey: key, kind };
}

// -- edits -------------------------------------------------------------------------------------------
export function failed(data, status) {
  return data.errors && data.errors.length ? data.errors
    : [{ field: "status", message: data.error || t("the server answered {status}", { status }) }];
}

// A value refused (`list`: [{field, message}]), beside the earlier refusals of other fields of the idea whose text is
// still in their box: each stays until its own field commits again (post).
export function refuse(file, list) {
  const e = bench.errors, fields = new Set(list.map((x) => x.field));
  const keep = e.file === file && !e.run ? e.list.filter((x) => !fields.has(x.field) && bench.typed.has(x.field)) : [];
  bench.errors = { file, list: [...keep, ...list] };
}

async function post(path, body, field, file = bench.shown) {
  busy++;
  try {
    const sent = { file, version: bench.state.version, ...body };
    const refusalBefore = bench.errors;
    asking++;
    let answer;
    try { answer = await api("POST", path, sent); } finally { asking--; }
    const { status, data } = answer;
    if (status === 200 && data.ok) {
      ownVersions.add(data.version);
      const chained = ownRun && ownRun.file === file && sent.version === ownRun.to;
      ownRun = path.endsWith("/edit") ? { file, from: chained ? ownRun.from : sent.version, to: data.version } : null;
      changes.set(file, { own: true, t: now() });
      // a refusal made while this edit was on its way ("5 in" typed in Metric) is about later text: it stays, with
      // its text, until that box commits. So does an earlier edit's refusal of another field, whose text is still in
      // its box: a refusal goes when its own field commits again. A run's refusal, an undo's or a refused edit with
      // no field of its own is about the file as it was: it goes.
      const later = bench.errors !== refusalBefore && bench.errors.file === file ? bench.errors : null;
      const others = !later && field && bench.errors.file === file && !bench.errors.run
        ? bench.errors.list.filter((e) => e.field !== field && bench.typed.has(e.field)) : [];
      const stays = new Set(later ? later.list.map((e) => e.field) : others.map((e) => e.field));
      bench.errors = later || (others.length ? { file, list: others } : { file: null, list: [] });
      for (const m of [bench.typed, bench.typedAt]) for (const k of [...m.keys()]) if (!stays.has(k)) m.delete(k);
      await refresh();
      return true;
    }
    if (status === 200 && !(data.errors && data.errors.length)) {   // undo/redo with nothing to undo or redo
      notice(path.endsWith("undo") ? t("Nothing to undo for {name}.", { name: ideaName(file) }) : t("Nothing to redo for {name}.", { name: ideaName(file) }), "info");
      return false;
    }
    ownRun = null;
    const errors = failed(data, status);
    if (status !== 200 || data.version !== undefined) {
      // stale (the idea changed since this page saw it: says which field wasn't saved), read-only or gone: reload
      notice(errors.map((e) => e.message).join("; "), "warn");
      if (field) { bench.typed.delete(field); bench.typedAt.delete(field); }
      // the refused text isn't kept over the new value: the redraw shows what the file says now
      const act = document.activeElement;
      if (act && act.closest && act.closest("[data-bench-fields]") && String(act.dataset.key).split("#")[0] === field && act.dataset.file === file) {
        act.dataset.orig = act.value;   // only the refused box; another half-typed box keeps its text
      }
      await refresh();
      return false;
    }
    const list = errors.map((e) => ({ ...e, field: e.field === "status" && field ? field : meters(e.field) }));
    if (body.op === "set") refuse(file, list);
    else bench.errors = { file, list };
    if (field) bench.typedAt.set(field, sent.version);   // the refused value stays tied to the version it was typed at
    render();
    const shown = document.querySelector("#tab-bench .field-msg.error");
    if (shown) shown.scrollIntoView({ block: "nearest" });
    return false;
  } finally {
    busy--;
  }
}

// One `set` edit of the idea on stage: `key` is the meters key (what the page shows), sent as the file writes it.
// `version` is the session version the value was typed against (default: the current one), so an edit typed before
// someone else changed the idea is refused as stale instead of overwriting their change.
export function setKey(key, value, version = null, file = null) {
  return setKeys([[key, value]], version, file, key);
}
// Several keys set in one edit ([[key, value], ...]: a bar moved by its middle sets both ends), so they are written,
// undone and refused together; `field` is the key the page shows them under (a refusal names and marks it), and
// `pairs.value` (when set) the value its box shows. `version` is the session version the value was typed against.
// The pairs may be a function, called when the edit's turn comes (a - or + step reads the value then).
export function setKeys(pairs, version = null, file = null, field = null) {
  const target = file || bench.shown;
  return serial(() => {
    if (!bench.state || !bench.shown) return false;
    const list = typeof pairs === "function" ? pairs() : pairs;
    if (!list || !list.length) return false;
    const shown = field || list[0][0];
    if (target !== bench.shown) {   // typed for an idea that has left the stage: never write it into another
      leftStage(target, shown);
      return false;
    }
    let v = Number.isInteger(version) ? version : drawnVersion();
    if (ownRun && ownRun.file === target && v >= ownRun.from && v < ownRun.to) v = ownRun.to;
    bench.typed.set(shown, list.value !== undefined ? list.value : list[0][1]);   // kept in its box if refused
    // not tied to `v` until it is refused (post): while it is on its way, the long poll can bring the version this
    // edit makes and redraw first, and a box stamped with `v` then would send the next value typed as stale
    bench.typedAt.delete(shown);
    const body = list.length === 1 && shown === list[0][0]
      ? { op: "set", key: valueOf(list[0][0]).sendKey, value: list[0][1], version: v }
      : { op: "set", values: Object.fromEntries(list.map(([k, x]) => [valueOf(k).sendKey, x])), field: shown, version: v };
    return post("/api/bench/edit", body, shown, target);
  });
}
// One edit of the shown idea other than `set` (add, remove, rename, override), in turn with the others.
export const editOp = (body, field = null) => serial(() => (bench.state && bench.shown ? post("/api/bench/edit", body, field) : false));
export function leftStage(file, key) {
  notice(t("{name} left the stage, so your edit to {key} wasn't saved", { name: ideaName(file), key }), "warn", "bench-left");
}
const undo = () => serial(() => (bench.shown ? post("/api/bench/undo", {}) : false));
const redo = () => serial(() => (bench.shown ? post("/api/bench/redo", {}) : false));

export async function job(path, body, record) {
  const { status, data } = await api("POST", path, body);
  if (status === 200 && data.ok) {
    ownJobs.set(data.id, record);
    bench.jobs = [{ id: data.id, kind: record.kind, files: record.files || [], state: "queued", error: null, out: null }, ...bench.jobs];
    renderStage();
    return true;
  }
  return failed(data, status);
}

async function run() {
  const file = bench.shown;
  if (!file) return;
  jobFailures.delete(file);
  const r = await job("/api/bench/run", { file }, { kind: "run", files: [file] });
  if (r === true) return;
  notice(t("Can't run {name}: {why}", { name: ideaName(file), why: r.map((e) => e.message).join("; ") }), "error", "bench-run");
  // about the idea as it is now: it goes when the idea changes (an edit here, or outside: loadIdea)
  bench.errors = { file, list: r.map((e) => ({ ...e, field: meters(e.field) })), run: true,
    merged: bench.idea && bench.idea.file === file ? JSON.stringify(bench.idea.merged) : null };
  render();
}

// -- state ---------------------------------------------------------------------------------------------
const sig = (i) => JSON.stringify([i.name, i.author, i.idea, i.focus, i.problems, i.read_only_reason]);
let applySeq = 0;
let loadedSeq = 0;   // the newest applyState whose idea read has been taken
// The session version the values drawn are of: the idea read's own (a newer state may be on its way while an older
// read is shown), so a value typed from them is refused as stale when the idea changed since
export const drawnVersion = () => (bench.idea && bench.idea.file === bench.shown && Number.isInteger(bench.idea.version)
  ? bench.idea.version : bench.state.version);

export async function applyState(s, force = false) {
  const prev = bench.state;
  if (prev && s.version <= prev.version && !force) return;
  const seq = ++applySeq;
  const own = busy > 0 || ownVersions.has(s.version);
  if (prev) {
    for (const i of s.ideas) {
      const was = prev.ideas.find((x) => x.file === i.file);
      if (was && sig(was) !== sig(i) && !own) changes.set(i.file, { own: false, t: now() });
    }
  }
  // the server's stage moved (a new stage, or the stage idea edited or run again): follow it
  const stageOf = (st) => st && st.ideas.find((i) => i.file === st.stage);
  const a = stageOf(prev), b = stageOf(s);
  const rt = (st) => (st && st.result ? `${st.result.t}` : "");
  const moved = !prev || s.stage !== prev.stage || JSON.stringify(a || null) !== JSON.stringify(b || null)
    || rt(prev) !== rt(s) || !!(prev.result && prev.result.on_stage) !== !!(s.result && s.result.on_stage);
  if (moved) { bench.pick = null; bench.pickResult = false; }
  const files = new Set(s.ideas.map((i) => i.file));
  if (bench.pick && !files.has(bench.pick)) bench.pick = null;
  if (!s.result) bench.pickResult = false;
  const old = bench.shown;
  bench.state = s;
  if (bench.editing && !files.has(bench.editing)) bench.editing = null;   // deleted outside: the editor closes
  // the session's newest sweep or compare is on stage until an edit or run moves it, or an idea is clicked
  bench.resultShown = !!s.result && !bench.editing && !bench.pick && (s.result.on_stage || bench.pickResult);
  const about = bench.resultShown ? s.result.files.find((f) => files.has(f)) : null;
  bench.shown = bench.editing || bench.pick || about || (files.has(s.stage) ? s.stage : (s.ideas[0] ? s.ideas[0].file : null));
  if (old && !files.has(old)) {
    notice(bench.shown ? t("{file} was deleted or renamed outside; showing {name}", { file: old, name: ideaName(bench.shown) })
      : t("{file} was deleted or renamed outside; showing nothing (no ideas left)", { file: old }), "info", "bench-gone");
  }
  if (old !== bench.shown) { bench.errors = { file: null, list: [] }; bench.typed.clear(); bench.typedAt.clear(); bench.gif = false; }
  await loadIdea(seq);
}

async function loadIdea(seq) {
  const file = bench.shown;
  let idea = null;
  if (file) {
    const { status, data } = await api("GET", `/api/bench/idea?file=${encodeURIComponent(file)}`);
    // a newer state's read has answered first, or another idea is on stage now: this read is older than what's shown.
    // One merely overtaken by a newer state still counts: an edit's refresh waits on its own read, and the next - or +
    // in line reads the value from it (a newer state's read, not waited on, may not have answered yet)
    if (seq < loadedSeq || file !== bench.shown) return;
    loadedSeq = seq;
    if (status === 200) idea = { file, ...data };
  }
  const prev = bench.idea;
  // changed since what was drawn, last by someone else: told by the read's own last change, not by whether an edit
  // of this page's was on its way (a read taken then can hold an outside change made after that edit). Only while an
  // edit is unanswered can its last change be that edit's, whose version isn't known yet
  if (idea && prev && prev.file === idea.file && JSON.stringify(prev.merged) !== JSON.stringify(idea.merged)
      && !ownVersions.has(idea.changed_at) && !asking) {
    changes.set(file, { own: false, t: now() });
  }
  bench.idea = idea || (prev && prev.file === file ? prev : null);
  const e = bench.errors;
  if (e.run && e.file === file && bench.idea && e.merged !== JSON.stringify(bench.idea.merged)) bench.errors = { file: null, list: [] };
  render();
}

export async function refresh() {
  const { status, data } = await api("GET", "/api/bench/state");
  if (status === 200) await applyState(data, true);
}

async function pollLoop() {
  for (;;) {
    const since = bench.state ? `?since=${bench.state.version}` : "";
    const { status, data } = await api("GET", "/api/bench/state" + since);
    if (status === 200) await applyState(data);
    else await sleep(status === 403 ? 10000 : 2000);
  }
}

async function jobsLoop() {
  let version = null;
  for (;;) {
    const { status, data } = await api("GET", "/api/bench/jobs" + (version === null ? "" : `?since=${version}`));
    if (status !== 200) { await sleep(2000); continue; }
    version = data.version;
    bench.jobs = data.jobs;
    for (const j of data.jobs) {
      const mine = ownJobs.get(j.id);
      if (!mine || mine.done || (j.state !== "done" && j.state !== "failed")) continue;
      mine.done = true;
      finished(j, mine);
    }
    renderStage();
  }
}

async function finished(j, mine) {
  if (j.state === "failed") {
    for (const f of j.files) jobFailures.set(f, { error: j.error || t("it stopped with no reason given"), t: now() });
    if (mine.win) mine.win.close();
    const why = j.error || t("no reason given");
    notice(JOB_FAILED[j.kind] ? JOB_FAILED[j.kind](why) : t("The {kind} failed: {why}", { kind: j.kind, why }), "error");
    renderStage();
    return;
  }
  if (j.kind === "gallery" && j.out) {
    const { blob } = await apiFile(`/api/bench/file?path=${encodeURIComponent(j.out)}`);
    if (!blob) { notice(t("The gallery was written, but this page couldn't read it."), "error"); return; }
    if (mine.win && !mine.win.closed) {
      const url = URL.createObjectURL(new Blob([blob], { type: "text/html" }));
      mine.win.opener = null;   // the gallery page gets no handle on this page (its link holds the token)
      mine.win.location = url;
      setTimeout(() => URL.revokeObjectURL(url), 60000);   // long enough for the new tab to load it
    } else notice(t("The gallery is ready: {file}", { file: j.out }), "info");
    return;
  }
  if (j.kind !== "run") notice(JOB_DONE[j.kind] ? JOB_DONE[j.kind]() : t("The {kind} finished; its picture is on the stage.", { kind: j.kind }), "info");
  renderStage();
}

// -- rendering --------------------------------------------------------------------------------------------
export function render() {
  renderStage();
  renderChange();
  renderStrip();
  renderTop();
  for (const fn of listeners) fn();
}

function renderTop() {
  if (!bench.state) return;
  const folder = bench.state.display_folder || bench.state.folder;
  if (document.getElementById("tab-bench").hidden || document.body.classList.contains("on-start")) return;
  showPath(bench.shown ? `${folder}/${bench.shown}` : folder, bench.state.folder);
  const i = shownIdea();
  $("#undo").disabled = !i || !i.can_undo || !!i.read_only_reason;
  $("#redo").disabled = !i || !i.can_redo || !!i.read_only_reason;
}

// -- boot ----------------------------------------------------------------------------------------------------
export async function initBench() {
  window.__scrimmageBench = {
    get state() { return bench.state; },
    get shown() { return bench.shown; },
    get idea() { return bench.idea; },
    get jobs() { return bench.jobs; },
    bench,
  };
  registerTab("bench", { renderTop, undo, redo, onShow: render });
  setTab("bench");
  for (const b of ["#bench-run", "#bench-change-run"]) $(b).addEventListener("click", () => run());
  $("#bench-gif").addEventListener("click", () => { bench.gif = !bench.gif; renderStage(); });
  $("#bench-showall").addEventListener("click", () => { bench.showAll = !bench.showAll; renderChange(); });
  $("#bench-edit").addEventListener("click", () => { if (stageHooks.edit && bench.shown) stageHooks.edit(bench.shown); });
  initDialogs();
  units.onChange(() => render());   // a half-typed box keeps its text (keepTyping), then read in the new units
  onChange(() => { render(); refresh(); });   // likewise; the server's words (problems, refusals) asked for again
  $("#bench-change").dataset.benchFields = "";
  const [f, app] = await Promise.all([api("GET", "/api/bench/fields"), api("GET", "/api/app/state")]);
  if (f.status === 200) bench.fields = f.data;
  else notice(t("Couldn't load the field help; editing is off until the page is reloaded."), "error");
  if (app.status === 200) bench.examples = app.data.examples || [];
  setInterval(() => {   // keep "... ago" current
    const i = shownIdea();
    if (i) $("#bench-changed").textContent = bench.resultShown && bench.state.result ? resultWords(bench.state.result).changed : changedLine(i);
  }, 1000);
  // the shape editor: loaded on its own, so a failure there leaves the stage working (Edit stays off)
  import("./bench_edit.js").then((m) => m.initEdit()).catch((e) => {
    console.error(e);
    notice(t("The shape editor failed to load: {why}", { why: e.message || e }), "error");
  });
  jobsLoop();
  await pollLoop();
}
