"""Stock differential (tank) drive: 2-6 wheels per side, the middle one driven, the rest chained to it."""

from __future__ import annotations

from ...motors import MOTORS
from . import TREAD_PARAMS, Param, Stock, register
from ...i18n import N_

DARK = [0.3, 0.3, 0.33, 1.0]
TIRE = [0.15, 0.15, 0.15, 1.0]


def build(p: dict) -> dict:
    n, track, wb = p["wheels_per_side"], p["track_width"], p["wheelbase"]
    r = p["wheel_diameter"] / 2
    xs = [wb / 2 - wb * i / (n - 1) for i in range(n)]
    d = n // 2                                      # index of the driven wheel
    bodies = [{"name": "rails", "mass": p["rail_mass"],
               "geom": [{"box": [wb + 0.1, 0.04, 0.04], "pos": [0, s * track / 2, r], "color": DARK,
                         "collide": False} for s in (1, -1)]}]
    couplings = []
    for side, sy in (("left", 1), ("right", -1)):
        for i, x in enumerate(xs):
            z = r - p["drop_center"] if (n % 2 == 1 and i == d) else r
            bodies.append({
                "name": f"{side}_{i + 1}", "parent": "rails", "mass": 0.25, "pos": [x, sy * track / 2, z],
                "joint": {"name": f"{side}_{i + 1}", "hinge": "y", "armature": 0.0005, "damping": 0.002},
                "geom": [{"cylinder": [r, 0.04], "axis": "y", "friction": p["wheel_friction"], "condim": 4,
                          "tread": True, "color": TIRE}]})
            if i != d:
                couplings.append({"joints": [f"{side}_{d + 1}", f"{side}_{i + 1}"], "ratio": 1.0})
    motor = {"motor": p["motor"], "count": p["motors_per_side"], "gearing": p["gearing"],
             "efficiency": p["efficiency"]}
    return {
        "part": {"name": "differential", "format": 1},
        "body": bodies,
        "coupling": couplings,
        "motor": [{"slot": "left", "joint": f"left_{d + 1}", **motor},
                  {"slot": "right", "joint": f"right_{d + 1}", "invert": True, **motor}],
        "encoder": [{"slot": "left_encoder", "joint": f"left_{d + 1}", "ratio": 1.0},
                    {"slot": "right_encoder", "joint": f"right_{d + 1}", "ratio": 1.0, "invert": True}],
        "mount": [{"name": "center", "body": "rails"},
                  {"name": "top", "body": "rails", "pos": [0, 0, 2 * r]}],
    }


register(Stock(
    "differential", N_("tank drive with 2-6 wheels per side; the middle wheel of each side is driven"),
    (Param("track_width", "m", 0.51, N_("distance between the left and right wheel centers"), positive=True),
     Param("wheelbase", "m", 0.52, N_("front-to-back spacing of the first and last wheel"), positive=True),
     Param("wheels_per_side", "", 3, N_("wheels on each side, spread evenly front to back"), kind="int", min=2, max=6),
     Param("wheel_diameter", "m", 0.1524, N_("wheel diameter"), positive=True),
     Param("drop_center", "m", 0.003, N_("how far the middle wheel of an odd count sits below the others"), min=0.0),
     Param("motor", "", "CIM", N_("motor model"), kind="str", choices=tuple(sorted(MOTORS))),
     Param("motors_per_side", "", 2, N_("motors on each side"), kind="int", min=1),
     Param("gearing", "", 10.71, N_("motor turns per wheel turn"), positive=True),
     Param("efficiency", "", 0.9, N_("drivetrain efficiency"), positive=True),
     Param("wheel_friction", "", 1.1, N_("wheel-to-carpet sliding friction coefficient"), positive=True),
     *TREAD_PARAMS,
     Param("rail_mass", "kg", 1.0, N_("mass of the rails the wheels hang from"), positive=True)),
    build, slots=("left", "right", "left_encoder", "right_encoder"), mounts=("center", "top"),
    note=N_("The right side is mirrored: its motor and encoder are inverted, so equal controller outputs "
         "drive the two sides in opposite directions unless the robot program inverts one.")))
