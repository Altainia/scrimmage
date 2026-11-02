// The start page: shown when `scrimmage edit` was run with no file, from the "Start page" link in the top bar, or
// when the open robot names a team folder this computer hasn't set (it then asks where that folder is first). It
// lists what was opened recently, browses folders, makes a new robot, scenario, idea session or part from CAD (Browse
// for a .step/.stl/.obj file or a folder of them, or drop one on the page), and sets this computer's team folders
// ({cad} and {code}), and starts a block test of a mechanism ("Test a mechanism": a robot file gets a test at once; a
// CAD file goes through the Part tab, whose Test it makes the stand-in robot and the test). After an
// open or new the server has already swapped what it holds, so the page reloads itself (see /api/app/* in
// scrimmage/assembler/server.py).
import { api, h, notice, sessionToken } from "./app.js";

const KIND = { robot: "Robot", scenario: "Scenario", session: "Idea session", bench: "Idea", folder: "Folder", import: "Part", cad: "CAD file" };
export const CAD_FILE = /\.(step|stp|stl|obj)$/i;

// A CAD file sent to POST /api/part/upload: {status, data} (data is the part's state on 200). With a part open it
// replaces that part's source; with none it makes a new import and opens it.
// Send dropped or chosen CAD files to /api/part/upload: one file (X-Filename), or several STL or OBJ files read
// together as one source (X-Files: each one's name and size, in the order the body holds them). `again` is Import
// again on a copied source: the settings are kept as for an ordinary re-import.
export async function uploadCad(files, { again = false } = {}) {
  const list = [...(files.length === undefined ? [files] : files)];
  const headers = { "X-Scrimmage-Token": sessionToken(), "Content-Type": "application/octet-stream" };
  if (list.length === 1) headers["X-Filename"] = encodeURIComponent(list[0].name);
  else headers["X-Files"] = encodeURIComponent(JSON.stringify(list.map((f) => ({ name: f.name, size: f.size }))));
  if (again) headers["X-Import-Again"] = "1";
  try {
    const res = await fetch("/api/part/upload", { method: "POST", body: list.length === 1 ? list[0] : new Blob(list), headers });
    let data;
    try { data = await res.json(); } catch { data = {}; }
    return { status: res.status, data: data && typeof data === "object" ? data : {} };
  } catch {
    return { status: 0, data: { error: "can't reach the editor (is scrimmage edit still running?)" } };
  }
}

// What is wrong with dropping or choosing these files together, in one sentence, or null. Several files are STL or
// OBJ files, read as one part (like a folder of them); a STEP file goes on its own.
export function cadFilesProblem(files) {
  const list = [...files];
  if (!list.length) return "No file came with that: drop or choose a .step, .stp, .stl or .obj file.";
  const bad = list.find((f) => !CAD_FILE.test(f.name));
  if (bad) return `${bad.name} isn't a CAD file: use a .step, .stp, .stl or .obj file.`;
  if (list.length === 1) return null;
  const steps = list.filter((f) => /\.(step|stp)$/i.test(f.name)).length;
  if (steps === list.length) return "Use one STEP file at a time: a STEP file already holds the whole assembly.";
  if (steps) return "Use a STEP file on its own, or only STL and OBJ files, not both at once.";
  return null;
}
export const filesName = (files) => (files.length === 1 ? files[0].name : `${files.length} files`);
const slash = (p) => p.replace(/\/+$/, "").lastIndexOf("/");
const base = (p) => p.slice(slash(p) + 1);
const dirOf = (p) => (slash(p) > 0 ? p.slice(0, slash(p)) : "/");

// Reload with a link that has no tab in it, so the new document opens on its own first tab.
function reload() {
  location.hash = `token=${encodeURIComponent(sessionToken())}`;
  location.reload();
}

async function post(path, body) {
  const { status, data } = await api("POST", path, body);
  if (status === 200 && data.ok) { reload(); return true; }
  const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
  notice(status === 409 ? `Can't switch right now: ${why}` : why, "error", "start");
  return false;
}
const open = (path) => post("/api/app/open", { path });

