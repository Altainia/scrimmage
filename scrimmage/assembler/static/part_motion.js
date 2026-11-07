// The Part tab's inspector sections for how a body moves: joint, tryall (the "Try it" sliders for every joint) and
// slots (motor, encoder, gyro), together because they share the motor's draft (part_inspector.js lists the sections).

import { h, api, notice, lengthWords } from "./app.js";
import { part, view3d, addSection, editOp, render, live, field, box, number, onPart, shownMeasure,
  boxMeasure } from "./part.js";
import { units } from "./units.js";
import { t } from "./i18n.js";
import { cadToRobot, robotToCad, dirToCad, dirToRobot, tryRange } from "./part_frame.js";
import { bodyOf, rt, why, fmtNum, origin, selSig, AXIS_WORDS, jointSpec, patchJoint, axisWords, pointWords, needView,
  buttons, descendants, details } from "./part_helpers.js";

// -- joint ----------------------------------------------------------------------------------------------------

// Turns or Slides chosen but no axis picked or snapped yet: nothing is saved until one is (a guessed axis would
// look right and simulate wrong). Esc, Cancel, welding it or selecting another body drops the draft.
let jointDraft = null;   // {body, kind}
const draftKind = (name) => (jointDraft && jointDraft.body === name ? jointDraft.kind : null);
function dropDraft(name) { if (draftKind(name)) { jointDraft = null; render(); } }
onPart(() => { if (jointDraft && !(part.sel.kind === "body" && part.sel.keys.has(jointDraft.body))) jointDraft = null; });

// Save body `name`'s joint as `kind` about `axis` through `anchor` (CAD), keeping its other settings when the kind
// stays; a turn changed to a slide (or back) drops the range and spring, whose units differ.
function saveJoint(name, kind, anchor, axis) {
  return editOp("set_joint", () => {
    const b = bodyOf(name);
    const j = b.joint;
    if (j && j.kind !== kind && (j.range || j.spring)) notice(t("The range and spring were cleared: a turn is in degrees and a slide in {unit}.", { unit: lengthWords() }), "info");
    const spec = !j ? { kind, anchor, axis } : jointSpec(j, j.kind === kind ? { anchor, axis } : { kind, anchor, axis, range: null, spring: null });
    return { body: name, parent: b.parent, joint: spec };
  });
}

function pickAxis(name) {
  if (!needView()) return;
  const b = bodyOf(name);
  const kind = draftKind(name) || (b && b.joint && b.joint.kind);
  if (!kind) return;
  const turns = kind === "hinge";
  view3d.startPick({
    owner: selSig(),
    key: `axis:${name}`,
    hint: turns ? t("Click inside a hole or on a round face: {body} turns about its center line.", { body: name })
      : t("Click a flat face that faces the way {body} slides.", { body: name }),
    onCancel: () => dropDraft(name),
    onPick: async (hit) => {
      const { status, data } = await api("POST", "/api/part/axis", { piece: hit.key, triangle: hit.triangle, kind: turns ? "round" : "flat" });
      if (status !== 200) return { again: why(data) };
      if (draftKind(name)) jointDraft = null;
      view3d.cancelPick();
      await saveJoint(name, kind, data.point, data.axis);
    },
  });
}

// Snap the axis to the robot's x, y or z (keeping its sign); a draft joint is saved through its center of gravity.
function snap(name, i) {
  const b = bodyOf(name), r = rt(), robot = [0, 0, 0];
  const kind = draftKind(name);
  if (kind) {
    robot[i] = 1;
    jointDraft = null;
    if (view3d.cancelPick) view3d.cancelPick();
    return saveJoint(name, kind, robotToCad(r, b.com || [0, 0, 0]), dirToCad(r, robot));
  }
  const now = dirToRobot(r, b.joint.axis);
  robot[i] = now[i] < 0 ? -1 : 1;
  return patchJoint(name, { axis: dirToCad(r, robot) });
}

