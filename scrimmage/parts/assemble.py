"""[[part]] entries -> one MuJoCo model (via MjSpec) plus the device bindings their slots and ids describe."""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import mujoco
import numpy as np

from ..i18n import t
from .field import add_field
from .ids import slot_bindings
from ..teamfolders import TeamFolderError, expand
from .model import Body, Geom, Part, PartError, Where, load_part, model_timestep, quat_from_rpy, tread_name
from ..pneumatics.model import ATM, MAX_WORKING_PSI, PSI, CylinderSpec

if TYPE_CHECKING:
    from .partner import PartnerInfo
    from ..testrange.build import RangeLayout
    from ..testrange.spec import Range

NAME_RE = re.compile(r"[A-Za-z0-9_]+$")
PLACEMENT_KEYS = ("name", "stock", "path", "attach", "pos", "rot", "ids", "grip")   # grip: path parts only
ROBOT_BITS = {"contype": 2, "conaffinity": 1}
NO_BITS = {"contype": 0, "conaffinity": 0}
GEOM_TYPES = {"box": mujoco.mjtGeom.mjGEOM_BOX, "cylinder": mujoco.mjtGeom.mjGEOM_CYLINDER,
              "sphere": mujoco.mjtGeom.mjGEOM_SPHERE, "mesh": mujoco.mjtGeom.mjGEOM_MESH,
              "capsule": mujoco.mjtGeom.mjGEOM_CAPSULE}
AXIS_TURN = {"z": (0, 0, 0), "x": (0, 90, 0), "y": (-90, 0, 0)}   # cylinder z -> axis

# Tread wheels. A geom with tread = true is a drive wheel's tread, and its contact is a spring of the drivetrain's
# tread stiffness (N/m per wheel) and damping ratio (tread_solref). The defaults are estimates for a 4 in FRC wheel
# with a TPU or Colson-like tread, about 1e5 to 3e5 N/m: a 5 mm layer of 70A to 95A urethane (E about 5 to 30 MPa)
# under a 1 to 1.5 in wide, 1 cm long contact patch, k = E A / t. Urethane treads barely bounce: critical damping.
TREAD_STIFFNESS = 2.0e5     # N/m per wheel (estimate)
TREAD_DAMPING = 1.0         # damping ratio (estimate)
# solimp dmin = dmax, held constant so the spring is linear. MuJoCo's soft contact at rest is preloaded: it holds a
# load at (1 - d') / d' times the sag of a bare spring with the same bounce frequency, d' the impedance the robot's
# whole weight sees (above d, as the contacts share it). With the sag set to the tread's (tread_solref), a lower d
# lands more like a bare spring (d = 0.25 sinks within 15 % of one) but makes a sliding wheel hover and chatter (the
# soft convex contact pushes a sliding contact apart): in a swerve robot's scrubbing turn its wheels spent 90 % of
# steps out of contact with 1.7 kN spikes, against 73 % and 0.65 kN before treads. d = 0.8 is the lowest that is no
# worse than before (71 %, 0.55 kN). Dropped 0.15 m, a 49 kg swerve robot then sinks 16 mm (12 mm before treads),
# about 3 times a bare critically damped spring's 5.6 mm. Parked across a 10 deg slope it creeps 0.18 mm/s sideways
# (0.06 mm/s before).
TREAD_IMPEDANCE = 0.8
TREAD_SOLMIX = 1e3          # above the carpet's and elements' default 1, so the tread's contact governs; below a
                            # ball's 1e6 (testrange/build.py), so a ball's contact governs its hits on a wheel
# Contacts MuJoCo makes between a tread geom resting on the carpet (a plane) and the carpet: a cylinder on its side
# touches along a line, its two end points; a box at its 4 corners. A mesh is a guess (MuJoCo finds 1 to 4).
PLANE_CONTACTS = {"cylinder": 2, "box": 4, "sphere": 1, "mesh": 1, "capsule": 2}


