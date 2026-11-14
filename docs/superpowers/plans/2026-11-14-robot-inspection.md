# Robot Inspection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare a robot model with a chosen FRC season's weight and size rules: a `scrimmage inspect` command, scenario notes for match-time breaches, and assembler warnings.

**Architecture:** A new package `scrimmage/inspection/`: `seasons.toml` (the manuals' limits) and `spec.py` (loading it, choosing the season, reading `[inspection]`); `measure.py` (the robot's mass, frame perimeter, height, reach and top-view diameter from a built World, no simulation); `report.py` (the lines comparing measures with limits, worded for the Units and Language settings); `monitor.py` (height and reach every 20 ms of a scenario, as notes). The climb monitor's frame-outline code moves to a shared function.

**Tech Stack:** Python 3.11+, MuJoCo, numpy, tomllib; pytest.

**Spec:** docs/superpowers/specs/2026-11-14-robot-inspection-design.md (limits and their sources: docs/superpowers/specs/2026-11-14-robot-inspection-sources.md)

## Global Constraints

- Words people read: American spelling, plain words, through `i18n.t()` like the code around it; machine text (report.json, MCP) stays American English and SI (meters, kilograms), shown text goes through `units.fmt(value, "length"|"mass")` in the Units setting.
- Every text file read or written names `encoding="utf-8"` (tests/test_text_encoding.py enforces it).
- Tests run with `/home/altainia/code/scrimmage/.venv/bin/python -m pytest`, from the worktree with `PYTHONPATH=/home/altainia/code/scrimmage-insp`. Never `uv run`.
- Test files import helpers bare (`from cad_helpers import ...`), never `from tests.x import`.
- Seasons covered: 2013, 2016, 2020, 2022, 2023, 2024, 2025, 2026; values exactly the spec's table (Call I1).
- The `[inspection]` keys: `season` (int, a season in the table), `battery_mass` (kg, ≥ 0), `bumper_mass` (kg, ≥ 0). Anything else in the section is a ConfigError naming the key.
- Default battery 12.89 lb (AndyMark am-0844 listing); default bumpers 15 lb (UNCALIBRATED estimate). Both labeled "estimate" wherever used.
- Start reach allowance: 0.25 in (minor protrusions). Monitor sampling: every 0.02 s of simulated time.
- Scenario notes and assembler warnings only when the robot config has an `[inspection]` section; `scrimmage inspect` always works.
- `scrimmage inspect` exit codes: 0 every checked limit within; 1 any checked limit over (estimates included); 2 the config doesn't load or build, or an unknown `--season`.

## Review Focus

- A robot whose body has no box geom (a hand-written MJCF robot): the frame falls back to all its geoms' outline and the perimeter line says so — Task 2 test `test_a_robot_without_a_frame_plate_uses_all_its_geoms`.
- An elevator built `collide = false` must still count for height — Task 2 test `test_a_non_colliding_elevator_counts_for_height`.
- A season with no limit for a measure (2025 match height, 2013 reach): "no limit" line, monitor skips it, never "within" — Task 3 test `test_a_season_without_a_limit_says_so`, Task 4 test `test_the_monitor_skips_a_measure_the_season_has_no_limit_for`.
- A world reset mid-scenario (the runner resets between some steps): the monitor's breach state must reset with the world and a later breach be noted again — Task 4 test `test_a_reset_starts_the_monitor_over`.
- `battery_mass`/`bumper_mass` larger than the model's mass (a model that left them out): the weight must not go negative silently — Task 3 test `test_subtracting_more_than_the_model_weighs_is_a_warning` (the line says the model weighs less than what was subtracted and to set them to 0).

---

### Task 1: The season table and choosing a season

**Files:**
- Create: `scrimmage/inspection/__init__.py`, `scrimmage/inspection/seasons.toml`, `scrimmage/inspection/spec.py`
- Modify: `pyproject.toml` (package data: `inspection/*.toml`, following how other `.toml` data under scrimmage/ is included)
- Test: `tests/test_inspection_spec.py`

**Interfaces:**
- Produces:
  - `Limit(value: float | None, rule: str, unless: str = "")` frozen dataclass; `value` in SI (m or kg), None = no limit.
  - `Season(year: int, manual: str, url: str, weight: Limit, weight_with_bumpers: Limit, bumper_set: Limit, perimeter: Limit, start_height: Limit, height: Limit, reach: Limit, diameter: Limit, not_checked: tuple[str, ...])`.
  - `SEASONS: dict[int, Season]` (loaded once from seasons.toml).
  - `InspectionSettings(season: int | None, battery_mass: float | None, bumper_mass: float | None, present: bool)`; `settings(raw: dict) -> InspectionSettings` raising `ConfigError` for a bad key or value.
  - `choose_season(raw: dict, override: int | None = None) -> tuple[Season, str]`: the season and why, the reason one of `"--season"`, `"[inspection] season"`, `"the [range] (<file>)"`, `"the newest season"`.
  - `DEFAULT_BATTERY = 12.89 lb in kg`, `DEFAULT_BUMPERS = 15 lb in kg`, `START_ALLOWANCE = 0.25 in in m`.