// The pivot point typed as numbers, for a hinge with no round face to click (or snapped, which puts it through the
// center of gravity): in the robot frame, in the chosen units, as the inspector shows points. One box commits all
// three; a box not changed keeps its exact value.
function pivotRow(b) {
  const now = cadToRobot(rt(), b.joint.anchor);
  const els = ["x", "y", "z"].map((a, i) => box({ id: `part-pivot-${a}`, type: "text", inputmode: "decimal", kind: "length", value: shownMeasure(now[i], "length"),
    "aria-label": t("Pivot point {axis}, {unit}", { axis: AXIS_WORDS[i](), unit: lengthWords() }) }, () => {
    const vals = els.map((e, k) => boxMeasure(e, now[k], "pivot", "length"));
    if (vals.some((v) => Number.isNaN(v))) return;
    if (vals.some((v) => v === null)) { part.errors = [{ field: "pivot", message: t("Type all three numbers: x, y and z.") }]; render(); return; }
    patchJoint(b.name, { anchor: robotToCad(rt(), vals) });
  }));
  const row = field(t("Pivot point"), h("span", { class: "with-unit part-pivot" },
    ...els.flatMap((el, i) => [h("span", { class: "unit", text: "xyz"[i] }), el]), h("span", { class: "unit", text: units.name("length") })), "pivot",
    t("Where it turns, in {unit}: x forward, y left, z up. Type it when there's no round face to click.", { unit: lengthWords() }));
  row.querySelector("label").htmlFor = "part-pivot-x";
  return row;
}

// "Try it": a slider that moves the body (and the bodies on it) in the view only.
const trySig = new Map();   // body -> the joint the slider value was for
onPart(() => {
  for (const b of part.state.bodies) {
    const sig = b.joint ? JSON.stringify([b.joint.kind, b.joint.anchor, b.joint.axis]) : null;
    if (trySig.get(b.name) !== sig) { trySig.set(b.name, sig); if (part.tryit.delete(b.name) && view3d.pose) view3d.pose(); }
  }
});

// which: "try" (the joint's own section) or "move" (the sliders for every joint)
function tryIt(b, which = "try") {
  const j = b.joint, slide = j.kind === "slide";
  const [lo, hi] = tryRange(j);
  const v = Math.min(hi, Math.max(lo, part.tryit.get(b.name) || 0));
  const id = `part-try-${b.name}`;
  // the slider runs in meters (or degrees); its text is in the chosen units
  const said = (x) => (slide ? units.fmt(x, "length") : `${fmtNum(x)}°`);
  const spoken = (x) => (slide ? `${units.shown(x, "length", 0)} ${lengthWords()}` : t("{n} degrees", { n: fmtNum(x) }));
  const out = h("output", { for: id, class: "part-try-out", text: said(v) });
  const input = h("input", { type: "range", id, min: String(lo), max: String(hi), step: slide ? "0.005" : "1",
    "aria-valuetext": spoken(v) });
  input.value = String(v);
  input.addEventListener("input", () => {
    const x = +input.value;
    part.tryit.set(b.name, x);
    out.textContent = said(x);
    input.setAttribute("aria-valuetext", spoken(x));
    if (view3d.pose) view3d.pose();
  });
  const zero = h("button", { type: "button", class: "linklike", onclick: () => {
    part.tryit.delete(b.name); input.value = "0"; out.textContent = said(0); if (view3d.pose) view3d.pose();
  } }, t("back to 0"));
  const p = { body: b.name };
  const label = which === "move" ? (slide ? t("Move: {body} slides", p) : t("Move: {body} turns", p))
    : slide ? t("Try it: {body} slides", p) : t("Try it: {body} turns", p);
  return h("div", { class: "field part-try", dataset: { field: `try-${b.name}` } },
    h("label", { class: "field-label", for: id, text: label }),
    h("div", { class: "part-try-row" }, input, out, zero),
    h("p", { class: "hint", text: t("Only moves the drawing, to check the axis. Nothing is saved.") }));
}

