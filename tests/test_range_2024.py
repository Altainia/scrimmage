"""The built-in 2024 range (scrimmage/testrange/ranges/2024.toml) and its worked example (examples/blocks/speaker):
the range checks clean and builds, a pinch shooter's NOTE from the SUBWOOFER scores in the blue SPEAKER, a SPEAKER
aim table has rows that still score in the world with the wheels 2% off, the AMP scores a NOTE, and the bench's
docs example scores against the built-in range."""

from __future__ import annotations

import math
import re
import threading
import tomllib
from pathlib import Path

import numpy as np
import pytest
import tomlkit

from scrimmage.bench import report
from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import parse_bench
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.motors import get_motor
from scrimmage.protocol import HalState
from scrimmage.testrange import aim, check
from scrimmage.testrange.game import score
from scrimmage.testrange.spec import BUILTIN_RANGES, load_range
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "blocks" / "speaker"
ROBOT = EXAMPLE / "note-shooter-standin.toml"
FIXTURE = Path(__file__).resolve().parent / "data" / "range_2024" / "2024.toml"
TABLE_DISTANCES = [1.17, 1.57, 1.97, 2.37]      # m: from the SUBWOOFER (1.17) out, every 0.4 m
TILT_LOW = 30.0                                 # deg: the example's tilt_range starts here (the tilt joint's 0)


# ------------------------------------------------------------------------------------------------ the range

def test_the_range_checks_clean():
    rng = check.load("2024")
    lines = check.summary("2024", rng)
    assert lines[0].startswith("2024: ok, range 'FRC 2024 CRESCENDO': field 16.541 x 8.211 m; 4 goals "
                               "(speaker_blue, speaker_red, amp_blue, amp_red); 11 floor pieces; 2 stations")
    assert "; 16 AprilTags (2024-crescendo); " in lines[0]
    # the six CHAINS are 1/2 in bars until K5 Task 9 hangs them as chains: each is warned about (R16)
    chains = [f"chain_{a}_{s}" for a in ("blue", "red") for s in ("center", "left", "right")]
    assert len(lines) == 7 and len(rng.warnings) == 6
    assert sorted(rng.warnings) == sorted(
        f"bar '{c}' is 0.5 in across: a stock climber's hook slips off a bar under 1 in (K5); hang a chain "
        f"(kind = \"chain\") or make the bar at least 1 in" for c in chains)
    assert "2024" in BUILTIN_RANGES and list(rng.kinds) == ["note"]
    assert {g.kind for g in rng.goals} == {"note"}
    # the SPEAKER's hood is an obstacle (a NOTE's corners can't clip it), not a plate of the goal
    assert {"speaker_blue_hood", "speaker_red_hood"} <= {o.name for o in rng.obstacles}
    assert all(not g.plates for g in rng.goals)


def test_the_range_has_the_2024_game():
    """The `[game]`: 15 s auto, a 3 s pause, 135 s teleop, SPEAKER 5/2 and AMP 2/1 for the blue goals only (the red
    ones count pieces, for 0 points), LEAVE 2 from blue_start, the rules, and ONSTAGE's 3 from [climb]; `range check`
    shows it."""
    rng = check.load("2024")
    g = rng.game
    assert (g.name, g.auto_s, g.pause_s, g.teleop_s) == ("2024 CRESCENDO", 15.0, 3.0, 135.0)
    assert {k: (v.auto, v.teleop) for k, v in g.points.items()} == {
        "speaker_blue": (5.0, 2.0), "amp_blue": (2.0, 1.0)}
    assert (g.leave.zone, g.leave.points) == ("blue_start", 2.0)
    assert (g.rules.possession, g.rules.extension, g.rules.height) == (
        1, pytest.approx(12 * 0.0254), pytest.approx(48 * 0.0254))
    assert g.climb_points == {"ONSTAGE": 3.0}
    red = score(g, [{"goal": "speaker_red", "scored_t": 1.0}], g.phases(0.0), None, None)    # a blue robot's shot
    assert red.total == 0.0 and [(i.source, i.count, i.points) for i in red.items] == [("speaker_red", 1, 0.0)]
    assert "game '2024 CRESCENDO' (15 s auto, 3 s pause, 135 s teleop, 2 goals score points)" in \
        check.summary("2024", rng)[0]