- [ ] **Step 1: Write the failing tests**

```python
LB, IN = 0.45359237, 0.0254
def test_every_season_matches_the_manuals():
    want = {  # year: (weight lb, perimeter in, start height in, match height in or None, reach in or None)
        2013: (120, 112, 84, 60, None), 2016: (120, 120, 54, 54, 15), 2020: (125, 120, 45, 45, 12),
        2022: (125, 120, 52, 52, 16), 2023: (125, 120, 54, 78, 48), 2024: (125.5, 120, 48, 48, 12),
        2025: (115, 120, 42, None, 18), 2026: (115, 110, 30, 30, 12)}
    assert sorted(SEASONS) == sorted(want)
    for year, (w, p, sh, h, r) in want.items():
        s = SEASONS[year]
        assert s.weight.value == pytest.approx(w * LB) and s.perimeter.value == pytest.approx(p * IN)
        assert s.start_height.value == pytest.approx(sh * IN)
        assert (s.height.value is None) == (h is None) and (h is None or s.height.value == pytest.approx(h * IN))
        assert (s.reach.value is None) == (r is None) and (r is None or s.reach.value == pytest.approx(r * IN))
        assert s.weight.rule and s.perimeter.rule and s.url.startswith("https://firstfrc.blob.core.windows.net/")
def test_bumpers_and_the_cylinder():
    assert SEASONS[2013].diameter.value == pytest.approx(54 * IN) and SEASONS[2016].diameter.value is None
    assert SEASONS[2013].bumper_set.value == pytest.approx(20 * LB) and SEASONS[2024].bumper_set.value == pytest.approx(15 * LB)
    assert SEASONS[2026].weight_with_bumpers.value == pytest.approx(135 * LB) and SEASONS[2024].weight_with_bumpers.value is None
    assert "hangar" in SEASONS[2022].height.unless and SEASONS[2023].not_checked
def test_choose_season():
    assert choose_season({})[0].year == 2026 and choose_season({})[1] == "the newest season"
    assert choose_season({"range": {"file": "2024"}})[0].year == 2024
    assert choose_season({"range": {"file": "2013-pyramid"}})[0].year == 2013
    assert choose_season({"range": {"file": "reef"}})[0].year == 2026          # no year in its name: newest
    assert choose_season({"inspection": {"season": 2022}, "range": {"file": "2024"}})[0].year == 2022
    assert choose_season({"inspection": {"season": 2022}}, override=2016) == (SEASONS[2016], "--season")
def test_bad_inspection_sections_are_config_errors():
    for bad in ({"season": 2019}, {"season": "2026"}, {"battery_mass": -1}, {"weight": 3}, {"bumper_mass": True}):
        with pytest.raises(ConfigError):
            settings({"inspection": bad})
    assert settings({}).present is False and settings({"inspection": {}}).present is True
```

- [ ] **Step 2: Run them to see them fail** — `pytest tests/test_inspection_spec.py -v`: FAIL (no module).
- [ ] **Step 3: Write `seasons.toml`** in the manual's units (`max_lb`, `max_in`, `rule`, optional `unless`), one table per season, with a comment above each limit quoting the rule short and the manual version, values and rules exactly as the spec's Call I1 table and the sources file. Start reach is not in the file (always 0 + START_ALLOWANCE).
- [ ] **Step 4: Implement `spec.py`** with the interfaces above; a range file's season is the leading 4 digits of its name when they are a season in the table. Error wording follows config.py's (`"[inspection] {key} ..."`).
- [ ] **Step 5: Run the tests** — PASS.
- [ ] **Step 6: Commit** — `git add scrimmage/inspection pyproject.toml tests/test_inspection_spec.py && git commit -m "Inspection: the seasons' limits and choosing a season"`

### Task 2: Measuring a robot

**Files:**
- Create: `scrimmage/inspection/measure.py`
- Modify: `scrimmage/terrain/climb.py` (move `_frame_outline` to a module function `frame_outline(model, body: int) -> tuple[float, float, float, float]` the monitor calls; behavior unchanged), `scrimmage/parts/stock/chassis.py` and `docs/parts.md` (the `mass` help becomes "everything on the chassis that isn't another part (frame, bumpers, battery, electronics)")
- Test: `tests/test_inspection_measure.py`

