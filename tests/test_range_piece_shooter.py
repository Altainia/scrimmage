"""A rigid piece shot in the world: the stock `pinch_shooter` part, `[shooter] kind = "piece"` with `piece = "note"`,
the pinch model (scrimmage/launcher/pinch.py) on the robot's battery, the NOTE leaving face-parallel to the channel;
the refusals in words; and `launcher aim` aiming a NOTE through the 2024 SPEAKER."""

from __future__ import annotations

import copy
import math
import time
from pathlib import Path

import mujoco
import numpy as np
import pytest
import tomlkit

from range_fixtures import KITBOT, range_cfg, shooter_cfg
from scrimmage.launcher import flight6
from scrimmage.launcher.pinch import single_shot
from scrimmage.parts.stock import build_stock
from scrimmage.protocol import HalState
from scrimmage.testrange import aim, goal_fit
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.spec import load_range
from scrimmage.world import World

RANGE_2024 = Path(__file__).resolve().parent / "data" / "range_2024" / "2024.toml"
# The blue SPEAKER's approach: 3 m out from the wall, facing it.
START = "\n[start.front]\npose = [3.0, 5.5446, 180.0]\n"
# The pinch shooter on the kitbot's top mount (its defaults: 4 in wheels, a 1.25 in gap, a fixed 45 deg channel),
# the bottom wheel on PWM 2 and the top on PWM 3; a loader roller (PWM 5) carries the NOTE from the hopper into it.
PINCH = {"name": "shooter", "stock": "pinch_shooter", "attach": "drive.top", "pos": [-0.1, 0.0, 0.3],
         "ids": {"bottom": "PWM:2", "top": "PWM:3"}}
LOADER = {"name": "loader", "stock": "roller", "attach": "drive.top", "pos": [-0.35, 0.0, 0.2], "gearing": 10.0,
          "ids": {"spin": "PWM:5"}}
PIECE_SHOOTER = {"kind": "piece", "part": "shooter", "piece": "note", "wheels": {"preset": "60A"},
                 "stage": [{"name": "loader", "part": "loader", "path_length": 0.30}]}


@pytest.fixture(scope="module")
def note_dir(tmp_path_factory) -> Path:
    """tests/data/range_2024/2024.toml with a start in front of the blue SPEAKER, as note.toml."""
    d = tmp_path_factory.mktemp("note")
    (d / "note.toml").write_text(RANGE_2024.read_text(encoding="utf-8") + START, encoding="utf-8")
    return d


def note_cfg(part: dict | None = None, shooter: dict | None = None, **overrides) -> dict:
    """The kitbot with PINCH (its keys updated by `part`) and LOADER on note.toml at the front start, one NOTE
    preloaded; [shooter] PIECE_SHOOTER updated by `shooter` (a None value removes that key)."""
    parts = copy.deepcopy(KITBOT) + [{**copy.deepcopy(PINCH), **(part or {})}, copy.deepcopy(LOADER)]
    sec = {**copy.deepcopy(PIECE_SHOOTER), **copy.deepcopy(shooter or {})}
    rng = {"file": "note.toml", "start": "front", "preload": 1, **overrides.pop("range", {})}
    return range_cfg(part=parts, range=rng, shooter={k: v for k, v in sec.items() if v is not None}, **overrides)


