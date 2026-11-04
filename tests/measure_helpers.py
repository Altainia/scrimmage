"""Shared by the measurement tests: a launcher setup and launcher measurement files whose shots come from the model
itself (synthetic, so a fit's answer is known), and a ball file with squeeze trials."""
import dataclasses

from scrimmage.launcher.inputs import load_setup
from scrimmage.measure import file
from scrimmage.measure.fit_launcher import horizontal_speed

INCH = 0.0254
MARKS_IN = 24.0  # marks two feet apart filmed at 960 fps: each shot spans 60 to 80 frames, so a speed read to 2%
FPS = 960.0

# A one-wheel hooded launcher shooting the 2026 FUEL ball (examples/launcher/hooded.toml, with the hood gap made for
# a 5.9 in. ball: a 1.6 in. squeeze, 27% of it). Its numbers are the presets': stiffness_n 80, wheel friction 0.95.
# The squeeze is chosen so neither test speed is near the wheel's own surface speed limit, where the exit speed
# hardly moves with stiffness or friction.
SETUP = """\
[ball]
preset = "2026-fuel"

[wheels]
preset = "35A"
diameter = 0.1016
roller = true
width = 0.127

[shaft]
wheel_inertia = 0.0006
added_inertia = 0.002
motor = "KrakenX60"

[control]
target_rps = 66.7
kP = 0.2
stator_limit = 80.0
supply_limit = 60.0

[hood]
gap = 0.11
exit_angle_deg = 50.0
friction = 0.5

[launcher]
height = 0.6
width = 0.2

[goal]
distance = 4.0
height = 2.5
diameter = 0.76

[feed]
lanes = [0.0]
"""

HEADER = """\
[measure]
kind = "launcher"
about = "the practice robot's shooter"
by = "J. Smith"
date = 2026-11-10
tools = "phone at 960 fps, tape marks 24 in apart"

[shot_setup]
setup = "shooter.toml"
"""


def write_setup(tmp_path):
    p = tmp_path / "shooter.toml"
    p.write_text(SETUP, encoding="utf-8")
    return load_setup(p)[0]


def truth(setup, stiffness_n, friction):
    return dataclasses.replace(setup, ball=dataclasses.replace(setup.ball, stiffness_n=stiffness_n),
                               wheels=dataclasses.replace(setup.wheels, friction=friction))


def frames(speed):
    """The frames a ball at horizontal `speed` takes between the marks (a fraction: the synthetic shots are exact)."""
    return MARKS_IN * INCH * FPS / speed


def measure_file(tmp_path, shots, header=HEADER):
    write_setup(tmp_path)
    rows = "".join(f"\n[[shot]]\nwheel_rps = {rps}\nmarks_in = {MARKS_IN}\nframes = {n!r}\nfps = {FPS}\n"
                   for rps, n in shots)
    p = tmp_path / "shooter.measure.toml"
    p.write_text(header + rows, encoding="utf-8")
    return file.load(p)


def synthetic(tmp_path, stiffness_n, friction, speeds, header=HEADER):
    model = truth(write_setup(tmp_path), stiffness_n, friction)
    return measure_file(tmp_path, [(rps, frames(horizontal_speed(model, rps))) for rps in speeds],
                        header=header)


def ball_file(tmp_path, squeezes_n, name="fuel.measure.toml"):
    """A ball file whose squeeze trials read `squeezes_n` (N, at a 25% squeeze)."""
    rows = "".join(f"\n[[squeeze]]\nforce_n = {f}\n" for f in squeezes_n)
    (tmp_path / name).write_text('[measure]\nkind = "ball"\nby = "J. Smith"\ndate = 2026-11-10\n'
                                 'base = "2026-fuel"\n' + rows, encoding="utf-8")
    return name


WITH_BALL = HEADER + 'ball = "fuel.measure.toml"\n'
