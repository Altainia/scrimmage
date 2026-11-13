"""Sensors in the world (scrimmage/sensors): beam breaks, distance and color sensors that see game pieces."""

from __future__ import annotations

import copy
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from range_fixtures import KITBOT, drive, range_cfg, spares_world  # noqa: E402
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.sensors.colors import NAMED_COLORS, nearest_color  # noqa: E402
from scrimmage.sensors import runtime  # noqa: E402
from scrimmage.sensors.runtime import CONE_RING, SensorRuntime, color_values, cone_directions  # noqa: E402
from scrimmage.sensors.spec import (CAN_FOV_DEG, CANRANGE_THRESHOLD, DEVICE_MAX_RANGE,  # noqa: E402
                                    SensorError, device_key, parse_sensors)
from scrimmage.testrange.build import BALL_BITS  # noqa: E402
from scrimmage.testrange.pieces import ball_kind, piece_color  # noqa: E402
from scrimmage.world import World  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INTAKE = ROOT / "examples" / "blocks" / "intake"
NO_TOUCH = {"contype": 0, "conaffinity": 0}
# A beam across the intake's mouth (examples/blocks/intake/parts/intake): on its frame (origin on the floor at the
# bumper's face), between the hopper's lip (x 0.22) and the roller (x 0.35), 5 cm up: a 2026 FUEL rolling in on the
# carpet crosses it, and one pausing on top of the lip (its center 0.25 m out, 0.13 m up) is clear of it.
MOUTH = {"name": "mouth", "kind": "beam", "body": "intake.frame", "from": [0.32, 0.17, 0.05],
         "to": [0.32, -0.17, 0.05], "dio": 4}   # DIO 0 to 3 are the drive's encoders


def intake_world(*sensors) -> World:
    cfg = tomllib.loads((INTAKE / "intake-standin.toml").read_text(encoding="utf-8"))
    if sensors:
        cfg["sensor"] = [copy.deepcopy(s) for s in sensors]
    return World(cfg, INTAKE)


def step(w, n=1):
    hal = HalState()
    for _ in range(n):
        w.step(hal)


def world_point(w, body: str, local) -> np.ndarray:
    b = w.model.body(body).id
    return w.data.xpos[b] + w.data.xmat[b].reshape(3, 3) @ np.asarray(local, dtype=float)


def body_point(w, body: str, world) -> list[float]:
    b = w.model.body(body).id
    return (w.data.xmat[b].reshape(3, 3).T @ (np.asarray(world, dtype=float) - w.data.xpos[b])).tolist()


def body_dir(w, body: str, world) -> list[float]:
    b = w.model.body(body).id
    return (w.data.xmat[b].reshape(3, 3).T @ np.asarray(world, dtype=float)).tolist()


def attach(w, *tables) -> SensorRuntime:
    """Sensors made after the world is built (so a test can place them from where things are), stepped by it."""
    w.sensors = SensorRuntime(w, parse_sensors(list(tables)))
    return w.sensors


# ---------------------------------------------------------------------------------------------------- beam breaks

def test_a_beam_across_the_intake_mouth_breaks_while_the_ball_rolls_through():
    """The intake example drives at its ball with the roller on (the ledger golden's run): the beam is clear before
    the ball reaches the mouth, broken while it rolls through, and clear again once it sits in the hopper."""
    w = intake_world(MOUTH)
    w.reset()
    seen = []
    for _ in range(3000):
        drive(w, 0.001, p0=0.33, p1=-0.33, p2=0.5)
        seen.append(w.sensors.reading("mouth").on)
    assert not any(seen[:300]), "clear while the robot drives at the ball"
    assert any(seen), "broken while the ball rolls through"
    first = seen.index(True)
    last = len(seen) - 1 - seen[::-1].index(True)
    assert all(seen[first:last + 1]), "broken the whole time it crosses, no flicker"
    assert not any(seen[last + 1:]) and last < len(seen) - 200, "clear again with the ball in the hopper"
    ball = w.test_range.balls.position(0)
    hopper = world_point(w, "intake.frame", [0.12, 0.0, 0.1])
    assert abs(ball[0] - hopper[0]) < 0.1, "the ball ended in the hopper"


def test_a_beam_drawn_through_a_robot_geom_ignores_the_robot_and_sees_a_piece_in_it():
    """Review Focus 1: both ends inside the chassis's plate. It never reads broken by itself, and a piece crossing
    the beam (overlapping the plate, as a non-colliding piece can) breaks it."""
    w = spares_world(range_cfg())
    m = w.model
    plate = next(g for g in range(m.ngeom) if m.geom_bodyid[g] == m.body("drive.chassis").id
                 and int(m.geom_type[g]) == 6)    # the frame plate, a box
    pos, half = m.geom_pos[plate], m.geom_size[plate]
    a, b = pos + [-0.8 * half[0], 0.0, 0.0], pos + [0.8 * half[0], 0.0, 0.0]
    attach(w, {"name": "through", "kind": "beam", "body": "drive.chassis", "from": a.tolist(), "to": b.tolist()})
    step(w, 50)
    assert not w.sensors.reading("through").on
    w.test_range.balls.put(0, world_point(w, "drive.chassis", pos), NO_TOUCH)
    step(w)
    assert w.sensors.reading("through").on


