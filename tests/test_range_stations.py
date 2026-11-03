import math
import re
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.build_elements import chute_parts
from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError, load_range, parse_range
from scrimmage.testrange.station import Chute, with_overrides

HEAD = '''
[range]
name = "t"
ball = "2026-fuel"
'''
FEEDER = '''
[[station]]
name = "feeder"
release = [1.0, 4.0, 0.5]
heading_deg = 0.0
'''


def parse(tmp_path, text):
    return parse_range(tomllib.loads(text), tmp_path / "r.toml")


def test_a_station_parses_with_its_defaults(tmp_path):
    rng = parse(tmp_path, HEAD + FEEDER)
    st = rng.stations[0]
    assert st.interval == 1.0 and st.stock is None and st.pool() == 50 and st.chute is None
    assert st.direction(0.0, 30.0) == pytest.approx([math.cos(math.radians(30)), 0, -0.5])


def test_release_in_inches_and_spreads(tmp_path):
    rng = parse(tmp_path, HEAD + '''
[[station]]
name = "feeder"
release_in = [39.37007874015748, 157.48031496062993, 40]
heading_deg = 90.0
pitch_deg = 20
speed = 1.5
interval = 0.4
stock = 8
spread_yaw_deg = 5
spread_pitch_deg = 3
spread_speed = 0.2
spread_position_in = [0, 1, 2]
spread_rotation_deg = [0, 0, 10]
''')
    st = rng.stations[0]
    assert st.release == pytest.approx((1.0, 4.0, 40 * 0.0254))
    assert (st.interval, st.stock, st.pool(), st.spread_speed) == (0.4, 8, 8, 0.2)
    assert st.spread_position == pytest.approx((0, 0.0254, 0.0508))
    assert st.spread_rotation_deg == (0.0, 0.0, 10.0)


def test_a_release_inside_a_box_is_refused(tmp_path):
    text = HEAD + '''
[[element]]
name = "wall"
kind = "box"
center = [1.0, 4.0]
size = [0.2, 2.0, 1.0]
''' + FEEDER
    with pytest.raises(RangeError, match=r"'feeder': its release point at \(1, 4, 0.5\) is inside element 'wall'; "
                                         r"move it clear \(the ball's center needs 0.075 m all round\)"):
        parse(tmp_path, text)


def test_a_release_inside_an_obstacle_is_refused(tmp_path):
    text = HEAD + '''
[[obstacle]]
name = "post"
pos = [1.0, 4.0, 0.5]
size = [0.3, 0.3, 1.0]
''' + FEEDER
    with pytest.raises(RangeError, match="is inside obstacle 'post'"):
        parse(tmp_path, text)


ELEMENTS = {
    "bump": 'center = [4.0, 2.0]\ndepth_in = 44.40\nwidth_in = 73.0\nheight_in = 6.51\nramp_deg = 15.0\n',
    "ramp": 'center = [4.0, 2.0]\nwidth = 1.0\nheight = 0.2\nramp_deg = 10.0\ndeck = 0.5\n',
    "platform": ('center = [4.0, 2.0]\nlength_in = 96\nwidth_in = 48\nheight_in = 9.125\nmax_tilt_deg = 15\n'
                 'hinge_height_in = 6.57\nhinge_spacing_in = 5.30\nramp_length_in = 15.125\nmass = 94.8\n'
                 'inertia = 24.6\ncom_height_in = 7.27\ndamping = 50.0\n'),
    "bar": 'center = [4.0, 2.0]\nlength = 2.0\ndiameter = 0.04\nheight = 1.2\n',
}


@pytest.mark.parametrize("kind", list(ELEMENTS))
def test_a_release_inside_each_kind_of_element_is_refused(tmp_path, kind):
    from scrimmage.testrange.station import _element_boxes

    element = f'\n[[element]]\nname = "e"\nkind = "{kind}"\n' + ELEMENTS[kind]
    boxes = _element_boxes(parse(tmp_path, HEAD + element).elements[0])
    x, y, z = max((b[0] for b in boxes), key=lambda c: c[2])      # the middle of its highest box
    station = FEEDER.replace("[1.0, 4.0, 0.5]", f"[{x}, {y}, {z}]")
    with pytest.raises(RangeError, match="'feeder': its release point at .* is inside element 'e'"):
        parse(tmp_path, HEAD + element + station)


