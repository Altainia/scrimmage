"""The robot frame: which CAD axis is up and which is forward, where the floor is, and where the footprint is centered.

Robot coordinates are x forward, y left, z up, in meters, with the floor at z = 0 and the footprint center at x = y = 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import i18n
from . import CadError
from .source import Source

AXES = ("+x", "-x", "+y", "-y", "+z", "-z")


def _axis(name: str) -> tuple[int, float]:
    return "xyz".index(name[1]), 1.0 if name[0] == "+" else -1.0


@dataclass
class RobotFrame:
    up: str = "+z"
    front: str = "+x"
    floor: float = 0.0                              # CAD coordinate along `up` where the floor is
    center: tuple[float, float] | None = None       # footprint center in the two other CAD coordinates (x<y<z order)
    skip: bool = False                              # True: CAD coordinates are already robot coordinates
    confirmed: bool = False                         # the student has looked at the frame and accepted it

    def __post_init__(self):
        for field, value in (("up", self.up), ("front", self.front)):
            if value not in AXES:
                raise CadError(i18n.t("The robot's {field} direction must be one of {axes}, not {value}.",
                                      field=field, axes=', '.join(AXES), value=repr(value)))
        if self.up[1] == self.front[1]:
            raise CadError(i18n.t("Up ({up}) and front ({front}) must point along different axes.",
                                  up=self.up, front=self.front))
        if self.center is not None:
            self.center = (float(self.center[0]), float(self.center[1]))
        self.floor = float(self.floor)


def default_frame(src: Source) -> RobotFrame:
    """Z up, X forward, the floor at the lowest point, the footprint centered on the bounding box."""
    lo = np.min([p.v.min(axis=0) for p in src.pieces], axis=0)
    hi = np.max([p.v.max(axis=0) for p in src.pieces], axis=0)
    return RobotFrame("+z", "+x", float(lo[2]), (float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2)))


def to_robot(frame: RobotFrame) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) with robot = R @ cad + t."""
    if frame.skip:
        return np.eye(3), np.zeros(3)
    ua, us = _axis(frame.up)
    fa, fs = _axis(frame.front)
    up = np.zeros(3)
    up[ua] = us
    front = np.zeros(3)
    front[fa] = fs
    left = np.cross(up, front)
    R = np.array([front, left, up])
    origin = np.zeros(3)
    origin[ua] = frame.floor
    if frame.center is not None:
        others = [i for i in range(3) if i != ua]
        origin[others[0]], origin[others[1]] = frame.center
    return R, -R @ origin
