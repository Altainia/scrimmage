# A match and its points (kickoff fixes K3): design

The 2024 kickoff dry run (docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md) found that a team can't ask
"how many points does this robot score in auto?" (gap 4) or "does it break the possession or extension rule?" (gap
17). K1 already added floor zones (gap 19) and the field size (gap 7). K2 let a NOTE score. What's left:

- **No points.** A goal counts pieces, and only `[climb] levels` carry points.
- **No match.** The Test tab has no auto or teleop. A scenario has both, but needs robot code: on kickoff day there is
  none ("has no [robot_program] section").
- **No rules.** One-piece possession isn't checked. The extension and height limits exist only in `climb run`
  scripts, with 2022's numbers as defaults.

Under the owner's standing authorization (2026-11-04: "do all remaining work my way"), every design question here is
decided and recorded with its cost.

## Goal and limits

- **Goal.** On kickoff day, with no robot code, a student can:
  - write a Test-tab test that plays a match (auto, a pause, teleop) on a range;
  - see the points it scored, split into auto, teleop and endgame, with what each came from;
  - check them with an `expect` block ("points in auto at least 7");
  - see any rule it broke during the match: holding more pieces than allowed, reaching past the extension limit, or
    standing taller than the height limit.
  A scenario with robot code gets the same points as signals.
- **One game's rules, kept simple.** Points come from what scrimmage already sees: goal crossings, the robot's center
  in a zone, and the climb level. Rules that depend on the state of the match are not modeled:
  - 2024's AMPLIFICATION, Coopertition, MELODY and ENSEMBLE;
  - 2026's hub shifts;
  - fouls and penalty points.
  The docs list each one.
- **Goal counts are unchanged.** `counts()` and `ball_log()` keep their keys, and the ledger golden, the range goldens
  and the 2024 tests stay as they are. Points are computed separately, from the ball log.
- **Non-goals:**
  - a no-code scenario (a scenario still needs robot code; the Test tab is the no-code path);
  - a second robot or an alliance score;
  - the referee: fouls, penalties and cards.

## Decisions

Each one gives what was decided, why, and what it costs if wrong.

- **D1. The game lives in the range file, in a `[game]` table.**
  ```toml
  [game]
  name = "2024 CRESCENDO"
  auto_seconds = 15              # source: the 2024 manual, AUTO
  pause_seconds = 3              # source: the 2024 manual, the pause between AUTO and TELEOP
  teleop_seconds = 135           # source: the 2024 manual, TELEOP
  points = { speaker_blue = { auto = 5, teleop = 2 }, amp_blue = { auto = 2, teleop = 1 } }   # source: M Table 6-2
  leave = { zone = "blue_start", points = 2 }   # source: M Table 6-2: LEAVE
  [game.rules]
  possession = 1                 # source: G403 (one NOTE)
  extension_in = 12              # source: R105
  height_in = 48                 # source: the 2024 manual's 4 ft height limit
  ```
  - `points` names goals. A goal's value is either one number (the same in both phases) or `{ auto, teleop }`. A goal
    not listed still counts pieces but scores 0 points. A name that isn't a goal is an error naming it.
  - `leave` names a zone. LEAVE scores when the robot's center starts the match inside it and is outside it when auto
    ends.
  - Endgame points are the `[climb] levels` points of the level the robot holds when the match ends. They stay in
    `[climb]`, where they already are.
  - Every key is optional, apart from the three phase lengths when there is a `[game]` at all.
  - Why: the game is a fact of the field, and the field is the range. *Cost:* a scenario's `[match] auto_seconds` (the
    Driver Station's timer, sub-project 27) and the range's `auto_seconds` can disagree. In a scenario the steps set
    the phases, as now, and the range's lengths are used only by the Test tab. The docs say so in both places.
- **D2. Points are computed from the ball log, not counted live.**
  - A new pure function, `testrange/game.py` `score(game, log, phases, leave, level) -> Points`.
    - `log`: each piece's goal and `scored_t`.
    - `phases`: the auto and teleop intervals.
    - `leave` and `level`: what the run saw.
    - `Points` holds `auto`, `teleop`, `endgame`, `total` and `items`: one row per source, with its phase, its count
      and its points.
  - A piece scored in the pause scores nothing, and the item says so. A piece scored after teleop ends doesn't count
    (a match ends at teleop's end).
  - Why: `scored_t` is already recorded for every piece, and keeping points off the step loop leaves every golden
    alone. *Cost:* points can't change how the field behaves (2024's amplified SPEAKER). That stays a non-goal.
- **D3. A match in the Test tab is a `[test]` setting, `match = true`.**
  - The run's clock starts at the first block, as now (after the 0.3 s settle).
  - Auto runs from 0 to `auto_seconds`. The pause comes next, with the robot disabled: every motor at neutral, by a
    DriverStation frame with `>enabled = false` in the step's HAL. Teleop follows for `teleop_seconds`, and the run
    ends at teleop's end.
  - The blocks run straight through. A new condition, **match is in auto / pause / teleop**, lets a student wait for
    teleop (`wait_until`). A block that runs during the pause starts its motors when teleop begins, as a real robot
    held disabled would.
  - `time_limit` becomes the match's length. A `time_limit` given with `match = true` is an error saying the match
    sets it.
  - `match = true` on a range with no `[game]` is a "fix these first" problem.
  - Why: the Test tab already runs blocks with no robot code, so it's the nearest path to a no-code match. *Cost:*
    one block list for both phases, rather than an auto routine and a teleop routine. `wait_until match is in teleop`
    marks the split. Two lists can come later if students ask.
