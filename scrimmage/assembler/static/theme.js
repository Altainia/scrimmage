// The page's theme: the Theme setting (scrimmage/settings.py: system, light or dark) as <html data-theme>, which
// app.css's tokens follow (each color token is light-dark(light, dark), and the root's color-scheme picks one).
// The server writes the setting into index.html, so the first paint is already in it; settings.js's answers and
// poll keep it current. Everything in JS that depends on the theme goes through here:
//   isDark()            the theme shown: Dark, or System on a computer set to dark mode
//   onChange(fn)        fn() when the setting changes, and when the computer's mode changes while System is chosen;
//                       returns a function that stops it. Canvas drawings redraw in it.
//   color("--token")    a token's color as the page shows it now (#rrggbb, or rgba(...) when it is see-through);
//                       color("--token", alpha) gives it that alpha
//   choice              "system", "light" or "dark"; setTheme(choice) saves it (null, or the server's error text)

import { settings } from "./settings.js";

const root = document.documentElement;
const THEMES = ["system", "light", "dark"];
const os = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const fns = new Set();
let choice = THEMES.includes(root.dataset.theme) ? root.dataset.theme : "system";
let colors = new Map();      // token -> its color, until the theme changes
let probe = null;

root.dataset.theme = choice;

export function isDark() {
  return choice === "dark" || (choice === "system" && !!os && os.matches);
}

function changed() {
  colors = new Map();
  for (const fn of [...fns]) {
    try { fn(); } catch (e) { console.error(e); }
  }
}

settings.onSettings((data) => {
  if (!THEMES.includes(data.theme) || data.theme === choice) return;
  choice = data.theme;
  root.dataset.theme = choice;
  changed();
});
if (os && os.addEventListener) os.addEventListener("change", () => { if (choice === "system") changed(); });

export function onChange(fn) {
  fns.add(fn);
  return () => fns.delete(fn);
}

export function setTheme(next) { return settings.save({ theme: next }); }

// A token holds light-dark(...), which only an element can resolve: a hidden probe takes it as its color.
function resolve(name) {
  if (!probe) {
    probe = document.createElement("span");
    probe.hidden = true;
    probe.setAttribute("aria-hidden", "true");
  }
  if (!probe.isConnected) (document.body || root).append(probe);
  if (!getComputedStyle(root).getPropertyValue(name).trim()) {
    console.warn(`theme.color: app.css has no ${name}`);
    return "";
  }
  probe.style.color = `var(${name})`;
  const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/.exec(getComputedStyle(probe).color);
  if (!m) return getComputedStyle(probe).color;
  return { rgb: [m[1], m[2], m[3]].map(Number), a: m[4] === undefined ? 1 : Number(m[4]) };
}

export function color(name, alpha) {
  if (!colors.has(name)) colors.set(name, resolve(name));
  const c = colors.get(name);
  if (!c || typeof c === "string") return c;
  const a = alpha === undefined ? c.a : alpha * c.a;
  if (a >= 1) return `#${c.rgb.map((x) => x.toString(16).padStart(2, "0")).join("")}`;
  return `rgba(${c.rgb.join(", ")}, ${+a.toFixed(3)})`;
}

export const theme = {
  get choice() { return choice; },
  isDark, onChange, color, setTheme,
};
export default theme;
