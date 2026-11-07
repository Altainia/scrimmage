// The Part tab's "Where's the robot?" step over the 3D view and the frame section (part_inspector.js lists the
// sections in order).

import { h, api } from "./app.js";
import { part, view3d, addSection, editOp, render, onPart } from "./part.js";
import { t } from "./i18n.js";
import { robotToCad, turnFront, floorAndCenter, otherAxes, upIndex } from "./part_frame.js";
import { rt, why, rich, needView, buttons, btn } from "./part_helpers.js";

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