def pwm_hal(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


# ------------------------------------------------------------------------------------------------ the stock part

def test_pinch_shooter_part_builds():
    part = build_stock("pinch_shooter", {}, "t")
    assert set(part.slot_kinds()) == {"bottom", "top"}
    assert part.joint_names() == ["spin0", "spin1"]
    assert all(not g.collide for b in part.bodies for g in b.geoms)
    assert [m.name for m in part.mounts] == ["base"]
    wheels = {b.joint.name: b for b in part.bodies if b.joint is not None}
    assert wheels["spin0"].parent == wheels["spin1"].parent == "channel"
    np.testing.assert_allclose(wheels["spin0"].joint.axis, [0.0, 1.0, 0.0])     # the bottom wheel's top goes downrange
    np.testing.assert_allclose(wheels["spin1"].joint.axis, [0.0, -1.0, 0.0])    # the top wheel's bottom does
    gap = (wheels["spin1"].pos[2] - 0.1016 / 2) - (wheels["spin0"].pos[2] + 0.1016 / 2)
    assert gap == pytest.approx(0.03175)
    art = build_stock("pinch_shooter", {"tilt": "articulated", "tilt_range": [30.0, 60.0]}, "t")
    assert set(art.slot_kinds()) == {"bottom", "top", "tilt", "tilt_encoder"}
    tilt = next(b.joint for b in art.bodies if b.joint is not None and b.joint.name == "tilt")
    assert tilt.range == pytest.approx((0.0, 30.0))


# ------------------------------------------------------------------------------------------------ firing

def test_a_note_leaves_at_the_models_speed_in_the_channels_direction(note_dir):
    """The wheels spun up on PWM 2 and 3, the loader feeds the preloaded NOTE: it leaves the robot as a free body at the
    pinch model's exit speed (the same pass run alone at the wheels' speeds when it was fed, within 5%), along the
    channel's x, its faces across the channel's z (the NOTE's axis along it), and it flies."""
    w = World(note_cfg(), note_dir)
    rt = w.test_range
    balls = rt.balls
    assert rt.ball_path.held() == 1
    for _ in range(1000):
        w.step(pwm_hal(p2=0.8, p3=0.8))
    m, d = w.model, w.data
    fed_rps = None
    launched = None
    for _ in range(2000):
        before = [s.speed / (2 * math.pi) for s in rt.shooter.launcher.shafts]
        w.step(pwm_hal(p2=0.8, p3=0.8, p5=1.0))
        if fed_rps is None and rt.shooter.launcher.in_contact():
            fed_rps = before
        if np.any(balls.field):
            launched = int(np.flatnonzero(balls.field)[0])
            break
    assert launched is not None and fed_rps is not None
    log = balls.log[launched]
    assert log.launcher_status == "launched" and balls.fired == 1
    assert log.hood_deg == pytest.approx(45.0)
    alone = single_shot(rt.shooter.config.setup, tuple(fed_rps), kP=0.0)
    assert log.exit_speed == pytest.approx(alone.speed, rel=0.05)
    vel = balls._qvel[launched, :3]
    assert np.linalg.norm(vel) == pytest.approx(log.exit_speed, rel=0.05)
    chan = d.xmat[m.body("shooter.channel").id].reshape(3, 3)
    assert math.degrees(math.acos(vel @ chan[:, 0] / np.linalg.norm(vel))) < 1.0
    body = d.xmat[balls.bodies[launched]].reshape(3, 3)
    assert abs(body[:, 2] @ chan[:, 2]) == pytest.approx(1.0, abs=1e-3)     # the ring's axis across the gap
    assert abs(log.spin_rpm) < 1.0                                          # equal wheels: no pitch spin
    apex = float(balls._qpos[launched, 2])
    z0 = apex
    for _ in range(300):
        w.step(pwm_hal())
        apex = max(apex, float(balls._qpos[launched, 2]))
    assert apex > z0 + 0.5 and balls.state[launched] != BallState.STUCK


# ------------------------------------------------------------------------------------------------ refusals

def test_kind_piece_needs_a_pinch_shooter_part(note_dir):
    cfg = shooter_cfg(range={"file": "note.toml", "start": "front"},
                      shooter={"kind": "piece", "piece": "note", "lanes": None, "hood": None, "wheels": None})
    with pytest.raises(ValueError, match=r"part 'launcher' is a stock launcher part; a piece shooter "
                                         r"\(kind = \"piece\"\) runs in a stock pinch_shooter part"):
        World(cfg, note_dir)


def test_an_unknown_piece_is_refused(note_dir):
    with pytest.raises(ValueError, match=r"piece 'cone': no such piece on the range .*; its pieces: note"):
        World(note_cfg(shooter={"piece": "cone"}), note_dir)
    with pytest.raises(ValueError, match=r"kind = \"piece\" needs piece = \"<kind name>\""):
        World(note_cfg(shooter={"piece": None}), note_dir)


MIXED = """
[range]
name = "fuel and notes"

[piece.fuel]
ball = "2026-fuel"

[piece.note]
preset = "2024-note"

[start.front]
pose = [3.0, 4.0, 180.0]
"""


def test_a_ball_piece_is_refused(note_dir):
    (note_dir / "mixed.toml").write_text(MIXED, encoding="utf-8")
    with pytest.raises(ValueError, match=r"piece 'fuel' is a ball; a pinch shooter shoots a rigid piece"):
        World(note_cfg(shooter={"piece": "fuel"}, range={"file": "mixed.toml", "preload": 0}), note_dir)
    # the robot holds the field's ball, so it can't take the NOTE
    with pytest.raises(ValueError, match=r"piece 'note': the robot holds the range's 'fuel'"):
        World(note_cfg(range={"file": "mixed.toml", "preload": 0}), note_dir)


def test_a_gap_the_note_slips_through_is_refused(note_dir):
    with pytest.raises(ValueError, match=r"gap 0.06 is no narrower than the piece 'note''s thickness 0.0508, so the "
                                         r"wheels can't grip it"):
        World(note_cfg(part={"gap": 0.06}), note_dir)
    with pytest.raises(ValueError, match=r"wheel_x = 0.15 puts the piece between the wheels"):
        World(note_cfg(part={"wheel_x": 0.15}), note_dir)
    with pytest.raises(ValueError, match=r"lanes: a pinch shooter feeds one piece at a time"):
        World(note_cfg(shooter={"lanes": [0.0]}), note_dir)


# ------------------------------------------------------------------------------------------------ aiming a NOTE

AIM_DISTANCES = [1.5, 2.5, 3.0]


@pytest.fixture(scope="module")
def note_aim(note_dir):
    """`launcher aim` on the blue SPEAKER from an articulated pinch shooter (30 to 60 deg, 5 deg steps): the model, its
    rows and the seconds they took."""
    cfg = note_cfg(part={"tilt": "articulated", "tilt_range": [30.0, 60.0], "ids": {**PINCH["ids"], "tilt": "PWM:4"}},
                   range={"preload": 0})
    path = note_dir / "aim.toml"
    path.write_text(tomlkit.dumps(cfg), encoding="utf-8")
    start = time.perf_counter()
    model = aim.load_model(path, "speaker_blue", hood_step_deg=5.0)
    rows = aim.aim_table(path, "speaker_blue", AIM_DISTANCES, hood_step_deg=5.0, model=model,
                         on_unreachable=lambda msg: None)
    return path, model, rows, time.perf_counter() - start


def test_note_aim_scores_through_the_speaker(note_aim):
    """At least one scoring row; each, flown again by flight6 from the pinch's exit on the field, crosses the
    SPEAKER's opening and goal_fit.fit scores it (all of the NOTE through, nothing hit first)."""
    path, model, rows, seconds = note_aim
    print(f"\nNOTE aim, {len(AIM_DISTANCES)} SPEAKER distances, {len(model.hood_angles)} tilts: {seconds:.1f} s")
    print(aim.to_csv(rows))
    scoring = [r for r in rows if r.status == "ok"]
    assert scoring, aim.to_csv(rows)
    assert aim.calibration(model).startswith("UNCALIBRATED") and "piece stiffness" in aim.calibration(model)
    s = model.goal.scoring_surface()
    kind = load_range("note.toml", path.parent).kinds["note"]
    for row in scoring:
        res = aim.piece_flight(model, row)
        assert isinstance(res, flight6.Flight6Result) and res.crossing is not None
        assert res.crossing[1] == pytest.approx(row.flight_s, abs=2e-3)
        assert goal_fit.fit(res, s, "speaker_blue", model.boxes, goal_fit.kind_corners(kind)).scored, row
        assert model.hood_angles[0] <= row.hood_deg <= model.hood_angles[-1]


def test_note_aim_refuses_a_level_goal(note_dir):
    (note_dir / "hub.toml").write_text("""
[range]
name = "a NOTE hub"

[piece.note]
preset = "2024-note"

[[goal]]
name = "hub"
kind = "note"
pos = [1.0, 4.0]
rim_height = 1.8
opening = { kind = "rect", width = 1.0, height = 1.0 }

[start.front]
pose = [4.0, 4.0, 180.0]
""", encoding="utf-8")
    path = note_dir / "hub_robot.toml"
    path.write_text(tomlkit.dumps(note_cfg(range={"file": "hub.toml", "preload": 0})), encoding="utf-8")
    with pytest.raises(aim.AimError, match=r"a pinch shooter's table aims through a goal's opening that isn't level"):
        aim.load_model(path, "hub")
