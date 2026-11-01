"""The bench's MuJoCo model: shapes, shafts, curves cut into chord boxes, and a pool of piece bodies.

Shapes (wheels, plates, bars, curve chords) collide only with pieces, never with each other: shapes have contype 1
and conaffinity 0, rigid pieces 1 and 1. Squishy pieces have both 0 and never collide natively; `squishy.py` finds
their contacts by geometry and applies the launcher model's laws. Rigid pieces collide natively, and a shape's geom
outranks the piece's (geom priority), so MuJoCo takes the shape's friction (its material's `rigid_friction`, a
tread's preset) and contact stiffness rather than mixing the two geoms' numbers.

Wheels on one shaft are one body on one hinge whose inertia is the shaft's; a coupled shaft follows its driver
through an equality JOINT constraint. A springy bar is a body on a slide joint. The pool holds one free body per
piece the inlets will send (at most POOL_MAX), parked below the bench's bounds until the runner spawns them.
"""

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from ..parts.assemble import TREAD_DAMPING, TREAD_IMPEDANCE, TREAD_STIFFNESS, tread_solref
from ..parts.model import quat_from_rpy
from . import cad as cadmod
from .cad import CadWheel
from .problems import Problem
from .spec import UNITS, Bench, BenchError, Curve, Piece

POOL_MAX = 200
BOUNDS_MARGIN = 0.5  # m: how far outside everything a piece must go to count as escaped
PARK_DROP = 1.0      # m below the bounds where the pool waits
PARK_GAP = 0.05      # m between parked pieces' bounding spheres
# Timesteps when [run] gives none. Squishy: 1e-4 s, with the stiff friction and rolling laws taken implicitly
# (squishy.py); the hooded reference (tests/test_bench_reference.py) moves under 0.2% at 1e-5. Rigid: a 2e5 N/m
# tread on a 0.2 kg piece gives tread_solref a 2 ms time constant, so 0.25 ms leaves it about 9 steps (MuJoCo wants 2+).
TIMESTEP = {"squishy": 1e-4, "rigid": 2.5e-4}
CURVE_MAX_DEG = 5.0  # a curve is cut into chords at most this many degrees apart (cut_curve); not a file key
SHAPE_BITS = {"contype": 1, "conaffinity": 0}  # shapes touch pieces, not each other
PIECE_BITS = {"squishy": {"contype": 0, "conaffinity": 0}, "rigid": {"contype": 1, "conaffinity": 1}}
SHAPE_PRIORITY = 1   # above a piece's 0: a rigid piece's contact with a shape uses the shape's friction and solref
# estimate: a piece has no material, so piece-on-piece friction is a middling plastic-on-plastic figure
PIECE_FRICTION = 0.5
# MuJoCo's own torsional and rolling friction defaults
SPIN_FRICTION, ROLL_FRICTION = 0.005, 0.0001
# estimate: a springy bar's moving mass is a solid aluminum rod of its size (2700 kg/m^3); the file gives no mass
BAR_DENSITY = 2700.0
# a wheel body's mass: its hinge passes through the center, so only the shaft inertia about the axis matters
WHEEL_BODY_MASS = 0.5
PROBE_GROUP = 4      # inlet probe geoms: hidden from the default render groups


@dataclass
class BenchLayout:
    shape_of_geom: dict[int, str] = field(default_factory=dict)  # every shape geom -> its shape's name
    shaft_dof: dict[str, int] = field(default_factory=dict)
    wheel_geoms: dict[str, int] = field(default_factory=dict)
    bar_dof: dict[str, int] = field(default_factory=dict)        # springy bars only
    piece_bodies: list[int] = field(default_factory=list)
    piece_geoms: list[int] = field(default_factory=list)
    piece_qpos: list[int] = field(default_factory=list)          # each pool body's free joint qpos address
    piece_qvel: list[int] = field(default_factory=list)          # and its dof address
    parked: list[np.ndarray] = field(default_factory=list)       # where each pool body waits (its qpos0 position)
    bounds: tuple[np.ndarray, np.ndarray] = (np.zeros(3), np.zeros(3))  # lo, hi corners
    outlet_boxes: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)  # name -> lo, hi
    inlet_boxes: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    # name -> a hidden, non-colliding box geom for check_clear: the inlet grown by the piece's own extent, or for a
    # sphere the inlet itself, which must then stay `inlet_margin` (the radius) from every shape
    inlet_probes: dict[str, int] = field(default_factory=dict)
    inlet_margin: float = 0.0
    cad_wheels: dict[str, CadWheel] = field(default_factory=dict)  # a part's wheel shapes (also in wheel_geoms)
    cad_material: dict[str, str] = field(default_factory=dict)     # every other CAD shape -> its material


