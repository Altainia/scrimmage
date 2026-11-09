# A match and its points (K3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a Test-tab test can play a match (auto, pause, teleop) with no robot code. It reports the points it scored,
by phase, and the rules it broke. A scenario gets the same points as signals.

**Architecture:**
- A range's `[game]` table, parsed in a new `testrange/game.py`, holds the phase lengths, each goal's points, LEAVE
  and the rules.
- Points come from a pure function over the ball log's `scored_t`. Nothing is counted live, and `counts()` is
  untouched.
- `TestRun` gets a match mode: it injects a disabled DriverStation frame during the pause.

**Tech Stack:** Python 3.11+, MuJoCo 4, numpy, pytest (with Playwright browser tests in the full suite), Blockly on
the Test tab's page.

**Spec:** docs/superpowers/specs/2026-11-09-match-points-design.md

## Global Constraints

- **Goldens unchanged:** don't add keys to `counts()` (testrange/runtime.py:~268) or to `ball_log()` rows. These
  stay byte-for-byte:
  - tests/test_range_ledger_golden.py (tests/data/ledger_golden.json);
  - range_ball_golden.npz and rangeless_golden.npz (tests/test_range_model.py);
  - tests/test_range_2024.py (until Task 7 adds its `[game]`, which must not change its numbers).
- **No new per-step work** in a world with no range, or on a range with no `[game]`.
- **Pinned docs:** docs/match.md is pinned by tests/test_match_docs.py. `MatchSettings` keys and defaults don't
  change.
- **Commands:**
  - Never `uv run`. Use `.venv/bin/python -m pytest`.
  - Don't run `git checkout`, `git switch` or `git stash`.
  - Don't push.
  - Don't dispatch subagents.
- **Messages:**
  - Every student-visible message goes through `i18n.t` (or `N_` at import time), with table and key names as
    placeholders.
  - Call `i18n.t` directly or through the module's existing `t`.
  - Run sentences use run_words.py's `_w`/`_ws` words, so machine text is the same in every Language and Units
    setting (tests/test_units_machine_text.py).
  - An en-GB entry is needed wherever a string has a word on the en-GB spelling list (tests/test_en_gb_catalog.py).
  - Range file errors go through `_Reader`'s err, naming the file and table.
- **Preset numbers:** every new preset number carries `# source:`, `# estimate:` or `# choice:`.
- **Wording:** American spellings and plain words in docs and messages.
- **Commits:** one per task, message `K3: <what>`, with the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done means:** the task's tests and the full suite (`.venv/bin/python -m pytest -q tests/`) pass. The report lists
  what changed, the decisions made with their costs, and the suite summary line.

## Review Focus

1. **A goal count or ledger number drifts.** Points must read the log, never write to it.
2. **Phase edges.** A piece scored exactly at `auto_seconds` is a pause piece, so it scores nothing. One scored
   exactly at the end of teleop counts. The intervals are half-open, `[0, auto)` and `[auto + pause, auto + pause +
   teleop]`. The 0.3 s settle is not match time.
3. **The pause really disables.** Motors at neutral, pneumatics held, as `World.step` does when `>enabled` is false.
   A drive block running across the pause starts again in teleop, and its clock (`drive ... for 2 s`) counts match
   time, pause included.
4. **No `[game]`, or a partial one.** A range with no `[game]` behaves exactly as today. A `[game]` with no
   `[game.rules]` checks no rules and tracks no extension.
5. **Possession counting.** A piece in the hopper and one held against an intake roller both count. A piece the robot
   only pushes along the floor doesn't count. A count above the limit for 2.9 s is no breach, and for 3.1 s it is.

---

### Task 1: parse `[game]` (spec D1)

**Files:**
- Create: `scrimmage/testrange/game.py`. Follow `zones.py`: frozen dataclasses and a `parse_game(path, table, goals,
  zones, climb)` function that reads with spec.py's `_Reader`. `climb` is the range's parsed `[climb]` rule, or None.
