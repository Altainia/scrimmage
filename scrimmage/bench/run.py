"""Running a bench: shafts on one battery, pieces in through the inlets and out through the outlets, and what
happened to each piece, each shaft and each shape.

Time starts at 0 with every shaft at rest. Spawning starts at `t_ready`, when every speed-controlled shaft is within
SPIN_UP_TOLERANCE of its target (or at SPIN_UP_TIMEOUT if one never gets there), as the launcher model's
wait_for_speed feed does; the inlets' schedules are relative to it. `[run] duration` caps the whole run, spin-up
included. The run ends early once every piece is settled (out, jammed, escaped or never able to spawn) and every
speed-controlled shaft is back within the tolerance, so the last dip's recovery is measured.

Each step follows `SquishyContacts`' contract: zero both applied arrays, hold the parked pool, write the motor
torques, add the squishy contacts, `mj_step`. Rigid pieces collide through MuJoCo's own contacts, read back after
the step with `mj_contactForce`. Pool pieces not in the bench are put back where they are parked, still, before
every step, so they never drift into anything.

Pieces: each inlet sends its pieces in order on its own seeded schedule, upright, the center kept inside the zone
(`check_clear` vouches for nothing outside it). A spawn waits while another piece's bounding sphere overlaps the new
one's. After every step, a piece whose center is in an outlet box is `out`; outside `bounds`, `escaped`; one whose
center has stayed within `jam_distance` of a point for `jam_time` is `jammed`. Each is then parked. A piece still in
the bench at the end is `timed_out`; one that never spawned is `stuck_at_inlet`. The exit numbers are read when the
center enters the outlet.

Shafts: a shaft with a motor is driven, the launcher model's `ShaftModel` giving the motor torque a - b w at the
hinge's own speed (MuJoCo integrates the hinge) and `VelocityController` the voltage for a speed target. Every
driven shaft runs on the bus voltage of one shared battery (`motors.Battery`, the launcher's default 12.5 V and
0.02 ohm), sagging with the step's total supply current (one step of lag, as `ShaftModel` has). A coupled shaft has
no motor of its own (the file rules it out); it follows its driver through the model's equality constraint. A free
shaft gets no torque. A voltage shaft gets its fixed voltage, clamped to the bus; current limits apply to speed
control only, as they are the velocity controller's.

Energy: `EnergyCheck.work_in` is the work done on the piece by moving bodies (wheels, springy bars, other pieces): a
contact's force on the piece dotted with the other body's velocity at the contact point (squishy contacts tally it
themselves; rigid contacts use `mj_contactForce` with the velocities the step started from). A still shape does no
work. The check is `e_out <= e_in + work_in + potential_lost + max(1% of e_out, 5 mJ)`.

Loads are normal contact forces (friction is not in them), as `SquishyContacts.apply` reports them.

A flex NOTE (`[piece] flex = true`, spec D9) is a pool of flex bodies (bench/flex_note.py) in place of the rigid pool's
free bodies, run on MuJoCo's `discrete` integrator: FlexPieces reads and writes it for the runner as RigidPieces does
the rigid pool. Its center is its vertices' center of mass, its orientation their Kabsch rotation against its rest
shape, its spin from its angular momentum about the center (as the world's soft ring's swap back takes them), so its
exit, its poses and its flight in `[flight]` are a rigid NOTE's from that state. Contacts are read by contact id
(scrimmage/flexcontact.py): a flex's side is past the geoms. A flex takes no stiffness spreading (its stiffness is its
elasticity).
"""

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from .. import i18n
from ..launcher.inputs import BatteryIn, Control
from ..launcher.inputs import Shaft as LauncherShaft
from ..launcher.shaft import ShaftModel, VelocityController
from ..flexcontact import contact_ids, n_ids
from ..motors import Battery
from .build import Built, build_bench, check_clear
from .spec import Bench
from .squishy import SquishyContacts

SPIN_UP_TOLERANCE = 0.02  # spawning waits until every speed-controlled shaft is this close to its target...
SPIN_UP_TIMEOUT = 2.0     # ...or this long (s), whichever comes first
RECOVERY_TOLERANCE = 0.02  # a shaft has recovered when back within this fraction of the speed it had (launcher default)
SLIP_RECENT_S = 0.15  # a ball's wheel slip counts as its exit slip when it left the wheels this recently before leaving
BLOWUP_SPEED = 100.0      # m/s: a piece faster than this means the simulation has blown up
PASS_GAP = 0.005          # s: a piece that stops touching a shape for longer than this starts a new pass
ENERGY_REL, ENERGY_ABS = 0.01, 0.005  # the energy check's allowance: max(1% of e_out, 5 mJ)

STATUSES = ("out", "jammed", "escaped", "timed_out", "stuck_at_inlet")


class BenchRunError(RuntimeError):
    """The run blew up (NaN, a piece faster than BLOWUP_SPEED, or MuJoCo gave up on the accelerations)."""


@dataclass(frozen=True, kw_only=True)
class PieceOutcome:
    index: int  # the pool piece
    inlet: str
    status: str  # one of STATUSES
    outlet: str | None
    t_in: float | None  # spawned (None: never spawned)
    t_out: float | None  # left, jammed, escaped, or the run's end
    path: list[tuple[float, float, float, float]]  # (t, x, y, z) of the center, sampled at trace_hz
    speed: float | None  # out only: exit speed (m/s), direction and spin (rad/s, world frame)
    elevation_deg: float | None
    yaw_deg: float | None
    spin: tuple[float, float, float] | None
    peak_force: float  # the largest total normal contact force on the piece (N)
    tilt_deg: float | None = None  # out only: the angle of the piece's own z axis from up (is a car still upright?)
    # (t, qpos: x y z qw qx qy qz) where each pass against a shape began, then where the piece ended: for pictures,
    # which draw the piece's true-size outline there
    poses: list[tuple[float, tuple[float, ...]]] = field(default_factory=list)
    exit_slip: float | None = None  # out only, balls: the slip on the wheels at the last wheel contact, a fraction of
                                    # the wheel's surface speed (as the launcher model's exit slip); None: no wheel


