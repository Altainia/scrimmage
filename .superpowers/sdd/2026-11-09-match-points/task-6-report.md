# Task 6 report: points in scenarios (D6)

Full suite on the final tree: 4610 passed, 7 skipped, 5 xfailed in 2368 s.

## Changes per file
- scrimmage/scenario_points.py (new): `StepPhases` (same `of(t)` as `game.Phases`, built from recorded step intervals:
  auto -> "auto", disabled -> "pause", teleop -> "teleop", else None; `auto[1]` is the end of the last auto step,
  `teleop[1]` the scenario's end) and `ScenarioMatch` (begin/finish per step, LEAVE from the center at the scenario's
  start and at the end of the last auto step, climb level read from the climb monitor, `points(at)` via `game.score`),
  plus `points_lines` reusing run_notes.points_lines.
- scrimmage/checks.py: `points`, `points.auto|teleop|endgame` signals (`_points_signal`), SIGNALS_HELP. No range ->
  the existing range-needed error; no `[game]` -> "needs a [game] table in the range file".
- scrimmage/scenario_runner.py: `_run_steps` makes a ScenarioMatch only on a range with a game and calls begin/finish
  per step; `Report.points`; `report.json` gets a `points` key and report.md's Score section a points line, only with a game.
- scrimmage/world.py: `scenario_match = None`.
- scrimmage/assembler/signals.py: lists the four signals on a range with a game.
- scrimmage/session.py: no-robot-program error adds the `[test]` / `match = true` pointer through `i18n.t`.
- docs/test-range.md: signals table row and a paragraph on how phases, LEAVE and endgame work in scenarios.
- tests/test_scenario_points.py (new, 16 tests).

## Decisions and concerns
- No per-physics-step work at all: LEAVE is sampled at step boundaries; the climb monitor is made only on a game range
  with climb levels. `score` is unchanged (Test tab byte-identical).
- A read within one physics step before a step's end counts as at the end (a check at the end of a step can land a step
  short), so `points.auto` in an auto step's at_end check includes LEAVE.
- While the last step runs, `points.endgame` is 0 until that step's end (its planned end is the match's end).
- Interrupted steps use their actual end. The error text omits the range name (avoids the apostrophe).
- No range in this repo has a `[game]` yet, so end-to-end runs show an empty-points scenario; scoring is tested at the
  world level with a range file plus game. Task 7 adds 2024's.
