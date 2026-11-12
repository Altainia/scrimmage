"""Running a bench: shafts, inlets, outlets, outcomes, loads and energy."""

import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.bench.run import BenchRunError, run_bench
from scrimmage.bench.spec import parse_bench, with_overrides


def bench_from(text: str, **overrides):
    raw = tomllib.loads(text)
    if overrides:
        raw = with_overrides(raw, overrides)
    return parse_bench(raw, Path("."))


# A toy car (a rigid block) fed along a plate into two 60 mm wheels that squeeze it 5 mm each side. Its own
# stiffness (3000 N/m, a soft plastic body) lets it slip a little as it is gripped.
PINCH = """
[bench]
name = "pinch car"

[piece]
name = "car"
kind = "rigid"
shape = "box"
size = [0.10, 0.06, 0.04]
mass = 0.2
stiffness = 3000

[shaft.bottom]
motor = "KrakenX60"
target_rps = 40
kP = 0.5
wheel_inertia = 0.0005

[shaft.top]
motor = "KrakenX60"
target_rps = 40
kP = 0.5
wheel_inertia = 0.0005

[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.045]
diameter = 0.06
width = 0.08
tread = "60A"

[[wheel]]
name = "top"
shaft = "top"
center = [0.0, 0.0, 0.045]
axis = [0.0, -1.0, 0.0]
diameter = 0.06
width = 0.08
tread = "60A"

[[plate]]
name = "track"
center = [-0.15, 0.0, -0.025]
size = [0.22, 0.12, 0.01]

[[inlet]]
name = "feed"
center = [-0.2, 0.0, 0.001]
size = [0.01, 0.01, 0.001]
count = 2
rate = 4
velocity = [3.0, 0.0, 0.0]

[[outlet]]
name = "shot"
center = [0.45, 0.0, 0.0]
size = [0.1, 0.4, 0.4]

[run]
duration = 1.5
"""

# A 4 in wheel with a hood around its back (the launcher's layout: the ball goes in at the bottom and comes out at
# the top, about 50 degrees up). The 2026 fuel rolls along a feed plate under the wheel, which pulls it into the
# hood's flared mouth; the hood squeezes it about 0.21 D.
HOODED = """
[bench]
name = "small hooded"

[piece]
preset = "2026-fuel"

[shaft.s]
motor = "KrakenX60"
target_rps = 40
kP = 0.3
wheel_inertia = 0.002

[[wheel]]
name = "wheel"
shaft = "s"
center = [0.0, 0.0, 0.0]
diameter = 0.1016
width = 0.15
tread = "35A"

[[curve]]
name = "hood"
center = [0.0, 0.0, 0.0]
radius = 0.17
start_deg = 140
end_deg = 235
width = 0.2

[[curve]]
name = "flare"
center = [0.0, 0.0, 0.0]
radius_start = 0.17
radius_end = 0.21
start_deg = 235
end_deg = 265
width = 0.2

[[plate]]
name = "feeder"
center = [0.13, 0.0, -0.21]
size = [0.26, 0.2, 0.01]

[[inlet]]
name = "feed"
center = [0.15, 0.0, -0.122]
size = [0.01, 0.01, 0.01]
count = 2
rate = 3
velocity = [-2.0, 0.0, 0.0]

[[outlet]]
name = "out"
center = [0.3, 0.0, 0.5]
size = [0.6, 0.6, 0.4]

[run]
duration = 1.2
"""


@pytest.fixture(scope="module")
def pinch():
    return run_bench(bench_from(PINCH))


@pytest.fixture(scope="module")
def hooded():
    return run_bench(bench_from(HOODED))


