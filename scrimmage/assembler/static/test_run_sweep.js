// The Test tab's run panel: the sweep and the floor spread shown over the run, each a table with a row per run, and
// the sweep's chart (test_run.js is the panel).

import { $, h } from "./app.js";
import { units } from "./units.js";
import { i18n, t } from "./i18n.js";
import { result, show } from "./test_run.js";
import { PAD_L, TICKS_H, rp, statusWord, inSetting, outcomeText, sentence, resultKey } from "./test_run_state.js";
import { shownUnit, toShown, tokens } from "./test_run_chart.js";

// -- the sweep -----------------------------------------------------------------------------------------------------
// What a sweep tried, in words, with the unit shown (the Units setting's)
const L = () => ({ unit: units.name("length") });
const ROBOT_WORDS = { mass: () => t("the robot's weight ({unit})", { unit: units.name("mass") }),
  com_forward: () => t("the robot's center of mass, forward ({unit})", L()),
  com_left: () => t("the robot's center of mass, left ({unit})", L()), com_up: () => t("the robot's center of mass, up ({unit})", L()) };
const AXIS_WORDS = { x: () => t("forward (x, {unit})", L()), y: () => t("left (y, {unit})", L()), z: () => t("up (z, {unit})", L()),
  yaw: () => t("turned (yaw, °)") };
const KEY_UNITS = { volts: () => "V", seconds: () => "s", distance: () => units.name("field") };
const STATION_WORDS = { interval: () => t("time between pieces (s)"), stock: () => t("stock"),
  spread_yaw_deg: () => t("left/right angle (± °)"), spread_pitch_deg: () => t("up/down angle (± °)"),
  spread_speed: () => t("speed (± {unit})", { unit: units.name("speed") }), spread_position: () => t("release box (± {unit})", L()),
  spread_rotation_deg: () => t("turned (± °)") };
const isGrip = (sw) => !!(sw.target && "grip" in sw.target);
const FILE_SI = { in: 0.0254, m: 1 };     // a block's length units in the test file
// A sweep's number as the file has it: [kind, SI per one of the file's unit], or null for one shown as it is
// (degrees, seconds, volts, the shuffle). The robot file and a station keep SI; a block, its own unit.
function targetMeasure(target) {
  if (!target) return null;
  if ("robot" in target) return target.robot === "mass" ? ["mass", 1] : ["length", 1];
  if ("mount" in target) return target.axis === "yaw" ? null : ["length", 1];
  if ("station" in target) return target.key === "spread_position" ? ["length", 1] : target.key === "spread_speed" ? ["speed", 1] : null;
  if ("block" in target) {
    if (target.key === "distance") return ["field", 1];
    const b = blockAt(rp.state && rp.state.test, target.block);
    return target.key === "to" && b && FILE_SI[b.unit] ? ["length", FILE_SI[b.unit]] : null;
  }
  return null;
}
// a sweep row's value as the table and chart show it: a length, weight or speed in the units shown (to 5 figures:
// the sweep keeps 6, so 100 lb tried reads 100, not 99.9999); a grip's material names (not numbers) are placed in
// order, one step apart, and labeled with the name
// (table.json's SI value and kind when the sweep has one: the block's unit as it was then, not as the test has it now)
function rowValue(sw, r) {
  if (isGrip(sw)) return sw.rows.indexOf(r);
  const kept = sw.table && sw.table.rows[sw.rows.indexOf(r)];
  if (sw.table && sw.table.kind && kept && kept.si != null) return +units.toDisplay(kept.si, sw.table.kind).toPrecision(5);
  const m = sw.table ? null : targetMeasure(sw.target);
  return m ? +units.toDisplay(Number(r.value) * m[1], m[0]).toPrecision(5) : Number(r.value);
}
const measured = (sw) => (sw.table ? !!sw.table.kind : !!targetMeasure(sw.target));
const rowText = (sw, r) => (measured(sw) ? String(rowValue(sw, r)) : r.value);

// The block at a place in the test JSON (0-based, through repeat bodies), or null.
function blockAt(test, place) {
  let blocks = (test && test.blocks) || [], b = null;
  for (const i of place || []) { b = blocks[i]; if (!b) return null; blocks = b.body || []; }
  return b;
}

const blockWords = (b) => {
  if (!b) return t("a block");
  const name = b.motor || b.joint;
  return name ? `${b.do} ${name}` : b.do;
};

