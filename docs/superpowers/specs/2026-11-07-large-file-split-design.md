# Large-file split: design

The owner asked on 2026-11-06 to "schedule breaking such a large file up for later". This is that project: split the
nine largest files by responsibility, without changing what any of them does.

## Goal and limits

- **Goal.** Each file has one clear job, and most land around 300–600 lines, so a person or an agent can hold one
  in mind while changing it.
- **Behavior unchanged.** No user-visible change: the same HTTP API, the same pages, the same machine text, and the
  same messages in every language. Code moves; it isn't rewritten. Any change that isn't a pure move (for example
  turning a module `let` into a field of an exported object) is called out in that task's report.
- **Non-goals.** No renames of public names, no new features, no clean-ups beyond what the move needs. The other
  files over ~900 lines (`__main__.py`, `bench/spec.py`, `testrange/shooter.py`, `checks.py`, `blocks_defs.js`,
  `session.py`, `scenario_runner.py`, `drive.js`) are left alone. `session.py` is patched by many tests, so
  splitting it is risky.
- **Green after every task.** Each task ends with the full suite (`.venv/bin/python -m pytest -q tests/`), browser
  tests included, because the gates scan the whole tree.

| File | Lines (2026-11-07) | New modules |
|---|---|---|
| scrimmage/assembler/server.py | 3172 | 9 |
| scrimmage/blocks/run.py | 1474 | 3 |
| scrimmage/assembler/bench_session.py | 1052 | 2 |
| scrimmage/assembler/part_session.py | 890 | 1 |
| static/test.js | 1562 | 4 |
| static/bench.js | 1192 | 3 |
| static/test_run.js | 1079 | 3 |
| static/part_inspector.js | 1061 | 5 |
| static/code.js | 1015 | 2 |

## Decisions

Each one is recorded as a ruling: what was decided, why, and what it costs if it's wrong.