def test_pinch_car_leaves_near_surface_speed(pinch):
    surface = 2 * math.pi * 40 * 0.03
    assert [p.status for p in pinch.pieces] == ["out", "out"]
    for p in pinch.pieces:
        assert p.outlet == "shot"
        # about 0.81 of it: the shafts droop about 11% while they grip the car, and the wheels grip it 5 mm into
        # their 30 mm radius (at 25 mm a wheel moves 0.83 of its surface speed, and with the droop 0.74). The floor
        # was 0.80 when MuJoCo 4.0.0's cylinders pushed the car out at 0.86 (their edge contacts' normals were
        # horizontal); with the right normals it is 0.81, and the wheel prism's side count moves it 0.5% (32 sides
        # 0.80, 128 sides 0.81)
        assert 0.75 * surface <= p.speed <= 0.95 * surface
        assert abs(p.elevation_deg) < 10 and abs(p.yaw_deg) < 2  # (gravity bends it down a few degrees by the outlet)
        assert p.tilt_deg < 10  # upright: its own z axis still within 10 degrees of up
        assert p.t_out > p.t_in
        assert p.peak_force > 0.0
        assert len(p.path) >= 3 and p.path[0][0] == pytest.approx(p.t_in)


def test_energy_never_created(pinch, hooded):
    for res in (pinch, hooded):
        assert len(res.energy) == len(res.pieces)
        for e in res.energy:
            assert e.ok, e
    # the wheels did real work on every car, and on the balls that went through the hood
    assert all(e.work_in > 0.5 * e.e_out for e in pinch.energy)
    assert [p.status for p in hooded.pieces] == ["out", "out"]
    assert all(e.work_in > 0.1 for e in hooded.energy)
    assert hooded.loads["hood"].peak_force_n > 0.0
    assert hooded.loads["wheel"].peak_impulse_ns > 0.0


def test_same_file_same_result(pinch):
    again = run_bench(bench_from(PINCH))
    assert again.pieces == pinch.pieces
    assert again.shafts == pinch.shafts
    assert again.loads == pinch.loads
    assert again.energy == pinch.energy
    assert len(again.trace) == len(pinch.trace)
    assert all(t1 == t2 and (q1 == q2).all() for (t1, q1), (t2, q2) in zip(again.trace, pinch.trace))


TRAP = """
[bench]
name = "trap"

[piece]
preset = "2026-fuel"

[shaft.stopped]
wheel_inertia = 0.002

[[wheel]]
name = "stopped"
shaft = "stopped"
center = [0.135, 0.0, 0.05]
diameter = 0.1016
width = 0.15

[[plate]]
name = "floor"
center = [0.0, 0.0, -0.005]
size = [0.5, 0.3, 0.01]

[[plate]]
name = "wall"
center = [-0.09, 0.0, 0.1]
size = [0.01, 0.3, 0.2]

[[inlet]]
name = "feed"
center = [0.0, 0.0, 0.1]
size = [0.01, 0.01, 0.01]
velocity = [1.0, 0.0, 0.0]

[[outlet]]
name = "never"
center = [1.0, 0.0, 0.0]
size = [0.1, 0.1, 0.1]

[run]
duration = 2.0
jam_time = 0.3
"""


def test_trapped_ball_jams():
    res = run_bench(bench_from(TRAP))
    (p,) = res.pieces
    assert p.status == "jammed"
    assert p.outlet is None
    assert p.t_out < 2.0  # found jammed, and the run ended with it
    assert p.t_out - p.t_in >= 0.3


BLOWUP = """
[bench]
name = "absurd"

[piece]
kind = "squishy"
shape = "sphere"
size = [0.15]
mass = 0.2
stiffness_n = 1e9
restitution = 0.5

[[plate]]
name = "floor"
center = [0.0, 0.0, -0.005]
size = [0.4, 0.4, 0.01]

[[inlet]]
name = "feed"
center = [0.0, 0.0, 0.08]
size = [0.01, 0.01, 0.01]
velocity = [0.0, 0.0, -3.0]

[[outlet]]
name = "out"
center = [0.6, 0.0, 0.0]
size = [0.1, 0.1, 0.1]

[run]
duration = 0.5
"""


def test_blow_up_is_reported():
    with pytest.raises(BenchRunError, match=r"t = \d.*piece 0 "):
        run_bench(bench_from(BLOWUP))


