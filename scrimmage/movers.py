"""Movers: the bodies and static geoms a live view animates, and their poses each frame."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import mujoco
import numpy as np


@dataclass(frozen=True)
class Mover:
    kind: Literal["body", "geom"]
    name: str
    id: int          # MuJoCo body id or geom id, by kind


def movers(world) -> list[Mover]:
    """Bodies not welded to the world (the robot's, free game pieces, their children) in id order, then the
    named static geoms that `World.movable_objects()` lists (moved by `move_object`), in name order."""
    m = world.model
    out = [Mover("body", mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or f"body{b}", b)
           for b in range(1, m.nbody) if int(m.body_weldid[b]) != 0]
    movable = set(world.movable_objects())
    geoms = []
    for g in range(m.ngeom):
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
        if name and name in movable and int(m.geom_bodyid[g]) == 0:
            geoms.append(Mover("geom", name, g))
    return out + sorted(geoms, key=lambda mv: mv.name)


def mover_poses(world, ms: list[Mover]) -> np.ndarray:
    """(n, 7) float32 rows x, y, z, qw, qx, qy, qz in the world frame. A soft NOTE's rigid stand-in, while its ring is
    in use (testrange/soft.py SoftPool.drawn_parked), is drawn parked: at PARK_Z under where it is, so the view
    (which hides movers parked under the floor) shows the ring alone."""
    d = world.data
    out = np.zeros((len(ms), 7), dtype=np.float32)
    pool = getattr(getattr(world, "test_range", None), "soft", None)
    parked = pool.drawn_parked() if pool is not None else ()
    if parked:
        from .testrange.build import PARK_Z
    for i, mv in enumerate(ms):
        if mv.kind == "body":
            out[i, :3], out[i, 3:] = d.xpos[mv.id], d.xquat[mv.id]
            if mv.id in parked:
                out[i, 2] = PARK_Z
        else:
            q = np.zeros(4)
            mujoco.mju_mat2Quat(q, d.geom_xmat[mv.id])
            out[i, :3], out[i, 3:] = d.geom_xpos[mv.id], q
    return out
