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

import { $, h, api, apiFile, notice, serial, registerTab, setTab, showPath, fmt, UNIT, markUnits, typedText, keptSelection } from "./app.js";
import { units } from "./units.js";
import { t, onChange } from "./i18n.js";

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
const who = (source) => ({ agent: () => t("the agent"), cli: () => t("someone at the command line"), page: () => t("this page") }[source]
  || (() => source || t("someone")))();
// "{who} ran this {ago}", by the kind of job (a key)
const DID = {
  run: (p) => t("{who} ran this {ago}", p), sweep: (p) => t("{who} swept this {ago}", p),
  compare: (p) => t("{who} compared this {ago}", p), gallery: (p) => t("{who} put in the gallery this {ago}", p),
};
// a job (a kind key) as the thing being run, and the notices when it ends
const JOB = { gallery: () => t("the gallery"), sweep: () => t("the sweep"), compare: () => t("the compare") };
const JOB_FAILED = {
  run: (why) => t("The run failed: {why}", { why }), sweep: (why) => t("The sweep failed: {why}", { why }),
  compare: (why) => t("The compare failed: {why}", { why }), gallery: (why) => t("The gallery failed: {why}", { why }),
};
const JOB_DONE = {
  sweep: () => t("The sweep finished; its picture is on the stage."), compare: () => t("The compare finished; its picture is on the stage."),
  gallery: () => t("The gallery finished; its picture is on the stage."),
};
const SINGLE = ["bench", "piece", "run", "flight"];
const now = () => Date.now() / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const changes = new Map();   // file -> {own, t}: the last edit seen (on this page, or outside it)
const ownVersions = new Set();
const ownJobs = new Map();   // job id -> {kind, file, key?, done?}
const jobFailures = new Map();   // file -> {error, t}
let busy = 0;
let asking = 0;   // edits sent and not answered yet: the version each makes isn't known (ownVersions) until then
// This page's last edits that went in one after another, of one idea: from the version the first was sent against
// to the version the last made. A value typed while they were on their way (at a version in between) was typed
// against nothing but them, so it is sent against `to`, not refused as stale by this page's own edit.
let ownRun = null;   // {file, from, to}

// -- helpers ---------------------------------------------------------------------------------------
const ideaOf = (file) => (bench.state ? bench.state.ideas.find((i) => i.file === file) || null : null);
export const shownIdea = () => ideaOf(bench.shown);
const base = (p) => String(p || "").replace(/\/+$/, "").split("/").pop();

function ideaName(file) {
  const i = ideaOf(base(file));
  return i ? i.name : base(file).replace(/\.toml$/, "");
}

