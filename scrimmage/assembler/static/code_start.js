// The robot code panel's start picture (part 3, "Where it starts") and the start picker (code.js holds the panel).
//
// The start picker (pickStart(), also used by the Drive tab's Start): the field (2026 or the plain red / blue
// field; it is also the field the robot drives on, so choosing one writes [range] at once), the range's named
// starts as quick choices, a "No balls on the floor" switch with its reason on the 2026 field, and the field
// drawing (fieldpick.js): click to place the robot, keep the button down and drag to turn it; "Exact position"
// holds the numbers. OK writes [robot] start_pose (the op `start`, one undo step). See server.py for the API.

import { h, api, edit, undo, notice, store, fmt, showMeasure, readMeasure, measureError, convertTyped } from "./app.js";
import { units } from "./units.js";
import { theme } from "./theme.js";
import { i18n, t } from "./i18n.js";
import { fieldGeometry, drawField, drawSides, fieldPoly, robotFootprint } from "./fieldpick.js";
import { code, els, loadState, loadView, FIELD_NAME } from "./code.js";

// The robot is drawn in a color nothing on a field uses (dark slate with a white triangle; light gray with a dark one
// in the dark theme: app.css --field-robot*), so it can't be mistaken for the plain field's amber practice box or a
// game piece, on either alliance's half.
const ROBOT = { fill: ["--field-robot", 0.82], stroke: ["--field-robot-edge"], tri: ["--field-robot-mark"],
  ghostFill: ["--field-robot", 0.35] };
const robotColor = (which) => theme.color(...ROBOT[which]);
const floorWhy = () => t("The 2026 field starts with 360 FUEL balls on the floor, which makes the simulation run slower.");

// -- 3. the start picture ------------------------------------------------------------------------------------------
function triangleOf(foot) {
  const r = foot.length ? Math.max(0.12, Math.min(Math.max(...foot.map((p) => p[0])), Math.max(...foot.map((p) => p[1])))) : 0.3;
  return [[0.8 * r, 0], [-0.5 * r, 0.5 * r], [-0.5 * r, -0.5 * r]];
}

const place = (pts, [x, y, deg]) => {
  const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  return pts.map(([u, v]) => [x + u * c - v * s, y + u * s + v * c]);
};

// The robot (its footprint, or a 0.8 m square when the view has none) at pose, with its heading triangle.
function drawRobot(ctx, toPx, foot, pose, { ghost = false } = {}) {
  const body = foot.length >= 3 ? foot : [[-0.4, -0.4], [0.4, -0.4], [0.4, 0.4], [-0.4, 0.4]];
  const at = place(body, pose), tri = place(triangleOf(body), pose);
  fieldPoly(ctx, toPx, at, robotColor(ghost ? "ghostFill" : "fill"), robotColor("stroke"), 2, ghost ? [5, 3] : null);
  fieldPoly(ctx, toPx, tri, robotColor("tri"), robotColor("stroke"), 1);
  return { footprint: at, triangle: tri };
}

// Names on the field's own boxes that could be taken for something else: the plain field's practice obstacle.
const BOX_LABEL = { obstacle: () => t("practice box") };
function labelBoxes(ctx, geo, toPx) {
  const out = [];
  for (const o of geo.outlines) {
    if (!BOX_LABEL[o.name]) continue;
    const label = BOX_LABEL[o.name]();
    const cx = o.poly.reduce((a, q) => a + q[0], 0) / o.poly.length, cy = o.poly.reduce((a, q) => a + q[1], 0) / o.poly.length;
    const [px, py] = toPx(cx, cy);
    ctx.save();
    ctx.font = "600 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = theme.color("--text");
    ctx.fillText(label, px, py);
    ctx.restore();
    out.push(label);
  }
  return out;
}

function fieldFor(view) {
  return { geo: fieldGeometry(view), foot: robotFootprint(view) };
}