def test_the_blue_speaker_is_the_bench_fixtures():
    """The tests' trimmed copy (tests/data/range_2024) has the same blue SPEAKER, hood, wall and AMP as the built-in
    range, so the bench's goal tests on it stand for the built-in range."""
    built, fixture = load_range("2024", ROOT), load_range(FIXTURE.name, FIXTURE.parent)
    for name in ("speaker_blue", "amp_blue"):
        assert next(g for g in built.goals if g.name == name) == next(g for g in fixture.goals if g.name == name)
    for name in ("speaker_blue_wall", "speaker_blue_hood"):
        assert next(o for o in built.obstacles if o.name == name) == \
               next(o for o in fixture.obstacles if o.name == name)


def test_the_field_builds_with_its_notes_and_stays_still():
    """Every NOTE on its mark, the SOURCES and the elements compile into one world, and nothing moves on its own."""
    cfg = tomllib.loads(ROBOT.read_text(encoding="utf-8"))
    cfg["range"] = {**cfg["range"], "floor_balls": True, "preload": 0}
    w = World(cfg, EXAMPLE)
    rt = w.test_range
    assert int(np.count_nonzero(rt.balls.field)) == 11
    start = rt.balls._qpos[rt.balls.field, :3].copy()
    for _ in range(300):
        w.step(HalState())
    assert np.abs(rt.balls._qpos[rt.balls.field, :3] - start).max() < 0.01


# ------------------------------------------------------------------------------------------------ the example

def test_the_example_scores_from_the_subwoofer():
    """examples/blocks/speaker/subwoofer.test.toml: from against the blue SUBWOOFER, the channel at 58 degrees, both
    wheels at 3.05 V (about 21.7 rps: a solution launcher aim finds there with more room than the table's lowest-arc
    row), the loader feeds the preloaded NOTE; it scores in the blue SPEAKER without touching anything on the way,
    and the check says "pieces" for a NOTE."""
    program = load(EXAMPLE / "subwoofer.test.toml")
    world = world_for(program)
    assert problems(program, world) == []
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    assert [c["passed"] for c in r.checks] == [True]
    assert r.checks[0]["text"] == "1 or more pieces are scored in speaker_blue"
    log = world.test_range.ball_log()[0]
    assert log["goal"] == "speaker_blue" and log["first_hit"] is None
    assert log["hood_deg"] == pytest.approx(58.0, abs=0.2)
    assert log["shooter_rps"] == pytest.approx(21.7, rel=0.03)


def test_a_missed_note_says_pieces(tmp_path):
    """A failed scored check on a NOTE field says "pieces" (a ball field keeps "balls":
    tests/test_blocks_shooting.py)."""
    (tmp_path / "miss.test.toml").write_text(f"""[test]
robot = "{ROBOT.as_posix()}"

[[block]]
do = "wait"
seconds = 0.2

[[block]]
do = "expect"
that = {{ scored = {{ goal = "speaker_blue", at_least = 1 }} }}
""", encoding="utf-8")
    program = load(tmp_path / "miss.test.toml")
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed"
    assert "expected 1 or more pieces scored in speaker_blue, but 0 were" in r.outcome


AMP_TEST = """[test]
robot = "note-shooter-standin.toml"

[[block]]
do = "move"
joint = "shooter.tilt"
to = 0.0
unit = "deg"

[[block]]
do = "spin"
motor = "shooter.spin0"
volts = 1.8

[[block]]
do = "spin"
motor = "shooter.spin1"
volts = 1.8

[[block]]
do = "wait"
seconds = 1.0

[[block]]
do = "spin"
motor = "loader.spin"
volts = 8.0

[[block]]
do = "wait"
seconds = 1.5

[[block]]
do = "expect"
that = { scored = { goal = "amp_blue", at_least = 1 } }
"""


def test_the_amp_scores_a_note(tmp_path):
    """The README's AMP shot: the example robot at the blue_amp start (1 m from the AMP, facing it), the channel at
    30 degrees (the tilt joint at 0), both wheels at 1.8 V (the AMP table's 1 m row, about 13.3 rps): the NOTE goes
    into the AMP."""
    cfg = tomllib.loads(ROBOT.read_text(encoding="utf-8"))
    cfg["range"] = {**cfg["range"], "start": "blue_amp"}
    (tmp_path / ROBOT.name).write_text(tomlkit.dumps(cfg), encoding="utf-8")
    (tmp_path / "amp.test.toml").write_text(AMP_TEST, encoding="utf-8")
    program = load(tmp_path / "amp.test.toml")
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    log = world.test_range.ball_log()[0]
    assert log["goal"] == "amp_blue" and log["first_hit"] is None
    assert log["hood_deg"] == pytest.approx(30.0, abs=0.2)
    assert log["shooter_rps"] == pytest.approx(13.3, rel=0.03)