def test_nan_contact_force_is_reported(monkeypatch):
    """A NaN in a contact force: MuJoCo would reset the state (time and all) and carry on; the run must stop and
    name the piece and the time it happened, not the reset's."""
    import scrimmage.bench.run as run_mod

    class Poisoned(run_mod.SquishyContacts):
        def apply(self, d):
            loads = super().apply(d)
            if d.time >= 0.35:
                d.xfrc_applied[self.lay.piece_bodies[0], 0] = float("nan")
            return loads

    monkeypatch.setattr(run_mod, "SquishyContacts", Poisoned)
    bench = bench_from(HOODED, **{"inlet.feed.rate": 10})  # both balls in the bench at once
    with pytest.raises(BenchRunError, match=r"t = 0\.35\d* s: piece 0 has a non-finite contact force"):
        run_bench(bench)


def test_bad_accelerations_are_reported_at_the_right_time(monkeypatch):
    """MuJoCo's own reset (bad qacc) from a NaN it finds itself: the time and piece come from the step's start."""
    import scrimmage.bench.run as run_mod

    real_step = run_mod.mujoco.mj_step
    seen = {}

    def step(m, d):
        if d.time >= 0.35 and not seen:
            seen["t"] = d.time
            d.qacc_warmstart[:] = 0.0
            d.qfrc_applied[0] = float("nan")  # past the runner's own check: only MuJoCo sees it
        real_step(m, d)

    monkeypatch.setattr(run_mod.mujoco, "mj_step", step)
    bench = bench_from(HOODED, **{"inlet.feed.rate": 10})
    with pytest.raises(BenchRunError, match=r"t = 0\.35\d* s: piece \d \(the fastest") as e:
        run_bench(bench)
    assert f"t = {seen['t']:.4f} s" in str(e.value)


ESCAPE = """
[bench]
name = "escape and stuck"

[piece]
name = "block"
kind = "rigid"
shape = "box"
size = [0.05, 0.05, 0.05]
mass = 0.1

[[plate]]
name = "floor"
center = [0.0, 0.0, -0.005]
size = [0.3, 0.3, 0.01]

[[inlet]]
name = "blocked"
center = [0.0, 0.0, 0.0255]
size = [0.001, 0.001, 0.001]
count = 2
rate = 20

[[inlet]]
name = "thrown"
center = [0.0, 0.1, 0.2]
size = [0.01, 0.01, 0.01]
velocity = [-6.0, 0.0, 0.0]

[[outlet]]
name = "out"
center = [0.6, 0.0, 0.0]
size = [0.1, 0.1, 0.1]

[run]
duration = 0.6
jam_time = 5.0
"""


def test_escape_and_stuck_statuses():
    res = run_bench(bench_from(ESCAPE))
    by_inlet = [(p.inlet, p.status) for p in res.pieces]
    # the first block sits on the floor where it spawned, so the second never gets in
    assert by_inlet == [("blocked", "timed_out"), ("blocked", "stuck_at_inlet"), ("thrown", "escaped")]
    assert res.pieces[1].t_in is None and res.pieces[1].path == []
    assert res.pieces[0].t_in is not None
    assert res.pieces[2].outlet is None and res.pieces[2].t_out is not None


EXAMPLE_HOOD = Path(__file__).resolve().parent.parent / "examples" / "bench" / "hooded.toml"


def _hood(**overrides):
    raw = with_overrides(tomllib.loads(EXAMPLE_HOOD.read_text()), overrides)
    return run_bench(parse_bench(raw, EXAMPLE_HOOD.parent)).shafts["shooter"]


def test_recovery_is_back_to_the_speed_it_had_not_the_target():
    # the speed loop settles a little under its target (about 1.6% at 2000 rpm), so "back within 2% of the target"
    # could take until the last of ten balls had gone: recovery is back to the speed the shaft had when the ball came
    s = _hood(**{"shaft.shooter.target_rps": 33.3})
    assert s.recovery_s is not None and 0.0 < s.recovery_s < 0.2
    assert s.back_before_next is True


