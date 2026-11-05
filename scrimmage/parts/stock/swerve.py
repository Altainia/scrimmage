"""Stock swerve modules: MK4i, MK4n and MAXSwerve. One builder, per-module constants."""

from __future__ import annotations

from dataclasses import dataclass

from ...motors import MOTORS
from . import TREAD_PARAMS, Param, Stock, register
from ...i18n import N_

ROTOR_MOI = 1.0e-4          # kg m^2: a Kraken-class rotor; matches the hand-tuned armature in the old swerve.xml
DARK = [0.3, 0.3, 0.33, 1.0]
TIRE = [0.15, 0.15, 0.15, 1.0]


@dataclass(frozen=True)
class _Module:
    name: str
    doc: str
    ratios: dict
    default_ratio: str
    steer_ratio: float
    wheel_radius: float
    steer_invert: bool
    drive_motor: str
    steer_motor: str
    note: str = ""
    encoder_invert: bool = False   # the encoder slot reads the module clockwise-positive


MODULES = (
    _Module("MK4i", "SDS MK4i swerve module (inverted steer motor), 4 in wheel",
            {"L1": 8.14, "L2": 6.75, "L3": 6.12}, "L2", 150 / 7, 0.0508, True, "KrakenX60", "KrakenX60"),
    _Module("MK4n", "SDS MK4n swerve module (narrow), 4 in wheel",
            {"L1+": 7.13, "L2+": 5.9, "L3+": 5.36}, "L2+", 18.75, 0.0508, True, "KrakenX60", "KrakenX60"),
    _Module("MAXSwerve", "REV MAXSwerve module, 3 in wheel, NEO drive and NEO 550 steer",
            {f"{t}T": 45 * 22 / (t * 15) for t in (12, 13, 14)}, "14T", 9424 / 203, 0.0381, False, "NEO", "NEO550",
            note=N_("Real MAXSwerve modules use REV SPARK controllers: bind the drive and steer slots with SparkMax:<id> "
                 "(or SparkFlex:<id>) and the encoder slot, the steer SPARK's absolute encoder, with "
                 "SparkMaxAbs:<id> (or SparkFlexAbs:<id>). The ratio choices are the pinion teeth (12T, 13T or 14T); "
                 "REV's MAXSwerve template uses 14T. The Through Bore encoder reads the module clockwise-positive, "
                 "so robot code inverts it, as REV's template does."),
            encoder_invert=True),
)


def _builder(mod: _Module):
    def build(p: dict) -> dict:
        r = mod.wheel_radius
        ratio = mod.ratios[p["ratio"]] if isinstance(p["ratio"], str) else p["ratio"]
        sr = p["steer_ratio"]
        return {
            "part": {"name": f"swerve_{mod.name}", "format": 1},
            "body": [
                {"name": "base", "mass": 0.05,
                 "geom": [{"box": [0.1, 0.1, 0.01], "pos": [0, 0, r + 0.09], "color": DARK, "collide": False}]},
                {"name": "azimuth", "parent": "base", "mass": p["module_mass"] - 0.25,
                 "joint": {"name": "steer", "hinge": "z", "armature": ROTOR_MOI * sr ** 2, "damping": 0.01},
                 "geom": [{"cylinder": [0.06, 0.07], "pos": [0, 0, r + 0.045], "color": DARK, "collide": False}]},
                {"name": "wheel", "parent": "azimuth", "mass": 0.25, "pos": [0, 0, r],
                 "joint": {"name": "drive", "hinge": "y", "armature": ROTOR_MOI * ratio ** 2, "damping": 0.002},
                 "geom": [{"cylinder": [r, p["wheel_width"]], "axis": "y", "friction": p["wheel_friction"],
                           "condim": 4, "tread": True, "color": TIRE}]},
            ],
            "motor": [
                {"slot": "drive", "joint": "drive", "motor": p["drive_motor"], "gearing": ratio,
                 "invert": p["drive_invert"]},
                {"slot": "steer", "joint": "steer", "motor": p["steer_motor"], "gearing": sr,
                 "invert": p["steer_invert"]},
            ],
            "encoder": [{"slot": "encoder", "joint": "steer", "ratio": 1.0, "invert": mod.encoder_invert}],
        }
    return build


_MOTOR_NAMES = tuple(sorted(MOTORS))

for _m in MODULES:
    register(Stock(
        f"swerve/{_m.name}", _m.doc,
        (Param("ratio", "", _m.default_ratio, "drive gear ratio (motor turns per wheel turn): "
               + ", ".join(f"{k} = {round(v, 2):g}" for k, v in _m.ratios.items()) + ", or your own number",
               kind="ratio", choices=tuple(_m.ratios)),
         Param("steer_ratio", "", _m.steer_ratio, N_("steer motor turns per module turn"), positive=True),
         Param("drive_motor", "", _m.drive_motor, N_("drive motor model"), kind="str", choices=_MOTOR_NAMES),
         Param("steer_motor", "", _m.steer_motor, N_("steer motor model"), kind="str", choices=_MOTOR_NAMES),
         Param("drive_invert", "", False, N_("invert the drive motor"), kind="bool"),
         Param("steer_invert", "", _m.steer_invert, N_("invert the steer motor"), kind="bool"),
         Param("wheel_friction", "", 1.2, N_("wheel-to-carpet sliding friction coefficient"), positive=True),
         Param("wheel_width", "m", 0.04, N_("tread width"), positive=True),
         *TREAD_PARAMS,
         Param("module_mass", "kg", 0.75, N_("azimuth housing plus wheel (0.25 kg of it is the wheel)"), min=0.3)),
        _builder(_m), slots=("drive", "steer", "encoder"), mounts=(), note=_m.note))
