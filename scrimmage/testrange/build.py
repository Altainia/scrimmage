"""A range in the MuJoCo model: its goals and obstacles as static boxes, its balls (or discs) as free bodies, and the
config's [range] section.

Goals are thin boxes (FIELD_BITS, so the robot and the balls hit them) on one static body per goal, welded to the
world. Balls are `ball.<i>`: the range's floor balls first, in file order, then one parked slot per ball the robot
can hold. The ball count is fixed when the model is built (the live view drops frames whose body count changes), so
a ball out of play is parked: PARK_Z under its own (x, y), with PARKED_BITS (it touches nothing).

Ball contact: MuJoCo's soft contact is a damped spring, solref = (time constant T, damping ratio zeta), solimp =
(dmin, dmax, width, mid, power) its impedance d, which grows from dmin to dmax as the penetration grows to `width`.
The contact's reference acceleration is -b v - k d r, with b = 2 / (dmax T) and k = 1 / (dmax^2 T^2 zeta^2), and
MuJoCo moves a fraction d of the way to it; so the contact is a spring of natural frequency w = (d / dmax) / (T zeta)
with damping ratio zeta, whatever d.

- zeta gives the ball's restitution e. A two-way damped spring bounces with e = exp(-zeta pi / sqrt(1 - zeta^2)),
  but a contact only pushes, so the ball leaves when the spring force reaches zero, at its fastest, not when the
  spring is back at its length: e = exp(-zeta (pi - 2 asin zeta) / sqrt(1 - zeta^2)) (Schwager and Poeschel, "Coefficient
  of restitution and linear-dashpot model revisited", Granular Matter 9, 465-469, 2007). zeta solves that.
- The spring's frequency is set in seconds, so the contact doesn't depend on the timestep: it runs from W_SOFT (at
  dmin, a gentle contact of about 12 ms, the ball sinking about 1 cm at 4.4 m/s; real foam squashes more) up to
  W_STIFF (at dmax), d growing linearly over STIFFEN ball diameters of penetration, so a fast ball stiffens the
  contact before it can pass through a 2 cm plate. T = 1 / (zeta W_STIFF) (never below MuJoCo's 2 timesteps) and
  dmin = dmax W_SOFT T zeta. The stiffening is bounded by W_STIFF * timestep <= 1: past that the contact gains
  energy or blows up (MuJoCo's own T = 2 timesteps puts e = 0.6 at about 3100 rad/s). So a range needs a timestep of
  at most MAX_TIMESTEP, and a ball's restitution at most MAX_RESTITUTION (e = 1 is zeta = 0, an infinite T).
- What that guarantees at a 1 ms step, for the shipped ball presets and e from 0.3 to 0.95 (measured; checked by
  tests/test_range_model.py):
  - A drop from 1 m rebounds at 0.33, 0.64, 0.88 and 0.96 for e = 0.3, 0.6, 0.87 and 0.95: within 0.05 of e, and
    closer at smaller steps (0.29 and 0.60 at 0.5 ms).
  - A normal hit on a 2 cm plate at up to 50 m/s bounces back; it never passes through.
  - On a fast hit the rebound can run up to about 0.07 of the impact speed above e (e = 0.6 rebounding at 0.67 at
    40 m/s). That error comes from the step, not the model: a ball at 40 m/s moves 4 cm per step, so it is already
    up to 4 cm into the plate, the spring compressed, at the step that first sees the contact; how far depends on
    where in the step it arrives. It shrinks with the timestep.

Discs: a disc range's pieces are also `ball.<i>` bodies (the ledger is the same), each a cylinder geom of the disc's
radius and height with the disc's diagonal inertia (Ixx, Ixx, Izz), body z its axis; a disc at rest lies flat, its
center half its height above the carpet. Its contact is the ball's, with restitution DISC_RESTITUTION, and it
stiffens over STIFFEN of the disc's height rather than its diameter: a disc meets a plate flat or on its rim, and a
rim hit has only the rim's few centimeters to stop in.
- A goal with a rect opening and a `slot` is built as a frame (four bars `frame` wide around the opening, on the wall
  face), a box behind it (four walls just outside the opening's edges, `depth` deep) and a back wall closing the box.
- An [[obstacle]] is one static box, `<name>.box`, on its own body welded to the world.

MuJoCo mixes two geoms' solref and solimp by their solmix weights; the ball's is far above every other geom's, so
its values govern its contacts with the carpet, the walls, the goals and the robot. Friction and condim take the
larger of the two geoms' values: the ball's are the low ones, so the carpet's rolling friction stops a rolling ball.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from ..disc.inputs import Disc
from ..launcher.inputs import Ball
from ..parts.field import FIELD_BITS
from .spec import BUILTIN_RANGES, Goal, Obstacle, Range, RangeError, RectOpening, load_range

BALL_BITS = {"contype": 4, "conaffinity": 7}       # touches the field, the robot and other balls
LAUNCHED_BITS = {"contype": 4, "conaffinity": 5}   # touches the field and balls, not the robot
PARKED_BITS = {"contype": 0, "conaffinity": 0}     # touches nothing
PARK_Z = -20.0                                     # m: a parked ball's height, under its own (x, y)

PLATE = 0.02                    # m, every goal plate's thickness
BALL_SOLMIX = 1e6               # every other geom has MuJoCo's default solmix, 1
BALL_DMAX = 0.95                # MuJoCo's default dmax
W_SOFT = math.pi / 0.012        # rad/s: a gentle contact's frequency (a 12 ms half period)
W_STIFF = 1000.0                # rad/s: the contact's frequency at its stiffest
STIFFEN = 0.75                  # the penetration, in ball diameters, over which d grows linearly from dmin to dmax
MAX_TIMESTEP = 1.0 / W_STIFF    # s: a range's largest timestep (W_STIFF * timestep <= 1)
MAX_RESTITUTION = 0.95          # the contact model's largest ball restitution (checked to 40 m/s)
# A foam ball on polycarbonate (estimate): sliding, torsional, rolling. The carpet's are higher, and MuJoCo takes
# the larger of two geoms' frictions, so on the carpet the carpet's apply.
BALL_FRICTION = [0.5, 0.005, 0.0001]
FUNNEL_RGBA = [0.85, 0.85, 0.9, 0.45]
BODY_RGBA = [0.35, 0.36, 0.4, 1.0]
PLATE_FRICTION = [0.3, 0.005, 0.0001]   # as the field's walls
# estimate: a polyethylene disc on polycarbonate and aluminum; no measurement found. 0.4 is the middle of the
# 0.3-0.5 guessed for it, and the disc preset's rim_damping_ratio 0.25 bounces at 0.44 (scrimmage/disc/presets.toml).
DISC_RESTITUTION = 0.4
DISC_RGBA = [0.95, 0.95, 0.95, 1.0]
SLOT_PITCH = 0.2                # m between parked slots' (x, y), in rows of SLOT_ROW from the blue right corner
SLOT_ROW = 80

RANGE_KEYS = ("file", "start", "preload", "floor_balls")


@dataclass
class RangeLayout:
    ball_names: list[str]       # ball.0 ...: the floor balls (n_floor), then the robot's slots
    n_floor: int                # 0 when built with floor_balls off: the world has no floor ball bodies
    goal_bodies: list[str]      # one static body per goal, named as the goal
    # platform name -> the qpos addresses of its two hinges (build_elements.platform_joints); the tilt is their sum
    platforms: dict[str, tuple[int, int]] = field(default_factory=dict)
    # bar name -> its pipe's geom names (not its posts): what a robot hangs from
    bars: dict[str, list[str]] = field(default_factory=dict)
    # switch name -> the qpos address of its hinge (build_elements.switch_joint)
    switches: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, eq=False)
class RangeSection:
    """The config's [range], checked against the range and the robot."""
    range: Range
    start: str | None
    preload: int
    floor_balls: bool
    capacity: int               # balls the robot holds (hopper, stages and launcher); 0 without a [shooter]

    def start_pose(self) -> tuple[float, float, float] | None:
        return None if self.start is None else self.range.starts[self.start]


