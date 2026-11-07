// The Bench tab's session tools: the New idea, Sweep and Compare dialogs and the Gallery button (bench.js is the tab).

import { $, h, api, notice, fmt } from "./app.js";
import { units } from "./units.js";
import { t } from "./i18n.js";
import { bench, now, changes, shownIdea, parseKey, valueOf, failed, job, refresh } from "./bench.js";
import { siOf, fileOf, unitOf, entries, keysOf, dotted } from "./bench_fields.js";

// -- session tools ----------------------------------------------------------------------------------------
function dialogErrors(sel, errors) {
  const ul = $(sel);
  ul.replaceChildren(...errors.map((e) => h("li", { text: e.message })));
  ul.hidden = !errors.length;
}

export function initDialogs() {
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
