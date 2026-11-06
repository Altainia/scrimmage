// The Part tab's inspector sections (registered with part.js's addSection, shown in this order when their
// `when(ctx)` holds) and the "Where's the robot?" step over the 3D view. part.js is the store, the edits and the tree.
//
//   summary      nothing selected: the part's weight and center of gravity, and a "Try it" slider per joint
//   pieces       the selected pieces: their weight and their body
//   material     what the selected pieces (or the selected body's pieces) are made of, or a typed weight
//   body         one body: its name, weight, center of gravity, and whether it is its own part
//   joint        one body: "Moves: no (welded) / turns / slides" relative to its parent, the axis (picked in the
//                3D view, or snapped to x, y or z; a new turn or slide is saved only then), "More" (range, spring,
//                damping, friction) and "Try it"
//   slots        one body: its motor (count, gear ratio, spool diameter for a slide, in the chosen units; stored
//                in meters), encoder (none, on the joint, on the motor) and gyro
//   mounts       one body: the spots another part bolts on ("Add mount": click a flat face, then name it)
//   collision    one body: how it touches things, with a one-click suggestion for hooks and wheels (never applied
//                by itself), and for a body that touches things its grip material (set_grip: the pads' friction, the
//                same picker as the Test tab's Setup)
//   links        one body, under "More": couplings ("turns with another joint") and loops ("pinned to another body")
//   bodies       several bodies: one button each
//   frame        the robot frame: which way is up and forward, and a button that shows the step again
//
// Plugin mode (a part folder from the FreeCAD add-on) keeps only what its overlay can change: weight, collision and
// a joint's range, spring, damping and friction; its motors, encoders and mounts are slot names, shown as text.
//
// Picking in the 3D view (an axis, a floor, a center, a mount, a pin) goes through view3d.startPick: a hint shows
// over the view and Esc (or Cancel) stops it. "Try it" only moves the drawing (part.tryit): it never makes an edit.
// Points and axes go to the server in CAD coordinates; part_frame.js converts them from and to the robot frame.

import { h, api, notice, defaultGrip, lengthWords } from "./app.js";
import { part, view3d, addSection, editOp, select, render, live, field, box, number, matLabel, kg, cog, expectBody,
  onPart, measure, shownMeasure, boxMeasure } from "./part.js";
import { units } from "./units.js";
import { t, tn } from "./i18n.js";
import { toRobot, cadToRobot, robotToCad, dirToCad, dirToRobot, normalize, tryRange, turnFront, floorAndCenter,
  otherAxes, upIndex } from "./part_frame.js";

if (window.__scrimmagePart) window.__scrimmagePart.toRobot = toRobot;

// Nothing selected: the part as a whole.
addSection({
  id: "summary",
  when: (c) => !c.sel.kind,
  render: (c) => h("section", { class: "insp-section" },
    h("h3", { text: c.state.name || t("This part") }),
    ...c.state.parts.map((p) => h("p", { class: "part-line" },
      c.state.parts.length > 1 ? h("strong", { text: `${p.name}: ` }) : null,
      t("Weight {weight} · center of gravity {cg}", { weight: kg(p.mass), cg: cog(p.com) }))),
    h("p", { class: "hint", text: c.plugin
      ? t("Click a body in the 3D view or the list to change its weight.")
      : t("Click a piece in the 3D view or the list to say what it is made of. Shift-click adds more pieces. Drag pieces onto a body to move them.") }),
    c.state.source ? h("p", { class: "hint" }, ...rich(t("From {file}. Drop a new CAD file anywhere here to use it instead.", { file: "{file}" }),
      { file: h("code", { text: c.state.source }) })) : null),
});

// The selected pieces: their weight, their body.
addSection({
  id: "pieces",
  when: (c) => c.sel.kind === "piece" && c.pieces.length > 0,
  render: (c) => {
    const ps = c.pieces;
    const mass = ps.reduce((a, p) => a + (p.left_out ? 0 : p.mass || 0), 0);
    const bodies = [...new Set(ps.map((p) => p.body))];
    const move = h("select", { id: "part-move" },
      bodies.length > 1 ? h("option", { value: "", disabled: true }, t("Different bodies")) : null,
      ...c.state.bodies.map((b) => h("option", { value: b.name }, b.name)));
    move.value = bodies.length > 1 ? "" : bodies[0];
    move.addEventListener("change", () => { if (live(move) && move.value) editOp("move_pieces", { pieces: ps.map((p) => p.key), body: move.value }); });
    const out = ps.filter((p) => p.left_out).length;
    return h("section", { class: "insp-section insp-head" },
      h("h3", { text: ps.length === 1 ? ps[0].name : tn("{count} piece", "{count} pieces", ps.length) }),
      ps.length === 1 ? h("p", { class: "hint mono", text: ps[0].key }) : null,
      h("p", { class: "part-line" }, t("Weight") + " ",
        h("strong", { id: "part-sel-mass", dataset: { kg: String(mass) }, text: kg(mass) }),
        out ? h("span", { class: "muted", text: " " + (out === ps.length ? t("(left out)") : t("({count} left out)", { count: out })) }) : null),
      field(t("In body"), move, "body", t("Pieces in one body move together. You can also drag them in the list.")));
  },
});