def damping_ratio(e: float) -> float:
    """The contact damping ratio whose push-only bounce has restitution e, for 0 < e < 1 (see the module
    docstring); a ValueError outside that."""
    if not 0.0 < e < 1.0:
        raise ValueError(f"a damping ratio needs a restitution above 0 and below 1, not {e!r}")

    def bounce(z):
        return math.exp(-z * (math.pi - 2.0 * math.asin(z)) / math.sqrt(1.0 - z * z))
    lo, hi = 0.0, 1.0 - 1e-9
    for _ in range(60):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if bounce(mid) > e else (lo, mid)
    return (lo + hi) / 2.0


def restitution(piece: Ball | Disc) -> float:
    """The piece's restitution in the world's contacts: a ball's own, a disc's DISC_RESTITUTION."""
    return DISC_RESTITUTION if isinstance(piece, Disc) else piece.restitution


def check_contact(ball: Ball | Disc, timestep: float) -> None:
    """A ValueError when the ball contact model doesn't hold for this ball (or disc) or timestep (see the module
    docstring)."""
    if timestep > MAX_TIMESTEP + 1e-12:
        raise ValueError(f"a range needs a timestep of at most {MAX_TIMESTEP:g} s, not {timestep:g}: its ball "
                         "contacts are stiff enough to stop a fast ball at a 2 cm plate, and a longer step lets them "
                         "gain energy or let the ball through")
    if not 0.0 < restitution(ball) <= MAX_RESTITUTION:
        raise ValueError(f"the ball's restitution {restitution(ball):g} is outside the range's contact model "
                         f"(above 0 and at most {MAX_RESTITUTION:g}): a bouncier ball gains energy on fast hits")


