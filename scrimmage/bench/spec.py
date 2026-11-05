"""The bench file: dataclasses, loading a bench TOML, includes, materials and dotted-key overrides.

The bench frame: x forward (the usual exit direction), z up, y across. SI units after parsing; angles are degrees
(keys end in `_deg`). Every length key also has an inch form, `<key>_in` (give one of the two), as the field
elements do. List sections (`[[wheel]]`, `[[plate]]`, ...) and `[shaft.<name>]` tables are addressed by their
`name`, which is how `[sweep]` and `with_overrides` reach them: `wheel.top.diameter`.
"""

import copy
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import i18n
from ..launcher.inputs import PRESETS, Ball
from ..motors import MOTORS
from .problems import Problem

IN = 0.0254  # m per inch
MATERIAL_DEFAULT = "polycarbonate"
# estimate: 1/8 in polycarbonate sheet, the usual FRC hood material
THICKNESS_DEFAULT = 0.125 * IN
UNITS = {"m": 1.0, "mm": 0.001, "in": IN}


class BenchError(ValueError):
    """A bench file problem; the message names the bad key and, where there is a short list, the valid ones. `key` is
    the dotted key of the field at fault (`wheel.top.diameter`, else the entry `wheel.top`), None when unknown.
    `code` says what the problem is for code that mustn't read its words ("no_coacd", "no_convex_pieces")."""

    def __init__(self, message: str, key: str | None = None, code: str | None = None):
        super().__init__(message)
        self.key, self.code = key, code


_frozen = dict(frozen=True, kw_only=True)
Vec = tuple[float, float, float]


@dataclass(**_frozen)
class Material:
    name: str
    foam_friction: float
    rigid_friction: float
    source: str


@dataclass(**_frozen)
class Piece:
    name: str
    kind: str  # "squishy" | "rigid"
    shape: str  # "sphere" | "box" | "cylinder" | "mesh" ("disc" is read as a cylinder)
    size: tuple[float, ...]  # sphere (diameter,); box (x, y, z); cylinder (diameter, length), axis along local z; mesh ()
    mass: float
    inertia: Vec  # principal moments about the piece's own axes (kg m^2)
    ball: Ball | None  # the launcher model's numbers; squishy pieces only
    stiffness: float | None  # N/m, a rigid piece's own contact stiffness in series with the tread
    mesh: Path | None
    units: str | None  # a mesh's length unit


@dataclass(**_frozen)
class Shaft:
    name: str
    motor: str | None
    count: int
    gearing: float  # motor rotations per shaft rotation
    wheel_inertia: float
    added_inertia: float
    control: str  # "speed" | "voltage" | "free"
    target_rps: float | None
    voltage: float | None
    kP: float
    kS: float
    kV: float | None  # None: derived from the motor's 1/Kv scaled by gearing, as in the launcher model
    stator_limit: float | None
    supply_limit: float | None
    coupled_to: str | None  # this shaft turns at `ratio` times that shaft's speed (a belt or gears)
    ratio: float


@dataclass(**_frozen)
class Wheel:
    name: str
    shaft: str
    center: Vec
    axis: Vec  # unit; the shaft's direction
    diameter: float
    width: float
    tread: str  # a launcher [wheels.*] preset name, also a material


@dataclass(**_frozen)
class Plate:
    name: str
    center: Vec
    size: Vec
    rot_deg: Vec  # rotations about x, then y, then z
    material: str


@dataclass(**_frozen)
class Bar:
    name: str
    a: Vec
    b: Vec
    diameter: float
    material: str
    spring: float | None  # N/m along spring_axis; None: rigid
    travel: float  # slide range, +- m (0 when rigid)
    spring_axis: Vec


@dataclass(**_frozen)
class Curve:
    name: str
    center: Vec
    axis: Vec  # unit; the curve's axis of revolution
    zero_dir: Vec  # unit, perpendicular to axis: where angle 0 sits
    radius_start: float
    radius_end: float
    start_deg: float
    end_deg: float
    width: float  # along the axis
    thickness: float
    material: str


@dataclass(**_frozen)
class Cad:
    name: str
    part: Path | None  # a part folder from the FreeCAD exporter
    mesh: Path | None  # a mesh file (STL or OBJ)
    units: str | None  # a mesh's length unit
    center: Vec
    rot_deg: Vec
    material: str  # every shape of the part but its wheels, or the mesh
    slots: dict[str, str]  # a part's motor slot -> the bench shaft that drives it
    tread: str | None = None  # a part's wheels' tread preset (None for a mesh)


@dataclass(**_frozen)
class Inlet:
    name: str
    center: Vec
    size: Vec
    count: int
    rate: float  # pieces per second
    velocity: Vec
    spin: Vec  # rad/s
    spread_speed: float  # standard deviations
    spread_position: float
    spread_time: float
    seed: int


@dataclass(**_frozen)
class Outlet:
    name: str
    center: Vec
    size: Vec


@dataclass(**_frozen)
class Flight:
    outlet: str
    height: float  # the bench origin's height above the floor
    target_distance: float
    target_height: float
    ring_diameter: float | None
    air: str  # a launcher [air.*] preset name


@dataclass(**_frozen)
class RunSettings:
    duration: float = 5.0
    timestep: float | None = None  # None: the builder picks one per contact law
    jam_time: float = 1.0
    jam_distance: float = 0.01