function targetText(target) {
  if (!target) return "";
  if ("block" in target) {
    const b = blockAt(rp.state && rp.state.test, target.block);
    const unit = target.key === "to" ? (b && b.unit === "deg" ? "°" : b && FILE_SI[b.unit] ? units.name("length") : b ? b.unit : "")
      : KEY_UNITS[target.key] ? KEY_UNITS[target.key]() : "";
    const p = { block: blockWords(b), place: target.block.map((i) => i + 1).join(" in "), key: target.key, unit };
    return unit ? t("{block} (block {place}): {key} ({unit})", p) : t("{block} (block {place}): {key}", p);
  }
  if ("robot" in target) return ROBOT_WORDS[target.robot] ? ROBOT_WORDS[target.robot]() : target.robot;
  if ("grip" in target) return t("grip material of {part}", { part: target.grip });
  if ("mount" in target) {
    return t("where {part} is mounted: {axis}", { part: target.mount, axis: AXIS_WORDS[target.axis] ? AXIS_WORDS[target.axis]() : target.axis });
  }
  if ("station" in target) {
    return t("{station}: {setting}", { station: target.station, setting: STATION_WORDS[target.key] ? STATION_WORDS[target.key]() : target.key });
  }
  if (target.setup === "seed") return t("the shuffle");
  return "";
}

const sweepNumber = (id) => (/^sweep-(\d+)$/.exec(id || "") || [])[1];
const rowRun = (sweep, k) => `${sweep.id}/${String(k + 1).padStart(3, "0")}`;
const hasRun = (id) => ((rp.state && rp.state.runs) || []).some((r) => r.id === id);
const shownSweep = () => ((rp.state && rp.state.sweeps) || []).find((s) => s.id === rp.sweepId) || null;
const checkColumns = (sweep) => (sweep.rows.length ? Object.keys(sweep.rows[0]).filter((c) => !["value", "status", "outcome", "t_end"].includes(c)) : []);

export function sweepShown() {
  const sw = shownSweep();
  return sw ? { id: sw.id, rows: sw.rows.length, y: rp.sweepY, points: sweepPoints(sw) } : null;
}

export function renderSweep() {
  const wrap = $("#test-sweep-wrap");
  const sw = shownSweep();
  wrap.hidden = !sw;
  if (!sw) return;
  $("#test-sweep-title").textContent = t("Sweep {sweep}: {what}", { sweep: +sweepNumber(sw.id), what: targetText(sw.target) });
  const notes = [...(sw.cancelled ? [t("You stopped this sweep, so it has the values done before that.")] : []), ...(sw.problems || [])];
  const note = $("#test-sweep-note");
  note.hidden = !notes.length;
  note.textContent = notes.join(" ");
  const checks = checkColumns(sw);
  const table = $("#test-sweep-table");
  const key = JSON.stringify([sw.id, sw.rows, checks, rp.shownId, (rp.state.runs || []).map((r) => r.id), units.system, i18n.lang]);
  if (table.dataset.key !== key) {
    table.dataset.key = key;
    table.replaceChildren(
      h("thead", {}, h("tr", {}, h("th", { scope: "col", text: t("Value") }), h("th", { scope: "col", text: t("Outcome") }),
        ...checks.map((c, i) => h("th", { scope: "col", title: c, text: t("Check {n}", { n: i + 1 }) })))),
      h("tbody", {}, ...sw.rows.map((r, k) => {
        const id = rowRun(sw, k), ran = hasRun(id);
        const current = id === rp.shownId;
        const outcome = rowOutcome(sw, r, k);
        return h("tr", { class: ran ? "test-sweep-row" : "test-sweep-row test-sweep-norun", "data-row": String(k), "aria-current": current ? "true" : null,
          onclick: ran ? () => { if (id !== rp.shownId) show(id); } : null },
          h("td", {}, ran ? h("button", { type: "button", class: "linklike test-sweep-value", "data-run": id,
            "aria-label": t("Show the run at {value}", { value: rowText(sw, r) }), text: rowText(sw, r) }) : h("span", { text: rowText(sw, r) })),
          h("td", { class: "test-sweep-outcome", text: outcome }),
          ...checks.map((c) => h("td", { class: r[c] === "passed" ? "test-check-ok" : r[c] === "failed" ? "test-check-bad" : "",
            title: c, text: r[c] === "passed" ? "✓" : r[c] === "failed" ? "✗" : "" })));
      })));
    if (checks.length) {
      const named = (inSetting(sw.table && sw.table.words) || {}).columns;
      const column = (c, i) => (Array.isArray(named) && named.length === checks.length ? named[i] : c);
      table.prepend(h("caption", { class: "test-sweep-caption" }, ...checks.map((c, i) => h("span", { text: t("Check {n}: {check}", { n: i + 1, check: sentence(column(c, i)) }) }))));
    }
  }
  const sel = $("#test-sweep-y");
  const signals = ((rp.state.palette && rp.state.palette.signals) || []);
  const opts = [["t_end", t("when the run ended (s)")], ...signals.map((s) => [s, shownUnit(s)
    ? t("{signal} at the end ({unit})", { signal: s, unit: shownUnit(s) }) : t("{signal} at the end", { signal: s })])];
  const okey = opts.map((o) => o.join("=")).join("|");
  if (sel.dataset.key !== okey) {
    sel.replaceChildren(...opts.map(([v, text]) => h("option", { value: v, text })));
    sel.dataset.key = okey;
  }
  if (!opts.some(([v]) => v === rp.sweepY)) rp.sweepY = "t_end";
  sel.value = rp.sweepY;
  drawSweep();
}