// What the selected pieces (or the selected body's pieces) are made of, and "or type a weight".
addSection({
  id: "material",
  when: (c) => !c.plugin && ((c.sel.kind === "piece" && c.pieces.length > 0)
    || (c.body && c.state.pieces.some((p) => p.body === c.body.name))),
  render: (c) => {
    const ps = c.sel.kind === "piece" ? c.pieces : c.state.pieces.filter((p) => p.body === c.body.name);
    const keys = ps.map((p) => p.key);
    const mats = new Set(ps.map((p) => p.material));
    const one = mats.size === 1 ? [...mats][0] : undefined;
    const choices = part.materials ? Object.keys(part.materials.materials) : [];
    const sel = h("select", { id: "part-material" },
      one === undefined ? h("option", { value: "-", disabled: true }, t("Different materials")) : null,
      one === null ? h("option", { value: "", disabled: true }, t("Not chosen yet (counted as aluminum)")) : null,
      ...choices.map((k) => h("option", { value: k }, matLabel(k))),
      h("option", { value: "custom" }, matLabel("custom")));
    sel.value = one === undefined ? "-" : one === null ? "" : one;
    const printed = one && part.materials && part.materials.materials[one] && part.materials.materials[one].printed;
    const infillOf = ps[0] && ps[0].infill;
    const send = (material, extra = {}) => editOp("set_material", { pieces: keys, material, ...extra });
    sel.addEventListener("change", () => {
      if (!live(sel)) return;
      const m = sel.value;
      if (m === "custom") {   // ask for the density first; the edit goes when it is typed
        customAsked = true;
        render();
        const d = document.getElementById("part-density");
        if (d) d.focus();
        return;
      }
      customAsked = false;
      const pm = part.materials.materials[m];
      send(m, pm && pm.printed ? { infill: one === m && infillOf != null ? infillOf : null } : {});
    });
    const rows = [field(t("Material"), sel, "material", ps.length > 1 ? t("Sets all {count} pieces.", { count: ps.length }) : null)];
    if (printed) {
      const fill = box({ id: "part-infill", type: "text", inputmode: "decimal",
        value: infillOf == null ? "" : String(+(infillOf * 100).toFixed(1)), placeholder: "40" }, (text) => {
        const v = number(text, "infill");
        if (!Number.isNaN(v)) send(one, { infill: v === null ? null : v / 100 });
      });
      rows.push(field(t("Infill (percent)"), h("span", { class: "with-unit" }, fill, h("span", { class: "unit", text: "%" })), "infill",
        t("How full the slicer fills the inside. Empty means 40%.")));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-infill";
    }
    if (one === "custom" || (customAsked && sel.value !== "-")) {
      if (one !== "custom") sel.value = "custom";
      const dens = box({ id: "part-density", type: "text", inputmode: "decimal", kind: "density",
        value: one === "custom" ? shownMeasure(ps[0].custom_density, "density") : "", placeholder: units.shown(2700, "density", 0) }, (text) => {
        const v = measure(text, "custom_density", "density");
        if (!Number.isNaN(v)) send("custom", { custom_density: v });
      });
      const d = (x) => units.shown(x, "density", 0);
      rows.push(field(t("Density"), h("span", { class: "with-unit" }, dens, h("span", { class: "unit", text: units.name("density") })), "custom_density",
        t("Aluminum is {aluminum}, steel {steel}, polycarbonate {polycarbonate}.", { aluminum: d(2700), steel: d(7850), polycarbonate: d(1200) })));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-density";
    }
    if (c.sel.kind === "piece") {
      const typed = new Set(ps.map((p) => p.typed_mass));
      const typedKg = typed.size === 1 ? [...typed][0] : null;
      const w = box({ id: "part-weight", type: "text", inputmode: "decimal", kind: "mass",
        value: shownMeasure(typedKg, "mass"), placeholder: ps.length === 1 ? placeholderMass(ps[0].mass) : "" }, async (text) => {
        const v = measure(text, "kg", "mass");
        if (Number.isNaN(v)) return;
        for (const k of keys) {   // one edit per piece: set_mass names one piece
          const r = await editOp("set_mass", { piece: k, kg: v });
          if (!r.ok) break;
        }
      });
      rows.push(field(ps.length > 1 ? t("or type a weight (each piece)") : t("or type a weight"),
        h("span", { class: "with-unit" }, w, h("span", { class: "unit", text: units.name("mass") })), "kg",
        t("A typed weight wins over the material. Empty it to use the material again.")));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-weight";
    }
    return h("section", { class: "insp-section" }, h("h3", { text: c.sel.kind === "body" ? t("What its pieces are made of") : t("What it is made of") }), ...rows);
  },
});
let customAsked = false;
onPart(() => { if (part.sel.kind !== "piece" && part.sel.kind !== "body") customAsked = false; });