@dataclass(**_frozen)
class Bench:
    name: str
    author: str
    idea: str
    notes: str
    piece: Piece
    shafts: dict[str, Shaft]
    wheels: tuple[Wheel, ...]
    plates: tuple[Plate, ...]
    bars: tuple[Bar, ...]
    curves: tuple[Curve, ...]
    cads: tuple[Cad, ...]
    inlets: tuple[Inlet, ...]
    outlets: tuple[Outlet, ...]
    flight: Flight | None
    run: RunSettings
    materials: dict[str, Material]
    sweep: dict[str, list]
    charts: list[dict]
    raw: dict  # the file with includes merged, ready for with_overrides and parse_bench
    base_dir: Path
    warnings: tuple[str, ...] = ()  # things the room should know that don't stop the run (a borrowed preset)


def _load_materials() -> dict[str, Material]:
    raw = tomllib.loads(Path(__file__).with_name("presets.toml").read_text(encoding="utf-8"))
    out = {n: Material(name=n, **v) for n, v in raw["material"].items()}
    for n, v in PRESETS["wheels"].items():  # a tread: the launcher's friction against foam; no rigid figure exists
        out[n] = Material(name=n, foam_friction=v["friction"], rigid_friction=v["friction"],
                          source="launcher tread preset (estimate; the same figure is used against rigid pieces)")
    return out


MATERIALS = _load_materials()

# Per section: keys, and which of them are lengths (each also readable as `<key>_in`).
_SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bench": (("name", "author", "idea", "notes"), ()),
    "piece": (("name", "preset", "kind", "shape", "size", "mass", "inertia", "stiffness_n", "stiffness_exponent",
               "restitution", "inertia_factor", "stiffness", "mesh", "units"), ("size",)),
    "shaft": (("motor", "count", "gearing", "wheel_inertia", "added_inertia", "control", "target_rps", "voltage",
               "kP", "kS", "kV", "stator_limit", "supply_limit", "coupled_to", "ratio"), ()),
    "wheel": (("name", "shaft", "center", "axis", "diameter", "width", "tread"), ("center", "diameter", "width")),
    "plate": (("name", "center", "size", "rot_deg", "material"), ("center", "size")),
    "bar": (("name", "a", "b", "center", "axis", "length", "diameter", "material", "spring", "travel",
             "spring_axis"), ("a", "b", "center", "length", "diameter", "travel")),
    "curve": (("name", "center", "axis", "zero_dir", "radius", "radius_start", "radius_end", "start_deg", "end_deg",
               "width", "thickness", "material"),
              ("center", "radius", "radius_start", "radius_end", "width", "thickness")),
    "cad": (("name", "part", "mesh", "units", "center", "rot_deg", "material", "slots", "tread"), ("center",)),
    "inlet": (("name", "center", "size", "count", "rate", "velocity", "spin", "spread_speed", "spread_position",
               "spread_time", "seed"), ("center", "size", "spread_position")),
    "outlet": (("name", "center", "size"), ("center", "size")),
    "flight": (("outlet", "height", "target_distance", "target_height", "ring_diameter", "air"),
               ("height", "target_distance", "target_height", "ring_diameter")),
    "run": (("duration", "timestep", "jam_time", "jam_distance"), ("jam_distance",)),
}
_LISTS = ("wheel", "plate", "bar", "curve", "cad", "inlet", "outlet")  # [[section]], entries named
_SINGLES = ("piece", "flight", "run")  # one table each; an include may supply them
_TOP = ("include", "bench", "shaft", "sweep", "chart", *_SINGLES, *_LISTS)
_MESH_PATHS = {"cad": ("part", "mesh"), "piece": ("mesh",)}  # keys holding paths, read relative to their file
_REQ = object()


def _valid_keys(section: str) -> tuple[str, ...]:
    keys, lengths = _SECTIONS[section]
    return keys + tuple(k + "_in" for k in lengths)