def test_a_release_inside_another_stations_chute_is_refused(tmp_path):
    chute = "chute = { length = 0.5, slope_deg = 10.0 }\n"
    floor = chute_parts(parse(tmp_path, HEAD + FEEDER + chute).stations[0], 0.15)["floor"][0]
    other = FEEDER.replace('"feeder"', '"second"').replace("[1.0, 4.0, 0.5]", f"[{floor[0]}, {floor[1]}, {floor[2]}]")
    with pytest.raises(RangeError, match="'second': its release point at .* is inside the chute of station 'feeder'"):
        parse(tmp_path, HEAD + FEEDER + chute + other)


def test_a_chute_over_a_start_is_refused(tmp_path):
    start = '\n[start.s]\npose = [{x}, 4.0, 0.0]\n'
    chute = "chute = { length = 0.5, slope_deg = 10.0 }\n"
    with pytest.raises(RangeError, match=r"'feeder': its chute \(floor\) overlaps start 's' at \(1.25, 4\)"):
        parse(tmp_path, HEAD + start.format(x=1.25) + FEEDER + chute)
    parse(tmp_path, HEAD + start.format(x=3.0) + FEEDER + chute)          # clear of it: accepted
    parse(tmp_path, HEAD + start.format(x=1.9325) + FEEDER + chute)       # outline just touching the low end: accepted
    with pytest.raises(RangeError, match="overlaps start 's'"):
        parse(tmp_path, HEAD + start.format(x=1.9125) + FEEDER + chute)   # 2 cm in: refused


def test_a_chute_through_the_carpet_is_refused(tmp_path):
    text = HEAD + FEEDER + "chute = { length = 2.0, slope_deg = 30.0 }\n"
    with pytest.raises(RangeError, match=r"chute: its low end would be 0\.\d+ m below the carpet; make it shorter"):
        parse(tmp_path, text)


def test_a_chute_narrower_than_the_ball_is_refused(tmp_path):
    text = (HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0, width = 0.15 }\n").replace(
        "2026-fuel", "2010-soccer-ball")
    with pytest.raises(RangeError, match=r"chute: 0.15 m wide, no wider than the ball \(0.2223 m\)"):
        parse(tmp_path, text)