@dataclass(frozen=True, kw_only=True)
class ShaftStats:
    name: str
    target_rps: float | None  # speed control only
    min_rps: float  # the lowest |speed| from t_ready on
    drop_pct: float  # the worst piece's speed drop (see _Run.shaft_stats); 0 when no piece touched the shaft
    recovery_s: float | None  # from that piece's lowest point back within RECOVERY_TOLERANCE of the speed the shaft had
                              # when it first touched; None: no drop, never, or not before the next piece touched
    peak_current: float  # the largest |stator current| (A, the shaft's motors together) from t_ready on; 0 if undriven
    energy_per_piece_j: float  # work the shaft's wheels did on pieces, per piece that touched them
    back_before_next: bool | None = None  # that piece's shaft was back to speed before the next piece touched it;
                                          # None: no later piece touched it


@dataclass(frozen=True, kw_only=True)
class LoadStats:
    shape: str
    peak_force_n: float  # the largest total normal force on the shape at one step
    peak_impulse_ns: float  # the largest normal impulse one piece gave it in one pass
    at: tuple[float, float, float] | None  # where the strongest contact was at the peak


@dataclass(frozen=True, kw_only=True)
class EnergyCheck:
    index: int
    e_in: float  # kinetic energy (linear and spin) at spawn
    work_in: float  # by moving bodies
    potential_lost: float
    e_out: float  # kinetic energy when it left, jammed, escaped, or at the run's end
    ok: bool


@dataclass(kw_only=True)
class BenchResult:
    pieces: list[PieceOutcome]
    shafts: dict[str, ShaftStats]
    loads: dict[str, LoadStats]
    energy: list[EnergyCheck]
    trace: list[tuple[float, np.ndarray]]  # (t, qpos) at trace_hz, for pictures
    built: Built
    bench: Bench
    t_ready: float
    t_end: float


# ----------------------------------------------------------------------------------------------------- pieces

@dataclass
class _Piece:
    index: int
    inlet: str
    due: float  # after t_ready
    pos: np.ndarray
    vel: np.ndarray
    spin: np.ndarray
    status: str | None = None  # None: waiting to spawn; "active"; then one of STATUSES
    outlet: str | None = None
    t_in: float | None = None
    t_out: float | None = None
    path: list = field(default_factory=list)
    anchor: np.ndarray | None = None
    anchor_t: float = 0.0
    peak_force: float = 0.0
    e_in: float = 0.0
    p_in: np.ndarray | None = None
    exit: tuple | None = None  # (speed, elevation, yaw, spin, tilt)
    exit_slip: float | None = None  # out only, balls: the wheel slip at the last wheel contact (PieceOutcome)
    energy: EnergyCheck | None = None
    poses: list = field(default_factory=list)


def _schedule(bench: Bench, built: Built) -> list[_Piece]:
    """Every pool piece's inlet, time, place and velocity, from each inlet's own seeded generator. Draws are taken
    in a fixed order per piece (time, position, speed) whatever the spreads, so a spread of 0 changes nothing else."""
    out: list[_Piece] = []
    for inlet in bench.inlets:
        rng = np.random.default_rng(inlet.seed)
        lo, hi = built.layout.inlet_boxes[inlet.name]
        v0 = np.array(inlet.velocity, dtype=float)
        speed = float(np.linalg.norm(v0))
        items = []
        for k in range(inlet.count):
            t = max(0.0, k / inlet.rate + rng.normal(0.0, inlet.spread_time))
            # spawn centers stay in the zone: check_clear vouches for nothing outside it
            pos = np.clip(np.array(inlet.center) + rng.normal(0.0, inlet.spread_position, 3), lo, hi)
            ds = rng.normal(0.0, inlet.spread_speed)  # m/s along the inlet's velocity
            v = v0 * max(0.0, speed + ds) / speed if speed > 0.0 else v0.copy()
            items.append((t, k, pos, v))
        items.sort(key=lambda it: (it[0], it[1]))
        for t, _, pos, v in items:
            out.append(_Piece(index=len(out), inlet=inlet.name, due=t, pos=pos, vel=v,
                              spin=np.array(inlet.spin, dtype=float)))
    return out


def _inside(p: np.ndarray, box: tuple[np.ndarray, np.ndarray]) -> bool:
    return bool(np.all(p >= box[0]) and np.all(p <= box[1]))


# ----------------------------------------------------------------------------------------------------- shafts

@dataclass
class _Drive:
    name: str
    dof: int
    model: ShaftModel
    ctrl: VelocityController | None  # speed control
    voltage: float | None  # voltage control
    stator: float = 0.0
    supply: float = 0.0


