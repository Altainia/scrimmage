# Robot Assembler GUI (sub-project 5a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scrimmage assemble` opens a local browser app where a student assembles a `[[part]]` robot config
visually (library, attach, parameters, IDs, live 3D view), with every edit validated by the real assembler and
written to the TOML with comments preserved.

**Architecture:** `scrimmage/assembler/` — a Python core (tomlkit document + pure edit functions + validation
through `load_config_text`/`build_world` + a model view from the compiled MuJoCo model + a stock/folder parts
library) behind a stdlib `ThreadingHTTPServer` JSON API with a session token; a no-build front end
(`static/`, plain ES modules, vendored three.js) that renders state and posts edits.

**Tech Stack:** Python 3.12, tomlkit, MuJoCo 3.15, stdlib http.server; JavaScript ES modules, three.js (vendored,
pinned); pytest, Playwright for Python (dev, browser tests).

**Spec:** docs/superpowers/specs/2026-10-12-assembler-gui-design.md

## Global Constraints

- Linux only. The server binds `127.0.0.1` only; every `/api/*` request needs header `X-Scrimmage-Token: <token>` (403 otherwise); the token is a `secrets.token_urlsafe(24)` passed to the page in the URL fragment (`#token=...`).
- The config file on disk is always valid: write only after `load_config_text` + `build_world` succeed on the candidate text; write atomically (temp file in the same directory + `os.replace`).
- tomlkit preserves comments, ordering and untouched sections; the GUI edits only `[[part]]` tables.
- Configs using `[model] mjcf`, or invalid when opened, are read-only in the GUI with an explanation.
- New `[[part]]` keys are written in the order: name, stock|path, attach, pos, rot, parameters (declaration order), ids. Parameters equal to their default, and all-zero `pos`/`rot`, are omitted.
- Front end: no build step, no CDN at runtime; three.js vendored under `static/vendor/` with its MIT license; pinned version at least two weeks old (today is 2026-10-12).
- Field errors are `{field, message}` where field is `"<instance>/<key>"` (`key` ∈ `name`, `attach`, `pos`, `rot`, `param:<name>`, `ids:<slot>`, `part`) or `"status"`.
- Python is `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest`. Browser tests are marked `browser` and skip when `playwright` or its Chromium is missing.

## Review Focus

1. An edit to one part must not reformat or drop comments elsewhere in the file (test on all three shipped configs with a byte-level diff outside the edited table). (Task 2.)
2. Renaming a part must rewrite every `attach = "old.<x>"` that points at it, or the config breaks. (Task 2.)
3. A duplicate-device error caused by the edited part must land on that part's ids field, not only the status bar. (Task 3.)
4. The file changing on disk while the page is open must not let an old in-memory document overwrite it. (Task 5.)
5. A non-ASCII part name or comment must survive load → edit → write. (Task 2.)

---

### Task 1: `load_config_text` and the tomlkit dependency

**Files:** Modify `scrimmage/config.py`, `pyproject.toml`; Test `tests/test_config.py`

- [ ] **Step 1: Failing test**

```python
def test_load_config_text_resolves_relative_paths(tmp_path):
    from scrimmage.config import load_config_text, build_world
    (tmp_path / "parts").mkdir()
    import shutil
    shutil.copytree(Path(__file__).parent / "parts" / "intake", tmp_path / "parts" / "intake")
    text = '[[part]]\nname = "drive"\nstock = "chassis"\n[[part]]\nname = "i"\npath = "parts/intake"\nattach = "drive.top"\n'
    cfg = load_config_text(text, tmp_path / "robot.toml")     # the file need not exist
    assert cfg.path == (tmp_path / "robot.toml").resolve()
    assert build_world(cfg).parts.instances[1].name == "i"


def test_load_config_text_reports_toml_errors_with_the_path(tmp_path):
    from scrimmage.config import ConfigError, load_config_text
    with pytest.raises(ConfigError, match=r"robot\.toml: .*"):
        load_config_text("[[part]\n", tmp_path / "robot.toml")
```