def test_a_chute_without_a_width_fits_the_ball(tmp_path):
    st = parse(tmp_path, HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n").stations[0]
    assert st.chute_width(0.15) == pytest.approx(0.195)


def test_the_chute_parts_slope_down_the_heading_with_walls_each_side(tmp_path):
    st = parse(tmp_path, HEAD + FEEDER.replace("heading_deg = 0.0", "heading_deg = 90.0")
               + "chute = { length = 0.5, slope_deg = 20.0 }\n").stations[0]
    parts = chute_parts(st, 0.15)
    assert set(parts) == {"floor", "wall_left", "wall_right"}
    floor_c, floor_r, floor_h = parts["floor"]
    assert floor_c[1] > 4.0 and floor_c[0] == pytest.approx(1.0)       # runs toward +y
    # the floor's top is tangent to a ball resting at the release point
    normal = floor_r[:, 2]
    assert (np.array(st.release) - (floor_c + normal * floor_h[2])) @ normal == pytest.approx(0.075)
    left, right = parts["wall_left"][0], parts["wall_right"][0]
    assert left[0] < 1.0 < right[0]                                       # left of +y is -x
    assert abs(left[0] - right[0]) == pytest.approx(0.195 + 0.25 * 0.0254)


@pytest.mark.parametrize("extra, key", [
    ("interval = 0", "interval must be positive"), ("stock = 0", "stock must be at least 1"),
    ("spread_speed = -1", "spread_speed must be 0 or more"), ("pitch_deg = 100", "pitch_deg must be from -90 to 90"),
    ("rate = 2", "unknown key 'rate'"), ("spread_position = [0, -1, 0]", "spread_position must be 0 or more"),
    ("release_in = [1, 2, 3]", "has both release and release_in"),
])
def test_bad_station_values(tmp_path, extra, key):
    with pytest.raises(RangeError, match=key):
        parse(tmp_path, HEAD + FEEDER + extra + "\n")


def test_a_chute_off_the_field_is_refused(tmp_path):
    text = HEAD + FEEDER.replace("[1.0, 4.0, 0.5]", "[0.1, 4.0, 0.5]") + "chute = { length = 0.3, slope_deg = 30.0 }\n"
    with pytest.raises(RangeError, match=r"'feeder': its chute reaches off the field .*its floor spans x -0\.0"):
        parse(tmp_path, text)


def test_a_chute_into_an_element_is_refused(tmp_path):
    text = HEAD + '''
[[element]]
name = "wall"
kind = "box"
center = [1.3, 4.0]
size = [0.2, 1.0, 2.0]
''' + FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n"
    with pytest.raises(RangeError, match="'feeder': its chute runs into element 'wall'; move the station or shorten"):
        parse(tmp_path, text)


def test_a_chute_into_another_chute_is_refused(tmp_path):
    chute = "chute = { length = 0.5, slope_deg = 10.0 }\n"
    other = FEEDER.replace('"feeder"', '"second"').replace("[1.0, 4.0, 0.5]", "[1.3, 4.0, 0.5]")
    with pytest.raises(RangeError, match="its chute runs into the chute of station 'second'"):
        parse(tmp_path, HEAD + FEEDER + chute + other + chute)


def test_two_stations_named_alike_are_refused(tmp_path):
    with pytest.raises(RangeError, match="shares its name"):
        parse(tmp_path, HEAD + FEEDER + FEEDER)


def test_a_station_named_like_a_goal_is_refused(tmp_path):
    goal = '[[goal]]\nname = "feeder"\npos = [4.0, 4.0]\nrim_height = 2.0\nopening = { sides = 6, across_flats = 1.0 }\n'
    with pytest.raises(RangeError, match="shares its name"):
        parse(tmp_path, HEAD + goal + FEEDER)


def test_a_release_off_the_field_or_on_the_carpet_is_refused(tmp_path):
    with pytest.raises(RangeError, match="off the field"):
        parse(tmp_path, HEAD + FEEDER.replace("[1.0, 4.0, 0.5]", "[-1.0, 4.0, 0.5]"))
    with pytest.raises(RangeError, match="above the carpet"):
        parse(tmp_path, HEAD + FEEDER.replace("0.5]", "0.05]"))


def test_a_station_in_a_disc_range_is_refused(tmp_path):
    text = HEAD.replace('ball = "2026-fuel"', 'disc = "2013-frisbee"') + FEEDER
    with pytest.raises(RangeError, match="'feeder': stations feed balls; this range's piece is a disc"):
        parse(tmp_path, text)


def test_ranges_without_stations_are_unchanged():
    for name in BUILTIN_RANGES:
        if name in ("station", "2026"):     # the station field and 2026's OUTPOSTS have them on purpose
            continue
        assert load_range(name, Path(".")).stations == ()


# ------------------------------------------------------------------------------------------------ in the world

ROOT = Path(__file__).resolve().parents[1]
STATION_TOML = ROOT / "scrimmage" / "testrange" / "ranges" / "station.toml"
AWAY = (8.0, 2.0, 0.0)          # a robot pose clear of the station and its chute


def station_world(tmp_path, extra: str = "", away: bool = True):
    """The stand-in robot (no extra parts) on the station field; `extra` lines go into its [[station]] (the range
    copied to tmp_path). The robot moved clear of the chute unless `away` is false."""
    from scrimmage.assembler.templates import new_config
    from scrimmage.world import World

    ref = "station"
    if extra:
        text = re.sub(r'(?m)^(name = "feeder".*\n)', lambda m: m.group(1) + extra + "\n",
                      STATION_TOML.read_text(encoding="utf-8"))
        (tmp_path / "station.toml").write_text(text, encoding="utf-8")
        ref = "station.toml"
    cfg = tomllib.loads(new_config("standin") + f'\n[range]\nfile = "{ref}"\n')
    w = World(cfg, tmp_path)
    if away:
        w.set_robot_pose(*AWAY)
    return w


def advance(w, seconds):
    hal = HalState()
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)


