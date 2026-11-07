// The Part tab's inspector sections for the part, its pieces and its bodies: summary, pieces, material, body and
// bodies (part_inspector.js lists every section in order).

import { h } from "./app.js";
import { part, addSection, editOp, select, render, live, field, box, number, matLabel, kg, cog, expectBody, onPart,
  measure, shownMeasure } from "./part.js";
import { units } from "./units.js";
import { t, tn } from "./i18n.js";
import { rich, placeholderMass, origin, descendants } from "./part_helpers.js";

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
