"""The bench's flex NOTE (spec D9, R15): a ring piece as a tetrahedral flex torus, opt-in with `flex = true`.

The model (the soft-note spike's candidate (a), at its coarser 16 x 6 mesh):
- MESH[0] sections around the ring, each a center vertex and MESH[1] boundary vertices on a regular polygon whose
  flat top and bottom faces sit FLEX_RADIUS inside the tube, so with the flex's contact radius the piece is as thick
  as the kind (torus_tets). Each section-to-section prism splits into 3 tets by the smallest-global-index rule, so the
  tets fit together.
- Every vertex is a body with three slides (MuJoCo flexcomp's `dof="full"`), the kind's mass shared equally. The flex
  has Saint Venant-Kirchhoff elasticity (Young's modulus E, Poisson's ratio POISSON) and Rayleigh damping beta
  (add_flex_note). Flex elasticity needs MuJoCo's `discrete` integrator: a model with a flex NOTE runs `discrete` with
  Newton at FLEX_TIMESTEP.

Its E and beta are not the kind's numbers: they are fitted so the flex answers the kind's two numbers on the two
scenes of launcher/soft_scenes.py (the world's soft ring is checked on the same scenes):
- `stiffness`: the squeeze, each roller's force at a 25 % squeeze, is target_force(kind);
- `restitution`: the 1 m drop's rebound gives e = `restitution`.

fit_flex reads the shipped fit (flex_fits.json, the 2024-note preset's) first, then a cache file under SHAPED_CACHE
named by the two numbers, the mesh, the timestep and FIT_VERSION, and fits only when neither has it (R15). A first fit
for new numbers runs up to 9 squeezes (about 1.2 s each) and 7 drops (about 0.7 s each), so up to about 20 s.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n
from ..cad import fit as cad_fit
from ..flexcontact import contact_ids
from ..launcher import soft_scenes as sc
from ..testrange.build import RIGID_SOLMIX
from ..testrange.pieces import PieceKind, ring_basis, ring_diameters
from .spec import BenchError

MESH = (16, 6)  # source: soft-note spike: 16x6 pinches within 2 % of 24x8 once E is refit
FLEX_RADIUS = 0.0005  # source: spike
POISSON = 0.3  # source: spike
FLEX_TIMESTEP = 5e-4  # source: spec D9
FLEX_CONTACT = {"condim": 3, "solref": (0.002, 1.0), "solmix": RIGID_SOLMIX}  # source: spike
# Raised when the model or its scenes change beyond MESH and FLEX_TIMESTEP (the cache key and the shipped table carry
# those two themselves), so old cache files and the shipped fit go unused.
FIT_VERSION = 1  # choice: the first fit
SPIN_FRICTION = (0.005, 0.0001)  # source: spike: the flex's torsional and rolling friction (build.BALL_FRICTION's)
VERTEX_INERTIA_BOX = 0.005  # source: MuJoCo flexcomp's default `inertiabox` (m): each vertex body's inertia box

FIT_E0 = 12235.0  # source: soft-note spike (extra_mesh.py): the 16x6 mesh's E for the preset's squeeze, Pa
FIT_FORCE_TOL = 0.02  # source: spec D9 / R15: the squeeze within 2 %
FIT_E_STEPS = 8  # source: spike (calibrate.fit): its secant's default step limit
FIT_MIN_SLOPE = 0.2  # source: spike (calibrate.fit): a secant slope below this is taken as 1 (force ~ E)
FIT_BETA = (2.5e-4, 2.5e-2)  # source: R15: the damping's bracket, s (the spike's fits were 0.0014 to 0.0025)
FIT_E_TOL = 0.02  # source: R15: the drop's e within 0.02
FIT_BETA_STEPS = 7  # source: R15

SHIPPED_FITS = Path(__file__).with_name("flex_fits.json")
CACHE_DIR = "flex-note"  # under cad.fit.SHAPED_CACHE

ROLL_Q = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)  # a cylinder's axis turned onto -y

PRISM_I = [[0, 1, 2, 3, 4, 5], [1, 2, 0, 4, 5, 3], [2, 0, 1, 5, 3, 4],
           [3, 5, 4, 0, 2, 1], [4, 3, 5, 1, 0, 2], [5, 4, 3, 2, 1, 0]]


@dataclass(frozen=True)
class FlexFit:
    E: float      # Young's modulus, Pa
    beta: float   # Rayleigh damping, s


def _split_prism(v):          # v: bottom v0 v1 v2, top v3 v4 v5 over them (Dompierre et al. 1999)
    p = [v[i] for i in PRISM_I[int(np.argmin(v))]]
    if min(p[1], p[5]) < min(p[2], p[4]):
        return [[p[0], p[1], p[2], p[5]], [p[0], p[1], p[5], p[4]], [p[0], p[4], p[5], p[3]]]
    return [[p[0], p[1], p[2], p[4]], [p[0], p[4], p[2], p[5]], [p[0], p[4], p[5], p[3]]]


def torus_tets(n_phi: int, n_sec: int, radius: float, r_major: float, r_minor: float) -> tuple[np.ndarray, np.ndarray]:
    """The torus's vertices (its center at the origin, its axis z) and its tets (4 vertex indices each, every one of
    positive volume). Section i is vertex i (n_sec + 1) on the center line, then n_sec vertices on a polygon of
    circumradius (r_minor - radius) / cos(pi / n_sec), a face centered at the top (and, n_sec even, the bottom)."""
    rp = (r_minor - radius) / math.cos(math.pi / n_sec)
    ez = np.array([0.0, 0.0, 1.0])
    verts = []
    for i in range(n_phi):
        ph = 2.0 * math.pi * i / n_phi
        er = np.array([math.cos(ph), math.sin(ph), 0.0])
        verts.append(r_major * er)
        for j in range(n_sec):
            a = math.pi / 2.0 + math.pi / n_sec + 2.0 * math.pi * j / n_sec
            verts.append((r_major + rp * math.cos(a)) * er + rp * math.sin(a) * ez)
    verts = np.array(verts)
    per = n_sec + 1
    tets = []
    for i in range(n_phi):
        i2 = (i + 1) % n_phi
        for j in range(n_sec):
            j2 = (j + 1) % n_sec
            for t in _split_prism([i * per, i * per + 1 + j, i * per + 1 + j2,
                                   i2 * per, i2 * per + 1 + j, i2 * per + 1 + j2]):
                a, b, c, d = (verts[x] for x in t)
                if np.dot(np.cross(b - a, c - a), d - a) < 0:
                    t = [t[0], t[2], t[1], t[3]]
                tets.append(t)
    return verts, np.array(tets, dtype=int)


def tet_volumes(verts: np.ndarray, tets: np.ndarray) -> np.ndarray:
    a, b, c, d = (verts[tets[:, k]] for k in range(4))
    return np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) / 6.0


def ring_radii(kind: PieceKind) -> tuple[float, float]:
    """The torus's center-line radius and tube radius from a ring kind's diameters."""
    outer, inner = ring_diameters(kind)
    if inner is None:
        raise BenchError(i18n.t("piece {name}: a flex piece must be a ring with a hole", name=repr(kind.name)),
                         code="flex_needs_ring")
    return (outer + inner) / 4.0, (outer - inner) / 4.0


def thickness(kind: PieceKind) -> float:
    """The ring's thickness across its tube (2 in for the preset)."""
    return 2.0 * ring_radii(kind)[1]