- Modify:
  - `scrimmage/testrange/spec.py`: add `"game"` to `_TOP` (around line 381), add `Range.game: Game | None = None`,
    and call the parser after goals and zones are parsed.
  - `scrimmage/testrange/check.py`: the summary lists the game's name, its phase lengths and how many goals score
    points.
- Test: a new `tests/test_range_game.py`.
- Docs: `docs/test-range.md`, a new "The game: points, the match and its rules" section after "Floor zones".

**Produces:**
- `Game(name: str, auto_s: float, pause_s: float, teleop_s: float, points: dict[str, GoalPoints],
  leave: Leave | None, rules: Rules | None, climb_points: dict[str, float])`. `climb_points` is a copy of `[climb]
  levels` (empty with no `[climb]`), so Task 2's scorer needs only the game.
- `GoalPoints(auto: float, teleop: float)`. One number means the same in both phases.
- `Leave(zone: str, points: float)`.
- `Rules(possession: int | None, extension: float | None, height: float | None)`. Lengths are in meters; the file
  gives `extension_in` and `height_in`. Each rule is optional.
- `Game.match_s` is the property `auto_s + pause_s + teleop_s`.

- [ ] Test parsing the spec's 2024 example on a small range that has the named goals and zone. The values come out
  as given, with the inches converted.
- [ ] Test the errors, each naming the file, the table and the key:
  - an unknown goal name in `points`;
  - an unknown zone in `leave`;
  - a missing `auto_seconds`, `pause_seconds` or `teleop_seconds`;
  - a negative or zero phase length (the pause may be 0);
  - negative points;
  - `possession` below 1 or not a whole number;
  - an unknown key in `[game]` or `[game.rules]`.
- [ ] Test that a range with no `[game]` has `Range.game is None`, and that `range check` on it prints no game lines.
- [ ] Implement it, write the docs, run the full suite, and commit.

### Task 2: the points (spec D2)

**Files:**
- Modify: `scrimmage/testrange/game.py`.
- Test: `tests/test_range_game.py`.

**Consumes:** Task 1's `Game`.

**Produces:**
- `Phases(auto: tuple[float, float], teleop: tuple[float, float])`, in seconds of match time.
- `Game.phases(start: float) -> Phases`: the phases for a match starting at run time `start`.
- `score(game: Game, log: list[dict], phases: Phases, left: bool | None, level: str | None, at: float | None = None)
  -> Points`.
  - `log` rows are `ball_log()`'s: `goal`, plus `scored_t` in run time, or None.
  - `left` says whether LEAVE was earned; None means not decided yet, which scores 0.
  - `level` is the climb level held at the match's end; None scores 0.
  - `at` scores only pieces with `scored_t <= at` (the points so far), and no endgame before the match ends.
- `Points(auto: float, teleop: float, endgame: float, total: float, items: tuple[PointsItem, ...])` with
  `to_dict()`.
- `PointsItem(source: str, phase: str, count: int, points: float)`.
  - `source` is a goal name, `"leave"` or `"climb"`.
  - `phase` is `"auto"`, `"teleop"`, `"endgame"` or `"pause"`.
  - A pause row has `points = 0`.

- [ ] Test hand-made logs on the 2024 numbers:
  - 2 SPEAKER pieces in auto, 1 in teleop, 1 AMP piece in teleop, and LEAVE give auto 12, teleop 3, total 15;
  - with `level = "ONSTAGE"` and a `[climb]` giving it 3 points, endgame is 3 and the total 18.
- [ ] Test the edges (Review Focus 2):
  - a piece at exactly `auto_s` is a pause item worth 0;
  - one at the exact end of teleop counts, and one after it doesn't appear;
  - a goal not in `points` gives a 0-point item with its count;
  - a missed piece (`goal` None) gives no item.
- [ ] Test `at`: points so far, mid-auto.
- [ ] Implement it, run the full suite, and commit.

