"""The compiled MuJoCo model as JSON for the 3D view (start pose; robot geoms and its CG, optionally the world's geoms too)."""

from __future__ import annotations

import base64

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
    pos], pos in that mover's frame; or, on a body that never moves (mover -1), its fixed world position."""
    if getattr(world, "sensors", None) is None:
        return []
    m = world.model
    out = []
    for s in world.sensors.specs:
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, s.body)
        mover = body_mover.get(b, -1)
        start = np.asarray(s.start, float)
        pins = []
        for local in (start, start + s.length * np.asarray(s.direction, float)):
            pos = local if mover >= 0 else d.xpos[b] + d.xmat[b].reshape(3, 3) @ local
            pins.append([mover, [float(v) for v in pos]])
        entry = {"name": s.name, "kind": s.kind, "a": pins[0], "b": pins[1]}
        if s.kind == "distance":
            entry["threshold_in"] = s.threshold / IN
        out.append(entry)
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
    out = []
    for g in range(m.ngeom):
        if int(m.body_rootid[m.geom_bodyid[g]]) == root:
            continue
        kind = _TYPES.get(int(m.geom_type[g]))
        b = int(m.geom_bodyid[g])
        if kind is None:        # hfields and the like have no simple drawing
            continue
        idx = None
        if kind == "mesh":      # a range piece's (the robot's are model_view's own); the field's are left out
            if not (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or "").startswith("ball."):
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
