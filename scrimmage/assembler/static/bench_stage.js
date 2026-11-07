// The Bench tab's stage (the idea or result shown, its picture, headlines and table) and the strip of the other ideas
// (bench.js is the tab's store).

import { $, h, apiFile } from "./app.js";
import { units } from "./units.js";
import { t } from "./i18n.js";
import { bench, stageHooks, who, DID, JOB, changes, jobFailures, ideaOf, shownIdea, base, ideaName, ago, parseKey,
  applyState } from "./bench.js";
import { kindOf } from "./bench_fields.js";

// `goal` in a headline: the backticked parts as bold text nodes (never parsed as HTML), without the backticks.
function ticks(text) {
  return String(text).split("`").map((part, k) => (k % 2 ? h("strong", { class: "bench-tick", text: part }) : part)).filter((x) => x !== "");
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
export function resultWords(r) {
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

export function changedLine(i) {
  const lt = i.latest;
  const ch = changes.get(i.file);
  const ran = lt && lt.t ? { t: lt.t, text: (DID[lt.kind] || DID.run)({ who: who(lt.source), ago: ago(lt.t) }) } : null;
  const ed = ch ? { t: ch.t, text: ch.own ? t("edited on this page {ago}", { ago: ago(ch.t) }) : t("changed outside this page {ago}", { ago: ago(ch.t) }) } : null;
  const last = ran && ed ? (ran.t >= ed.t ? ran : ed) : ran || ed;
  return last ? last.text : "";
}

export function renderStage() {
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

export function renderStrip() {
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