// One body: its name, weight, center of gravity and whether it is its own part.
addSection({
  id: "body",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body, s = c.state;
    const rows = [];
    if (c.plugin) rows.push(h("h3", { text: b.name }));
    else {
      rows.push(h("h3", { text: t("Body") }));
      const name = box({ id: "part-body-name", type: "text", value: b.name, spellcheck: "false" }, (text) => {
        if (!text) { part.errors = [{ field: "new", message: t("A body needs a name: type one.") }]; render(); return; }
        expectBody(text);
        editOp("rename_body", { old: b.name, new: text });
      });
      rows.push(field(t("Name"), name, "new"));
    }
    rows.push(h("p", { class: "part-line" }, t("Weight") + " ", h("strong", { id: "part-body-mass", dataset: { kg: String(b.mass ?? "") }, text: kg(b.mass) })));
    rows.push(h("p", { class: "part-line" }, t("Center of gravity") + " ", h("span", { class: "mono", text: cog(b.com) })));
    if (b.typed_mass !== undefined || c.plugin) {
      const w = box({ id: "part-body-weight", type: "text", inputmode: "decimal", kind: "mass",
        value: shownMeasure(b.typed_mass, "mass"), placeholder: placeholderMass(b.mass) }, (text) => {
        const v = measure(text, "kg", "mass");
        if (!Number.isNaN(v)) editOp("set_mass", { body: b.name, kg: v });
      });
      rows.push(origin(c, b, ["mass"], field(t("or type its weight"), h("span", { class: "with-unit" }, w, h("span", { class: "unit", text: units.name("mass") })), "kg",
        c.plugin ? t("A typed weight wins over the one the CAD plugin wrote.") : t("A typed weight wins over its pieces' materials. Empty it to use them again."))));
      rows[rows.length - 1].querySelector("label").htmlFor = "part-body-weight";
    }
    if (!c.plugin) {
      const own = b.parent == null;
      const roots = s.bodies.filter((x) => x.parent == null);
      const check = h("input", { type: "checkbox", id: "part-own" });
      check.checked = own && askParent !== b.name;
      const only = own && roots.length < 2;
      if (only) check.disabled = true;
      check.addEventListener("change", () => {
        if (!live(check)) return;
        if (check.checked) { askParent = null; editOp("set_part", { body: b.name, part: b.name }); }
        else { askParent = b.name; render(); const p = document.getElementById("part-parent"); if (p) p.focus(); }
      });
      const ownRow = h("div", { class: "field part-own", dataset: { field: "part" } },
        h("label", { class: "part-check-label", for: "part-own" }, check, " " + t("Its own part")),
        h("p", { class: "hint", text: only ? t("Every import makes at least one part: this is it.")
          : own ? t("It is placed on the robot by itself, and the bodies on it go with it.") : t("Tick it to place this body on the robot by itself.") }),
        ...part.errors.filter((e) => e.field === "part").map((e) => h("p", { class: "error field-msg", role: "alert", text: e.message })));
      rows.push(ownRow);
      if (own && askParent !== b.name) {
        const pn = box({ id: "part-part-name", type: "text", value: b.part || b.name, spellcheck: "false" }, (text) => {
          if (text) editOp("set_part", { body: b.name, part: text });
        });
        rows.push(field(t("Part name"), pn, "part-name", t("The name of its folder in parts/.")));
      }
      if (askParent === b.name) {
        const below = descendants(b.name);
        const choices = s.bodies.filter((x) => x.name !== b.name && !below.has(x.name));
        const ps = h("select", { id: "part-parent" }, h("option", { value: "", disabled: true }, t("Pick a body")),
          ...choices.map((x) => h("option", { value: x.name }, x.name)));
        ps.value = "";
        ps.addEventListener("change", async () => {
          if (!live(ps) || !ps.value) return;
          const r = await editOp("set_part", { body: b.name, part: null, parent: ps.value });
          if (r.ok) { askParent = null; render(); }
        });
        rows.push(field(t("It hangs on"), ps, "parent", t("It stops being a part and moves with that body.")));
      }
    }
    return h("section", { class: "insp-section insp-head" }, ...rows);
  },
});
let askParent = null;   // the body whose "Its own part" was just unticked: which body does it hang on?

function descendants(name) {
  const out = new Set();
  let grew = true;
  while (grew) {
    grew = false;
    for (const b of part.state.bodies) if (b.parent && (b.parent === name || out.has(b.parent)) && !out.has(b.name)) { out.add(b.name); grew = true; }
  }
  return out;
}

