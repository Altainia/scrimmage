"""The compiled MuJoCo model as JSON for the 3D view (start pose; robot geoms and its CG, optionally the world's geoms too)."""

from __future__ import annotations

import base64
import math

import mujoco
import numpy as np

from ..movers import movers as _movers
from ..parts.model import quat_from_rpy

IN = 0.0254     # m per inch

_TYPES = {int(mujoco.mjtGeom.mjGEOM_BOX): "box", int(mujoco.mjtGeom.mjGEOM_CYLINDER): "cylinder",
          int(mujoco.mjtGeom.mjGEOM_SPHERE): "sphere", int(mujoco.mjtGeom.mjGEOM_CAPSULE): "capsule",
          int(mujoco.mjtGeom.mjGEOM_ELLIPSOID): "ellipsoid", int(mujoco.mjtGeom.mjGEOM_MESH): "mesh",
          int(mujoco.mjtGeom.mjGEOM_PLANE): "plane"}


def _quat(xmat) -> list[float]:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.asarray(xmat, float).reshape(9))
    return [float(v) for v in q]


def _mesh(m, i: int) -> dict:
    v0, vn = int(m.mesh_vertadr[i]), int(m.mesh_vertnum[i])
    f0, fn = int(m.mesh_faceadr[i]), int(m.mesh_facenum[i])
    vert = np.ascontiguousarray(m.mesh_vert[v0:v0 + vn], dtype="<f4")
    face = np.ascontiguousarray(m.mesh_face[f0:f0 + fn], dtype="<i4")
    return {"vert": base64.b64encode(vert.tobytes()).decode(), "face": base64.b64encode(face.tobytes()).decode()}


def model_view(world, include_world: bool = False) -> dict:
    m = world.model
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    root = int(m.body_rootid[world.robot_body])
    ms = _movers(world)
    body_mover = {mv.id: i for i, mv in enumerate(ms) if mv.kind == "body"}
    meshes: list[dict] = []
    remap: dict[int, int] = {}
    geoms = []
    for g in range(m.ngeom):
        if int(m.body_rootid[m.geom_bodyid[g]]) != root or int(m.geom_bodyid[g]) == 0:
            continue
        kind = _TYPES.get(int(m.geom_type[g]), "box")
        idx = None
        if kind == "mesh":
            did = int(m.geom_dataid[g])
            if did not in remap:
                remap[did] = len(meshes)
                meshes.append(_mesh(m, did))
            idx = remap[did]
        b = int(m.geom_bodyid[g])
        if b in body_mover:
            mover, lpos, lquat = body_mover[b], [float(v) for v in m.geom_pos[g]], [float(v) for v in m.geom_quat[g]]
        else:       # a body welded to the world (a robot with no free joint) never moves: drawn at its world pose
            mover, lpos, lquat = -1, [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]
        geoms.append({"instance": _instance_of(world, int(m.geom_bodyid[g])), "type": kind,
                      "size": [float(v) for v in m.geom_size[g]], "pos": [float(v) for v in d.geom_xpos[g]],
                      "quat": _quat(d.geom_xmat[g]), "rgba": [float(v) for v in m.geom_rgba[g]], "mesh": idx,
                      "mover": mover, "lpos": lpos, "lquat": lquat})
    instances = world.parts.instances if world.parts is not None else []   # none for a hand-written robot
    mounts = []
    for inst in instances:
        for mt in inst.part.mounts:
            b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, f"{inst.name}.{mt.body}")
            bq = d.xquat[b]
            pos, q = np.zeros(3), np.zeros(4)
            mujoco.mju_rotVecQuat(pos, np.asarray(mt.pos, float), bq)
            mujoco.mju_mulQuat(q, bq, np.asarray(quat_from_rpy(mt.rot), float))
            mounts.append({"name": f"{inst.name}.{mt.name}", "pos": [float(v) for v in d.xpos[b] + pos],
                           "quat": [float(v) for v in q]})
    floor = {"pos": [8.27, 4.035, 0.0], "size": [8.27, 4.035]}
    for g in range(m.ngeom):
        if int(m.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_PLANE):
            floor = {"pos": [float(v) for v in m.geom_pos[g]], "size": [float(v) for v in m.geom_size[g][:2]]}
            break
    out = {"instances": [i.name for i in instances], "geoms": geoms, "meshes": meshes,
           "mounts": mounts, "floor": floor, "movers": [{"kind": mv.kind, "name": mv.name} for mv in ms],
           "root_mover": body_mover.get(world.robot_body, -1),   # the robot body's index in `movers` (-1: not a mover)
           "com": [float(v) for v in d.subtree_com[world.robot_body]],   # the robot's CG in the world (the CG marker)
           "cylinders": _cylinders(world, d, body_mover),
           "sensors": _sensors(world, d, body_mover)}
    rng = world.test_range.range if world.test_range is not None else None
    layout = rng.apriltags if rng is not None else None
    out["apriltags"] = [] if layout is None else [
        {"id": t.id, "face": list(t.face), "quat": list(t.quat), "size": layout.size} for t in layout.tags]
    if include_world:
        out["world_geoms"] = _world_geoms(world, d, root, ms, meshes, remap)
        out["movable"] = world.movable_objects()
    return out