// A folder's listing from /api/app/browse in `box`: folders (a click goes in), then its .toml files when `onFile`
// is given (a click calls it with the entry {name, path, kind}; `kinds` lists the kinds shown, default all).
// `folderButton` {key, label, title, onclick} adds a button beside each folder (data-<key> is the folder's name).
// Also used by the Bench tab's CAD part picker.
export async function browseList(box, dir, opts = {}) {
  const { onFile = null, folderButton = null, noticeId = "start", kinds = null } = opts;
  const { status: st, data } = await api("GET", "/api/app/browse" + (dir ? `?dir=${encodeURIComponent(dir)}` : ""));
  if (st !== 200) { notice((data.errors && data.errors[0].message) || "Can't list that folder.", "error", noticeId); return null; }
  const list = h("ul", { class: "start-list" });
  if (data.parent) {
    list.append(h("li", {}, h("button", { type: "button", class: "start-item", dataset: { entry: ".." }, onclick: () => browseList(box, data.parent, opts) }, "↑ Up one folder")));
  }
  const shown = data.entries.filter((e) => e.kind === "folder" || (onFile && (!kinds || kinds.includes(e.kind))));
  for (const e of shown) {
    const folder = e.kind === "folder";
    list.append(h("li", {},
      h("button", { type: "button", class: "start-item", dataset: { entry: e.name }, title: e.path,
        onclick: () => (folder ? browseList(box, e.path, opts) : onFile(e)) },
        h("span", { class: "start-kind", text: folder ? "Folder" : KIND[e.kind] || e.kind }), h("strong", { text: e.name })),
      folder && folderButton ? h("button", { type: "button", class: "btn btn-small", dataset: { [folderButton.key]: e.name }, title: folderButton.title, onclick: () => folderButton.onclick(e) }, folderButton.label) : null));
  }
  if (!shown.length) list.append(h("li", { class: "start-hint", text: !onFile ? "No folders here." : kinds && kinds.includes("cad") ? "No folders or CAD files here." : "No folders or .toml files here." }));
  box.replaceChildren(h("p", { class: "start-hint", text: data.dir }), list);
  return data;
}