// [[value, y], ...] of the rows that ran (a signal's final value once its run is loaded)
function sweepPoints(sw) {
  const out = [];
  sw.rows.forEach((r, k) => {
    const x = rowValue(sw, r);
    if (rp.sweepY === "t_end") { if (r.t_end !== "" && r.t_end != null) out.push([x, Number(r.t_end)]); return; }
    const fin = rp.sweepFinals.get(resultKey(rowRun(sw, k)));
    if (fin && rp.sweepY in fin) out.push([x, toShown(rp.sweepY)(fin[rp.sweepY])]);
  });
  return out;
}

const loadingFinals = new Set();
async function loadFinals(sw) {
  await Promise.all(sw.rows.map(async (r, k) => {
    const id = rowRun(sw, k), key = resultKey(id);
    if (!hasRun(id) || rp.sweepFinals.has(key) || loadingFinals.has(key)) return;
    loadingFinals.add(key);
    try {
      const res = await result(id);
      const fin = {};
      // an empty last sample (null: no piece on a rod) leaves that row's point out
      for (const [name, vs] of Object.entries(res.signals || {})) if (name !== "t" && vs.length && vs[vs.length - 1] != null) fin[name] = vs[vs.length - 1];
      rp.sweepFinals.set(key, fin);
    } catch {
      rp.sweepFinals.set(key, {});     // that row's point is left out
    } finally {
      loadingFinals.delete(key);
    }
  }));
  while (rp.sweepFinals.size > 64) rp.sweepFinals.delete(rp.sweepFinals.keys().next().value);
  const now = shownSweep();
  if (now && now.id === sw.id) drawSweep();
}

export function drawSweep() {
  const sw = shownSweep();
  const canvas = $("#test-sweep-chart");
  if (!sw) return;
  if (rp.sweepY !== "t_end" && sw.rows.some((r, k) => hasRun(rowRun(sw, k)) && !rp.sweepFinals.has(resultKey(rowRun(sw, k))))) loadFinals(sw);
  const pts = sweepPoints(sw);
  const tk = tokens();
  const W = Math.max(200, Math.floor(canvas.clientWidth || canvas.parentElement.clientWidth || 320)), H = 140;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.height = `${H}px`;
  }
  const yName = rp.sweepY === "t_end" ? t("when the run ended") : t("{signal} at the end", { signal: rp.sweepY });
  canvas.setAttribute("aria-label", t("{y} against {x}: {points}", { y: yName, x: targetText(sw.target),
    points: pts.map(([x, y]) => `${isGrip(sw) ? sw.rows[x].value : +x.toPrecision(4)} → ${+y.toPrecision(4)}`).join(", ") || t("no runs") }));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = tk.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.font = `10px ${tk.font}`;
  ctx.fillStyle = tk.text;
  if (!pts.length) {
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(rp.sweepY !== "t_end" && loadingFinals.size ? t("Loading…") : t("No run to chart"), W / 2, H / 2);
    return;
  }
  const xs = sw.rows.map((r) => rowValue(sw, r)).filter(isFinite);
  let [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  if (x1 - x0 < 1e-9) { x0 -= 1; x1 += 1; }
  let [y0, y1] = [Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1]))];
  if (y1 - y0 < 1e-6) { y0 -= 1; y1 += 1; }
  const padR = 14, padT = 8, padB = 10, plotH = H - TICKS_H - padT - padB;   // padB: the lowest dot clears the labels
  const X = (v) => PAD_L + 6 + ((v - x0) / (x1 - x0)) * (W - PAD_L - 6 - padR);
  const Y = (v) => padT + (1 - (v - y0) / (y1 - y0)) * plotH;
  ctx.lineWidth = 1; ctx.strokeStyle = tk.grid;
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const v of [y0, y1]) {
    ctx.beginPath(); ctx.moveTo(PAD_L, Math.round(Y(v)) + 0.5); ctx.lineTo(W - padR, Math.round(Y(v)) + 0.5); ctx.stroke();
    ctx.fillText(+v.toFixed(Math.abs(v) >= 100 ? 0 : 2) + "", PAD_L - 5, Y(v));
  }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const labels = xs.length <= 6 ? xs : [x0, x1];
  for (const v of labels) ctx.fillText(isGrip(sw) ? String(sw.rows[v].value) : +v.toPrecision(4) + "", X(v), H - TICKS_H + 3);
  ctx.strokeStyle = tk.run; ctx.lineWidth = 1.75;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
  ctx.stroke();
  const shown = sw.rows.findIndex((_, k) => rowRun(sw, k) === rp.shownId);
  ctx.fillStyle = tk.run;
  for (const [x, y] of pts) { ctx.beginPath(); ctx.arc(X(x), Y(y), 3.5, 0, 2 * Math.PI); ctx.fill(); }
  if (shown >= 0) {
    const p = pts.find(([x]) => x === rowValue(sw, sw.rows[shown]));
    if (p) { ctx.strokeStyle = tk.mark; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(X(p[0]), Y(p[1]), 6.5, 0, 2 * Math.PI); ctx.stroke(); }
  }
}