// Several bodies (a checklist item names them): one button each.
addSection({
  id: "bodies",
  when: (c) => c.sel.kind === "body" && c.bodies.length > 1,
  render: (c) => h("section", { class: "insp-section" },
    h("h3", { text: tn("{count} body", "{count} bodies", c.bodies.length) }),
    h("ul", { class: "part-body-list" }, ...c.bodies.map((b) => h("li", {},
      h("button", { type: "button", class: "linklike", onclick: () => select("body", [b.name]) }, b.name),
      h("span", { class: "muted", text: ` · ${kg(b.mass)}` })))),
    h("p", { class: "hint", text: t("Pick one to change it.") })),
});


// -- helpers -------------------------------------------------------------------------------------------------
const bodyOf = (name) => part.state.bodies.find((b) => b.name === name) || null;
const rt = () => toRobot(part.state.frame);
const sentence = (m) => { const text = String(m || "").trim(); return text ? text[0].toUpperCase() + text.slice(1) : text; };
const why = (data) => sentence((data.errors && data.errors[0] && data.errors[0].message) || data.error || t("That didn't work"));
// A translated sentence with elements in it (a <code>, a <strong>): t() is given each of those placeholders as itself,
// and here the elements go in their places, wherever the translation puts them
const rich = (text, nodes) => text.split(/(\{[A-Za-z_]\w*\})/).map((bit) => nodes[bit.slice(1, -1)] ?? bit);
const fmtNum = (x, digits = 3) => (typeof x === "number" && isFinite(x) ? String(+x.toFixed(digits)) : "");
// A weight box's placeholder: the weight it has now, to three significant digits in the chosen units
const placeholderMass = (m) => (typeof m === "number" && isFinite(m) ? String(+units.toDisplay(m, "mass").toPrecision(3)) : "");
// Plugin mode: say on a field whether its value was changed here (in scrimmage-edits.toml) or comes from the CAD
// plugin (the state's body.changed lists the overlay's keys), as data-from="here" | "plugin" and a line.
function origin(c, b, keys, row) {
  if (!c.plugin) return row;
  const here = keys.some((k) => (b.changed || []).includes(k));
  row.dataset.from = here ? "here" : "plugin";
  row.append(h("p", { class: "hint part-origin", text: here ? t("Changed here. Empty it to go back to what the CAD plugin wrote.")
    : t("From the CAD plugin.") }));
  return row;
}
const selSig = () => `${part.sel.kind}:${[...part.sel.keys].join("\n")}`;
const AXIS_WORDS = [() => t("x (forward)"), () => t("y (left)"), () => t("z (up)")];

// The joint as set_joint takes it (`patch` replaces fields; null fields are left out).
function jointSpec(j, patch = {}) {
  const out = {};
  for (const k of ["kind", "anchor", "axis", "range", "spring", "damping", "frictionloss"]) {
    const v = k in patch ? patch[k] : j[k];
    if (v !== null && v !== undefined) out[k] = v;
  }
  return out;
}

// set_joint for body `name`, built when the edit runs (from the state then), with `patch` on its joint.
function patchJoint(name, patch) {
  return editOp("set_joint", () => {
    const b = bodyOf(name);
    if (!b || !b.joint) return { body: name, parent: b ? b.parent : null, joint: null };
    if (part.state.mode === "plugin") return { body: name, joint: patch };
    return { body: name, parent: b.parent, joint: jointSpec(b.joint, patch) };
  });
}

// "along z (up)" for an axis along a robot axis, else its three numbers.
function axisWords(axisCad) {
  const a = normalize(dirToRobot(rt(), axisCad));
  const i = a.findIndex((x) => Math.abs(x) > 0.999);
  if (i >= 0) return t("along {axis}", { axis: `${a[i] < 0 ? "−" : ""}${AXIS_WORDS[i]()}` });
  return t("along ({numbers})", { numbers: a.map((x) => x.toFixed(2)).join(", ") });
}
const pointWords = (p) => cog(p);

function needView() {
  if (view3d.startPick) return true;
  notice(t("Picking needs the 3D view, which isn't working here."), "warn");
  return false;
}

// A small row of buttons.
const buttons = (label, ...btns) => h("div", { class: "row part-btns", role: "group", "aria-label": label }, ...btns);
const btn = (id, text, onclick, title = null) => h("button", { type: "button", class: "btn btn-small", id, title, onclick,
  disabled: !!(part.state && part.state.read_only_reason) }, text);

// -- the "Where's the robot?" step ------------------------------------------------------------------------------
// Shown over the 3D view while the frame isn't confirmed: a frame edit leaves it unconfirmed on the server, and only
// "Looks right" (or "This isn't a whole robot") sends confirmed = true, so the step and its checklist item stay after a
// reload too. The frame section's "Check up and front" shows it again for a confirmed frame.
let reopened = false;

function frameEdit(args) { return editOp("set_frame", args); }