- [ ] **Step 2: Implement.** Split `load_config` into reading the file and `load_config_text(text, path)` that
  does the parsing and everything after (same messages, prefixed with the resolved path for TOML errors);
  `load_config` calls it. Add `"tomlkit>=0.12"` to `dependencies`; add `"playwright"` to the `dev` extra.
- [ ] **Step 3: Run** `tests/test_config.py`, then the non-Java suite. **Commit** `"Config: load_config_text; tomlkit dependency"`.

---

### Task 2: Document and edits

**Files:** Create `scrimmage/assembler/__init__.py`, `scrimmage/assembler/doc.py`, `scrimmage/assembler/edits.py`, `tests/test_assembler_edits.py`; Modify `pyproject.toml` (`packages` gains `scrimmage.assembler`; package-data `assembler/static/**/*`)

**Interfaces:**
- `edits.EditError(field: str, message: str)`.
- `edits.parts(doc) -> list[tomlkit table]` (the `part` array of tables, possibly empty).
- `edits.add_part(doc, *, name, stock=None, path=None, attach=None, pos=None, rot=None, params=None, ids=None, defaults: dict | None = None)` — appends a table with keys in the Global-Constraints order; `defaults` (param name → default) lets it omit default-valued parameters.
- `edits.remove_part(doc, name)` — `EditError(f"{name}/part", "…is attached to by a, b; move or delete those first")` when others attach to it.
- `edits.rename_part(doc, old, new)` — validates `[A-Za-z0-9_]+` and uniqueness; rewrites every `attach` whose instance is `old`.
- `edits.set_attach(doc, name, attach, pos, rot)`, `edits.set_params(doc, name, params, defaults)` (merge; `None` value removes a key; default-valued removed), `edits.set_ids(doc, name, ids)` (replace the `ids` table; empty → key removed).
- `doc.ConfigDoc(path)`: `.text` (current text), `.version` (int, bumped on every successful change), `.doc` (tomlkit document), `.read_only_reason: str | None`; `.apply(fn) -> str` returns candidate text from applying `fn(doc_copy)` to a deep copy (does not commit); `.commit(text)` writes atomically, pushes undo, clears redo, bumps version; `.undo()/.redo() -> bool` (write the snapshot, bump version); `.reload_if_changed() -> bool` (mtime + content check; on change: reload, clear history, bump version).