// Range, spring, damping and friction (under "More").
const pairDraft = new Map();   // "<body>:range" / "<body>:spring" -> the two boxes' text while only one is typed (.sys: its units)
function jointMore(c, b) {
  const j = b.joint, slide = j.kind === "slide", plugin = c.plugin;
  // kinds (units.js) for the ones shown in the chosen units; null: as written (degrees, and the per-radian and
  // damping units, which have no imperial form here)
  const k = slide ? { pos: "length", k: "spring", d: null, f: "force" } : { pos: null, k: null, d: null, f: "torque" };
  const u = slide ? { pos: units.name("length"), k: units.name("spring"), d: "N·s/m", f: units.name("force") }
    : { pos: "°", k: "N·m/rad", d: "N·m·s/rad", f: units.name("torque") };
  const shownAs = (x, kind, digits) => (kind ? shownMeasure(x, kind) : fmtNum(x, digits));
  const readAs = (el, saved, kind, name) => (kind ? boxMeasure(el, saved, name, kind) : number(el.value, name));
  const rows = [];
  // two boxes that are saved together (range: from and to; spring: stiffness and rest); one typed alone waits
  const pair = (which, saved, ids, kinds, send, waitText) => {
    const key = `${b.name}:${which}`, kept = pairDraft.get(key);
    // typed in the other system (Units changed since): converted, as a box being typed in is
    // (from what was typed, kept with it, so switching back and forth doesn't drift: carryText's rule)
    const orig = kept && (kept.orig || kept), origSys = kept && (kept.origSys || kept.sys);
    const d = kept && kept.sys !== units.system
      ? Object.assign(orig.map((x, i) => (origSys === units.system ? x : units.convert(x, kinds[i], origSys))),
        { sys: units.system, orig, origSys }) : kept;
    if (d !== kept) pairDraft.set(key, d);
    const text = d || (saved ? saved.map((x, i) => shownAs(x, kinds[i], 4)) : ["", ""]);
    const els = ids.map(([id, extra], i) => box({ id, type: "text", inputmode: "decimal", value: text[i], kind: kinds[i], ...extra }, () => {
      const now = els.map((e) => e.value.trim());
      const vals = els.map((e, n) => readAs(e, saved ? saved[n] : null, kinds[n], which));
      if (vals.some((v) => Number.isNaN(v))) return;
      pairDraft.set(key, Object.assign(now, { sys: units.system }));
      const r = send(vals);
      if (r) r.then((x) => { if (x.ok) { pairDraft.delete(key); render(); } });
      else {   // only one typed: wait for the other, without a redraw (so Tab still reaches the other box)
        const hint = els[0].closest(".field") && els[0].closest(".field").querySelector(".hint");
        if (hint) hint.textContent = waitText;
      }
    }));
    return { els, waiting: !!d };
  };
  const rangeWait = plugin ? t("Type the other end too, or empty both to go back to the CAD plugin's range.")
    : t("Type the other end too, or empty both for no limit.");
  const range = pair("range", j.range, [["part-range-from", { placeholder: t("no limit") }], ["part-range-to", { placeholder: t("no limit"), "aria-label": t("Range to") }]], [k.pos, k.pos],
    ([a, z]) => ((a === null) !== (z === null) ? null : patchJoint(b.name, a === null
      ? { range: null, ...(plugin ? {} : { limit_solref: null }) } : { range: [a, z] })),   // no range: no stops to stiffen
    rangeWait);
  const rangeRow = field(t("Range: from"), h("span", { class: "with-unit part-pair" }, range.els[0], h("span", { class: "unit", text: t("{unit} to", { unit: u.pos }) }), range.els[1], h("span", { class: "unit", text: u.pos })), "range",
    range.waiting ? rangeWait
      : slide ? t("How far it slides each way, in {unit} from where the CAD shows it.", { unit: lengthWords() }) : t("How far it turns each way, in degrees from where the CAD shows it."));
  rangeRow.querySelector("label").htmlFor = "part-range-from";
  rows.push(origin(c, b, ["range"], rangeRow));
  const springSaved = j.spring && !Array.isArray(j.spring) ? [j.spring.stiffness, j.spring.rest] : j.spring;
  const spring = pair("spring", springSaved, [["part-spring", { placeholder: t("none") }], ["part-spring-rest", { placeholder: "0", "aria-label": t("Spring rest ({unit})", { unit: u.pos }) }]], [k.k, k.pos],
    ([k, r]) => (k === null && r !== null ? null
      : patchJoint(b.name, { spring: k === null ? null : plugin ? { stiffness: k, rest: r || 0 } : [k, r || 0] })),
    t("Type the stiffness too."));
  const springRow = field(t("Spring"), h("span", { class: "with-unit part-pair" }, spring.els[0], h("span", { class: "unit", text: t("{unit}, rest at", { unit: u.k }) }), spring.els[1], h("span", { class: "unit", text: u.pos })), "spring",
    spring.waiting ? t("Type the stiffness too.") : t("Pulls it back toward its rest position. Empty means no spring."));
  springRow.querySelector("label").htmlFor = "part-spring";
  rows.push(origin(c, b, ["spring"], springRow));
  for (const [key, label, unitText, kind, hint] of [["damping", t("Damping"), u.d, k.d, t("Slows it more the faster it moves.")],
    ["frictionloss", t("Friction"), u.f, k.f, t("A steady drag that holds it until pushed hard enough.")]]) {
    const el = box({ id: `part-${key}`, type: "text", inputmode: "decimal", kind, value: j[key] ? shownAs(j[key], kind, 5) : "", placeholder: "0" }, () => {
      const v = readAs(el, j[key], kind, key);
      if (!Number.isNaN(v)) patchJoint(b.name, { [key]: plugin ? v : v || 0 });
    });
    const row = field(label, h("span", { class: "with-unit" }, el, h("span", { class: "unit", text: unitText })), key, hint);
    row.querySelector("label").htmlFor = `part-${key}`;
    rows.push(origin(c, b, [key], row));
  }
  if (j.range || j.limit_solref) rows.push(origin(c, b, ["limit_solref"], stopsRow(b, j)));
  return details("joint-more", t("More: range, spring, damping, friction"), rows,
    part.errors.some((e) => ["range", "spring", "damping", "frictionloss", "limit_solref"].includes(e.field)));
}