class _Shafts:
    """The driven shafts on one battery, and every shaft's speed record."""

    def __init__(self, bench: Bench, built: Built):
        b = BatteryIn()
        self.battery = Battery(nominal_voltage=b.nominal_voltage, resistance=b.resistance)
        self.bus = b.nominal_voltage
        self.names = list(bench.shafts)
        self.dofs = np.array([built.layout.shaft_dof[n] for n in self.names], dtype=int)
        self.targets = {n: s.target_rps for n, s in bench.shafts.items() if s.control == "speed"}
        self.drives: list[_Drive] = []
        for n, s in bench.shafts.items():
            if s.motor is None or s.control == "free":
                continue
            ls = LauncherShaft(wheel_inertia=s.wheel_inertia, added_inertia=s.added_inertia, motor=s.motor,
                               motor_count=s.count, gearing=s.gearing)
            model = ShaftModel(ls, None)
            model.set_bus_voltage(self.bus)
            ctrl = None
            if s.control == "speed":
                ctrl = VelocityController(Control(target_rps=s.target_rps, kS=s.kS, kV=s.kV, kP=s.kP,
                                                  stator_limit=s.stator_limit, supply_limit=s.supply_limit), ls)
            self.drives.append(_Drive(n, built.layout.shaft_dof[n], model, ctrl,
                                      s.voltage if s.control == "voltage" else None))

    def write_torques(self, d: mujoco.MjData) -> None:
        for dr in self.drives:
            w = float(d.qvel[dr.dof])
            dr.model.speed = w
            volts = dr.ctrl.voltage(dr.model) if dr.ctrl is not None else dr.voltage
            a, b = dr.model.torque_coefficients(volts)
            tau = a - b * w
            d.qfrc_applied[dr.dof] += tau
            m = dr.model.motor
            dr.stator = tau / (m.Kt * dr.model.gearing)
            v = max(-self.bus, min(self.bus, volts))
            dr.supply = dr.stator * (abs(v) / self.bus if self.bus > 0.0 else 0.0)

    def sag(self) -> None:
        """The bus for the next step, from this step's total supply current."""
        self.bus = self.battery.voltage(sum(dr.supply for dr in self.drives))
        for dr in self.drives:
            dr.model.set_bus_voltage(self.bus)

    def at_speed(self, d: mujoco.MjData) -> bool:
        return all(abs(d.qvel[self.dofs[self.names.index(n)]] / (2 * math.pi) - t) <= SPIN_UP_TOLERANCE * t
                   for n, t in self.targets.items())


# ------------------------------------------------------------------------------------------------------- run

def spread_stiffness(m: mujoco.MjModel, d: mujoco.MjData, geom_piece: np.ndarray, n_pieces: int, soft: np.ndarray,
                     tc1: np.ndarray) -> None:
    """Each soft geom's stiffness spread over the points it makes: a rigid piece is many solids, and one face
    against a wheel is 16 MuJoCo contact points on the 2024 NOTE, each of which would otherwise carry the whole
    stiffness (16 times the squeeze). tread_solref's time constant for n points is sqrt(n) times its one-point one
    (`tc1`, BenchLayout.contact_tc). n is d.contact's count, the last step's (this step's are not found yet): for a
    shape (whose solref a piece contact takes, by priority) its most with any one piece, for a piece's geoms their
    most with any one other piece (MuJoCo averages the two sides' solref there). A geom touching nothing goes back
    to one point. `geom_piece` is each contact id's pool piece (flexcontact: n_ids long), -1 for the rest."""
    n = np.ones(len(geom_piece))
    g = contact_ids(m, d)
    p = geom_piece[g]
    shape_rows = (p[:, 0] >= 0) != (p[:, 1] >= 0)
    if shape_rows.any():
        gp, pp = g[shape_rows], p[shape_rows]
        shape = np.where(pp[:, 0] >= 0, gp[:, 1], gp[:, 0])
        keys, counts = np.unique(np.stack([shape, pp.max(axis=1)], axis=1), axis=0, return_counts=True)
        np.maximum.at(n, keys[:, 0], counts)
    piece_rows = (p[:, 0] >= 0) & (p[:, 1] >= 0)
    if piece_rows.any():
        pairs, counts = np.unique(np.sort(p[piece_rows], axis=1), axis=0, return_counts=True)
        most = np.ones(n_pieces)
        np.maximum.at(most, pairs[:, 0], counts)
        np.maximum.at(most, pairs[:, 1], counts)
        in_piece = geom_piece >= 0
        n[in_piece] = most[geom_piece[in_piece]]
    m.geom_solref[soft, 0] = np.maximum(tc1 * np.sqrt(n[soft]), 2.0 * m.opt.timestep)


