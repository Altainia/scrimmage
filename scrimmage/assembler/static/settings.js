// The user's settings (scrimmage/settings.py) as the page has them: GET /api/settings, shared by units.js (Units),
// i18n.js (Language) and theme.js (Theme), so one request and one poll serve them all.
//   load()              fetch them (app.js awaits it before the tabs start)
//   save(changes)       save {units?, language?, theme?}; resolves to null, or the server's error text
//   watch()             poll every 2 s, so a change made elsewhere (another tab, browser, editor or by hand) arrives
//   onSettings(fn)      fn(answer) for each answer kept: units.js, i18n.js and theme.js take their parts from it
//   onWarning(fn)       fn(text) when a settings warning appears or changes (not for one load() found before)

let warning = null;
const takers = new Set();
const warners = new Set();

function token() { return new URLSearchParams(location.hash.slice(1)).get("token") || ""; }

// The language's catalog comes once, not with every poll: a request says which catalog the page has
// (?catalog=<its catalog_version>), and the answer leaves it out while that is still the one. The page keeps the few
// it was sent, by version, and fills an answer's catalog back in, so the takers always see a whole answer.
const catalogs = new Map();     // catalog_version -> catalog, the newest last
let catalogHave = null;         // the version the page shows

function withCatalog(data) {
  if (!data.catalog_version) return data;
  if (data.catalog) {
    catalogs.delete(data.catalog_version);
    catalogs.set(data.catalog_version, data.catalog);
    while (catalogs.size > 4) catalogs.delete(catalogs.keys().next().value);
  } else if (catalogs.has(data.catalog_version)) {
    data.catalog = catalogs.get(data.catalog_version);
  }
  return data;
}

async function request(method, body) {
  const headers = { "X-Scrimmage-Token": token() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const query = method === "GET" && catalogHave && catalogs.has(catalogHave) ? `?catalog=${encodeURIComponent(catalogHave)}` : "";
  try {
    const res = await fetch(`/api/settings${query}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    return { status: res.status, data: withCatalog(data || {}) };
  } catch {
    return { status: 0, data: { error: "can't reach the editor (is scrimmage edit still running?)" } };
  }
}

let shown = {};      // the answer the takers last had

function apply(data) {
  shown = data;
  if (data.catalog_version && data.catalog) catalogHave = data.catalog_version;
  const hadWarning = warning;
  warning = data.warning || null;
  for (const fn of [...takers]) {
    try { fn(data); } catch (e) { console.error(e); }
  }
  if (warning && warning !== hadWarning) {
    for (const fn of [...warners]) {
      try { fn(warning); } catch (e) { console.error(e); }
    }
  }
}

// Requests are numbered: a GET sent before a save() was answered can carry the old setting (a poll that left just
// before the POST and came back just after it), so its answer is dropped, as is any GET answered while a save() is
// still on its way. Two save()s answered out of order: each setting is taken from the later save that changed it
// (a Units save answered after a newer Language save still shows the new units), and the rest from the later one.
let sent = 0, settled = 0, saving = 0;
let newest = 0;            // the latest save() answered
const answered = {};       // setting -> the latest save() answered that changed it
const PART_OF = { catalog: "language", catalog_version: "language" };   // an answer's catalog goes with its language

export async function load() {
  const n = ++sent;
  const { status, data } = await request("GET");
  if (status === 200 && !saving && n > settled) apply(data);
  return status === 200;
}

export async function save(changes) {
  const n = ++sent;
  saving++;
  let res;
  try { res = await request("POST", changes); } finally { saving--; settled = ++sent; }
  const { status, data } = res;
  if (status !== 200) return data.error || `the server answered ${status}`;
  const kept = { ...shown };
  for (const [key, value] of Object.entries(data)) {
    const setting = PART_OF[key] || key;
    if (setting in changes ? n > (answered[setting] || 0) : n > newest) kept[key] = value;
  }
  for (const setting of Object.keys(changes)) answered[setting] = Math.max(answered[setting] || 0, n);
  newest = Math.max(newest, n);
  apply(kept);      // the takers act only on what changed
  return null;
}

let watching = null;
export function watch(ms = 2000) {
  if (watching) return;
  const tick = async () => {
    if (!document.hidden) await load();
    watching = setTimeout(tick, ms);
  };
  watching = setTimeout(tick, ms);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
}

export function onSettings(fn) {
  takers.add(fn);
  return () => takers.delete(fn);
}

export function onWarning(fn) {
  warners.add(fn);
  return () => warners.delete(fn);
}

export const settings = {
  get warning() { return warning; },
  load, save, watch, onSettings, onWarning,
};
export default settings;