def _cylinders(world, d, body_mover: dict[int, int]) -> list[dict]:
    """Each pneumatic cylinder (parts/assemble.py Built.cylinders) for the live view to draw between its pins:
    {name, a, b, bore, rod, retracted, stroke}, sizes in m. A pin is [mover, pos]: pos in that mover's frame, as a
    geom's lpos; or, on a body that never moves (mover -1), its fixed world position."""
    m = world.model
    out = []
    for cy in (world.parts.cylinders if world.parts is not None else []):
        pins = []
        for key in ("pin_a", "pin_b"):
            site = m.site(f"{cy.name}.{key}").id
            mover = body_mover.get(int(m.site_bodyid[site]), -1)
            pos = m.site_pos[site] if mover >= 0 else d.site_xpos[site]
            pins.append([mover, [float(v) for v in pos]])
        out.append({"name": cy.name, "a": pins[0], "b": pins[1], "bore": cy.bore, "rod": cy.rod,
                    "retracted": float(cy.length), "stroke": cy.stroke})
    return out


def _sensors(world, d, body_mover: dict[int, int]) -> list[dict]:
    """Each [[sensor]] (sensors/spec.py) for the replay to draw as a line, lit while on: {name, kind, a, b}, and a
    distance sensor's threshold_in (it is on closer than that). A beam break's line runs from its `from` to its `to`; a
    distance or color sensor's from `at` along `toward`, max_range long. a and b are pins as _cylinders' are: [mover,
    pos], pos in that mover's frame; or, on a body that never moves (mover -1), its fixed world position. A camera's
    entry adds hfov_deg, vfov_deg, max_range, reach (how far it sees the range's tags: camera_reach) and `corners`:
    the four far corners of its view (pins, reach out along each edge of its fields of view; up is the body's z), which
    the replay draws as its view cone."""
    if getattr(world, "sensors", None) is None:
        return []
    m = world.model
    out = []
    for s in world.sensors.specs:
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, s.body)
        mover = body_mover.get(b, -1)
        start = np.asarray(s.start, float)

        def pin(local):
            pos = local if mover >= 0 else d.xpos[b] + d.xmat[b].reshape(3, 3) @ local
            return [mover, [float(v) for v in pos]]
        pins = [pin(local) for local in (start, start + s.length * np.asarray(s.direction, float))]
        entry = {"name": s.name, "kind": s.kind, "a": pins[0], "b": pins[1]}
        if s.kind == "distance":
            entry["threshold_in"] = s.threshold / IN
        if s.kind == "camera":
            reach = camera_reach(world, s)
            entry.update(hfov_deg=s.hfov_deg, vfov_deg=s.vfov_deg, max_range=s.length, reach=reach,
                         corners=[pin(start + reach * edge) for edge in _view_edges(s)])
        out.append(entry)
    return out


