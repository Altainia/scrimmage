// The page's words in the user's language: the Language setting and the catalogs of scrimmage/i18n.py, which
// GET /api/settings sends (the current language's entries only), so the page and the server translate alike.
//
// The source text is American English and is its own key; it must be a literal string (no variable, no + or
// ${...}), so `scrimmage i18n template` can find it. Dynamic parts are named {placeholders}; {{ and }} are braces.
//   t("Center of gravity")                     "Center of gravity" or "Centre of gravity"
//   t("Saved {name}", { name })                placeholders filled from params
//   t("Open", { context: "file" })             a source that means two different things
//   tn("{count} part", "{count} parts", n)     the plural form Intl.PluralRules picks for n; {count} is n
//   i18n.lang                                  the language's tag ("en-US"); <html lang> follows it
//   i18n.languages                             {tag: name}, for the Settings menu
//   onChange(fn)                               fn(lang) after the setting changes (here, elsewhere or by hand):
//                                              re-render what's shown; returns a function that stops it
//   setLanguage(tag)                           save the setting; resolves to null, or the server's error text
//   translate(root)                            fill in root's data-i18n* attributes (below); the page's own are
//                                              filled at a change, before the onChange functions run
//   live(() => t("Recent"))                    a text node that writes itself again at a change, for words in a
//                                              part of the page built once (no re-render, so no typing is lost)
//   liveAttr(el, "title", () => t("..."))      likewise for an attribute
//   markup(el, "title")                        the translation of what index.html's data-i18n-title (or data-i18n,
//                                              "text") names on el, for a script that puts the page's own text back
// Loading and the poll are settings.js's (app.js awaits settings.load() before the tabs start). A broken entry
// shows in English, with one console warning for it (L6).
//
// Static text in index.html names its source in an attribute, which `scrimmage i18n template` also reads:
//   <h2 data-i18n="Parts library">Parts library</h2>                the text (an element holding only text)
//   <button data-i18n-title="Undo (Ctrl+Z)" title="Undo (Ctrl+Z)">  likewise -placeholder and -aria-label
//   data-i18n-context="verb"                                        the context for all of them on the element
// An element whose text (or attribute) a script has since changed is left alone: its script re-renders it.

import { settings } from "./settings.js";

export const SOURCE = "en-US";
const CONTEXT = "\u0004";
const FIELD = /\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

let lang = SOURCE;
let catalog = {};
let languages = { [SOURCE]: "English (US)" };
const listeners = new Set();
const warned = new Set();

settings.onSettings((data) => {
  const was = lang, had = catalog;
  if (data.languages) languages = data.languages;
  if (data.language) {
    lang = data.language;
    catalog = data.catalog || (lang === was ? catalog : {});
  }
  document.documentElement.lang = lang;
  if (lang !== was || catalog !== had) { translate(document); rewriteLives(); }
  if (lang !== was) {
    for (const fn of [...listeners]) {
      try { fn(lang); } catch (e) { console.error(e); }
    }
  }
});

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setLanguage(tag) { return settings.save({ language: tag }); }

const rules = new Map();
function pluralRules(tag) {
  if (!rules.has(tag)) rules.set(tag, new Intl.PluralRules(tag));
  return rules.get(tag);
}
const category = (tag, n) => pluralRules(tag).select(n);

function names(text) {
  return new Set([...text.matchAll(FIELD)].map((m) => m[1]).filter(Boolean));
}

function extra(text, allowed) {
  return [...names(text)].filter((n) => !allowed.has(n)).sort().map((n) => `{${n}}`).join(", ");
}

const blank = (e) => e === "" || (e && typeof e === "object" && Object.values(e).every((v) => v === ""));

// Why a catalog entry can't be used (scrimmage/i18n.py entry_problem), else null
function problem(source, entry, plural) {
  if (!plural) {
    if (typeof entry !== "string") return "should be text";
    const x = extra(entry, names(source));
    return x ? `uses ${x}, which the source doesn't have` : null;
  }
  const forms = pluralRules(lang).resolvedOptions().pluralCategories;
  const want = ["one", "two", "few", "many", "zero", "other"].filter((f) => forms.includes(f));
  if (!entry || typeof entry !== "object") return `should be a plural's forms, an object of ${want.join(" and ")}`;
  for (const f of want) {
    if (typeof entry[f] !== "string" || !entry[f]) return `has no ${f} form (a plural in ${lang} needs ${want.join(" and ")})`;
  }
  const [singular, pluralText] = source.split("|");
  const allowed = new Set([...names(singular), ...names(pluralText), "count"]);
  for (const [f, text] of Object.entries(entry)) {
    if (typeof text !== "string") return `${f} should be text`;
    const x = extra(text, allowed);
    if (x) return `${f} uses ${x}, which the source doesn't have`;
  }
  return null;
}