// How hard the ends of the range are: MuJoCo's limit_solref ([time constant s, damping ratio]; docs/part-tab.md), in
// plain words. Normal is MuJoCo's default (no key); a file's own numbers show as they are and stay until another is
// picked.
const STOPS = [["soft", [0.05, 1.0], () => t("Soft: gives a little")], ["normal", null, () => t("Normal")],
  ["stiff", [0.004, 1.0], () => t("Stiff: holds a loaded joint on its stop")]];
function stopsRow(b, j) {
  const now = j.limit_solref || null;
  const same = (a, z) => (a === null || z === null ? a === z : a[0] === z[0] && a[1] === z[1]);
  const known = STOPS.find(([, v]) => same(v, now));
  const sel = h("select", { id: "part-stops" },
    ...STOPS.map(([k, , words]) => h("option", { value: k }, words())),
    known ? null : h("option", { value: "own" }, t("As set: {seconds} s, damping ratio {ratio}", { seconds: now[0], ratio: now[1] })));
  sel.value = known ? known[0] : "own";
  sel.addEventListener("change", () => {
    if (!live(sel) || sel.value === "own") return;
    const v = STOPS.find(([k]) => k === sel.value)[1];
    patchJoint(b.name, { limit_solref: v ? [...v] : null });
  });
  const row = field(t("Stops"), sel, "limit_solref",
    t("How hard the ends of the range are. A light hook carrying a robot pushes through a normal stop; stiff keeps it there."));
  row.querySelector("label").htmlFor = "part-stops";
  return row;
}

