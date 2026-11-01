"""Squishy contacts: the launcher model's ball laws (`launcher/contact.py`) on MuJoCo geometry.

Squishy pieces (balls) never collide through MuJoCo's own contacts (build.py gives them contype and conaffinity 0).
Each step `SquishyContacts.apply` finds where every ball in the bench touches a shape (`mj_geomDistance`, after a
bounding-sphere prefilter) or another ball, and applies the launcher model's forces, as the spike's hybrid did
(within 1% of the launcher model in the grip regime):

- Normal: the power-law Hunt-Crossley squeeze of one contact, `contact_force(x, x_rate)`. On a wheel tread the
  rubber is a spring in series (`wheel_stiffness` under a patch sqrt(D 2x) across, the durometer from the tread
  preset). Ball on ball: two balls in series, each taking half the approach at the same force.
- Friction: regularized Coulomb (`friction_force`) on the slip at the contact point; mu is the shape material's
  `foam_friction` (a tread preset is a material too).
- Rolling: Tabor's loss (`rolling_loss_torque`) against the ball's rolling relative to the surface, regularized by
  tanh(w / ROLL_SPIN_SCALE) so a ball at rest feels none.
- Both regularized laws are taken implicitly (backward Euler, per contact): the slip (or rolling spin) they act on
  is the one at the step's END, as their own force or torque leaves it, `_implicit`. A gripping contact is a very
  stiff damper (its time constant m mu_slip / (mu N) is tens of microseconds for a squeezed ball), which an explicit
  step of 1e-4 s would chatter across; taken implicitly it gives the force that holds the slip near zero, and a
  sliding contact (the slip far beyond FRICTION_SLIP_SPEED) still gets the full mu N. The response used is the
  ball's own (a force at the contact point moves its surface by 1/m + rho^2/I per newton-second, rho = r - x/2),
  plus the other body's when it moves: the other ball, or a shaft or springy bar through its one dof.
- Merging: contacts with one body whose normals are within MERGE_DEG of a deeper one are dropped, so a faceted
  curve, or a ball on the seam between two coplanar plates, is one contact and not several pushing at once.

The contact point is c - (r - x/2) n: the middle of the overlap along the normal n (from the surface to the ball's
center c), which for a ball pressed x into a flat face is the middle of its flattened patch.
"""

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from ..launcher import contact as C
from ..launcher.inputs import PRESETS, Wheels
from .build import PIECE_FRICTION, Built
from .spec import Bench, BenchError

MERGE_DEG = 30.0       # the spike's merge angle: contacts with one body closer than this in normal count once
ROLL_SPIN_SCALE = 0.1  # rad/s: rolling loss is regularized by tanh(w / ROLL_SPIN_SCALE), as the spike did
DIST_MAX = 1e-3       # m: mj_geomDistance stops looking past this; only overlaps (< 0) matter
_MERGE_COS = math.cos(math.radians(MERGE_DEG))


def _implicit(s: float, a: float, scale: float) -> float:
    """The slip at the end of a step, s' >= 0, for a regularized law F tanh(s' / scale) acting on a slip s >= 0
    with response k over a step dt, a = dt k F: s' + a tanh(s' / scale) = s (backward Euler). The left side is
    increasing and concave in s', so Newton's method from a point below the root climbs to it without passing it;
    both starts are below the root (tanh(y) <= y, and tanh <= 1)."""
    if a <= 0.0 or s <= 0.0:
        return s
    x = max(s - a, s / (1.0 + a / scale))
    for _ in range(50):
        t = math.tanh(x / scale)
        g = x + a * t - s
        step = g / (1.0 + a * (1.0 - t * t) / scale)
        x -= step
        if abs(step) <= 1e-12 * s:
            break
    return min(max(x, 0.0), s)


@dataclass
class Touch:
    """One contact applied this step: piece `piece` against shape `shape`, or against piece `other`."""

    piece: int
    shape: str | None
    other: int | None
    normal: float  # N
    point: np.ndarray


