"""Floor zones a range file declares: `[[zone]]`, an area of the carpet marked with tape (2024's wing, starting
zone, stage zone), for the Test tab's `in_zone` condition (the robot's center in or out of it).

    [[zone]]
    name = "blue_wing"
    corners = [[0.0, 0.0], [5.87, 0.0], [5.87, 8.21], [0.0, 8.21]]   # field x, y (m), in order around it
    color = "blue"                # the tape's color (scrimmage/sensors/colors.py's names, or [r, g, b]); default white

A zone is a simple polygon (its edges don't cross), at least 3 corners, each on the field. It is drawn on the carpet
as tape TAPE_WIDTH wide along its edges, touching nothing (a robot drives over it; a ray for the ground never finds
it). `Zone.contains` is the even-odd test, an edge counting as inside.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import i18n

TAPE_WIDTH = 0.0508     # m: FRC field tape is 2 in wide
TAPE_HEIGHT = 0.001     # m: drawn this far above the carpet
DEFAULT_TAPE = (0.95, 0.95, 0.95)
_ZONE = ("name", "corners", "color")


@dataclass(frozen=True)
class Zone:
    name: str
    corners: tuple[tuple[float, float], ...]
    color: tuple[float, float, float] | None = None

    def contains(self, x: float, y: float) -> bool:
        """Whether field point (x, y) is inside the zone or on its edge."""
        pts = self.corners
        inside = False
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
            if abs(cross) < 1e-9 and min(x0, x1) - 1e-9 <= x <= max(x0, x1) + 1e-9 \
                    and min(y0, y1) - 1e-9 <= y <= max(y0, y1) + 1e-9:
                return True                                     # on an edge
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        return inside


def parse_zones(path, tables, size: tuple[float, float], taken: tuple[str, ...] = ()) -> tuple[Zone, ...]:
    """Read the `[[zone]]` tables on a field of `size` (length, width). `taken` are the field's other names, which a
    zone may share (a zone is a place on the floor, not a thing)."""
    from .pieces import parse_color_key
    from .spec import RangeError, _Reader

    if not isinstance(tables, list):
        raise RangeError(i18n.t("{path}: zone must be an array of tables ({zone_table})", path=path,
                                zone_table="[[zone]]"))
    out: list[Zone] = []
    length, width = size
    for i, t in enumerate(tables):
        r = _Reader(path, f"[[zone]] #{i + 1}", t, _ZONE)
        name = r.text("name")
        r.where = f"[[zone]] '{name}'"
        if any(z.name == name for z in out):
            raise RangeError(i18n.t("{path}: two zones named '{name}'; zone names must be unique", path=path,
                                    name=name))
        raw = r.raw("corners")
        if (not isinstance(raw, list) or len(raw) < 3 or any(
                not isinstance(p, list) or len(p) != 2
                or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in p) for p in raw)):
            raise r.err(i18n.t("corners must be a list of at least 3 [x, y] points, in order around the zone, not "
                               "{corners}", corners=repr(raw)))
        corners = tuple((float(x), float(y)) for x, y in raw)
        for x, y in corners:
            if not (0.0 <= x <= length and 0.0 <= y <= width):
                raise r.err(i18n.t("corner ({x}, {y}) is off the field (0..{length} x 0..{width} m)", x=f"{x:g}",
                                   y=f"{y:g}", length=f"{length:g}", width=f"{width:g}"))
        a = np.array(corners)
        area = 0.5 * float(np.sum(a[:, 0] * np.roll(a[:, 1], -1) - np.roll(a[:, 0], -1) * a[:, 1]))
        if abs(area) < 1e-6:
            raise r.err(i18n.t("has no area: its corners lie on one line"))
        color = parse_color_key(f"{path}: {r.where}", r.raw("color")) if r.has("color") else None
        out.append(Zone(name=name, corners=corners, color=color))
    return tuple(out)


def add_zones(spec, zones) -> None:
    """Each zone's edges as tape on the carpet: thin boxes that touch nothing, in no ground group."""
    import mujoco

    for z in zones:
        rgba = [*(z.color if z.color is not None else DEFAULT_TAPE), 1.0]
        pts = z.corners
        for k, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:] + pts[:1])):
            dx, dy = x1 - x0, y1 - y0
            half = float(np.hypot(dx, dy)) / 2.0
            yaw = float(np.arctan2(dy, dx))
            spec.worldbody.add_geom(name=f"zone.{z.name}.{k}", type=mujoco.mjtGeom.mjGEOM_BOX,
                                    pos=[(x0 + x1) / 2.0, (y0 + y1) / 2.0, TAPE_HEIGHT / 2.0],
                                    quat=[float(np.cos(yaw / 2.0)), 0.0, 0.0, float(np.sin(yaw / 2.0))],
                                    size=[half + TAPE_WIDTH / 2.0, TAPE_WIDTH / 2.0, TAPE_HEIGHT / 2.0],
                                    rgba=rgba, contype=0, conaffinity=0, group=1)