def test_a_station_has_its_pool_parked(tmp_path):
    w = station_world(tmp_path)
    lay = w.parts.layout
    assert len(lay.station_slots["feeder"]) == 50 and lay.station_slots["feeder"].start == lay.robot_slots.stop
    assert all(w.test_range.balls.state[i] == BallState.SPARE for i in lay.station_slots["feeder"])
    assert all(w.test_range.balls.log[i].origin == "station" for i in lay.station_slots["feeder"])
    assert w.model.geom("element.feeder.chute.floor") is not None


def test_start_feeds_at_once_then_every_interval(tmp_path):
    w = station_world(tmp_path)
    feeder = w.test_range.feeder
    t0 = w.time
    feeder.start("feeder")
    advance(w, 3.05)
    assert [round(f.t - t0, 3) for f in feeder.fed] == [0.0, 1.0, 2.0, 3.0]
    assert feeder.count("feeder") == 4 and feeder.left("feeder") is None and not feeder.empty("feeder")
    lay = w.parts.layout
    assert [f.ball for f in feeder.fed] == list(lay.station_slots["feeder"][:4])
    assert all(f.station == "feeder" for f in feeder.fed)


def test_a_fed_piece_slides_down_the_chute_onto_the_carpet(tmp_path):
    w = station_world(tmp_path)
    st = w.test_range.range.stations[0]
    chute_low_end_x = st.release[0] + st.chute.length * math.cos(math.radians(st.chute.slope_deg))
    w.test_range.feeder.request("feeder")
    advance(w, 3.0)
    i = w.test_range.feeder.fed[0].ball
    x, _, z = w.test_range.balls.positions()[i]
    r = w.test_range.balls.r
    assert z == pytest.approx(r, abs=0.005) and x > chute_low_end_x
    assert w.test_range.balls.state[i] == BallState.FIELD


def feeds(w, seed):
    w.test_range.reset(seed=seed)
    w.set_robot_pose(*AWAY)
    feeder = w.test_range.feeder
    assert feeder.seed == seed and feeder.fed == []
    t0 = w.time
    feeder.start("feeder")
    advance(w, 2.05)
    return [(round(f.t - t0, 6), f.release, f.velocity) for f in feeder.fed]


def test_the_same_seed_gives_the_same_feeds_and_another_seed_doesnt(tmp_path):
    w = station_world(tmp_path, "spread_yaw_deg = 10\nspread_speed = 0.5\nspeed = 0.5")
    a, b, c = feeds(w, 7), feeds(w, 7), feeds(w, 8)
    assert len(a) == 3 and a == b and a != c
    assert w.test_range.settings()["seed"] == 8
    w.test_range.reset()                    # kept like the other overrides
    assert w.test_range.feeder.seed == 8


def test_a_fed_piece_varies_within_its_spreads(tmp_path):
    w = station_world(tmp_path, "speed = 1.0\nspread_yaw_deg = 10\nspread_pitch_deg = 5\nspread_speed = 0.5\n"
                                "spread_position = [0.0, 0.04, 0.02]")
    feeder = w.test_range.feeder
    st = w.test_range.range.stations[0]
    feeder.start("feeder")
    advance(w, 5.05)
    assert len(feeder.fed) == 6
    for f in feeder.fed:
        d = np.array(f.release) - np.array(st.release)
        assert abs(d[0]) < 1e-12 and abs(d[1]) <= 0.04 and abs(d[2]) <= 0.02
        v = np.array(f.velocity)
        speed = float(np.linalg.norm(v))
        assert 0.5 <= speed <= 1.5
        assert abs(math.degrees(math.atan2(v[1], v[0]))) <= 10.0 + 1e-9
        assert abs(math.degrees(math.asin(-v[2] / speed))) <= 5.0 + 1e-9
    assert len({f.velocity for f in feeder.fed}) == 6
    # +-spread_position on each axis: the first feed's dy is its fifth draw times the whole 0.04
    draws = np.random.default_rng([1, 0]).uniform(-1.0, 1.0, 9)
    assert feeder.fed[0].release[1] - st.release[1] == pytest.approx(draws[4] * 0.04)
    assert max(abs(f.release[1] - st.release[1]) for f in feeder.fed) > 0.02