// The CAD box of the drawn pieces (the view's robot-frame box mapped back through the current frame).
function cadBox() {
  const box = view3d.solidBox && view3d.solidBox();
  if (!box) return null;
  const r = rt(), lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const x of [box.min[0], box.max[0]]) for (const y of [box.min[1], box.max[1]]) for (const z of [box.min[2], box.max[2]]) {
    const c = robotToCad(r, [x, y, z]);
    for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], c[i]); hi[i] = Math.max(hi[i], c[i]); }
  }
  return { lo, hi };
}

function setUp(up) {
  const f = part.state.frame;
  let front = f.front;
  if (front[1] === up[1]) front = up[1] === "x" ? "+y" : "+x";
  const args = { up, front, skip: false };
  const b = cadBox();
  if (b) Object.assign(args, floorAndCenter(up, b.lo, b.hi));
  return frameEdit(args);
}

function pickFloor() {
  if (!needView()) return;
  view3d.startPick({
    hint: t("Click the floor: a flat face the robot stands on, such as the bottom of a wheel or the frame."),
    onPick: async (hit) => {
      const { status, data } = await api("POST", "/api/part/axis", { piece: hit.key, triangle: hit.triangle, kind: "flat" });
      if (status !== 200) return { again: why(data) };
      view3d.cancelPick();
      const f = part.state.frame;
      await frameEdit({ floor: data.point[upIndex(f.up)], skip: false });
    },
  });
}

function pickCenter() {
  if (!needView()) return;
  view3d.startPick({
    hint: t("Click the middle of the robot's frame, seen from above."),
    onPick: async (hit) => {
      view3d.cancelPick();
      const f = part.state.frame, c = robotToCad(rt(), hit.rest);
      const [i, j] = otherAxes(f.up);
      await frameEdit({ center: [c[i], c[j]], skip: false });
    },
  });
}

function renderFrameStep() {
  const el = document.getElementById("part-frame-step");
  if (!el || !part.state) return;
  const s = part.state, f = s.frame;
  const show = !!f && s.mode === "import" && !s.read_only_reason && (!f.confirmed || reopened);
  part.frameStep = show;
  el.hidden = !show;
  if (!show) { el.replaceChildren(); return; }
  const sig = JSON.stringify([f, document.documentElement.lang]);
  if (el.contains(document.activeElement) && el.dataset.sig === sig) return;   // keep focus while tabbing
  el.dataset.sig = sig;
  el.replaceChildren(
    h("p", { class: "part-step-text" },
      h("strong", { text: t("Where's the robot?") + " " }),
      f.skip ? t("The CAD file's own axes are used as they are.") :
        t("The grid is the floor and the red arrow points to the robot's front. Up is {up} and front is {front} in the CAD file.", { up: AXIS_TEXT[f.up], front: AXIS_TEXT[f.front] })),
    buttons(t("Which way is up"),
      btn("part-frame-yup", t("Y is up"), () => setUp("+y"), t("Inventor saves with Y up")),
      btn("part-frame-zup", t("Z is up"), () => setUp("+z"), t("Most CAD programs save with Z up")),
      btn("part-frame-turn", t("Turn front 90°"), () => frameEdit({ front: turnFront(f), skip: false }), t("Turn the front a quarter turn, seen from above")),
      btn("part-frame-floor", t("Click the floor"), pickFloor, t("Click a flat face the robot stands on")),
      btn("part-frame-center", t("Click the frame's center"), pickCenter, t("Click the middle of the robot, seen from above"))),
    buttons(t("Done"),
      h("button", { type: "button", class: "btn btn-small btn-primary", id: "part-frame-ok",
        onclick: async () => { const r = await frameEdit({ confirmed: true }); if (r.ok) { reopened = false; view3d.cancelPick && view3d.cancelPick(); render(); } } }, t("Looks right")),
      h("button", { type: "button", class: "btn btn-small", id: "part-frame-skip", title: t("Use the CAD file's own axes (for a mechanism, not a whole robot)"),
        onclick: async () => { const r = await frameEdit({ skip: true, confirmed: true }); if (r.ok) { reopened = false; render(); } } }, t("This isn't a whole robot"))));
}
const AXIS_TEXT = { "+x": "+x", "-x": "−x", "+y": "+y", "-y": "−y", "+z": "+z", "-z": "−z" };
onPart(renderFrameStep);

