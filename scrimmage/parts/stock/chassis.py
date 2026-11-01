"""A rectangular drive base: frame, bumpers and a heading marker, with corner mounts for modules."""

from __future__ import annotations

from . import Param, Stock, register

YELLOW = [1.0, 0.9, 0.1, 1.0]
BLUE = [0.1, 0.25, 0.85, 1.0]
GRAY = [0.65, 0.65, 0.68, 1.0]


def build(p: dict) -> dict:
    L, W, fh, b, inset = p["length"], p["width"], p["frame_height"], p["bumper"], p["module_inset"]
    h = 0.30                                   # height the mass is spread over (frame, battery, electronics)
    m = p["mass"]
    bumper_h = 0.12
    bumper_z = p["bumper_clearance"] + bumper_h / 2     # the boxes' center: their bottom is bumper_clearance up
    geoms = [
        {"box": [L, W, 0.04], "pos": [0, 0, fh], "color": GRAY},
        {"box": [b, W + 2 * b, bumper_h], "pos": [L / 2 + b / 2, 0, bumper_z], "color": BLUE, "friction": 0.3},
        {"box": [b, W + 2 * b, bumper_h], "pos": [-L / 2 - b / 2, 0, bumper_z], "color": BLUE, "friction": 0.3},
        {"box": [L, b, bumper_h], "pos": [0, W / 2 + b / 2, bumper_z], "color": BLUE, "friction": 0.3},
        {"box": [L, b, bumper_h], "pos": [0, -W / 2 - b / 2, bumper_z], "color": BLUE, "friction": 0.3},
        {"box": [0.1, 0.04, 0.01], "pos": [L / 2 - 0.1, 0, fh + 0.025], "color": YELLOW, "collide": False},
    ]
    x, y = L / 2 - inset, W / 2 - inset
    return {
        "part": {"name": "chassis", "format": 1},
        "body": [{"name": "chassis", "mass": m, "geom": geoms,
                  "com": [0, 0, fh + 0.06],
                  "inertia": [m * (W ** 2 + h ** 2) / 12, m * (L ** 2 + h ** 2) / 12, m * (L ** 2 + W ** 2) / 12]}],
        "gyro": [{"slot": "gyro", "body": "chassis"}],
        "mount": [{"name": "front_left", "body": "chassis", "pos": [x, y, 0]},
                  {"name": "front_right", "body": "chassis", "pos": [x, -y, 0]},
                  {"name": "back_left", "body": "chassis", "pos": [-x, y, 0]},
                  {"name": "back_right", "body": "chassis", "pos": [-x, -y, 0]},
                  {"name": "center", "body": "chassis"},
                  {"name": "top", "body": "chassis", "pos": [0, 0, fh + 0.02]}],
    }


register(Stock(
    "chassis", "rectangular drive base with bumpers; modules mount at its corners",
    (Param("length", "m", 0.7, "frame length (front to back), without bumpers", positive=True),
     Param("width", "m", 0.7, "frame width (side to side), without bumpers", positive=True),
     Param("mass", "kg", 46.0, "everything on the chassis that isn't another part (frame, battery, electronics)", positive=True),
     Param("frame_height", "m", 0.12, "height of the frame's middle above the floor", positive=True),
     Param("bumper", "m", 0.08, "bumper thickness", positive=True),
     Param("bumper_clearance", "m", 0.02, "height of the bumpers' bottom above the carpet; with the bumpers' reach "
           "past the front wheels it sets the approach angle, the steepest ramp the robot drives onto "
           "(docs/terrain.md)", positive=True),
     Param("module_inset", "m", 0.0579, "distance from each frame edge to a corner mount (module center)",
           min=0.0, max=lambda q: min(q["length"], q["width"]) / 2)),
    build, slots=("gyro",), mounts=("front_left", "front_right", "back_left", "back_right", "center", "top")))