def _settled_ball(w) -> tuple[np.ndarray, float]:
    """Ball 0 resting on the carpet 0.6 m to the robot's left; its center and radius."""
    r = w.test_range.balls.r
    at = world_point(w, "drive.chassis", [0.0, 0.6, 0.0])
    w.test_range.balls.put(0, [at[0], at[1], r], BALL_BITS)
    step(w, 500)
    return w.test_range.balls.position(0).copy(), r


@pytest.mark.parametrize("end", ["to", "from"])
def test_a_piece_resting_on_the_beam_end_reads_broken_without_flicker(end):
    """Review Focus 2: a ball at rest whose surface is exactly the beam's end point (its `to`, or its `from`) reads
    broken on every step; one 5 mm past the end reads clear."""
    w = spares_world(range_cfg())
    c, r = _settled_ball(w)
    side = np.array([0.0, 1.0, 0.0])
    near, far = c - r * side, c - (r + 0.3) * side
    a, b = (far, near) if end == "to" else (near, far)
    beyond_a, beyond_b = (far, near - 0.005 * side) if end == "to" else (near - 0.005 * side, far)
    attach(w, {"name": "touch", "kind": "beam", "body": "drive.chassis", "from": body_point(w, "drive.chassis", a),
               "to": body_point(w, "drive.chassis", b)},
           {"name": "short", "kind": "beam", "body": "drive.chassis",
            "from": body_point(w, "drive.chassis", beyond_a), "to": body_point(w, "drive.chassis", beyond_b)})
    seen, short = [], []
    for _ in range(300):
        step(w)
        seen.append(w.sensors.reading("touch").on)
        short.append(w.sensors.reading("short").on)
    assert all(seen)
    assert not any(short)


# ----------------------------------------------------------------------------------------------- distance sensors

def _ahead_world(**sensor):
    """The kitbot with a sensor at its front bumper's face, looking ahead, and spare balls."""
    w = spares_world(range_cfg())
    front = world_point(w, "drive.chassis", [0.42, 0.0, 0.0])
    front[2] = 0.12
    attach(w, {"name": "s", "body": "drive.chassis", "at": body_point(w, "drive.chassis", front),
               "toward": body_dir(w, "drive.chassis", [1.0, 0.0, 0.0]), **sensor})
    return w, front


def test_a_distance_sensor_reads_a_ball_half_a_meter_ahead_and_the_max_with_nothing_in_range():
    w, front = _ahead_world(kind="distance", max_range=2.0)
    step(w)
    got = w.sensors.reading("s")
    assert got.distance == pytest.approx(2.0) and not got.on, "nothing in range: the max"
    r = w.test_range.balls.r
    w.test_range.balls.put(0, front + [0.5 + r, 0.0, 0.0], BALL_BITS)
    step(w)
    got = w.sensors.reading("s")
    assert got.distance == pytest.approx(0.5, abs=0.001)
    assert got.on, "closer than the threshold (half of max_range)"


def test_a_distance_sensor_that_sees_anything_reads_the_field_but_never_the_robot():
    w = spares_world(range_cfg())
    at = world_point(w, "drive.chassis", [0.0, 0.0, 0.0])
    at[2] = 0.3
    # Straight down through the chassis: the robot is ignored, so the carpet 0.3 m below is what it reads.
    attach(w, {"name": "down", "kind": "distance", "body": "drive.chassis", "sees": "anything",
               "at": body_point(w, "drive.chassis", at), "toward": body_dir(w, "drive.chassis", [0, 0, -1])},
           {"name": "pieces", "kind": "distance", "body": "drive.chassis",
            "at": body_point(w, "drive.chassis", at), "toward": body_dir(w, "drive.chassis", [0, 0, -1])})
    step(w)
    assert w.sensors.reading("down").distance == pytest.approx(0.3, abs=0.005)
    assert w.sensors.reading("pieces").distance == pytest.approx(2.0), "pieces only: the carpet isn't one"


def test_the_field_hides_a_piece_behind_it():
    """A wall between a pieces-only sensor and a ball hides the ball (a real beam can't see through a wall); the
    same ball on this side of the wall is seen, and a sensor that sees anything reads the wall."""
    w = spares_world(range_cfg())
    rear = world_point(w, "drive.chassis", [-0.42, 0.0, 0.0])
    rear[2] = 0.12
    back = body_dir(w, "drive.chassis", [-1.0, 0.0, 0.0])
    at = body_point(w, "drive.chassis", rear)
    attach(w, {"name": "s", "kind": "distance", "body": "drive.chassis", "at": at, "toward": back},
           {"name": "any", "kind": "distance", "body": "drive.chassis", "at": at, "toward": back, "sees": "anything"},
           {"name": "beam", "kind": "beam", "body": "drive.chassis", "from": at,
            "to": body_point(w, "drive.chassis", [-0.5, rear[1], rear[2]])})
    assert 0.5 < rear[0] < 1.9, "the blue alliance wall (x = 0) is within 2 m behind the robot"
    r = w.test_range.balls.r
    w.test_range.balls.put(0, [-0.3, rear[1], rear[2]], NO_TOUCH)     # behind the wall
    step(w)
    assert w.sensors.reading("s").distance == pytest.approx(2.0) and not w.sensors.reading("beam").on
    wall = w.sensors.reading("any").distance
    assert wall < rear[0] + 0.01
    w.test_range.balls.put(0, [rear[0] - 0.5 - r, rear[1], rear[2]], NO_TOUCH)    # on this side
    step(w)
    assert w.sensors.reading("s").distance == pytest.approx(0.5, abs=0.001)