function ago(when) {
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
function valueOf(key) {
  const { section, name, k, def } = parseKey(key);
  const vt = entryOf(bench.idea && bench.idea.merged, section, name) || {};
  const kind = kindOf(key);
  if (def && def.length && vt[k] === undefined && vt[`${k}_in`] !== undefined) {
    return { value: vt[`${k}_in`], inches: true, sendKey: `${key}_in`, kind };
  }
  return { value: vt[k], inches: false, sendKey: key, kind };
}

// -- units: a key's number in the Units setting -------------------------------------------------------------------
// By the unit fields.toml gives a key: a length is "length" (in or mm), a flight's target "field" (ft or m). Angles,
// times, rps and the rest are shown as the file writes them.
const KINDS = { m: "length", "m/s": "speed", kg: "mass", N: "force", "N/m": "spring" };
const FIELD_KEYS = ["flight.target_distance", "flight.target_height"];
function kindOf(key) {
  const { section, k, def } = parseKey(key);
  if (!def || def.choices) return null;
  return def.unit === "m" && FIELD_KEYS.includes(`${section}.${k}`) ? "field" : KINDS[def.unit] || null;
}
const isNumber = (x) => typeof x === "number" && Number.isFinite(x);
const each = (v, f) => (Array.isArray(v) ? v.map((x) => (isNumber(x) ? f(x) : x)) : isNumber(v) ? f(v) : v);
// a value as the file writes it, in SI (and back: to 1 µm, or a millionth of an inch)
const siOf = (vt, v) => each(v, (x) => (vt.inches ? units.fromKey(x, "in") : x));
const fileOf = (vt, si) => each(si, (x) => round6(vt.inches ? units.toKey(x, "in") : x));
// a number's text in a box (no unit), and a whole value's ("1, 2, 3"), in the Units setting
const shownNumber = (vt, x) => (vt.kind ? units.shown(siOf(vt, x), vt.kind) : fmt(x));
const shownValue = (vt, v) => (vt.kind && v != null && typeof v !== "string" ? (Array.isArray(v) ? v.map((x) => shownNumber(vt, x)).join(", ") : shownNumber(vt, v)) : show(v));
const unitOf = (vt, def) => (vt.kind ? units.name(vt.kind) : vt.inches ? "in" : def && def.unit ? UNIT[def.unit] || def.unit : "");

// What a box of a key with a kind holds, as the file writes it: "" -> null (remove); a number, or a list "1, 2, 3",
// each typed in the Units setting ("4", "4 in", "100 mm"). A part still showing `orig`'s text keeps the file's own
// number (`file`), so a list with one number changed writes the others back exactly.
function parseMeasured(text, vt, orig, file) {
  const s = text.trim();
  if (s === "") return { value: null };
  const list = /^\[/.test(s) || s.includes(",");
  let parts = s.replace(/^\[|\]$/g, "").split(",").map((x) => x.trim()).filter(Boolean);
  const spaced = parts.length === 1 ? parts[0].split(/\s+/) : [];
  if (spaced.length > 1 && spaced.every((x) => NUM_RE.test(x))) parts = spaced;   // "1 2 3", as parseTyped takes it
  const was = String(orig ?? "").split(",").map((x) => x.trim());
  const exact = Array.isArray(file) ? file : [file];
  const out = [];
  for (const [j, x] of parts.entries()) {
    if (parts.length === was.length && x === was[j] && isNumber(exact[j])) { out.push(exact[j]); continue; }
    const v = units.parse(x, vt.kind);
    if (typeof v === "object") return { error: v.error };
    out.push(fileOf(vt, v));
  }
  return { value: out.length === 1 && !list ? out[0] : out };
}

const meters = (field) => {
  if (!field || !field.endsWith("_in")) return field;
  const m = field.slice(0, -3);
  const { def } = parseKey(m);
  return def && def.length ? m : field;
};

function choiceList(kind) {
  const merged = bench.idea ? bench.idea.merged : {};
  if (kind === "shafts") return Object.keys(merged.shaft || {});
  if (kind === "outlets") return (merged.outlet || []).map((o) => o && o.name).filter(Boolean);
  return (bench.fields && bench.fields.choices[kind]) || [];
}

export function included(section, name) {
  const inc = (bench.idea && bench.idea.included) || {};
  return inc[name === null ? section : `${section}.${name}`] || null;
}

// What a text box holds: "" -> null (remove), a number, a list "a, b, c", true/false, else the text.
function parseTyped(text, numeric) {
  const s = text.trim();
  if (s === "") return { value: null };
  const num = (x) => (/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(x) ? Number(x) : NaN);
  if (numeric) {
    const parts = s.replace(/^\[|\]$/g, "").split(/[\s,]+/).filter(Boolean);
    const nums = parts.map(num);
    if (nums.some((n) => Number.isNaN(n))) return { error: t("type a number (or numbers separated by commas)") };
    return { value: parts.length === 1 && !/^\[/.test(s) ? nums[0] : nums };
  }
  if (s === "true" || s === "false") return { value: s === "true" };
  return { value: s };
}

// `goal` in a headline: the backticked parts as bold text nodes (never parsed as HTML), without the backticks.
function ticks(text) {
  return String(text).split("`").map((part, k) => (k % 2 ? h("strong", { class: "bench-tick", text: part }) : part)).filter((x) => x !== "");
}

// Chromium fires `change` on a focused, edited box when a redraw removes it: that is not the user committing (the
// text is restored into the new box, waiting for Enter or a real change), so it is ignored.
let redrawing = 0;
export const live = (box) => box.isConnected && !redrawing;
export function redraw(fn) {
  redrawing++;
  try { return fn(); } finally { redrawing--; }
}

const show = (v) => (Array.isArray(v) ? v.map(fmt).join(", ") : typeof v === "boolean" ? String(v) : fmt(v));

// -- edits -------------------------------------------------------------------------------------------
function failed(data, status) {
  return data.errors && data.errors.length ? data.errors
    : [{ field: "status", message: data.error || t("the server answered {status}", { status }) }];
}

// A value refused (`list`: [{field, message}]), beside the earlier refusals of other fields of the idea whose text is
// still in their box: each stays until its own field commits again (post).
function refuse(file, list) {
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
function leftStage(file, key) {
  notice(t("{name} left the stage, so your edit to {key} wasn't saved", { name: ideaName(file), key }), "warn", "bench-left");
}
const undo = () => serial(() => (bench.shown ? post("/api/bench/undo", {}) : false));
const redo = () => serial(() => (bench.shown ? post("/api/bench/redo", {}) : false));

async function job(path, body, record) {
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

async function applyState(s, force = false) {
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

async function refresh() {
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

// -- pictures (token-protected files, fetched into blob: URLs) ----------------------------------------
const pics = new Map();   // path -> {stamp, url, waiting}
function picture(path, stamp) {
  if (!path) return null;
  const have = pics.get(path);
  if (have && have.stamp === stamp) return have.url;
  if (have && have.url) URL.revokeObjectURL(have.url);
  const rec = { stamp, url: null };
  pics.set(path, rec);
  apiFile(`/api/bench/file?path=${encodeURIComponent(path)}`).then(({ blob }) => {
    if (pics.get(path) !== rec) return;
    rec.url = blob ? URL.createObjectURL(blob) : "";
    renderStage();
    renderStrip();
  });
  return null;
}

// The pictures of what the stage shows: {main, gif} paths. The session's result (a sweep or compare) shows its
// chart, as the server names it; an idea's run its side path and pass GIF.
function stagePictures(lt, result) {
  if (result) return { main: (result.pictures || [])[0] || null, gif: null };
  const p = (lt && lt.pictures) || {};
  return { main: p.path_side || null, gif: p.pass_gif || null };
}

// A sweep's or compare's table header in plain words. The CSV is SI: exit speed and peak load are shown in the Units
// setting, to 4 significant figures, as the CSV has them. A swept key keeps its name; a length, weight or speed key's
// values are shown in the Units setting too (as From and To are), the unit in its header: "wheel.top.diameter (in)",
// for `diameter_in` as for `diameter`.
const COLUMN = { idea: () => t("idea"), out: () => t("got out"), jammed: () => t("jammed"),
  exit_speed: () => t("exit speed ({unit})", { unit: units.name("speed") }), elevation_deg: () => t("elevation (°)"),
  spin_rps: () => t("spin (turns/s)"), speed_drop_pct: () => t("speed drop (%)"),
  peak_load_n: () => t("peak load ({unit})", { unit: units.name("force") }) };
const COLUMN_KIND = { exit_speed: "speed", peak_load_n: "force" };
const columnName = (c) => (COLUMN[c] ? COLUMN[c]() : undefined);

// a swept key's column: {kind, inches, key (without `_in`)}, or null for one shown as the file has it
function sweptColumn(c) {
  if (COLUMN[c]) return null;
  const inches = /_in$/.test(c), key = inches ? c.slice(0, -3) : c;
  const { def } = parseKey(key);
  const kind = kindOf(key);
  return kind && (!inches || (def && def.length)) ? { kind, inches, key } : null;
}

function renderTable(result) {
  const wrap = $("#bench-table-wrap");
  const vt = result && result.table;
  wrap.hidden = !vt || !vt.rows.length;
  if (wrap.hidden) { $("#bench-table").replaceChildren(); return; }
  const swept = vt.columns.map(sweptColumn);
  const cell = (x, c) => {
    if (x === "nan") return "–";
    const v = Number(x);
    if (x === "" || !Number.isFinite(v)) return x;
    if (swept[c]) return String(+units.toDisplay(swept[c].inches ? units.fromKey(v, "in") : v, swept[c].kind).toPrecision(5));
    const kind = COLUMN_KIND[vt.columns[c]];
    return kind ? String(+units.toDisplay(v, kind).toPrecision(4)) : x;
  };
  const head = (c, k) => columnName(c) || (swept[k] ? t("{key} ({unit})", { key: swept[k].key, unit: units.name(swept[k].kind) }) : c);
  $("#bench-table").replaceChildren(
    h("thead", {}, h("tr", {}, ...vt.columns.map((c, k) => h("th", { scope: "col", class: COLUMN[c] ? "" : "mono", text: head(c, k) })))),
    h("tbody", {}, ...vt.rows.map((r) => h("tr", {}, ...r.map((x, c) => h("td", { text: cell(x, c) }))))));
}

const listOf = (xs) => (xs.length < 2 ? xs.join("") : t("{list} and {last}", { list: xs.slice(0, -1).join(", "), last: xs[xs.length - 1] }));

// The stage's words for the session's result: {name, author, idea, changed, failed}.
function resultWords(r) {
  const names = r.files.map(ideaName);
  const authors = [...new Set(r.files.map((f) => (ideaOf(f) || {}).author).filter(Boolean))];
  const by = who(r.source);
  const keys = r.table ? r.table.columns.filter((c) => !COLUMN[c]) : [];
  if (r.kind === "compare") {
    return { name: names.length ? t("Comparing {names}", { names: listOf(names) }) : t("Comparing ideas"), author: listOf(authors) || t("someone"),
      idea: names.length ? t("Each idea with the same piece and feed (from {name}).", { name: names[0] }) : t("Each idea with the same piece and feed."),
      changed: r.t ? t("{who} compared these {ago}", { who: by, ago: ago(r.t) }) : "", failed: (why) => t("The comparison failed: {why}", { why }) };
  }
  return { name: names[0] ? t("{name}, swept", { name: names[0] }) : t("An idea, swept"), author: listOf(authors) || t("someone"),
    idea: keys.length ? t("How it does at each value of {keys}.", { keys: listOf(keys) }) : t("How it does at each value swept."),
    changed: r.t ? t("{who} swept this {ago}", { who: by, ago: ago(r.t) }) : "", failed: (why) => t("The sweep failed: {why}", { why }) };
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

function changedLine(i) {
  const lt = i.latest;
  const ch = changes.get(i.file);
  const ran = lt && lt.t ? { t: lt.t, text: (DID[lt.kind] || DID.run)({ who: who(lt.source), ago: ago(lt.t) }) } : null;
  const ed = ch ? { t: ch.t, text: ch.own ? t("edited on this page {ago}", { ago: ago(ch.t) }) : t("changed outside this page {ago}", { ago: ago(ch.t) }) } : null;
  const last = ran && ed ? (ran.t >= ed.t ? ran : ed) : ran || ed;
  return last ? last.text : "";
}

function renderStage() {
  const s = bench.state;
  if (!s) return;
  const i = shownIdea();
  const lockEl = $("#bench-lock");
  lockEl.hidden = !s.lock;
  if (s.lock) lockEl.textContent = t("{who} is running {what}", { who: who(s.lock.source), what: s.lock.file ? ideaName(s.lock.file) : t("a bench") });
  const ro = $("#bench-readonly");
  ro.hidden = !(i && i.read_only_reason);
  ro.textContent = i && i.read_only_reason ? t("This idea can't be changed here until its file is fixed: {why}", { why: i.read_only_reason }) : "";
  for (const b of ["#bench-run", "#bench-change-run", "#bench-sweep"]) $(b).disabled = !i || !!i.read_only_reason;
  $("#bench-compare").disabled = s.ideas.length < 2;
  $("#bench-edit").disabled = !i || !stageHooks.edit;
  $("#bench-edit").title = stageHooks.edit ? t("Edit the shape in 3D") : t("Edit the shape in 3D (coming next)");
  // a result is on stage: Run, Edit shape and Change it act on one of its ideas, so they say which, on the buttons
  // (set before the no-ideas return, so they never keep a deleted idea's name)
  const named = bench.resultShown && s.result && i ? i.name : null;
  $("#bench-run").textContent = named ? t("Run {name}", { name: named }) : t("Run");
  $("#bench-change-run").textContent = named ? t("Run {name}", { name: named }) : t("Run");
  $("#bench-edit").textContent = named ? t("Edit {name}'s shape", { name: named }) : t("Edit shape");
  $("#bench-change-title").textContent = named ? t("Change it: {name}", { name: named }) : t("Change it");
  if (!i) {
    $("#bench-name").textContent = t("No ideas yet");
    $("#bench-author").textContent = "";
    $("#bench-idea").textContent = t("Press New idea to start one.");
    $("#bench-changed").textContent = "";
    $("#bench-headlines").replaceChildren();
    renderTable(null);
    for (const id of ["#bench-uncalibrated", "#bench-failure", "#bench-running", "#bench-picture"]) $(id).hidden = true;
    $("#bench-noresult").hidden = true;
    $("#bench-nopicture").hidden = false;
    return;
  }
  const r = bench.resultShown ? s.result : null;   // the session's sweep or compare, in place of the idea's run
  const words = r ? resultWords(r) : null;
  $("#bench-name").textContent = words ? words.name : i.name;
  $("#bench-author").textContent = words ? words.author : i.author || t("someone");
  $("#bench-idea").textContent = words ? words.idea : i.idea || "";
  $("#bench-changed").textContent = words ? words.changed : changedLine(i);
  $("#bench-run").title = r ? t("Run {name} on its own", { name: i.name }) : "";
  if (r && stageHooks.edit) $("#bench-edit").title = t("Edit the shape of {name} in 3D", { name: i.name });

  // running: this page's jobs for the idea, queued or running (the server always settles them)
  const mine = bench.jobs.find((j) => (j.state === "queued" || j.state === "running")
    && (j.files.includes(i.file) || j.kind === "gallery" || (r && j.files.some((f) => r.files.includes(f)))));
  const runEl = $("#bench-running");
  runEl.hidden = !mine;
  if (mine) {
    const what = mine.kind === "run" ? i.name : JOB[mine.kind] ? JOB[mine.kind]() : mine.kind;
    runEl.textContent = mine.state === "running" ? t("Running {what}…", { what })
      : s.lock && s.lock.source !== "page" ? t("Waiting: {who} is running {what}", { who: who(s.lock.source), what: s.lock.file ? ideaName(s.lock.file) : t("a bench") })
        : t("Waiting to run {what}…", { what });
  }

  const lt = r ? null : i.latest;
  const shownResult = r || lt;   // what the stage shows: never without its UNCALIBRATED line
  const fail = $("#bench-failure");
  const jf = r ? null : jobFailures.get(i.file);
  let why = null;
  if (r && r.ok === false) why = words.failed(r.error || t("no reason given"));
  else if (jf && (!lt || !lt.t || jf.t > lt.t)) why = t("The run failed: {why}", { why: jf.error });
  else if (lt && lt.ok === false) why = t("The run failed: {why}", { why: lt.error || t("no reason given") });
  else if (lt && lt.energy_failed) {
    why = t("This run went wrong inside the simulation (its energy check failed), so its numbers can't be trusted. It is not a verdict on the design: run it again, or ask the agent to look.");
  }
  fail.hidden = !why;
  fail.textContent = why || "";

  // the UNCALIBRATED line (the headlines' last) is shown once, on its own line (L3: the server's field, not its words)
  const heads = lt ? lt.headlines.filter((x) => x !== lt.uncalibrated) : [];
  $("#bench-headlines").replaceChildren(...heads.map((x) => h("li", {}, ...ticks(x))));
  if (r && r.ok !== false && !(r.table && r.table.rows.length)) {
    $("#bench-headlines").append(h("li", { class: "bench-head-note", text: t("Its numbers are in {file}.", { file: base(r.out) }) }));
  }
  renderTable(r);
  $("#bench-noresult").hidden = !!shownResult;
  const unc = $("#bench-uncalibrated");
  unc.hidden = !shownResult;   // never a result without it
  unc.textContent = shownResult ? shownResult.uncalibrated : "";

  const p = stagePictures(lt, r);
  const stamp = shownResult ? `${shownResult.t}` : "";
  const gifBtn = $("#bench-gif");
  gifBtn.hidden = !!r;   // a chart has no moving picture
  gifBtn.disabled = !p.gif;
  if (!p.gif) bench.gif = false;
  gifBtn.setAttribute("aria-pressed", String(bench.gif));
  const path = bench.gif ? p.gif : p.main;
  const url = picture(path, stamp);
  const img = $("#bench-picture");
  if (url) {
    if (img.getAttribute("src") !== url) img.src = url;
    img.alt = r ? t("{name}: the chart", { name: words.name }) : bench.gif ? t("{name}: a piece going through, moving", { name: i.name })
      : t("{name}: the paths of the pieces, from the side", { name: i.name });
    img.hidden = false;
    $("#bench-nopicture").hidden = true;
  } else if (url === null && path && !img.hidden) {
    // fetching the next picture: keep the last one until it arrives
  } else {
    img.hidden = true;
    img.removeAttribute("src");
    $("#bench-nopicture").hidden = false;
    $("#bench-nopicture").textContent = path && url === "" ? t("The picture couldn't be loaded.") : shownResult ? t("This result has no picture.") : t("No picture yet. Press Run to see it work.");
  }
}

// -- vectors (the shape editor): three number boxes, or one for an angle, each with - and + -----------------------
// a - or + step: an angle's 5 degrees; a length's 0.5 in or 10 mm, a field length's 0.5 ft or 0.1 m (in SI, by the
// Units setting, whatever unit the file writes the key in)
const STEP = { degrees: 5 };
const STEP_SI = { imperial: { length: 0.0127, field: 0.1524 }, metric: { length: 0.01, field: 0.1 } };
const stepOf = (vt, def) => (vt.kind ? (STEP_SI[units.system] || STEP_SI.metric)[vt.kind] || null : (def && STEP[def.unit]) || null);
const AXES = ["x", "y", "z"];
const round6 = (x) => Math.round(x * 1e6) / 1e6;
const isNum = (x) => typeof x === "number" && Number.isFinite(x);
const vec3 = (v) => Array.isArray(v) && v.length === 3 && v.every(isNum);
const NUM_RE = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

// Whether `key` is drawn as number boxes with - and +: a point or size ([x, y, z]), any three numbers, an angle, or a
// length (a radius, diameter, width, travel...: 0.5 in or 10 mm a step).
function isVector(key) {
  const { section, k, def } = parseKey(key);
  if (!def || def.choices) return false;
  const { value } = valueOf(key);
  const v = value !== undefined ? value : def.default;
  if (Array.isArray(v)) return vec3(v);
  if (v === undefined || v === null) return ["center", "a", "b"].includes(k) || (k === "size" && section !== "piece");
  return isNum(v) && (def.unit === "degrees" || def.unit === "m");
}

// What a vector field holds and how a new value is written: {value, base (what - and + start from), n, inches, kind,
// write(v) -> [[key, value], ...]}, each as the file writes it. A bar given by its two ends has no center: its
// center is their middle, and moving it moves both ends.
function vectorOf(key) {
  const { section, name, k, def } = parseKey(key);
  const { value, inches, kind } = valueOf(key);
  if (section === "bar" && k === "center" && value === undefined) {
    const ak = dotted(section, name, "a"), bk = dotted(section, name, "b");
    const a = valueOf(ak), b = valueOf(bk);
    if (vec3(a.value) && vec3(b.value) && a.inches === b.inches) {
      const mid = a.value.map((x, j) => round6((x + b.value[j]) / 2));
      return { value: mid, base: mid, n: 3, inches: a.inches, kind, ends: true, write: (v) => {
        const pairs = [[ak, a.value.map((x, j) => round6(x + v[j] - mid[j]))], [bk, b.value.map((x, j) => round6(x + v[j] - mid[j]))]];
        pairs.value = v;
        return pairs;
      } };
    }
  }
  const dv = def ? def.default : undefined;
  const n = isNum(value ?? dv) || (value == null && dv == null && def && def.unit === "degrees" && !["center", "a", "b", "size"].includes(k)) ? 1 : 3;
  return { value, base: value ?? dv ?? null, n, inches, kind, write: (v) => [[key, v]] };
}

function vectorBoxes(key, id, disabled) {
  const { k, def } = parseKey(key);
  const vt = vectorOf(key);
  const step = stepOf(vt, def);   // SI for a key with a kind
  const typed = bench.typed.has(key) ? bench.typed.get(key) : undefined;
  const cur = typed !== undefined ? typed : vt.value;
  const comp = (j) => (vt.n === 1 ? (Array.isArray(cur) ? cur[0] : cur) : Array.isArray(cur) ? cur[j] : undefined);
  const baseOf = (j) => (vt.base == null ? null : vt.n === 1 ? vt.base : vt.base[j]);
  const boxes = [];
  const row = h("div", { class: vt.n === 1 ? "vec vec-1" : "vec" });
  const commit = () => {
    if (boxes.some((b) => !live(b))) return;
    const texts = boxes.map((b) => b.value.trim());
    let v;
    if (texts.every((x) => x === "")) {
      if (vt.ends) return;
      v = null;
    } else {
      // a box still showing the file's number keeps it exactly; another is typed in the Units setting (with a kind)
      const read = (x, j) => {
        if (x === "") return baseOf(j);
        if (!vt.kind) return NUM_RE.test(x) ? Number(x) : NaN;
        if (x === boxes[j].dataset.orig && isNum(comp(j))) return comp(j);
        const v = units.parse(x, vt.kind);
        return typeof v === "object" ? v : fileOf(vt, v);
      };
      const vals = texts.map(read);
      const bad = vals.find((x) => x && typeof x === "object");
      if (bad || vals.some((x) => !isNum(x))) {
        const message = bad ? bad.error : vt.n === 1 ? t("type a number") : t("type a number in each box");
        refuse(bench.shown, [{ field: key, message }]);
        bench.typed.set(key, vt.n === 1 ? texts[0] : texts);
        bench.typedAt.set(key, Math.min(...boxes.map((b) => Number(b.dataset.typedAt || b.dataset.version))));
        render();
        return;
      }
      v = vt.n === 1 ? vals[0] : vals;
    }
    if (row.dataset.sent === JSON.stringify(v)) return;
    row.dataset.sent = JSON.stringify(v);
    const version = Math.min(...boxes.map((b) => Number(b.dataset.typedAt || b.dataset.version)));
    const pairs = v === null ? [[key, null]] : vt.write(v);
    for (const b of boxes) b.dataset.orig = b.value;   // sent: the redraw shows the saved value, as fieldRow's
    setKeys(pairs, version, boxes[0].dataset.file, key);
  };
  // - and +: the step is added to what the file holds when the edit's turn comes, so quick clicks all count
  const nudge = (j, sign) => setKeys(() => {
    const now2 = vectorOf(key);
    if (now2.base == null) return [];
    const add = (x) => (now2.kind ? fileOf(now2, siOf(now2, x) + sign * step) : round6(x + sign * step));
    const v = now2.n === 1 ? add(Array.isArray(now2.base) ? now2.base[0] : now2.base)
      : now2.base.map((x, m) => (m === j ? add(x) : x));
    return now2.write(v);
  }, null, bench.shown, key);
  const unit = unitOf(vt, def);
  const stepText = !step ? "" : vt.kind ? `${units.number(units.toDisplay(step, vt.kind), 4)} ${unit}` : `${fmt(step)} ${def.unit === "degrees" ? "°" : unit}`;
  for (let j = 0; j < vt.n; j++) {
    const c = comp(j);
    const text = c == null ? "" : typeof c === "string" ? c : shownNumber(vt, c);
    const box = h("input", { id: `${id}-${j}`, type: "text", class: "bench-vnum", inputmode: vt.kind ? null : "decimal", autocomplete: "off",
      spellcheck: "false", disabled, dataset: { key: `${key}#${j}` }, "aria-label": vt.n === 1 ? k : `${k} ${AXES[j]}`,
      placeholder: baseOf(j) != null && vt.value === undefined ? shownNumber(vt, baseOf(j)) : "" });
    box.value = text;
    box.dataset.orig = text;
    markUnits(box, vt.kind);
    box.dataset.file = bench.shown;
    box.dataset.version = String(drawnVersion());
    if (bench.typedAt.has(key)) box.dataset.typedAt = String(bench.typedAt.get(key));
    box.addEventListener("input", () => { if (!box.dataset.typedAt) box.dataset.typedAt = box.dataset.version; });
    box.addEventListener("change", () => { if (live(box)) commit(); });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    boxes.push(box);
    const can = !!step && !disabled && vt.base != null;
    const btn = (sign) => h("button", { type: "button", class: "nudge", dataset: { step: sign > 0 ? "+" : "-", axis: String(j) },
      disabled: !can, title: `${sign > 0 ? "+" : "−"}${stepText}`,
      "aria-label": sign > 0 ? t("{what} plus {step}", { what: vt.n === 1 ? k : `${k} ${AXES[j]}`, step: stepText })
        : t("{what} minus {step}", { what: vt.n === 1 ? k : `${k} ${AXES[j]}`, step: stepText }),
      onclick: () => nudge(j, sign) }, sign > 0 ? "+" : "−");
    row.append(h("div", { class: "vec-cell" },
      vt.n === 1 ? null : h("span", { class: "vec-axis", text: AXES[j] }),
      h("div", { class: "nudge-group" }, step ? btn(-1) : null, box, step ? btn(1) : null)));
  }
  return { control: row, unitText: unit, note: vt.ends ? t("the middle of the bar: moving it moves both ends") : null, firstId: `${id}-0` };
}

// One field: label, help, the box (a list for choices, a number box with its unit, a text box; with `vectors`,
// number boxes with - and + for points, sizes and angles), its messages. `prefix` keeps ids unique per panel.
export function fieldRow(key, { focus = false, prefix = null, vectors = false } = {}) {
  const { section, name, k, def } = parseKey(key);
  const i = shownIdea();
  const vt = valueOf(key);
  const { value } = vt;
  const inc = included(section, name);
  const disabled = !!(i && i.read_only_reason) || !!inc;
  const id = `bench-${prefix || (focus ? "f" : "a")}-${key.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  let unitText = unitOf(vt, def);
  const typed = bench.typed.has(key) ? bench.typed.get(key) : undefined;
  let box, vec = null;
  const commit = (v) => {
    if (box.dataset.sent === JSON.stringify(v)) return;
    box.dataset.sent = JSON.stringify(v);
    box.dataset.orig = box.value;   // sent: the redraw shows the saved value ("12 cm" reads back "120"), not this text
    setKey(key, v, Number(box.dataset.typedAt || box.dataset.version), box.dataset.file);
  };
  const stamp = () => {   // which idea and version this box shows, and the version typing began at
    box.dataset.file = bench.shown;
    box.dataset.version = String(drawnVersion());
    if (bench.typedAt.has(key)) box.dataset.typedAt = String(bench.typedAt.get(key));
    box.addEventListener("input", () => { if (!box.dataset.typedAt) box.dataset.typedAt = box.dataset.version; });
  };
  if (vectors && isVector(key)) {
    vec = vectorBoxes(key, id, disabled);
    unitText = vec.unitText;
  } else if (def && def.choices) {
    const opts = choiceList(def.choices);
    const cur = typed !== undefined ? typed : value;
    box = h("select", { id, dataset: { key }, disabled },
      h("option", { value: "" }, def.default != null ? t("(not set: {value})", { value: def.default }) : t("(not set)")),
      ...opts.map((o) => h("option", { value: o }, o)),
      cur != null && !opts.includes(cur) ? h("option", { value: cur }, cur) : null);
    box.value = cur == null ? "" : String(cur);
    box.addEventListener("change", () => { if (live(box)) commit(box.value === "" ? null : box.value); });
  } else if (typeof (value ?? def?.default) === "boolean") {
    box = h("select", { id, dataset: { key }, disabled },
      h("option", { value: "" }, t("(not set)")), h("option", { value: "true" }, t("yes")), h("option", { value: "false" }, t("no")));
    const cur = typed !== undefined ? typed : value;
    box.value = cur == null ? "" : String(cur);
    box.addEventListener("change", () => { if (live(box)) commit(box.value === "" ? null : box.value === "true"); });
  } else {
    const numeric = !!(def && def.unit) || typeof value === "number" || (Array.isArray(value) && value.every((x) => typeof x === "number"));
    const text = typed !== undefined ? (typeof typed === "string" ? typed : shownValue(vt, typed)) : value == null ? "" : shownValue(vt, value);
    const dflt = def && def.default != null ? shownValue({ kind: vt.kind, inches: false }, def.default) : "";   // in meters
    box = h("input", { id, type: "text", dataset: { key }, disabled, autocomplete: "off", spellcheck: "false",
      inputmode: numeric && !Array.isArray(value) && !vt.kind ? "decimal" : null,
      placeholder: dflt ? t("{value} if left out", { value: dflt }) : "", class: numeric ? "bench-num" : "bench-text" });
    box.value = text;
    box.dataset.orig = text;
    markUnits(box, vt.kind);
    const go = () => {
      // a key with a kind is typed in the Units setting and written as the file writes it (U3)
      const r = vt.kind ? parseMeasured(box.value, vt, typed === undefined ? text : null, value) : parseTyped(box.value, numeric);
      if (r.error) {
        refuse(bench.shown, [{ field: key, message: r.error }]);
        bench.typed.set(key, box.value);
        bench.typedAt.set(key, Number(box.dataset.typedAt || box.dataset.version));
        renderChange();
        return;
      }
      commit(r.value);
    };
    box.addEventListener("change", () => { if (live(box)) go(); });
    box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  }
  if (!vec) stamp();
  const label = name === null ? `${section}: ${k}` : `${section} ${name}: ${k}`;
  const msgs = [];
  for (const pr of (i ? i.problems : [])) if (pr.key === key) msgs.push(h("p", { class: `field-msg ${pr.level === "error" ? "error" : "warning"}`, text: pr.message }));
  if (bench.errors.file === bench.shown) {
    for (const e of bench.errors.list) if (e.field === key) msgs.push(h("p", { class: "field-msg error", role: "alert", text: e.message }));
  }
  return h("div", { class: `bench-field${msgs.length ? " has-msg" : ""}${vec ? " bench-vec" : ""}`, dataset: { field: key } },
    h("label", { for: vec ? vec.firstId : id, class: "bench-label" }, label, vec && unitText ? h("span", { class: "unit", text: ` (${unitText})` }) : null),
    vec ? vec.control : h("div", { class: "with-unit" }, box, unitText ? h("span", { class: "unit", text: unitText }) : null),
    vec && vec.note ? h("p", { class: "hint", text: vec.note }) : def ? h("p", { class: "hint", text: def.help }) : null,
    inc ? h("p", { class: "hint", text: t("comes from {file}; change it there", { file: inc }) }) : null,
    ...msgs);
}

// Every entry of the idea, each with every key its section has (bench.name editable; other names via rename).
export function entries() {
  const merged = (bench.idea && bench.idea.merged) || {};
  const out = [];
  const sections = bench.fields ? [...new Set(Object.keys(bench.fields.fields).map((d) => d.split(".")[0]))] : [];
  for (const section of sections) {
    if (SINGLE.includes(section)) {
      if (section === "bench" || entryOf(merged, section, null)) out.push({ section, name: null });
    } else if (section === "shaft") {
      for (const n of Object.keys(merged.shaft || {})) out.push({ section, name: n });
    } else {
      for (const e of Array.isArray(merged[section]) ? merged[section] : []) if (e && typeof e.name === "string") out.push({ section, name: e.name });
    }
  }
  return out;
}
export const keysOf = (section) => Object.keys(bench.fields.fields).filter((d) => d.startsWith(`${section}.`)).map((d) => d.slice(section.length + 1));
export const dotted = (section, name, k) => (name === null ? `${section}.${k}` : `${section}.${name}.${k}`);

function renderChange() {
  redraw(drawChange);
}

// What is being typed in a field panel (`root`, a selector), and the caret, so a redraw can put it back. After a
// Units change, a length, weight or speed half typed is converted to the new units (typedText).
export function keepTyping(root) {
  const act = document.activeElement;
  if (!(act && act.closest && act.closest(root) && act.dataset.key)) return null;
  return { id: act.id, key: act.dataset.key, file: act.dataset.file, typedAt: act.dataset.typedAt, value: typedText(act),
    dirty: act.tagName === "INPUT" && act.value !== act.dataset.orig,
    sel: { text: act.value, start: act.selectionStart, end: act.selectionEnd, dir: act.selectionDirection } };
}

export function restoreTyping(keep, root) {
  if (!keep) return;
  if (keep.file !== bench.shown) {
    // the stage moved to another idea while something was half typed: never write it into the other idea
    if (keep.dirty) leftStage(keep.file, keep.key.split("#")[0]);
    return;
  }
  const el = document.getElementById(keep.id);
  if (!el || !el.closest(root) || el.dataset.key !== keep.key) return;
  if (keep.dirty && el.tagName === "INPUT") {
    // the text typed so far stays, still tied to the version typing began at: if the idea changed since,
    // committing it is refused as stale and says so
    el.value = keep.value;
    el.dataset.typedAt = keep.typedAt || el.dataset.version;
  }
  el.focus();
  // where it was while the text is the same; else all of the new text if all was selected, or the caret at its end
  try { if (el.tagName === "INPUT" && keep.sel.start !== null) el.setSelectionRange(...keptSelection(keep.sel, el.value)); } catch { /* not a text box */ }
}

function drawChange() {
  const i = shownIdea();
  if (!bench.fields || !bench.state) return;
  const keep = keepTyping("#bench-change");   // what is being typed (and the caret) stays across a redraw

  const focus = $("#bench-focus");
  if (!i || !bench.idea) {
    focus.replaceChildren(h("p", { class: "hint", text: i ? t("Loading…") : "" }));
    $("#bench-all").replaceChildren();
    $("#bench-problems").replaceChildren();
    return;
  }
  const keys = [...new Set(i.focus)].slice(0, FOCUS_SHOWN);
  focus.replaceChildren(...(keys.length ? keys.map((k) => fieldRow(k, { focus: true }))
    : [h("p", { class: "hint bench-empty", text: t("Nothing talked about yet. Change a number in Show all, or ask the agent to pin some here.") })]));

  const all = $("#bench-all");
  all.hidden = !bench.showAll;
  $("#bench-showall").textContent = bench.showAll ? t("Hide all") : t("Show all");
  $("#bench-showall").setAttribute("aria-expanded", String(bench.showAll));
  if (bench.showAll) {
    all.replaceChildren(...entries().map(({ section, name }) => h("fieldset", { class: "bench-group", dataset: { entry: name === null ? section : `${section}.${name}` } },
      h("legend", { text: name === null ? section : `${section} ${name}` }),
      ...keysOf(section).filter((k) => k !== "name" || section === "bench").map((k) => fieldRow(dotted(section, name, k))))));
  } else all.replaceChildren();

  const probs = $("#bench-problems");
  const list = [...i.problems];
  if (bench.errors.file === bench.shown) for (const e of bench.errors.list) list.push({ level: "error", key: e.field, message: t("not saved: {why}", { why: e.message }) });
  probs.replaceChildren(...(list.length ? list.map((p) => h("li", { class: `message message-${p.level === "error" ? "error" : "warning"}` },
    h("span", { class: "message-kind", text: p.level === "error" ? t("Error") : t("Warning") }),
    p.key ? h("span", { class: "mono bench-problem-key", text: p.key }) : null,
    h("span", { text: p.message }))) : [h("li", { class: "hint", text: t("No problems.") })]));

  restoreTyping(keep, "#bench-change");
}

function renderStrip() {
  const s = bench.state;
  if (!s) return;
  const others = s.ideas.filter((i) => bench.resultShown || i.file !== bench.shown);
  const r = s.result;
  const card = r && !bench.resultShown ? h("button", { type: "button", class: "bench-card bench-card-result", dataset: { result: r.kind },
    title: t("Put the newest result back on the stage"),
    onclick: () => { bench.pick = null; bench.pickResult = true; applyState(bench.state, true); } },
  h("span", { class: "bench-thumb bench-thumb-empty", text: r.kind === "compare" ? t("comparison") : t("sweep") }),
  h("strong", { class: "bench-card-name", text: r.kind === "compare" ? t("The comparison") : t("The sweep") }),
  h("span", { class: "bench-card-author", text: listOf(r.files.map(ideaName)) })) : null;
  $("#bench-strip").replaceChildren(...(card ? [card] : []), ...(others.length ? others.map((i) => {
    const lt = i.latest;
    const url = picture(lt && lt.pictures ? lt.pictures.path_side : null, lt ? `${lt.t}` : "");
    return h("button", { type: "button", class: "bench-card", dataset: { file: i.file }, title: i.idea || i.name,
      onclick: () => { bench.pick = i.file; bench.pickResult = false; applyState(bench.state, true); } },
      url ? h("img", { class: "bench-thumb", src: url, alt: "" }) : h("span", { class: "bench-thumb bench-thumb-empty", text: lt ? "" : t("not run") }),
      h("strong", { class: "bench-card-name", text: i.name }),
      h("span", { class: "bench-card-author", text: i.author || "" }),
      i.problems.some((p) => p.level === "error") ? h("span", { class: "dot dot-error", title: t("has problems") }) : null);
  }) : [h("p", { class: "hint", text: t("Other ideas in this folder show here.") })]));
}

// -- session tools ----------------------------------------------------------------------------------------
function dialogErrors(sel, errors) {
  const ul = $(sel);
  ul.replaceChildren(...errors.map((e) => h("li", { text: e.message })));
  ul.hidden = !errors.length;
}

function initDialogs() {
  for (const d of document.querySelectorAll("#bench-new-dialog, #bench-sweep-dialog, #bench-compare-dialog")) {
    for (const b of d.querySelectorAll("[data-close]")) b.addEventListener("click", () => d.close());
  }
  // New idea
  $("#bench-new").addEventListener("click", () => {
    const sel = $("#bench-new-from");
    sel.replaceChildren(h("option", { value: "" }, t("Blank")), ...bench.examples.map((x) => h("option", { value: x }, t("the {name} example", { name: x.replace(/_/g, " ") }))));
    for (const id of ["#bench-new-name", "#bench-new-author", "#bench-new-idea"]) $(id).value = "";
    dialogErrors("#bench-new-errors", []);
    $("#bench-new-dialog").showModal();
    $("#bench-new-name").focus();
  });
  $("#bench-new-dialog form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { name: $("#bench-new-name").value.trim(), author: $("#bench-new-author").value.trim(), idea: $("#bench-new-idea").value.trim() };
    if (!body.name) { dialogErrors("#bench-new-errors", [{ message: t("Give the idea a name first.") }]); $("#bench-new-name").focus(); return; }
    if ($("#bench-new-from").value) body.example = $("#bench-new-from").value;
    const { status, data } = await api("POST", "/api/bench/new_idea", body);
    if (status === 200 && data.ok) {
      $("#bench-new-dialog").close();
      bench.pick = null;
      changes.set(data.file, { own: true, t: now() });
      await refresh();
    } else dialogErrors("#bench-new-errors", failed(data, status));
  });

  // Sweep: a talked-about or any number key of the idea, from, to, steps
  const sweepKeys = () => {
    const i = shownIdea();
    const out = [];
    for (const k of i ? i.focus : []) if (typeof valueOf(k).value === "number") out.push(k);
    for (const { section, name } of entries()) {
      for (const k of keysOf(section)) {
        const key = dotted(section, name, k);
        if (typeof valueOf(key).value === "number" && !out.includes(key)) out.push(key);
      }
    }
    return out;
  };
  const fillRange = () => {
    const key = $("#bench-sweep-key").value;
    if (!key) return;
    const vt = valueOf(key);
    const { def } = parseKey(key);
    const now = vt.kind ? units.toDisplay(siOf(vt, vt.value), vt.kind) : vt.value;   // From and To are in the Units setting
    $("#bench-sweep-from").value = fmt(+(now * 0.8).toPrecision(4));
    $("#bench-sweep-to").value = fmt(+(now * 1.2).toPrecision(4));
    $("#bench-sweep-unit").textContent = `${t("Now {value}.", { value: vt.kind ? units.fmt(siOf(vt, vt.value), vt.kind) : `${fmt(vt.value)} ${unitOf(vt, def)}` })} ${def ? def.help : ""}`;
  };
  $("#bench-sweep").addEventListener("click", () => {
    const keys = sweepKeys();
    $("#bench-sweep-key").replaceChildren(...keys.map((k) => h("option", { value: k }, k)));
    dialogErrors("#bench-sweep-errors", keys.length ? [] : [{ message: t("This idea has no numbers to sweep.") }]);
    fillRange();
    $("#bench-sweep-dialog").showModal();
  });
  $("#bench-sweep-key").addEventListener("change", fillRange);
  units.onChange(() => { if ($("#bench-sweep-dialog").open) fillRange(); });   // From and To in the new units
  $("#bench-sweep-dialog form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = $("#bench-sweep-key").value;
    const vt = key ? valueOf(key) : {};
    // a number, or with a kind, one in the Units setting ("4", "4 in"): From and To in its shown unit
    const typed = (sel) => {
      const x = $(sel).value.trim();
      if (!vt.kind || x === "") return Number(x);
      const v = units.parse(x, vt.kind);
      return typeof v === "object" ? NaN : units.toDisplay(v, vt.kind);
    };
    const a = typed("#bench-sweep-from"), b = typed("#bench-sweep-to");
    const n = Number($("#bench-sweep-steps").value);
    const errs = [];
    if (!key) errs.push({ message: t("Pick a number to sweep.") });
    if (!Number.isFinite(a) || !Number.isFinite(b) || $("#bench-sweep-from").value.trim() === "" || $("#bench-sweep-to").value.trim() === "") errs.push({ message: t("From and To must be numbers.") });
    if (!Number.isInteger(n) || n < 2 || n > 25) errs.push({ message: t("Steps must be a whole number from 2 to 25.") });
    if (errs.length) { dialogErrors("#bench-sweep-errors", errs); return; }
    const values = Array.from({ length: n }, (_, k) => {
      const v = +(a + ((b - a) * k) / (n - 1)).toPrecision(6);
      return vt.kind ? fileOf(vt, units.fromDisplay(v, vt.kind)) : v;   // written as the file writes the key
    });
    const sendKey = vt.sendKey;
    const file = bench.shown;
    const r = await job("/api/bench/sweep", { file, key: sendKey, values }, { kind: "sweep", files: [file], key: sendKey });
    if (r === true) $("#bench-sweep-dialog").close();
    else dialogErrors("#bench-sweep-errors", r);
  });

  // Compare: tick ideas
  $("#bench-compare").addEventListener("click", () => {
    const list = $("#bench-compare-list");
    list.replaceChildren(...bench.state.ideas.map((i) => h("label", { class: "bench-compare-item" },
      h("input", { type: "checkbox", value: i.file, checked: i.file === bench.shown }),
      h("strong", { text: i.name }), h("span", { class: "muted", text: ` ${i.author || ""} · ${i.file}` }))));
    dialogErrors("#bench-compare-errors", []);
    $("#bench-compare-dialog").showModal();
  });
  $("#bench-compare-dialog form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = [...document.querySelectorAll("#bench-compare-list input:checked")].map((x) => x.value);
    if (files.length < 2) { dialogErrors("#bench-compare-errors", [{ message: t("Tick two or more ideas.") }]); return; }
    const r = await job("/api/bench/compare", { files }, { kind: "compare", files });
    if (r === true) $("#bench-compare-dialog").close();
    else dialogErrors("#bench-compare-errors", r);
  });

  // Gallery: write it, then open it in a new tab (opened now, while the click still allows a new window)
  $("#bench-gallery").addEventListener("click", async () => {
    const win = window.open("", "_blank");
    if (win) win.document.title = t("Making the gallery…");
    const r = await job("/api/bench/gallery", {}, { kind: "gallery", files: [], win });
    if (r !== true) {
      if (win) win.close();
      notice(t("Can't make the gallery: {why}", { why: r.map((x) => x.message).join("; ") }), "error");
    } else notice(t("Making the gallery: every idea is run once. It opens in a new tab when it is ready."), "info");
  });
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