def add_flex_note(spec: mujoco.MjSpec, name: str, pos, kind: PieceKind, fit: FlexFit) -> str:
    """The kind as a flex torus in `spec`, its center at `pos`, unturned (its axis the kind's axis), at rest: one body
    `<name>.v<i>` per vertex with three slides, and the flex `name`, which is returned. The same model as MuJoCo's
    `<flexcomp type="direct" dim="3" dof="full">` with these options (the spike's)."""
    big_r, r = ring_radii(kind)
    verts, tets = torus_tets(MESH[0], MESH[1], FLEX_RADIUS, big_r, r)
    world = np.asarray(pos, dtype=float) + verts @ ring_basis(kind.axis).T
    m = kind.mass / len(verts)
    inertia = m * 2.0 * VERTEX_INERTIA_BOX ** 2 / 3.0      # a box of half-side VERTEX_INERTIA_BOX, as flexcomp's
    bodies = []
    for i, v in enumerate(world):
        b = spec.worldbody.add_body(name=f"{name}.v{i}", pos=[float(c) for c in v])
        b.mass, b.ipos, b.inertia, b.explicitinertial = m, [0.0, 0.0, 0.0], [inertia] * 3, True
        for k, ax in enumerate(np.eye(3)):
            b.add_joint(name=f"{name}.v{i}_{'xyz'[k]}", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=list(ax))
        bodies.append(b.name)
    f = spec.add_flex()
    f.name = name
    f.dim = 3
    f.radius = FLEX_RADIUS
    f.vertbody = bodies
    f.vert = [0.0] * (3 * len(bodies))
    f.elem = [int(x) for x in tets.ravel()]
    f.condim = FLEX_CONTACT["condim"]
    f.solref = list(FLEX_CONTACT["solref"])
    f.solmix = FLEX_CONTACT["solmix"]
    f.friction = [kind.friction, *SPIN_FRICTION]
    f.selfcollide = mujoco.mjtFlexSelf.mjFLEXSELF_NONE
    f.young, f.poisson, f.damping = fit.E, POISSON, fit.beta
    return name