// cadFor: a robot config's path (the Robot tab's "Import CAD…"): "New part from CAD" opens at once, and the part goes in
// parts/ next to that robot config.
export async function showStart(root, { canGoBack, onBack, cadFor = null }) {
  root.replaceChildren(h("p", { class: "start-hint", text: "Loading…" }));   // not the last visit's answers
  const { status, data: app } = await api("GET", "/api/app/state");
  if (status !== 200) { notice("Couldn't load the start page.", "error"); return; }

  const newBox = h("div", { id: "start-new" });
  const browseBox = h("div", { id: "start-browse" });
  const recent = h("ul", { class: "start-list", id: "start-recent" });

  if (!app.recent.length) recent.append(h("li", { class: "start-hint", text: "Nothing yet. Make something new or browse for a file." }));
  for (const r of app.recent) {
    recent.append(h("li", {},
      h("button", { type: "button", class: "start-item", dataset: { recent: r.path }, title: r.path, onclick: () => open(r.path) },
        h("span", { class: "start-kind", text: KIND[r.kind] || r.kind }),
        h("strong", { text: base(r.path) }),
        h("span", { class: "start-dir", text: `in ${dirOf(r.path)}` }))));
  }

  const browse = (dir) => browseList(browseBox, dir, { onFile: (e) => open(e.path),
    folderButton: { key: "open", label: "Open as idea session", title: "Open this folder as an idea session", onclick: (e) => open(e.path) } });

  async function robotChoices() {
    const out = new Map();
    const { status: st, data } = await api("GET", "/api/app/browse");
    if (st === 200) for (const e of data.entries) if (e.kind === "robot") out.set(e.path, e.name);
    for (const r of app.recent) if (r.kind === "robot" && !out.has(r.path)) out.set(r.path, `${base(r.path)}  (in ${dirOf(r.path)})`);
    return out;
  }

  // New part from CAD: Browse (the file is used where it is) or drop a file (it is copied in next to the import)
  function cadForm(robot = null, forTest = false) {
    const list = h("div", { id: "start-cad-browse", class: "start-folder-browse" });
    const pick = (path) => post("/api/app/new", { kind: "import", path, ...(robot ? { robot } : {}) });
    const send = async (fileList) => {
      const files = [...(fileList || [])];
      const problem = cadFilesProblem(files);
      if (problem) { notice(problem, "error", "start"); return; }
      drop.classList.add("busy");
      drop.querySelector("p").textContent = `Reading ${filesName(files)}…`;
      const { status, data } = await uploadCad(files);
      if (status === 200) { reload(); return; }
      drop.classList.remove("busy");
      drop.querySelector("p").textContent = dropText;
      const why = (data.errors && data.errors[0] && data.errors[0].message) || data.error || `the server answered ${status}`;
      notice(status === 409 ? `Can't switch right now: ${why}` : `Couldn't read ${filesName(files)}: ${why}`, "error", "start");
    };
    const dropText = "Drop a .step file here, or several .stl or .obj files";
    const chooser = h("input", { type: "file", id: "start-cad-file", accept: ".step,.stp,.stl,.obj", multiple: true, hidden: true, "aria-label": "CAD files",
      onchange: (e) => send(e.target.files) });
    const drop = h("div", { id: "start-drop", class: "start-drop" },
      h("p", { text: dropText }),
      h("button", { type: "button", class: "btn btn-small", onclick: () => chooser.click() }, "Choose a file…"), chooser);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; drop.classList.add("drop-here"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("drop-here"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drop-here"); send(e.dataTransfer.files); });
    newBox.replaceChildren(h("div", { class: "start-form start-cad" },
      h("p", { class: "start-hint", text: "Export your robot mechanism from your CAD program as a STEP file (or STL files), then pick it here. "
        + "Scrimmage reads it, guesses what is what, and shows you what is left to check." }),
      robot ? h("p", { class: "start-hint", id: "start-cad-for", text: `The parts go in the parts folder next to ${base(robot)}. `
        + "When you're done, click \"Back to the robot\"." }) : null,
      forTest ? h("p", { class: "start-hint", id: "start-cad-test", text: "The Part tab opens on it. When its parts are up to date, "
        + "click \"Test it\" at the bottom: your part goes on a stand-in robot with a test to start from." }) : null,
      drop,
      h("div", { class: "start-buttons" },
        h("button", { type: "button", class: "btn", id: "start-cad-browse-btn", onclick: () => browseList(list, null, { onFile: (e) => (e.kind === "import" ? open(e.path) : pick(e.path)), kinds: ["cad", "import"],
          folderButton: { key: "stl", label: "Use its STL files", title: "Make one part from the STL or OBJ files in this folder", onclick: (e) => pick(e.path) } }) }, "Browse…"),
        h("button", { type: "button", class: "btn", onclick: () => newBox.replaceChildren() }, "Cancel")),
      list));
  }

  // Test a mechanism: a robot file gets a new test with starter blocks; a CAD file goes through the Part tab first
  async function testForm() {
    const choices = await robotChoices();
    const robot = h("select", { id: "start-test-robot" }, ...[...choices].map(([p, n]) => h("option", { value: p }, n)));
    newBox.replaceChildren(h("form", { class: "start-form", novalidate: true, onsubmit: (e) => e.preventDefault() },
      h("p", { class: "start-hint", text: "Try a mechanism with blocks: spin its motors, wait, check what happened, and watch the replay. "
        + "Start from your CAD, or from a robot file you already have." }),
      h("div", { class: "start-buttons" },
        h("button", { type: "button", class: "btn btn-primary", id: "start-test-cad", onclick: () => cadForm(null, true) }, "From a CAD file…")),
      choices.size ? h("div", {}, h("label", { for: "start-test-robot", text: "Or test a robot file" }),
        h("div", { class: "row" }, robot,
          h("button", { type: "button", class: "btn", id: "start-test-go", onclick: () => post("/api/test/new", { robot: robot.value }) }, "Make the test")))
        : h("p", { class: "start-hint", text: "There is no robot file here yet: start from a CAD file." }),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "btn", onclick: () => newBox.replaceChildren() }, "Cancel"))));
    document.getElementById("start-test-cad").focus();
  }

  async function newForm(kind) {
    const name = h("input", { id: "start-name", type: "text", autocomplete: "off" });
    const rows = [];
    let template = null, robot = null, help = "";
    if (kind === "robot") {
      template = h("select", { id: "start-template" },
        h("option", { value: "swerve" }, "Swerve drive (four modules)"),
        h("option", { value: "swerve-rev" }, "Swerve drive with REV motors"),
        h("option", { value: "differential" }, "Tank drive (two sides)"));
      rows.push(h("div", {}, h("label", { for: "start-template", text: "Start from" }), template));
      help = "A file name such as mybot (.toml is added).";
    } else if (kind === "scenario") {
      const choices = await robotChoices();
      robot = h("select", { id: "start-robot" }, ...[...choices].map(([p, n]) => h("option", { value: p }, n)));
      if (!choices.size) rows.push(h("p", { class: "start-hint", text: "There is no robot file here yet. Make a new robot first, or open one." }));
      else rows.push(h("div", {}, h("label", { for: "start-robot", text: "Which robot?" }), robot));
      help = "A file name such as practice (.toml is added).";
    } else help = "A folder name such as kickoff. It is made here and holds your ideas.";
    rows.push(h("div", {}, h("label", { for: "start-name", text: kind === "session" ? "Folder name" : "File name" }), name, h("p", { class: "start-hint", text: help })));
    const create = () => {
      const n = name.value.trim();
      if (!n) { notice("Type a name first.", "warn", "start"); name.focus(); return; }
      const body = { kind, path: n };
      if (template) body.template = template.value;
      if (kind === "scenario") {
        if (!robot.value) { notice("Pick a robot first.", "warn", "start"); return; }
        body.robot = robot.value;
      }
      post("/api/app/new", body);
    };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); create(); } });
    newBox.replaceChildren(h("form", { class: "start-form", novalidate: true, onsubmit: (e) => e.preventDefault() }, ...rows,
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "btn", onclick: () => newBox.replaceChildren() }, "Cancel"),
        h("button", { type: "button", class: "btn btn-primary", id: "start-create", onclick: create }, "Create"))));
    name.focus();
  }

  const folders = await teamFolders(app, { canGoBack, onBack, again: () => showStart(root, { canGoBack, onBack }) });

  root.replaceChildren(h("div", { class: "start-inner" },
    h("div", { class: "start-buttons" },
      h("h1", { text: "Start" }),
      canGoBack ? h("button", { type: "button", class: "btn", id: "start-back", onclick: onBack }, "Back to what I was doing") : null),
    folders.ask,
    h("section", { class: "start-card" }, h("h2", { text: "Recent" }), recent),
    h("section", { class: "start-card" }, h("h2", { text: "Make something new" }),
      h("div", { class: "start-buttons" },
        h("button", { type: "button", class: "btn", dataset: { key: "new-robot" }, onclick: () => newForm("robot") }, "New robot"),
        h("button", { type: "button", class: "btn", dataset: { key: "new-scenario" }, onclick: () => newForm("scenario") }, "New scenario"),
        h("button", { type: "button", class: "btn", dataset: { key: "new-session" }, onclick: () => newForm("session") }, "New idea session"),
        h("button", { type: "button", class: "btn", dataset: { key: "new-part" }, onclick: () => cadForm(cadFor) }, "New part from CAD"),
        h("button", { type: "button", class: "btn", dataset: { key: "test-mechanism" }, onclick: () => testForm() }, "Test a mechanism")),
      newBox),
    h("section", { class: "start-card" }, h("h2", { text: "Open a file or folder" }),
      h("div", { class: "start-buttons" }, h("button", { type: "button", class: "btn", dataset: { key: "browse" }, onclick: () => browse(null) }, "Browse…")),
      browseBox),
    folders.card));
  if (cadFor) {
    cadForm(cadFor);
    newBox.scrollIntoView({ block: "nearest" });
  }
}