export function drawPicture() {
  const s = code.state;
  if (!els || !s || !s.start.picked || !s.start.pose || !code.view || els.canvas.hidden) return;
  const { geo, foot } = fieldFor(code.view);
  const b = geo.floor.bounds;
  const W = Math.max(200, Math.min(640, Math.floor(els.canvas.parentElement.clientWidth || 480)));
  const scale = W / (b.maxx - b.minx), H = Math.round((b.maxy - b.miny) * scale);
  const dpr = window.devicePixelRatio || 1;
  els.canvas.width = Math.round(W * dpr); els.canvas.height = Math.round(H * dpr);
  els.canvas.style.width = `${W}px`; els.canvas.style.height = `${H}px`;
  const ctx = els.canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const toPx = (x, y) => [(x - b.minx) * scale, (b.maxy - y) * scale];
  ctx.fillStyle = theme.color("--field-surround"); ctx.fillRect(0, 0, W, H);
  drawField(ctx, geo, toPx);
  const labels = labelBoxes(ctx, geo, toPx);
  if (s.start.range === null) drawSides(ctx, geo, toPx);
  const pic = drawRobot(ctx, toPx, foot, s.start.pose);
  els.canvas.setAttribute("aria-label", t("{start}: the field seen from above, with the robot at its real size and a triangle pointing the way it faces.", { start: s.start.sentence }));
  window.__scrimmage.code.picture = { pose: [...s.start.pose], ...pic, labels };
}

// -- the start picker ----------------------------------------------------------------------------------------------
let dlg = null;
export let picking = null;     // the open picker's promise, so a second ask joins it

export function pickStart() {
  if (picking) return picking;
  picking = openPicker().finally(() => { picking = null; });
  return picking;
}

const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const normDeg = (d) => { let a = ((d + 180) % 360 + 360) % 360 - 180; if (a === -180) a = 180; return a; };

