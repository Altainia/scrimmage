"""The beam-break intake (examples/blocks/beam): the intake example's roller and bin with a beam break across the bin.
The robot drives at the ball with the roller on until the beam breaks, then stops everything. Each test asserts what
the example's README says the run does: when the beam broke, where the ball stopped, and the README's changes (the
beam at the mouth, the roller's voltage)."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load, loads
from scrimmage.blocks.run import TestRun, world_for

BEAM = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "beam"
TEST = BEAM / "stop.test.toml"
MOUTH = ("from = [0.32, 0.17, 0.05]", "to = [0.32, -0.17, 0.05]")     # the README's beam at the mouth


def _run(program) -> dict:
    """The run, with the beam's reading every sample and the ball's center in the intake's frame at the end."""
    world = world_for(program)
    m, d = world.model, world.data
    frame, ball = m.body("intake.frame").id, m.body("ball.0").id
    seen: list[tuple[float, bool, float]] = []        # (t, the beam broken, the robot's x)
    path: list[tuple[float, np.ndarray, np.ndarray]] = []   # (t, the ball's center in the intake's frame, in the field)

    def local():
        return d.xmat[frame].reshape(3, 3).T @ (d.xpos[ball] - d.xpos[frame])

    def on_sample(t, row):
        seen.append((t, world.sensors.reading("bin").on, float(d.xpos[world.robot_body][0])))
        path.append((t, local(), d.xpos[ball].copy()))

    result = TestRun(world, program).run(on_sample=on_sample)
    return {"program": program, "world": world, "result": result, "seen": seen, "path": path, "ball": local()}


def _variant(tmp_path: Path, test_text: str, config_text: str | None = None) -> dict:
    """A changed copy of the example (the test, and the config if given) run from a folder of its own."""
    for f in ("beam-standin.toml", "stop.test.toml"):
        (tmp_path / f).write_text((BEAM / f).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "parts").symlink_to(BEAM / "parts")
    if config_text is not None:
        (tmp_path / "beam-standin.toml").write_text(config_text, encoding="utf-8")
    return _run(loads(test_text, tmp_path / "stop.test.toml"))


def _first_broken(seen) -> float:
    return next(t for t, on, _ in seen if on)


@pytest.fixture(scope="module")
def stop():
    return _run(load(TEST))


def test_beam_example_robot(stop):
    raw = tomllib.loads((BEAM / "beam-standin.toml").read_text(encoding="utf-8"))
    assert raw["robot"]["standin"] is True
    assert raw["range"] == {"file": "intake", "start": "facing_ball"}
    assert raw["sensor"] == [{"name": "bin", "kind": "beam", "body": "intake.hopper", "from": [0.12, 0.17, 0.08],
                              "to": [0.12, -0.17, 0.08], "dio": 4}]
    # the intake is the intake example's, unchanged
    assert (BEAM / "parts/intake/part.toml").read_text() == \
        (BEAM.parent / "intake/parts/intake/part.toml").read_text()
    assert problems(stop["program"], stop["world"]) == []
    # the README quotes the sensor table as the config has it
    readme = (BEAM / "README.md").read_text(encoding="utf-8")
    assert 'body = "intake.hopper"\nfrom = [0.12, 0.17, 0.08]\nto = [0.12, -0.17, 0.08]\ndio = 4' in readme


def test_beam_example_stops_with_the_ball_in_the_bin(stop):
    r = stop["result"]
    assert r.status == "finished", r.outcome
    assert [c["line"] for c in r.checks] == ["passed: bin is on at 3.0 s (block 7)",
                                             "passed: a ball reaches intake.hopper at 3.0 s (block 8)"]
    seen = stop["seen"]
    # the beam breaks at 2.0 s, when the ball rolls into the bin, and reads broken every sample from then on
    broke = _first_broken(seen)
    assert broke == pytest.approx(2.0, abs=0.05)
    assert all(on for t, on, _ in seen if t >= broke)
    assert not any(on for t, on, _ in seen if t < broke)
    # the roller reaches the ball at about 1.0 s (the ball starts moving toward the robot) ...
    rest = stop["path"][0][2]
    moved = next(t for t, _, at in stop["path"] if np.linalg.norm(at - rest) > 0.005)
    assert moved == pytest.approx(1.0, abs=0.05)
    # ... and for most of a second the ball rides on the lip, up off the carpet, squeezed under the roller
    on_lip = [p for t, p, _ in stop["path"] if 1.2 <= t <= 1.8]
    assert all(0.2 < p[0] < 0.26 and p[2] > 0.125 for p in on_lip)
    # the robot rolls on about 19 cm after the stop
    x_at_break = next(x for t, on, x in seen if on)
    assert seen[-1][2] - x_at_break == pytest.approx(0.19, abs=0.02)
    # the ball rests on the bin's floor, its center 13 cm in front of the bumper's face (1 cm in front of the beam)
    x, y, z = stop["ball"]
    assert x == pytest.approx(0.13, abs=0.01) and abs(y) < 0.01 and z == pytest.approx(0.115, abs=0.003)
    # the chart: sensor.bin is 0 until the beam breaks, 1 after
    sig = r.signals["sensor.bin"]
    assert set(sig) == {0.0, 1.0}
    assert r.signals["t"][sig.index(1.0)] == pytest.approx(broke, abs=1e-6)