# --- the two scenes, on `discrete` with Newton at FLEX_TIMESTEP ---------------------------------------------------

def _spec(gravity: bool, timestep: float) -> mujoco.MjSpec:
    spec = mujoco.MjSpec()
    o = spec.option
    o.timestep, o.integrator = timestep, mujoco.mjtIntegrator.mjINT_DISCRETE
    o.solver = mujoco.mjtSolver.mjSOL_NEWTON
    o.cone, o.impratio = mujoco.mjtCone.mjCONE_ELLIPTIC, 10  # source: the bench's own (bench/build.py)
    if not gravity:
        o.gravity = [0.0, 0.0, 0.0]     # else MuJoCo's own
    return spec


def _flex_dofs(model: mujoco.MjModel, name: str) -> np.ndarray:
    """(nvert, 3): each vertex body's x, y, z slide dofs."""
    f = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_FLEX, name)
    a, n = int(model.flex_vertadr[f]), int(model.flex_vertnum[f])
    bodies = model.flex_vertbodyid[a:a + n]
    return np.array([model.body_dofadr[b] + np.arange(3) for b in bodies])


def _finite(data: mujoco.MjData) -> bool:
    return bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all())


@contextmanager
def _warnings_caught():
    """MuJoCo's warnings while a scene runs, as a list of their texts. MuJoCo's own handler would print each one and
    write MUJOCO_LOG.TXT into the working directory; this one only keeps them (scene_warned turns them into an error).
    The handler is MuJoCo's, so process-wide: the previous one is put back after."""
    caught: list[str] = []
    before = mujoco.get_mju_user_warning()
    mujoco.set_mju_user_warning(lambda msg: caught.append(str(msg).strip()))
    try:
        yield caught
    finally:
        mujoco.set_mju_user_warning(before)


def _warned(data: mujoco.MjData) -> str | None:
    """The first MuJoCo warning this run raised, by its mjtWarning name; None with none."""
    for i, w in enumerate(data.warning):
        if w.number:
            return mujoco.mjtWarning(i).name
    return None


def _check_warnings(kind: PieceKind, data: mujoco.MjData, caught: list[str]) -> None:
    """A scene that made MuJoCo warn (an unstable or ill-conditioned flex) gives no number: BenchError `flex_fit`."""
    name = _warned(data)
    if name is None and not caught:
        return
    raise BenchError(i18n.t("piece {name}: MuJoCo warned while fitting the flex model ({warning}); try numbers "
                            "nearer the 2024-note preset's", name=repr(kind.name),
                            warning=caught[0] if caught else name), code="flex_fit")


def squeeze_force(kind: PieceKind, fit: FlexFit, timestep: float = FLEX_TIMESTEP) -> float:
    """The flex flat, no gravity, between two mocap rollers on a line across its middle, closing from
    SQUEEZE_START_GAP to SQUEEZE_GAP and held: the top roller's normal force (N), averaged over the last
    SQUEEZE_READ_S (NaN if the model blew up). A MuJoCo warning raises BenchError `flex_fit`."""
    spec = _spec(gravity=False, timestep=timestep)
    r = sc.ROLLER_DIAMETER / 2.0
    for name, sign in (("top", 1.0), ("bot", -1.0)):
        b = spec.worldbody.add_body(name=name, mocap=True, pos=[0.0, 0.0, sign * (sc.SQUEEZE_START_GAP / 2.0 + r)])
        b.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[r, sc.ROLLER_WIDTH / 2.0, 0.0],
                   quat=list(ROLL_Q), friction=list(sc.ROLLER_FRICTION), condim=6)
    add_flex_note(spec, "n", (0.0, 0.0, 0.0), kind, fit)
    m = spec.compile()
    d = mujoco.MjData(m)
    top = m.geom("top").id
    total = sc.SQUEEZE_CLOSE_S + sc.SQUEEZE_HOLD_S
    forces, f6 = [], np.zeros(6)
    with _warnings_caught() as caught:
        for _ in range(int(round(total / timestep))):
            t = d.time
            gap = sc.SQUEEZE_START_GAP + (sc.SQUEEZE_GAP - sc.SQUEEZE_START_GAP) * min(t / sc.SQUEEZE_CLOSE_S, 1.0)
            d.mocap_pos[0] = [0.0, 0.0, gap / 2.0 + r]
            d.mocap_pos[1] = [0.0, 0.0, -(gap / 2.0 + r)]
            mujoco.mj_step(m, d)
            _check_warnings(kind, d, caught)
            if not _finite(d):
                return math.nan
            if t > total - sc.SQUEEZE_READ_S:
                ids = contact_ids(m, d)
                f = 0.0
                for i in np.flatnonzero((ids == top).any(axis=1)):
                    mujoco.mj_contactForce(m, d, int(i), f6)
                    f += f6[0]
                forces.append(f)
    return float(np.mean(forces))