def test_a_stock_runs_out(tmp_path):
    w = station_world(tmp_path, "stock = 3")
    feeder = w.test_range.feeder
    assert len(w.parts.layout.station_slots["feeder"]) == 3
    feeder.start("feeder")
    assert feeder.left("feeder") == 3 and not feeder.empty("feeder")
    advance(w, 5.0)
    assert feeder.count("feeder") == 3 and feeder.left("feeder") == 0
    assert feeder.empty("feeder") and feeder.capped("feeder")
    advance(w, 1.0)
    assert feeder.count("feeder") == 3


def test_feed_one_waits_out_the_interval(tmp_path):
    w = station_world(tmp_path)
    feeder = w.test_range.feeder
    t0 = w.time
    feeder.request("feeder")
    feeder.request("feeder")
    advance(w, 2.5)
    assert [round(f.t - t0, 3) for f in feeder.fed] == [0.0, 1.0]


def test_stop_ends_feeding(tmp_path):
    w = station_world(tmp_path)
    feeder = w.test_range.feeder
    feeder.start("feeder")
    advance(w, 1.5)
    feeder.stop_all()
    advance(w, 2.0)
    assert feeder.count("feeder") == 2
    with pytest.raises(ValueError, match="no station 'nope'; this range's stations: feeder"):
        feeder.start("nope")


def test_a_blocked_release_point_waits(tmp_path):
    w = station_world(tmp_path)
    rt = w.test_range
    st = rt.range.stations[0]
    rt.balls.put(0, st.release, BALL_BITS)          # ball 0 is the field's floor ball
    feeder = rt.feeder
    assert feeder.blocked("feeder")
    feeder.start("feeder")
    advance(w, 0.05)
    assert feeder.fed == [] and feeder.blocked("feeder")
    advance(w, 1.0)                                 # ball 0 has rolled down the chute: the feed goes
    assert len(feeder.fed) == 1 and not rt.balls.positions()[0][0] < st.release[0] + 0.15


def test_reset_puts_the_pool_back(tmp_path):
    w = station_world(tmp_path)
    feeder = w.test_range.feeder
    feeder.start("feeder")
    advance(w, 1.05)
    w.test_range.reset()
    lay = w.parts.layout
    assert feeder.fed == [] and feeder.count("feeder") == 0 and not feeder.capped("feeder")
    assert all(w.test_range.balls.state[i] == BallState.SPARE for i in lay.station_slots["feeder"])
    advance(w, 0.5)
    assert feeder.fed == []                          # nothing feeding after a reset


def test_preload_never_takes_a_station_slot():
    from range_fixtures import shooter_cfg
    from scrimmage.world import World

    w = World(shooter_cfg(range={"file": "station", "preload": 3}), None)
    lay = w.parts.layout
    state = w.test_range.balls.state
    assert len(lay.robot_slots) == 4 and lay.robot_slots.start == lay.n_floor
    assert [i for i in range(w.test_range.balls.n) if state[i] == BallState.ROBOT] == list(lay.robot_slots[:3])
    assert all(state[i] == BallState.SPARE for i in lay.station_slots["feeder"])
    with pytest.raises(ValueError, match="holds at most 4"):
        w.test_range.reset(preload=5)


def test_a_range_without_stations_has_no_feeder():
    from range_fixtures import range_cfg
    from scrimmage.world import World

    w = World(range_cfg(range={"file": "2026-bump"}), None)
    assert w.test_range.feeder is None and w.parts.layout.station_slots == {}
    assert w.test_range.settings()["seed"] == 1