// -- team folders ---------------------------------------------------------------------------------------------------
// Each computer knows where the team's CAD folder and code folder are ({cad} and {code} in robot files; see
// scrimmage/teamfolders.py). When the open robot names one this computer hasn't set (app.needs_folder), the page
// asks for it at the top; the "This computer's team folders" card shows and changes both.
const TEAM = {
  cad: { name: "CAD folder", what: "CAD", why: "Robot files and part folders the CAD students make live here." },
  code: { name: "code folder", what: "code", why: "The robot code (the programmers' projects) lives here." },
};

async function setFolder(name, path) {
  const { status, data } = await api("POST", "/api/app/folders", { name, path });
  if (status === 200 && data.ok) return true;
  notice((data.errors && data.errors[0] && data.errors[0].message) || `the server answered ${status}`, "error", "start");
  return false;
}

// A folder browser in `box` whose folders each have a `label` button; also one for the folder shown.
async function folderBrowser(box, dir, label, key, pick) {
  const data = await browseList(box, dir, { folderButton: { key, label, title: "Use this folder", onclick: (e) => pick(e.path) } });
  if (data) box.append(h("div", { class: "start-buttons" }, h("button", { type: "button", class: "btn btn-small", dataset: { [`${key}Here`]: "1" }, onclick: () => pick(data.dir) }, "Use the folder shown")));
}