def tread_solref(stiffness: float, damping_ratio: float, solver_mass: float, contacts: int = 1,
                 timestep: float = 0.001) -> tuple[float, float]:
    """MuJoCo solref (timeconst, dampratio) for a tread of `stiffness` N/m that touches the ground at `contacts`
    points, so a wheel carrying a load F rests sunk F / stiffness into the ground, whatever F. `solver_mass` is
    the mass MuJoCo's solver sees at the wheel, 1 / body_invweight0 of the wheel's body (with the joints at qpos0).

    MuJoCo's contact (see testrange/build.py) moves a fraction d of the way from the unconstrained acceleration a0
    to the reference aref = -b v - k d r, with k = 1 / (dmax^2 T^2 zeta^2) and b = 2 / (dmax T). In matrix form it
    solves (A + R) f = aref - a0, where R = A_ii (1 - d) / d regularizes each contact by its own approximate
    inverse mass A_ii = 1 / solver_mass. At rest the contacts hold the robot, A f = -a0, which leaves R f = aref
    for each contact, whatever the others do: with d held at dmax (solimp dmin = dmax = TREAD_IMPEDANCE) and v = 0,
        (1 - d) f / (d solver_mass) = k d sag   ->   sag = (1 - d) T^2 zeta^2 f / solver_mass.
    The wheel's load F is spread over its contacts, f = F / contacts, and sag = F / stiffness gives
        T = sqrt(contacts solver_mass / ((1 - d) stiffness)) / zeta.
    MuJoCo raises a time constant below 2 timesteps to 2 timesteps, so a stiffer request is clamped there and the
    wheel sags more than asked: at a 1 ms step, a tread above about 7e5 N/m per kg of solver_mass (2 contacts,
    zeta 1); on a 49 kg stock swerve robot a wheel's solver mass is about 17 kg."""
    tc = math.sqrt(contacts * solver_mass / ((1.0 - TREAD_IMPEDANCE) * stiffness)) / damping_ratio
    return max(tc, 2.0 * timestep), damping_ratio


@dataclass
class Instance:
    name: str
    part: Part
    source: str                     # "stock chassis" or the part folder
    attach: str | None
    pos: tuple[float, float, float]
    rot: tuple[float, float, float]
    ids: dict
    where: str
    stock_params: dict = field(default_factory=dict)   # a stock part's checked parameters ({} for a path part)


@dataclass
class Built:
    model: mujoco.MjModel
    spec: mujoco.MjSpec
    robot_body: str
    instances: list[Instance]
    bindings: dict[str, list[dict]] = field(default_factory=lambda: {"motor": [], "encoder": [], "gyro": [],
                                                                      "valve": []})
    warnings: list[str] = field(default_factory=list)
    root_offset: tuple = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))   # root part's pos and rot quat, in the start-pose frame
    range: Range | None = None          # the config's [range], its goals and balls in the model (scrimmage/testrange)
    layout: RangeLayout | None = None
    tread_geoms: list[str] = field(default_factory=list)   # every geom with tread = true, by its full name
    cylinders: list[CylinderSpec] = field(default_factory=list)   # named by instance; full body names, length set
    partner: PartnerInfo | None = None      # the config's [partner] (parts/partner.py): not part of the robot


def _qmul(p, q) -> np.ndarray:
    out = np.zeros(4)
    mujoco.mju_mulQuat(out, np.asarray(p, float), np.asarray(q, float))
    return out


def _rot(v, q) -> np.ndarray:
    out = np.zeros(3)
    mujoco.mju_rotVecQuat(out, np.asarray(v, float), np.asarray(q, float))
    return out


DEFAULT_START_POSE = (2.0, 4.035, 0.0)    # a parts robot with no [robot] start_pose: inside the field, facing +x


def parse_start_pose(value, default=None) -> tuple[float, float, float] | None:
    """[robot] start_pose as (x, y, heading_deg): 3 finite numbers, else a ValueError naming the key."""
    if value is None:
        return default
    ok = (isinstance(value, (list, tuple)) and len(value) == 3
          and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value))
    if not ok:
        raise ValueError(t("{robot_table} start_pose must be [x, y, heading_deg], 3 finite numbers, not {value}",
                           value=repr(value), robot_table="[robot]"))
    return tuple(float(v) for v in value)


def _start(cfg: dict, section) -> tuple[float, float, float]:
    """Where a parts robot starts: its [range] start, else [robot] start_pose, else DEFAULT_START_POSE."""
    if section is not None and section.start is not None:
        return section.start_pose()
    return parse_start_pose(cfg.get("robot", {}).get("start_pose"), DEFAULT_START_POSE)


def robot_start(cfg: dict, cfg_dir: Path | None) -> tuple[float, float, float]:
    """(x, y, heading_deg) a parts robot's config starts it at (see _start), its [range] read from `cfg_dir`."""
    from ..testrange.build import parse_range_section
    return _start(cfg, parse_range_section(cfg, cfg_dir) if "range" in cfg else None)


def _vec3(e: dict, key: str, where: str) -> tuple[float, float, float]:
    v = e.get(key, [0, 0, 0])
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
        raise PartError(t("{where}: {key} must be a list of 3 numbers, not {value}", where=where, key=key,
                          value=repr(v)), where=where)
    return tuple(float(x) for x in v)


