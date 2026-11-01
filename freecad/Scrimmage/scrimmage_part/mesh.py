"""Body meshes (binary STL in meters, in the body frame) and wheel collision cylinders (runs inside FreeCAD).

Tessellation is `Shape.tessellate(LINEAR_DEFLECTION)`: it returns points already placed by the shape's Placement, in
mm, and is enough for a visual mesh (MuJoCo collides through the convex hull anyway). The STL is written here with
`struct` so the mm -> m scaling and the triangle normals are under our control.
"""

import math
import struct

from .mass import placement

LINEAR_DEFLECTION = 0.5      # mm


def _body_shapes(comps, body_frame):
    """Each component's whole shape, placed in the body frame (empty ones left out)."""
    to_body = placement(body_frame).inverse()
    out = []
    for c in comps:
        if c.shape is None or c.shape.isNull():
            continue
        s = c.shape.copy()
        s.Placement = to_body * c.placement * s.Placement
        out.append(s)
    return out


def _triangles(comps, body_frame):
    """([(x, y, z) mm], [(i, j, k)]) of the body's tessellation in the body frame."""
    pts, tris = [], []
    for s in _body_shapes(comps, body_frame):
        p, t = s.tessellate(LINEAR_DEFLECTION)
        base = len(pts)
        pts += [(v.x, v.y, v.z) for v in p]
        tris += [(a + base, b + base, c + base) for a, b, c in t]
    return pts, tris


def write_body_stl(comps, body_frame, path_stl):
    """Write the body's components (read.Comp) as one binary STL at `path_stl`, in meters, in the body frame
    (pos_mm, quat_wxyz in Assembly coordinates). Returns the number of triangles (0: nothing written, no geometry)."""
    pts, tris = _triangles(comps, body_frame)
    if not tris:
        return 0
    m = [(x * 1e-3, y * 1e-3, z * 1e-3) for x, y, z in pts]
    with open(path_stl, "wb") as f:
        f.write(b"scrimmage body mesh (m)".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            p, q, r = m[a], m[b], m[c]
            u = [q[i] - p[i] for i in range(3)]
            v = [r[i] - p[i] for i in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            ln = math.sqrt(sum(x * x for x in n)) or 1.0
            f.write(struct.pack("<12fH", *(x / ln for x in n), *p, *q, *r, 0))
    return len(tris)


def wheel_cylinder(comps, body_frame, axis_body):
    """(radius m, width m, center m) of the cylinder enclosing the body's shape about the line through the body
    origin along `axis_body` (a direction in the body frame: the wheel's joint axis). The center lies on that line,
    halfway across the shape's extent along it. Taken from tessellation vertices, which lie on the surface (circles'
    vertices exactly on the rim)."""
    ln = math.sqrt(sum(x * x for x in axis_body))
    ax = [x / ln for x in axis_body]
    pts, _ = _triangles(comps, body_frame)
    if not pts:
        return 0.0, 0.0, (0.0, 0.0, 0.0)
    along = [sum(p[i] * ax[i] for i in range(3)) for p in pts]
    radius = max(math.sqrt(max(sum(x * x for x in p) - t * t, 0.0)) for p, t in zip(pts, along))
    lo, hi = min(along), max(along)
    mid = (lo + hi) / 2
    return radius * 1e-3, (hi - lo) * 1e-3, tuple(mid * x * 1e-3 for x in ax)
