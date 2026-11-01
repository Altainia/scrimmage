# Robot assembler GUI (sub-project 5a): design

## Context

Robots in Scrimmage are TOML configs built from `[[part]]` entries: stock parts (Python builders with typed
`Param` metadata, listed by `scrimmage parts`) and part folders (`part.toml` + meshes, written by hand or by the
FreeCAD exporter). Students edit these configs in a text editor today. The user wants students, who are
UI-focused, to assemble robots visually; agents keep using MCP and the CLI is kept.

Sub-project 5 was originally "GUI assembler plus scenario editor"; the user scoped this sub-project to the
**robot assembler only**. A scenario editor and a run-and-watch view are later sub-projects; this design should
not block them (the web app can grow pages).

Decisions made with the user:

- Linux only, like scrimmage.
- Approach A: a local web app (`scrimmage assemble`) served from Python, a browser front end with a three.js 3D
  view; edits validated by the real assembler; the config TOML is the single source of truth.

## Goals

1. A student opens or creates a robot config in a browser, adds stock or folder parts, attaches them to mounts or
   bodies (from a list or by clicking a mount in 3D), sets parameters and device IDs, and sees the assembled robot
   update live.
2. The file on disk is always a valid config: every edit is validated by `load_config` + the assembler before it
   is written; invalid edits are rejected with the message shown at the field that caused it.
3. Comments, ordering and non-part sections of the TOML survive GUI edits.
4. The 3D view shows exactly the compiled MuJoCo model Scrimmage simulates.
5. New stock parts and parameters appear in the GUI automatically (forms come from `Param` metadata).

## Non-goals

- Scenario editing, starting robot programs, watching runs, driving with a joystick (later sub-projects).
- Editing `[robot_program]`, `[lockstep]`, `[battery]`, `[telemetry]`, `[field]`, `[session]` (shown read-only).
- Editing part folders' contents (`part.toml`); that is the FreeCAD exporter's or a text editor's job.
- Multiple users, remote access, authentication beyond a local session token.
- Windows/macOS.

## Design

### What the student sees

`scrimmage assemble [CONFIG]` opens a browser tab. Without a config (or with `--new swerve|differential PATH`)
it creates one from a template: a chassis plus four MK4i modules (IDs as in `configs/swerve.toml`), or a chassis
plus a differential (as in `configs/kitbot.toml`), with the `[robot_program]` section commented as a placeholder.

Layout: three columns plus a status bar.

- **Library (left):** stock parts grouped (Drivetrain: chassis, swerve modules, differential; Mechanisms:
  elevator, arm, flywheel, roller), each with its `describe()` text on hover/expand; part folders found in
  `parts/*/part.toml` next to the config, plus folders added with "Add folder…" (remembered for the session).
  "Add" asks for an instance name (prefilled, unique) and an attach point: a list of the robot's mounts and bodies
  grouped by part, or a click on a highlighted mount triad in 3D.
- **3D view (center):** the assembled robot on a carpet patch; orbit/pan/zoom; hover highlights a part instance;
  click selects it (and its tree row); mount frames drawn as small axis triads (toggle); "frame robot" button.
- **Robot tree + inspector (right):** parts nested by attachment. The inspector for the selected instance:
  name (rename), source (stock name or folder), attach target (dropdown) and offset (`pos` meters, `rot` degrees,
  with ± nudge buttons), stock parameters (one field per `Param`: number with unit/min/max, int, choice dropdown,
  ratio choice-or-number, pair, bool), and device slots (one row per slot with its kind; a short-form text box
  validated live, a "table" toggle for advanced keys as key/value rows; list entry for multi-motor slots).
  Delete refuses while other parts attach to the instance and names them. Unbound-slot warnings show beside
  their slot.
- **Status bar:** total robot mass, the number of bodies/joints, warnings and errors in the sim's own words, and
  a "saved" indicator.
- **Read-only panel:** the config's other sections, collapsed, with "edit these in a text editor".

Every valid edit is saved immediately. Ctrl+Z / Ctrl+Shift+Z undo and redo within the session. If the file
changes on disk, the page reloads it with a notice and the undo history is cleared.

### Architecture

`scrimmage/assembler/` (Python):

- `doc.py` — `ConfigDoc`: the config as a tomlkit document plus its path; `text()`, `version`; `apply(edit)` →
  candidate text; undo/redo stacks of document snapshots (bounded, e.g. 100); `reload_if_changed()` by mtime.
