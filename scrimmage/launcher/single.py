"""One ball through the launcher with the wheel held at a speed: the pass the aiming table and the shot fit run.

The shaft is spinning at `rps` when the ball enters at the setup's entry speed, and a velocity loop holds it while the
ball passes (the setup's own gains, or `kP` in their place). Unlike a `run_setup` burst there is no spin-up and no
feed rule, so the ball meets the wheel at exactly the speed asked for.
"""

import dataclasses
import math

from .inputs import Setup
from .model import ExitedBall, FedBall, Launcher
from .shaft import VelocityController

MAX_PASS_S = 1.0  # s: a ball still in the launcher after this long is not coming out


def single_pass(setup: Setup, rps: float, lane: float, kP: float | None = None) -> ExitedBall | None:
    """The ball's outcome, or None if it is still in the launcher after MAX_PASS_S."""
    launcher = Launcher(setup)
    launcher.shaft.speed = 2.0 * math.pi * rps
    control = dataclasses.replace(setup.control, target_rps=rps)
    if kP is not None:
        control = dataclasses.replace(control, kP=kP)
    ctrl = VelocityController(control, setup.shaft)
    fed = FedBall(0.0, lane, setup.feed.entry_speed, setup.ball)
    shot = launcher.insert(fed, 0.0)
    dt = launcher.default_dt()
    exits = [] if shot is None else [shot]
    while not exits and launcher.in_contact() and launcher.time < MAX_PASS_S:
        exits = launcher.step(dt, ctrl.voltage(launcher.shaft))
    return exits[0] if exits else None
