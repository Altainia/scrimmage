"""The passive partner robot: a robot config's [partner] and the Test tab's [setup] partner
(scrimmage/parts/partner.py), its signals (partner.height, tilt and moved), the partner_lifted and partner_moved
conditions, and its start check (Review Focus 3)."""

from __future__ import annotations

import math
import tomllib
from types import SimpleNamespace

import mujoco
import pytest

from scrimmage.assembler.signals import signals
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.conditions import Watch, describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, dumps, loads
from scrimmage.blocks.run import TestRun, setup_config, world_for
from scrimmage.checks import ScenarioError, resolve_signal
from scrimmage.parts.model import PartError
from scrimmage.parts.partner import IN, SIGNALS
from scrimmage.protocol import HalState
from scrimmage.world import World



def _world(tmp_path, extra: str = "") -> World:
    return World(tomllib.loads(new_config("standin") + extra), tmp_path)


def _settle(w: World, seconds: float) -> None:
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(HalState())


def _program(folder, setup: str, blocks: str = "", robot: str = ""):
    (folder / "robot.toml").write_text(new_config("standin") + robot, encoding="utf-8")
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n\n[setup]\n{setup}\n{blocks}', encoding="utf-8")
    return loads(path.read_text(encoding="utf-8"), path)


def _push(folder, brake: bool, seconds: float = 2.0) -> tuple[World, object, list, list]:
    """The stand-in drives at 6 V into a partner 40 in ahead of its start: (world, result, the partner's height
    above the carpet and its gap to the robot along x, at each sample)."""
    program = _program(folder, f"partner = {{ ahead_in = 40, brake = {'true' if brake else 'false'} }}",
                       f'[[block]]\ndo = "drive"\nseconds = {seconds}\nvolts = 6.0\n')
    w = world_for(program)
    zs, gaps = [], []

    def see(t, row):
        zs.append(float(w.data.xpos[w.partner.body][2]))
        gaps.append(float(w.data.xpos[w.partner.body][0] - w.data.xpos[w.robot_body][0]))

    return w, TestRun(w, program).run(on_sample=see), zs, gaps


def test_partner_builds_and_settles(tmp_path):
    w = _world(tmp_path, "\n[partner]\nstart = [4, 4, 0]\n")
    m = w.model
    bodies = [m.body(b).name for b in range(m.nbody) if m.body(b).name.startswith("partner.")]
    assert bodies == ["partner.chassis", "partner.rails", *(f"partner.{s}_{i}" for s in ("left", "right")
                                                            for i in (1, 2, 3))]
    chassis = m.body("partner.chassis").id
    # not the robot: its own root, none of its treads or bindings
    assert w.robot_body_name == "drive.chassis" and m.body_rootid[chassis] == chassis != m.body_rootid[w.robot_body]
    assert all(not g.startswith("partner.") for g in w.parts.tread_geoms)
    assert all(b["joint"].startswith("dt.") for b in w.parts.bindings["motor"])
    assert m.joint("partner.free").type == mujoco.mjtJoint.mjJNT_FREE
    assert float(m.body_subtreemass[chassis]) == pytest.approx(60.0)
    # every colliding geom touches the robot, the field and balls; its own bodies never touch each other
    mine = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == chassis]
    colliding = [g for g in mine if m.geom_contype[g] or m.geom_conaffinity[g]]
    assert len(colliding) == 1 + 4 + 6       # the frame, 4 bumpers, 6 wheels
    assert all((m.geom_contype[g], m.geom_conaffinity[g]) == (3, 3) for g in colliding)
    partner_bodies = {m.body(n).id for n in bodies}
    excluded = {frozenset((int(a), int(b))) for a, b in zip(m.exclude_signature >> 16, m.exclude_signature & 0xFFFF)}
    assert all(frozenset((a, b)) in excluded for a in partner_bodies for b in partner_bodies if a < b)
    wheels = w.parts.partner.wheels
    # braked: each wheel shares its side's back-EMF damping, 2 CIMs at 10.71:1, 90 % efficient, over 3 wheels
    assert len(wheels) == 6
    assert all(m.dof_damping[m.jnt_dofadr[m.joint(j).id]] == pytest.approx(0.293, abs=0.001) for j in wheels)
    pal = palette(w, [])
    assert pal["partner"] is True and set(SIGNALS) <= set(pal["signals"])
    assert not any(j["name"].startswith("partner.") for j in pal["joints"])     # no block turns its wheels

    _settle(w, 1.0)
    assert w.partner.height() == pytest.approx(0.0, abs=0.02)
    assert w.partner.tilt() < 1.0 and w.partner.moved() == pytest.approx(0.0, abs=0.02)
    assert float(w.data.qvel[m.jnt_dofadr[m.joint("partner.free").id] + 2]) == pytest.approx(0.0, abs=1e-3)
    assert w.data.xpos[chassis][:2] == pytest.approx([4.0, 4.0], abs=1e-3)

    w.move_object("partner.chassis", 5.0, 3.0, 0.2, 90.0)      # moved and lifted: the readings follow
    assert w.partner.height() == pytest.approx(0.2 / IN, abs=0.1)
    assert w.partner.moved() == pytest.approx(math.hypot(1.0, 1.0) / IN, abs=0.1)
    session = SimpleNamespace(world=w)
    assert resolve_signal("partner.height", w).read(session) == pytest.approx(w.partner.height())
    assert set(w.partner.state()) == {"height_in", "tilt_deg", "moved_in"}
    assert set(SIGNALS) <= set(signals(w)["fixed"])
    w.reset()                                                   # back at its start, on its wheels
    assert w.data.xpos[chassis] == pytest.approx([4.0, 4.0, 0.0], abs=1e-9)
    _settle(w, 1.0)
    assert w.partner.height() == pytest.approx(0.0, abs=0.02) and w.partner.moved() < 0.05