// The robot frame (which way is up and forward).
addSection({
  id: "frame",
  when: (c) => c.sel.kind === "frame",
  render: (c) => {
    const f = c.state.frame;
    if (!f) return h("section", { class: "insp-section" }, h("p", { class: "hint", text: t("A plugin-made part keeps the frame its CAD plugin wrote.") }));
    return h("section", { class: "insp-section" },
      h("h3", { text: t("Robot frame") }),
      f.skip ? h("p", { class: "part-line", text: t("The CAD file's own axes are used (this isn't a whole robot).") })
        : h("p", { class: "part-line" }, ...rich(t("Up is {up} and forward is {front} in the CAD file.", { up: "{up}", front: "{front}" }),
          { up: h("strong", { text: AXIS_TEXT[f.up] }), front: h("strong", { text: AXIS_TEXT[f.front] }) })),
      h("p", { class: "hint", text: f.confirmed ? t("Checked.") : t("This is a guess: most CAD programs use +z up, but Inventor uses +y up.") }),
      c.ro || c.plugin ? null : buttons(t("Robot frame"),
        btn("part-frame-check", t("Check up and front"), () => { reopened = true; render(); const b = document.getElementById("part-frame-yup"); if (b) b.focus(); }),
        f.skip ? btn("part-frame-unskip", t("It is a whole robot"), () => frameEdit({ skip: false })) : null));
  },
});

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
    const d = kept && kept.sys !== units.system
      ? Object.assign(kept.map((x, i) => units.convert(x, kinds[i], kept.sys)), { sys: units.system }) : kept;
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
    ([a, z]) => ((a === null) !== (z === null) ? null : patchJoint(b.name, { range: a === null ? null : [a, z] })),
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
  return details("joint-more", t("More: range, spring, damping, friction"), rows,
    part.errors.some((e) => ["range", "spring", "damping", "frictionloss"].includes(e.field)));
}

// A <details> that stays open across redraws.
const opened = new Set();
function details(key, title, rows, forceOpen = false) {
  const now = document.getElementById(`part-${key}`);   // the one on the page now (its toggle event may not have run)
  if (now) { if (now.open) opened.add(key); else opened.delete(key); }
  const d = h("details", { class: "part-more", id: `part-${key}`, open: opened.has(key) || forceOpen });
  d.addEventListener("toggle", () => { if (d.open) opened.add(key); else opened.delete(key); });
  d.append(h("summary", { text: title }), ...rows);
  return d;
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

// -- mounts ------------------------------------------------------------------------------------------------------
part.pendingMount = null;   // {body, point, normal} picked, waiting for a name (drawn by part3d.js)

function addMount(name) {
  if (!needView()) return;
  view3d.startPick({
    owner: selSig(),
    hint: t("Click a flat face of {body} where another part bolts on.", { body: name }),
    onPick: async (hit) => {
      const { status, data } = await api("POST", "/api/part/axis", { piece: hit.key, triangle: hit.triangle, kind: "flat" });
      if (status !== 200) return { again: why(data) };
      view3d.cancelPick();
      part.pendingMount = { body: name, point: data.point, normal: data.axis };
      render();
      const el = document.getElementById("part-mount-name");
      if (el) { el.focus(); el.select(); }
    },
  });
}

function freeMountName(b) {
  const taken = new Set(part.state.bodies.flatMap((x) => x.mounts.map((m) => m.name)));
  let n = 1, name = `${b.name}_mount`;
  while (taken.has(name)) name = `${b.name}_mount_${++n}`;
  return name;
}

addSection({
  id: "mounts",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body;
    const rows = [h("h3", { text: t("Mounts") })];
    if (c.plugin) {
      rows.push(h("p", { class: "part-line", text: b.mounts.length ? b.mounts.join(", ") : t("None.") }));
      return h("section", { class: "insp-section" }, ...rows);
    }
    if (b.mounts.length) {
      rows.push(h("ul", { class: "part-body-list" }, ...b.mounts.map((m) => h("li", {},
        h("span", { class: "mono", text: m.name }), " ",
        h("button", { type: "button", class: "linklike", "aria-label": t("Remove mount {name}", { name: m.name }),
          onclick: () => editOp("remove_mount", { body: b.name, name: m.name }) }, t("remove"))))));
    }
    const pm = part.pendingMount && part.pendingMount.body === b.name ? part.pendingMount : null;
    if (pm) {
      const save = async () => {
        const name = nameBox.value.trim();
        if (!name) { part.errors = [{ field: "name", message: t("A mount needs a name: type one, such as top.") }]; render(); return; }
        const r = await editOp("add_mount", { body: b.name, name, point: pm.point, normal: pm.normal });
        if (r.ok) { part.pendingMount = null; render(); }
      };
      const nameBox = h("input", { type: "text", id: "part-mount-name", autocomplete: "off", spellcheck: "false", value: freeMountName(b) });
      nameBox.dataset.orig = nameBox.value;
      nameBox.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); save(); }
        if (e.key === "Escape") { e.preventDefault(); part.pendingMount = null; render(); }
      });
      rows.push(field(t("Name the new mount"), nameBox, "name", t("Other parts attach to it by this name.")),
        buttons(t("New mount"), btn("part-mount-save", t("Save mount"), save),
          btn("part-mount-cancel", t("Cancel"), () => { part.pendingMount = null; render(); })));
    } else {
      rows.push(buttons(t("Mounts"), btn("part-add-mount", t("Add mount"), () => addMount(b.name))),
        h("p", { class: "hint", text: t("A mount is a spot where another part, or the robot, bolts on.") }));
    }
    return h("section", { class: "insp-section" }, ...rows);
  },
});
onPart(() => {
  if (part.pendingMount && !(part.sel.kind === "body" && part.sel.keys.has(part.pendingMount.body))) {
    part.pendingMount = null;
    if (view3d.pose) view3d.pose();
  }
});

