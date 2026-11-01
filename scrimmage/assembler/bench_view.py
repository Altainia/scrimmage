"""A compiled bench as JSON for the 3D view: its shapes, inlets and outlets at their start pose (no pieces)."""

from __future__ import annotations

import mujoco

from ..bench.build import Built
from .view import _TYPES, _mesh, _quat


def _instance(geom_name: str, shape: str) -> str:
    """The shape's dotted entry key. Geoms are named `<section>:<name>[:...]`; a CAD part's belong to `cad.<name>`."""
    parts = geom_name.split(":")
    return f"cad.{parts[1]}" if parts[0] == "cad" else f"{parts[0]}.{shape}"


def bench_view(built: Built) -> dict:
    m, lay = built.model, built.layout
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    meshes: list[dict] = []
    remap: dict[int, int] = {}
    geoms = []
    for g, shape in sorted(lay.shape_of_geom.items()):
        kind = _TYPES.get(int(m.geom_type[g]), "box")
        idx = None
        if kind == "mesh":
            did = int(m.geom_dataid[g])
            if did not in remap:
                remap[did] = len(meshes)
                meshes.append(_mesh(m, did))
            idx = remap[did]
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
        geoms.append({"instance": _instance(name, shape), "type": kind, "size": [float(v) for v in m.geom_size[g]],
                      "pos": [float(v) for v in d.geom_xpos[g]], "quat": _quat(d.geom_xmat[g]),
                      "rgba": [float(v) for v in m.geom_rgba[g]], "mesh": idx})
    boxes = [{"instance": f"{kind}.{name}", "lo": [float(v) for v in lo], "hi": [float(v) for v in hi]}
             for kind, table in (("inlet", lay.inlet_boxes), ("outlet", lay.outlet_boxes))
             for name, (lo, hi) in table.items()]
    lo, hi = lay.bounds
    return {"geoms": geoms, "meshes": meshes, "boxes": boxes,
            "bounds": {"lo": [float(v) for v in lo], "hi": [float(v) for v in hi]}}