def test_partner_touches_robot_and_field(tmp_path):
    w, r, zs, gaps = _push(tmp_path, brake=True)
    assert r.status == "finished", r.outcome
    assert max(r.signals["partner.moved"]) > 2.0
    # the robot's and the partner's bumpers meet (0.43 m from each center) and don't pass through each other
    assert min(gaps) > 0.86 - 0.02
    assert min(zs) > -0.005                                     # never falls through the carpet
    assert "partner.chassis" in w.contact_names()


def test_brake_resists_more_than_coast(tmp_path):
    (tmp_path / "brake").mkdir()
    (tmp_path / "coast").mkdir()
    braked = _push(tmp_path / "brake", brake=True)[1].signals["partner.moved"][-1]
    coasted = _push(tmp_path / "coast", brake=False)[1].signals["partner.moved"][-1]
    # measured: coast 122 in, braked 68 in, a ratio of about 1.8 (the same at 3, 6 and 12 V). Brake mode only shorts
    # the motors, so a braked stock drive resists through back-EMF (about 300 N·s/m on the whole partner), well under
    # its tires' grip: it rolls when shoved, just less far
    assert braked > 0.5 and coasted >= 1.5 * braked


def test_partner_overlap_errors(tmp_path):
    """Review Focus 3: a partner placed overlapping the robot's start or an element on the carpet is refused."""
    # the stand-in starts at (2.0, 4.035), heading 0: a 0.9 m outline; the partner's frame plus bumpers is 0.86 m
    with pytest.raises(PartError) as e:
        _world(tmp_path, "\n[partner]\nstart = [2.68, 4.035, 0]\n")
    assert str(e.value) == "[partner] starts inside the robot (their outlines overlap by 0.20 m)"
    _world(tmp_path, "\n[partner]\nstart = [2.88, 4.035, 0]\n")     # just touching: allowed

    (tmp_path / "gate.toml").write_text("""
[range]
name = "a portcullis"
ball = "2026-fuel"

[start.here]
pose = [2.5, 4.0, 0.0]

[[element]]
name = "portcullis"
kind = "sliding"
center = [5.0, 4.0]
axis = "z"
size = [0.1, 1.0, 0.8]
range = [0, 0.5]
mass = 2.27

[[element]]
name = "door"
kind = "hinged"
center = [5.0, 6.0]
hinge = "top"
size = [0.1, 1.0, 0.5]
base_height = 0.4
range_deg = [0, 90]
mass = 1
""", encoding="utf-8")
    on_range = '\n[range]\nfile = "gate.toml"\nstart = "here"\n'
    with pytest.raises(PartError) as e:
        _world(tmp_path, on_range + "\n[partner]\nstart = [5.2, 4.0, 0]\n")
    assert str(e.value) == "[partner] starts inside element portcullis (their outlines overlap by 0.10 m)"
    _world(tmp_path, on_range + "\n[partner]\nstart = [5.0, 6.0, 0]\n")     # under a raised panel: allowed

    for bad, msg in (("start = [4, 4, 0]\nmass_kg = 50", "unknown key 'mass_kg'; allowed: length, width, mass, "
                      "bumper_clearance, brake, start"),
                     ("mass = 50", r"\[partner\] needs start = \[x, y, heading_deg\]"),
                     ("start = [4, 4]", "start must be"),
                     ("start = [4, 4, 0]\nwidth = 0.2", "width must be at least 0.4 m"),
                     ("start = [4, 4, 0]\nmass = 2", "mass must be more than its drive's 2.5 kg"),
                     ("start = [4, 4, 0]\nbrake = 1", "brake must be true")):
        with pytest.raises(PartError, match=msg):
            _world(tmp_path, f"\n[partner]\n{bad}\n")


