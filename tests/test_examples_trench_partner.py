"""The trench clearance test and the partner push (examples/blocks/trench and examples/blocks/partner): two worked
Test-tab examples on the stand-in robot. Stowed, the elevator and arm clear the 2026 TRENCH's arm by 2.9 in; with
the arm raised the clears check fails before the arm hits, and without the check it runs into the trench. A braked
partner is pushed 63 in in 2 s at 6 V, a coasting one 112 in. Each test asserts what the example's README says."""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.palette import hints, problems
from scrimmage.blocks.program import load, loads
from scrimmage.blocks.run import TestRun, clearance_lines, world_for
from scrimmage.testrange.spec import load_range

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "blocks"
TRENCH = EXAMPLES / "trench"
PARTNER = EXAMPLES / "partner"
CLEARS = """[[block]]
do = "expect"
that = { clears = { element = "trench_arm", at_least_in = 0.5 } }
throughout = true

"""


def _parts(path: Path) -> tuple[dict, dict]:
    """The config's raw TOML and its [[part]] tables by name."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return raw, {p["name"]: p for p in raw["part"]}


def _run(path: Path, text: str | None = None, on_sample=None):
    program = load(path) if text is None else loads(text, path)
    world = world_for(program)
    return program, world, TestRun(world, program).run(on_sample=on_sample)


def test_trench_example_robot():
    raw, parts = _parts(TRENCH / "trench-standin.toml")
    assert raw["robot"]["standin"] is True
    assert raw["range"] == {"file": "2026-trench", "start": "approach"}
    assert parts["elevator"]["stock"] == "elevator" and parts["elevator"]["collide"] is True
    assert parts["arm"]["stock"] == "arm" and parts["arm"]["attach"] == "elevator.carriage"
    assert parts["arm"]["collide"] is True
    for name in ("clear", "raised"):
        program = load(TRENCH / f"{name}.test.toml")
        assert problems(program, world_for(program)) == []


def test_trench_example_clears_stowed():
    program = load(TRENCH / "clear.test.toml")
    world = world_for(program)
    xs = []
    r = TestRun(world, program).run(on_sample=lambda t, row: xs.append(float(world.data.xpos[world.robot_body][0])))
    assert r.status == "finished", r.outcome
    assert [c["line"] for c in r.checks] == \
        ["passed: the robot clears trench_arm by at least 0.5 in from 0.3 s on (block 1)"]
    # the README: closest 2.89 in at 2.5 s, the elevator's top under the trench's arm
    assert r.clearance["trench_arm"]["min_in"] == pytest.approx(2.9, abs=0.05)
    assert r.clearance["trench_arm"]["t"] == pytest.approx(2.5, abs=0.1)
    assert clearance_lines(r) == ["closest to trench_arm: 2.89 in at 2.5 s"]
    # it went all the way through: its back bumper (0.43 m behind its center) past the trench's far face
    arm = next(e for e in load_range("2026-trench", ".").elements if e.name == "trench_arm")
    assert xs[-1] - 0.43 > arm.center[0] + arm.size[0] / 2


def test_trench_example_raised_arm_fails():
    tip = []

    def top(t, row):        # the arm's highest point (m): its box's top corner
        m, d = world.model, world.data
        arm = m.body("arm.arm").id
        tip.append(max(float(d.geom_xpos[g][2] + np.abs(d.geom_xmat[g].reshape(3, 3)[2]) @ m.geom_size[g])
                       for g in range(m.ngeom) if m.geom_bodyid[g] == arm))

    program = load(TRENCH / "raised.test.toml")
    world = world_for(program)
    r = TestRun(world, program).run(on_sample=top)
    assert r.status == "check failed", r.outcome
    assert r.outcome == ("a check failed (block 2): expected the robot to clear trench_arm by at least 0.5 in, but it "
                         "was 0.45 in at 1.7 s")
    assert clearance_lines(r) == ["closest to trench_arm: 0.45 in at 1.7 s"]
    assert max(r.signals["arm.pivot"]) > 89.0                   # the arm was up
    assert max(tip) == pytest.approx(0.64, abs=0.01)            # the README: its tip about 0.64 m (25 in) up


def test_trench_example_raised_arm_hits_without_the_check(units):
    units("metric")
    path = TRENCH / "raised.test.toml"
    text = path.read_text(encoding="utf-8")
    assert CLEARS in text
    _, world, r = _run(path, text.replace(CLEARS, ""))
    assert r.status == "timed out", r.outcome
    assert r.outcome == "drive (block 2) didn't reach 4 m in 10 s (went 1.5 m)"
    assert r.clearance["trench_arm"]["min_in"] == 0.0
    assert r.clearance["trench_arm"]["t"] == pytest.approx(1.7, abs=0.1)
    m, d = world.model, world.data
    arm, trench = m.body("arm.arm").id, m.body("element.trench_arm").id
    assert any({int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])} == {arm, trench}
               for c in d.contact[:d.ncon])                     # still pressed against it at the end


@pytest.mark.parametrize("deg, passed, closest", [(40, True, 2.9), (45, True, 2.5), (50, True, 1.3), (55, False, None)])
def test_trench_example_how_high_the_arm_can_be(deg, passed, closest):
    path = TRENCH / "raised.test.toml"
    _, _, r = _run(path, path.read_text(encoding="utf-8").replace("to = 90.0", f"to = {deg}.0"))
    assert r.passed is passed, r.outcome
    if closest is not None:
        assert r.clearance["trench_arm"]["min_in"] == pytest.approx(closest, abs=0.06)


def test_trench_example_taller_elevator(tmp_path):
    """The README: travel 0.75 m leaves about 1 in."""
    cfg = (TRENCH / "trench-standin.toml").read_text(encoding="utf-8")
    assert "travel = 0.6\n" in cfg
    (tmp_path / "trench-standin.toml").write_text(cfg.replace("travel = 0.6\n", "travel = 0.75\n"), encoding="utf-8")
    _, _, r = _run(tmp_path / "clear.test.toml", (TRENCH / "clear.test.toml").read_text(encoding="utf-8"))
    assert r.status == "finished", r.outcome
    assert r.clearance["trench_arm"]["min_in"] == pytest.approx(1.0, abs=0.15)


def _trench_copy(tmp_path: Path, cfg: str) -> Path:
    """The trench example's raised test beside `cfg` (the robot config's text) in tmp_path."""
    (tmp_path / "trench-standin.toml").write_text(cfg, encoding="utf-8")
    path = tmp_path / "raised.test.toml"
    path.write_text((TRENCH / "raised.test.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return path


UNCOUNTED = ("clearance counts only parts that collide: arm and elevator have collide off, so they aren't measured "
             "(set collide = true on them)")


def test_trench_raised_with_collide_off_says_so(tmp_path):
    """Fix 1: with collide off on the elevator and the arm, the raised arm passes through the trench unmeasured: the
    run passes on the chassis's clearance, and its notes and the Test tab's hints say the parts aren't counted."""
    cfg = (TRENCH / "trench-standin.toml").read_text(encoding="utf-8")
    assert cfg.count("collide = true\n") == 2
    path = _trench_copy(tmp_path, cfg.replace("collide = true\n", "collide = false\n"))
    program, world, r = _run(path)
    assert r.passed, r.outcome
    assert UNCOUNTED in r.notes
    assert hints(program, world) == [{"place": [], "message": UNCOUNTED}]
    assert problems(program, world) == []