### Task 3: a match in the Test tab, runner side (spec D3, D4)

**Files:**
- Modify:
  - `scrimmage/blocks/program.py`: `Program.match: bool`, from `[test] match = true`. A `time_limit` given with it
    is an error. The `match` condition kind, `{kind = "match", phase = "auto" | "pause" | "teleop"}`. The `points`
    condition kind, `{kind = "points", phase = "auto" | "teleop" | "endgame" | "total", at_least = N}`. Add both to
    `to_json`, `from_json` and `dumps`.
  - `scrimmage/blocks/run.py`:
    - `_play` sets `match_start` to the first block's run time. The run ends at `match_start + game.match_s`.
    - During the pause, every step's `HalState` gets a DriverStation frame with `>enabled = false`
      (`hal.apply('{"type":"DriverStation","device":"","data":{">enabled":false}}')`, protocol.py:44-56).
    - A phase-change mark goes into `RunResult.marks` (`kind = "phase"`).
    - `RunResult` gains `match`, `points` and `rules`.
  - `scrimmage/blocks/conditions.py`: `Watch.holds` and `describe` for the two new conditions. The points condition
    calls `game.score(..., at=now)`.
  - `scrimmage/blocks/run_words.py`: their failure sentences.
  - `scrimmage/blocks/palette.py`: `match = true` on a range with no `[game]` is a "fix these first" problem, and so
    is a `match` or `points` condition in a test that isn't a match.
  - `scrimmage/blocks/run_notes.py`: `points_lines(result)`, after `goal_lines`.
  - `scrimmage/blocks/store.py`: save and load `match`, `points` and `rules`.
  - `scrimmage/assembler/test_session.py`: `run_result`'s `lines` include the points lines.
- Test:
  - a new `tests/test_blocks_match.py`, on a small range with a `[game]`, a hub-like goal and a start zone, using
    the kitbot shooter fixtures from `tests/range_fixtures.py`;
  - `tests/test_blocks_program.py` for the round trips.
- Docs: `docs/test-tab.md`, a "Playing a match" section.

**Consumes:** Task 1's `Game`, and Task 2's `Phases`, `score` and `Points`.

- [ ] Test the timing:
  - in a match test, a `drive` block running across the pause gives zero motor output during the pause, and output
    again in teleop;
  - the run lasts the match's length after the settle;
  - the marks hold "auto", "pause" and "teleop" at the right times.
- [ ] Test the points: a test that shoots a preloaded piece in auto and leaves the start zone gets the expected auto
  points and LEAVE, and `expect points in auto at least N` passes or fails on them.
- [ ] Test `wait_until match is in teleop`: it waits until teleop starts.
- [ ] Test the problems:
  - `match = true` on a range with no `[game]`;
  - a `match` or `points` condition outside a match;
  - `time_limit` given with `match`.
- [ ] Test that the result is stored and loaded with `match`, `points` and `rules`, and that the kept run's lines
  start with "N points: ..." in English, with the same machine text in every Language.
- [ ] Test that a non-match test's result has none of these keys, and that its run is unchanged (same samples).
- [ ] Implement it, write the docs, run the full suite, and commit.

### Task 4: the rules in a match (spec D5)

**Files:**
- Create: `scrimmage/blocks/possession.py`, the controlled-piece counter.
  - The pieces the ball path holds: the range runtime's ledger state for the robot. Find where the ball path
    marks pieces held by the robot.
  - Plus pieces held by contact: `HeldTracker`'s rule (grip_watch.py:64).
  - `POSSESSION_GRACE_S = 3.0  # source: G403, MOMENTARY`.