def test_no_partner_unchanged(tmp_path):
    w = _world(tmp_path)
    m = w.model
    assert not any(m.body(b).name.startswith("partner.") for b in range(m.nbody))
    assert w.partner is None and w.parts.partner is None
    pal = palette(w, [])
    assert pal["partner"] is False and not set(SIGNALS) & set(pal["signals"])
    assert not set(SIGNALS) & set(signals(w)["fixed"])
    with pytest.raises(ScenarioError, match="partner.height needs a partner robot"):
        resolve_signal("partner.height", w)
    with pytest.raises(ScenarioError, match="the partner signals are"):
        resolve_signal("partner.speed", w)


def test_setup_partner_ahead(tmp_path):
    robot = "\n[partner]\nstart = [8, 2, 0]\nmass = 50\nbrake = false\n"
    program = _program(tmp_path, "partner = { ahead_in = 60 }")
    assert program.setup["partner"] == {"ahead_in": 60.0}
    assert loads(dumps(program), program.path) == program
    raw = tomllib.loads(new_config("standin").replace("[robot]\n", "[robot]\nstart_pose = [3.0, 4.0, 30.0]\n", 1))
    out = setup_config(raw, program.setup)
    assert "partner" not in raw
    w = World(out, tmp_path)
    x, y, _ = w.data.xpos[w.partner.body]
    h = math.radians(30.0)
    assert (x, y) == pytest.approx((3.0 + 60 * IN * math.cos(h), 4.0 + 60 * IN * math.sin(h)), abs=1e-3)
    zz = w.data.xmat[w.partner.body].reshape(3, 3)[:, 0]
    assert math.degrees(math.atan2(zz[1], zz[0])) == pytest.approx(30.0, abs=1e-6)     # facing the robot's way

    # turned from the robot's heading, braked as asked, and the config's own [partner] keeps its other settings
    out = setup_config({**raw, **tomllib.loads(robot)}, {"partner": {"ahead_in": 60.0, "heading_deg": 180.0,
                                                                      "brake": True}})
    assert out["partner"]["start"][2] == pytest.approx(210.0)
    assert out["partner"]["mass"] == 50 and out["partner"]["brake"] is True
    assert "partner" not in setup_config({**raw, **tomllib.loads(robot)}, {"partner": False})
    (tmp_path / "f").mkdir()
    w = world_for(_program(tmp_path / "f", "partner = false", robot=robot))
    assert w.partner is None

    for bad, msg in (("partner = true", "must be a table like partner"), ("partner = { heading_deg = 90 }",
                      "needs ahead_in"), ('partner = { ahead_in = 60, side = "left" }', "'side' isn't something"),
                     ('partner = { ahead_in = 60, brake = "on" }', "brake must be true or false")):
        with pytest.raises(TestFileError, match=msg):
            _program(tmp_path, bad)


