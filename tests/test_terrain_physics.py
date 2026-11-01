"""Terrain physics checked against hand calculations: a robot's flight off a 15 deg ramp, the slope a robot tips over
on, the CHARGE STATION's tilt under a robot, and a raised elevator's effect on tipping.

These guard the spec's anti-goal: a robot that sails over a ramp because its contact is too soft, or bounces like a
ball because it's too stiff. Each test sets the robot up, then compares what the simulation does with the number a
hand calculation gives from the built model (its masses, CoM and wheel positions), not with a recorded run.

The robots' motors are unpowered. `push` starts a robot rolling: its free joint moving forward and every tread wheel
spinning to match, so it rolls rather than skids.
"""

from __future__ import annotations

import copy
import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import KITBOT, range_cfg
from scrimmage.parts.field import ELEMENT_GROUP
from scrimmage.protocol import HalState
from scrimmage.testrange.build_elements import platform_tilt
from scrimmage.testrange.elements import IN, Platform
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]
G = 9.81
HAL = HalState()
# The kitbot without device IDs: no motor is bound, so its wheels coast (a PWM motor brakes at neutral).
COASTING_KITBOT = [copy.deepcopy(KITBOT[0]), {k: v for k, v in KITBOT[1].items() if k != "ids"}]
# configs/fastmode.toml's robot (swerve, a 1 m elevator, an arm on its carriage), its devices unbound likewise.
FASTMODE = [{k: v for k, v in p.items() if k != "ids"}
            for p in tomllib.loads((ROOT / "configs" / "fastmode.toml").read_text())["part"]]
BUMPER_CLEARANCE = 0.019     # m: the stock chassis's bumpers above the carpet, resting (tests/test_tread.py)
TOE_X = 4.0                  # m: where a test ramp's slope meets the carpet; its slope climbs toward +x


def ramp_world(tmp_path: Path, parts, *, deg: float, height: float, deck: float, heading: float = 0.0,
               friction: float | None = None) -> World:
    """`parts` on a range with one single-sided ramp: a slope climbing `height` at `deg` from TOE_X toward +x, then
    a `deck` and a drop. The robot starts on the carpet 2 m short of the toe, facing `heading`."""
    run = height / math.tan(math.radians(deg))
    rng = tmp_path / f"ramp-{deg:g}.toml"
    rng.write_text(f'''
[range]
name = "test ramp"
ball = "2026-fuel"
[[element]]
name = "ramp"
kind = "ramp"
center = [{TOE_X + (run + deck) / 2.0}, 2.0]
width = 1.6
height = {height}
ramp_deg = {deg}
deck = {deck}
two_sided = false
{f"friction = {friction}" if friction is not None else ""}
[start.a]
pose = [{TOE_X - 2.0}, 2.0, {heading}]
''')
    return World(range_cfg(part=copy.deepcopy(parts), range={"file": rng.name, "start": "a"}), tmp_path)


def settle(w: World, seconds: float) -> None:
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(HAL)


def tread_wheels(w: World) -> list[tuple[int, int, float]]:
    """Each tread wheel's (geom, hinge joint, radius)."""
    m = w.model
    out = []
    for name in w.parts.tread_geoms:
        g = m.geom(name).id
        out.append((g, int(m.body_jntadr[m.geom_bodyid[g]]), float(m.geom_size[g][0])))
    return out


def push(w: World, vx: float) -> None:
    """Set the robot moving at vx along its own x axis (up a slope it rests on, along the slope), every tread wheel
    spinning at vx / r about its axle so it rolls without slipping, and everything else at rest."""
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    rot = d.xmat[w.robot_body].reshape(3, 3)
    vel = vx * rot[:, 0]
    d.qvel[:] = 0.0
    d.qvel[w.free_dadr:w.free_dadr + 3] = vel
    spin = np.cross(rot[:, 2], vel)          # the wheel's angular velocity when its bottom is still: n x v / r
    for _, j, r in tread_wheels(w):
        d.qvel[m.jnt_dofadr[j]] = float(spin @ d.xaxis[j]) / r
    mujoco.mj_forward(m, d)