addSection({
  id: "joint",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body, s = c.state;
    const rows = [h("h3", { text: t("How it moves") })];
    if (c.plugin) {
      if (!b.joint) rows.push(h("p", { class: "part-line", text: b.parent ? t("Fixed to {parent}.", { parent: b.parent }) : t("The base of the part: the other bodies move on it.") }));
      else {
        const on = { parent: b.parent };
        rows.push(h("p", { class: "part-line" }, (b.joint.kind === "slide" ? t("It slides on {parent}.", on) : t("It turns on {parent}.", on)) + " ",
          h("span", { class: "muted", text: t("Its axis comes from the CAD plugin.") })));
        rows.push(jointMore(c, b));
      }
      return h("section", { class: "insp-section" }, ...rows);
    }
    if (b.parent == null) {
      rows.push(h("p", { class: "part-line", text: t("This is the base of its part: it doesn't move, and the bodies on it move relative to it.") }));
      return h("section", { class: "insp-section" }, ...rows);
    }
    const kind = h("select", { id: "part-joint-kind" },
      h("option", { value: "" }, t("No (welded)")), h("option", { value: "hinge" }, t("Turns")), h("option", { value: "slide" }, t("Slides")));
    const draft = draftKind(b.name);
    kind.value = draft || (b.joint ? b.joint.kind : "");
    kind.addEventListener("change", () => {
      if (!live(kind)) return;
      if (!kind.value) {
        jointDraft = null;
        if (view3d.cancelPick) view3d.cancelPick();
        if (b.joint) editOp("set_joint", { body: b.name, parent: b.parent, joint: null }); else render();
        return;
      }
      jointDraft = b.joint && b.joint.kind === kind.value ? null : { body: b.name, kind: kind.value };
      render();
      pickAxis(b.name);
    });
    rows.push(field(t("Moves"), kind, "joint"));
    const below = descendants(b.name);
    const parent = h("select", { id: "part-joint-parent" },
      ...s.bodies.filter((x) => x.name !== b.name && !below.has(x.name)).map((x) => h("option", { value: x.name }, x.name)));
    parent.value = b.parent;
    parent.addEventListener("change", () => {
      if (!live(parent)) return;
      editOp("set_joint", { body: b.name, parent: parent.value, joint: b.joint ? jointSpec(b.joint) : null });
    });
    rows.push(field(t("relative to"), parent, "parent", b.joint || draft ? null : t("Welded: it moves only when that body moves.")));
    const axisButtons = () => buttons(t("The axis"),
      h("button", { type: "button", class: "btn btn-small", id: "part-pick-axis",
        "aria-pressed": String(!!(view3d.picking && view3d.picking()) && view3d.pickOwner() === selSig()),
        onclick: () => pickAxis(b.name) }, t("Pick the axis")),
      h("span", { class: "muted part-snap-label", text: t("Snap to") }),
      ...["x", "y", "z"].map((a, i) => h("button", { type: "button", class: "btn btn-small", id: `part-snap-${a}`,
        title: t("Along the robot's {axis}", { axis: AXIS_WORDS[i]() }), onclick: () => snap(b.name, i) }, a.toUpperCase())));
    if (draft) {
      rows.push(h("p", { class: "hint part-draft", id: "part-joint-draft", role: "status",
        text: draft === "hinge" ? t("Not saved yet: pick the axis (a hole or a round face), or snap it to X, Y or Z. Esc cancels.")
          : t("Not saved yet: pick the axis (a flat face that faces the way it slides), or snap it to X, Y or Z. Esc cancels.") }),
      axisButtons());
    } else if (b.joint) {
      const j = b.joint;
      rows.push(h("p", { class: "part-line" }, t("Axis") + " ", h("strong", { text: axisWords(j.axis) }), h("br"),
        h("span", { class: "muted", text: t("through {point}", { point: pointWords(cadToRobot(rt(), j.anchor)) }) })));
      rows.push(axisButtons());
      rows.push(h("p", { class: "hint", text: j.kind === "hinge" ? t("Click Pick the axis, then a hole or a round face (a bearing, a shaft).") : t("Click Pick the axis, then a flat face that faces the way it slides.") }));
      if (j.kind === "hinge") rows.push(pivotRow(b));
      rows.push(tryIt(b));
      rows.push(jointMore(c, b));
    }
    return h("section", { class: "insp-section" }, ...rows);
  },
});