@dataclass
class Built:
    model: mujoco.MjModel
    layout: BenchLayout


def _inertial(body, mass: float, inertia, iquat=(1.0, 0.0, 0.0, 0.0)) -> None:
    """An explicit inertia at the body's origin (MjSpec leaves ipos unset otherwise, and the compiler fills it in)."""
    body.explicitinertial = True
    body.mass, body.inertia, body.ipos, body.iquat = mass, list(inertia), [0.0, 0.0, 0.0], list(iquat)


def _quat_z_to(v) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_quatZ2Vec(q, np.asarray(v, dtype=float))
    return q


def _mat(q) -> np.ndarray:
    R = np.zeros(9)
    mujoco.mju_quat2Mat(R, np.asarray(q, dtype=float))
    return R.reshape(3, 3)


def _box(center, size) -> tuple[np.ndarray, np.ndarray]:
    c, h = np.asarray(center, dtype=float), np.asarray(size, dtype=float) / 2
    return c - h, c + h


def _aabb(m: mujoco.MjModel, g: int, pos, R) -> tuple[np.ndarray, np.ndarray]:
    """The axis-aligned box around geom g's own bounding box, placed at pos with rotation R."""
    c, h = m.geom_aabb[g, :3], m.geom_aabb[g, 3:]
    mid, half = pos + R @ c, np.abs(R) @ h
    return mid - half, mid + half


def cut_curve(curve: Curve, max_deg: float = 5.0) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The curve as flat chord boxes: (center, quaternion, half-size) each. The inner face (toward the axis) of each
    box is the chord between two points on the curve, at most `max_deg` apart, and the box's thickness lies outside
    it. The radius changes linearly with angle from radius_start to radius_end. Angles turn from zero_dir toward
    zero_dir x axis: about the default axis +y, 0 deg is +x and 90 deg is +z, the launcher hood's convention.
    The box frame: x along the chord, y along the axis, z = x cross y."""
    a, u, o = (np.asarray(v, dtype=float) for v in (curve.axis, curve.zero_dir, curve.center))
    v = np.cross(u, a)
    span = curve.end_deg - curve.start_deg
    n = max(1, math.ceil(abs(span) / max_deg - 1e-9))

    def point(deg):
        r = curve.radius_start + (curve.radius_end - curve.radius_start) * (deg - curve.start_deg) / span
        t = math.radians(deg)
        return o + r * (math.cos(t) * u + math.sin(t) * v)

    pts = [point(curve.start_deg + span * i / n) for i in range(n + 1)]
    out = []
    for p1, p2 in zip(pts[:-1], pts[1:]):
        chord = p2 - p1
        length = float(np.linalg.norm(chord))
        ex = chord / length
        ez = np.cross(ex, a)
        mid = (p1 + p2) / 2
        outward = ez if ez @ (mid - o) > 0 else -ez
        q = np.zeros(4)
        mujoco.mju_mat2Quat(q, np.column_stack([ex, a, ez]).ravel())
        half = np.array([length / 2, curve.width / 2, curve.thickness / 2])
        out.append((mid + outward * curve.thickness / 2, q, half))
    return out


def _series(*ks: float | None) -> float | None:
    """Springs in series; None is a hard (infinitely stiff) side. None when every side is hard."""
    soft = [k for k in ks if k is not None]
    return 1.0 / sum(1.0 / k for k in soft) if soft else None


def _contact(k: float | None, solver_mass: float, dt: float) -> dict:
    """A geom's solref and solimp for a contact of stiffness k (tread_solref: a load F sinks F / k, one contact
    point assumed). A hard contact (k None) keeps MuJoCo's default."""
    if k is None:
        return {}
    return {"solref": list(tread_solref(k, TREAD_DAMPING, solver_mass, 1, dt)),
            "solimp": [TREAD_IMPEDANCE, TREAD_IMPEDANCE, 0.001, 0.5, 2.0]}


