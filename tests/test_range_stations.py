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


def fuel():
    """The 2026 FUEL kind (a 0.15 m ball), as HEAD's range has it."""
    from scrimmage.testrange.pieces import ball_kind

    return ball_kind("2026-fuel", "2026-fuel")


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


def test_a_release_over_a_platform_that_tilts_into_it_is_refused(tmp_path):
    """B9: a platform tilts (15° here), so a release point clear of its level deck but in the way of its tilted end is
    refused; one clear of both is kept."""
    element = '\n[[element]]\nname = "e"\nkind = "platform"\n' + ELEMENTS["platform"]
    deck = 9.125 * 0.0254                                    # the deck's top, level
    x = 4.0 - 1.0                                            # 1 m toward the -x end, which tilting about +x raises
    over = FEEDER.replace("[1.0, 4.0, 0.5]", f"[{x}, 2.0, {deck + 0.075 + 0.1}]")    # 0.1 m clear of the level deck
    with pytest.raises(RangeError, match="'feeder': its release point at .* is inside element 'e'"):
        parse(tmp_path, HEAD + element + over)
    clear = FEEDER.replace("[1.0, 4.0, 0.5]", f"[{x}, 2.0, {deck + 0.075 + 0.4}]")
    parse(tmp_path, HEAD + element + clear)


def test_a_release_inside_another_stations_chute_is_refused(tmp_path):
    chute = "chute = { length = 0.5, slope_deg = 10.0 }\n"
    floor = chute_parts(parse(tmp_path, HEAD + FEEDER + chute).stations[0], fuel())["floor"][0]
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
    assert st.chute_width(fuel()) == pytest.approx(1.3 * 0.1501)


def test_the_chute_parts_slope_down_the_heading_with_walls_each_side(tmp_path):
    st = parse(tmp_path, HEAD + FEEDER.replace("heading_deg = 0.0", "heading_deg = 90.0")
               + "chute = { length = 0.5, slope_deg = 20.0 }\n").stations[0]
    parts = chute_parts(st, fuel())
    assert set(parts) == {"floor", "wall_left", "wall_right"}
    floor_c, floor_r, floor_h = parts["floor"]
    assert floor_c[1] > 4.0 and floor_c[0] == pytest.approx(1.0)       # runs toward +y
    # the floor's top is tangent to a ball resting at the release point
    normal = floor_r[:, 2]
    assert (np.array(st.release) - (floor_c + normal * floor_h[2])) @ normal == pytest.approx(0.1501 / 2)
    left, right = parts["wall_left"][0], parts["wall_right"][0]
    assert left[0] < 1.0 < right[0]                                       # left of +y is -x
    assert abs(left[0] - right[0]) == pytest.approx(1.3 * 0.1501 + 0.25 * 0.0254)


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
        if name in ("station", "2026", "reef"):     # the station field, 2026's OUTPOSTS and the reef's coral station
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
    walls = [c[1] + k * h[1] for c, _, h in chute_parts(st, st.kind_of(rng)).values() for k in (-1, 1)]
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


# ------------------------------------------------------------------------------------------------ any piece kind

IN = 0.0254
CORAL_HEAD = '''
[range]
name = "t"
ball = "2026-fuel"

[piece.coral]
preset = "2025-coral"
'''
CORAL_FEEDER = FEEDER + 'kind = "coral"\n'


def coral_station_world(tmp_path, extra: str = ""):
    """The station field with its feeder feeding 2025 corals (`extra` lines into its [[station]])."""
    text = STATION_TOML.read_text(encoding="utf-8").replace(
        "[[station]]", '[piece.coral]\npreset = "2025-coral"\n\n[[station]]', 1)
    text = re.sub(r'(?m)^(name = "feeder".*\n)', lambda m: m.group(1) + 'kind = "coral"\n' + extra + "\n", text)
    (tmp_path / "coral_station.toml").write_text(text, encoding="utf-8")
    from scrimmage.assembler.templates import new_config
    from scrimmage.world import World

    cfg = tomllib.loads(new_config("standin") + '\n[range]\nfile = "coral_station.toml"\n')
    w = World(cfg, tmp_path)
    w.set_robot_pose(*AWAY)
    return w


def placed_quats(w) -> list:
    """Each fed piece's quaternion as the feeder sets it (Balls.place recorded)."""
    out, place = [], w.test_range.balls.place

    def record(i, pos, vel, ang_vel, bits, full_bits_after=None, quat=None):
        out.append(quat)
        return place(i, pos, vel, ang_vel, bits, full_bits_after, quat)

    w.test_range.balls.place = record
    return out