def test_reading_an_unknown_sensor():
    w, _ = _ahead_world(kind="distance")
    with pytest.raises(SensorError, match='the robot has no sensor "nope"; its sensors: s'):
        w.sensors.reading("nope")


def test_a_clear_sensor_casts_one_ray_a_step():
    w, _ = _ahead_world(kind="distance")
    before = w.sensors.casts
    step(w, 10)
    assert w.sensors.casts - before == 10


def test_a_cone_casts_seventeen_rays_a_step_and_a_single_ray_sensor_one():
    w, _ = _ahead_world(kind="distance", fov_deg=27)
    before = w.sensors.casts
    step(w, 4)
    assert w.sensors.casts - before == 4 * (1 + 2 * CONE_RING)


def test_cone_directions_are_unit_rings_at_a_quarter_and_half_the_angle_off_the_axis():
    axis = np.array([0.0, 0.6, 0.8])
    d = cone_directions(axis, 40.0)
    assert d.shape == (17, 3) and np.allclose(np.linalg.norm(d, axis=1), 1.0) and np.allclose(d[0], axis)
    off = np.degrees(np.arccos(d[1:] @ axis))
    assert np.allclose(off[:8], 10.0) and np.allclose(off[8:], 20.0)
    assert len({tuple(np.round(r, 6)) for r in d}) == 17, "no two rays alike (the rings are offset)"
    for a in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]):     # an axis along any axis has a basis
        assert np.allclose(np.degrees(np.arccos(cone_directions(a, 20.0)[1:] @ np.asarray(a)))[8:], 10.0)


def _cone_reading(fov, y, x=1.0):
    """The reading of a sensor ahead of the bumper with a ball at `x` ahead and `y` to the side of its axis."""
    w, front = _ahead_world(kind="distance", max_range=3.0, **({"fov_deg": fov} if fov else {}))
    r = w.test_range.balls.r
    w.test_range.balls.put(0, front + [x, y, 0.0], BALL_BITS)
    step(w)
    return w.sensors.reading("s"), r


def test_a_cone_sees_a_piece_off_the_axis_inside_it_and_not_outside_it():
    r = _cone_reading(None, 0.0)[1]
    y = r + 0.05    # clear of the axis ray, 6.6 deg off at 1 m
    assert _cone_reading(None, y)[0].distance == pytest.approx(3.0), "one ray misses it"
    assert _cone_reading(27, y)[0].distance < 3.0, "inside the cone's 13.5 deg half angle"
    assert _cone_reading(27, 0.5)[0].distance == pytest.approx(3.0), "26.6 deg off: outside"
    assert _cone_reading(5, y)[0].distance == pytest.approx(3.0), "a narrow cone doesn't reach it"


def test_a_cone_reads_the_nearest_ray_along_its_own_ray():
    got, r = _cone_reading(27, 0.15)
    center = np.hypot(1.0, 0.15)
    assert center - r - 1e-3 <= got.distance <= center, "a hit on the ball's near surface, along a slanted ray"
    ahead = _cone_reading(27, 0.0)[0].distance
    assert ahead == pytest.approx(1.0 - r, abs=0.001), "on the axis the axis ray is nearest: as one ray reads it"
    assert ahead == pytest.approx(_cone_reading(None, 0.0)[0].distance, abs=1e-9)


def test_a_sensor_that_runs_out_of_casts_warns_once_naming_it(monkeypatch):
    """A beam on the drive's rails (not the chassis) straight down through the chassis's plate: with one cast a
    step, the plate uses it up. The world's warnings say so once, naming the sensor; a full MAX_CASTS gets past."""
    w = spares_world(range_cfg())
    m = w.model
    plate = next(g for g in range(m.ngeom) if m.geom_bodyid[g] == m.body("drive.chassis").id
                 and int(m.geom_type[g]) == 6)    # the frame plate, a box
    pos, half = m.geom_pos[plate], m.geom_size[plate]
    top, under = pos + [0.0, 0.0, half[2] + 0.02], pos - [0.0, 0.0, half[2] + 0.005]
    rails = m.body("dt.rails").id
    assert rails != m.body("drive.chassis").id
    beam = {"name": "down", "kind": "beam", "body": "dt.rails",
            "from": body_point(w, "dt.rails", world_point(w, "drive.chassis", top)),
            "to": body_point(w, "dt.rails", world_point(w, "drive.chassis", under))}
    attach(w, beam)
    step(w, 5)
    assert not any("down" in x for x in w.warnings), "every surface in the way recast within MAX_CASTS"
    monkeypatch.setattr(runtime, "MAX_CASTS", 1)
    step(w, 5)
    said = [x for x in w.warnings if '"down"' in x]
    assert said == ['[[sensor]] "down": the robot is in its way: its ray met 1 of the robot\'s surfaces in one step, '
                    "the most a sensor looks past, so it read nothing beyond them; move it or point it clear of the "
                    "robot"], w.warnings


