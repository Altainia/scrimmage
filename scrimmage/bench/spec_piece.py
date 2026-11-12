"""The bench file's [piece]: a ball (a launcher preset or its own numbers), a rigid shape, mesh or ring, or a range's
piece preset (the 2024 NOTE), parsed into spec.py's Piece.
"""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from .. import i18n
from ..launcher.inputs import PRESETS, Ball
from ..testrange.pieces import _AIR, air_given, kind_preset, parse_kind, ring_diameters
from ..testrange.spec import RangeError, _Reader, piece_box
from .spec import PIECE_FRICTION, _REQ, Piece, Vec, _Table, _resolve_path, _units, _valid_keys


def _default_inertia(shape: str, size: tuple, mass: float) -> Vec:
    if shape == "sphere":
        return (0.4 * mass * (size[0] / 2) ** 2,) * 3
    if shape == "box":
        x, y, z = size
        return (mass / 12 * (y * y + z * z), mass / 12 * (x * x + z * z), mass / 12 * (x * x + y * y))
    r, h = size[0] / 2, size[1]  # cylinder, axis along local z
    side = mass * (3 * r * r + h * h) / 12
    return (side, side, mass * r * r / 2)


_BALL_KEYS = ("stiffness_n", "stiffness_exponent", "restitution", "inertia_factor")
_SHAPE_SIZES = {"sphere": 1, "box": 3, "cylinder": 2}


class _AirReader(_Reader):
    """The range's reader for a bench [piece]'s air keys: its messages read "[piece]: <what>", as a [piece.*]
    kind's do after its file name."""

    def err(self, msg: str) -> RangeError:
        return RangeError(f"{self.where}: {msg}")


def _parse_piece(raw, base_dir: Path, warned: list[str]) -> Piece:
    """The [piece]; a warning that doesn't stop the run (a borrowed ball preset) is appended to `warned`."""
    piece = _parse_piece_shape(raw, base_dir, warned)
    t = _Table("piece", "[piece]", raw)
    air = {k: t.t[k] for k in _AIR if k in t.t}
    if not air:
        return piece
    if piece.kind != "rigid":
        key = next(iter(air))
        raise t.err(i18n.t("{key} is a rigid piece's air drag; a ball flies with the {air_table} drag and a disc "
                           "with its aero set", key=key, air_table="[air]"), key)
    given = {}
    for key, value in air.items():  # one at a time, so the error names its key
        try:
            given |= air_given(_AirReader(Path("."), "[piece]", {key: value}, _AIR))
        except RangeError as e:
            raise t.err(str(e), key) from None
    kind = piece.rigid_kind
    return replace(piece, **given, rigid_kind=None if kind is None else replace(kind, **given))