# ------------------------------------------------------------------------------------------------ the aim table

@pytest.fixture(scope="module")
def speaker_table():
    model = aim.load_model(ROBOT, "speaker_blue", hood_step_deg=5.0)
    rows = aim.aim_table(ROBOT, "speaker_blue", TABLE_DISTANCES, hood_step_deg=5.0, model=model,
                         on_unreachable=lambda msg: None)
    return model, rows


def test_a_speaker_aim_table_has_at_least_3_rows(speaker_table):
    """Each row peaks at least UPWARD_APEX_MARGIN above the opening's center (still rising as it crosses); the
    SUBWOOFER's row is the example's shot."""
    model, rows = speaker_table
    scoring = [r for r in rows if r.status == "ok"]
    assert len(scoring) >= 3, aim.to_csv(rows)
    center = model.goal.scoring_surface().center[2]
    assert all(r.apex >= center + aim.UPWARD_APEX_MARGIN for r in scoring), aim.to_csv(rows)
    assert rows[0].hood_deg == pytest.approx(60.0) and rows[0].wheel_rps == pytest.approx(19.87, abs=0.05)


def _fly_in_world(distance: float, rps: float, tilt: float) -> dict:
    """The example robot `distance` back from the blue SPEAKER on its approach line, facing it; robot code that
    holds the tilt and both wheels at `rps` (the aim's loop: kV feedforward plus 0.5 V per rps, trimmed onto the
    speed before the feed), then feeds the NOTE: its log once it has scored, or after 4 s."""
    cfg = tomllib.loads(ROBOT.read_text(encoding="utf-8"))
    goal = next(g for g in load_range("2024", ROOT).goals if g.name == "speaker_blue")
    cfg["range"] = {k: v for k, v in cfg["range"].items() if k != "start"}
    cfg["robot"] = {**cfg["robot"], "start_pose": [goal.pos[0] + distance, goal.pos[1], 180.0]}
    w = World(cfg, EXAMPLE)
    rt, m, d = w.test_range, w.model, w.data
    joint = m.joint("shooter.tilt")
    qadr, vadr = m.jnt_qposadr[joint.id], m.jnt_dofadr[joint.id]
    kv = 2.0 * math.pi / get_motor("KrakenX60", 1).Kv
    trim = [0.0, 0.0]
    for k in range(4000):
        feeding = k >= 1500
        pwm = {}
        for i, shaft in enumerate(rt.shooter.launcher.shafts):
            error = rps - shaft.speed / (2.0 * math.pi)
            if not feeding:
                trim[i] += 0.5 * error * 0.001
            pwm[str(2 + i)] = (kv * rps + 0.5 * error + trim[i]) / w.vbatt
        pwm["4"] = 4.0 * (math.radians(tilt - TILT_LOW) - d.qpos[qadr]) - 0.3 * d.qvel[vadr]
        pwm["5"] = 1.0 if feeding else 0.0
        hal = HalState()
        for port, value in pwm.items():
            hal.devices[("PWM", port)] = {"<init": True, "<speed": float(np.clip(value, -1.0, 1.0))}
        w.step(hal)
        if rt.counts()["goals"].get("speaker_blue"):
            break
    return rt.ball_log()[0]


def test_the_tables_rows_score_in_the_world_with_the_wheels_2_percent_off(speaker_table):
    """Each row flown in the world, the robot at the row's distance, the wheels at its speed and 2% either side: every
    shot scores in the blue SPEAKER. (Without UPWARD_APEX_MARGIN the lowest-apex rows crossed flat at their apex and
    most caught the opening's front lip.)"""
    _, rows = speaker_table
    missed = []
    for row in (r for r in rows if r.status == "ok"):
        for f in (0.98, 1.0, 1.02):
            log = _fly_in_world(row.distance, row.wheel_rps * f, row.hood_deg)
            if log["goal"] != "speaker_blue":
                missed.append((row.distance, f, log["first_hit"]))
    assert not missed


# ------------------------------------------------------------------------------------------------ the bench

