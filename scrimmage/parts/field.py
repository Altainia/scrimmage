"""The simplified 2026-size field every generated model sits on: same names and look as tests/models/kitbot.xml."""

from __future__ import annotations

import mujoco

from .. import i18n

FIELD_BITS = {"contype": 1, "conaffinity": 2}
# The ground's geom group: the carpet and the range's field elements (scrimmage/testrange/build_elements.py), and
# nothing else, so a ray in it finds the ground and never a ball or the robot. MuJoCo draws groups 0 to 2 by default.
ELEMENT_GROUP = 2
WALL = {"friction": [0.3, 0.005, 0.0001], "rgba": [0.75, 0.75, 0.8, 0.35]}


def add_field(spec: mujoco.MjSpec, cfg: dict, *, obstacle_default: bool = True) -> None:
    """`obstacle_default`: whether the center obstacle is there when [field] doesn't say (a range turns it off)."""
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
    w.add_light(pos=[8.27, 4.035, 12], dir=[0, 0, -1], type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL)
    box = mujoco.mjtGeom.mjGEOM_BOX
    w.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, pos=[8.27, 4.035, 0], size=[8.27, 4.035, 0.1],
               material="carpet", friction=[1.0, 0.01, 0.001], group=ELEMENT_GROUP, **FIELD_BITS)
    for name, pos, size in (("wall_blue", [-0.05, 4.035, 0.25], [0.05, 4.135, 0.25]),
                            ("wall_red", [16.59, 4.035, 0.25], [0.05, 4.135, 0.25]),
                            ("wall_right", [8.27, -0.05, 0.25], [8.37, 0.05, 0.25]),
                            ("wall_left", [8.27, 8.12, 0.25], [8.37, 0.05, 0.25])):
        w.add_geom(name=name, type=box, pos=pos, size=size, **WALL, **FIELD_BITS)
    if cfg.get("obstacle", obstacle_default):
        w.add_geom(name="obstacle", type=box, pos=[8.27, 4.035, 0.25], size=[0.6, 0.6, 0.25],
                   rgba=[0.8, 0.55, 0.1, 1], friction=[0.3, 0.005, 0.0001], **FIELD_BITS)