def _num_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Table:
    """Typed reads from one table, with errors that name the table and (for unknown keys) list the valid ones."""

    def __init__(self, section: str, label: str, table, name: str | None = None):
        self.section, self.label, self.t = section, label, table
        self.prefix = section if name is None else f"{section}.{name}"  # the dotted key of this table
        if not isinstance(table, dict):
            raise BenchError(i18n.t("{table} must be a table, not {value}", table=label, value=repr(table)),
                             self.prefix)
        valid = _valid_keys(section)
        for key in table:
            if key not in valid:
                raise BenchError(i18n.t("unknown key '{key}' in {table}; valid keys: {keys}", key=key, table=label,
                                        keys=", ".join(valid)), f"{self.prefix}.{key}")

    def err(self, message: str, key: str | None = None) -> BenchError:
        """`message` is the whole sentence, naming the table (`self.label`); `key` is the field at fault (an `_in`
        form counts as its meters key); without it, the whole table."""
        if key is not None and key.endswith("_in"):
            key = key[:-3]
        return BenchError(message, self.prefix if key is None else f"{self.prefix}.{key}")

    def has(self, key: str) -> bool:
        return key in self.t or key + "_in" in self.t

    def _raw(self, key: str, default, length: bool):
        """The stored value, scaled to meters for an inch form; `default` when absent."""
        if length and key in self.t and key + "_in" in self.t:
            raise self.err(i18n.t("{table} has both {key} and {key}_in; give one", table=self.label, key=key), key)
        scale = 1.0
        if length and key + "_in" in self.t:
            key, scale = key + "_in", IN
        if key not in self.t:
            if default is _REQ:
                keys = ", ".join(_valid_keys(self.section))
                if length:
                    raise self.err(i18n.t("{table} is missing required key '{key}' (or {key}_in); valid keys: {keys}",
                                          table=self.label, key=key, keys=keys), key)
                raise self.err(i18n.t("{table} is missing required key '{key}'; valid keys: {keys}",
                                      table=self.label, key=key, keys=keys), key)
            return default, key, scale
        return self.t[key], key, scale

    def num(self, key, default=_REQ, *, positive=False, nonneg=False, length=False):
        v, k, scale = self._raw(key, default, length)
        if k not in self.t:
            return v
        if not _num_ok(v):
            raise self.err(i18n.t("{table} {key} must be a number, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        v = float(v) * scale
        if positive and not v > 0:
            raise self.err(i18n.t("{table} {key} must be positive, not {value}", table=self.label, key=k,
                                  value=repr(self.t[k])), k)
        if nonneg and v < 0:
            raise self.err(i18n.t("{table} {key} must be zero or more, not {value}", table=self.label, key=k,
                                  value=repr(self.t[k])), k)
        return v

    def whole(self, key, default=_REQ, *, minimum=None):
        v, k, _ = self._raw(key, default, False)
        if k not in self.t:
            return v
        if isinstance(v, bool) or not isinstance(v, int):
            raise self.err(i18n.t("{table} {key} must be a whole number, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        if minimum is not None and v < minimum:
            raise self.err(i18n.t("{table} {key} must be at least {minimum}, not {value}", table=self.label, key=k,
                                  minimum=minimum, value=repr(v)), k)
        return v

    def vec(self, key, n=3, default=_REQ, *, positive=False, length=False):
        v, k, scale = self._raw(key, default, length)
        if k not in self.t:
            return v
        if (not isinstance(v, list) or len(v) != n or not all(_num_ok(x) for x in v)):
            raise self.err(i18n.t("{table} {key} must be a list of {n} numbers, not {value}", table=self.label,
                                  key=k, n=n, value=repr(v)), k)
        out = tuple(float(x) * scale for x in v)
        if positive and not all(x > 0 for x in out):
            raise self.err(i18n.t("{table} {key} must be positive, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        return out

    def text(self, key, default=_REQ, *, one_of=None):
        v, k, _ = self._raw(key, default, False)
        if k not in self.t:
            return v
        if not isinstance(v, str):
            raise self.err(i18n.t("{table} {key} must be text, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        if one_of is not None and v not in one_of:
            raise self.err(i18n.t("{table} {key} {value} is not one of: {choices}", table=self.label, key=k,
                                  value=repr(v), choices=", ".join(one_of)), k)
        return v


def _unit_vec(t: _Table, key: str, v: Vec) -> Vec:
    n = math.sqrt(sum(x * x for x in v))
    if n == 0.0:
        raise t.err(i18n.t("{table} {key} must not be all zeros", table=t.label, key=key), key)
    return tuple(x / n for x in v)


def _name_of(section: str, entry, index: int) -> str:
    if not isinstance(entry, dict):
        raise BenchError(i18n.t("[[{section}]] entry {n} must be a table, not {value}", section=section, n=index + 1,
                                value=repr(entry)))
    name = entry.get("name")
    if not isinstance(name, str) or not name or not all(c.isalnum() or c in "_-" for c in name):
        raise BenchError(i18n.t("[[{section}]] entry {n} needs a name of letters, digits, '_' or '-' (got {name}); "
                                "valid keys: {keys}", section=section, n=index + 1, name=repr(name),
                                keys=", ".join(_valid_keys(section))))
    return name


def _material(t: _Table) -> str:
    m = t.text("material", MATERIAL_DEFAULT)
    if m not in MATERIALS:
        raise t.err(i18n.t("{table} material {material} is not known; known materials: {known}", table=t.label,
                           material=repr(m), known=", ".join(MATERIALS)), "material")
    return m


def _resolve_path(t: _Table, key: str, base_dir: Path) -> Path | None:
    v = t.text(key, None)
    return None if v is None else base_dir / v


def _units(t: _Table, required: bool) -> str | None:
    if "units" not in t.t:
        if required:
            raise t.err(i18n.t("{table} needs units (\"m\", \"mm\" or \"in\") for its mesh", table=t.label))
        return None
    if not required:
        raise t.err(i18n.t("{table} has units, but only a mesh has units", table=t.label))
    return t.text("units", one_of=tuple(UNITS))


# --- piece --------------------------------------------------------------------------------------------------------

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


def _parse_piece(raw, base_dir: Path, warned: list[str]) -> Piece:
    """The [piece]; a warning that doesn't stop the run (a borrowed ball preset) is appended to `warned`."""
    t = _Table("piece", "[piece]", raw)
    preset = t.text("preset", None)
    base: dict = {}
    if preset is not None:
        known = PRESETS["ball"]
        if preset not in known:
            raise t.err(i18n.t("{table} preset {preset} is not a ball preset; known ball presets: {known}",
                               table=t.label, preset=repr(preset), known=", ".join(known)), "preset")
        base = known[preset]
    kind = t.text("kind", "squishy" if preset else "rigid", one_of=("squishy", "rigid"))
    shape = t.text("shape", "sphere" if preset else _REQ, one_of=("sphere", "box", "cylinder", "disc", "mesh"))
    if shape == "disc":  # a disc is a short cylinder
        shape = "cylinder"
    if preset and shape != "sphere":
        raise t.err(i18n.t("{table} preset {preset} is a ball; its shape is sphere, not {shape}", table=t.label,
                           preset=repr(preset), shape=shape))
    if kind == "squishy" and shape != "sphere":
        raise t.err(i18n.t(
            "{table} is squishy with shape {shape}, but squishy pieces are balls only (a sphere, or a ball preset); "
            "use kind = \"rigid\" as a rigid stand-in (rigid pieces may give their own contact `stiffness`)",
            table=t.label, shape=shape))
    ball_given = [k for k in _BALL_KEYS if k in t.t]
    if kind == "rigid" and ball_given:
        raise t.err(i18n.t("{table} is rigid, so {key} does not apply (ball numbers are for squishy pieces); "
                           "a rigid piece's own contact stiffness is `stiffness` (N/m)", table=t.label,
                           key=ball_given[0]))
    if kind == "squishy" and "stiffness" in t.t:
        raise t.err(i18n.t("{table} is squishy, so `stiffness` does not apply; give the launcher model's "
                           "stiffness_n (N for a quarter-diameter squeeze)", table=t.label))
    name = t.text("name", preset or "piece")
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
            raise t.err(i18n.t("{table} has a mesh but shape is not \"mesh\"", table=t.label))
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
                warned.append(i18n.t("[piece] {keys} not given; using the nearest ball preset's ({near}). Measure "
                                     "your own ball (docs/launcher.md)", keys=" and ".join(missing), near=near))
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
        raise t.err(i18n.t("{table} is a mesh, so it needs an inertia = [Ixx, Iyy, Izz] (kg m^2); there is no "
                           "default", table=t.label))
    elif ball is not None:
        inertia = (ball.inertia_factor * mass * (size[0] / 2) ** 2,) * 3
    else:
        inertia = _default_inertia(shape, size, mass)
    stiffness = t.num("stiffness", None, positive=True)
    return Piece(name=name, kind=kind, shape=shape, size=size, mass=mass, inertia=inertia, ball=ball,
                 stiffness=stiffness, mesh=mesh, units=units)


# --- shafts and shapes ----------------------------------------------------------------------------------------------

def _parse_shaft(name: str, raw) -> Shaft:
    t = _Table("shaft", f"[shaft.{name}]", raw, name)
    motor = t.text("motor", None)
    if motor is not None and motor not in MOTORS:
        raise t.err(i18n.t("{table} motor {motor} is not known; known motors: {known}", table=t.label,
                           motor=repr(motor), known=", ".join(sorted(MOTORS))), "motor")
    target = t.num("target_rps", None, positive=True)
    voltage = t.num("voltage", None)
    coupled = t.text("coupled_to", None)
    if target is not None and voltage is not None:
        raise t.err(i18n.t("{table} has both target_rps and voltage; give one", table=t.label), "voltage")
    if "control" in t.t:
        control = t.text("control", one_of=("speed", "voltage", "free"))
    elif target is not None:
        control = "speed"
    elif voltage is not None:
        control = "voltage"
    elif motor is None or coupled is not None:
        control = "free"
    else:
        raise t.err(i18n.t("{table} has motor {motor} but neither target_rps nor voltage; give one, or control = "
                           "\"free\"", table=t.label, motor=repr(motor)))
    if control == "speed" and target is None:
        raise t.err(i18n.t("{table} control is speed, so it needs target_rps", table=t.label), "target_rps")
    if control == "voltage" and voltage is None:
        raise t.err(i18n.t("{table} control is voltage, so it needs voltage", table=t.label), "voltage")
    if control == "free" and (target is not None or voltage is not None):
        raise t.err(i18n.t("{table} control is free, so it takes no target_rps or voltage", table=t.label))
    if control != "free" and motor is None:
        raise t.err(i18n.t("{table} has a target_rps or voltage but no motor; a motor is needed to drive the shaft",
                           table=t.label))
    if control == "speed" and "kP" not in t.t:
        raise t.err(i18n.t("{table} control is speed, so it needs kP (volts per rps of error); valid keys: {keys}",
                           table=t.label, keys=", ".join(_valid_keys("shaft"))))
    if coupled is not None and (motor is not None or control != "free"):
        raise t.err(i18n.t("{table} is coupled to {shaft}, so it takes no motor or control (the coupling drives it)",
                           table=t.label, shaft=repr(coupled)))
    if "ratio" in t.t and coupled is None:
        raise t.err(i18n.t("{table} has a ratio but no coupled_to", table=t.label), "ratio")
    wheel_inertia = t.num("wheel_inertia", nonneg=True)
    added = t.num("added_inertia", 0.0, nonneg=True)
    if wheel_inertia + added <= 0.0:
        raise t.err(i18n.t("{table} wheel_inertia + added_inertia must be positive", table=t.label))
    ratio = t.num("ratio", 1.0)
    if ratio == 0.0:
        raise t.err(i18n.t("{table} ratio must not be zero", table=t.label), "ratio")
    return Shaft(
        name=name, motor=motor, count=t.whole("count", 1, minimum=1), gearing=t.num("gearing", 1.0, positive=True),
        wheel_inertia=wheel_inertia, added_inertia=added, control=control, target_rps=target, voltage=voltage,
        kP=t.num("kP", 0.0, nonneg=True), kS=t.num("kS", 0.0, nonneg=True), kV=t.num("kV", None, positive=True),
        stator_limit=t.num("stator_limit", None, positive=True), supply_limit=t.num("supply_limit", None, positive=True),
        coupled_to=coupled, ratio=ratio)


def _parse_wheel(name, raw) -> Wheel:
    t = _Table("wheel", f"[[wheel]] '{name}'", raw, name)
    tread = t.text("tread", "35A")  # estimate: a common shooter-wheel durometer
    if tread not in PRESETS["wheels"]:
        raise t.err(i18n.t("{table} tread {tread} is not a tread preset; known treads: {known}", table=t.label,
                           tread=repr(tread), known=", ".join(PRESETS["wheels"])), "tread")
    return Wheel(name=name, shaft=t.text("shaft"), center=t.vec("center", length=True),
                 axis=_unit_vec(t, "axis", t.vec("axis", 3, (0.0, 1.0, 0.0))),
                 diameter=t.num("diameter", positive=True, length=True), width=t.num("width", positive=True, length=True),
                 tread=tread)


def _parse_plate(name, raw) -> Plate:
    t = _Table("plate", f"[[plate]] '{name}'", raw, name)
    return Plate(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True),
                 rot_deg=t.vec("rot_deg", 3, (0.0, 0.0, 0.0)), material=_material(t))


def _parse_bar(name, raw) -> Bar:
    t = _Table("bar", f"[[bar]] '{name}'", raw, name)
    ends = t.has("a") or t.has("b")
    placed = t.has("center") or t.has("axis") or t.has("length")
    if ends and placed:
        raise t.err(i18n.t("{table} gives both ends (a, b) and a position (center, axis, length); give one",
                           table=t.label))
    if ends:
        a, b = t.vec("a", length=True), t.vec("b", length=True)
    else:
        c = t.vec("center", length=True)
        ax = _unit_vec(t, "axis", t.vec("axis"))
        h = t.num("length", positive=True, length=True) / 2
        a, b = (tuple(ci - h * x for ci, x in zip(c, ax)), tuple(ci + h * x for ci, x in zip(c, ax)))
    if a == b:
        raise t.err(i18n.t("{table} has both ends at the same point", table=t.label), "b")
    spring = t.num("spring", None, positive=True)
    if spring is None and t.has("travel"):
        raise t.err(i18n.t("{table} has travel but no spring", table=t.label), "travel")
    travel = t.num("travel", positive=True, length=True) if spring is not None else 0.0
    return Bar(name=name, a=a, b=b, diameter=t.num("diameter", positive=True, length=True), material=_material(t),
               spring=spring, travel=travel, spring_axis=_unit_vec(t, "spring_axis", t.vec("spring_axis", 3, (0.0, 0.0, 1.0))))


def _parse_curve(name, raw) -> Curve:
    t = _Table("curve", f"[[curve]] '{name}'", raw, name)
    if t.has("radius") and (t.has("radius_start") or t.has("radius_end")):
        raise t.err(i18n.t("{table} has radius with radius_start or radius_end; give radius, or the two ends",
                           table=t.label))
    if t.has("radius"):
        rs = re = t.num("radius", positive=True, length=True)
    else:
        rs = t.num("radius_start", positive=True, length=True)
        re = t.num("radius_end", positive=True, length=True)
    thickness = t.num("thickness", THICKNESS_DEFAULT, positive=True, length=True)
    if min(rs, re) < thickness:
        raise t.err(i18n.t("{table} has a radius ({radius} m) smaller than its thickness ({thickness} m)",
                           table=t.label, radius=f"{min(rs, re):.4g}", thickness=f"{thickness:.4g}"))
    start, end = t.num("start_deg"), t.num("end_deg")
    if end == start:
        raise t.err(i18n.t("{table} has a zero angle span (start_deg equals end_deg)", table=t.label))
    if abs(end - start) > 360.0:
        raise t.err(i18n.t("{table} spans {span} degrees; the angle span must be at most 360", table=t.label,
                           span=f"{abs(end - start):g}"))
    axis = _unit_vec(t, "axis", t.vec("axis", 3, (0.0, 1.0, 0.0)))
    zd = t.vec("zero_dir", 3, None)
    if zd is None:
        zd = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 0.0, 1.0)
    dot = sum(a * z for a, z in zip(axis, zd))
    zd = _unit_vec(t, "zero_dir", tuple(z - dot * a for a, z in zip(axis, zd)))  # drop the part along the axis
    return Curve(name=name, center=t.vec("center", length=True), axis=axis, zero_dir=zd, radius_start=rs, radius_end=re,
                 start_deg=start, end_deg=end, width=t.num("width", positive=True, length=True), thickness=thickness,
                 material=_material(t))


def _parse_cad(name, raw, base_dir: Path) -> Cad:
    t = _Table("cad", f"[[cad]] '{name}'", raw, name)
    part, mesh = _resolve_path(t, "part", base_dir), _resolve_path(t, "mesh", base_dir)
    if (part is None) == (mesh is None):
        raise t.err(i18n.t("{table} needs exactly one of part = \"<part folder>\" or mesh = \"<file.stl>\"",
                           table=t.label))
    slots = t.t.get("slots", {})
    if not isinstance(slots, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in slots.items()):
        raise t.err(i18n.t("{table} slots must be a table of slot name = shaft name, not {value}", table=t.label,
                           value=repr(slots)))
    tread = None
    if mesh is not None:
        for key in ("slots", "tread"):
            if t.has(key):
                raise t.err(i18n.t("{table} has {key}, but a mesh doesn't move: {key} is for a part folder's turning "
                                   "wheels", table=t.label, key=key))
    else:
        tread = t.text("tread", "35A")  # estimate: a common shooter-wheel durometer, as for [[wheel]]
        if tread not in PRESETS["wheels"]:
            raise t.err(i18n.t("{table} tread {tread} is not a tread preset; known treads: {known}", table=t.label,
                               tread=repr(tread), known=", ".join(PRESETS["wheels"])), "tread")
    return Cad(name=name, part=part, mesh=mesh, units=_units(t, mesh is not None),
               center=t.vec("center", 3, (0.0, 0.0, 0.0), length=True), rot_deg=t.vec("rot_deg", 3, (0.0, 0.0, 0.0)),
               material=_material(t), slots=dict(slots), tread=tread)


def _parse_inlet(name, raw) -> Inlet:
    t = _Table("inlet", f"[[inlet]] '{name}'", raw, name)
    return Inlet(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True),
                 count=t.whole("count", 1, minimum=1), rate=t.num("rate", 5.0, positive=True),
                 velocity=t.vec("velocity", 3, (0.0, 0.0, 0.0)), spin=t.vec("spin", 3, (0.0, 0.0, 0.0)),
                 spread_speed=t.num("spread_speed", 0.0, nonneg=True),
                 spread_position=t.num("spread_position", 0.0, nonneg=True, length=True),
                 spread_time=t.num("spread_time", 0.0, nonneg=True), seed=t.whole("seed", 1))


def _parse_outlet(name, raw) -> Outlet:
    t = _Table("outlet", f"[[outlet]] '{name}'", raw, name)
    return Outlet(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True))


def _parse_flight(raw, known: list[str]) -> Flight:
    """The [flight]; `known` is the names of the file's outlets."""
    t = _Table("flight", "[flight]", raw)
    outlet = t.text("outlet")
    if outlet not in known:
        raise t.err(i18n.t("{table} outlet {outlet} is not defined; outlets: {known}", table=t.label,
                           outlet=repr(outlet), known=", ".join(known) or i18n.t("(none)")), "outlet")
    air = t.text("air", "default")
    if air not in PRESETS["air"]:
        raise t.err(i18n.t("{table} air {air} is not an air preset; known: {known}", table=t.label, air=repr(air),
                           known=", ".join(PRESETS["air"])), "air")
    return Flight(outlet=outlet, height=t.num("height", 0.0, nonneg=True, length=True),
                  target_distance=t.num("target_distance", positive=True, length=True),
                  target_height=t.num("target_height", nonneg=True, length=True),
                  ring_diameter=t.num("ring_diameter", None, positive=True, length=True), air=air)


def _parse_run(raw) -> RunSettings:
    t = _Table("run", "[run]", raw)
    return RunSettings(duration=t.num("duration", 5.0, positive=True), timestep=t.num("timestep", None, positive=True),
                       jam_time=t.num("jam_time", 1.0, positive=True),
                       jam_distance=t.num("jam_distance", 0.01, positive=True, length=True))


# --- includes -------------------------------------------------------------------------------------------------------

def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError(i18n.t("{path}: {error}", path=path, error=e)) from None


def _absolute_paths(raw: dict, base_dir: Path) -> dict:
    """Paths in an included file are read relative to that file; pin them before merging."""
    out = copy.deepcopy(raw)
    for section, keys in _MESH_PATHS.items():
        entries = out.get(section, [])
        for e in ([entries] if isinstance(entries, dict) else entries):
            for k in keys:
                if isinstance(e, dict) and isinstance(e.get(k), str):
                    e[k] = str(base_dir / e[k])
    return out


def _shaft_tables(value, where: str = "") -> dict:
    """A file's `shaft` value, checked to be one table per shaft ([shaft.top], not [[shaft]] or shaft = 5)."""
    if isinstance(value, dict):
        bad = next((f"shaft.{k} = {v!r}" for k, v in value.items() if not isinstance(v, dict)), None)
    else:
        bad = "[[shaft]]" if isinstance(value, list) else f"shaft = {value!r}"
    if bad:
        raise BenchError(i18n.t("{where}[shaft.<name>] must be a table: write [shaft.top], not {bad}", where=where,
                                bad=bad))
    return value


def _resolve_includes(raw: dict, base_dir: Path, stack: tuple[Path, ...] = ()) -> dict:
    """`raw` with its `include` files merged in first (the file's own entries win by name), `include` dropped."""
    includes = raw.get("include", [])
    if not isinstance(includes, list) or not all(isinstance(i, str) for i in includes):
        raise BenchError(i18n.t("include must be a list of file names, not {value}", value=repr(includes)))
    own = {k: v for k, v in raw.items() if k != "include"}
    if not includes:
        return own
    merged: dict = {}
    origin: dict[tuple, Path] = {}

    def claim(key: tuple, path: Path, label: str):
        if key in origin and origin[key] != path:
            raise BenchError(i18n.t("{table} is defined in both {first} and {second}; define it in the including "
                                    "file to choose", table=label, first=origin[key], second=path))
        origin[key] = path

    for inc in includes:
        path = (base_dir / inc).resolve()
        if path in stack:
            raise BenchError(i18n.t("{path} includes itself (an include cycle)", path=path))
        sub = _read_toml(path)
        sub = _absolute_paths(_resolve_includes(sub, path.parent, stack + (path,)), path.parent)
        for section in _LISTS:
            entries = sub.get(section, [])
            if not isinstance(entries, list):
                raise BenchError(i18n.t("{path}: [[{section}]] must be a list of tables", path=path, section=section))
            for i, e in enumerate(entries):
                name = _name_of(section, e, i)
                claim((section, name), path, f"[[{section}]] '{name}'")
                merged.setdefault(section, []).append(e)
        for name, e in _shaft_tables(sub.get("shaft", {}), f"{path}: ").items():
            claim(("shaft", name), path, f"[shaft.{name}]")
            merged.setdefault("shaft", {})[name] = e
        for section in _SINGLES:
            if section in sub:
                claim((section,), path, f"[{section}]")
                merged[section] = sub[section]
    for section in _LISTS:  # the file's own entries replace included ones of the same name, in place
        entries = merged.get(section, [])
        own_entries = own.get(section, [])
        if not isinstance(own_entries, list):
            raise BenchError(i18n.t("[[{section}]] must be a list of tables", section=section))
        names = {_name_of(section, e, i): i for i, e in enumerate(entries)}
        for i, e in enumerate(own_entries):
            n = _name_of(section, e, i)
            if n in names:
                entries[names[n]] = e
            else:
                entries.append(e)
        if entries:
            merged[section] = entries
    own_shafts = _shaft_tables(own.get("shaft", {}))
    if "shaft" in merged or "shaft" in own:
        merged.setdefault("shaft", {}).update(own_shafts)
    for key, v in own.items():
        if key not in _LISTS and key != "shaft":
            merged[key] = v  # the file's own tables (bench, sweep, chart, and any single section) win whole
    return merged


# --- parsing --------------------------------------------------------------------------------------------------------

def _named(section: str, entries, problems: list[Problem] | None = None) -> list[tuple[str, dict]]:
    """The section's (name, table) pairs. With `problems`, a bad entry is reported there and left out."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        message = i18n.t("[[{section}]] must be a list of tables (write each as [[{section}]])", section=section)
        if problems is None:
            raise BenchError(message, section)
        problems.append(Problem("error", section, message))
        return []
    out: dict[str, dict] = {}
    for i, e in enumerate(entries):
        try:
            n = _name_of(section, e, i)
            if n in out:
                raise BenchError(i18n.t("[[{section}]] has two entries named '{name}' (twice); names must be unique",
                                        section=section, name=n), f"{section}.{n}")
        except BenchError as err:
            if problems is None:
                raise
            problems.append(Problem("error", err.key, str(err)))
            continue
        out[n] = e
    return list(out.items())


def parse_bench(raw: dict, base_dir: Path, problems: list[Problem] | None = None) -> Bench | None:
    """Build a `Bench` from a raw dict (the parsed TOML); file names in it are relative to `base_dir`.

    The first problem raises `BenchError`. With a `problems` list, every table and entry is parsed on its own: each
    error is appended (as a `Problem` with its key) and that entry left out, the cross-checks run on what parsed,
    and the result is None if there was any error."""
    base_dir = Path(base_dir)
    collect = problems is not None
    errors: list[Problem] = problems if collect else []
    start = len(errors)

    def attempt(fn, *args, key: str | None = None):
        """fn(*args), or None after recording its error (collecting mode); the error's own key wins over `key`."""
        try:
            return fn(*args)
        except BenchError as e:
            if not collect:
                raise
            errors.append(Problem("error", e.key or key, str(e)))
            return None

    for key in raw:
        if key not in _TOP:
            attempt(_raise, BenchError(i18n.t("unknown section [{section}]; valid sections: {sections}", section=key,
                                              sections=", ".join(_TOP)), key))
    try:
        raw = _resolve_includes(raw, base_dir)
    except BenchError as e:
        if not collect:
            raise
        errors.append(Problem("error", e.key, str(e)))
        return None
    bt = fields = None
    if "bench" not in raw:
        attempt(_raise, BenchError(i18n.t("[bench] is missing: give at least name = \"...\" (valid keys: name, "
                                          "author, idea, notes)"), "bench"))
    else:
        bt = attempt(_Table, "bench", "[bench]", raw["bench"])
    warned: list[str] = []
    piece = None
    if "piece" not in raw:
        attempt(_raise, BenchError(i18n.t("[piece] is missing: give preset = \"<ball preset>\" or a custom piece "
                                          "(valid keys: {keys})", keys=", ".join(_valid_keys("piece"))), "piece"))
    else:
        piece = attempt(_parse_piece, raw["piece"], base_dir, warned, key="piece")
    shafts_raw = attempt(_shaft_tables, raw.get("shaft", {}), key="shaft") or {}
    shafts = {n: sh for n, v in shafts_raw.items() if (sh := attempt(_parse_shaft, n, v, key=f"shaft.{n}"))}
    parsers = {
        "wheel": _parse_wheel, "plate": _parse_plate, "bar": _parse_bar, "curve": _parse_curve,
        "inlet": _parse_inlet, "outlet": _parse_outlet,
    }
    named = {s: _named(s, raw.get(s, []), errors if collect else None) for s in (*parsers, "cad")}
    parsed = {s: tuple(x for n, e in named[s] if (x := attempt(p, n, e, key=f"{s}.{n}")) is not None)
              for s, p in parsers.items()}
    cads = tuple(x for n, e in named["cad"] if (x := attempt(_parse_cad, n, e, base_dir, key=f"cad.{n}")) is not None)
    for s in shafts.values():
        if s.coupled_to is not None:
            attempt(_check_coupling, s, shafts, list(shafts_raw), key=f"shaft.{s.name}.coupled_to")
    names = ", ".join(shafts_raw) or i18n.t("(none)")
    for w in parsed["wheel"]:
        if w.shaft not in shafts_raw:
            attempt(_raise, BenchError(i18n.t("[[wheel]] '{name}' names shaft {shaft}, which is not defined; "
                                              "shafts: {shafts}", name=w.name, shaft=repr(w.shaft), shafts=names),
                                       f"wheel.{w.name}.shaft"))
    for c in cads:
        for slot, shaft in c.slots.items():
            if shaft not in shafts_raw:
                attempt(_raise, BenchError(i18n.t("[[cad]] '{name}' slot {slot} names shaft {shaft}, which is not "
                                                  "defined; shafts: {shafts}", name=c.name, slot=repr(slot),
                                                  shaft=repr(shaft), shafts=names), f"cad.{c.name}.slots"))
    flight = None
    if "flight" in raw:
        flight = attempt(_parse_flight, raw["flight"], [n for n, _ in named["outlet"]], key="flight")
    sweep = raw.get("sweep", {})
    if not isinstance(sweep, dict):
        attempt(_raise, BenchError(i18n.t("[sweep] must be a table, not {value}", value=repr(sweep)), "sweep"))
        sweep = {}
    for dotted, values in sweep.items():
        attempt(_check_path, raw, dotted, key=f"sweep.{dotted}")
        if not isinstance(values, list) or not values:
            attempt(_raise, BenchError(i18n.t("[sweep] {key} must be a non-empty list of values, not {value}",
                                              key=dotted, value=repr(values)), f"sweep.{dotted}"))
    charts = raw.get("chart", [])
    if not isinstance(charts, list) or not all(isinstance(c, dict) for c in charts):
        attempt(_raise, BenchError(i18n.t("[[chart]] must be a list of tables"), "chart"))
        charts = []
    if bt is not None:
        fields = attempt(lambda: (bt.text("name"), bt.text("author", ""), bt.text("idea", ""), bt.text("notes", "")))
    run = attempt(_parse_run, raw.get("run", {}), key="run")
    if len(errors) > start:
        return None
    return Bench(
        name=fields[0], author=fields[1], idea=fields[2], notes=fields[3],
        piece=piece, shafts=shafts, wheels=parsed["wheel"], plates=parsed["plate"], bars=parsed["bar"],
        curves=parsed["curve"], cads=cads, inlets=parsed["inlet"], outlets=parsed["outlet"], flight=flight,
        run=run, materials=dict(MATERIALS), sweep=dict(sweep), charts=list(charts),
        raw=copy.deepcopy(raw), base_dir=base_dir, warnings=tuple(warned))


def _raise(err: BenchError):
    raise err


def _check_coupling(shaft: Shaft, shafts: dict[str, Shaft], defined=None) -> None:
    """`defined`: every shaft name in the file, parsed or not (a shaft that failed to parse is not reported as
    missing, and its chain is not followed)."""
    defined = shafts if defined is None else defined
    chain, cur = [shaft.name], shaft
    while cur.coupled_to is not None:
        if cur.coupled_to not in defined:
            raise BenchError(i18n.t("[shaft.{name}] is coupled to {shaft}, which is not defined; shafts: {shafts}",
                                    name=cur.name, shaft=repr(cur.coupled_to), shafts=", ".join(defined)))
        if cur.coupled_to in chain:
            if cur.coupled_to == cur.name:
                raise BenchError(i18n.t("[shaft.{name}] is coupled to itself", name=cur.name))
            raise BenchError(i18n.t("shafts are coupled in a loop: {chain}",
                                    chain=" -> ".join(chain + [cur.coupled_to])))
        if cur.coupled_to not in shafts:
            return
        cur = shafts[cur.coupled_to]
        chain.append(cur.name)


def load_bench(path: Path) -> Bench:
    """Load a bench file. Errors name the file."""
    path = Path(path)
    raw = _read_toml(path)
    base = path.resolve().parent
    try:
        return parse_bench(_resolve_includes(raw, base, (path.resolve(),)), base)
    except BenchError as e:
        msg = str(e)
        raise BenchError(msg if msg.startswith(str(path)) else i18n.t("{path}: {error}", path=path, error=msg),
                         e.key) from None


# --- overrides ------------------------------------------------------------------------------------------------------

def _split(dotted: str) -> tuple[str, str | None, str]:
    """(section, entry name or None, key) of a dotted key; errors for a key that cannot be one."""
    parts = dotted.split(".")
    section = parts[0]
    if section in _LISTS or section == "shaft":
        if len(parts) != 3:
            raise BenchError(i18n.t("'{dotted}' should read {section}.<name>.<key>, e.g. {section}.top.{key}",
                                    dotted=dotted, section=section, key=_SECTIONS[section][0][0]))
        return section, parts[1], parts[2]
    if section in _SINGLES or section == "bench":
        if len(parts) != 2:
            raise BenchError(i18n.t("'{dotted}' should read {section}.<key>", dotted=dotted, section=section))
        return section, None, parts[1]
    raise BenchError(i18n.t("unknown section '{section}' in '{dotted}'; valid sections: {sections}", section=section,
                            dotted=dotted, sections=", ".join(("bench", *_SINGLES, "shaft", *_LISTS))))


def _check_path(raw: dict, dotted: str) -> tuple[str, str | None, str]:
    section, name, key = _split(dotted)
    if key not in _valid_keys(section):
        raise BenchError(i18n.t("unknown key '{key}' in '{dotted}'; valid keys for {section}: {keys}", key=key,
                                dotted=dotted, section=section, keys=", ".join(_valid_keys(section))))
    if name is not None:
        known = list(raw.get("shaft", {})) if section == "shaft" else [e.get("name") for e in raw.get(section, [])]
        if name not in known:
            raise BenchError(i18n.t("no {section} named '{name}' in '{dotted}'; {section}s: {known}", section=section,
                                    name=name, dotted=dotted, known=", ".join(known) or i18n.t("(none)")))
    return section, name, key


def with_overrides(raw: dict, overrides: dict[str, object]) -> dict:
    """A deep copy of `raw` with dotted keys set: `wheel.top.diameter`, `curve.hood.radius_end`, `run.duration`.

    A list-section entry (and a shaft) is addressed by its name. Setting `diameter` replaces a stored `diameter_in`
    and the other way round."""
    out = copy.deepcopy(raw)
    for dotted, value in overrides.items():
        section, name, key = _check_path(out, dotted)
        if name is None:
            table = out.setdefault(section, {})
        elif section == "shaft":
            table = out["shaft"][name]
        else:
            table = next(e for e in out[section] if e.get("name") == name)
        table.pop(key[:-3] if key.endswith("_in") else key + "_in", None)
        table[key] = copy.deepcopy(value)
    return out