// The "Try it" sliders for every joint, when nothing is selected.
addSection({
  id: "tryall",
  when: (c) => !c.sel.kind && !c.plugin && c.state.bodies.some((b) => b.joint && b.joint.anchor),
  render: (c) => h("section", { class: "insp-section" }, h("h3", { text: t("Try it") }),
    ...c.state.bodies.filter((b) => b.joint && b.joint.anchor).map((b) => tryIt(b, "move"))),
}, { before: "pieces" });

// -- motor, encoder, gyro --------------------------------------------------------------------------------------
let draft = null;       // the motor being set up but not saved yet: {body, motor, count, ratio, spool}

function motorRows(c, b) {
  const j = b.joint, slide = j.kind === "slide", saved = b.motor;
  const d = draft && draft.body === b.name ? draft : null;
  const cur = d || { motor: saved ? saved.motor : "", count: saved ? saved.count : 1, ratio: saved ? saved.ratio : 1,
    spool: saved ? saved.spool_diameter : null };
  const send = async (patch) => {
    const next = { ...cur, ...patch };
    if (!next.motor) {
      draft = patch.motor === "" ? null : { body: b.name, ...next };
      if (patch.motor === "" && saved) await editOp("set_motor", { body: b.name, settings: null });
      else render();
      return;
    }
    draft = { body: b.name, ...next };
    if (slide && !next.spool) {   // a motor on a slide needs its spool: ask for it before saving
      render();
      const el = document.getElementById("part-spool");
      if (el) el.focus();
      return;
    }
    const settings = { motor: next.motor, count: next.count, ratio: next.ratio,
      invert: saved ? saved.invert : false, efficiency: saved ? saved.efficiency : 1 };
    if (next.spool) settings.spool_diameter = next.spool;
    const r = await editOp("set_motor", { body: b.name, settings });
    if (r.ok && draft && draft.body === b.name) { draft = null; render(); }
  };
  const motors = (part.materials && part.materials.motors) || [];
  const sel = h("select", { id: "part-motor" }, h("option", { value: "" }, t("None")), ...motors.map((m) => h("option", { value: m }, m)));
  sel.value = cur.motor || "";
  sel.addEventListener("change", () => { if (live(sel)) send({ motor: sel.value }); });
  const rows = [field(t("Motor"), sel, "motor")];
  if (cur.motor) {
    const count = box({ id: "part-motor-count", type: "text", inputmode: "numeric", value: String(cur.count) }, (text) => {
      const v = number(text, "count");
      if (Number.isNaN(v)) return;
      if (v === null || v < 1 || Math.round(v) !== v) { part.errors = [{ field: "count", message: t("Type a whole number of motors, such as 2.") }]; render(); return; }
      send({ count: v });
    });
    rows.push(field(t("How many"), count, "count", t("Motors geared together on this joint.")));
    const ratio = box({ id: "part-motor-ratio", type: "text", inputmode: "decimal", value: fmtNum(cur.ratio, 4) }, (text) => {
      const v = number(text, "ratio");
      if (Number.isNaN(v)) return;
      if (v === null || v <= 0) { part.errors = [{ field: "ratio", message: t("Type the gear ratio, such as 25 for 25:1.") }]; render(); return; }
      send({ ratio: v });
    });
    rows.push(field(t("Gear ratio"), h("span", { class: "with-unit" }, ratio, h("span", { class: "unit", text: ": 1" })), "ratio",
      slide ? t("Motor turns per spool turn.") : t("Motor turns per turn of the joint.")));
    rows[rows.length - 1].querySelector("label").htmlFor = "part-motor-ratio";
    if (slide) {
      // in the chosen units (Settings), stored in meters
      const example = units.name("length") === "mm" ? 0.025 : 0.0254;   // 25 mm or 1 in
      const spool = box({ id: "part-spool", type: "text", inputmode: "decimal", kind: "length",
        value: cur.spool ? shownMeasure(cur.spool, "length") : "", placeholder: units.shown(example, "length", 0) }, () => {
        const v = boxMeasure(spool, cur.spool, "spool_diameter", "length");
        if (Number.isNaN(v)) return;
        if (v === null || v <= 0) { part.errors = [{ field: "spool_diameter", message: t("Type the spool's diameter, such as {example}.", { example: units.fmt(example, "length") }) }]; render(); return; }
        send({ spool: v });
      });
      rows.push(field(t("Spool diameter"), h("span", { class: "with-unit" }, spool, h("span", { class: "unit", text: units.name("length") })), "spool_diameter",
        t("The drum the rope or belt winds on.")));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-spool";
    }
    if (d) rows.push(h("p", { class: "hint part-draft", role: "status", text: slide && !cur.spool ? t("Type the spool diameter to save the motor.") : t("Not saved yet.") }));
  }
  const enc = h("select", { id: "part-encoder" }, h("option", { value: "" }, t("None")),
    h("option", { value: "joint" }, t("On the joint")), h("option", { value: "motor" }, t("On the motor")));
  enc.value = b.encoder ? b.encoder.on : "";
  enc.addEventListener("change", () => {
    if (live(enc)) editOp("set_encoder", { body: b.name, settings: enc.value ? { on: enc.value, invert: b.encoder ? b.encoder.invert : false } : null });
  });
  rows.push(field(t("Encoder"), enc, "on", t("Where the robot code reads how far it has moved.")));
  return rows;
}