function entry(key, source, plural) {
  if (lang === SOURCE || !Object.prototype.hasOwnProperty.call(catalog, key)) return null;
  const e = catalog[key];
  if (e === null || blank(e)) return null;
  const why = problem(source, e, plural);
  if (why) {
    const once = `${lang}\u0000${key}`;
    if (!warned.has(once)) {
      warned.add(once);
      console.warn(`${lang}.json: the entry ${JSON.stringify(key)} ${why}; it shows in English`);
    }
    return null;
  }
  return e;
}

function fill(text, params, source) {
  return text.replace(FIELD, (m, name) => {
    if (m === "{{") return "{";
    if (m === "}}") return "}";
    if (!(name in params)) throw new Error(`${JSON.stringify(source)} is missing a value for {${name}}`);
    return String(params[name]);
  });
}

const keyOf = (source, context) => (context ? `${context}${CONTEXT}${source}` : source);

export function t(source, params = {}) {
  const e = entry(keyOf(source, params.context), source, false);
  return fill(e || source, params, source);
}

export function tn(singular, plural, count, params = {}) {
  const source = `${singular}|${plural}`;
  const e = entry(keyOf(source, params.context), source, true);
  const text = e ? e[category(lang, count)] : category(SOURCE, count) === "one" ? singular : plural;
  return fill(text, { ...params, count }, singular);
}

// -- words that write themselves again ---------------------------------------------------------------------------
const lives = new Set();     // [WeakRef(node or element), write]: dropped once the page has let go of the node
let added = 0;

function keep(target, write) {
  lives.add([new WeakRef(target), write]);
  if (++added % 200 === 0) {     // dialogs built on every open add their words each time: let go of the gone ones
    for (const item of lives) if (!item[0].deref()) lives.delete(item);
  }
}

export function live(fn) {
  const node = document.createTextNode(fn());
  keep(node, (n) => { n.data = fn(); });
  return node;
}

export function liveAttr(el, name, fn) {
  el.setAttribute(name, fn());
  keep(el, (e) => e.setAttribute(name, fn()));
  return el;
}

export function markup(el, which = "text") {
  const source = el.getAttribute(which === "text" ? "data-i18n" : `data-i18n-${which}`);
  return source === null ? null : t(source, { context: el.getAttribute("data-i18n-context") || undefined });
}

function rewriteLives() {
  for (const item of lives) {
    const node = item[0].deref();
    if (!node) { lives.delete(item); continue; }
    try { item[1](node); } catch (e) { console.error(e); }
  }
}

// -- the page's static text --------------------------------------------------------------------------------------
const ATTRS = [["data-i18n", null], ["data-i18n-title", "title"], ["data-i18n-placeholder", "placeholder"],
  ["data-i18n-aria-label", "aria-label"]];
const SELECTOR = ATTRS.map(([a]) => `[${a}]`).join(",");
const written = new WeakMap();      // element -> {target: the text last written there}
const same = (a, b) => String(a ?? "").replace(/\s+/g, " ").trim() === String(b ?? "").replace(/\s+/g, " ").trim();

export function translate(root = document) {
  const els = [...(root.matches && root.matches(SELECTOR) ? [root] : []), ...root.querySelectorAll(SELECTOR)];
  for (const el of els) {
    const context = el.getAttribute("data-i18n-context") || undefined;
    const last = written.get(el) || {};
    for (const [attr, target] of ATTRS) {
      const source = el.getAttribute(attr);
      if (source === null) continue;
      const now = target ? el.getAttribute(target) : el.textContent;
      if (!same(now, last[target] ?? source)) continue;      // a script has changed it since
      const text = t(source, { context });
      if (target) el.setAttribute(target, text); else el.textContent = text;
      last[target] = text;
    }
    written.set(el, last);
  }
}

export const i18n = {
  get lang() { return lang; },
  get languages() { return { ...languages }; },
  t, tn, onChange, setLanguage, translate, live, liveAttr, markup,
};
export default i18n;