def _instances(cfg: dict, cfg_dir: Path) -> list[Instance]:
    from .stock import build_stock, stock_params
    entries = cfg.get("part")
    if not entries:
        raise PartError(t("no {part_table} entries", part_table="[[part]]"))
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise PartError(t("{part_table} must be an array of tables", part_table="[[part]]"))
    out: list[Instance] = []
    for i, e in enumerate(entries):
        name = e.get("name")
        where = (Where(f"parts[{i}] {name!r}", name) if isinstance(name, str) else Where(f"parts[{i}]"))
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise PartError(t("{where}: name must be letters, digits and _ (it prefixes the part's names), not {name}",
                              where=where, name=repr(name)), where=where, field="name")
        if any(x.name == name for x in out):
            raise PartError(t("{where}: the part name {name} is used twice", where=where, name=repr(name)),
                            where=where, field="name")
        if ("stock" in e) == ("path" in e):
            raise PartError(t("{where}: give exactly one of {stock_setting} or {path_setting}", where=where,
                              stock_setting="stock = \"<stock part>\"",
                              path_setting="path = \"<part folder>\""), where=where)
        params = {k: v for k, v in e.items() if k not in PLACEMENT_KEYS}
        checked: dict = {}
        if "stock" in e:
            if "grip" in e:
                raise PartError(t("{where}: grip is for a part folder's bodies; a stock part has its own materials",
                                  where=where), where=where)
            checked = stock_params(str(e["stock"]), params, where)
            measured = checked.get("measured") if e["stock"] == "cylinder" else ""
            provenance, notes = None, []
            if measured:
                from ..measure.file import MeasureError
                from ..measure.use import apply_cylinder
                try:
                    params, provenance, notes = apply_cylinder(params, measured, where, base=cfg_dir,
                                                               cylinder=checked)
                except MeasureError as err:
                    raise PartError(str(err), where=where, field=f"param:{err.key or 'measured'}") from None
                checked = stock_params(str(e["stock"]), params, where)
            part, source = build_stock(str(e["stock"]), params, where), f"stock {e['stock']}"
            if provenance is not None:
                part.cylinders = [dataclasses.replace(c, provenance=provenance) for c in part.cylinders]
                part.warnings = [*part.warnings, *notes]
        else:
            if params:
                raise PartError(t("{where}: {keys} isn't a key of a path part (allowed: {allowed})", where=where,
                                  keys=", ".join(repr(k) for k in params), allowed=", ".join(PLACEMENT_KEYS)),
                                where=where)
            try:
                folder = (cfg_dir / expand(str(e["path"]))).resolve()
            except TeamFolderError as err:
                raise PartError(f"{where}: {err}", where=where) from None
            try:
                part, source = load_part(folder if folder.is_file() else folder / "part.toml"), str(folder)
            except PartError as err:
                raise PartError(f"{where}: {err}", where=where) from None
            if "grip" in e:     # over the folder's own (its scrimmage-edits.toml)
                from .grip import apply_grip
                part, _ = apply_grip(part, e["grip"], where, base=cfg_dir)
        attach = e.get("attach")
        if attach is not None and not (isinstance(attach, str) and attach.count(".") == 1 and all(attach.split("."))):
            raise PartError(t("{where}: attach must be \"{part_mount_or}\", not {attach}", where=where,
                              attach=repr(attach), part_mount_or="<part>.<mount or body>"), where=where, field="attach")
        ids = e.get("ids", {})
        if not isinstance(ids, dict):
            raise PartError(t("{where}: ids must be a table of {slot_setting}, not {ids}", where=where,
                              ids=repr(ids), slot_setting="slot = device"),
                            where=where, field="ids")
        out.append(Instance(name, part, source, attach, _vec3(e, "pos", where), _vec3(e, "rot", where), ids, where,
                            checked))
    roots = [x for x in out if x.attach is None]
    if len(roots) != 1:
        if roots:
            raise PartError(t("exactly one part without attach is the robot's root (it gets the free joint); parts "
                              "without attach: {parts}", parts=", ".join(x.name for x in roots)))
        raise PartError(t("exactly one part without attach is the robot's root (it gets the free joint); parts "
                          "without attach: none"))
    return out