def drop_restitution(kind: PieceKind, fit: FlexFit, timestep: float = FLEX_TIMESTEP) -> float:
    """The flex flat, its bottom DROP_HEIGHT above a carpet plane, for DROP_TIME_S: e = sqrt(its center of mass's
    rebound apex above where it ends / DROP_HEIGHT); 0 with no rebound, NaN if the model blew up. A MuJoCo warning
    raises BenchError `flex_fit`."""
    spec = _spec(gravity=True, timestep=timestep)
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[20.0, 20.0, 0.1],
                            friction=list(sc.CARPET_FRICTION))
    _, r = ring_radii(kind)
    add_flex_note(spec, "n", (0.0, 0.0, sc.DROP_HEIGHT + r), kind, fit)
    m = spec.compile()
    d = mujoco.MjData(m)
    dofs = _flex_dofs(m, "n")
    f = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_FLEX, "n")
    a, n = int(m.flex_vertadr[f]), int(m.flex_vertnum[f])
    apex, phase, touched = -1.0, "fall", False
    with _warnings_caught() as caught:
        for _ in range(int(round(sc.DROP_TIME_S / timestep))):
            mujoco.mj_step(m, d)
            _check_warnings(kind, d, caught)
            if not _finite(d):
                return math.nan
            z = float(d.flexvert_xpos[a:a + n, 2].mean())        # equal masses: the mean is the center of mass
            vz = float(d.qvel[dofs[:, 2]].mean())
            touched = touched or d.ncon > 0
            if touched:
                if phase == "fall" and vz > 0:
                    phase = "rise"
                if phase == "rise":
                    apex = max(apex, z)
                    if vz < 0:
                        phase = "done"
    rest = float(d.flexvert_xpos[a:a + n, 2].mean())
    return math.sqrt(max(apex - rest, 0.0) / sc.DROP_HEIGHT) if apex > 0 else 0.0


def target_force(kind: PieceKind) -> float:
    """Each roller's force at a 25 % squeeze, as the kind's `stiffness` says: stiffness x thickness / 8 (stiffness
    times half the squeeze of a quarter of the thickness; 12.70 N for the preset)."""
    return kind.stiffness * thickness(kind) / 8.0


# --- the fit -------------------------------------------------------------------------------------------------------

def _shipped_key(kind: PieceKind) -> str:
    """The shipped table's key: the two numbers exactly, as repr gives them ("2000.0_0.3")."""
    return f"{float(kind.stiffness)!r}_{float(kind.restitution)!r}"


def cache_path(kind: PieceKind, timestep: float = FLEX_TIMESTEP) -> Path:
    """The cache file for the kind's two numbers on this mesh, timestep and fit version (R15)."""
    key = repr((float(kind.stiffness), float(kind.restitution), MESH, float(timestep), FIT_VERSION))
    return cad_fit.SHAPED_CACHE / CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _read(entry) -> FlexFit | None:
    """A cached or shipped entry's fit; None (so it is fitted again) unless E and beta are finite and positive: JSON
    reads NaN, Infinity and negatives, and the cache lives in the shared temp folder."""
    try:
        E, beta = float(entry["E"]), float(entry["beta"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(E) and math.isfinite(beta) and E > 0 and beta > 0):
        return None
    return FlexFit(E=E, beta=beta)


def _shipped(kind: PieceKind, timestep: float) -> FlexFit | None:
    try:
        table = json.loads(SHIPPED_FITS.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict) or (table.get("fit_version"), table.get("mesh"), table.get("timestep")) != (
            FIT_VERSION, list(MESH), timestep):
        return None
    entry = table.get(_shipped_key(kind))
    return _read(entry) if entry is not None else None


def _cached(kind: PieceKind, timestep: float) -> FlexFit | None:
    try:
        return _read(json.loads(cache_path(kind, timestep).read_text()))
    except (OSError, ValueError):
        return None


def _write_cache(kind: PieceKind, fit: FlexFit, timestep: float) -> None:
    path = cache_path(kind, timestep)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps({"E": fit.E, "beta": fit.beta, "stiffness": kind.stiffness,
                               "restitution": kind.restitution, "mesh": list(MESH), "timestep": timestep,
                               "fit_version": FIT_VERSION}))
    os.replace(tmp, path)


