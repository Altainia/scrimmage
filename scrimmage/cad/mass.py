"""Materials and mass properties of triangle meshes (numpy only).

Units are SI: meters, kilograms, kg/m^3, kg m^2. A mesh's volume integrals are summed over signed tetrahedra from
the origin, so a closed mesh with flipped winding (inside out) gives the same mass as the correct one: a negative
signed volume is flipped. An open mesh has no volume and weighs nothing (`is_closed` says which). The
`principal`/`rpy_deg` math is the FreeCAD add-on's (`freecad/Scrimmage/scrimmage_part/mass.py`) ported to numpy
and tested against it.
"""

import math
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from ..i18n import N_
from . import CadError


@dataclass(frozen=True)
class Material:
    key: str
    label: str          # the user-facing name (N_(): shown with i18n.translated())
    density: float      # kg/m^3, solid
    printed: bool = False


MATERIALS: dict[str, Material] = {m.key: m for m in (
    Material("aluminum", N_("Aluminum 6061"), 2700.0),      # same as the FreeCAD exporter's DEFAULT_DENSITY
    Material("steel", N_("Steel"), 7850.0),
    Material("polycarbonate", N_("Polycarbonate"), 1200.0),
    Material("hdpe", N_("HDPE"), 950.0),
    Material("plywood", N_("Plywood"), 600.0),
    Material("carbon_fiber", N_("Carbon fiber"), 1550.0),
    Material("nylon", N_("Nylon"), 1150.0),
    Material("pla", N_("PLA (3D printed)"), 1240.0, printed=True),
    Material("petg", N_("PETG (3D printed)"), 1270.0, printed=True),
)}

DEFAULT_INFILL = 0.4
SHELL_SHARE = 0.25      # a printed part is a solid shell (25% of the plastic) plus infill for the rest


def density(material: str, infill: float | None = None, custom: float | None = None) -> float:
    """Effective density (kg/m^3) of `material` (a MATERIALS key, or "custom" with `custom` > 0). Printed
    materials are `rho * (0.25 + 0.75 * infill)`, with infill (0 to 1) defaulting to 0.4."""
    if material == "custom":
        if custom is None or not math.isfinite(custom) or custom <= 0:
            raise CadError("A custom material needs a density above zero (kg per cubic meter): enter one.")
        return float(custom)
    mat = MATERIALS.get(material)
    if mat is None:
        raise CadError(f"Unknown material '{material}': pick one from the list.")
    if not mat.printed:
        return mat.density
    fill = DEFAULT_INFILL if infill is None else infill
    if not math.isfinite(fill) or not 0 <= fill <= 1:
        raise CadError("Infill must be between 0 and 100 percent: enter a smaller number.")
    return mat.density * (SHELL_SHARE + (1 - SHELL_SHARE) * fill)


class Inertial(NamedTuple):
    mass: float
    com: np.ndarray         # (3,) in the mesh's frame
    tensor: np.ndarray      # (3, 3) about the com, in the mesh's frame


def is_closed(f: np.ndarray) -> bool:
    """Whether every edge is shared by exactly two triangles (by vertex index)."""
    f = np.asarray(f)
    if len(f) == 0:
        return False
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    # One integer per edge: np.unique over rows is an order of magnitude slower.
    _, counts = np.unique(edges[:, 0].astype(np.int64) * (int(edges.max()) + 1) + edges[:, 1], return_counts=True)
    return bool(np.all(counts == 2))


def mesh_inertial(v: np.ndarray, f: np.ndarray, density: float) -> Inertial:
    """Mass, center of mass and inertia tensor about it of the solid enclosed by mesh (v, f) at `density`.
    Open or empty meshes give mass 0 (com at the vertex mean, zero tensor)."""
    v = np.asarray(v, dtype=float)
    f = np.asarray(f, dtype=int)
    zero = Inertial(0.0, v.mean(axis=0) if len(v) else np.zeros(3), np.zeros((3, 3)))
    if len(f) == 0 or not is_closed(f):
        return zero
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    det = np.einsum("ij,ij->i", a, np.cross(b, c))          # 6 x signed tetra volume from the origin
    if det.sum() < 0:
        det = -det                                          # inside out
    vol = det.sum() / 6
    if vol <= 0:
        return zero
    com = ((a + b + c) * (det / 24)[:, None]).sum(axis=0) / vol     # tetra centroid (a+b+c)/4, volume det/6

    # Second moments about the origin: sum over tetra (0,a,b,c) of det/120 * (sum_i v_i v_i^T + S S^T), S = a+b+c.
    s = a + b + c
    outer = (np.einsum("ni,nj->nij", a, a) + np.einsum("ni,nj->nij", b, b) + np.einsum("ni,nj->nij", c, c)
             + np.einsum("ni,nj->nij", s, s))
    second = (outer * (det / 120)[:, None, None]).sum(axis=0)       # integral of x x^T over the solid
    second_c = second - vol * np.outer(com, com)                    # about the com
    tensor = density * (np.trace(second_c) * np.eye(3) - second_c)
    return Inertial(density * vol, com, (tensor + tensor.T) / 2)


def combine(items: list[Inertial]) -> Inertial:
    """One Inertial for several (parallel-axis theorem about the combined center of mass)."""
    total = sum(i.mass for i in items)
    if total <= 0:
        return Inertial(0.0, np.zeros(3), np.zeros((3, 3)))
    com = sum(i.mass * np.asarray(i.com, dtype=float) for i in items) / total
    tensor = np.zeros((3, 3))
    for i in items:
        d = np.asarray(i.com, dtype=float) - com
        tensor += i.tensor + i.mass * (d @ d * np.eye(3) - np.outer(d, d))
    return Inertial(float(total), com, tensor)


def principal(tensor) -> tuple[np.ndarray, np.ndarray]:
    """(ascending principal moments, R) with tensor = R diag(moments) R^T and R a proper rotation (det +1)."""
    w, r = np.linalg.eigh(np.asarray(tensor, dtype=float))     # ascending
    r = r.copy()
    if np.linalg.det(r) < 0:
        r[:, 2] = -r[:, 2]
    return np.maximum(w, 0.0), r


def rpy_deg(r) -> tuple[float, float, float]:
    """Roll, pitch, yaw (degrees) of rotation matrix r = Rz(yaw) Ry(pitch) Rx(roll): rotations about the fixed x,
    y, z axes applied in that order (the same convention as the FreeCAD add-on's `rpy_deg`)."""
    sp = max(-1.0, min(1.0, -float(r[2][0])))
    pitch = math.asin(sp)
    if abs(sp) > 1 - 1e-12:                         # gimbal lock: only yaw -/+ roll is defined; take roll 0
        roll, yaw = 0.0, math.atan2(-r[0][1], r[1][1])
    else:
        roll, yaw = math.atan2(r[2][1], r[2][2]), math.atan2(r[1][0], r[0][0])
    return tuple(math.degrees(x) for x in (roll, pitch, yaw))