def ball_contact(ball: Ball | Disc, timestep: float) -> dict:
    """The ball (or disc) geom's MuJoCo contact keywords (see the module docstring); a ValueError from
    check_contact."""
    check_contact(ball, timestep)
    zeta = damping_ratio(restitution(ball))
    tc = max(2.0 * timestep, 1.0 / (zeta * W_STIFF))   # MuJoCo raises a time constant below 2 steps to 2 steps
    rho = W_SOFT * tc * zeta                          # d / dmax at the gentle frequency
    size = ball.height if isinstance(ball, Disc) else ball.diameter
    return {"solref": [tc, zeta], "solimp": [rho * BALL_DMAX, BALL_DMAX, STIFFEN * size, 0.5, 1.0],
            "solmix": BALL_SOLMIX, "condim": 6, "friction": list(BALL_FRICTION)}


def slot_xy(j: int) -> tuple[float, float]:
    """Slot j's parking (x, y): inside the field's outline, away from the floor balls."""
    return 0.25 + SLOT_PITCH * (j % SLOT_ROW), 0.25 + SLOT_PITCH * (j // SLOT_ROW)


def _quat(rot: np.ndarray) -> list[float]:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.ascontiguousarray(rot, dtype=float).ravel())
    return list(q)


def _rpy(rpy_deg) -> np.ndarray:
    """Roll about x, then pitch about y, then yaw about z (fixed axes)."""
    r, p, y = (math.radians(a) for a in rpy_deg)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _plates(goal: Goal) -> list[tuple[str, np.ndarray, np.ndarray, list[float], list[float]]]:
    """(name, local center, local rotation (columns: the box's axes), half sizes, rgba) in the goal's frame; the
    body's walls come back in the goal frame too, built level in the heading-only frame."""
    out = []
    if goal.slot is not None:
        out += _slot_plates(goal)
    v = goal.opening.vertices()
    n = 0 if goal.slot is not None else len(v)
    throat = goal.throat_across_flats()
    for k in range(n):
        a, b = v[k], v[(k + 1) % n]
        side = float(np.linalg.norm(b - a))
        t = np.array([*(b - a) / side, 0.0])
        mid = (a + b) / 2
        u = np.array([*mid / np.linalg.norm(mid), 0.0])          # the side's outward normal (a regular polygon)
        rim_mid = np.array([*mid, 0.0])
        if goal.funnel is not None:
            # the wall plane holds the rim side and the throat side; its inner face on that plane
            inset, depth = (goal.opening.across_flats - throat) / 2.0, goal.funnel.depth
            slant = math.hypot(inset, depth)
            inner = (-depth * u + inset * np.array([0.0, 0.0, 1.0])) / slant
            face = rim_mid + (-inset * u + np.array([0.0, 0.0, -depth])) / 2.0
            rot = np.column_stack([t, np.cross(inner, t), inner])
            out.append((f"funnel_{k}", face - inner * PLATE / 2, rot, [side / 2, slant / 2, PLATE / 2], FUNNEL_RGBA))
        else:
            # a square bar just outside the side, its top flush with the rim
            rot = np.column_stack([t, u, np.cross(t, u)])
            center = rim_mid + u * PLATE / 2 - np.array([0.0, 0.0, PLATE / 2])
            out.append((f"rim_{k}", center, rot, [side / 2, PLATE / 2, PLATE / 2], FUNNEL_RGBA))
    if goal.body is not None:
        # level from the carpet, whatever the tilt: undo the tilt so these come out in the heading-only frame
        level = goal.rotation().T @ _rpy((0.0, 0.0, goal.heading_deg))
        sx, sy = goal.body.size
        h = goal.body.height
        walls = [((sx / 2 - PLATE / 2, 0.0), [PLATE / 2, sy / 2, h / 2]),
                 ((-sx / 2 + PLATE / 2, 0.0), [PLATE / 2, sy / 2, h / 2]),
                 ((0.0, sy / 2 - PLATE / 2), [sx / 2 - PLATE, PLATE / 2, h / 2]),
                 ((0.0, -sy / 2 + PLATE / 2), [sx / 2 - PLATE, PLATE / 2, h / 2])]
        for k, ((x, y), half) in enumerate(walls):
            center = level @ np.array([x, y, h / 2 - goal.rim_height])
            out.append((f"body_{k}", center, level, half, BODY_RGBA))
    for p in goal.plates:
        out.append((p.name, np.array(p.pos, dtype=float), _rpy(p.rpy_deg), [PLATE / 2, p.size[0] / 2, p.size[1] / 2],
                    BODY_RGBA))
    return out