@dataclass(frozen=True)
class _Surface:
    shape: str
    body: int
    moving: bool           # the body has dofs (a shaft or a springy bar): it takes a reaction and can do work
    mu: float              # against foam
    wheels: Wheels | None  # a tread: its rubber is in series with the ball


class SquishyContacts:
    """Contact forces for a bench's squishy pieces.

    Contract with the caller, every step:
      1. zero `d.qfrc_applied` and `d.xfrc_applied`;
      2. write anything else (motor torques on shaft dofs, holding forces) into them, before or after `apply`;
      3. call `apply(d)` once;
      4. `mj_step`.
    `apply` only ever ADDS: the forces and torques on piece bodies into `xfrc_applied`, the reactions on moving
    shape bodies (shafts, springy bars) into `qfrc_applied` through `mj_applyFT` (a shaft's hinge gets the torque
    about its axis, a bar's slide the force along it). Static shapes take nothing. `apply` refreshes the positions
    and velocities it reads (`mj_kinematics`, `mj_comPos`, `mj_comVel`) itself, so it can follow `mj_step` directly.

    `work_on_piece[i]` accumulates, over every `apply`, the work (J) done on pool piece i by moving bodies, a force
    at a moving surface point and a rolling torque against a turning body: f . v_surface + T . w_body, times the
    model's timestep (so `apply` must be called once per step). Other pieces count as moving bodies. A still
    surface does no work; what the contact itself stores and loses is not in it. Zero it to start a new tally.

    `touches` lists this step's contacts (`Touch`), for per-piece loads and impulses.
    """

    def __init__(self, built: Built, bench: Bench):
        piece = bench.piece
        if piece.kind != "squishy" or piece.ball is None:
            raise BenchError(f"[piece] '{piece.name}' is {piece.kind}; squishy contacts are for balls only")
        self.m, self.lay, self.ball = built.model, built.layout, piece.ball
        m, lay = self.m, self.lay
        self.r = self.ball.diameter / 2
        self.dt = m.opt.timestep
        self.surface = self._surfaces(bench)
        self.geoms = np.array(list(self.surface), dtype=int)
        self.geom_rbound = m.geom_rbound[self.geoms] if len(self.geoms) else np.zeros(0)
        self.shape_names = list(dict.fromkeys(s.shape for s in self.surface.values()))
        self.bodies = np.array(lay.piece_bodies, dtype=int)
        self.piece_geoms = np.array(lay.piece_geoms, dtype=int)
        self.qvel = np.array(lay.piece_qvel, dtype=int)
        self.lo, self.hi = lay.bounds
        self.work_on_piece = np.zeros(len(self.bodies))
        # each piece's slip on a wheel at its latest wheel contact, as a fraction of the wheel's surface speed (the launcher
        # model's exit slip: 0 grips, 0.1 and more is grip-limited); NaN until it touches a wheel. Reset it at spawn.
        self.wheel_slip = np.full(len(self.bodies), np.nan)
        self.wheel_slip_t = np.full(len(self.bodies), -np.inf)   # when (d.time) that contact was
        self.touches: list[Touch] = []
        self.mass = float(bench.piece.mass)
        self.inertia = float(np.mean(bench.piece.inertia))  # a ball's three moments are equal
        self._jacp = np.zeros((3, m.nv))
        self._jacr = np.zeros((3, m.nv))
        self._fromto = np.zeros(6)
        self._vel6 = np.zeros(6)
        self._zero3 = np.zeros(3)

    def _surfaces(self, bench: Bench) -> dict[int, _Surface]:
        m, lay = self.m, self.lay
        material = {p.name: p.material for p in bench.plates}
        material |= {b.name: b.material for b in bench.bars}
        material |= {c.name: c.material for c in bench.curves}
        material |= lay.cad_material
        wheel = {w.name: w for w in bench.wheels}
        wheel |= lay.cad_wheels  # a part's wheels: diameter, width and tread as a [[wheel]]'s
        out = {}
        for g, shape in lay.shape_of_geom.items():
            body = int(m.geom_bodyid[g])
            moving = bool(m.body_weldid[body] != 0)
            if shape in wheel:
                w = wheel[shape]
                mu = bench.materials[w.tread].foam_friction
                tread = Wheels(diameter=w.diameter, width=w.width, durometer=PRESETS["wheels"][w.tread]["durometer"],
                               friction=mu)
                out[g] = _Surface(shape, body, moving, mu, tread)
            else:
                out[g] = _Surface(shape, body, moving, bench.materials[material[shape]].foam_friction, None)
        return out

    # ------------------------------------------------------------------------------------------- geometry

    def _active(self, d: mujoco.MjData) -> np.ndarray:
        """Indices of the pool pieces inside the bench's bounds (parked and escaped pieces are skipped)."""
        c = d.xpos[self.bodies]
        return np.flatnonzero(np.all((c >= self.lo) & (c <= self.hi), axis=1))

    def _shape_candidates(self, d: mujoco.MjData, active: np.ndarray) -> list[np.ndarray]:
        """Per active piece, the shape geoms whose bounding spheres overlap its own (vectorized prefilter)."""
        if not len(self.geoms):
            return [self.geoms] * len(active)
        c = d.geom_xpos[self.piece_geoms[active]]
        gp = d.geom_xpos[self.geoms]
        dist = np.linalg.norm(c[:, None, :] - gp[None, :, :], axis=2)
        near = dist < self.r + self.geom_rbound[None, :]
        return [self.geoms[row] for row in near]

    def _shape_contacts(self, d: mujoco.MjData, i: int, c: np.ndarray, geoms: np.ndarray) -> list[tuple]:
        """Piece i's contacts with shapes after merging: (x, n, geom) each, deepest first per body."""
        found: dict[int, list] = {}
        pg = int(self.piece_geoms[i])
        for g in geoms:
            dist = mujoco.mj_geomDistance(self.m, d, pg, int(g), DIST_MAX, self._fromto)
            if dist >= 0.0:
                continue
            arm = c - self._fromto[0:3]  # from the ball's deepest point back to its center
            length = np.linalg.norm(arm)
            if length < 1e-12:
                continue
            found.setdefault(self.surface[int(g)].body, []).append((-dist, arm / length, int(g)))
        out = []
        for lst in found.values():
            lst.sort(key=lambda t: -t[0])
            kept: list[tuple] = []
            for x, n, g in lst:
                if all(n @ kn < _MERGE_COS for _, kn, _ in kept):
                    kept.append((x, n, g))
            out += kept
        return out

    # ----------------------------------------------------------------------------------------------- laws

    def _forces(self, n, p, c, v, w, v_o, w_o, N, mu, roll_delta, other):
        """The force on the ball at p (normal N along n plus friction), its torque about c, and the rolling torque
        alone. v, w: the ball's; v_o: the other surface's velocity at p; w_o: the other body's spin. `other` is how
        the other body responds (see `_response`): None when it is still."""
        rel = v + np.cross(w, p - c) - v_o
        slip = -(rel - (rel @ n) * n)  # the other surface relative to the ball's surface
        sm = np.linalg.norm(slip)
        f = N * n
        rho2 = float((p - c) @ (p - c))
        if sm > 1e-12:
            u = slip / sm
            k = 1.0 / self.mass + rho2 / self.inertia + (0.0 if other is None else self._response(other, u, None))
            sm = _implicit(sm, self.dt * k * mu * N, C.FRICTION_SLIP_SPEED)
            f = f + u * C.friction_force(sm, N, mu)
        roll = self._zero3
        w_rel = w - w_o
        w_roll = w_rel - (w_rel @ n) * n
        wr = np.linalg.norm(w_roll)
        if wr > 1e-9:
            u = w_roll / wr
            M = C.rolling_loss_torque(N, self.ball, roll_delta)
            k = 1.0 / self.inertia + (0.0 if other is None else self._response(other, None, u))
            wr = _implicit(wr, self.dt * k * M, ROLL_SPIN_SCALE)
            roll = -u * M * math.tanh(wr / ROLL_SPIN_SCALE)
        return f, np.cross(p - c, f) + roll, roll

    def _slip(self, n, p, c, v, w, v_o) -> float:
        """The ball's slip on a wheel at p: the tangential speed of the wheel's surface relative to the ball's, as a
        fraction of the wheel surface's own tangential speed (NaN when the wheel barely moves there)."""
        rel = v + np.cross(w, p - c) - v_o
        surf = v_o - (v_o @ n) * n
        speed = float(np.linalg.norm(surf))
        if speed < 0.1:
            return math.nan
        return float(np.linalg.norm(rel - (rel @ n) * n)) / speed

    def _response(self, other, u_lin, u_rot) -> float:
        """How fast the other body's surface at the contact moves along u_lin (m/s per N s of force on it, opposite
        to the ball's), or its spin along u_rot (rad/s per N m s). `other` is ("ball", rho^2), another ball touched
        rho from its center, or ("body", jacp, jacr, inverse mass) for a shaft or bar's one dof."""
        if other[0] == "ball":
            return (1.0 / self.mass + other[1] / self.inertia) if u_lin is not None else 1.0 / self.inertia
        _, jacp, jacr, inv = other
        j = float(jacp @ u_lin) if u_lin is not None else float(jacr @ u_rot)
        return j * j * inv

    def _body_dof(self, d: mujoco.MjData, body: int, p: np.ndarray):
        """("body", jacp, jacr, 1 / M) for a moving shape's body at world point p: the point's motion per unit speed
        of the body's one dof (a shaft's hinge or a bar's slide, each hung from the world) and that dof's inverse
        inertia, armature included. A coupled shaft's partner is not counted (the response is overestimated, which
        is the safe side: the step never reverses the slip)."""
        mujoco.mj_jac(self.m, d, self._jacp, self._jacr, p, body)
        dof = int(self.m.body_dofadr[body])
        return ("body", self._jacp[:, dof].copy(), self._jacr[:, dof].copy(), 1.0 / float(self.m.dof_M0[dof]))

    def _approach_rate(self, n, p, c, v, w, v_o) -> float:
        return -((v + np.cross(w, p - c) - v_o) @ n)

    def _body_velocity(self, d: mujoco.MjData, body: int, p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """A body's linear velocity at world point p and its angular velocity."""
        mujoco.mj_objectVelocity(self.m, d, mujoco.mjtObj.mjOBJ_BODY, body, self._vel6, 0)
        w = self._vel6[0:3].copy()
        return self._vel6[3:6] + np.cross(w, p - d.xipos[body]), w

    # ---------------------------------------------------------------------------------------------- apply

    def apply(self, d: mujoco.MjData) -> dict[str, float]:
        """Add this step's squishy contact forces (see the class docstring for the contract). Returns the total
        normal force (N) on each shape this step, every shape named, 0.0 when untouched."""
        m, ball, r = self.m, self.ball, self.r
        mujoco.mj_kinematics(m, d)
        mujoco.mj_comPos(m, d)
        mujoco.mj_comVel(m, d)
        loads = dict.fromkeys(self.shape_names, 0.0)
        self.touches = []
        active = self._active(d)
        if not len(active):
            return loads
        c_all = d.geom_xpos[self.piece_geoms[active]].copy()
        v_all = np.array([d.qvel[self.qvel[i]:self.qvel[i] + 3] for i in active])
        w_all = np.array([d.xmat[self.bodies[i]].reshape(3, 3) @ d.qvel[self.qvel[i] + 3:self.qvel[i] + 6]
                          for i in active])
        F = np.zeros((len(active), 3))
        T = np.zeros((len(active), 3))

        for k, (i, geoms) in enumerate(zip(active, self._shape_candidates(d, active))):
            c, v, w = c_all[k], v_all[k], w_all[k]
            for x, n, g in self._shape_contacts(d, i, c, geoms):
                s = self.surface[g]
                p = c - (r - x / 2) * n
                v_o, w_o = self._body_velocity(d, s.body, p) if s.moving else (self._zero3, self._zero3)
                x_rate = self._approach_rate(n, p, c, v, w, v_o)
                k_w = None if s.wheels is None else C.wheel_stiffness(s.wheels, math.sqrt(ball.diameter * 2 * x))
                N = C.contact_force(x, x_rate, ball, k_w)
                if N <= 0.0:
                    continue
                other = self._body_dof(d, s.body, p) if s.moving else None
                f, t, roll = self._forces(n, p, c, v, w, v_o, w_o, N, s.mu, 2 * x, other)
                if s.wheels is not None:
                    self.wheel_slip[i] = self._slip(n, p, c, v, w, v_o)
                    self.wheel_slip_t[i] = d.time
                F[k] += f
                T[k] += t
                loads[s.shape] += N
                self.touches.append(Touch(int(i), s.shape, None, N, p))
                if s.moving:
                    mujoco.mj_applyFT(m, d, -f, -roll, p, s.body, d.qfrc_applied)
                    self.work_on_piece[i] += (f @ v_o + roll @ w_o) * self.dt

        self._piece_pairs(active, c_all, v_all, w_all, F, T)
        body = self.bodies[active]
        d.xfrc_applied[body, 0:3] += F
        # xfrc_applied's torque is about the body's center of mass; the forces above are about the ball's center
        d.xfrc_applied[body, 3:6] += T + np.cross(c_all - d.xipos[body], F)
        return loads

    def _piece_pairs(self, active, c_all, v_all, w_all, F, T) -> None:
        """Ball on ball: two of the same ball in series, each taking half the approach x at the same force, so the
        law is one rigid-surface contact at x / 2; the rolling loss acts on the balls' relative spin with each
        ball's patch (a squeeze of x across it). The surface at the contact moves with the other ball, so each
        does work on the other."""
        if len(active) < 2:
            return
        ball, r = self.ball, self.r
        dist = np.linalg.norm(c_all[:, None, :] - c_all[None, :, :], axis=2)
        for a, b in zip(*np.nonzero(np.triu(dist < 2 * r, k=1))):
            x = 2 * r - dist[a, b]
            if dist[a, b] < 1e-12:
                continue
            n = (c_all[a] - c_all[b]) / dist[a, b]  # onto a
            ca, cb = c_all[a], c_all[b]
            p = ca - (r - x / 2) * n
            v_b = v_all[b] + np.cross(w_all[b], p - cb)
            x_rate = self._approach_rate(n, p, ca, v_all[a], w_all[a], v_b)
            N = C.contact_force(x / 2, x_rate / 2, ball)
            if N <= 0.0:
                continue
            f, t, roll = self._forces(n, p, ca, v_all[a], w_all[a], v_b, w_all[b], N, PIECE_FRICTION, x,
                                      ("ball", float((p - cb) @ (p - cb))))
            F[a] += f
            T[a] += t
            F[b] -= f
            T[b] += np.cross(p - cb, -f) - roll
            ia, ib = int(active[a]), int(active[b])
            v_a = v_all[a] + np.cross(w_all[a], p - ca)
            self.work_on_piece[ia] += (f @ v_b + roll @ w_all[b]) * self.dt
            self.work_on_piece[ib] += (-f @ v_a - roll @ w_all[a]) * self.dt
            self.touches.append(Touch(ia, None, ib, N, p))
            self.touches.append(Touch(ib, None, ia, N, p))