def axis_of(w, quat) -> np.ndarray:
    from scrimmage.testrange.pieces import _matrix

    kind = w.test_range.range.kinds["coral"]
    return _matrix(quat) @ np.array(kind.axis)


def test_a_ball_stations_feeds_are_unchanged(tmp_path):
    w = station_world(tmp_path, "spread_yaw_deg = 10\nspread_speed = 0.5\nspeed = 0.5")
    w.test_range.reset(seed=7)
    w.set_robot_pose(*AWAY)
    quats = placed_quats(w)
    t0 = w.time
    w.test_range.feeder.start("feeder")
    advance(w, 2.05)
    # printed by this body on the code before stations fed any kind
    assert [(f.ball, round(f.t - t0, 6), f.release, f.velocity) for f in w.test_range.feeder.fed] == [
        (1, 0.0, (0.3, 4.0, 1.1), (0.7749462813958566, 0.03386075902716931, -0.0)),
        (2, 1.0, (0.3, 4.0, 1.1), (0.2784081718331147, -0.003116300728936605, -0.0)),
        (3, 2.0, (0.3, 4.0, 1.1), (0.21511291511775546, 0.009179830360608196, -0.0))]
    assert [list(q) for q in quats] == [[1.0, 0.0, 0.0, 0.0]] * 3


def test_a_coral_station_sizes_its_chute_from_the_coral(tmp_path):
    rng = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n")
    st, coral = rng.stations[0], rng.kinds["coral"]
    assert st.chute_width(coral) == pytest.approx(1.3 * 11.875 * IN)
    parts = chute_parts(st, coral)
    assert 2 * parts["wall_left"][2][2] == pytest.approx(11.875 * IN)          # the walls a coral's length high
    # the floor's top is tangent to the coral lying in line at the release point: it touches, nothing passes below
    floor_c, floor_r, floor_h = parts["floor"]
    normal = floor_r[:, 2]
    gap = (np.array(st.release) - (floor_c + normal * floor_h[2])) @ normal
    assert gap == pytest.approx(coral.lowest(_on_slope(coral, 10.0)))
    # the ball's chute is as it was, and the default kind's ball fills in for a station naming none
    ball = parse(tmp_path, CORAL_HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n")
    assert ball.stations[0].chute_width(ball.stations[0].kind_of(ball)) == pytest.approx(1.3 * 0.1501)


def _on_slope(kind, slope_deg):
    """The coral lying in line (turn 0 along the heading +x) seen in the frame of a floor sloping down +x."""
    from scrimmage.testrange.pieces import _axis_angle, _matrix, _quat

    return _quat(_axis_angle((0, 1, 0), -math.radians(slope_deg)) @ _matrix(kind.orient(0.0, 0.0)))


def test_a_chute_narrower_than_a_coral_is_refused_in_its_words(tmp_path):
    with pytest.raises(RangeError, match=r"chute: 0.3 m wide, no wider than the coral \(0.3016 m\)"):
        parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + "chute = { length = 0.5, slope_deg = 10.0, width = 0.3 }\n")


def test_a_tip_spread_lowers_the_chute_for_the_worst_tip(tmp_path):
    chute = "chute = { length = 0.5, slope_deg = 10.0 }\n"
    rng = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + chute)
    tipped = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + chute + "spread_tip_deg = 30\n")
    coral = rng.kinds["coral"]
    flat, worst = rng.stations[0].chute_top(coral), tipped.stations[0].chute_top(coral)
    assert worst < flat - 0.05            # a 30 degree tip reaches about 7.5 cm further down
    # no tip in the spread puts the coral's lowest point below the floor's top under it
    from scrimmage.testrange.pieces import _axis_angle, _matrix, _quat

    st = tipped.stations[0]
    for tip in np.linspace(-30, 30, 13):
        q = _quat(_axis_angle((0, 1, 0), -math.radians(10.0)) @ _matrix(coral.orient(tip, 0.0)))
        assert coral.lowest(q) <= (st.release[2] - worst) * math.cos(math.radians(10.0)) + 1e-9


def test_a_flat_kinds_station_feeds_it_lying_flat_by_default(tmp_path):
    rng = parse(tmp_path, CORAL_HEAD.replace("coral]\npreset = \"2025-coral\"", "gear]\npreset = \"2017-gear\"")
                + FEEDER + 'kind = "gear"\n')
    assert rng.stations[0].tip_deg == 90.0
    assert parse(tmp_path, CORAL_HEAD + CORAL_FEEDER).stations[0].tip_deg == 0.0