def effective_mass(w: World) -> float:
    """The robot's mass plus its spinning wheels' (and their rotors') inertia over r^2: twice its kinetic energy
    rolling at 1 m/s. Leaves it at rest."""
    push(w, 1.0)
    out = np.zeros(w.model.nv)
    mujoco.mj_mulM(w.model, w.data, out, w.data.qvel)
    mass = float(w.data.qvel @ out)
    w.data.qvel[:] = 0.0
    mujoco.mj_forward(w.model, w.data)
    return mass


def onto_slope(w: World, deg: float, x: float) -> None:
    """Move the robot, resting on the carpet, so its origin is at `x`, then turn it rigidly about the toe line so the
    carpet's plane becomes the slope's: a point on the carpet s past the toe lands s up the slope, at its height above
    the carpet. The robot keeps its tread sag and every joint; it is left at rest."""
    m, d = w.model, w.data
    q = w.free_qadr
    d.qpos[q] = x
    turn = np.zeros(4)
    mujoco.mju_axisAngle2Quat(turn, np.array([0.0, 1.0, 0.0]), -math.radians(deg))    # +x end up
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, turn)
    toe = np.array([TOE_X, d.qpos[q + 1], 0.0])
    d.qpos[q:q + 3] = toe + rot.reshape(3, 3) @ (d.qpos[q:q + 3] - toe)
    quat = np.zeros(4)
    mujoco.mju_mulQuat(quat, turn, d.qpos[q + 3:q + 7].copy())
    d.qpos[q + 3:q + 7] = quat
    d.qvel[:] = 0.0
    mujoco.mj_forward(m, d)


def robot_frame(w: World, p) -> np.ndarray:
    """World point p in the robot body's frame."""
    b = w.robot_body
    return w.data.xmat[b].reshape(3, 3).T @ (np.asarray(p) - w.data.xpos[b])


# ---- Flight off a ramp ---------------------------------------------------------------------------------------------

FLIGHT_DEG = 15.0
FLIGHT_HEIGHT = 6.51 * IN    # m: the 2026 BUMP's height, so the drop is the one a robot leaving its crest falls
FLIGHT_DECK = 0.1            # m: a short deck past the crest; the robot clears it
FLIGHT_SPEED = 3.0           # m/s: the robot's speed leaving the crest
REAR_AXLE_UP = 0.12          # m: the start's rear axle up the slope from the toe, so the rear bumper clears the carpet
TAKEOFF_SPEED = 0.05         # of the speed energy gives at the takeoff's height
FLIGHT_TOLERANCE = 0.02      # m: the CoM's x at first touch against projectile motion from the takeoff
REBOUND = 0.002              # m: how far the CoM may come back above its resting height once the treads take the
                             # landing (0.02 mm at the default tread damping, 4 mm at 0.3, 15 mm at 0.1)
BUMPER_STRIKE_S = 0.25       # s: how long after touch-down the bumpers may still touch the carpet (see the test)


def takeoff(com, off: int, dt: float) -> tuple[np.ndarray, np.ndarray]:
    """The CoM's position and velocity on airborne step `off` + 1, by a central difference over off .. off + 2 (in
    free fall both ends of it, so exact up to the integrator's g dt^2 terms)."""
    return com[off + 1], (com[off + 2] - com[off]) / (2.0 * dt)


