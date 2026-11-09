"""The range's balls in the world: one state per ball body (the ledger), parking, flight forces, scoring, leaving the
field, and a log per ball that says what became of it.

Every ball body is in exactly one BallState. Only `field` balls are in play; every other ball is parked (PARK_Z
under its own (x, y), PARKED_BITS, at rest), and after_step writes that back after every physics step, so a ball
put in another state is out of play from the next step on.

Per-step work is vectorized over numpy index arrays (the 2026 range has 360 floor balls): a free joint's qpos is
(x, y, z, quaternion) and its qvel (linear velocity in the world, angular velocity in the body's own frame).

A disc range's pieces go through the same ledger (Range.piece is a Disc; see build.py). What differs is in one place
each:
- Rest height: a disc lies flat with its center at half its height (`rest_z`), a ball at its radius.
- Flight: a disc is airborne when its center is more than AIRBORNE above lying flat, or it touches nothing. Each
  physics step an airborne disc gets scrimmage/disc/aero.py's force and torque, the same function the standalone
  flight model (disc/flight.py) uses, in the air's density (`air.density`, as ball flight: the [shooter]'s, or
  DiscAir's sea-level default without one): the force into xfrc_applied[:3] and the body-frame torque, turned into
  the world frame (R tau_b), into xfrc_applied[3:6]. R comes from the body's qpos quaternion, not data.xmat, which after
  mj_step still holds the step's start: a disc spinning at 90 rad/s turns 5 degrees a step. Below aero.V_MIN the
  aerodynamics are zero, so a disc at rest is left alone.
- MuJoCo integrates those forces with the world's own integrator (implicitfast, 1 ms), holding them over the step,
  and computes the gyroscopic term w x I w itself; the standalone model takes RK4 steps of the same equations. They
  agree within a few millimeters over 5 m (tests/test_range_discs.py).
- Rolling resistance: none. A disc lying flat doesn't roll, and one rolling on its rim is rare enough to leave to
  MuJoCo's own rolling friction.

A field of several kinds (Range.kinds) has them all in the one ledger: each slot has its kind (`kind`, masks
`ball_form`, `disc_form` and `rolls`), its own bounding `radius` and rest height (`rest_z_of`). The per-step work
filters the in-play balls by form first, so a ball-only range does the same arithmetic as before (pinned by
tests/test_range_ledger_golden.py):
- Flight: drag and lift on balls only, the disc aerodynamics on discs only, and on a rigid piece touching nothing
  scrimmage/launcher/rigid_aero.py's drag (its bounding box's area facing the air) and spin damping, the same
  function flight6 flies it with (the bench's `[flight]`, `launcher aim`): the force and the world-frame torque into
  xfrc_applied, R from the qpos quaternion as for a disc. A rigid piece touching anything (lying on the carpet,
  leaning on a goal) feels no air.
- Rolling resistance: on every `rolls` slot on the carpet (its center below r + ON_CARPET): a ball, and a rigid tube,
  ring or cylinder lying on its side (standing or leaning, its center is too high), each with its own r (a tube's
  outer radius), torque C m g r and inertia about the contact (I about its axis + m r^2).
- Scoring: a goal counts only its kind (`counted_by`: Goal.kind, else the field's first ball or disc kind, else its
  first kind), so a coral through a ball's goal doesn't score. Leaving the field is the same for every kind.
`r` and `rest_z` are Range.piece's kind's (the shooter's and the ball path's).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from enum import StrEnum

import mujoco
import numpy as np

from ..disc import aero as disc_aero
from ..disc.inputs import DEFAULT_AERO, Disc, aero_set
from ..launcher import rigid_aero
from ..launcher.inputs import Air, Ball
from .build import BALL_BITS, PARK_Z, PARKED_BITS, RangeLayout, kind_piece, rest_pose
from .pieces import PieceKind
from .spec import Range
from .spec import _one_round_kind as one_round_kind

AIRBORNE = 0.001        # m above resting on the floor (center above rest_z) before the air acts on a ball
TRACK_S = 10.0          # s after its release that a ball's first hit and apex are tracked
AT_REST_SPEED = 0.02    # m/s: a ball slower than this is at rest
OUT_Z = -1.0            # m: a ball whose center is below this has gone through the floor
ON_CARPET = 0.005       # m above resting on the floor (center above r) within which a ball rolls on the carpet
ROLLING_SPEED = 5e-5    # m/s: a ball rolling slower than this feels no rolling resistance (at rest: it may sleep)
GRAVITY = 9.81
ROLLING_SHAPES = ("tube", "ring", "cylinder")    # rigid shapes that roll on their side, as a ball rolls
NAN = math.nan


class BallState(StrEnum):
    """A StrEnum, not (str, Enum): numpy compares an object array with a str scalar through str(), which for
    (str, Enum) is "BallState.FIELD", so `state == BallState.FIELD` would be wrong."""
    FIELD = "field"     # in play: on the floor or flying
    ROBOT = "robot"     # in the hopper, a stage or the launcher
    STUCK = "stuck"     # the launcher returned no_contact, no_wheel or jammed
    SCORED = "scored"
    OUT = "out"         # left the field
    SPARE = "spare"     # an unused slot (or a floor ball with floor balls off)


OUTCOME = {BallState.FIELD: "on_field", BallState.ROBOT: "held", BallState.STUCK: "stuck",
           BallState.SCORED: "scored", BallState.OUT: "out_of_field", BallState.SPARE: "spare"}


@dataclass
class BallLog:
    """What happened to one ball. Floats are NaN and the rest None until set (ball_log() gives None for both)."""
    id: int
    origin: str                                                 # "floor", "preload" or "station"
    fired_t: float = NAN
    robot_pose: tuple[float, float, float] | None = None       # x, y, heading_deg at the exit
    robot_velocity: tuple[float, float, float] | None = None   # vx, vy, omega deg/s at the exit
    hood_deg: float = NAN
    shooter_rps: float = NAN
    exit_speed: float = NAN
    spin_rpm: float = NAN
    exit_slip: float = NAN
    lane: int | None = None
    launcher_status: str | None = None
    outcome: str = "spare"      # held, on_field, in_flight, scored, out_of_field, stuck, spare
    goal: str | None = None
    scored_t: float = NAN
    first_hit: str | None = None    # the first geom (or body) it touched after its release
    apex_z: float = NAN
    nearest_goal: str | None = None     # the goal nearest its release (across the floor), to read apex_z against
    rim_height: float = NAN             # that goal's rim height
    last_pos: tuple[float, float, float] | None = None
    at_rest: bool | None = None


class Balls:
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, layout: RangeLayout, rng: Range, air: Air):
        self.model, self.data, self.layout, self.range, self.air = model, data, layout, rng, air
        m = model
        n = len(layout.ball_names)
        self.n = n
        self.disc = rng.disc                                    # None for a range without a disc kind
        self.kinds = rng.kinds
        self.shooter_kind = rng.piece_kind                      # Range.piece's kind: the shooter's and the ball path's
        piece = rng.piece
        if isinstance(piece, PieceKind):                        # no ball or disc kind: the default kind's sizes
            self.r = piece.radius
            self.rest_z = rest_pose(rng, self.shooter_kind)[0]
        else:
            self.r = piece.diameter / 2.0
            self.rest_z = self.disc.height / 2.0 if self.disc is not None else self.r     # center height at rest
        kinds = list(layout.kinds) if len(layout.kinds) == n else [self.shooter_kind] * n
        self.kind = np.array(kinds, dtype=object)               # each slot's kind's name
        forms = np.array([rng.kinds[k].form if k in rng.kinds else "ball" for k in kinds], dtype=object)
        self.ball_form, self.disc_form, self.rigid_form = forms == "ball", forms == "disc", forms == "rigid"
        self.of_shooter = self.kind == self.shooter_kind        # the slots the shooter and its ball path take
        self._slots(rng, layout)
        self.bodies = np.array([m.body(name).id for name in layout.ball_names], dtype=int)
        self._slot_geoms = [np.arange(m.body_geomadr[b], m.body_geomadr[b] + m.body_geomnum[b]) for b in self.bodies]
        self.geoms = np.array([g[0] for g in self._slot_geoms], dtype=int)      # each slot's first geom
        self._one_geom = all(len(g) == 1 for g in self._slot_geoms)
        jnt = m.body_jntadr[self.bodies]
        self._q = m.jnt_qposadr[jnt][:, None] + np.arange(7)    # (n, 7) qpos indices
        self._v = m.jnt_dofadr[jnt][:, None] + np.arange(6)     # (n, 6) qvel indices
        # add_range adds the balls last and in order, so their bodies, and their free joints' qpos and qvel, are
        # consecutive: views of those rows, which the per-step work reads and writes without index arrays
        b0, q0, v0 = (int(self.bodies[0]), int(self._q[0, 0]), int(self._v[0, 0])) if n else (0, 0, 0)
        assert (self.bodies == b0 + np.arange(n)).all() and (self._q[:, 0] == q0 + 7 * np.arange(n)).all() \
            and (self._v[:, 0] == v0 + 6 * np.arange(n)).all(), "the range's ball bodies aren't consecutive"
        self._qpos = data.qpos[q0:q0 + 7 * n].reshape(n, 7)
        self._qvel = data.qvel[v0:v0 + 6 * n].reshape(n, 6)
        self._xmat = data.xmat[b0:b0 + n]                       # (n, 9) each body's rotation, row-major
        self._xfrc = data.xfrc_applied[b0:b0 + n]
        self._park = np.zeros((n, 7))                           # each ball's parked qpos: under its own (x, y)
        self._park[:, :2] = m.qpos0[self._q[:, :2]]
        self._park[:, 2] = PARK_Z
        self._park[:, 3] = 1.0
        self._geom_ball = np.full(m.ngeom, -1, dtype=int)       # geom id -> ball index, -1 for other geoms
        for i, g in enumerate(self._slot_geoms):
            self._geom_ball[g] = i
        self._geom_names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
                            or mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, int(m.geom_bodyid[g])) or f"geom {g}"
                            for g in range(m.ngeom)]
        if not one_round_kind(rng.kinds):       # a piece is named by its kind and number ("coral 1"), not its body
            for i, g in enumerate(self._slot_geoms):
                for k in g.tolist():
                    self._geom_names[k] = self.piece_name(i)
        self.surfaces = [(g.name, g.scoring_surface()) for g in rng.goals]
        # goal name -> the slots it counts: those of its kind (Goal.kind, else the field's first ball or disc kind)
        first = next((k.name for k in rng.kinds.values() if k.form != "rigid"), next(iter(rng.kinds)))
        self.counted_by = {g.name: self.kind == (g.kind if g.kind is not None else first) for g in rng.goals}
        self._counted = [self.counted_by[name] for name, _ in self.surfaces]
        # each scoring surface's reach: no point of its polygon is further than this from its center
        self._reach = [float(np.max(np.linalg.norm(sf.polygon, axis=1))) for _, sf in self.surfaces]
        self._centers = [tuple(float(c) for c in sf.center) for _, sf in self.surfaces]
        self.state = np.full(n, BallState.SPARE, dtype=object)    # write it through set_state() only
        self.field = np.zeros(n, dtype=bool)                        # state == FIELD, kept by set_state()
        self._trees = m.body_treeid[self.bodies]                    # each ball's kinematic tree (for sleeping)
        self._forced = np.zeros(0, dtype=int)                       # balls given an applied force last step
        self.log: list[BallLog] = []
        self.goal_counts: dict[str, int] = {}
        self.fired = 0                                  # balls the launcher has launched
        self._released = np.full(n, -np.inf)            # sim time of each ball's last place()
        self._full_bits_at = np.full(n, np.inf)         # sim time a placed ball's bits become BALL_BITS
        self._next_due = math.inf                       # at most the earliest of those
        self._flight = [0.5 * air.density * (math.pi * r * r) for r in self._r_list]     # each ball's 1/2 rho A
        self.clear()

    def _slots(self, rng: Range, layout: RangeLayout) -> None:
        """Each slot's sizes, from its kind: its bounding radius, rest height and orientation (a floor piece's own
        turn), its flight's ball or disc, and its rolling resistance on the carpet."""
        n, turns = self.n, rng.floor_turns if len(rng.floor_turns) == len(rng.floor_balls) else None
        self.radius = np.zeros(n)                               # the bounding sphere's radius (a ball's own)
        self._rest = np.zeros(n)                                # its center's height set down on the carpet
        self._quat: list[list[float] | None] = [None] * n      # its orientation set down (None: unturned)
        self._r = np.zeros(n)                                   # a ball's radius, its flight's and its roll's
        self._disc_of: list = [None] * n                        # a disc's (Disc, AeroSet)
        self._air_of: list = [None] * n                         # a rigid piece's (half sizes, Cd, spin damping)
        self.rolls = np.zeros(n, dtype=bool)                    # rolls on the carpet: a ball, a tube on its side
        self._roll_torque, self._roll_inertia = np.zeros(n), np.zeros(n)
        self._numbers = np.zeros(n, dtype=int)                  # its number among its kind's pieces, from 1
        seen: dict[str, int] = {}
        for i, name in enumerate(self.kind.tolist()):
            seen[name] = seen.get(name, 0) + 1
            self._numbers[i] = seen[name]
            kind = rng.kinds.get(name)
            piece = kind_piece(rng, name)
            turn = turns[i] if turns is not None and i < layout.n_floor else None
            self._rest[i], self._quat[i] = rest_pose(rng, name, turn)
            if isinstance(piece, Ball):
                r = piece.diameter / 2.0
                self.radius[i] = self._r[i] = r
                self.rolls[i] = True
                self._roll_torque[i] = rng.rolling_resistance * piece.mass * GRAVITY * r     # N m against the roll
                self._roll_inertia[i] = (piece.inertia_factor + 1.0) * piece.mass * r ** 2   # about the contact point
            elif isinstance(piece, Disc):
                self.radius[i] = self._r[i] = piece.diameter / 2.0
                aero = self._aero_of(rng, name, kind)
                self._disc_of[i] = (piece, aero)
            else:
                self.radius[i] = kind.radius
                self._air_of[i] = (rigid_aero.half_extents(kind), kind.drag_coefficient, kind.spin_damping)
                if kind.shape in ROLLING_SHAPES:                # a tube, ring or cylinder rolls on its side
                    r = kind.reach(kind.orient(90.0, 0.0))      # its outer radius
                    a = np.asarray(kind.axis, dtype=float)
                    xx, yy, zz, xy, xz, yz = kind.inertia
                    i_axis = float(a @ np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]) @ a)
                    self._r[i] = r
                    self.rolls[i] = True
                    self._roll_torque[i] = rng.rolling_resistance * kind.mass * GRAVITY * r
                    self._roll_inertia[i] = i_axis + kind.mass * r ** 2
                else:
                    self._r[i] = kind.radius
        self._rolling = bool((self._roll_torque[self.rolls] > 0.0).any())
        self._any_ball, self._any_disc = bool(self.ball_form.any()), bool(self.disc_form.any())
        self._any_rigid = bool(self.rigid_form.any())
        # the per-step loops read these as Python floats
        self._r_list, self._torque_list = self._r.tolist(), self._roll_torque.tolist()
        self._inertia_list = self._roll_inertia.tolist()

    @staticmethod
    def _aero_of(rng: Range, name: str, kind):
        if name == rng.piece_kind and rng.aero is not None:
            return rng.aero
        return kind.aero if kind is not None and kind.aero is not None else aero_set(DEFAULT_AERO)

    def rest_z_of(self, i: int) -> float:
        """Piece i's center height set down on the carpet: a ball's radius, a disc's half height lying flat, a rigid
        piece's lowest point (as it lies: a floor piece's own turn, else its kind's default) plus FLOOR_DROP."""
        return float(self._rest[i])

    def clearance(self, i: int) -> float:
        """How far piece i's lowest point is above the carpet as it lies now: a ball's center less its radius, any
        other piece's less how far below its center its lowest point is, turned as it is."""
        z = float(self._qpos[i, 2])
        kind = self.kinds.get(str(self.kind[i]))
        if kind is None or kind.form == "ball":
            return z - float(self._r[i])
        return z - kind.lowest(self._qpos[i, 3:7])

    def rest_quat(self, i: int) -> list[float] | None:
        """Piece i's orientation set down on the carpet (w, x, y, z), None for a ball's or a flat disc's (unturned)."""
        return self._quat[i]

    def piece_name(self, i: int) -> str:
        """What a student reads for piece i: "ball i" on a field of one ball or disc kind (as always), else its kind's
        plain name and its number among that kind's pieces, from 1 ("coral 1")."""
        if one_round_kind(self.kinds):
            return f"ball {i}"
        name = str(self.kind[i])
        label = self.kinds[name].label if name in self.kinds else name
        return f"{label} {int(self._numbers[i])}"

    def clear(self) -> None:
        """Every ball spare and parked, its log empty, the counts zero."""
        self.state[:] = BallState.SPARE
        self.field[:] = False
        station = np.zeros(self.n, dtype=bool)
        for slots in self.layout.station_slots.values():
            station[slots.start:slots.stop] = True
        self.log = [BallLog(id=i, origin="floor" if i < self.layout.n_floor else "station" if station[i] else "preload")
                    for i in range(self.n)]
        self.goal_counts = {name: 0 for name, _ in self.surfaces}
        self.fired = 0
        self._released[:] = -np.inf
        self._full_bits_at[:] = np.inf
        self._next_due = math.inf
        for i in range(self.n):
            self.park(i)

    # ------------------------------------------------------------------------------------------------ one ball

    def set_state(self, i: int, state: BallState) -> None:
        """Ball i's state and its log's outcome (park it yourself if it leaves play)."""
        self.state[i] = state
        self.field[i] = state == BallState.FIELD
        self.log[i].outcome = OUTCOME[state]

    def _bits(self, i, bits: dict) -> None:
        """Collision bits of ball(s) i: its geoms', and its body's too (MuJoCo's broadphase filters bodies by the
        union of their geoms' bits, worked out when the model is compiled)."""
        m = self.model
        if self._one_geom:
            g = self.geoms[i]
        else:       # a rigid piece's geoms (its solids)
            g = np.concatenate([self._slot_geoms[j] for j in np.atleast_1d(i).tolist()] + [np.zeros(0, dtype=int)])
        m.geom_contype[g] = m.body_contype[self.bodies[i]] = bits["contype"]
        m.geom_conaffinity[g] = m.body_conaffinity[self.bodies[i]] = bits["conaffinity"]

    def positions(self) -> np.ndarray:
        """(n, 3) ball centers (a copy)."""
        return self._qpos[:, :3].copy()

    def position(self, i: int) -> np.ndarray:
        """Ball i's center (a view: read it, don't write it)."""
        return self._qpos[i, :3]

    def velocity(self, i: int) -> np.ndarray:
        """Ball i's velocity (a view: read it, don't write it)."""
        return self._qvel[i, :3]

    def ball_of_geom(self, geom) -> np.ndarray:
        """The ball index of each geom id in `geom` (any shape), -1 for a geom that isn't a ball."""
        return self._geom_ball[geom]

    def park(self, i: int) -> None:
        """Out of play: under its own (x, y) at PARK_Z, at rest, touching nothing. The state is the caller's."""
        d = self.data
        d.qpos[self._q[i]] = self._park[i]
        d.qvel[self._v[i]] = 0.0
        d.xfrc_applied[self.bodies[i]] = 0.0
        self._bits(i, PARKED_BITS)
        self._full_bits_at[i] = np.inf

    def put(self, i: int, pos, bits: dict, quat=None) -> None:
        """At rest at `pos`, in play, as a floor ball is at the start (not a release: no flight tracking), turned to
        `quat` (w, x, y, z; unturned when None)."""
        d = self.data
        q = [1.0, 0.0, 0.0, 0.0] if quat is None else list(np.asarray(quat, dtype=float) / np.linalg.norm(quat))
        d.qpos[self._q[i]] = [*pos, *q]
        d.qvel[self._v[i]] = 0.0
        self._bits(i, bits)
        self._full_bits_at[i] = np.inf
        self.set_state(i, BallState.FIELD)

    def place(self, i: int, pos, vel, ang_vel, bits: dict, full_bits_after: float | None = None,
              quat=None) -> None:
        """Release ball i at `pos` with velocity `vel` and angular velocity `ang_vel` (both in the world): it is in
        play (`field`), turned to `quat` (w, x, y, z; unturned when None, so its body frame is the world's), and its
        first hit and apex are tracked, read against the nearest goal's rim. With `full_bits_after` its `bits` give
        way to BALL_BITS that many seconds on (a launched ball clearing its robot)."""
        d = self.data
        if quat is None:
            d.qpos[self._q[i]] = [*pos, 1.0, 0.0, 0.0, 0.0]
            d.qvel[self._v[i]] = [*vel, *ang_vel]
        else:
            q = np.asarray(quat, dtype=float) / np.linalg.norm(quat)
            rot = np.zeros(9)
            mujoco.mju_quat2Mat(rot, q)
            d.qpos[self._q[i]] = [*pos, *q]
            d.qvel[self._v[i]] = [*vel, *(rot.reshape(3, 3).T @ np.asarray(ang_vel, dtype=float))]   # body frame
        self._bits(i, bits)
        self._full_bits_at[i] = np.inf if full_bits_after is None else d.time + full_bits_after
        self._next_due = min(self._next_due, float(self._full_bits_at[i]))
        self.set_state(i, BallState.FIELD)
        log = self.log[i]
        log.first_hit, log.apex_z, log.last_pos, log.at_rest = None, float(pos[2]), None, None
        goal = self.nearest_goal(pos)
        if goal is not None:
            log.nearest_goal, log.rim_height = goal.name, float(goal.rim_height)
        self._released[i] = d.time

    def nearest_goal(self, pos):
        """The range's goal whose opening center is nearest `pos` across the floor, or None on a range without goals."""
        goals = self.range.goals
        if not goals:
            return None
        return min(goals, key=lambda g: math.hypot(g.pos[0] - pos[0], g.pos[1] - pos[1]))

    # ------------------------------------------------------------------------------------------------ each step

    def _in_play(self) -> np.ndarray:
        return np.flatnonzero(self.field)

    def apply_flight_forces(self) -> None:
        """Drag and spin lift (launcher/flight.py's law) on every flying ball, the disc's and the rigid piece's own
        air on theirs (see the module docstring); every other ball's applied force zero. Only ball bodies' rows of
        xfrc_applied are touched: the rows this and apply_rolling_resistance wrote last step are zeroed (park()
        zeroes a ball's own), not all 376 (the cost of a range's step is its balls'). The few flying balls are worked
        out one by one in Python floats: numpy's per-call cost on arrays this small was most of the step's."""
        xfrc = self._xfrc
        if len(self._forced):
            xfrc[self._forced] = 0.0
            self._forced = self._forced[:0]
        f = self._in_play()
        if not len(f):
            return
        if self._any_disc:
            self._disc_forces(f[self.disc_form[f]])
        if self._any_rigid:
            self._rigid_forces(f[self.rigid_form[f]])
        if not self._any_ball:
            return
        f = f[self.ball_form[f]]                       # only balls feel drag and spin lift
        f = f[self._qpos[f, 2] > self._r[f] + AIRBORNE]
        if not len(f):
            return
        qvel, xmat, air = self._qvel, self._xmat, self.air
        cd, slope, lift_max, rs, flight = air.drag_coefficient, air.lift_slope, air.lift_max, self._r_list, self._flight
        done, forces = [], []
        for i in f.tolist():
            vx, vy, vz, a, b, c = qvel[i].tolist()     # velocity in the world, angular velocity in the body
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if not speed > 1e-9:
                continue
            half_rho_a = flight[i]
            drag, lift_r = -half_rho_a * cd, slope * rs[i]
            r0, r1, r2, r3, r4, r5, r6, r7, r8 = xmat[i].tolist()
            wx, wy, wz = r0 * a + r1 * b + r2 * c, r3 * a + r4 * b + r5 * c, r6 * a + r7 * b + r8 * c
            wn = math.sqrt(wx * wx + wy * wy + wz * wz)
            cl = min(lift_r * wn / speed, lift_max)
            hx, hy, hz = (wx / wn, wy / wn, wz / wn) if wn > 0.0 else (0.0, 0.0, 0.0)
            d, lift = drag * speed, half_rho_a * cl * speed        # |v|^2 (w_hat x v_hat) for the lift
            forces.append((d * vx + lift * (hy * vz - hz * vy), d * vy + lift * (hz * vx - hx * vz),
                           d * vz + lift * (hx * vy - hy * vx)))
            done.append(i)
        if done:
            xfrc[done, :3] = forces
            self._forced = np.union1d(self._forced, done) if len(self._forced) else np.array(done, dtype=int)

    def _disc_forces(self, f: np.ndarray) -> None:
        """aero.forces on every airborne disc of the in-play discs `f` (see the module docstring): the force and the
        world-frame torque into xfrc_applied. The rows written are zeroed next step (apply_flight_forces)."""
        if not len(f):
            return
        low = self._qpos[f, 2] <= self._rest[f] + AIRBORNE
        if low.any():       # low and touching something (the carpet, a goal, the robot): not flying
            d = self.data
            touching = np.zeros(self.n, dtype=bool)
            if d.ncon:
                ball = self._geom_ball[d.contact.geom[:d.ncon]].ravel()
                touching[ball[ball >= 0]] = True
            f = f[~(low & touching[f])]
        rho, rot = self.air.density, np.zeros(9)
        done, rows = [], []
        for i in f.tolist():
            vel = self._qvel[i, :3]
            if not float(vel @ vel) >= disc_aero.V_MIN * disc_aero.V_MIN:
                continue        # aero.forces is zero there
            disc, aero = self._disc_of[i]
            mujoco.mju_quat2Mat(rot, self._qpos[i, 3:7])
            r = rot.reshape(3, 3)
            force, torque = disc_aero.forces(disc, aero, rho, vel, r, self._qvel[i, 3:])
            rows.append(np.concatenate([force, r @ torque]))
            done.append(i)
        if done:
            self._xfrc[done] = rows
            self._forced = np.array(done, dtype=int)

    def _rigid_forces(self, f: np.ndarray) -> None:
        """rigid_aero.forces on every flying rigid piece of the in-play pieces `f` (see the module docstring): the
        force and the world-frame torque into xfrc_applied. The rows written are zeroed next step
        (apply_flight_forces)."""
        if not len(f):
            return
        d = self.data
        if d.ncon:          # touching anything (the carpet, a goal, the robot): not flying
            touching = np.zeros(self.n, dtype=bool)
            ball = self._geom_ball[d.contact.geom[:d.ncon]].ravel()
            touching[ball[ball >= 0]] = True
            f = f[~touching[f]]
        rho, rot = self.air.density, np.zeros(9)
        done, rows = [], []
        for i in f.tolist():
            h, cd, damping = self._air_of[i]
            vel, w_body = self._qvel[i, :3], self._qvel[i, 3:]
            mujoco.mju_quat2Mat(rot, self._qpos[i, 3:7])
            r = rot.reshape(3, 3)
            force, torque = rigid_aero.forces(h, cd, damping, rho, vel, r, r @ w_body)
            if not (force.any() or torque.any()):
                continue
            rows.append(np.concatenate([force, torque]))
            done.append(i)
        if done:
            self._xfrc[done] = rows
            self._forced = np.union1d(self._forced, done) if len(self._forced) else np.array(done, dtype=int)

    def apply_rolling_resistance(self) -> None:
        """The carpet's rolling resistance (Range.rolling_resistance, C): a torque C m g r against the roll of every
        in-play ball on the carpet rolling faster than ROLLING_SPEED, capped at what stops its roll in one step.
        Call after apply_flight_forces (which zeroes the balls' rows): it writes only the torque's x and y.

        MuJoCo's own rolling friction (condim 6, the carpet's 0.001 m) is a soft constraint that acts like a light
        viscous damper (a ball rolled at 0.5 m/s slows with a time constant of about 6 s), so on its own a ball rolls
        on for 10 s and more; this torque is the carpet's Coulomb rolling resistance. A ball at rest (rolling slower
        than ROLLING_SPEED, far above a resting ball's ~1e-9 m/s jitter and below MuJoCo's sleep tolerance) gets none,
        so it still falls asleep."""
        if not self._rolling:
            return
        f = self._in_play()
        if not len(f):
            return
        f = f[self.rolls[f]]
        # on the carpet and awake: a sleeping ball's velocity is zero (MuJoCo zeroes it), so it doesn't roll
        f = f[(self._qpos[f, 2] < self._r[f] + ON_CARPET) & (self.data.tree_asleep[self._trees[f]] < 0)]
        if not len(f):
            return
        qvel, xmat, rs = self._qvel, self._xmat, self._r_list
        torque, inertia, slow2 = self._torque_list, self._inertia_list, ROLLING_SPEED * ROLLING_SPEED
        done, torques = [], []
        for i in f.tolist():
            r = rs[i]
            a, b, c = qvel[i, 3:].tolist()             # |omega| is the same in any frame: drop the still balls first
            if not (a * a + b * b + c * c) * (r * r) > slow2:
                continue
            m0, m1, m2, m3, m4, m5, _, _, _ = xmat[i].tolist()
            wx, wy = m0 * a + m1 * b + m2 * c, m3 * a + m4 * b + m5 * c    # the roll: horizontal spin, world frame
            wn = math.sqrt(wx * wx + wy * wy)
            if not wn * r > ROLLING_SPEED:
                continue
            k = -(min(torque[i], inertia[i] * wn / self.model.opt.timestep) / wn)
            torques.append((k * wx, k * wy))
            done.append(i)
        if done:
            self._xfrc[done, 3:5] = torques
            self._forced = np.union1d(self._forced, done)

    def after_step(self, prev_pos: np.ndarray) -> list[tuple[int, str]]:
        """After mj_step (prev_pos: the (n, 3) centers before it): parked balls back in their places; released balls'
        first hit and apex; scoring (the ball parked, `scored`); balls off the field (parked, `out`). The step's
        (ball, "scored" | "out") events."""
        d = self.data
        if self._next_due <= d.time + 1e-12:
            due = np.flatnonzero(self._full_bits_at <= d.time + 1e-12)
            if len(due):
                self._full_bits_at[due] = np.inf
                due = due[self.field[due]]
                if len(due):
                    self._bits(due, BALL_BITS)
            self._next_due = float(self._full_bits_at.min())
        # A parked ball touches nothing, so it falls; put back each one that's awake. One asleep (MuJoCo's sleeping)
        # was put back at rest the step it fell asleep and hasn't moved since: writing it again changes nothing.
        parked = np.flatnonzero(~self.field & (d.tree_asleep[self._trees] < 0))
        if len(parked):
            self._qpos[parked] = self._park[parked]
            self._qvel[parked] = 0.0
            self._bits(parked, PARKED_BITS)
        f = self._in_play()
        if not len(f):
            return []
        now = self._qpos[f, :3]
        self._track(f, now)
        events: list[tuple[int, str]] = []
        # Only the awake balls can score or leave: a sleeping one hasn't moved since it was last checked. They are few
        # (balls in flight, and those the robot disturbs), so they are worked out one by one in Python floats.
        awake = d.tree_asleep[self._trees[f]] < 0
        f, now, prev = f[awake], now[awake], prev_pos[f[awake]]
        balls, rows, before = f.tolist(), now.tolist(), prev.tolist()
        steps = [math.sqrt((x - a) * (x - a) + (y - b) * (y - b) + (z - c) * (z - c))
                 for (x, y, z), (a, b, c) in zip(rows, before)]
        live = [True] * len(balls)
        for (name, surface), reach, (cx, cy, cz), counted in zip(self.surfaces, self._reach, self._centers,
                                                                 self._counted):
            # a crossing point lies within `reach` of the center and within `step` of `now`: test only those balls
            # (of the kind the goal counts)
            near = [k for k, ((x, y, z), step) in enumerate(zip(rows, steps)) if live[k] and step > 0.0
                    and counted[balls[k]]
                    and math.sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy) + (z - cz) * (z - cz))
                    <= reach + step + 1e-9]
            if not near:
                continue
            for k, hit in zip(near, surface.crossed(prev[near], now[near])):
                if not hit:
                    continue
                i = balls[k]
                self.set_state(i, BallState.SCORED)
                log = self.log[i]
                log.goal, log.scored_t, log.last_pos = name, float(d.time), tuple(rows[k])
                self.goal_counts[name] += 1
                self.park(i)
                events.append((i, "scored"))
                live[k] = False
        length, width = self.range.field_length, self.range.field_width
        for k, (x, y, z) in enumerate(rows):
            if live[k] and (x < 0.0 or x > length or y < 0.0 or y > width or z < OUT_Z):
                i = balls[k]
                self.set_state(i, BallState.OUT)
                self.log[i].last_pos = (x, y, z)
                self.park(i)
                events.append((i, "out"))
        return events

    def _track(self, f: np.ndarray, now: np.ndarray) -> None:
        """First hit and apex of the in-play balls `f` (centers `now`) released in the last TRACK_S."""
        d = self.data
        recent = d.time - self._released[f] <= TRACK_S
        if not recent.any():
            return
        for k in np.flatnonzero(recent):
            log = self.log[int(f[k])]
            if now[k, 2] > log.apex_z:
                log.apex_z = float(now[k, 2])
        want = np.zeros(self.n, dtype=bool)
        for i in f[recent]:
            want[i] = self.log[int(i)].first_hit is None
        if not want.any() or d.ncon == 0:
            return
        geom = d.contact.geom[:d.ncon]                  # (ncon, 2)
        ball = self._geom_ball[geom]                    # ball index per side, -1 if not a ball
        for side in (0, 1):
            b = ball[:, side]
            hits = np.flatnonzero((b >= 0) & want[np.maximum(b, 0)])
            for c in hits:
                i = int(b[c])
                if want[i]:
                    self.log[i].first_hit = self._geom_names[int(geom[c, 1 - side])]
                    want[i] = False

    # ------------------------------------------------------------------------------------------------ reports

    def at_rest(self) -> np.ndarray:
        """(n,) True for balls slower than AT_REST_SPEED."""
        return np.linalg.norm(self.data.qvel[self._v[:, :3]], axis=1) < AT_REST_SPEED

    def update_log(self) -> None:
        """Bring the in-play balls' logs up to date: last position, at rest, and in flight (released and has touched
        nothing yet) or on the field."""
        f = self._in_play()
        pos = self.positions()
        rest = self.at_rest()
        for i in f:
            log = self.log[int(i)]
            log.last_pos = tuple(float(c) for c in pos[i])
            log.at_rest = bool(rest[i])
            released = self._released[i] > -np.inf
            log.outcome = "in_flight" if released and log.first_hit is None and not rest[i] else "on_field"