def _order(insts: list[Instance]) -> list[Instance]:
    names = [x.name for x in insts]
    for x in insts:
        if x.attach is not None:
            target = x.attach.split(".")[0]
            if target not in names:
                raise PartError(t("{where}: attach {attach}: no part {target}; parts: {parts}", where=x.where,
                                  attach=repr(x.attach), target=repr(target), parts=", ".join(names)),
                                where=x.where, field="attach")
    placed: list[Instance] = [x for x in insts if x.attach is None]
    rest = [x for x in insts if x.attach is not None]
    while rest:
        ready = [x for x in rest if x.attach.split(".")[0] in {p.name for p in placed}]
        if not ready:
            raise PartError(t("attach cycle: {parts}", parts=", ".join(sorted(x.name for x in rest))))
        placed += ready
        rest = [x for x in rest if x not in ready]
    return placed


def _target_frame(inst: Instance, by_name: dict[str, Instance]) -> tuple[str, np.ndarray, np.ndarray]:
    """(prefixed body name, pos, quat) of the frame `inst` attaches to, before its own offset."""
    tname, local = inst.attach.split(".")
    target = by_name[tname].part
    for m in target.mounts:
        if m.name == local:
            return f"{tname}.{m.body}", np.array(m.pos), np.array(quat_from_rpy(m.rot))
    if any(b.name == local for b in target.bodies):
        return f"{tname}.{local}", np.zeros(3), np.array([1.0, 0, 0, 0])
    mounts, bodies = ", ".join(m.name for m in target.mounts), ", ".join(b.name for b in target.bodies)
    if mounts:
        message = t("{where}: attach {attach}: no mount or body {local} on {target}; mounts: {mounts}; bodies: "
                    "{bodies}", where=inst.where, attach=repr(inst.attach), local=repr(local), target=repr(tname),
                    mounts=mounts, bodies=bodies)
    else:
        message = t("{where}: attach {attach}: no mount or body {local} on {target}; mounts: none; bodies: {bodies}",
                    where=inst.where, attach=repr(inst.attach), local=repr(local), target=repr(tname), bodies=bodies)
    raise PartError(message, where=inst.where, field="attach")


def _add_geom(body, g: Geom, part: Part, spec: mujoco.MjSpec, mesh_names: dict, looks_only: bool = False,
              name: str = "") -> object:
    """`looks_only`: the body's inertia is given explicitly, so a mesh adds none; its mass properties are taken as a
    shell's, so an open or flat CAD mesh (faces with no volume) doesn't fail MuJoCo's mesh-volume check."""
    quat = quat_from_rpy(g.rot)
    kw: dict = {"type": GEOM_TYPES[g.shape], "pos": list(g.pos), **(ROBOT_BITS if g.collide else NO_BITS)}
    if name:
        kw["name"] = name
    if g.shape == "box":
        kw["size"] = [s / 2 for s in g.size]
    elif g.shape == "cylinder":
        kw["size"] = [g.size[0], g.size[1] / 2, 0]
        quat = list(_qmul(quat, quat_from_rpy(AXIS_TURN[g.axis])))
    elif g.shape == "capsule":
        kw["size"] = [g.size[0], g.size[1] / 2, 0]
    elif g.shape == "sphere":
        kw["size"] = [g.size[0], 0, 0]
    else:
        path = str((part.base_dir / g.size).resolve())
        key = (path, looks_only)
        if key not in mesh_names:
            mesh_names[key] = f"mesh{len(mesh_names)}"
            mesh = spec.add_mesh(name=mesh_names[key], file=path)
            if looks_only:
                mesh.inertia = mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL
        kw["meshname"] = mesh_names[key]
    kw["quat"] = list(quat)
    if g.friction is not None:
        kw["friction"] = [g.friction, 0.01, 0.001]
    if g.color is not None:
        kw["rgba"] = list(g.color)
    if g.condim is not None:
        kw["condim"] = g.condim
    if g.solref is not None:
        kw["solref"] = list(g.solref)
    if g.priority:
        kw["priority"] = g.priority
    return body.add_geom(**kw)


