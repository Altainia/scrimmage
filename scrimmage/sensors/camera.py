"""A `kind = "camera"` sensor (spec D6): which of the range's AprilTags it sees, checked by geometry, with no image.

At each camera frame (`fps` frames a second of sim time, and again after a poke: World._read_sensors forces one), a
tag is seen when all of these hold (`in_view`, then the ray):

  - its center is inside both fields of view, measured in the camera's frame: forward is `toward`, up is the body's z
    with its forward part taken out, left is up x forward. The horizontal angle is atan2(d.left, d.forward), the
    vertical atan2(d.up, d.forward), and both need d.forward > 0 (a tag behind the camera isn't seen);
  - it is within `max_range`, scaled by the range's tag size (R7: max_range is for 6.5 in tags, and a tag's pixel
    count goes with its size);
  - its face points toward the camera within `max_angle_deg`;
  - a ray from the camera toward the tag's face point (Tag.face, R6: its center pushed out of the solid it hangs
    on) stops 2 cm short of it without hitting anything that blocks the view.

The field of view, range and angle use the tag's true pose; only the ray uses its face point. Exactly at a limit
counts as seen.

What blocks the view: everything but the robot's own geoms, the tags' squares (`apriltag.*`, visual only) and the
four perimeter walls (testrange.spec.PERIMETER_WALLS), which are where tags are mounted. The ray excludes the camera's
own body and casts again from just past each of those it hits, as SensorRuntime._cast does, at most MAX_CASTS times.

Cost: one mj_ray per tag in view per frame (more only past robot surfaces), and nothing on a range with no tags.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from .runtime import MAX_CASTS, SKIP, Reading
from .spec import SensorSpec

SHORT = 0.02        # m: the ray stops this far short of the tag's face point (spec D6)
EDGE = 1e-9         # a limit's slack: a tag exactly at a field of view's edge, max_range or max_angle_deg is seen
IN = 0.0254
NOTHING = Reading(on=False, distance=None)


def in_view(cam_pos: np.ndarray, forward: np.ndarray, up: np.ndarray, spec: SensorSpec, tag, size: float
            ) -> float | None:
    """The distance (m) from the camera at `cam_pos` (unit `forward` and `up`, in the world) to `tag`'s center when
    the tag is inside both fields of view, within range for tags of side `size` (m) and faces the camera within
    max_angle_deg; else None. Pure geometry: no ray."""
    from ..testrange.apriltags import TAG_36H11_SIZE_IN
    d = np.asarray(tag.pos, float) - cam_pos
    dist = float(np.linalg.norm(d))
    if dist > spec.length * size / (TAG_36H11_SIZE_IN * IN) + EDGE:
        return None
    ahead = float(d @ forward)
    if ahead <= 0.0:
        return None
    left = np.cross(up, forward)
    if abs(math.degrees(math.atan2(float(d @ left), ahead))) > spec.hfov_deg / 2.0 + EDGE:
        return None
    if abs(math.degrees(math.atan2(float(d @ up), ahead))) > spec.vfov_deg / 2.0 + EDGE:
        return None
    facing = float(tag.normal @ -d) / dist
    if math.degrees(math.acos(max(-1.0, min(1.0, facing)))) > spec.max_angle_deg + EDGE:
        return None
    return dist


class CameraRuntime:
    """A robot's cameras on a range with `layout` (an AprilTagLayout, or None: every reading stays empty and update
    returns at once)."""

    def __init__(self, world, specs: list[SensorSpec], layout):
        from ..testrange.spec import PERIMETER_WALLS
        m = world.model
        self.world, self.model, self.specs, self.layout = world, m, list(specs), layout
        self.names = {s.name for s in self.specs}
        self._bodies = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, s.body) for s in self.specs]
        root = int(m.body_rootid[world.robot_body])
        names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or "" for g in range(m.ngeom)]
        # per geom: the ray steps past it (the robot's own, a tag's square, a perimeter wall)
        self._past = [int(m.body_rootid[m.geom_bodyid[g]]) == root or names[g].startswith("apriltag.")
                      or names[g] in PERIMETER_WALLS for g in range(m.ngeom)]
        self._start = [np.asarray(s.start, float) for s in self.specs]
        self._dir = [np.asarray(s.direction, float) for s in self.specs]
        self._period = [1.0 / s.fps for s in self.specs]
        self._last: list[float | None] = [None] * len(self.specs)     # each camera's last frame's time
        self._gid = np.zeros(1, dtype=np.int32)
        self._pnt = np.zeros(3)
        self.casts = 0                                  # mj_ray calls so far (a cost check)
        self._readings = {s.name: NOTHING for s in self.specs}

    def reading(self, name: str) -> Reading:
        return self._readings[name]

    def update(self, data: mujoco.MjData, force: bool = False) -> None:
        """A frame for each camera whose 1/fps has passed since its last one (every camera with `force`)."""
        if self.layout is None:
            return
        now = float(data.time)
        for i, s in enumerate(self.specs):
            last = self._last[i]
            if not force and last is not None and now < last + self._period[i] - EDGE:
                continue
            self._last[i] = now
            self._readings[s.name] = self._frame(data, i, s)

    def _frame(self, data, i: int, s: SensorSpec) -> Reading:
        b = self._bodies[i]
        rot = data.xmat[b].reshape(3, 3)
        cam = data.xpos[b] + rot @ self._start[i]
        forward = rot @ self._dir[i]
        z = rot[:, 2]
        up = z - (z @ forward) * forward
        up /= np.linalg.norm(up)
        seen = []
        for tag in self.layout.tags:
            dist = in_view(cam, forward, up, s, tag, self.layout.size)
            if dist is not None and self._clear(data, cam, np.asarray(tag.face, float), b):
                seen.append((tag.id, dist))
        seen.sort()
        return Reading(on=bool(seen), distance=None, tags=tuple(t for t, _ in seen),
                       tag_distances=tuple(d for _, d in seen))

    def _clear(self, data, cam: np.ndarray, face: np.ndarray, own_body: int) -> bool:
        """Nothing that blocks the view lies between the camera and SHORT before the tag's face point."""
        vec = face - cam
        reach = float(np.linalg.norm(vec)) - SHORT
        if reach <= 0.0:
            return True
        vec = vec / (reach + SHORT)
        m, gid, start, gone = self.model, self._gid, self._pnt, 0.0
        start[:] = cam
        for _ in range(MAX_CASTS):
            self.casts += 1
            x = mujoco.mj_ray(m, data, start, vec, None, 1, own_body, gid)
            if x < 0.0 or gone + x > reach:
                return True
            if not self._past[int(gid[0])]:
                return False
            gone += x + SKIP
            start[:] = cam + gone * vec
        return False        # the robot is in its way past MAX_CASTS surfaces: as a sensor, it sees nothing beyond