def test_shaft_not_back_before_the_next_piece_is_said():
    # balls every 0.05 s, each in contact about 0.1 s: the shaft can't get back to speed in between
    s = _hood(**{"shaft.shooter.target_rps": 33.3, "inlet.feed.rate": 20})
    assert s.back_before_next is False and s.recovery_s is None


def test_shaft_drop_and_recovery(pinch):
    for name in ("top", "bottom"):
        s = pinch.shafts[name]
        assert s.target_rps == 40
        assert s.min_rps < 40
        assert 1.0 < s.drop_pct < 50.0
        assert s.recovery_s is not None and 0.0 < s.recovery_s < 0.5
        assert s.peak_current > 0.0
        assert s.energy_per_piece_j > 0.0
    # the pinch car gets its energy from both wheels; together they gave each car more than it left with
    given = pinch.shafts["top"].energy_per_piece_j + pinch.shafts["bottom"].energy_per_piece_j
    car = pinch.energy[0]
    assert given > car.e_out - car.e_in


def test_headline_says_the_shaft_is_not_back_in_time():
    from scrimmage.bench.report import headlines
    raw = with_overrides(tomllib.loads(EXAMPLE_HOOD.read_text()),
                         {"shaft.shooter.target_rps": 33.3, "inlet.feed.rate": 20})
    lines = headlines(run_bench(parse_bench(raw, EXAMPLE_HOOD.parent)))
    assert any("isn't back to speed when the next piece arrives" in line for line in lines), lines


EXAMPLE_BAR = EXAMPLE_HOOD.parent / "bounce_bar.toml"


def _run_file(path, **overrides):
    raw = with_overrides(tomllib.loads(path.read_text()), overrides)
    return run_bench(parse_bench(raw, path.parent))


def test_exit_slip_tells_grip_from_slipping():
    # the hooded example grips (its exit speed follows the wheel); the bounce bar's pinch is still slipping when the
    # ball leaves (its exit speed is the same at 30 and 60 rps)
    hood = [p.exit_slip for p in _run_file(EXAMPLE_HOOD).pieces if p.status == "out"]
    bar = [p.exit_slip for p in _run_file(EXAMPLE_BAR).pieces if p.status == "out"]
    assert hood and all(s is not None and s < 0.10 for s in hood), hood
    assert bar and all(s is not None and s > 0.15 for s in bar), bar


def test_headline_says_more_speed_wont_help_while_slipping():
    from scrimmage.bench.report import headlines
    lines = headlines(_run_file(EXAMPLE_BAR))
    line = next((x for x in lines if "still slipping" in x), None)
    assert line is not None and "more wheel speed" in line and "more grip" in line, lines
    assert not any("still slipping" in x for x in headlines(_run_file(EXAMPLE_HOOD)))


def test_any_piece_not_back_in_time_is_said_even_if_the_worst_came_later():
    # rate 4: the last ball's dip is the worst, but earlier balls weren't back to speed when the next arrived
    s = _hood(**{"shaft.shooter.target_rps": 33.3, "inlet.feed.rate": 7})
    assert s.back_before_next is False and s.recovery_s is None


def test_slip_counts_only_when_the_wheel_contact_ended_just_before_leaving():
    # the bounce bar's balls leave the wheels, then fly to an outlet 0.9 m away: still the launch's slip; the same
    # pinch with the outlet moved far away (a long roll after the wheels) is not a launch, so no exit slip
    near = [p.exit_slip for p in _run_file(EXAMPLE_BAR).pieces if p.status == "out"]
    assert near and all(s is not None for s in near)
    far = _run_file(EXAMPLE_BAR, **{"outlet.goal.center": [3.0, 0.0, -2.0], "outlet.goal.size": [1.0, 1.0, 1.0],
                                     "run.duration": 4.0})
    gone = [p.exit_slip for p in far.pieces if p.status == "out"]
    assert gone and all(s is None for s in gone), gone