def test_trench_no_uncounted_note_when_every_part_collides(tmp_path):
    program, world, r = _run(TRENCH / "raised.test.toml")
    assert not any("clearance counts" in n for n in r.notes), r.notes
    assert hints(program, world) == []
    # a robot with no stock parts but its chassis and drive (neither has a collide setting): no note either
    cfg = (TRENCH / "trench-standin.toml").read_text(encoding="utf-8")
    head = cfg[:cfg.index("[[part]]\nname = \"elevator\"")]
    path = _trench_copy(tmp_path, head + cfg[cfg.index("[range]"):])
    text = path.read_text(encoding="utf-8")
    block = '[[block]]\ndo = "move"\njoint = "arm.pivot"\nto = 90.0\nunit = "deg"\n\n'
    assert block in text
    path.write_text(text.replace(block, "").replace(', "arm.pivot"', ""), encoding="utf-8")
    program, world, r = _run(path)
    assert not any("clearance counts" in n for n in r.notes), r.notes
    assert hints(program, world) == []


PUSH_CYLINDER = """
[[part]]
name = "cyl"
stock = "cylinder"
attach = "elevator.carriage"
pos = [0.0, 0.0, -0.1]
push = "arm.arm"
push_pos = [0.2, 0.0, 0.0]
ids = { valve = "PCM:0" }

"""