def test_a_replaced_ball_sizes_the_chute(tmp_path):
    import dataclasses

    from scrimmage.testrange.pieces import ball_kind

    rng = parse(tmp_path, HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n")
    big = ball_kind("x", "2019-cargo").ball
    st = rng.stations[0]
    kind = st.kind_of(dataclasses.replace(rng, ball=big))
    assert kind.ball is big and st.chute_width(kind) == pytest.approx(1.3 * big.diameter)


def test_coral_comes_down_at_varied_turns(tmp_path):
    w = coral_station_world(tmp_path, "spread_turn_deg = 45\nstock = 6")
    quats = placed_quats(w)
    w.test_range.feeder.start("feeder")
    advance(w, 8.0)
    assert len(quats) == 6
    turns = [math.degrees(math.atan2(a[1], a[0])) for a in (axis_of(w, q) for q in quats)]
    assert len({round(t) for t in turns}) >= 4 and all(abs(t) <= 45 + 1 for t in turns)
    # the two extra draws come after the nine: the first feed's turn is its eleventh draw times 45
    draws = np.random.default_rng([1, 0]).uniform(-1.0, 1.0, 11)
    assert turns[0] == pytest.approx(draws[10] * 45.0)


def test_a_fed_coral_slides_down_the_chute_onto_the_carpet(tmp_path):
    w = coral_station_world(tmp_path)
    st = w.test_range.range.stations[0]
    chute_low_end_x = st.release[0] + st.chute.length * math.cos(math.radians(st.chute.slope_deg))
    quats = placed_quats(w)
    w.test_range.feeder.request("feeder")
    advance(w, 4.0)
    a = axis_of(w, quats[0])
    assert abs(a[2]) < 1e-9 and a[0] == pytest.approx(1.0)                  # fed lying in line along the heading
    i = w.test_range.feeder.fed[0].ball
    x, _, z = w.test_range.balls.positions()[i]
    b = w.test_range.balls
    axis = w.data.xmat[b.bodies[i]].reshape(3, 3) @ np.array(w.test_range.range.kinds["coral"].axis)
    assert x > chute_low_end_x and z == pytest.approx(4.5 * IN / 2, abs=0.01) and abs(axis[2]) < 0.05
    assert b.state[i] == BallState.FIELD


def test_station_tip_and_turn_overrides():
    rng = load_range("station", ROOT)
    assert with_overrides(rng, {"feeder": {"spread_turn_deg": 30}}).stations[0].spread_turn_deg == 30
    s = with_overrides(rng, {"feeder": {"tip_deg": -20, "turn_deg": -90, "spread_tip_deg": 5}}).stations[0]
    assert (s.tip_deg, s.turn_deg, s.spread_tip_deg) == (-20.0, -90.0, 5.0)
    with pytest.raises(RangeError, match="tip_deg must be from -90 to 90, not 100"):
        with_overrides(rng, {"feeder": {"tip_deg": 100}})
    with pytest.raises(RangeError, match="spread_turn_deg must be 0 or more"):
        with_overrides(rng, {"feeder": {"spread_turn_deg": -1}})


def test_spread_corals_all_slide_out_and_the_stock_is_fed(tmp_path):
    """Ruling P16: each feed is set down just clear of the floor at its own drawn tip and turn, so none drops onto
    the floor (lowered for the worst tip) and wedges under the release point. The corals pile up past the low end:
    one may come to rest leaning on another."""
    w = coral_station_world(tmp_path, "spread_tip_deg = 20\nspread_turn_deg = 20\nstock = 4")
    st = w.test_range.range.stations[0]
    chute_low_end_x = st.release[0] + st.chute.length * math.cos(math.radians(st.chute.slope_deg))
    w.test_range.feeder.start("feeder")
    advance(w, 12.0)
    assert w.test_range.feeder.empty("feeder") and len(w.test_range.feeder.fed) == 4
    b = w.test_range.balls
    for f in w.test_range.feeder.fed:
        assert f.release[2] <= st.release[2]
        x, _, z = b.positions()[f.ball]
        assert x > chute_low_end_x and z < 0.2 and b.at_rest()[f.ball]     # on the carpet, or leaning on a coral


def test_a_position_spread_lowers_the_chute_by_its_reach_toward_the_floor(tmp_path):
    chute = "chute = { length = 0.5, slope_deg = 30.0 }\n"
    st = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + chute).stations[0]
    spread = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + chute + "spread_position = [0.01, 0.05, 0.02]\n").stations[0]
    coral = parse(tmp_path, CORAL_HEAD + CORAL_FEEDER + chute).kinds["coral"]
    s = math.radians(30.0)
    # dx up the slope and dz down both move the release toward the floor; dy runs along it
    assert spread.clearance(coral) - st.clearance(coral) == pytest.approx(0.01 * math.sin(s) + 0.02 * math.cos(s))
    assert spread.clearance(coral) is spread.clearance(coral)          # kept, not worked out again
    ball = parse(tmp_path, HEAD + FEEDER + chute + "spread_position = [0, 0, 0.02]\n")
    assert ball.stations[0].clearance(fuel()) == pytest.approx(0.1501 / 2 + 0.02 * math.cos(s))