addSection({
  id: "slots",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body;
    const rows = [h("h3", { text: t("Motor and sensors") })];
    if (c.plugin) {
      const list = (xs) => (xs && xs.length ? xs.join(", ") : t("none"));
      rows.push(h("p", { class: "part-line" }, t("Motor slots:") + " ", h("span", { class: "mono", text: list(b.motor) })),
        h("p", { class: "part-line" }, t("Encoder slots:") + " ", h("span", { class: "mono", text: list(b.encoder) })),
        h("p", { class: "part-line", text: b.gyro ? t("Has a gyro.") : t("No gyro.") }),
        h("p", { class: "hint", text: t("Slots come from the CAD plugin: change them there.") }));
      return h("section", { class: "insp-section" }, ...rows);
    }
    if (b.joint) rows.push(...motorRows(c, b));
    else rows.push(h("p", { class: "hint", text: t("A motor and an encoder need a joint: make it turn or slide first.") }));
    const gyro = h("input", { type: "checkbox", id: "part-gyro" });
    gyro.checked = !!b.gyro;
    gyro.addEventListener("change", () => { if (live(gyro)) editOp("set_gyro", { body: b.name, on: gyro.checked }); });
    rows.push(h("div", { class: "field part-own", dataset: { field: "gyro" } },
      h("label", { class: "part-check-label", for: "part-gyro" }, gyro, " " + t("Has a gyro")),
      h("p", { class: "hint", text: t("It measures how this body turns, like a navX or a Pigeon.") })));
    return h("section", { class: "insp-section" }, ...rows);
  },
});
onPart(() => { if (draft && !(part.sel.kind === "body" && part.sel.keys.has(draft.body))) draft = null; });