def test_the_beam_at_the_mouth_breaks_too_soon(tmp_path):
    """The README's first change: the beam between the lip and the roller breaks at 1.0 s and leaves the ball on the
    carpet against the lip; the hopper check fails."""
    cfg = (BEAM / "beam-standin.toml").read_text(encoding="utf-8")
    cfg = cfg.replace("from = [0.12, 0.17, 0.08]", MOUTH[0]).replace("to = [0.12, -0.17, 0.08]", MOUTH[1])
    assert MOUTH[0] in cfg and MOUTH[1] in cfg
    run = _variant(tmp_path, TEST.read_text(encoding="utf-8"), cfg)
    r = run["result"]
    assert r.status == "check failed"
    assert r.outcome == "a check failed (block 8): no ball was in intake.hopper at 2.0 s; 0 of 1 made it in (0%); 1 stuck in the robot"
    assert r.checks[0]["line"] == "passed: bin is on at 2.0 s (block 7)"
    assert _first_broken(run["seen"]) == pytest.approx(1.0, abs=0.05)
    x, _, z = run["ball"]
    assert x == pytest.approx(0.30, abs=0.01) and z == pytest.approx(0.078, abs=0.005)    # on the carpet
    readme = (BEAM / "README.md").read_text(encoding="utf-8")
    assert f"{MOUTH[0]}\n{MOUTH[1]}" in readme and r.outcome in readme


@pytest.mark.parametrize("volts, finished, broke", [
    (0.0, False, None), (2.0, False, None),
    (4.0, True, 1.7), (6.0, True, 2.0), (8.0, True, 1.3), (10.0, True, None), (12.0, True, None),
])
def test_the_roller_voltage_sweep(tmp_path, volts, finished, broke):
    """The README's sweep: 0 and 2 V time out at 5.3 s; 4 V to 12 V finish, the beam breaking between 1.3 and 2.0 s
    (1.7 s at 4 V, 2.0 s at 6 V, 1.3 s at 8 V)."""
    text = TEST.read_text(encoding="utf-8")
    assert text.count("volts = 6.0") == 1
    run = _variant(tmp_path, text.replace("volts = 6.0", f"volts = {volts}"))
    r = run["result"]
    if not finished:
        assert r.status == "timed out", r.outcome
        assert r.outcome == "waiting for bin to be on timed out at 5.3 s (block 4); 0 of 1 made it in (0%); " \
            "1 missed (1 still moving when the run ended)"
        return
    assert r.status == "finished", r.outcome
    t = _first_broken(run["seen"])
    assert 1.3 - 0.05 <= t <= 2.0 + 0.05
    if broke is not None:
        assert t == pytest.approx(broke, abs=0.05)
    x, _, z = run["ball"]
    assert 0.05 < x < 0.2 and z == pytest.approx(0.115, abs=0.003)       # in the bin


def test_without_the_stop_it_still_passes(tmp_path):
    """The README: take block 5 (stop all) out and the run still passes, with the roller spinning to the end."""
    text = TEST.read_text(encoding="utf-8")
    stop_block = '[[block]]\ndo = "stop"\nmotor = "all"\n\n'
    assert stop_block in text
    run = _variant(tmp_path, text.replace(stop_block, ""))
    r = run["result"]
    assert r.status == "finished", r.outcome
    assert all(c["passed"] for c in r.checks)
    assert not math.isclose(r.signals["intake.roller"][-1], r.signals["intake.roller"][-2])   # still spinning