def test_a_cone_with_several_rays_out_of_casts_warns_once(monkeypatch):
    """A cone looking down through the chassis's plate from the rails: with one cast a step, most of its 17 rays run
    out, and the world's warnings name the sensor once, not once a ray or a step."""
    w = spares_world(range_cfg())
    m = w.model
    plate = next(g for g in range(m.ngeom) if m.geom_bodyid[g] == m.body("drive.chassis").id
                 and int(m.geom_type[g]) == 6)
    top = m.geom_pos[plate] + [0.0, 0.0, m.geom_size[plate][2] + 0.02]
    attach(w, {"name": "cone", "kind": "distance", "body": "dt.rails", "fov_deg": 20.0,
               "at": body_point(w, "dt.rails", world_point(w, "drive.chassis", top)),
               "toward": body_dir(w, "dt.rails", [0.0, 0.0, -1.0])})
    step(w, 3)
    assert not any('"cone"' in x for x in w.warnings), "every ray recast within MAX_CASTS"
    monkeypatch.setattr(runtime, "MAX_CASTS", 1)
    step(w, 5)
    assert len([x for x in w.warnings if '"cone"' in x]) == 1, w.warnings


# -------------------------------------------------------------------------------------------------- color sensors

TWO_COLORS = """
[range]
name = "Red and blue cargo"

[piece.red_cargo]
ball = "2022-cargo"
color = "red"

[piece.blue_cargo]
ball = "2022-cargo"
color = [0.1, 0.2, 0.9]

[[floor_balls]]
at = [[8.0, 1.0]]
kind = "red_cargo"

[[floor_balls]]
at = [[8.0, 7.0]]
kind = "blue_cargo"
"""


def _two_color_world(tmp_path):
    (tmp_path / "two.toml").write_text(TWO_COLORS, encoding="utf-8")
    cfg = {"part": copy.deepcopy(KITBOT), "range": {"file": "two.toml"},
           "battery": {"nominal_voltage": 12.0, "resistance": 0.0}, "robot": {"start_pose": [3.0, 4.0, 0.0]}}
    w = World(cfg, tmp_path)
    front = world_point(w, "drive.chassis", [0.42, 0.0, 0.0])
    front[2] = 0.12
    attach(w, {"name": "eye", "kind": "color", "body": "drive.chassis", "at": body_point(w, "drive.chassis", front),
               "toward": body_dir(w, "drive.chassis", [1.0, 0.0, 0.0]), "i2c": "mxp"})
    return w, front


def test_a_color_sensor_names_the_color_of_the_piece_in_front_and_two_kinds_read_differently(tmp_path):
    w, front = _two_color_world(tmp_path)
    balls = w.test_range.balls
    r = balls.r
    names = list(balls.kind)
    red, blue = names.index("red_cargo"), names.index("blue_cargo")
    balls.put(red, front + [0.03 + r, 0.0, 0.0], BALL_BITS)
    step(w)
    got = w.sensors.reading("eye")
    assert got.on and got.color_name == "red" and got.distance == pytest.approx(0.03, abs=0.001)
    red_values = color_values(got)
    assert red_values["Red"] > red_values["Blue"]
    balls.park(red)
    balls.put(blue, front + [0.03 + r, 0.0, 0.0], BALL_BITS)
    step(w)
    got = w.sensors.reading("eye")
    assert got.on and got.color_name == "blue" and got.color == (0.1, 0.2, 0.9)
    assert color_values(got)["Blue"] > color_values(got)["Red"]
    # The field's color is the drawn color too.
    m = w.model
    assert m.geom_rgba[m.body(f"ball.{red}").geomadr[0]].tolist() == pytest.approx([*NAMED_COLORS["red"], 1.0])


def test_a_color_sensor_with_nothing_in_front_sees_nothing(tmp_path):
    """Review Focus 4: low values and no color, not the carpet's or the robot's."""
    w, front = _two_color_world(tmp_path)
    above = front.copy()
    above[2] = 0.05
    attach(w, {"name": "eye", "kind": "color", "body": "drive.chassis",
               "at": body_point(w, "drive.chassis", front), "toward": body_dir(w, "drive.chassis", [1, 0, 0])},
           {"name": "floor", "kind": "color", "body": "drive.chassis", "sees": "anything",
            "at": body_point(w, "drive.chassis", above), "toward": body_dir(w, "drive.chassis", [0, 0, -1])},
           {"name": "back", "kind": "color", "body": "drive.chassis", "sees": "anything",
            "at": body_point(w, "drive.chassis", front), "toward": body_dir(w, "drive.chassis", [-1, 0, 0])})
    step(w)
    for name in ("eye", "floor", "back"):
        got = w.sensors.reading(name)
        assert not got.on and got.color is None and got.color_name is None, name
        values = color_values(got)
        assert max(values["Red"], values["Green"], values["Blue"]) < 50, name
        if name != "floor":
            assert values["IR"] < 50 and values["Proximity"] == 0, name
    assert w.sensors.reading("floor").distance == pytest.approx(0.05, abs=0.002), "the carpet, read as a distance"
    # IR and proximity follow what it hits, the carpet a gray (GRAY_REFLECTANCE), so they agree; colors stay dark.
    floor = color_values(w.sensors.reading("floor"))
    assert 200 < floor["IR"] < 600 and floor["Proximity"] > 0