def _slot_plates(goal: Goal) -> list[tuple[str, np.ndarray, np.ndarray, list[float], list[float]]]:
    """A slot goal's frame, box and back wall, as _plates's tuples. In the goal's frame the opening lies in the x-y
    plane at z = 0, a scoring piece comes from +z, and the box runs back along -z."""
    o, s = goal.opening, goal.slot
    assert isinstance(o, RectOpening)
    hx, hy, f, t, depth = o.height / 2.0, o.width / 2.0, s.frame, PLATE, s.depth
    eye = np.eye(3)
    z_face = -t / 2.0               # the frame's front face on the opening's plane
    z_box = -depth / 2.0
    frame = [("frame_top", (hx + f / 2, 0.0), [f / 2, hy + f, t / 2]),
             ("frame_bottom", (-hx - f / 2, 0.0), [f / 2, hy + f, t / 2]),
             ("frame_left", (0.0, hy + f / 2), [hx, f / 2, t / 2]),
             ("frame_right", (0.0, -hy - f / 2), [hx, f / 2, t / 2])]
    box = [("box_top", (hx + t / 2, 0.0), [t / 2, hy + t, depth / 2]),
           ("box_bottom", (-hx - t / 2, 0.0), [t / 2, hy + t, depth / 2]),
           ("box_left", (0.0, hy + t / 2), [hx, t / 2, depth / 2]),
           ("box_right", (0.0, -hy - t / 2), [hx, t / 2, depth / 2])]
    out = [(name, np.array([x, y, z_face]), eye, half, BODY_RGBA) for name, (x, y), half in frame]
    out += [(name, np.array([x, y, z_box]), eye, half, FUNNEL_RGBA) for name, (x, y), half in box]
    out.append(("back", np.array([0.0, 0.0, -depth - t / 2]), eye, [hx + t, hy + t, t / 2], BODY_RGBA))
    return out


def add_obstacle(spec: mujoco.MjSpec, obstacle: Obstacle) -> None:
    body = spec.worldbody.add_body(name=obstacle.name)    # no joint: welded to the world
    body.add_geom(name=f"{obstacle.name}.box", type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(obstacle.pos),
                  quat=_quat(obstacle.rotation()), size=[c / 2.0 for c in obstacle.size], rgba=list(BODY_RGBA),
                  friction=list(PLATE_FRICTION), **FIELD_BITS)


def add_goal(spec: mujoco.MjSpec, goal: Goal) -> None:
    body = spec.worldbody.add_body(name=goal.name)    # no joint: welded to the world
    rot = goal.rotation()
    for name, center, local_rot, half, rgba in _plates(goal):
        body.add_geom(name=f"{goal.name}.{name}", type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(goal.to_world(center)),
                      quat=_quat(rot @ local_rot), size=list(half), rgba=list(rgba), friction=list(PLATE_FRICTION),
                      **FIELD_BITS)


