"""Aiming a stock turret at a range goal (spec C2, C3): how far off it points, and the joint angle that points it.

The error is the horizontal angle from the aiming frame's forward (+x) axis to the line from the frame's origin to
the goal's center, signed positive when the goal is to the left (counter-clockwise from above, as the turret's joint
turns). The aiming frame is the `[shooter]` launcher's base when the launcher rides on this turret (its body chain
passes through `<turret>.platform`), else the turret's platform. Aiming takes no account of the robot's motion (C3).

The target is the turret's joint angle now plus the error, clamped to the joint's range. On a turret whose range
spans more than a turn, the target is the in-range angle pointing at the goal nearest the angle now (our call: a
turret at 170° of +-180° aiming 20° further left goes to -170°, not to its stop); when none is in range, the stop
nearest such an angle, and the goal is out of range.
"""

from __future__ import annotations

import math

TURN = "turn"           # the stock turret's joint, `<inst>.turn`
PLATFORM = "platform"   # the body it turns


def turrets(world) -> list[str]:
    """The robot's stock turrets' instance names, sorted."""
    parts = world.parts
    if parts is None:
        return []
    return sorted(i.name for i in parts.instances if i.source == "stock turret")


def _frame(world, turret: str) -> int:
    """The aiming frame's body: the [shooter] launcher's base when it rides on the turret's platform, else the
    platform."""
    m = world.model
    platform = m.body(f"{turret}.{PLATFORM}").id
    tr = world.test_range
    shooter = tr.shooter if tr is not None else None
    if shooter is not None:
        cfg = shooter.config
        b = m.body(f"{cfg.part}.{'channel' if cfg.kind == 'disc' else 'base'}").id
        base = b
        while b > 0:
            if b == platform:
                return base
            b = int(m.body_parentid[b])
    return platform


def _goal(world, goal: str) -> tuple[float, float]:
    for g in world.parts.range.goals:
        if g.name == goal:
            return float(g.pos[0]), float(g.pos[1])
    raise KeyError(goal)


def aim_error(world, turret: str, goal: str) -> float:
    """rad, signed: positive when the goal is to the left of where the turret (its launcher) points, horizontally."""
    d = world.data
    b = _frame(world, turret)
    gx, gy = _goal(world, goal)
    r = d.xmat[b].reshape(3, 3)
    fx, fy = float(r[0, 0]), float(r[1, 0])                 # the frame's x axis, seen from above
    dx, dy = gx - float(d.xpos[b][0]), gy - float(d.xpos[b][1])
    return math.atan2(fx * dy - fy * dx, fx * dx + fy * dy)


def joint_range(world, turret: str) -> tuple[float, float]:
    """The turret joint's range, rad."""
    m = world.model
    j = m.joint(f"{turret}.{TURN}").id
    lo, hi = m.jnt_range[j]
    return float(lo), float(hi)


def aim_target(world, turret: str, goal: str) -> tuple[float, bool]:
    """(the joint target, rad; whether it points at the goal within the turret's range)."""
    m, d = world.model, world.data
    q = float(d.qpos[m.jnt_qposadr[m.joint(f"{turret}.{TURN}").id]])
    want = q + aim_error(world, turret, goal)
    if not m.jnt_limited[m.joint(f"{turret}.{TURN}").id]:
        return want, True
    lo, hi = joint_range(world, turret)
    tau = 2.0 * math.pi
    ks = range(math.floor((lo - want) / tau) - 1, math.ceil((hi - want) / tau) + 2)
    inside = [want + k * tau for k in ks if lo <= want + k * tau <= hi]
    if inside:
        return min(inside, key=lambda x: abs(x - q)), True
    # none in range: the stop nearest an angle that points at the goal
    best = min((want + k * tau for k in ks), key=lambda x: min(abs(x - lo), abs(x - hi)))
    return (lo if abs(best - lo) <= abs(best - hi) else hi), False