def test_flight_off_a_ramp_matches_projectile_motion(tmp_path):
    """A rigid robot leaving a 15 deg ramp's crest at 3 m/s leaves it as energy says, without a spring's kick, and
    flies as projectile motion says.

    The robot rolls up the slope from rest-on-the-slope at v0, the speed that leaves it at 3 m/s at the crest (energy:
    1/2 m_eff v0^2 = 1/2 m_eff v^2 + m g dh, m_eff counting the wheels' spin). It leaves the ramp twice: a hop of a few
    ms off the crest, its rear wheels then rolling over the short deck, and the flight proper off the deck's edge, about
    0.1 s later. At each takeoff (the first step touching nothing) its CoM speed is within TAKEOFF_SPEED of what energy
    gives at that height, and it rises no faster than 1.1 v sin 15 + 0.05 m/s: soft contact springing the robot up
    would beat that. Then, from the flight's measured takeoff (CoM position and velocity), projectile motion gives the
    CoM's x when it has fallen to the height it has at its first touch: the measured x there agrees within
    FLIGHT_TOLERANCE. The robot touches pitched nose-down, its CoM about 70 mm above its resting height, so the matched
    height is the measured one, not the resting one.

    What scrapes: the robot pitches nose-down over the crest (its rear wheels still on the slope as the front leaves),
    about 13 deg at touch-down, and the kitbot's front bumper reaches 0.15 m past its front wheels, 19 mm up: past
    7 deg of pitch it is the robot's lowest point. So the front bumper touches first and the robot rocks back onto its
    rear wheels, the rear bumper touching as it does. That is geometry, not soft contact. The contact's checks: the
    treads carry the landing short of the bumpers' clearance; once sunk into its treads the robot doesn't bounce back
    up (a ball would); and the bumpers are off the carpet within BUMPER_STRIKE_S."""
    w = ramp_world(tmp_path, COASTING_KITBOT, deg=FLIGHT_DEG, height=FLIGHT_HEIGHT, deck=FLIGHT_DECK)
    m, d, b = w.model, w.data, w.robot_body
    settle(w, 0.5)
    rest_z = float(d.subtree_com[b][2])                  # h: the CoM's height resting on the carpet
    rear_axle = min(robot_frame(w, d.geom_xpos[g])[0] for g, _, _ in tread_wheels(w))
    origin = TOE_X + REAR_AXLE_UP - rear_axle
    com_s = origin + robot_frame(w, d.subtree_com[b])[0] - TOE_X     # the CoM's distance up the slope, once on it
    onto_slope(w, FLIGHT_DEG, origin)

    th = math.radians(FLIGHT_DEG)
    slope = FLIGHT_HEIGHT / math.sin(th)
    mass, m_eff = float(m.body_subtreemass[b]), effective_mass(w)
    v0 = math.sqrt(FLIGHT_SPEED ** 2 + 2.0 * mass * G * (slope - com_s) * math.sin(th) / m_eff)
    start_z = float(d.subtree_com[b][2])
    dt = m.opt.timestep

    robot = m.body_rootid[m.geom_bodyid] == m.body_rootid[b]
    ground = (m.geom_group == ELEMENT_GROUP) & ~robot
    treads = {g for g, _, _ in tread_wheels(w)}
    push(w, v0)
    mon = w.terrain
    mon.consume()
    air, com, sink, frame_hits = [], [], [], []      # per step
    for _ in range(round(1.5 / m.opt.timestep)):
        w.step(HAL)
        air.append(mon.sample()["robot.air_time"])
        mon.consume()
        com.append(d.subtree_com[b].copy())
        deepest, frame = 0.0, False
        for i in range(d.ncon):
            g1, g2 = d.contact.geom[i]
            mine, other = (g1, g2) if robot[g1] else (g2, g1)
            if robot[mine] and ground[other]:
                if mine in treads:
                    deepest = max(deepest, -float(d.contact.dist[i]))
                else:
                    frame = True
        sink.append(deepest)
        frame_hits.append(frame)

    longest = int(np.argmax(air))                     # the flight's last airborne step
    down = longest + 1                                # touch-down: the first touch after the flight
    assert air[longest] > 0.1, "the robot never left the ramp"
    leave = longest
    while air[leave - 1] > 0:
        leave -= 1                                    # the flight's first airborne step
    hop = next(i for i, a in enumerate(air) if a > 0)     # the first: leaving the crest, before the rear wheels touch
    for off in sorted({hop, leave}):                  # the deck and roll off its edge
        pos, vel = takeoff(com, off, dt)
        climbed = math.sqrt(v0 ** 2 - 2.0 * mass * G * (pos[2] - start_z) / m_eff)     # energy, to that height
        assert np.linalg.norm(vel) == pytest.approx(climbed, rel=TAKEOFF_SPEED)
        assert vel[2] <= FLIGHT_SPEED * math.sin(th) * 1.1 + 0.05, f"launched upward at {vel[2]:.2f} m/s: a spring"
    pos, vel = takeoff(com, leave, dt)
    touch_z = float(com[down][2])
    vx, vz = math.hypot(vel[0], vel[1]), float(vel[2])
    t = (vz + math.sqrt(vz ** 2 + 2.0 * G * (pos[2] - touch_z))) / G
    expected = pos[0] + vx * t
    landed = float(com[down][0])
    assert landed == pytest.approx(expected, abs=FLIGHT_TOLERANCE), (
        f"first touch at CoM x {landed:.3f}, projectile motion from the takeoff says {expected:.3f}")
    assert not any(frame_hits[:longest])                        # the climb and takeoff: wheels only
    assert max(sink[down:]) < BUMPER_CLEARANCE                  # the treads carry the landing
    sunk = next(i for i in range(down, len(com)) if com[i][2] < rest_z)     # first below its resting height
    assert max(c[2] for c in com[sunk:]) - rest_z < REBOUND                  # then no bounce back above it
    strike_steps = round(BUMPER_STRIKE_S / m.opt.timestep)
    assert not any(frame_hits[down + strike_steps:])
    assert com[-1][2] == pytest.approx(rest_z, abs=0.002)        # rolling on, level, on its treads
    assert mon.sample()["robot.tilt"] < 1.0