def test_trench_push_cylinder_isnt_named(tmp_path):
    """A push cylinder is drawn only and never collides, so "set collide = true" would be wrong for it: a robot whose
    only collide-off stock part is a push cylinder gets no note and no hint."""
    cfg = (TRENCH / "trench-standin.toml").read_text(encoding="utf-8")
    path = _trench_copy(tmp_path, cfg.replace("[range]", PUSH_CYLINDER + "[range]"))
    program = load(path)
    world = world_for(program)
    cyl = next(i for i in world.parts.instances if i.name == "cyl")
    assert cyl.stock_params["collide"] is False and cyl.stock_params["push"] == "arm.arm"
    _, _, r = _run(path)
    assert not any("clearance counts" in n for n in r.notes), r.notes
    assert hints(program, world) == []


def _push(name: str, text: str | None = None):
    path = PARTNER / f"{name}.test.toml"
    return _run(path, text)


def test_partner_example_robot():
    raw, parts = _parts(PARTNER / "partner-standin.toml")
    assert raw["robot"]["standin"] is True and "range" not in raw and "partner" not in raw
    assert set(parts) == {"drive", "dt"}
    for name, brake in (("push", True), ("coast", False)):
        program = load(PARTNER / f"{name}.test.toml")
        assert program.setup["partner"] == {"ahead_in": 50.0, "brake": brake}
        assert problems(program, world_for(program)) == []


def test_partner_example_braked_push():
    _, world, r = _push("push")
    assert r.status == "finished", r.outcome
    assert [c["line"] for c in r.checks] == ["passed: the partner moved at least 60 in at 2.3 s (block 2)"]
    moved = r.signals["partner.moved"]
    assert moved[-1] == pytest.approx(63, abs=1.0)              # the README: 63 in
    # the bumpers meet at about 0.7 s
    first = next(t for t, mv in zip(r.signals["t"], moved) if mv > 0.1)
    assert first == pytest.approx(0.7, abs=0.05)
    assert max(r.signals["partner.tilt"]) < 2.0                 # pushed along the floor, not tipped


def test_partner_example_coast_moves_farther():
    _, _, braked = _push("push")
    _, _, coast = _push("coast")
    assert coast.status == "finished", coast.outcome
    assert coast.signals["partner.moved"][-1] == pytest.approx(112, abs=1.5)    # the README: 112 in
    ratio = coast.signals["partner.moved"][-1] / braked.signals["partner.moved"][-1]
    assert ratio == pytest.approx(1.8, abs=0.05)                # about 1.8 times as far


def test_partner_example_braked_stops_soon_after():
    """The README: the braked partner stops within about 8 in once the robot stops; a coasting one keeps rolling."""
    wait = 'volts = 6.0\n\n[[block]]\ndo = "wait"\nseconds = 1.0\n'
    for name, most in (("push", 8.5), ("coast", None)):
        src = (PARTNER / f"{name}.test.toml").read_text(encoding="utf-8")
        _, _, r = _push(name, src.replace("volts = 6.0\n", wait))
        moved = r.signals["partner.moved"]
        after = moved[-1] - moved[next(i for i, t in enumerate(r.signals["t"]) if t >= 2.3)]
        if most is not None:
            assert 6.0 < after < most
        else:
            assert after > 30.0


@pytest.mark.parametrize("change, moved, passed", [
    (("volts = 6.0", "volts = 8.0"), 86, True),
    (("volts = 6.0", "volts = 4.0"), 40, False),
    (("brake = true", "heading_deg = 90, brake = true"), 3, False),
    (("brake = true", "heading_deg = 90, brake = false"), 3, False),
])
def test_partner_example_change_it(change, moved, passed):
    text = (PARTNER / "push.test.toml").read_text(encoding="utf-8")
    assert change[0] in text
    _, _, r = _push("push", text.replace(*change))
    assert r.passed is passed, r.outcome
    assert r.signals["partner.moved"][-1] == pytest.approx(moved, abs=1.0)
