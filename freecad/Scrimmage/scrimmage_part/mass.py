"""Mass, center of mass and principal inertia of a body, from its components' solids (runs inside FreeCAD).

Units: FreeCAD works in mm; results are SI (kg, m, kg m^2). Probed in FreeCAD 1.1: a solid's `MatrixOfInertia` is
about its own center of mass, for density 1, in mm^5 (a 100x40x20 box gives Ixx = 80000 * (40^2 + 20^2) / 12), and,
like `CenterOfMass`, it honors the shape's Placement (it comes back in the axes the shape is placed in). A compound
has neither, so each solid is taken on its own.
"""

import math

from .errors import ExportError

DEFAULT_DENSITY = 2700.0     # kg/m^3: aluminum, for components with no material (or FreeCAD's "Default")


def placement(frame):
    """A (pos_mm, quat_wxyz) frame as a FreeCAD Placement."""
    import FreeCAD
    (x, y, z), (qw, qx, qy, qz) = frame
    return FreeCAD.Placement(FreeCAD.Vector(x, y, z), FreeCAD.Rotation(qx, qy, qz, qw))


def _solids(comp, to_body):
    """[(mass kg, com m, inertia about com kg m^2)] for each solid of `comp`, in the body frame (`to_body` maps the
    Assembly frame to the body frame), at the solid's density."""
    out = []
    for shape, density in comp.pieces:
        rho = density or DEFAULT_DENSITY
        for solid in shape.Solids:
            s = solid.copy()
            s.Placement = to_body * comp.placement * s.Placement
            vol = s.Volume
            if vol <= 0:
                continue
            c = s.CenterOfMass
            moi = s.MatrixOfInertia
            k = rho * 1e-9 * 1e-6                       # kg/mm^3, then mm^2 -> m^2
            out.append((vol * rho * 1e-9, (c.x * 1e-3, c.y * 1e-3, c.z * 1e-3),
                        [[moi.A[4 * i + j] * k for j in range(3)] for i in range(3)]))
    return out


def lost_volume(comp):
    """Why part of component `comp` (read.Comp) adds no mass, as words following its name, or None: faces or shells
    that aren't closed solids, or a solid with no volume (inside out or broken). Edges and points never count."""
    loose = bad = 0
    for shape, _ in comp.pieces:
        if shape is None or shape.isNull():
            continue
        solids = shape.Solids
        bad += sum(1 for s in solids if s.Volume <= 0)
        loose += len(shape.Faces) - sum(len(s.Faces) for s in solids)
    if loose > 0:
        return "has faces or shells that aren't closed solids"
    if bad:
        return "has a solid with no volume (inside out or broken)"
    return None


def body_inertial(comps, overrides, body_frame):
    """(mass kg, com m, principal moments kg m^2, inertia_rot (roll, pitch, yaw) degrees) of the body made of
    `comps` (read.Comp), all in the body frame `body_frame` (pos_mm, quat_wxyz in Assembly coordinates).

    Density is the component's material's, else aluminum. `overrides` maps a component's internal name to its mass
    in kg: it replaces the computed mass, the volume still shaping the inertia (a component with no volume becomes a
    point mass at its origin). Principal moments are ascending; `inertia_rot` (scrimmage's convention: about the
    fixed x, y, z axes in that order) turns the body frame into the principal axes, so the tensor is
    R diag(moments) R^T. Raises ExportError when the total mass is zero."""
    to_body = placement(body_frame).inverse()
    items = []
    for c in comps:
        parts = _solids(c, to_body)
        if c.name in overrides:
            want = float(overrides[c.name])
            have = sum(p[0] for p in parts)
            if have > 0:
                k = want / have
                parts = [(m * k, com, [[v * k for v in row] for row in i]) for m, com, i in parts]
            else:
                o = (to_body * c.placement).Base
                parts = [(want, (o.x * 1e-3, o.y * 1e-3, o.z * 1e-3), [[0.0] * 3 for _ in range(3)])]
        items += parts

    total = sum(p[0] for p in items)
    if total <= 0:
        names = ", ".join(f"'{c.label}'" for c in comps) or "(none)"
        raise ExportError([f"body made of {names} has no mass (no solid volume): give one of its components a "
                           f"mass override"])
    com = tuple(sum(m * cm[n] for m, cm, _ in items) / total for n in range(3))
    tensor = [[0.0] * 3 for _ in range(3)]
    for m, cm, i in items:
        d = [cm[n] - com[n] for n in range(3)]
        dd = sum(v * v for v in d)
        for a in range(3):
            for b in range(3):
                tensor[a][b] += i[a][b] + m * ((dd if a == b else 0.0) - d[a] * d[b])
    moments, r = principal(tensor)
    return total, com, moments, rpy_deg(r)


def jacobi(a, sweeps=50):
    """Eigen-decomposition of a symmetric 3x3: (eigenvalues, V) with a = V diag(eigenvalues) V^T, V's columns the
    eigenvectors (cyclic Jacobi rotations)."""
    a = [list(map(float, row)) for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    scale = max(abs(a[i][j]) for i in range(3) for j in range(3))
    for _ in range(sweeps):
        off = math.sqrt(sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j))
        if off <= 1e-15 * scale or scale == 0:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if a[p][q] == 0.0:
                continue
            theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
            t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1))
            c = 1 / math.sqrt(t * t + 1)
            s = t * c
            for k in range(3):                      # a <- a J (columns p, q)
                akp, akq = a[k][p], a[k][q]
                a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
            for k in range(3):                      # a <- J^T a (rows p, q)
                apk, aqk = a[p][k], a[q][k]
                a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
            for k in range(3):                      # v <- v J
                vkp, vkq = v[k][p], v[k][q]
                v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    return [a[i][i] for i in range(3)], v


def principal(tensor):
    """(ascending principal moments, R) with tensor = R diag(moments) R^T and R a proper rotation (det +1)."""
    w, v = jacobi(tensor)
    order = sorted(range(3), key=lambda i: w[i])
    moments = [max(w[i], 0.0) for i in order]
    r = [[v[row][i] for i in order] for row in range(3)]
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1]) - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    if det < 0:
        for row in r:
            row[2] = -row[2]
    return moments, r


def rpy_deg(r):
    """Roll, pitch, yaw (degrees) of rotation matrix r = Rz(yaw) Ry(pitch) Rx(roll): rotations about the fixed x,
    y, z axes applied in that order (scrimmage's `quat_from_rpy`)."""
    sp = max(-1.0, min(1.0, -r[2][0]))
    pitch = math.asin(sp)
    if abs(sp) > 1 - 1e-12:                         # gimbal lock: only yaw -/+ roll is defined; take roll 0
        roll, yaw = 0.0, math.atan2(-r[0][1], r[1][1])
    else:
        roll, yaw = math.atan2(r[2][1], r[2][2]), math.atan2(r[1][0], r[0][0])
    return tuple(math.degrees(x) for x in (roll, pitch, yaw))
