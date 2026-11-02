import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.build_elements import chute_parts
from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError, load_range, parse_range
from scrimmage.testrange.station import Chute

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
    ("interval = 0", "interval"), ("stock = 0", "stock"), ("spread_speed = -1", "spread_speed"),
    ("pitch_deg = 100", "pitch_deg"), ("rate = 2", "rate"), ("spread_position = [0, -1, 0]", "spread_position"),
    ("release_in = [1, 2, 3]", "release"),
])
def test_bad_station_values(tmp_path, extra, key):
    with pytest.raises(RangeError, match=key):
        parse(tmp_path, HEAD + FEEDER + extra + "\n")


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
        if name == "station":       # the station field has one on purpose
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
        text = STATION_TOML.read_text(encoding="utf-8").replace('name = "feeder"\n', f'name = "feeder"\n{extra}\n')
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
        assert abs(d[0]) < 1e-12 and abs(d[1]) <= 0.02 and abs(d[2]) <= 0.01
        v = np.array(f.velocity)
        speed = float(np.linalg.norm(v))
        assert 0.5 <= speed <= 1.5
        assert abs(math.degrees(math.atan2(v[1], v[0]))) <= 10.0 + 1e-9
        assert abs(math.degrees(math.asin(-v[2] / speed))) <= 5.0 + 1e-9
    assert len({f.velocity for f in feeder.fed}) == 6


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

    w = World(range_cfg(), None)
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