- **D4. The result shows points, the phases and broken rules.**
  - result.json gains `match` (the phase times), `points` (the `Points`, as a dict) and `rules` (each breach: rule,
    time, value, limit).
  - The kept run's lines gain a points line ("12 points: 7 in auto, 5 in teleop, 0 in endgame") and one line per
    item. They are built in the viewer's Language and Units, like the goal lines.
  - The replay's mark strip gets a mark at each phase change.
  - A new condition, **points [in auto / teleop / endgame / total] at least N**, works in `expect` and
    `wait_until`. It reads the points so far (D2's `score` on the log at that time).
- **D5. Rules are checked in a match and reported, not enforced.**
  - **possession:** the number of pieces the robot controls. It counts pieces the ball path holds (in the hopper, a
    stage or the launcher), plus pieces held by contact (`HeldTracker`'s rule: touching a robot part, off the
    floor, moving with it). A breach is more than `possession` for longer than 3 s (G403's MOMENTARY). The 3 s is a
    constant, `POSSESSION_GRACE_S`.
  - **extension_in, height_in:** the climb monitor's `climb.extension` and `own_height`, the same measures `climb run`
    uses (colliding geoms only). They are tracked only when the game has rules.
  - Each breach is reported once, at its first moment, with its peak value. The run carries on.
  - `climb run`'s `[limits]` defaults come from the range's `[game.rules]` when it has them. A script's own
    `[limits]` still wins. The 2022 constants stay as the fallback.
  - Why: a referee calls a foul and the match goes on, so a test should do the same. *Cost:* contact-held counting can
    miss a piece wedged in a frame without lifting it. The docs say so.
- **D6. Scenarios get the points as signals.**
  - `points`, `points.auto`, `points.teleop` and `points.endgame`, for a range with a `[game]`.
  - Phases come from the steps: a piece scored during a step in mode `auto` is an auto piece, and in `teleop` a teleop
    one. A disabled step is the pause. A step's mode changes are recorded with their times.
  - Endgame is the climb level when the scenario ends.
  - The no-robot-program error gains a pointer: a match without robot code is a Test-tab test with `match = true`.
  - *Cost:* a scenario with its auto step longer than the game's auto still counts its pieces as auto. The steps are
    the scenario's own timeline, by design.
- **D7. 2024 gets its game.**
  - The built-in `ranges/2024.toml` gains `[game]` with the manual's numbers: SPEAKER 5/2, AMP 2/1, LEAVE 2,
    possession 1, extension 12 in, height 48 in, and 15/3/135 s. Each number carries a source comment. The note "the
    game's points are not modeled" becomes a list of what isn't (AMPLIFICATION and the rest).
  - A new example, `examples/blocks/match`, plays an auto that shoots a preloaded NOTE from the SUBWOOFER and leaves
    the starting zone, then waits for teleop. Its test expects at least 7 auto points.
  - The other built-in ranges get no `[game]` in K3. 2026's hub shifts would make its points misleading, and 2022's
    hangar has no taxi zone.

## Order and tasks

Each task is one reviewed unit and runs the full suite.

1. **D1.** Parse `[game]` (a new `testrange/game.py`, after `zones.py`). Add `Range.game` and the `range check`
   summary. Docs (S).
2. **D2.** `game.score` and `Points`, with tests on hand-made logs (S).
3. **D3 + D4's runner side.** Match mode in `TestRun`: the phases, the disabled pause, the phase condition, the
   points condition. Store `match`, `points` and `rules` in result.json. Add the lines and the marks (L).
4. **D5.** Possession, extension and height in a match. Make `climb run`'s defaults come from the range (M).
5. **D4's page side.** The blocks for the two new conditions, the points lines' list label, the phase marks, i18n and
   en-GB entries (M).
6. **D6.** Scenario signals and the error's pointer (S).
7. **D7.** 2024's `[game]`, the example, the docs, and a "K3 status" note in the findings doc under gaps 4 and 17 (S).

## Rules every task follows

- Messages go through `i18n.t` (or `N_` at import), with table and key names as placeholders, called directly (not
  through an alias). Run sentences use run_words.py's `_w` words, so machine text stays the same in every Language
  and Units. Keep test_i18n_calls, test_i18n_sweep, test_i18n_typed, test_en_gb_catalog and test_units_machine_text
  green.
- Don't add keys to `counts()` or to `ball_log()` rows. Don't add per-step work to a world with no range, or to a range
  with no `[game]`. The ledger golden, the range goldens and tests/test_range_2024.py stay byte-for-byte.
- docs/match.md is pinned by tests/test_match_docs.py. `[match]`'s keys and defaults don't change.
- New preset numbers carry `# source:` or `# estimate:` (or `# choice:`).
- American spellings and plain words in docs and messages.
- `.venv/bin/python -m pytest`, never `uv run`.