- `edits.py` — edit operations as pure functions on a tomlkit document: `add_part(source, name, attach, pos,
  rot)`, `remove_part(name)`, `rename_part(old, new)` (also rewrites other parts' `attach = "old.x"`),
  `set_attach(name, attach, pos, rot)`, `set_params(name, params)`, `set_ids(name, ids)`. Each raises
  `EditError(field, message)` for structural problems (unknown part, name taken, delete with dependants).
- `validate.py` — runs a candidate text through `load_config` and `build_world` (a small addition to
  `scrimmage/config.py`: `load_config_text(text, path)`, which `load_config` then uses, so relative paths resolve
  against the config's directory without writing the file); returns `(ok, errors, warnings, world)`. Errors are mapped to fields: messages carrying
  `parts[i] 'name'` and a key (`ids: slot`, a parameter name, `attach`) are attached to that instance's field;
  others are shown in the status bar.
- `view.py` — from the World's compiled model at its start pose: for each robot geom (bodies under the root free
  body) its type, size, world pose, rgba, instance name (from the `<instance>.` prefix) and, for meshes, vertices
  and faces (base64 float32/int32); mount frames (world pose, `<instance>.<mount>` name) composed from body poses
  and `Part.mounts`; the field's carpet patch extents.
- `library.py` — stock parts from `REGISTRY` with `Param` metadata (name, kind, unit, default, min, max — numbers
  only, callable limits reported as "depends on other parameters" — choices, doc), slots and mounts; folder parts
  from scanning `parts/*/part.toml` (name, slots, mounts, or the load error).
- `server.py` — `ThreadingHTTPServer` on `127.0.0.1`, random port by default; a random session token required
  in a header (`X-Scrimmage-Token`) on every API call, passed to the page in the opened URL's fragment; static files
  from `static/`; JSON API:
  `GET /api/state?since=N` (long-poll up to 25 s: document version, config path, part tree with sources,
  attachments, params, ids, warnings/errors, read-only sections), `GET /api/view` (the model view),
  `GET /api/library`, `POST /api/edit` (`{op, ...}` → `{ok, version}` or `{ok: false, field, message}`),
  `POST /api/undo`, `POST /api/redo`, `POST /api/folder` (add a parts folder).
  A single lock serializes edits.
- CLI in `__main__.py`: `scrimmage assemble [CONFIG] [--new swerve|differential] [--port N] [--no-browser]`;
  prints the URL; Ctrl+C stops it. A missing config without `--new` exits 2 with a hint.

`scrimmage/assembler/static/` (no build step): `index.html`, `app.css`, `app.js` (state, API, long-poll,
undo shortcuts), `view3d.js` (three.js scene from `/api/view`, picking by instance, mount triads, highlight),
`tree.js`, `inspector.js` (forms generated from library metadata), `library.js`, and
`vendor/three.module.min.js` (+ `OrbitControls`) — a pinned three.js release (MIT, license file alongside),
so the page works offline. Logic that can live in Python does; the JS stays thin.

Dependencies: `tomlkit` (runtime). Dev: `playwright` (Python) for browser tests; its browsers install with
`playwright install chromium` (tests skip when unavailable).

### Edits and the TOML

- New `[[part]]` tables are appended after the last existing `[[part]]` (or after `[robot]` if none), with keys
  in the order name, stock/path, attach, pos, rot, parameters, ids.
- `ids` short forms are written as strings; table and list forms as inline tables/arrays.
- Parameters equal to their default are removed rather than written.
- `pos`/`rot` all zeros are removed.
- The `[model] mjcf` style is not supported by the GUI: opening such a config shows "this robot uses a
  hand-written model; the assembler edits [[part]] robots" and stays read-only.

## Failure handling

| Situation | Behavior |
|---|---|
| Edit makes the config invalid | not written; field-level message (or status bar if not mappable); the page keeps the previous valid state |
| Structural edit error (name taken, delete with dependants) | `EditError` shown at the field |
| Config invalid when opened (hand-edited) | opened read-only with the errors listed; "fix in a text editor; the page reloads when you save" |
| Hand-written `[model] mjcf` config | read-only notice (above) |
| File changed on disk | reload, notice, undo history cleared; if the new text is invalid, read-only as above |
| Missing token / wrong token | 403 |
| Port in use (with `--port`) | exit 2 naming the port |
| Browser can't be opened | the URL is printed; the server keeps running |

## Testing

- Unit tests for each edit (including comment and order preservation on `configs/swerve.toml`,
  `configs/kitbot.toml`, `configs/elevator_arm.toml`; rename rewriting attaches; delete refusal; default-param
  removal), undo/redo, reload on change, error-to-field mapping, `view` (geom count, an instance's geoms, mount
  frames match the assembler's mount poses, mesh arrays for an exported part folder), `library` (every stock
  part with metadata; folder parts; a broken folder's error).
- API tests through the real server on a random port (token enforcement, long-poll returns on change, edit
  round trip writes the file atomically).
- Browser tests (pytest + Playwright, marked `browser`, skipped when Playwright or its Chromium is missing):
  open `configs/kitbot.toml` copied to tmp; add an `arm` at `drive.top` via the dialog; set its `pivot` id; assert
  the file contains it, the 3D scene has the new instance (exposed via a `window.__scrimmage` test hook), and the
  status bar warning count changed; enter an invalid id and see the field error with the file unchanged; undo;
  click a mount triad to attach; screenshot saved for review.
- `scrimmage assemble --no-browser` smoke test (prints a URL; `/api/state` answers).

## Docs

- README: "Assembling a robot in the browser" (command, screenshot, what it edits and what it leaves alone).
- `docs/assembler.md`: walkthrough, keyboard shortcuts, how errors are shown, the read-only cases.