# ---- Tipping across a slope ----------------------------------------------------------------------------------------

TIP_SLOPE = 1.3              # m: the test ramps' slope length, room for the robot sideways with its bumpers
TIP_FRICTION = 2.0           # the test ramps' friction: atan(2) = 63 deg, so the robots tip before they slide
TIP_STEP = 2.0               # deg between ramps
TIP_FROM = 30.0              # deg: the first ramp
TIP_HOLD = 1.5               # s on each ramp
TIP_ROLL = 1.0               # deg: rolled this far off the slope, the robot's uphill wheels have lifted: it tips
TIP_TOLERANCE = 3.0          # deg
ROBOTS = {"kitbot": COASTING_KITBOT, "fastmode": FASTMODE}


def lock_joint(w: World, name: str, q: float) -> None:
    """Hold joint `name` at q: its range closed to [q, q]."""
    j = w.model.joint(name).id
    w.model.jnt_range[j] = [q, q]
    w.model.jnt_limited[j] = 1
    w.data.qpos[w.model.jnt_qposadr[j]] = q


def tips_on(tmp_path: Path, robot: str, elevator: float | None, deg: float) -> tuple[float, bool]:
    """The robot, parked sideways (its left side downhill) on a ramp at `deg`: (the tipping angle the built model's
    geometry predicts, whether it tips within TIP_HOLD). The fast-mode robot's elevator is held at `elevator` and its
    arm level."""
    th = math.radians(deg)
    w = ramp_world(tmp_path, ROBOTS[robot], deg=deg, height=round(TIP_SLOPE * math.sin(th), 6), deck=0.2,
                   heading=90.0, friction=TIP_FRICTION)
    m, d, b = w.model, w.data, w.robot_body
    if elevator is not None:
        lock_joint(w, "elevator.carriage", elevator)
        lock_joint(w, "arm.pivot", 0.0)
        mujoco.mj_forward(m, d)
    settle(w, 0.6)
    com = robot_frame(w, d.subtree_com[b])
    downhill = max(robot_frame(w, d.geom_xpos[g])[1] for g, _, _ in tread_wheels(w))    # the left wheels' line
    predicted = math.degrees(math.atan2(downhill - com[1], float(d.subtree_com[b][2])))
    onto_slope(w, deg, TOE_X + TIP_SLOPE / 2.0)
    mon = w.terrain
    roll = 0.0
    for _ in range(round(TIP_HOLD / m.opt.timestep)):
        w.step(HAL)
        roll = max(roll, mon.sample()["robot.tilt"] - deg)
        mon.consume()
    return predicted, roll > TIP_ROLL


@pytest.fixture(scope="module")
def tip_sweep(tmp_path_factory):
    """sweep(robot, elevator): ramps from TIP_FROM up in TIP_STEP steps until the robot tips, as (the predicted
    tipping angle, the first angle it tipped at); each answer computed once."""
    done: dict = {}

    def sweep(robot: str, elevator: float | None = None) -> tuple[float, float]:
        if (robot, elevator) not in done:
            tmp = tmp_path_factory.mktemp(f"tip-{robot}-{elevator}")
            deg = TIP_FROM
            while deg < math.degrees(math.atan(TIP_FRICTION)):
                predicted, tipped = tips_on(tmp, robot, elevator, deg)
                if tipped:
                    assert deg > TIP_FROM, f"{robot} tipped on the first ramp, {deg} deg: start lower"
                    done[robot, elevator] = (predicted, deg)
                    break
                deg += TIP_STEP
            else:
                pytest.fail(f"{robot} never tipped up to {deg} deg")
        return done[robot, elevator]
    return sweep


