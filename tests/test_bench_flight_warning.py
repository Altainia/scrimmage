"""bench check warns when [flight] is given with a rigid piece (kickoff dry run gap 20): before, only a run's summary
said there would be no landings."""

from scrimmage.bench.problems import collect_problems

BENCH = '''
[bench]
name = "note pinch shooter"
[piece]
name = "note"
kind = "rigid"
shape = "disc"
size_in = [14.0, 2.0]
mass = 0.2353
stiffness = 2000
[shaft.bottom]
motor = "KrakenX60"
target_rps = 60
kP = 0.5
wheel_inertia = 0.001
[[wheel]]
name = "bottom"
shaft = "bottom"
center_in = [0.0, 0.0, -2.625]
diameter_in = 4.0
width_in = 14.0
tread = "60A"
[[inlet]]
name = "feed"
center_in = [-12.0, 0.0, 0.02]
size = [0.005, 0.005, 0.001]
count = 1
velocity = [2.0, 0.0, 0.0]
[[outlet]]
name = "shot"
center = [0.6, 0.0, 0.0]
size = [0.2, 0.8, 0.8]
[run]
duration = 1.0
'''


def test_flight_with_a_rigid_piece_is_a_check_warning(tmp_path):
    f = tmp_path / "b.toml"
    f.write_text(BENCH + '[flight]\noutlet = "shot"\nheight = 0.5\ntarget_distance = 2.0\ntarget_height_in = 80.0\n')
    bench, problems = collect_problems(f)
    assert bench is not None, problems
    assert any(p.level == "warning" and "flies balls only" in p.message for p in problems), problems
    f.write_text(BENCH)
    assert not any("flies balls only" in p.message for p in collect_problems(f)[1])