def test_piece_colors():
    fuel = ball_kind("fuel", "2026-fuel")
    assert piece_color(fuel) == (0.95, 0.85, 0.1)
    assert nearest_color(piece_color(fuel)) == "yellow"
    assert nearest_color((0.92, 0.9, 0.84)) == "white"
    assert nearest_color((0.8, 0.0, 0.05)) == "red"


def test_a_fields_colors(tmp_path):
    """[range] color colors the range's own ball; a color that isn't named or [r, g, b] names the table and lists
    the named colors."""
    from scrimmage.testrange.spec import RangeError, load_range
    (tmp_path / "a.toml").write_text('[range]\nname = "a"\nball = "2026-fuel"\ncolor = "green"\n', encoding="utf-8")
    assert piece_color(load_range("a.toml", tmp_path).kinds["2026-fuel"]) == NAMED_COLORS["green"]
    (tmp_path / "b.toml").write_text('[range]\nname = "b"\n[piece.x]\nball = "2026-fuel"\ncolor = "pink"\n',
                                     encoding="utf-8")
    with pytest.raises(RangeError, match=r"\[piece.x\]: color 'pink' isn't one of the named colors \(red, blue"):
        load_range("b.toml", tmp_path)
    (tmp_path / "c.toml").write_text('[range]\nname = "c"\ncolor = "red"\n[piece.x]\nball = "2026-fuel"\n',
                                     encoding="utf-8")
    with pytest.raises(RangeError, match="color goes with its ball"):
        load_range("c.toml", tmp_path)


def test_a_setup_piece_in_place_of_a_red_and_blue_fields_first_kind_keeps_its_color(tmp_path):
    """The Test tab's Setup piece replaces the field's default kind, its first [piece.<name>] here: the new ball keeps
    the red of the table it replaced (B11: the field says which pieces are red), and the blue kind keeps its blue."""
    from scrimmage.testrange.spec import load_range
    (tmp_path / "rb.toml").write_text('[range]\nname = "rb"\n[piece.red_cargo]\nball = "2022-cargo"\ncolor = "red"\n'
                                      '[piece.blue_cargo]\nball = "2022-cargo"\ncolor = "blue"\n', encoding="utf-8")
    kinds = load_range("rb.toml", tmp_path, piece="2026-fuel").kinds
    assert kinds["red_cargo"].preset == "2026-fuel" and nearest_color(piece_color(kinds["red_cargo"])) == "red"
    assert nearest_color(piece_color(kinds["blue_cargo"])) == "blue"


# ------------------------------------------------------------------------------------------------- config checks

def test_a_sensor_on_a_body_the_robot_doesnt_have_lists_the_robots_bodies():
    """Review Focus 5."""
    with pytest.raises(ValueError) as e:
        intake_world({**MOUTH, "body": "intake.base"})
    msg = str(e.value)
    assert '[[sensor]] "mouth"' in msg and "intake.base" in msg
    assert "intake.frame" in msg and "drive.chassis" in msg and "intake.roller" in msg