// -- collision ---------------------------------------------------------------------------------------------------
const COLLISION = [["none", () => t("Only shows (doesn't touch anything)")], ["wheel", () => t("A wheel")],
  ["shaped", () => t("Touches things, as shaped")], ["simple", () => t("Touches things, as a simple box")]];
// [name pattern, the question, the mode it suggests, the button]
const LOOKS = [[/hook|latch|claw/i, () => t("This looks like a hook: make it touch things as shaped?"), "shaped", () => t("Yes, as shaped")],
  [/wheel|tread|roller/i, () => t("This looks like a wheel: make it touch things as a wheel?"), "wheel", () => t("Yes, as a wheel")]];

addSection({
  id: "collision",
  when: (c) => !!c.body,
  render: (c) => {
    const b = c.body;
    const sel = h("select", { id: "part-collision" },
      c.plugin && !b.collision ? h("option", { value: "" }, t("As the CAD plugin made it")) : null,
      ...COLLISION.map(([v, words]) => h("option", { value: v }, words())));
    sel.value = b.collision || "";
    sel.addEventListener("change", () => { if (live(sel) && sel.value) editOp("set_collision", { body: b.name, mode: sel.value }); });
    const rows = [h("h3", { text: t("Touching things") }), field(t("How it touches things"), sel, "mode",
      t("Hooks and claws that grab need to touch things. A wheel needs a joint that turns."))];
    if (c.plugin && b.collision) {
      rows[1].dataset.from = "here";
      rows[1].append(h("p", { class: "hint part-origin", text: t("Changed here.") }));
    }
    const look = LOOKS.find(([re]) => re.test(b.name));
    const turns = !!b.joint && b.joint.kind === "hinge";
    if (look && (look[2] !== "wheel" || turns) && (b.collision || "none") === "none" && !c.ro) {
      rows.push(h("p", { class: "part-suggest" }, look[1]() + " ",
        h("button", { type: "button", class: "btn btn-small", id: "part-collision-suggest",
          onclick: () => editOp("set_collision", { body: b.name, mode: look[2] }) }, look[3]())));
    }
    if (touches(c, b)) rows.push(...gripRows(c, b));
    return h("section", { class: "insp-section" }, ...rows);
  },
});

// A body touches things when it is set to (plugin mode: unless set to only show; the plugin's own may touch). A
// wheel's tread keeps its own friction, so a wheel has no grip.
const touches = (c, b) => (c.plugin ? (b.collision || "plugin") : b.collision || "none") !== "none" && b.collision !== "wheel";
let gripOwnAsked = null;    // the body whose Grip picker shows "My own number…" before a number is typed
onPart(() => { if (part.sel.kind !== "body") gripOwnAsked = null; });

// The Grip row: the materials with their friction estimates (ruling P27: two with the same estimate read the same),
// and my own number, a measured friction. Import mode writes the built folder, so it waits while a build runs.
function gripRows(c, b) {
  const mats = (part.materials && part.materials.grips) || [];
  const own = typeof b.grip === "number" || gripOwnAsked === b.name;
  const building = !c.plugin && c.state.build && c.state.build.state === "building";
  const sel = h("select", { id: "part-grip" },
    h("option", { value: "" }, defaultGrip()),
    ...mats.map(([k, label, mu]) => h("option", { value: k }, t("{material} — {friction}, estimate", { material: label, friction: +mu.toPrecision(6) }))),
    h("option", { value: "own" }, t("My own number…")));
  sel.value = own ? "own" : b.grip == null ? "" : b.grip;
  sel.addEventListener("change", () => {
    if (!live(sel)) return;
    if (sel.value === "own") {    // ask for the number first; the edit goes when it is typed
      gripOwnAsked = b.name;
      render();
      const el = document.getElementById("part-grip-own");
      if (el) el.focus();
      return;
    }
    gripOwnAsked = null;
    editOp("set_grip", { body: b.name, grip: sel.value || null });
  });
  if (building) sel.disabled = true;
  const rows = [field(t("Grip"), sel, "grip", building ? t("Wait for the parts to update, then pick it.")
    : t("What its pads are made of, for gripping a game piece. Friction values are estimates (UNCALIBRATED); docs/test-tab.md says how to measure yours."))];
  if (own) {
    const v = box({ id: "part-grip-own", type: "text", inputmode: "decimal", placeholder: "0.9",
      value: typeof b.grip === "number" ? String(b.grip) : "" }, async (text) => {
      const x = number(text, "grip");
      if (Number.isNaN(x) || x === null) return;
      const r = await editOp("set_grip", { body: b.name, grip: x });
      if (r && r.ok && gripOwnAsked === b.name) { gripOwnAsked = null; render(); }     // the number shows it now
    });
    if (building) v.disabled = true;
    rows.push(field(t("My own friction"), v, "grip_own", t("A friction you measured with a sled pull, such as 0.9.")));
  }
  return rows;
}

