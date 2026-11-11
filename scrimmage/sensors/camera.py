"""A `kind = "camera"` sensor (spec D6): which of the range's AprilTags it sees, checked by geometry, with no image.

At each camera frame (`fps` frames a second of sim time, on an ideal schedule: frame k is due at k/fps from the
first, and runs at the first physics step at or after that, so the rate is `fps` whatever the timestep; and again
after a poke: World._read_sensors forces a frame, which leaves the schedule as it was), a tag is seen when all of these hold (`in_view`, then the ray):

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

What blocks the view: every solid but the robot's own geoms and the four perimeter walls
(testrange.spec.PERIMETER_WALLS), which are where tags are mounted. A geom that touches nothing (contype and
conaffinity 0: the tags' squares, zone tape, any visual-only decoration) isn't a solid and never blocks it; that is
read when the ray hits it, since a piece's bits change while it runs. The ray excludes the camera's own body and casts
again from just past each geom it steps past, as SensorRuntime._cast does, at most MAX_CASTS times; a camera that
runs out of them sees nothing beyond and is a warning in the world's warnings, once.

Cost: one mj_ray per tag in view per frame (more only past robot surfaces), and nothing on a range with no tags.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from .. import i18n
from ..checks import Said
from .runtime import MAX_CASTS, SKIP, Reading
from .spec import SensorSpec

SHORT = 0.02        # m: the ray stops this far short of the tag's face point (spec D6)
EDGE = 1e-9         # a limit's slack: a tag exactly at a field of view's edge, max_range or max_angle_deg is seen
IN = 0.0254
NOTHING = Reading(on=False, distance=None)


def reach(spec: SensorSpec, size: float) -> float:
    """How far (m) camera `spec` sees tags of side `size` (m): max_range is for 6.5 in tags, and a tag's size in pixels
    goes with its size (R7)."""
    from ..testrange.apriltags import TAG_36H11_SIZE_IN
    return spec.length * size / (TAG_36H11_SIZE_IN * IN)


def in_view(cam_pos: np.ndarray, forward: np.ndarray, up: np.ndarray, spec: SensorSpec, tag, size: float
            ) -> float | None:
    """The distance (m) from the camera at `cam_pos` (unit `forward` and `up`, in the world) to `tag`'s center when
    the tag is inside both fields of view, within range for tags of side `size` (m) and faces the camera within
    max_angle_deg; else None. Pure geometry: no ray."""
    d = np.asarray(tag.pos, float) - cam_pos
    dist = float(np.linalg.norm(d))
    if dist > reach(spec, size) + EDGE:
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
        # per geom: the ray steps past it (the robot's own, a perimeter wall, and, by name, a tag's square, which
        # touches nothing too: _steps_past)
        self._past = [int(m.body_rootid[m.geom_bodyid[g]]) == root or names[g].startswith("apriltag.")
                      or names[g] in PERIMETER_WALLS for g in range(m.ngeom)]
        self._start = [np.asarray(s.start, float) for s in self.specs]
        self._dir = [np.asarray(s.direction, float) for s in self.specs]
        self._period = [1.0 / s.fps for s in self.specs]
        self._due: list[float | None] = [None] * len(self.specs)      # when each camera's next frame is due
        self._out_of_casts: set[str] = set()            # the cameras already warned about running out of casts
        self._gid = np.zeros(1, dtype=np.int32)
        self._pnt = np.zeros(3)
        self.casts = 0                                  # mj_ray calls so far (a cost check)
        self._readings = {s.name: NOTHING for s in self.specs}

    def reading(self, name: str) -> Reading:
        return self._readings[name]

    def reset(self) -> None:
        """Start each camera's schedule again (the world's clock went back to 0): its next frame is due now."""
        self._due = [None] * len(self.specs)

    def update(self, data: mujoco.MjData, force: bool = False) -> None:
        """A frame for each camera whose next frame is due (every camera with `force`, leaving the schedule as it
        was). A camera's frames are due 1/fps apart from its first: a frame that runs a step late doesn't put the
        next one off, so the rate stays `fps`."""
        if self.layout is None:
            return
        now = float(data.time)
        for i, s in enumerate(self.specs):
            due = self._due[i]
            if due is None:                         # the first frame: the schedule starts here
                self._due[i] = now + self._period[i]
            elif now >= due - EDGE:
                due += self._period[i]
                self._due[i] = due if due > now + EDGE else now + self._period[i]   # far behind: start again
            elif not force:
                continue
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
            if dist is not None and self._clear(data, cam, np.asarray(tag.face, float), b, s.name):
                seen.append((tag.id, dist))
        seen.sort()
        return Reading(on=bool(seen), distance=None, tags=tuple(t for t, _ in seen),
                       tag_distances=tuple(d for _, d in seen))

    def _clear(self, data, cam: np.ndarray, face: np.ndarray, own_body: int, name: str) -> bool:
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
            if not self._steps_past(int(gid[0])):
                return False
            gone += x + SKIP
            start[:] = cam + gone * vec
        self._warn_out_of_casts(name)
        return False        # past MAX_CASTS geoms it steps past, it sees nothing beyond, as a sensor does

    def _steps_past(self, g: int) -> bool:
        """The ray goes on past geom g: the robot's own, a perimeter wall, or a geom that touches nothing (read now:
        a piece's bits change while it runs)."""
        m = self.model
        return self._past[g] or (int(m.geom_contype[g]) == 0 and int(m.geom_conaffinity[g]) == 0)

    def _warn_out_of_casts(self, name: str) -> None:
        if name in self._out_of_casts:
            return
        self._out_of_casts.add(name)
        # made on the simulation's thread: machine text (report.json, MCP state), worded for people when shown (Said)
        self.world.warnings.append(Said(
            f'[[sensor]] "{name}": too much is in its way: a ray to a tag met {MAX_CASTS} surfaces it looks past (the '
            "robot's, the perimeter walls' and ones that touch nothing) in one frame, the most a camera looks past, "
            "so it saw nothing beyond them; move it or point it clear of the robot",
            lambda: i18n.t('{sensor_table} "{name}": too much is in its way: a ray to a tag met {casts} surfaces it '
                           "looks past (the robot's, the perimeter walls' and ones that touch nothing) in one frame, "
                           "the most a camera looks past, so it saw nothing beyond them; move it or point it clear of "
                           "the robot", sensor_table="[[sensor]]", name=name, casts=MAX_CASTS)))
