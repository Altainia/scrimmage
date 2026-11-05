// The page's words in the user's language: the Language setting and the catalogs of scrimmage/i18n.py, which
// GET /api/settings sends (the current language's entries only), so the page and the server translate alike.
//
// The source text is American English and is its own key; it must be a literal string (no variable, no + or
// ${...}), so `scrimmage i18n template` can find it. Dynamic parts are named {placeholders}; {{ and }} are braces.
//   t("Color")                                 "Color" or "Colour"
//   t("Saved {name}", { name })                placeholders filled from params
//   t("Open", { context: "file" })             a source that means two different things
//   tn("{count} part", "{count} parts", n)     the plural form Intl.PluralRules picks for n; {count} is n
//   i18n.lang                                  the language's tag ("en-US"); <html lang> follows it
//   i18n.languages                             {tag: name}, for the Settings menu
//   onChange(fn)                               fn(lang) after the setting changes (here, elsewhere or by hand):
//                                              re-render what's shown; returns a function that stops it
//   setLanguage(tag)                           save the setting; resolves to null, or the server's error text
// Loading and the poll are settings.js's (app.js awaits settings.load() before the tabs start). A broken entry
// shows in English, with one console warning for it (L6).

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
  const was = lang;
  if (data.languages) languages = data.languages;
  if (data.language) {
    lang = data.language;
    catalog = data.catalog || {};
  }
  document.documentElement.lang = lang;
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

export const i18n = {
  get lang() { return lang; },
  get languages() { return { ...languages }; },
  t, tn, onChange, setLanguage,
};
export default i18n;
