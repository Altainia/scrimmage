# Large-file split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** split nine large files by responsibility, behavior unchanged.

**Architecture:** pure moves into flat sibling modules. Moved names are re-exported from their old paths, except
where the spec bans it. App is split into mixins, and the JS pages into `<tab>_<aspect>.js` satellites.

**Tech Stack:** Python 3.11+, native ES modules (no bundler), pytest with Playwright browser tests.

**Spec:** docs/superpowers/specs/2026-11-07-large-file-split-design.md

## Global Constraints

- **No behavior change:** the same API, pages, machine text and messages. Anything that isn't a pure move goes in
  the task report.
- **Commands:**
  - Never `uv run` in the repo root. Run the suite with `.venv/bin/python -m pytest -q tests/`.
  - Don't run `git checkout`, `git switch` or `git stash`.
  - Don't push.
- **Python:** new modules are flat files in the existing packages, never new subpackages.
- **JS:**
  - Every new JS file imports `t` and `tn` straight from `./i18n.js`.
  - Don't call across a core ↔ satellite import cycle at module level.
- **Re-exports and patched names:**
  - Every name imported from the old path today stays importable from there, except `BenchJobs`.
  - Patched names keep their readers in the patched module: `TIP_DEG`, `TURN_RATE_TOL`, `STEER_TIMEOUT`,
    `load_source`.
  - Patched functions are called through their module attribute: `bench_run.run_bench`, `robotcode.*`.
- **Gates:** keep test_i18n_sweep, test_units_sweep, test_i18n_calls, test_i18n_import_time, test_i18n_typed,
  test_theme_browser, test_assembler_server, test_bench_tab_docs and test_packaging green.
  - Re-key ALLOWED entries whose strings move.
  - Add new modules to the units `PY` list where the spec says.
- **Commits:** one per task, with the message `split: <file>` and the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Server sub-steps are one commit each.
- **Done means:** the full suite passes, and the task report lists each new file's line count.

## Review Focus

1. **A patched name read from a moved copy.** The test still passes but tests nothing. Check that each patch
   target's readers are in the patched module.
2. **A JS string dropping out of the catalog.** A file that gets `t` through another module is silently skipped.
   Check the import in each new file.
3. **Part inspector section order.** Registration order is display order. Compare the section ids in order before
   and after.
4. **A JS cycle hitting a TDZ error at load.** Only browser tests catch it, so they must run, not just the server
   tests.
5. **A stale or missing gate key.** A string the units sweep no longer scans, because the file isn't in `PY`,
   passes silently.

---

Each task: read the spec's rules and that file's section; read the file; move the groups; update imports,
re-exports and gate keys; run that file's guarding tests (listed below), then the full suite; commit.

### Task 1: part_session.py → part_files.py
Guards: test_part_server, test_part_browser, test_part_grip, test_part_cli, test_app_open, plus the gates.

### Task 2: bench_session.py → bench_results.py, bench_jobs.py
Guards: test_bench_server, test_bench_browser, test_bench_mcp, test_display_units_bench, test_i18n_rest,
test_i18n_loaders, test_i18n_tabs.

### Task 3: blocks/run.py → run_words.py, run_setup.py, run_notes.py
Guards: tests/test_blocks_*.py, test_clearance, test_partner, test_part_grip, test_measure_labels,
test_kept_words, test_i18n_describe, test_i18n_test_tab, test_display_units_test_tab, test_examples_*,
test_range_ledger_golden, test_test_server, test_mcp_blocks. Also update docs/translating.md if `_w` moves.

### Task 4: code.js → code_devices.js, code_start.js
Guards: test_robot_code_browser, test_drive_browser, test_start_browser, test_scenario_browser,
test_robot_code_server.

### Task 5: part_inspector.js → part_helpers, part_bodies, part_where, part_motion, part_hardware
Guards: test_part_browser, test_part_server, test_display_units_browser, test_i18n_tabs_browser,
test_theme_browser. Record the section-id order before and after the move in the report; it must match.

### Task 6: test_run.js → test_run_state, test_run_chart, test_run_sweep
Guards: test_test_browser, test_i18n_test_tab_browser, test_display_units_browser, test_theme_browser,
test_test_server.

### Task 7: bench.js → bench_fields, bench_stage, bench_dialogs
Guards: test_bench_browser, test_display_units_bench, test_i18n_tabs_browser, test_theme_browser,
test_bench_server.

### Task 8: test.js → test_state, test_setup, test_floor, test_dialogs
Guards: test_test_browser, test_i18n_test_tab_browser, test_display_units_browser, test_assembler_server,
test_theme_browser, test_test_server.

### Task 9: server.py → server_common, http_handler, then app_files … app_live mixins
Sub-steps J1–J9, one commit each. Run the server guards after each sub-step and the full suite at the end.
- Guards: test_assembler_server, test_app_open, test_scenario_server, test_test_server, test_part_server,
  test_measure_server, test_bench_server, test_robot_code_server, test_live_server, test_live_words,
  test_nt_and_recorder, test_settings, test_i18n_rest, test_i18n_tabs, test_i18n_pages, test_bench_tab_docs,
  test_e2e, and all *_browser tests.
- Add a test, `tests/test_server_mixins.py::test_no_method_is_defined_in_two_mixins`. It checks that the sets of
  names in each mixin's `__dict__` (dunders excluded) are pairwise disjoint and disjoint from App's own.