def test_the_station_field_loads_and_its_starts_are_clear():
    from scrimmage.testrange.elements import ROBOT_OUTLINE

    rng = load_range("station", ROOT)
    assert rng.name == "A human player station" and rng.piece_preset == "2026-fuel"
    (st,) = rng.stations
    assert st.name == "feeder" and st.heading_deg == 0.0 and st.stock is None and st.interval == 1.0
    assert st.release[2] == pytest.approx(1.1, abs=0.1) and st.chute == Chute(length=0.5, slope_deg=30.0)
    low_x = st.release[0] + 0.5 * math.cos(math.radians(30.0))
    x, y, heading = rng.starts["at_station"]
    assert heading == 180.0 and y == st.release[1] and x - ROBOT_OUTLINE / 2 == pytest.approx(low_x, abs=1e-3)
    # facing_ball: 1.5 m behind the floor ball, facing it, clear of the chute (and its walls) across y
    x, y, heading = rng.starts["facing_ball"]
    (bx, by), = rng.floor_balls
    assert y == 2.0 and heading == 0.0 and (bx - x, by - y) == pytest.approx((1.5, 0.0))
    walls = [c[1] + k * h[1] for c, _, h in chute_parts(st, rng.piece.diameter).values() for k in (-1, 1)]
    assert y + ROBOT_OUTLINE / 2 < min(walls)


def test_the_2026_field_has_two_outposts_and_a_start_at_the_blue_one_clear_of_the_elements():
    from scrimmage.testrange.elements import ROBOT_OUTLINE, _on_floor, _rect, _separated
    from scrimmage.testrange.spec import FIELD_LENGTH, FIELD_WIDTH

    rng = load_range("2026", ROOT)
    assert [s.name for s in rng.stations] == ["outpost_blue", "outpost_red"]
    for st in rng.stations:
        assert st.stock is not None and st.stock > 0 and st.release[2] > rng.piece.diameter / 2
    blue = rng.stations[0]
    # blue_outpost: a 0.9 m robot (unturned, so either heading) with a bumper against the wall below the opening
    x, y, heading = rng.starts["blue_outpost"]
    assert x - ROBOT_OUTLINE / 2 == pytest.approx(0.0, abs=0.02) and y == pytest.approx(blue.release[1], abs=1e-3)
    assert ROBOT_OUTLINE / 2 <= y <= FIELD_WIDTH - ROBOT_OUTLINE / 2 and x + ROBOT_OUTLINE / 2 < FIELD_LENGTH
    outline = _rect((x, y), heading, ROBOT_OUTLINE, ROBOT_OUTLINE)
    assert all(_separated(outline, f) for e in rng.elements for f in _on_floor(e, start=True))


def test_fuel_fed_from_the_blue_outpost_reach_the_carpet_and_rest():
    from range_fixtures import range_cfg
    from scrimmage.testrange.spec import FIELD_LENGTH, FIELD_WIDTH
    from scrimmage.world import World

    w = World(range_cfg(), None)            # the 2026 range, floor balls off
    w.set_robot_pose(*AWAY)                 # in the NEUTRAL ZONE, clear of both outposts
    for _ in range(4):
        w.test_range.feeder.request("outpost_blue")
    advance(w, 10.0)
    fed = w.test_range.feeder.fed
    assert len(fed) == 4
    balls = w.test_range.balls
    for f in fed:
        x, y, z = balls.positions()[f.ball]
        assert z == pytest.approx(balls.r, abs=0.005)
        assert 0.0 < x < FIELD_LENGTH and 0.0 < y < FIELD_WIDTH
        assert balls.state[f.ball] == BallState.FIELD
        assert balls.at_rest()[f.ball]


# ------------------------------------------------------------------------------------------------ overrides


def test_a_ball_override_rebuilds_the_range():
    rng = load_range("station", ROOT, ball="2019-cargo")
    assert rng.ball.diameter == pytest.approx(0.3302)


def test_a_ball_that_overlaps_the_floor_balls_is_refused():
    with pytest.raises(RangeError, match="overlap"):
        load_range("2026", ROOT, ball="2025-algae")


def test_a_ball_override_on_a_disc_range_is_refused():
    disc = next(n for n in BUILTIN_RANGES if load_range(n, ROOT).disc is not None)
    with pytest.raises(RangeError, match="is a disc field; it can't take a ball"):
        load_range(disc, ROOT, ball="2019-cargo")


def test_station_overrides():
    rng = load_range("station", ROOT)
    s = with_overrides(rng, {"feeder": {"interval": 0.4, "stock": 6}}).stations[0]
    assert (s.interval, s.stock, s.pool()) == (0.4, 6, 6)
    s = with_overrides(rng, {"feeder": {"spread_position": [0.01, 0.02, 0.03], "spread_yaw_deg": 3}}).stations[0]
    assert s.spread_position == (0.01, 0.02, 0.03) and s.spread_yaw_deg == 3.0
    rng2 = with_overrides(rng, {"feeder": {"stock": 6}})
    assert with_overrides(rng2, {"feeder": {"stock": 0}}).stations[0].stock is None