class RigidPieces:
    """The rigid pool's free bodies, read and written for the runner: each piece's addresses and where it waits."""

    def __init__(self, m: mujoco.MjModel, lay, n: int):
        self.m, self.lay = m, lay
        self.qadr = np.array([[q + k for k in range(7)] for q in lay.piece_qpos], dtype=int).reshape(n, 7)
        self.vadr = np.array([[v + k for k in range(6)] for v in lay.piece_qvel], dtype=int).reshape(n, 6)
        self.parked_q = np.array([np.r_[p, 1.0, 0.0, 0.0, 0.0] for p in lay.parked]).reshape(n, 7)

    def hold(self, d: mujoco.MjData, idx: np.ndarray) -> None:
        d.qpos[self.qadr[idx]] = self.parked_q[idx]
        d.qvel[self.vadr[idx]] = 0.0

    def state(self, d: mujoco.MjData, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        q, v = d.qpos[self.qadr[i]], d.qvel[self.vadr[i]]
        R = np.zeros(9)
        mujoco.mju_quat2Mat(R, q[3:7])
        R = R.reshape(3, 3)
        return q[:3].copy(), v[:3].copy(), R @ v[3:6], R

    def kinetic(self, d: mujoco.MjData, i: int) -> float:
        m, b = self.m, self.lay.piece_bodies[i]
        v = d.qvel[self.vadr[i]]
        Ri = np.zeros(9)
        mujoco.mju_quat2Mat(Ri, m.body_iquat[b])
        w = Ri.reshape(3, 3).T @ v[3:6]  # the body frame's spin in its principal axes
        return float(0.5 * m.body_mass[b] * (v[:3] @ v[:3]) + 0.5 * (m.body_inertia[b] @ (w * w)))

    def mass(self, i: int) -> float:
        return float(self.m.body_mass[self.lay.piece_bodies[i]])

    def spawn(self, d: mujoco.MjData, i: int, pos, vel, spin) -> None:
        d.qpos[self.qadr[i]] = np.r_[pos, 1.0, 0.0, 0.0, 0.0]
        d.qvel[self.vadr[i]] = np.r_[vel, spin]  # upright, so the body frame's spin is the world's

    def center(self, d: mujoco.MjData, i: int) -> np.ndarray:
        return d.qpos[self.qadr[i, :3]].copy()

    def speed(self, d: mujoco.MjData, i: int) -> float:
        return float(np.linalg.norm(d.qvel[self.vadr[i, :3]]))

    def pose(self, d: mujoco.MjData, i: int) -> tuple[float, ...]:
        """x y z qw qx qy qz."""
        return tuple(map(float, d.qpos[self.qadr[i]]))

    def forces_finite(self, d: mujoco.MjData, i: int) -> bool:
        return bool(np.isfinite(d.xfrc_applied[self.lay.piece_bodies[i]]).all()
                    and np.isfinite(d.qfrc_applied[self.vadr[i]]).all())


class FlexPieces:
    """The flex pool (`flex = true`): each piece's vertices, each a body on three slides at qpos 0 where it waits
    (BenchLayout.parked). Its center is the vertices' center of mass, its orientation the Kabsch rotation of their
    offsets against the rest shape's (the kind's frame: the rigid piece's, unturned), its spin the one its angular
    momentum about the center gives with the vertices' inertia there (a vertex has no spin of its own), all from
    qpos and qvel (after a step)."""

    def __init__(self, m: mujoco.MjModel, lay):
        self.m = m
        qadr, vadr, base, bodies = [], [], [], []
        for f in lay.flex_pieces:
            a, n = int(m.flex_vertadr[f]), int(m.flex_vertnum[f])
            b = m.flex_vertbodyid[a:a + n]
            jnt = m.body_jntadr[b][:, None] + np.arange(3)       # each vertex's x, y, z slides, in order
            qadr.append(m.jnt_qposadr[jnt])
            vadr.append(m.jnt_dofadr[jnt])
            base.append(m.body_pos[b].copy())                    # where each vertex is at qpos 0 (world children)
            bodies.append(b.copy())
        self.qadr, self.vadr = np.array(qadr, dtype=int), np.array(vadr, dtype=int)
        self.base, self.bodies = np.array(base), np.array(bodies, dtype=int)
        self.m_v = m.body_mass[self.bodies[0]].copy() if len(bodies) else np.zeros(0)
        self.total = float(self.m_v.sum())
        self.rest = (self.base[0] - self.m_v @ self.base[0] / self.total) if len(bodies) else np.zeros((0, 3))

    def hold(self, d: mujoco.MjData, idx: np.ndarray) -> None:
        d.qpos[self.qadr[idx]] = 0.0
        d.qvel[self.vadr[idx]] = 0.0

    def _x(self, d: mujoco.MjData, i: int) -> np.ndarray:
        return self.base[i] + d.qpos[self.qadr[i]]

    def state(self, d: mujoco.MjData, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x, v, mv = self._x(d, i), d.qvel[self.vadr[i]], self.m_v
        c, vc = mv @ x / self.total, mv @ v / self.total
        r, u = x - c, v - vc
        a, _, bt = np.linalg.svd(self.rest.T @ r)              # Kabsch: the rotation taking rest onto r
        s = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(bt.T @ a.T)))])
        R = bt.T @ s @ a.T
        L = mv @ np.cross(r, u)
        mr = mv[:, None] * r
        inertia = float((mr * r).sum()) * np.eye(3) - r.T @ mr
        return c, vc, np.linalg.solve(inertia, L), R

    def kinetic(self, d: mujoco.MjData, i: int) -> float:
        v = d.qvel[self.vadr[i]]
        return float(0.5 * self.m_v @ (v * v).sum(axis=1))

    def mass(self, i: int) -> float:
        return self.total

    def spawn(self, d: mujoco.MjData, i: int, pos, vel, spin) -> None:
        d.qpos[self.qadr[i]] = np.asarray(pos, dtype=float) + self.rest - self.base[i]
        d.qvel[self.vadr[i]] = np.asarray(vel, dtype=float) + np.cross(np.asarray(spin, dtype=float), self.rest)

    def center(self, d: mujoco.MjData, i: int) -> np.ndarray:
        return self.m_v @ self._x(d, i) / self.total

    def speed(self, d: mujoco.MjData, i: int) -> float:
        return float(np.linalg.norm(self.m_v @ d.qvel[self.vadr[i]] / self.total))

    def pose(self, d: mujoco.MjData, i: int) -> tuple[float, ...]:
        """x y z qw qx qy qz: the center and the Kabsch rotation, as a rigid piece's qpos."""
        c, _, _, R = self.state(d, i)
        q = np.zeros(4)
        mujoco.mju_mat2Quat(q, R.ravel())
        return tuple(map(float, np.r_[c, q]))

    def forces_finite(self, d: mujoco.MjData, i: int) -> bool:
        return bool(np.isfinite(d.xfrc_applied[self.bodies[i]]).all()
                    and np.isfinite(d.qfrc_applied[self.vadr[i]]).all())