- **D1. Split `App` with mixins.** server.py's `App` becomes `class App(FilesMixin, AttachMixin, RobotMixin,
  CodeMixin, ScenarioMixin, TestMeasureMixin, LiveMixin)`. *Why:* this is the only split that keeps the same `self`
  and the same attributes. Tests patch on the instance (`app.live`, `app._runs_changed`, `app._lock`, `app._httpd`,
  `app._start_problem`), and those patches keep working. The repo had no mixins before. *Cost if wrong:* an
  unfamiliar pattern. A later split into collaborator objects can still start from these files.
- **D2. The API docstring stays in server.py.** `tests/test_bench_tab_docs.py` reads `server.__doc__`, so server.py
  stays at about 900 lines, 597 of them docstring. *Cost:* the file still looks long.
- **D3. Flat modules only.** New Python files go into the existing packages, never a new subpackage, because
  `tests/test_packaging.py` needs every package folder listed by hand. JS files follow the `<tab>_<aspect>.js` naming.
- **D4. Re-export from the old path.** Every moved name that a test or another module imports stays importable from
  its old module. Re-exports are banned where they would make a cycle (`BenchJobs`) or where the name is patched.
  For a patched name, the code that reads it stays in the patched module (list below).
- **D5. One branch, one PR, one commit per task** (server.py: one commit per sub-step). *Why:* every task is a pure
  move with a full-suite run behind it, so one review of the whole branch at the end is enough. *Cost:* a large PR.
  Each commit stands on its own, so a bad one can be reverted by itself.

## Rules every task follows

### Patched names: their readers stay put

Patching a re-exported copy of a name doesn't change what the new module uses.

- `blocks.run.TIP_DEG`, `TURN_RATE_TOL` and `STEER_TIMEOUT` (imported into run.py from `.drive`). Tests patch them
  in test_blocks_feeds:287, test_blocks_drive:152 and test_blocks_drive:205. `_play`, `_waited`, `_steer_turn`, the
  constants and `from .drive import STEER_TIMEOUT` stay in run.py.
- `part_session.load_source` (test_part_server:415). Its callers, `_read` and reimport, stay in part_session.py.
- `scrimmage.bench.run.run_bench`. bench_jobs calls it as `bench_run.run_bench(...)` through
  `from ..bench import run as bench_run`, never `from ..bench.run import run_bench`.
- `robotcode.run_fix`, `checklist` and `_stream` (test_robot_code_server:153, 154, 194, 267). The mixins keep
  `from .. import robotcode` and call `robotcode.x(...)`. Use the same import style for `settings` and
  `part_session`.

### Gates that scan source by file name

- **test_i18n_sweep ALLOWED** is keyed `(file, text)`, and `test_every_allowed_entry_is_still_needed` fails on a
  stale key. Re-key each entry whose string moves. If a string ends up in two files, it needs an entry in each.
- **test_units_sweep.** Its Python scan covers only the files in the hand-written `PY` list. Add every new module
  split from `blocks/run.py`, `assembler/server.py` and `assembler/bench_session.py`. Re-key its ALLOWED entries
  by file too.
- **test_i18n_calls / i18n_tool.**
  - A JS file's `t()` and `tn()` calls are checked, and put in the translation template, only when that file
    imports them straight from `./i18n.js`. **Every new JS file imports t and tn from ./i18n.js itself**, never
    through app.js or another module.
  - `translated(NAME)` is checked only when `NAME` is an `N_()` constant in the same module. These stay together:
    `NO_*` and `need_*` in server.py, `DEFAULT_GRIP_NOTE` with `default_grip_note()`, and `PNEUMATIC_ESTIMATES`
    with `pneumatic_note()`.
- **test_i18n_import_time** fails on a `t()` call at module level.
- **test_theme_browser** allowlists `("test_run.js", 'return "#ffffff"')`. That line, in `behind()`, stays in
  test_run.js.
- **test_assembler_server** checks that `'media: "vendor/blockly-media/"'` is in test.js, so `inject()` stays there.
- **docs/translating.md** names "blocks/run.py's `_w`". Update it when `_w` moves.

### JS module rules

- Pages are native ES modules with no bundler. When the core and a satellite import each other, neither may call
  into the other at module level, and anything the core needs at module level must be a hoisted
  `function` declaration.
- An exported `let` is read-only to importers. Module state written from more than one place stays in the core:
  bench.js's `busy`, `asking`, `ownRun`, `applySeq` and `loadedSeq`; part_inspector.js's motor `draft`.
- The core keeps every name other files import:
  - code.js: `pickStart`, `showChecks` and `showDevices`, which drive.js and report.js import dynamically;
  - bench.js: the whole bench_edit.js import list;
  - test_run.js: `sweepShown` and `spreadShown`.
- The test hooks (`window.__scrimmageTest`, `__scrimmageBench`, `__scrimmagePart`) stay in each tab's init function.

## Per-file design

This section comes from a survey of the tree at b8e9b5e on 2026-11-07. Each task reads its file and finds the
exact groups itself.

1. **part_session.py → part_files.py** (lines 111–291: the import and upload helpers `source_text`, `_taken`,
   `free_import`, `import_for_source`, `is_part_target`, `MESH_FOLDER`, `upload_*`, `_copy_in`, `save_upload(s)`).
   - `open_part` stays wherever its imports stay acyclic.
   - part_files never imports part_session. part_session re-exports every moved public name.
   - plugin_session's private imports stay as they are.
2. **bench_session.py → bench_results.py** (the result-file helpers and `RESULT_SUFFIXES`, `TABLE_*`) **and
   bench_jobs.py** (`KEEP_JOBS`, `_Stopped`, `_plain`, `BenchJobs`, `_*_job`).
   - server.py imports `BenchJobs` from bench_jobs. bench_session doesn't re-export it.
   - bench_session re-exports `_summary`, `_result_files` and `_energy_failed`. `SessionError` stays in
     bench_session.
   - Three i18n keys move to bench_jobs.py.
3. **blocks/run.py → run_words.py, run_setup.py, run_notes.py.**
   - **run_words.py:** `_VERBS`, `_Words`, `_w`, `_v`, `_ws`, `WORDED_CHECK`, `_keep_words`, `_where`, `_recast`, and
     a `_FailureWords` mixin holding TestRun's failure-wording methods.
   - **run_setup.py:** `sensor_value`, `setup_config`, `setup_partner`, `_range_setup`, `world_for`,
     `partner_overlap`, `setup_partner_words`.
   - **run_notes.py:** the grip, pneumatic, piece and run notes, and `goal_lines`, `clearance_lines`, `air_lines`.
   - **run.py keeps:** the constants, `RunResult`, `_Frame`, `_End`, `_Air`, `_aims` (palette.py imports it lazily),
     `check_problems`, `turn_tolerance`, and `TestRun(_FailureWords)` with its playing and block methods.
   - The new modules never import `.run`. `RunResult` is imported only under `TYPE_CHECKING`.
   - Every name imported from `scrimmage.blocks.run` today stays importable from there.
   - Units keys are re-keyed. All three new files go in `PY`.
4. **code.js → code_devices.js** (the device check and battery) **and code_start.js** (the start picture and
   picker). code.js re-exports `pickStart`.
5. **part_inspector.js → part_helpers.js, part_bodies.js, part_where.js** (the frame step; part_frame.js already
   exists as frame math) **, part_motion.js** (joint and slots together, because they share `draft`) **and
   part_hardware.js** (mounts, collision, grip, links).
   - part_inspector.js becomes side-effect imports, in this exact order: bodies, where, motion, hardware.
   - The order matters because `addSection` order is display order, and `tryall` registers `{before: "pieces"}`.
6. **test_run.js → test_run_state.js** (`rp` and small pure helpers; a leaf) **, test_run_chart.js and
   test_run_sweep.js** (sweep and spread).
7. **bench.js → bench_fields.js** (units helpers, vectors, field rows, change panel, with `redrawing`) **,
   bench_stage.js** (pictures, table, stage, strip) **and bench_dialogs.js.**
8. **test.js → test_state.js** (`T` and small helpers; a leaf) **, test_setup.js, test_floor.js** (floor spread,
   station boxes, leftovers, grip, hints) **and test_dialogs.js** (block settings, sweep).
   - Blockly, saving, state, problems, render and boot stay in test.js.
9. **server.py**, in sub-steps:
   - **J1 server_common.py:** `ApiError` and the module helpers, including `classify`, `remember_opened`,
     `unset_folder`, `start_sentence`, `_not_built(why)` and `_info_json`.
     - server.py re-exports `App`, `classify`, `remember_opened` and `ApiError`.
     - bench_session's lazy `classify` import points at server_common.
   - **J2 http_handler.py:** `_Bounded`, `_Handler`, `STATIC`, `MAX_BODY`, `_TYPES`, `_FORM`.
   - **J3–J9:** one mixin per commit:
     - app_files, app_attach, app_robot, app_code, app_scenario, app_tests, app_live.
     - Where `self._not_built()` and the helper `_not_built(why)` share a name, keep both as they are.
     - `'bad edit: {}'` gets an i18n entry in both app_robot and app_scenario.
     - A one-off check confirms that no method name appears in two mixins.

## Who builds it

The build is subagent-driven, one task per file in the order above, lowest risk first. Each task ends with a
review and the full suite. A final review of the whole branch comes before the PR. The PR merges on green.
