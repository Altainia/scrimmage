// The Part tab's inspector sections for what a body carries: mounts, collision (with its grip) and links (couplings
// and loops); and the rule that stops a pick when its selection changes (part_inspector.js lists the sections).

import { h, api, defaultGrip } from "./app.js";
import { part, view3d, addSection, editOp, render, live, field, box, number, onPart } from "./part.js";
import { t } from "./i18n.js";
import { robotToCad } from "./part_frame.js";
import { rt, why, fmtNum, selSig, needView, buttons, btn, details } from "./part_helpers.js";

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