async function teamFolders(app, { canGoBack, onBack, again }) {
  const { status, data } = await api("GET", "/api/app/folders");
  const known = status === 200 ? data : { cad: null, code: null, from_env: { cad: false, code: false } };
  let ask = null;
  const need = app.needs_folder;
  if (need && TEAM[need]) {
    const t = TEAM[need];
    const box = h("div", { class: "start-folder-browse" });
    const pick = async (path) => {
      if (!(await setFolder(need, path))) return;
      notice(`Saved: this computer's ${t.name} is ${path}.`, "info", "start");
      const { status: st2, data: now } = await api("GET", "/api/app/state");
      if (canGoBack && st2 === 200 && !now.needs_folder) onBack();
      else again();   // the robot needs the other folder too (or this is the start page itself)
    };
    ask = h("section", { class: "start-card start-ask", id: "start-folder-ask", "aria-labelledby": "start-folder-ask-title" },
      h("h2", { id: "start-folder-ask-title", text: `Where is your team's ${t.what} on this computer?` }),
      h("p", { text: `The open robot file points into the team's ${t.name} ({${need}}), and this computer doesn't know where that is yet. `
        + "Pick it once; scrimmage remembers it on this computer. Until then the robot can't be edited." }),
      h("p", { class: "start-hint", text: t.why }),
      h("div", { class: "start-buttons" }, h("button", { type: "button", class: "btn btn-primary", id: "start-folder-ask-browse",
        onclick: () => folderBrowser(box, known[need] || null, "This is it", "folder", pick) }, "Browse…")),
      box);
  }
  const rows = Object.entries(TEAM).map(([name, t]) => {
    const box = h("div", { class: "start-folder-browse" });
    const pick = async (path) => { if (await setFolder(name, path)) again(); };
    const env = known.from_env && known.from_env[name];
    return h("li", { class: "start-folder", dataset: { team: name } },
      h("div", { class: "start-folder-line" },
        h("strong", { text: `${t.name[0].toUpperCase()}${t.name.slice(1)}: ` }),
        known[name] ? h("span", { class: "mono", text: known[name] }) : h("span", { class: "muted", text: "Not set" }),
        env ? h("span", { class: "start-hint", text: ` (set by SCRIMMAGE_${name.toUpperCase()}, which wins)` }) : null,
        env ? null : h("button", { type: "button", class: "btn btn-small", dataset: { teamBrowse: name },
          onclick: () => folderBrowser(box, known[name] || null, "Use this one", "teamUse", pick) }, known[name] ? "Change…" : "Browse…"),
        known[name] && !env ? h("button", { type: "button", class: "btn btn-small btn-ghost", dataset: { teamForget: name },
          onclick: () => pick(null) }, "Forget") : null),
      h("p", { class: "start-hint", text: t.why }),
      box);
  });
  const card = h("section", { class: "start-card", id: "start-folders", "aria-labelledby": "start-folders-title" },
    h("h2", { id: "start-folders-title", text: "This computer's team folders" }),
    h("p", { class: "start-hint", text: "Robot files say \"in the CAD folder\" or \"in the code folder\" instead of a path on one laptop, "
      + "so the same file works on every computer. Each computer sets where those folders are: a shared drive, a flash drive or a git checkout." }),
    h("ul", { class: "start-list start-folders" }, ...rows));
  return { ask, card };
}