- Modify:
  - `scrimmage/blocks/run.py`: when the game has rules, check them each step. Call `world.climb_monitor()` and
    `track_extent()` for extension and height (terrain/climb.py:180, 355-386). Record each breach once:
    `{rule, t, value, limit}`, with the peak value updated while it lasts.
  - `scrimmage/climb/script.py`: `Limits` defaults come from the range's `game.rules` when it has them. A script's
    `[limits]` still wins, and G107_EXTENSION / G106_HEIGHT stay the fallback.
  - `scrimmage/blocks/run_notes.py`: a rules line per breach, in plain words, with values in the viewer's units.
- Test: a new `tests/test_blocks_rules.py`.

- [ ] Test possession (Review Focus 5):
  - two pieces in the hopper over a limit of 1, for 3.1 s, is a breach, and for 2.9 s is not;
  - a piece pushed along the floor isn't controlled.
- [ ] Test extension: a robot whose colliding arm reaches 14 in past its frame, on a 12 in rule, gives a breach with
  a value of about 14 in.
- [ ] Test that a game with no `[game.rules]` tracks no extent. Prove it by the monitor's flag, not by timing.
- [ ] Test that `climb run` on a range whose game has rules takes its limits from there, unless the script gives
  `[limits]`.
- [ ] Implement it, run the full suite, and commit.

### Task 5: the Test tab's page (spec D4)

**Files:**
- Modify:
  - `scrimmage/assembler/static/blocks_defs.js`: blocks for "match is in [auto|pause|teleop]" and "points [in
    auto|teleop|endgame|total] at least N", next to `in_zone` (around lines 489 and 694). Add a match checkbox to the
    test's settings, wherever `time_limit` is edited.
  - `scrimmage/assembler/static/index.html`: the `#test-totals` label "Goals and air" becomes "Points, goals and air".
  - `scrimmage/assembler/static/test_run.js`: phase marks in the mark strip (`renderMarks`) with their own style, and
    the phase bands on the chart.
  - `scrimmage/locales/en-GB.json`: entries as needed.
- Test: the Test tab's browser tests, following tests/test_blocks_browser*.py or the Test-tab browser test that
  covers `in_zone`. Cover these:
  - the new blocks round-trip through save and load;
  - a match run shows its points lines and phase marks;
  - the i18n page tests stay green.
- [ ] Implement it, run the full suite, and commit.

### Task 6: points in scenarios (spec D6)

**Files:**
- Modify:
  - `scrimmage/checks.py`: `resolve_signal` (244-296) gains `points`, `points.auto`, `points.teleop` and
    `points.endgame` for a range with a `[game]`. Add them to `SIGNALS_HELP`.
  - `scrimmage/scenario_runner.py`: record each step's mode interval, so a scored piece's phase is the mode of the
    step it was scored in. The report's Score section gains a points line when the range has a game.
  - `scrimmage/assembler/signals.py`: list the signals.
  - `scrimmage/session.py`: the no-robot-program error (around line 230) adds that a match without robot code is a
    Test-tab test with `[test] match = true`.
- Test: `tests/test_scenario_points.py`, using the `tests/match_probe_py` fixture or the smallest scenario test with
  robot code already in the suite.
- Docs: docs/test-range.md, in the scenario signals list.
- [ ] Implement it, run the full suite, and commit.

### Task 7: 2024's game and an example (spec D7)

**Files:**
- Modify: `scrimmage/testrange/ranges/2024.toml`: `[game]` with the spec's numbers and source comments, for the blue
  and red goals and zones. Replace the "points not modeled" note with what isn't modeled.
- Create: `examples/blocks/match/`, a robot config (reuse examples/blocks/speaker's pinch shooter robot) and a match
  test, plus a README written for a student.
- Test: `tests/test_range_2024.py`:
  - the range still checks clean, and `range check` shows the game;
  - the example's match scores at least 7 auto points (a SPEAKER NOTE and LEAVE);
  - the existing tests' numbers are unchanged.
- Docs:
  - docs/test-range.md: the 2024 field section.
  - The findings doc: a "K3 status" note under gaps 4 and 17.
- [ ] Implement it, run the full suite, and commit.