def add_range(spec: mujoco.MjSpec, rng: Range, n_slots: int, *, floor_balls: bool = True) -> RangeLayout:
    """The range's goals, field elements and ball bodies into `spec`: the floor balls on the carpet (none when
    `floor_balls` is false: 360 parked balls would still cost each physics step about as much as the robot), then
    `n_slots` parked slots for the balls the robot holds. The layout's `platforms` is filled in once the model is
    compiled (assemble), and so are its `bars` and `switches`."""
    from .build_elements import add_element   # build_elements imports this module
    for goal in rng.goals:
        add_goal(spec, goal)
    for obstacle in rng.obstacles:
        add_obstacle(spec, obstacle)
    for e in rng.elements:     # before the balls, which must come last (see Balls)
        add_element(spec, e)
    ball = rng.piece
    r = ball.diameter / 2.0
    contact = ball_contact(ball, spec.option.timestep)
    if isinstance(ball, Disc):
        rest_z, inertia = ball.height / 2.0, [ball.ixx, ball.ixx, ball.izz]
        geom = {"type": mujoco.mjtGeom.mjGEOM_CYLINDER, "size": [r, ball.height / 2.0, 0], "rgba": DISC_RGBA}
    else:
        rest_z, inertia = r, [ball.inertia_factor * ball.mass * r * r] * 3
        geom = {"type": mujoco.mjtGeom.mjGEOM_SPHERE, "size": [r, 0, 0], "rgba": [0.95, 0.85, 0.1, 1.0]}
    places = [((x, y, rest_z), BALL_BITS) for x, y in rng.floor_balls] if floor_balls else []
    places += [((*slot_xy(j), PARK_Z), PARKED_BITS) for j in range(n_slots)]
    names = []
    for i, (pos, bits) in enumerate(places):
        name = f"ball.{i}"
        b = spec.worldbody.add_body(name=name, pos=[float(c) for c in pos])
        b.mass, b.ipos, b.inertia, b.explicitinertial = ball.mass, [0, 0, 0], list(inertia), True
        b.add_freejoint(name=name)
        b.add_geom(name=name, density=0, **geom, **contact, **bits)
        names.append(name)
    n_floor = len(rng.floor_balls) if floor_balls else 0
    return RangeLayout(ball_names=names, n_floor=n_floor, goal_bodies=[g.name for g in rng.goals])


def _capacity(cfg: dict) -> int | None:
    """[shooter] hopper capacity (the balls the whole robot holds; a disc shooter's default is DISC_CAPACITY), or None
    without a [shooter]."""
    from ..parts.model import PartError
    shooter = cfg.get("shooter")
    if shooter is None:
        return None
    from .ballpath import DISC_CAPACITY
    hopper = shooter.get("hopper", {}) if isinstance(shooter, dict) else None
    default = DISC_CAPACITY if isinstance(shooter, dict) and shooter.get("kind") == "disc" else 0
    cap = hopper.get("capacity", default) if isinstance(hopper, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise PartError(f"[shooter] hopper capacity must be a whole number of balls, not {cap!r}")
    return cap


def parse_range_section(cfg: dict, cfg_dir: Path | None) -> RangeSection | None:
    """The config's [range] (None without one), with every mistake a PartError naming the key."""
    from ..parts.model import PartError, model_timestep
    sec = cfg.get("range")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise PartError("[range] must be a table")
    for k in sec:
        if k not in RANGE_KEYS:
            raise PartError(f"[range]: unknown key {k!r}; allowed: {', '.join(RANGE_KEYS)}")
    ref = sec.get("file")
    if not isinstance(ref, str):
        raise PartError(f"[range] file must be a built-in range's name ({', '.join(BUILTIN_RANGES)}) or a path "
                        f"relative to the config, not {ref!r}")
    try:
        rng = load_range(ref, Path(cfg_dir) if cfg_dir is not None else Path("."))
    except RangeError as e:
        raise PartError(f"[range] file: {e}") from None
    try:
        check_contact(rng.piece, model_timestep(cfg, MAX_TIMESTEP))
    except ValueError as e:
        raise PartError(f"[range] ({rng.path.name}, ball {rng.piece_preset!r}): {e}") from None
    start = sec.get("start")
    if start is not None:
        if not isinstance(start, str) or start not in rng.starts:
            raise PartError(f"[range] start: {start!r} isn't a start of range {rng.name!r}; its starts: "
                            f"{', '.join(rng.starts) or 'none'}")
        if cfg.get("robot", {}).get("start_pose") is not None:
            raise PartError("[range] start and [robot] start_pose both place the robot: give one")
    preload = sec.get("preload", 0)
    if isinstance(preload, bool) or not isinstance(preload, int) or preload < 0:
        raise PartError(f"[range] preload must be a whole number of balls (0 or more), not {preload!r}")
    floor = sec.get("floor_balls", True)
    if not isinstance(floor, bool):
        raise PartError(f"[range] floor_balls must be true or false, not {floor!r}")
    capacity = _capacity(cfg)
    if preload > 0 and capacity is None:
        raise PartError(f"[range] preload = {preload}: the robot has no [shooter] to hold balls; set preload = 0")
    capacity = capacity or 0
    if preload > capacity:
        raise PartError(f"[range] preload = {preload} is more balls than the robot holds "
                        f"([shooter] hopper capacity = {capacity})")
    return RangeSection(range=rng, start=start, preload=preload, floor_balls=floor, capacity=capacity)