// -- couplings and loops ("More") --------------------------------------------------------------------------------
let loopWith = null;   // the body chosen for a new loop (kept across redraws)
const linkDraft = { with: null, ratio: "1" };   // the new coupling's other joint and ratio, kept across redraws

function pickPin(name, other) {
  if (!needView()) return;
  loopWith = other;
  view3d.startPick({
    owner: selSig(),
    hint: t("Click where {body} and {other} are pinned together.", { body: name, other }),
    onPick: async (hit) => {
      view3d.cancelPick();
      await editOp("add_loop", { body1: name, body2: other, point: robotToCad(rt(), hit.rest) });
    },
  });
}

addSection({
  id: "links",
  when: (c) => !!c.body && !c.plugin,
  render: (c) => {
    const b = c.body, s = c.state;
    const rows = [];
    // couplings: this joint turns (or slides) with another one
    const couples = (s.couplings || []).filter((x) => x.a === b.name || x.b === b.name);
    rows.push(h("h4", { text: t("Turns with another joint") }));
    if (couples.length) rows.push(h("ul", { class: "part-body-list" }, ...couples.map((x) => h("li", {},
      t("{a} moves {ratio} × {b}", { a: x.a, ratio: fmtNum(x.ratio), b: x.b }) + " ",
      h("button", { type: "button", class: "linklike", "aria-label": t("Remove the coupling of {a} and {b}", { a: x.a, b: x.b }),
        onclick: () => editOp("remove_coupling", { a: x.a, b: x.b }) }, t("remove"))))));
    const others = s.bodies.filter((x) => x.name !== b.name && x.joint);
    if (!b.joint) rows.push(h("p", { class: "hint", text: t("Give it a joint first: a coupling links two joints, like gears or a belt.") }));
    else if (!others.length) rows.push(h("p", { class: "hint", text: t("No other body has a joint yet.") }));
    else {
      const w = h("select", { id: "part-couple-with" }, ...others.map((x) => h("option", { value: x.name }, x.name)));
      if (linkDraft.with && others.some((x) => x.name === linkDraft.with)) w.value = linkDraft.with;
      w.addEventListener("change", () => { linkDraft.with = w.value; });
      const ratio = h("input", { type: "text", id: "part-couple-ratio", inputmode: "decimal", value: linkDraft.ratio, autocomplete: "off" });
      ratio.addEventListener("input", () => { linkDraft.ratio = ratio.value; });
      rows.push(field(t("Moves with"), w, "b"),
        field(t("times"), ratio, "ratio", t("1 means as much as that joint; −1 the other way; 2 twice as far.")),
        buttons(t("New coupling"), btn("part-couple-add", t("Add"), () => {
          const r = number(ratio.value.trim(), "ratio");
          if (Number.isNaN(r)) return;
          editOp("add_coupling", { a: b.name, b: w.value, ratio: r === null ? 1 : r });
        })));
    }
    // loops: pinned to another body (a four-bar's last link)
    const loops = (s.loops || []).filter((x) => x.body1 === b.name || x.body2 === b.name);
    rows.push(h("h4", { text: t("Pinned to another body") }));
    if (loops.length) rows.push(h("ul", { class: "part-body-list" }, ...loops.map((x) => h("li", {},
      t("{a} and {b}", { a: x.body1, b: x.body2 }) + " ",
      h("button", { type: "button", class: "linklike", "aria-label": t("Remove the pin between {a} and {b}", { a: x.body1, b: x.body2 }),
        onclick: () => editOp("remove_loop", { body1: x.body1, body2: x.body2 }) }, t("remove"))))));
    const all = s.bodies.filter((x) => x.name !== b.name);
    if (all.length) {
      const w = h("select", { id: "part-loop-with" }, ...all.map((x) => h("option", { value: x.name }, x.name)));
      if (loopWith && all.some((x) => x.name === loopWith)) w.value = loopWith;
      w.addEventListener("change", () => { loopWith = w.value; });
      rows.push(field(t("Pinned to"), w, "body2", t("For a linkage that closes on itself, like a four-bar.")),
        buttons(t("New pin"), btn("part-loop-pick", t("Click the pin"), () => pickPin(b.name, w.value))));
    }
    return h("section", { class: "insp-section" }, details("links", t("More: turns with another joint, pinned to another body"), rows,
      part.errors.some((e) => ["a", "b", "ratio", "body1", "body2"].includes(e.field))));
  },
});

// Picking stops when the selection it was for changes, or the part goes read-only.
onPart(() => {
  if (!view3d.picking || !view3d.picking()) return;
  const owner = view3d.pickOwner();
  if (part.state.read_only_reason || (owner && owner !== selSig())) view3d.cancelPick();
});