async function openPicker() {
  if (store.state && store.state.read_only) {
    notice(t("This robot is read-only: {why}", { why: store.state.read_only_reason }), "warn");
    return false;
  }
  await loadState();
  if (!code.state) return false;
  if (!dlg) {
    dlg = h("dialog", { id: "start-dialog", class: "start-dialog", "aria-labelledby": "sp-title" });
    document.body.append(dlg);
  }
  const d = dlg;
  const st0 = code.state.start;
  const cur = { x: st0.pose ? st0.pose[0] : null, y: st0.pose ? st0.pose[1] : null, heading: st0.pose ? st0.pose[2] : 0 };
  let view = await loadView(), geo = null, foot = [], W = 0, H = 0, scale = 1, b = null;
  let changingField = false;

  return new Promise((resolve) => {
    let done = false;
    // Cancel means cancel: the field and floor-ball choices were written as they were made (the field drawing
    // comes from the robot's world), so closing without OK undoes each of them, newest first. Only the picker's
    // own: before each undo the robot must still be at the version the picker's last write (or undo) left; when
    // someone else (another tab, the agent, a text editor) changed it meanwhile, nothing more is undone.
    const written = [];    // the versions the picker's writes returned, oldest first
    let inFlight = Promise.resolve();   // the picker's writes still on their way: Cancel waits for them, since the
                                        // page can show a write (the robot's refresh) before its version is recorded
    const finish = async (ok) => {
      if (done) return;
      done = true;
      if (d.open) d.close();
      await inFlight;
      if (!ok && written.length) {
        let expect = written[written.length - 1];
        for (let n = written.length; n > 0; n--) {
          const { status, data } = await api("GET", "/api/robot/state");
          if (status !== 200 || data.version !== expect) {
            notice(t("Someone else changed the robot while the start picker was open, so the field wasn't put back. Use Undo if you want to."), "warn", "start-cancel");
            break;
          }
          const r = await undo(expect);   // refused if someone else's edit lands between the check and the undo
          if (!r.ok) { notice(t("Couldn't put the field back after Cancel; use Undo if you want to."), "warn", "start-cancel"); break; }
          expect = r.version;
        }
        await loadState();
      }
      resolve(ok);
    };
    d.onclose = () => { delete window.__scrimmage.startPicker; offUnits(); offTheme(); offLanguage(); finish(false); };

    const err = h("ul", { id: "sp-errors", class: "error-list", role: "alert", hidden: true });
    const showErrors = (errors) => {
      err.replaceChildren(...errors.map((e) => h("li", { text: e.message })));
      err.hidden = !errors.length;
    };
    const canvas = h("canvas", { id: "start-field-canvas", class: "field-canvas", tabindex: "0", role: "application",
      "aria-label": t("Field, seen from above. Click where the robot starts; keep the button down and drag to turn it. Arrow keys move it, [ and ] turn it.") });
    const ctx = canvas.getContext("2d");
    // x and y are field lengths in the chosen units (units.js): text boxes, so "12.5 ft" can be typed too
    // the picker's fixed words, done again on a language change (what is typed in its boxes stays)
    const words = [];
    const W_ = (fn) => { const node = document.createTextNode(fn()); words.push([node, fn]); return node; };
    const num = (id, label, unit, step, kind) => {
      const el = kind ? h("input", { id, type: "text", inputmode: "decimal", autocomplete: "off", class: "fp-num", "aria-label": label() })
        : h("input", { id, type: "number", step, inputmode: "decimal", class: "fp-num", "aria-label": label() });
      words.push([{ set data(v) { el.setAttribute("aria-label", v); } }, label]);
      const u = h("span", { class: "unit" }, unit);
      return [el, h("label", { class: "fp-field", for: id }, h("span", { class: "fp-label sp-label" }, W_(label)), h("span", { class: "with-unit" }, el, u)), u];
    };
    const [xIn, xF, xU] = num("sp-x", () => t("x (from the blue wall)"), units.name("field"), null, "field");
    const [yIn, yF, yU] = num("sp-y", () => t("y (from the right side, seen from blue)"), units.name("field"), null, "field");
    const [hIn, hF] = num("sp-heading", () => t("facing"), "°", "1");
    // a box being typed in (its text isn't what it showed): kept across a change of units
    const typing = (el) => document.activeElement === el && el.value.trim() !== el.dataset.shown;
    const sync = ({ keepTyping = false } = {}) => {
      for (const [el, k] of [[xIn, "x"], [yIn, "y"]]) if (!(keepTyping && typing(el))) showMeasure(el, cur[k], "field");
      hIn.value = fmt(round(cur.heading, 1));
    };
    // Units changed in another tab or editor while this is open: the boxes show the new units. Text being typed in is
    // converted to the new units, as in the inspector and the step editor (it is read again here and at OK).
    const offUnits = units.onChange(() => {
      xU.textContent = yU.textContent = units.name("field");
      const kept = [xIn, yIn].filter(typing);
      sync({ keepTyping: true });
      for (const el of kept) convertTyped(el).dispatchEvent(new Event("input"));
      draw();   // the grid's labels are in the chosen units
    });
    const offTheme = theme.onChange(() => draw());
    const offLanguage = i18n.onChange(() => {
      for (const [node, fn] of words) node.data = fn();
      canvas.setAttribute("aria-label", t("Field, seen from above. Click where the robot starts; keep the button down and drag to turn it. Arrow keys move it, [ and ] turn it."));
      renderTop(); draw();
    });
    for (const [el, k] of [[xIn, "x"], [yIn, "y"], [hIn, "heading"]]) {
      el.addEventListener("input", () => {
        if (el.validity.badInput || el.value.trim() === "") return;
        if (k !== "heading") {
          const v = readMeasure(el, "field");   // not a field length yet: OK says what to type
          if (typeof v !== "number") return;
          cur[k] = v;
        } else cur[k] = Number(el.value);
        if (k !== "heading" && cur.x !== null && cur.y === null) cur.y = (geo.floor.miny + geo.floor.maxy) / 2;
        if (k !== "heading" && cur.y !== null && cur.x === null) cur.x = (geo.floor.minx + geo.floor.maxx) / 2;
        draw();
      });
    }
    const exact = h("details", { id: "sp-exact", class: "sp-exact" }, h("summary", {}, W_(() => t("Exact position"))),
      h("div", { class: "fp-row" }, xF, yF, hF),
      h("p", { class: "hint" }, W_(() => t("Measured from the blue alliance's corner of the field; facing 0° is toward the red wall, 90° to the left."))));
    const top = h("div", { class: "sp-top" });

    function layout() {
      ({ geo, foot } = fieldFor(view));
      b = geo.floor.bounds;
      const maxW = Math.min(980, window.innerWidth * 0.92 - 48), maxH = Math.max(220, window.innerHeight * 0.92 - 330);
      scale = Math.min(maxW / (b.maxx - b.minx), maxH / (b.maxy - b.miny));
      W = Math.round((b.maxx - b.minx) * scale); H = Math.round((b.maxy - b.miny) * scale);
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const toPx = (x, y) => [(x - b.minx) * scale, (b.maxy - y) * scale];
    const toField = (px, py) => [b.minx + px / scale, b.maxy - py / scale];

    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = theme.color("--field-surround"); ctx.fillRect(0, 0, W, H);
      drawField(ctx, geo, toPx);
      labelBoxes(ctx, geo, toPx);
      if (code.state.start.range === null) drawSides(ctx, geo, toPx);
      if (cur.x !== null && cur.y !== null) drawRobot(ctx, toPx, foot, [cur.x, cur.y, cur.heading], { ghost: !placed });
    }
    let placed = !!st0.pose;

    // the field choice, the floor balls and the named starts: written as they are chosen
    function renderTop() {
      const st = code.state.start;
      const radio = (value, label) => h("label", { class: "sp-choice" },
        h("input", { type: "radio", name: "sp-field", id: `sp-field-${value}`, value,
          checked: (value === "plain" ? st.range === null : st.range === value) || undefined,
          onchange: () => chooseField(value) }), h("span", { text: label }));
      const rows = [];
      if (st.range_read_only && !changingField) {
        rows.push(h("p", { class: "sp-field-row" }, h("strong", { text: t("Field:") + " " }), h("span", { class: "mono", text: st.range }),
          h("span", { class: "muted", text: " " + t("(named in the robot file)") + " " }),
          h("button", { type: "button", class: "btn btn-small", id: "sp-field-change", onclick: () => { changingField = true; renderTop(); } }, t("Change"))));
      } else {
        rows.push(h("fieldset", { class: "sp-field-row" }, h("legend", { class: "sp-legend" }, t("Field")),
          radio("2026", FIELD_NAME[2026]()), radio("plain", FIELD_NAME.plain()),
          h("p", { class: "hint", text: t("It's also the field the robot drives on.") })));
      }
      if (st.range === "2026") {
        const sw = h("input", { type: "checkbox", id: "sp-floor-balls", checked: !st.floor_balls || undefined,
          onchange: () => floorBalls(!sw.checked) });
        rows.push(h("div", { class: "sp-floor" }, h("label", { class: "sp-choice", for: "sp-floor-balls" }, sw, h("strong", { text: t("No balls on the floor") })),
          h("p", { class: "hint", id: "sp-floor-why", text: floorWhy() })));
      }
      if (st.named_starts.length) {
        rows.push(h("div", { class: "sp-named" }, h("strong", { class: "sp-legend", text: t("Quick choices:") + " " }),
          ...st.named_starts.map((n) => h("button", { type: "button", class: `btn btn-small${st.named === n ? " btn-primary-soft" : ""}`,
            dataset: { named: n }, onclick: () => named(n) }, n.replace(/_/g, " ")))));
      }
      top.replaceChildren(...rows);
    }

    function apply(fields) {
      const go = (async () => {
        showErrors([]);
        const r = await edit("start", fields, { ownErrors: true });
        if (!r.ok) { if (!r.conflict) showErrors(r.errors || []); return false; }
        written.push(r.version);
        await loadState();
        return true;
      })();
      inFlight = inFlight.then(() => go, () => go).catch(() => {});
      return go;
    }
    async function chooseField(value) {
      if (!(await apply({ range: value === "2026" ? "2026" : null }))) { renderTop(); return; }
      changingField = false;
      renderTop();
      view = await loadView(true);
      layout(); draw();
    }
    async function floorBalls(on) {
      if (!(await apply({ floor_balls: on }))) renderTop();
    }
    async function named(n) {
      if (await apply({ named: n })) finish(true);
    }

    // pointer: press to place, keep the button down and drag to turn
    let drag = null;
    const at = (e) => { const rc = canvas.getBoundingClientRect(); return [e.clientX - rc.left, e.clientY - rc.top]; };
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      canvas.focus();
      const [px, py] = at(e);
      const [x, y] = toField(px, py);
      cur.x = round(x, 2); cur.y = round(y, 2);
      placed = true;
      drag = { px, py, aiming: false };
      canvas.setPointerCapture(e.pointerId);
      sync(); draw();
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const [px, py] = at(e);
      if (!drag.aiming && Math.hypot(px - drag.px, py - drag.py) < 6) return;
      drag.aiming = true;
      const [tx, ty] = toPx(cur.x, cur.y);
      cur.heading = round(normDeg(Math.atan2(-(py - ty), px - tx) * 180 / Math.PI), 0);
      sync(); draw();
    });
    const endDrag = () => { drag = null; };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("keydown", (e) => {
      const stepM = e.shiftKey ? 0.5 : 0.05, stepD = e.shiftKey ? 15 : 5;
      const move = { ArrowLeft: [-stepM, 0], ArrowRight: [stepM, 0], ArrowUp: [0, stepM], ArrowDown: [0, -stepM] }[e.key];
      if (move) {
        if (cur.x === null) { cur.x = (geo.floor.minx + geo.floor.maxx) / 4; cur.y = (geo.floor.miny + geo.floor.maxy) / 2; }
        cur.x = round(cur.x + move[0], 3); cur.y = round(cur.y + move[1], 3);
      } else if (e.key === "[" || e.key === "]") {
        if (cur.x === null) return;
        cur.heading = normDeg(cur.heading + (e.key === "[" ? stepD : -stepD));
      } else return;
      placed = true;
      e.preventDefault();
      sync(); draw();
    });

    const form = h("form", { method: "dialog", novalidate: true },
      h("h2", { id: "sp-title" }, W_(() => t("Where does the robot start?"))),
      top,
      h("p", { class: "muted fp-help" }, W_(() => t("Click the field where the robot starts; keep the button down and drag to turn it the way it faces. The triangle shows its front."))),
      h("div", { class: "fp-canvas-wrap" }, canvas),
      exact,
      err,
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", id: "sp-cancel", class: "btn", onclick: () => finish(false) }, W_(() => t("Cancel"))),
        h("button", { type: "submit", id: "sp-ok", class: "btn btn-primary" }, W_(() => t("OK")))));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (hIn.validity.badInput) { showErrors([{ message: t("{box} must be a number", { box: hIn.getAttribute("aria-label") }) }]); hIn.focus(); return; }
      // x and y as their boxes say now (an empty box keeps the picked value)
      for (const [el, k] of [[xIn, "x"], [yIn, "y"]]) {
        const v = readMeasure(el, "field");
        if (measureError(v)) { showErrors([{ message: measureError(v) }]); el.focus(); return; }
        if (v !== null) cur[k] = v;
      }
      if (cur.x === null || cur.y === null || !placed) { showErrors([{ message: t("Click the field where the robot starts first.") }]); return; }
      // to 1 µm: fine enough that a typed 11.5 ft (3.5052 m) reads back as typed, coarse enough for no float noise
      if (await apply({ pose: [round(cur.x, 6), round(cur.y, 6), round(normDeg(cur.heading), 1)] })) finish(true);
    });
    d.replaceChildren(form);
    layout(); renderTop(); sync(); draw();
    window.__scrimmage.startPicker = {
      toScreen: (x, y) => { const rc = canvas.getBoundingClientRect(); const [px, py] = toPx(x, y); return { x: rc.left + px, y: rc.top + py }; },
      get value() { return { ...cur }; },
    };
    d.showModal();
    canvas.focus();
  });
}