def _fill(spec: mujoco.MjSpec, part: Part, densities: dict[str, float] | None) -> dict[str, list]:
    """Add the part's bodies under spec.worldbody; returns body name -> its geoms (spec objects)."""
    bodies, geoms, mesh_names = {}, {}, {}
    for b in part.bodies:
        parent = spec.worldbody if b.parent is None else bodies[b.parent]
        sb = parent.add_body(name=b.name, pos=list(b.pos), quat=quat_from_rpy(b.rot))
        bodies[b.name] = sb
        if b.joint is not None:
            j = b.joint
            hinge = j.kind == "hinge"
            kw = {"name": j.name, "axis": list(j.axis), "damping": j.damping, "frictionloss": j.frictionloss,
                  "armature": j.armature,
                  "type": mujoco.mjtJoint.mjJNT_HINGE if hinge else mujoco.mjtJoint.mjJNT_SLIDE}
            if j.range is not None:
                kw["range"] = [math.radians(v) for v in j.range] if hinge else list(j.range)   # autolimits
            if j.limit_solref is not None:
                kw["solref_limit"] = list(j.limit_solref)
            if j.spring is not None:
                kw["stiffness"] = j.spring[0]
                kw["springref"] = math.radians(j.spring[1]) if hinge else j.spring[1]
            sb.add_joint(**kw)
        geoms[b.name] = [_add_geom(sb, g, part, spec, mesh_names, looks_only=b.com is not None,
                                   name=tread_name(b.name, i) if g.tread else g.name or "")
                        for i, g in enumerate(b.geoms)]
        if b.com is not None:
            sb.mass, sb.ipos, sb.explicitinertial = b.mass, list(b.com), True
            sb.inertia = list(b.inertia) if b.inertia is not None else [0.4 * b.mass * 0.05 ** 2] * 3
            if b.inertia_rot is not None:
                sb.iquat = quat_from_rpy(b.inertia_rot)
            for g in geoms[b.name]:
                g.density = 0
        elif not b.geoms:   # nothing to measure: the whole mass at the body origin
            sb.mass, sb.ipos, sb.explicitinertial = b.mass, [0, 0, 0], True
            sb.inertia = [0.4 * b.mass * 0.05 ** 2] * 3
        else:
            for g in geoms[b.name]:
                g.density = 1.0 if densities is None else densities.get(b.name, 0.0)
    return geoms


def part_spec(part: Part, where: str | None = None) -> mujoco.MjSpec:
    """The part on its own: names unprefixed, each body's mass spread over its geoms by volume."""
    def fresh(densities):
        s = mujoco.MjSpec()
        s.compiler.degree = False
        _fill(s, part, densities)
        return s
    try:
        probe = fresh(None).compile()
    except ValueError as e:
        if where:
            raise PartError(f"{where}: {e}", where=where) from None
        raise PartError(t("part {name}: {problem}", name=repr(part.name), problem=e)) from None
    densities = {}
    for b in part.bodies:
        if b.com is None:
            vol = float(probe.body_mass[mujoco.mj_name2id(probe, mujoco.mjtObj.mjOBJ_BODY, b.name)])
            densities[b.name] = b.mass / vol if vol > 0 else 0.0
    s = fresh(densities)
    for b in part.bodies:   # no geometry volume: the whole mass at the body origin
        if b.com is None and densities[b.name] == 0.0:
            sb = s.body(b.name)
            sb.mass, sb.ipos, sb.explicitinertial = b.mass, [0, 0, 0], True
            sb.inertia = [0.4 * b.mass * 0.05 ** 2] * 3
    return s


def _set_treads(built: Built, timestep: float) -> None:
    """Give every tread geom its contact (tread_solref), in the compiled model and the spec, and list them in
    built.tread_geoms. The stiffness and damping are the part's tread_stiffness and tread_damping (a stock
    drivetrain's parameters), else TREAD_STIFFNESS and TREAD_DAMPING. A tread geom with its own solref keeps it."""
    built.tread_geoms.extend(_tread_contacts(built, built.instances, timestep))
    if built.partner is not None:       # the partner's wheels get the same contact, but aren't the robot's
        _tread_contacts(built, built.partner.parts, timestep)


def _tread_contacts(built: Built, insts: list[Instance], timestep: float) -> list[str]:
    """Set each tread geom's contact (see _set_treads) of `insts`; their full names."""
    m = built.model
    names = []
    for inst in insts:
        for b in inst.part.bodies:
            for i, g in enumerate(b.geoms):
                if not g.tread:
                    continue
                name = f"{inst.name}.{tread_name(b.name, i)}"
                names.append(name)
                if g.solref is not None:
                    continue
                gid = m.geom(name).id
                k = inst.stock_params.get("tread_stiffness", TREAD_STIFFNESS)
                zeta = inst.stock_params.get("tread_damping", TREAD_DAMPING)
                mass = 1.0 / float(m.body_invweight0[m.geom_bodyid[gid], 0])
                solref = list(tread_solref(k, zeta, mass, PLANE_CONTACTS[g.shape], timestep))
                solimp = [TREAD_IMPEDANCE, TREAD_IMPEDANCE, 0.001, 0.5, 2.0]
                m.geom_solref[gid], m.geom_solimp[gid], m.geom_solmix[gid] = solref, solimp, TREAD_SOLMIX
                sg = built.spec.geom(name)
                sg.solref, sg.solimp, sg.solmix = solref, solimp, TREAD_SOLMIX
    return names


