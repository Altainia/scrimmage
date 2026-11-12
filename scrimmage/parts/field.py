"""The simplified field every generated model sits on: same names and look as tests/models/kitbot.xml. It is 2026's
size, FIELD_LENGTH by FIELD_WIDTH, unless a range gives its own (`[range] field = { length, width }`,
scrimmage/testrange/spec.py): the carpet, the four walls (their inner faces on the field's edges), the light and the
center obstacle follow it."""

from __future__ import annotations

import mujoco

from .. import i18n

FIELD_BITS = {"contype": 1, "conaffinity": 2}
# The ground's geom group: the carpet and the range's field elements (scrimmage/testrange/build_elements.py), and
# nothing else, so a ray in it finds the ground and never a ball or the robot. MuJoCo draws groups 0 to 2 by default.
ELEMENT_GROUP = 2
FIELD_LENGTH = 16.54  # m, the alliance walls' inner faces (2026's)
FIELD_WIDTH = 8.07  # m, the guardrails' inner faces
CARPET_FRICTION = (1.0, 0.01, 0.001)  # estimate: the carpet's sliding, torsional and rolling friction, not measured
WALL = {"friction": [0.3, 0.005, 0.0001], "rgba": [0.75, 0.75, 0.8, 0.35]}


def add_field(spec: mujoco.MjSpec, cfg: dict, *, obstacle_default: bool = True,
              size: tuple[float, float] = (FIELD_LENGTH, FIELD_WIDTH)) -> None:
    """`obstacle_default`: whether the center obstacle is there when [field] doesn't say (a range turns it off).
    `size`: the field's length and width (m), the walls' inner faces."""
    from .model import PartError
    for k in cfg:
        if k != "obstacle":
            raise PartError(i18n.t("{field_table}: unknown key {key}; allowed: obstacle", key=repr(k),
                                   field_table="[field]"))
    if not isinstance(cfg.get("obstacle", obstacle_default), bool):
        raise PartError(i18n.t("{field_table} obstacle must be true or false", field_table="[field]"))
    g = spec.visual.global_
    g.offwidth, g.offheight, g.azimuth, g.elevation = 1600, 1000, -125, -30
    spec.visual.quality.shadowsize = 4096
    spec.add_texture(name="carpet", type=mujoco.mjtTexture.mjTEXTURE_2D, builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER,
                     rgb1=[0.28, 0.29, 0.32], rgb2=[0.30, 0.31, 0.34], width=512, height=512)
    mat = spec.add_material(name="carpet")
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "carpet"
    mat.texrepeat = [16, 8]
    w = spec.worldbody
    hx, hy = size[0] / 2.0, size[1] / 2.0
    w.add_light(pos=[hx, hy, 12], dir=[0, 0, -1], type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL)
    box = mujoco.mjtGeom.mjGEOM_BOX
    w.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, pos=[hx, hy, 0], size=[hx, hy, 0.1],
               material="carpet", friction=list(CARPET_FRICTION), group=ELEMENT_GROUP, **FIELD_BITS)
    t = 0.05    # half the walls' thickness: each reaches past the corners by its own thickness
    for name, pos, half in (("wall_blue", [-t, hy, 0.25], [t, hy + 2 * t, 0.25]),
                            ("wall_red", [size[0] + t, hy, 0.25], [t, hy + 2 * t, 0.25]),
                            ("wall_right", [hx, -t, 0.25], [hx + 2 * t, t, 0.25]),
                            ("wall_left", [hx, size[1] + t, 0.25], [hx + 2 * t, t, 0.25])):
        w.add_geom(name=name, type=box, pos=pos, size=half, **WALL, **FIELD_BITS)
    if cfg.get("obstacle", obstacle_default):
        w.add_geom(name="obstacle", type=box, pos=[hx, hy, 0.25], size=[0.6, 0.6, 0.25],
                   rgba=[0.8, 0.55, 0.1, 1], friction=[0.3, 0.005, 0.0001], **FIELD_BITS)