# ---- the position spread in the station's own frame, and checked like the release point (kickoff dry run gap 16) --

def test_the_position_spread_is_along_and_across_the_heading(tmp_path):
    # heading 90 (fed toward +y): across it is along x, so spread_position = [0, 0.3, 0] varies x, not y
    w = station_world(tmp_path, "spread_position = [0.0, 0.04, 0.0]")
    st = w.test_range.range.stations[0]
    assert st.heading_deg == 0.0
    turned = tmp_path / "turned"
    turned.mkdir()
    text = (tmp_path / "station.toml").read_text().replace("heading_deg = 0.0 ", "heading_deg = 90.0 ")
    text = re.sub(r"(?m)^chute = .*\n", "", text)
    (turned / "station.toml").write_text(text)
    from scrimmage.assembler.templates import new_config
    from scrimmage.world import World
    w = World(tomllib.loads(new_config("standin") + '\n[range]\nfile = "station.toml"\n'), turned)
    w.set_robot_pose(*AWAY)
    st = w.test_range.range.stations[0]
    feeder = w.test_range.feeder
    feeder.start("feeder")
    advance(w, 3.05)
    assert len(feeder.fed) >= 3
    for f in feeder.fed:
        d = np.array(f.release) - np.array(st.release)
        assert abs(d[1]) < 1e-12 and abs(d[0]) <= 0.04 + 1e-12
    assert max(abs(f.release[0] - st.release[0]) for f in feeder.fed) > 0.01


SOURCE = HEAD + '''
[[element]]
name = "source_wall"
kind = "box"
center = [15.5, 0.6]
yaw_deg = 30.0
size = [2.0, 0.2, 1.4]

[[station]]
name = "source"
release = [15.3, 0.85, 1.0]
heading_deg = 120.0
pitch_deg = 50.0
'''


def test_a_spread_into_a_wall_is_refused(tmp_path):
    # the dry run's SOURCE: spread along the field's axes at a 30 deg wall put a feed inside the wall, and it shot out
    # at 13 m/s. Now the spread is the station's, and a spread that starts a piece inside the wall is refused
    with pytest.raises(RangeError, match=r"'source': its spread_position reaches into element 'source_wall': a piece "
                                         r"fed at \(.*\) would start inside it"):
        parse(tmp_path, SOURCE + "spread_position = [0.3, 0.3, 0.0]\n")
    rng = parse(tmp_path, SOURCE + "spread_position = [0.0, 0.3, 0.0]\n")     # across the heading: along the wall
    assert rng.stations[0].spread_position == (0.0, 0.3, 0.0)


def test_a_spread_off_the_field_is_refused(tmp_path):
    with pytest.raises(RangeError, match=r"'feeder': its spread_position reaches off the field"):
        parse(tmp_path, HEAD + FEEDER.replace("release = [1.0, 4.0, 0.5]", "release = [0.2, 4.0, 0.5]")
              + "spread_position = [0.3, 0.0, 0.0]\n")


def test_a_spread_that_grazes_a_wall_is_warned_about(tmp_path):
    # 0.33 m out from the wall's face, spread 0.3 m along the heading: the nearest feed starts 0.03 m from the face,
    # its center outside the wall but the ball (0.075 m) in it
    rng = parse(tmp_path, SOURCE.replace("release = [15.3, 0.85, 1.0]", "release = [15.25, 0.95, 1.0]")
                + "spread_position = [0.3, 0.0, 0.0]\n")
    assert any("'source'" in w and "spread_position" in w and "touches" in w for w in rng.warnings)


def test_overrides_check_the_spread_too(tmp_path):
    rng = parse(tmp_path, SOURCE)
    with pytest.raises(RangeError, match="spread_position reaches into element 'source_wall'"):
        with_overrides(rng, {"source": {"spread_position": [0.3, 0.3, 0.0]}})