# A cylinder's end stops. MuJoCo's default soft limit (0.02 s) lets 60 psi on the stock 1-1/16 in bore (about 240 N)
# push the free form's 0.1 kg rod about 2 cm past its stroke; at 2 ms (2 timesteps, the stiffest MuJoCo keeps) it
# is a fraction of a millimeter. The free form's slide has the same stops (parts/stock/mechanisms.py).
CYLINDER_STOP_SOLREF = (0.002, 1.0)


def cylinder_armature(cy: CylinderSpec, timestep: float) -> float:
    """The inertia (kg) along a cylinder's line that keeps its air stable at the physics step.

    The air in a chamber is a spring on the rod, k = p_abs A^2 / V, and the air model hands MuJoCo its force once per
    step from the length at the step's start (PneumaticsRuntime.tendon_length): an explicit spring, which on a mass m
    is unstable once k dt^2 / m passes about 4. k is largest at a stroke's end, where only the dead volume is left:
    k_max on the cap side (the larger area) at the working limit. Armature k_max dt^2 / 2 on the tendon keeps
    k dt^2 / m_eff below 2 whatever load the cylinder drives, while its chambers are at or below the working
    pressure. A vented side squeezed above it, cushioning a moving load, is stiffer; there the load's own inertia
    (in m_eff) keeps it stable. The armature stands for real inertia along the cylinder's line (the rod, and the
    air's lag in the tube), not a change to the force: about 0.02 kg on the stock 1-1/16 in bore, 0.2 kg on a 2 in
    bore, at 1 ms."""
    a_cap = math.pi * cy.bore ** 2 / 4
    k_max = (MAX_WORKING_PSI * PSI + ATM) * a_cap ** 2 / cy.dead_volume
    return k_max * timestep ** 2 / 2


def _pin_body(inst: Instance, body: str, by_name: dict[str, Instance]) -> str:
    """A cylinder pin's body by its full name: the instance's own body, or (push) "<instance>.<body>", checked."""
    if any(b.name == body for b in inst.part.bodies):
        return f"{inst.name}.{body}"
    tname, local = body.split(".")
    if tname not in by_name:
        raise PartError(t("{part}: push {body}: no part {target}; parts: {parts}", part=inst.name, body=repr(body),
                          target=repr(tname), parts=", ".join(by_name)))
    bodies = [b.name for b in by_name[tname].part.bodies]
    if local not in bodies:
        raise PartError(t("{part}: push {body}: no body {local} on {target}; its bodies: {bodies}", part=inst.name,
                          body=repr(body), local=repr(local), target=repr(tname), bodies=", ".join(bodies)))
    return body


def _add_cylinders(spec: mujoco.MjSpec, insts: list[Instance], by_name: dict[str, Instance],
                   timestep: float) -> list[CylinderSpec]:
    """Each cylinder's pins as sites "<inst>.pin_a" and "<inst>.pin_b", a spatial tendon "<inst>.cylinder" between
    them that stops at both ends of the stroke and carries the seal friction and the armature that keeps the air
    stable at `timestep` (cylinder_armature), and an actuator of the same name whose
    ctrl is the air's force in N (positive extends). The tendon's range is set in _resolve_cylinders, once compiled."""
    out = []
    for inst in insts:
        for cy in inst.part.cylinders:
            pins = []
            for key, (body, pos) in (("pin_a", cy.pin_a), ("pin_b", cy.pin_b)):
                full = _pin_body(inst, body, by_name)
                spec.body(full).add_site(name=f"{inst.name}.{key}", pos=list(pos), size=[0.005, 0, 0])
                pins.append((full, tuple(pos)))
            ten = spec.add_tendon(name=f"{inst.name}.cylinder", limited=mujoco.mjtLimited.mjLIMITED_TRUE,
                                  range=[0.0, cy.stroke], frictionloss=cy.friction, width=cy.bore / 2,
                                  solref_limit=list(CYLINDER_STOP_SOLREF),
                                  armature=cylinder_armature(cy, timestep))
            ten.wrap_site(f"{inst.name}.pin_a")
            ten.wrap_site(f"{inst.name}.pin_b")
            spec.add_actuator(name=f"{inst.name}.cylinder", trntype=mujoco.mjtTrn.mjTRN_TENDON,
                              target=f"{inst.name}.cylinder", gainprm=[1.0] + [0.0] * 9, biasprm=[0.0] * 10,
                              ctrllimited=mujoco.mjtLimited.mjLIMITED_FALSE,
                              forcelimited=mujoco.mjtLimited.mjLIMITED_FALSE)
            out.append(dataclasses.replace(cy, name=inst.name, pin_a=pins[0], pin_b=pins[1]))
    return out