def _parse_piece_shape(raw, base_dir: Path, warned: list[str]) -> Piece:
    """The [piece] but for its air keys (`_parse_piece`)."""
    t = _Table("piece", "[piece]", raw)
    preset = t.text("preset", None)
    base: dict = {}
    if preset is not None and preset in PRESETS.get("piece", {}):
        return _preset_piece(t, preset)
    if preset is not None:
        known = PRESETS["ball"]
        if preset not in known:
            raise t.err(i18n.t("{table} preset {preset} is not a ball or piece preset; known ball presets: {known}; "
                               "known piece presets: {pieces}", table=t.label, preset=repr(preset),
                               known=", ".join(known), pieces=", ".join(PRESETS.get("piece", {}))), "preset")
        base = known[preset]
    kind = t.text("kind", "squishy" if preset else "rigid", one_of=("squishy", "rigid"))
    shape = t.text("shape", "sphere" if preset else _REQ, one_of=("sphere", "box", "cylinder", "disc", "ring", "mesh"))
    if shape == "disc":  # a disc is a short cylinder
        shape = "cylinder"
    flex = t.flag("flex")
    if flex and shape != "ring":
        raise _not_a_ring(t, shape)
    if preset and shape != "sphere":
        raise t.err(i18n.t("{table} preset {preset} is a ball; its shape is sphere, not {shape}", table=t.label,
                           preset=repr(preset), shape=shape))
    if kind == "squishy" and shape != "sphere":
        raise t.err(i18n.t("{table} is squishy with shape {shape}, but squishy pieces are balls only (a sphere, or a "
                           "ball preset); use {kind_setting} as a rigid stand-in (rigid pieces may give their own "
                           "contact `{stiffness}`)", table=t.label, shape=shape, kind_setting="kind = \"rigid\"",
                           stiffness="stiffness"))
    ball_given = [k for k in _BALL_KEYS if k in t.t and not (flex and k == "restitution")]
    if kind == "rigid" and ball_given:
        raise t.err(i18n.t("{table} is rigid, so {key} does not apply (ball numbers are for squishy pieces); a rigid "
                           "piece's own contact stiffness is `{stiffness}` (N/m)", table=t.label, key=ball_given[0],
                           stiffness="stiffness"))
    if kind == "squishy" and "stiffness" in t.t:
        raise t.err(i18n.t("{table} is squishy, so `{stiffness}` does not apply; give the launcher model's "
                           "stiffness_n (N for a quarter-diameter squeeze)", table=t.label, stiffness="stiffness"))
    name = t.text("name", preset or "piece")
    if shape == "ring":
        return _ring_piece(t, name, kind)
    if shape == "mesh":
        if t.has("size"):
            raise t.err(i18n.t("{table} is a mesh and takes no size", table=t.label))
        size: tuple = ()
        mesh = _resolve_path(t, "mesh", base_dir)
        if mesh is None:
            raise t.err(i18n.t("{table} is missing required key '{key}'; valid keys: {keys}", table=t.label,
                               key="mesh", keys=", ".join(_valid_keys("piece"))))
        units = _units(t, True)
    else:
        if "mesh" in t.t:
            raise t.err(i18n.t("{table} has a mesh but shape is not \"{mesh}\"", table=t.label, mesh="mesh"))
        mesh, units = None, _units(t, False)
        n = _SHAPE_SIZES[shape]
        size = t.vec("size", n, (base["diameter"],) if preset else _REQ, positive=True, length=True)
    mass = t.num("mass", base.get("mass", _REQ), positive=True)
    ball = None
    if kind == "squishy":
        nums = dict(base)
        if preset is None:
            near = min(PRESETS["ball"], key=lambda p: abs(PRESETS["ball"][p]["diameter"] - size[0]))
            missing = [k for k in ("stiffness_n", "restitution") if k not in t.t]
            if missing:
                warned.append(i18n.t("{piece_table} {keys} not given; using the nearest ball preset's ({near}). "
                                     "Measure your own ball (docs/launcher.md)", keys=" and ".join(missing),
                                     near=near, piece_table="[piece]"))
                nums = dict(PRESETS["ball"][near])
        for k in _BALL_KEYS:
            if k in t.t:
                nums[k] = t.num(k, positive=True)
        if not 0.0 < nums["restitution"] <= 1.0:
            raise t.err(i18n.t("{table} restitution must be above 0 and at most 1, not {value}", table=t.label,
                               value=repr(nums["restitution"])))
        ball = Ball(diameter=size[0], mass=mass, **{k: nums[k] for k in ("stiffness_n", "restitution")},
                    **{k: nums[k] for k in ("stiffness_exponent", "inertia_factor") if k in nums})
    if "inertia" in t.t:
        inertia = t.vec("inertia", 3, positive=True)
    elif shape == "mesh":
        raise t.err(i18n.t("{table} is a mesh, so it needs an {inertia_setting} (kg m^2); there is no default",
                           table=t.label, inertia_setting="inertia = [Ixx, Iyy, Izz]"))
    elif ball is not None:
        inertia = (ball.inertia_factor * mass * (size[0] / 2) ** 2,) * 3
    else:
        inertia = _default_inertia(shape, size, mass)
    stiffness = t.num("stiffness", None, positive=True)
    return Piece(name=name, kind=kind, shape=shape, size=size, mass=mass, inertia=inertia, ball=ball,
                 stiffness=stiffness, mesh=mesh, units=units)


# the keys a piece preset takes beside `preset`: the rest are the preset's own
_PRESET_KEYS = ("preset", "name", "kind", "shape", "stiffness", "drag_coefficient", "spin_damping", "flex",
                "restitution")
# a flex is a torus: a ring whose length (along its axis) is its wall's width, (diameter - inner_diameter) / 2
FLEX_TORUS_TOL = 0.05  # choice: the 2024 NOTE's is exact; a flat ring (the 2019 HATCH PANEL) is far off


def _not_a_ring(t: _Table, shape: str):
    return t.err(i18n.t("{table} has flex, but a flex piece must be a ring, such as the 2024 NOTE, not a "
                        "{shape}", table=t.label, shape=shape), "flex")