@pytest.mark.parametrize("table, words", [
    ({"kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0]}, "needs a name"),
    ({"name": "x", "kind": "laser", "body": "a.b"}, 'kind must be "beam", "distance", "color" or "camera", not "laser"'),
    ({"name": "x", "body": "a.b"}, 'needs kind = "beam", "distance", "color" or "camera"'),
    ({"name": "x", "kind": "beam", "body": "a.b", "to": [1, 0, 0]}, "needs from"),
    ({"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0], "to": [1, 0, 0]},
     "from must be [x, y, z], 3 numbers, not [0, 0]"),
    ({"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [0, 0, 0]}, "same point"),
    ({"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0], "sees": "anything"},
     "unknown key 'sees'"),
    ({"name": "x", "kind": "distance", "body": "a.b", "at": [0, 0, 0], "toward": [0, 0, 0]}, "toward"),
    ({"name": "x", "kind": "distance", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "threshold": 3.0},
     "threshold"),
    ({"name": "x", "kind": "color", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "max_range": 0.2},
     "10 cm"),
    ({"name": "x", "kind": "color", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "i2c": "usb"}, "i2c"),
    ({"name": "x", "kind": "distance", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "volts_per_m": 1.0},
     "analog"),
])
def test_config_mistakes_name_the_sensor_and_the_key(table, words):
    with pytest.raises(SensorError) as e:
        parse_sensors([table])
    assert "[[sensor]]" in str(e.value) and words in str(e.value)


@pytest.mark.parametrize("name", ["front beam", "beam-1", "intake.beam", "eye!"])
def test_a_sensor_name_is_letters_digits_and_underscores(name):
    t = {"name": name, "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0]}
    with pytest.raises(SensorError) as e:
        parse_sensors([t])
    assert str(e.value) == f'[[sensor]] 1: name must be letters, digits and _ (like a part\'s name), not "{name}"'


def test_two_sensors_with_one_name():
    t = {"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0]}
    with pytest.raises(SensorError, match='two sensors named "x"'):
        parse_sensors([t, dict(t)])


def dist_table(**kw) -> dict:
    return {"name": "d", "kind": "distance", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], **kw}


def test_ctre_canrange_key():
    s, = parse_sensors([dist_table(ctre="CANrange:5")])
    assert s.device == "CANrange" and s.device_id == 5 and s.length == DEVICE_MAX_RANGE["CANrange"]
    assert device_key(s) == ("CAN", "CANrange (v6)[5]")


def test_grapple_lasercan_key():
    s, = parse_sensors([dist_table(grapple="LaserCAN:7")])
    assert s.device == "LaserCAN" and s.device_id == 7 and s.length == DEVICE_MAX_RANGE["LaserCAN"]
    assert device_key(s) == ("ScrimmageGrapple", "LaserCAN[7]")


def test_scrimmage_fast_canrange_key():
    s, = parse_sensors([dist_table(scrimmage="intake_range")])
    assert s.device == "ScrimmageCANrange" and s.device_id is None and s.device_name == "intake_range"
    assert s.length == DEVICE_MAX_RANGE["ScrimmageCANrange"] == 3.0 and s.threshold == CANRANGE_THRESHOLD
    assert device_key(s) == ("ScrimmageCANrange", "intake_range")
    assert parse_sensors([dist_table(scrimmage="r", max_range=0.3)])[0].threshold == 0.3   # capped, as a CANrange's
    assert parse_sensors([dist_table(scrimmage="r", analog=0)])[0].analog == 0


@pytest.mark.parametrize("extra, words", [
    ({"scrimmage": ""}, "non-empty device name without spaces"),
    ({"scrimmage": "a b"}, 'scrimmage = "a b"'),
    ({"scrimmage": 3}, "scrimmage = 3"),
    ({"scrimmage": "r", "ctre": "CANrange:5"}, "only one"),
    ({"scrimmage": "r", "grapple": "LaserCAN:5"}, "only one"),
])
def test_bad_scrimmage_canrange_keys(extra, words):
    with pytest.raises(SensorError) as e:
        parse_sensors([dist_table(**extra)])
    assert "[[sensor]] \"d\"" in str(e.value) and words in str(e.value)


def test_same_scrimmage_canrange_twice():
    a, b = dist_table(scrimmage="r"), {**dist_table(scrimmage="r"), "name": "e"}
    with pytest.raises(SensorError) as e:
        parse_sensors([a, b])
    assert '"d"' in str(e.value) and '"e"' in str(e.value) and 'ScrimmageCANrange "r"' in str(e.value)
    parse_sensors([a, {**b, "scrimmage": "s"}])     # another name is fine


def test_device_key_with_analog():
    s, = parse_sensors([dist_table(ctre="CANrange:5", analog=0)])
    assert s.device_id == 5 and s.analog == 0
    assert device_key(parse_sensors([dist_table(analog=0)])[0]) is None


def test_explicit_max_range_wins():
    s, = parse_sensors([dist_table(ctre="CANrange:5", max_range=1.0)])
    assert s.length == 1.0


@pytest.mark.parametrize("extra, words", [
    ({"ctre": "TalonFX:5"}, 'ctre = "TalonFX:5"'),
    ({"ctre": "CANrange:63"}, "CANrange:<id>"),
    ({"ctre": "CANrange:x"}, "CANrange:<id>"),
    ({"ctre": 5}, "CANrange:<id>"),
    ({"ctre": "CANrange:\u00b2"}, "CANrange:<id>"),
    ({"grapple": "LaserCan:7"}, 'grapple = "LaserCan:7"'),
    ({"grapple": "LaserCAN:-1"}, "LaserCAN:<id>"),
    ({"ctre": "CANrange:5", "grapple": "LaserCAN:7"}, "only one"),
])
def test_bad_device_keys(extra, words):
    with pytest.raises(SensorError) as e:
        parse_sensors([dist_table(**extra)])
    assert "[[sensor]] \"d\"" in str(e.value) and words in str(e.value)


def test_device_id_boundaries():
    a, b = parse_sensors([dist_table(ctre="CANrange:0"), {**dist_table(grapple="LaserCAN:62"), "name": "e"}])
    assert a.device_id == 0 and b.device_id == 62


def test_device_key_on_other_kind():
    t = {"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0], "ctre": "CANrange:5"}
    with pytest.raises(SensorError, match="unknown key 'ctre' for a beam sensor"):
        parse_sensors([t])


def test_pwf_not_supported_yet():
    with pytest.raises(SensorError) as e:
        parse_sensors([dist_table(pwf="TimeOfFlight:6")])
    assert "CAN sensor" in str(e.value) and "can't feed it yet" in str(e.value) and "analog stand-in" in str(e.value)


def test_canrange_default_threshold_is_phoenix_default():
    s, = parse_sensors([dist_table(ctre="CANrange:5")])
    assert CANRANGE_THRESHOLD == 0.4 and s.threshold == CANRANGE_THRESHOLD     # not half of max_range (1.5 m)
    assert parse_sensors([dist_table(ctre="CANrange:5", threshold=0.25)])[0].threshold == 0.25   # explicit wins
    assert parse_sensors([dist_table(ctre="CANrange:5", max_range=0.3)])[0].threshold == 0.3     # capped at max_range
    assert parse_sensors([dist_table(grapple="LaserCAN:7")])[0].threshold == DEVICE_MAX_RANGE["LaserCAN"] / 2
    assert parse_sensors([dist_table()])[0].threshold == 1.0


def test_same_device_twice():
    a, b = dist_table(ctre="CANrange:5"), {**dist_table(ctre="CANrange:5"), "name": "e"}
    with pytest.raises(SensorError) as e:
        parse_sensors([a, b])
    assert '"d"' in str(e.value) and '"e"' in str(e.value) and "CANrange 5" in str(e.value)
    parse_sensors([a, {**b, "ctre": "CANrange:6"}])                     # another id is fine
    parse_sensors([a, {**dist_table(grapple="LaserCAN:5"), "name": "e"}])   # another device kind is fine


def test_defaults_and_inches():
    beam, dist, color = parse_sensors([
        {"name": "b", "kind": "beam", "body": "a.b", "from_in": [0, 0, 0], "to_in": [10, 0, 0]},
        {"name": "d", "kind": "distance", "body": "a.b", "at": [0, 0, 0], "toward": [0, 2, 0], "analog": 1},
        {"name": "c", "kind": "color", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0]}])
    assert beam.length == pytest.approx(0.254) and beam.sees == "pieces" and beam.active_low
    assert dist.length == 2.0 and dist.threshold == 1.0 and dist.volts_per_m == 0.977 and dist.direction == (0, 1, 0)
    assert color.length == 0.10 and color.sees == "pieces"


def test_a_robot_without_sensors_has_none():
    assert intake_world().sensors is None
    assert World(range_cfg(), None).sensors is None


# ------------------------------------------------------------------------------------- get_state, scenarios, pokes

def test_a_sensor_reads_again_when_the_robot_is_set_somewhere_else():
    """B12: set_pose (World.set_robot_pose) reads the sensors at once: the reading is of the new pose, not the old
    one until the next physics step."""
    w, front = _ahead_world(kind="distance", max_range=2.0)
    r = w.test_range.balls.r
    w.test_range.balls.put(0, front + [0.5 + r, 0.0, 0.0], BALL_BITS)
    step(w)
    assert w.sensors.reading("s").on
    x, y, yaw = w.robot_pose()
    w.set_robot_pose(x - 1.0, y, float(np.degrees(yaw)) + 180.0)     # turned away from the ball, no step
    got = w.sensors.reading("s")
    assert not got.on and got.distance == pytest.approx(2.0)
    assert w.sensors.state() == {"s": {"kind": "distance", "on": False, "distance_m": 2.0, "color": None}}


def test_the_sensors_state_is_in_si_with_the_color_it_sees(tmp_path):
    """B12: get_state's sensors: each one's kind, on, distance_m (meters) and the named color it sees."""
    w, front = _two_color_world(tmp_path)
    balls = w.test_range.balls
    assert w.sensors.state() == {"eye": {"kind": "color", "on": False, "distance_m": None, "color": None}}
    balls.put(list(balls.kind).index("red_cargo"), front + [0.03 + balls.r, 0.0, 0.0], BALL_BITS)
    step(w)
    assert w.sensors.state() == {"eye": {"kind": "color", "on": True, "distance_m": 0.03, "color": "red"}}


def test_a_scenario_sensor_check_reads_as_the_test_tabs_sensor_condition(tmp_path):
    """B12: a [[step.check]] sensor = {name, is, sees, when}: on or off at the end of the step, on every tick, or
    by a time, as the at_end, always and by_time checks of sensor.<name> (or .sees.<color>) equal to true or false;
    and the sensor signals themselves."""
    from types import SimpleNamespace

    from scrimmage.checks import ScenarioError, build_check, resolve_signal
    w, front = _two_color_world(tmp_path)
    session = SimpleNamespace(world=w, t=w.time)
    balls = w.test_range.balls
    balls.put(list(balls.kind).index("red_cargo"), front + [0.03 + balls.r, 0.0, 0.0], BALL_BITS)
    step(w)

    def result(spec):
        c = build_check({"sensor": spec}, w, "x.toml, step 1 (A)")
        c.begin(session)
        c.tick(session, w.time)
        return c.end(session)

    assert result({"name": "eye"}).passed
    assert result({"name": "eye", "sees": "red"}).passed
    assert not result({"name": "eye", "sees": "blue"}).passed
    assert result({"name": "eye", "is": "off", "sees": "blue"}).passed
    assert not result({"name": "eye", "is": "off", "when": "always"}).passed
    assert result({"name": "eye", "when": "by", "t": 1.0}).passed
    r = result({"name": "eye", "sees": "red"})
    assert r.check == "at_end sensor.eye.sees.red == True" and r.measured is True
    assert resolve_signal("sensor.eye.color", w).read(session) == "red"
    assert resolve_signal("sensor.eye.distance", w).read(session) == pytest.approx(0.03, abs=0.001)
    for spec, words in (({"name": "nope"}, "the robot's sensors: eye"), ({"name": "eye", "is": "broken"},
                        'is must be "on" or "off"'), ({"name": "eye", "when": "by"}, "t goes with"),
                        ({"name": "eye", "sees": "teal"}, "teal' isn't a named color"),
                        ({"name": "eye", "glows": True}, "unknown key 'glows'")):
        with pytest.raises(ScenarioError, match=words):
            build_check({"sensor": spec}, w, "x.toml, step 1 (A)")
    with pytest.raises(ScenarioError, match="needs a sensor"):
        resolve_signal("sensor.eye", spares_world(range_cfg()))


def test_get_state_has_the_sensors_only_with_sensors_and_reads_them_again_after_set_pose(tmp_path):
    """B12: the MCP tool get_state (Session.state) gains a sensors block, in SI, on a robot with [[sensor]]s, and
    set_pose answers with the readings at the new pose."""
    import asyncio

    from parts_fake import parts_config
    from scrimmage.session import Session

    plain = parts_config(tmp_path)
    with_sensor = tmp_path / "with_sensor.toml"
    with_sensor.write_text(plain.read_text() + '\n[[sensor]]\nname = "front"\nkind = "distance"\nbody = "drive.chassis"\n'
                           'at = [0.45, 0.0, 0.1]\ntoward = [1.0, 0.0, 0.0]\nsees = "anything"\nmax_range = 3.0\n')

    async def states(path):
        s = Session(path, nt=False)
        try:
            await s.start()
            await s.advance(0.1)
            first = s.state()
            x, y, h = first["pose"]["x"], first["pose"]["y"], first["pose"]["heading_deg"]
            return first, s.set_pose(x, y, h + 180.0)
        finally:
            await s.stop()

    assert "sensors" not in asyncio.run(states(plain))[0]
    first, turned = asyncio.run(states(with_sensor))
    assert set(first["sensors"]) == {"front"} and first["sensors"]["front"]["kind"] == "distance"
    assert set(first["sensors"]["front"]) == {"kind", "on", "distance_m", "color"}
    # at (2, 4.035) facing +x nothing is within 3 m; turned round, the wall behind is 1.55 m off
    assert first["sensors"]["front"]["distance_m"] == 3.0
    assert turned["sensors"]["front"]["distance_m"] == pytest.approx(1.55, abs=0.01)


def test_fov_deg_on_a_distance_sensor():
    s, = parse_sensors([dist_table(fov_deg=40)])
    assert s.fov_deg == 40.0
    assert parse_sensors([dist_table(fov_deg=60)])[0].fov_deg == 60.0


def test_a_distance_sensor_is_one_ray_without_fov_deg_or_a_can_key():
    assert parse_sensors([dist_table()])[0].fov_deg is None
    assert parse_sensors([dist_table(analog=0, dio=1)])[0].fov_deg is None


@pytest.mark.parametrize("key, value", [("ctre", "CANrange:5"), ("grapple", "LaserCAN:7")])
def test_a_can_sensor_defaults_to_the_vendors_27_degree_cone(key, value):
    s, = parse_sensors([dist_table(**{key: value})])
    assert s.fov_deg == CAN_FOV_DEG == 27.0
    assert parse_sensors([dist_table(**{key: value}, fov_deg=10)])[0].fov_deg == 10.0, "fov_deg wins"


@pytest.mark.parametrize("value", [0, -5, 61, 60.1, "wide", True, float("nan"), float("inf"), [27]])
def test_bad_fov_deg(value):
    with pytest.raises(SensorError) as e:
        parse_sensors([dist_table(fov_deg=value)])
    assert 'fov_deg must be an angle more than 0 and at most 60 degrees' in str(e.value)
    assert '[[sensor]] "d"' in str(e.value)


@pytest.mark.parametrize("t", [
    {"name": "x", "kind": "beam", "body": "a.b", "from": [0, 0, 0], "to": [1, 0, 0], "fov_deg": 27},
    {"name": "x", "kind": "color", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "fov_deg": 27},
    {"name": "x", "kind": "camera", "body": "a.b", "at": [0, 0, 0], "toward": [1, 0, 0], "fov_deg": 27},
])
def test_fov_deg_only_on_a_distance_sensor(t):
    with pytest.raises(SensorError, match="unknown key 'fov_deg'"):
        parse_sensors([t])