**Interfaces:**
- Consumes: a built `World` (`world.model`, `world.data`, `world.robot_body`), `frame_outline` from climb.py.
- Produces:
  - `Measures(mass: float, perimeter: float, frame_plate: bool, height: float, reach: float, diameter: float)` (SI).
  - `measure(world) -> Measures`: at the world's current state (the start pose right after building).
  - `Extent` class: `Extent(world)`; `.read() -> tuple[float, float]` (height, reach) at the current state, reusing precomputed geom lists — the monitor calls it every 20 ms.
  - `smallest_circle(points: np.ndarray) -> float` (diameter; Welzl, iterative or recursion-safe for a few thousand points).
- Definitions (spec Call I4 and answers 3, 7): every geom of the robot's bodies (root = robot body's root), colliding or not; bounding-box corners via `geom_aabb` in world coordinates; height = highest corner z minus the floor (z = 0); reach = largest horizontal distance, in the chassis body's x-y plane, from the frame outline to any corner of a geom not on the chassis body itself; perimeter = 2 × (dx + dy) of the frame outline; `frame_plate` False when the body has no box geom; diameter = smallest circle of all corners (bumpers included) projected on the floor.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_stock_kitbot():   # configs/kitbot.toml: chassis 0.66 x 0.6
    m = measure(build_world(load_config(ROOT / "configs/kitbot.toml")))
    assert m.perimeter == pytest.approx(2 * (0.66 + 0.6), abs=1e-6) and m.frame_plate
    assert m.mass == pytest.approx(<the world's body_subtreemass>) and m.reach == pytest.approx(0, abs=0.01)
    assert 0.6 + 2 * 0.08 <= m.diameter <= math.hypot(0.66 + 0.16, 0.6 + 0.16) + 1e-6
def test_a_non_colliding_elevator_counts_for_height(): ...   # configs/elevator_arm.toml: height > the chassis's top, and the elevator's geoms are collide = false
def test_an_arm_reaching_out_reads_its_reach(): ...           # set an arm joint so its tip is 0.3 m past the frame: reach ≈ 0.3
def test_a_robot_without_a_frame_plate_uses_all_its_geoms(): ...   # a small MJCF robot with a cylinder body: frame_plate False, perimeter of its outline
def test_smallest_circle(): ...   # square of side 1 → √2; three collinear points → their span; random points all inside
def test_the_climb_frame_outline_is_unchanged(): ...          # tests/test_climb_* still pass (run them)
```

- [ ] **Step 2: Run them** — FAIL.
- [ ] **Step 3: Move `frame_outline` out of `ClimbMonitor`** and call it from there; run `pytest tests/test_climb_*.py -q` — PASS.
- [ ] **Step 4: Implement `measure.py`.**
- [ ] **Step 5: Run the new tests and tests/test_parts*.py** — PASS.
- [ ] **Step 6: Commit** — "Inspection: measure a robot's weight, frame, height and reach"

### Task 3: The report and `scrimmage inspect`

**Files:**
- Create: `scrimmage/inspection/report.py`, `docs/inspection.md`
- Modify: `scrimmage/__main__.py` (an `inspect` subcommand next to `model`), `README.md` (a line in the docs list and in the robot-building section linking docs/inspection.md)
- Test: `tests/test_inspection_report.py`

**Interfaces:**
- Consumes: Task 1's `Season`, `choose_season`, `settings`, defaults; Task 2's `measure`.
- Produces:
  - `Line(key: str, rule: str, measured: float | None, limit: float | None, over: float, estimate: bool, text: Said)`; `key` one of `weight`, `weight_with_bumpers`, `bumper_set`, `perimeter`, `start_height`, `start_reach`, `diameter`, `height`, `reach`; `over` > 0 means over by that much (SI); `measured`/`limit` None for "no limit".
  - `Inspection(season: Season, reason: str, lines: list[Line], not_checked: tuple[str, ...])` with `.over -> list[Line]`.
  - `inspect_world(world, raw: dict, override: int | None = None) -> Inspection`.
  - `warnings(inspection) -> list[Said]`: one per over line ("2026 R104: frame perimeter 110.2 in, over the 110 in limit by 0.2 in").
- The weight line shows its sum (spec Call I3): "weight without bumpers and battery: 112.4 lb (141.0 lb modeled − 12.89 lb battery, estimate − 15 lb bumpers, estimate)"; an `unless` is quoted with ": not checked".
- CLI: `scrimmage inspect CONFIG [--season YEAR]` prints: the season line ("2026 rules (the newest season; set [inspection] season to choose)"), one line per Line (within/over/no limit), "Not checked:" with the season's list, then "Limits from <manual> <url>". Exit codes as Global Constraints.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_default_chassis_is_just_over_2026s_perimeter(tmp_path): ...  # a config with a default chassis: perimeter over by 0.24 in (0.006 m) on 2026, within on 2024
def test_the_weight_line_shows_what_it_subtracted(): ...   # unset: both "estimate"; set battery_mass/bumper_mass: no "estimate"
def test_subtracting_more_than_the_model_weighs_is_a_warning(): ...
def test_a_season_without_a_limit_says_so(): ...           # 2025 height → measured None/limit None line, "no limit in 2025"; 2013 reach likewise; never in .over
def test_unless_is_quoted(): ...                            # a tall 2022 robot: the height over line quotes the hangar exception and "not checked"
def test_cli_exit_codes(tmp_path): ...                      # 0 within (2024 kitbot), 1 over (2026 default chassis), 2 bad config, 2 --season 2019
def test_lines_follow_the_units_setting(): ...              # metric shows mm/kg, imperial in/lb (units.fixed)
```

- [ ] **Step 2: Run them** — FAIL.
- [ ] **Step 3: Implement `report.py` and the CLI**; start reach compares with 0 + START_ALLOWANCE; the bumper checks use `bumper_mass` (or the default, labeled estimate).
- [ ] **Step 4: Write `docs/inspection.md`**: what it checks, the season table (from the spec), choosing a season, `[inspection]` keys (battery_mass, bumper_mass: "set them to 0 if your model leaves them out"), what each measure means in a model, what isn't checked, that it isn't a certification. American spelling, plain words.
- [ ] **Step 5: Run the tests and tests/test_check_words.py tests/test_kept_words.py** — PASS.
- [ ] **Step 6: Commit** — "Inspection: scrimmage inspect"

### Task 4: Scenarios, the assembler and MCP

**Files:**
- Create: `scrimmage/inspection/monitor.py`
- Modify: `scrimmage/world.py` (an `inspection` monitor made when the config has `[inspection]`, stepped in `step()` after physics next to terrain/climb, reset in the world's resets), `scrimmage/scenario_runner.py` (start-check notes at the start, breach notes in the per-step `finally` next to `terrain_notes`), `scrimmage/assembler/validate.py` (`check()` adds `warnings(inspect_world(...))` when `[inspection]` is present, as status-line warnings), `scrimmage/assembler/app_robot.py` (robot state `"inspection"`: season, reason and each line's key/measured/limit/over/estimate, or None), `scrimmage/mcp_server.py` (`robot_parts` includes the same warnings), `docs/inspection.md` (a Scenarios and Assembler section)
- Test: `tests/test_inspection_monitor.py`, additions to the assembler and MCP tests

**Interfaces:**
- Consumes: Task 2's `Extent`, Task 3's `inspect_world`, `warnings`, `Line`.
- Produces: `InspectionMonitor(world, season)` with `after_physics()`, `reset()`, `notes() -> list[Said]` (each breach once: when it first went over, peak over, total time over; worded like the climb runs' G106/G107 notes); `world.inspection` (None without `[inspection]`).

- [ ] **Step 1: Write the failing tests**

```python
def test_an_arm_out_past_the_limit_for_a_second_is_one_note(): ...   # drive a world (no robot program; set qpos) past 2026's 12 in reach for 1.0 s: one note, peak and ~1.0 s
def test_the_monitor_samples_every_20_ms(): ...
def test_the_monitor_skips_a_measure_the_season_has_no_limit_for(): ...
def test_a_reset_starts_the_monitor_over(): ...
def test_no_inspection_section_no_monitor_and_no_notes(): ...       # world.inspection is None; an existing scenario's report unchanged
def test_the_assembler_warns_with_an_inspection_section(): ...       # validate.check on a default chassis with [inspection] season = 2026: a perimeter warning; without: none
def test_robot_parts_carries_the_inspection_warnings(): ...
```

- [ ] **Step 2: Run them** — FAIL.
- [ ] **Step 3: Implement the monitor, the world hook and the runner notes.**
- [ ] **Step 4: Implement the assembler and MCP parts.**
- [ ] **Step 5: Run the new tests, tests/test_scenario*.py, tests/test_climb*.py, tests/test_assembler*.py, tests/test_mcp*.py** — PASS.
- [ ] **Step 6: Commit** — "Inspection: scenario notes, assembler warnings, MCP"