class _Shapes:
    """Adds shape geoms with their collision bits, priority, friction and (rigid pieces) contact stiffness."""

    def __init__(self, bench: Bench, dt: float):
        self.dt, self.piece = dt, bench.piece
        self.names: dict[str, str] = {}  # geom name -> shape name
        self.k: dict[str, float | None] = {}  # geom name -> the shape's own stiffness (a tread's), None when hard

    def add(self, body, gname: str, shape: str, gtype, size, pos, quat, friction: float,
            k_shape: float | None = None, **extra):
        body.add_geom(name=gname, type=gtype, size=list(size) + [0.0] * (3 - len(size)), pos=list(pos),
                      quat=list(quat), friction=[friction, SPIN_FRICTION, ROLL_FRICTION], priority=SHAPE_PRIORITY,
                      **SHAPE_BITS, **extra)
        self.names[gname], self.k[gname] = shape, k_shape

    def set_stiffness(self, spec: mujoco.MjSpec, m: mujoco.MjModel, n_pool: int) -> None:
        """A rigid piece's contacts: each shape's stiffness in series with the piece's, and piece on piece two of the
        piece's in series. tread_solref's solver mass is what MuJoCo's solver regularizes with, 1 / body_invweight0
        of the two bodies together (a compiled model's, so this runs between the two compiles)."""
        if self.piece.kind != "rigid" or not n_pool:
            return
        iw_piece = m.body_invweight0[m.body("piece:0").id, 0]
        for gname, k_shape in self.k.items():
            iw_shape = m.body_invweight0[m.geom_bodyid[m.geom(gname).id], 0]
            for key, v in _contact(_series(k_shape, self.piece.stiffness), 1.0 / (iw_piece + iw_shape),
                                   self.dt).items():
                setattr(spec.geom(gname), key, v)
        for i in range(n_pool):
            for key, v in _contact(_series(self.piece.stiffness, self.piece.stiffness), 1.0 / (2 * iw_piece),
                                   self.dt).items():
                setattr(spec.geom(f"piece:{i}:geom"), key, v)


def _check_names(bench: Bench) -> None:
    seen: dict[str, str] = {}
    for kind, items in (("wheel", bench.wheels), ("plate", bench.plates), ("bar", bench.bars),
                        ("curve", bench.curves), ("cad", bench.cads)):
        for it in items:
            if it.name in seen:
                raise BenchError(f"[[{kind}]] '{it.name}' has the same name as [[{seen[it.name]}]] '{it.name}'; "
                                 "shape names must be unique across wheels, plates, bars, curves and CAD (loads are "
                                 "reported by name)")
            seen[it.name] = kind


def _add_shafts(spec: mujoco.MjSpec, bench: Bench, shapes: _Shapes,
                anchors: dict[str, list[tuple[str, np.ndarray, np.ndarray]]]) -> None:
    """`anchors`: per shaft, the CAD joints it turns (label, a point on the joint's axis, the axis). With no
    [[wheel]] on a shaft, its first joint sets its line and direction."""
    by_shaft: dict[str, list] = {s: [] for s in bench.shafts}
    for w in bench.wheels:
        by_shaft[w.shaft].append(w)
    for name, shaft in bench.shafts.items():
        wheels = by_shaft[name]
        lines = [(f"[[wheel]] '{w.name}'", np.array(w.center, float), np.array(w.axis, float)) for w in wheels]
        lines += anchors.get(name, [])
        origin, axis = (lines[0][1], lines[0][2]) if lines else (np.zeros(3), np.array([0.0, 1.0, 0.0]))
        for label, center, ax in lines[1:]:
            off = center - origin
            if abs(np.dot(ax, axis)) < 1 - 1e-9 or np.linalg.norm(off - (off @ axis) * axis) > 1e-6:
                raise BenchError(f"{label} is on shaft '{name}' but not on its axis (the line through "
                                 f"{lines[0][0]} along {tuple(float(x) for x in axis)}); wheels on one "
                                 "shaft turn on one axis, so give it the same axis and a center on that line")
        q = _quat_z_to(axis)
        R = _mat(q)
        j = shaft.wheel_inertia + shaft.added_inertia
        body = spec.worldbody.add_body(name=f"shaft:{name}", pos=list(origin), quat=list(q))
        _inertial(body, WHEEL_BODY_MASS, [j / 2, j / 2, j])  # only the axial moment acts on a hinge
        body.add_joint(name=f"shaft:{name}", type=mujoco.mjtJoint.mjJNT_HINGE, axis=[0, 0, 1])
        for w in wheels:
            mu = bench.materials[w.tread].rigid_friction
            shapes.add(body, f"wheel:{w.name}", w.name, mujoco.mjtGeom.mjGEOM_CYLINDER, (w.diameter / 2, w.width / 2),
                       R.T @ (np.array(w.center) - origin), (1, 0, 0, 0), mu, k_shape=TREAD_STIFFNESS)
    # MuJoCo regularizes a joint equality q1 = c q2 unscaled by c (see parts/assemble.py's couplings), so each is
    # written with |c| >= 1
    for name, shaft in bench.shafts.items():
        if shaft.coupled_to is None:
            continue
        a, b, r = f"shaft:{name}", f"shaft:{shaft.coupled_to}", shaft.ratio
        dep, drv, c = (a, b, r) if abs(r) >= 1 else (b, a, 1.0 / r)
        spec.add_equality(type=mujoco.mjtEq.mjEQ_JOINT, name1=dep, name2=drv,
                          data=[0, c] + [0] * (mujoco.mjNEQDATA - 2), solref=[0.002, 1],
                          solimp=[0.9999, 0.9999, 0.001, 0.5, 2])