- [ ] **Step 1: Failing tests** (`tests/test_assembler_edits.py`), with a `doc_for(path)` helper that copies a shipped config (and the `parts/` or `examples` it needs — use the configs' own relative paths; copy `configs/` and `examples/` minimal pieces, or rewrite `cwd` to absolute) into tmp:
  - add an `arm` (stock) at `drive.top` to `configs/kitbot.toml`: the new table's keys are in order, default params omitted; every byte outside the appended table is unchanged (compare the prefix of the file).
  - add a folder part (`path = "parts/intake"`) with ids.
  - rename `drive` → `base` in `configs/swerve.toml`: every `attach = "drive.<x>"` becomes `base.<x>`; comments untouched; the renamed name rejected if taken or invalid (`EditError` field `drive/name`).
  - remove refuses while `fl` etc. attach to `drive` (message names them); removing a leaf works.
  - set_params merges, removes defaults and `None`s; set_ids replaces and removes empty.
  - a comment with non-ASCII text and a part named `bras` survive add/edit/write.
  - ConfigDoc: commit writes atomically (no temp file left), undo/redo restore exact texts, version bumps, reload_if_changed detects an external write and clears history.
- [ ] **Step 2: Implement.** Use `tomlkit.parse`, `tomlkit.table()`, `tomlkit.inline_table()`, `tomlkit.aot()`;
  deep copy with `copy.deepcopy` of the document (or re-parse `as_string()`); atomic write via
  `tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` + `os.replace`.
- [ ] **Step 3: Run**, full non-Java suite, **commit** `"Assembler: tomlkit document and edit operations"`.

---

### Task 3: Validation, library and model view

**Files:** Create `scrimmage/assembler/validate.py`, `scrimmage/assembler/library.py`, `scrimmage/assembler/view.py`, `tests/test_assembler_core.py`

**Interfaces:**
- `validate.check(text, path) -> Result(ok: bool, errors: list[FieldError], warnings: list[FieldError], world: World | None)` with `FieldError(field, message)`. Mapping rules (messages come from `scrimmage.parts` and `world.py`):
  - strip a leading `"<config path>: "`;
  - `parts[i] '<name>': ids: <slot> …` or `… ids: no slot '<slot>'` → `<name>/ids:<slot>` (or `<name>/ids` when no slot);
  - `parts[i] '<name>': attach …` → `<name>/attach`; `… unknown parameter '<p>'` or `… <p> must …` where `<p>` is one of the stock part's parameter names → `<name>/param:<p>`; `parts[i] '<name>': name …` → `<name>/name`; other `parts[i] '<name>': …` → `<name>/part`;
  - `<dev> is bound by <kind> <inst>.<slot> and <kind> <inst2>.<slot2>` → two errors, `<inst>/ids:<slot>` and `<inst2>/ids:<slot2>`;
  - warnings `"<inst>.<slot> has no device ID…"` → `<inst>/ids:<slot>`; everything else → `status`.
- `library.stock() -> list[dict]` (name, group: "Drivetrain"|"Mechanisms"|"Chassis", doc, note, params: [{name, kind, unit, default, min, max (number or null), max_depends: bool, choices, doc, positive}], slots, mounts); `library.folders(dirs: list[Path]) -> list[dict]` (name, path relative to the config dir when possible, slots: {slot: kind}, mounts, error or null).
- `view.model_view(world) -> dict`: `{instances: [name], geoms: [{instance, type: "box"|"cylinder"|"sphere"|"capsule"|"ellipsoid"|"mesh"|"plane", size: [3], pos: [3], quat: [4], rgba: [4], mesh: int | null}], meshes: [{vert: base64 float32, face: base64 int32}], mounts: [{name: "<inst>.<mount>", pos, quat}], floor: {pos, size}}` at the start pose (after `mj_forward`), robot geoms only (bodies whose root is the robot root), world positions in meters.

- [ ] **Step 1: Failing tests:** each mapping rule with a real assembler error (build bad configs in tmp: unknown slot, wrong-kind id, bad attach, unknown/out-of-range parameter, duplicate device across two parts, missing id warning); `stock()` lists every `REGISTRY` entry with metadata matching `Param` (spot-check `swerve/MK4i` ratio choices, `chassis` module_inset `max_depends`); `folders()` on `tests/parts` and a broken folder; `model_view` on `configs/swerve.toml`: 5 instances, geoms of `fl` near the front-left corner, mount `drive.front_left` at the chassis corner (compare with the assembler's composed mount pose), and a mesh-bearing part folder (`tests/parts` has none — write a tiny binary STL part in tmp) yields mesh arrays whose vertex count matches `model.mesh_vertnum`.
- [ ] **Step 2: Implement; Step 3: run; commit** `"Assembler: validation with field mapping, parts library, model view"`.

---

### Task 4: Server, API and CLI

**Files:** Create `scrimmage/assembler/server.py`, `scrimmage/assembler/templates.py`, `tests/test_assembler_server.py`; Modify `scrimmage/__main__.py`

**Interfaces:**
- `server.App(config_path, open_browser=True, port=0)`: `.start() -> url` (background thread), `.stop()`. State held: `ConfigDoc`, extra library folders, last validation result.
- API (JSON; all require the token):
  - `GET /api/state?since=N` — returns immediately if `version != N`, else long-polls ≤ 25 s (checking `reload_if_changed()` each second); body: `{version, path, read_only, read_only_reason, parts: [{name, stock, path, attach, pos, rot, params, ids}], errors, warnings, other_sections: str (the TOML text of non-part sections), can_undo, can_redo, mass_kg, bodies, joints}`.
  - `GET /api/view` — `model_view` of the current valid world (or `{}` when read-only/invalid).
  - `GET /api/library` — `{stock, folders}`.
  - `POST /api/edit` — `{op: add|remove|rename|attach|params|ids, ...}` → applies, validates the candidate, commits on success → `{ok: true, version}`; on failure `{ok: false, errors: [FieldError]}` and nothing written. Read-only docs → 409 with the reason.
  - `POST /api/undo`, `POST /api/redo`, `POST /api/folder {path}`.
  - Static files from `scrimmage/assembler/static/` (index at `/`).
- Before applying an edit, call `reload_if_changed()`; if the file changed, reject the edit with 409 "the file changed on disk; the page has reloaded it" (Review Focus 4).
- `templates.new_config(kind: "swerve"|"differential") -> str` — commented TOML matching `configs/swerve.toml` / `configs/kitbot.toml` parts, with a commented placeholder `[robot_program]`.
- CLI: `scrimmage assemble [CONFIG] [--new swerve|differential] [--port N] [--no-browser]` — `--new` writes the template to CONFIG (refuses to overwrite an existing file, exit 2); missing CONFIG without `--new` exits 2 with "create one with --new swerve or --new differential"; prints the URL (`http://127.0.0.1:<port>/#token=<token>`); opens a browser with `webbrowser.open` unless `--no-browser`; Ctrl+C stops.

- [ ] **Step 1: Failing tests** with the real server on port 0: missing/wrong token → 403; `/api/state` content for `configs/kitbot.toml` (copied); an `add` edit writes the file and bumps version; an invalid `ids` edit returns field errors and leaves the file byte-identical; undo/redo; long-poll returns when another thread edits; external file change → reload + 409 on the next stale edit; `[model] mjcf` config (`tests/fake_robot.toml` copy) is read-only; `--new` template validates; CLI `--no-browser` prints a URL and `/api/state` answers (run `main` in a thread, stop it).
- [ ] **Step 2: Implement; Step 3: run; commit** `"Assembler: local server, JSON API, scrimmage assemble"`.

---

### Task 5: Front end: shell, library, tree and inspector

**Files:** Create `scrimmage/assembler/static/index.html`, `app.css`, `app.js`, `library.js`, `tree.js`, `inspector.js`

- `index.html`: three columns (library, 3D view placeholder `<div id="view">`, tree + inspector), a status bar, a collapsed "Other sections (read-only)" panel, a read-only banner. Loads `app.js` as a module.
- `app.js`: reads the token from `location.hash`; `api(method, path, body)` with the header; the long-poll loop on `/api/state?since=`; on new state re-render library/tree/inspector/status and reload `/api/view`; keyboard Ctrl+Z / Ctrl+Shift+Z → undo/redo; shows notices (file reloaded, read-only reasons, edit failures).
- `library.js`: groups, descriptions, "Add" → a dialog: instance name (prefilled unique `arm`, `arm_2`…), attach point (a select of `<inst>.<mount>` and `<inst>.<body>` options built from state + view, or "pick in 3D" which waits for a mount click from `view3d.js` — Task 6 wires the event), then `POST /api/edit {op:"add", stock|path, name, attach}`.
- `tree.js`: parts nested by attach; select syncs with the inspector and (Task 6) the 3D highlight.
- `inspector.js`: forms from library metadata and the part's current values: name (rename on blur/Enter), attach select + pos/rot number fields with ± nudge (10 mm / 5°), params by kind (number with unit + min/max hints, int, select for choices, ratio = select of choices plus "custom number", pair = two numbers, bool = checkbox), ids per slot (text box for short form, "table" toggle → key/value rows, list editor for multi-motor slots); each change posts one edit; field errors from the response or state render under their field (`field` ids from Global Constraints); Delete button.
- Expose `window.__scrimmage = {state, select(name), lastErrors}` for tests.

- [ ] Steps: implement; verify by running `scrimmage assemble` on a tmp copy of `configs/kitbot.toml` with `--no-browser` and driving the page with Playwright (Python) in a scratch script: add an arm, set an id, see errors; take a screenshot and look at it. (Formal browser tests come in Task 7.) **Commit** `"Assembler front end: library, robot tree, inspector"`.

---

### Task 6: Front end: 3D view and picking

**Files:** Create `scrimmage/assembler/static/view3d.js`, `scrimmage/assembler/static/vendor/` (three.js module build + `OrbitControls` + `LICENSE`); Modify `index.html` (importmap `"three": "./vendor/three.module.min.js"`), `app.js`, `library.js`, `tree.js`

- Vendor three.js: download a pinned release from npm (`three@<version>`: `build/three.module.min.js`, `examples/jsm/controls/OrbitControls.js`, `LICENSE`), version at least two weeks old; record the version and sha256 in `vendor/README`.
- `view3d.js`: scene with hemisphere + directional light, the floor patch, geoms built from `/api/view` (box/cylinder/sphere/capsule/ellipsoid via three geometries with MuJoCo size conventions — box half-sizes, cylinder radius + half-length along z, capsule likewise; meshes from base64 arrays), each mesh tagged with its instance; MuJoCo z-up → three: set `camera.up = (0,0,1)` (keep the robot's frame); OrbitControls; raycast hover → highlight instance (emissive); click → select (tree + inspector); mount triads (small `AxesHelper` at each mount pose, toggle button) clickable when the Add dialog is in "pick in 3D" mode; "frame robot" button; re-build on each new view without leaking geometries (dispose).
- Expose `window.__scrimmage.scene` summary for tests: `{instances: [...], meshes: count, mounts: count}`.

- [ ] Steps: implement; verify in a scratch Playwright script (screenshot the view after adding a part; pick a mount triad to attach); look at the screenshots. **Commit** `"Assembler front end: 3D view with picking"`.

---

### Task 7: Browser tests, docs

**Files:** Create `tests/test_assembler_browser.py`, `docs/assembler.md`; Modify `tests/conftest.py` (`browser` marker; skip when `playwright` isn't importable or Chromium isn't installed), `README.md`, `.github/workflows/ci.yml` (Python job: `pip install -e .[dev]` already includes playwright; add `python -m playwright install --with-deps chromium` and let `-m browser` tests run — or set `SCRIMMAGE_SKIP_BROWSER=1` if the job can't, with a comment)

- Tests (spec "Testing", browser): open a tmp copy of `configs/kitbot.toml`; add an `arm` at `drive.top` via the dialog; set `pivot` to `PWM:3`; the file contains it; `window.__scrimmage.scene.instances` contains `arm`; the status warning count changed; an invalid id (`TalonFX`) shows a field error and leaves the file byte-identical; Ctrl+Z undoes; add a second part by clicking a mount triad; screenshot saved to the test's tmp dir; `--new swerve` page loads with 5 instances.
- `docs/assembler.md`: starting it, the three columns, adding/attaching/moving parts, parameters and ids, errors and warnings, undo, read-only cases, what it never touches, keyboard shortcuts, troubleshooting (port in use, browser didn't open → copy the URL).
- README: "Assembling a robot in the browser" with the command and a screenshot (store under `docs/img/assembler.png`, taken by the browser test run with a fixed viewport).
- [ ] Steps: implement, run (`-m browser` here), full suite, **commit** `"Assembler: browser tests and docs"`.