@pytest.mark.parametrize("robot, elevator", [("kitbot", None), ("fastmode", 0.0)])
def test_tip_angle_on_a_slope_matches_geometry(tip_sweep, robot, elevator):
    """A rigid robot parked across a slope tips when its CoM passes over its downhill wheels: at
    atan(half track / CoM height), from the built model's subtree CoM resting on the carpet and its tread wheels'
    centers. (A wheel's outer edge is about 0.02 m further out, about 2 deg more: inside the tolerance.)"""
    predicted, tipped = tip_sweep(robot, elevator)
    assert tipped == pytest.approx(predicted, abs=TIP_TOLERANCE)


def test_raising_the_elevator_lowers_the_tip_angle(tip_sweep):
    """The fast-mode robot's carriage and arm (5.5 kg of 56 kg) raised 1 m lift its CoM about 0.1 m: the tipping angle
    falls from about 54 deg to about 42, as the geometry predicts for each."""
    low_predicted, low = tip_sweep("fastmode", 0.0)
    high_predicted, high = tip_sweep("fastmode", 1.0)
    assert high_predicted < low_predicted - 5.0
    assert high == pytest.approx(high_predicted, abs=TIP_TOLERANCE)
    assert high < low


# ---- The CHARGE STATION under a robot ------------------------------------------------------------------------------

LEVEL_DEG = 2.5              # 2023 manual 5.4: LEVEL is within about 2.5 deg of the carpet


HINGE_SPACING = 5.30 * IN     # m: GE-23300 sheet 6, the double hinge's axes apart (the hand calculation's, not the
                              # model's, so a model that lost its dead band fails)
DEAD_BAND = {"inside": 0.85, "beyond": 1.1}      # robot offsets, as a share of the hand-calculated dead band


@pytest.mark.parametrize("where", ["centered", "inside", "beyond", "end"])
def test_charge_station_tilt_follows_robot_position(where):
    """The kitbot parked sideways on the deck (its wheels can't roll down the tilt), at an offset along the tilt.

    The hand calculation: level, the deck rests on both hinge axes, spacing / 2 either side of its center, and its own
    weight holds it there with m_deck g spacing / 2 (about 63 N m) about either axis. A robot offset x toward one end
    turns it about the near axis with m_robot g (x - spacing / 2). So the deck stays level until
    x = spacing / 2 (1 + m_deck / m_robot), about 0.196 m for the 49.5 kg kitbot, and tips to its 15 deg stop beyond.
    Checked centered, at 0.85 and 1.1 of that, and on one end (its bumper at the deck's edge)."""
    w = World(range_cfg(range={"file": "2023-charge-station", "start": "approach"}), None)
    m, d, b = w.model, w.data, w.robot_body
    station = next(e for e in w.parts.range.elements if isinstance(e, Platform))
    settle(w, 0.5)
    mass = float(m.body_subtreemass[b])
    tips_beyond = HINGE_SPACING / 2.0 * (1.0 + station.mass / mass)
    half_span = max(abs(m.geom_pos[g][1]) + m.geom_size[g][1]          # half its width over the bumpers
                    for g in range(m.ngeom) if m.geom_bodyid[g] == b)
    end = station.length / 2.0 - half_span
    offset = {"centered": 0.0, "end": end}[where] if where in ("centered", "end") else DEAD_BAND[where] * tips_beyond
    assert offset <= end                                                # all of the robot on the deck
    tips = offset > tips_beyond
    q = w.free_qadr
    d.qpos[q] = station.center[0] + offset
    d.qpos[q + 1] = station.center[1]
    d.qpos[q + 2] += station.height + 0.003
    d.qpos[q + 3:q + 7] = [math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4)]     # heading 90 deg
    d.qvel[:] = 0.0
    mujoco.mj_forward(m, d)
    tilt = []
    for _ in range(round(4.0 / m.opt.timestep)):
        w.step(HAL)
        tilt.append(math.degrees(platform_tilt(m, d, station.name)))
    last = tilt[-1000:]                                  # the last second
    if not tips:
        assert max(abs(t) for t in last) < LEVEL_DEG
    else:
        assert all(abs(t - station.max_tilt_deg) < 0.5 for t in last)
    assert abs(d.subtree_com[b][0] - station.center[0]) < station.length / 2.0     # still on the deck