def camera_reach(world, s) -> float:
    """How far camera `s` sees the range's tags (m): max_range, scaled by the layout's tag size (R7: max_range is for
    6.5 in tags); max_range itself on a range with no tags."""
    from ..sensors.camera import reach
    rng = world.parts.range if getattr(world, "parts", None) is not None else None
    layout = rng.apriltags if rng is not None else None
    return s.length if layout is None else reach(s, layout.size)


def _view_edges(s) -> list[np.ndarray]:
    """A camera's four view edges, unit vectors in its body's frame: forward turned by half of each field of view
    (left and up, left and down, right and down, right and up), up being the body's z made square to forward."""
    f = np.asarray(s.direction, float)
    z = np.array([0.0, 0.0, 1.0])
    up = z - (z @ f) * f
    up /= np.linalg.norm(up)
    left = np.cross(up, f)
    h, v = math.tan(math.radians(s.hfov_deg / 2.0)), math.tan(math.radians(s.vfov_deg / 2.0))
    out = []
    for sl, su in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
        e = f + sl * h * left + su * v * up
        out.append(e / np.linalg.norm(e))
    return out


def _world_geoms(world, d, root: int, ms, meshes: list[dict], remap: dict[int, int]) -> list[dict]:
    """The field: every geom outside the robot's body tree, as {name, type, size, pos, quat, rgba}.

    `type` is plane (size = [half_x, half_y]), box (size = [half_x, half_y, half_z]), cylinder (size = [radius,
    half_height, 0]), sphere (size = [radius, 0, 0]), capsule (size = [radius, half_length, 0]) or ellipsoid
    (size = 3 semi-axes). `pos`/`quat` (w, x, y, z) are world-frame at the start pose; `name` is "" for unnamed geoms.
    `mover` indexes the view's `movers` (-1: static); `lpos`/`lquat` are the geom's pose in its mover's frame
    (identity for static geoms and for geom movers). A range piece's mesh geoms (a coral's slices: bodies `ball.<i>`)
    are `mesh`, `mesh` indexing the view's `meshes` (added to `meshes` and `remap`, shared with the robot's); other
    mesh and height-field geoms are skipped."""
    m = world.model
    body_mover = {mv.id: i for i, mv in enumerate(ms) if mv.kind == "body"}
    geom_mover = {mv.id: i for i, mv in enumerate(ms) if mv.kind == "geom"}
    layout = world.parts.layout if world.parts is not None else None
    probes = {c.probe for c in layout.chains.values()} if layout is not None else set()   # a chain's: never drawn
    out = []
    for g in range(m.ngeom):
        if int(m.body_rootid[m.geom_bodyid[g]]) == root or g in probes:
            continue
        kind = _TYPES.get(int(m.geom_type[g]))
        b = int(m.geom_bodyid[g])
        if kind is None:        # hfields and the like have no simple drawing
            continue
        idx = None
        if kind == "mesh":      # a range piece's or a mesh element's (the robot's are model_view's own); no others
            if not (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or "").startswith(("ball.", "element.")):
                continue
            did = int(m.geom_dataid[g])
            if did not in remap:
                remap[did] = len(meshes)
                meshes.append(_mesh(m, did))
            idx = remap[did]
        size = [float(v) for v in m.geom_size[g]]
        if kind == "plane":
            size = size[:2]
        if b in body_mover:
            mover, lpos, lquat = body_mover[b], [float(v) for v in m.geom_pos[g]], [float(v) for v in m.geom_quat[g]]
        else:       # a movable static geom is its own mover; the rest of the field never moves
            mover, lpos, lquat = geom_mover.get(g, -1), [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]
        geom = {"name": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or "", "type": kind, "size": size,
                "pos": [float(v) for v in d.geom_xpos[g]], "quat": _quat(d.geom_xmat[g]),
                "rgba": [float(v) for v in m.geom_rgba[g]], "mover": mover, "lpos": lpos, "lquat": lquat}
        if idx is not None:
            geom["mesh"] = idx
        out.append(geom)
    return out


def _instance_of(world, body: int) -> str:
    name = mujoco.mj_id2name(world.model, mujoco.mjtObj.mjOBJ_BODY, body) or ""
    return name.split(".", 1)[0]