def _add_static(spec: mujoco.MjSpec, bench: Bench, shapes: _Shapes) -> None:
    world, mats = spec.worldbody, bench.materials
    for p in bench.plates:
        shapes.add(world, f"plate:{p.name}", p.name, mujoco.mjtGeom.mjGEOM_BOX, np.array(p.size) / 2, p.center,
                   quat_from_rpy(p.rot_deg), mats[p.material].rigid_friction)
    for bar in bench.bars:
        a, b = np.array(bar.a), np.array(bar.b)
        mid, length = (a + b) / 2, float(np.linalg.norm(b - a))
        size, q, mu = (bar.diameter / 2, length / 2), _quat_z_to(b - a), mats[bar.material].rigid_friction
        if bar.spring is None:
            shapes.add(world, f"bar:{bar.name}", bar.name, mujoco.mjtGeom.mjGEOM_CAPSULE, size, mid, q, mu)
            continue
        mass = BAR_DENSITY * math.pi * (bar.diameter / 2) ** 2 * length
        body = world.add_body(name=f"bar:{bar.name}", pos=list(mid))
        body.add_joint(name=f"bar:{bar.name}", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=list(bar.spring_axis),
                       stiffness=bar.spring, range=[-bar.travel, bar.travel], limited=mujoco.mjtLimited.mjLIMITED_TRUE)
        # the spring is undamped, as given: the file has no damping key
        shapes.add(body, f"bar:{bar.name}", bar.name, mujoco.mjtGeom.mjGEOM_CAPSULE, size, (0, 0, 0), q, mu)
        r = bar.diameter / 2
        _inertial(body, mass, [mass * (3 * r * r + length ** 2) / 12] * 2 + [mass * r * r / 2], q)
    for c in bench.curves:
        mu = mats[c.material].rigid_friction
        for i, (center, q, half) in enumerate(cut_curve(c, CURVE_MAX_DEG)):
            shapes.add(world, f"curve:{c.name}:{i}", c.name, mujoco.mjtGeom.mjGEOM_BOX, half, center, q, mu)


def _add_piece(body, piece: Piece) -> None:
    # A mesh piece collides as its convex hull (MuJoCo's own mesh collision); concave pieces are out of scope.
    kw = dict(name=f"{body.name}:geom", friction=[PIECE_FRICTION, SPIN_FRICTION, ROLL_FRICTION],
              **PIECE_BITS[piece.kind])
    if piece.shape == "sphere":
        kw.update(type=mujoco.mjtGeom.mjGEOM_SPHERE, size=[piece.size[0] / 2, 0, 0])
    elif piece.shape == "box":
        kw.update(type=mujoco.mjtGeom.mjGEOM_BOX, size=[s / 2 for s in piece.size])
    elif piece.shape == "cylinder":
        kw.update(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[piece.size[0] / 2, piece.size[1] / 2, 0])
    else:
        kw.update(type=mujoco.mjtGeom.mjGEOM_MESH, meshname="piece_mesh")
    body.add_geom(**kw)


