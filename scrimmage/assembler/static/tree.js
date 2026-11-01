// The robot tree (right column): parts nested by what they attach to. Keyboard: Up/Down move,
// Left/Right collapse/expand (or go to parent/first child), Home/End, Enter/Space select.

import { $, h, select, store } from "./app.js";

const collapsed = new Set();

function parentOf(p, names) {
  if (!p.attach) return null;
  const parent = String(p.attach).split(".")[0];
  return names.has(parent) && parent !== p.name ? parent : null;
}

function problemKinds(st) {
  const out = new Map();   // instance -> "error" | "warning"
  const mark = (list, kind) => {
    for (const e of list) {
      const m = /^([A-Za-z0-9_]+)\//.exec(e.field || "");
      if (m && out.get(m[1]) !== "error") out.set(m[1], kind);
    }
  };
  mark(st.state.warnings, "warning");
  mark([...st.state.errors, ...st.lastErrors], "error");
  return out;
}

export function renderTree(st) {
  const root = $("#tree");
  const s = st.state;
  const hadFocus = root.contains(document.activeElement);
  if (!s.parts.length) {
    root.replaceChildren(h("p", { class: "empty" },
      "No parts yet. Add a part from the library on the left; the first one becomes the robot's root."));
    return;
  }
  const names = new Set(s.parts.map((p) => p.name));
  const kids = new Map();
  const tops = [];
  for (const p of s.parts) {
    const par = parentOf(p, names);
    if (par === null) tops.push(p);
    else { if (!kids.has(par)) kids.set(par, []); kids.get(par).push(p); }
  }
  // a part in an attach loop has a parent but no path to a top; show it at the top level too
  const reached = new Set();
  const walk = (p) => { if (reached.has(p.name)) return; reached.add(p.name); for (const c of kids.get(p.name) || []) walk(c); };
  tops.forEach(walk);
  for (const p of s.parts) if (!reached.has(p.name)) { tops.push(p); walk(p); }

  const problems = problemKinds(st);
  const seen = new Set();
  const item = (p, level) => {
    seen.add(p.name);
    const children = (kids.get(p.name) || []).filter((c) => !seen.has(c.name));
    const open = !collapsed.has(p.name);
    const sel = st.selected === p.name;
    const prob = problems.get(p.name);
    const mount = p.attach ? String(p.attach).split(".").slice(1).join(".") : null;
    const li = h("li", {
      role: "treeitem", "aria-level": String(level), "aria-selected": sel ? "true" : "false",
      "aria-expanded": children.length ? String(open) : null, tabindex: sel ? "0" : "-1",
      class: `tree-item${sel ? " selected" : ""}`, dataset: { name: p.name },
    },
    h("div", { class: "tree-row", style: `padding-left:${0.5 + (level - 1) * 1.1}rem` },
      children.length
        ? h("span", { class: "twisty", "aria-hidden": "true", onclick: (e) => { e.stopPropagation(); toggle(p.name); } }, open ? "▾" : "▸")
        : h("span", { class: "twisty", "aria-hidden": "true" }, ""),
      h("span", { class: "tree-name", text: p.name }),
      h("span", { class: "tree-src", text: p.stock || `folder ${p.path}` }),
      mount ? h("span", { class: "tree-mount", text: `@ ${mount}` }) : h("span", { class: "tree-mount", text: "root" }),
      prob ? h("span", { class: `dot dot-${prob}`, title: prob === "error" ? "has errors" : "has warnings", "aria-label": prob === "error" ? "has errors" : "has warnings" }) : null));
    if (children.length && open) {
      li.append(h("ul", { role: "group" }, children.map((c) => item(c, level + 1))));
    }
    return li;
  };
  const ul = h("ul", { role: "tree", "aria-label": "Robot parts", class: "tree" }, tops.map((p) => item(p, 1)));
  if (!ul.querySelector('[tabindex="0"]')) ul.querySelector('[role="treeitem"]').tabIndex = 0;
  ul.addEventListener("click", (e) => {
    const li = e.target.closest('[role="treeitem"]');
    if (li) { select(li.dataset.name); focusItem(li.dataset.name); }
  });
  ul.addEventListener("keydown", onKey);
  // hovering a row highlights the part in the 3D view
  ul.addEventListener("pointerover", (e) => hoverRow(e.target.closest('[role="treeitem"]')));
  ul.addEventListener("pointerleave", () => hoverRow(null));
  root.replaceChildren(ul);
  if (hadFocus) focusItem(st.selected);
}

let hoveredRow = null;
function hoverRow(li) {
  const name = li ? li.dataset.name : null;
  if (name === hoveredRow) return;
  hoveredRow = name;
  window.dispatchEvent(new CustomEvent("scrimmage:hover", { detail: { name, source: "tree" } }));
}

function toggle(name) {
  if (collapsed.has(name)) collapsed.delete(name); else collapsed.add(name);
  renderTree(store);
  focusItem(name);
}

function focusItem(name) {
  const el = name && document.querySelector(`#tree [role="treeitem"][data-name="${CSS.escape(name)}"]`);
  if (el) el.focus();
}

function visibleItems() {
  return [...document.querySelectorAll('#tree [role="treeitem"]')].filter((el) => el.offsetParent !== null);
}

function onKey(e) {
  const cur = e.target.closest('[role="treeitem"]');
  if (!cur) return;
  const items = visibleItems();
  const i = items.indexOf(cur);
  const go = (el) => { if (el) { e.preventDefault(); select(el.dataset.name); focusItem(el.dataset.name); } };
  switch (e.key) {
    case "ArrowDown": go(items[i + 1]); break;
    case "ArrowUp": go(items[i - 1]); break;
    case "Home": go(items[0]); break;
    case "End": go(items[items.length - 1]); break;
    case "ArrowRight":
      if (cur.getAttribute("aria-expanded") === "false") { e.preventDefault(); toggle(cur.dataset.name); }
      else if (cur.getAttribute("aria-expanded") === "true") go(cur.querySelector('[role="treeitem"]'));
      break;
    case "ArrowLeft":
      if (cur.getAttribute("aria-expanded") === "true") { e.preventDefault(); toggle(cur.dataset.name); }
      else go(cur.parentElement.closest('[role="treeitem"]'));
      break;
    case "Enter": case " ":
      e.preventDefault(); select(cur.dataset.name);
      { const first = document.querySelector("#inspector input, #inspector select"); if (e.key === "Enter" && first) first.focus(); }
      break;
    default:
  }
}