def test_bad_station_overrides_name_themselves():
    rng = load_range("station", ROOT)
    with pytest.raises(RangeError, match="the field has no station 'nope'; its stations: feeder"):
        with_overrides(rng, {"nope": {}})
    with pytest.raises(RangeError, match="unknown key 'speed'.*valid keys: interval, stock"):
        with_overrides(rng, {"feeder": {"speed": 1}})
    with pytest.raises(RangeError, match="spread_position must be a list of 3 numbers"):
        with_overrides(rng, {"feeder": {"spread_position": [1, 2]}})
    with pytest.raises(RangeError, match="spread_speed must be 0 or more"):
        with_overrides(rng, {"feeder": {"spread_speed": -1}})


def range_world(tmp_path, **sec):
    from scrimmage.assembler.templates import new_config
    from scrimmage.world import World

    lines = "".join(f"{k} = {v}\n" for k, v in sec.items())
    cfg = tomllib.loads(new_config("standin") + f'\n[range]\nfile = "station"\n{lines}')
    return World(cfg, tmp_path)


def test_config_range_keys_build_a_world_with_them(tmp_path):
    w = range_world(tmp_path, ball='"2017-fuel"', station="{ feeder = { stock = 4 } }", seed=9)
    assert len(w.parts.layout.station_slots["feeder"]) == 4 and w.test_range.feeder.seed == 9
    assert w.test_range.range.ball.diameter == pytest.approx(0.127)


@pytest.mark.parametrize("line, key", [
    ('ball = "nope"', "ball"), ("ball = 3", "ball"), ("seed = -1", "seed"), ("seed = 1.5", "seed"),
    ("station = { nope = {} }", "station"), ("station = { feeder = { stock = -1 } }", "station"),
    ("station = 3", "station")])
def test_bad_range_override_keys_name_themselves(tmp_path, line, key):
    from scrimmage.assembler.templates import new_config
    from scrimmage.parts.model import PartError
    from scrimmage.testrange.build import parse_range_section

    cfg = tomllib.loads(new_config("standin") + f'\n[range]\nfile = "station"\n{line}\n')
    with pytest.raises(PartError, match=rf"\[range\] {key}"):
        parse_range_section(cfg, tmp_path)


def test_a_stock_is_at_most_200(tmp_path):
    """Ruling R13: each piece of a stock is parked in the world from the start, so a stock is capped at 200."""
    assert parse(tmp_path, HEAD + FEEDER + "stock = 200\n").stations[0].pool() == 200
    with pytest.raises(RangeError, match="'feeder' stock can be at most 200 pieces, not 201"):
        parse(tmp_path, HEAD + FEEDER + "stock = 201\n")
    rng = load_range("station", ROOT)
    assert with_overrides(rng, {"feeder": {"stock": 200}}).stations[0].stock == 200
    with pytest.raises(RangeError, match="stock can be at most 200 pieces, not 500"):
        with_overrides(rng, {"feeder": {"stock": 500}})


def test_a_broken_range_file_is_named_as_the_file_even_with_a_ball(tmp_path):
    """A config's [range] ball doesn't take the blame for a range file that doesn't load on its own."""
    from scrimmage.assembler.templates import new_config
    from scrimmage.parts.model import PartError
    from scrimmage.testrange.build import parse_range_section

    (tmp_path / "bad.toml").write_text("[range\nname = 'x'\n", encoding="utf-8")
    cfg = tomllib.loads(new_config("standin") + '\n[range]\nfile = "bad.toml"\nball = "2017-fuel"\n')
    with pytest.raises(PartError, match=r"^\[range\] file: "):
        parse_range_section(cfg, tmp_path)
    cfg = tomllib.loads(new_config("standin") + '\n[range]\nfile = "2026"\nball = "2025-algae"\n')
    with pytest.raises(PartError, match=r"^\[range\] ball: .*overlap"):
        parse_range_section(cfg, tmp_path)