def _add_couplings(spec: mujoco.MjSpec, prefix: str, part: Part) -> None:
    """The part's couplings as joint equalities, its joints named "<prefix>.<joint>".

    MuJoCo regularizes a joint equality q1 = c q2 with both joints' inverse inertias, unscaled by c, while the
    constraint itself weighs joint 2 by c^2: with |c| < 1 a light joint 2 (a pinion driving a rack at 0.02 m/rad)
    makes the coupling far too soft. So each coupling is written with |c| >= 1 (b = r a, or a = b / r), and
    near-hard (solimp 0.9999)."""
    for cp in part.couplings:
        a, b = (f"{prefix}.{j}" for j in cp.joints)
        dep, drv, c = (b, a, cp.ratio) if abs(cp.ratio) >= 1 else (a, b, 1.0 / cp.ratio)
        spec.add_equality(type=mujoco.mjtEq.mjEQ_JOINT, name1=dep, name2=drv,
                          data=[0, c] + [0] * (mujoco.mjNEQDATA - 2), solref=[0.002, 1],
                          solimp=[0.9999, 0.9999, 0.001, 0.5, 2])


def _moving_body(m: mujoco.MjModel, b: int) -> int:
    """The body itself if it has a joint, else its nearest ancestor that does (the world if none)."""
    while b != 0 and m.body_jntnum[b] == 0:
        b = int(m.body_parentid[b])
    return b


def _resolve_cylinders(built: Built, cylinders: list[CylinderSpec]) -> None:
    """Check each cylinder can move, find its retracted length, and set its tendon's range to
    [length, length + stroke] in the model and the spec. The push form's length comes from the pins' distance at
    qpos0, which needs the compiled model; so the tendon is built with a stand-in range and patched here (as the
    treads are), rather than compiling twice."""
    m = built.model
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    for cy in cylinders:
        a, b = (_moving_body(m, m.body(pin[0]).id) for pin in (cy.pin_a, cy.pin_b))
        if a == b:
            raise PartError(t("{cylinder}: the cylinder's two ends are on bodies that can't move relative to each "
                              "other (nothing between {a} and {b} turns or slides)", cylinder=cy.name, a=cy.pin_a[0],
                              b=cy.pin_b[0]))
        apart = float(np.linalg.norm(d.site_xpos[m.site(f"{cy.name}.pin_a").id]
                                     - d.site_xpos[m.site(f"{cy.name}.pin_b").id]))
        if apart < 1e-6:
            raise PartError(t("{cylinder}: the cylinder's two ends are at the same point; move the part or push_pos "
                              "so they are apart", cylinder=cy.name))
        length = apart if cy.length is None else cy.length
        if not length - 1e-9 <= apart <= length + cy.stroke + 1e-9:
            raise PartError(t("{cylinder}: the cylinder's ends are {apart} m apart as assembled, outside its travel "
                              "from {retracted} m (retracted) to {extended} m (extended); set length between those, "
                              "or 0 to take it from the assembled pose", cylinder=cy.name, apart=f"{apart:.4g}",
                              retracted=f"{length:.4g}", extended=f"{length + cy.stroke:.4g}"))
        tid = m.tendon(f"{cy.name}.cylinder").id
        m.tendon_range[tid] = [length, length + cy.stroke]
        built.spec.tendon(f"{cy.name}.cylinder").range = [length, length + cy.stroke]
        built.cylinders.append(dataclasses.replace(cy, length=length))


