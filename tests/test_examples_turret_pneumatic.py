"""The turret shooter and the pneumatic arm (examples/blocks/turret and examples/blocks/pneumatic): two worked Test-tab
examples on the stand-in robot. The turret keeps the launcher on hub_blue while the robot turns on the spot, and
shots fired before and after the turn score; the arm is lifted by a cylinder 10 times and the air stays above the
working pressure. Each test asserts what the example's README says the run does."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, air_lines, goal_lines, world_for

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "blocks"
TURRET = EXAMPLES / "turret"
PNEUMATIC = EXAMPLES / "pneumatic"


def _parts(path: Path) -> tuple[dict, dict]:
    """The config's raw TOML and its [[part]] tables by name."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return raw, {p["name"]: p for p in raw["part"]}


@pytest.fixture(scope="module")
def turret():
    program = load(TURRET / "aim.test.toml")
    world = world_for(program)
    m, d = world.model, world.data
    q = m.joint("turret.turn").qposadr[0]
    scored_at_turn: list[int] = []      # hub_blue's count when the turret first swung away from straight ahead

    def on_sample(t, row):
        if not scored_at_turn and abs(math.degrees(d.qpos[q])) > 5.0:
            scored_at_turn.append(world.test_range.counts()["goals"]["hub_blue"])

    result = TestRun(world, program).run(on_sample=on_sample)
    r = d.xmat[world.robot_body].reshape(3, 3)
    return {"program": program, "world": world, "result": result, "scored_at_turn": scored_at_turn,
            "yaw": math.degrees(math.atan2(r[1, 0], r[0, 0])), "turret": math.degrees(d.qpos[q])}


def test_turret_example_robot(turret):
    raw, parts = _parts(TURRET / "turret-standin.toml")
    assert raw["robot"]["standin"] is True
    assert raw["range"] == {"file": "2026", "preload": 8}
    assert parts["turret"]["stock"] == "turret" and parts["turret"]["attach"] == "drive.top"
    assert parts["launcher"]["attach"] == "turret.top"
    assert [s["part"] for s in raw["shooter"]["stage"]] == ["hopper_floor", "feeder"]
    assert problems(turret["program"], turret["world"]) == []


def test_turret_example_scores_before_and_after_the_turn(turret):
    r = turret["result"]
    assert r.status == "finished", r.outcome
    assert [c["passed"] for c in r.checks] == [True, True, True], r.checks
    assert r.checks[0]["line"].startswith("passed: the turret is aimed at hub_blue within 5° from ")
    # the README's times: aimed from 1.4 s, 3 scored at 3.8 s, 6 scored at 6.6 s
    assert [c["t"] for c in r.checks] == [pytest.approx(1.4, abs=0.1), pytest.approx(3.8, abs=0.1),
                                          pytest.approx(6.6, abs=0.1)]
    assert r.goals == {"hub_blue": 8, "hub_red": 0}         # the README: all 8
    assert goal_lines(r) == ["scored 8 in hub_blue"]
    assert turret["scored_at_turn"] == [4]                  # 4 in before the turn, 4 after
    # the robot coasts on past its 90° to about 120°, and the turret turns back as far to stay on the hub
    assert 110 < turret["yaw"] < 130
    assert turret["turret"] == pytest.approx(-turret["yaw"], abs=3)
    assert max(abs(e) for e in r.signals["aim_error"]) < 5


@pytest.fixture(scope="module")
def pneumatic():
    program = load(PNEUMATIC / "cycles.test.toml")
    world = world_for(program)
    m, d = world.model, world.data
    q = m.joint("arm.pivot").qposadr[0]
    lifts = [0, False]                  # times the arm rose past 60°; whether it is above now

    def on_sample(t, row):
        up = math.degrees(d.qpos[q]) > 60.0
        if up and not lifts[1]:
            lifts[0] += 1
        lifts[1] = up

    result = TestRun(world, program).run(on_sample=on_sample)
    return {"program": program, "world": world, "result": result, "lifts": lifts[0]}


def test_pneumatic_example_robot(pneumatic):
    raw, parts = _parts(PNEUMATIC / "arm-standin.toml")
    assert raw["robot"]["standin"] is True
    assert raw["pneumatics"] == {}                          # the defaults
    lift = parts["lift"]
    assert lift["stock"] == "cylinder" and lift["push"] == "arm.arm"
    assert lift["stroke"] == pytest.approx(8 * 0.0254)
    assert lift["bore"] == pytest.approx(0.75 * 0.0254)
    assert parts["arm"]["stock"] == "arm" and parts["arm"]["attach"] == "drive.top"
    assert problems(pneumatic["program"], pneumatic["world"]) == []


def test_pneumatic_example_air_lasts_ten_lifts(pneumatic):
    r = pneumatic["result"]
    assert r.status == "finished", r.outcome
    assert [c["passed"] for c in r.checks] == [True], r.checks
    assert pneumatic["lifts"] == 10
    air = r.air
    assert air["below_working_t"] is None
    # the README: 67 psi at the end, lowest 67 psi at 4.0 s; the compressor ran 2.1 s
    assert air["low_psi"] == pytest.approx(67, abs=0.5) and air["end_psi"] == pytest.approx(67, abs=0.5)
    assert air["low_t"] == pytest.approx(4.0, abs=0.1)
    assert air["compressor_s"] == pytest.approx(2.1, abs=0.1)
    assert max(r.signals["lift.extension"]) > 7.9           # the arm's swing uses the whole 8 in. stroke
    assert air_lines(r) == [f"air: 120 psi at the start, {air['end_psi']:.0f} psi at the end, lowest "
                            f"{air['low_psi']:.0f} psi at {air['low_t']:.1f} s; the compressor ran "
                            f"{air['compressor_s']:.1f} s"]


def test_pneumatic_example_air_budget(capsys):
    main(["air", str(PNEUMATIC / "arm-standin.toml")])
    out = capsys.readouterr().out
    assert "  lift: 0.597 L" in out
    assert "Cycles from the tanks alone: 7" in out
    assert "Cycles per match, tanks and compressor: 29" in out