def _fit_error(kind: PieceKind, what: str) -> BenchError:
    return BenchError(i18n.t("piece {name}: the flex model can't match its {what} (stiffness {stiffness} N/m, "
                             "restitution {restitution}); try numbers nearer the 2024-note preset's",
                             name=repr(kind.name), what=what, stiffness=f"{kind.stiffness:g}",
                             restitution=f"{kind.restitution:g}"), code="flex_fit")


def _fit_E(kind: PieceKind, beta: float, timestep: float) -> float:
    """A secant on log force against log E from FIT_E0 until the squeeze is within FIT_FORCE_TOL of target_force."""
    target = target_force(kind)
    xa = FIT_E0
    fa = squeeze_force(kind, FlexFit(xa, beta), timestep=timestep)
    if not (fa > 0):
        raise _fit_error(kind, i18n.t("squeeze"))
    if abs(fa - target) <= FIT_FORCE_TOL * target:
        return xa
    xb = xa * target / fa
    for _ in range(FIT_E_STEPS):
        fb = squeeze_force(kind, FlexFit(xb, beta), timestep=timestep)
        if not (fb > 0):
            raise _fit_error(kind, i18n.t("squeeze"))
        if abs(fb - target) <= FIT_FORCE_TOL * target:
            return xb
        slope = (math.log(fb) - math.log(fa)) / (math.log(xb) - math.log(xa)) if xb != xa else 1.0
        slope = slope if slope > FIT_MIN_SLOPE else 1.0
        xa, fa = xb, fb
        xb = xb * math.exp((math.log(target) - math.log(fb)) / slope)
    raise _fit_error(kind, i18n.t("squeeze"))


def _fit_beta(kind: PieceKind, E: float, timestep: float) -> float:
    """A bisection on log beta in FIT_BETA for the drop's e within FIT_E_TOL of `restitution` (e falls as beta
    rises; a blown-up run counts as too little damping), at most FIT_BETA_STEPS runs."""
    lo, hi = FIT_BETA
    for _ in range(FIT_BETA_STEPS):
        mid = math.sqrt(lo * hi)
        e = drop_restitution(kind, FlexFit(E, mid), timestep=timestep)
        if math.isfinite(e) and abs(e - kind.restitution) <= FIT_E_TOL:
            return mid
        if not math.isfinite(e) or e > kind.restitution:
            lo = mid
        else:
            hi = mid
    raise _fit_error(kind, i18n.t("bounce"))


def fit_flex(kind: PieceKind, timestep: float = FLEX_TIMESTEP) -> FlexFit:
    """The flex's E and beta for the kind's `stiffness` and `restitution` at `timestep` (the bench run's: R15, the
    fit runs at the run's own step, and a flex's answer moves a few percent with the step): the shipped fit (the
    preset's, at FLEX_TIMESTEP), else the cached one (keyed by the timestep too), else fitted now (E by the squeeze,
    then beta by the drop at that E) and cached. Raises BenchError when no E or beta in range matches."""
    if kind.stiffness is None or kind.restitution is None:
        raise BenchError(i18n.t("piece {name}: a flex piece needs a stiffness and a restitution",
                                name=repr(kind.name)), code="flex_needs_numbers")
    timestep = float(timestep)
    fit = _shipped(kind, timestep) or _cached(kind, timestep)
    if fit is not None:
        return fit
    beta0 = math.sqrt(FIT_BETA[0] * FIT_BETA[1])   # the squeeze is held still, so beta barely matters
    E = _fit_E(kind, beta0, timestep)
    fit = FlexFit(E=E, beta=_fit_beta(kind, E, timestep))
    _write_cache(kind, fit, timestep)
    return fit
