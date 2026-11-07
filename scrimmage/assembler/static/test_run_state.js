// The Test tab's run panel: its state (rp) and the small helpers its files share (test_run.js is the panel).

import { units } from "./units.js";
import { i18n, t, SOURCE } from "./i18n.js";

export const CHART_H = 92, PAD_L = 44, PAD_R = 8, PAD_T = 6, TICKS_H = 16;

export const rp = {
  state: null,
  newest: undefined,  // the newest kept run's id last seen (undefined: no state yet)
  shownId: null,
  run: null,          // the shown run: {id, result, motion: {n, times, poses}} once loaded
  compareId: undefined,   // undefined: the default (the run before the shown one); null: none
  compare: null,      // the compared run's result, once loaded
  results: new Map(), // "id|started" -> result (a kept run never changes; an id comes back if the runs folder is emptied)
  picked: null,       // the signals the student picked (a Set), or null for the default
  chartOf: () => null,    // the test's chart as on screen (initRunPanel's chart)
  setChart: () => {},     // writes the test's chart (null for none)
  time: 0,
  playing: false,
  speed: 1,
  wall: 0,
  view: null,
  viewFailed: null,
  seq: 0,
  sweepId: null,      // the sweep shown over the run (its id, "sweep-002"), or null
  newestSweep: undefined,
  sweepY: "t_end",    // what the sweep's chart shows: "t_end" or a signal's name
  sweepFinals: new Map(),  // run id|started -> {signal: its last value}
  spreadId: null,     // the floor spread shown over the run ("spread-001"), or null
  newestSpread: undefined,
  outlined: null,     // the fed piece outlined in the replay (its ball index), or null
};

export function runName(id) {
  const m = /^(?:(sweep|spread)-(\d+)\/)?(\d+)$/.exec(id || "");
  if (!m) return String(id);
  if (m[1] === "spread") return t("Spread {spread}, placement {placement}", { spread: +m[2], placement: +m[3] });
  return m[1] ? t("Sweep {sweep}, run {run}", { sweep: +m[2], run: +m[3] }) : t("Run {run}", { run: +m[3] });
}

// a run's status (run.py's: a key) in words
const STATUS_WORDS = { finished: () => t("finished"), "check failed": () => t("check failed"), "timed out": () => t("timed out"),
  "tipped over": () => t("tipped over"), "walked off": () => t("walked off"), "time limit": () => t("time limit"),
  "repeat limit": () => t("repeat limit"), cancelled: () => t("stopped") };
export const statusWord = (status) => (STATUS_WORDS[status] ? STATUS_WORDS[status]() : status || "");
// A run kept by a sweep, a spread or MCP test_run is machine text (American English, fixed units), so it also keeps
// its words in every Language and Units setting, keyed "en-GB|metric" (result.json's and table.json's `words`:
// blocks/run.py's RunResult.words); the viewer's are shown, switched live. null for anything else, or a language
// added after the run was made, whose viewer gets the American English words in their units (else the machine text).
export function inSetting(words) {
  if (!words || typeof words !== "object") return null;
  return words[`${i18n.lang}|${units.system}`] || words[`${SOURCE}|${units.system}`] || null;
}
// a kept run's result in the viewer's setting: its outcome, checks, marks, seated and notes from its words, if any
export function shownResult(r) {
  const w = r && inSetting(r.words);
  if (!w) return r;
  const checks = Array.isArray(w.checks) ? w.checks : [];
  const marks = Array.isArray(w.marks) ? w.marks : [];
  return { ...r, outcome: w.outcome ?? r.outcome, seated: w.seated || r.seated, notes: w.notes || r.notes,
    checks: (r.checks || []).map((c, i) => ({ ...c, ...(checks[i] || {}) })),
    marks: (r.marks || []).map((m, i) => (marks[i] != null ? { ...m, text: marks[i] } : m)) };
}
// the outcome as the student reads it ("finished" alone says nothing the status doesn't)
export const outcomeText = (r) => {
  const w = inSetting(r.words), said = w && w.outcome != null ? w.outcome : r.outcome;
  return sentence(r.status === "finished" && said === "finished" ? t("every block played") : said);
};
export const sentence = (s) => (s ? `${s[0].toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? "" : "."}` : "");
export const end = () => (rp.run ? rp.run.result.t_end || 0 : 0);
export const start = () => (rp.run && rp.run.motion.times.length ? rp.run.motion.times[0] : 0);

export function resultKey(id) {
  const entry = ((rp.state && rp.state.runs) || []).find((r) => r.id === id);
  return `${id}|${entry ? entry.started : ""}`;
}