def assemble(cfg: dict, cfg_dir: Path) -> Built:
    insts = _order(_instances(cfg, cfg_dir))
    by_name = {x.name: x for x in insts}
    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = 0.001
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10
    field_cfg = cfg.get("field", {})
    if not isinstance(field_cfg, dict):
        raise PartError(t("{field_table} must be a table", field_table="[field]"))
    section = None
    timestep = model_timestep(cfg)
    if "range" in cfg:
        from ..testrange.build import parse_range_section
        section = parse_range_section(cfg, cfg_dir)
        if field_cfg.get("obstacle") is True:
            raise PartError(t("{field_table} {obstacle_setting}: the range's field elements replace the center "
                              "obstacle; remove the key or set it false", field_table="[field]",
                              obstacle_setting="obstacle = true"))
        if timestep is not None:   # the balls' contacts are built for the run's timestep
            spec.option.timestep = timestep
    add_field(spec, field_cfg, obstacle_default=section is None)

    pose = _start(cfg, section)
    root = insts[0]
    for inst in insts:
        child = part_spec(inst.part, inst.where)
        root_body = child.body(inst.part.bodies[0].name)
        oq = np.array(quat_from_rpy(inst.rot))
        if inst is root:
            q = quat_from_rpy([0, 0, float(pose[2])])
            # pos is in the start-pose frame (heading only); rot is applied below it
            at = np.array([float(pose[0]), float(pose[1]), 0.0]) + _rot(inst.pos, q)
            frame = spec.worldbody.add_frame(pos=list(at), quat=list(_qmul(q, oq)))
            frame.attach_body(root_body, f"{inst.name}.", "")
            attached = spec.body(f"{inst.name}.{inst.part.bodies[0].name}")
            attached.add_freejoint(name=f"{inst.name}.free")
        else:
            body_name, mpos, mquat = _target_frame(inst, by_name)
            pos = mpos + _rot(inst.pos, mquat)
            frame = spec.body(body_name).add_frame(pos=list(pos), quat=list(_qmul(mquat, oq)))
            frame.attach_body(root_body, f"{inst.name}.", "")
        for lp in inst.part.loops:   # data[0:3]: the pin, in body1's frame; MuJoCo finds body2's anchor from qpos0
            spec.add_equality(type=mujoco.mjtEq.mjEQ_CONNECT, name1=f"{inst.name}.{lp.body1}",
                              name2=f"{inst.name}.{lp.body2}", objtype=mujoco.mjtObj.mjOBJ_BODY,
                              data=[*lp.pos] + [0] * (mujoco.mjNEQDATA - 3), solref=[0.002, 1])
        _add_couplings(spec, inst.name, inst.part)
    cylinders = _add_cylinders(spec, insts, by_name, timestep if timestep is not None else spec.option.timestep)
    layout = None
    if section is not None:
        from ..testrange.build import add_range
        layout = add_range(spec, section.range, section.capacity, floor_balls=section.floor_balls)
    partner = None
    if "partner" in cfg:
        from .partner import add_partner, check_start, floor_pieces
        if "partner" in by_name:
            at = by_name["partner"].where
            raise PartError(t("{where}: the part name 'partner' is the {partner_table} robot's; rename the part",
                              where=at, partner_table="[partner]"),
                            where=at, field="name")
        partner = add_partner(spec, cfg)
        check_start(partner, pose, section.range.elements if section is not None else (),
                    floor_pieces(section.range) if section is not None and section.floor_balls else ())
    try:
        model = spec.compile()
    except ValueError as e:
        raise PartError(t("assembling the robot: {problem}", problem=e)) from None
    if layout is not None:     # qpos addresses exist only once compiled
        from ..testrange.build_elements import fill_layout
        fill_layout(layout, model, section.range.elements)
    rb = root.part.bodies[0]   # the free joint sits on the root body, so set_robot_pose needs its own pos/rot too
    rq = np.array(quat_from_rpy(root.rot))
    built = Built(model, spec, f"{root.name}.{rb.name}", insts,
                  root_offset=(tuple(np.array(root.pos) + _rot(rb.pos, rq)),
                               tuple(_qmul(rq, quat_from_rpy(rb.rot)))),
                  range=None if section is None else section.range, layout=layout, partner=partner)
    _set_treads(built, timestep if timestep is not None else spec.option.timestep)
    _resolve_cylinders(built, cylinders)
    from ..bindings import not_modeled
    left_out = not_modeled(cfg)[1]      # slots unbound on purpose: no "has no device ID" for them
    for inst in insts:
        b, w = slot_bindings(inst)
        for k, v in b.items():
            built.bindings[k].extend(v)
        built.warnings.extend(x for x in w if not any(f"{p}.{f.removeprefix('ids:')}" in left_out
                                                      for p, f in getattr(x, "fields", ())))
        built.warnings.extend(f"{inst.name}: {n}" for n in inst.part.warnings)
    _one_module_kind(built.bindings["valve"])
    return built


def _one_module_kind(valves: list[dict]) -> None:
    """One robot's valves are all on PCMs or all on Pneumatic Hubs (C10)."""
    first: dict[str, str] = {}
    for v in valves:
        first.setdefault(v["module"], v["name"])
    if len(first) > 1:
        (ka, a), (kb, b) = first.items()
        raise PartError(t("{a} is on a {kind_a} and {b} is on a {kind_b}: one robot's valves must all be on one "
                          "kind of module (all PCM or all PH)", a=a, kind_a=ka, b=b, kind_b=kb))