// A sweep's or spread's row k in words: its outcome from table.json in the viewer's setting, else table.csv's (machine
// text: an older folder, or a row that couldn't run, whose why is the error's American English).
function rowOutcome(sw, r, k) {
  if (r.status === "couldn't run") return t("Couldn't run: {why}", { why: r.outcome });
  const kept = (inSetting(sw.table && sw.table.words) || {}).outcomes;
  const said = Array.isArray(kept) && kept[k] != null ? kept[k] : r.outcome;
  return t("{status}: {outcome}", { status: statusWord(r.status), outcome: outcomeText({ status: r.status, outcome: said }) });
}

// -- the floor spread ------------------------------------------------------------------------------------------------
const shownSpread = () => ((rp.state && rp.state.spreads) || []).find((s) => s.id === rp.spreadId) || null;

export function spreadShown() {
  const sp = shownSpread();
  return sp ? { id: sp.id, rows: sp.rows.length, sentence: sp.sentence } : null;
}

export function renderSpread() {
  const wrap = $("#test-spread-wrap");
  const sp = shownSpread();
  wrap.hidden = !sp;
  if (!sp) return;
  const num = /^spread-(\d+)$/.exec(sp.id);
  $("#test-spread-title").textContent = num ? t("Spread {spread}", { spread: +num[1] }) : String(sp.id);
  const words = inSetting(sp.table && sp.table.words) || {};
  $("#test-spread-sentence").textContent = sentence(words.sentence || sp.sentence || "");
  const note = $("#test-spread-note");
  note.hidden = !sp.cancelled;
  note.textContent = sp.cancelled ? t("You stopped this spread, so it has the placements done before that.") : "";
  const table = $("#test-spread-table");
  const key = JSON.stringify([sp.id, sp.rows, rp.shownId, (rp.state.runs || []).map((r) => r.id), units.system, i18n.lang]);
  if (table.dataset.key === key) return;
  table.dataset.key = key;
  table.replaceChildren(
    h("thead", {}, h("tr", {}, h("th", { scope: "col", text: t("Placement") }), h("th", { scope: "col", text: t("Picked up") }),
      h("th", { scope: "col", text: t("Outcome") }))),
    h("tbody", {}, ...sp.rows.map((r, k) => {
      const id = rowRun(sp, k), ran = hasRun(id);
      const outcome = rowOutcome(sp, r, k);
      const picked = r["picked up"] === "yes";
      const placement = Array.isArray(words.placements) && words.placements[k] != null ? words.placements[k] : r.placement;
      return h("tr", { class: ran ? "test-sweep-row" : "test-sweep-row test-sweep-norun", "data-row": String(k),
        "aria-current": id === rp.shownId ? "true" : null, onclick: ran ? () => { if (id !== rp.shownId) show(id); } : null },
        h("td", {}, ran ? h("button", { type: "button", class: "linklike", "data-run": id,
          "aria-label": t("Show the run at {value}", { value: placement }), text: placement }) : h("span", { text: placement })),
        h("td", { class: picked ? "test-check-ok" : "test-check-bad", text: picked ? t("yes") : r["picked up"] === "no" ? t("no") : r["picked up"] }),
        h("td", { class: "test-sweep-outcome", text: outcome }));
    })));
}