# The per-step vector math on a few balls: numpy's norm(axis=1) and cross() spend most of their time on argument
# handling for arrays this small. These do the same arithmetic in the same order (the same bits).

def _norms(x: np.ndarray) -> np.ndarray:
    """np.linalg.norm(x, axis=1) of a real (k, 3) array."""
    return np.sqrt(np.add.reduce(x * x, axis=1))


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """np.cross(a, b) of (k, 3) arrays."""
    a0, a1, a2 = a[:, 0], a[:, 1], a[:, 2]
    b0, b1, b2 = b[:, 0], b[:, 1], b[:, 2]
    return np.stack((a1 * b2 - a2 * b1, a2 * b0 - a0 * b2, a0 * b1 - a1 * b0), axis=1)


def log_dict(log: BallLog) -> dict:
    """A BallLog as a dict with None for every unset (NaN) value, so it goes into JSON and compares with ==."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in dataclasses.asdict(log).items()}


def took_part(b: dict, start, r: float, height: float = 0.0) -> bool:
    """Did ball log `b` take part in the run: a preload, or a floor ball that was collected, fired, scored, stuck,
    left the field, is flying, or ended more than a radius `r` from where it started (`start`, its (x, y) on the
    carpet, or `height` up on what it was staged on)? Spares never do."""
    if b["outcome"] == "spare":
        return False
    if b["origin"] != "floor" or b["fired_t"] is not None or b["outcome"] != "on_field":
        return True
    p = b["last_pos"]
    return p is None or math.dist(p, (float(start[0]), float(start[1]), r + height)) > r