def _kind_piece(t: _Table, name: str, kind, size: tuple) -> Piece:
    """A rigid bench piece that is a range kind: its mass, inertia (the full tensor's diagonal; the off-diagonal
    moments go to the builder through the kind), solids and contact stiffness (a piece preset's own, such as the 2024
    NOTE's; a `stiffness` key overrides it) are the kind's. With `flex = true` (a ring only) it is a flex NOTE, and
    its `restitution` (the kind's own unless given) is read too: a rigid piece has no use for it."""
    flex = t.flag("flex")
    if flex and kind.shape != "ring":
        raise _not_a_ring(t, kind.shape)
    restitution = None
    if "restitution" in t.t:
        if not flex:
            raise t.err(i18n.t("{table} restitution is read only for a flex piece (add {flex_setting}); a rigid "
                               "piece's bounce comes from MuJoCo's contact", table=t.label,
                               flex_setting="flex = true"), "restitution")
        restitution = t.num("restitution", positive=True)
        if not restitution < 1.0:
            raise t.err(i18n.t("{table} restitution must be above 0 and below 1, not {value}", table=t.label,
                               value=repr(t.t["restitution"])), "restitution")
    elif flex:
        restitution = kind.restitution
    if flex:
        outer, inner = ring_diameters(kind)
        a = np.asarray(kind.axis, dtype=float)
        pts = kind._points @ (a / np.linalg.norm(a))
        length, wall = float(np.ptp(pts)), (outer - inner) / 2.0 if inner is not None else math.inf
        if not abs(length - wall) <= FLEX_TORUS_TOL * wall:
            raise t.err(i18n.t("{table} has flex, but a flex piece is a round tube bent into a ring (as the 2024 "
                               "NOTE is): its length must equal half the difference of its two diameters ({wall} "
                               "mm), not {length} mm", table=t.label, wall=f"{wall * 1000:.1f}",
                               length=f"{length * 1000:.1f}"), "flex")
    stiffness = t.num("stiffness", kind.stiffness, positive=True)
    if flex and stiffness is None:
        raise t.err(i18n.t("{table} has {flex_setting} but no stiffness: add {stiffness_setting} (the 2024 NOTE's "
                           "estimate is 2000)", table=t.label, flex_setting="flex = true",
                           stiffness_setting="stiffness = <N/m>"), "stiffness")
    if flex and restitution is None:
        raise t.err(i18n.t("{table} has {flex_setting} but no restitution: add {restitution_setting} (the 2024 "
                           "NOTE's estimate is 0.3)", table=t.label, flex_setting="flex = true",
                           restitution_setting="restitution = <above 0, below 1>"), "restitution")
    return Piece(name=name, kind="rigid", shape=kind.shape, size=size, mass=kind.mass,
                 inertia=tuple(kind.inertia[:3]), ball=None, stiffness=stiffness, mesh=None, units=None,
                 rigid_kind=kind, flex=flex, restitution=restitution)


def _preset_piece(t: _Table, preset: str) -> Piece:
    """`preset = "<piece preset>"` (launcher/presets.toml's [piece.*], such as 2024-note): the range's own kind."""
    extra = [k for k in t.t if k not in _PRESET_KEYS]
    if extra:
        raise t.err(i18n.t("{table} preset {preset} is a piece preset, so {key} comes from it; a piece preset takes "
                           "only {keys}", table=t.label, preset=repr(preset), key=extra[0].removesuffix("_in"),
                           keys=", ".join(_PRESET_KEYS)), extra[0])
    t.text("kind", "rigid", one_of=("rigid",))
    try:
        kind = kind_preset(preset)
    except RangeError as e:  # pragma: no cover - the shipped presets load
        raise t.err(str(e), "preset") from None
    shape = t.text("shape", kind.shape)
    if shape != kind.shape:
        raise t.err(i18n.t("{table} preset {preset} is a {own}, not a {shape}", table=t.label, preset=repr(preset),
                           own=kind.shape, shape=shape), "shape")
    return _kind_piece(t, t.text("name", preset), kind, piece_box(kind))


def _ring_piece(t: _Table, name: str, kind: str) -> Piece:
    """`shape = "ring"`: a hollow round, `size = [diameter, inner_diameter, length]`, built by the range's piece
    builder (TUBE_SLICES convex slices, so a rod passes through the hole)."""
    if kind != "rigid":  # pragma: no cover - squishy non-spheres are refused before
        raise t.err(i18n.t("{table} is a ring, so it is rigid", table=t.label), "kind")
    for key in ("mesh", "units", "inertia"):
        if key in t.t:  # (a ring's air keys are read by _parse_piece)
            raise t.err(i18n.t("{table} is a ring and takes no {key}: its inertia comes from its shape",
                               table=t.label, key=key), key)
    size = t.vec("size", 3, positive=True, length=True)
    if not size[1] < size[0]:
        raise t.err(i18n.t("{table} ring size is [diameter, inner_diameter, length]: the inner diameter must be "
                           "smaller than the diameter", table=t.label), "size")
    table = {"shape": "ring", "diameter": size[0], "inner_diameter": size[1], "length": size[2],
             "mass": t.num("mass", positive=True), "friction": PIECE_FRICTION}
    try:
        built = parse_kind(Path("[piece]"), name, table, Path("."))
    except RangeError as e:
        raise t.err(str(e), "size") from None
    return _kind_piece(t, name, built, size)