class _Run:
    def __init__(self, bench: Bench, trace_hz: float):
        self.bench = bench
        self.built = build_bench(bench)
        check_clear(self.built)
        self.m, self.lay = self.built.model, self.built.layout
        m, lay = self.m, self.lay
        self.d = mujoco.MjData(m)
        self.dt = m.opt.timestep
        self.trace_dt = 1.0 / trace_hz
        self.pieces = _schedule(bench, self.built)
        self.squishy = SquishyContacts(self.built, bench) if bench.piece.kind == "squishy" else None
        self.shafts = _Shafts(bench, self.built)
        n = len(self.pieces)
        self.work = np.zeros(n)  # rigid pieces' work in (squishy pieces use SquishyContacts.work_on_piece)
        self.flex = bench.piece.flex
        self.pool = FlexPieces(m, lay) if self.flex else RigidPieces(m, lay, n)
        self.geom_piece = np.full(n_ids(m), -1, dtype=int)   # each contact id's pool piece (flexcontact)
        for i, geoms in enumerate(lay.piece_geom_sets):
            self.geom_piece[list(geoms)] = i
        for i, f in enumerate(lay.flex_pieces):
            self.geom_piece[m.ngeom + f] = i
        self.reach = lay.piece_reach
        self.soft = np.array(sorted(lay.contact_tc), dtype=int)
        self.soft_tc1 = np.array([lay.contact_tc[g] for g in self.soft])
        self.shaft_of_body = {int(m.body(f"shaft:{s}").id): s for s in bench.shafts}
        self.shaft_of_shape = {w.name: w.shaft for w in bench.wheels}
        self.shaft_of_shape |= {n: w.shaft for n, w in lay.cad_wheels.items()}
        self.gravity = m.opt.gravity.copy()
        self.shape_names = list(dict.fromkeys(lay.shape_of_geom.values()))
        self.peak = {s: (0.0, None) for s in self.shape_names}  # shape -> (force, point)
        self.passes: dict[tuple[int, str], list[float]] = {}  # (piece, shape) -> [impulse, last touch time]
        self.peak_impulse = dict.fromkeys(self.shape_names, 0.0)
        self.shaft_energy = dict.fromkeys(bench.shafts, 0.0)
        # shaft -> piece -> [first, last] time the piece touched one of the shaft's wheels
        self.shaft_touch: dict[str, dict[int, list[float]]] = {s: {} for s in bench.shafts}
        self.t_ready: float | None = None
        self.trace: list[tuple[float, np.ndarray]] = []
        self.next_trace = 0.0
        self.speed_t: list[float] = []
        self.speeds: list[np.ndarray] = []
        self.currents: list[np.ndarray] = []
        self.badqacc = 0
        # rigid_contacts' lookups, by contact id and by body: the shape (an index into shape_names, -1 for none) and
        # the shaft (an index into bench.shafts, -1 for none)
        self.id_shape = np.full(n_ids(m), -1, dtype=int)
        for g, s in lay.shape_of_geom.items():
            self.id_shape[g] = self.shape_names.index(s)
        self.shaft_names = list(bench.shafts)
        self.body_shaft = np.full(m.nbody, -1, dtype=int)
        for b, s in self.shaft_of_body.items():
            self.body_shaft[b] = self.shaft_names.index(s)
        self._held = np.ones(n, dtype=bool)

    # ---------------------------------------------------------------------------------------- piece state

    def state(self, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Piece i's center, velocity, world spin and orientation matrix (from qpos and qvel, after a step)."""
        return self.pool.state(self.d, i)

    def kinetic(self, i: int) -> float:
        return self.pool.kinetic(self.d, i)

    def work_in(self, i: int) -> float:
        return float(self.squishy.work_on_piece[i] if self.squishy is not None else self.work[i])

    def spawn(self, p: _Piece, t: float) -> None:
        i = p.index
        self.pool.spawn(self.d, i, p.pos, p.vel, p.spin)  # upright: the body frame's spin is the world's
        self._held[i] = False
        if self.squishy is not None:
            self.squishy.work_on_piece[i] = 0.0
            self.squishy.wheel_slip[i] = math.nan
            self.squishy.wheel_slip_t[i] = -math.inf
        self.work[i] = 0.0
        p.status, p.t_in, p.anchor, p.anchor_t = "active", t, p.pos.copy(), t
        p.e_in, p.p_in = self.kinetic(i), p.pos.copy()
        p.path.append((t, *map(float, p.pos)))

    def settle(self, p: _Piece, status: str, t: float, outlet: str | None = None) -> None:
        """Record piece p's end and park it."""
        i = p.index
        pos, vel, spin, R = self.state(i)
        if status == "out":
            speed = float(np.linalg.norm(vel))
            p.exit_slip = self.wheel_slip(i, t)   # now: the pool body may carry another piece later
            p.exit = (speed, math.degrees(math.atan2(vel[2], math.hypot(vel[0], vel[1]))),
                      math.degrees(math.atan2(vel[1], vel[0])), tuple(map(float, spin)),
                      math.degrees(math.acos(max(-1.0, min(1.0, R[2, 2])))))
        e_out = self.kinetic(i)
        lost = float(self.pool.mass(i) * (self.gravity @ (pos - p.p_in)))
        w_in = self.work_in(i)
        ok = e_out <= p.e_in + w_in + lost + max(ENERGY_REL * e_out, ENERGY_ABS)
        p.energy = EnergyCheck(index=i, e_in=p.e_in, work_in=w_in, potential_lost=lost, e_out=e_out, ok=bool(ok))
        p.status, p.outlet, p.t_out = status, outlet, t
        p.path.append((t, *map(float, pos)))
        p.poses.append((t, self.pool.pose(self.d, i)))
        self._held[i] = True

    def spawn_due(self, t: float) -> None:
        heads: dict[str, _Piece] = {}
        for p in self.pieces:  # the first waiting piece of each inlet; pieces leave an inlet in order
            if p.status is None and p.inlet not in heads:
                heads[p.inlet] = p
        if not heads:
            return
        active = [q for q in self.pieces if q.status == "active"]
        centers = np.array([self.pool.center(self.d, q.index) for q in active]).reshape(-1, 3)
        for p in heads.values():
            if t < self.t_ready + p.due - 1e-12:
                continue
            # the spot is taken while another piece's bounding sphere overlaps the new one's
            if len(centers) and np.min(np.linalg.norm(centers - p.pos, axis=1)) < 2 * self.reach:
                continue
            self.spawn(p, t)
            centers = np.vstack([centers, p.pos])

    # ------------------------------------------------------------------------------------------ contacts

    def touch(self, i: int, shape: str | None, normal: float, point: np.ndarray, t: float,
              forces: np.ndarray | None, shape_force: dict, shape_best: dict) -> None:
        """A piece pressing on a shape. `forces` None: its share of the piece's total is added already."""
        if forces is not None:
            forces[i] += normal
        if shape is None:
            return
        shape_force[shape] = shape_force.get(shape, 0.0) + normal
        if normal > shape_best.get(shape, (0.0, None))[0]:
            shape_best[shape] = (normal, point)
        key = (i, shape)
        run = self.passes.get(key)
        if run is None or t - run[1] > PASS_GAP:
            run = self.passes[key] = [0.0, t]
            self.pieces[i].poses.append((t, self.pool.pose(self.d, i)))
        run[0] += normal * self.dt
        run[1] = t
        self.peak_impulse[shape] = max(self.peak_impulse[shape], run[0])
        s = self.shaft_of_shape.get(shape)
        if s is not None:
            self.shaft_touch[s].setdefault(i, [t, t])[1] = t

    def rigid_contacts(self, t: float, forces, shape_force, shape_best) -> None:
        """Rigid and flex pieces after a step: d.contact, efc_force and cvel are all the step's start, so each
        contact's power is its force times the other body's velocity then. Sides are contact ids (flexcontact).
        Read as arrays: two flex NOTEs lying flat make about 900 contacts a step. Only contacts with a named shape go
        through touch() one at a time, in contact order."""
        m, d = self.m, self.d
        ids = contact_ids(m, d)
        pcs = self.geom_piece[ids]
        rows = np.flatnonzero((pcs >= 0).any(axis=1))
        if rows.size == 0:
            return
        f6 = self._contact_forces(rows)
        pushing = f6[:, 0] > 0.0
        rows, f6 = rows[pushing], f6[pushing]
        frame = d.contact.frame[rows].reshape(-1, 3, 3)
        f2 = np.einsum("kji,kj->ki", frame, f6)  # on side 2, from side 1 (the frame's normal points from 1 to 2)
        normal, p = f6[:, 0], d.contact.pos[rows]
        g, pc = ids[rows], pcs[rows]
        shaped = []
        for side in (0, 1):
            k = np.flatnonzero(pc[:, side] >= 0)
            i, other = pc[k, side], g[k, 1 - side]
            f = f2[k] if side == 1 else -f2[k]
            v_o = np.zeros((k.size, 3))
            geom = other < m.ngeom
            body = m.geom_bodyid[other[geom]]
            moving = m.body_weldid[body] != 0  # a moving body does work on the piece
            v = np.zeros((body.size, 3))
            if moving.any():
                v[moving] = self._point_velocity(body[moving], p[k[geom][moving]])
            v_o[geom] = v
            if not geom.all():  # another flex piece: its vertices' velocity at the contact
                v_o[~geom] = self._flex_velocity(rows[k[~geom]], 1 - side, other[~geom] - m.ngeom)
            power = np.einsum("ki,ki->k", f, v_o)
            np.add.at(self.work, i, power * self.dt)
            shaft = self.body_shaft[body]
            for s in np.unique(shaft[shaft >= 0]):
                self.shaft_energy[self.shaft_names[s]] += float(power[geom][shaft == s].sum()) * self.dt
            np.add.at(forces, i, normal[k])
            named = np.flatnonzero(self.id_shape[other] >= 0)
            shaped += [(int(k[j]), side, int(i[j]), int(self.id_shape[other[j]])) for j in named]
        for c, _, i, s in sorted(shaped):
            self.touch(i, self.shape_names[s], float(normal[c]), p[c].copy(), t, None, shape_force, shape_best)

    def _contact_forces(self, rows: np.ndarray) -> np.ndarray:
        """(len(rows), 3): contacts' forces in their own frames (normal, then the two tangents), mj_contactForce's
        first three. The bench's cone is elliptic, so these are efc_force's rows as they stand."""
        m, d = self.m, self.d
        out = np.zeros((rows.size, 3))
        if m.opt.cone != mujoco.mjtCone.mjCONE_ELLIPTIC:  # pragma: no cover - build.py sets an elliptic cone
            f6 = np.zeros(6)
            for k, c in enumerate(rows):
                mujoco.mj_contactForce(m, d, int(c), f6)
                out[k] = f6[:3]
            return out
        adr, dim = d.contact.efc_address[rows], d.contact.dim[rows]
        on = adr >= 0
        out[on, 0] = d.efc_force[adr[on]]
        full = on & (dim >= 3)
        out[full, 1] = d.efc_force[adr[full] + 1]
        out[full, 2] = d.efc_force[adr[full] + 2]
        return out

    def _point_velocity(self, bodies: np.ndarray, points: np.ndarray) -> np.ndarray:
        """Each body's velocity at its point (at the step's start, from cvel): mj_objectVelocity's, moved from the
        body's center of mass to the point."""
        m, d = self.m, self.d
        cv = d.cvel[bodies]
        lin = cv[:, 3:6] + np.cross(cv[:, 0:3], d.xipos[bodies] - d.subtree_com[m.body_rootid[bodies]])
        return lin + np.cross(cv[:, 0:3], points - d.xipos[bodies])

    def _flex_velocity(self, rows: np.ndarray, side: int, flexes: np.ndarray) -> np.ndarray:
        """(len(rows), 3): flex `flexes[k]`'s velocity at contact `rows[k]`'s `side` (at the step's start, from cvel):
        its vertex's there, else the mean of its element's vertices'."""
        m, d = self.m, self.d
        out = np.zeros((rows.size, 3))
        vert, elem = d.contact.vert[rows, side], d.contact.elem[rows, side]
        for f in np.unique(flexes):
            here = flexes == f
            a = int(m.flex_vertadr[f])
            one = here & (vert >= 0)
            if one.any():
                b = m.flex_vertbodyid[a + vert[one]]
                out[one] = self._point_velocity(b, d.xipos[b])
            many = here & (vert < 0) & (elem >= 0)
            if many.any():
                n = int(m.flex_dim[f]) + 1
                start = int(m.flex_elemdataadr[f]) + elem[many] * n
                b = m.flex_vertbodyid[a + m.flex_elem[start[:, None] + np.arange(n)]]   # (rows, n)
                out[many] = self._point_velocity(b.ravel(), d.xipos[b.ravel()]).reshape(-1, n, 3).mean(axis=1)
        return out

    def soften(self) -> None:
        if self.soft.size and not self.flex:   # a flex's stiffness is its elasticity: nothing to spread
            spread_stiffness(self.m, self.d, self.geom_piece, len(self.pieces), self.soft, self.soft_tc1)

    # ---------------------------------------------------------------------------------------------- loop

    def _blew_up(self, t: float, what: str) -> BenchRunError:
        return BenchRunError(i18n.t("the run blew up at {t_setting} s: {what}. A contact may be far too stiff for "
                                    "the timestep ({dt} s): check the piece's stiffness and the {run_table} timestep",
                                    what=what, dt=f"{self.dt:g}", t_setting="t = {}".format(f"{t:.4f}"),
                                    run_table="[run]"))

    def check_inputs(self, t: float) -> list[tuple[float, int]]:
        """Before a step: the forces about to be applied must be finite (a NaN contact force is named here, before
        MuJoCo sees it). Returns the active pieces' (speed, index), to name a piece if the step itself goes bad."""
        d = self.d
        speeds = [(self.pool.speed(d, p.index), p.index) for p in self.pieces if p.status == "active"]
        if not (np.isfinite(d.xfrc_applied).all() and np.isfinite(d.qfrc_applied).all()):
            for _, i in speeds:
                if not self.pool.forces_finite(d, i):
                    raise self._blew_up(t, i18n.t("piece {n} has a non-finite contact force", n=i))
            raise self._blew_up(t, i18n.t("a shaft or bar has a non-finite force (piece {n} was the fastest in the "
                                          "bench)", n=max(speeds)[1]) if speeds else
                                i18n.t("a shaft or bar has a non-finite force"))
        return speeds

    def check(self, t: float, before: list[tuple[float, int]]) -> None:
        """After the step that started at t. MuJoCo resets the whole state (time included) when the accelerations
        go bad, so a reset is caught by its warning count, and the piece is named from `before`, the step's start."""
        d = self.d
        bad = d.warning[mujoco.mjtWarning.mjWARN_BADQACC].number
        speeds = [(self.pool.speed(d, i), i) for _, i in before]
        nan = [i for s_, i in speeds if not math.isfinite(s_)]
        fast = [(s_, i) for s_, i in speeds if s_ > BLOWUP_SPEED]
        if bad > self.badqacc:
            what = (i18n.t("piece {n} (the fastest, at {speed} m/s) was in the bench when MuJoCo's accelerations "
                           "went bad", n=max(before)[1], speed=f"{max(before)[0]:.1f}") if before else
                    i18n.t("no piece was in the bench, but MuJoCo's accelerations went bad"))
        elif nan:
            what = i18n.t("piece {n} has a non-finite velocity", n=nan[0])
        elif fast:
            s_, i = max(fast)
            what = i18n.t("piece {n} is moving at {speed} m/s (over {most})", n=i, speed=f"{s_:.0f}",
                          most=f"{BLOWUP_SPEED:.0f}")
        elif not (np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all()):
            what = (i18n.t("piece {n} (the fastest) was in the bench when the state went non-finite",
                           n=max(before)[1])
                    if before else i18n.t("no piece was in the bench, but the model's state went non-finite"))
        else:
            return
        raise self._blew_up(t, what)

    def run(self) -> BenchResult:
        m, d, lay, run = self.m, self.d, self.lay, self.bench.run
        n_steps = int(math.floor(run.duration / self.dt + 1e-9))
        sh = self.shafts
        self.sample(0.0)
        for _ in range(n_steps):
            t = d.time
            if self.t_ready is None and (sh.at_speed(d) or t >= SPIN_UP_TIMEOUT - 1e-12):
                self.t_ready = t
            if self.t_ready is not None:
                self.spawn_due(t)
                if all(p.status not in (None, "active") for p in self.pieces) and self.recovered():
                    break
            held_idx = np.flatnonzero(self._held)
            if len(held_idx):
                self.pool.hold(d, held_idx)
            d.qfrc_applied[:] = 0.0
            d.xfrc_applied[:] = 0.0
            sh.write_torques(d)
            forces = np.zeros(len(self.pieces))
            shape_force: dict[str, float] = {}
            shape_best: dict[str, tuple] = {}
            if self.squishy is not None:
                before = d.qfrc_applied[sh.dofs].copy()
                self.squishy.apply(d)
                # what the pieces did to each shaft, against its speed: the work its wheels did on them
                reaction = d.qfrc_applied[sh.dofs] - before
                for k, name in enumerate(sh.names):
                    self.shaft_energy[name] -= float(reaction[k] * d.qvel[sh.dofs[k]]) * self.dt
                for tc in self.squishy.touches:
                    self.touch(tc.piece, tc.shape, tc.normal, tc.point, t, forces, shape_force, shape_best)
            self.soften()
            before = self.check_inputs(t)
            mujoco.mj_step(m, d)
            t1 = d.time
            if self.squishy is None:
                self.rigid_contacts(t, forces, shape_force, shape_best)
            sh.sag()
            self.check(t, before)
            self.badqacc = d.warning[mujoco.mjtWarning.mjWARN_BADQACC].number
            for s, f in shape_force.items():
                if f > self.peak[s][0]:
                    self.peak[s] = (f, shape_best[s][1])
            self.speed_t.append(t1)
            self.speeds.append(d.qvel[sh.dofs].copy())
            self.currents.append(np.array([abs(dr.stator) for dr in sh.drives]))
            self.follow(t1, forces)
            if t1 >= self.next_trace - 1e-12:
                self.sample(t1)
        t_end = d.time
        for p in self.pieces:
            if p.status == "active":
                self.settle(p, "timed_out", t_end)
            elif p.status is None:
                p.status = "stuck_at_inlet"
        if self.t_ready is None:
            self.t_ready = t_end
        return self.result(t_end)

    def recovered(self) -> bool:
        return all(abs(self.d.qvel[self.shafts.dofs[self.shafts.names.index(n)]]) / (2 * math.pi)
                   >= (1.0 - RECOVERY_TOLERANCE) * t for n, t in self.shafts.targets.items())

    def follow(self, t: float, forces: np.ndarray) -> None:
        """Outlets, escapes and jams for every piece in the bench, after a step."""
        lay, run = self.lay, self.bench.run
        sample = t >= self.next_trace - 1e-12
        for p in self.pieces:
            if p.status != "active":
                continue
            i = p.index
            p.peak_force = max(p.peak_force, float(forces[i]))
            c = self.pool.center(self.d, i)
            if sample:
                p.path.append((t, *map(float, c)))
            outlet = next((n for n, box in lay.outlet_boxes.items() if _inside(c, box)), None)
            if outlet is not None:
                self.settle(p, "out", t, outlet)
            elif not _inside(c, lay.bounds):
                self.settle(p, "escaped", t)
            elif np.linalg.norm(c - p.anchor) > run.jam_distance:
                p.anchor, p.anchor_t = c, t
            elif t - p.anchor_t >= run.jam_time - 1e-12:
                self.settle(p, "jammed", t)

    def sample(self, t: float) -> None:
        self.trace.append((t, self.d.qpos.copy()))
        self.next_trace += self.trace_dt

    # -------------------------------------------------------------------------------------------- results

    def result(self, t_end: float) -> BenchResult:
        pieces = []
        for p in self.pieces:
            ex = p.exit or (None, None, None, None, None)
            pieces.append(PieceOutcome(index=p.index, inlet=p.inlet, status=p.status, outlet=p.outlet, t_in=p.t_in,
                                       t_out=p.t_out, path=p.path, speed=ex[0], elevation_deg=ex[1], yaw_deg=ex[2],
                                       spin=ex[3], peak_force=p.peak_force, tilt_deg=ex[4],
                                       poses=p.poses, exit_slip=p.exit_slip))
        loads = {s: LoadStats(shape=s, peak_force_n=float(f), peak_impulse_ns=float(self.peak_impulse[s]),
                              at=None if at is None else tuple(map(float, at)))
                 for s, (f, at) in self.peak.items()}
        return BenchResult(pieces=pieces, shafts=self.shaft_stats(), loads=loads,
                           energy=[p.energy for p in self.pieces if p.energy is not None], trace=self.trace,
                           built=self.built, bench=self.bench, t_ready=self.t_ready, t_end=t_end)

    def wheel_slip(self, i: int, t: float) -> float | None:
        """Piece i's slip on the wheels as it left them, when it left them just before leaving the bench (within
        SLIP_RECENT_S): a ball that rolls on after the wheels (an intake into a chute) wasn't launched by them."""
        if self.squishy is None:
            return None
        s, when = float(self.squishy.wheel_slip[i]), float(self.squishy.wheel_slip_t[i])
        return None if math.isnan(s) or t - when > SLIP_RECENT_S else s

    def shaft_stats(self) -> dict[str, ShaftStats]:
        """Speed drop, as the launcher model measures it: per piece that touched the shaft's wheels, the speed when it
        first touched less the lowest speed until it last touched, as a percentage of the reference (the target, or
        for a shaft with none its speed at that first touch); the worst piece's is reported, and the recovery is from
        that piece's lowest point until the shaft is back within RECOVERY_TOLERANCE of the speed it had when that piece
        first touched. A speed loop settles a little under its target (P control), so measuring against the target
        could take until a stream of pieces had gone; a shaft not back before the next piece touches has no recovery
        time, and back_before_next says so."""
        sh = self.shafts
        t = np.array(self.speed_t)
        w = np.abs(np.array(self.speeds).reshape(len(t), len(sh.names))) / (2 * math.pi)
        cur = np.array(self.currents).reshape(len(t), len(sh.drives))
        start = min(int(np.searchsorted(t, self.t_ready - 1e-12)), max(len(t) - 1, 0))
        out = {}
        for k, name in enumerate(sh.names):
            target = sh.targets.get(name)
            drop, recovery, before_next, any_late = 0.0, None, None, False
            touches = sorted(self.shaft_touch[name].items(), key=lambda kv: kv[1][0])
            for n, (i, (t0, t1)) in enumerate(touches):
                a = int(np.searchsorted(t, t0 - 1e-12))
                b = max(a, min(int(np.searchsorted(t, t1 - 1e-12)), len(t) - 1))
                j = a + int(np.argmin(w[a:b + 1, k]))
                ref = target if target is not None else float(w[a, k])
                if ref <= 0.0:
                    continue
                level = min(float(w[a, k]), ref)              # back to where it was, not to a target it never held
                back = np.flatnonzero(w[j:, k] >= (1.0 - RECOVERY_TOLERANCE) * level)
                t_back = float(t[j + back[0]]) if len(back) else None
                t_next = touches[n + 1][1][0] if n + 1 < len(touches) else None
                late = t_next is not None and (t_back is None or t_back > t_next)
                any_late = any_late or late
                pct = float(100.0 * (w[a, k] - w[j, k]) / ref)
                if pct > drop:
                    drop = pct
                    before_next = None if t_next is None else not late
                    recovery = None if t_back is None or late else t_back - float(t[j])
            if any_late:   # any piece not back to speed before the next came: say so, even if the worst came later
                before_next, recovery = False, None
            low = float(np.min(w[start:, k])) if len(t) else 0.0
            di = next((n for n, dr in enumerate(sh.drives) if dr.name == name), None)
            peak = float(np.max(cur[start:, di])) if di is not None and len(t) else 0.0
            touched = len(self.shaft_touch[name])
            out[name] = ShaftStats(name=name, target_rps=target, min_rps=low, drop_pct=drop, recovery_s=recovery,
                                   back_before_next=before_next,
                                   peak_current=peak,
                                   energy_per_piece_j=self.shaft_energy[name] / touched if touched else 0.0)
        return out


def run_bench(bench: Bench, trace_hz: float = 60.0) -> BenchResult:
    """Run `bench` to its duration (or until every piece is settled and the shafts have recovered)."""
    if trace_hz <= 0.0:
        raise ValueError(f"trace_hz must be positive, not {trace_hz!r}")
    return _Run(bench, trace_hz).run()