def build_bench(bench: Bench) -> Built:
    _check_names(bench)
    piece = bench.piece
    n_pool = sum(i.count for i in bench.inlets)
    if n_pool > POOL_MAX:
        raise BenchError(f"the inlets send {n_pool} pieces in all; a bench holds at most {POOL_MAX} "
                         "(lower an [[inlet]] count)")
    dt = bench.run.timestep if bench.run.timestep is not None else TIMESTEP[piece.kind]

    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = dt
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10
    shapes = _Shapes(bench, dt)
    placed = [cadmod.place(c) for c in bench.cads]
    anchors: dict[str, list] = {}
    for pl in placed:
        for shaft, label, origin, axis in pl.anchors:
            anchors.setdefault(shaft, []).append((label, origin, axis))
    _add_shafts(spec, bench, shapes, anchors)
    _add_static(spec, bench, shapes)
    cad_wheels, cad_material = {}, {}
    for pl in placed:
        w, mat = cadmod.add_cad(spec, pl, bench, shapes)
        cad_wheels |= w
        cad_material |= mat
    if piece.shape == "mesh":
        spec.add_mesh(name="piece_mesh", file=str(piece.mesh), scale=[UNITS[piece.units]] * 3)
    for i in range(n_pool):
        body = spec.worldbody.add_body(name=f"piece:{i}")
        body.add_freejoint(name=f"piece:{i}")
        _inertial(body, piece.mass, piece.inertia)
        _add_piece(body, piece)
    for inlet in bench.inlets:  # sized below, once the piece's extent is known
        spec.worldbody.add_geom(name=f"inlet:{inlet.name}", type=mujoco.mjtGeom.mjGEOM_BOX,
                                size=list(np.array(inlet.size) / 2), pos=list(inlet.center), group=PROBE_GROUP,
                                rgba=[0, 0, 0, 0], contype=0, conaffinity=0)

    # first compile: the shapes' extent (bounds) and the piece's (parking, probes)
    try:
        m = spec.compile()
    except ValueError as e:
        raise BenchError(f"building the bench: {e}") from None
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    boxes = [_aabb(m, m.geom(g).id, d.geom_xpos[m.geom(g).id], d.geom_xmat[m.geom(g).id].reshape(3, 3))
             for g in shapes.names]
    inlet_boxes = {i.name: _box(i.center, i.size) for i in bench.inlets}
    outlet_boxes = {o.name: _box(o.center, o.size) for o in bench.outlets}
    boxes += list(inlet_boxes.values()) + list(outlet_boxes.values())
    if not boxes:
        raise BenchError("the bench has no shapes, inlets or outlets; add at least one")
    # bounds cover the inlets and outlets too, so a piece spawning or leaving there is never counted as escaped
    lo = np.min([b[0] for b in boxes], axis=0) - BOUNDS_MARGIN
    hi = np.max([b[1] for b in boxes], axis=0) + BOUNDS_MARGIN
    parked = []
    shapes.set_stiffness(spec, m, n_pool)
    if n_pool:
        pg = m.geom("piece:0:geom").id
        gpos = m.geom_pos[pg].copy()
        if piece.shape == "mesh":
            # MuJoCo moves a mesh geom to the mesh's centroid (and turns it to its principal axes). Shift it back by
            # the centroid so the body's origin is the centroid, its axes the file's, and piece.inertia (about the
            # piece's own axes) acts about the centroid. The body origin is then the point the runner spawns and tracks.
            for i in range(n_pool):
                spec.geom(f"piece:{i}:geom").pos = list(-m.mesh_pos[0])
            gpos -= m.mesh_pos[0]
        plo, phi = _aabb(m, pg, gpos, _mat(m.geom_quat[pg]))  # in the piece's frame, as spawned upright
        reach = np.maximum(-plo, phi)
        if piece.shape == "sphere":
            reach = np.zeros(3)  # exact for a ball: the zone must stay a radius clear (inlet_margin), corners and all
        for inlet in bench.inlets:
            spec.geom(f"inlet:{inlet.name}").size = list(np.array(inlet.size) / 2 + reach)
        step = 2 * m.geom_rbound[pg] + PARK_GAP
        cols = math.ceil(math.sqrt(n_pool))
        for i in range(n_pool):
            p = np.array([lo[0] + step * (i % cols), lo[1] + step * (i // cols), lo[2] - PARK_DROP])
            spec.body(f"piece:{i}").pos = list(p)
            parked.append(p)
    try:
        m = spec.compile()
    except ValueError as e:
        raise BenchError(f"building the bench: {e}") from None

    lay = BenchLayout(bounds=(lo, hi), outlet_boxes=outlet_boxes, inlet_boxes=inlet_boxes, parked=parked,
                      inlet_margin=piece.size[0] / 2 if piece.shape == "sphere" else 0.0)
    lay.shape_of_geom = {m.geom(g).id: s for g, s in shapes.names.items()}
    lay.wheel_geoms = {w.name: m.geom(f"wheel:{w.name}").id for w in bench.wheels}
    lay.wheel_geoms |= {s: g for g, s in lay.shape_of_geom.items() if s in cad_wheels}
    lay.cad_wheels, lay.cad_material = cad_wheels, cad_material
    lay.shaft_dof = {s: int(m.joint(f"shaft:{s}").dofadr[0]) for s in bench.shafts}
    lay.bar_dof = {b.name: int(m.joint(f"bar:{b.name}").dofadr[0]) for b in bench.bars if b.spring is not None}
    for i in range(n_pool):
        j = m.joint(f"piece:{i}")
        lay.piece_bodies.append(m.body(f"piece:{i}").id)
        lay.piece_geoms.append(m.geom(f"piece:{i}:geom").id)
        lay.piece_qpos.append(int(j.qposadr[0]))
        lay.piece_qvel.append(int(j.dofadr[0]))
    lay.inlet_probes = {i.name: m.geom(f"inlet:{i.name}").id for i in bench.inlets}
    return Built(model=m, layout=lay)


def check_clear(built: Built) -> None:
    """Every inlet clears every shape: a piece spawned anywhere in the zone, upright, starts outside all of them
    (touching is allowed). Assumes the runner keeps spawn centers inside the inlet box. A ball's test is exact (the
    zone kept a radius from every shape); any other piece's zone is grown by the piece's bounding box, which is safe
    but conservative at the box's corners. Raises BenchError naming the inlet and the first shape it
    overlaps."""
    m, lay = built.model, built.layout
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    for inlet, probe in lay.inlet_probes.items():
        for g, shape in lay.shape_of_geom.items():
            depth = lay.inlet_margin - mujoco.mj_geomDistance(m, d, probe, g, lay.inlet_margin + 0.01, None)
            if depth > 1e-6:
                raise BenchError(f"inlet '{inlet}' overlaps shape '{shape}' by {depth * 1000:.1f} mm: a piece spawned "
                                 "there would start inside it; move the inlet or the shape (the zone, grown by the "
                                 "piece's own size, must clear every shape)", f"inlet.{inlet}")


OVERLAP_MIN = 0.002  # m: a moving part this far into another shape is worth a warning (touching is fine)


def overlap_warnings(built: Built, bench: Bench) -> list[Problem]:
    """A warning for each pair of different shapes where one moves (a wheel, a springy bar, a CAD part with slots) and
    they overlap by more than OVERLAP_MIN at rest. Static shapes touching each other (a hood joined to its flare)
    are never warned about; a part's own shapes are one entry."""
    m, lay = built.model, built.layout
    entry: dict[str, tuple[str, str]] = {}  # shape name -> (section, entry name)
    for section, items in (("wheel", bench.wheels), ("plate", bench.plates), ("bar", bench.bars),
                           ("curve", bench.curves)):
        entry |= {i.name: (section, i.name) for i in items}
    moving = {w.name for w in bench.wheels} | {b.name for b in bench.bars if b.spring is not None}
    for shape in set(lay.shape_of_geom.values()):
        for c in bench.cads:
            if shape == c.name or shape.startswith(c.name + "."):
                entry[shape] = ("cad", c.name)
                if c.slots:
                    moving.add(shape)
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    geoms, seen, out = sorted(lay.shape_of_geom), set(), []
    for i, g1 in enumerate(geoms):
        for g2 in geoms[i + 1:]:
            s1, s2 = lay.shape_of_geom[g1], lay.shape_of_geom[g2]
            if s1 == s2 or entry.get(s1) == entry.get(s2) or not (s1 in moving or s2 in moving):
                continue
            if s2 in moving and s1 not in moving:
                s1, s2, g1, g2 = s2, s1, g2, g1
            if (s1, s2) in seen:
                continue
            dist = mujoco.mj_geomDistance(m, d, g1, g2, 0.01, None)
            if dist < -OVERLAP_MIN:
                seen.add((s1, s2))
                (k1, n1), (k2, n2) = entry[s1], entry[s2]
                out.append(Problem("warning", f"{k1}.{n1}",
                                   f"{k1} '{n1}' and {k2} '{n2}' overlap by {-dist * 1000:.0f} mm"))
    return out