def test_setups_partner_overlap_errors_name_setup(tmp_path):
    """Fix 6: a partner Setup places inside the robot or an element is refused in Setup's words, not [partner]'s."""
    with pytest.raises(TestFileError) as e:
        world_for(_program(tmp_path, "partner = { ahead_in = 20 }"))
    assert str(e.value) == "the partner robot (Setup) starts inside the robot (their outlines overlap by 0.37 m)"
    (tmp_path / "gate.toml").write_text("""
[range]
name = "a portcullis"
ball = "2026-fuel"

[start.here]
pose = [2.5, 4.0, 0.0]

[[element]]
name = "portcullis"
kind = "sliding"
center = [5.0, 4.0]
axis = "z"
size = [0.1, 1.0, 0.8]
range = [0, 0.5]
mass = 2.27
""", encoding="utf-8")
    on_range = '\n[range]\nfile = "gate.toml"\nstart = "here"\n'
    with pytest.raises(TestFileError) as e:
        world_for(_program(tmp_path, "partner = { ahead_in = 106.3 }", robot=on_range))
    assert str(e.value) == ("the partner robot (Setup) starts inside element portcullis (their outlines overlap by "
                            "0.10 m)")
    # the config's own [partner], with Setup leaving it alone, keeps the [partner] words
    with pytest.raises(ValueError, match=r"\[partner\] starts inside the robot"):
        world_for(_program(tmp_path, "", robot="\n[partner]\nstart = [2.68, 4.035, 0]\n"))


def test_partner_conditions(tmp_path):
    expect_moved = '[[block]]\ndo = "expect"\nthat = { partner_moved = { at_least_in = 2.0 } }\n'
    program = _program(tmp_path, "partner = { ahead_in = 40 }",
                       '[[block]]\ndo = "drive"\nseconds = 2.0\nvolts = 6.0\n\n' + expect_moved)
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished" and r.checks[0]["passed"], r.outcome
    assert r.checks[0]["text"] == "the partner moved at least 2 in"

    program = _program(tmp_path, "partner = { ahead_in = 60 }", expect_moved)      # nothing pushes it
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed"
    assert r.checks[0]["failure"].startswith("expected the partner to move at least 2 in, but it moved 0.0 in")

    lifted = Cond("partner_lifted", {"at_least_in": 4.0, "level_within_deg": 5.0})
    assert describe(lifted) == "the partner is lifted at least 4 in (level within 5°)"
    assert describe(Cond("partner_lifted", {"at_least_in": 4.0, "level_within_deg": None})) == \
        "the partner is lifted at least 4 in"
    program = _program(tmp_path, "partner = { ahead_in = 60 }",
                       '[[block]]\ndo = "wait_until"\ntimeout = 0.5\n'
                       'until = { partner_lifted = { at_least_in = 4.0, level_within_deg = 5.0 } }\n')
    assert program.blocks[0].args["until"] == lifted
    assert loads(dumps(program), program.path) == program
    r = TestRun(world_for(program), program).run()
    assert r.status == "timed out"
    assert r.outcome.startswith("waiting for the partner to be lifted at least 4 in (level within 5°) timed out")
    program = _program(tmp_path, "partner = { ahead_in = 60 }",
                       '[[block]]\ndo = "expect"\nthat = { partner_lifted = { at_least_in = 4.0 } }\n')
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed"
    assert r.checks[0]["failure"] == "expected the partner lifted at least 4 in, but it was 0 in up, 0.0° from level"

    w = world_for(program)
    _settle(w, 0.5)
    watch = Watch(w, program)
    x, y, z = w.data.xpos[w.partner.body]
    w.move_object("partner.chassis", x, y, z + 0.12, 0.0)          # 4.7 in up, level
    assert watch.holds(lifted)
    w.move_object("partner.chassis", x, y, z + 0.05, 0.0)          # 2 in up
    assert not watch.holds(lifted)
    assert not watch.holds(Cond("partner_moved", {"at_least_in": 0.05}))     # lifted straight up: not moved

    with pytest.raises(TestFileError, match="at_least_in must be more than 0"):
        _program(tmp_path, "", '[[block]]\ndo = "expect"\nthat = { partner_moved = { at_least_in = 0 } }\n')
    with pytest.raises(TestFileError, match="level_within_deg must be more than 0"):
        _program(tmp_path, "", '[[block]]\ndo = "expect"\n'
                               'that = { partner_lifted = { at_least_in = 2, level_within_deg = -1 } }\n')
    alone = _program(tmp_path, "", expect_moved)
    assert [p["message"] for p in problems(alone, world_for(alone))] == [
        "there's no partner robot; add one in Setup"]