def test_the_bench_docs_note_lob_scores_in_the_builtin_range(tmp_path):
    """docs/bench.md's `[flight] goal` example names the built-in range (`range = "2024"`), and its throw scores."""
    blocks = re.findall(r"<!-- bench -->\s*\n```toml\n(.*?)\n```", (ROOT / "docs" / "bench.md").read_text("utf-8"),
                        re.S)
    text = next(b for b in blocks if 'name = "note lob"' in b)
    assert 'goal = { range = "2024", name = "speaker_blue" }' in text
    result = run_bench(parse_bench(tomllib.loads(text), tmp_path))      # the built-in name, from any folder
    lands = report.landings(result)
    assert [land.goal for _, land in lands] == ["scored"]
    assert "1 of 1 shots score in `speaker_blue`" in report.headlines(result, lands)


def test_the_match_example_scores_7_points_in_auto():
    """examples/blocks/match/auto.test.toml: the speaker example's robot plays the 2024 match (15 s auto, 3 s pause,
    135 s teleop). In auto it shoots the preloaded NOTE into the SPEAKER (5) and drives out of blue_start (LEAVE, 2);
    it waits for teleop and expects 7 points in auto. The run stops a second into teleop (at 19 s), once LEAVE is
    judged and the expect has run: the whole match is about 40 s of wall time, and the match tests cover its end."""
    program = load(ROOT / "examples" / "blocks" / "match" / "auto.test.toml")
    world = world_for(program)
    assert problems(program, world) == []
    stop = threading.Event()
    r = TestRun(world, program).run(on_sample=lambda t, row: stop.set() if t >= 19 else None, cancel=stop)
    assert r.status == "cancelled", r.outcome
    assert [c["passed"] for c in r.checks] == [True]
    assert r.checks[0]["text"] == "7 or more points are scored in auto"
    assert r.points["auto"] == 7.0
    assert [(i["source"], i["points"]) for i in r.points["items"]] == [("speaker_blue", 5.0), ("leave", 2.0)]


# ------------------------------------------------------------------------------------------------ the camera example

CAMERA_EXAMPLE = ROOT / "examples" / "blocks" / "camera"


def test_the_camera_example_sees_tags_7_and_8():
    """examples/blocks/camera: a camera 0.90 m up on the speaker example's robot sees the blue SPEAKER's tags 7 and 8
    from the blue SUBWOOFER, over the SUBWOOFER's top."""
    program = load(CAMERA_EXAMPLE / "sees-speaker-tags.test.toml")
    world = world_for(program)
    assert problems(program, world) == []
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    assert [c["passed"] for c in r.checks] == [True, True]
    assert r.cameras["front_cam"][-1][1] == [7, 8]


def test_the_camera_readmes_other_starts(tmp_path):
    """The README's "try this": from blue_amp the camera sees the AMP's tag 6; from blue_amp_side, none."""
    robot = (CAMERA_EXAMPLE / "note-shooter-standin.toml").read_text(encoding="utf-8")
    seen = {}
    for start in ("blue_amp", "blue_amp_side"):
        (tmp_path / f"{start}.toml").write_text(robot.replace('start = "blue_subwoofer"', f'start = "{start}"'),
                                                encoding="utf-8")
        (tmp_path / f"{start}.test.toml").write_text(
            f'[test]\nrobot = "{start}.toml"\n\n[[block]]\ndo = "wait"\nseconds = 0.5\n', encoding="utf-8")
        program = load(tmp_path / f"{start}.test.toml")
        seen[start] = TestRun(world_for(program), program).run().cameras["front_cam"][-1][1]
    assert seen == {"blue_amp": [6], "blue_amp_side": []}


def test_a_low_camera_is_blocked_by_the_subwoofer(tmp_path):
    """Review Focus 3: the same robot with its camera at z = 0.25 m looks into the SUBWOOFER's face and sees neither
    tag; the example mounts it high for that reason."""
    robot = (CAMERA_EXAMPLE / "note-shooter-standin.toml").read_text(encoding="utf-8")
    assert "at = [0.25, 0.0, 0.90]" in robot
    (tmp_path / "low.toml").write_text(robot.replace("at = [0.25, 0.0, 0.90]", "at = [0.25, 0.0, 0.25]"),
                                       encoding="utf-8")
    text = (CAMERA_EXAMPLE / "sees-speaker-tags.test.toml").read_text(encoding="utf-8")
    (tmp_path / "low.test.toml").write_text(text.replace("note-shooter-standin.toml", "low.toml"), encoding="utf-8")
    program = load(tmp_path / "low.test.toml")
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed" and [c["passed"] for c in r.checks] == [False], r.checks
    assert r.cameras["front_cam"][-1][1] == []
